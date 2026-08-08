"""Prepare one provider-neutral registered Module invocation context."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ..contracts.execution_module_definition import ModuleExecutorRequest
from ..contracts.registry_release_definition import ModuleKind
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_prompt_assembly import (
    validate_prompt_output_constraint,
    validate_registered_output_schema,
)
from .invocation_tool_definition import ModuleArtifactHost


@dataclass(frozen=True)
class InvocationExecutionExpectation:
    """Provider Adapter invariants fixed by one Executor implementation."""

    executor_adapter_id: str
    executor_adapter_revision: str
    transport_kind: str
    execution_mode: str
    semantic_input_delivery_mode: str
    attempt_workspace_policy: str
    network_policy: str
    tool_policy: tuple[str, ...] | None


@dataclass(frozen=True)
class PreparedInvocationContext:
    """Exact model-visible prompt and registered task-plane output schema."""

    prompt: str
    registered_output_schema: dict[str, object]


def prepare_registered_invocation_context(
    *,
    request: ModuleExecutorRequest,
    release_registry: RuntimeReleaseRegistry,
    artifact_host: ModuleArtifactHost,
    expectation: InvocationExecutionExpectation,
) -> PreparedInvocationContext:
    """Validate shared release closure and load one exact Prompt Envelope."""

    if type(request) is not ModuleExecutorRequest:
        raise ValueError("request must be an exact ModuleExecutorRequest")
    module = request.module
    profile = request.execution_profile
    module.validate()
    profile.validate()
    if module.module_kind is not ModuleKind.AGENT:
        raise ValueError("Agent Executor accepts only Agent Modules")
    if profile.executor_adapter_id != expectation.executor_adapter_id:
        raise ValueError("Execution Profile targets another Executor adapter")
    if profile.executor_adapter_revision != expectation.executor_adapter_revision:
        raise ValueError("Execution Profile targets another Executor revision")
    if profile.transport_kind != expectation.transport_kind:
        raise ValueError("Execution Profile transport differs from Executor transport")
    if profile.transport_kind not in module.compatible_transport_kinds:
        raise ValueError("Execution Profile transport is incompatible with Module")
    if profile.execution_mode != expectation.execution_mode:
        raise ValueError("Execution Profile execution mode differs from Executor mode")
    if (
        profile.semantic_input_delivery_mode
        != expectation.semantic_input_delivery_mode
    ):
        raise ValueError(
            "Execution Profile semantic input delivery differs from Executor mode"
        )
    if profile.attempt_workspace_policy != expectation.attempt_workspace_policy:
        raise ValueError("Execution Profile workspace policy differs from Executor mode")
    if profile.network_policy != expectation.network_policy:
        raise ValueError("Execution Profile network policy differs from Executor mode")
    if expectation.tool_policy is not None and (
        profile.tool_policy != expectation.tool_policy
    ):
        raise ValueError("Execution Profile tool policy differs from Executor mode")
    if module.prompt_bundle_ref is None or module.prompt_bundle_sha256 is None:
        raise ValueError("Agent Module lacks an exact Prompt Bundle")
    prompt_bundle = release_registry.get_prompt_bundle(
        module.prompt_bundle_ref,
        module.prompt_bundle_sha256,
    )
    output_schema_asset = release_registry.get_schema_asset(
        module.output_schema_ref,
        module.output_schema_sha256,
    )
    registered_output_schema = validate_registered_output_schema(
        compiled_static_body=prompt_bundle.compiled_static_body,
        canonical_schema=output_schema_asset.schema_document(),
    )
    if request.prompt_envelope_ref is None:
        raise ValueError("Agent execution requires a final Prompt Envelope")
    if request.prompt_envelope_sha256 is None:
        raise ValueError("Prompt Envelope hash is missing")
    prompt_bytes = artifact_host.read_bytes(
        request.prompt_envelope_ref,
        request.prompt_envelope_sha256,
    )
    if hashlib.sha256(prompt_bytes).hexdigest() != request.prompt_envelope_sha256:
        raise ValueError("Prompt Envelope content hash mismatch")
    try:
        prompt = prompt_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Prompt Envelope must be exact UTF-8 provider text") from exc
    validate_prompt_output_constraint(
        prompt=prompt,
        output_constraint_mode=profile.output_constraint_mode,
    )
    return PreparedInvocationContext(
        prompt=prompt,
        registered_output_schema=registered_output_schema,
    )


__all__ = [
    "InvocationExecutionExpectation",
    "PreparedInvocationContext",
    "prepare_registered_invocation_context",
]
