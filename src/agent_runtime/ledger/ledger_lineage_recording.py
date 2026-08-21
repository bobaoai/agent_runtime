"""In-memory recording of Module Run, Variant, and Attempt lineage.

This implementation supports isolated tests and evaluation. Its record shapes
precede the final PostgreSQL claim-token and recovery bindings in AR06 and are
not a substitute for the formal production execution ledger.
"""

from __future__ import annotations

from threading import RLock

from ..contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleAttemptStartedRecord,
    ModuleExecutionVariantRecord,
    ModuleRunRecord,
)
from ..contracts.execution_module_definition import (
    ModuleExecutionRequest,
    ModuleRunResult,
    WorkflowModuleExecutionRequest,
)


class InMemoryModuleExecutionLedger:
    """Thread-safe test ledger with request idempotency and immutable results."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._request_hashes: dict[str, str] = {}
        self._idempotency_requests: dict[str, tuple[str, str]] = {}
        self._in_progress: set[str] = set()
        self._run_records: dict[str, ModuleRunRecord] = {}
        self._variant_records: dict[str, ModuleExecutionVariantRecord] = {}
        self._attempt_starts: dict[str, ModuleAttemptStartedRecord] = {}
        self._attempts: dict[str, ModuleAttemptRecord] = {}
        self._results: dict[str, ModuleRunResult] = {}

    def existing_result(
        self, request: ModuleExecutionRequest | WorkflowModuleExecutionRequest
    ) -> ModuleRunResult | None:
        """Return an identical prior result or reject an ID/hash collision."""

        with self._lock:
            prior_hash = self._request_hashes.get(request.request_id)
            if prior_hash is not None and prior_hash != request.request_sha256:
                raise ValueError("Module execution request ID reused with different bytes")
            prior_request = self._idempotency_requests.get(request.idempotency_key)
            current_request = (request.request_id, request.request_sha256)
            if prior_request is not None and prior_request != current_request:
                raise ValueError(
                    "Module execution idempotency key reused by another request"
                )
            return self._results.get(request.request_id)

    def begin(
        self,
        request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
        module_run: ModuleRunRecord,
        variants: tuple[ModuleExecutionVariantRecord, ...],
        attempt_starts: tuple[ModuleAttemptStartedRecord, ...],
    ) -> ModuleRunResult | None:
        """Begin once, returning a result won by a concurrent identical caller."""

        request.validate()
        module_run.validate()
        for variant in variants:
            variant.validate()
        for started in attempt_starts:
            started.validate()
        with self._lock:
            prior_request = self._idempotency_requests.get(request.idempotency_key)
            current_request = (request.request_id, request.request_sha256)
            if prior_request is not None and prior_request != current_request:
                raise ValueError(
                    "Module execution idempotency key reused by another request"
                )
            prior_hash = self._request_hashes.get(request.request_id)
            if prior_hash is not None:
                if prior_hash != request.request_sha256:
                    raise ValueError(
                        "Module execution request ID reused with different bytes"
                    )
                if request.request_id in self._in_progress:
                    raise RuntimeError("Module Run is already in progress")
                if request.request_id in self._results:
                    return self._results[request.request_id]
                raise RuntimeError("Module Run requires recovery before retry")
            self._request_hashes[request.request_id] = request.request_sha256
            self._idempotency_requests[request.idempotency_key] = current_request
            self._in_progress.add(request.request_id)
            self._run_records[module_run.module_run_id] = module_run
            for variant in variants:
                self._variant_records[variant.variant_id] = variant
            for started in attempt_starts:
                self._attempt_starts[started.attempt_id] = started
            return None

    def commit_attempt(self, attempt: ModuleAttemptRecord) -> None:
        """Store one immutable terminal Attempt record idempotently."""

        attempt.validate()
        with self._lock:
            existing = self._attempts.get(attempt.attempt_id)
            if existing is not None and existing != attempt:
                raise ValueError("Attempt ID reused with different terminal content")
            self._attempts[attempt.attempt_id] = attempt

    def commit_result(self, request_id: str, result: ModuleRunResult) -> None:
        """Commit the immutable run result and clear its in-progress marker."""

        with self._lock:
            existing = self._results.get(request_id)
            if existing is not None and existing != result:
                raise ValueError("Module Run result changed during idempotent replay")
            self._results[request_id] = result
            self._in_progress.discard(request_id)


__all__ = ["InMemoryModuleExecutionLedger"]
