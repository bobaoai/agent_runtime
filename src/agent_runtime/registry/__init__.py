"""Release authority plus an explicit predecessor-registry compatibility export."""

from .registry_release_registration import RuntimeReleaseBundle, RuntimeReleaseRegistry
from .registry_postgres_persistence import (
    PostgresRuntimeReleaseStore,
    postgres_release_ddl,
    serialize_registry_tables,
)
from .registry_workflow_registration import (
    WorkflowRuntimeRegistry,
    resolve_registration_reference,
)
from .registry_architecture_registration import (
    RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS,
    RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS,
    RUNTIME_MIGRATION_DEBT_PATHS,
    RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS,
    RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS,
    RUNTIME_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_STRUCTURAL_SOURCE_PATHS,
    RuntimeImplementationBindingRegistration,
    RuntimeLogicalResponsibilityRegistration,
    RuntimeSourceDirectoryRegistration,
    RuntimeSourceFileRegistration,
    validate_registry_architecture_registration,
    validate_runtime_architecture_registration,
)

__all__ = [
    "RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS",
    "RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS",
    "RUNTIME_MIGRATION_DEBT_PATHS",
    "RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS",
    "RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS",
    "RUNTIME_SOURCE_FILE_REGISTRATIONS",
    "RUNTIME_STRUCTURAL_SOURCE_PATHS",
    "RuntimeImplementationBindingRegistration",
    "RuntimeLogicalResponsibilityRegistration",
    "PostgresRuntimeReleaseStore",
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistry",
    "RuntimeSourceFileRegistration",
    "RuntimeSourceDirectoryRegistration",
    "WorkflowRuntimeRegistry",
    "postgres_release_ddl",
    "resolve_registration_reference",
    "serialize_registry_tables",
    "validate_registry_architecture_registration",
    "validate_runtime_architecture_registration",
]
