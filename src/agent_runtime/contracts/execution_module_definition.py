"""Provider-neutral boundary definitions for one Runtime Module invocation.

These values cross the Execution and Provider product-module boundary. They
contain immutable references, hashes, normalized usage, and diagnostics; they
do not choose a provider, execute a Workflow, or persist Runtime records. The
provider adapter boundary itself is the canonical
``AuthorizedAgentExecutionAdapter`` contract in
``invocation_adapter_definition``.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping, Protocol

from ..foundation.foundation_contract_validation import (
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
)
from .registry_release_definition import ModuleExecutionPurpose


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
class WorkflowModuleExecutionRequest:
    """Content-free request for one Module Run inside a Workflow Execution.

    Unlike :class:`ModuleExecutionRequest`, this request is not an isolated
    evaluation scope.  Its authority, input package, provider invocation, and
    resulting lineage are all bound to an already admitted Workflow Execution.
    The durable Workflow driver owns ``dispatch_id`` and ``workflow_node_id``;
    Runtime owns provider execution and canonical ledger recording.
    """

    request_id: str
    purpose: ModuleExecutionPurpose
    workflow_execution_id: str
    dispatch_id: str
    workflow_node_id: str
    module_run_id: str
    module_release_ref: str
    module_release_sha256: str
    input_package_ref: str
    input_package_sha256: str
    inputs: tuple[ModuleInputBinding, ...]
    input_closure_sha256: str
    variants: tuple[ModuleVariantRequest, ...]
    idempotency_key: str
    request_sha256: str
    attempt_ordinal: int = 1
    parent_attempt_id: str | None = None

    def _payload(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "purpose": self.purpose.value,
            "workflow_execution_id": self.workflow_execution_id,
            "dispatch_id": self.dispatch_id,
            "workflow_node_id": self.workflow_node_id,
            "module_run_id": self.module_run_id,
            "module_release_ref": self.module_release_ref,
            "module_release_sha256": self.module_release_sha256,
            "input_package_ref": self.input_package_ref,
            "input_package_sha256": self.input_package_sha256,
            "inputs": [item.as_dict() for item in self.inputs],
            "input_closure_sha256": self.input_closure_sha256,
            "variants": [variant.as_dict() for variant in self.variants],
            "idempotency_key": self.idempotency_key,
            "attempt_ordinal": self.attempt_ordinal,
            "parent_attempt_id": self.parent_attempt_id,
        }

    def validate(self) -> None:
        """Validate exact Workflow, Module, input, and Variant identity."""

        for label, value in (
            ("request_id", self.request_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("dispatch_id", self.dispatch_id),
            ("workflow_node_id", self.workflow_node_id),
            ("module_run_id", self.module_run_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        validate_int("attempt_ordinal", self.attempt_ordinal, minimum=1)
        if self.attempt_ordinal == 1:
            if self.parent_attempt_id is not None:
                raise ValueError("first Attempt cannot declare a parent Attempt")
        else:
            if self.parent_attempt_id is None:
                raise ValueError("retry Attempt requires parent_attempt_id")
            validate_id("parent_attempt_id", self.parent_attempt_id)
        if type(self.purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        if self.purpose not in {
            ModuleExecutionPurpose.WORKFLOW,
            ModuleExecutionPurpose.EVALUATION,
            ModuleExecutionPurpose.TEST,
            ModuleExecutionPurpose.REPLAY,
        }:
            raise ValueError(
                "Workflow Module execution requires a workflow-bound purpose"
            )
        validate_opaque_ref("module_release_ref", self.module_release_ref)
        validate_sha256("module_release_sha256", self.module_release_sha256)
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
            raise ValueError("Workflow Module input closure hash mismatch")
        validate_exact_record_tuple(
            "variants",
            self.variants,
            expected_type=ModuleVariantRequest,
            item_validator=lambda variant: variant.validate(),
            unique_key=lambda variant: f"{variant.arm_key}:{variant.replicate_index}",
            unique_key_label="arm/replicate identity",
            require_non_empty=True,
        )
        validate_sha256("request_sha256", self.request_sha256)
        if self.request_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Workflow Module execution request hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "WorkflowModuleExecutionRequest":
        """Build and validate one content-addressed Workflow Module request."""

        input_closure_sha256 = _canonical_sha256(
            [item.as_dict() for item in fields["inputs"]]
        )
        prepared = {**fields, "input_closure_sha256": input_closure_sha256}
        provisional = cls(**prepared, request_sha256="0" * 64)
        record = cls(
            **prepared,
            request_sha256=_canonical_sha256(provisional._payload()),
        )
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
    "ModuleExecutionRequest",
    "ModuleExecutionLedger",
    "ModuleFailureDetailBinding",
    "ModuleInputBinding",
    "ModuleOutputBinding",
    "ModuleRunResult",
    "ModuleVariantRequest",
    "WorkflowModuleExecutionRequest",
]
