"""Verify one installed Portable review's live Claude CLI progress without saving process data."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import time

import psutil


FIXTURE = Path(__file__).resolve().parent
CRITERION = "Declared liveness_probe is actually run once and exits zero; no other command or data resource is used."


def _alive_at_receipt(identity: tuple[int, float] | None) -> bool:
    if identity is None:
        return False
    try:
        process = psutil.Process(identity[0])
        return (process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                and process.create_time() == identity[1])
    except (psutil.Error, OSError):
        return False


def _review_is_reusable(entry: Path, record: dict) -> bool:
    if not record:
        return False
    sys.path.insert(0, str(entry.resolve().parents[1]))
    from software_delivery import engineering_review_input as inputs
    try:
        inputs._validate_plan_review(
            inputs.hashed_body("liveness-review", json.dumps(record, ensure_ascii=False)),
            record["semantic_input"]["code_design_basis"],
            record["semantic_input"]["acceptance_criteria"], inputs.INPUT_SCHEMA_PATH,
        )
        return True
    except (KeyError, TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="One real installed-entry liveness gate; no raw process retention")
    parser.add_argument("--root", type=Path, required=True, help="host root with registered Reviewer")
    parser.add_argument("--entry", type=Path, required=True, help="installed Portable engineering_review.py")
    parser.add_argument("--workflow-version", required=True, help="exact registered Reviewer version")
    parser.add_argument("--cli-path", type=Path, required=True, help="authenticated Claude CLI executable")
    parser.add_argument("--review-output", type=Path, required=True, help="new compact review JSON path")
    parser.add_argument("--verification-output", type=Path, required=True, help="new safe gate result JSON path")
    parser.add_argument("--run-timeout-seconds", type=int, default=1200)
    args = parser.parse_args(argv)
    if (args.review_output.exists() or args.verification_output.exists()
            or args.review_output == args.verification_output):
        parser.error("review and verification outputs must be different new paths")
    if args.run_timeout_seconds < 1:
        parser.error("run timeout must be positive")

    observations = {"lines": 0, "shapes": {}, "identity": None,
                    "request": None, "result": None, "finished": None,
                    "declared_started": False, "declared_finished": False}
    stdout_kept: list[str] = []

    def drain_stdout(stream) -> None:
        kept = 0
        while chunk := stream.read(4096):
            if kept < 8192:
                part = chunk[:8192 - kept]
                stdout_kept.append(part)
                kept += len(part)

    def drain_stderr(stream) -> None:
        while line := stream.readline(8192):
            observations["lines"] += 1
            arrived = time.monotonic()
            try:
                envelope = json.loads(line)
            except (TypeError, ValueError):
                shape = "non_json"
            else:
                if not isinstance(envelope, dict):
                    shape = "non_object"
                elif set(envelope) != {"runtime_process_progress"}:
                    shape = "error" if "error_type" in envelope else "other"
                else:
                    shape = "progress"
                    value = envelope["runtime_process_progress"]
                    if not isinstance(value, dict):
                        shape = "invalid_progress"
                    else:
                        trigger = value.get("update_trigger")
                        event = value.get("current_cli_event")
                        phase = event.get("phase") if isinstance(event, dict) else None
                        category = event.get("tool_category") if isinstance(event, dict) else None
                        command_id = event.get("command_id") if isinstance(event, dict) else None
                        if trigger == "process_started" and observations["identity"] is None:
                            try:
                                pid = value["process_id"]
                                observations["identity"] = (pid, psutil.Process(pid).create_time())
                            except (KeyError, TypeError, psutil.Error, OSError):
                                pass
                        if trigger == "process_finished" and observations["finished"] is None:
                            observations["finished"] = (arrived, value.get("elapsed_seconds"))
                        if phase == "declared_command_started" and command_id == "liveness_probe":
                            observations["declared_started"] = True
                        if phase == "declared_command_finished" and command_id == "liveness_probe":
                            observations["declared_finished"] = True
                        if (trigger == "event_received" and category == "declared_command"
                                and value.get("process_running") is True
                                and observations["identity"] is not None
                                and value.get("process_id") == observations["identity"][0]
                                and _alive_at_receipt(observations["identity"])):
                            fact = (arrived, value.get("elapsed_seconds"))
                            if phase == "tool_requested" and observations["request"] is None:
                                observations["request"] = fact
                            elif (phase == "tool_result_observed" and observations["request"] is not None
                                  and observations["result"] is None and arrived > observations["request"][0]):
                                observations["result"] = fact
            if shape != "progress":
                observations["shapes"][shape] = observations["shapes"].get(shape, 0) + 1

    with tempfile.TemporaryDirectory(prefix="installed-review-liveness-") as directory:
        command = [
            sys.executable, "-B", str(args.entry),
            "--plan", str(FIXTURE / "plan.md"),
            "--goal", "Validate a bounded live review progress fixture",
            "--change", "Review the one local declared liveness command; no project data",
            "--criterion", CRITERION,
            "--commands", str(FIXTURE / "commands.json"),
            "--output", str(args.review_output),
            "--root", str(args.root), "--workflow", "engineering_change_reviewer",
            "--version", args.workflow_version,
            "--transport", "claude_cli", "--model", "claude-opus-5-5", "--effort", "xhigh",
            "--run-timeout-seconds", str(args.run_timeout_seconds),
            "--cli-path", str(args.cli_path),
        ]
        process = subprocess.Popen(command, cwd=directory, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, bufsize=1)
        stdout_reader = Thread(target=drain_stdout, args=(process.stdout,), daemon=True)
        stderr_reader = Thread(target=drain_stderr, args=(process.stderr,), daemon=True)
        stdout_reader.start()
        stderr_reader.start()
        timed_out = False
        try:
            process.wait(timeout=args.run_timeout_seconds + 30)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        stdout_reader.join(timeout=2)
        stderr_reader.join(timeout=2)

    try:
        terminal = json.loads("".join(stdout_kept).strip())
    except (TypeError, ValueError):
        terminal = {}
    if not isinstance(terminal, dict):
        terminal = {}
    try:
        saved = json.loads(args.review_output.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    request, result, finished = (observations[key] for key in ("request", "result", "finished"))
    ordered = bool(request and result and finished
                   and request[0] < result[0] < finished[0]
                   and isinstance(request[1], (float, int))
                   and isinstance(result[1], (float, int))
                   and isinstance(finished[1], (float, int))
                   and request[1] < finished[1] and result[1] < finished[1])

    sys.path.insert(0, str(args.entry.resolve().parents[1]))
    from software_delivery import engineering_review_input as inputs
    allowed = set(inputs._PLAN_REVIEW_RUNTIME_IDENTITY) | {
        "status", "module_id", "review_purpose", "semantic_validation", "semantic_input",
        "output", "failure_class", "execution_budget", "failure_code", "provider_process", "command_evidence",
    }
    required = {"status", "module_id", "review_purpose", "semantic_validation", "semantic_input", "output",
                "failure_code", "provider_process", "command_evidence"}
    terminal_validation = terminal.get("output_validation") if isinstance(terminal.get("output_validation"), dict) else {}
    saved_validation = saved.get("semantic_validation") if isinstance(saved.get("semantic_validation"), dict) else {}
    saved_output = saved.get("output") if isinstance(saved.get("output"), dict) else {}
    checks = {
        "entry_exit_zero": process.returncode == 0 and not timed_out,
        "cli_identity_captured": observations["identity"] is not None,
        "live_request_received": request is not None,
        "live_correlated_result_received": result is not None,
        "declared_command_started": observations["declared_started"],
        "declared_command_finished": observations["declared_finished"],
        "events_before_process_finished": ordered,
        "owner_validation_passed": terminal_validation.get("status") == "passed"
                                   and saved_validation.get("status") == "passed",
        "review_verdict_passed": terminal.get("verdict") == "passed"
                                 and saved_output.get("verdict") == "passed",
        "projected_keyset_valid": required <= set(saved) <= allowed,
        "compact_command_evidence_valid": saved.get("command_evidence") == {
            "log_complete": True,
            "commands": [{"command_id": "liveness_probe", "disposition": "completed_exit_zero"}],
        },
        "compact_plan_review_reusable": _review_is_reusable(args.entry, saved),
    }
    verification = {
        "status": "passed" if all(checks.values()) else "failed_or_unverified",
        "checks": checks, "progress_line_count": observations["lines"],
        "stderr_shape_counts": observations["shapes"],
        "entry_exit_code": process.returncode,
        "review_result_sha256": (hashlib.sha256(args.review_output.read_bytes()).hexdigest()
                                 if args.review_output.exists() else None),
    }
    with args.verification_output.open("x", encoding="utf-8") as stream:
        json.dump(verification, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(verification, ensure_ascii=False))
    return 0 if verification["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
