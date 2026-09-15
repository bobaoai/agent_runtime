"""Shared Runtime-owned result assembly for provider execution adapters.

Context-compatibility identity, canonical trace encoding, descriptor
composition, and the failed-result shape are cross-provider Runtime concepts.
Each provider adapter keeps only its transport invocation and failure
classification; assembling these records per adapter is how compatibility
hashes and ledger record shapes silently diverge between providers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Callable, Mapping, NoReturn

from ..contracts.invocation_adapter_definition import (
    AdapterContextResult,
    AgentExecutionAdapterDescriptor,
    AgentExecutionFailure,
    AgentExecutionResult,
    AuthorizedAgentExecutionRequest,
    OutputSubmission,
)
from ..contracts.ledger_lineage_definition import ModuleToolCallObservation
from ..contracts.registry_release_definition import ExecutionProfileRelease
from .invocation_failure_recording import build_provider_failure_detail
from .invocation_tool_definition import (
    ModuleArtifactHost,
    runtime_package_version,
)


TRACE_SECTION_MAX_BYTES = 64 * 1024


class TerminalAdapterFailure(Exception):
    """Internal control flow carrying one typed failed provider result."""

    def __init__(self, result: AgentExecutionResult, *, pending_failure_detail: bytes | None = None) -> None:
        super().__init__(result.failure.failure_class if result.failure else "")
        self.result = result
        self.pending_failure_detail = pending_failure_detail


def bounded_trace_text(text: str) -> str:
    """Truncate one trace section to an exact byte bound, like failure detail."""

    encoded = text.encode("utf-8")
    if len(encoded) <= TRACE_SECTION_MAX_BYTES:
        return text
    return (
        encoded[:TRACE_SECTION_MAX_BYTES].decode("utf-8", errors="ignore")
        + "\n[truncated]"
    )


def provider_adapter_descriptor(
    *,
    adapter_id: str,
    adapter_revision: str,
    provider_id: str,
    transport_family: str,
    transport_kind: str,
    execution_mode: str,
    input_delivery_mode: str,
    network_policy: str,
    supports_dynamic_operation_authorization: bool = False,
    admission_state: str = "integration_tested",
) -> AgentExecutionAdapterDescriptor:
    """Compose one validated descriptor from adapter-specific identity facts."""

    descriptor = AgentExecutionAdapterDescriptor(
        adapter_contract_version="v1",
        adapter_id=adapter_id,
        adapter_revision=adapter_revision,
        provider_id=provider_id,
        transport_family=transport_family,
        transport_kind=transport_kind,
        runtime_package_id="agent_runtime_core",
        runtime_package_version=runtime_package_version(),
        supported_context_modes=("stateless",),
        supported_output_constraint_modes=(
            "prompt_only_json",
            "native_structured_output",
        ),
        supported_read_isolation_modes=("entitled_refs",),
        supported_execution_modes=(execution_mode,),
        supported_input_delivery_modes=(input_delivery_mode,),
        supported_network_policies=(network_policy,),
        supports_dynamic_operation_authorization=(
            supports_dynamic_operation_authorization
        ),
        admission_state=admission_state,
    )
    descriptor.validate()
    return descriptor


def stateless_context_result(
    request: AuthorizedAgentExecutionRequest,
) -> AdapterContextResult:
    """Derive the stateless context disposition and its compatibility identity."""

    compatibility = hashlib.sha256(
        "\x1f".join(
            (
                request.module_release_sha256,
                request.execution_profile_sha256,
                request.prompt_envelope_sha256 or "none",
            )
        ).encode("utf-8")
    ).hexdigest()
    return AdapterContextResult(
        disposition_id="stateless_closed",
        context_ref=None,
        compatibility_sha256=compatibility,
    )


def commit_attempt_trace_json(
    artifact_host: ModuleArtifactHost,
    request: AuthorizedAgentExecutionRequest,
    trace: Mapping[str, Any],
) -> tuple[str, str]:
    """Commit one canonical bounded Cell-local trace for an Attempt."""

    return artifact_host.commit_attempt_trace(
        module_run_id=request.module_run_id,
        variant_id=request.variant_id,
        attempt_id=request.attempt_id,
        content=json.dumps(
            dict(trace),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        media_type="application/json",
    )


def completed_adapter_result(
    *,
    profile: ExecutionProfileRelease,
    request: AuthorizedAgentExecutionRequest,
    outputs: tuple[OutputSubmission, ...],
    tool_operation_ref_ids: tuple[str, ...],
    tool_observations: tuple[ModuleToolCallObservation, ...] = (),
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_creation_tokens: int | None,
    trace_ref: str,
    trace_sha256: str,
) -> AgentExecutionResult:
    """Assemble and validate one completed canonical adapter result."""

    completed = AgentExecutionResult(
        terminal_status="completed",
        provider_id=profile.provider_id,
        model_id=profile.model_id,
        runtime_version=runtime_package_version(),
        outputs=outputs,
        model_operation_ref_ids=(),
        tool_operation_ref_ids=tool_operation_ref_ids,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        estimated_cost_usd=None,
        provider_charge_usd=None,
        context=stateless_context_result(request),
        failure=None,
        cell_local_trace_ref=trace_ref,
        cell_local_trace_sha256=trace_sha256,
        tool_observations=tool_observations,
    )
    completed.validate()
    return completed


def finalize_adapter_result(*, artifact_host: ModuleArtifactHost,
                            request: AuthorizedAgentExecutionRequest, result: AgentExecutionResult,
                            pending_failure_detail: bytes | None, interruption_requested: Callable[[], bool],
                            cleanup_error: Exception | None, interruption_code: str,
                            cleanup_failure_code: str) -> AgentExecutionResult:
    """Deliver a trace-bound result after cleanup with the final failure facts.

    Use with raise_terminal_failure(defer_failure_detail=True). The original
    failure facts already belong to the immutable trace; this function decides
    the final status after cleanup and cancellation without replacing that trace.
    Both cleanup failure and cancellation deny retry and suppress output. A
    simultaneous cleanup failure remains explicit in the cancellation diagnostic.
    The interruption flag is monotonic. A signal during diagnostic commit adds
    a cancellation diagnostic without overwriting the preceding failure or trace;
    this bounded second pass never retries the model or resource cleanup.
    An unavailable cancellation control produces ADAPTER_BINDING_UNAVAILABLE
    while retaining the received trace and usage. It is neither proof of user
    cancellation nor a cleanup failure; successful outputs are withheld.
    """
    if result.failure is not None and result.failure.detail_ref is not None:
        raise ValueError("finalization requires a deferred failure detail")
    control_error = None
    observed_interruption = False
    def query_interruption():
        nonlocal control_error, observed_interruption
        if observed_interruption or control_error is not None:
            return observed_interruption
        try:
            observed_interruption = interruption_requested()
        except Exception as exc:
            control_error = exc
        return observed_interruption
    for _ in range(2):
        status, failure = result.terminal_status, result.failure
        content = pending_failure_detail
        interrupted = status == "cancelled" or query_interruption()
        control_failure = control_error is not None
        if control_failure:
            status = "failed"
            failure = AgentExecutionFailure(failure_class="dependency_unavailable",
                retry_disposition_id="retry_denied", failure_scope_id="attempt_only")
            diagnostic = f"{type(control_error).__name__}: {control_error}"
            if cleanup_error is not None:
                diagnostic += f"; cleanup error: {type(cleanup_error).__name__}: {cleanup_error}"
            content = build_provider_failure_detail(failure_class=failure.failure_class,
                failure_code="ADAPTER_BINDING_UNAVAILABLE",
                message="Adapter cancellation control failed; preceding execution facts remain in the provider trace",
                provider_response="", provider_error_message=diagnostic,
                transport_exit_code=None, retryable=False)
        elif interrupted or status == "cancelled" or cleanup_error is not None:
            cancelled = interrupted or status == "cancelled"
            status = "cancelled" if cancelled else "failed"
            failure = AgentExecutionFailure(failure_class="cancelled" if cancelled else "transport",
                retry_disposition_id="retry_denied", failure_scope_id="attempt_only")
            content = build_provider_failure_detail(failure_class=failure.failure_class,
                failure_code=interruption_code if cancelled else cleanup_failure_code,
                message=("Adapter interrupted" if cancelled else "Adapter resource cleanup failed")
                        + "; preceding execution facts remain in the provider trace",
                provider_response="", provider_error_message=None if cleanup_error is None else str(cleanup_error),
                transport_exit_code=None, retryable=False)
        finalized = result
        if failure is not None:
            if content is None:
                raise ValueError("failed result requires its actual diagnostic content")
            detail = artifact_host.commit_failure_detail(module_run_id=request.module_run_id,
                variant_id=request.variant_id, attempt_id=request.attempt_id, failure_class=failure.failure_class,
                content=content, media_type="application/json")
            finalized = replace(result, terminal_status=status, outputs=(), failure=replace(failure,
                detail_ref=detail.detail_ref, detail_sha256=detail.detail_sha256))
            finalized.validate()
        if status == "cancelled":
            return finalized
        if not query_interruption():
            if control_error is not None and not control_failure:
                continue  # A late control error must not return the earlier success.
            return finalized
    raise AssertionError("interruption flag must be monotonic")


def raise_terminal_failure(
    *,
    artifact_host: ModuleArtifactHost,
    request: AuthorizedAgentExecutionRequest,
    profile: ExecutionProfileRelease,
    failure_class: str,
    failure_code: str,
    message: str,
    provider_response: str,
    retry_disposition_id: str,
    trace: Mapping[str, Any],
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    tool_operation_ref_ids: tuple[str, ...] = (),
    tool_observations: tuple[ModuleToolCallObservation, ...] = (),
    transport_exit_code: int | None = None,
    cause: BaseException | None = None,
    terminal_status: str = "failed",
    defer_failure_detail: bool = False,
) -> NoReturn:
    """Commit trace and raise a typed result; optionally defer the final detail.

    Deferral is internal to an Adapter that calls finalize_adapter_result after
    resource cleanup. The provisional result must not be returned to the kernel.
    Other Adapters retain the original immediate diagnostic commit behavior.
    """

    if terminal_status not in {"failed", "cancelled"}:
        raise ValueError("terminal failure must be failed or cancelled")

    content = build_provider_failure_detail(
        failure_class=failure_class, failure_code=failure_code, message=message,
        provider_response=provider_response, provider_error_message=str(cause) if cause is not None else None,
        transport_exit_code=transport_exit_code, retryable=retry_disposition_id == "retry_allowed")
    detail = None if defer_failure_detail else artifact_host.commit_failure_detail(
        module_run_id=request.module_run_id,
        variant_id=request.variant_id,
        attempt_id=request.attempt_id,
        failure_class=failure_class,
        content=content,
        media_type="application/json",
    )
    trace_ref, trace_sha256 = commit_attempt_trace_json(
        artifact_host, request, trace
    )
    failed = AgentExecutionResult(
        terminal_status=terminal_status,
        provider_id=profile.provider_id,
        model_id=profile.model_id,
        runtime_version=runtime_package_version(),
        outputs=(),
        model_operation_ref_ids=(),
        tool_operation_ref_ids=tool_operation_ref_ids,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        estimated_cost_usd=None,
        provider_charge_usd=None,
        context=stateless_context_result(request),
        failure=AgentExecutionFailure(
            failure_class=failure_class,
            retry_disposition_id=retry_disposition_id,
            failure_scope_id="attempt_only",
            retry_after_seconds=None,
            detail_ref=detail.detail_ref if detail is not None else None,
            detail_sha256=detail.detail_sha256 if detail is not None else None,
        ),
        cell_local_trace_ref=trace_ref,
        cell_local_trace_sha256=trace_sha256,
        tool_observations=tool_observations,
    )
    failed.validate()
    raise TerminalAdapterFailure(failed, pending_failure_detail=content if defer_failure_detail else None)


__all__ = [
    "TRACE_SECTION_MAX_BYTES",
    "TerminalAdapterFailure",
    "bounded_trace_text",
    "commit_attempt_trace_json",
    "completed_adapter_result",
    "finalize_adapter_result",
    "provider_adapter_descriptor",
    "raise_terminal_failure",
    "stateless_context_result",
]
