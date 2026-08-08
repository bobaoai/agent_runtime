"""Release authority plus an explicit predecessor-registry compatibility export."""

from .registry_release_registration import RuntimeReleaseBundle, RuntimeReleaseRegistry
from .registry_workflow_registration import (
    WorkflowRuntimeRegistry,
    resolve_registration_reference,
)
from .registry_architecture_registration import (
    RUNTIME_MIGRATION_DEBT_PATHS,
    RUNTIME_PRODUCT_MODULE_REGISTRATIONS,
    RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS,
    RUNTIME_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_STRUCTURAL_SOURCE_PATHS,
    RuntimeProductModuleRegistration,
    RuntimeSourceDirectoryRegistration,
    RuntimeSourceFileRegistration,
    validate_registry_architecture_registration,
)

__all__ = [
    "RUNTIME_MIGRATION_DEBT_PATHS",
    "RUNTIME_PRODUCT_MODULE_REGISTRATIONS",
    "RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS",
    "RUNTIME_SOURCE_FILE_REGISTRATIONS",
    "RUNTIME_STRUCTURAL_SOURCE_PATHS",
    "RuntimeProductModuleRegistration",
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistry",
    "RuntimeSourceFileRegistration",
    "RuntimeSourceDirectoryRegistration",
    "WorkflowRuntimeRegistry",
    "resolve_registration_reference",
    "validate_registry_architecture_registration",
]
