"""Temporal projection of the backend-neutral two-Cell conformance contract.

The Temporal Workflow persists only local identities, opaque artifact references,
hashes, and minimal control state.  Activities own module execution and write their
attempt audit to a Cell-local SQLite ledger injected by the worker.  The module is
an integration harness, not a second domain graph.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from .durability_backend_conformance import (
    CONFORMANCE_CONTRACT_VERSION,
    ConformanceCase,
    ConformanceEffectKind,
    ConformanceModulePhase,
    build_conformance_module_dispatch,
)
from ..contracts.durability_execution_definition import assert_ref_only_backend_payload


TEMPORAL_ACTIVITY_NAME = "agent_runtime_temporal_conformance_module"
TEMPORAL_WORKFLOW_NAME = "agent_runtime_temporal_conformance_v3"
_OPAQUE_EXTERNAL_EVENT_REF = re.compile(
    r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$"
)


def _validate_temporal_external_event(event: Mapping[str, Any]) -> None:
    if set(event) != {"action", "event_ref"}:
        raise ValueError("external event has an invalid shape")
    if event.get("action") not in {"repeat", "complete"}:
        raise ValueError("unsupported external event action")
    event_ref = event.get("event_ref")
    if not isinstance(event_ref, str) or not _OPAQUE_EXTERNAL_EVENT_REF.fullmatch(
        event_ref
    ):
        raise ValueError("external event requires a scheme-prefixed opaque ref")
    assert_ref_only_backend_payload(event)


def build_temporal_workflow_input(case: ConformanceCase) -> dict[str, Any]:
    """Project one shared conformance case into a ref-only Temporal input."""

    case_payload = case.backend_payload()
    payload: dict[str, Any] = {
        "conformance_contract_version": CONFORMANCE_CONTRACT_VERSION,
        "execution": case_payload["execution"],
        "module_plan": case_payload["module_plan"],
        "requires_revision_event": case.requires_revision_event,
        "side_effect_key": (
            f"{case.envelope.workflow_execution_id}:synthetic_idempotent_effect"
        ),
    }
    assert_ref_only_backend_payload(
        payload,
        forbidden_sentinels=(case.private_sentinel,),
    )
    return payload


def build_temporal_external_event(
    action: str,
    event_ref: str,
    *,
    forbidden_sentinels: tuple[str, ...] = (),
) -> dict[str, str]:
    """Build a validated content-free external-event Signal payload."""

    payload = {"action": action, "event_ref": event_ref}
    _validate_temporal_external_event(payload)
    assert_ref_only_backend_payload(
        payload,
        forbidden_sentinels=forbidden_sentinels,
    )
    return payload


class TemporalConformanceLedger:
    """Cell-local Attempt audit and idempotent side-effect ledger."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS attempt_audit (
                    audit_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    workflow_execution_id TEXT NOT NULL,
                    module_run_id TEXT NOT NULL,
                    variant_id TEXT NOT NULL,
                    attempt_id TEXT NOT NULL,
                    activity_attempt INTEGER NOT NULL,
                    operation TEXT NOT NULL,
                    status TEXT NOT NULL,
                    UNIQUE(workflow_execution_id, attempt_id, status)
                );

                CREATE TABLE IF NOT EXISTS side_effect_commit (
                    idempotency_key TEXT PRIMARY KEY,
                    workflow_execution_id TEXT NOT NULL,
                    artifact_ref TEXT NOT NULL
                );
                """
            )

    def append_attempt(
        self,
        *,
        workflow_execution_id: str,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        activity_attempt: int,
        operation: str,
        status: str,
    ) -> None:
        """Append one immutable Attempt status transition."""

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO attempt_audit (
                    workflow_execution_id,
                    module_run_id,
                    variant_id,
                    attempt_id,
                    activity_attempt,
                    operation,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workflow_execution_id,
                    module_run_id,
                    variant_id,
                    attempt_id,
                    activity_attempt,
                    operation,
                    status,
                ),
            )

    def commit_side_effect_once(
        self,
        *,
        idempotency_key: str,
        workflow_execution_id: str,
        artifact_ref: str,
    ) -> bool:
        """Commit once and return whether this call created the durable row."""

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO side_effect_commit (
                    idempotency_key,
                    workflow_execution_id,
                    artifact_ref
                ) VALUES (?, ?, ?)
                """,
                (idempotency_key, workflow_execution_id, artifact_ref),
            )
            return cursor.rowcount == 1

    def attempt_rows(self) -> tuple[dict[str, Any], ...]:
        """Return Attempt audit rows in append order for assertions and operators."""

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT workflow_execution_id,
                       module_run_id,
                       variant_id,
                       attempt_id,
                       activity_attempt,
                       operation,
                       status
                  FROM attempt_audit
              ORDER BY audit_sequence
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def side_effect_count(self) -> int:
        """Return the number of unique committed external effects."""

        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS row_count FROM side_effect_commit"
            ).fetchone()
        assert row is not None
        return int(row["row_count"])


class TemporalConformanceActivities:
    """Deterministic synthetic Activities with a Cell-local audit boundary."""

    def __init__(
        self,
        ledger: TemporalConformanceLedger,
        *,
        crash_after_first_effect_commit: bool = False,
    ) -> None:
        self._ledger = ledger
        self._crash_after_first_effect_commit = crash_after_first_effect_commit

    @activity.defn(name=TEMPORAL_ACTIVITY_NAME)
    async def run_module(self, module_input: dict[str, Any]) -> dict[str, Any]:
        """Execute one synthetic module and preserve Temporal retry identity."""

        assert_ref_only_backend_payload(module_input)
        info = activity.info()
        activity_attempt = info.attempt
        attempt_id = (
            f"{module_input['attempt_base_id']}_try_{activity_attempt:03d}"
        )
        common = {
            "workflow_execution_id": str(module_input["workflow_execution_id"]),
            "module_run_id": str(module_input["module_run_id"]),
            "variant_id": str(module_input["variant_id"]),
            "attempt_id": attempt_id,
            "activity_attempt": activity_attempt,
            "operation": str(module_input["operation"]),
        }

        if module_input["effect_kind"] == "idempotent":
            inserted = self._ledger.commit_side_effect_once(
                idempotency_key=str(module_input["side_effect_key"]),
                workflow_execution_id=common["workflow_execution_id"],
                artifact_ref=str(module_input["artifact_ref"]),
            )
            if self._crash_after_first_effect_commit and inserted:
                self._ledger.append_attempt(
                    **common,
                    status="worker_crashed_after_commit",
                )
                os._exit(70)

        self._ledger.append_attempt(**common, status="completed")
        result = {
            **common,
            "status": "completed",
            "artifact_ref": str(module_input["artifact_ref"]),
        }
        assert_ref_only_backend_payload(result)
        return result


@workflow.defn(name=TEMPORAL_WORKFLOW_NAME)
class TemporalConformanceWorkflow:
    """Durable ref-only cursor for the shared Runtime conformance graph."""

    def __init__(self) -> None:
        self._status = "created"
        self._workflow_execution_id = "unbound"
        self._tenant_id = "unbound"
        self._cell_id = "unbound"
        self._external_events: list[dict[str, str]] = []
        self._activity_results: list[dict[str, Any]] = []
        self._revision_event_count = 0

    async def _execute_module(
        self,
        *,
        module: Mapping[str, Any],
        execution: Mapping[str, Any],
        side_effect_key: str,
    ) -> None:
        artifact = execution["artifacts"][0]
        dispatch = build_conformance_module_dispatch(
            workflow_execution_id=str(execution["workflow_execution_id"]),
            backend_id="temporal",
            module_key=str(module["module_key"]),
            phase=ConformanceModulePhase(str(module["phase"])),
            effect_kind=ConformanceEffectKind(str(module["effect_kind"])),
        ).dispatch_payload()
        module_input = {
            "workflow_execution_id": dispatch["workflow_execution_id"],
            "module_run_id": dispatch["module_run_id"],
            "variant_id": dispatch["variant_id"],
            "attempt_base_id": dispatch["attempt_base_id"],
            "operation": module["module_key"],
            "effect_kind": module["effect_kind"],
            "artifact_ref": artifact["uri_ref"],
            "artifact_sha256": artifact["sha256"],
            "side_effect_key": side_effect_key,
        }
        result = await workflow.execute_activity(
            TEMPORAL_ACTIVITY_NAME,
            module_input,
            start_to_close_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100),
                maximum_interval=timedelta(milliseconds=500),
                maximum_attempts=5,
            ),
            result_type=dict[str, Any],
        )
        self._activity_results.append(result)

    @workflow.run
    async def run(self, workflow_input: dict[str, Any]) -> dict[str, Any]:
        """Interpret projected modules, exercise one effect, and run an event loop."""

        if (
            workflow_input["conformance_contract_version"]
            != CONFORMANCE_CONTRACT_VERSION
        ):
            raise ValueError("unsupported Temporal conformance contract version")
        execution = workflow_input["execution"]
        module_plan = workflow_input["module_plan"]
        self._workflow_execution_id = str(execution["workflow_execution_id"])
        self._tenant_id = str(execution["tenant_id"])
        self._cell_id = str(execution["cell_id"])
        side_effect_key = str(workflow_input["side_effect_key"])
        pre_event_modules = [
            module for module in module_plan if module["phase"] == "pre_event"
        ]
        revision_event_modules = [
            module for module in module_plan if module["phase"] == "revision_event"
        ]
        if not pre_event_modules or len(revision_event_modules) != 1:
            raise ValueError("invalid Temporal conformance module projection")
        for module in pre_event_modules:
            self._status = str(module["module_key"])
            await self._execute_module(
                module=module,
                execution=execution,
                side_effect_key=side_effect_key,
            )

        while True:
            self._status = "awaiting_external_event"
            await workflow.wait_condition(lambda: bool(self._external_events))
            event = self._external_events.pop(0)
            if event["action"] == "repeat":
                if (
                    not workflow_input["requires_revision_event"]
                    or self._revision_event_count >= 1
                ):
                    raise ValueError("unexpected Temporal revision event")
                self._status = "revision_event"
                self._revision_event_count += 1
                await self._execute_module(
                    module=revision_event_modules[0],
                    execution=execution,
                    side_effect_key=side_effect_key,
                )
                continue
            if event["action"] == "complete":
                if (
                    workflow_input["requires_revision_event"]
                    and self._revision_event_count != 1
                ):
                    raise ValueError("required Temporal revision event is missing")
                self._status = "complete"
                return self.snapshot()
            raise ValueError("unsupported external event action")

    @workflow.signal(name="external_event")
    async def external_event(self, event: dict[str, str]) -> None:
        """Append an external event represented by an opaque reference."""

        _validate_temporal_external_event(event)
        self._external_events.append(event)

    @workflow.query(name="snapshot")
    def snapshot(self) -> dict[str, Any]:
        """Return content-free execution state for operator inspection."""

        return {
            "status": self._status,
            "tenant_id": self._tenant_id,
            "cell_id": self._cell_id,
            "workflow_execution_id": self._workflow_execution_id,
            "revision_event_count": self._revision_event_count,
            "activity_results": list(self._activity_results),
        }


async def _run_crash_worker(
    *,
    target_host: str,
    namespace: str,
    task_queue: str,
    ledger_path: str,
    ready_connection: Any,
) -> None:
    client = await Client.connect(target_host, namespace=namespace)
    activities = TemporalConformanceActivities(
        TemporalConformanceLedger(ledger_path),
        crash_after_first_effect_commit=True,
    )
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[TemporalConformanceWorkflow],
        activities=[activities.run_module],
    )
    ready_connection.send("ready")
    ready_connection.close()
    await worker.run()


def run_temporal_crash_worker_process(
    target_host: str,
    namespace: str,
    task_queue: str,
    ledger_path: str,
    ready_connection: Any,
) -> None:
    """Run a worker process that exits after its first committed side effect."""

    asyncio.run(
        _run_crash_worker(
            target_host=target_host,
            namespace=namespace,
            task_queue=task_queue,
            ledger_path=ledger_path,
            ready_connection=ready_connection,
        )
    )
