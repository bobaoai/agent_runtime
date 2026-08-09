"""Shadow/test execution path for independently registered Runtime Modules.

The service proves the new Module Run, Execution Variant, and Attempt lineage
without reusing predecessor Module records.  Production and protected-operation
execution intentionally fail closed until the AR09 authorization coordinator
and target provider adapters implement the same target-model DTOs.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from ..contracts.registry_contract_validation import validate_id
from ..contracts.execution_module_definition import (
    ModuleExecutionLedger,
    ModuleExecutionRequest,
    ModuleExecutor,
    ModuleExecutorFailure,
    ModuleExecutorRequest,
    ModuleExecutorResult,
    ModuleFailureDetailBinding,
    ModuleInputBinding,
    ModuleOutputBinding,
    ModuleRunResult,
    ModuleVariantRequest,
)
from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
)
from ..contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleAttemptStartedRecord,
    ModuleExecutionVariantRecord,
    ModuleOutputResolutionRecord,
    ModuleRunRecord,
    ModuleUsageObservation,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry


def _canonical_sha256(payload: Mapping[str, Any] | list[Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _runtime_failure_class(exc: Exception) -> str:
    """Normalize Python exceptions into bounded Runtime failure taxonomy."""

    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    if isinstance(exc, ValueError):
        return "validation_error"
    if isinstance(exc, TypeError):
        return "type_error"
    if isinstance(exc, RuntimeError):
        return "runtime_error"
    return "executor_failure"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


class ModuleExecutorRegistry:
    """Duplicate-safe registry keyed by exact adapter ID."""

    def __init__(self) -> None:
        self._executors: dict[str, ModuleExecutor] = {}

    def register(self, executor_adapter_id: str, executor: ModuleExecutor) -> None:
        """Register one exact adapter ID and reject duplicate ownership."""

        validate_id("executor_adapter_id", executor_adapter_id)
        if executor_adapter_id in self._executors:
            raise ValueError(f"Executor already registered: {executor_adapter_id}")
        if not callable(getattr(executor, "execute", None)):
            raise ValueError("Executor must implement execute(request)")
        self._executors[executor_adapter_id] = executor

    def resolve(self, executor_adapter_id: str) -> ModuleExecutor:
        """Resolve a previously registered Executor by exact adapter ID."""

        try:
            return self._executors[executor_adapter_id]
        except KeyError as exc:
            raise KeyError(f"unknown Module Executor: {executor_adapter_id}") from exc


def run_module(
    request: ModuleExecutionRequest,
    *,
    release_registry: RuntimeReleaseRegistry,
    executors: ModuleExecutorRegistry,
    ledger: ModuleExecutionLedger,
    clock: Callable[[], str] = _utc_now,
) -> ModuleRunResult:
    """Run one registered Module through the additive shadow/test execution path.

    The first implementation accepts only isolated ``test`` and ``evaluation``
    purposes, operation-free Modules, and ``in_process_test`` profiles.  Every
    production or protected path fails before Executor invocation.
    """

    if type(request) is not ModuleExecutionRequest:
        raise ValueError("request must be an exact ModuleExecutionRequest")
    request.validate()
    existing = ledger.existing_result(request)
    if existing is not None:
        return existing
    if request.purpose not in {
        ModuleExecutionPurpose.TEST,
        ModuleExecutionPurpose.EVALUATION,
    }:
        raise NotImplementedError(
            "production Module execution awaits AR09 target authorization admission"
        )

    module = release_registry.get_module(
        request.module_release_ref,
        request.module_release_sha256,
    )
    release_registry.assert_module_execution_allowed(module, request.purpose)
    _assert_module_dependencies_shadow_executable(release_registry, module)
    if module.declared_operation_ids:
        raise NotImplementedError(
            "protected Module operations await AR09 request and grant binding"
        )
    if (
        module.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE
        and len(request.variants) != 1
    ):
        raise ValueError("direct_single Module Run requires exactly one Variant")

    started_at = clock()
    module_run_id = _stable_id("module_run", request.request_id, request.request_sha256)
    module_run = ModuleRunRecord(
        module_run_id=module_run_id,
        request_id=request.request_id,
        request_sha256=request.request_sha256,
        purpose=request.purpose,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        input_package_ref=request.input_package_ref,
        input_package_sha256=request.input_package_sha256,
        input_closure_sha256=request.input_closure_sha256,
        isolated_scope_ref=request.isolated_scope_ref,
        isolated_scope_sha256=request.isolated_scope_sha256,
        recorded_at_utc=started_at,
    )

    resolved_profiles: list[ExecutionProfileRelease] = []
    resolved_executors: list[ModuleExecutor] = []
    variant_records: list[ModuleExecutionVariantRecord] = []
    attempt_starts: list[ModuleAttemptStartedRecord] = []
    for variant_request in request.variants:
        profile = release_registry.get_execution_profile(
            variant_request.execution_profile_ref,
            variant_request.execution_profile_sha256,
        )
        _assert_profile_shadow_executable(release_registry, profile)
        if profile.transport_kind != "in_process_test":
            raise NotImplementedError(
                "first Module slice admits only in_process_test Executor profiles"
            )
        if profile.transport_kind not in module.compatible_transport_kinds:
            raise ValueError("Execution Profile transport is incompatible with Module")
        executor = executors.resolve(profile.executor_adapter_id)
        if module.prompt_bundle_ref is not None and (
            variant_request.prompt_envelope_ref is None
        ):
            raise ValueError("Agent Module Variant requires a Cell-local Prompt Envelope")
        variant_id = _stable_id(
            "module_variant",
            module_run_id,
            variant_request.arm_key,
            str(variant_request.replicate_index),
            profile.release_sha256,
            variant_request.prompt_envelope_sha256 or "none",
        )
        attempt_id = _stable_id("module_attempt", variant_id, "1")
        resolved_profiles.append(profile)
        resolved_executors.append(executor)
        variant_records.append(
            ModuleExecutionVariantRecord(
                module_run_id=module_run_id,
                variant_id=variant_id,
                arm_key=variant_request.arm_key,
                replicate_index=variant_request.replicate_index,
                execution_profile_ref=profile.release_ref,
                execution_profile_sha256=profile.release_sha256,
                prompt_envelope_ref=variant_request.prompt_envelope_ref,
                prompt_envelope_sha256=variant_request.prompt_envelope_sha256,
                input_closure_sha256=request.input_closure_sha256,
                recorded_at_utc=started_at,
            )
        )
        attempt_starts.append(
            ModuleAttemptStartedRecord(
                module_run_id=module_run_id,
                variant_id=variant_id,
                attempt_id=attempt_id,
                attempt_ordinal=1,
                recorded_at_utc=started_at,
            )
        )

    concurrent_result = ledger.begin(
        request,
        module_run,
        tuple(variant_records),
        tuple(attempt_starts),
    )
    if concurrent_result is not None:
        return concurrent_result

    attempts: list[ModuleAttemptRecord] = []
    outputs: list[ModuleOutputBinding] = []
    for variant_request, profile, executor, variant, attempt_start in zip(
        request.variants,
        resolved_profiles,
        resolved_executors,
        variant_records,
        attempt_starts,
        strict=True,
    ):
        try:
            executor_result = executor.execute(
                ModuleExecutorRequest(
                    module_run_id=module_run_id,
                    variant_id=variant.variant_id,
                    attempt_id=attempt_start.attempt_id,
                    module=module,
                    execution_profile=profile,
                    input_package_ref=request.input_package_ref,
                    input_package_sha256=request.input_package_sha256,
                    inputs=request.inputs,
                    prompt_envelope_ref=variant_request.prompt_envelope_ref,
                    prompt_envelope_sha256=variant_request.prompt_envelope_sha256,
                    isolated_scope_ref=request.isolated_scope_ref,
                    isolated_scope_sha256=request.isolated_scope_sha256,
                )
            )
            if type(executor_result) is not ModuleExecutorResult:
                raise TypeError("Module Executor returned an invalid result type")
            executor_result.validate()
            for output in executor_result.outputs:
                if (
                    output.schema_ref != module.output_schema_ref
                    or output.schema_sha256 != module.output_schema_sha256
                ):
                    raise ValueError("Module Executor output schema mismatch")
            ended_at = clock()
            attempt = ModuleAttemptRecord(
                module_run_id=module_run_id,
                variant_id=variant.variant_id,
                attempt_id=attempt_start.attempt_id,
                status="completed",
                output_refs=tuple(output.output_ref for output in executor_result.outputs),
                usage=executor_result.usage,
                failure_class=None,
                period_start_at_utc=attempt_start.recorded_at_utc,
                period_end_at_utc=ended_at,
                recorded_at_utc=ended_at,
                tool_calls=executor_result.tool_calls,
                prompt_envelope_ref=variant.prompt_envelope_ref,
                prompt_envelope_sha256=variant.prompt_envelope_sha256,
            )
            attempts.append(attempt)
            outputs.extend(executor_result.outputs)
            ledger.commit_attempt(attempt)
        except Exception as exc:
            ended_at = clock()
            executor_failure = (
                exc if isinstance(exc, ModuleExecutorFailure) else None
            )
            detail = (
                executor_failure.detail
                if executor_failure is not None
                else None
            )
            attempt = ModuleAttemptRecord(
                module_run_id=module_run_id,
                variant_id=variant.variant_id,
                attempt_id=attempt_start.attempt_id,
                status="failed",
                output_refs=(),
                usage=(
                    executor_failure.usage
                    if executor_failure is not None
                    else ModuleUsageObservation(
                        input_tokens=None,
                        output_tokens=None,
                        cache_read_tokens=None,
                        cache_creation_tokens=None,
                    )
                ),
                failure_class=(
                    executor_failure.failure_class
                    if executor_failure is not None
                    else _runtime_failure_class(exc)
                ),
                period_start_at_utc=attempt_start.recorded_at_utc,
                period_end_at_utc=ended_at,
                recorded_at_utc=ended_at,
                tool_calls=(
                    executor_failure.tool_calls
                    if executor_failure is not None
                    else ()
                ),
                prompt_envelope_ref=variant.prompt_envelope_ref,
                prompt_envelope_sha256=variant.prompt_envelope_sha256,
                failure_detail_ref=(
                    detail.detail_ref if detail is not None else None
                ),
                failure_detail_sha256=(
                    detail.detail_sha256 if detail is not None else None
                ),
            )
            attempts.append(attempt)
            ledger.commit_attempt(attempt)

    resolution = _resolve_shadow_outputs(
        module,
        module_run_id,
        tuple(variant_records),
        tuple(attempts),
        tuple(outputs),
        clock(),
    )
    result = ModuleRunResult(
        module_run=module_run,
        variants=tuple(variant_records),
        attempts=tuple(attempts),
        outputs=tuple(outputs),
        resolution=resolution,
    )
    ledger.commit_result(request.request_id, result)
    return result


def _assert_profile_shadow_executable(
    release_registry: RuntimeReleaseRegistry,
    profile: ExecutionProfileRelease,
) -> None:
    state = release_registry.get_admission_state(
        ReleaseSubjectKind.EXECUTION_PROFILE,
        profile.release_ref,
    )
    if state not in {
        ReleaseAdmissionState.CANDIDATE,
        ReleaseAdmissionState.SHADOW_EXECUTABLE,
        ReleaseAdmissionState.PRODUCTION_CANARY,
        ReleaseAdmissionState.ACTIVE,
    }:
        raise PermissionError(f"Execution Profile is not shadow-executable: {state.value}")


def _assert_module_dependencies_shadow_executable(
    release_registry: RuntimeReleaseRegistry,
    module: RuntimeModuleRelease,
) -> None:
    dependencies: list[tuple[ReleaseSubjectKind, str]] = []
    if module.source_skill_package_ref is not None:
        if module.source_skill_package_sha256 is None:
            raise ValueError("Module source Skill Package hash is missing")
        release_registry.get_skill_package(
            module.source_skill_package_ref,
            module.source_skill_package_sha256,
        )
        dependencies.append(
            (ReleaseSubjectKind.SKILL_PACKAGE, module.source_skill_package_ref)
        )
    if module.prompt_bundle_ref is not None:
        if module.prompt_bundle_sha256 is None:
            raise ValueError("Module Prompt Bundle hash is missing")
        prompt_bundle = release_registry.get_prompt_bundle(
            module.prompt_bundle_ref,
            module.prompt_bundle_sha256,
        )
        dependencies.append(
            (ReleaseSubjectKind.PROMPT_BUNDLE, module.prompt_bundle_ref)
        )
        dependencies.extend(
            (
                ReleaseSubjectKind.PROMPT_COMPONENT,
                member.member_ref,
            )
            for member in prompt_bundle.members
            if member.member_ref.startswith("prompt-component:")
        )
    allowed = {
        ReleaseAdmissionState.CANDIDATE,
        ReleaseAdmissionState.SHADOW_EXECUTABLE,
        ReleaseAdmissionState.PRODUCTION_CANARY,
        ReleaseAdmissionState.ACTIVE,
    }
    for kind, release_ref in dependencies:
        state = release_registry.get_admission_state(kind, release_ref)
        if state not in allowed:
            raise PermissionError(
                f"{kind.value} dependency is not shadow-executable: {state.value}"
            )


def _resolve_shadow_outputs(
    module: RuntimeModuleRelease,
    module_run_id: str,
    variants: tuple[ModuleExecutionVariantRecord, ...],
    attempts: tuple[ModuleAttemptRecord, ...],
    outputs: tuple[ModuleOutputBinding, ...],
    recorded_at_utc: str,
) -> ModuleOutputResolutionRecord | None:
    successful = tuple(attempt for attempt in attempts if attempt.status == "completed")
    if len(successful) != len(attempts):
        return None
    if module.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE:
        candidate_ref = f"attempt-output-bundle:{successful[0].attempt_id}"
        candidate_sha256 = _canonical_sha256(
            [output.as_dict() for output in outputs]
        )
        return ModuleOutputResolutionRecord.build(
            module_output_resolution_id=_stable_id(
                "module_resolution", module_run_id, "resolved"
            ),
            workflow_execution_id=None,
            source_module_run_id=module_run_id,
            resolution_mode=module.output_resolution_policy.value,
            candidate_output_bundle_refs=(candidate_ref,),
            candidate_output_bundle_sha256s=(candidate_sha256,),
            evaluation_set_ref=None,
            selection_ref=None,
            resolved_execution_output_refs=tuple(
                output.output_ref for output in outputs
            ),
            resolution_status="resolved",
            recorded_at_utc=recorded_at_utc,
        )
    return None


__all__ = [
    "ModuleExecutionRequest",
    "ModuleExecutor",
    "ModuleExecutorFailure",
    "ModuleExecutorRegistry",
    "ModuleExecutorRequest",
    "ModuleExecutorResult",
    "ModuleFailureDetailBinding",
    "ModuleInputBinding",
    "ModuleOutputBinding",
    "ModuleRunResult",
    "ModuleVariantRequest",
    "run_module",
]
