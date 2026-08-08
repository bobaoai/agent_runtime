"""Authorization-enforcing Gateway for Runtime-owned model execution.

The Gateway joins a Runtime protected-operation intent with a Product
Authorization decision before invoking a registered Module Executor.  It does
not issue Entitlements, grants, Module permissions, or provider credentials.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextBinding,
    ExecutionAuthorizationContextState,
    GatewayDecisionEffect,
    OperationAuthorizationQuery,
    ProductAuthorizationContextStatus,
    ProductOperationDecision,
    ProtectedOperationIntent,
)
from ..contracts.execution_module_definition import (
    ModuleExecutor,
    ModuleExecutorFailure,
    ModuleExecutorRequest,
    ModuleExecutorResult,
)
from ..execution.execution_operation_resolution import RuntimeProtectedOperationClient
from ..execution.execution_authorization_resolution import (
    ProductOperationAuthorizationClient,
)


@dataclass(frozen=True)
class ModelExecutionGatewayRequest:
    """Exact authority and Executor inputs for one provider invocation."""

    runtime_intent_ref: str
    runtime_intent_sha256: str
    authorization_query: OperationAuthorizationQuery
    executor_request: ModuleExecutorRequest


@dataclass(frozen=True)
class ModelExecutionAuthorization:
    """Product decision evidence returned with the Executor result."""

    intent_ref: str
    intent_sha256: str
    decision: ProductOperationDecision
    decision_ref: str
    decision_sha256: str


@dataclass(frozen=True)
class ModelExecutionGatewayResult:
    """Authorized Executor result and immutable Product decision lineage."""

    executor_result: ModuleExecutorResult
    authorization: ModelExecutionAuthorization


class ModelExecutionDenied(PermissionError):
    """Explicit Product denial that guarantees zero provider invocation."""

    def __init__(self, authorization: ModelExecutionAuthorization) -> None:
        super().__init__(authorization.decision.reason_code)
        self.authorization = authorization


class ModelExecutionGatewayFailure(RuntimeError):
    """Post-authorization Executor failure with both exact lineages."""

    def __init__(
        self,
        authorization: ModelExecutionAuthorization,
        executor_failure: ModuleExecutorFailure,
    ) -> None:
        super().__init__(str(executor_failure))
        self.authorization = authorization
        self.executor_failure = executor_failure


class ModelExecutionGateway:
    """Enforce Runtime and Product authority before one model Attempt."""

    def __init__(
        self,
        *,
        gateway_id: str,
        runtime_client: RuntimeProtectedOperationClient,
        authorization_client: ProductOperationAuthorizationClient,
        executor: ModuleExecutor,
    ) -> None:
        if not gateway_id:
            raise ValueError("gateway_id is required")
        for method_name in (
            "resolve_protected_operation_intent",
            "resolve_execution_authorization_binding",
        ):
            if not callable(getattr(runtime_client, method_name, None)):
                raise ValueError("runtime_client lacks protected-operation resolution")
        for method_name in ("authorize_operation", "validate_execution_context"):
            if not callable(getattr(authorization_client, method_name, None)):
                raise ValueError("authorization_client lacks Product authorization")
        if not callable(getattr(executor, "execute", None)):
            raise ValueError("executor must implement execute(request)")
        self.gateway_id = gateway_id
        self._runtime_client = runtime_client
        self._authorization_client = authorization_client
        self._executor = executor

    def execute(
        self,
        request: ModelExecutionGatewayRequest,
    ) -> ModelExecutionGatewayResult:
        """Authorize and execute once, returning both result lineages."""

        if type(request) is not ModelExecutionGatewayRequest:
            raise ValueError("request must be an exact ModelExecutionGatewayRequest")
        if not request.runtime_intent_ref:
            raise ValueError("runtime_intent_ref is required")
        request.authorization_query.validate()
        intent = self._runtime_client.resolve_protected_operation_intent(
            request.runtime_intent_ref,
            request.runtime_intent_sha256,
        )
        if type(intent) is not ProtectedOperationIntent:
            raise TypeError("Runtime returned an invalid protected-operation intent")
        intent.validate()
        binding = self._runtime_client.resolve_execution_authorization_binding(
            intent.binding_ref,
            intent.binding_sha256,
        )
        if type(binding) is not ExecutionAuthorizationContextBinding:
            raise TypeError("Runtime returned an invalid execution binding")
        binding.validate()
        self._validate_closure(intent, binding, request)
        if intent.requires_grant:
            raise PermissionError("model execution must not require a high-risk grant")

        status = self._authorization_client.validate_execution_context(
            binding.context_id,
            binding.tenant_id,
            request.authorization_query.observed_at_utc,
        )
        if type(status) is not ProductAuthorizationContextStatus:
            raise TypeError("Product Authorization returned an invalid context status")
        status.validate()
        if (
            status.context_id != binding.context_id
            or status.state is not ExecutionAuthorizationContextState.EFFECTIVE
        ):
            raise PermissionError("execution authorization context is not effective")

        decision = self._authorization_client.authorize_operation(
            request.authorization_query
        )
        if type(decision) is not ProductOperationDecision:
            raise TypeError("Product Authorization returned an invalid decision")
        decision.validate()
        if (
            decision.query_id != request.authorization_query.query_id
            or decision.query_sha256 != request.authorization_query.query_sha256
        ):
            raise PermissionError("Product Authorization decision closure mismatch")
        authorization = ModelExecutionAuthorization(
            intent_ref=intent.intent_ref,
            intent_sha256=intent.intent_sha256,
            decision=decision,
            decision_ref=decision.decision_ref,
            decision_sha256=decision.decision_sha256,
        )
        if decision.effect is not GatewayDecisionEffect.ALLOW:
            raise ModelExecutionDenied(authorization)
        try:
            executor_result = self._executor.execute(request.executor_request)
        except ModuleExecutorFailure as exc:
            raise ModelExecutionGatewayFailure(authorization, exc) from exc
        if type(executor_result) is not ModuleExecutorResult:
            raise TypeError("Module Executor returned an invalid result")
        executor_result.validate()
        return ModelExecutionGatewayResult(
            executor_result=executor_result,
            authorization=authorization,
        )

    def _validate_closure(
        self,
        intent: ProtectedOperationIntent,
        binding: ExecutionAuthorizationContextBinding,
        request: ModelExecutionGatewayRequest,
    ) -> None:
        authorization_query = request.authorization_query
        executor_request = request.executor_request
        exact = (
            request.runtime_intent_ref == intent.intent_ref,
            request.runtime_intent_sha256 == intent.intent_sha256,
            intent.binding_ref == binding.binding_ref,
            intent.binding_sha256 == binding.binding_sha256,
            authorization_query.principal_id == binding.principal_id,
            authorization_query.actor_workload_id == binding.actor_workload_id,
            authorization_query.tenant_id == binding.tenant_id,
            authorization_query.cell_id == binding.cell_id,
            authorization_query.workflow_release_id == binding.workflow_release_id,
            authorization_query.execution_context_id == binding.context_id,
            authorization_query.operation_id == intent.operation_id,
            authorization_query.resource_ref == intent.resource_ref,
            authorization_query.enforcing_gateway_id == self.gateway_id,
            intent.enforcing_gateway_id == self.gateway_id,
            authorization_query.idempotency_key == intent.idempotency_key,
            executor_request.module_run_id == intent.module_run_id,
            executor_request.module.release_ref == intent.module_release_ref,
            executor_request.module.release_sha256
            == intent.module_release_sha256,
        )
        if not all(exact):
            raise PermissionError("model execution authority closure mismatch")


__all__ = [
    "ModelExecutionAuthorization",
    "ModelExecutionDenied",
    "ModelExecutionGateway",
    "ModelExecutionGatewayFailure",
    "ModelExecutionGatewayRequest",
    "ModelExecutionGatewayResult",
]
