"""Provider-adapter ports shared by SDK, API, and CLI Executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from ..contracts.registry_contract_validation import validate_id
from ..contracts.ledger_lineage_definition import ModuleToolCallObservation
from ..contracts.execution_module_definition import (
    ModuleExecutorRequest,
    ModuleFailureDetailBinding,
    ModuleOutputBinding,
)


class ModuleArtifactHost(Protocol):
    """Cell-local content boundary used by a provider adapter."""

    def read_bytes(self, artifact_ref: str, artifact_sha256: str) -> bytes:
        """Resolve exact bytes after Runtime admitted the reference."""

    def commit_output(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        logical_name: str,
        content: bytes,
        schema_ref: str,
        schema_sha256: str,
        media_type: str,
    ) -> ModuleOutputBinding:
        """Persist one immutable Module output and return its binding."""

    def commit_failure_detail(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        failure_class: str,
        content: bytes,
        media_type: str,
    ) -> ModuleFailureDetailBinding:
        """Persist one bounded Cell-local failure diagnostic."""


@dataclass(frozen=True)
class ProviderToolDefinition:
    """One exact tool schema exposed by an Execution Profile Adapter."""

    tool_name: str
    description: str
    input_schema: Mapping[str, Any]

    def validate(self) -> None:
        """Validate tool identity, description, and Draft 2020-12 input schema."""

        validate_id("tool_name", self.tool_name)
        if type(self.description) is not str or not self.description.strip():
            raise ValueError("Provider tool description must be non-empty")
        if not isinstance(self.input_schema, Mapping):
            raise ValueError("Provider tool input schema must be a mapping")
        Draft202012Validator.check_schema(dict(self.input_schema))


class ModuleProviderToolSession(Protocol):
    """Attempt-local, authorization-closed provider tool surface."""

    @property
    def definitions(self) -> tuple[ProviderToolDefinition, ...]:
        """Return every and only tool exposed for this Attempt."""

    def invoke(self, tool_name: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        """Execute one admitted call and return model-visible semantic content."""

    def validate_completion(self) -> None:
        """Fail when required registered reads were skipped or left incomplete."""

    @property
    def observations(self) -> tuple[ModuleToolCallObservation, ...]:
        """Return Runtime-authored request/response lineage for completed calls."""


class ModuleProviderToolSessionFactory(Protocol):
    """Create one isolated tool session from an exact Executor request."""

    def open_session(
        self,
        request: ModuleExecutorRequest,
    ) -> ModuleProviderToolSession:
        """Bind exact Module inputs and authorization to one provider Attempt."""


__all__ = [
    "ModuleArtifactHost",
    "ModuleProviderToolSession",
    "ModuleProviderToolSessionFactory",
    "ProviderToolDefinition",
]
