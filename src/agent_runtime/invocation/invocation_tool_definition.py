"""Provider-adapter ports shared by SDK, API, and CLI Executors."""

from __future__ import annotations

from dataclasses import dataclass
import functools
import json
import inspect
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
    def request(self) -> AuthorizedAgentExecutionRequest:
        """The exact request passed to this session's factory."""

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
        authorization: AuthorizedOperationReceipt | None,
    ) -> Mapping[str, Any]:
        """Execute under a real receipt or this session's bound self-test resources.

        None is not a permission. Only Runtime's live self-test dispatcher may
        use it; an external-operation session must reject None.
        """

    def validate_completion(self) -> None:
        """Check resource/record integrity; business tool usage is task-owned."""

    @property
    def observations(self) -> tuple[ModuleToolCallObservation, ...]:
        """Return Runtime-authored request/response lineage for completed calls."""

    def close(self) -> None:
        """Stop and join owned work, including any in-flight child invocation."""


class ModuleProviderToolSessionFactory(Protocol):
    """Create one isolated tool session from an exact authorized request."""

    @property
    def definitions(self) -> tuple[ProviderToolDefinition, ...]:
        """Return fixed definitions before constructing the exact request."""

    def open_session(
        self,
        request: AuthorizedAgentExecutionRequest,
    ) -> ModuleProviderToolSession:
        """Bind exact Module inputs and authorization to one provider Attempt."""


def freeze_tool_definitions(definitions) -> tuple[ProviderToolDefinition, ...]:
    """Copy and validate the complete tool table without retaining mutable schemas."""
    if type(definitions) is not tuple:
        raise ValueError("tool definitions must be an immutable tuple")
    records = []
    seen = set()
    for definition in definitions:
        if type(definition) is not ProviderToolDefinition:
            raise ValueError("invalid provider tool definition")
        definition.validate()
        if definition.tool_name in seen:
            raise ValueError("duplicate provider tool name")
        seen.add(definition.tool_name)
        records.append(ProviderToolDefinition(definition.tool_name, definition.description,
            json.loads(json.dumps(dict(definition.input_schema), allow_nan=False))))
    return tuple(records)


def tool_definition_records(definitions) -> list[dict]:
    """Canonical model-visible fields shared by prompt, resource binding and MCP."""
    return [{"name": item.tool_name, "description": item.description, "inputSchema": dict(item.input_schema)}
            for item in freeze_tool_definitions(definitions)]


def self_test_cancellation_callbacks(host, request) -> dict:
    """Resolve optional controls without weakening the required resource guard.

    Legacy hosts need only the existing validation/launch ports. A declared
    control must be callable and return actual booleans; getter/call failures
    propagate instead of being mistaken for an absent optional port.
    """
    if request.self_test_binding_ref is None:
        return {}
    try:
        inspect.getattr_static(host, "self_test_cancel_requested")
    except AttributeError:
        return {}
    query = host.self_test_cancel_requested
    if not callable(query):
        raise TypeError("optional self_test_cancel_requested must be callable")
    def check(user):
        result = query(request, user=user)
        if type(result) is not bool:
            raise TypeError("self_test_cancel_requested must return bool")
        return result
    return {"cancel_requested": lambda: check(False), "user_cancel_requested": lambda: check(True)}


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
