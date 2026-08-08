"""Public Agent Runtime port for registered Module Release resolution."""

from __future__ import annotations

from typing import Protocol

from ..contracts.registry_release_definition import RuntimeModuleRelease


class RuntimeModuleReleaseClient(Protocol):
    """Resolve one Module Release produced by Runtime registration."""

    def resolve_registered_module_release(
        self,
        release_ref: str,
        release_sha256: str,
    ) -> RuntimeModuleRelease:
        """Resolve one exact registered Runtime Module Release."""

        ...


__all__ = ["RuntimeModuleReleaseClient"]
