"""Code-derived projection of the execution-ledger record schema.

The ledger is an event store: every record type is persisted as one typed row
in the ``execution_record`` table (plus the ``workflow_execution`` header
projection and ``execution_content`` blobs). Records are NOT normalized into a
table per type; their relationships are logical foreign keys — id/ref fields
enforced by the in-memory validator, not database constraints.

This module makes that logical schema explicit and authoritative: identities,
storage table, control/observation plane, and the reference graph are projected
from the actual record dataclasses. Fields carry the truth; the small registries
below record only what field names cannot express (polymorphic references and
the semantic plane), and a test asserts the projection covers every record type
and that every declared field exists.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, get_args

from ..contracts.ledger_record_definition import PersistedRuntimeRecord


RECORD_SCHEMA_PROJECTION_VERSION = "agent_runtime_record_schema_v1"

# Physical tables (see postgres_execution_ledger_ddl).
_HEADER_RECORD = "WorkflowExecutionRecord"

# Each record's own identity field. Authoritative: the ★ entries are the field
# names the validator keys on via _unique_map; the rest name the record's own id.
_RECORD_IDENTITY: dict[str, str] = {
    "WorkflowExecutionRecord": "workflow_execution_id",
    "ExecutionAuthorizationBindingRecord": "execution_authorization_binding_id",
    "ExecutionStartAdmissionRecord": "execution_start_admission_id",
    "ModuleDispatchAdmissionRecord": "module_dispatch_admission_id",
    "ExecutionAuthorizationInvalidationRecord": "execution_authorization_invalidation_id",
    "ExecutionProfileRecord": "execution_profile_id",
    "WorkflowModuleRunRecord": "module_run_id",
    "WorkflowModuleExecutionVariantRecord": "variant_id",
    "WorkflowAttemptStartedRecord": "attempt_id",
    "WorkflowAttemptRecord": "attempt_id",
    "InvocationCommitRecord": "invocation_commit_id",
    "StaleOutputRecord": "stale_output_id",
    "AttemptOrphanedRecord": "orphaned_record_id",
    "AttemptOutputBundle": "attempt_output_bundle_id",
    "ExecutionInputRef": "execution_input_id",
    "ExecutionOutputRef": "execution_output_id",
    "ModelCallRecord": "model_call_id",
    "ToolCallRecord": "tool_call_id",
    "UsageEvent": "usage_event_id",
    "UsageMeterOutboxRecord": "usage_meter_outbox_id",
    "UsageMeterDeliveryRecord": "usage_meter_delivery_id",
    "ContextBinding": "context_binding_id",
    "ContextEvent": "context_event_id",
    "ExternalEventApplicationRecord": "event_id",
    "CheckpointRecord": "checkpoint_id",
    "BackendAcknowledgementRecord": "backend_acknowledgement_id",
    "EvaluationRun": "evaluation_run_id",
    "EvaluationResult": "evaluation_result_id",
    "EvaluationSet": "evaluation_set_id",
    "Selection": "selection_id",
    "OperationAuthorizationRequestRecord": "operation_authorization_request_id",
    "OperationAuthorizationBindingRecord": "operation_authorization_binding_id",
    "OperationGrantBindingRecord": "operation_grant_binding_id",
    "GatewayOperationEffectRecord": "gateway_operation_effect_id",
    "InvocationGatewayEffectBindingRecord": "invocation_gateway_effect_binding_id",
    "ModuleOutputResolutionRecord": "module_output_resolution_id",
    "ModuleOutcome": "dispatch_id",
    "LegacyExecutionEntitlementSnapshot": "entitlement_snapshot_id",
    "LegacyModuleCapabilityGrant": "grant_id",
}

# The canonical record that owns each shared identity field, for resolving a
# reference to one target. attempt_id is defined on both the start and terminal
# attempt records; the terminal record is the canonical join target.
_FK_TARGET: dict[str, str] = {
    "workflow_execution_id": "WorkflowExecutionRecord",
    "execution_authorization_binding_id": "ExecutionAuthorizationBindingRecord",
    "execution_profile_id": "ExecutionProfileRecord",
    "module_run_id": "WorkflowModuleRunRecord",
    "variant_id": "WorkflowModuleExecutionVariantRecord",
    "attempt_id": "WorkflowAttemptRecord",
    "dispatch_id": "ModuleOutcome",
    "context_binding_id": "ContextBinding",
    "checkpoint_id": "CheckpointRecord",
    "evaluation_run_id": "EvaluationRun",
    "operation_grant_binding_id": "OperationGrantBindingRecord",
    "grant_id": "LegacyModuleCapabilityGrant",
    "entitlement_snapshot_id": "LegacyExecutionEntitlementSnapshot",
}

# References that field names cannot resolve because the field is renamed or
# polymorphic. operation_id names either a model or a tool call.
_POLYMORPHIC_REFS: dict[str, tuple[str, ...]] = {
    "operation_id": ("ModelCallRecord", "ToolCallRecord"),
}

# The observation plane: per-call telemetry / usage. These are the evaluation
# payload, not the control state machine; a duplicate here is a harmless extra
# sample rather than a broken invariant.
_OBSERVATION_RECORDS: frozenset[str] = frozenset(
    {
        "ModelCallRecord",
        "ToolCallRecord",
        "UsageEvent",
        "UsageMeterOutboxRecord",
        "UsageMeterDeliveryRecord",
    }
)


def _record_types() -> dict[str, type]:
    return {cls.__name__: cls for cls in get_args(PersistedRuntimeRecord)}


def _storage_table(record_name: str) -> str:
    if record_name == _HEADER_RECORD:
        return "workflow_execution+execution_record"
    return "execution_record"


def build_runtime_record_schema_projection() -> Mapping[str, Any]:
    """Project the ledger record schema and logical reference graph from code."""

    types = _record_types()
    unregistered = sorted(set(types) - set(_RECORD_IDENTITY))
    if unregistered:
        raise RuntimeError(
            f"record types missing a registered identity: {unregistered}"
        )

    records: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for name in sorted(types):
        cls = types[name]
        identity = _RECORD_IDENTITY[name]
        field_names = [f.name for f in dataclasses.fields(cls)]
        if identity not in field_names:
            raise RuntimeError(
                f"{name} identity field {identity!r} is not a declared field"
            )
        references: list[dict[str, Any]] = []
        for field_name in field_names:
            if field_name == identity:
                continue
            if not (field_name.endswith("_id") or field_name.endswith("_ref")):
                continue
            if field_name in _POLYMORPHIC_REFS:
                targets = list(_POLYMORPHIC_REFS[field_name])
                references.append(
                    {"field": field_name, "targets": targets, "kind": "polymorphic"}
                )
                for target in targets:
                    edges.append({"from": name, "field": field_name, "to": target})
            elif field_name in _FK_TARGET and _FK_TARGET[field_name] != name:
                target = _FK_TARGET[field_name]
                references.append(
                    {"field": field_name, "targets": [target], "kind": "reference"}
                )
                edges.append({"from": name, "field": field_name, "to": target})
            else:
                references.append(
                    {"field": field_name, "targets": [], "kind": "external"}
                )
        records.append(
            {
                "record": name,
                "identity": identity,
                "table": _storage_table(name),
                "plane": (
                    "observation" if name in _OBSERVATION_RECORDS else "control"
                ),
                "references": references,
            }
        )

    return MappingProxyType(
        {
            "schema_version": RECORD_SCHEMA_PROJECTION_VERSION,
            "storage_model": "event_store",
            "tables": (
                "workflow_execution",
                "execution_transaction",
                "execution_record",
                "execution_content",
            ),
            "record_count": len(records),
            "records": tuple(records),
            "edges": tuple(edges),
        }
    )


def render_runtime_record_schema_markdown() -> str:
    """Render the record-schema projection as a deterministic Markdown table."""

    projection = build_runtime_record_schema_projection()
    lines = [
        "# Generated Agent Runtime Record Schema",
        "",
        f"schema_version: `{projection['schema_version']}`  ",
        f"storage_model: `{projection['storage_model']}`  ",
        f"records: {projection['record_count']}  ",
        "",
        "Every record is one typed row in `execution_record`; relationships are "
        "logical foreign keys (id/ref fields), not database constraints.",
        "",
        "| Record | Plane | Identity | References |",
        "| --- | --- | --- | --- |",
    ]
    for record in projection["records"]:
        refs = []
        for reference in record["references"]:
            if reference["kind"] == "external":
                refs.append(f"{reference['field']} (ext)")
            elif reference["kind"] == "polymorphic":
                refs.append(
                    f"{reference['field']} → {'|'.join(reference['targets'])}"
                )
            else:
                refs.append(f"{reference['field']} → {reference['targets'][0]}")
        lines.append(
            f"| {record['record']} | {record['plane']} | `{record['identity']}` "
            f"| {', '.join(refs) if refs else '(none)'} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "RECORD_SCHEMA_PROJECTION_VERSION",
    "build_runtime_record_schema_projection",
    "render_runtime_record_schema_markdown",
]
