"""The four packaged usage-pattern examples: definitions, preparation, checks and installed use.

Deterministic cases pin the registered requirements and prove that the check
rejects self-report, stale or echoed evidence over synthetic Claude and Codex
records. fake_run cases go through the real registration and
run_local_workflow_test path with a Claude process double that performs real
file effects and a real MCP exchange with the packaged fixture; they are not
model evidence. real_run cases start the selected CLI and are skipped unless
AGENT_RUNTIME_REAL_RUN=1 and AGENT_RUNTIME_CLAUDE_BIN or AGENT_RUNTIME_CODEX_BIN
name the native program. Optional: AGENT_RUNTIME_CLAUDE_MODEL (default
claude-opus-5-5), AGENT_RUNTIME_CODEX_MODEL (default gpt-6-astra),
AGENT_RUNTIME_ACCEPTANCE_EFFORT (default low) and AGENT_RUNTIME_EXAMPLE_ROOT
(default: an "agent runtime examples" folder next to this checkout).
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import zipfile

import pytest

from agent_runtime.foundation.foundation_environment_setup import load_runtime_config
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation.invocation_codex_module_invocation import validate_codex_mcp_connections
from agent_runtime.registry import RuntimeReleaseRegistry
from agent_runtime.registry.registry_local_persistence import load_runtime_registration
from agent_runtime.registry.registry_module_loading import load_module_registration
from agent_runtime.registry.registry_definition_selection import read_definition_selection
from agent_runtime.testing import conformance_fixture_mcp_serving as fixture
from agent_runtime.testing import conformance_pattern_examples as examples


HAS_MCP = importlib.util.find_spec("mcp") is not None
NEEDS_MCP = pytest.mark.skipif(not HAS_MCP, reason="unverified: the mcp package (cli_tools extra) is not installed")


@pytest.fixture
def short_root():
    """A short root with spaces: pytest's own tmp_path is too deep for Windows' 260-character paths
    once Runtime's Attempt folders and the CLI's files nest below it."""
    import shutil
    import tempfile
    import uuid
    root = Path(tempfile.gettempdir()) / f"ar ex {uuid.uuid4().hex[:6]}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


_ROOTS = {}


def _root(tmp_path):
    """A short example root with spaces for this test (one per tmp_path).

    The examples write an ordinary task source folder
    (source/.claude/skills/<skill>/runtime_modules/<module>/schemas/...) below
    each run folder; pytest's own tmp_path is too deep for Windows' 260-character
    paths once that layout nests below it. Users are told to keep roots short.
    """
    import tempfile
    import uuid
    if tmp_path not in _ROOTS:
        _ROOTS[tmp_path] = Path(tempfile.gettempdir()) / f"ar root {uuid.uuid4().hex[:6]}"
    return _ROOTS[tmp_path]


@pytest.fixture(autouse=True)
def _remove_short_roots():
    yield
    import shutil
    while _ROOTS:
        shutil.rmtree(_ROOTS.popitem()[1], ignore_errors=True)


# --- Definitions ------------------------------------------------------------------------------

@pytest.mark.deterministic
def test_each_pattern_registers_one_ordinary_module_with_its_documented_requirements():
    expected = {
        "structured_output": ("tool_free", "none", (), "denied"),
        "file_review": ("agent", "own_draft_read_write", ("edit", "read", "search", "shell"), "provider_managed"),
        "code_change": ("agent", "explicit_project_read_write", ("edit", "read", "search", "shell"),
                        "provider_managed"),
        "mcp_data": ("agent", "explicit_project_read_write", ("edit", "mcp__example_data", "read", "shell"),
                     "provider_managed"),
    }
    assert tuple(expected) == examples.PATTERN_NAMES
    for pattern, (mode, workspace, tools, network) in expected.items():
        exported = examples.build_pattern_workflow(pattern)
        assert exported == examples.build_pattern_workflow(pattern)  # Pure and deterministic.
        (module,) = exported.origin_bundle.modules
        requirements = module.execution_requirements
        assert (module.module_id, module.module_version) == (f"example_{pattern}", "v1")
        assert (requirements.execution_mode, requirements.attempt_workspace_policy, requirements.tool_policy,
                requirements.network_policy) == (mode, workspace, tools, network)
        assert requirements.semantic_input_delivery_mode == "inline" and requirements.max_attempts == 1
        assert exported.origin_bundle.execution_profiles == () and exported.origin_bundle.execution_variant_policies == ()
        assert exported.workflow_release.workflow_id == module.module_id and len(exported.workflow_release.nodes) == 1
        # Codex serves read/search/edit only through its shell; never a shell-less file tool set.
        assert not ({"read", "search", "edit"} & set(tools)) or "shell" in tools
        RuntimeReleaseRegistry().register_bundle(exported.origin_bundle)
    with pytest.raises(ValueError, match="unknown pattern"):
        examples.build_pattern_workflow("callback_graph")


@pytest.mark.deterministic
def test_requirements_presets_are_explicit_values_tied_to_the_reviewer_preset():
    from dataclasses import replace
    from agent_runtime import MODULE_REQUIREMENT_PRESETS, ModuleReviewer, module_requirements_preset
    reviewer = ModuleReviewer._default_execution_requirements
    assert MODULE_REQUIREMENT_PRESETS == ("structured_output", "file_review", "code_change", "mcp_data")
    assert module_requirements_preset("file_review") == replace(reviewer, max_attempts=1)
    assert module_requirements_preset("file_review", max_attempts=3) == reviewer
    assert module_requirements_preset("code_change").attempt_workspace_policy == "explicit_project_read_write"
    assert module_requirements_preset("mcp_data", mcp_connections=("zeta", "alpha")).tool_policy == (
        "edit", "mcp__alpha", "mcp__zeta", "read", "shell")
    for preset in MODULE_REQUIREMENT_PRESETS:
        value = module_requirements_preset(preset, mcp_connections=("x",) if preset == "mcp_data" else ())
        assert value.schema_version == "module_execution_requirements_v2" and value.max_attempts == 1
    for arguments, message in (
            (("unknown",), {}), (("mcp_data",), {}), (("code_change",), {"mcp_connections": ("x",)}),
            (("mcp_data",), {"mcp_connections": ("Bad Name",)}), (("mcp_data",), {"mcp_connections": ("x", "x")}),
            (("mcp_data",), {"mcp_connections": ["x"]}), (("file_review",), {"max_attempts": 0})):
        with pytest.raises(ValueError):
            module_requirements_preset(*arguments, **message)


_PRESET_DOCUMENTS = ("README.md", "src/agent_runtime/README.md", "src/agent_runtime/skills/agent-runtime-usage/SKILL.md",
                     "docs/agent_runtime_registration_runbook.md", "src/agent_runtime/docs/agent_runtime_registration_runbook.md")


@pytest.mark.deterministic
@pytest.mark.parametrize("document", _PRESET_DOCUMENTS)
def test_documented_preset_rows_state_the_values_the_function_returns(document):
    """Docs cite the presets; this keeps every documented row equal to the code, not a second table."""
    from agent_runtime import MODULE_REQUIREMENT_PRESETS, module_requirements_preset
    text = (Path(__file__).resolve().parents[1] / document).read_text(encoding="utf-8")
    for preset in MODULE_REQUIREMENT_PRESETS:
        value = module_requirements_preset(preset, mcp_connections=("name",) if preset == "mcp_data" else ())
        # The one requirements row per preset (other tables, such as artifact lists, name no execution mode).
        (row,) = [line for line in text.splitlines() if line.startswith(f"| `{preset}`")
                  and ("`tool_free`" in line or "`agent`" in line)]
        for field in (value.execution_mode, value.attempt_workspace_policy, value.network_policy):
            assert f"`{field}`" in row, (document, preset, field)
        tools = [tool for tool in value.tool_policy if not tool.startswith("mcp__")]
        if tools:
            assert ", ".join(tools) in row, (document, preset)
        assert ("mcp__<name>" in row) == (preset == "mcp_data"), (document, preset)


# --- Preparation ------------------------------------------------------------------------------

@pytest.mark.deterministic
def test_prepare_creates_a_fresh_folder_per_run_under_a_root_with_spaces(tmp_path):
    root = _root(tmp_path)
    first = examples.prepare_pattern_example(root, "structured_output", "claude_cli")
    second = examples.prepare_pattern_example(root, "structured_output", "claude_cli")
    assert first["run_directory"] != second["run_directory"]
    assert sorted(path.name for path in root.iterdir()) == sorted(
        Path(item["run_directory"]).name for item in (first, second))
    for manifest in (first, second):
        folder = Path(manifest["run_directory"])
        assert " " in str(folder) and folder.parent == root.resolve()
        assert json.loads((folder / "run.json").read_text(encoding="utf-8")) == manifest
        saved = load_runtime_registration(Path(manifest["host_root"]), "workflow", "example_structured_output", "v1")
        assert saved.release == examples.build_pattern_workflow("structured_output").workflow_release
        payload = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))
        assert payload["order_id"] == manifest["expected"]["order_id"] and payload["order_id"] in payload["note"]
        argv = manifest["test_run_argv"]
        # The run command names the code-written selection, never an ID, version, ref or hash.
        assert argv[:2] == ["agent-runtime-test-run", "--root"] and argv[argv.index("--selection") + 1] == str(
            folder / "selection.json")
        assert not {"--workflow", "--version", "--expected-module-id", "--workflow-ref", "--workflow-sha256"} & set(argv)
        assert argv[argv.index("--record-out") + 1] == manifest["record_path"] == str(folder / "record.json")
        selection = read_definition_selection(folder / "selection.json")
        assert (selection.workflow_id, selection.workflow_version, selection.module_id) == (
            "example_structured_output", "v1", "example_structured_output")
        assert manifest["registered"]["workflow_release_ref"] == saved.release.release_ref == selection.workflow_release_ref
        assert manifest["registered"]["workflow_release_sha256"] == saved.release.release_sha256
        # The registered source is an ordinary v4 folder whose loaded content is the pattern definition.
        source = load_module_registration(folder / "source", skill_id="runtime-pattern-examples",
                                          module_id="example_structured_output")
        assert source == examples.pattern_source("structured_output")
        assert "'" + manifest["host_root"] + "'" in manifest["powershell"]  # Quoted: the path has spaces.
    assert first["nonce"] != second["nonce"]


@pytest.mark.deterministic
def test_prepare_never_reuses_an_existing_run_folder(tmp_path, monkeypatch):
    root = _root(tmp_path)
    manifest = examples.prepare_pattern_example(root, "structured_output", "codex_cli")
    folder = Path(manifest["run_directory"])
    stamp = folder.name.split("-")[2]  # UTC yymmddHHMMSS.
    monkeypatch.setattr(examples.secrets, "token_hex", lambda size: manifest["nonce"])

    class Clock:
        @staticmethod
        def now(zone):
            from datetime import datetime
            return datetime.strptime(stamp, "%y%m%d%H%M%S")
    monkeypatch.setattr(examples, "datetime", Clock)
    before = (folder / "run.json").read_bytes()
    with pytest.raises(FileExistsError):
        examples.prepare_pattern_example(root, "structured_output", "codex_cli")
    assert (folder / "run.json").read_bytes() == before


@pytest.mark.deterministic
def test_file_review_fact_exists_only_in_the_frozen_material(tmp_path):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "file_review", "claude_cli")
    folder = Path(manifest["run_directory"])
    material = folder / "materials" / "release_notes.md"
    code = next(line.split(": ", 1)[1] for line in material.read_text(encoding="utf-8").splitlines()
                if line.startswith("Release code:"))
    resources = json.loads(Path(manifest["resources_path"]).read_text(encoding="utf-8"))
    assert resources["material_files"] == [{"relative_path": "release_notes.md", "executable": False,
                                            "sha256": hashlib.sha256(material.read_bytes()).hexdigest()}]
    assert code not in Path(manifest["input_path"]).read_text(encoding="utf-8")
    assert code not in json.dumps(manifest) and manifest["project_directory"] is None


@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_code_change_starts_failing_with_a_transport_specific_check_command(tmp_path, transport):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "code_change", transport)
    project = Path(manifest["project_directory"])
    payload = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))
    assert manifest["expected"]["initial_check"]["returncode"] == 1
    assert manifest["expected"]["initial_check"]["last_line"].startswith(f"CHECK FAILED nonce={manifest['nonce']}")
    if transport == "claude_cli":
        assert payload["check_command"] == "sh run_check.sh" and manifest["command_permits"] == ["sh run_check.sh"]
        script = (project / "run_check.sh").read_bytes()
        assert b"\r" not in script and script.startswith(b"#!/bin/sh\n")
        assert manifest["test_run_argv"][-4:-2] == ["--command-permit", "sh run_check.sh"]
    else:
        assert manifest["command_permits"] == [] and "--command-permit" not in manifest["test_run_argv"]
        assert not (project / "run_check.sh").exists()
        assert payload["check_command"].endswith(" check_pricing.py")
        if os.name == "nt":
            assert payload["check_command"].startswith("& ")


@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_mcp_data_writes_one_credential_free_native_connection_entry(tmp_path, transport, monkeypatch):
    monkeypatch.setattr(examples.importlib.util, "find_spec", lambda name: object())
    manifest = examples.prepare_pattern_example(_root(tmp_path), "mcp_data", transport)
    config = load_runtime_config(Path(manifest["host_root"]))
    entries = config["mcp_servers"][transport]
    assert list(entries) == ["example_data"] and entries["example_data"]["environment_keys"] == []
    entry = entries["example_data"]["config"]
    assert entry["command"] == sys.executable
    assert Path(entry["args"][0]) == Path(fixture.__file__).resolve()
    assert entry["args"][1:] == ["--audit", manifest["expected"]["audit_path"]]
    if transport == "codex_cli":
        validate_codex_mcp_connections(entries)
        assert entry["default_tools_approval_mode"] == "approve"
    assert Path(manifest["project_directory"]).is_dir() and not any(Path(manifest["project_directory"]).iterdir())


@pytest.mark.deterministic
def test_mcp_data_names_the_missing_fixture_dependency_before_writing_config(tmp_path, monkeypatch):
    monkeypatch.setattr(examples.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(ModuleNotFoundError, match="cli_tools"):
        examples.prepare_pattern_example(_root(tmp_path), "mcp_data", "claude_cli")
    (folder,) = _root(tmp_path).iterdir()
    assert not (folder / "host" / ".runtime" / "config.json").exists()


# --- Checks over synthetic records (no CLI, no model) ---------------------------------------

def _record(folder, calls, output, status="completed", **identity):
    """A synthetic record that executed the folder's registered definition unless identity overrides it.

    Like Test Run, its Attempt folder holds an archive of the record (without attempt_workspaces).
    """
    registered = json.loads((folder / "run.json").read_text(encoding="utf-8"))["registered"]
    record = {"status": status, "output": output, "persistence": "not_requested",
              "execution_log": {"attempts": [{"attempt_id": "attempt", "tool_calls": calls}]}, **registered, **identity}
    archive = folder / "attempt" / "archive" / "execution_record.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(json.dumps(record), encoding="utf-8")
    return {**record, "attempt_workspaces": [{"attempt_id": "attempt", "workspace_path": str(folder / "attempt"),
                                              "archive_path": str(archive), "sealed": True,
                                              "seal_skipped_reason": None}]}


def _codex_command(command, output, exit_code=0):
    return {"tool_name": "command_execution", "status": "completed" if exit_code == 0 else "failed",
            "request": {"command": command},
            "response": {"command": command, "aggregated_output": output, "exit_code": exit_code, "status": "completed"}}


def _claude(name, request, content, **result):
    response = {"tool_result": {"type": "tool_result", "is_error": False, "content": content}}
    if result:
        response["tool_use_result"] = result
    return {"tool_name": name, "status": "completed", "request": request, "response": response}


def _failed(result):
    return {row["check"] for row in result["checks"] if not row["passed"]}


@pytest.mark.deterministic
def test_structured_output_check_compares_semantics_with_the_generated_input(tmp_path):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "structured_output", "codex_cli")
    folder, expected = Path(manifest["run_directory"]), manifest["expected"]
    good = {"order_id": expected["order_id"], "urgent": expected["urgent"], "total_quantity": expected["total_quantity"],
            "lines": [{"sku": sku, "quantity": quantity} for sku, quantity in expected["lines"].items()]}
    assert examples.check_pattern_example(folder, _record(folder, [], good))["passed"]
    wrong = {**good, "total_quantity": expected["total_quantity"] + 1, "urgent": not expected["urgent"]}
    result = examples.check_pattern_example(folder, _record(folder, [], wrong))
    assert _failed(result) == {"output_total_quantity", "output_urgent"}
    assert json.loads((folder / "check.json").read_text(encoding="utf-8")) == result
    # The right output from another registered version is not evidence for this definition.
    other = examples.check_pattern_example(folder, _record(folder, [], good, workflow_release_sha256="0" * 64))
    assert _failed(other) == {"executed_the_registered_definition"}


def _as_dev12_folder(folder):
    """Rewrite run.json as 0.2.0.dev12 wrote it (runtime_pattern_example_v1: no source, selection or record path)."""
    manifest = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    for key in ("source_directory", "selection_path", "record_path", "inspect_argv"):
        manifest.pop(key)
    manifest["schema_version"] = "runtime_pattern_example_v1"
    (folder / "run.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "selection.json").unlink()
    return manifest


@pytest.mark.deterministic
def test_check_rereads_a_dev12_run_folder_by_its_recorded_readback(tmp_path):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "code_change", "codex_cli")
    folder, project = Path(manifest["run_directory"]), Path(manifest["project_directory"])
    _as_dev12_folder(folder)
    command = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))["check_command"]
    pricing = project / "pricing.py"
    pricing.write_bytes(pricing.read_bytes().replace(b"unit_price_cents + quantity", b"unit_price_cents * quantity"))
    marker = (f"CHECK PASSED nonce={manifest['nonce']} "
              f"pricing_sha256={hashlib.sha256(pricing.read_bytes()).hexdigest()}")
    # A dev12 record still carries the model's restated checker line; it is neither required nor trusted.
    output = {"changed_files": ["pricing.py"], "check_command": command, "check_result_line": marker, "passed": True}
    record = _record(folder, [_codex_command(command, marker + "\r\n")], output)
    (folder / "record.json").write_text(json.dumps(record), encoding="utf-8")
    result = examples.check_pattern_example(folder)
    assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    other = examples.check_pattern_example(folder, {**record, "module_release_sha256": "0" * 64})
    assert _failed(other) == {"executed_the_registered_definition"}


@pytest.mark.deterministic
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_check_command_reads_a_saved_record_as_powershell_redirection_writes_it(tmp_path, capsys, encoding):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "structured_output", "claude_cli")
    folder, expected = Path(manifest["run_directory"]), manifest["expected"]
    output = {"order_id": expected["order_id"], "urgent": expected["urgent"], "total_quantity": expected["total_quantity"],
              "lines": [{"sku": sku, "quantity": quantity} for sku, quantity in expected["lines"].items()]}
    saved = tmp_path / "saved record.json"
    saved.write_text(json.dumps(_record(folder, [], output)), encoding=encoding)
    assert examples.main(["check", "--run-dir", str(folder), "--record", str(saved)]) == 0
    assert json.loads(capsys.readouterr().out)["passed"] is True
    output["order_id"] = "ORD-OTHER"
    saved.write_text(json.dumps(_record(folder, [], output)), encoding=encoding)
    assert examples.main(["check", "--run-dir", str(folder), "--record", str(saved)]) == 1


def _review_setup(tmp_path, transport):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "file_review", transport)
    folder = Path(manifest["run_directory"])
    text = (folder / "materials/release_notes.md").read_text(encoding="utf-8")
    code = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Release code:"))
    return folder, code


def _write_review(folder, code):
    scratch = folder / "attempt" / "work" / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    body = f"# Review: release_notes.md\nRelease code: {code}\n- Rollback window is 30 minutes but the runbook says 15.\n"
    (scratch / "review.md").write_text(body, encoding="utf-8")
    return body


@pytest.mark.deterministic
def test_file_review_check_requires_the_written_file_and_a_logged_read_back(tmp_path):
    folder, code = _review_setup(tmp_path, "codex_cli")
    output = {"review_file": "review.md", "release_code": code, "finding_count": 1,
              "readback_first_line": "# Review: release_notes.md"}
    write = _codex_command(f"Set-Content review.md '# Review ... Release code: {code}'", "")
    readback = _codex_command("Get-Content review.md", None)
    # Self-report only: correct output, no file and no read step.
    result = examples.check_pattern_example(folder, _record(folder, [], output))
    assert {"review_file_written", "review_read_back_in_log"} <= _failed(result) and not result["passed"]
    body = _write_review(folder, code)
    # The write command itself contains the fact, so it is not a read-back.
    assert _failed(examples.check_pattern_example(folder, _record(folder, [write], output))) == {"review_read_back_in_log"}
    readback["response"]["aggregated_output"] = body
    assert examples.check_pattern_example(folder, _record(folder, [write, readback], output))["passed"]
    wrong_name = examples.check_pattern_example(folder, _record(folder, [write, readback], {**output, "review_file": "x.md"}))
    assert _failed(wrong_name) == {"output_review_file"}
    # A stale review from another run does not carry this run's material-only fact.
    _write_review(folder, "RC-000000")
    stale = examples.check_pattern_example(folder, _record(folder, [write, readback], output))
    assert "review_contains_material_fact" in _failed(stale)


@pytest.mark.deterministic
def test_code_change_check_uses_the_host_rerun_and_the_final_codes_command_result(tmp_path):
    manifest = examples.prepare_pattern_example(_root(tmp_path), "code_change", "codex_cli")
    folder, project = Path(manifest["run_directory"]), Path(manifest["project_directory"])
    command = json.loads(Path(manifest["input_path"]).read_text())["check_command"]
    claimed_output = {"changed_files": ["pricing.py"], "check_command": command, "passed": True}
    claimed = examples.check_pattern_example(folder, _record(folder, [], claimed_output))
    assert {"only_pricing_changed", "host_rerun_passes", "model_ran_check_on_final_code",
            "output_passed_agrees_with_host_rerun"} <= _failed(claimed)
    pricing = project / "pricing.py"
    pricing.write_bytes(pricing.read_bytes().replace(b"unit_price_cents + quantity", b"unit_price_cents * quantity"))
    marker = (f"CHECK PASSED nonce={manifest['nonce']} "
              f"pricing_sha256={hashlib.sha256(pricing.read_bytes()).hexdigest()}")
    output = dict(claimed_output)
    echoed = _codex_command(f'Write-Output "{marker}"', marker + "\r\n")
    assert _failed(examples.check_pattern_example(folder, _record(folder, [echoed], output))) == {
        "model_ran_check_on_final_code"}
    ran = _codex_command(command, marker + "\r\n")
    passed = examples.check_pattern_example(folder, _record(folder, [ran], output))
    assert passed["passed"]
    # The pass evidence is attached by code from the logged command result, not restated by the model.
    assert passed["artifacts"]["command_evidence"] == [{"log_index": 0, "tool_name": "command_execution",
                                                        "request": {"command": command}, "observed_last_line": marker}]
    absolute = {**output, "changed_files": [str(pricing), "./pricing.py"]}
    assert examples.check_pattern_example(folder, _record(folder, [ran], absolute))["passed"]
    # Final output must state the actual change, the task's command and the observed checker line.
    for field, value, failed in (("changed_files", ["not_pricing.py"], "output_changed_files"),
                                 ("changed_files", [], "output_changed_files"),
                                 ("check_command", "python check_pricing.py", "output_check_command"),
                                 ("passed", False, "output_passed_agrees_with_host_rerun")):
        result = examples.check_pattern_example(folder, _record(folder, [ran], {**output, field: value}))
        assert _failed(result) == {failed}, (field, value)
    (project / "unexpected.txt").write_text("not allowed\n", encoding="utf-8")
    assert _failed(examples.check_pattern_example(folder, _record(folder, [ran], output))) == {"no_files_added"}
    (project / "unexpected.txt").unlink()
    (project / "check_pricing.py").write_text("print('CHECK PASSED')\n", encoding="utf-8")
    assert "only_pricing_changed" in _failed(examples.check_pattern_example(folder, _record(folder, [ran], output)))


def _mcp_setup(tmp_path, transport, monkeypatch):
    monkeypatch.setattr(examples.importlib.util, "find_spec", lambda name: object())
    manifest = examples.prepare_pattern_example(_root(tmp_path), "mcp_data", transport)
    payload = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))
    return manifest, payload, Path(manifest["run_directory"]), Path(manifest["project_directory"])


def _save_mcp_files(project, payload, result):
    data = json.dumps(result)
    (project / payload["data_file"]).write_text(data, encoding="utf-8")
    summary = {"response_id": result["response_id"], "warehouse": result["warehouse"],
               "total_units": sum(item["units"] for item in result["items"]), "sku_count": len(result["items"])}
    (project / payload["summary_file"]).write_text(json.dumps(summary), encoding="utf-8")
    return data, summary


@pytest.mark.deterministic
def test_mcp_data_check_requires_the_fixture_audit_saved_bytes_and_ordered_read_back(tmp_path, monkeypatch):
    manifest, payload, folder, project = _mcp_setup(tmp_path, "codex_cli", monkeypatch)
    audit_path = Path(manifest["expected"]["audit_path"])
    requested = {"warehouse": payload["warehouse"], "request_tag": payload["request_tag"]}
    result = fixture.stock_snapshot(**requested)
    data, summary = _save_mcp_files(project, payload, result)
    output = {**{key: summary[key] for key in ("response_id", "total_units", "sku_count")},
              "data_file": payload["data_file"], "summary_file": payload["summary_file"]}
    mcp = {"tool_name": "get_stock_snapshot", "status": "completed", "request": dict(requested), "response": result}
    readback = _codex_command(f"Get-Content {payload['data_file']}", data)
    # Without the fixture's own audit, an invented result is not evidence of an MCP call.
    fabricated = examples.check_pattern_example(folder, _record(folder, [mcp, readback], output))
    assert {"fixture_received_tools_call", "mcp_call_in_log"} <= _failed(fabricated)
    # A real call for another warehouse with the right request_tag is not this task's data.
    fixture.append_audit(audit_path, tool=fixture.TOOL_NAME, arguments={**requested, "warehouse": "wrong warehouse"},
                         result={**result, "warehouse": "wrong warehouse"})
    assert "fixture_received_tools_call" in _failed(
        examples.check_pattern_example(folder, _record(folder, [mcp, readback], output)))
    fixture.append_audit(audit_path, tool=fixture.TOOL_NAME, arguments=requested, result=result)
    assert examples.check_pattern_example(folder, _record(folder, [mcp, readback], output))["passed"]
    absolute = {**output, "data_file": str(project / payload["data_file"])}
    assert examples.check_pattern_example(folder, _record(folder, [mcp, readback], absolute))["passed"]
    assert _failed(examples.check_pattern_example(folder, _record(folder, [readback, mcp], output))) == {
        "data_file_read_back_in_log"}
    for field, value in (("data_file", "elsewhere/stock_snapshot.json"), ("summary_file", "summary.json"),
                         ("data_file", None)):
        checked = examples.check_pattern_example(folder, _record(folder, [mcp, readback], {**output, field: value}))
        assert _failed(checked) == {"output_artifact_paths"}, (field, value)
    # The actual audited response, minus two of its fields, is still not the complete saved result.
    partial = {key: value for key, value in result.items() if key not in ("request_tag", "source")}
    assert set(result) - set(partial) == {"request_tag", "source"} and partial["response_id"] == result["response_id"]
    (project / payload["data_file"]).write_text(json.dumps(partial), encoding="utf-8")
    assert _failed(examples.check_pattern_example(folder, _record(folder, [mcp, readback], output))) == {
        "data_file_matches_fixture_response"}
    (project / payload["data_file"]).write_text(data, encoding="utf-8")
    (project / payload["summary_file"]).write_text(json.dumps({**summary, "total_units": 1}), encoding="utf-8")
    assert _failed(examples.check_pattern_example(folder, _record(folder, [mcp, readback], output))) == {
        "summary_file_derived"}


# --- Real registration and Test Run with a Claude process double ---------------------------

def _double(monkeypatch, behavior):
    """Run the real Test Run with a Claude process double that performs actual file and MCP effects."""
    from test_agent_runtime_claude_native_tools import _init, _result, _tool_result, _tool_use
    manifests, launches = [], []
    prepare = examples.prepare_pattern_example

    def capture(*args, **kwargs):
        manifests.append(prepare(*args, **kwargs))
        return manifests[-1]
    monkeypatch.setattr(examples, "prepare_pattern_example", capture)
    base = claude.ClaudeAdapter

    class Adapter(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, process_runner=self._process)

        def _process(self, **fields):
            launches.append(fields)
            argv, cwd = fields["argv"], Path(fields["cwd"])
            tools = [name for name in argv[argv.index("--tools") + 1].split(",") if name]
            init = _init(tuple(tools))
            lines, counter = [], iter(range(1000))

            def emit(event):
                line = json.dumps(event)
                assert fields["on_stdout_line"](line)
                lines.append(line)

            def call(name, result_text, extra=None, /, **arguments):
                identity = f"toolu_{next(counter)}"
                emit(_tool_use(identity, name, **arguments))
                event = _tool_result(identity, content=result_text)
                if extra is not None:
                    event["tool_use_result"] = extra
                emit(event)
            emit(init)
            output = behavior(manifests[-1], argv, cwd, call)
            emit(_result(structured_output=output))
            raw = "\n".join(lines).encode()
            completed = subprocess.CompletedProcess(argv, 0, raw.decode(), "")
            completed.stdout_bytes, completed.stderr_bytes = raw, b""
            return completed
    monkeypatch.setattr(claude, "ClaudeAdapter", Adapter)
    return launches


def _structured(manifest, argv, cwd, call, *, honest=True):
    expected = manifest["expected"]
    return {"order_id": expected["order_id"], "urgent": expected["urgent"],
            "total_quantity": expected["total_quantity"] + (0 if honest else 3),
            "lines": [{"sku": sku, "quantity": quantity} for sku, quantity in expected["lines"].items()]}


def _review(manifest, argv, cwd, call, *, honest=True):
    material = cwd / "materials" / "source" / "release_notes.md"  # Runtime's frozen copy.
    text = material.read_text(encoding="utf-8")
    call("Read", text, file_path=str(material))
    code = next(line.split(": ", 1)[1] for line in text.splitlines() if line.startswith("Release code:"))
    body = f"# Review: release_notes.md\nRelease code: {code}\n- Rollback window 30 vs 15 minutes.\n"
    if honest:
        review = cwd / "scratch" / "review.md"
        review.write_text(body, encoding="utf-8")
        call("Write", "File created successfully", file_path=str(review), content=body)
        call("Read", review.read_text(encoding="utf-8"), file_path=str(review))
    return {"review_file": "review.md", "release_code": code, "finding_count": 1,
            "readback_first_line": "# Review: release_notes.md"}


def _code(manifest, argv, cwd, call, *, honest=True):
    command = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))["check_command"]
    if honest:
        pricing = cwd / "pricing.py"
        pricing.write_bytes(pricing.read_bytes().replace(b"unit_price_cents + quantity", b"unit_price_cents * quantity"))
        call("Edit", "The file has been updated.", file_path=str(pricing))
        # The double has no Git Bash; it runs what run_check.sh runs and reports it as that Bash call.
        completed = subprocess.run([sys.executable, "check_pricing.py"], cwd=cwd, capture_output=True, text=True,
                                   env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=120)
        stdout = completed.stdout
    else:
        stdout = f"CHECK PASSED nonce={manifest['nonce']} pricing_sha256=unchanged\n"
        command = f"echo '{stdout.strip()}'"
    call("Bash", stdout, {"stdout": stdout, "stderr": "", "interrupted": False}, command=command)
    return {"changed_files": ["pricing.py"], "check_command": command, "passed": True}


def _mcp(manifest, argv, cwd, call, *, honest=True):
    payload = json.loads(Path(manifest["input_path"]).read_text(encoding="utf-8"))
    arguments = {"warehouse": payload["warehouse"], "request_tag": payload["request_tag"]}
    if honest:
        private = Path(argv[argv.index("--mcp-config") + 2])  # Runtime's private mcp.json for this call.
        entry = json.loads(private.read_text(encoding="utf-8"))["mcpServers"][examples.MCP_CONNECTION]
        result = asyncio.run(_call_fixture(entry, arguments))
    else:
        result = fixture.stock_snapshot(payload["warehouse"], payload["request_tag"])  # Invented, never served.
    call("mcp__example_data__get_stock_snapshot", json.dumps(result), **arguments)
    data_file = cwd / payload["data_file"]
    data_file.write_text(json.dumps(result), encoding="utf-8")
    call("Write", "File created successfully", file_path=str(data_file), content=json.dumps(result))
    read = json.loads(data_file.read_text(encoding="utf-8"))
    call("Read", json.dumps(read), file_path=str(data_file))
    summary = {"response_id": read["response_id"], "warehouse": read["warehouse"],
               "total_units": sum(item["units"] for item in read["items"]), "sku_count": len(read["items"])}
    (cwd / payload["summary_file"]).write_text(json.dumps(summary), encoding="utf-8")
    call("Write", "File created successfully", file_path=str(cwd / payload["summary_file"]), content=json.dumps(summary))
    return {key: summary[key] for key in ("response_id", "total_units", "sku_count")} | {
        "data_file": payload["data_file"], "summary_file": payload["summary_file"]}


async def _call_fixture(entry, arguments):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with stdio_client(StdioServerParameters(command=entry["command"], args=entry["args"])) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert [tool.name for tool in listed.tools] == [fixture.TOOL_NAME]
            result = await session.call_tool(fixture.TOOL_NAME, arguments)
            assert not result.isError
            return result.structuredContent


_BEHAVIORS = {"structured_output": _structured, "file_review": _review, "code_change": _code, "mcp_data": _mcp}
_SELF_REPORT_FAILURES = {
    "structured_output": {"output_total_quantity"},
    "file_review": {"review_file_written", "review_read_back_in_log"},
    "code_change": {"only_pricing_changed", "host_rerun_passes", "model_ran_check_on_final_code"},
    "mcp_data": {"fixture_received_tools_call", "mcp_call_in_log", "data_file_matches_fixture_response"},
}


@pytest.mark.fake_run
@pytest.mark.parametrize("pattern", examples.PATTERN_NAMES)
@pytest.mark.parametrize("honest", [True, False], ids=["actual_effects", "self_report"])
def test_registered_test_run_with_a_claude_double_passes_only_with_actual_effects(tmp_path, short_root, monkeypatch,
                                                                                   pattern, honest):
    from test_agent_runtime_claude_native_tools import _fake_cli
    if pattern == "mcp_data" and not HAS_MCP:
        pytest.skip("unverified: the mcp package (cli_tools extra) is not installed")
    behavior = _BEHAVIORS[pattern]
    launches = _double(monkeypatch, lambda *args: behavior(*args, honest=honest))
    result = examples.run_pattern_example(short_root, pattern, "claude_cli", cli_path=_fake_cli(tmp_path, monkeypatch))
    folder = Path(result["run_directory"])
    record = json.loads((folder / "record.json").read_text(encoding="utf-8"))
    assert len(launches) == 1 and record["status"] == "completed" and result["status"] == "completed", (
        record.get("failure_detail"), record.get("provider_trace", {}).get("adapter_failure"))
    assert json.loads((folder / "check.json").read_text(encoding="utf-8")) == result
    assert result["attempt_workspace"]["sealed"] is True
    effective = record["provider_trace"]["effective_configuration"]
    if pattern == "code_change":
        assert effective["command_permits"] == ["sh run_check.sh"]
        assert effective["allowed_rules"][-1] == "Bash(sh run_check.sh)"
    else:
        assert effective["command_permits"] == []
    if pattern == "mcp_data":
        assert [entry["name"] for entry in effective["mcp_connections"]] == ["example_data"]
    if honest:
        assert result["passed"], [row for row in result["checks"] if not row["passed"]]
    else:
        assert not result["passed"] and _SELF_REPORT_FAILURES[pattern] <= _failed(result)


@pytest.mark.fake_run
@NEEDS_MCP
def test_packaged_fixture_serves_a_standard_mcp_client_and_audits_what_it_returned(tmp_path):
    audit = tmp_path / "audit folder" / "audit.jsonl"
    audit.parent.mkdir()
    entry = {"command": sys.executable, "args": [str(Path(fixture.__file__).resolve()), "--audit", str(audit)]}
    first = asyncio.run(_call_fixture(entry, {"warehouse": "north", "request_tag": "tag-1"}))
    second = asyncio.run(_call_fixture(entry, {"warehouse": "north", "request_tag": "tag-2"}))
    rows = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    assert [row["result"] for row in rows] == [first, second]
    assert [row["arguments"]["request_tag"] for row in rows] == ["tag-1", "tag-2"]
    assert first["response_id"] != second["response_id"] and [item["sku"] for item in first["items"]] == list(fixture.SKUS)


# --- The installed wheel, outside this checkout ----------------------------------------------

@pytest.mark.deterministic
def test_installed_wheel_contains_and_runs_the_examples_outside_the_checkout(tmp_path):
    from test_agent_runtime_packaging_boundary import _build_runtime_wheel
    wheel = _build_runtime_wheel(tmp_path / "build")
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
        for name in ("testing/conformance_pattern_examples.py", "testing/conformance_fixture_mcp_serving.py",
                     "registry/registry_definition_selection.py", "inspection/inspection_test_run_reading.py",
                     "invocation/invocation_local_resource_preparation.py",
                     "skills/agent-runtime-registration/SKILL.md", "skills/agent-runtime-test-run/SKILL.md",
                     "skills/agent-runtime-usage/SKILL.md", "docs/agent_runtime_registration_runbook.md",
                     "docs/agent_runtime_reviewer_api.md", "README.md"):
            assert f"agent_runtime/{name}" in members, name
        archive.extractall(tmp_path / "site with spaces")
    site, root = tmp_path / "site with spaces", _root(tmp_path)
    source = textwrap.dedent("""
        import json, runpy, sys
        sys.path.insert(0, sys.argv[1])
        import agent_runtime.testing.conformance_pattern_examples as examples
        assert examples.__file__.startswith(sys.argv[1]), examples.__file__
        sys.argv = ["examples", "prepare", "--pattern", "code_change", "--transport", "codex_cli", "--root", sys.argv[2]]
        try:
            runpy.run_module(examples.__name__, run_name="__main__")
        except SystemExit as stop:
            assert stop.code in (0, None), stop.code
    """)
    completed = subprocess.run([sys.executable, "-I", "-c", source, str(site), str(root)], cwd=tmp_path,
                               capture_output=True, text=True, timeout=300)
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(completed.stdout)
    assert Path(manifest["run_directory"]).parent == root.resolve()
    assert manifest["expected"]["initial_check"]["returncode"] == 1
    assert manifest["check_argv"][1:3] == ["-m", "agent_runtime.testing.conformance_pattern_examples"]
    # The installed package registered the written source and wrote the selection the run command names.
    assert Path(manifest["selection_path"]).is_file()
    assert manifest["test_run_argv"][manifest["test_run_argv"].index("--selection") + 1] == manifest["selection_path"]
    for argv in (["agent_runtime.registry.registry_local_persistence", "register-module", "--help"],
                 ["agent_runtime.registry.registry_local_persistence", "select", "--help"],
                 ["agent_runtime.testing.conformance_local_test_run", "--help"],
                 ["agent_runtime.inspection.inspection_snapshot_exporting", "--help"]):
        command = ("import runpy, sys; sys.path.insert(0, sys.argv[1]); module = sys.argv[2]; "
                   "sys.argv = [module, *sys.argv[3:]]; runpy.run_module(module, run_name='__main__')")
        shown = subprocess.run([sys.executable, "-I", "-c", command, str(site), *argv], cwd=tmp_path,
                               capture_output=True, text=True, timeout=120)
        assert shown.returncode == 0, (argv, shown.stderr)
        expected = {"register-module": "--preset", "select": "--release-store"}.get(argv[1], "")
        assert expected in shown.stdout
        if argv[0].endswith("conformance_local_test_run"):
            assert "--selection" in shown.stdout and "--material-root" in shown.stdout and "--record-out" in shown.stdout
        if argv[0].endswith("inspection_snapshot_exporting"):
            assert "--test-run-record" in shown.stdout


# --- Real CLIs (skipped unless explicitly enabled) --------------------------------------------

REAL = os.environ.get("AGENT_RUNTIME_REAL_RUN") == "1"
BINARIES = {"claude_cli": os.environ.get("AGENT_RUNTIME_CLAUDE_BIN"), "codex_cli": os.environ.get("AGENT_RUNTIME_CODEX_BIN")}
MODELS = {"claude_cli": os.environ.get("AGENT_RUNTIME_CLAUDE_MODEL", "claude-opus-5-5"),
          "codex_cli": os.environ.get("AGENT_RUNTIME_CODEX_MODEL", "gpt-6-astra")}


@pytest.mark.real_run
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
@pytest.mark.parametrize("pattern", examples.PATTERN_NAMES)
def test_real_cli_runs_each_pattern_with_actual_effects(pattern, transport):
    if not (REAL and BINARIES[transport]):
        pytest.skip(f"unverified: set AGENT_RUNTIME_REAL_RUN=1 and AGENT_RUNTIME_{transport[:-4].upper()}_BIN")
    if pattern == "mcp_data" and not HAS_MCP:
        pytest.skip("unverified: the mcp package (cli_tools extra) is not installed")
    root = Path(os.environ.get("AGENT_RUNTIME_EXAMPLE_ROOT")
                or Path(__file__).resolve().parents[2] / "agent runtime examples")
    result = examples.run_pattern_example(root, pattern, transport, cli_path=BINARIES[transport],
                                          model_id=MODELS[transport],
                                          reasoning_profile=os.environ.get("AGENT_RUNTIME_ACCEPTANCE_EFFORT", "low"),
                                          run_timeout_seconds=900)
    assert result["passed"], json.dumps([row for row in result["checks"] if not row["passed"]], indent=2)
