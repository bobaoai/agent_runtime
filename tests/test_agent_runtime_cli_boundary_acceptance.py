"""Real CLI acceptance for the outer CLI boundary (plan section 8): the four usage patterns.

Every case goes through the public run_local_workflow_test with owned scratch
roots, tiny synthetic inputs and the host's normal CLI login. Nothing reads
credential contents or real databases. The MCP fixture is a temporary stdio
server (mcp FastMCP) written into the test's own folder; it reports only
whether it received FIXTURE_TOKEN, never the value.

Gates (all real_run, skipped otherwise):
  AGENT_RUNTIME_REAL_RUN=1
  AGENT_RUNTIME_CLAUDE_BIN=<native claude.exe>   for the Claude cases
  AGENT_RUNTIME_CODEX_BIN=<native codex.exe>     for the Codex cases
Optional: AGENT_RUNTIME_CLAUDE_MODEL (default claude-opus-5-5),
AGENT_RUNTIME_CODEX_MODEL (default gpt-6-astra), AGENT_RUNTIME_ACCEPTANCE_EFFORT
(default low), AGENT_RUNTIME_ACCEPTANCE_OUT (a folder that receives one JSON
fact file per case, including the exact CLI path, version and sha256).
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import uuid

import pytest

from agent_runtime import (
    Module, RuntimeModulePlugin, register_reviewer, register_runtime_module_plugin, run_local_workflow_test,
)
from agent_runtime.invocation import invocation_workspace_preparation as workspaces
from agent_runtime.invocation.invocation_host_platform_selection import HOST_PLATFORM
from agent_runtime.registry import RuntimeReleaseRegistry
from test_agent_runtime_module_authoring import _requirements, _task_project, SKILL_ID
from test_agent_runtime_reviewer_registration_cli import _source, MODULE_ID as REVIEWER_ID


REAL = os.environ.get("AGENT_RUNTIME_REAL_RUN") == "1"
CLAUDE_BIN = os.environ.get("AGENT_RUNTIME_CLAUDE_BIN")
CODEX_BIN = os.environ.get("AGENT_RUNTIME_CODEX_BIN")
CLAUDE = pytest.mark.skipif(not (REAL and CLAUDE_BIN), reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 and AGENT_RUNTIME_CLAUDE_BIN")
CODEX = pytest.mark.skipif(not (REAL and CODEX_BIN), reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 and AGENT_RUNTIME_CODEX_BIN")
MODELS = {"claude_cli": os.environ.get("AGENT_RUNTIME_CLAUDE_MODEL", "claude-opus-5-5"),
          "codex_cli": os.environ.get("AGENT_RUNTIME_CODEX_MODEL", "gpt-6-astra")}
EFFORT = os.environ.get("AGENT_RUNTIME_ACCEPTANCE_EFFORT", "low")
SENTINEL = "ACCEPTANCE-SENTINEL-41c9"
MODULE = "summarize_note"  # Fixture output schema: {"summary": string}.

FIXTURE_SERVER = '''
import os
from mcp.server.fastmcp import FastMCP

app = FastMCP("fixture")


@app.tool()
def fixture_echo(text: str) -> str:
    """Echo the text with a fixture marker and whether FIXTURE_TOKEN was received."""
    return "fixture:" + text + (" token_present" if os.environ.get("FIXTURE_TOKEN") else " token_absent")


@app.tool()
def fixture_numbers() -> dict:
    """Return two synthetic numbers."""
    return {"first": 1234, "second": 5678}


if __name__ == "__main__":
    app.run()
'''


def _identity(transport):
    program = Path(CLAUDE_BIN if transport == "claude_cli" else CODEX_BIN)
    version = subprocess.run([str(program), "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
    return {"cli_path": str(program), "cli_version": version,
            "cli_sha256": hashlib.sha256(program.read_bytes()).hexdigest(), "model": MODELS[transport], "effort": EFFORT}


def _save(name, transport, record, **facts):
    folder = os.environ.get("AGENT_RUNTIME_ACCEPTANCE_OUT")
    if not folder:
        return
    attempts = (record.get("execution_log") or {}).get("attempts") or [{}]
    trace = record.get("provider_trace") or {}
    document = {"case": name, **_identity(transport), "status": record.get("status"),
                "failure_detail": record.get("failure_detail"), "output": record.get("output"),
                "attempt_workspaces": record.get("attempt_workspaces"),
                "effective_configuration": trace.get("effective_configuration"),
                "permission_denials": trace.get("permission_denials"),
                "tool_calls": [{**{key: call.get(key) for key in ("tool_name", "status", "request")},
                                "response_tail": json.dumps(call.get("response"), default=str)[-1500:]}
                               for call in attempts[0].get("tool_calls") or []],
                "log_complete": attempts[0].get("complete"), "exit_code": trace.get("exit_code"),
                "stderr_tail": str(trace.get("stderr") or "")[-800:], "facts": facts}
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / f"{name}.json").write_text(json.dumps(document, indent=2, default=str), encoding="utf-8")


def _module_root(tmp_path, prompt, **requirements):
    source = _task_project(tmp_path / "source", module_id=MODULE)
    (source / ".claude/skills" / SKILL_ID / "runtime_modules" / MODULE / "prompt.md").write_text(prompt)
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id=MODULE,
                                      execution_requirements=_requirements(**requirements))
    workflow = module.to_workflow(module.export(module_version="v1")).export()
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin("acceptance", "v1", workflow.origin_bundle),
                                   root=root)
    return root


def _reviewer_root(tmp_path, prompt):
    source, registration = _source(tmp_path / "source")
    (registration.parent / "prompt.md").write_text(prompt)
    root = tmp_path / "host"
    register_reviewer(root, source_root=source, skill_id=SKILL_ID, module_id=REVIEWER_ID, module_version="v1")
    return root


def _run(root, transport, workflow=MODULE, **options):
    return run_local_workflow_test(root, workflow, input_payload={}, transport_kind=transport,
                                   model_id=MODELS[transport], reasoning_profile=EFFORT,
                                   cli_path=CLAUDE_BIN if transport == "claude_cli" else CODEX_BIN,
                                   run_timeout_seconds=options.pop("run_timeout_seconds", 900), **options)


def _fixture_config(root, tmp_path, transport):
    server = tmp_path / "fixture_server.py"
    server.write_text(FIXTURE_SERVER, encoding="utf-8")
    if transport == "claude_cli":
        config = {"command": sys.executable, "args": [str(server)], "env": {"FIXTURE_TOKEN": "${FIXTURE_TOKEN}"}}
    else:
        config = {"command": sys.executable, "args": [str(server)], "env_vars": ["FIXTURE_TOKEN"],
                  "default_tools_approval_mode": "approve"}
    (root / ".runtime").mkdir(parents=True, exist_ok=True)
    (root / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {transport: {
        "fixture": {"config": config, "environment_keys": ["FIXTURE_TOKEN"]}}}}))


def _calls(record):
    return record["execution_log"]["attempts"][0]["tool_calls"]


# --- Oracles over the public record (pure; exercised on synthetic records below) -----------------

_PYTEST_PASSED = re.compile(r"\b1 passed in \d+(?:\.\d+)?s\b")


def _tokens(text):
    """Whitespace tokens with surrounding quotes removed, case-folded (Windows paths compare case-insensitively)."""
    return [token.strip("'\"").casefold() for token in str(text).split()]


def _is_exactly(command, declared):
    """The recorded command is the declared one, token for token: nothing prefixed, appended or chained."""
    return _tokens(command) == _tokens(declared)


_SHELLS = {"pwsh", "pwsh.exe", "powershell", "powershell.exe", "bash", "bash.exe", "sh", "zsh"}


def _shell_words(text):
    """Quote-aware words (a quoted path with spaces stays one word), unquoted and case-folded."""
    return [word.strip("'\"").casefold() for word in shlex.split(str(text), posix=False)]


def _is_standalone_invocation(command, invocation):
    """Whether a recorded command runs exactly `invocation` and nothing else.

    Accepted shapes: the invocation itself (optionally after PowerShell's call operator `&`), or a
    single shell wrapper (pwsh/powershell/bash/sh with only dash options, then -Command, -c or -lc)
    whose whole script is that invocation. A script that chains, conditions, quotes or echoes the
    invocation is not accepted; this is a bounded shape check, not a shell interpreter.
    """
    text = str(command).replace("\\\\", "\\").strip()
    wanted = _shell_words(invocation)

    def alone(script):
        try:
            words = _shell_words(script.strip().strip("'\"").strip())
        except ValueError:  # Unbalanced quotes: not a recognizable standalone invocation.
            return False
        return words in (wanted, ["&", *wanted])

    if alone(text):
        return True
    for flag in (" -Command ", " -lc ", " -c "):
        if flag in text:
            prefix, script = text.split(flag, 1)
            try:
                program = _shell_words(prefix)
            except ValueError:
                return False
            return (bool(program) and re.split(r"[\\/]", program[0])[-1] in _SHELLS
                    and all(word.startswith("-") for word in program[1:]) and alone(script))
    return False


def _declared_pytest_passes(record, command):
    """Completed Bash calls that ran exactly the declared pytest command and returned pytest's own passing output.

    The Bash result must not be an error or interrupted, and its stdout must carry pytest -q's
    progress line and summary. An echo, a chained command or another program does not count.
    """
    passes = []
    for call in _calls(record):
        if (call.get("tool_name") != "Bash" or call.get("status") != "completed"
                or not _is_exactly((call.get("request") or {}).get("command", ""), command)):
            continue
        response = call.get("response") or {}
        block, result = response.get("tool_result") or {}, response.get("tool_use_result") or {}
        stdout = str(result.get("stdout", ""))
        if not block.get("is_error") and not result.get("interrupted") and "[100%]" in stdout and _PYTEST_PASSED.search(stdout):
            passes.append(call)
    return passes


READBACK_SCRIPT = '''import hashlib, sys
from pathlib import Path
body = Path(sys.argv[1]).read_bytes()
print("READBACK sha256=" + hashlib.sha256(body).hexdigest())
print(body.decode("utf-8", "replace"))
'''


def _verified_readbacks(record, saved_bytes, invocation):
    """Completed runs of the declared reader invocation, after the fixture MCP call, that hashed the saved file.

    The test-owned readback.py prints the SHA-256 of the bytes it read; the command must be that
    declared invocation on its own (not chained, conditional, echoed or quoted) and its output
    must carry the digest of the final numbers.json.
    """
    calls = _calls(record)
    fetched = next((index for index, call in enumerate(calls) if str(call.get("tool_name", "")).endswith("fixture_numbers")
                    and call.get("status") == "completed"
                    and all(value in json.dumps(call.get("response")) for value in ("1234", "5678"))), None)
    if fetched is None:
        return []
    digest = "READBACK sha256=" + hashlib.sha256(saved_bytes).hexdigest()
    readbacks = []
    for call in calls[fetched + 1:]:
        response = call.get("response") or {}
        if (call.get("tool_name") == "command_execution" and call.get("status") == "completed"
                and response.get("exit_code") == 0
                and _is_standalone_invocation((call.get("request") or {}).get("command", ""), invocation)
                and digest in str(response.get("aggregated_output", ""))):
            readbacks.append(call)
    return readbacks


# Claude's own message when a saved Profile requires its sandbox and the host cannot provide it.
_CLAUDE_SANDBOX_REFUSAL = re.compile(r"sandbox required but unavailable|Refusing to start without a working sandbox")
_COMMAND_REFUSAL = re.compile(r"(?i)permission|denied|not permitted|sandbox")


def _saved_strict_shell_outcome(record, command):
    """Claude's own attributed outcome for the requested command under a saved strict denied+Shell Profile.

    Returns one of:
    - claude_refused_to_start_without_its_sandbox: a provider failure with a nonzero exit, Claude's
      own sandbox-required refusal on stderr, and no Bash call at all;
    - claude_ran_the_requested_command: the call completed and the exact requested command's Bash
      result carries its output;
    - claude_refused_the_requested_command: the call completed and the exact requested command was
      refused (listed in Claude's permission denials, or an error result whose text is a refusal).
    Anything else, including another command's failure or an unexplained failure, is None.
    """
    trace = record.get("provider_trace") or {}
    detail = record.get("failure_detail") or {}
    bash = [call for call in _calls(record) if call.get("tool_name") == "Bash"]
    if (record.get("status") == "failed" and not bash and detail.get("failure_class") == "provider"
            and detail.get("failure_code") == "claude_cli_result_missing_or_invalid"
            and trace.get("exit_code") not in (0, None) and _CLAUDE_SANDBOX_REFUSAL.search(str(trace.get("stderr") or ""))):
        return "claude_refused_to_start_without_its_sandbox"
    if record.get("status") != "completed":
        return None
    denied = [item for item in trace.get("permission_denials") or []
              if _is_exactly(((item or {}).get("tool_input") or {}).get("command", ""), command)]
    for call in bash:
        if not _is_exactly((call.get("request") or {}).get("command", ""), command):
            continue
        response = call.get("response") or {}
        block, result = response.get("tool_result") or {}, response.get("tool_use_result") or {}
        if call.get("status") == "completed" and not block.get("is_error") and "DENIED_PROFILE_ok" in str(result.get("stdout", "")):
            return "claude_ran_the_requested_command"
        if denied or (call.get("status") == "failed" and _COMMAND_REFUSAL.search(str(block.get("content", "")))):
            return "claude_refused_the_requested_command"
    return None


def _attempt_row(record):
    (row,) = record["attempt_workspaces"]
    return row


def _expire_and_clean(record, *, delay_seconds=5):
    """Host retention: query keeps the fresh folder; cleanup after expiry deletes it."""
    row = _attempt_row(record)
    folder = Path(row["workspace_path"])
    facts = {row["attempt_id"]: workspaces.AttemptWorkspaceRetentionFacts(
        execution_ended=True, process_stop_confirmed=True, archive_complete=True, retention_hold=False)}
    seal = json.loads((folder / ".agent_runtime_attempt_seal.json").read_text())
    sealed_at = datetime.fromisoformat(seal["effective_at_utc"][:-1] + "+00:00")
    later = (sealed_at + timedelta(seconds=delay_seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    (queried,) = workspaces.maintain_attempt_workspaces(workspace_root=folder.parent, facts=facts,
        retention_seconds=3600, observed_at_utc=later, intent="query")
    (cleaned,) = workspaces.maintain_attempt_workspaces(workspace_root=folder.parent, facts=facts,
        retention_seconds=1, observed_at_utc=later, intent="cleanup")
    return queried, cleaned, folder


def _package(folder, *, broken):
    folder.mkdir(parents=True)
    (folder / "calc.py").write_text("def add(a, b):\n    return a %s b\n" % ("-" if broken else "+"))
    (folder / "test_calc.py").write_text("from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n")
    return folder


def _material_resources(tmp_path, files):
    materials = tmp_path / "materials"
    materials.mkdir()
    rows = []
    for name, body in files.items():
        (materials / name).parent.mkdir(parents=True, exist_ok=True)
        (materials / name).write_bytes(body)
        rows.append({"relative_path": name, "sha256": hashlib.sha256(body).hexdigest(), "executable": False})
    path = tmp_path / "resources.json"
    path.write_text(json.dumps({"material_root": str(materials), "material_files": rows,
                                "read_only_dependencies": [], "commands": []}))
    return path


_TOOL_FREE = dict(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="none", max_attempts=1)


# --- Claude ---------------------------------------------------------------------------------

@pytest.mark.real_run
@CLAUDE
def test_r1_claude_tool_free_run_is_retained_sealed_and_cleaned_by_policy(tmp_path):
    root = _module_root(tmp_path, "Return the summary 'R1 acceptance'. Use no tools.\n", **_TOOL_FREE)
    record = _run(root, "claude_cli")
    assert record["status"] == "completed", record["failure_detail"]
    row = _attempt_row(record)
    assert row["sealed"] and Path(row["archive_path"]).is_file()
    queried, cleaned, folder = _expire_and_clean(record)
    _save("R1_claude_tool_free_retention", "claude_cli", record, query=str(queried), cleanup=str(cleaned))
    assert (queried.outcome, queried.retained_reasons) == ("retained", ("retention_not_expired",))
    assert cleaned.outcome == "deleted" and not folder.exists()


@pytest.mark.real_run
@CLAUDE
def test_r2_claude_reads_verified_materials_and_cleanup_removes_read_only_copies(tmp_path):
    marker = "MATERIAL_MARKER_7d1"
    resources = _material_resources(tmp_path, {"notes/note.txt": f"{marker}\nsecond line\n".encode()})
    root = _module_root(tmp_path, "Read the material file notes/note.txt with your file tools and return its exact "
                        "first line as the summary.\n", tool_policy=("read", "search"), attempt_workspace_policy="none",
                        network_policy="provider_managed")
    record = _run(root, "claude_cli", resources_path=resources)
    assert record["status"] == "completed", record["failure_detail"]
    assert marker in record["output"]["summary"]
    assert record["provider_trace"]["material_sha256"]
    queried, cleaned, folder = _expire_and_clean(record)
    _save("R2_claude_materials", "claude_cli", record, cleanup=str(cleaned))
    assert cleaned.outcome == "deleted" and not folder.exists()


@pytest.mark.real_run
@CLAUDE
def test_r3_claude_runs_a_permitted_shell_command(tmp_path):
    root = _module_root(tmp_path, "Run exactly this shell command once: printf 'PERMITTED_%s' ok\nReturn its output "
                        "as the summary.\n", tool_policy=("read", "shell"), network_policy="provider_managed")
    record = _run(root, "claude_cli", command_permits=("printf:*",))
    _save("R3_claude_permit_printf", "claude_cli", record)
    assert record["status"] == "completed", record["failure_detail"]
    bash = [call for call in _calls(record) if call["tool_name"] == "Bash"]
    assert any("printf" in json.dumps(call["request"]) and call["status"] == "completed" for call in bash)
    assert "PERMITTED_ok" in record["output"]["summary"]
    assert record["provider_trace"]["effective_configuration"]["allowed_rules"][-1] == "Bash(printf:*)"


@pytest.mark.real_run
@CLAUDE
def test_r4_claude_mcp_only_module_uses_the_declared_connection_without_leaking_its_value(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    root = _module_root(tmp_path, "Call the MCP tool fixture_echo once with text 'R4'. Return its exact result as the "
                        "summary.\n", tool_policy=("mcp__fixture",), attempt_workspace_policy="none",
                        network_policy="provider_managed")
    _fixture_config(root, tmp_path, "claude_cli")
    record = _run(root, "claude_cli")
    plain_root = _module_root(tmp_path / "plain", "Return the summary 'no tools used'.\n", tool_policy=("read", "search"),
                              attempt_workspace_policy="none", network_policy="provider_managed")
    (plain_root / ".runtime" / "config.json").write_text((root / ".runtime" / "config.json").read_text())
    plain = _run(plain_root, "claude_cli")
    _save("R4_claude_mcp_only", "claude_cli", record, plain_module_tools=plain["provider_trace"]["effective_configuration"]["tools"])
    assert record["status"] == "completed", record["failure_detail"]
    assert any(call["tool_name"] == "mcp__fixture__fixture_echo" and call["status"] == "completed" for call in _calls(record))
    assert "fixture:R4" in record["output"]["summary"] and "token_present" in record["output"]["summary"]
    effective = record["provider_trace"]["effective_configuration"]
    assert [entry["name"] for entry in effective["mcp_connections"]] == ["fixture"]
    assert SENTINEL not in json.dumps(record, default=str)
    assert plain["status"] == "completed" and not any("mcp__" in tool for tool in plain["provider_trace"]["effective_configuration"]["tools"])


@pytest.mark.real_run
@CLAUDE
def test_r5_claude_saved_denied_shell_reports_claudes_own_result(tmp_path):
    """An explicitly saved strict Profile (network denied with Shell) reaches Claude unchanged.

    Claude decides whether it can honor it; Runtime reports that outcome with its attribution.
    This is not an ordinary-call default and asserts no Runtime sandbox.
    """
    command = "printf 'DENIED_PROFILE_%s' ok"
    root = _module_root(tmp_path, f"Run exactly this shell command once: {command}\nReturn its output, "
                        "or the exact refusal you received, as the summary.\n", tool_policy=("read", "shell"),
                        network_policy="denied")
    record = _run(root, "claude_cli", command_permits=("printf:*",))
    outcome = _saved_strict_shell_outcome(record, command)
    _save("R5_claude_saved_denied_shell", "claude_cli", record, settings=record["provider_trace"].get("settings"),
          outcome=outcome)
    sandbox = record["provider_trace"]["settings"]["sandbox"]
    assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True  # Passed to Claude unchanged.
    assert outcome is not None, (record["status"], record.get("failure_detail"), record["provider_trace"].get("stderr"))


@pytest.mark.real_run
@CLAUDE
def test_r5a_claude_coding_pattern_edits_the_project_directory_which_survives_cleanup(tmp_path):
    project = _package(tmp_path / "project", broken=True)
    interpreter = Path(sys.executable).as_posix()  # The exact interpreter selected for this host (with pytest).
    command = f"{interpreter} -m pytest -q -p no:cacheprovider"
    root = _module_root(tmp_path, "The current directory is a small Python project whose test fails. Use the Read and "
                        "Edit file tools to fix calc.py so test_calc.py passes. The only pre-approved shell command is "
                        f"exactly: {command}\nRun that command once, as written (no cd, no "
                        "other commands), and return the final pytest summary line as the summary.\n",
                        tool_policy=("edit", "read", "search", "shell"),
                        attempt_workspace_policy="explicit_project_read_write", network_policy="provider_managed")
    record = _run(root, "claude_cli", project_directory=project, command_permits=(f"{interpreter} -m pytest:*",))
    passes = _declared_pytest_passes(record, command) if record["status"] == "completed" else []
    queried, cleaned, folder = _expire_and_clean(record) if passes else (None, None, None)
    _save("R5a_claude_coding", "claude_cli", record, calc=(project / "calc.py").read_text(), cleanup=str(cleaned),
          interpreter=interpreter, declared_pytest_passes=len(passes))
    assert record["status"] == "completed", record["failure_detail"]
    assert "a + b" in (project / "calc.py").read_text()
    assert passes, "the declared interpreter's pytest run must itself pass in its actual Bash result"
    assert cleaned.outcome == "deleted" and not folder.exists() and (project / "test_calc.py").is_file()


@pytest.mark.real_run
@CLAUDE
def test_r6_claude_short_deadline_stops_the_process_and_releases_its_folder(tmp_path):
    root = _module_root(tmp_path, "Write a 3000-word essay about rivers, then summarize it.\n", **_TOOL_FREE)
    record = _run(root, "claude_cli", run_timeout_seconds=8)
    trace = record["provider_trace"]
    temporary = (trace.get("environment") or {}).get("TMPDIR")
    _save("R6_claude_timeout", "claude_cli", record, stop_reason=trace.get("stop_reason"), cleanup_error=trace.get("cleanup_error"),
          crt_exists=bool(temporary) and Path(temporary).exists())
    assert record["status"] == "failed" and record["failure_class"] == "timeout"
    assert trace.get("cleanup_error") is None and temporary and not Path(temporary).exists()


@pytest.mark.real_run
@CLAUDE
def test_r7_claude_injected_stop_failure_retains_crt_and_leaves_the_folder_unsealed(tmp_path, monkeypatch):
    original = HOST_PLATFORM.stop_cli_process

    def uncertain(process):
        original(process)
        if "-p" in list(getattr(process, "args", ())):
            raise RuntimeError("injected stop confirmation failure")

    monkeypatch.setattr(HOST_PLATFORM, "stop_cli_process", uncertain)
    root = _module_root(tmp_path, "Return the summary 'R7'. Use no tools.\n", **_TOOL_FREE)
    record = _run(root, "claude_cli")
    monkeypatch.setattr(HOST_PLATFORM, "stop_cli_process", original)
    trace = record["provider_trace"]
    temporary = Path((trace.get("environment") or {})["TMPDIR"])
    row = _attempt_row(record)
    _save("R7_claude_stop_failure", "claude_cli", record, crt_retained=temporary.exists(), crt=str(temporary))
    assert record["failure_detail"]["failure_code"] == "claude_cli_cleanup_failed"
    assert "retained at" in record["failure_detail"]["provider_error_message"] and temporary.is_dir()
    assert (row["sealed"], row["seal_skipped_reason"]) == (False, "process_stop_unconfirmed")
    HOST_PLATFORM.remove_private_directory(temporary, HOST_PLATFORM.private_directory_identity(temporary))


@pytest.mark.real_run
@CLAUDE
def test_r8_claude_mcp_data_chain_saves_reads_back_and_writes_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    output = tmp_path / "output"
    output.mkdir()
    root = _module_root(tmp_path, "Call the MCP tool fixture_numbers once. Use Write to save its two numbers to "
                        "numbers.json in the current directory as {\"first\": <first>, \"second\": <second>}. Read "
                        "numbers.json back with Read. Then use Write to create summary.json containing {\"sum\": <first + "
                        "second>}. Return 'sum=<value>' as the summary.\n", tool_policy=("edit", "mcp__fixture", "read"),
                        attempt_workspace_policy="explicit_project_read_write", network_policy="provider_managed")
    _fixture_config(root, tmp_path, "claude_cli")
    record = _run(root, "claude_cli", project_directory=output)
    queried, cleaned, folder = _expire_and_clean(record) if record["status"] == "completed" else (None, None, None)
    names = [call["tool_name"] for call in _calls(record)]
    _save("R8_claude_mcp_data_chain", "claude_cli", record, tool_names=names,
          files={path.name: path.read_text() for path in output.iterdir()}, cleanup=str(cleaned))
    assert record["status"] == "completed", record["failure_detail"]
    assert "mcp__fixture__fixture_numbers" in names and "Write" in names and "Read" in names
    assert json.loads((output / "numbers.json").read_text()) == {"first": 1234, "second": 5678}
    assert json.loads((output / "summary.json").read_text()) == {"sum": 6912}
    assert cleaned.outcome == "deleted" and not folder.exists() and (output / "summary.json").is_file()
    assert SENTINEL not in json.dumps(record, default=str)


_REVIEW = ("This is a Runtime acceptance check of a small Python package in your materials (source/calc.py, "
           "source/test_calc.py). Run exactly: python -m pytest -q -p no:cacheprovider materials/source\nThen also run "
           "exactly: python -c \"print('UNLISTED_EXECUTION')\"\nIf a command is refused, do not retry it another way. "
           "Then return verdict 'passed', one check_result with check_id 'steps', disposition 'passed', a one-sentence "
           "assessment and finding_ids [], findings [] and safe_next_step 'none'.\n")


def _review(tmp_path, permits):
    package = _package(tmp_path / "pkg", broken=False)
    resources = _material_resources(tmp_path, {name: (package / name).read_bytes() for name in ("calc.py", "test_calc.py")})
    root = _reviewer_root(tmp_path, _REVIEW)
    return _run(root, "claude_cli", workflow=REVIEWER_ID, resources_path=resources, command_permits=permits)


def _results_text(record):
    return json.dumps([call.get("response") for call in _calls(record)], default=str)


@pytest.mark.real_run
@CLAUDE
def test_r9_claude_reviewer_permit_runs_pytest_and_denies_unlisted_python(tmp_path):
    record = _review(tmp_path, ("python -m pytest:*",))
    denials = record["provider_trace"].get("permission_denials") or []
    _save("R9_claude_reviewer_permit", "claude_cli", record)
    assert record["status"] == "completed", record["failure_detail"]
    assert any(call["tool_name"] == "Bash" and "pytest" in json.dumps(call["request"]) and call["status"] == "completed"
               for call in _calls(record))
    assert any("UNLISTED_EXECUTION" in json.dumps(denial) for denial in denials)
    assert "UNLISTED_EXECUTION\\n" not in _results_text(record) and "UNLISTED_EXECUTION\\r\\n" not in _results_text(record)
    effective = record["provider_trace"]["effective_configuration"]
    assert effective["command_scope"] == "native_read_only_plus_permits" and effective["command_permits"] == ["python -m pytest:*"]


@pytest.mark.real_run
@CLAUDE
def test_r9b_claude_reviewer_without_permits_denies_unlisted_python(tmp_path):
    record = _review(tmp_path, ())
    denials = record["provider_trace"].get("permission_denials") or []
    _save("R9b_claude_reviewer_no_permits", "claude_cli", record)
    assert any("UNLISTED_EXECUTION" in json.dumps(denial) for denial in denials)
    assert "UNLISTED_EXECUTION\\n" not in _results_text(record) and "UNLISTED_EXECUTION\\r\\n" not in _results_text(record)
    effective = record["provider_trace"]["effective_configuration"]
    assert effective["command_permits"] == [] and not any(rule.startswith("Bash") for rule in effective["allowed_rules"])


# --- Codex ----------------------------------------------------------------------------------

@pytest.mark.real_run
@CODEX
def test_c1_codex_tool_free_run_with_host_servers_disabled(tmp_path):
    root = _module_root(tmp_path, "Return the summary 'C1 acceptance'. Use no tools.\n", **_TOOL_FREE)
    record = _run(root, "codex_cli")
    effective = record["provider_trace"].get("effective_configuration") or {}
    _save("C1_codex_tool_free", "codex_cli", record, host_mcp_disabled=effective.get("host_mcp_disabled"))
    assert record["status"] == "completed", record["failure_detail"]
    assert effective["tools"] == [] and "host_mcp_disabled" in effective
    assert _attempt_row(record)["sealed"]


def _host_style_root(name):
    """An ordinary host folder that inherits the user-profile access chain, like a project root.

    pytest creates tmp_path with mode 0o700 (owner-only on Windows with Python 3.13),
    the host temp folder carries its own protected access list, and an app-package
    session may redirect application-data writes into its private cache, so none
    reflects a host project folder: a CLI sandbox identity that the host set up on
    the user profile cannot traverse them. Codex acceptance therefore uses
    Path.mkdir with the default mode next to this checkout (where host projects
    live), or under AGENT_RUNTIME_ACCEPTANCE_ROOT when set. The folder is kept as evidence.
    """
    parent = Path(os.environ.get("AGENT_RUNTIME_ACCEPTANCE_ROOT") or Path(__file__).resolve().parents[2])
    root = parent / "agent_runtime_acceptance" / f"{name}_{uuid.uuid4().hex[:12]}"
    root.mkdir(parents=True)
    return root, {"fixture_root": str(root), "fixture_kind": "ordinary host project folder: Path.mkdir default mode "
                  "next to the checkout, permissions inherited from its parent (not pytest's owner-only tmp_path, "
                  "the protected host temp folder or redirected application data)"}


@pytest.mark.real_run
@CODEX
def test_c2_codex_workspace_reads_materials_through_its_shell_and_is_cleaned():
    base, provenance = _host_style_root("c2")
    marker = "CODEX_MATERIAL_3b8"
    resources = _material_resources(base, {"notes/note.txt": f"{marker}\n".encode()})
    root = _module_root(base, "Use your shell to read ../materials/source/notes/note.txt and write its first line "
                        "into draft.txt in the current directory. Return that line as the summary.\n",
                        tool_policy=("read", "search", "shell"), network_policy="provider_managed")
    record = _run(root, "codex_cli", resources_path=resources)
    row = _attempt_row(record)
    draft = Path(row["workspace_path"]) / "work" / "scratch" / "draft.txt"
    try:  # Parent readback of what the sandboxed shell wrote.
        draft_text = draft.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        draft_text = f"<unreadable: {type(exc).__name__}: {exc}>"
    profile = record["provider_trace"].get("codex_permission_profile")
    scenario = (record["status"] == "completed" and marker in record["output"]["summary"]
                and draft_text.strip() == marker)
    # Clean only a completed scenario; a failed one keeps its Attempt folder as found for diagnosis.
    queried, cleaned, folder = _expire_and_clean(record) if scenario else (None, None, None)
    _save("C2_codex_workspace_materials", "codex_cli", record, cleanup=str(cleaned), draft=draft_text,
          permission_profile=profile, attempt_folder_kept=None if scenario else row["workspace_path"], **provenance)
    assert record["status"] == "completed", record["failure_detail"]
    assert marker in record["output"]["summary"] and draft_text.strip() == marker
    assert "read" in profile["filesystem"].values()
    assert cleaned.outcome == "deleted" and not folder.exists()


@pytest.mark.real_run
@CODEX
def test_c3_codex_coding_pattern_edits_its_project_directory_and_runs_its_test():
    base, provenance = _host_style_root("c3")
    project = _package(base / "project", broken=True)
    interpreter = str(Path(sys.executable))  # The interpreter already selected for this host (with pytest).
    command = f"{interpreter} -m pytest -q -p no:cacheprovider"
    root = _module_root(base, "The current directory is a small Python project whose test fails. Fix calc.py so "
                        f"test_calc.py passes. Then run exactly this command once: {command}\nReturn the final pytest "
                        "summary line as the summary.\n", tool_policy=("edit", "read", "search", "shell"),
                        attempt_workspace_policy="explicit_project_read_write", network_policy="provider_managed")
    record = _run(root, "codex_cli", project_directory=project)

    def command_text(call):  # The recorded command may carry escaped (doubled) backslashes.
        return str((call.get("request") or {}).get("command", "")).replace("\\\\", "\\").lower()

    runs = [call for call in _calls(record) if call["tool_name"] == "command_execution"
            and interpreter.lower() in command_text(call) and "pytest" in command_text(call)]
    scenario = (record["status"] == "completed" and "a + b" in (project / "calc.py").read_text()
                and any(call["status"] == "completed" and "1 passed" in json.dumps(call.get("response"))
                        for call in runs))
    # Clean only a completed scenario; a failed one keeps its Attempt folder as found for diagnosis.
    queried, cleaned, folder = _expire_and_clean(record) if scenario else (None, None, None)
    _save("C3_codex_coding", "codex_cli", record, calc=(project / "calc.py").read_text(), cleanup=str(cleaned),
          interpreter=interpreter, pytest_calls=[{"status": call["status"], "response": call.get("response")}
                                                 for call in runs],
          attempt_folder_kept=None if scenario else _attempt_row(record)["workspace_path"], **provenance)
    assert record["status"] == "completed", record["failure_detail"]
    assert "a + b" in (project / "calc.py").read_text()
    assert runs and any(call["status"] == "completed" and "1 passed" in json.dumps(call.get("response"))
                        for call in runs), "the exact selected interpreter's pytest run must succeed"
    assert "passed" in record["output"]["summary"]
    assert cleaned.outcome == "deleted" and (project / "test_calc.py").is_file()

@pytest.mark.real_run
@CODEX
def test_c4_codex_mcp_only_uses_the_declared_fixture_with_shell_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    root = _module_root(tmp_path, "Call the MCP tool fixture_echo once with text 'C4'. Return its exact result as the "
                        "summary.\n", tool_policy=("mcp__fixture",), attempt_workspace_policy="none",
                        network_policy="provider_managed")
    _fixture_config(root, tmp_path, "codex_cli")
    record = _run(root, "codex_cli")
    effective = record["provider_trace"].get("effective_configuration") or {}
    _save("C4_codex_mcp_only", "codex_cli", record)
    assert record["status"] == "completed", record["failure_detail"]
    assert "fixture:C4" in record["output"]["summary"] and "token_present" in record["output"]["summary"]
    assert effective["file_io"] == "unavailable_without_shell" and "fixture" not in effective["host_mcp_disabled"]
    assert SENTINEL not in json.dumps(record, default=str)


@pytest.mark.real_run
@CODEX
def test_c5_codex_mcp_data_chain_with_shell(monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    base, provenance = _host_style_root("c5")
    output = base / "output"
    output.mkdir()
    (output / "readback.py").write_text(READBACK_SCRIPT, encoding="utf-8")  # Test-owned reader in the project.
    interpreter = str(Path(sys.executable))
    root = _module_root(base, "Call the MCP tool fixture_numbers once. Save its two numbers to numbers.json in the "
                        "current directory as {\"first\": <first>, \"second\": <second>}. Then read numbers.json back by "
                        "running this exact command on its own, as a separate shell command with nothing else in it: "
                        f"{interpreter} readback.py numbers.json\nUse the numbers it prints to create "
                        "summary.json containing {\"sum\": <first + second>}. Return 'sum=<value>' as the summary.\n",
                        tool_policy=("edit", "mcp__fixture", "read", "shell"),
                        attempt_workspace_policy="explicit_project_read_write", network_policy="provider_managed")
    _fixture_config(root, base, "codex_cli")
    record = _run(root, "codex_cli", project_directory=output)
    files = {}
    for path in output.iterdir():  # Parent readback; an unreadable file is recorded, not hidden.
        try:
            files[path.name] = path.read_text()
        except OSError as exc:
            files[path.name] = f"<unreadable: {type(exc).__name__}: {exc}>"
    saved = (output / "numbers.json").read_bytes() if (output / "numbers.json").is_file() else b""
    readbacks = _verified_readbacks(record, saved, f"{interpreter} readback.py numbers.json")
    _save("C5_codex_mcp_data_chain", "codex_cli", record, files=files, verified_readbacks=len(readbacks),
          interpreter=interpreter, **provenance)
    assert record["status"] == "completed", record["failure_detail"]
    assert json.loads(files["numbers.json"]) == {"first": 1234, "second": 5678}
    assert readbacks, "the fixture MCP result must be saved and then actually read back from numbers.json"
    assert json.loads(files["summary.json"]) == {"sum": 6912}
    assert SENTINEL not in json.dumps(record, default=str)

@pytest.mark.real_run
@CODEX
def test_c6_codex_injected_stop_failure_retains_its_schema_folder(tmp_path, monkeypatch):
    original = HOST_PLATFORM.stop_cli_process

    def uncertain(process):
        original(process)
        if "exec" in list(getattr(process, "args", ())):
            raise RuntimeError("injected stop confirmation failure")

    monkeypatch.setattr(HOST_PLATFORM, "stop_cli_process", uncertain)
    root = _module_root(tmp_path, "Return the summary 'C6'. Use no tools.\n", **_TOOL_FREE)
    record = _run(root, "codex_cli")
    monkeypatch.setattr(HOST_PLATFORM, "stop_cli_process", original)
    row = _attempt_row(record)
    _save("C6_codex_stop_failure", "codex_cli", record)
    assert record["failure_detail"]["failure_code"] == "codex_cli_cleanup_failed"
    assert "retained at" in record["failure_detail"]["provider_error_message"]
    assert (row["sealed"], row["seal_skipped_reason"]) == (False, "process_stop_unconfirmed")


# --- The live oracles on synthetic public records (no CLI, model or process) --------------------

def _synthetic(calls, **fields):
    return {"execution_log": {"attempts": [{"tool_calls": calls}]}, **fields}


def _bash(command, *, status="completed", stdout="", is_error=False, interrupted=False):
    return {"tool_name": "Bash", "status": status, "request": {"command": command},
            "response": {"tool_result": {"type": "tool_result", "is_error": is_error, "content": stdout},
                         "tool_use_result": {"stdout": stdout, "stderr": "", "interrupted": interrupted}}}


_PYTEST_OUTPUT = ".                                                                        [100%]\r\n1 passed in 0.01s"


@pytest.mark.deterministic
def test_r5a_oracle_accepts_only_the_declared_pytest_programs_own_passing_result():
    interpreter = "C:/env/Scripts/python.exe"
    command = f"{interpreter} -m pytest -q -p no:cacheprovider"
    assert len(_declared_pytest_passes(_synthetic([_bash(command, stdout=_PYTEST_OUTPUT)]), command)) == 1
    rejected = {
        "echo of the command and summary": _bash(f'echo "{command} [100%] 1 passed in 0.01s"', stdout="[100%] 1 passed in 0.01s"),
        "chained echo": _bash(f"{command}; echo '[100%] 1 passed in 0.01s'", stdout=_PYTEST_OUTPUT),
        "other program with the same prefix": _bash(f"{interpreter} -m pytest_fake -q -p no:cacheprovider",
                                                    stdout=_PYTEST_OUTPUT),
        "error result": _bash(command, stdout=_PYTEST_OUTPUT, is_error=True),
        "interrupted": _bash(command, stdout=_PYTEST_OUTPUT, interrupted=True),
        "failing pytest": _bash(command, stdout=".                [100%]\r\n1 failed in 0.02s"),
        "not completed": _bash(command, status="failed", stdout=_PYTEST_OUTPUT),
        "other interpreter": _bash("python -m pytest -q -p no:cacheprovider", stdout=_PYTEST_OUTPUT),
    }
    for name, call in rejected.items():
        assert _declared_pytest_passes(_synthetic([call]), command) == [], name


def _command(command, output, *, exit_code=0, status="completed"):
    return {"tool_name": "command_execution", "status": status, "request": {"command": command},
            "response": {"command": command, "aggregated_output": output, "exit_code": exit_code, "status": status}}


@pytest.mark.deterministic
def test_c5_oracle_requires_the_readers_hash_of_the_saved_file_after_the_fixture_call():
    saved = b'{"first": 1234, "second": 5678}\n'
    digest = "READBACK sha256=" + hashlib.sha256(saved).hexdigest()
    fetched = {"tool_name": "fixture_numbers", "status": "completed", "request": {},
               "response": {"first": 1234, "second": 5678}}
    invocation = r"C:\env\Scripts\python.exe readback.py numbers.json"
    # The recorded shape of the live Codex run: a pwsh wrapper (quoted path with a space, doubled
    # backslashes) whose whole script is the reader invocation.
    wrapped = (r'"C:\\Program Files\\Codex\\pwsh.exe" -NoProfile -Command '
               r'"C:\\env\\Scripts\\python.exe readback.py numbers.json"')
    reader = _command(wrapped, digest + "\n" + saved.decode())
    assert len(_verified_readbacks(_synthetic([fetched, reader]), saved, invocation)) == 1
    for accepted in (invocation, f"& {invocation}", f"/bin/bash -lc '{invocation}'"):
        assert len(_verified_readbacks(_synthetic([fetched, _command(accepted, digest)]), saved, invocation)) == 1, accepted
    rejected = {
        "echo mentioning the file and numbers": [fetched, _command('echo "numbers.json 1234 5678"', "numbers.json 1234 5678")],
        "echo of the reader with the expected digest": [fetched, _command(f"echo {invocation}", digest)],
        "reader skipped by a failed && condition": [fetched, _command(
            f'cmd /c exit 1 && {invocation}; Write-Output "{digest}"', digest)],
        "reader only quoted in Write-Output text": [fetched, _command(
            f'Write-Output "example; {invocation}"; Write-Output "{digest}"', digest)],
        "reader chained after the save inside the wrapper": [fetched, _command(
            r'"C:\pwsh\pwsh.exe" -NoProfile -Command "Set-Content numbers.json $data; ' + invocation + '"', digest)],
        "reader inside a wrapper that is not a shell": [fetched, _command(f'Write-Output x -Command "{invocation}"', digest)],
        "reader with extra arguments": [fetched, _command(invocation + " extra.json", digest)],
        "hash of other bytes": [fetched, _command(invocation, "READBACK sha256=" + "0" * 64)],
        "reader before the fixture call": [reader, fetched],
        "reader failed": [fetched, _command(invocation, digest, exit_code=1)],
        "no fixture call": [reader],
    }
    for name, calls in rejected.items():
        assert _verified_readbacks(_synthetic(calls), saved, invocation) == [], name


@pytest.mark.deterministic
def test_r5_oracle_requires_claudes_own_attributed_outcome():
    command = "printf 'DENIED_PROFILE_%s' ok"
    refusal = {"status": "failed", "failure_detail": {"failure_class": "provider",
               "failure_code": "claude_cli_result_missing_or_invalid"},
               "provider_trace": {"exit_code": 1, "stderr": "Error: sandbox required but unavailable: ...\n"
                                  "  Refusing to start without a working sandbox (see sandbox.failIfUnavailable)."}}
    assert _saved_strict_shell_outcome(_synthetic([], **refusal), command) == "claude_refused_to_start_without_its_sandbox"
    ran = _bash(command, stdout="DENIED_PROFILE_ok")
    assert _saved_strict_shell_outcome(_synthetic([ran], status="completed"), command) == "claude_ran_the_requested_command"
    refused = _bash(command, status="failed", stdout="Operation not permitted", is_error=True)
    assert _saved_strict_shell_outcome(_synthetic([refused], status="completed"), command) == "claude_refused_the_requested_command"
    listed = _synthetic([_bash(command, status="failed")], status="completed",
                        provider_trace={"permission_denials": [{"tool_name": "Bash", "tool_input": {"command": command}}]})
    assert _saved_strict_shell_outcome(listed, command) == "claude_refused_the_requested_command"
    rejected = {
        "failure without Claude's sandbox refusal": _synthetic([], **{**refusal, "provider_trace": {"exit_code": 1, "stderr": "boom"}}),
        "failure with an arbitrary sandbox word": _synthetic([], **{**refusal, "provider_trace": {"exit_code": 1,
                                                                    "stderr": "sandbox: unrelated warning"}}),
        "failure with a zero exit": _synthetic([], **{**refusal, "provider_trace": {"exit_code": 0, "stderr":
                                                                 "sandbox required but unavailable"}}),
        "another failure class": _synthetic([], **{**refusal, "failure_detail": {"failure_class": "transport",
                                                                 "failure_code": "claude_cli_process_failed"}}),
        "completed without a command result": _synthetic([], status="completed"),
        "an unrelated command failed": _synthetic([_bash("unrelated_missing_program", status="failed",
                                                         stdout="command not found", is_error=True)], status="completed"),
        "the requested command failed without a refusal": _synthetic([_bash(command, status="failed", stdout="exit 1",
                                                                            is_error=True)], status="completed"),
        "a command ran, so not a refusal to start": _synthetic([ran], **refusal),
    }
    for name, record in rejected.items():
        assert _saved_strict_shell_outcome(record, command) is None, name
