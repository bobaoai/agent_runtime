"""Runtime-owned execution-context binding, revalidation, and fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from threading import RLock

from ..contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextBinding,
    ExecutionAuthorizationContextEnvelope,
    ExecutionAuthorizationContextState,
    ExecutionAuthorizationFence,
    ExecutionAuthorizationFenceState,
    GatewayAuthorizationObservation,
    GatewayDecisionEffect,
    ProductAuthorizationContextStatus,
    ProtectedOperationIntent,
)
from ..contracts.registry_release_definition import RuntimeModuleRelease
from ..foundation.foundation_contract_validation import validate_utc_timestamp
from ..registry.registry_release_retrieval import RuntimeModuleReleaseClient
from .execution_authorization_resolution import ProductAuthorizationContextClient
from .execution_operation_resolution import RuntimeProtectedOperationClient


def _as_datetime(value: str) -> datetime:
    validate_utc_timestamp("observed_at_utc", value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class ExecutionAuthorizationAdmissionResult:
    """Binding and initial open fence admitted before workflow start."""

    binding: ExecutionAuthorizationContextBinding
    fence: ExecutionAuthorizationFence


class InMemoryExecutionAuthorizationLedger:
    """Duplicate-safe Runtime ledger for portable tests and host integration."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._bindings_by_ref: dict[str, ExecutionAuthorizationContextBinding] = {}
        self._binding_ref_by_execution: dict[str, str] = {}
        self._fences_by_ref: dict[str, ExecutionAuthorizationFence] = {}
        self._current_fence_ref_by_binding: dict[str, str] = {}
        self._intents_by_ref: dict[str, ProtectedOperationIntent] = {}
        self._intent_ref_by_idempotency: dict[tuple[str, str], str] = {}
        self._observations_by_intent: dict[str, GatewayAuthorizationObservation] = {}

    def commit_binding(
        self,
        binding: ExecutionAuthorizationContextBinding,
    ) -> ExecutionAuthorizationContextBinding:
        """Commit one duplicate-safe authorization binding per execution."""

        binding.validate()
        with self._lock:
            existing_ref = self._binding_ref_by_execution.get(
                binding.workflow_execution_id
            )
            if existing_ref is not None:
                existing = self._bindings_by_ref[existing_ref]
                if existing != binding:
                    raise ValueError(
                        "Workflow Execution authorization binding conflict"
                    )
                return existing
            if binding.binding_ref in self._bindings_by_ref:
                raise ValueError("execution authorization binding ref conflict")
            self._bindings_by_ref[binding.binding_ref] = binding
            self._binding_ref_by_execution[binding.workflow_execution_id] = (
                binding.binding_ref
            )
            return binding

    def binding(self, binding_ref: str) -> ExecutionAuthorizationContextBinding:
        """Resolve one committed execution authorization binding."""

        with self._lock:
            try:
                return self._bindings_by_ref[binding_ref]
            except KeyError as exc:
                raise KeyError(
                    f"unknown execution authorization binding: {binding_ref}"
                ) from exc

    def commit_fence(
        self,
        fence: ExecutionAuthorizationFence,
    ) -> ExecutionAuthorizationFence:
        """Commit a fence without reopening a previously fenced binding."""

        fence.validate()
        with self._lock:
            current_ref = self._current_fence_ref_by_binding.get(fence.binding_ref)
            if current_ref is not None:
                current = self._fences_by_ref[current_ref]
                if (
                    current.state is ExecutionAuthorizationFenceState.FENCED
                    and fence.state is ExecutionAuthorizationFenceState.OPEN
                ):
                    return current
            existing = self._fences_by_ref.get(fence.fence_ref)
            if existing is not None and existing != fence:
                raise ValueError("execution authorization fence ref conflict")
            self._fences_by_ref[fence.fence_ref] = fence
            self._current_fence_ref_by_binding[fence.binding_ref] = fence.fence_ref
            return fence

    def current_fence(self, binding_ref: str) -> ExecutionAuthorizationFence:
        """Return the current monotonic fence for an authorization binding."""

        with self._lock:
            try:
                return self._fences_by_ref[
                    self._current_fence_ref_by_binding[binding_ref]
                ]
            except KeyError as exc:
                raise KeyError(
                    f"unknown execution authorization fence: {binding_ref}"
                ) from exc

    def commit_intent(self, intent: ProtectedOperationIntent) -> ProtectedOperationIntent:
        """Commit one protected-operation intent by idempotency identity."""

        intent.validate()
        key = (intent.binding_ref, intent.idempotency_key)
        with self._lock:
            existing_ref = self._intent_ref_by_idempotency.get(key)
            if existing_ref is not None:
                existing = self._intents_by_ref[existing_ref]
                if existing != intent:
                    raise ValueError("protected operation idempotency conflict")
                return existing
            self._intents_by_ref[intent.intent_ref] = intent
            self._intent_ref_by_idempotency[key] = intent.intent_ref
            return intent

    def intent(self, intent_ref: str) -> ProtectedOperationIntent:
        """Resolve one committed protected-operation intent."""

        with self._lock:
            try:
                return self._intents_by_ref[intent_ref]
            except KeyError as exc:
                raise KeyError(
                    f"unknown protected operation intent: {intent_ref}"
                ) from exc

    def intents_for_execution(
        self,
        workflow_execution_id: str,
    ) -> tuple[ProtectedOperationIntent, ...]:
        """Return committed intents for one execution in commit order."""

        with self._lock:
            return tuple(
                intent
                for intent in self._intents_by_ref.values()
                if intent.workflow_execution_id == workflow_execution_id
            )

    def observations_for_execution(
        self,
        workflow_execution_id: str,
    ) -> tuple[GatewayAuthorizationObservation, ...]:
        """Return terminal Gateway observations for one execution's intents."""

        with self._lock:
            return tuple(
                self._observations_by_intent[intent.intent_ref]
                for intent in self.intents_for_execution(workflow_execution_id)
                if intent.intent_ref in self._observations_by_intent
            )

    def resolve_protected_operation_intent(
        self,
        intent_ref: str,
        intent_sha256: str,
    ) -> ProtectedOperationIntent:
        """Resolve one committed intent through the public Runtime client seam."""

        intent = self.intent(intent_ref)
        if intent.intent_sha256 != intent_sha256:
            raise PermissionError("protected operation intent hash mismatch")
        return intent

    def resolve_execution_authorization_binding(
        self,
        binding_ref: str,
        binding_sha256: str,
    ) -> ExecutionAuthorizationContextBinding:
        """Resolve one execution binding through the public Runtime client seam."""

        binding = self.binding(binding_ref)
        if binding.binding_sha256 != binding_sha256:
            raise PermissionError("execution authorization binding hash mismatch")
        return binding

    def commit_observation(
        self,
        observation: GatewayAuthorizationObservation,
    ) -> GatewayAuthorizationObservation:
        """Commit one terminal Gateway observation per protected intent."""

        observation.validate()
        with self._lock:
            existing = self._observations_by_intent.get(observation.intent_ref)
            if existing is not None:
                if existing != observation:
                    raise ValueError("Gateway authorization observation conflict")
                return existing
            self._observations_by_intent[observation.intent_ref] = observation
            return observation

    def commit_under_current_fence(self, binding_ref, operation):
        """Run one finalization atomically against the committed fence.

        The fence lock is held for the whole operation, so no fence transition
        can interleave between the compare and the commit.
        """

        with self._lock:
            return operation(self.current_fence(binding_ref))


class ExecutionAuthorizationController:
    """Coordinate Product status observations with Runtime-owned ordering."""

    def __init__(
        self,
        *,
        client: ProductAuthorizationContextClient,
        ledger: InMemoryExecutionAuthorizationLedger,
        module_release_client: RuntimeModuleReleaseClient | None = None,
    ) -> None:
        self._client = client
        self._ledger = ledger
        self._module_release_client = module_release_client

    @property
    def protected_operation_client(self) -> RuntimeProtectedOperationClient:
        """Expose the read-only authority-resolution port to enforcing Gateways."""

        return self._ledger

    @property
    def product_authorization_client(self) -> ProductAuthorizationContextClient:
        """Expose the host-supplied opaque Product Authorization client port."""

        return self._client

    def bind_execution_context(
        self,
        *,
        envelope: ExecutionAuthorizationContextEnvelope,
        expected_workflow_execution_id: str,
        expected_workflow_release_id: str,
        expected_principal_id: str,
        expected_actor_workload_id: str,
        expected_tenant_id: str,
        expected_cell_id: str,
        execution_input_package_ref: str,
        execution_input_package_sha256: str,
        observed_at_utc: str,
    ) -> ExecutionAuthorizationAdmissionResult:
        """Validate and bind one exact context before durable execution starts."""

        envelope.validate()
        expected = (
            expected_workflow_execution_id,
            expected_workflow_release_id,
            expected_principal_id,
            expected_actor_workload_id,
            expected_tenant_id,
            expected_cell_id,
        )
        actual = (
            envelope.workflow_execution_id,
            envelope.workflow_release_id,
            envelope.principal_id,
            envelope.actor_workload_id,
            envelope.tenant_id,
            envelope.cell_id,
        )
        if actual != expected:
            raise PermissionError("execution authorization context closure mismatch")
        observed = _as_datetime(observed_at_utc)
        if not (
            _as_datetime(envelope.effective_at_utc)
            <= observed
            < _as_datetime(envelope.expiry_at_utc)
        ):
            raise PermissionError("execution authorization context is outside validity")
        status = self._observe_status(envelope, observed_at_utc)
        if status.state is not ExecutionAuthorizationContextState.EFFECTIVE:
            raise PermissionError(status.reason_code)

        binding_id = _stable_id(
            "execution_authorization_binding",
            envelope.workflow_execution_id,
            envelope.context_sha256,
            execution_input_package_sha256,
        )
        binding = ExecutionAuthorizationContextBinding.build(
            binding_id=binding_id,
            binding_ref=f"runtime-authorization:{binding_id}",
            workflow_execution_id=envelope.workflow_execution_id,
            workflow_release_id=envelope.workflow_release_id,
            execution_input_package_ref=execution_input_package_ref,
            execution_input_package_sha256=execution_input_package_sha256,
            context_id=envelope.context_id,
            context_ref=envelope.context_ref,
            context_sha256=envelope.context_sha256,
            principal_id=envelope.principal_id,
            actor_workload_id=envelope.actor_workload_id,
            tenant_id=envelope.tenant_id,
            cell_id=envelope.cell_id,
            effective_at_utc=envelope.effective_at_utc,
            expiry_at_utc=envelope.expiry_at_utc,
            recorded_at_utc=observed_at_utc,
        )
        binding = self._ledger.commit_binding(binding)
        fence = self._commit_fence(
            binding,
            status,
            ExecutionAuthorizationFenceState.OPEN,
            "context_effective",
        )
        return ExecutionAuthorizationAdmissionResult(binding=binding, fence=fence)

    def revalidate(
        self,
        *,
        binding_ref: str,
        observed_at_utc: str,
    ) -> ExecutionAuthorizationFence:
        """Revalidate at re-entry or dispatch and monotonically close the fence."""

        binding = self._ledger.binding(binding_ref)
        current = self._ledger.current_fence(binding_ref)
        if current.state is ExecutionAuthorizationFenceState.FENCED:
            return current
        status = self._client.validate_execution_context(
            binding.context_id,
            binding.tenant_id,
            observed_at_utc,
        )
        if type(status) is not ProductAuthorizationContextStatus:
            raise TypeError("Product Authorization client returned an invalid status")
        status.validate()
        closure_matches = (
            status.context_id == binding.context_id
            and status.context_ref == binding.context_ref
            and status.context_sha256 == binding.context_sha256
            and status.observed_at_utc == observed_at_utc
        )
        observed = _as_datetime(observed_at_utc)
        effective = (
            closure_matches
            and status.state is ExecutionAuthorizationContextState.EFFECTIVE
            and _as_datetime(binding.effective_at_utc)
            <= observed
            < _as_datetime(binding.expiry_at_utc)
        )
        if effective:
            return self._commit_fence(
                binding,
                status,
                ExecutionAuthorizationFenceState.OPEN,
                "context_effective",
            )
        reason = status.reason_code if closure_matches else "context_status_mismatch"
        return self._commit_fence(
            binding,
            status,
            ExecutionAuthorizationFenceState.FENCED,
            reason,
        )

    def commit_protected_operation_intent(
        self,
        *,
        binding_ref: str,
        module_run_id: str,
        module_release_ref: str,
        module_release_sha256: str,
        operation_id: str,
        resource_ref: str,
        enforcing_gateway_id: str,
        idempotency_key: str,
        requires_grant: bool,
        operation_grant_ref: str | None,
        observed_at_utc: str,
    ) -> ProtectedOperationIntent:
        """Commit intent after revalidation and before calling the Gateway."""

        if self._module_release_client is None:
            raise RuntimeError(
                "protected operation requires a registered Module Release client"
            )
        module_release = (
            self._module_release_client.resolve_registered_module_release(
                module_release_ref,
                module_release_sha256,
            )
        )
        if type(module_release) is not RuntimeModuleRelease:
            raise TypeError("Module Release client returned an invalid release")
        module_release.validate()
        if (
            module_release.release_ref != module_release_ref
            or module_release.release_sha256 != module_release_sha256
        ):
            raise PermissionError("registered Module Release resolution mismatch")
        binding = self._ledger.binding(binding_ref)
        fence = self.revalidate(
            binding_ref=binding_ref,
            observed_at_utc=observed_at_utc,
        )
        if fence.state is not ExecutionAuthorizationFenceState.OPEN:
            raise PermissionError("execution authorization fence is closed")
        if operation_id not in module_release.declared_operation_ids:
            raise PermissionError("operation is absent from Module Release declaration")
        intent_id = _stable_id(
            "protected_operation_intent",
            binding.binding_sha256,
            module_run_id,
            operation_id,
            idempotency_key,
        )
        intent = ProtectedOperationIntent.build(
            intent_id=intent_id,
            intent_ref=f"runtime-authorization:{intent_id}",
            binding_ref=binding.binding_ref,
            binding_sha256=binding.binding_sha256,
            workflow_execution_id=binding.workflow_execution_id,
            module_run_id=module_run_id,
            module_release_ref=module_release.release_ref,
            module_release_sha256=module_release.release_sha256,
            operation_id=operation_id,
            resource_ref=resource_ref,
            enforcing_gateway_id=enforcing_gateway_id,
            idempotency_key=idempotency_key,
            requires_grant=requires_grant,
            operation_grant_ref=operation_grant_ref,
            recorded_at_utc=observed_at_utc,
        )
        return self._ledger.commit_intent(intent)

    def finalize_under_current_fence(self, binding_ref, operation):
        """Run one commit operation atomically against the committed fence."""

        return self._ledger.commit_under_current_fence(binding_ref, operation)

    def record_gateway_observation(
        self,
        *,
        intent_ref: str,
        decision_ref: str,
        decision_sha256: str,
        effect: GatewayDecisionEffect,
        effect_evidence_ref: str | None,
        grant_disposition_ref: str | None,
        observed_at_utc: str,
    ) -> GatewayAuthorizationObservation:
        """Record terminal Gateway refs without treating them as Runtime authority."""

        intent = self._ledger.intent(intent_ref)
        if (
            effect is GatewayDecisionEffect.ALLOW
            and intent.requires_grant
            and grant_disposition_ref is None
        ):
            raise ValueError("allowed grant-required operation needs disposition ref")
        if not intent.requires_grant and grant_disposition_ref is not None:
            raise ValueError("standard operation cannot record grant disposition")
        observation_id = _stable_id(
            "gateway_authorization_observation",
            intent.intent_sha256,
            decision_sha256,
        )
        observation = GatewayAuthorizationObservation.build(
            observation_id=observation_id,
            observation_ref=f"runtime-authorization:{observation_id}",
            intent_ref=intent.intent_ref,
            intent_sha256=intent.intent_sha256,
            decision_ref=decision_ref,
            decision_sha256=decision_sha256,
            effect=effect,
            effect_evidence_ref=effect_evidence_ref,
            grant_disposition_ref=grant_disposition_ref,
            recorded_at_utc=observed_at_utc,
        )
        return self._ledger.commit_observation(observation)

    def _observe_status(
        self,
        envelope: ExecutionAuthorizationContextEnvelope,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        status = self._client.validate_execution_context(
            envelope.context_id,
            envelope.tenant_id,
            observed_at_utc,
        )
        if type(status) is not ProductAuthorizationContextStatus:
            raise TypeError("Product Authorization client returned an invalid status")
        status.validate()
        if (
            status.context_id != envelope.context_id
            or status.context_ref != envelope.context_ref
            or status.context_sha256 != envelope.context_sha256
            or status.observed_at_utc != observed_at_utc
        ):
            raise PermissionError("Product Authorization status closure mismatch")
        return status

    def _commit_fence(
        self,
        binding: ExecutionAuthorizationContextBinding,
        status: ProductAuthorizationContextStatus,
        state: ExecutionAuthorizationFenceState,
        reason_code: str,
    ) -> ExecutionAuthorizationFence:
        fence_id = _stable_id(
            "execution_authorization_fence",
            binding.binding_sha256,
            status.status_sha256,
            state.value,
        )
        fence = ExecutionAuthorizationFence.build(
            fence_id=fence_id,
            fence_ref=f"runtime-authorization:{fence_id}",
            binding_ref=binding.binding_ref,
            binding_sha256=binding.binding_sha256,
            state=state,
            reason_code=reason_code,
            product_status_ref=status.status_ref,
            product_status_sha256=status.status_sha256,
            recorded_at_utc=status.observed_at_utc,
        )
        return self._ledger.commit_fence(fence)


__all__ = [
    "ExecutionAuthorizationAdmissionResult",
    "ExecutionAuthorizationController",
    "InMemoryExecutionAuthorizationLedger",
]
