"""Real Temporal dev-server integration for the shared two-Cell fixture."""

from __future__ import annotations

import asyncio
import multiprocessing
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("temporalio")

from temporalio.api.history.v1 import History
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from agent_runtime.testing.durability_temporal_conformance import (
    TemporalConformanceActivities,
    TemporalConformanceLedger,
    TemporalConformanceWorkflow,
    build_temporal_external_event,
    build_temporal_workflow_input,
    run_temporal_crash_worker_process,
)
from agent_runtime.testing.durability_backend_conformance import (
    ALPHA_PRIVATE_SENTINEL,
    BETA_PRIVATE_SENTINEL,
    build_two_cell_conformance_fixture,
)


async def _wait_for_snapshot(
    handle: Any,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while loop.time() < deadline:
        snapshot = await handle.query(TemporalConformanceWorkflow.snapshot)
        if predicate(snapshot):
            return snapshot
        await asyncio.sleep(0.05)
    raise AssertionError("Temporal workflow did not reach the expected snapshot")


async def _run_real_temporal_two_cell_test(tmp_path: Path) -> None:
    fixture = build_two_cell_conformance_fixture("temporal")
    alpha_backend_db = tmp_path / "alpha_temporal.sqlite"
    beta_backend_db = tmp_path / "beta_temporal.sqlite"
    alpha_ledger_path = tmp_path / "alpha_attempts.sqlite"
    beta_ledger_path = tmp_path / "beta_attempts.sqlite"

    alpha_environment = await WorkflowEnvironment.start_local(
        namespace=fixture.alpha.binding.backend_namespace,
        dev_server_database_filename=str(alpha_backend_db),
        ui=False,
    )
    beta_environment: WorkflowEnvironment | None = None
    crash_worker: multiprocessing.Process | None = None
    try:
        beta_environment = await WorkflowEnvironment.start_local(
            namespace=fixture.beta.binding.backend_namespace,
            dev_server_database_filename=str(beta_backend_db),
            ui=False,
        )
        alpha_target = (
            alpha_environment.client.service_client.config.target_host
        )
        beta_target = beta_environment.client.service_client.config.target_host
        assert alpha_target != beta_target
        assert alpha_backend_db != beta_backend_db

        alpha_queue = "temporal_cell_alpha_us"
        beta_queue = "temporal_cell_beta_cn"
        alpha_ledger = TemporalConformanceLedger(alpha_ledger_path)
        beta_ledger = TemporalConformanceLedger(beta_ledger_path)
        alpha_activities = TemporalConformanceActivities(alpha_ledger)
        alpha_worker = Worker(
            alpha_environment.client,
            task_queue=alpha_queue,
            workflows=[TemporalConformanceWorkflow],
            activities=[alpha_activities.run_module],
        )

        process_context = multiprocessing.get_context("spawn")
        ready_parent, ready_child = process_context.Pipe(duplex=False)
        crash_worker = process_context.Process(
            target=run_temporal_crash_worker_process,
            args=(
                beta_target,
                fixture.beta.binding.backend_namespace,
                beta_queue,
                str(beta_ledger_path),
                ready_child,
            ),
            name="temporal-beta-crash-worker",
        )

        async with alpha_worker:
            crash_worker.start()
            ready_child.close()
            ready = await asyncio.to_thread(ready_parent.poll, 10.0)
            assert ready is True
            assert ready_parent.recv() == "ready"
            ready_parent.close()

            alpha_handle = await alpha_environment.client.start_workflow(
                TemporalConformanceWorkflow.run,
                build_temporal_workflow_input(fixture.alpha),
                id=fixture.alpha.envelope.workflow_execution_id,
                task_queue=alpha_queue,
            )
            beta_handle = await beta_environment.client.start_workflow(
                TemporalConformanceWorkflow.run,
                build_temporal_workflow_input(fixture.beta),
                id=fixture.beta.envelope.workflow_execution_id,
                task_queue=beta_queue,
            )

            alpha_waiting = await _wait_for_snapshot(
                alpha_handle,
                lambda snapshot: snapshot["status"] == "awaiting_external_event",
            )
            assert alpha_waiting["revision_event_count"] == 0

            await asyncio.to_thread(crash_worker.join, 20.0)
            assert crash_worker.exitcode == 70

            replacement_activities = TemporalConformanceActivities(beta_ledger)
            replacement_worker = Worker(
                beta_environment.client,
                task_queue=beta_queue,
                workflows=[TemporalConformanceWorkflow],
                activities=[replacement_activities.run_module],
            )
            async with replacement_worker:
                beta_waiting = await _wait_for_snapshot(
                    beta_handle,
                    lambda snapshot: (
                        snapshot["status"] == "awaiting_external_event"
                    ),
                )
                assert beta_waiting["revision_event_count"] == 0

                await alpha_handle.signal(
                    TemporalConformanceWorkflow.external_event,
                    build_temporal_external_event(
                        "repeat",
                        "event-ref:alpha:repeat:001",
                        forbidden_sentinels=(
                            ALPHA_PRIVATE_SENTINEL,
                            BETA_PRIVATE_SENTINEL,
                        ),
                    ),
                )
                alpha_revised = await _wait_for_snapshot(
                    alpha_handle,
                    lambda snapshot: (
                        snapshot["status"] == "awaiting_external_event"
                        and snapshot["revision_event_count"] == 1
                    ),
                )
                assert alpha_revised["workflow_execution_id"] == (
                    fixture.alpha.envelope.workflow_execution_id
                )
                beta_unchanged = await beta_handle.query(
                    TemporalConformanceWorkflow.snapshot
                )
                assert beta_unchanged["status"] == "awaiting_external_event"
                assert beta_unchanged["revision_event_count"] == 0

                await alpha_handle.signal(
                    TemporalConformanceWorkflow.external_event,
                    build_temporal_external_event(
                        "complete",
                        "event-ref:alpha:complete:001",
                        forbidden_sentinels=(
                            ALPHA_PRIVATE_SENTINEL,
                            BETA_PRIVATE_SENTINEL,
                        ),
                    ),
                )
                await beta_handle.signal(
                    TemporalConformanceWorkflow.external_event,
                    build_temporal_external_event(
                        "complete",
                        "event-ref:beta:complete:001",
                        forbidden_sentinels=(
                            ALPHA_PRIVATE_SENTINEL,
                            BETA_PRIVATE_SENTINEL,
                        ),
                    ),
                )
                alpha_result, beta_result = await asyncio.gather(
                    alpha_handle.result(),
                    beta_handle.result(),
                )

        assert alpha_result["status"] == "complete"
        assert beta_result["status"] == "complete"
        assert alpha_result["tenant_id"] == fixture.alpha.binding.tenant_id
        assert beta_result["tenant_id"] == fixture.beta.binding.tenant_id
        assert alpha_result["cell_id"] == fixture.alpha.binding.cell_id
        assert beta_result["cell_id"] == fixture.beta.binding.cell_id
        assert alpha_result["revision_event_count"] == 1
        assert beta_result["revision_event_count"] == 0

        for result, case in (
            (alpha_result, fixture.alpha),
            (beta_result, fixture.beta),
        ):
            assert result["workflow_execution_id"] == (
                case.envelope.workflow_execution_id
            )
            assert result["activity_results"]
            expected_modules = {module.module_key: module for module in case.modules}
            for activity_result in result["activity_results"]:
                expected_module = expected_modules[activity_result["operation"]]
                assert activity_result["workflow_execution_id"] == (
                    case.envelope.workflow_execution_id
                )
                assert activity_result["module_run_id"] == expected_module.module_run_id
                assert activity_result["variant_id"] == expected_module.variant_id
                assert activity_result["attempt_id"].startswith(
                    expected_module.attempt_base_id
                )
            lineage_pairs = {
                (row["module_run_id"], row["variant_id"])
                for row in result["activity_results"]
            }
            assert len({variant for _, variant in lineage_pairs}) == len(
                {module_run for module_run, _ in lineage_pairs}
            )

        beta_attempts = beta_ledger.attempt_rows()
        crashed_attempt = next(
            row
            for row in beta_attempts
            if row["status"] == "worker_crashed_after_commit"
        )
        recovered_attempt = next(
            row
            for row in beta_attempts
            if row["operation"] == "synthetic_module_d"
            and row["status"] == "completed"
        )
        assert crashed_attempt["activity_attempt"] == 1
        assert recovered_attempt["activity_attempt"] >= 2
        assert crashed_attempt["module_run_id"] == recovered_attempt["module_run_id"]
        assert crashed_attempt["variant_id"] == recovered_attempt["variant_id"]
        assert crashed_attempt["attempt_id"] != recovered_attempt["attempt_id"]
        assert alpha_ledger.side_effect_count() == (
            fixture.alpha.expected_side_effect_commits
        )
        assert beta_ledger.side_effect_count() == (
            fixture.beta.expected_side_effect_commits
        )

        alpha_history = await alpha_handle.fetch_history()
        beta_history = await beta_handle.fetch_history()
        alpha_event_kinds = {
            event.WhichOneof("attributes") for event in alpha_history.events
        }
        beta_event_kinds = {
            event.WhichOneof("attributes") for event in beta_history.events
        }
        assert "workflow_execution_signaled_event_attributes" in alpha_event_kinds
        assert "workflow_execution_signaled_event_attributes" in beta_event_kinds
        assert "activity_task_scheduled_event_attributes" in beta_event_kinds
        assert "activity_task_completed_event_attributes" in beta_event_kinds
        for history in (alpha_history, beta_history):
            serialized_history = History(events=history.events).SerializeToString()
            assert ALPHA_PRIVATE_SENTINEL.encode() not in serialized_history
            assert BETA_PRIVATE_SENTINEL.encode() not in serialized_history
            await Replayer(
                workflows=[TemporalConformanceWorkflow]
            ).replay_workflow(history)
    finally:
        if crash_worker is not None and crash_worker.is_alive():
            crash_worker.kill()
            crash_worker.join(timeout=5.0)
        if beta_environment is not None:
            await beta_environment.shutdown()
        await alpha_environment.shutdown()

    for persistence_file in tmp_path.iterdir():
        raw = persistence_file.read_bytes()
        assert ALPHA_PRIVATE_SENTINEL.encode() not in raw
        assert BETA_PRIVATE_SENTINEL.encode() not in raw


@pytest.mark.skipif(
    os.environ.get("RUN_TEMPORAL_INTEGRATION") != "1",
    reason="set RUN_TEMPORAL_INTEGRATION=1 to start local Temporal dev servers",
)
def test_real_temporal_two_cell_durable_execution(tmp_path: Path) -> None:
    """Prove isolation, Signals, worker recovery, replay, and ref-only history."""

    asyncio.run(_run_real_temporal_two_cell_test(tmp_path))


def test_temporal_external_event_rejects_non_ref_payloads() -> None:
    """Keep external-event content outside Temporal Signal history."""

    with pytest.raises(ValueError, match="scheme-prefixed opaque ref"):
        build_temporal_external_event("complete", "inline event prose")
    with pytest.raises(ValueError, match="private sentinel"):
        build_temporal_external_event(
            "complete",
            "event-ref:alpha:complete:001",
            forbidden_sentinels=("event-ref:alpha:complete:001",),
        )


def test_temporal_start_payload_excludes_dispatch_identity() -> None:
    """Create Module lineage only when the Temporal workflow dispatches a module."""

    fixture = build_two_cell_conformance_fixture("temporal")
    payload = build_temporal_workflow_input(fixture.alpha)
    encoded_module_plan = str(payload["module_plan"])

    for dispatch_identity in (
        "module_run_id",
        "variant_id",
        "attempt_base_id",
        "execution_profile_ref",
    ):
        assert dispatch_identity not in encoded_module_plan
