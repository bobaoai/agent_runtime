"""In-memory recording of Module Run, Variant, and Attempt lineage.

This implementation supports isolated tests and evaluation. Its record shapes
precede the final PostgreSQL claim-token and recovery bindings in AR06 and are
not a substitute for the formal production execution ledger.
"""

from __future__ import annotations

from threading import RLock
from copy import deepcopy

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
from ..foundation.foundation_contract_validation import validate_id


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
        self._workflow_requests: dict[str, WorkflowModuleExecutionRequest] = {}
        self._outcomes: dict[tuple[str, str], object] = {}

    def guarded(self, operation):
        """Serialize a test-resource check/close with terminal record commits."""
        with self._lock:
            return operation()

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
            if type(request) is WorkflowModuleExecutionRequest:
                self._workflow_requests[request.request_id] = request
            for variant in variants:
                self._variant_records[variant.variant_id] = variant
            for started in attempt_starts:
                self._attempt_starts[started.attempt_id] = started
            return None

    def record_attempt_start(self, started: ModuleAttemptStartedRecord) -> None:
        """Record one actual start under an already claimed Run and Variant."""

        if type(started) is not ModuleAttemptStartedRecord:
            raise ValueError("started must be a ModuleAttemptStartedRecord")
        started.validate()
        with self._lock:
            existing = self._attempt_starts.get(started.attempt_id)
            if existing is not None:
                if existing != started:
                    raise ValueError("Attempt start is immutable")
                return
            variant = self._variant_records.get(started.variant_id)
            module_run = self._run_records.get(started.module_run_id)
            if (variant is None or module_run is None
                or variant.module_run_id != started.module_run_id):
                raise ValueError("Attempt start requires its registered Run and Variant")
            if module_run.request_id not in self._in_progress:
                raise ValueError("cannot start an Attempt after its Run has finished")
            self._attempt_starts[started.attempt_id] = started

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

    def results_for_execution(self, workflow_execution_id: str) -> tuple[ModuleRunResult, ...]:
        """Read exact completed node results from this process-local Ledger."""
        validate_id("workflow_execution_id", workflow_execution_id)
        with self._lock:
            return tuple(result for _, result in sorted(self._results.items())
                         if result.module_run.workflow_execution_id == workflow_execution_id)

    def get_committed_outcome(self, workflow_execution_id: str, dispatch_id: str) -> object | None:
        validate_id("workflow_execution_id", workflow_execution_id)
        validate_id("dispatch_id", dispatch_id)
        with self._lock:
            return deepcopy(self._outcomes.get((workflow_execution_id, dispatch_id)))

    def read_node_execution(self, workflow_execution_id: str, dispatch_id: str):
        """Return recorded request, Run, Attempts and result for Execution to validate.

        These are existing recorded values, not an Outcome schema or judgment.
        An absent dispatch returns None; its unfinished result remains None.
        """
        validate_id('workflow_execution_id', workflow_execution_id)
        validate_id('dispatch_id', dispatch_id)
        with self._lock:
            candidates = [request for request in self._workflow_requests.values()
                          if (request.workflow_execution_id, request.dispatch_id) == (workflow_execution_id, dispatch_id)]
            if not candidates:
                return None
            if len(candidates) != 1:
                raise ValueError('dispatch has multiple recorded requests')
            request = candidates[0]
            result = self._results.get(request.request_id)
            attempts = tuple(item for item in self._attempts.values() if item.module_run_id == request.module_run_id)
            return request, self._run_records.get(request.module_run_id), attempts, result

    def commit_outcome(self, workflow_execution_id: str, dispatch_id: str, outcome: object) -> object:
        """Store one immutable caller-validated value under its exact execution key.

        Execution checks the existing Outcome against recorded facts inside
        guarded(), then calls this method in the same critical section.
        """
        validate_id('workflow_execution_id', workflow_execution_id)
        validate_id('dispatch_id', dispatch_id)
        if outcome is None:
            raise ValueError('an absent outcome cannot be committed')
        with self._lock:
            key = (workflow_execution_id, dispatch_id)
            prior = self._outcomes.get(key)
            if prior is not None and prior != outcome:
                raise ValueError("Outcome changed during local replay")
            self._outcomes[key] = deepcopy(outcome)
            return deepcopy(outcome)


__all__ = ["InMemoryModuleExecutionLedger"]
