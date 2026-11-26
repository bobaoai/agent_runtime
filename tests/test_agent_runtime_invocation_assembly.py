"""What each CLI actually receives from Runtime's assembly: stdin, locations, temp folders and pytest plugins.

Every case runs the public run_local_workflow_test on the shipped pattern
definitions with Runtime's own process runner. Only the Provider program is a
named substitute: a stdlib script started exactly as Runtime starts the CLI
(argv, cwd, environment, stdin). It saves the bytes it read on stdin and, as a
real child of that environment, reads the frozen material through the location
section, reports the temporary folder seen by Python, cmd.exe and .NET, and runs
pytest with an entry-point plugin. No model, login, MCP service or network is used.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

import pytest

from agent_runtime import prepare_material_files, run_local_workflow_test
from agent_runtime.invocation.invocation_local_resource_preparation import (
    INVOCATION_LOCATIONS_HEADING, compose_invocation_prompt, describe_local_resources,
)
from agent_runtime.testing import conformance_pattern_examples as examples
from test_agent_runtime_claude_native_tools import SYNTHETIC_CLAUDE_HELP, _scripted_cli, _sent_envelope

MATERIAL = "spec.md"
MATERIAL_BODY = "frozen material read through the location section\n"
CODEX_OPTIONS = {"model_id": "gpt-5.5", "reasoning_profile": "high"}

# A Provider substitute for both CLIs. It answers the preflight probes Runtime runs
# (--version/--help for Claude; --version and mcp list for Codex), then serves one call.
_STAND_IN = r'''
import json, os, shutil, subprocess, sys
config = json.load(open(CONFIG, encoding="utf-8"))
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.999 (stand-in)"); sys.exit(0)
if args == ["--help"]:
    print(HELP); sys.exit(0)
if args[-3:] == ["mcp", "list", "--json"]:
    print("[]"); sys.exit(0)
stdin = sys.stdin.buffer.read()
with open(config["capture"], "wb") as stream:
    stream.write(stdin)
facts = {"cwd": os.getcwd(), "environment": {name: os.environ.get(name) for name in (
    "TMPDIR", "TEMP", "TMP", "CLAUDE_CODE_TMPDIR", "PYTEST_DISABLE_PLUGIN_AUTOLOAD")}}
text = stdin.decode("utf-8")
if text.startswith(HEADING):
    start = text.index("{")
    locations = json.loads(text[start:text.index("\n}\n", start) + 2])
    with open(os.path.join(locations["materials_directory"], "source", MATERIAL), encoding="utf-8") as stream:
        facts["material"] = stream.read()
    python = shutil.which("python", path=os.environ["PATH"])
    facts["python"] = python
    def child(argv, **extra):
        return subprocess.run(argv, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL, **extra)
    plugin = child([python, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", config["plugin_project"]],
                   env=dict(os.environ, PYTHONPATH=config["plugin_site"]), cwd=config["plugin_project"])
    facts["pytest"] = [plugin.returncode, plugin.stdout[-400:]]
    facts["python_tempdir"] = child([python, "-B", "-c", "import tempfile; print(tempfile.gettempdir())"]).stdout.strip()
    if os.name == "nt":
        facts["cmd_temp"] = child(["cmd.exe", "/d", "/c", "echo %TEMP%"]).stdout.strip()
        facts["dotnet_temp_file"] = child(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                           "[System.IO.Path]::GetTempFileName()"]).stdout.strip()
with open(config["facts"], "w", encoding="utf-8") as stream:
    json.dump(facts, stream)
output = config["output"]
if "exec" in args:
    events = [{"type": "thread.started", "thread_id": "stand-in"}, {"type": "turn.started"},
              {"type": "item.completed", "item": {"type": "agent_message", "id": "answer", "text": json.dumps(output)}},
              {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}}]
else:
    model = args[args.index("--model") + 1]
    events = [{"type": "system", "subtype": "init", "model": model, "permissionMode": "dontAsk", "tools": [],
               "skills": [], "plugins": [], "slash_commands": [], "mcp_servers": []},
              {"type": "result", "subtype": "success", "is_error": False, "structured_output": output,
               "modelUsage": {model: {"canonicalModel": model.removesuffix("[1m]"), "outputTokens": 3}},
               "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0,
                         "cache_creation_input_tokens": 0}}]
for event in events:
    print(json.dumps(event))
'''


@pytest.fixture
def short_root():
    """Windows keeps 260-character paths here; pytest's tmp_path is too deep for nested Attempt folders."""
    root = Path(tempfile.gettempdir()) / f"ar asm {uuid.uuid4().hex[:6]}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _plugin(tmp_path):
    """A pytest plugin found only through its pytest11 entry point, and a test that needs its fixture."""
    site = tmp_path / "plugin_site"
    (site / "runtime_probe_plugin-1.0.dist-info").mkdir(parents=True)
    (site / "runtime_probe_plugin.py").write_text(
        "import pytest\n\n@pytest.fixture\ndef runtime_probe_value():\n    return 42\n", encoding="utf-8")
    (site / "runtime_probe_plugin-1.0.dist-info/METADATA").write_text(
        "Metadata-Version: 2.1\nName: runtime-probe-plugin\nVersion: 1.0\n", encoding="utf-8")
    (site / "runtime_probe_plugin-1.0.dist-info/entry_points.txt").write_text(
        "[pytest11]\nruntime_probe = runtime_probe_plugin\n", encoding="utf-8")
    project = tmp_path / "plugin_project"
    project.mkdir()
    (project / "test_plugin_fixture.py").write_text(
        "def test_entry_point_plugin_loaded(runtime_probe_value):\n    assert runtime_probe_value == 42\n",
        encoding="utf-8")
    return site, project


def _run(tmp_path, short_root, monkeypatch, pattern, transport, *, materials):
    """Prepare one shipped pattern, run it through the public Test Run with the stand-in CLI; return facts."""
    from agent_runtime.testing.execution_local_test_run import _resource_arguments
    manifest = examples.prepare_pattern_example(short_root / "runs", pattern, transport)
    site, project = _plugin(tmp_path)
    output = ({"changed_files": ["pricing.py"], "check_command": "stand-in", "passed": True}
              if pattern == "code_change" else {
                  "order_id": manifest["expected"]["order_id"], "urgent": manifest["expected"]["urgent"],
                  "total_quantity": manifest["expected"]["total_quantity"],
                  "lines": [{"sku": sku, "quantity": quantity}
                            for sku, quantity in manifest["expected"]["lines"].items()]})
    config = {"capture": str(tmp_path / "stdin.bin"), "facts": str(tmp_path / "facts.json"), "output": output,
              "plugin_site": str(site), "plugin_project": str(project)}
    (tmp_path / "stand_in.json").write_text(json.dumps(config), encoding="utf-8")
    header = (f"CONFIG = {str(tmp_path / 'stand_in.json')!r}\nHELP = {SYNTHETIC_CLAUDE_HELP!r}\n"
              f"HEADING = {INVOCATION_LOCATIONS_HEADING!r}\nMATERIAL = {MATERIAL!r}\n")
    program = _scripted_cli(tmp_path / "bin" / transport.removesuffix("_cli"), header + _STAND_IN, monkeypatch)
    resources = {}
    origin = None
    if materials:
        origin = short_root / "origin"
        origin.mkdir(parents=True)
        (origin / MATERIAL).write_text(MATERIAL_BODY, encoding="utf-8", newline="\n")
        dependency = short_root / "dependency"
        dependency.mkdir()
        resources = dict(material_root=origin, material_files=prepare_material_files(origin, [MATERIAL]),
                         read_only_dependencies=(dependency,))
    elif manifest["resources_path"] is not None:
        resources = _resource_arguments(Path(manifest["resources_path"]))
    record = run_local_workflow_test(
        Path(manifest["host_root"]), selection=Path(manifest["selection_path"]),
        input_payload=json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8")),
        transport_kind=transport, cli_path=program,
        project_directory=None if manifest["project_directory"] is None else Path(manifest["project_directory"]),
        command_permits=tuple(manifest["command_permits"]), **resources,
        **({} if transport == "claude_cli" else CODEX_OPTIONS))
    assert record["status"] == "completed", (record.get("failure_detail"),
                                             record.get("provider_trace", {}).get("adapter_failure"))
    stdin = (tmp_path / "stdin.bin").read_bytes().decode("utf-8")
    facts = json.loads((tmp_path / "facts.json").read_text(encoding="utf-8"))
    return manifest, record, stdin, facts, origin


def _attempt(record):
    return Path(record["attempt_workspaces"][0]["workspace_path"])


def _escaped(path):
    """A path as it appears inside the JSON location section."""
    return json.dumps(str(path))[1:-1]


@pytest.mark.fake_run
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_agent_stdin_is_locations_then_unchanged_envelope_and_equals_inspection(
        tmp_path, short_root, monkeypatch, transport):
    manifest, record, stdin, facts, origin = _run(tmp_path, short_root, monkeypatch, "code_change", transport,
                                                  materials=True)
    trace = record["provider_trace"]
    inspected = record["execution_log"]["attempts"][0]["provider_log"]
    # The bytes the CLI process read are exactly the text Inspection returns for this Attempt.
    assert stdin == inspected["actual_prompt"] == trace["actual_prompt"]
    locations = trace["effective_configuration"]["invocation_locations"]
    assert stdin == compose_invocation_prompt(locations, _sent_envelope(stdin, locations))
    # The frozen envelope is sent unchanged; its recorded hash identifies it, not the whole stdin.
    envelope = _sent_envelope(stdin, locations)
    envelope_sha256 = record["execution_trace"]["attempts"][0]["prompt_envelope_sha256"]
    assert hashlib.sha256(envelope.encode("utf-8")).hexdigest() == envelope_sha256
    assert hashlib.sha256(stdin.encode("utf-8")).hexdigest() != envelope_sha256
    assert INVOCATION_LOCATIONS_HEADING not in envelope and "## Runtime Input Resources" in envelope

    # Every location is the one Runtime actually prepared for this Attempt.
    project = Path(manifest["project_directory"]).resolve()
    work = _attempt(record) / "work"
    assert facts["cwd"] == str(project) == locations["working_directory"]
    assert locations["writable_directories"] == ([str(project)] if transport == "claude_cli"
                                                 else [str(project), str(work / "tmp")])
    assert locations["materials_directory"] == str(work / "materials")
    assert locations["read_only_dependencies"] == [str((short_root / "dependency").resolve())]
    assert locations["runtime_python"] == str(Path(sys.executable).absolute())
    assert locations["temporary_directory_variables"] == ["TMPDIR", "TEMP", "TMP"]
    # A real child reads the frozen copy through the section's path. The origin stays private, and
    # so does Claude's per-call folder, which also holds its private mcp.json (Codex's work/tmp is
    # an ordinary declared write folder and is listed above).
    assert facts["material"] == MATERIAL_BODY
    private = [origin.resolve()] + ([Path(facts["environment"]["TMPDIR"])] if transport == "claude_cli" else [])
    for path in private:
        assert str(path) not in stdin and _escaped(path) not in stdin
    assert os.path.normcase(facts["python"]) == os.path.normcase(locations["runtime_python"])


@pytest.mark.fake_run
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_tool_free_stdin_is_the_envelope_alone(tmp_path, short_root, monkeypatch, transport):
    _, record, stdin, facts, _ = _run(tmp_path, short_root, monkeypatch, "structured_output", transport,
                                      materials=False)
    inspected = record["execution_log"]["attempts"][0]["provider_log"]
    assert stdin == inspected["actual_prompt"] == record["provider_trace"]["actual_prompt"]
    assert not stdin.startswith(INVOCATION_LOCATIONS_HEADING) and "material" not in facts
    assert (hashlib.sha256(stdin.encode("utf-8")).hexdigest()
            == record["execution_trace"]["attempts"][0]["prompt_envelope_sha256"])
    assert record["provider_trace"]["effective_configuration"].get("invocation_locations") is None


@pytest.mark.fake_run
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_child_temporary_folder_and_pytest_plugins_follow_the_prepared_environment(
        tmp_path, short_root, monkeypatch, transport):
    _, record, _, facts, _ = _run(tmp_path, short_root, monkeypatch, "code_change", transport, materials=True)
    environment = facts["environment"]
    folder = environment["TMPDIR"]
    expected = (Path(tempfile.gettempdir()).resolve() if transport == "claude_cli"
                else _attempt(record) / "work")
    if transport == "claude_cli":  # The per-call private folder Claude also uses for itself.
        assert Path(folder).parent == expected and Path(folder).name.startswith("crt-")
        assert environment["CLAUDE_CODE_TMPDIR"] == folder
    else:
        assert folder == str(expected / "tmp")
    assert environment["TEMP"] == environment["TMP"] == folder
    # Real children: Python creates its probe file there; on Windows cmd.exe and .NET use it too.
    assert facts["python_tempdir"] == folder
    if os.name == "nt":
        assert facts["cmd_temp"] == folder
        assert str(Path(facts["dotnet_temp_file"]).parent) == folder
    # Installed pytest plugins load through their entry points under both CLIs: the real pytest
    # run passes only when the entry-point plugin supplies its fixture.
    assert facts["pytest"][0] == 0, facts["pytest"][1]
    assert environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] is None


# The no-resources descriptions every current Claude/Codex agent envelope carries, computed from
# the 0.2.0.dev14 source (eed754b). Committed replay rebuilds these envelopes, so they must not change.
_FROZEN_DESCRIPTION_SHA256 = {
    ("claude_cli", "explicit_project_read_write"): "235442fea794b65de3077efda57e0e7e7afa784863bb3a160d9b7ecc6d49a52f",
    ("claude_cli", "own_draft_read_write"): "7d2d1839fb69f16915d72d0d4dcb10b7ef083d7238af526761ce01178fdda1d0",
    ("claude_cli", "none"): "cb7828e18e37e09240aac4cbee40c37a3bfac743cce583f9cb2802964f3e3773",
    ("codex_cli", "explicit_project_read_write"): "235442fea794b65de3077efda57e0e7e7afa784863bb3a160d9b7ecc6d49a52f",
    ("codex_cli", "own_draft_read_write"): "d65b56b0e33c35a6182f4516ce22942316b36df92145486d3850a0d46df9af7e",
    ("codex_cli", "none"): "cb7828e18e37e09240aac4cbee40c37a3bfac743cce583f9cb2802964f3e3773",
}


@pytest.mark.deterministic
@pytest.mark.parametrize("transport,workspace", sorted(_FROZEN_DESCRIPTION_SHA256))
def test_frozen_envelope_resource_descriptions_are_unchanged(tmp_path, transport, workspace):
    from test_agent_runtime_local_resources import profile as resource_profile
    binding = ({"executor_adapter_id": "claude_cli_adapter", "executor_adapter_revision": "v4",
                "provider_id": "anthropic"} if transport == "claude_cli" else
               {"executor_adapter_id": "codex_cli_agent_workspace_executor", "executor_adapter_revision": "v5",
                "provider_id": "openai", "model_id": "gpt-5.5", "reasoning_profile": "high"})
    tools = ("read", "search", "shell") if workspace != "none" else (("read",) if transport == "claude_cli" else ("shell",))
    profile = resource_profile(tmp_path, transport_kind=transport, attempt_workspace_policy=workspace,
                               tool_policy=tools, **binding)
    text = describe_local_resources(profile=profile, body=None)
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == _FROZEN_DESCRIPTION_SHA256[(transport, workspace)]
