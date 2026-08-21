"""Canonical provider-neutral lineage records for Runtime Module execution.

This module owns immutable execution records only. The Execution Kernel creates
them, storage adapters persist them, and inspection services project them.
Provider adapters return observations and never construct ledger metadata from
model-authored output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from typing import Any

from ..foundation.foundation_contract_validation import (
    validate_exact_record_tuple,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_utc_timestamp,
)
from .registry_release_definition import ModuleExecutionPurpose


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ModuleUsageObservation:
    """Provider-reported token usage normalized by an admitted Adapter."""

    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None

    def validate(self) -> None:
        """Validate each provider-reported token count when present."""

        for label, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_read_tokens", self.cache_read_tokens),
            ("cache_creation_tokens", self.cache_creation_tokens),
        ):
            if value is not None:
                validate_int(label, value, minimum=0)

    def as_dict(self) -> dict[str, int | None]:
        """Return the validated JSON-ready usage observation."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ModuleToolCallObservation:
    """Content-free lineage for one provider-visible Gateway tool call."""

    tool_call_id: str
    tool_name: str
    request_ref: str
    request_sha256: str
    response_ref: str
    response_sha256: str

    def validate(self) -> None:
        """Validate exact request and response lineage for the tool call."""

        validate_id("tool_call_id", self.tool_call_id)
        validate_id("tool_name", self.tool_name)
        validate_opaque_ref("request_ref", self.request_ref)
        validate_sha256("request_sha256", self.request_sha256)
        validate_opaque_ref("response_ref", self.response_ref)
        validate_sha256("response_sha256", self.response_sha256)

    def as_dict(self) -> dict[str, str]:
        """Return the validated JSON-ready tool-call observation."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ModuleRunRecord:
    """Immutable start record for one isolated or Workflow-bound Module Run."""

    module_run_id: str
    request_id: str
    request_sha256: str
    purpose: ModuleExecutionPurpose
    module_release_ref: str
    module_release_sha256: str
    input_package_ref: str
    input_package_sha256: str
    input_closure_sha256: str
    isolated_scope_ref: str | None
    isolated_scope_sha256: str | None
    recorded_at_utc: str
    workflow_execution_id: str | None = None

    def validate(self) -> None:
        """Validate Module Run identity, release, input, and scope closure."""

        validate_id("module_run_id", self.module_run_id)
        validate_id("request_id", self.request_id)
        if type(self.purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        validate_opaque_ref("module_release_ref", self.module_release_ref)
        validate_opaque_ref("input_package_ref", self.input_package_ref)
        for label, value in (
            ("request_sha256", self.request_sha256),
            ("module_release_sha256", self.module_release_sha256),
            ("input_package_sha256", self.input_package_sha256),
            ("input_closure_sha256", self.input_closure_sha256),
        ):
            validate_sha256(label, value)
        isolated = (
            self.isolated_scope_ref is not None
            or self.isolated_scope_sha256 is not None
        )
        workflow_bound = self.workflow_execution_id is not None
        if isolated == workflow_bound:
            raise ValueError(
                "Module Run requires exactly one isolated or Workflow scope"
            )
        if isolated:
            if (
                self.isolated_scope_ref is None
                or self.isolated_scope_sha256 is None
            ):
                raise ValueError("isolated scope ref and hash must be paired")
            validate_opaque_ref("isolated_scope_ref", self.isolated_scope_ref)
            validate_sha256(
                "isolated_scope_sha256", self.isolated_scope_sha256
            )
        else:
            validate_id(
                "workflow_execution_id", self.workflow_execution_id
            )
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready Module Run record."""

        self.validate()
        payload = {**asdict(self), "purpose": self.purpose.value}
        if self.workflow_execution_id is None:
            payload.pop("workflow_execution_id")
        return payload


@dataclass(frozen=True)
class ModuleExecutionVariantRecord:
    """Immutable behavior-complete configuration under one Module Run."""

    module_run_id: str
    variant_id: str
    arm_key: str
    replicate_index: int
    execution_profile_ref: str
    execution_profile_sha256: str
    prompt_envelope_ref: str | None
    prompt_envelope_sha256: str | None
    input_closure_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate one immutable Variant and its behavior bindings."""

        validate_id("module_run_id", self.module_run_id)
        validate_id("variant_id", self.variant_id)
        validate_id("arm_key", self.arm_key)
        validate_int("replicate_index", self.replicate_index, minimum=0)
        validate_opaque_ref("execution_profile_ref", self.execution_profile_ref)
        validate_sha256(
            "execution_profile_sha256", self.execution_profile_sha256
        )
        if (self.prompt_envelope_ref is None) != (
            self.prompt_envelope_sha256 is None
        ):
            raise ValueError("Prompt Envelope ref and hash must be both set or both null")
        if self.prompt_envelope_ref is not None:
            validate_opaque_ref("prompt_envelope_ref", self.prompt_envelope_ref)
            validate_sha256(
                "prompt_envelope_sha256", self.prompt_envelope_sha256
            )
        validate_sha256("input_closure_sha256", self.input_closure_sha256)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready Variant record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ModuleAttemptStartedRecord:
    """Durable pre-invocation marker for one Module Variant Attempt."""

    module_run_id: str
    variant_id: str
    attempt_id: str
    attempt_ordinal: int
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate the durable Attempt-start marker and ordinal."""

        validate_id("module_run_id", self.module_run_id)
        validate_id("variant_id", self.variant_id)
        validate_id("attempt_id", self.attempt_id)
        validate_int("attempt_ordinal", self.attempt_ordinal, minimum=1)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready Attempt-start record."""

        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ModuleAttemptRecord:
    """Terminal immutable result of one Module Variant Attempt."""

    module_run_id: str
    variant_id: str
    attempt_id: str
    status: str
    output_refs: tuple[str, ...]
    usage: ModuleUsageObservation
    failure_class: str | None
    period_start_at_utc: str
    period_end_at_utc: str
    recorded_at_utc: str
    tool_calls: tuple[ModuleToolCallObservation, ...] = ()
    prompt_envelope_ref: str | None = None
    prompt_envelope_sha256: str | None = None
    failure_detail_ref: str | None = None
    failure_detail_sha256: str | None = None

    def validate(self) -> None:
        """Validate terminal status, outputs, diagnostics, and observations."""

        validate_id("module_run_id", self.module_run_id)
        validate_id("variant_id", self.variant_id)
        validate_id("attempt_id", self.attempt_id)
        if self.status not in {"completed", "failed", "cancelled"}:
            raise ValueError("invalid Module Attempt status")
        if len(self.output_refs) != len(set(self.output_refs)):
            raise ValueError("Module Attempt output refs must be unique")
        for output_ref in self.output_refs:
            validate_opaque_ref("output_ref", output_ref)
        if type(self.usage) is not ModuleUsageObservation:
            raise ValueError("usage must be a ModuleUsageObservation")
        self.usage.validate()
        if self.failure_class is not None:
            validate_id("failure_class", self.failure_class)
        for label, value in (
            ("period_start_at_utc", self.period_start_at_utc),
            ("period_end_at_utc", self.period_end_at_utc),
            ("recorded_at_utc", self.recorded_at_utc),
        ):
            validate_utc_timestamp(label, value)
        period_start = datetime.fromisoformat(
            self.period_start_at_utc[:-1] + "+00:00"
        )
        period_end = datetime.fromisoformat(
            self.period_end_at_utc[:-1] + "+00:00"
        )
        if period_end < period_start:
            raise ValueError(
                "Module Attempt period_end_at_utc precedes period_start_at_utc"
            )
        validate_exact_record_tuple(
            "tool_calls",
            self.tool_calls,
            expected_type=ModuleToolCallObservation,
            item_validator=lambda item: item.validate(),
            unique_key=lambda item: item.tool_call_id,
            unique_key_label="tool_call_id",
            require_non_empty=False,
        )
        for ref_label, sha_label, ref_value, sha_value in (
            (
                "prompt_envelope_ref",
                "prompt_envelope_sha256",
                self.prompt_envelope_ref,
                self.prompt_envelope_sha256,
            ),
            (
                "failure_detail_ref",
                "failure_detail_sha256",
                self.failure_detail_ref,
                self.failure_detail_sha256,
            ),
        ):
            if (ref_value is None) != (sha_value is None):
                raise ValueError(f"{ref_label} and {sha_label} must be paired")
            if ref_value is not None:
                validate_opaque_ref(ref_label, ref_value)
                validate_sha256(sha_label, sha_value)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready terminal Attempt record."""

        self.validate()
        payload = asdict(self)
        payload["output_refs"] = list(self.output_refs)
        payload["usage"] = self.usage.as_dict()
        payload["tool_calls"] = [item.as_dict() for item in self.tool_calls]
        return payload


@dataclass(frozen=True)
class ModuleOutputResolutionRecord:
    """Only Runtime authority that permits Attempt output to flow downstream."""

    module_output_resolution_id: str
    workflow_execution_id: str | None
    source_module_run_id: str
    resolution_mode: str
    candidate_output_bundle_refs: tuple[str, ...]
    candidate_output_bundle_sha256s: tuple[str, ...]
    evaluation_set_ref: str | None
    selection_ref: str | None
    resolved_execution_output_refs: tuple[str, ...]
    resolution_status: str
    resolution_sha256: str
    recorded_at_utc: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("resolution_sha256")
        for field in (
            "candidate_output_bundle_refs",
            "candidate_output_bundle_sha256s",
            "resolved_execution_output_refs",
        ):
            payload[field] = list(payload[field])
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "ModuleOutputResolutionRecord":
        """Build a content-addressed downstream output authority record."""

        payload = dict(fields)
        for field in (
            "candidate_output_bundle_refs",
            "candidate_output_bundle_sha256s",
            "resolved_execution_output_refs",
        ):
            payload[field] = tuple(payload[field])
        provisional = cls(**payload, resolution_sha256="0" * 64)
        record = cls(
            **payload,
            resolution_sha256=_sha256_json(provisional._identity_payload()),
        )
        record.validate()
        return record

    def validate(self) -> None:
        """Validate candidate, evaluation, selection, and output closure."""

        for label, value in (
            ("module_output_resolution_id", self.module_output_resolution_id),
            ("source_module_run_id", self.source_module_run_id),
            ("resolution_mode", self.resolution_mode),
            ("resolution_status", self.resolution_status),
        ):
            validate_id(label, value)
        if self.workflow_execution_id is not None:
            validate_id("workflow_execution_id", self.workflow_execution_id)
        if self.resolution_mode not in {
            "direct_single",
            "evaluated_single",
            "selected",
        }:
            raise ValueError("unsupported output resolution mode")
        if self.resolution_status != "resolved":
            raise ValueError("downstream output resolution must be resolved")
        for label, values in (
            ("candidate_output_bundle_refs", self.candidate_output_bundle_refs),
            ("resolved_execution_output_refs", self.resolved_execution_output_refs),
        ):
            if not values:
                if label == "candidate_output_bundle_refs":
                    raise ValueError("candidate output bundles are required")
                raise ValueError("resolved output refs are required")
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
            for value in values:
                validate_opaque_ref(label, value)
        if len(self.candidate_output_bundle_refs) != len(
            self.candidate_output_bundle_sha256s
        ):
            raise ValueError("candidate bundle refs and hashes disagree")
        for value in self.candidate_output_bundle_sha256s:
            validate_sha256("candidate_output_bundle_sha256", value)
        for label, value in (
            ("evaluation_set_ref", self.evaluation_set_ref),
            ("selection_ref", self.selection_ref),
        ):
            if value is not None:
                validate_opaque_ref(label, value)
        if self.resolution_mode == "direct_single" and (
            self.evaluation_set_ref is not None or self.selection_ref is not None
        ):
            raise ValueError("direct_single forbids evaluation and selection refs")
        if self.resolution_mode == "direct_single" and len(
            self.candidate_output_bundle_refs
        ) != 1:
            raise ValueError("direct_single requires exactly one candidate bundle")
        if self.resolution_mode == "evaluated_single" and (
            len(self.candidate_output_bundle_refs) != 1
            or self.evaluation_set_ref is None
            or self.selection_ref is not None
        ):
            raise ValueError(
                "evaluated_single requires one candidate and one evaluation set"
            )
        if self.resolution_mode == "selected" and (
            len(self.candidate_output_bundle_refs) < 2
            or self.evaluation_set_ref is None
            or self.selection_ref is None
        ):
            raise ValueError(
                "selected requires sibling candidates, evaluations, and Selection"
            )
        validate_sha256("resolution_sha256", self.resolution_sha256)
        if self.resolution_sha256 != _sha256_json(self._identity_payload()):
            raise ValueError("module output resolution hash mismatch")
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-ready output resolution."""

        self.validate()
        return {
            **self._identity_payload(),
            "resolution_sha256": self.resolution_sha256,
        }


__all__ = [
    "ModuleAttemptRecord",
    "ModuleAttemptStartedRecord",
    "ModuleExecutionVariantRecord",
    "ModuleOutputResolutionRecord",
    "ModuleRunRecord",
    "ModuleToolCallObservation",
    "ModuleUsageObservation",
]
