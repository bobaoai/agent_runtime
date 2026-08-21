"""Provider-neutral conformance package for managed agentic workflows.

The package is a control-plane closure index.  Module input projections are
the least-privilege task-plane views assembled from that index for one Module
Variant.  Neither record contains domain semantics or provider behavior.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_OPERATION_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_OPAQUE_REF = re.compile(r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _validate_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")


def _validate_token(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or len(value) > 512
    ):
        raise ValueError(f"invalid {label}: bounded token required")


def _validate_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _OPAQUE_REF.fullmatch(value):
        raise ValueError(f"invalid {label}: scheme-prefixed opaque ref required")
    if value.partition(":")[0] == "data":
        raise ValueError(f"invalid {label}: inline data refs are forbidden")


def _validate_sha256(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"invalid {label}: lowercase SHA-256 required")


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


class WorkflowManagementLifecycle(StrEnum):
    """Migration lifecycle for one logical model-backed workflow."""

    DEVELOPING = "developing"
    MIGRATION_PLANNED = "migration_planned"
    MANAGED = "managed"
    DIRECT_ENTRY_RETIRED = "direct_entry_retired"


class ConformanceContractKind(StrEnum):
    """Required control-plane contract families in every complete package."""

    INTENT = "intent"
    GRAPH = "graph"
    DOMAIN_MANIFEST = "domain_manifest"
    ARTIFACT_SCHEMA_INDEX = "artifact_schema_index"
    MODULE_DEFINITION_INDEX = "module_definition_index"
    MODULE_INPUT_PROJECTION_INDEX = "module_input_projection_index"
    EXECUTION_PROFILE_INDEX = "execution_profile_index"
    EVALUATION_BINDING_INDEX = "evaluation_binding_index"
    AUTHORIZATION_MANIFEST = "authorization_manifest"
    CONTEXT_AND_DATA_BOUNDARY = "context_and_data_boundary"
    TELEMETRY_CONTRACT = "telemetry_contract"
    RECOVERY_AND_REPLAY_POLICY = "recovery_and_replay_policy"
    TEST_AND_EVALUATION_SUITE = "test_and_evaluation_suite"
    RELEASE_ADMISSION_EVIDENCE = "release_admission_evidence"


REQUIRED_CONFORMANCE_CONTRACT_KINDS = frozenset(ConformanceContractKind)


@dataclass(frozen=True)
class ConformanceContractBinding:
    """Immutable ref/hash binding for one package contract family."""

    contract_kind: ConformanceContractKind
    artifact_ref: str
    artifact_sha256: str

    def validate(self) -> None:
        """Reject malformed or untyped contract authority."""

        if not isinstance(self.contract_kind, ConformanceContractKind):
            raise ValueError("contract_kind must be a ConformanceContractKind")
        _validate_ref("conformance artifact_ref", self.artifact_ref)
        _validate_sha256("conformance artifact_sha256", self.artifact_sha256)

    def as_dict(self) -> dict[str, str]:
        """Return the canonical serialization shape."""

        self.validate()
        return {
            "contract_kind": self.contract_kind.value,
            "artifact_ref": self.artifact_ref,
            "artifact_sha256": self.artifact_sha256,
        }


@dataclass(frozen=True)
class DynamicInputBinding:
    """One authorized task-local material available to a module."""

    logical_name: str
    artifact_ref: str
    artifact_sha256: str
    schema_ref: str
    schema_sha256: str

    def validate(self) -> None:
        """Validate content identity without interpreting domain meaning."""

        _validate_id("dynamic input logical_name", self.logical_name)
        _validate_ref("dynamic input artifact_ref", self.artifact_ref)
        _validate_sha256("dynamic input artifact_sha256", self.artifact_sha256)
        _validate_ref("dynamic input schema_ref", self.schema_ref)
        _validate_sha256("dynamic input schema_sha256", self.schema_sha256)

    def as_dict(self) -> dict[str, str]:
        """Return the canonical serialization shape."""

        self.validate()
        return {
            "logical_name": self.logical_name,
            "artifact_ref": self.artifact_ref,
            "artifact_sha256": self.artifact_sha256,
            "schema_ref": self.schema_ref,
            "schema_sha256": self.schema_sha256,
        }


@dataclass(frozen=True)
class ModuleInputProjectionContract:
    """Allowlist describing one module's task-plane package projection."""

    module_id: str
    module_release_ref: str
    module_release_sha256: str
    projected_contract_kinds: tuple[ConformanceContractKind, ...]
    input_schema_ref: str
    input_schema_sha256: str
    output_schema_ref: str
    output_schema_sha256: str
    declared_operation_ids: tuple[str, ...]
    allows_dynamic_materials: bool

    def validate(self) -> None:
        """Validate a closed, provider-neutral module declaration."""

        _validate_id("module_id", self.module_id)
        _validate_ref("module_release_ref", self.module_release_ref)
        _validate_sha256(
            "module_release_sha256", self.module_release_sha256
        )
        if not isinstance(self.projected_contract_kinds, tuple):
            raise ValueError("projected_contract_kinds must be an immutable tuple")
        if not self.projected_contract_kinds:
            raise ValueError("module projection requires contract material")
        if len(self.projected_contract_kinds) != len(
            set(self.projected_contract_kinds)
        ):
            raise ValueError("module projected contract kinds must be unique")
        if any(
            not isinstance(kind, ConformanceContractKind)
            for kind in self.projected_contract_kinds
        ):
            raise ValueError("invalid projected contract kind")
        for label, value in (
            ("input_schema_ref", self.input_schema_ref),
            ("output_schema_ref", self.output_schema_ref),
        ):
            _validate_ref(label, value)
        for label, value in (
            ("input_schema_sha256", self.input_schema_sha256),
            ("output_schema_sha256", self.output_schema_sha256),
        ):
            _validate_sha256(label, value)
        if not isinstance(self.declared_operation_ids, tuple):
            raise ValueError("declared_operation_ids must be an immutable tuple")
        if len(self.declared_operation_ids) != len(set(self.declared_operation_ids)):
            raise ValueError("declared operation ids must be unique")
        for operation_id in self.declared_operation_ids:
            if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(
                operation_id
            ):
                raise ValueError(f"invalid declared operation id: {operation_id!r}")
        if type(self.allows_dynamic_materials) is not bool:
            raise ValueError("allows_dynamic_materials must be a boolean")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical serialization shape."""

        self.validate()
        return {
            "module_id": self.module_id,
            "module_release_ref": self.module_release_ref,
            "module_release_sha256": self.module_release_sha256,
            "projected_contract_kinds": [
                kind.value for kind in self.projected_contract_kinds
            ],
            "input_schema_ref": self.input_schema_ref,
            "input_schema_sha256": self.input_schema_sha256,
            "output_schema_ref": self.output_schema_ref,
            "output_schema_sha256": self.output_schema_sha256,
            "declared_operation_ids": list(self.declared_operation_ids),
            "allows_dynamic_materials": self.allows_dynamic_materials,
        }


@dataclass(frozen=True)
class AgenticWorkflowConformancePackage:
    """Complete contract closure for one managed agentic workflow release."""

    package_id: str
    package_version: str
    workflow_id: str
    workflow_contract_version: str
    lifecycle: WorkflowManagementLifecycle
    contract_bindings: tuple[ConformanceContractBinding, ...]
    module_input_projections: tuple[ModuleInputProjectionContract, ...]
    package_sha256: str

    def canonical_body(self) -> dict[str, Any]:
        """Return the hash body in a stable provider-neutral shape."""

        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "workflow_id": self.workflow_id,
            "workflow_contract_version": self.workflow_contract_version,
            "lifecycle": self.lifecycle.value,
            "contract_bindings": [
                binding.as_dict() for binding in self.contract_bindings
            ],
            "module_input_projections": [
                projection.as_dict() for projection in self.module_input_projections
            ],
        }

    def calculate_sha256(self) -> str:
        """Calculate the canonical package hash."""

        return _canonical_sha256(self.canonical_body())

    def validate(self) -> None:
        """Require a complete closure and an exact canonical package hash."""

        _validate_id("package_id", self.package_id)
        _validate_token("package_version", self.package_version)
        _validate_id("workflow_id", self.workflow_id)
        _validate_token("workflow_contract_version", self.workflow_contract_version)
        if not isinstance(self.lifecycle, WorkflowManagementLifecycle):
            raise ValueError("invalid workflow management lifecycle")
        if not isinstance(self.contract_bindings, tuple):
            raise ValueError("contract_bindings must be an immutable tuple")
        for binding in self.contract_bindings:
            binding.validate()
        kinds = tuple(binding.contract_kind for binding in self.contract_bindings)
        if len(kinds) != len(set(kinds)):
            raise ValueError("conformance contract kinds must be unique")
        missing = REQUIRED_CONFORMANCE_CONTRACT_KINDS.difference(kinds)
        extra = set(kinds).difference(REQUIRED_CONFORMANCE_CONTRACT_KINDS)
        if missing or extra:
            raise ValueError(
                "incomplete conformance contract closure: "
                f"missing={sorted(kind.value for kind in missing)}, "
                f"extra={sorted(kind.value for kind in extra)}"
            )
        if not isinstance(self.module_input_projections, tuple):
            raise ValueError("module_input_projections must be an immutable tuple")
        if not self.module_input_projections:
            raise ValueError("conformance package requires module projections")
        module_ids: list[str] = []
        for projection in self.module_input_projections:
            projection.validate()
            module_ids.append(projection.module_id)
        if len(module_ids) != len(set(module_ids)):
            raise ValueError("module projection ids must be unique")
        _validate_sha256("package_sha256", self.package_sha256)
        if self.package_sha256 != self.calculate_sha256():
            raise ValueError("conformance package hash mismatch")

    def module(self, module_id: str) -> ModuleInputProjectionContract:
        """Resolve one declared module without name inference or fallback."""

        self.validate()
        for projection in self.module_input_projections:
            if projection.module_id == module_id:
                return projection
        raise KeyError(f"unknown module projection: {module_id}")


@dataclass(frozen=True)
class ModuleInputProjection:
    """Least-privilege material closure for one Module Execution Variant."""

    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    conformance_package_id: str
    conformance_package_sha256: str
    module_id: str
    module_release_ref: str
    module_release_sha256: str
    static_contract_bindings: tuple[ConformanceContractBinding, ...]
    dynamic_input_bindings: tuple[DynamicInputBinding, ...]
    authorized_operation_ids: tuple[str, ...]
    input_closure_sha256: str

    def canonical_body(self) -> dict[str, Any]:
        """Return the task-plane input closure used for hashing."""

        return {
            "workflow_execution_id": self.workflow_execution_id,
            "module_run_id": self.module_run_id,
            "variant_id": self.variant_id,
            "conformance_package_id": self.conformance_package_id,
            "conformance_package_sha256": self.conformance_package_sha256,
            "module_id": self.module_id,
            "module_release_ref": self.module_release_ref,
            "module_release_sha256": self.module_release_sha256,
            "static_contract_bindings": [
                binding.as_dict() for binding in self.static_contract_bindings
            ],
            "dynamic_input_bindings": [
                binding.as_dict() for binding in self.dynamic_input_bindings
            ],
            "authorized_operation_ids": list(self.authorized_operation_ids),
        }

    def calculate_sha256(self) -> str:
        """Calculate the canonical task-local input-closure hash."""

        return _canonical_sha256(self.canonical_body())

    def validate_against(self, package: AgenticWorkflowConformancePackage) -> None:
        """Reject missing, extra, cross-release, or unauthorized task input."""

        package.validate()
        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("module_id", self.module_id),
        ):
            _validate_id(label, value)
        if (
            self.conformance_package_id != package.package_id
            or self.conformance_package_sha256 != package.package_sha256
        ):
            raise ValueError("module input crossed conformance package")
        module = package.module(self.module_id)
        if (
            self.module_release_ref != module.module_release_ref
            or self.module_release_sha256 != module.module_release_sha256
        ):
            raise ValueError("module input crossed module release")
        if not isinstance(self.static_contract_bindings, tuple):
            raise ValueError("static_contract_bindings must be an immutable tuple")
        for binding in self.static_contract_bindings:
            binding.validate()
        actual_kinds = tuple(
            binding.contract_kind for binding in self.static_contract_bindings
        )
        if actual_kinds != module.projected_contract_kinds:
            raise ValueError("module input has missing, extra, or reordered contracts")
        package_by_kind = {
            binding.contract_kind: binding for binding in package.contract_bindings
        }
        if any(binding != package_by_kind[binding.contract_kind] for binding in self.static_contract_bindings):
            raise ValueError("module input contract binding differs from package")
        if not isinstance(self.dynamic_input_bindings, tuple):
            raise ValueError("dynamic_input_bindings must be an immutable tuple")
        if self.dynamic_input_bindings and not module.allows_dynamic_materials:
            raise ValueError("module does not allow dynamic materials")
        logical_names: list[str] = []
        for binding in self.dynamic_input_bindings:
            binding.validate()
            logical_names.append(binding.logical_name)
        if len(logical_names) != len(set(logical_names)):
            raise ValueError("dynamic input logical names must be unique")
        if not isinstance(self.authorized_operation_ids, tuple):
            raise ValueError("authorized_operation_ids must be an immutable tuple")
        if len(self.authorized_operation_ids) != len(
            set(self.authorized_operation_ids)
        ):
            raise ValueError("authorized operation ids must be unique")
        undeclared = set(self.authorized_operation_ids).difference(
            module.declared_operation_ids
        )
        if undeclared:
            raise ValueError(
                f"module input contains undeclared operations: {sorted(undeclared)}"
            )
        _validate_sha256("input_closure_sha256", self.input_closure_sha256)
        if self.input_closure_sha256 != self.calculate_sha256():
            raise ValueError("module input closure hash mismatch")


__all__ = [
    "AgenticWorkflowConformancePackage",
    "ModuleInputProjection",
    "ModuleInputProjectionContract",
    "ConformanceContractBinding",
    "ConformanceContractKind",
    "DynamicInputBinding",
    "REQUIRED_CONFORMANCE_CONTRACT_KINDS",
    "WorkflowManagementLifecycle",
]
