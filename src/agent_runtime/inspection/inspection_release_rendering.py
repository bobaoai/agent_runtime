"""Project selected Runtime Release metadata without reading another authority."""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import StrEnum
import hashlib
import json
from typing import Any, Mapping

from ..contracts.inspection_release_definition import ReleaseInventorySelection
from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    PromptBundleRelease,
    PromptComponentRelease,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseMember,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowRelease,
)
from ..foundation.foundation_contract_validation import validate_sha256
from ..registry.registry_release_registration import RuntimeReleaseRegistrySnapshot


RELEASE_INVENTORY_SCHEMA_VERSION = "agent_runtime_release_inventory_v4"


@dataclass(frozen=True)
class _ReleaseFamilyProjection:
    snapshot_field: str
    subject_kind: ReleaseSubjectKind
    record_type: type[Any]
    subject_id_field: str
    version_field: str
    extension_fields: tuple[str, ...]


_RELEASE_FAMILIES = (
    _ReleaseFamilyProjection(
        "schema_assets", ReleaseSubjectKind.SCHEMA_ASSET, SchemaAssetRelease,
        "schema_asset_id", "schema_asset_version", ("schema_sha256",),
    ),
    _ReleaseFamilyProjection(
        "prompt_components", ReleaseSubjectKind.PROMPT_COMPONENT,
        PromptComponentRelease, "prompt_component_id", "prompt_component_version",
        (
            "component_kind", "media_type", "formatter_id", "formatter_version",
            "source_members", "formatted_content_sha256",
        ),
    ),
    _ReleaseFamilyProjection(
        "prompt_bundles", ReleaseSubjectKind.PROMPT_BUNDLE, PromptBundleRelease,
        "prompt_bundle_id", "prompt_bundle_version",
        ("compiler_version", "members", "compiled_static_body_sha256"),
    ),
    _ReleaseFamilyProjection(
        "behavior_policies", ReleaseSubjectKind.BEHAVIOR_POLICY,
        BehaviorPolicyRelease, "policy_id", "policy_version",
        ("policy_schema_ref", "policy_schema_sha256", "policy_sha256"),
    ),
    _ReleaseFamilyProjection(
        "evaluation_policies", ReleaseSubjectKind.EVALUATION_POLICY,
        EvaluationPolicyRelease, "policy_id", "policy_version",
        ("policy_schema_ref", "policy_schema_sha256", "policy_sha256"),
    ),
    _ReleaseFamilyProjection(
        "retry_policies", ReleaseSubjectKind.RETRY_POLICY, RetryPolicyRelease,
        "policy_id", "policy_version",
        ("policy_schema_ref", "policy_schema_sha256", "policy_sha256"),
    ),
    _ReleaseFamilyProjection(
        "execution_variant_policies", ReleaseSubjectKind.EXECUTION_VARIANT_POLICY,
        ExecutionVariantPolicyRelease, "policy_id", "policy_version",
        ("policy_schema_ref", "policy_schema_sha256", "policy_sha256"),
    ),
    _ReleaseFamilyProjection(
        "execution_profiles", ReleaseSubjectKind.EXECUTION_PROFILE,
        ExecutionProfileRelease, "execution_profile_id", "execution_profile_version",
        (
            "executor_adapter_id", "executor_adapter_revision", "transport_kind",
            "provider_id", "model_id", "reasoning_profile", "execution_mode",
            "semantic_input_delivery_mode", "attempt_workspace_policy",
            "gateway_access_reasons", "output_constraint_mode", "tool_policy",
            "network_policy", "timeout_seconds",
        ),
    ),
    _ReleaseFamilyProjection(
        "modules", ReleaseSubjectKind.RUNTIME_MODULE, RuntimeModuleRelease,
        "module_id", "module_version",
        (
            "module_kind", "owner_contract_ref", "owner_contract_sha256",
            "executable_ref", "executable_sha256", "input_schema_ref",
            "input_schema_sha256", "output_schema_ref", "output_schema_sha256",
            "prompt_bundle_ref", "prompt_bundle_sha256", "declared_operation_ids",
            "behavior_policy_ref", "behavior_policy_sha256",
            "evaluation_policy_ref", "evaluation_policy_sha256",
            "retry_policy_ref", "retry_policy_sha256", "compatible_transport_kinds",
            "entry_policy", "output_resolution_policy",
        ),
    ),
    _ReleaseFamilyProjection(
        "workflows", ReleaseSubjectKind.WORKFLOW, WorkflowRelease,
        "workflow_id", "workflow_version",
        (
            "workflow_contract_version", "owner_contract_ref",
            "owner_contract_sha256", "graph_ref", "graph_sha256",
            "initial_node_id", "node_count", "edge_count", "parallel_group_count",
            "authorization_manifest_ref", "authorization_manifest_sha256",
            "execution_release_ref", "execution_release_sha256",
        ),
    ),
)

_SNAPSHOT_FIELDS = tuple(family.snapshot_field for family in _RELEASE_FAMILIES) + (
    "admissions", "active_release_refs",
)
_INVENTORY_FIELDS = (
    "schema_version", "selection_id", "selection_sha256",
    "projected_closure_sha256", *_SNAPSHOT_FIELDS,
)
_COMMON_RELEASE_ROW_FIELDS = (
    "subject_kind", "subject_id", "version", "release_ref", "release_sha256",
    "latest_admission_state", "active",
)
_ADMISSION_ROW_FIELDS = (
    "admission_id", "subject_kind", "subject_id", "release_ref",
    "release_sha256", "state", "evidence_members", "admission_intent_sha256",
    "recorded_at_utc",
    "admission_sha256",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _project_value(value: Any) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if type(value) is ReleaseMember:
        return value.as_dict()
    if type(value) is tuple:
        return [_project_value(item) for item in value]
    if value is None or type(value) in {str, int, bool}:
        return value
    raise TypeError(
        f"unsupported Release inventory metadata type: {type(value).__name__}"
    )


def _family_extension_value(release: Any, field_name: str) -> Any:
    if field_name == "node_count":
        return len(release.nodes)
    if field_name == "edge_count":
        return len(release.edges)
    if field_name == "parallel_group_count":
        return len(release.parallel_groups)
    return _project_value(getattr(release, field_name))


def _validate_snapshot_shape(snapshot: RuntimeReleaseRegistrySnapshot) -> None:
    if type(snapshot) is not RuntimeReleaseRegistrySnapshot:
        raise ValueError("snapshot must be a RuntimeReleaseRegistrySnapshot")
    actual_fields = tuple(field.name for field in fields(RuntimeReleaseRegistrySnapshot))
    if actual_fields != _SNAPSHOT_FIELDS:
        raise RuntimeError("RuntimeReleaseRegistrySnapshot projection fence mismatch")
    for family in _RELEASE_FAMILIES:
        records = getattr(snapshot, family.snapshot_field)
        if type(records) is not tuple:
            raise ValueError(f"snapshot {family.snapshot_field} must be a tuple")
        for record in records:
            if type(record) is not family.record_type:
                raise ValueError(
                    f"snapshot {family.snapshot_field} contains an invalid release"
                )
            record.validate()
    if type(snapshot.admissions) is not tuple:
        raise ValueError("snapshot admissions must be a tuple")
    for admission in snapshot.admissions:
        if type(admission) is not ReleaseAdmissionRecord:
            raise ValueError("snapshot admissions contain an invalid record")
        admission.validate()
    if not isinstance(snapshot.active_release_refs, Mapping):
        raise ValueError("snapshot active_release_refs must be a mapping")
    if any(
        type(key) is not str or type(value) is not str
        for key, value in snapshot.active_release_refs.items()
    ):
        raise ValueError("snapshot active_release_refs must map strings to strings")


def _selected_release_records(
    snapshot: RuntimeReleaseRegistrySnapshot,
    selection: ReleaseInventorySelection,
) -> dict[str, tuple[_ReleaseFamilyProjection, Any]]:
    candidates_by_ref: dict[str, list[tuple[_ReleaseFamilyProjection, Any]]] = {}
    for family in _RELEASE_FAMILIES:
        for release in getattr(snapshot, family.snapshot_field):
            if release.release_ref in selection.selected_release_refs:
                candidates_by_ref.setdefault(release.release_ref, []).append(
                    (family, release)
                )
    selected: dict[str, tuple[_ReleaseFamilyProjection, Any]] = {}
    for release_ref in selection.selected_release_refs:
        candidates = candidates_by_ref.get(release_ref, [])
        if not candidates:
            raise ValueError(f"unknown selected release_ref: {release_ref}")
        if len(candidates) != 1:
            raise ValueError(f"ambiguous selected release_ref: {release_ref}")
        selected[release_ref] = candidates[0]
    return selected


def _selected_admissions(
    snapshot: RuntimeReleaseRegistrySnapshot,
    selected: Mapping[str, tuple[_ReleaseFamilyProjection, Any]],
) -> dict[tuple[ReleaseSubjectKind, str], tuple[ReleaseAdmissionRecord, ...]]:
    grouped: dict[
        tuple[ReleaseSubjectKind, str], list[ReleaseAdmissionRecord]
    ] = {}
    for admission in snapshot.admissions:
        selected_release = selected.get(admission.release_ref)
        if selected_release is None:
            continue
        family, release = selected_release
        if admission.subject_kind is not family.subject_kind:
            raise ValueError(
                "selected admission subject_kind does not match its Release family"
            )
        if admission.subject_id != getattr(release, family.subject_id_field):
            raise ValueError("selected admission subject_id does not match its Release")
        if admission.release_sha256 != release.release_sha256:
            raise ValueError("selected admission hash does not match its Release")
        grouped.setdefault(
            (admission.subject_kind, admission.release_ref), []
        ).append(admission)
    return {key: tuple(records) for key, records in grouped.items()}


def _active_pointer_projection(
    snapshot: RuntimeReleaseRegistrySnapshot,
    selected: Mapping[str, tuple[_ReleaseFamilyProjection, Any]],
) -> dict[str, str]:
    expected_keys = {
        release_ref: (
            f"{family.subject_kind.value}:"
            f"{getattr(release, family.subject_id_field)}"
        )
        for release_ref, (family, release) in selected.items()
    }
    projected: dict[str, str] = {}
    for key, release_ref in snapshot.active_release_refs.items():
        if release_ref not in selected:
            continue
        if key != expected_keys[release_ref]:
            raise ValueError("selected active pointer does not match its Release identity")
        projected[key] = release_ref
    return {key: projected[key] for key in sorted(projected)}


def _latest_selected_admission_state(
    subject_kind: ReleaseSubjectKind,
    release_ref: str,
    admissions: Mapping[
        tuple[ReleaseSubjectKind, str], tuple[ReleaseAdmissionRecord, ...]
    ],
) -> str | None:
    matching = admissions.get((subject_kind, release_ref), ())
    if not matching:
        return None
    lifecycle_rank = {
        state: rank for rank, state in enumerate(ReleaseAdmissionState)
    }
    return max(matching, key=lambda row: lifecycle_rank[row.state]).state.value


def _release_row(
    family: _ReleaseFamilyProjection,
    release: Any,
    admissions: Mapping[
        tuple[ReleaseSubjectKind, str], tuple[ReleaseAdmissionRecord, ...]
    ],
    active_release_refs: Mapping[str, str],
) -> dict[str, Any]:
    subject_id = getattr(release, family.subject_id_field)
    active_key = f"{family.subject_kind.value}:{subject_id}"
    row = {
        "subject_kind": family.subject_kind.value,
        "subject_id": subject_id,
        "version": getattr(release, family.version_field),
        "release_ref": release.release_ref,
        "release_sha256": release.release_sha256,
        "latest_admission_state": _latest_selected_admission_state(
            family.subject_kind, release.release_ref, admissions
        ),
        "active": active_release_refs.get(active_key) == release.release_ref,
    }
    row.update(
        {
            field_name: _family_extension_value(release, field_name)
            for field_name in family.extension_fields
        }
    )
    return row


def _admission_row(admission: ReleaseAdmissionRecord) -> dict[str, Any]:
    return {
        "admission_id": admission.admission_id,
        "subject_kind": admission.subject_kind.value,
        "subject_id": admission.subject_id,
        "release_ref": admission.release_ref,
        "release_sha256": admission.release_sha256,
        "state": admission.state.value,
        "evidence_members": [
            member.as_dict() for member in admission.evidence_members
        ],
        "admission_intent_sha256": admission.admission_intent_sha256,
        "recorded_at_utc": admission.recorded_at_utc,
        "admission_sha256": admission.admission_sha256,
    }


def _closure_payload(inventory: Mapping[str, Any]) -> dict[str, Any]:
    return {field_name: inventory[field_name] for field_name in _SNAPSHOT_FIELDS}


def build_runtime_release_inventory(
    *,
    snapshot: RuntimeReleaseRegistrySnapshot,
    selection: ReleaseInventorySelection,
) -> dict[str, Any]:
    """Project selected, content-free metadata from one immutable snapshot."""

    _validate_snapshot_shape(snapshot)
    if type(selection) is not ReleaseInventorySelection:
        raise ValueError("selection must be a ReleaseInventorySelection")
    selection.validate()
    selected = _selected_release_records(snapshot, selection)
    admissions_by_release = _selected_admissions(snapshot, selected)
    active_release_refs = _active_pointer_projection(snapshot, selected)

    inventory: dict[str, Any] = {
        "schema_version": RELEASE_INVENTORY_SCHEMA_VERSION,
        "selection_id": selection.selection_id,
        "selection_sha256": selection.selection_sha256,
        "projected_closure_sha256": "",
    }
    for family in _RELEASE_FAMILIES:
        releases = [
            release
            for selected_family, release in selected.values()
            if selected_family is family
        ]
        inventory[family.snapshot_field] = [
            _release_row(
                family, release, admissions_by_release, active_release_refs
            )
            for release in sorted(releases, key=lambda item: item.release_ref)
        ]

    selected_admissions = [
        admission
        for records in admissions_by_release.values()
        for admission in records
    ]
    inventory["admissions"] = [
        _admission_row(admission)
        for admission in sorted(
            selected_admissions,
            key=lambda row: (
                row.release_ref, row.recorded_at_utc, row.admission_id
            ),
        )
    ]
    inventory["active_release_refs"] = active_release_refs
    inventory["projected_closure_sha256"] = _canonical_sha256(
        _closure_payload(inventory)
    )
    _validate_runtime_release_inventory(inventory)
    return inventory


def _validate_runtime_release_inventory(inventory: Mapping[str, Any]) -> None:
    if type(inventory) is not dict or set(inventory) != set(_INVENTORY_FIELDS):
        raise ValueError("Runtime Release inventory fields are invalid")
    if inventory["schema_version"] != RELEASE_INVENTORY_SCHEMA_VERSION:
        raise ValueError("unsupported Runtime Release inventory schema")

    observed_refs: list[str] = []
    release_rows_by_ref: dict[str, dict[str, Any]] = {}
    for family in _RELEASE_FAMILIES:
        rows = inventory[family.snapshot_field]
        if type(rows) is not list:
            raise ValueError(f"inventory {family.snapshot_field} must be an array")
        release_refs = [row.get("release_ref") for row in rows]
        if release_refs != sorted(release_refs):
            raise ValueError(f"inventory {family.snapshot_field} is not sorted")
        expected_fields = (*_COMMON_RELEASE_ROW_FIELDS, *family.extension_fields)
        for row in rows:
            if type(row) is not dict or set(row) != set(expected_fields):
                raise ValueError(
                    f"inventory {family.snapshot_field} row fields are invalid"
                )
            if row["subject_kind"] != family.subject_kind.value:
                raise ValueError("inventory Release row subject_kind is invalid")
            validate_sha256("release_sha256", row["release_sha256"])
            if row["latest_admission_state"] is not None:
                ReleaseAdmissionState(row["latest_admission_state"])
            if type(row["active"]) is not bool:
                raise ValueError("inventory Release row active must be a bool")
            observed_refs.append(row["release_ref"])
            release_rows_by_ref[row["release_ref"]] = row

    selection = ReleaseInventorySelection.from_dict(
        {
            "selection_id": inventory["selection_id"],
            "selected_release_refs": sorted(observed_refs),
            "selection_sha256": inventory["selection_sha256"],
        }
    )
    selected_refs = set(selection.selected_release_refs)
    if tuple(sorted(observed_refs)) != selection.selected_release_refs:
        raise ValueError("inventory Release rows do not close the selection")
    if len(observed_refs) != len(selected_refs):
        raise ValueError("inventory Release refs are ambiguous")

    admission_rows = inventory["admissions"]
    if type(admission_rows) is not list:
        raise ValueError("inventory admissions must be an array")
    if admission_rows != sorted(
        admission_rows,
        key=lambda row: (
            row.get("release_ref"),
            row.get("recorded_at_utc"),
            row.get("admission_id"),
        ),
    ):
        raise ValueError("inventory admissions are not sorted")
    for row in admission_rows:
        if type(row) is not dict or set(row) != set(_ADMISSION_ROW_FIELDS):
            raise ValueError("inventory admission row fields are invalid")
        if row["release_ref"] not in selected_refs:
            raise ValueError("inventory admission falls outside the selection")
        ReleaseSubjectKind(row["subject_kind"])
        ReleaseAdmissionState(row["state"])
        validate_sha256("release_sha256", row["release_sha256"])
        validate_sha256("admission_sha256", row["admission_sha256"])
        release_row = release_rows_by_ref[row["release_ref"]]
        if (
            row["subject_kind"] != release_row["subject_kind"]
            or row["subject_id"] != release_row["subject_id"]
            or row["release_sha256"] != release_row["release_sha256"]
        ):
            raise ValueError("inventory admission identity does not match its Release")

    lifecycle_rank = {
        state.value: rank for rank, state in enumerate(ReleaseAdmissionState)
    }
    for release_ref, release_row in release_rows_by_ref.items():
        states = [
            row["state"]
            for row in admission_rows
            if row["subject_kind"] == release_row["subject_kind"]
            and row["release_ref"] == release_ref
        ]
        latest_state = max(states, key=lifecycle_rank.__getitem__) if states else None
        if release_row["latest_admission_state"] != latest_state:
            raise ValueError("inventory latest admission state is inconsistent")

    active_release_refs = inventory["active_release_refs"]
    if type(active_release_refs) is not dict:
        raise ValueError("inventory active_release_refs must be an object")
    if list(active_release_refs) != sorted(active_release_refs):
        raise ValueError("inventory active_release_refs are not sorted")
    if any(ref not in selected_refs for ref in active_release_refs.values()):
        raise ValueError("inventory active pointer falls outside the selection")
    for key, release_ref in active_release_refs.items():
        release_row = release_rows_by_ref[release_ref]
        expected_key = (
            f"{release_row['subject_kind']}:{release_row['subject_id']}"
        )
        if key != expected_key:
            raise ValueError("inventory active pointer identity is inconsistent")
    for release_ref, release_row in release_rows_by_ref.items():
        expected_key = (
            f"{release_row['subject_kind']}:{release_row['subject_id']}"
        )
        if release_row["active"] != (
            active_release_refs.get(expected_key) == release_ref
        ):
            raise ValueError("inventory active Release state is inconsistent")
    validate_sha256(
        "projected_closure_sha256", inventory["projected_closure_sha256"]
    )
    if inventory["projected_closure_sha256"] != _canonical_sha256(
        _closure_payload(inventory)
    ):
        raise ValueError("Runtime Release inventory closure hash mismatch")


def render_runtime_release_markdown(inventory: Mapping[str, Any]) -> str:
    """Render one validated Release inventory without reading another source."""

    _validate_runtime_release_inventory(inventory)
    labels = {
        "schema_assets": "Schema Assets",
        "prompt_components": "Prompt Components",
        "prompt_bundles": "Prompt Bundles",
        "behavior_policies": "Behavior Policies",
        "evaluation_policies": "Evaluation Policies",
        "retry_policies": "Retry Policies",
        "execution_variant_policies": "Execution Variant Policies",
        "execution_profiles": "Execution Profiles",
        "modules": "Runtime Modules",
        "workflows": "Workflows",
    }
    lines = [
        "# Agent Runtime Release Inventory",
        "",
        (
            "> Deterministic, selection-bounded metadata projection; not "
            "Registry or authorization authority."
        ),
        "",
        f"- Selection: `{inventory['selection_id']}`",
        f"- Selection SHA-256: `{inventory['selection_sha256']}`",
        f"- Projected closure SHA-256: `{inventory['projected_closure_sha256']}`",
        "",
        "## Summary",
        "",
        "| Release kind | Count |",
        "| --- | ---: |",
    ]
    for family in _RELEASE_FAMILIES:
        lines.append(
            f"| {labels[family.snapshot_field]} | "
            f"`{len(inventory[family.snapshot_field])}` |"
        )
    lines.extend(
        [
            f"| Admission records | `{len(inventory['admissions'])}` |",
            f"| Active pointers | `{len(inventory['active_release_refs'])}` |",
        ]
    )
    for family in _RELEASE_FAMILIES:
        lines.extend(
            [
                "",
                f"## {labels[family.snapshot_field]}",
                "",
                "| ID | Version | Admission | Active | Release |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in inventory[family.snapshot_field]:
            admission_state = row["latest_admission_state"] or "unadmitted"
            active = "yes" if row["active"] else "no"
            lines.append(
                f"| `{row['subject_id']}` | `{row['version']}` | "
                f"`{admission_state}` | `{active}` | `{row['release_ref']}` |"
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            (
                "Prompt text, Schema bodies, Policy documents, owner-contract "
                "bodies, task inputs, outputs, credentials, and provider "
                "transcripts are structurally absent."
            ),
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "RELEASE_INVENTORY_SCHEMA_VERSION",
    "build_runtime_release_inventory",
    "render_runtime_release_markdown",
]
