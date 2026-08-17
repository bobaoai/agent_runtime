"""Workflow-bound predecessor ledger records pending Module-model migration.

The file still owns the current durable Workflow execution ledger and several
predecessor authorization batches. It is not the canonical public Module Run,
Variant, or Attempt schema. Those records converge under
``agent_runtime.contracts.execution`` before standalone release. Domain Drivers
request work and return typed Outcomes; they do not construct or commit the
final canonical record family.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, TypeAlias, TypeVar

from .ledger_lineage_definition import ModuleOutputResolutionRecord
from ..foundation.foundation_contract_validation import (
    validate_usd_amount,
    validate_utc_timestamp,
)
from .registry_workflow_definition import ModuleOutcome


_ID = re.compile(r"^[a-z][a-z0-9_.-]{2,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_REF = re.compile(r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$")
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")


def canonical_json(value: Any) -> str:
    """Serialize one record value into the runtime's stable JSON form."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 of UTF-8 text."""

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    """Return the SHA-256 of canonical JSON."""

    return sha256_text(canonical_json(value))


def stable_runtime_id(prefix: str, *parts: str) -> str:
    """Build a deterministic runtime identity from explicit parent parts."""

    clean_prefix = str(prefix).strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", clean_prefix):
        raise ValueError(f"invalid runtime id prefix: {prefix!r}")
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{clean_prefix}_{digest[:24]}"


def attempt_output_bundle_sha256(
    outputs: tuple["ExecutionOutputRef", ...],
) -> str:
    """Hash ordered Runtime output handles, types, hashes, and schemas."""

    for output in outputs:
        output.validate()
    return sha256_json(
        [
            {
                "output_ref": output.output_ref,
                "output_sha256": output.output_sha256,
                "output_type_id": output.output_type_id,
                "schema_version": output.schema_version,
                "media_type": output.media_type,
                "logical_name": output.logical_name,
            }
            for output in outputs
        ]
    )


def _validate_id(label: str, value: str) -> None:
    if not _ID.fullmatch(str(value)):
        raise ValueError(f"invalid {label}: {value!r}")


def _validate_sha(label: str, value: str) -> None:
    if not _SHA256.fullmatch(str(value)):
        raise ValueError(f"invalid {label}: {value!r}")


def _validate_ref(label: str, value: str) -> None:
    if not _OPAQUE_REF.fullmatch(str(value)) or str(value).startswith("data:"):
        raise ValueError(f"invalid {label}: scheme-prefixed opaque ref required")
    lowered = str(value).lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"invalid {label}: embedded secret is forbidden")


def _validate_unique_refs(label: str, refs: tuple[str, ...]) -> None:
    if len(refs) != len(set(refs)):
        raise ValueError(f"{label} must be unique")
    for value in refs:
        _validate_ref(label, value)


def _validate_utc(label: str, value: str) -> None:
    # Delegates to the single canonical-timestamp rule so a non-canonical
    # instant (space separator, missing seconds) can never enter an immutable
    # record and break lexicographic ordering or replay string equality.
    validate_utc_timestamp(label, value)


@dataclass(frozen=True)
class ModelUsageRecord:
    """Trustworthy provider usage fields for one model Attempt."""

    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    estimated_cost_usd: str | None
    provider_charge_usd: str | None = None

    def validate(self) -> None:
        """Reject negative usage while preserving unknown values as null."""

        for label, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_read_tokens", self.cache_read_tokens),
            ("cache_creation_tokens", self.cache_creation_tokens),
        ):
            if value is not None and (not isinstance(value, int) or value < 0):
                raise ValueError(f"{label} must be a non-negative integer or null")
        for label, value in (
            ("estimated_cost_usd", self.estimated_cost_usd),
            ("provider_charge_usd", self.provider_charge_usd),
        ):
            if value is not None:
                validate_usd_amount(label, value)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready usage record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class LegacyRuntimeArtifactRecord:
    """Compatibility projection retired with the monolithic candidate oracle."""

    artifact_id: str
    artifact_kind: str
    schema_version: str
    sha256: str
    byte_size: int
    media_type: str
    data_class: str
    artifact_ref: str
    producing_attempt_id: str | None

    def validate(self) -> None:
        """Validate artifact identity, hash, boundary, and producer reference."""

        _validate_id("artifact_id", self.artifact_id)
        _validate_id("artifact_kind", self.artifact_kind)
        _validate_sha("artifact sha256", self.sha256)
        if not self.schema_version:
            raise ValueError("artifact schema_version is required")
        if self.byte_size < 0:
            raise ValueError("artifact byte_size must be non-negative")
        if not self.media_type or not self.artifact_ref:
            raise ValueError("artifact media_type and artifact_ref are required")
        if self.data_class not in {"S0", "S1", "S2", "S3"}:
            raise ValueError(f"invalid artifact data_class: {self.data_class!r}")
        if self.producing_attempt_id is not None:
            _validate_id("producing_attempt_id", self.producing_attempt_id)

    def as_dict(self) -> dict[str, Any]:
        """Return the deterministic compatibility projection."""

        self.validate()
        return {**asdict(self), "projection_kind": "compatibility_projection"}


@dataclass(frozen=True)
class LegacyModuleRunRecord:
    """Compatibility projection retired with the monolithic candidate oracle."""

    workflow_execution_id: str
    dispatch_id: str
    module_run_id: str
    module_key: str
    round_index: int
    input_artifact_ids: tuple[str, ...]
    input_closure_sha256: str
    created_at_utc: str

    def validate(self) -> None:
        """Validate the predecessor shape without admitting it to Runtime ledger."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("module_run_id", self.module_run_id)
        _validate_id("module_key", self.module_key)
        if self.round_index < 0:
            raise ValueError("round_index must be non-negative")
        if not self.input_artifact_ids:
            raise ValueError("Module Run requires at least one input artifact")
        if len(set(self.input_artifact_ids)) != len(self.input_artifact_ids):
            raise ValueError("Module Run input artifact ids must be unique")
        for artifact_id in self.input_artifact_ids:
            _validate_id("input artifact id", artifact_id)
        _validate_sha("input_closure_sha256", self.input_closure_sha256)
        _validate_utc("created_at_utc", self.created_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the deterministic compatibility projection."""

        self.validate()
        payload = asdict(self)
        payload["input_artifact_ids"] = list(self.input_artifact_ids)
        payload["projection_kind"] = "compatibility_projection"
        return payload


@dataclass(frozen=True)
class WorkflowModuleRunRecord:
    """One opaque graph state with a frozen Cell-local input-ref closure."""

    workflow_execution_id: str
    module_run_id: str
    state_id: str
    module_id: str
    input_refs: tuple[str, ...]
    input_closure_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate opaque state/module IDs and immutable input refs."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("state_id", self.state_id),
            ("module_id", self.module_id),
        ):
            _validate_id(label, value)
        if not self.input_refs:
            raise ValueError("Module Run requires at least one input ref")
        _validate_unique_refs("input_refs", self.input_refs)
        _validate_sha("input_closure_sha256", self.input_closure_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-ready Module Run record."""

        self.validate()
        payload = asdict(self)
        payload["input_refs"] = list(self.input_refs)
        return payload


@dataclass(frozen=True)
class LegacyModuleExecutionVariantRecord:
    """Compatibility projection retired with the monolithic candidate oracle."""

    module_run_id: str
    variant_id: str
    module_id: str
    agent_execution_adapter_id: str
    execution_profile_id: str
    model_id: str
    reasoning_profile: str
    prompt_sha256: str
    static_module_sha256: str
    input_package_sha256: str
    entitlement_snapshot_hash: str
    agent_execution_adapter_revision: str
    runtime_version: str
    tool_policy: tuple[str, ...]
    context_mode: str
    output_schema_sha256: str
    timeout_seconds: int
    max_attempts: int
    execution_profile_sha256: str

    def validate(self) -> None:
        """Validate the predecessor shape without ledger admission."""

        _validate_variant_fields(self)

    def as_dict(self) -> dict[str, Any]:
        """Return the deterministic compatibility projection."""

        self.validate()
        payload = asdict(self)
        payload["tool_policy"] = list(self.tool_policy)
        payload["projection_kind"] = "compatibility_projection"
        return payload


@dataclass(frozen=True)
class WorkflowModuleExecutionVariantRecord:
    """Behavior-complete immutable configuration under one Module Run."""

    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    module_id: str
    agent_execution_adapter_id: str
    execution_profile_id: str
    model_id: str
    reasoning_profile: str
    prompt_sha256: str
    static_module_sha256: str
    input_closure_sha256: str
    entitlement_snapshot_hash: str
    agent_execution_adapter_revision: str
    runtime_version: str
    tool_policy: tuple[str, ...]
    context_mode: str
    output_schema_sha256: str
    timeout_seconds: int
    max_attempts: int
    execution_profile_sha256: str
    recorded_at_utc: str
    prompt_envelope_ref: str | None = None

    def validate(self) -> None:
        """Validate complete Variant identity and behavior-affecting hashes."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_variant_fields(self)
        if self.prompt_envelope_ref is not None:
            _validate_ref("prompt_envelope_ref", self.prompt_envelope_ref)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-ready Variant record."""

        self.validate()
        payload = asdict(self)
        payload["tool_policy"] = list(self.tool_policy)
        return payload


def _validate_variant_fields(record: Any) -> None:
    for label, value in (
        ("module_run_id", record.module_run_id),
        ("variant_id", record.variant_id),
        ("module_id", record.module_id),
        ("agent_execution_adapter_id", record.agent_execution_adapter_id),
        ("execution_profile_id", record.execution_profile_id),
    ):
        _validate_id(label, value)
    if not record.model_id or not record.reasoning_profile:
        raise ValueError("Variant model and reasoning profile are required")
    if not record.agent_execution_adapter_revision or not record.runtime_version:
        raise ValueError("Variant adapter revision and runtime version are required")
    if not record.tool_policy or len(set(record.tool_policy)) != len(record.tool_policy):
        raise ValueError("Variant tool_policy must be non-empty and unique")
    if not record.context_mode:
        raise ValueError("Variant context_mode is required")
    if record.timeout_seconds < 1 or record.max_attempts < 1:
        raise ValueError("Variant timeout_seconds and max_attempts must be positive")
    input_hash_label = (
        "input_package_sha256"
        if isinstance(record, LegacyModuleExecutionVariantRecord)
        else "input_closure_sha256"
    )
    for label, value in (
        ("prompt_sha256", record.prompt_sha256),
        ("static_module_sha256", record.static_module_sha256),
        (input_hash_label, getattr(record, input_hash_label)),
        ("entitlement_snapshot_hash", record.entitlement_snapshot_hash),
        ("output_schema_sha256", record.output_schema_sha256),
        ("execution_profile_sha256", record.execution_profile_sha256),
    ):
        _validate_sha(label, value)


@dataclass(frozen=True)
class LegacyAttemptRecord:
    """Compatibility projection retired with the monolithic candidate oracle."""

    variant_id: str
    attempt_id: str
    attempt_ordinal: int
    status: str
    started_at_utc: str
    ended_at_utc: str
    trace_run_id: str
    output_artifact_id: str | None
    terminal_reason: str | None
    usage: ModelUsageRecord

    def validate(self) -> None:
        """Validate the predecessor shape without ledger admission."""

        _validate_id("variant_id", self.variant_id)
        _validate_id("attempt_id", self.attempt_id)
        _validate_id("trace_run_id", self.trace_run_id)
        if self.attempt_ordinal < 1:
            raise ValueError("attempt_ordinal must start at 1")
        if self.status not in {"completed", "failed", "blocked", "cancelled"}:
            raise ValueError(f"invalid Attempt status: {self.status!r}")
        _validate_utc("started_at_utc", self.started_at_utc)
        _validate_utc("ended_at_utc", self.ended_at_utc)
        if self.output_artifact_id is not None:
            _validate_id("output_artifact_id", self.output_artifact_id)
        self.usage.validate()

    def as_dict(self) -> dict[str, Any]:
        """Return the deterministic compatibility projection."""

        self.validate()
        payload = asdict(self)
        payload["usage"] = self.usage.as_dict()
        payload["projection_kind"] = "compatibility_projection"
        return payload


@dataclass(frozen=True)
class WorkflowAttemptStartedRecord:
    """Durable claim boundary recorded before an external operation starts."""

    workflow_execution_id: str
    dispatch_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    parent_attempt_id: str | None
    attempt_ordinal: int
    trace_id: str
    request_sha256: str
    claim_token_hash: str
    input_closure_sha256: str
    execution_profile_sha256: str
    entitlement_snapshot_hash: str
    timeout_seconds: int
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate immutable lineage, request identity, claim, and timeout."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("trace_id", self.trace_id),
        ):
            _validate_id(label, value)
        if self.parent_attempt_id is not None:
            _validate_id("parent_attempt_id", self.parent_attempt_id)
        if self.attempt_ordinal < 1:
            raise ValueError("attempt_ordinal must start at 1")
        for label, value in (
            ("request_sha256", self.request_sha256),
            ("claim_token_hash", self.claim_token_hash),
            ("input_closure_sha256", self.input_closure_sha256),
            ("execution_profile_sha256", self.execution_profile_sha256),
            ("entitlement_snapshot_hash", self.entitlement_snapshot_hash),
        ):
            _validate_sha(label, value)
        if not isinstance(self.timeout_seconds, int) or not (
            1 <= self.timeout_seconds <= 86_400
        ):
            raise ValueError("timeout_seconds must be between 1 and 86400")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def deadline_at(self) -> datetime:
        """Derive the execution deadline without adding another event timestamp."""

        self.validate()
        start = datetime.fromisoformat(self.recorded_at_utc.replace("Z", "+00:00"))
        return start + timedelta(seconds=self.timeout_seconds)

    def lease_expired_at(self, observed_at: datetime) -> bool:
        """Report whether the lease is expired at one observed instant.

        The boundary rule — ``observed_at == deadline`` counts as expired — is
        owned here so the Inspector projection and Attempt recovery can never
        disagree about the same instant.
        """

        return observed_at >= self.deadline_at()

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-ready Attempt start record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class WorkflowAttemptRecord:
    """One immutable invocation result with separate child usage/call rows."""

    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    parent_attempt_id: str | None
    attempt_ordinal: int
    status: str
    period_start_at_utc: str
    period_end_at_utc: str
    recorded_at_utc: str
    trace_id: str
    execution_output_refs: tuple[str, ...]
    failure_class: str | None

    def validate(self) -> None:
        """Validate complete lineage, interval, outputs, and infrastructure status."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("trace_id", self.trace_id),
        ):
            _validate_id(label, value)
        if self.parent_attempt_id is not None:
            _validate_id("parent_attempt_id", self.parent_attempt_id)
        if self.attempt_ordinal < 1:
            raise ValueError("attempt_ordinal must start at 1")
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid infrastructure Attempt status: {self.status!r}")
        _validate_utc("period_start_at_utc", self.period_start_at_utc)
        _validate_utc("period_end_at_utc", self.period_end_at_utc)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)
        period_start = datetime.fromisoformat(
            self.period_start_at_utc.replace("Z", "+00:00")
        )
        period_end = datetime.fromisoformat(
            self.period_end_at_utc.replace("Z", "+00:00")
        )
        if period_end < period_start:
            raise ValueError("Attempt period_end_at_utc precedes period_start_at_utc")
        _validate_unique_refs("execution_output_refs", self.execution_output_refs)
        if self.failure_class is not None:
            _validate_id("failure_class", self.failure_class)

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-ready Attempt record."""

        self.validate()
        payload = asdict(self)
        payload["execution_output_refs"] = list(self.execution_output_refs)
        return payload


@dataclass(frozen=True)
class InvocationCommitRecord:
    """Atomic provider-result commit that can be reused before Outcome commit."""

    invocation_commit_id: str
    workflow_execution_id: str
    dispatch_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    request_sha256: str
    attempt_output_bundle_sha256: str
    terminal_status: str
    context_disposition_id: str
    commit_transaction_id: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate result identity without carrying provider content."""

        for label, value in (
            ("invocation_commit_id", self.invocation_commit_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("context_disposition_id", self.context_disposition_id),
            ("commit_transaction_id", self.commit_transaction_id),
        ):
            _validate_id(label, value)
        _validate_sha("request_sha256", self.request_sha256)
        _validate_sha(
            "attempt_output_bundle_sha256",
            self.attempt_output_bundle_sha256,
        )
        if self.terminal_status not in {"completed", "failed", "cancelled"}:
            raise ValueError(f"invalid invocation terminal status: {self.terminal_status!r}")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a content-free invocation commit."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class StaleOutputRecord:
    """Quarantine lineage for an output rejected by compare-and-commit."""

    stale_output_id: str
    workflow_execution_id: str
    dispatch_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    quarantine_ref: str
    quarantine_sha256: str
    reason_code: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate bounded stale-result metadata and quarantine binding."""

        for label, value in (
            ("stale_output_id", self.stale_output_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("reason_code", self.reason_code),
        ):
            _validate_id(label, value)
        _validate_ref("quarantine_ref", self.quarantine_ref)
        _validate_sha("quarantine_sha256", self.quarantine_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a content-free stale-output record."""

        self.validate()
        return asdict(self)


ATTEMPT_ORPHAN_REASON_CODES = frozenset(
    {"attempt_lease_expired", "worker_lost", "operator_requested"}
)
ATTEMPT_ORPHAN_CONTEXT_DISPOSITION_IDS = frozenset({"invalidate", "retain"})


@dataclass(frozen=True)
class AttemptOrphanedRecord:
    """Bounded recovery disposition for a started Attempt with no result commit."""

    orphaned_record_id: str
    workflow_execution_id: str
    dispatch_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    reason_code: str
    context_disposition_id: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate orphan lineage without provider exception or output content."""

        for label, value in (
            ("orphaned_record_id", self.orphaned_record_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
        ):
            _validate_id(label, value)
        # Both vocabularies are load-bearing for recovery replay equality, so
        # membership is pinned contract-side exactly like failure_class.
        if self.reason_code not in ATTEMPT_ORPHAN_REASON_CODES:
            raise ValueError(
                "orphan reason_code is outside the contract vocabulary"
            )
        if (
            self.context_disposition_id
            not in ATTEMPT_ORPHAN_CONTEXT_DISPOSITION_IDS
        ):
            raise ValueError(
                "orphan context_disposition_id is outside the contract vocabulary"
            )
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a content-free orphan disposition."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class WorkflowExecutionRecord:
    """Frozen workflow, Cell, graph, intake, and entitlement identity."""

    workflow_execution_id: str
    workflow_id: str
    workflow_contract_version: str
    tenant_id: str
    cell_id: str
    principal_id: str
    execution_release_ref: str
    graph_sha256: str
    runtime_execution_binding_ref: str
    runtime_execution_binding_sha256: str
    authorization_decision_ref: str
    authorization_decision_sha256: str
    execution_principal_delegation_ref: str
    execution_principal_delegation_sha256: str
    entitlement_snapshot_ref: str
    entitlement_snapshot_hash: str
    execution_input_package_refs: tuple[str, ...]
    execution_input_package_sha256: str
    recorded_at_utc: str
    execution_authorization_binding_ref: str | None = None
    execution_authorization_binding_hash: str | None = None
    execution_start_admission_ref: str | None = None
    execution_start_admission_hash: str | None = None
    workflow_release_ref: str | None = None
    workflow_release_sha256: str | None = None
    execution_release_sha256: str | None = None
    execution_profile_selection_ref: str | None = None
    execution_profile_selection_sha256: str | None = None

    def validate(self) -> None:
        """Validate frozen execution identity without inspecting domain values."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_id", self.workflow_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("principal_id", self.principal_id),
        ):
            _validate_id(label, value)
        if not self.workflow_contract_version:
            raise ValueError("workflow_contract_version is required")
        for label, value in (
            ("execution_release_ref", self.execution_release_ref),
            ("runtime_execution_binding_ref", self.runtime_execution_binding_ref),
            ("authorization_decision_ref", self.authorization_decision_ref),
            (
                "execution_principal_delegation_ref",
                self.execution_principal_delegation_ref,
            ),
            ("entitlement_snapshot_ref", self.entitlement_snapshot_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            ("graph_sha256", self.graph_sha256),
            (
                "runtime_execution_binding_sha256",
                self.runtime_execution_binding_sha256,
            ),
            ("authorization_decision_sha256", self.authorization_decision_sha256),
            (
                "execution_principal_delegation_sha256",
                self.execution_principal_delegation_sha256,
            ),
            ("entitlement_snapshot_hash", self.entitlement_snapshot_hash),
            ("execution_input_package_sha256", self.execution_input_package_sha256),
        ):
            _validate_sha(label, value)
        if not self.execution_input_package_refs:
            raise ValueError("execution input package refs are required")
        _validate_unique_refs(
            "execution_input_package_refs", self.execution_input_package_refs
        )
        _validate_utc("recorded_at_utc", self.recorded_at_utc)
        authority_values = (
            self.execution_authorization_binding_ref,
            self.execution_authorization_binding_hash,
            self.execution_start_admission_ref,
            self.execution_start_admission_hash,
        )
        if any(value is not None for value in authority_values):
            if any(value is None for value in authority_values):
                raise ValueError("Workflow Execution authorization closure is incomplete")
            _validate_ref(
                "execution_authorization_binding_ref",
                str(self.execution_authorization_binding_ref),
            )
            _validate_sha(
                "execution_authorization_binding_hash",
                str(self.execution_authorization_binding_hash),
            )
            _validate_ref(
                "execution_start_admission_ref",
                str(self.execution_start_admission_ref),
            )
            _validate_sha(
                "execution_start_admission_hash",
                str(self.execution_start_admission_hash),
            )
        target_release_values = (
            self.workflow_release_ref,
            self.workflow_release_sha256,
            self.execution_release_sha256,
            self.execution_profile_selection_ref,
            self.execution_profile_selection_sha256,
        )
        if any(value is not None for value in target_release_values):
            if any(value is None for value in target_release_values):
                raise ValueError(
                    "Workflow Execution target release closure is incomplete"
                )
            _validate_ref("workflow_release_ref", str(self.workflow_release_ref))
            _validate_sha(
                "workflow_release_sha256",
                str(self.workflow_release_sha256),
            )
            _validate_sha(
                "execution_release_sha256",
                str(self.execution_release_sha256),
            )
            _validate_ref(
                "execution_profile_selection_ref",
                str(self.execution_profile_selection_ref),
            )
            _validate_sha(
                "execution_profile_selection_sha256",
                str(self.execution_profile_selection_sha256),
            )

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready execution record."""

        self.validate()
        payload = asdict(self)
        payload["execution_input_package_refs"] = list(
            self.execution_input_package_refs
        )
        return payload


@dataclass(frozen=True)
class ExecutionAuthorizationBindingRecord:
    """Runtime binding to Product-owned execution authority; never a grant."""

    execution_authorization_binding_id: str
    workflow_execution_id: str
    runtime_execution_binding_ref: str
    runtime_execution_binding_sha256: str
    authorization_decision_ref: str
    authorization_decision_sha256: str
    execution_principal_delegation_ref: str
    execution_principal_delegation_sha256: str
    entitlement_snapshot_ref: str
    entitlement_snapshot_sha256: str
    initiating_principal_sha256: str
    binding_status: str
    recorded_at_utc: str
    binding_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("binding_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationBindingRecord":
        """Build one immutable Runtime-to-Product authority binding."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        return cls(
            **fields,
            binding_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate exact authority refs without interpreting Product policy."""

        for label, value in (
            ("execution_authorization_binding_id", self.execution_authorization_binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("binding_status", self.binding_status),
        ):
            _validate_id(label, value)
        if self.binding_status != "bound":
            raise ValueError("execution authorization binding must be bound")
        for label, value in (
            ("runtime_execution_binding_ref", self.runtime_execution_binding_ref),
            ("authorization_decision_ref", self.authorization_decision_ref),
            (
                "execution_principal_delegation_ref",
                self.execution_principal_delegation_ref,
            ),
            ("entitlement_snapshot_ref", self.entitlement_snapshot_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            ("runtime_execution_binding_sha256", self.runtime_execution_binding_sha256),
            ("authorization_decision_sha256", self.authorization_decision_sha256),
            (
                "execution_principal_delegation_sha256",
                self.execution_principal_delegation_sha256,
            ),
            ("entitlement_snapshot_sha256", self.entitlement_snapshot_sha256),
            ("initiating_principal_sha256", self.initiating_principal_sha256),
            ("binding_sha256", self.binding_sha256),
        ):
            _validate_sha(label, value)
        if self.binding_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("execution authorization binding hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready execution authority binding."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionStartAdmissionRecord:
    """Runtime admission committed under one Product authority status lease."""

    execution_start_admission_id: str
    workflow_execution_id: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    input_closure_sha256: str
    product_authority_result_ref: str
    product_authority_result_sha256: str
    authority_high_water_mark: int
    status_lease_expires_at_utc: str
    observed_authority_sha256: str
    admission_status: str
    recorded_at_utc: str
    admission_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("admission_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionStartAdmissionRecord":
        """Build one content-addressed execution-start admission."""

        provisional = cls(**fields, admission_sha256="0" * 64)
        return cls(
            **fields,
            admission_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate execution-start authority and input closure."""

        for label, value in (
            ("execution_start_admission_id", self.execution_start_admission_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("admission_status", self.admission_status),
        ):
            _validate_id(label, value)
        if self.admission_status != "admitted":
            raise ValueError("execution start admission must be admitted")
        _validate_ref(
            "execution_authorization_binding_ref",
            self.execution_authorization_binding_ref,
        )
        _validate_ref("product_authority_result_ref", self.product_authority_result_ref)
        if self.authority_high_water_mark < 0:
            raise ValueError("authority high-water mark cannot be negative")
        for label, value in (
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            ("input_closure_sha256", self.input_closure_sha256),
            ("product_authority_result_sha256", self.product_authority_result_sha256),
            ("observed_authority_sha256", self.observed_authority_sha256),
            ("admission_sha256", self.admission_sha256),
        ):
            _validate_sha(label, value)
        if self.admission_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("execution start admission hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)
        _validate_utc("status_lease_expires_at_utc", self.status_lease_expires_at_utc)
        if datetime.fromisoformat(
            self.recorded_at_utc.replace("Z", "+00:00")
        ) >= datetime.fromisoformat(
            self.status_lease_expires_at_utc.replace("Z", "+00:00")
        ):
            raise ValueError("execution start authority lease already expired")

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready execution-start admission."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ModuleDispatchAdmissionRecord:
    """Per-dispatch proof that the execution remained unfenced and authorized."""

    module_dispatch_admission_id: str
    workflow_execution_id: str
    dispatch_id: str
    state_id: str
    transition_sequence: int
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    product_authority_result_ref: str
    product_authority_result_sha256: str
    authority_high_water_mark: int
    status_lease_expires_at_utc: str
    observed_authority_sha256: str
    admission_status: str
    recorded_at_utc: str
    admission_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("admission_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "ModuleDispatchAdmissionRecord":
        """Build one content-addressed per-dispatch admission."""

        provisional = cls(**fields, admission_sha256="0" * 64)
        return cls(
            **fields,
            admission_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate dispatch identity and observed authority closure."""

        for label, value in (
            ("module_dispatch_admission_id", self.module_dispatch_admission_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("state_id", self.state_id),
            ("admission_status", self.admission_status),
        ):
            _validate_id(label, value)
        if self.transition_sequence < 0:
            raise ValueError("transition_sequence cannot be negative")
        if self.admission_status != "admitted":
            raise ValueError("Module dispatch admission must be admitted")
        _validate_ref(
            "execution_authorization_binding_ref",
            self.execution_authorization_binding_ref,
        )
        _validate_ref("product_authority_result_ref", self.product_authority_result_ref)
        if self.authority_high_water_mark < 0:
            raise ValueError("authority high-water mark cannot be negative")
        for label, value in (
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            ("product_authority_result_sha256", self.product_authority_result_sha256),
            ("observed_authority_sha256", self.observed_authority_sha256),
            ("admission_sha256", self.admission_sha256),
        ):
            _validate_sha(label, value)
        if self.admission_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("Module dispatch admission hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)
        _validate_utc("status_lease_expires_at_utc", self.status_lease_expires_at_utc)
        if datetime.fromisoformat(
            self.recorded_at_utc.replace("Z", "+00:00")
        ) >= datetime.fromisoformat(
            self.status_lease_expires_at_utc.replace("Z", "+00:00")
        ):
            raise ValueError("dispatch admission authority lease already expired")

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready dispatch admission."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionAuthorizationInvalidationRecord:
    """Sticky Runtime fence after Product authority drift or expiry is observed."""

    execution_authorization_invalidation_id: str
    workflow_execution_id: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    reason_code: str
    last_admitted_dispatch_id: str | None
    authority_evidence_ref: str
    authority_evidence_sha256: str
    detected_authority_sha256: str
    detected_at_utc: str
    invalidation_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("invalidation_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationInvalidationRecord":
        """Build one content-addressed sticky authorization fence."""

        provisional = cls(**fields, invalidation_sha256="0" * 64)
        return cls(
            **fields,
            invalidation_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate invalidation identity and detected authority evidence."""

        for label, value in (
            (
                "execution_authorization_invalidation_id",
                self.execution_authorization_invalidation_id,
            ),
            ("workflow_execution_id", self.workflow_execution_id),
            ("reason_code", self.reason_code),
        ):
            _validate_id(label, value)
        if self.last_admitted_dispatch_id is not None:
            _validate_id("last_admitted_dispatch_id", self.last_admitted_dispatch_id)
        _validate_ref(
            "execution_authorization_binding_ref",
            self.execution_authorization_binding_ref,
        )
        _validate_ref("authority_evidence_ref", self.authority_evidence_ref)
        for label, value in (
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            ("authority_evidence_sha256", self.authority_evidence_sha256),
            ("detected_authority_sha256", self.detected_authority_sha256),
            ("invalidation_sha256", self.invalidation_sha256),
        ):
            _validate_sha(label, value)
        if self.invalidation_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("execution authorization invalidation hash mismatch")
        _validate_utc("detected_at_utc", self.detected_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready authorization invalidation."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionProfileRecord:
    """Immutable provider-neutral identity for one resolved execution profile."""

    execution_profile_id: str
    module_id: str
    agent_execution_adapter_id: str
    agent_execution_adapter_revision: str
    profile_ref: str
    profile_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate opaque module, adapter, and profile identity."""

        for label, value in (
            ("execution_profile_id", self.execution_profile_id),
            ("module_id", self.module_id),
            ("agent_execution_adapter_id", self.agent_execution_adapter_id),
        ):
            _validate_id(label, value)
        if not self.agent_execution_adapter_revision:
            raise ValueError("agent_execution_adapter_revision is required")
        _validate_ref("profile_ref", self.profile_ref)
        _validate_sha("profile_sha256", self.profile_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready execution-profile record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class LegacyExecutionEntitlementSnapshot:
    """Pre-AR09 Runtime entitlement projection preserved as legacy fact only."""

    entitlement_snapshot_id: str
    workflow_execution_id: str
    tenant_id: str
    cell_id: str
    principal_id: str
    entitlement_snapshot_ref: str
    entitlement_snapshot_hash: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate frozen-scope identity and hash."""

        for label, value in (
            ("entitlement_snapshot_id", self.entitlement_snapshot_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("principal_id", self.principal_id),
        ):
            _validate_id(label, value)
        _validate_ref("entitlement_snapshot_ref", self.entitlement_snapshot_ref)
        _validate_sha("entitlement_snapshot_hash", self.entitlement_snapshot_hash)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready entitlement record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class LegacyModuleCapabilityGrant:
    """Pre-AR09 Runtime-minted grant preserved as legacy fact only."""

    grant_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    capability_id: str
    resource_id: str
    action_id: str
    entitlement_snapshot_hash: str
    idempotency_key: str
    expires_after_seconds: int
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate lineage, frozen scope, operation, and bounded lifetime."""

        for label, value in (
            ("grant_id", self.grant_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("capability_id", self.capability_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("idempotency_key", self.idempotency_key),
        ):
            _validate_id(label, value)
        _validate_sha("entitlement_snapshot_hash", self.entitlement_snapshot_hash)
        if not isinstance(self.expires_after_seconds, int) or not (
            1 <= self.expires_after_seconds <= 86_400
        ):
            raise ValueError("expires_after_seconds must be between 1 and 86400")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def expires_at(self) -> datetime:
        """Return the absolute UTC expiry derived from the compliant record time."""

        self.validate()
        start = datetime.fromisoformat(self.recorded_at_utc.replace("Z", "+00:00"))
        return start + timedelta(seconds=self.expires_after_seconds)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready grant record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionInputRef:
    """One immutable input binding admitted into a Workflow Execution.

    This is Runtime's content-free binding to an input package member or an
    independently authorized event/revision input. It may reference an
    Artifact Graph object, but it is not that ``ArtifactInstance`` and owns no
    readiness or domain-admission state.
    """

    execution_input_id: str
    workflow_execution_id: str
    input_type_id: str
    schema_version: str
    input_ref: str
    input_sha256: str
    byte_size: int
    media_type: str
    recorded_at_utc: str
    logical_name: str | None = None

    def validate(self) -> None:
        """Validate immutable input identity, content handle, and commit time."""

        for label, value in (
            ("execution_input_id", self.execution_input_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("input_type_id", self.input_type_id),
        ):
            _validate_id(label, value)
        if not self.schema_version or not self.media_type:
            raise ValueError("input schema_version and media_type are required")
        _validate_ref("input_ref", self.input_ref)
        _validate_sha("input_sha256", self.input_sha256)
        if not isinstance(self.byte_size, int) or self.byte_size < 0:
            raise ValueError("input byte_size must be non-negative")
        if self.logical_name is not None:
            if (
                not isinstance(self.logical_name, str)
                or not self.logical_name
                or self.logical_name.strip() != self.logical_name
                or len(self.logical_name) > 255
                or any(character.isspace() for character in self.logical_name)
            ):
                raise ValueError(
                    "ExecutionInputRef logical_name must be a bounded opaque token"
                )
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready Runtime input binding."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExecutionOutputRef:
    """One immutable Runtime output handle with exact producer lineage.

    The record is an execution-owned content handle; it does not identify an
    Artifact Graph ``ArtifactInstance`` or carry domain-admission state.
    """

    execution_output_id: str
    workflow_execution_id: str
    output_type_id: str
    schema_version: str
    output_ref: str
    output_sha256: str
    byte_size: int
    media_type: str
    recorded_at_utc: str
    module_run_id: str | None = None
    variant_id: str | None = None
    attempt_id: str | None = None
    logical_name: str | None = None
    source_artifact_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        """Validate metadata while keeping output content outside the ledger."""

        for label, value in (
            ("execution_output_id", self.execution_output_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("output_type_id", self.output_type_id),
        ):
            _validate_id(label, value)
        if not self.schema_version or not self.media_type:
            raise ValueError("output schema_version and media_type are required")
        _validate_ref("output_ref", self.output_ref)
        _validate_sha("output_sha256", self.output_sha256)
        if not isinstance(self.byte_size, int) or self.byte_size < 0:
            raise ValueError("output byte_size must be non-negative")
        if self.variant_id is not None and self.module_run_id is None:
            raise ValueError("output variant_id requires module_run_id")
        if self.attempt_id is not None and self.variant_id is None:
            raise ValueError("output attempt_id requires variant_id")
        for label, value in (
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
        ):
            if value is not None:
                _validate_id(label, value)
        if self.logical_name is not None:
            if (
                not isinstance(self.logical_name, str)
                or not self.logical_name
                or self.logical_name.strip() != self.logical_name
                or len(self.logical_name) > 255
                or any(character.isspace() for character in self.logical_name)
            ):
                raise ValueError(
                    "ExecutionOutputRef logical_name must be a bounded opaque token"
                )
        _validate_unique_refs(
            "source_artifact_refs", self.source_artifact_refs
        )
        if self.output_ref in self.source_artifact_refs:
            raise ValueError("ExecutionOutputRef cannot cite itself as a source")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready Runtime output record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class AttemptOutputBundle:
    """Ordered immutable ExecutionOutputRef membership for one Attempt."""

    attempt_output_bundle_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    execution_output_refs: tuple[str, ...]
    bundle_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate exact Attempt lineage, member refs, hash, and commit time."""

        for label, value in (
            ("attempt_output_bundle_id", self.attempt_output_bundle_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
        ):
            _validate_id(label, value)
        _validate_unique_refs("execution_output_refs", self.execution_output_refs)
        _validate_sha("bundle_sha256", self.bundle_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready Attempt output bundle."""

        self.validate()
        payload = asdict(self)
        payload["execution_output_refs"] = list(self.execution_output_refs)
        return payload


@dataclass(frozen=True)
class ModelCallRecord:
    """One provider call bound to a single-operation capability grant."""

    model_call_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    grant_id: str
    resource_id: str
    action_id: str
    agent_execution_adapter_id: str
    model_id: str
    status_id: str
    recorded_at_utc: str
    authorization_intent_ref: str | None = None
    authorization_intent_sha256: str | None = None
    authorization_decision_ref: str | None = None
    authorization_decision_sha256: str | None = None
    authorization_observation_ref: str | None = None
    authorization_observation_sha256: str | None = None

    @property
    def operation_id(self) -> str:
        """Return the grant-consuming operation identity."""

        return self.model_call_id

    def validate(self) -> None:
        """Validate call lineage without interpreting model or status IDs."""

        for label, value in (
            ("model_call_id", self.model_call_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("grant_id", self.grant_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("agent_execution_adapter_id", self.agent_execution_adapter_id),
            ("model_id", self.model_id),
            ("status_id", self.status_id),
        ):
            _validate_id(label, value)
        _validate_optional_authorization_refs(self)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready model-call record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool operation bound to a single-operation capability grant."""

    tool_call_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    grant_id: str
    resource_id: str
    action_id: str
    tool_id: str
    status_id: str
    recorded_at_utc: str
    request_ref: str | None = None
    request_sha256: str | None = None
    response_ref: str | None = None
    response_sha256: str | None = None
    authorization_intent_ref: str | None = None
    authorization_intent_sha256: str | None = None
    authorization_decision_ref: str | None = None
    authorization_decision_sha256: str | None = None
    authorization_observation_ref: str | None = None
    authorization_observation_sha256: str | None = None

    @property
    def operation_id(self) -> str:
        """Return the grant-consuming operation identity."""

        return self.tool_call_id

    def validate(self) -> None:
        """Validate tool-call lineage without interpreting tool semantics."""

        for label, value in (
            ("tool_call_id", self.tool_call_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("grant_id", self.grant_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("tool_id", self.tool_id),
            ("status_id", self.status_id),
        ):
            _validate_id(label, value)
        tool_content_values = (
            self.request_ref,
            self.request_sha256,
            self.response_ref,
            self.response_sha256,
        )
        if any(value is not None for value in tool_content_values):
            if any(value is None for value in tool_content_values):
                raise ValueError(
                    "ToolCallRecord request/response lineage is incomplete"
                )
            _validate_ref("request_ref", str(self.request_ref))
            _validate_sha("request_sha256", str(self.request_sha256))
            _validate_ref("response_ref", str(self.response_ref))
            _validate_sha("response_sha256", str(self.response_sha256))
        _validate_optional_authorization_refs(self)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready tool-call record."""

        self.validate()
        return asdict(self)


def _validate_optional_authorization_refs(record: Any) -> None:
    """Validate an all-or-none AR09 authorization evidence closure."""

    values = (
        record.authorization_intent_ref,
        record.authorization_intent_sha256,
        record.authorization_decision_ref,
        record.authorization_decision_sha256,
        record.authorization_observation_ref,
        record.authorization_observation_sha256,
    )
    if not any(value is not None for value in values):
        return
    if any(value is None for value in values):
        raise ValueError("call authorization evidence closure is incomplete")
    for label, value in (
        ("authorization_intent_ref", record.authorization_intent_ref),
        ("authorization_decision_ref", record.authorization_decision_ref),
        (
            "authorization_observation_ref",
            record.authorization_observation_ref,
        ),
    ):
        _validate_ref(label, str(value))
    for label, value in (
        ("authorization_intent_sha256", record.authorization_intent_sha256),
        (
            "authorization_decision_sha256",
            record.authorization_decision_sha256,
        ),
        (
            "authorization_observation_sha256",
            record.authorization_observation_sha256,
        ),
    ):
        _validate_sha(label, str(value))


@dataclass(frozen=True)
class UsageEvent:
    """Provider-reported usage for exactly one committed model or tool call."""

    usage_event_id: str
    operation_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    grant_id: str
    resource_id: str
    action_id: str
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    estimated_cost_usd: str | None
    provider_charge_usd: str | None
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate usage lineage while preserving unavailable values as null."""

        for label, value in (
            ("usage_event_id", self.usage_event_id),
            ("operation_id", self.operation_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("grant_id", self.grant_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
        ):
            _validate_id(label, value)
        ModelUsageRecord(
            self.input_tokens,
            self.output_tokens,
            self.cache_read_tokens,
            self.cache_creation_tokens,
            self.estimated_cost_usd,
            self.provider_charge_usd,
        ).validate()
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready usage record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class UsageMeterOutboxRecord:
    """Durable, idempotent request to derive one Client meter line."""

    usage_meter_outbox_id: str
    workflow_execution_id: str
    module_id: str
    execution_profile_id: str
    usage_event_ref: str
    usage_event_sha256: str
    operation_grant_ref: str
    operation_grant_sha256: str
    operation_grant_binding_ref: str
    operation_grant_binding_sha256: str
    delivery_status: str
    recorded_at_utc: str
    outbox_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("outbox_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "UsageMeterOutboxRecord":
        """Build one content-addressed pending meter delivery request."""

        provisional = cls(**fields, outbox_sha256="0" * 64)
        return cls(
            **fields,
            outbox_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate exact usage, grant, binding, profile, and module closure."""

        for label, value in (
            ("usage_meter_outbox_id", self.usage_meter_outbox_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_id", self.module_id),
            ("execution_profile_id", self.execution_profile_id),
            ("delivery_status", self.delivery_status),
        ):
            _validate_id(label, value)
        if self.delivery_status != "pending":
            raise ValueError("usage meter outbox must begin pending")
        for label, value in (
            ("usage_event_ref", self.usage_event_ref),
            ("operation_grant_ref", self.operation_grant_ref),
            ("operation_grant_binding_ref", self.operation_grant_binding_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            ("usage_event_sha256", self.usage_event_sha256),
            ("operation_grant_sha256", self.operation_grant_sha256),
            (
                "operation_grant_binding_sha256",
                self.operation_grant_binding_sha256,
            ),
            ("outbox_sha256", self.outbox_sha256),
        ):
            _validate_sha(label, value)
        if self.outbox_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("usage meter outbox hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready meter outbox request."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class UsageMeterDeliveryRecord:
    """Immutable acknowledgement that one outbox request reached the meter."""

    usage_meter_delivery_id: str
    workflow_execution_id: str
    usage_meter_outbox_ref: str
    usage_meter_outbox_sha256: str
    meter_record_ref: str
    meter_record_sha256: str
    delivered_at_utc: str

    def validate(self) -> None:
        """Validate outbox-to-meter delivery acknowledgement lineage."""

        for label, value in (
            ("usage_meter_delivery_id", self.usage_meter_delivery_id),
            ("workflow_execution_id", self.workflow_execution_id),
        ):
            _validate_id(label, value)
        _validate_ref("usage_meter_outbox_ref", self.usage_meter_outbox_ref)
        _validate_sha("usage_meter_outbox_sha256", self.usage_meter_outbox_sha256)
        _validate_ref("meter_record_ref", self.meter_record_ref)
        _validate_sha("meter_record_sha256", self.meter_record_sha256)
        _validate_utc("delivered_at_utc", self.delivered_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready meter delivery acknowledgement."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ContextBinding:
    """Variant-local provider context compatibility and opaque reference."""

    context_binding_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    context_ref: str
    compatibility_sha256: str
    entitlement_snapshot_hash: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate context lineage, reference, and compatibility hashes."""

        for label, value in (
            ("context_binding_id", self.context_binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
        ):
            _validate_id(label, value)
        _validate_ref("context_ref", self.context_ref)
        _validate_sha("compatibility_sha256", self.compatibility_sha256)
        _validate_sha("entitlement_snapshot_hash", self.entitlement_snapshot_hash)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready context binding."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ContextEvent:
    """One create, resume, reconstruct, compact, close, or invalidation event."""

    context_event_id: str
    context_binding_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    event_type_id: str
    recorded_at_utc: str
    attempt_id: str | None = None
    context_ref: str | None = None

    def validate(self) -> None:
        """Validate context-event identity without interpreting event meaning."""

        for label, value in (
            ("context_event_id", self.context_event_id),
            ("context_binding_id", self.context_binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("event_type_id", self.event_type_id),
        ):
            _validate_id(label, value)
        if self.attempt_id is not None:
            _validate_id("attempt_id", self.attempt_id)
        if self.context_ref is not None:
            _validate_ref("context_ref", self.context_ref)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready context event."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ExternalEventApplicationRecord:
    """Acknowledged application of one authorized, ref-only external event."""

    event_id: str
    workflow_execution_id: str
    event_type_id: str
    expected_state_id: str
    target_state_id: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    authorization_ref: str
    graph_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate event identity, state refs, authorization, and graph binding."""

        for label, value in (
            ("event_id", self.event_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("event_type_id", self.event_type_id),
            ("expected_state_id", self.expected_state_id),
            ("target_state_id", self.target_state_id),
        ):
            _validate_id(label, value)
        _validate_ref("decision_artifact_ref", self.decision_artifact_ref)
        _validate_sha("decision_artifact_sha256", self.decision_artifact_sha256)
        _validate_ref("authorization_ref", self.authorization_ref)
        _validate_sha("graph_sha256", self.graph_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical acknowledged-event representation."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class CheckpointRecord:
    """Gate D-produced recovery boundary after a locally committed outcome."""

    checkpoint_id: str
    workflow_execution_id: str
    dispatch_id: str
    execution_release_ref: str
    graph_sha256: str
    entitlement_snapshot_hash: str
    current_state_id: str
    runtime_status_id: str
    committed_outcome_ref: str
    committed_outcome_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate the local pre-ack recovery and Outcome boundary."""

        for label, value in (
            ("checkpoint_id", self.checkpoint_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("current_state_id", self.current_state_id),
            ("runtime_status_id", self.runtime_status_id),
        ):
            _validate_id(label, value)
        _validate_ref("execution_release_ref", self.execution_release_ref)
        _validate_sha("graph_sha256", self.graph_sha256)
        _validate_sha("entitlement_snapshot_hash", self.entitlement_snapshot_hash)
        _validate_ref("committed_outcome_ref", self.committed_outcome_ref)
        _validate_sha("committed_outcome_sha256", self.committed_outcome_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready checkpoint record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class BackendAcknowledgementRecord:
    """Append-only proof that a durable backend accepted one checkpoint."""

    backend_acknowledgement_id: str
    workflow_execution_id: str
    backend_id: str
    backend_execution_id: str
    dispatch_id: str
    checkpoint_id: str
    outcome_ref: str
    outcome_sha256: str
    acknowledgement_kind: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate backend and checkpoint lineage without backend payload content."""

        for label, value in (
            ("backend_acknowledgement_id", self.backend_acknowledgement_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("backend_id", self.backend_id),
            ("backend_execution_id", self.backend_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("checkpoint_id", self.checkpoint_id),
            ("acknowledgement_kind", self.acknowledgement_kind),
        ):
            _validate_id(label, value)
        _validate_ref("outcome_ref", self.outcome_ref)
        _validate_sha("outcome_sha256", self.outcome_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-ready backend acknowledgement."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class EvaluationRun:
    """Runtime-owned evaluation lineage over one immutable candidate output."""

    evaluation_run_id: str
    workflow_execution_id: str
    source_module_run_id: str
    candidate_variant_id: str
    candidate_attempt_id: str
    evaluator_id: str
    rubric_ref: str
    rubric_sha256: str
    candidate_output_bundle_sha256: str
    recorded_at_utc: str
    evaluation_run_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("evaluation_run_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "EvaluationRun":
        """Build one content-addressed evaluation invocation."""

        provisional = cls(**fields, evaluation_run_sha256="0" * 64)
        return cls(
            **fields,
            evaluation_run_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate candidate/evaluator lineage without interpreting the rubric."""

        for label, value in (
            ("evaluation_run_id", self.evaluation_run_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("source_module_run_id", self.source_module_run_id),
            ("candidate_variant_id", self.candidate_variant_id),
            ("candidate_attempt_id", self.candidate_attempt_id),
            ("evaluator_id", self.evaluator_id),
        ):
            _validate_id(label, value)
        _validate_ref("rubric_ref", self.rubric_ref)
        _validate_sha("rubric_sha256", self.rubric_sha256)
        _validate_sha(
            "candidate_output_bundle_sha256", self.candidate_output_bundle_sha256
        )
        _validate_sha("evaluation_run_sha256", self.evaluation_run_sha256)
        if self.evaluation_run_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("evaluation run hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready evaluation run."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class EvaluationResult:
    """Opaque Runtime output bound to one evaluation and candidate hash."""

    evaluation_result_id: str
    evaluation_run_id: str
    workflow_execution_id: str
    verdict_id: str
    candidate_output_bundle_sha256: str
    result_execution_output_ref: str
    result_execution_output_sha256: str
    recorded_at_utc: str
    evaluation_result_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("evaluation_result_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "EvaluationResult":
        """Build one content-addressed evaluation result."""

        provisional = cls(**fields, evaluation_result_sha256="0" * 64)
        return cls(
            **fields,
            evaluation_result_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate result identity and immutable candidate/result bindings."""

        for label, value in (
            ("evaluation_result_id", self.evaluation_result_id),
            ("evaluation_run_id", self.evaluation_run_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("verdict_id", self.verdict_id),
        ):
            _validate_id(label, value)
        _validate_sha(
            "candidate_output_bundle_sha256", self.candidate_output_bundle_sha256
        )
        _validate_ref("result_execution_output_ref", self.result_execution_output_ref)
        _validate_sha(
            "result_execution_output_sha256",
            self.result_execution_output_sha256,
        )
        _validate_sha("evaluation_result_sha256", self.evaluation_result_sha256)
        if self.evaluation_result_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("evaluation result hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready evaluation result."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class Selection:
    """Immutable downstream-consumption authority for one selected Variant."""

    selection_id: str
    workflow_execution_id: str
    source_module_run_id: str
    selected_variant_id: str
    selected_attempt_id: str
    selected_output_bundle_sha256: str
    authority_ref: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    recorded_at_utc: str
    evaluation_result_ref: str | None = None

    def validate(self) -> None:
        """Validate selection lineage and immutable authority references."""

        for label, value in (
            ("selection_id", self.selection_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("source_module_run_id", self.source_module_run_id),
            ("selected_variant_id", self.selected_variant_id),
            ("selected_attempt_id", self.selected_attempt_id),
        ):
            _validate_id(label, value)
        _validate_sha(
            "selected_output_bundle_sha256", self.selected_output_bundle_sha256
        )
        _validate_ref("authority_ref", self.authority_ref)
        _validate_ref("decision_artifact_ref", self.decision_artifact_ref)
        _validate_sha("decision_artifact_sha256", self.decision_artifact_sha256)
        if self.evaluation_result_ref is not None:
            _validate_ref("evaluation_result_ref", self.evaluation_result_ref)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready selection record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class OperationAuthorizationRequestRecord:
    """Durable Runtime request sent to Product before grant issuance."""

    operation_authorization_request_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    operation_id: str
    delegation_ref: str
    delegation_sha256: str
    permission_id: str
    resource_id: str
    action_id: str
    entitlement_snapshot_sha256: str
    target_closure_sha256: str
    input_closure_sha256: str
    execution_profile_id: str
    data_use_purpose_id: str
    idempotency_key: str
    recorded_at_utc: str
    request_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("request_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "OperationAuthorizationRequestRecord":
        """Build a content-addressed pre-authorization request."""

        provisional = cls(**fields, request_sha256="0" * 64)
        return cls(**fields, request_sha256=sha256_json(provisional._identity_payload()))

    def validate(self) -> None:
        """Validate operation lineage, purpose, authority, and request hash."""

        for label, value in (
            (
                "operation_authorization_request_id",
                self.operation_authorization_request_id,
            ),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("operation_id", self.operation_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("execution_profile_id", self.execution_profile_id),
            ("data_use_purpose_id", self.data_use_purpose_id),
            ("idempotency_key", self.idempotency_key),
        ):
            _validate_id(label, value)
        if not re.fullmatch(
            r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+",
            self.permission_id,
        ):
            raise ValueError("invalid permission_id")
        _validate_ref("delegation_ref", self.delegation_ref)
        for label, value in (
            ("delegation_sha256", self.delegation_sha256),
            ("entitlement_snapshot_sha256", self.entitlement_snapshot_sha256),
            ("target_closure_sha256", self.target_closure_sha256),
            ("input_closure_sha256", self.input_closure_sha256),
            ("request_sha256", self.request_sha256),
        ):
            _validate_sha(label, value)
        if self.request_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("operation authorization request hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready Product authorization request."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class OperationAuthorizationBindingRecord:
    """Runtime binding of its request to the exact Product result and grant."""

    operation_authorization_binding_id: str
    workflow_execution_id: str
    operation_id: str
    operation_authorization_request_ref: str
    operation_authorization_request_sha256: str
    product_result_ref: str
    product_result_sha256: str
    observed_authority_sha256: str
    effect: str
    reason_code: str
    operation_grant_ref: str | None
    operation_grant_sha256: str | None
    recorded_at_utc: str
    binding_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("binding_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "OperationAuthorizationBindingRecord":
        """Build a content-addressed request/result/grant authority edge."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        return cls(**fields, binding_sha256=sha256_json(provisional._identity_payload()))

    def validate(self) -> None:
        """Validate the exact Product resolution bound before Gateway use."""

        for label, value in (
            (
                "operation_authorization_binding_id",
                self.operation_authorization_binding_id,
            ),
            ("workflow_execution_id", self.workflow_execution_id),
            ("operation_id", self.operation_id),
            ("reason_code", self.reason_code),
        ):
            _validate_id(label, value)
        for label, value in (
            (
                "operation_authorization_request_ref",
                self.operation_authorization_request_ref,
            ),
            ("product_result_ref", self.product_result_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            (
                "operation_authorization_request_sha256",
                self.operation_authorization_request_sha256,
            ),
            ("product_result_sha256", self.product_result_sha256),
            ("observed_authority_sha256", self.observed_authority_sha256),
            ("binding_sha256", self.binding_sha256),
        ):
            _validate_sha(label, value)
        if self.effect not in {"allow", "deny"}:
            raise ValueError("unsupported operation authorization effect")
        if self.effect == "allow":
            if self.operation_grant_ref is None or self.operation_grant_sha256 is None:
                raise ValueError("allow binding requires exact operation grant")
            _validate_ref("operation_grant_ref", self.operation_grant_ref)
            _validate_sha("operation_grant_sha256", self.operation_grant_sha256)
        elif self.operation_grant_ref is not None or self.operation_grant_sha256 is not None:
            raise ValueError("deny binding cannot carry an operation grant")
        if self.binding_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("operation authorization binding hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready authorization binding."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class OperationGrantBindingRecord:
    """Durable pre-effect claim binding one exact operation to Product authority."""

    operation_grant_binding_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    operation_id: str
    gateway_id: str
    operation_authorization_binding_ref: str
    operation_authorization_binding_sha256: str
    grant_ref: str
    grant_sha256: str
    permission_id: str
    resource_id: str
    action_id: str
    target_closure_sha256: str
    idempotency_key: str
    binding_status: str
    recorded_at_utc: str
    binding_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("binding_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "OperationGrantBindingRecord":
        """Build a content-addressed operation claim before the protected effect."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        return cls(
            **fields,
            binding_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate exact Product grant and pre-effect operation lineage."""

        for label, value in (
            ("operation_grant_binding_id", self.operation_grant_binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("operation_id", self.operation_id),
            ("gateway_id", self.gateway_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("idempotency_key", self.idempotency_key),
            ("binding_status", self.binding_status),
        ):
            _validate_id(label, value)
        if not re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+", self.permission_id):
            raise ValueError("invalid permission_id")
        if self.binding_status != "claimed":
            raise ValueError("operation grant binding must record a durable claim")
        _validate_ref("grant_ref", self.grant_ref)
        _validate_ref(
            "operation_authorization_binding_ref",
            self.operation_authorization_binding_ref,
        )
        for label, value in (
            (
                "operation_authorization_binding_sha256",
                self.operation_authorization_binding_sha256,
            ),
            ("grant_sha256", self.grant_sha256),
            ("target_closure_sha256", self.target_closure_sha256),
            ("binding_sha256", self.binding_sha256),
        ):
            _validate_sha(label, value)
        if self.binding_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("operation grant binding hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return a validated JSON-ready grant-consumption record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class GatewayOperationEffectRecord:
    """Post-effect evidence closing one durable operation-grant claim."""

    gateway_operation_effect_id: str
    workflow_execution_id: str
    operation_grant_binding_id: str
    operation_grant_binding_sha256: str
    operation_id: str
    outcome_status: str
    effect_evidence_ref: str
    effect_evidence_sha256: str
    recorded_at_utc: str
    effect_record_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("effect_record_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "GatewayOperationEffectRecord":
        """Build content-addressed evidence after a protected effect succeeds."""

        provisional = cls(**fields, effect_record_sha256="0" * 64)
        return cls(
            **fields,
            effect_record_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate claim binding, operation identity, outcome, and evidence hash."""

        for label, value in (
            ("gateway_operation_effect_id", self.gateway_operation_effect_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("operation_grant_binding_id", self.operation_grant_binding_id),
            ("operation_id", self.operation_id),
            ("outcome_status", self.outcome_status),
        ):
            _validate_id(label, value)
        if self.outcome_status != "succeeded":
            raise ValueError("successful Gateway effect record is required")
        _validate_ref("effect_evidence_ref", self.effect_evidence_ref)
        for label, value in (
            ("operation_grant_binding_sha256", self.operation_grant_binding_sha256),
            ("effect_evidence_sha256", self.effect_evidence_sha256),
            ("effect_record_sha256", self.effect_record_sha256),
        ):
            _validate_sha(label, value)
        if self.effect_record_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("Gateway effect record hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return validated JSON-ready Gateway effect evidence."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class InvocationGatewayEffectBindingRecord:
    """Bind a prior immutable Gateway effect into Runtime finalization."""

    invocation_gateway_effect_binding_id: str
    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    operation_id: str
    operation_grant_binding_ref: str
    operation_grant_binding_sha256: str
    gateway_operation_effect_ref: str
    gateway_operation_effect_sha256: str
    effect_evidence_ref: str
    effect_evidence_sha256: str
    recorded_at_utc: str
    binding_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("binding_sha256")
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "InvocationGatewayEffectBindingRecord":
        """Build a content-addressed Runtime-to-Gateway effect edge."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        return cls(**fields, binding_sha256=sha256_json(provisional._identity_payload()))

    def validate(self) -> None:
        """Validate exact invocation lineage and Gateway evidence hashes."""

        for label, value in (
            (
                "invocation_gateway_effect_binding_id",
                self.invocation_gateway_effect_binding_id,
            ),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("operation_id", self.operation_id),
        ):
            _validate_id(label, value)
        for label, value in (
            ("operation_grant_binding_ref", self.operation_grant_binding_ref),
            ("gateway_operation_effect_ref", self.gateway_operation_effect_ref),
            ("effect_evidence_ref", self.effect_evidence_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            (
                "operation_grant_binding_sha256",
                self.operation_grant_binding_sha256,
            ),
            (
                "gateway_operation_effect_sha256",
                self.gateway_operation_effect_sha256,
            ),
            ("effect_evidence_sha256", self.effect_evidence_sha256),
            ("binding_sha256", self.binding_sha256),
        ):
            _validate_sha(label, value)
        if self.binding_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("invocation Gateway effect binding hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready Gateway effect binding."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class EvaluationCoverageBinding:
    """One exact candidate/evaluator/result edge in an evaluation matrix."""

    candidate_output_bundle_sha256: str
    evaluator_id: str
    evaluation_run_ref: str
    evaluation_run_sha256: str
    evaluation_result_ref: str
    evaluation_result_sha256: str
    rubric_ref: str
    rubric_sha256: str
    result_execution_output_ref: str
    result_execution_output_sha256: str

    def validate(self) -> None:
        """Validate one candidate, evaluator, and result edge."""

        _validate_sha(
            "candidate_output_bundle_sha256",
            self.candidate_output_bundle_sha256,
        )
        _validate_id("evaluator_id", self.evaluator_id)
        for label, value in (
            ("evaluation_run_ref", self.evaluation_run_ref),
            ("evaluation_result_ref", self.evaluation_result_ref),
            ("rubric_ref", self.rubric_ref),
            ("result_execution_output_ref", self.result_execution_output_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            ("evaluation_run_sha256", self.evaluation_run_sha256),
            ("evaluation_result_sha256", self.evaluation_result_sha256),
            ("rubric_sha256", self.rubric_sha256),
            (
                "result_execution_output_sha256",
                self.result_execution_output_sha256,
            ),
        ):
            _validate_sha(label, value)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready evaluation coverage edge."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class EvaluationSet:
    """Complete candidate-by-evaluator matrix used by one output resolution."""

    evaluation_set_id: str
    workflow_execution_id: str
    source_module_run_id: str
    candidate_output_bundle_sha256s: tuple[str, ...]
    required_evaluator_ids: tuple[str, ...]
    rubric_refs: tuple[str, ...]
    rubric_sha256s: tuple[str, ...]
    coverage_bindings: tuple[EvaluationCoverageBinding, ...]
    evaluation_result_refs: tuple[str, ...]
    veto_disposition: str
    evaluation_set_sha256: str
    recorded_at_utc: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("evaluation_set_sha256")
        payload["candidate_output_bundle_sha256s"] = list(
            self.candidate_output_bundle_sha256s
        )
        payload["required_evaluator_ids"] = list(self.required_evaluator_ids)
        payload["rubric_refs"] = list(self.rubric_refs)
        payload["rubric_sha256s"] = list(self.rubric_sha256s)
        payload["coverage_bindings"] = [
            item.as_dict() for item in self.coverage_bindings
        ]
        payload["evaluation_result_refs"] = list(self.evaluation_result_refs)
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "EvaluationSet":
        """Build a content-addressed evaluation-result membership set."""

        payload = dict(fields)
        payload["candidate_output_bundle_sha256s"] = tuple(
            payload["candidate_output_bundle_sha256s"]
        )
        payload["required_evaluator_ids"] = tuple(payload["required_evaluator_ids"])
        payload["rubric_refs"] = tuple(payload["rubric_refs"])
        payload["rubric_sha256s"] = tuple(payload["rubric_sha256s"])
        payload["coverage_bindings"] = tuple(payload["coverage_bindings"])
        payload["evaluation_result_refs"] = tuple(payload["evaluation_result_refs"])
        provisional = cls(**payload, evaluation_set_sha256="0" * 64)
        return cls(
            **payload,
            evaluation_set_sha256=sha256_json(provisional._identity_payload()),
        )

    def validate(self) -> None:
        """Validate evaluation membership and its content hash."""

        for label, value in (
            ("evaluation_set_id", self.evaluation_set_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("source_module_run_id", self.source_module_run_id),
        ):
            _validate_id(label, value)
        if not self.candidate_output_bundle_sha256s:
            raise ValueError("evaluation set requires candidate bundle hashes")
        for value in self.candidate_output_bundle_sha256s:
            _validate_sha("candidate_output_bundle_sha256", value)
        if len(set(self.candidate_output_bundle_sha256s)) != len(
            self.candidate_output_bundle_sha256s
        ):
            raise ValueError("evaluation set candidate bundle hashes must be unique")
        if not self.required_evaluator_ids:
            raise ValueError("evaluation set requires evaluators")
        for evaluator_id in self.required_evaluator_ids:
            _validate_id("required_evaluator_id", evaluator_id)
        if len(set(self.required_evaluator_ids)) != len(self.required_evaluator_ids):
            raise ValueError("required evaluator IDs must be unique")
        if not (
            len(self.required_evaluator_ids)
            == len(self.rubric_refs)
            == len(self.rubric_sha256s)
        ):
            raise ValueError("each required evaluator needs one exact rubric")
        _validate_unique_refs("rubric_refs", self.rubric_refs)
        for value in self.rubric_sha256s:
            _validate_sha("rubric_sha256", value)
        if not self.coverage_bindings:
            raise ValueError("evaluation set requires coverage bindings")
        for binding in self.coverage_bindings:
            if not isinstance(binding, EvaluationCoverageBinding):
                raise TypeError("coverage binding must be EvaluationCoverageBinding")
            binding.validate()
        expected_pairs = {
            (candidate, evaluator)
            for candidate in self.candidate_output_bundle_sha256s
            for evaluator in self.required_evaluator_ids
        }
        actual_pairs = {
            (binding.candidate_output_bundle_sha256, binding.evaluator_id)
            for binding in self.coverage_bindings
        }
        if len(actual_pairs) != len(self.coverage_bindings):
            raise ValueError("evaluation coverage contains duplicate edges")
        if actual_pairs != expected_pairs:
            raise ValueError("evaluation coverage is not a complete matrix")
        rubric_by_evaluator = dict(
            zip(
                self.required_evaluator_ids,
                zip(self.rubric_refs, self.rubric_sha256s, strict=True),
                strict=True,
            )
        )
        if any(
            (binding.rubric_ref, binding.rubric_sha256)
            != rubric_by_evaluator[binding.evaluator_id]
            for binding in self.coverage_bindings
        ):
            raise ValueError("evaluation coverage differs from evaluator rubric")
        _validate_unique_refs("evaluation_result_refs", self.evaluation_result_refs)
        if set(self.evaluation_result_refs) != {
            binding.evaluation_result_ref for binding in self.coverage_bindings
        }:
            raise ValueError("evaluation result membership differs from coverage")
        _validate_id("veto_disposition", self.veto_disposition)
        if self.veto_disposition not in {"passed", "failed"}:
            raise ValueError("unsupported evaluation veto disposition")
        _validate_sha("evaluation_set_sha256", self.evaluation_set_sha256)
        if self.evaluation_set_sha256 != sha256_json(self._identity_payload()):
            raise ValueError("evaluation set hash mismatch")
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready evaluation set."""

        self.validate()
        return {**self._identity_payload(), "evaluation_set_sha256": self.evaluation_set_sha256}


RuntimeLedgerRecord: TypeAlias = (
    WorkflowExecutionRecord
    | ExecutionAuthorizationBindingRecord
    | ExecutionStartAdmissionRecord
    | ModuleDispatchAdmissionRecord
    | ExecutionAuthorizationInvalidationRecord
    | ExecutionProfileRecord
    | WorkflowModuleRunRecord
    | WorkflowModuleExecutionVariantRecord
    | WorkflowAttemptStartedRecord
    | WorkflowAttemptRecord
    | InvocationCommitRecord
    | StaleOutputRecord
    | AttemptOrphanedRecord
    | AttemptOutputBundle
    | ExecutionInputRef
    | ExecutionOutputRef
    | ModelCallRecord
    | ToolCallRecord
    | UsageEvent
    | UsageMeterOutboxRecord
    | UsageMeterDeliveryRecord
    | ContextBinding
    | ContextEvent
    | ExternalEventApplicationRecord
    | CheckpointRecord
    | BackendAcknowledgementRecord
    | EvaluationRun
    | EvaluationResult
    | EvaluationSet
    | Selection
    | OperationAuthorizationRequestRecord
    | OperationAuthorizationBindingRecord
    | OperationGrantBindingRecord
    | GatewayOperationEffectRecord
    | InvocationGatewayEffectBindingRecord
    | ModuleOutputResolutionRecord
    | ModuleOutcome
)

LegacyAuthorizationLedgerRecord: TypeAlias = (
    LegacyExecutionEntitlementSnapshot | LegacyModuleCapabilityGrant
)
PersistedRuntimeRecord: TypeAlias = (
    RuntimeLedgerRecord | LegacyAuthorizationLedgerRecord
)


def runtime_record_as_dict(record: RuntimeLedgerRecord) -> dict[str, Any]:
    """Return a stable discriminated representation of one Runtime record."""

    if not isinstance(record, RuntimeLedgerRecord):
        raise TypeError(f"unsupported Runtime record: {type(record).__name__}")
    validate = getattr(record, "validate", None)
    if not callable(validate):
        raise TypeError(f"unsupported Runtime record: {type(record).__name__}")
    validate()
    as_dict_method = getattr(record, "as_dict", None)
    if not callable(as_dict_method):
        raise TypeError(f"Runtime record lacks as_dict: {type(record).__name__}")
    return {"record_type": type(record).__name__, "record": as_dict_method()}


@dataclass(frozen=True)
class RuntimeRecordBatch:
    """Atomic immutable group keyed by Workflow Execution and transaction ID."""

    workflow_execution_id: str
    transaction_id: str
    records: tuple[RuntimeLedgerRecord, ...]

    def validate(self) -> None:
        """Validate batch identity, non-emptiness, and member execution scope."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        if not self.records:
            raise ValueError("RuntimeRecordBatch requires records")
        for record in self.records:
            runtime_record_as_dict(record)
            record_execution_id = getattr(record, "workflow_execution_id", None)
            if (
                record_execution_id is not None
                and record_execution_id != self.workflow_execution_id
            ):
                raise ValueError("RuntimeRecordBatch contains cross-execution record")

    def as_dict(self) -> dict[str, Any]:
        """Return a stable JSON-ready batch representation."""

        self.validate()
        return {
            "workflow_execution_id": self.workflow_execution_id,
            "transaction_id": self.transaction_id,
            "records": [runtime_record_as_dict(record) for record in self.records],
        }

    @property
    def transaction_sha256(self) -> str:
        """Return the content hash used for idempotent transaction replay."""

        return sha256_json(self.as_dict())


def legacy_authorization_record_as_dict(
    record: LegacyAuthorizationLedgerRecord,
) -> dict[str, Any]:
    """Serialize an explicitly non-authoritative predecessor fact."""

    if not isinstance(record, LegacyAuthorizationLedgerRecord):
        raise TypeError(
            f"unsupported legacy authorization record: {type(record).__name__}"
        )
    record.validate()
    return {
        "record_type": type(record).__name__,
        "record": {**record.as_dict(), "authority_disposition": "legacy_fact_only"},
    }


@dataclass(frozen=True)
class LegacyRuntimeRecordBatch:
    """Compatibility batch for immutable pre-AR09 execution history only."""

    workflow_execution_id: str
    transaction_id: str
    records: tuple[PersistedRuntimeRecord, ...]

    def validate(self) -> None:
        """Validate a compatibility batch without admitting legacy authority."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        if not self.records:
            raise ValueError("LegacyRuntimeRecordBatch requires records")
        for record in self.records:
            if isinstance(record, LegacyAuthorizationLedgerRecord):
                legacy_authorization_record_as_dict(record)
            else:
                runtime_record_as_dict(record)
            record_execution_id = getattr(record, "workflow_execution_id", None)
            if (
                record_execution_id is not None
                and record_execution_id != self.workflow_execution_id
            ):
                raise ValueError("legacy batch contains cross-execution record")

    def as_dict(self) -> dict[str, Any]:
        """Return a discriminated predecessor-history batch."""

        self.validate()
        return {
            "workflow_execution_id": self.workflow_execution_id,
            "transaction_id": self.transaction_id,
            "legacy_compatibility": True,
            "records": [
                legacy_authorization_record_as_dict(record)
                if isinstance(record, LegacyAuthorizationLedgerRecord)
                else runtime_record_as_dict(record)
                for record in self.records
            ],
        }

    @property
    def transaction_sha256(self) -> str:
        """Return the content hash used for compatibility replay."""

        return sha256_json(self.as_dict())


@dataclass(frozen=True)
class CommitReceipt:
    """Result of one atomic commit or byte-identical transaction replay."""

    workflow_execution_id: str
    transaction_id: str
    transaction_sha256: str
    record_count: int
    committed_outcome_refs: tuple[str, ...]
    replayed: bool


@dataclass(frozen=True)
class AttemptClaim:
    """Secret-bearing caller handle for one active Attempt claim."""

    workflow_execution_id: str
    attempt_id: str
    claim_token: str

    def validate(self) -> None:
        """Validate claim identity while keeping the token outside the ledger."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("attempt_id", self.attempt_id)
        if (
            not isinstance(self.claim_token, str)
            or len(self.claim_token) < 32
            or len(self.claim_token) > 512
            or any(character.isspace() for character in self.claim_token)
        ):
            raise ValueError("claim_token must be a bounded opaque secret")


@dataclass(frozen=True)
class LegacyAttemptBeginBatch:
    """Pre-AR09 Attempt start plus Runtime-minted grants; compatibility only."""

    workflow_execution_id: str
    transaction_id: str
    start: WorkflowAttemptStartedRecord
    claim: AttemptClaim
    grants: tuple[LegacyModuleCapabilityGrant, ...]

    def validate(self) -> None:
        """Validate the begin batch before converting it to a ledger batch."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        self.start.validate()
        self.claim.validate()
        if self.start.workflow_execution_id != self.workflow_execution_id:
            raise ValueError("Attempt begin crossed Workflow Execution")
        if (
            self.claim.workflow_execution_id != self.workflow_execution_id
            or self.claim.attempt_id != self.start.attempt_id
            or sha256_text(self.claim.claim_token) != self.start.claim_token_hash
        ):
            raise PermissionError("Attempt claim does not match start record")
        for grant in self.grants:
            grant.validate()
            if (
                grant.workflow_execution_id != self.workflow_execution_id
                or grant.module_run_id != self.start.module_run_id
                or grant.variant_id != self.start.variant_id
                or grant.attempt_id != self.start.attempt_id
            ):
                raise ValueError("Attempt begin grant crossed start lineage")

    def as_record_batch(self) -> LegacyRuntimeRecordBatch:
        """Return the atomic ledger representation."""

        self.validate()
        return LegacyRuntimeRecordBatch(
            workflow_execution_id=self.workflow_execution_id,
            transaction_id=self.transaction_id,
            records=(self.start, *self.grants),
        )


@dataclass(frozen=True)
class AttemptBeginReceipt:
    """Durable start receipt plus the caller-only active claim."""

    commit_receipt: CommitReceipt
    claim: AttemptClaim


@dataclass(frozen=True)
class LegacyOperationGrantBatch:
    """Pre-AR09 Runtime-minted dynamic grants; compatibility only."""

    workflow_execution_id: str
    transaction_id: str
    claim: AttemptClaim
    grants: tuple[LegacyModuleCapabilityGrant, ...]

    def validate(self) -> None:
        """Validate dynamic grants against the supplied claim identity."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        self.claim.validate()
        if self.claim.workflow_execution_id != self.workflow_execution_id:
            raise ValueError("operation grant claim crossed Workflow Execution")
        if not self.grants:
            raise ValueError("LegacyOperationGrantBatch requires at least one grant")
        for grant in self.grants:
            grant.validate()
            if (
                grant.workflow_execution_id != self.workflow_execution_id
                or grant.attempt_id != self.claim.attempt_id
            ):
                raise ValueError("operation grant crossed Attempt claim")

    def as_record_batch(self) -> LegacyRuntimeRecordBatch:
        """Return the atomic ledger representation."""

        self.validate()
        return LegacyRuntimeRecordBatch(
            workflow_execution_id=self.workflow_execution_id,
            transaction_id=self.transaction_id,
            records=self.grants,
        )


@dataclass(frozen=True)
class LegacyOperationGrantReceipt:
    """Compatibility receipt for pre-AR09 Runtime-minted grants."""

    commit_receipt: CommitReceipt
    grant_ids: tuple[str, ...]


@dataclass(frozen=True)
class AttemptFinalizationBatch:
    """Terminal Attempt, invocation commit, and normalized child records."""

    workflow_execution_id: str
    transaction_id: str
    terminal_attempt: WorkflowAttemptRecord
    invocation_commit: InvocationCommitRecord
    records: tuple[RuntimeLedgerRecord, ...] = ()

    def _canonical_output_bundle(self) -> AttemptOutputBundle:
        """Resolve or build the one canonical output bundle for this Attempt."""

        explicit_bundles = tuple(
            record
            for record in self.records
            if isinstance(record, AttemptOutputBundle)
        )
        if len(explicit_bundles) > 1:
            raise ValueError("Attempt finalization has multiple AttemptOutputBundles")

        outputs = tuple(
            record
            for record in self.records
            if isinstance(record, ExecutionOutputRef)
        )
        for output in outputs:
            output.validate()
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
            ):
                if getattr(output, field) != getattr(self.terminal_attempt, field):
                    raise ValueError(
                        f"ExecutionOutputRef {field} differs from terminal Attempt"
                    )

        output_refs = tuple(output.output_ref for output in outputs)
        if output_refs != self.terminal_attempt.execution_output_refs:
            raise ValueError(
                "Attempt execution_output_refs differ from ordered ExecutionOutputRefs"
            )
        bundle_sha256 = attempt_output_bundle_sha256(outputs)
        if bundle_sha256 != self.invocation_commit.attempt_output_bundle_sha256:
            raise ValueError(
                "InvocationCommitRecord bundle hash differs from ExecutionOutputRefs"
            )

        if explicit_bundles:
            bundle = explicit_bundles[0]
            bundle.validate()
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
            ):
                if getattr(bundle, field) != getattr(self.terminal_attempt, field):
                    raise ValueError(
                        f"AttemptOutputBundle {field} differs from terminal Attempt"
                    )
            if bundle.execution_output_refs != output_refs:
                raise ValueError(
                    "AttemptOutputBundle membership differs from ExecutionOutputRefs"
                )
            if bundle.bundle_sha256 != bundle_sha256:
                raise ValueError(
                    "AttemptOutputBundle hash differs from ExecutionOutputRefs"
                )
            return bundle

        return AttemptOutputBundle(
            attempt_output_bundle_id=stable_runtime_id(
                "output_bundle",
                self.workflow_execution_id,
                self.terminal_attempt.attempt_id,
                bundle_sha256,
            ),
            workflow_execution_id=self.workflow_execution_id,
            module_run_id=self.terminal_attempt.module_run_id,
            variant_id=self.terminal_attempt.variant_id,
            attempt_id=self.terminal_attempt.attempt_id,
            execution_output_refs=output_refs,
            bundle_sha256=bundle_sha256,
            recorded_at_utc=self.invocation_commit.recorded_at_utc,
        )

    def validate(self) -> None:
        """Validate exact terminal identities before compare-and-commit."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        self.terminal_attempt.validate()
        self.invocation_commit.validate()
        if self.invocation_commit.commit_transaction_id != self.transaction_id:
            raise ValueError("InvocationCommitRecord transaction differs from batch")
        for field in (
            "workflow_execution_id",
            "module_run_id",
            "variant_id",
            "attempt_id",
        ):
            if getattr(self.terminal_attempt, field) != getattr(
                self.invocation_commit, field
            ):
                raise ValueError(f"invocation commit {field} differs from Attempt")
        if self.invocation_commit.terminal_status != self.terminal_attempt.status:
            raise ValueError("invocation terminal status differs from Attempt")
        for record in self.records:
            runtime_record_as_dict(record)
            if isinstance(
                record,
                (WorkflowAttemptStartedRecord, WorkflowAttemptRecord, InvocationCommitRecord),
            ):
                raise ValueError("finalization child records duplicate lifecycle records")
        self._canonical_output_bundle()

    def as_record_batch(self) -> RuntimeRecordBatch:
        """Return the atomic ledger representation."""

        self.validate()
        output_bundle = self._canonical_output_bundle()
        child_records = tuple(
            record
            for record in self.records
            if not isinstance(record, AttemptOutputBundle)
        )
        return RuntimeRecordBatch(
            workflow_execution_id=self.workflow_execution_id,
            transaction_id=self.transaction_id,
            records=(
                self.terminal_attempt,
                output_bundle,
                *child_records,
                self.invocation_commit,
            ),
        )


@dataclass(frozen=True)
class AttemptFinalizationReceipt:
    """Terminal commit receipt for one compare-and-commit result."""

    commit_receipt: CommitReceipt
    invocation_commit_id: str


@dataclass(frozen=True)
class AttemptOrphaningBatch:
    """Terminal failed Attempt and orphan disposition committed together."""

    workflow_execution_id: str
    transaction_id: str
    terminal_attempt: WorkflowAttemptRecord
    orphaned: AttemptOrphanedRecord

    def validate(self) -> None:
        """Validate orphan terminalization against one exact Attempt identity."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        self.terminal_attempt.validate()
        self.orphaned.validate()
        if (
            self.terminal_attempt.status != "failed"
            or self.terminal_attempt.failure_class != "orphaned_attempt"
            or self.terminal_attempt.execution_output_refs
        ):
            raise ValueError("orphan terminal Attempt must be failed with no outputs")
        for field in (
            "workflow_execution_id",
            "module_run_id",
            "variant_id",
            "attempt_id",
        ):
            if getattr(self.terminal_attempt, field) != getattr(self.orphaned, field):
                raise ValueError(f"orphan disposition {field} differs from Attempt")

    def as_record_batch(self) -> RuntimeRecordBatch:
        """Return the atomic ledger representation."""

        self.validate()
        return RuntimeRecordBatch(
            workflow_execution_id=self.workflow_execution_id,
            transaction_id=self.transaction_id,
            records=(self.terminal_attempt, self.orphaned),
        )


@dataclass(frozen=True)
class AttemptOrphaningReceipt:
    """Commit receipt for one orphaned Attempt disposition."""

    commit_receipt: CommitReceipt
    orphaned_record_id: str


@dataclass(frozen=True)
class OutcomeCommitBatch:
    """Atomic Domain Outcome and local pre-ack checkpoint."""

    workflow_execution_id: str
    transaction_id: str
    outcome: ModuleOutcome
    checkpoint: CheckpointRecord
    records: tuple[RuntimeLedgerRecord, ...] = ()

    def validate(self) -> None:
        """Validate Outcome and checkpoint identity and content hash."""

        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("transaction_id", self.transaction_id)
        self.outcome.validate()
        self.checkpoint.validate()
        if (
            self.outcome.workflow_execution_id != self.workflow_execution_id
            or self.checkpoint.workflow_execution_id != self.workflow_execution_id
            or self.checkpoint.dispatch_id != self.outcome.dispatch_id
            or self.checkpoint.committed_outcome_ref != self.outcome.outcome_ref
            or self.checkpoint.committed_outcome_sha256 != self.outcome.outcome_sha256
        ):
            raise ValueError("CheckpointRecord differs from committed ModuleOutcome")
        for record in self.records:
            runtime_record_as_dict(record)

    def as_record_batch(self) -> RuntimeRecordBatch:
        """Return the atomic ledger representation."""

        self.validate()
        return RuntimeRecordBatch(
            workflow_execution_id=self.workflow_execution_id,
            transaction_id=self.transaction_id,
            records=(*self.records, self.outcome, self.checkpoint),
        )


@dataclass(frozen=True)
class OutcomeCommitReceipt:
    """Atomic Outcome and checkpoint commit receipt."""

    commit_receipt: CommitReceipt
    checkpoint_id: str


@dataclass(frozen=True)
class BackendAcknowledgementReceipt:
    """Receipt for one append-only durable-backend acknowledgement."""

    commit_receipt: CommitReceipt
    backend_acknowledgement_id: str


_RecordT = TypeVar("_RecordT")


@dataclass(frozen=True)
class RuntimeExecutionTrace:
    """Immutable content-free reconstruction of one execution ledger."""

    workflow_execution_id: str
    records: tuple[PersistedRuntimeRecord, ...]
    commit_receipts: tuple[CommitReceipt, ...]

    def records_of_type(self, record_type: type[_RecordT]) -> tuple[_RecordT, ...]:
        """Return records of one Runtime type in append order."""

        return tuple(
            record for record in self.records if isinstance(record, record_type)
        )


def write_immutable_text(path: Path, value: str) -> bool:
    """Write one artifact atomically; identical replay is a no-op, drift fails."""

    encoded = str(value).encode("utf-8")
    if path.exists():
        if path.read_bytes() == encoded:
            return False
        raise FileExistsError(f"refusing to mutate immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists():
        raise FileExistsError(f"stale immutable-write temporary exists: {temporary}")
    temporary.write_bytes(encoded)
    temporary.replace(path)
    return True


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> bool:
    """Write stable pretty JSON through the immutable artifact boundary."""

    text = json.dumps(
        dict(payload),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    return write_immutable_text(path, text)


__all__ = [
    "LegacyAttemptBeginBatch",
    "AttemptBeginReceipt",
    "AttemptClaim",
    "AttemptFinalizationBatch",
    "AttemptFinalizationReceipt",
    "AttemptOutputBundle",
    "AttemptOrphanedRecord",
    "AttemptOrphaningBatch",
    "AttemptOrphaningReceipt",
    "WorkflowAttemptRecord",
    "WorkflowAttemptStartedRecord",
    "BackendAcknowledgementReceipt",
    "BackendAcknowledgementRecord",
    "CommitReceipt",
    "ContextBinding",
    "ContextEvent",
    "CheckpointRecord",
    "LegacyExecutionEntitlementSnapshot",
    "ExecutionAuthorizationBindingRecord",
    "ExecutionAuthorizationInvalidationRecord",
    "ExecutionInputRef",
    "ExecutionOutputRef",
    "ExecutionProfileRecord",
    "ExecutionStartAdmissionRecord",
    "EvaluationCoverageBinding",
    "EvaluationResult",
    "EvaluationRun",
    "EvaluationSet",
    "GatewayOperationEffectRecord",
    "InvocationGatewayEffectBindingRecord",
    "ExternalEventApplicationRecord",
    "InvocationCommitRecord",
    "LegacyAttemptRecord",
    "LegacyModuleRunRecord",
    "LegacyModuleExecutionVariantRecord",
    "ModelCallRecord",
    "ModelUsageRecord",
    "OperationGrantBindingRecord",
    "OperationAuthorizationBindingRecord",
    "OperationAuthorizationRequestRecord",
    "RuntimeExecutionTrace",
    "RuntimeLedgerRecord",
    "RuntimeRecordBatch",
    "Selection",
    "LegacyModuleCapabilityGrant",
    "ModuleOutputResolutionRecord",
    "ModuleDispatchAdmissionRecord",
    "WorkflowModuleRunRecord",
    "WorkflowModuleExecutionVariantRecord",
    "StaleOutputRecord",
    "ToolCallRecord",
    "UsageEvent",
    "UsageMeterDeliveryRecord",
    "UsageMeterOutboxRecord",
    "WorkflowExecutionRecord",
    "attempt_output_bundle_sha256",
    "LegacyOperationGrantBatch",
    "LegacyOperationGrantReceipt",
    "LegacyAuthorizationLedgerRecord",
    "LegacyRuntimeRecordBatch",
    "PersistedRuntimeRecord",
    "OutcomeCommitBatch",
    "OutcomeCommitReceipt",
    "canonical_json",
    "sha256_json",
    "sha256_text",
    "stable_runtime_id",
    "runtime_record_as_dict",
    "legacy_authorization_record_as_dict",
    "write_immutable_json",
    "write_immutable_text",
]
