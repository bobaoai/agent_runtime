"""Antigravity CLI (agy) binding: the structured_output and file_review shapes only.

Deterministic cases pin the admitted capability shapes, the exact argv, stdin,
session settings and custom agent rendered from the passing native probes, the
usage mapping and the Inspection view of the observed stream-json events.
fake_run cases go through the real registration and run_local_workflow_test
path with an agy process double that performs real file effects and emits the
event shapes observed with agy 1.5.14; they are not model evidence. Setup cases
use the fake native programs of the setup tests.
"""
import base64
import json
from pathlib import Path
import re
import subprocess

import pytest

from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
from agent_runtime.inspection.inspection_execution_logging import parse_cli_log
from agent_runtime.invocation import invocation_antigravity_cli_execution as agy
from agent_runtime.registry.registry_module_authoring import module_requirements_preset
from agent_runtime.testing import conformance_pattern_examples as examples


MODEL, EFFORT = "gemini-3.8-flash-low", "low"
# init.tools as agy 1.5.14 reported it for a finish-only agent: the general catalog, not the agent's tools.
CATALOG = ["ask_custom_permission", "ask_permission", "ask_question", "browser_click_element", "browser_subagent",
           "call_mcp_tool", "command_status", "define_subagent", "find_by_name", "finish", "generate_image",
           "grep_search", "invoke_subagent", "list_dir", "manage_task", "multi_replace_file_content",
           "read_url_content", "replace_file_content", "run_command", "search_web", "send_command_input",
           "view_file", "write_to_file"]
CONVERSATION = "4e82a1b3-6216-49c6-a23f-caf276605680"
# result.denied_actions as agy reported a headless command denial.
DENIED = [{"action": "command", "display_name": "Bash"}]


def _profile(preset, **changes):
    from dataclasses import replace
    requirements = replace(module_requirements_preset(preset), **changes)
    return _execution_profile_for_requirements(requirements, transport_kind="antigravity_cli", model_id=MODEL,
                                               reasoning_profile=EFFORT)


def _step(index, state, step_type, **fields):
    return {"event": "step_update", "step_update": {"conversation_id": CONVERSATION, "step_index": index,
                                                     "state": state, "step_type": step_type, **fields}}


def _stream(*, agent=agy.AGENT_NAME, model=MODEL, cwd="C:/w", schema=None, tools=(), output=None,
            status="SUCCESS", usage=None, extra_lines=(), denied_actions=None):
    """A stream in the shape agy 1.5.14 emitted: init, step_update events, then one result.

    tools: (tool_name, parameters, output or None, final state) per tool step.
    denied_actions: the result's native denial list, when agy reported one.
    """
    usage = usage or {"input_tokens": 2100, "output_tokens": 257, "thinking_tokens": 236,
                      "cache_read_tokens": 0, "total_tokens": 2357}
    events = [{"event": "init", "conversation_id": CONVERSATION, "init": {
        "model": model, "cwd": str(cwd), "agent": agent, "tools": CATALOG, "permission_mode": "request-review",
        "json_schema": schema or {"type": "object"}}}, _step(0, "DONE", "user_input")]
    index = 1
    for name, parameters, result, state in tools:
        events.append(_step(index, "DONE", "agent_response", duration_seconds=2.4, usage={
            "input_tokens": 2188, "output_tokens": 39, "thinking_tokens": 0, "cache_read_tokens": 1629,
            "total_tokens": 2227}))
        info = {"name": name, "parameters": parameters}
        events.append(_step(index + 1, "ACTIVE", "tool", tool_name=name, tool_info=info))
        done = {**info, **({"output": result} if result is not None else {})}
        events.append(_step(index + 1, state, "tool", tool_name=name, duration_seconds=0.06, tool_info=done))
        index += 2
    text = json.dumps(output) + "\n" if output is not None else "done\n"
    events.append(_step(index, "DONE", "agent_response", text_delta=text, duration_seconds=2.5, usage=usage))
    events.append(_step(index + 1, "DONE", "finish", duration_seconds=0.03))
    result = {"conversation_id": CONVERSATION, "status": status, "response": text, "duration_seconds": 5.4,
              "num_turns": 1, "json_schema": schema or {"type": "object"}, "usage": usage}
    if output is not None:
        result["structured_output"] = output
    if denied_actions is not None:
        result["denied_actions"] = denied_actions
    events.append({"event": "result", "result": result})
    return ("\n".join(json.dumps(event) for event in events) + "\n" + "".join(extra_lines)).encode("utf-8")


def _trace(raw, **fields):
    return {"transport": "antigravity_cli", "byte_capture_exact": True, "process_output_complete": True,
            "raw_streams": {"stdout": {"encoding": "base64", "data": base64.b64encode(raw).decode()},
                            "stderr": {"encoding": "base64", "data": ""}}, **fields}


# --- Admitted shapes and rendered invocation --------------------------------------------------

@pytest.mark.deterministic
def test_only_the_structured_output_and_file_review_shapes_bind_antigravity():
    for preset in ("structured_output", "file_review"):
        profile = _profile(preset)
        assert (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind,
                profile.provider_id, profile.model_id, profile.reasoning_profile) == (
            "antigravity_cli_adapter", "v1", "antigravity_cli", "google", MODEL, EFFORT)
    for preset, extra in (("code_change", {}), ("mcp_data", {"mcp_connections": ("data",)})):
        requirements = module_requirements_preset(preset, **extra)
        with pytest.raises(agy.AntigravityCapabilityUnsupportedError) as refused:
            _execution_profile_for_requirements(requirements, transport_kind="antigravity_cli", model_id=MODEL,
                                                reasoning_profile=EFFORT)
        assert refused.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"
    for changes in ({"output_constraint_mode": "prompt_only_json"}, {"tool_policy": ("read", "shell", "edit")},
                    {"network_policy": "denied"}):
        with pytest.raises(agy.AntigravityCapabilityUnsupportedError):
            _profile("file_review", **changes)
    with pytest.raises(ValueError, match="explicit model_id and reasoning_profile"):
        _execution_profile_for_requirements(module_requirements_preset("structured_output"),
                                            transport_kind="antigravity_cli", model_id=MODEL)


@pytest.mark.deterministic
def test_argv_stdin_agent_and_settings_follow_the_passing_probes(tmp_path):
    profile = _profile("structured_output")
    argv = agy.build_command(cli_path="C:/agy/agy.exe", profile=profile, schema_path=Path("C:/t/output_schema.json"),
                             settings_path=Path("C:/t/settings.json"))
    assert argv == ["C:/agy/agy.exe", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--json-schema", str(Path("C:/t/output_schema.json")), "--agent", "agent-runtime-task",
                    "--model", MODEL, "--effort", EFFORT, "--settings", str(Path("C:/t/settings.json")),
                    "--disable-slash-commands"]
    assert not {"-p", "--print", "--continue", "-c", "--conversation", "--dangerously-skip-permissions",
                "--sandbox", "--print-timeout"} & set(argv)
    wire = agy.stdin_event("Task text with \u201cquotes\u201d\nand lines")
    assert wire.endswith("\n") and wire.count("\n") == 1
    assert json.loads(wire) == {"event": "user", "message": {"content": "Task text with \u201cquotes\u201d\nand lines"}}

    tool_free = agy.render_agent_definition("tool_free")
    assert "tools:\n  - finish\n" in tool_free and 'commandExecutionPolicy: "off"' in tool_free
    agent = agy.render_agent_definition("agent")
    assert ("tools:\n  - view_file\n  - write_to_file\n  - replace_file_content\n  - run_command\n"
            "  - command_status\n  - finish\n") in agent and 'commandExecutionPolicy: "auto"' in agent
    for text in (tool_free, agent):
        assert text.startswith("---\nname: agent-runtime-task\n") and "grep_search" not in text
        assert "mainAgent: true\nsubagent: false\n" in text and "mcpServers: []\nskills: []\nplugins: []\n" in text

    assert agy.session_settings("tool_free") == {
        "agentMode": "", "enableTerminalSandbox": False, "toolPermission": "request-review",
        "artifactReviewPolicy": "always-proceed", "permissions": {"deny": [
            "read_file(*)", "write_file(*)", "command(*)", "mcp(*)", "read_url(*)", "execute_url(*)"]}}
    permit = agy.antigravity_command_permit(
        "Select-String -LiteralPath 'C:/Users/me/Documents/Antigravity Runtime Probe/search/sample.txt' -Pattern '^TOTAL='")
    # The exact rule body that agy 1.5.14 accepted in the live search probe.
    assert permit == ("regex:Select\\-String\\ \\-LiteralPath\\ 'C:/Users/me/Documents/Antigravity\\ Runtime\\ "
                      "Probe/search/sample\\.txt'\\ \\-Pattern\\ '\\^TOTAL='")
    scratch, materials, dependency = tmp_path / "work" / "scratch", tmp_path / "work" / "materials", tmp_path / "lib"
    settings = agy.session_settings("agent", working_directory=scratch, materials=materials,
                                    read_only_dependencies=(dependency,), command_permits=(permit,))
    assert settings["agentMode"] == "accept-edits" and settings["enableTerminalSandbox"] is False
    assert settings["permissions"] == {
        "allow": [f"read_file({scratch.as_posix()})", f"read_file({materials.as_posix()})",
                  f"read_file({dependency.as_posix()})", f"command({permit})"],
        "deny": [f"write_file({materials.as_posix()})", f"write_file({(scratch / '.agents').as_posix()})",
                 "mcp(*)", "read_url(*)", "execute_url(*)"]}
    assert not any(rule.startswith("command(") for rule in agy.session_settings(
        "agent", working_directory=scratch, materials=materials)["permissions"]["allow"])
    assert agy.antigravity_command_permit("python -c print(1)") == "regex:python\\ \\-c\\ print\\(1\\)"


@pytest.mark.deterministic
def test_each_permit_is_the_escaped_literal_of_the_exact_command_the_caller_gives_the_task(tmp_path):
    # The measured native path probe (user folder replaced): agy accepted this rule and ran the interpreter.
    probe = "& 'C:/Users/me/Documents/Runtime Command Cases (\u8fc1\u79fb)/env/Scripts/python.exe' 'probe_path.py'"
    assert agy.antigravity_command_permit(probe) == (
        "regex:\\&\\ 'C:/Users/me/Documents/Runtime\\ Command\\ Cases\\ \\(\u8fc1\u79fb\\)/env/Scripts/python\\.exe'"
        "\\ 'probe_path\\.py'")
    # Caller pattern: one canonical string per command, built from the selected Runtime Python, given to the
    # task and to the helper unchanged. Other drives, spaces and parentheses need nothing more.
    commands = (probe, "& 'D:/Program Files (x86)/Agent Runtime/env/Scripts/python.exe' 'build.py'",
                "& 'E:\\Runtime Envs\\Python 3.12\\python.exe' 'check.py'", "Get-Content 'output (draft).json'")
    permits = tuple(agy.antigravity_command_permit(command) for command in commands)
    assert permits == tuple("regex:" + re.escape(command) for command in commands)
    from agent_runtime.registry import RuntimeReleaseRegistry
    agy.AntigravityCliAdapter(release_registry=RuntimeReleaseRegistry(), artifact_host=type("Host", (), {})(),
                              workspace_root=tmp_path, cli_path=_cli(tmp_path), command_permits=permits)
    settings = agy.session_settings("agent", working_directory=tmp_path / "scratch", materials=tmp_path / "materials",
                                    command_permits=permits)
    rendered = json.loads(json.dumps(settings, ensure_ascii=False))["permissions"]["allow"]
    assert rendered[-len(permits):] == [f"command({permit})" for permit in permits]

    # The helper only escapes what it is given: an alias, another path form or a joined pair is another
    # string with another rule; it is never derived from the separate permits.
    interpreter = "& 'D:/Program Files (x86)/Agent Runtime/env/Scripts/python.exe'"
    build, check = f"{interpreter} 'build.py'", f"{interpreter} 'check.py'"
    separate = {agy.antigravity_command_permit(build), agy.antigravity_command_permit(check)}
    for other in ("python build.py", "D:\\Program Files (x86)\\Agent Runtime\\env\\Scripts\\python.exe build.py",
                  f"{build}; {check}"):
        assert agy.antigravity_command_permit(other) not in separate
    for refused in ("python a.py\npython b.py", "python a.py\r", "python a\x00.py", " python a.py"):
        with pytest.raises(ValueError):
            agy.antigravity_command_permit(refused)
    with pytest.raises(ValueError, match="command_permits"):
        agy.AntigravityCliAdapter(release_registry=RuntimeReleaseRegistry(), artifact_host=type("Host", (), {})(),
                                  workspace_root=tmp_path, cli_path=_cli(tmp_path), command_permits=("regex:a\x00",))


@pytest.mark.deterministic
def test_usage_keeps_reported_output_and_leaves_unestablished_input_and_cache_unknown():
    usage, invalid = agy.usage_fields({"usage": {"input_tokens": 2113, "output_tokens": 4169, "thinking_tokens": 4152,
                                                 "cache_read_tokens": 7, "total_tokens": 6282}})
    assert not invalid and usage == {"input_tokens": None, "output_tokens": 4169, "cache_read_tokens": None,
                                     "cache_creation_tokens": None, "ordinary_input_tokens": None}
    assert agy.usage_fields({"usage": {"output_tokens": -1}})[1] is True
    assert agy.usage_fields({"usage": "12"})[1] is True
    assert agy.usage_fields({}) == ({key: None for key in usage}, False)


# --- Inspection of observed stream-json events ------------------------------------------------

@pytest.mark.deterministic
def test_inspection_pairs_tool_steps_and_keeps_summary_outputs_as_content_limitations():
    material = "C:/w/work/materials/source/invoice.txt"
    raw = _stream(tools=[
        ("read_file", {"Path": material}, "4 lines, 69 bytes", "DONE"),
        ("write_file", {"Path": "C:/w/work/scratch/review.json"}, None, "DONE"),
        ("run_command", {"CommandLine": "Select-String -LiteralPath x -Pattern '^TOTAL='"},
         "Created At: 2026-11-22T11:34:16+08:00\nCommand exited with code 0.\n\nCommand output:\nsample.txt:3:TOTAL=497260\r\n",
         "DONE"),
        ("run_command", {"CommandLine": "Remove-Item x"}, "Permission denied", "ERROR")], output={"ok": True})
    view = parse_cli_log(_trace(raw))
    calls = view["tool_calls"]
    assert [(call["tool_name"], call["status"]) for call in calls] == [
        ("read_file", "completed"), ("write_file", "completed"), ("run_command", "completed"), ("run_command", "failed")]
    assert calls[0]["request"] == {"Path": material} and calls[0]["response"]["tool_info"]["output"] == "4 lines, 69 bytes"
    assert "TOTAL=497260" in json.dumps(calls[2]["response"])
    assert view["issues"] == [f"provider_result_content_unavailable:{calls[0]['tool_call_id']}",
                              f"provider_request_content_unavailable:{calls[1]['tool_call_id']}"]
    assert view["complete"] is False
    # finish and the init catalog are events, never task tool calls.
    assert not any(call["tool_name"] in {"finish", "manage_task"} for call in calls)
    assert any(event["event"].get("event") == "init" for event in view["events"])

    shell_only = parse_cli_log(_trace(_stream(tools=[("run_command", {"CommandLine": "Get-Content review.md"},
                                                      "Command output:\n# Review\r\n", "DONE")])))
    assert shell_only["complete"] is True and shell_only["issues"] == []
    no_terminal = raw.split(b'{"event": "result"')[0]
    assert "terminal_event_missing_or_duplicated" in parse_cli_log(_trace(no_terminal))["issues"]
    parsed = agy.parse_antigravity_stream(raw)
    assert parsed["issues"] == [] and parsed["init"]["agent"] == agy.AGENT_NAME and parsed["result"]["status"] == "SUCCESS"


# --- Public Test Run with an agy process double -----------------------------------------------

@pytest.fixture
def short_root():
    import shutil
    import tempfile
    import uuid
    root = Path(tempfile.gettempdir()) / f"ar agy {uuid.uuid4().hex[:6]}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _double(monkeypatch, behavior):
    """Serve agy --version and the provider call; the call performs the behavior's real file effects."""
    launches, manifests = [], []
    prepare = examples.prepare_pattern_example

    def capture(*args, **kwargs):
        manifests.append(prepare(*args, **kwargs))
        return manifests[-1]
    monkeypatch.setattr(examples, "prepare_pattern_example", capture)

    class Adapter(agy.AntigravityCliAdapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, process_runner=self._process)

        def _process(self, **fields):
            argv = fields["argv"]
            if argv[1:] == ["--version"]:
                completed = subprocess.CompletedProcess(argv, 0, "1.5.14\n", "")
                completed.stdout_bytes, completed.stderr_bytes = b"1.5.14\n", b""
                return completed
            cwd = Path(fields["cwd"])
            settings = json.loads(Path(argv[argv.index("--settings") + 1]).read_text(encoding="utf-8"))
            schema = json.loads(Path(argv[argv.index("--json-schema") + 1]).read_text(encoding="utf-8"))
            agent_file = cwd / ".agents" / "agents" / f"{argv[argv.index('--agent') + 1]}.md"
            event = json.loads(fields["prompt"])
            launches.append({**fields, "settings": settings, "schema": schema, "agent_text": agent_file.read_text(
                encoding="utf-8"), "content": event["message"]["content"]})
            returncode, raw, stderr = behavior(manifests[-1], cwd, schema)
            completed = subprocess.CompletedProcess(argv, returncode, raw.decode(), stderr.decode())
            completed.stdout_bytes, completed.stderr_bytes = raw, stderr
            return completed
    monkeypatch.setattr(agy, "AntigravityCliAdapter", Adapter)
    return launches


def _structured_output(manifest, cwd, schema, **stream):
    expected = manifest["expected"]
    output = {"order_id": expected["order_id"], "urgent": expected["urgent"],
              "total_quantity": expected["total_quantity"],
              "lines": [{"sku": sku, "quantity": quantity} for sku, quantity in expected["lines"].items()]}
    return 0, _stream(cwd=cwd, schema=schema, output=output, **stream), b""


def _cli(tmp_path):
    program = Path(tmp_path) / "agy.exe"
    program.write_bytes(b"not started: the double serves every call")
    return program


def _failed(result):
    return {row["check"] for row in result["checks"] if not row["passed"]}


@pytest.mark.fake_run
def test_structured_output_runs_tool_free_with_a_finish_only_agent(tmp_path, short_root, monkeypatch):
    launches = _double(monkeypatch, _structured_output)
    result = examples.run_pattern_example(short_root, "structured_output", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert result["passed"], _failed(result)
    assert record["status"] == "completed" and len(launches) == 1
    launch, trace = launches[0], record["provider_trace"]
    cwd = Path(launch["cwd"])
    assert cwd.name == "work" and (cwd / ".agents" / "agents" / "agent-runtime-task.md").is_file()
    assert "tools:\n  - finish\n" in launch["agent_text"]
    assert launch["settings"] == agy.session_settings("tool_free")
    # The canonical registered output schema body, unchanged (no Provider-specific pruning).
    document = json.loads(examples.pattern_source("structured_output").output_schema_document)
    assert launch["schema"] == {key: value for key, value in document.items() if key not in {"$id", "$schema"}}
    # Tool-free stdin is the frozen envelope alone, wrapped as one stream-json user event.
    assert launch["content"] == trace["actual_prompt"] and trace["stdin_jsonl"] == launch["prompt"]
    assert "Runtime Invocation Locations" not in trace["actual_prompt"]
    assert trace["argv"][trace["argv"].index("--model") + 1] == MODEL and "-p" not in trace["argv"]
    assert trace["initialization"]["agent"] == "agent-runtime-task" and trace["result_status"] == "SUCCESS"
    assert trace["initialization_tool_catalog"] == CATALOG
    assert trace["native_usage"]["thinking_tokens"] == 236 and trace["cli_version"] == "1.5.14"
    assert record["usage"]["output_tokens"] == 257 and record["usage"]["input_tokens"] is None
    assert record["usage"]["cache_read_tokens"] is None
    assert trace["effective_configuration"]["tools"] == ["finish"]
    assert trace["effective_configuration"]["command_scope"] == "none"
    assert result["attempt_workspace"]["sealed"] is True


def _review(manifest, cwd, schema, *, readback="summary"):
    material = cwd.parent / "materials" / "source" / "release_notes.md"
    text = material.read_text(encoding="utf-8")
    code = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Release code:"))
    body = f"# Review: release_notes.md\nRelease code: {code}\n- Rollback window 30 vs 15 minutes.\n"
    review = cwd / "review.md"
    steps = [("read_file", {"Path": material.as_posix()}, f"{len(text.splitlines())} lines, {len(text)} bytes", "DONE"),
             ("run_command", {"CommandLine": f"Select-String -LiteralPath '{material.as_posix()}' -Pattern 'Release code'"},
              f"Command exited with code 0.\n\nCommand output:\nrelease_notes.md:3:Release code: {code}\r\n", "DONE")]
    if readback != "before_write":
        review.write_text(body, encoding="utf-8", newline="\n")
        steps.append(("write_file", {"Path": review.as_posix()}, None, "DONE"))
    if readback == "summary" or readback == "before_write":
        steps.append(("read_file", {"Path": review.as_posix()}, f"3 lines, {len(body)} bytes", "DONE"))
    else:
        steps.append(("run_command", {"CommandLine": "Get-Content review.md"},
                      "Command exited with code 0.\n\nCommand output:\n" + body.replace("\n", "\r\n"), "DONE"))
    if readback == "before_write":
        review.write_text(body, encoding="utf-8", newline="\n")
        steps.append(("write_file", {"Path": review.as_posix()}, None, "DONE"))
    output = {"review_file": "review.md", "release_code": code, "finding_count": 1,
              "readback_first_line": "# Review: release_notes.md"}
    return 0, _stream(cwd=cwd, schema=schema, tools=steps, output=output), b""


@pytest.mark.fake_run
@pytest.mark.parametrize("readback", ["summary", "shell"])
def test_file_review_runs_in_the_private_scratch_with_materials_read_only(tmp_path, short_root, monkeypatch, readback):
    launches = _double(monkeypatch, lambda *args: _review(*args, readback=readback))
    result = examples.run_pattern_example(short_root, "file_review", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    launch, trace = launches[0], record["provider_trace"]
    cwd = Path(launch["cwd"])
    materials = cwd.parent / "materials"
    assert cwd.name == "scratch" and Path(result["attempt_workspace"]["workspace_path"]) == cwd.parent.parent
    assert launch["settings"] == agy.session_settings(
        "agent", working_directory=cwd, materials=materials,
        command_permits=(agy.antigravity_command_permit("Get-Content review.md"),))
    assert "  - run_command\n" in launch["agent_text"] and "grep_search" not in launch["agent_text"]
    assert launch["environment"]["TEMP"] == str(cwd / ".tmp")
    locations = trace["effective_configuration"]["invocation_locations"]
    assert locations["working_directory"] == str(cwd) and locations["materials_directory"] == str(materials)
    assert trace["actual_prompt"].startswith("## Runtime Invocation Locations")
    assert json.loads(trace["stdin_jsonl"])["message"]["content"] == trace["actual_prompt"]
    assert trace["effective_configuration"]["command_permits"] == [agy.antigravity_command_permit("Get-Content review.md")]
    readback_row = next(row for row in result["checks"] if row["check"] == "review_read_back_in_log")
    assert ("provider log does not contain the read content" in readback_row["detail"]) is (readback == "summary")
    names = [call["tool_name"] for call in record["execution_log"]["attempts"][0]["tool_calls"]]
    assert names[:3] == ["read_file", "run_command", "write_file"]


@pytest.mark.fake_run
def test_a_summary_read_before_the_last_write_is_not_read_back_evidence(tmp_path, short_root, monkeypatch):
    _double(monkeypatch, lambda *args: _review(*args, readback="before_write"))
    result = examples.run_pattern_example(short_root, "file_review", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    assert result["status"] == "completed" and _failed(result) == {"review_read_back_in_log"}


@pytest.mark.fake_run
@pytest.mark.parametrize("case, code", [
    ("no_structured_output", "antigravity_cli_structured_output_missing"),
    ("schema_violation", "ADAPTER_OUTPUT_INVALID"),
    ("schema_violation_after_denial", "ADAPTER_OUTPUT_INVALID"),
    ("error_status", "antigravity_cli_error_result"),
    ("nonzero_exit", "antigravity_cli_nonzero_exit"),
    ("other_agent", "antigravity_cli_agent_not_applied"),
    ("other_model", "antigravity_cli_model_identity_mismatch"),
    ("no_result", "antigravity_cli_result_missing_or_invalid"),
    ("invalid_usage", "antigravity_cli_usage_invalid"),
])
def test_terminal_outcomes_fail_with_their_own_code_and_keep_the_native_facts(tmp_path, short_root, monkeypatch,
                                                                             case, code):
    def behavior(manifest, cwd, schema):
        if case == "no_structured_output":
            # As the tools: [] probe returned: SUCCESS with fenced text and no structured_output.
            return 0, _stream(cwd=cwd, schema=schema, output=None), b""
        if case == "schema_violation":
            return 0, _stream(cwd=cwd, schema=schema, output={"order_id": 7}), b""
        if case == "schema_violation_after_denial":
            return 0, _stream(cwd=cwd, schema=schema, output={"order_id": 7}, denied_actions=DENIED), b""
        if case == "error_status":
            return 0, _stream(cwd=cwd, schema=schema, output=None, status="ERROR"), b""
        if case == "nonzero_exit":
            return 1, _stream(cwd=cwd, schema=schema, output=None, status="ERROR", model="unknown-model"), b"model error\n"
        if case == "no_result":
            return 0, _stream(cwd=cwd, schema=schema, output=None).split(b'{"event": "result"')[0], b""
        if case == "invalid_usage":
            return _structured_output(manifest, cwd, schema, usage={"output_tokens": -3})
        changed = {"other_agent": {"agent": "default"}, "other_model": {"model": "gemini-other"}}[case]
        return _structured_output(manifest, cwd, schema, **changed)
    _double(monkeypatch, behavior)
    result = examples.run_pattern_example(short_root, "structured_output", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["output"] is None
    assert record["failure_detail"]["failure_code"] == code, record["failure_detail"]
    trace = record["provider_trace"]
    assert trace["adapter_failure"]["failure_code"] == code and trace["raw_streams"]["stdout"]["data"]
    if case == "error_status":
        assert "'ERROR'" in trace["adapter_failure"]["message"] and trace["result_status"] == "ERROR"
    if case == "nonzero_exit":
        assert base64.b64decode(trace["raw_streams"]["stderr"]["data"]) == b"model error\n"
    if case == "no_structured_output":
        # Without a native denial the missing receipt keeps its generic explanation.
        assert record["failure_detail"]["message"] == (
            "Antigravity CLI result carries no structured_output; response text is not parsed")
        assert "native_denied_actions" not in trace
    if case == "schema_violation_after_denial":
        assert "denied" not in record["failure_detail"]["message"] and trace["native_denied_actions"] == DENIED


@pytest.mark.fake_run
def test_an_observed_command_denial_without_structured_output_fails_and_names_the_native_denial(
        tmp_path, short_root, monkeypatch):
    fixture = json.loads((Path(__file__).parent / "fixtures" / "antigravity_command_denial"
                          / "denied_command_no_output.json").read_text(encoding="utf-8"))
    stdout = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in fixture["stdout_events"]).encode("utf-8")
    stderr = fixture["stderr"].encode("utf-8")
    _double(monkeypatch, lambda *args: (0, stdout, stderr))
    result = examples.run_pattern_example(short_root, "file_review", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["output"] is None
    detail, trace = record["failure_detail"], record["provider_trace"]
    assert (detail["failure_class"], detail["failure_code"], detail["retryable"]) == (
        "schema", "antigravity_cli_structured_output_missing", False)
    assert detail["message"] == (
        'Antigravity CLI denied native actions [{"action": "command", "display_name": "Bash"}] '
        "(failed tool steps: run_command #8); its result carries no structured_output and response text is not parsed")
    assert trace["adapter_failure"]["retry_disposition_id"] == "retry_denied"
    assert (trace["exit_code"], trace["result_status"], trace["native_denied_actions"]) == (0, "SUCCESS", DENIED)
    # Complete native evidence: both raw streams are kept byte for byte.
    assert base64.b64decode(trace["raw_streams"]["stdout"]["data"]) == stdout
    assert base64.b64decode(trace["raw_streams"]["stderr"]["data"]) == stderr
    denied_command = fixture["stdout_events"][-2]["step_update"]["tool_info"]["parameters"]
    calls = record["execution_log"]["attempts"][0]["tool_calls"]
    assert [(call["tool_name"], call["status"]) for call in calls][-1] == ("run_command", "failed")
    assert calls[-1]["request"] == denied_command


@pytest.mark.fake_run
def test_a_soft_denied_tool_is_kept_as_a_failed_step_without_failing_a_valid_result(tmp_path, short_root, monkeypatch):
    def behavior(manifest, cwd, schema):
        returncode, raw, _ = _structured_output(manifest, cwd, schema, denied_actions=DENIED)
        events = raw.decode().splitlines()
        denied = [json.dumps(_step(40, "ACTIVE", "tool", tool_name="run_command",
                                   tool_info={"name": "run_command", "parameters": {"CommandLine": "whoami"}})),
                  json.dumps(_step(40, "ERROR", "tool", tool_name="run_command",
                                   tool_info={"name": "run_command", "parameters": {"CommandLine": "whoami"}}))]
        stream = "\n".join([*events[:2], *denied, *events[2:]]) + "\n"
        return returncode, stream.encode(), b"run_command was denied: add an allow rule to permit it\n"
    _double(monkeypatch, behavior)
    result = examples.run_pattern_example(short_root, "structured_output", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert record["status"] == "completed" and record["failure_detail"] is None
    calls = record["execution_log"]["attempts"][0]["tool_calls"]
    assert [(call["tool_name"], call["status"]) for call in calls] == [("run_command", "failed")]
    assert "denied" in record["provider_trace"]["stderr"]
    assert record["provider_trace"]["native_denied_actions"] == DENIED
    assert _failed(result) == {"no_task_tools"}  # The example still reports the attempted tool.


@pytest.mark.fake_run
def test_provider_timeout_is_a_typed_timeout_with_captured_bytes(tmp_path, short_root, monkeypatch):
    def behavior(manifest, cwd, schema):
        raise subprocess.TimeoutExpired(["agy"], 5, output=b'{"event": "init"}\n', stderr=b"")
    _double(monkeypatch, behavior)
    result = examples.run_pattern_example(short_root, "structured_output", "antigravity_cli", cli_path=_cli(tmp_path),
                                          model_id=MODEL, reasoning_profile=EFFORT)
    record = json.loads((Path(result["run_directory"]) / "record.json").read_text(encoding="utf-8"))
    assert record["status"] == "failed" and record["failure_detail"]["failure_code"] == "antigravity_cli_timeout"
    assert record["provider_trace"]["process_output_complete"] is False


@pytest.mark.deterministic
def test_unsupported_patterns_and_resources_are_refused_before_any_folder_or_model(tmp_path, short_root):
    for pattern in ("code_change", "mcp_data"):
        with pytest.raises(ValueError, match="supports only the structured_output and file_review"):
            examples.prepare_pattern_example(short_root, pattern, "antigravity_cli")
    assert not short_root.exists()
    from agent_runtime.registry import RuntimeReleaseRegistry
    artifact_host = type("Host", (), {})()
    with pytest.raises(agy.AntigravityCapabilityUnsupportedError):
        agy.AntigravityCliAdapter(release_registry=RuntimeReleaseRegistry(), artifact_host=artifact_host,
                                  workspace_root=tmp_path, cli_path=_cli(tmp_path), mcp_servers={"data": {}})
    with pytest.raises(agy.AntigravityCapabilityUnsupportedError):
        agy.AntigravityCliAdapter(release_registry=RuntimeReleaseRegistry(), artifact_host=artifact_host,
                                  workspace_root=tmp_path, cli_path=_cli(tmp_path), project_directory=tmp_path)
    with pytest.raises(ValueError, match="command_permits"):
        agy.AntigravityCliAdapter(release_registry=RuntimeReleaseRegistry(), artifact_host=artifact_host,
                                  workspace_root=tmp_path, cli_path=_cli(tmp_path), command_permits=("a\nb",))


# --- agent-runtime-setup ------------------------------------------------------------------------

_AGY_READY = {"--version": {"stdout": "1.5.14\n"}}


@pytest.mark.fake_run
def test_setup_finds_agy_on_path_then_at_its_installed_location_and_only_asks_its_version(tmp_path, monkeypatch, capsys):
    from test_agent_runtime_host_setup_cli import _calls, _config, _fake_cli, _setup
    on_path = _fake_cli(tmp_path / "bin", "agy", _AGY_READY)
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    root = tmp_path / "project"
    code, report = _setup(capsys, "--root", root, "--provider", "antigravity")
    assert code == 0 and report["status"] == "prepared" and report["missing"] == []
    assert report["providers"] == {"antigravity_cli": {"program": str(on_path), "source": "path", "started": True,
                                                       "version": "1.5.14", "login": "not_verified"}}
    assert _config(root) == {"provider_cli_paths": {"antigravity_cli": str(on_path)}}
    assert _calls(on_path) == [["--version"]]  # No login, model listing or model call.
    assert any("cannot check it" in step for step in report["next_steps"])
    assert any("--transport antigravity_cli" in step and "--model" in step for step in report["next_steps"])
    for skill in ("agent-runtime-usage", "agent-runtime-registration", "agent-runtime-test-run"):
        assert (root / ".agents" / "skills" / skill / "SKILL.md").is_file()

    installed = _fake_cli(tmp_path / "local" / "agy" / "bin", "agy", _AGY_READY)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    other = tmp_path / "other project"
    code, report = _setup(capsys, "--root", other, "--provider", "antigravity")
    if installed.suffix == ".exe":
        assert code == 0 and report["providers"]["antigravity_cli"]["source"] == "installed_path"
        assert report["providers"]["antigravity_cli"]["program"] == str(installed)


@pytest.mark.fake_run
def test_setup_both_still_means_claude_and_codex_and_antigravity_takes_no_mcp(tmp_path, monkeypatch, capsys):
    from test_agent_runtime_host_setup_cli import _setup
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    code, report = _setup(capsys, "--root", tmp_path / "p", "--provider", "both")
    assert set(report["providers"]) == {"claude_cli", "codex_cli"}
    with pytest.raises(SystemExit):
        _setup(capsys, "--root", tmp_path / "p", "--provider", "both", "--antigravity-path", tmp_path / "agy.exe")
    with pytest.raises(SystemExit):
        _setup(capsys, "--root", tmp_path / "p", "--provider", "antigravity", "--mcp", "data")
    code, report = _setup(capsys, "--root", tmp_path / "q", "--provider", "antigravity")
    assert code == 1 and report["providers"]["antigravity_cli"]["program"] is None
    assert "agy" in report["missing"][0] and "--antigravity-path" in report["missing"][0]
