"""Immutable release and execution contracts for Runtime Modules.

This module is the additive target-model surface.  It deliberately does not
import or alias the predecessor Module/Module contracts.  Domain meaning stays
opaque; Runtime validates identity, hashes, dependency closure, and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, ClassVar, Mapping

from ..foundation.foundation_contract_validation import (
    validate_exact_record_tuple,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_string_tuple,
    validate_utc_timestamp,
)


_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,127}$")
_MEDIA_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,63}$"
)

# Shared capability vocabularies. The Execution Profile contract and the
# adapter-descriptor contract must accept the same mode sets; each owning a
# private copy is how the two drift apart.
EXECUTION_MODES = frozenset({"tool_free", "agent"})
SEMANTIC_INPUT_DELIVERY_MODES = frozenset(
    {"inline", "gateway_read", "managed_attachment", "hybrid"}
)
NETWORK_POLICIES = frozenset({"denied", "gateway_only", "direct_sandboxed"})
OUTPUT_CONSTRAINT_MODES = frozenset(
    {"prompt_only_json", "native_structured_output"}
)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_token(label: str, value: Any) -> None:
    if type(value) is not str or not _TOKEN_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}")


def _validate_optional_opaque_ref(label: str, value: Any) -> None:
    if value is not None:
        validate_opaque_ref(label, value)


def _validate_optional_sha256(label: str, value: Any) -> None:
    if value is not None:
        validate_sha256(label, value)


class ModuleKind(StrEnum):
    """Executable implementation family of one Runtime Module."""

    AGENT = "agent"
    DETERMINISTIC = "deterministic"
    HUMAN_TASK = "human_task"
    EXTERNAL_SERVICE = "external_service"


class ModuleEntryPolicy(StrEnum):
    """Whether a production caller may enter a Module outside a Workflow."""

    WORKFLOW_BOUND = "workflow_bound"
    STANDALONE_ALLOWED = "standalone_allowed"


class ModuleExecutionPurpose(StrEnum):
    """Declared purpose of one Module Run."""

    WORKFLOW = "workflow"
    STANDALONE = "standalone"
    EVALUATION = "evaluation"
    TEST = "test"
    REPLAY = "replay"


class WorkflowNodeKind(StrEnum):
    """Whether one workflow-local position executes a Module or holds control."""

    MODULE = "module"
    CONTROL = "control"


class WorkflowParallelJoinPolicy(StrEnum):
    """How Runtime closes one immutable parallel branch group."""

    ALL_REQUIRED = "all_required"


class OutputResolutionPolicy(StrEnum):
    """Rule controlling which Module output may leave the Runtime."""

    DIRECT_SINGLE = "direct_single"
    EVALUATED_SINGLE = "evaluated_single"
    SELECTED = "selected"


class ReleaseSubjectKind(StrEnum):
    """Persisted release family governed by an admission record."""

    SCHEMA_ASSET = "schema_asset"
    PROMPT_COMPONENT = "prompt_component"
    PROMPT_BUNDLE = "prompt_bundle"
    BEHAVIOR_POLICY = "behavior_policy"
    EVALUATION_POLICY = "evaluation_policy"
    RETRY_POLICY = "retry_policy"
    EXECUTION_VARIANT_POLICY = "execution_variant_policy"
    EXECUTION_PROFILE = "execution_profile"
    RUNTIME_MODULE = "runtime_module"
    WORKFLOW = "workflow"


class ReleaseAdmissionState(StrEnum):
    """Append-only admission state of one exact immutable release."""

    CANDIDATE = "candidate"
    SHADOW_EXECUTABLE = "shadow_executable"
    PRODUCTION_CANARY = "production_canary"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETIRED = "retired"


class PromptComponentKind(StrEnum):
    """Semantic position of one static component in a Module Prompt."""

    TASK_INSTRUCTION = "task_instruction"
    OUTPUT_CONSTRAINT = "output_constraint"


def is_prompt_component_member_ref(member_ref: str) -> bool:
    """Report whether one bundle member ref names a Prompt Component release.

    Every consumer must use this single predicate; per-call-site prefix
    parsing is how a new member scheme silently falls through closure and
    admission checks. A typed member kind on ``ReleaseMember`` replaces this
    at the Module-model cutover.
    """

    return member_ref.startswith("prompt-component:")


@dataclass(frozen=True)
class ReleaseMember:
    """One immutable member selected into a Skill or Prompt release closure."""

    record_type: ClassVar[str] = "release_member"

    member_ref: str
    member_sha256: str
    media_type: str

    def validate(self) -> None:
        """Validate the immutable member reference and media type."""

        validate_opaque_ref("member_ref", self.member_ref)
        validate_sha256("member_sha256", self.member_sha256)
        if type(self.media_type) is not str or not _MEDIA_TYPE_PATTERN.fullmatch(
            self.media_type
        ):
            raise ValueError("invalid media_type")

    def as_dict(self) -> dict[str, str]:
        """Return the canonical JSON-compatible member body."""

        self.validate()
        return {
            "member_ref": self.member_ref,
            "member_sha256": self.member_sha256,
            "media_type": self.media_type,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReleaseMember":
        """Reconstruct one member from persisted canonical JSON."""

        return cls(
            member_ref=payload["member_ref"],
            member_sha256=payload["member_sha256"],
            media_type=payload["media_type"],
        )


@dataclass(frozen=True)
class SchemaAssetRelease:
    """One immutable JSON Schema asset stored by the Runtime control plane."""

    record_type: ClassVar[str] = "schema_asset_release"

    schema_asset_id: str
    schema_asset_version: str
    release_ref: str
    canonical_schema_json: str
    schema_sha256: str
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_asset_id": self.schema_asset_id,
            "schema_asset_version": self.schema_asset_version,
            "release_ref": self.release_ref,
            "canonical_schema_json": self.canonical_schema_json,
            "schema_sha256": self.schema_sha256,
        }

    def schema_document(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible schema document."""

        payload = json.loads(self.canonical_schema_json)
        if type(payload) is not dict:  # pragma: no cover - guarded by validate
            raise ValueError("schema asset document must be one JSON object")
        return payload

    def validate(self) -> None:
        """Validate schema identity, canonical bytes, and release hashes."""

        validate_snake_case_name("schema_asset_id", self.schema_asset_id)
        _validate_token("schema_asset_version", self.schema_asset_version)
        validate_opaque_ref("release_ref", self.release_ref)
        expected_release_ref = (
            f"schema:{self.schema_asset_id}@{self.schema_asset_version}"
        )
        if self.release_ref != expected_release_ref:
            raise ValueError(
                "schema asset release_ref must match schema_asset_id and version"
            )
        if type(self.canonical_schema_json) is not str:
            raise ValueError("canonical_schema_json must be a string")
        try:
            schema_document = json.loads(self.canonical_schema_json)
        except json.JSONDecodeError as exc:
            raise ValueError("canonical_schema_json must be valid JSON") from exc
        if type(schema_document) is not dict:
            raise ValueError("schema asset document must be one JSON object")
        canonical = json.dumps(
            schema_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical != self.canonical_schema_json:
            raise ValueError("schema asset JSON must use canonical serialization")
        if schema_document.get("$id") != self.release_ref:
            raise ValueError("schema asset $id must equal release_ref")
        if schema_document.get("$schema") != (
            "https://json-schema.org/draft/2020-12/schema"
        ):
            raise ValueError("schema asset must declare JSON Schema Draft 2020-12")
        validate_sha256("schema_sha256", self.schema_sha256)
        observed_schema_sha256 = hashlib.sha256(
            self.canonical_schema_json.encode("utf-8")
        ).hexdigest()
        if self.schema_sha256 != observed_schema_sha256:
            raise ValueError("schema asset content hash mismatch")
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Schema Asset release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible Schema Asset release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(
        cls,
        *,
        schema_asset_id: str,
        schema_asset_version: str,
        release_ref: str,
        schema_document: Mapping[str, Any],
    ) -> "SchemaAssetRelease":
        """Build a hash-complete release from one JSON Schema document."""

        if not isinstance(schema_document, Mapping):
            raise ValueError("schema_document must be a mapping")
        canonical_schema_json = json.dumps(
            dict(schema_document),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        schema_sha256 = hashlib.sha256(
            canonical_schema_json.encode("utf-8")
        ).hexdigest()
        fields = {
            "schema_asset_id": schema_asset_id,
            "schema_asset_version": schema_asset_version,
            "release_ref": release_ref,
            "canonical_schema_json": canonical_schema_json,
            "schema_sha256": schema_sha256,
        }
        provisional = cls(**fields, release_sha256="0" * 64)
        record = cls(
            **fields,
            release_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "SchemaAssetRelease":
        """Reconstruct one persisted Schema Asset release."""

        return cls(
            schema_asset_id=payload["schema_asset_id"],
            schema_asset_version=payload["schema_asset_version"],
            release_ref=payload["release_ref"],
            canonical_schema_json=payload["canonical_schema_json"],
            schema_sha256=payload["schema_sha256"],
            release_sha256=payload["release_sha256"],
        )


@dataclass(frozen=True)
class _PolicyRelease:
    """Shared immutable payload shape for one typed Runtime policy family."""

    record_type: ClassVar[str] = "policy_release"
    release_prefix: ClassVar[str] = "policy"

    policy_id: str
    policy_version: str
    release_ref: str
    policy_schema_ref: str
    policy_schema_sha256: str
    canonical_policy_json: str
    policy_sha256: str
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "release_ref": self.release_ref,
            "policy_schema_ref": self.policy_schema_ref,
            "policy_schema_sha256": self.policy_schema_sha256,
            "canonical_policy_json": self.canonical_policy_json,
            "policy_sha256": self.policy_sha256,
        }

    def policy_document(self) -> dict[str, Any]:
        """Return a fresh JSON-compatible policy document."""

        payload = json.loads(self.canonical_policy_json)
        if type(payload) is not dict:  # pragma: no cover - guarded by validate
            raise ValueError("policy document must be one JSON object")
        return payload

    def validate(self) -> None:
        """Validate policy identity, canonical content, and hashes."""

        validate_snake_case_name("policy_id", self.policy_id)
        _validate_token("policy_version", self.policy_version)
        expected_ref = (
            f"{self.release_prefix}:{self.policy_id}@{self.policy_version}"
        )
        if self.release_ref != expected_ref:
            raise ValueError(
                f"{type(self).__name__} release_ref must match ID and version"
            )
        validate_opaque_ref("policy_schema_ref", self.policy_schema_ref)
        validate_sha256("policy_schema_sha256", self.policy_schema_sha256)
        if type(self.canonical_policy_json) is not str:
            raise ValueError("canonical_policy_json must be a string")
        try:
            document = json.loads(self.canonical_policy_json)
        except json.JSONDecodeError as exc:
            raise ValueError("canonical_policy_json must be valid JSON") from exc
        if type(document) is not dict:
            raise ValueError("policy document must be one JSON object")
        canonical = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        if canonical != self.canonical_policy_json:
            raise ValueError("policy JSON must use canonical serialization")
        validate_sha256("policy_sha256", self.policy_sha256)
        observed_policy_sha256 = hashlib.sha256(
            self.canonical_policy_json.encode("utf-8")
        ).hexdigest()
        if self.policy_sha256 != observed_policy_sha256:
            raise ValueError("policy content hash mismatch")
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError(f"{type(self).__name__} release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible policy release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(
        cls,
        *,
        policy_id: str,
        policy_version: str,
        release_ref: str,
        policy_schema_ref: str,
        policy_schema_sha256: str,
        policy_document: Mapping[str, Any],
    ) -> "_PolicyRelease":
        """Build one hash-complete typed policy release."""

        if not isinstance(policy_document, Mapping):
            raise ValueError("policy_document must be a mapping")
        canonical_policy_json = json.dumps(
            dict(policy_document),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        fields = {
            "policy_id": policy_id,
            "policy_version": policy_version,
            "release_ref": release_ref,
            "policy_schema_ref": policy_schema_ref,
            "policy_schema_sha256": policy_schema_sha256,
            "canonical_policy_json": canonical_policy_json,
            "policy_sha256": hashlib.sha256(
                canonical_policy_json.encode("utf-8")
            ).hexdigest(),
        }
        provisional = cls(**fields, release_sha256="0" * 64)
        record = cls(
            **fields,
            release_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "_PolicyRelease":
        """Reconstruct one persisted typed policy release."""

        record = cls(
            policy_id=payload["policy_id"],
            policy_version=payload["policy_version"],
            release_ref=payload["release_ref"],
            policy_schema_ref=payload["policy_schema_ref"],
            policy_schema_sha256=payload["policy_schema_sha256"],
            canonical_policy_json=payload["canonical_policy_json"],
            policy_sha256=payload["policy_sha256"],
            release_sha256=payload["release_sha256"],
        )
        record.validate()
        return record


@dataclass(frozen=True)
class BehaviorPolicyRelease(_PolicyRelease):
    """Immutable Context-isolation behavior selected by one Module."""

    record_type: ClassVar[str] = "behavior_policy_release"
    release_prefix: ClassVar[str] = "behavior-policy"


@dataclass(frozen=True)
class EvaluationPolicyRelease(_PolicyRelease):
    """Immutable evaluation behavior selected by one Module."""

    record_type: ClassVar[str] = "evaluation_policy_release"
    release_prefix: ClassVar[str] = "evaluation-policy"


@dataclass(frozen=True)
class RetryPolicyRelease(_PolicyRelease):
    """Immutable retry limit selected by one Module."""

    record_type: ClassVar[str] = "retry_policy_release"
    release_prefix: ClassVar[str] = "retry-policy"


@dataclass(frozen=True)
class ExecutionVariantPolicyRelease(_PolicyRelease):
    """Immutable ordered Execution Profile selection for one exact origin."""

    record_type: ClassVar[str] = "execution_variant_policy_release"
    release_prefix: ClassVar[str] = "execution-variant-policy"


@dataclass(frozen=True)
class PromptComponentRelease:
    """Immutable model-ready static Prompt content with exact source lineage."""

    record_type: ClassVar[str] = "prompt_component_release"

    prompt_component_id: str
    prompt_component_version: str
    release_ref: str
    component_kind: PromptComponentKind
    media_type: str
    formatter_id: str
    formatter_version: str
    source_members: tuple[ReleaseMember, ...]
    formatted_content: str
    formatted_content_sha256: str
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "prompt_component_id": self.prompt_component_id,
            "prompt_component_version": self.prompt_component_version,
            "release_ref": self.release_ref,
            "component_kind": self.component_kind.value,
            "media_type": self.media_type,
            "formatter_id": self.formatter_id,
            "formatter_version": self.formatter_version,
            "source_members": [member.as_dict() for member in self.source_members],
            "formatted_content": self.formatted_content,
            "formatted_content_sha256": self.formatted_content_sha256,
        }

    def validate(self) -> None:
        """Validate identity, formatted bytes, source closure, and hashes."""

        validate_snake_case_name("prompt_component_id", self.prompt_component_id)
        _validate_token("prompt_component_version", self.prompt_component_version)
        expected_ref = (
            "prompt-component:"
            f"{self.prompt_component_id}@{self.prompt_component_version}"
        )
        if self.release_ref != expected_ref:
            raise ValueError(
                "Prompt Component release_ref must match ID and version"
            )
        if type(self.component_kind) is not PromptComponentKind:
            raise ValueError("component_kind must be a PromptComponentKind")
        if type(self.media_type) is not str or not _MEDIA_TYPE_PATTERN.fullmatch(
            self.media_type
        ):
            raise ValueError("invalid media_type")
        validate_snake_case_name("formatter_id", self.formatter_id)
        _validate_token("formatter_version", self.formatter_version)
        validate_exact_record_tuple(
            "source_members",
            self.source_members,
            expected_type=ReleaseMember,
            item_validator=lambda member: member.validate(),
            unique_key=lambda member: member.member_ref,
            unique_key_label="member_ref",
            require_non_empty=True,
        )
        if type(self.formatted_content) is not str or not self.formatted_content:
            raise ValueError("formatted_content is required")
        if "\x00" in self.formatted_content:
            raise ValueError("formatted_content contains a null byte")
        validate_sha256("formatted_content_sha256", self.formatted_content_sha256)
        observed_content_sha256 = hashlib.sha256(
            self.formatted_content.encode("utf-8")
        ).hexdigest()
        if self.formatted_content_sha256 != observed_content_sha256:
            raise ValueError("Prompt Component content hash mismatch")
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Prompt Component release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible component release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "PromptComponentRelease":
        """Build one hash-complete immutable component release."""

        formatted_content_sha256 = hashlib.sha256(
            fields["formatted_content"].encode("utf-8")
        ).hexdigest()
        prepared = {
            **fields,
            "formatted_content_sha256": formatted_content_sha256,
        }
        provisional = cls(**prepared, release_sha256="0" * 64)
        record = cls(
            **prepared,
            release_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "PromptComponentRelease":
        """Reconstruct one persisted component release."""

        return cls(
            prompt_component_id=payload["prompt_component_id"],
            prompt_component_version=payload["prompt_component_version"],
            release_ref=payload["release_ref"],
            component_kind=PromptComponentKind(payload["component_kind"]),
            media_type=payload["media_type"],
            formatter_id=payload["formatter_id"],
            formatter_version=payload["formatter_version"],
            source_members=tuple(
                ReleaseMember.from_dict(member)
                for member in payload["source_members"]
            ),
            formatted_content=payload["formatted_content"],
            formatted_content_sha256=payload["formatted_content_sha256"],
            release_sha256=payload["release_sha256"],
        )


@dataclass(frozen=True)
class PromptBundleRelease:
    """Static, globally managed Prompt Bundle with no execution-specific content."""

    record_type: ClassVar[str] = "prompt_bundle_release"

    prompt_bundle_id: str
    prompt_bundle_version: str
    release_ref: str
    compiler_version: str
    members: tuple[ReleaseMember, ...]
    compiled_static_body: str
    compiled_static_body_sha256: str
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "prompt_bundle_id": self.prompt_bundle_id,
            "prompt_bundle_version": self.prompt_bundle_version,
            "release_ref": self.release_ref,
            "compiler_version": self.compiler_version,
            "members": [member.as_dict() for member in self.members],
            "compiled_static_body": self.compiled_static_body,
            "compiled_static_body_sha256": self.compiled_static_body_sha256,
        }

    def validate(self) -> None:
        """Validate static Prompt membership, body integrity, and release hash."""

        validate_snake_case_name("prompt_bundle_id", self.prompt_bundle_id)
        _validate_token("prompt_bundle_version", self.prompt_bundle_version)
        validate_opaque_ref("release_ref", self.release_ref)
        _validate_token("compiler_version", self.compiler_version)
        validate_exact_record_tuple(
            "members",
            self.members,
            expected_type=ReleaseMember,
            item_validator=lambda member: member.validate(),
            unique_key=lambda member: member.member_ref,
            unique_key_label="member_ref",
            require_non_empty=True,
        )
        if type(self.compiled_static_body) is not str or not self.compiled_static_body:
            raise ValueError("compiled_static_body is required")
        if "\x00" in self.compiled_static_body:
            raise ValueError("compiled_static_body contains a null byte")
        validate_sha256(
            "compiled_static_body_sha256", self.compiled_static_body_sha256
        )
        body_sha256 = hashlib.sha256(
            self.compiled_static_body.encode("utf-8")
        ).hexdigest()
        if self.compiled_static_body_sha256 != body_sha256:
            raise ValueError("compiled static Prompt body hash mismatch")
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Prompt Bundle release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible Prompt Bundle release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "PromptBundleRelease":
        """Build a hash-complete immutable Prompt Bundle Release."""

        body_sha256 = hashlib.sha256(
            fields["compiled_static_body"].encode("utf-8")
        ).hexdigest()
        prepared = {**fields, "compiled_static_body_sha256": body_sha256}
        provisional = cls(**prepared, release_sha256="0" * 64)
        record = cls(**prepared, release_sha256=_canonical_sha256(provisional._payload()))
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PromptBundleRelease":
        """Reconstruct a persisted Prompt Bundle Release."""

        return cls(
            prompt_bundle_id=payload["prompt_bundle_id"],
            prompt_bundle_version=payload["prompt_bundle_version"],
            release_ref=payload["release_ref"],
            compiler_version=payload["compiler_version"],
            members=tuple(
                ReleaseMember.from_dict(member) for member in payload["members"]
            ),
            compiled_static_body=payload["compiled_static_body"],
            compiled_static_body_sha256=payload["compiled_static_body_sha256"],
            release_sha256=payload["release_sha256"],
        )


@dataclass(frozen=True)
class ExecutionProfileRelease:
    """Immutable provider and execution configuration available to Variants."""

    record_type: ClassVar[str] = "execution_profile_release"

    execution_profile_id: str
    execution_profile_version: str
    release_ref: str
    executor_adapter_id: str
    executor_adapter_revision: str
    transport_kind: str
    provider_id: str
    model_id: str
    reasoning_profile: str
    execution_mode: str
    semantic_input_delivery_mode: str
    attempt_workspace_policy: str
    gateway_access_reasons: tuple[str, ...]
    output_constraint_mode: str
    tool_policy: tuple[str, ...]
    network_policy: str
    timeout_seconds: int
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "execution_profile_id": self.execution_profile_id,
            "execution_profile_version": self.execution_profile_version,
            "release_ref": self.release_ref,
            "executor_adapter_id": self.executor_adapter_id,
            "executor_adapter_revision": self.executor_adapter_revision,
            "transport_kind": self.transport_kind,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "reasoning_profile": self.reasoning_profile,
            "execution_mode": self.execution_mode,
            "semantic_input_delivery_mode": self.semantic_input_delivery_mode,
            "attempt_workspace_policy": self.attempt_workspace_policy,
            "gateway_access_reasons": list(self.gateway_access_reasons),
            "output_constraint_mode": self.output_constraint_mode,
            "tool_policy": list(self.tool_policy),
            "network_policy": self.network_policy,
            "timeout_seconds": self.timeout_seconds,
        }

    def validate(self) -> None:
        """Validate Executor configuration and behavior-complete release hash."""

        validate_snake_case_name(
            "execution_profile_id", self.execution_profile_id
        )
        _validate_token("execution_profile_version", self.execution_profile_version)
        validate_opaque_ref("release_ref", self.release_ref)
        validate_snake_case_name("executor_adapter_id", self.executor_adapter_id)
        _validate_token("executor_adapter_revision", self.executor_adapter_revision)
        _validate_token("transport_kind", self.transport_kind)
        validate_snake_case_name("provider_id", self.provider_id)
        validate_id("model_id", self.model_id)
        _validate_token("reasoning_profile", self.reasoning_profile)
        if self.execution_mode not in EXECUTION_MODES:
            raise ValueError("invalid Execution Profile execution_mode")
        if self.semantic_input_delivery_mode not in (
            SEMANTIC_INPUT_DELIVERY_MODES
        ):
            raise ValueError(
                "invalid Execution Profile semantic_input_delivery_mode"
            )
        if self.attempt_workspace_policy not in {
            "none",
            "own_draft_read_write",
        }:
            raise ValueError(
                "invalid Execution Profile attempt_workspace_policy"
            )
        validate_string_tuple(
            "gateway_access_reasons",
            self.gateway_access_reasons,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=False,
        )
        allowed_gateway_access_reasons = {
            "entitlement_specific_semantic_search",
            "external_fact_verification",
            "unfrozen_input_set",
            "oversized_knowledge_retrieval",
            "authorized_package_external_exploration",
        }
        if not set(self.gateway_access_reasons).issubset(
            allowed_gateway_access_reasons
        ):
            raise ValueError(
                "invalid Execution Profile gateway_access_reasons"
            )
        if tuple(sorted(set(self.gateway_access_reasons))) != (
            self.gateway_access_reasons
        ):
            raise ValueError(
                "Execution Profile gateway_access_reasons must be sorted and unique"
            )
        if self.output_constraint_mode not in OUTPUT_CONSTRAINT_MODES:
            raise ValueError("invalid Execution Profile output_constraint_mode")
        validate_string_tuple(
            "tool_policy",
            self.tool_policy,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=False,
        )
        if len(self.tool_policy) != len(set(self.tool_policy)):
            raise ValueError("Execution Profile tool_policy must be unique")
        if self.network_policy not in NETWORK_POLICIES:
            raise ValueError("invalid Execution Profile network_policy")
        if self.execution_mode == "tool_free" and self.tool_policy:
            raise ValueError("tool_free Execution Profile cannot declare tools")
        if self.execution_mode == "tool_free" and self.network_policy != "denied":
            raise ValueError("tool_free Execution Profile must deny network")
        if (
            self.execution_mode == "tool_free"
            and self.semantic_input_delivery_mode != "inline"
        ):
            raise ValueError(
                "tool_free Execution Profile requires inline semantic input"
            )
        if (
            self.execution_mode == "tool_free"
            and self.attempt_workspace_policy != "none"
        ):
            raise ValueError(
                "tool_free Execution Profile cannot grant a draft workspace"
            )
        gateway_delivery = self.semantic_input_delivery_mode in {
            "gateway_read",
            "hybrid",
        }
        if gateway_delivery and not self.tool_policy:
            raise ValueError(
                "Gateway semantic input delivery requires registered tools"
            )
        if gateway_delivery and not self.gateway_access_reasons:
            raise ValueError(
                "Gateway semantic input delivery requires an admitted reason"
            )
        if gateway_delivery and self.network_policy != "gateway_only":
            raise ValueError(
                "Gateway semantic input delivery requires gateway_only network"
            )
        if not gateway_delivery and self.tool_policy:
            raise ValueError(
                "registered Gateway tools require gateway_read or hybrid delivery"
            )
        if not gateway_delivery and self.gateway_access_reasons:
            raise ValueError(
                "non-Gateway semantic input cannot declare Gateway access reasons"
            )
        if (
            self.attempt_workspace_policy == "own_draft_read_write"
            and self.execution_mode != "agent"
        ):
            raise ValueError(
                "draft workspace requires agent execution mode"
            )
        validate_int("timeout_seconds", self.timeout_seconds, minimum=1, maximum=86_400)
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Execution Profile release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible Execution Profile release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionProfileRelease":
        """Build a hash-complete immutable Execution Profile Release."""

        provisional = cls(**fields, release_sha256="0" * 64)
        record = cls(**fields, release_sha256=_canonical_sha256(provisional._payload()))
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExecutionProfileRelease":
        """Reconstruct a persisted Execution Profile Release."""

        return cls(
            execution_profile_id=payload["execution_profile_id"],
            execution_profile_version=payload["execution_profile_version"],
            release_ref=payload["release_ref"],
            executor_adapter_id=payload["executor_adapter_id"],
            executor_adapter_revision=payload["executor_adapter_revision"],
            transport_kind=payload["transport_kind"],
            provider_id=payload["provider_id"],
            model_id=payload["model_id"],
            reasoning_profile=payload["reasoning_profile"],
            execution_mode=payload["execution_mode"],
            semantic_input_delivery_mode=payload[
                "semantic_input_delivery_mode"
            ],
            attempt_workspace_policy=payload["attempt_workspace_policy"],
            gateway_access_reasons=tuple(payload["gateway_access_reasons"]),
            output_constraint_mode=payload["output_constraint_mode"],
            tool_policy=tuple(payload["tool_policy"]),
            network_policy=payload["network_policy"],
            timeout_seconds=payload["timeout_seconds"],
            release_sha256=payload["release_sha256"],
        )


@dataclass(frozen=True)
class RuntimeModuleRelease:
    """One immutable, independently executable Runtime Module contract."""

    record_type: ClassVar[str] = "runtime_module_release"

    module_id: str
    module_version: str
    release_ref: str
    module_kind: ModuleKind
    owner_contract_ref: str
    owner_contract_sha256: str
    executable_ref: str | None
    executable_sha256: str | None
    input_schema_ref: str
    input_schema_sha256: str
    output_schema_ref: str
    output_schema_sha256: str
    prompt_bundle_ref: str | None
    prompt_bundle_sha256: str | None
    declared_operation_ids: tuple[str, ...]
    behavior_policy_ref: str
    behavior_policy_sha256: str
    evaluation_policy_ref: str
    evaluation_policy_sha256: str
    retry_policy_ref: str
    retry_policy_sha256: str
    compatible_transport_kinds: tuple[str, ...]
    entry_policy: ModuleEntryPolicy
    output_resolution_policy: OutputResolutionPolicy
    release_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "release_ref": self.release_ref,
            "module_kind": self.module_kind.value,
            "owner_contract_ref": self.owner_contract_ref,
            "owner_contract_sha256": self.owner_contract_sha256,
            "executable_ref": self.executable_ref,
            "executable_sha256": self.executable_sha256,
            "input_schema_ref": self.input_schema_ref,
            "input_schema_sha256": self.input_schema_sha256,
            "output_schema_ref": self.output_schema_ref,
            "output_schema_sha256": self.output_schema_sha256,
            "prompt_bundle_ref": self.prompt_bundle_ref,
            "prompt_bundle_sha256": self.prompt_bundle_sha256,
            "declared_operation_ids": list(self.declared_operation_ids),
            "behavior_policy_ref": self.behavior_policy_ref,
            "behavior_policy_sha256": self.behavior_policy_sha256,
            "evaluation_policy_ref": self.evaluation_policy_ref,
            "evaluation_policy_sha256": self.evaluation_policy_sha256,
            "retry_policy_ref": self.retry_policy_ref,
            "retry_policy_sha256": self.retry_policy_sha256,
            "compatible_transport_kinds": list(self.compatible_transport_kinds),
            "entry_policy": self.entry_policy.value,
            "output_resolution_policy": self.output_resolution_policy.value,
        }

    def validate(self) -> None:
        """Validate Module contracts, executable closure, and release hash."""

        validate_snake_case_name("module_id", self.module_id)
        _validate_token("module_version", self.module_version)
        validate_opaque_ref("release_ref", self.release_ref)
        if type(self.module_kind) is not ModuleKind:
            raise ValueError("module_kind must be a ModuleKind")
        validate_opaque_ref("owner_contract_ref", self.owner_contract_ref)
        validate_sha256("owner_contract_sha256", self.owner_contract_sha256)
        prompt_binding = (self.prompt_bundle_ref, self.prompt_bundle_sha256)
        executable_binding = (self.executable_ref, self.executable_sha256)
        if self.module_kind is ModuleKind.AGENT:
            if any(value is None for value in prompt_binding):
                raise ValueError("Agent Module requires a Prompt Bundle")
            if any(value is not None for value in executable_binding):
                raise ValueError("Agent Module cannot own a direct executable binding")
            if not self.declared_operation_ids:
                raise ValueError(
                    "Agent Module must declare its protected operations"
                )
        else:
            if any(value is not None for value in prompt_binding):
                raise ValueError("non-Agent Module cannot own a Prompt binding")
            if any(value is None for value in executable_binding):
                raise ValueError("non-Agent Module requires an executable binding")
        _validate_optional_opaque_ref("executable_ref", self.executable_ref)
        _validate_optional_sha256("executable_sha256", self.executable_sha256)
        validate_opaque_ref("input_schema_ref", self.input_schema_ref)
        validate_sha256("input_schema_sha256", self.input_schema_sha256)
        validate_opaque_ref("output_schema_ref", self.output_schema_ref)
        validate_sha256("output_schema_sha256", self.output_schema_sha256)
        validate_string_tuple(
            "declared_operation_ids",
            self.declared_operation_ids,
            item_validator=lambda label, value: validate_snake_case_name(
                label, value
            ),
            require_non_empty=False,
        )
        for label, ref, digest in (
            ("behavior_policy", self.behavior_policy_ref, self.behavior_policy_sha256),
            (
                "evaluation_policy",
                self.evaluation_policy_ref,
                self.evaluation_policy_sha256,
            ),
            ("retry_policy", self.retry_policy_ref, self.retry_policy_sha256),
        ):
            validate_opaque_ref(f"{label}_ref", ref)
            validate_sha256(f"{label}_sha256", digest)
        validate_string_tuple(
            "compatible_transport_kinds",
            self.compatible_transport_kinds,
            item_validator=lambda label, value: _validate_token(label, value),
            require_non_empty=True,
        )
        if type(self.entry_policy) is not ModuleEntryPolicy:
            raise ValueError("entry_policy must be a ModuleEntryPolicy")
        if type(self.output_resolution_policy) is not OutputResolutionPolicy:
            raise ValueError(
                "output_resolution_policy must be an OutputResolutionPolicy"
            )
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Runtime Module release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible Runtime Module release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "RuntimeModuleRelease":
        """Build a hash-complete immutable Runtime Module Release."""

        provisional = cls(**fields, release_sha256="0" * 64)
        record = cls(**fields, release_sha256=_canonical_sha256(provisional._payload()))
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RuntimeModuleRelease":
        """Reconstruct a persisted Runtime Module Release."""

        return cls(
            module_id=payload["module_id"],
            module_version=payload["module_version"],
            release_ref=payload["release_ref"],
            module_kind=ModuleKind(payload["module_kind"]),
            owner_contract_ref=payload["owner_contract_ref"],
            owner_contract_sha256=payload["owner_contract_sha256"],
            executable_ref=payload.get("executable_ref"),
            executable_sha256=payload.get("executable_sha256"),
            input_schema_ref=payload["input_schema_ref"],
            input_schema_sha256=payload["input_schema_sha256"],
            output_schema_ref=payload["output_schema_ref"],
            output_schema_sha256=payload["output_schema_sha256"],
            prompt_bundle_ref=payload.get("prompt_bundle_ref"),
            prompt_bundle_sha256=payload.get("prompt_bundle_sha256"),
            declared_operation_ids=tuple(payload["declared_operation_ids"]),
            behavior_policy_ref=payload["behavior_policy_ref"],
            behavior_policy_sha256=payload["behavior_policy_sha256"],
            evaluation_policy_ref=payload["evaluation_policy_ref"],
            evaluation_policy_sha256=payload["evaluation_policy_sha256"],
            retry_policy_ref=payload["retry_policy_ref"],
            retry_policy_sha256=payload["retry_policy_sha256"],
            compatible_transport_kinds=tuple(payload["compatible_transport_kinds"]),
            entry_policy=ModuleEntryPolicy(payload["entry_policy"]),
            output_resolution_policy=OutputResolutionPolicy(
                payload["output_resolution_policy"]
            ),
            release_sha256=payload["release_sha256"],
        )


@dataclass(frozen=True)
class WorkflowNodeBinding:
    """Workflow-local position bound to one exact Runtime Module Release."""

    record_type: ClassVar[str] = "workflow_node_binding"

    node_id: str
    node_kind: WorkflowNodeKind
    module_release_ref: str | None
    module_release_sha256: str | None
    input_mapping_ref: str | None
    input_mapping_sha256: str | None

    def validate(self) -> None:
        """Validate one workflow-local exact Module binding."""

        validate_snake_case_name("node_id", self.node_id)
        if type(self.node_kind) is not WorkflowNodeKind:
            raise ValueError("node_kind must be a WorkflowNodeKind")
        binding = (
            self.module_release_ref,
            self.module_release_sha256,
            self.input_mapping_ref,
            self.input_mapping_sha256,
        )
        if self.node_kind is WorkflowNodeKind.MODULE:
            if any(value is None for value in binding):
                raise ValueError("Module node requires exact Module and input mapping")
        elif any(value is not None for value in binding):
            raise ValueError("control node cannot own Module or input-mapping refs")
        _validate_optional_opaque_ref(
            "module_release_ref", self.module_release_ref
        )
        _validate_optional_sha256(
            "module_release_sha256", self.module_release_sha256
        )
        _validate_optional_opaque_ref("input_mapping_ref", self.input_mapping_ref)
        _validate_optional_sha256(
            "input_mapping_sha256", self.input_mapping_sha256
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible node binding."""

        self.validate()
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind.value,
            "module_release_ref": self.module_release_ref,
            "module_release_sha256": self.module_release_sha256,
            "input_mapping_ref": self.input_mapping_ref,
            "input_mapping_sha256": self.input_mapping_sha256,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowNodeBinding":
        """Reconstruct a persisted workflow node binding."""

        return cls(
            node_id=payload["node_id"],
            node_kind=WorkflowNodeKind(payload["node_kind"]),
            module_release_ref=payload.get("module_release_ref"),
            module_release_sha256=payload.get("module_release_sha256"),
            input_mapping_ref=payload.get("input_mapping_ref"),
            input_mapping_sha256=payload.get("input_mapping_sha256"),
        )


@dataclass(frozen=True)
class WorkflowEdge:
    """One legal outcome route between workflow-local Module positions."""

    record_type: ClassVar[str] = "workflow_edge"

    source_node_id: str
    outcome_id: str
    target_node_id: str | None
    terminal: bool

    def validate(self) -> None:
        """Validate one legal nonterminal or terminal graph route."""

        validate_snake_case_name("source_node_id", self.source_node_id)
        validate_snake_case_name("outcome_id", self.outcome_id)
        if self.target_node_id is not None:
            validate_snake_case_name("target_node_id", self.target_node_id)
        if type(self.terminal) is not bool:
            raise ValueError("terminal must be boolean")
        if self.terminal == (self.target_node_id is not None):
            raise ValueError("terminal edge must have no target; nonterminal edge needs one")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible workflow edge."""

        self.validate()
        return {
            "source_node_id": self.source_node_id,
            "outcome_id": self.outcome_id,
            "target_node_id": self.target_node_id,
            "terminal": self.terminal,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowEdge":
        """Reconstruct a persisted workflow edge."""

        return cls(**payload)


@dataclass(frozen=True)
class WorkflowParallelGroupBinding:
    """Workflow-local fan-out binding with one durable join target."""

    record_type: ClassVar[str] = "workflow_parallel_group_binding"

    group_id: str
    control_node_id: str
    branch_node_ids: tuple[str, ...]
    join_node_id: str
    completion_outcome_id: str
    join_policy: WorkflowParallelJoinPolicy

    def validate(self) -> None:
        """Validate bounded group identity without loading domain content."""

        validate_snake_case_name("group_id", self.group_id)
        validate_snake_case_name("control_node_id", self.control_node_id)
        validate_snake_case_name("join_node_id", self.join_node_id)
        validate_snake_case_name(
            "completion_outcome_id", self.completion_outcome_id
        )
        if type(self.branch_node_ids) is not tuple:
            raise ValueError("branch_node_ids must be an immutable tuple")
        if len(self.branch_node_ids) < 2:
            raise ValueError("parallel group requires at least two branches")
        if len(self.branch_node_ids) != len(set(self.branch_node_ids)):
            raise ValueError("parallel group branch_node_ids must be unique")
        for branch_node_id in self.branch_node_ids:
            validate_snake_case_name("branch_node_id", branch_node_id)
        if self.control_node_id in self.branch_node_ids:
            raise ValueError("parallel control node cannot be a branch")
        if self.join_node_id in self.branch_node_ids:
            raise ValueError("parallel join node cannot be a branch")
        if self.control_node_id == self.join_node_id:
            raise ValueError("parallel control and join nodes must differ")
        if type(self.join_policy) is not WorkflowParallelJoinPolicy:
            raise ValueError("join_policy must be WorkflowParallelJoinPolicy")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible group binding."""

        self.validate()
        return {
            "group_id": self.group_id,
            "control_node_id": self.control_node_id,
            "branch_node_ids": list(self.branch_node_ids),
            "join_node_id": self.join_node_id,
            "completion_outcome_id": self.completion_outcome_id,
            "join_policy": self.join_policy.value,
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any]
    ) -> "WorkflowParallelGroupBinding":
        """Reconstruct one persisted parallel group binding."""

        return cls(
            group_id=payload["group_id"],
            control_node_id=payload["control_node_id"],
            branch_node_ids=tuple(payload["branch_node_ids"]),
            join_node_id=payload["join_node_id"],
            completion_outcome_id=payload["completion_outcome_id"],
            join_policy=WorkflowParallelJoinPolicy(payload["join_policy"]),
        )


@dataclass(frozen=True)
class WorkflowRelease:
    """Immutable graph assembled only from exact Runtime Module Releases."""

    record_type: ClassVar[str] = "workflow_release"

    workflow_id: str
    workflow_version: str
    workflow_contract_version: str
    release_ref: str
    owner_contract_ref: str
    owner_contract_sha256: str
    graph_ref: str
    graph_sha256: str
    initial_node_id: str
    nodes: tuple[WorkflowNodeBinding, ...]
    edges: tuple[WorkflowEdge, ...]
    authorization_manifest_ref: str
    authorization_manifest_sha256: str
    execution_release_ref: str
    execution_release_sha256: str
    release_sha256: str
    parallel_groups: tuple[WorkflowParallelGroupBinding, ...] = ()

    def _payload(self) -> dict[str, Any]:
        payload = {
            "workflow_id": self.workflow_id,
            "workflow_version": self.workflow_version,
            "workflow_contract_version": self.workflow_contract_version,
            "release_ref": self.release_ref,
            "owner_contract_ref": self.owner_contract_ref,
            "owner_contract_sha256": self.owner_contract_sha256,
            "graph_ref": self.graph_ref,
            "graph_sha256": self.graph_sha256,
            "initial_node_id": self.initial_node_id,
            "nodes": [node.as_dict() for node in self.nodes],
            "edges": [edge.as_dict() for edge in self.edges],
            "authorization_manifest_ref": self.authorization_manifest_ref,
            "authorization_manifest_sha256": self.authorization_manifest_sha256,
            "execution_release_ref": self.execution_release_ref,
            "execution_release_sha256": self.execution_release_sha256,
        }
        if self.parallel_groups:
            payload["parallel_groups"] = [
                group.as_dict() for group in self.parallel_groups
            ]
        return payload

    def validate(self) -> None:
        """Validate graph closure, terminal reachability surface, and hash."""

        validate_snake_case_name("workflow_id", self.workflow_id)
        _validate_token("workflow_version", self.workflow_version)
        _validate_token(
            "workflow_contract_version", self.workflow_contract_version
        )
        validate_opaque_ref("release_ref", self.release_ref)
        validate_opaque_ref("owner_contract_ref", self.owner_contract_ref)
        validate_sha256("owner_contract_sha256", self.owner_contract_sha256)
        validate_opaque_ref("graph_ref", self.graph_ref)
        validate_sha256("graph_sha256", self.graph_sha256)
        validate_snake_case_name("initial_node_id", self.initial_node_id)
        validate_exact_record_tuple(
            "nodes",
            self.nodes,
            expected_type=WorkflowNodeBinding,
            item_validator=lambda node: node.validate(),
            unique_key=lambda node: node.node_id,
            unique_key_label="node_id",
            require_non_empty=True,
        )
        node_ids = {node.node_id for node in self.nodes}
        nodes_by_id = {node.node_id: node for node in self.nodes}
        if self.initial_node_id not in node_ids:
            raise ValueError("initial_node_id is not registered in nodes")
        validate_exact_record_tuple(
            "edges",
            self.edges,
            expected_type=WorkflowEdge,
            item_validator=lambda edge: edge.validate(),
            unique_key=lambda edge: f"{edge.source_node_id}:{edge.outcome_id}",
            unique_key_label="source/outcome route",
            require_non_empty=True,
        )
        for edge in self.edges:
            if edge.source_node_id not in node_ids:
                raise ValueError("workflow edge has unknown source_node_id")
            if edge.target_node_id is not None and edge.target_node_id not in node_ids:
                raise ValueError("workflow edge has unknown target_node_id")
        validate_exact_record_tuple(
            "parallel_groups",
            self.parallel_groups,
            expected_type=WorkflowParallelGroupBinding,
            item_validator=lambda group: group.validate(),
            unique_key=lambda group: group.group_id,
            unique_key_label="group_id",
            require_non_empty=False,
        )
        branch_owner: dict[str, str] = {}
        control_nodes: set[str] = set()
        for group in self.parallel_groups:
            control = nodes_by_id.get(group.control_node_id)
            if control is None or control.node_kind is not WorkflowNodeKind.CONTROL:
                raise ValueError("parallel group requires one control node")
            if group.control_node_id in control_nodes:
                raise ValueError("parallel control node belongs to several groups")
            control_nodes.add(group.control_node_id)
            if group.join_node_id not in nodes_by_id:
                raise ValueError("parallel group has unknown join_node_id")
            control_routes = tuple(
                edge
                for edge in self.edges
                if edge.source_node_id == group.control_node_id
            )
            completion_routes = tuple(
                edge
                for edge in control_routes
                if edge.outcome_id == group.completion_outcome_id
            )
            if (
                len(control_routes) != 1
                or len(completion_routes) != 1
                or completion_routes[0].terminal
                or completion_routes[0].target_node_id != group.join_node_id
            ):
                raise ValueError(
                    "parallel control must have only its declared join route"
                )
            for branch_node_id in group.branch_node_ids:
                branch = nodes_by_id.get(branch_node_id)
                if branch is None or branch.node_kind is not WorkflowNodeKind.MODULE:
                    raise ValueError("parallel branch requires one Module node")
                if branch_node_id in branch_owner:
                    raise ValueError("parallel branch belongs to several groups")
                branch_owner[branch_node_id] = group.group_id
                branch_routes = tuple(
                    edge
                    for edge in self.edges
                    if edge.source_node_id == branch_node_id
                )
                if not branch_routes or any(
                    edge.terminal or edge.target_node_id != group.join_node_id
                    for edge in branch_routes
                ):
                    raise ValueError(
                        "every parallel branch route must target its join node"
                    )
        if self.initial_node_id in branch_owner:
            raise ValueError("parallel branch cannot be the initial node")
        for edge in self.edges:
            if edge.target_node_id in branch_owner:
                raise ValueError(
                    "parallel branch cannot be entered by an ordinary graph edge"
                )
        structural_nodes = control_nodes | {
            group.join_node_id for group in self.parallel_groups
        }
        if structural_nodes & set(branch_owner):
            raise ValueError("parallel branch cannot own group structure")
        routed_node_ids = {edge.source_node_id for edge in self.edges}
        unrouted_node_ids = node_ids - routed_node_ids
        if unrouted_node_ids:
            raise ValueError(
                "workflow nodes require at least one outcome route: "
                + ", ".join(sorted(unrouted_node_ids))
            )
        reachable = {self.initial_node_id}
        pending = [self.initial_node_id]
        while pending:
            source_node_id = pending.pop()
            for group in self.parallel_groups:
                if group.control_node_id != source_node_id:
                    continue
                for branch_node_id in group.branch_node_ids:
                    if branch_node_id not in reachable:
                        reachable.add(branch_node_id)
                        pending.append(branch_node_id)
            for edge in self.edges:
                if edge.source_node_id != source_node_id:
                    continue
                if (
                    edge.target_node_id is not None
                    and edge.target_node_id not in reachable
                ):
                    reachable.add(edge.target_node_id)
                    pending.append(edge.target_node_id)
        unreachable_node_ids = node_ids - reachable
        if unreachable_node_ids:
            raise ValueError(
                "workflow contains nodes unreachable from initial_node_id: "
                + ", ".join(sorted(unreachable_node_ids))
            )
        if not any(edge.terminal for edge in self.edges):
            raise ValueError("workflow requires at least one terminal edge")
        validate_opaque_ref(
            "authorization_manifest_ref", self.authorization_manifest_ref
        )
        validate_sha256(
            "authorization_manifest_sha256", self.authorization_manifest_sha256
        )
        validate_opaque_ref("execution_release_ref", self.execution_release_ref)
        validate_sha256("execution_release_sha256", self.execution_release_sha256)
        validate_sha256("release_sha256", self.release_sha256)
        if self.release_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Workflow release hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible Workflow Release."""

        self.validate()
        return {**self._payload(), "release_sha256": self.release_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "WorkflowRelease":
        """Build a hash-complete immutable Workflow Release."""

        provisional = cls(**fields, release_sha256="0" * 64)
        record = cls(**fields, release_sha256=_canonical_sha256(provisional._payload()))
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowRelease":
        """Reconstruct a persisted Workflow Release."""

        return cls(
            workflow_id=payload["workflow_id"],
            workflow_version=payload["workflow_version"],
            workflow_contract_version=payload["workflow_contract_version"],
            release_ref=payload["release_ref"],
            owner_contract_ref=payload["owner_contract_ref"],
            owner_contract_sha256=payload["owner_contract_sha256"],
            graph_ref=payload["graph_ref"],
            graph_sha256=payload["graph_sha256"],
            initial_node_id=payload["initial_node_id"],
            nodes=tuple(WorkflowNodeBinding.from_dict(node) for node in payload["nodes"]),
            edges=tuple(WorkflowEdge.from_dict(edge) for edge in payload["edges"]),
            authorization_manifest_ref=payload["authorization_manifest_ref"],
            authorization_manifest_sha256=payload["authorization_manifest_sha256"],
            execution_release_ref=payload["execution_release_ref"],
            execution_release_sha256=payload["execution_release_sha256"],
            release_sha256=payload["release_sha256"],
            parallel_groups=tuple(
                WorkflowParallelGroupBinding.from_dict(group)
                for group in payload.get("parallel_groups", ())
            ),
        )


@dataclass(frozen=True)
class ReleaseAdmissionIntent:
    """Timestamp-free request for one authoritative admission commit."""

    record_type: ClassVar[str] = "release_admission_intent"

    admission_id: str
    subject_kind: ReleaseSubjectKind
    subject_id: str
    release_ref: str
    release_sha256: str
    state: ReleaseAdmissionState
    evidence_members: tuple[ReleaseMember, ...]
    admission_intent_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "release_ref": self.release_ref,
            "release_sha256": self.release_sha256,
            "state": self.state.value,
            "evidence_members": [member.as_dict() for member in self.evidence_members],
        }

    def validate(self) -> None:
        """Validate intent identity, evidence closure, and content hash."""

        validate_snake_case_name("admission_id", self.admission_id)
        if type(self.subject_kind) is not ReleaseSubjectKind:
            raise ValueError("subject_kind must be a ReleaseSubjectKind")
        validate_snake_case_name("subject_id", self.subject_id)
        validate_opaque_ref("release_ref", self.release_ref)
        validate_sha256("release_sha256", self.release_sha256)
        if type(self.state) is not ReleaseAdmissionState:
            raise ValueError("state must be a ReleaseAdmissionState")
        validate_exact_record_tuple(
            "evidence_members",
            self.evidence_members,
            expected_type=ReleaseMember,
            item_validator=lambda member: member.validate(),
            unique_key=lambda member: member.member_ref,
            unique_key_label="member_ref",
            require_non_empty=False,
        )
        validate_sha256("admission_intent_sha256", self.admission_intent_sha256)
        if self.admission_intent_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("release admission intent hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible intent."""

        self.validate()
        return {
            **self._payload(),
            "admission_intent_sha256": self.admission_intent_sha256,
        }

    @classmethod
    def build(cls, **fields: Any) -> "ReleaseAdmissionIntent":
        """Build one hash-complete timestamp-free intent."""

        provisional = cls(**fields, admission_intent_sha256="0" * 64)
        intent = cls(
            **fields,
            admission_intent_sha256=_canonical_sha256(provisional._payload()),
        )
        intent.validate()
        return intent

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReleaseAdmissionIntent":
        """Reconstruct one serialized admission intent."""

        intent = cls(
            admission_id=payload["admission_id"],
            subject_kind=ReleaseSubjectKind(payload["subject_kind"]),
            subject_id=payload["subject_id"],
            release_ref=payload["release_ref"],
            release_sha256=payload["release_sha256"],
            state=ReleaseAdmissionState(payload["state"]),
            evidence_members=tuple(
                ReleaseMember.from_dict(member)
                for member in payload["evidence_members"]
            ),
            admission_intent_sha256=payload["admission_intent_sha256"],
        )
        intent.validate()
        return intent


@dataclass(frozen=True)
class ReleaseAdmissionRecord:
    """Append-only admission decision committed by an authoritative store."""

    record_type: ClassVar[str] = "release_admission_record"

    admission_id: str
    subject_kind: ReleaseSubjectKind
    subject_id: str
    release_ref: str
    release_sha256: str
    state: ReleaseAdmissionState
    evidence_members: tuple[ReleaseMember, ...]
    admission_intent_sha256: str
    recorded_at_utc: str
    admission_sha256: str

    def _intent_payload(self) -> dict[str, Any]:
        return {
            "admission_id": self.admission_id,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "release_ref": self.release_ref,
            "release_sha256": self.release_sha256,
            "state": self.state.value,
            "evidence_members": [member.as_dict() for member in self.evidence_members],
        }

    def _payload(self) -> dict[str, Any]:
        return {
            **self._intent_payload(),
            "admission_intent_sha256": self.admission_intent_sha256,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate final admission identity, store time, and hashes."""

        ReleaseAdmissionIntent(
            admission_id=self.admission_id,
            subject_kind=self.subject_kind,
            subject_id=self.subject_id,
            release_ref=self.release_ref,
            release_sha256=self.release_sha256,
            state=self.state,
            evidence_members=self.evidence_members,
            admission_intent_sha256=self.admission_intent_sha256,
        ).validate()
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        validate_sha256("admission_sha256", self.admission_sha256)
        if self.admission_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("release admission hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible final record."""

        self.validate()
        return {**self._payload(), "admission_sha256": self.admission_sha256}

    @classmethod
    def _from_intent(
        cls,
        intent: ReleaseAdmissionIntent,
        *,
        recorded_at_utc: str,
    ) -> "ReleaseAdmissionRecord":
        """Finalize one intent with the authoritative store commit time."""

        if type(intent) is not ReleaseAdmissionIntent:
            raise ValueError("intent must be a ReleaseAdmissionIntent")
        intent.validate()
        validate_utc_timestamp("recorded_at_utc", recorded_at_utc)
        fields = {
            **intent._payload(),
            "subject_kind": intent.subject_kind,
            "state": intent.state,
            "evidence_members": intent.evidence_members,
            "admission_intent_sha256": intent.admission_intent_sha256,
            "recorded_at_utc": recorded_at_utc,
        }
        provisional = cls(**fields, admission_sha256="0" * 64)
        record = cls(
            **fields,
            admission_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ReleaseAdmissionRecord":
        """Reconstruct a persisted release admission record."""

        record = cls(
            admission_id=payload["admission_id"],
            subject_kind=ReleaseSubjectKind(payload["subject_kind"]),
            subject_id=payload["subject_id"],
            release_ref=payload["release_ref"],
            release_sha256=payload["release_sha256"],
            state=ReleaseAdmissionState(payload["state"]),
            evidence_members=tuple(
                ReleaseMember.from_dict(member)
                for member in payload["evidence_members"]
            ),
            admission_intent_sha256=payload["admission_intent_sha256"],
            recorded_at_utc=payload["recorded_at_utc"],
            admission_sha256=payload["admission_sha256"],
        )
        record.validate()
        return record


__all__ = [
    "BehaviorPolicyRelease",
    "EXECUTION_MODES",
    "EvaluationPolicyRelease",
    "ExecutionVariantPolicyRelease",
    "is_prompt_component_member_ref",
    "ExecutionProfileRelease",
    "ModuleEntryPolicy",
    "ModuleExecutionPurpose",
    "ModuleKind",
    "NETWORK_POLICIES",
    "OUTPUT_CONSTRAINT_MODES",
    "SEMANTIC_INPUT_DELIVERY_MODES",
    "PromptComponentKind",
    "PromptComponentRelease",
    "OutputResolutionPolicy",
    "PromptBundleRelease",
    "ReleaseAdmissionIntent",
    "ReleaseAdmissionRecord",
    "ReleaseAdmissionState",
    "ReleaseMember",
    "ReleaseSubjectKind",
    "RetryPolicyRelease",
    "RuntimeModuleRelease",
    "SchemaAssetRelease",
    "WorkflowEdge",
    "WorkflowNodeKind",
    "WorkflowNodeBinding",
    "WorkflowParallelGroupBinding",
    "WorkflowParallelJoinPolicy",
    "WorkflowRelease",
]
