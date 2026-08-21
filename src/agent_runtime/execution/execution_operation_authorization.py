"""Runtime-owned authorization ordering and grant-binding service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from threading import RLock
from typing import Callable, ClassVar, Protocol

from ..contracts.execution_operation_definition import (
    AuthorizationEffect,
    ExecutionAuthorizationBinding,
    ExecutionAuthorizationStatus,
    ExecutionAuthorizationStatusEvidence,
    ExecutionControlFenceStatus,
    OperationAuthorizationRequest,
    OperationAuthorizationResolution,
    OperationAuthorizationResolutionRecord,
    OperationGrantBindingRecord,
    ProductOperationAuthorizationResult,
)
from ..contracts.registry_release_definition import RuntimeModuleRelease


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00")


class AuthorizationAdapter(Protocol):
    """Provider-neutral Product Authorization operation port."""

    def authorize_operation(
        self,
        request: OperationAuthorizationRequest,
    ) -> ProductOperationAuthorizationResult:
        """Return exact Product-owned allow or deny evidence."""


@dataclass(frozen=True)
class BoundOperationAuthorization:
    """Runtime result returned after Product evidence reaches a terminal state."""

    record_type: ClassVar[str] = "bound_operation_authorization"

    request: OperationAuthorizationRequest
    product_result: ProductOperationAuthorizationResult
    resolution: OperationAuthorizationResolutionRecord
    grant_binding: OperationGrantBindingRecord | None


class InMemoryAuthorizationLedger:
    """Duplicate-safe reference ledger for authorization conformance tests."""

    service_id: ClassVar[str] = "in_memory_authorization_ledger"

    def __init__(self) -> None:
        self._lock = RLock()
        self._requests: dict[str, OperationAuthorizationRequest] = {}
        self._results: dict[str, BoundOperationAuthorization] = {}

    def existing(
        self,
        request: OperationAuthorizationRequest,
    ) -> BoundOperationAuthorization | None:
        """Return the committed result for an identical request, when present."""

        with self._lock:
            existing_request = self._requests.get(request.authorization_request_ref)
            if existing_request is not None and existing_request != request:
                raise ValueError("authorization request ref collision")
            return self._results.get(request.authorization_request_ref)

    def commit_request(self, request: OperationAuthorizationRequest) -> None:
        """Commit one exact request before Product Authorization is called."""

        with self._lock:
            existing = self._requests.get(request.authorization_request_ref)
            if existing is not None and existing != request:
                raise ValueError("authorization request ref collision")
            self._requests[request.authorization_request_ref] = request

    def commit_result(self, result: BoundOperationAuthorization) -> None:
        """Commit one terminal Product result without overwriting a conflict."""

        request_ref = result.request.authorization_request_ref
        with self._lock:
            if request_ref not in self._requests:
                raise RuntimeError("authorization request must commit before result")
            existing = self._results.get(request_ref)
            if existing is not None and existing != result:
                raise ValueError("authorization result conflict")
            self._results[request_ref] = result


class RuntimeAuthorizationCoordinator:
    """Bind Product authorization to exact Runtime execution and release state."""

    service_id: ClassVar[str] = "runtime_authorization_coordinator"

    def __init__(
        self,
        *,
        adapter: AuthorizationAdapter,
        ledger: InMemoryAuthorizationLedger,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._adapter = adapter
        self._ledger = ledger
        self._clock = clock

    def authorize_and_bind_operation(
        self,
        *,
        request: OperationAuthorizationRequest,
        execution_binding: ExecutionAuthorizationBinding,
        status_evidence: ExecutionAuthorizationStatusEvidence,
        module_release: RuntimeModuleRelease,
    ) -> BoundOperationAuthorization:
        """Resolve one exact Product result and bind an allow before Gateway use."""

        if type(request) is not OperationAuthorizationRequest:
            raise ValueError("request must be an exact OperationAuthorizationRequest")
        if type(execution_binding) is not ExecutionAuthorizationBinding:
            raise ValueError(
                "execution_binding must be an exact ExecutionAuthorizationBinding"
            )
        if type(status_evidence) is not ExecutionAuthorizationStatusEvidence:
            raise ValueError(
                "status_evidence must be exact ExecutionAuthorizationStatusEvidence"
            )
        if type(module_release) is not RuntimeModuleRelease:
            raise ValueError("module_release must be an exact RuntimeModuleRelease")

        request.validate()
        execution_binding.validate()
        status_evidence.validate()
        module_release.validate()
        self._validate_closure(
            request=request,
            execution_binding=execution_binding,
            status_evidence=status_evidence,
            module_release=module_release,
        )

        existing = self._ledger.existing(request)
        if existing is not None:
            return existing
        self._ledger.commit_request(request)

        product_result = self._adapter.authorize_operation(request)
        if type(product_result) is not ProductOperationAuthorizationResult:
            raise TypeError("AuthorizationAdapter returned an invalid result type")
        product_result.validate()
        if (
            product_result.authorization_request_ref
            != request.authorization_request_ref
            or product_result.authorization_request_sha256
            != request.authorization_request_sha256
        ):
            raise ValueError("Product authorization result request mismatch")
        request_time = _as_datetime(request.recorded_at_utc)
        if not (
            _as_datetime(product_result.effective_at_utc)
            <= request_time
            < _as_datetime(product_result.expiry_at_utc)
        ):
            raise PermissionError("Product authorization result is outside validity")

        recorded_at_utc = self._clock()
        if product_result.effect is AuthorizationEffect.DENY:
            resolution = OperationAuthorizationResolutionRecord.build(
                resolution_id=_stable_id(
                    "operation_resolution",
                    request.authorization_request_sha256,
                    product_result.result_sha256,
                ),
                authorization_request_ref=request.authorization_request_ref,
                authorization_request_sha256=request.authorization_request_sha256,
                product_result_ref=product_result.result_ref,
                product_result_sha256=product_result.result_sha256,
                selected_authority_ref=product_result.selected_authority_ref,
                selected_authority_sha256=(
                    product_result.selected_authority_sha256
                ),
                resolution=OperationAuthorizationResolution.DENIED,
                operation_grant_binding_ref=None,
                operation_grant_binding_sha256=None,
                recorded_at_utc=recorded_at_utc,
            )
            bound = BoundOperationAuthorization(
                request=request,
                product_result=product_result,
                resolution=resolution,
                grant_binding=None,
            )
            self._ledger.commit_result(bound)
            return bound

        binding_id = _stable_id(
            "operation_grant_binding",
            request.authorization_request_sha256,
            product_result.selected_authority_sha256,
        )
        grant_binding = OperationGrantBindingRecord.build(
            binding_id=binding_id,
            binding_ref=f"runtime-authorization:{binding_id}",
            authorization_request_ref=request.authorization_request_ref,
            authorization_request_sha256=request.authorization_request_sha256,
            product_result_ref=product_result.result_ref,
            product_result_sha256=product_result.result_sha256,
            operation_grant_ref=product_result.selected_authority_ref,
            operation_grant_sha256=product_result.selected_authority_sha256,
            execution_authorization_status_evidence_ref=status_evidence.evidence_ref,
            execution_authorization_status_evidence_sha256=(
                status_evidence.evidence_sha256
            ),
            recorded_at_utc=recorded_at_utc,
        )
        resolution = OperationAuthorizationResolutionRecord.build(
            resolution_id=_stable_id(
                "operation_resolution",
                request.authorization_request_sha256,
                product_result.result_sha256,
            ),
            authorization_request_ref=request.authorization_request_ref,
            authorization_request_sha256=request.authorization_request_sha256,
            product_result_ref=product_result.result_ref,
            product_result_sha256=product_result.result_sha256,
            selected_authority_ref=product_result.selected_authority_ref,
            selected_authority_sha256=product_result.selected_authority_sha256,
            resolution=OperationAuthorizationResolution.GRANT_BOUND,
            operation_grant_binding_ref=grant_binding.binding_ref,
            operation_grant_binding_sha256=grant_binding.binding_sha256,
            recorded_at_utc=recorded_at_utc,
        )
        bound = BoundOperationAuthorization(
            request=request,
            product_result=product_result,
            resolution=resolution,
            grant_binding=grant_binding,
        )
        self._ledger.commit_result(bound)
        return bound

    @staticmethod
    def _validate_closure(
        *,
        request: OperationAuthorizationRequest,
        execution_binding: ExecutionAuthorizationBinding,
        status_evidence: ExecutionAuthorizationStatusEvidence,
        module_release: RuntimeModuleRelease,
    ) -> None:
        if (
            request.workflow_execution_id
            != execution_binding.workflow_execution_id
            or request.execution_authorization_binding_ref
            != execution_binding.binding_ref
            or request.execution_authorization_binding_sha256
            != execution_binding.binding_sha256
        ):
            raise ValueError("operation request execution binding mismatch")
        if (
            status_evidence.binding_ref != execution_binding.binding_ref
            or status_evidence.binding_sha256 != execution_binding.binding_sha256
        ):
            raise ValueError("authorization status evidence binding mismatch")
        if status_evidence.status is not ExecutionAuthorizationStatus.EFFECTIVE:
            raise PermissionError("execution authorization binding is not effective")
        if (
            status_evidence.control_fence_status
            is not ExecutionControlFenceStatus.OPEN
        ):
            raise PermissionError("execution control fence is closed")
        if (
            request.module_release_ref != module_release.release_ref
            or request.module_release_sha256 != module_release.release_sha256
        ):
            raise ValueError("operation request Module Release mismatch")
        if request.operation_id not in module_release.declared_operation_ids:
            raise PermissionError("operation is absent from Module Release declaration")


__all__ = [
    "AuthorizationAdapter",
    "BoundOperationAuthorization",
    "InMemoryAuthorizationLedger",
    "RuntimeAuthorizationCoordinator",
]
