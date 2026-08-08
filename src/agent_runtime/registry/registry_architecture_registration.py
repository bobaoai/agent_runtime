"""Code-owned registration of the Agent Runtime product architecture.

This repository-only registry separates logical product ownership from physical
source placement.  CI compares every Runtime Python file with either one target
registration, one structural-package exception, or one explicit migration-debt
entry.  A new file therefore cannot silently bypass architecture review.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import ClassVar


_SOURCE_NAME = re.compile(
    r"^[a-z][a-z0-9]*_[a-z][a-z0-9_]*_[a-z][a-z0-9]*$"
)
_RUNTIME_SOURCE_ROOT = "src/agent_runtime"


@dataclass(frozen=True)
class RuntimeProductModuleRegistration:
    """One peer logical responsibility inside Agent Runtime."""

    record_type: ClassVar[str] = "runtime_product_module_registration"

    module_id: str
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
class RuntimeSourceFileRegistration:
    """One target source file with logical and physical ownership."""

    record_type: ClassVar[str] = "runtime_source_file_registration"

    source_path: str
    module_id: str
    source_directory_id: str
    subject: str
    nominalized_action: str
    owner_contract_ref: str

    @property
    def expected_file_name(self) -> str:
        """Return the required three-part Python filename."""

        return f"{self.module_id}_{self.subject}_{self.nominalized_action}.py"


RUNTIME_PRODUCT_MODULE_REGISTRATIONS = (
    RuntimeProductModuleRegistration(
        module_id="registry",
        responsibility=(
            "Register Runtime architecture; compile, activate, retrieve, and "
            "project immutable releases."
        ),
        owner_contract_ref="designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    RuntimeProductModuleRegistration(
        module_id="execution",
        responsibility=(
            "Run workflows and modules; own portable execution-record "
            "contracts, Cell-local staging, and execution lineage."
        ),
        owner_contract_ref="designDoc/agent_runtime_00_execution_charter.md",
    ),
    RuntimeProductModuleRegistration(
        module_id="provider",
        responsibility="Assemble model context and invoke registered provider profiles.",
        owner_contract_ref="designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    RuntimeProductModuleRegistration(
        module_id="durability",
        responsibility="Coordinate durable workflow execution through Temporal.",
        owner_contract_ref="designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    ),
    RuntimeProductModuleRegistration(
        module_id="postgres",
        responsibility=(
            "Implement production PostgreSQL persistence for Runtime releases, "
            "execution records, and recorded content."
        ),
        owner_contract_ref=(
            "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
        ),
    ),
    RuntimeProductModuleRegistration(
        module_id="review",
        responsibility="Read and render authorized Runtime execution records.",
        owner_contract_ref=(
            "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
        ),
    ),
)


RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS = (
    *(
        RuntimeSourceDirectoryRegistration(
            source_directory_id=module.module_id,
            source_directory=f"{_RUNTIME_SOURCE_ROOT}/{module.module_id}",
            purpose=f"{module.module_id} product implementation",
        )
        for module in RUNTIME_PRODUCT_MODULE_REGISTRATIONS
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="contracts",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/contracts",
        purpose="cross-module boundary definitions owned by a product module",
    ),
    RuntimeSourceDirectoryRegistration(
        source_directory_id="testing",
        source_directory=f"{_RUNTIME_SOURCE_ROOT}/testing",
        purpose="standalone architecture and conformance implementations",
    ),
)


def _source(
    module_id: str,
    subject: str,
    nominalized_action: str,
    owner_contract_ref: str,
    *,
    source_directory_id: str | None = None,
) -> RuntimeSourceFileRegistration:
    physical_directory = source_directory_id or module_id
    return RuntimeSourceFileRegistration(
        source_path=(
            f"{_RUNTIME_SOURCE_ROOT}/{physical_directory}/"
            f"{module_id}_{subject}_{nominalized_action}.py"
        ),
        module_id=module_id,
        source_directory_id=physical_directory,
        subject=subject,
        nominalized_action=nominalized_action,
        owner_contract_ref=owner_contract_ref,
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
        "designDoc/agent_runtime_05_delivery_roadmap.md",
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
        "registry",
        "contract",
        "validation",
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
        "topology",
        "definition",
        "designDoc/agent_runtime_02_product_target_topology.md",
        source_directory_id="contracts",
    ),
    _source(
        "provider",
        "invocation",
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
        "execution",
        "lineage",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
        source_directory_id="contracts",
    ),
    _source(
        "execution",
        "record",
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
        "module",
        "exporting",
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
        "execution",
        "lineage",
        "recording",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "execution",
        "record",
        "persistence",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "execution",
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
        "provider",
        "prompt",
        "assembly",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "provider",
        "schema",
        "projection",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "provider",
        "model",
        "invocation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "provider",
        "claude_module",
        "invocation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "provider",
        "codex_module",
        "invocation",
        "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    ),
    _source(
        "provider",
        "tool",
        "definition",
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
        "designDoc/agent_runtime_02_product_target_topology.md",
        source_directory_id="testing",
    ),
    _source(
        "durability",
        "native",
        "evaluation",
        "designDoc/agent_runtime_02_product_target_topology.md",
        source_directory_id="testing",
    ),
    _source(
        "postgres",
        "release",
        "persistence",
        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    ),
    _source(
        "review",
        "architecture",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "review",
        "release",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "review",
        "snapshot",
        "definition",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "review",
        "snapshot",
        "rendering",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
    _source(
        "review",
        "snapshot",
        "exporting",
        "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    ),
)


# Package initializers are structural Python files, not executable product
# components, and therefore do not use the three-part semantic filename.
RUNTIME_STRUCTURAL_SOURCE_PATHS = (
    "src/agent_runtime/__init__.py",
    "src/agent_runtime/contracts/__init__.py",
    "src/agent_runtime/execution/__init__.py",
    "src/agent_runtime/durability/__init__.py",
    "src/agent_runtime/postgres/__init__.py",
    "src/agent_runtime/provider/__init__.py",
    "src/agent_runtime/registry/__init__.py",
    "src/agent_runtime/review/__init__.py",
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


def validate_registry_architecture_registration(project_root: Path) -> tuple[str, ...]:
    """Return deterministic repository architecture violations."""

    errors: list[str] = []
    modules = {item.module_id: item for item in RUNTIME_PRODUCT_MODULE_REGISTRATIONS}
    directories = {
        item.source_directory_id: item
        for item in RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
    }
    if len(modules) != len(RUNTIME_PRODUCT_MODULE_REGISTRATIONS):
        errors.append("duplicate Runtime product module")
    if len(directories) != len(RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS):
        errors.append("duplicate Runtime source directory")

    source_paths: set[str] = set()
    detached_file_names: set[str] = set()
    for source in RUNTIME_SOURCE_FILE_REGISTRATIONS:
        if source.source_path in source_paths:
            errors.append(f"duplicate Runtime source path: {source.source_path}")
        source_paths.add(source.source_path)
        if source.module_id not in modules:
            errors.append(
                f"{source.source_path}: unknown product module {source.module_id}"
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


__all__ = [
    "RUNTIME_MIGRATION_DEBT_PATHS",
    "RUNTIME_PRODUCT_MODULE_REGISTRATIONS",
    "RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS",
    "RUNTIME_SOURCE_FILE_REGISTRATIONS",
    "RUNTIME_STRUCTURAL_SOURCE_PATHS",
    "RuntimeProductModuleRegistration",
    "RuntimeSourceDirectoryRegistration",
    "RuntimeSourceFileRegistration",
    "validate_registry_architecture_registration",
]
