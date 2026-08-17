"""Build-time conformance checks for the Agent Runtime source tree."""

from .conformance_architecture_validation import validate_runtime_architecture
from .conformance_consumer_manifesting import (
    build_downstream_consumer_manifest,
    validate_downstream_consumer_manifest,
)

__all__ = [
    "build_downstream_consumer_manifest",
    "validate_downstream_consumer_manifest",
    "validate_runtime_architecture",
]
