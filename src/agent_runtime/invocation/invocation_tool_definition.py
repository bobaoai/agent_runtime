"""Provider-adapter ports shared by SDK, API, and CLI Executors."""

from __future__ import annotations

from dataclasses import dataclass
import functools
from importlib import metadata
from typing import Any, Mapping, Protocol

from ..contracts.execution_module_definition import (
    ModuleFailureDetailBinding,
    ModuleOutputBinding,
)
from ..contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionRequest,
    AuthorizedOperationReceipt,
    ProviderOperationIntent,
)
from ..contracts.ledger_lineage_definition import ModuleToolCallObservation
from ..foundation.foundation_contract_validation import validate_id


@functools.cache
def runtime_package_version() -> str:
    """Return the installed Runtime package version for adapter descriptors.

    Cached: the installed version cannot change within a process and the
    metadata read sits on the per-attempt path.
    """

    try:
        return metadata.version("agent-runtime-core")
    except metadata.PackageNotFoundError:
        return "0.0.0.dev0"


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

    def commit_attempt_trace(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        content: bytes,
        media_type: str,
    ) -> tuple[str, str]:
        """Persist one bounded Cell-local provider trace; return (ref, sha256)."""


@dataclass(frozen=True)
class ProviderToolDefinition:
    """One exact tool schema exposed by an Execution Profile Adapter."""

    tool_name: str
    description: str
    input_schema: Mapping[str, Any]

    def validate(self) -> None:
        """Validate tool identity, description, and Draft 2020-12 input schema."""

        # Imported lazily so the dependency-free core namespace stays
        # importable from a clean wheel without provider extras.
        from jsonschema import Draft202012Validator

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

    def operation_intent(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
    ) -> ProviderOperationIntent:
        """Build the exact Runtime authorization request for one tool call."""

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        authorization: AuthorizedOperationReceipt,
    ) -> Mapping[str, Any]:
        """Execute only after receiving the Runtime authorization receipt."""

    def validate_completion(self) -> None:
        """Fail when required registered reads were skipped or left incomplete."""

    @property
    def observations(self) -> tuple[ModuleToolCallObservation, ...]:
        """Return Runtime-authored request/response lineage for completed calls."""


class ModuleProviderToolSessionFactory(Protocol):
    """Create one isolated tool session from an exact authorized request."""

    def open_session(
        self,
        request: AuthorizedAgentExecutionRequest,
    ) -> ModuleProviderToolSession:
        """Bind exact Module inputs and authorization to one provider Attempt."""


def validate_provider_tool_set(
    definitions: tuple[ProviderToolDefinition, ...],
    tool_policy: tuple[str, ...],
) -> tuple[str, ...]:
    """Validate exact set equality while preserving provider definition order."""

    for definition in definitions:
        if type(definition) is not ProviderToolDefinition:
            raise ValueError("tool session returned an invalid definition")
        definition.validate()
    declared_names = tuple(definition.tool_name for definition in definitions)
    if len(declared_names) != len(set(declared_names)):
        raise ValueError("Gateway tool session returned duplicate tool names")
    if set(declared_names) != set(tool_policy):
        raise PermissionError(
            "Gateway tool session differs from the selected Execution Profile"
        )
    return declared_names


__all__ = [
    "ModuleArtifactHost",
    "ModuleProviderToolSession",
    "ModuleProviderToolSessionFactory",
    "ProviderToolDefinition",
    "runtime_package_version",
    "validate_provider_tool_set",
]
