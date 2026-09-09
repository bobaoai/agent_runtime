"""Path-free host candidate set for one Registry schema migration plan."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import hashlib
import json
import re
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints

from .registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    ExecutionVariantPolicyReleaseCandidate,
    NonAgentModuleReleaseCandidate,
    RetryPolicyReleaseCandidate,
    WorkflowReleaseCandidate,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_execution_variant_policy_release,
    compile_non_agent_module_release,
    compile_retry_policy_release,
    compile_workflow_release,
    runtime_owned_policy_schema_assets,
)
from .registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)
from .registry_schema_migration import (
    RegistryActivePointerDisposition,
    RegistryAdmissionIdentityDisposition,
    RegistryReleaseIdentityDisposition,
)


_PATH_FIELD_FRAGMENTS = ("path", "project_root", "repository_root")
_DIRECT_PATH = re.compile(r"^(?:/|\./|\.\./|~/|[A-Za-z]:[\\/])")
_CONTENT_FIELDS = frozenset(
    {
        "owner_contract_content",
        "instruction_text",
        "input_schema_document",
        "output_schema_document",
        "executable_content",
    }
)
_CANDIDATE_SET_SCHEMA_VERSION = "registry_migration_candidate_set_v2"


def _reject_host_path(value: Any, *, field_name: str | None = None) -> None:
    normalized_name = "" if field_name is None else field_name.lower()
    if any(fragment in normalized_name for fragment in _PATH_FIELD_FRAGMENTS):
        raise ValueError("Registry migration candidate contains a host path field")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _reject_host_path(
                getattr(value, field.name),
                field_name=field.name,
            )
        return
    if isinstance(value, dict):
        for key, child in value.items():
            _reject_host_path(child, field_name=str(key))
        return
    if isinstance(value, (tuple, list)):
        for child in value:
            _reject_host_path(child, field_name=field_name)
        return
    if (
        isinstance(value, str)
        and field_name not in _CONTENT_FIELDS
        and (
            _DIRECT_PATH.search(value) is not None
            or "/.claude/" in value
            or "/.agents/" in value
        )
    ):
        raise ValueError("Registry migration candidate contains a host path value")


@dataclass(frozen=True)
class RegistryReleaseDispositionCandidate:
    """Owner intent for one predecessor release before target resolution."""

    source_table: str
    source_release_ref: str
    source_release_sha256: str
    disposition: str
    target_release_ref: str | None


@dataclass(frozen=True)
class RegistryAdmissionDispositionCandidate:
    """Owner intent for one predecessor admission before target resolution."""

    source_admission_id: str
    source_admission_sha256: str
    disposition: str
    target_admission_id: str | None


@dataclass(frozen=True)
class RegistryActivePointerDispositionCandidate:
    """Owner intent for one predecessor pointer before target resolution."""

    subject_kind: str
    subject_id: str
    source_release_ref: str
    source_release_sha256: str
    disposition: str
    target_release_ref: str | None


@dataclass(frozen=True)
class RegistryMigrationCandidateSet:
    """One immutable host export consumed by Registry migration tooling."""

    candidate_set_id: str
    behavior_policies: tuple[BehaviorPolicyReleaseCandidate, ...] = ()
    evaluation_policies: tuple[EvaluationPolicyReleaseCandidate, ...] = ()
    retry_policies: tuple[RetryPolicyReleaseCandidate, ...] = ()
    execution_profiles: tuple[ExecutionProfileReleaseSpec, ...] = ()
    agent_modules: tuple[AgentModuleReleaseCandidate, ...] = ()
    non_agent_modules: tuple[NonAgentModuleReleaseCandidate, ...] = ()
    workflows: tuple[WorkflowReleaseCandidate, ...] = ()
    execution_variant_policies: tuple[
        ExecutionVariantPolicyReleaseCandidate, ...
    ] = ()
    release_dispositions: tuple[
        RegistryReleaseDispositionCandidate, ...
    ] = ()
    admission_dispositions: tuple[
        RegistryAdmissionDispositionCandidate, ...
    ] = ()
    active_pointer_dispositions: tuple[
        RegistryActivePointerDispositionCandidate, ...
    ] = ()

    def validate(self) -> None:
        if type(self.candidate_set_id) is not str or not self.candidate_set_id:
            raise ValueError("candidate_set_id is required")
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name != "candidate_set_id" and type(value) is not tuple:
                raise ValueError(
                    f"Registry migration {field.name} must be an immutable tuple"
                )
        _reject_host_path(self)

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "schema_version": _CANDIDATE_SET_SCHEMA_VERSION,
            **{
                field.name: _encode_value(getattr(self, field.name))
                for field in fields(self)
            },
        }

    def artifact_sha256(self) -> str:
        encoded = json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RegistryMigrationCandidateSet":
        expected = {"schema_version", *(field.name for field in fields(cls))}
        if type(payload) is not dict or set(payload) != expected:
            raise ValueError("Registry migration candidate set has an invalid shape")
        if payload["schema_version"] != _CANDIDATE_SET_SCHEMA_VERSION:
            raise ValueError("unsupported Registry migration candidate schema")
        record = _decode_dataclass(
            cls,
            {key: value for key, value in payload.items() if key != "schema_version"},
        )
        record.validate()
        return record


def _encode_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _encode_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _encode_value(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_encode_value(child) for child in value]
    return value


def _decode_value(annotation: Any, value: Any) -> Any:
    if annotation is Any:
        return value
    origin = get_origin(annotation)
    arguments = get_args(annotation)
    if origin in {tuple}:
        if type(value) is not list or len(arguments) != 2 or arguments[1] is not Ellipsis:
            raise ValueError("candidate tuple field has an invalid value")
        return tuple(_decode_value(arguments[0], child) for child in value)
    if origin in {dict, Mapping}:
        if type(value) is not dict:
            raise ValueError("candidate mapping field has an invalid value")
        key_type, value_type = arguments
        return {
            _decode_value(key_type, key): _decode_value(value_type, child)
            for key, child in value.items()
        }
    if origin in {UnionType} or (origin is not None and type(None) in arguments):
        if value is None and type(None) in arguments:
            return None
        for member in arguments:
            if member is type(None):
                continue
            try:
                return _decode_value(member, value)
            except (TypeError, ValueError):
                continue
        raise ValueError("candidate union field has an invalid value")
    if annotation is bytes:
        if type(value) is not dict or set(value) != {"encoding", "data"}:
            raise ValueError("candidate bytes field has an invalid envelope")
        if value["encoding"] != "base64" or type(value["data"]) is not str:
            raise ValueError("candidate bytes field must use base64")
        try:
            return base64.b64decode(value["data"], validate=True)
        except ValueError as exc:
            raise ValueError("candidate bytes field is not valid base64") from exc
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        return _decode_dataclass(annotation, value)
    if annotation in {str, int, bool}:
        if type(value) is not annotation:
            raise ValueError("candidate scalar field has an invalid type")
        return value
    return value


def _decode_dataclass(record_type: type[Any], payload: Any) -> Any:
    if type(payload) is not dict:
        raise ValueError("candidate record must be one JSON object")
    record_fields = fields(record_type)
    if set(payload) != {field.name for field in record_fields}:
        raise ValueError("candidate record has an invalid shape")
    annotations = get_type_hints(record_type)
    return record_type(
        **{
            field.name: _decode_value(
                annotations[field.name],
                payload[field.name],
            )
            for field in record_fields
        }
    )


@dataclass(frozen=True)
class CompiledRegistryMigrationCandidateSet:
    """Resolved target bundle and exact predecessor disposition records."""

    target_bundle: RuntimeReleaseBundle
    release_dispositions: tuple[RegistryReleaseIdentityDisposition, ...]
    admission_dispositions: tuple[
        RegistryAdmissionIdentityDisposition, ...
    ]
    active_pointer_dispositions: tuple[
        RegistryActivePointerDisposition, ...
    ]


def _merge_exact_records(*groups: tuple[Any, ...]) -> tuple[Any, ...]:
    merged: dict[str, Any] = {}
    for group in groups:
        for record in group:
            key = record.release_ref
            existing = merged.get(key)
            if existing is not None and existing != record:
                raise ValueError(f"candidate release_ref collision: {key}")
            merged[key] = record
    return tuple(merged[key] for key in sorted(merged))


def _release_lookup(bundle: RuntimeReleaseBundle) -> dict[str, Any]:
    return {
        record.release_ref: record
        for group in (
            bundle.schema_assets,
            bundle.prompt_components,
            bundle.prompt_bundles,
            bundle.behavior_policies,
            bundle.evaluation_policies,
            bundle.retry_policies,
            bundle.execution_variant_policies,
            bundle.execution_profiles,
            bundle.modules,
            bundle.workflows,
        )
        for record in group
    }


def _required_release(releases: dict[str, Any], release_ref: str) -> Any:
    try:
        return releases[release_ref]
    except KeyError as exc:
        raise ValueError(
            f"migration candidate names an unknown target release: {release_ref}"
        ) from exc


def compile_registry_migration_candidate_set(
    candidate_set: RegistryMigrationCandidateSet,
    *,
    unchanged_bundle: RuntimeReleaseBundle = RuntimeReleaseBundle(),
) -> CompiledRegistryMigrationCandidateSet:
    """Compile one host export and resolve every target identity exactly."""

    if type(candidate_set) is not RegistryMigrationCandidateSet:
        raise ValueError("candidate_set must be a RegistryMigrationCandidateSet")
    candidate_set.validate()
    if type(unchanged_bundle) is not RuntimeReleaseBundle:
        raise ValueError("unchanged_bundle must be a RuntimeReleaseBundle")

    behavior = tuple(
        compile_behavior_policy_release(candidate)
        for candidate in candidate_set.behavior_policies
    )
    evaluation = tuple(
        compile_evaluation_policy_release(candidate)
        for candidate in candidate_set.evaluation_policies
    )
    retry = tuple(
        compile_retry_policy_release(candidate)
        for candidate in candidate_set.retry_policies
    )
    profiles = tuple(
        compile_execution_profile_release(candidate)
        for candidate in candidate_set.execution_profiles
    )
    compiled_agents = tuple(
        compile_agent_module_release(candidate)
        for candidate in candidate_set.agent_modules
    )
    compiled_non_agents = tuple(
        compile_non_agent_module_release(candidate)
        for candidate in candidate_set.non_agent_modules
    )
    workflows = tuple(
        compile_workflow_release(candidate)
        for candidate in candidate_set.workflows
    )
    variants = tuple(
        compile_execution_variant_policy_release(candidate)
        for candidate in candidate_set.execution_variant_policies
    )
    target = RuntimeReleaseBundle(
        schema_assets=_merge_exact_records(
            unchanged_bundle.schema_assets,
            runtime_owned_policy_schema_assets(),
            *(compiled.schema_assets for compiled in compiled_agents),
            *(compiled.schema_assets for compiled in compiled_non_agents),
        ),
        prompt_components=_merge_exact_records(
            unchanged_bundle.prompt_components,
            *(compiled.prompt_components for compiled in compiled_agents),
        ),
        prompt_bundles=_merge_exact_records(
            unchanged_bundle.prompt_bundles,
            tuple(compiled.prompt_bundle for compiled in compiled_agents),
        ),
        behavior_policies=_merge_exact_records(
            unchanged_bundle.behavior_policies,
            behavior,
        ),
        evaluation_policies=_merge_exact_records(
            unchanged_bundle.evaluation_policies,
            evaluation,
        ),
        retry_policies=_merge_exact_records(
            unchanged_bundle.retry_policies,
            retry,
        ),
        execution_variant_policies=_merge_exact_records(
            unchanged_bundle.execution_variant_policies,
            variants,
        ),
        execution_profiles=_merge_exact_records(
            unchanged_bundle.execution_profiles,
            profiles,
        ),
        modules=_merge_exact_records(
            unchanged_bundle.modules,
            tuple(compiled.module for compiled in compiled_agents),
            tuple(compiled.module for compiled in compiled_non_agents),
        ),
        workflows=_merge_exact_records(
            unchanged_bundle.workflows,
            workflows,
        ),
    )
    releases = _release_lookup(target)
    target_registry = RuntimeReleaseRegistry()
    target_registry.register_bundle(target)
    releases = _release_lookup(target)

    release_dispositions = tuple(
        RegistryReleaseIdentityDisposition(
            source_table=candidate.source_table,
            source_release_ref=candidate.source_release_ref,
            source_release_sha256=candidate.source_release_sha256,
            disposition=candidate.disposition,
            target_release_ref=candidate.target_release_ref,
            target_release_sha256=(
                None
                if candidate.target_release_ref is None
                else _required_release(
                    releases,
                    candidate.target_release_ref,
                ).release_sha256
            ),
        )
        for candidate in candidate_set.release_dispositions
    )
    admission_dispositions = tuple(
        RegistryAdmissionIdentityDisposition(
            source_admission_id=candidate.source_admission_id,
            source_admission_sha256=candidate.source_admission_sha256,
            disposition=candidate.disposition,
            target_admission_id=candidate.target_admission_id,
            target_admission_sha256=None,
        )
        for candidate in candidate_set.admission_dispositions
    )
    pointer_dispositions = tuple(
        RegistryActivePointerDisposition(
            subject_kind=candidate.subject_kind,
            subject_id=candidate.subject_id,
            source_release_ref=candidate.source_release_ref,
            source_release_sha256=candidate.source_release_sha256,
            disposition=candidate.disposition,
            target_release_ref=candidate.target_release_ref,
            target_release_sha256=(
                None
                if candidate.target_release_ref is None
                else _required_release(
                    releases,
                    candidate.target_release_ref,
                ).release_sha256
            ),
        )
        for candidate in candidate_set.active_pointer_dispositions
    )
    for disposition in (
        *release_dispositions,
        *admission_dispositions,
        *pointer_dispositions,
    ):
        disposition.validate()
    return CompiledRegistryMigrationCandidateSet(
        target_bundle=target,
        release_dispositions=release_dispositions,
        admission_dispositions=admission_dispositions,
        active_pointer_dispositions=pointer_dispositions,
    )


__all__ = [
    "CompiledRegistryMigrationCandidateSet",
    "RegistryActivePointerDispositionCandidate",
    "RegistryAdmissionDispositionCandidate",
    "RegistryMigrationCandidateSet",
    "RegistryReleaseDispositionCandidate",
    "compile_registry_migration_candidate_set",
]
