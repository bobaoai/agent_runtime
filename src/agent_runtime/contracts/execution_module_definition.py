"""Provider-neutral boundary definitions for one Runtime Module invocation.

These values cross the Execution and Provider product-module boundary. They
contain immutable references, hashes, normalized usage, and diagnostics; they
do not choose a provider, execute a Workflow, or persist Runtime records.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol

from .registry_contract_validation import (
    validate_exact_record_tuple,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
)
from .ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleAttemptStartedRecord,
    ModuleExecutionVariantRecord,
    ModuleOutputResolutionRecord,
    ModuleRunRecord,
    ModuleToolCallObservation,
    ModuleUsageObservation,
)
from .registry_release_definition import (
    ExecutionProfileRelease,
    ModuleExecutionPurpose,
    RuntimeModuleRelease,
)


def _canonical_sha256(payload: Mapping[str, Any] | list[Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ModuleInputBinding:
    """One immutable input reference admitted into a Module input closure."""

    logical_name: str
    input_ref: str
    input_sha256: str
    schema_ref: str
    schema_sha256: str
    media_type: str

    def validate(self) -> None:
        """Validate the exact input binding and its immutable references."""

        validate_id("logical_name", self.logical_name)
        validate_opaque_ref("input_ref", self.input_ref)
        validate_sha256("input_sha256", self.input_sha256)
        validate_opaque_ref("schema_ref", self.schema_ref)
        validate_sha256("schema_sha256", self.schema_sha256)
        if type(self.media_type) is not str or "/" not in self.media_type:
            raise ValueError("invalid media_type")

    def as_dict(self) -> dict[str, str]:
        """Return the validated canonical mapping used by closure hashing."""

        self.validate()
        return {
            "logical_name": self.logical_name,
            "input_ref": self.input_ref,
            "input_sha256": self.input_sha256,
            "schema_ref": self.schema_ref,
            "schema_sha256": self.schema_sha256,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class ModuleVariantRequest:
    """One requested sibling Execution Variant under a shared Module Run."""

    arm_key: str
    replicate_index: int
    execution_profile_ref: str
    execution_profile_sha256: str
    prompt_envelope_ref: str | None
    prompt_envelope_sha256: str | None

    def validate(self) -> None:
        """Validate one variant arm and its exact execution dependencies."""

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

    def as_dict(self) -> dict[str, Any]:
        """Return the validated canonical variant-request mapping."""

        self.validate()
        return {
            "arm_key": self.arm_key,
            "replicate_index": self.replicate_index,
            "execution_profile_ref": self.execution_profile_ref,
            "execution_profile_sha256": self.execution_profile_sha256,
            "prompt_envelope_ref": self.prompt_envelope_ref,
            "prompt_envelope_sha256": self.prompt_envelope_sha256,
        }


@dataclass(frozen=True)
class ModuleExecutionRequest:
    """Content-free request for one isolated Module Run."""

    request_id: str
    purpose: ModuleExecutionPurpose
    module_release_ref: str
    module_release_sha256: str
    isolated_scope_ref: str
    isolated_scope_sha256: str
    input_package_ref: str
    input_package_sha256: str
    inputs: tuple[ModuleInputBinding, ...]
    input_closure_sha256: str
    variants: tuple[ModuleVariantRequest, ...]
    idempotency_key: str
    request_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "purpose": self.purpose.value,
            "module_release_ref": self.module_release_ref,
            "module_release_sha256": self.module_release_sha256,
            "isolated_scope_ref": self.isolated_scope_ref,
            "isolated_scope_sha256": self.isolated_scope_sha256,
            "input_package_ref": self.input_package_ref,
            "input_package_sha256": self.input_package_sha256,
            "inputs": [item.as_dict() for item in self.inputs],
            "input_closure_sha256": self.input_closure_sha256,
            "variants": [variant.as_dict() for variant in self.variants],
            "idempotency_key": self.idempotency_key,
        }

    def validate(self) -> None:
        """Validate request identity, closure hashes, and variant uniqueness."""

        validate_id("request_id", self.request_id)
        if type(self.purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        validate_opaque_ref("module_release_ref", self.module_release_ref)
        validate_sha256("module_release_sha256", self.module_release_sha256)
        validate_opaque_ref("isolated_scope_ref", self.isolated_scope_ref)
        validate_sha256("isolated_scope_sha256", self.isolated_scope_sha256)
        validate_opaque_ref("input_package_ref", self.input_package_ref)
        validate_sha256("input_package_sha256", self.input_package_sha256)
        validate_exact_record_tuple(
            "inputs",
            self.inputs,
            expected_type=ModuleInputBinding,
            item_validator=lambda item: item.validate(),
            unique_key=lambda item: item.logical_name,
            unique_key_label="logical_name",
            require_non_empty=False,
        )
        validate_sha256("input_closure_sha256", self.input_closure_sha256)
        if self.input_closure_sha256 != _canonical_sha256(
            [item.as_dict() for item in self.inputs]
        ):
            raise ValueError("Module input closure hash mismatch")
        validate_exact_record_tuple(
            "variants",
            self.variants,
            expected_type=ModuleVariantRequest,
            item_validator=lambda variant: variant.validate(),
            unique_key=lambda variant: f"{variant.arm_key}:{variant.replicate_index}",
            unique_key_label="arm/replicate identity",
            require_non_empty=True,
        )
        validate_id("idempotency_key", self.idempotency_key)
        validate_sha256("request_sha256", self.request_sha256)
        if self.request_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Module execution request hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ModuleExecutionRequest":
        """Build and validate a request with derived closure and request hashes."""

        input_closure_sha256 = _canonical_sha256(
            [item.as_dict() for item in fields["inputs"]]
        )
        prepared = {**fields, "input_closure_sha256": input_closure_sha256}
        provisional = cls(**prepared, request_sha256="0" * 64)
        record = cls(**prepared, request_sha256=_canonical_sha256(provisional._payload()))
        record.validate()
        return record


@dataclass(frozen=True)
class ModuleOutputBinding:
    """One immutable output reference returned by a Module Executor."""

    logical_name: str
    output_ref: str
    output_sha256: str
    schema_ref: str
    schema_sha256: str
    media_type: str

    def validate(self) -> None:
        """Validate the exact output binding and its immutable references."""

        validate_id("logical_name", self.logical_name)
        validate_opaque_ref("output_ref", self.output_ref)
        validate_sha256("output_sha256", self.output_sha256)
        validate_opaque_ref("schema_ref", self.schema_ref)
        validate_sha256("schema_sha256", self.schema_sha256)
        if type(self.media_type) is not str or "/" not in self.media_type:
            raise ValueError("invalid media_type")

    def as_dict(self) -> dict[str, str]:
        """Return the validated canonical output-binding mapping."""

        self.validate()
        return {
            "logical_name": self.logical_name,
            "output_ref": self.output_ref,
            "output_sha256": self.output_sha256,
            "schema_ref": self.schema_ref,
            "schema_sha256": self.schema_sha256,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class ModuleFailureDetailBinding:
    """Cell-local bounded diagnostic retained for one failed Attempt."""

    detail_ref: str
    detail_sha256: str
    media_type: str

    def validate(self) -> None:
        """Validate the bounded diagnostic reference and media type."""

        validate_opaque_ref("detail_ref", self.detail_ref)
        validate_sha256("detail_sha256", self.detail_sha256)
        if type(self.media_type) is not str or "/" not in self.media_type:
            raise ValueError("invalid failure detail media_type")


class ModuleExecutorFailure(RuntimeError):
    """Typed post-invocation failure with Runtime-owned diagnostic lineage."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str,
        failure_code: str,
        usage: ModuleUsageObservation,
        tool_calls: tuple[ModuleToolCallObservation, ...] = (),
        detail: ModuleFailureDetailBinding | None = None,
    ) -> None:
        super().__init__(message)
        validate_id("failure_class", failure_class)
        validate_id("failure_code", failure_code)
        usage.validate()
        validate_exact_record_tuple(
            "tool_calls",
            tool_calls,
            expected_type=ModuleToolCallObservation,
            item_validator=lambda tool_call: tool_call.validate(),
            unique_key=lambda tool_call: tool_call.tool_call_id,
            unique_key_label="tool_call_id",
            require_non_empty=False,
        )
        if detail is not None:
            if type(detail) is not ModuleFailureDetailBinding:
                raise ValueError("detail must be a ModuleFailureDetailBinding")
            detail.validate()
        self.failure_class = failure_class
        self.failure_code = failure_code
        self.usage = usage
        self.tool_calls = tool_calls
        self.detail = detail


@dataclass(frozen=True)
class ModuleExecutorRequest:
    """Variant-bound request passed to one registered Executor implementation."""

    module_run_id: str
    variant_id: str
    attempt_id: str
    module: RuntimeModuleRelease
    execution_profile: ExecutionProfileRelease
    input_package_ref: str
    input_package_sha256: str
    inputs: tuple[ModuleInputBinding, ...]
    prompt_envelope_ref: str | None
    prompt_envelope_sha256: str | None
    isolated_scope_ref: str
    isolated_scope_sha256: str


@dataclass(frozen=True)
class ModuleExecutorResult:
    """Normalized output of one synthetic or admitted Module Executor."""

    outputs: tuple[ModuleOutputBinding, ...]
    usage: ModuleUsageObservation
    tool_calls: tuple[ModuleToolCallObservation, ...] = ()

    def validate(self) -> None:
        """Validate executor outputs and their provider usage observation."""

        validate_exact_record_tuple(
            "outputs",
            self.outputs,
            expected_type=ModuleOutputBinding,
            item_validator=lambda output: output.validate(),
            unique_key=lambda output: output.logical_name,
            unique_key_label="logical_name",
            require_non_empty=True,
        )
        if type(self.usage) is not ModuleUsageObservation:
            raise ValueError("usage must be a ModuleUsageObservation")
        self.usage.validate()
        validate_exact_record_tuple(
            "tool_calls",
            self.tool_calls,
            expected_type=ModuleToolCallObservation,
            item_validator=lambda tool_call: tool_call.validate(),
            unique_key=lambda tool_call: tool_call.tool_call_id,
            unique_key_label="tool_call_id",
            require_non_empty=False,
        )


class ModuleExecutor(Protocol):
    """Replaceable Executor interface for one frozen Module Attempt."""

    def execute(self, request: ModuleExecutorRequest) -> ModuleExecutorResult:
        """Execute one frozen Variant Attempt and return immutable output refs."""


@dataclass(frozen=True)
class ModuleRunResult:
    """Complete content-free result returned by one Module invocation."""

    module_run: ModuleRunRecord
    variants: tuple[ModuleExecutionVariantRecord, ...]
    attempts: tuple[ModuleAttemptRecord, ...]
    outputs: tuple[ModuleOutputBinding, ...]
    resolution: ModuleOutputResolutionRecord | None


class ModuleExecutionLedger(Protocol):
    """Persistence-neutral ledger required by Module invocation orchestration."""

    def existing_result(
        self,
        request: ModuleExecutionRequest,
    ) -> ModuleRunResult | None:
        """Return a committed idempotent result when one already exists."""

    def begin(
        self,
        request: ModuleExecutionRequest,
        module_run: ModuleRunRecord,
        variants: tuple[ModuleExecutionVariantRecord, ...],
        attempt_starts: tuple[ModuleAttemptStartedRecord, ...],
    ) -> ModuleRunResult | None:
        """Claim one Module Run and record its initial lineage."""

    def commit_attempt(self, attempt: ModuleAttemptRecord) -> None:
        """Commit one terminal Attempt record."""

    def commit_result(self, request_id: str, result: ModuleRunResult) -> None:
        """Commit the terminal Module Run result."""


__all__ = [
    "ModuleExecutor",
    "ModuleExecutorFailure",
    "ModuleExecutorRequest",
    "ModuleExecutorResult",
    "ModuleExecutionRequest",
    "ModuleExecutionLedger",
    "ModuleFailureDetailBinding",
    "ModuleInputBinding",
    "ModuleOutputBinding",
    "ModuleRunResult",
    "ModuleVariantRequest",
]
