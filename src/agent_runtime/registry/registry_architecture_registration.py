"""Code-owned registration of the Agent Runtime product architecture.

This repository-only registry keeps logical responsibilities, physical source
placement, and concrete implementation bindings as independent axes. CI
compares every Runtime Python file with either one target registration, one
structural-package exception, or one explicit migration-debt entry. A new file
therefore cannot silently bypass architecture review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import ClassVar


_SOURCE_NAME = re.compile(
    r"^[a-z][a-z0-9]*_[a-z][a-z0-9_]*_[a-z][a-z0-9]*$"
)
_ARCHITECTURE_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_RUNTIME_SOURCE_ROOT = "src/agent_runtime"
RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS = (
    "registry",
    "execution",
    "invocation",
    "durability",
    "ledger",
    "inspection",
)
RUNTIME_REQUIRED_SUPPORTING_PLANE_IDS = (
    "foundation",
    "conformance",
)
RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS = (
    "claude_agent_sdk",
    "codex_cli",
    "html",
    "http",
    "postgresql",
    "temporal",
)


@dataclass(frozen=True)
class RuntimeLogicalResponsibilityRegistration:
    """One stable logical responsibility owned by Agent Runtime."""

    record_type: ClassVar[str] = "runtime_logical_responsibility_registration"

    responsibility_id: str
    responsibility: str
    owner_contract_ref: str


@dataclass(frozen=True)
class RuntimeSourceDirectoryRegistration:
    """One physical source directory and its allowed purpose."""

    record_type: ClassVar[str] = "runtime_source_directory_registration"

    source_directory_id: str
    source_directory: str
    purpose: str


@dataclass(frozen=True)
class RuntimeSupportingPlaneRegistration:
    """One non-service plane supporting all Runtime responsibilities."""

    record_type: ClassVar[str] = "runtime_supporting_plane_registration"

    supporting_plane_id: str
    purpose: str
    owner_contract_ref: str


@dataclass(frozen=True)
class RuntimeSupportingSourceFileRegistration:
    """One source file owned by Foundation or build-time Conformance."""

    record_type: ClassVar[str] = "runtime_supporting_source_file_registration"

    source_path: str
    supporting_plane_id: str
    source_directory_id: str
    subject: str
    nominalized_action: str
    owner_contract_ref: str

    @property
    def expected_file_name(self) -> str:
        """Return the required three-part Python filename."""

        return (
            f"{self.supporting_plane_id}_"
            f"{self.subject}_{self.nominalized_action}.py"
        )


@dataclass(frozen=True)
class RuntimeSourceFileRegistration:
    """One target source file with logical and physical ownership."""

    record_type: ClassVar[str] = "runtime_source_file_registration"

    source_path: str
    logical_responsibility_id: str
    source_directory_id: str
    subject: str
    nominalized_action: str
    owner_contract_ref: str
    implementation_binding_id: str | None = None

    @property
    def expected_file_name(self) -> str:
        """Return the required three-part Python filename."""

        return (
            f"{self.logical_responsibility_id}_"
            f"{self.subject}_{self.nominalized_action}.py"
        )


@dataclass(frozen=True)
class RuntimeImplementationBindingRegistration:
    """One concrete technology implementing a logical responsibility."""

    record_type: ClassVar[str] = "runtime_implementation_binding_registration"

    implementation_binding_id: str
    logical_responsibility_id: str
    technology_id: str
    implementation_source_paths: tuple[str, ...]


RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS = (
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="registry",
        responsibility=(
            "Register Runtime architecture; compile, activate, retrieve, and "
            "project immutable releases."
        ),
        owner_contract_ref="designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="execution",
        responsibility=(
            "Start and advance workflows and modules; stage admitted content; "
            "coordinate authorization, evaluation, and output resolution."
        ),
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="invocation",
        responsibility=(
            "Assemble admitted model context and invoke registered model or "
            "tool execution profiles."
        ),
        owner_contract_ref="designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="durability",
        responsibility=(
            "Coordinate acknowledged commands, waits, retries, replay, and "
            "recovery through a replaceable durable backend."
        ),
        owner_contract_ref="designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    ),
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="ledger",
        responsibility=(
            "Define and commit authoritative execution lineage, attempts, "
            "usage, outcomes, and resolution facts."
        ),
        owner_contract_ref=(
            "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
        ),
    ),
    RuntimeLogicalResponsibilityRegistration(
        responsibility_id="inspection",
        responsibility=(
            "Project and render authorized, read-only Runtime release and "
            "execution views."
        ),
        owner_contract_ref=(
            "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
        ),
    ),
)


RUNTIME_SUPPORTING_PLANE_REGISTRATIONS = (
    RuntimeSupportingPlaneRegistration(
        supporting_plane_id="foundation",
        purpose=(
            "Own responsibility-neutral identity, serialization, validation, "
            "and immutable-value primitives without importing Runtime peers."
        ),
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingPlaneRegistration(
        supporting_plane_id="conformance",
        purpose=(
            "Validate source ownership, dependency direction, public surfaces, "
            "and shrinking migration debt outside product execution."
        ),
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
)


RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS = (
    *(
        RuntimeSourceDirectoryRegistration(
            source_directory_id=responsibility_id,
            source_directory=f"{_RUNTIME_SOURCE_ROOT}/{responsibility_id}",
            purpose=f"{responsibility_id} logical responsibility implementation",
        )
        for responsibility_id in (
            RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS
        )
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="contracts",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/contracts",
        purpose=(
            "cross-responsibility boundary definitions owned by one logical "
            "responsibility"
        ),
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="testing",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/testing",
        purpose=(
            "shipped Runtime evaluation and Adapter-conformance entry points; "
            "each source retains its registered responsibility owner"
        ),
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="foundation",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/foundation",
        purpose="responsibility-neutral import-free Runtime primitives",
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="conformance",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/conformance",
        purpose="build-time Runtime architecture and release validation",
    ),
)


RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS = (
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="ledger_postgres_persistence",
        logical_responsibility_id="ledger",
        technology_id="postgresql",
        implementation_source_paths=(
            "src/agent_runtime/ledger/ledger_postgres_persistence.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="registry_postgres_persistence",
        logical_responsibility_id="registry",
        technology_id="postgresql",
        implementation_source_paths=(
            "src/agent_runtime/registry/registry_postgres_persistence.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="inspection_http_serving",
        logical_responsibility_id="inspection",
        technology_id="http",
        implementation_source_paths=(
            "src/agent_runtime/inspection/inspection_http_serving.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="inspection_postgres_querying",
        logical_responsibility_id="inspection",
        technology_id="postgresql",
        implementation_source_paths=(
            "src/agent_runtime/inspection/inspection_postgres_querying.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="invocation_claude_agent_sdk",
        logical_responsibility_id="invocation",
        technology_id="claude_agent_sdk",
        implementation_source_paths=(
            "src/agent_runtime/invocation/invocation_claude_module_invocation.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="invocation_codex_cli",
        logical_responsibility_id="invocation",
        technology_id="codex_cli",
        implementation_source_paths=(
            "src/agent_runtime/invocation/invocation_codex_module_invocation.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="durability_temporal_coordination",
        logical_responsibility_id="durability",
        technology_id="temporal",
        implementation_source_paths=(
            "src/agent_runtime/durability/durability_temporal_coordination.py",
        ),
    ),
    RuntimeImplementationBindingRegistration(
        implementation_binding_id="inspection_html_rendering",
        logical_responsibility_id="inspection",
        technology_id="html",
        implementation_source_paths=(
            "src/agent_runtime/inspection/inspection_snapshot_rendering.py",
        ),
    ),
)


def _source(
    logical_responsibility_id: str,
    subject: str,
    nominalized_action: str,
    owner_contract_ref: str,
    *,
    source_directory_id: str | None = None,
    implementation_binding_id: str | None = None,
) -> RuntimeSourceFileRegistration:
    physical_directory = source_directory_id or logical_responsibility_id
    return RuntimeSourceFileRegistration(
        source_path=(
            f"{_RUNTIME_SOURCE_ROOT}/{physical_directory}/"
            f"{logical_responsibility_id}_{subject}_{nominalized_action}.py"
        ),
        logical_responsibility_id=logical_responsibility_id,
        source_directory_id=physical_directory,
        subject=subject,
        nominalized_action=nominalized_action,
        owner_contract_ref=owner_contract_ref,
        implementation_binding_id=implementation_binding_id,
    )


RUNTIME_SOURCE_FILE_REGISTRATIONS = (
    _source(
        "registry",
        "architecture",
        "registration",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "registry",
        "migration",
        "validation",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        source_directory_id="testing",
    ),
    _source(
        "registry",
        "release",
        "definition",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        source_directory_id="contracts",
    ),
    _source(
        "inspection",
        "release",
        "definition",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        source_directory_id="contracts",
    ),
    _source(
        "registry",
        "package",
        "definition",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        source_directory_id="contracts",
    ),
    _source(
        "durability",
        "backend",
        "definition",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "durability",
        "execution",
        "definition",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "invocation",
        "adapter",
        "definition",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "execution",
        "module",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "ledger",
        "lineage",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "ledger",
        "record",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "ledger",
        "content",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "execution",
        "host",
        "definition",
        "designDoc/agent_runtime_00_execution_charter.md",
        source_directory_id="contracts",
    ),
    _source(
        "execution",
        "event",
        "definition",
        "designDoc/agent_runtime_03_authorized_external_event_ingress.md",
        source_directory_id="contracts",
    ),
    _source(
        "execution",
        "authorization",
        "definition",
        "designDoc/agent_runtime_09_authorization_integration_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "registry",
        "release",
        "registration",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "release",
        "compilation",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "release",
        "retrieval",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "plugin",
        "registration",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "authoring_inventory",
        "loading",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "authoring_release",
        "building",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "authoring_release",
        "submission",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "registry",
        "graph",
        "projection",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "execution",
        "module",
        "invocation",
        "designDoc/agent_runtime_00_execution_charter.md",
    ),
    _source(
        "ledger",
        "lineage",
        "recording",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "ledger",
        "workflow_module",
        "recording",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "ledger",
        "workflow_execution",
        "recording",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "ledger",
        "execution_content",
        "recording",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "ledger",
        "record",
        "persistence",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "ledger",
        "postgres",
        "persistence",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        implementation_binding_id="ledger_postgres_persistence",
    ),
    _source(
        "ledger",
        "usage",
        "aggregation",
        "designDoc/agent_runtime_00_execution_charter.md",
    ),
    _source(
        "execution",
        "content",
        "staging",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "execution",
        "authorization",
        "coordination",
        "designDoc/agent_runtime_09_authorization_integration_contract.md",
    ),
    _source(
        "execution",
        "authorization",
        "resolution",
        "designDoc/agent_runtime_09_authorization_integration_contract.md",
    ),
    _source(
        "execution",
        "operation",
        "resolution",
        "designDoc/agent_runtime_09_authorization_integration_contract.md",
    ),
    _source(
        "invocation",
        "prompt",
        "assembly",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "context",
        "preparation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "failure",
        "recording",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "result",
        "assembly",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "schema",
        "projection",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "claude_module",
        "invocation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
        implementation_binding_id="invocation_claude_agent_sdk",
    ),
    _source(
        "invocation",
        "codex_module",
        "invocation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
        implementation_binding_id="invocation_codex_cli",
    ),
    _source(
        "invocation",
        "tool",
        "definition",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "invocation",
        "workspace",
        "preparation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "durability",
        "backend",
        "registration",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    ),
    _source(
        "durability",
        "temporal",
        "coordination",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        implementation_binding_id="durability_temporal_coordination",
    ),
    _source(
        "durability",
        "workflow",
        "coordination",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    ),
    _source(
        "durability",
        "backend",
        "conformance",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="testing",
    ),
    _source(
        "durability",
        "temporal",
        "conformance",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="testing",
    ),
    _source(
        "durability",
        "hatchet",
        "evaluation",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="testing",
    ),
    _source(
        "durability",
        "native",
        "evaluation",
        "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
        source_directory_id="testing",
    ),
    _source(
        "execution",
        "module",
        "evaluation",
        "designDoc/agent_runtime_00_execution_charter.md",
        source_directory_id="testing",
    ),
    _source(
        "execution",
        "context",
        "resolution",
        "designDoc/agent_runtime_00_execution_charter.md",
    ),
    _source(
        "registry",
        "postgres",
        "persistence",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        implementation_binding_id="registry_postgres_persistence",
    ),
    _source(
        "inspection",
        "architecture",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "release",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "record",
        "schema_rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "execution",
        "projecting",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "snapshot",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "snapshot",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        implementation_binding_id="inspection_html_rendering",
    ),
    _source(
        "inspection",
        "snapshot",
        "exporting",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "inspection",
        "http",
        "serving",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        implementation_binding_id="inspection_http_serving",
    ),
    _source(
        "inspection",
        "postgres",
        "querying",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        implementation_binding_id="inspection_postgres_querying",
    ),
)


RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS = (
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/foundation/foundation_contract_validation.py"
        ),
        supporting_plane_id="foundation",
        source_directory_id="foundation",
        subject="contract",
        nominalized_action="validation",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/foundation/foundation_retry_policy_validation.py"
        ),
        supporting_plane_id="foundation",
        source_directory_id="foundation",
        subject="retry_policy",
        nominalized_action="validation",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/foundation/foundation_schema_traversal.py"
        ),
        supporting_plane_id="foundation",
        source_directory_id="foundation",
        subject="schema",
        nominalized_action="traversal",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/foundation/foundation_json_schema_validation.py"
        ),
        supporting_plane_id="foundation",
        source_directory_id="foundation",
        subject="json_schema",
        nominalized_action="validation",
        owner_contract_ref="designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/conformance/"
            "conformance_architecture_manifest.py"
        ),
        supporting_plane_id="conformance",
        source_directory_id="conformance",
        subject="architecture",
        nominalized_action="manifest",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/conformance/"
            "conformance_architecture_validation.py"
        ),
        supporting_plane_id="conformance",
        source_directory_id="conformance",
        subject="architecture",
        nominalized_action="validation",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeSupportingSourceFileRegistration(
        source_path=(
            "src/agent_runtime/conformance/"
            "conformance_consumer_manifesting.py"
        ),
        supporting_plane_id="conformance",
        source_directory_id="conformance",
        subject="consumer",
        nominalized_action="manifesting",
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
)


# Package initializers are structural Python files, not executable product
# components, and therefore do not use the three-part semantic filename.
RUNTIME_STRUCTURAL_SOURCE_PATHS = (
    "src/agent_runtime/__init__.py",
    "src/agent_runtime/conformance/__init__.py",
    "src/agent_runtime/contracts/__init__.py",
    "src/agent_runtime/durability/__init__.py",
    "src/agent_runtime/execution/__init__.py",
    "src/agent_runtime/foundation/__init__.py",
    "src/agent_runtime/inspection/__init__.py",
    "src/agent_runtime/invocation/__init__.py",
    "src/agent_runtime/ledger/__init__.py",
    "src/agent_runtime/registry/__init__.py",
    "src/agent_runtime/testing/__init__.py",
)


# Every remaining predecessor file is explicit, reviewable debt.  The list must
# shrink as files are split, migrated, or retired; a new unregistered file fails
# validation immediately.
RUNTIME_MIGRATION_DEBT_PATHS = (
    "src/agent_runtime/contracts/execution_operation_definition.py",
    "src/agent_runtime/contracts/registry_workflow_definition.py",
    "src/agent_runtime/execution/execution_event_ingestion.py",
    "src/agent_runtime/execution/execution_operation_authorization.py",
    "src/agent_runtime/registry/registry_workflow_registration.py",
)


def _registered_source_paths() -> set[str]:
    """Return source paths declared by the current registration tuple."""

    return {
        source.source_path
        for source in (
            *RUNTIME_SOURCE_FILE_REGISTRATIONS,
            *RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS,
        )
    }


def validate_runtime_architecture_registration() -> tuple[str, ...]:
    """Return registry-internal violations without reading a source checkout."""

    errors: list[str] = []
    responsibilities = {
        item.responsibility_id: item
        for item in RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
    }
    supporting_planes = {
        item.supporting_plane_id: item
        for item in RUNTIME_SUPPORTING_PLANE_REGISTRATIONS
    }
    directories = {
        item.source_directory_id: item
        for item in RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
    }
    implementation_bindings = {
        item.implementation_binding_id: item
        for item in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
    }
    if len(responsibilities) != len(
        RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
    ):
        errors.append("duplicate Runtime logical responsibility")
    if tuple(responsibilities) != RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS:
        errors.append(
            "Runtime logical responsibilities must be exactly: "
            + ", ".join(RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS)
        )
    if len(supporting_planes) != len(RUNTIME_SUPPORTING_PLANE_REGISTRATIONS):
        errors.append("duplicate Runtime supporting plane")
    if tuple(supporting_planes) != RUNTIME_REQUIRED_SUPPORTING_PLANE_IDS:
        errors.append(
            "Runtime supporting planes must be exactly: "
            + ", ".join(RUNTIME_REQUIRED_SUPPORTING_PLANE_IDS)
        )
    if len(directories) != len(RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS):
        errors.append("duplicate Runtime source directory")
    if len(implementation_bindings) != len(
        RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
    ):
        errors.append("duplicate Runtime implementation binding")
    if tuple(
        sorted(
            {
                binding.technology_id
                for binding in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
            }
        )
    ) != RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS:
        errors.append("Runtime implementation technologies differ from the required set")

    architecture_ids = (
        *(
            ("logical responsibility", row.responsibility_id)
            for row in RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
        ),
        *(
            ("source directory", row.source_directory_id)
            for row in RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
        ),
        *(
            ("supporting plane", row.supporting_plane_id)
            for row in RUNTIME_SUPPORTING_PLANE_REGISTRATIONS
        ),
        *(
            ("implementation binding", row.implementation_binding_id)
            for row in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
        ),
        *(
            ("technology", row.technology_id)
            for row in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
        ),
    )
    for identifier_kind, identifier in architecture_ids:
        if not _ARCHITECTURE_ID.fullmatch(identifier):
            errors.append(f"invalid Runtime {identifier_kind} id: {identifier}")

    for binding in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS:
        if binding.technology_id in responsibilities:
            errors.append(
                f"{binding.implementation_binding_id}: technology id cannot be "
                f"a logical responsibility: {binding.technology_id}"
            )
        if binding.logical_responsibility_id not in responsibilities:
            errors.append(
                f"{binding.implementation_binding_id}: unknown logical "
                f"responsibility {binding.logical_responsibility_id}"
            )
        if not binding.implementation_source_paths:
            errors.append(
                f"{binding.implementation_binding_id}: no implementation source"
            )

    source_paths: set[str] = set()
    detached_file_names: set[str] = set()
    for source in RUNTIME_SOURCE_FILE_REGISTRATIONS:
        if source.source_path in source_paths:
            errors.append(f"duplicate Runtime source path: {source.source_path}")
        source_paths.add(source.source_path)
        if source.logical_responsibility_id not in responsibilities:
            errors.append(
                f"{source.source_path}: unknown logical responsibility "
                f"{source.logical_responsibility_id}"
            )
        directory = directories.get(source.source_directory_id)
        if directory is None:
            errors.append(
                f"{source.source_path}: unknown source directory "
                f"{source.source_directory_id}"
            )
        elif Path(source.source_path).parent.as_posix() != directory.source_directory:
            errors.append(
                f"{source.source_path}: not contained by registered source directory "
                f"{directory.source_directory}"
            )
        if Path(source.source_path).name != source.expected_file_name:
            errors.append(
                f"{source.source_path}: expected filename {source.expected_file_name}"
            )
        if source.expected_file_name in detached_file_names:
            errors.append(
                f"duplicate detached Runtime source name: {source.expected_file_name}"
            )
        detached_file_names.add(source.expected_file_name)
        if not _SOURCE_NAME.fullmatch(Path(source.source_path).stem):
            errors.append(f"{source.source_path}: invalid three-part source name")
        if source.implementation_binding_id is not None:
            binding = implementation_bindings.get(
                source.implementation_binding_id
            )
            if binding is None:
                errors.append(
                    f"{source.source_path}: unknown implementation binding "
                    f"{source.implementation_binding_id}"
                )
            elif (
                binding.logical_responsibility_id
                != source.logical_responsibility_id
            ):
                errors.append(
                    f"{source.source_path}: implementation binding crosses "
                    "logical responsibility"
                )
            elif source.source_path not in binding.implementation_source_paths:
                errors.append(
                    f"{source.source_path}: implementation binding does not "
                    "declare this source"
                )

    for source in RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS:
        if source.source_path in source_paths:
            errors.append(f"duplicate Runtime source path: {source.source_path}")
        source_paths.add(source.source_path)
        if source.supporting_plane_id not in supporting_planes:
            errors.append(
                f"{source.source_path}: unknown supporting plane "
                f"{source.supporting_plane_id}"
            )
        directory = directories.get(source.source_directory_id)
        if directory is None:
            errors.append(
                f"{source.source_path}: unknown source directory "
                f"{source.source_directory_id}"
            )
        elif Path(source.source_path).parent.as_posix() != directory.source_directory:
            errors.append(
                f"{source.source_path}: not contained by registered source directory "
                f"{directory.source_directory}"
            )
        if Path(source.source_path).name != source.expected_file_name:
            errors.append(
                f"{source.source_path}: expected filename {source.expected_file_name}"
            )
        if source.expected_file_name in detached_file_names:
            errors.append(
                f"duplicate detached Runtime source name: {source.expected_file_name}"
            )
        detached_file_names.add(source.expected_file_name)
        if not _SOURCE_NAME.fullmatch(Path(source.source_path).stem):
            errors.append(f"{source.source_path}: invalid three-part source name")

    registered_implementation_sources = {
        source.source_path: source.implementation_binding_id
        for source in RUNTIME_SOURCE_FILE_REGISTRATIONS
        if source.implementation_binding_id is not None
    }
    for binding in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS:
        for source_path in binding.implementation_source_paths:
            if registered_implementation_sources.get(source_path) != (
                binding.implementation_binding_id
            ):
                errors.append(
                    f"{binding.implementation_binding_id}: implementation source "
                    f"is not bound by its source registration: {source_path}"
                )

    overlaps = (
        source_paths.intersection(RUNTIME_STRUCTURAL_SOURCE_PATHS)
        | source_paths.intersection(RUNTIME_MIGRATION_DEBT_PATHS)
        | set(RUNTIME_STRUCTURAL_SOURCE_PATHS).intersection(
            RUNTIME_MIGRATION_DEBT_PATHS
        )
    )
    for path in sorted(overlaps):
        errors.append(f"Runtime source has duplicate disposition: {path}")
    return tuple(errors)


def validate_registry_architecture_registration(project_root: Path) -> tuple[str, ...]:
    """Return registry and repository-checkout architecture violations."""

    project_root = project_root.resolve()
    errors = list(validate_runtime_architecture_registration())
    source_paths = _registered_source_paths()
    for source in RUNTIME_SOURCE_FILE_REGISTRATIONS:
        if not (project_root / source.source_path).is_file():
            errors.append(f"registered Runtime source not found: {source.source_path}")
        if not (project_root / source.owner_contract_ref).is_file():
            errors.append(
                f"{source.source_path}: owner contract not found: "
                f"{source.owner_contract_ref}"
            )
    for source in RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS:
        if not (project_root / source.source_path).is_file():
            errors.append(f"registered Runtime source not found: {source.source_path}")
        if not (project_root / source.owner_contract_ref).is_file():
            errors.append(
                f"{source.source_path}: owner contract not found: "
                f"{source.owner_contract_ref}"
            )

    declared_paths = (
        source_paths
        | set(RUNTIME_STRUCTURAL_SOURCE_PATHS)
        | set(RUNTIME_MIGRATION_DEBT_PATHS)
    )
    runtime_root = project_root / _RUNTIME_SOURCE_ROOT
    actual_paths = {
        path.relative_to(project_root).as_posix()
        for path in runtime_root.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    for path in sorted(actual_paths - declared_paths):
        errors.append(f"unregistered Runtime source file: {path}")
    for path in sorted(declared_paths - actual_paths):
        errors.append(f"declared Runtime source file not found: {path}")
    return tuple(errors)


__all__ = [
    "RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS",
    "RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS",
    "RUNTIME_MIGRATION_DEBT_PATHS",
    "RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS",
    "RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS",
    "RUNTIME_REQUIRED_SUPPORTING_PLANE_IDS",
    "RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS",
    "RUNTIME_SOURCE_FILE_REGISTRATIONS",
    "RUNTIME_SUPPORTING_PLANE_REGISTRATIONS",
    "RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS",
    "RUNTIME_STRUCTURAL_SOURCE_PATHS",
    "RuntimeImplementationBindingRegistration",
    "RuntimeLogicalResponsibilityRegistration",
    "RuntimeSourceDirectoryRegistration",
    "RuntimeSourceFileRegistration",
    "RuntimeSupportingPlaneRegistration",
    "RuntimeSupportingSourceFileRegistration",
    "validate_registry_architecture_registration",
    "validate_runtime_architecture_registration",
]
