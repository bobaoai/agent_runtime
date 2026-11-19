"""Code-owned machine fields: source -> registration -> selection -> run -> inspection.

Each ordinary step takes only what an operator chooses (a source folder, a
version, a preset, a root, input and material files, a configured store or
connection name, output file locations). Code reads identities from the
source, computes hashes from actual bytes, writes the selection and record,
and checks every later use against the authoritative store. Provider calls go
through Runtime's real Test Run with a Claude process double (no model); the
synthetic CLI answers only its preflight, so an unintended real launch fails.
PostgreSQL stores are in-memory doubles of the release-store port except in
the cases gated on AGENT_RUNTIME_TEST_DATABASE_URL.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

import pytest

from agent_runtime import (
    Module, ModuleReviewer, RuntimeDefinitionSelection, inspect_test_run_record, module_requirements_preset,
    prepare_material_files, read_definition_selection, register_module, register_reviewer,
    run_local_workflow_test, select_registered_workflow, selection_from_registration, write_local_resources,
)
from agent_runtime.foundation import foundation_environment_setup as setup
from agent_runtime.foundation.foundation_environment_setup import load_runtime_config
from agent_runtime.inspection import inspection_snapshot_exporting as inspect_cli
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation.invocation_local_resource_preparation import LocalResourceError, capture_local_resources
from agent_runtime.registry import PostgresRuntimeReleaseStore, RuntimeReleaseRegistry
from agent_runtime.registry import registry_local_persistence as registry_cli
from agent_runtime.registry.registry_local_persistence import _closure, load_runtime_registration
from agent_runtime.testing import conformance_local_test_run as test_run_cli
from agent_runtime.testing.execution_local_test_run import _resource_arguments

from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result
from test_agent_runtime_module_authoring import _reviewer_output_schema


SKILL = "quantity-checks"
SUMMARY = "quantity_summary"
REVIEWER = "quantity_reviewer"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REVIEW = {"verdict": "passed", "safe_next_step": "Use the summary.", "findings": [],
           "check_results": [{"check_id": "total", "disposition": "passed", "assessment": "13 equals 13.",
                              "finding_ids": []}]}
_SUMMARY_OUTPUT = {"total_quantity": 13, "item_count": 3, "matches_declared": True}


@pytest.fixture
def root():
    """A short root with spaces (Windows' 260-character path limit applies below Attempt folders)."""
    folder = Path(tempfile.gettempdir()) / f"mf root {uuid.uuid4().hex[:6]}"
    folder.mkdir()
    yield folder
    shutil.rmtree(folder, ignore_errors=True)


def _schema(ref, properties, required):
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": ref, "type": "object",
                       "additionalProperties": False, "properties": properties, "required": required}) + "\n"


def _write_source(project: Path, module_id: str, *, prompt: str | None = None) -> Path:
    """An owner's ordinary v4 task source; returns the Module folder an operator would select."""
    skill = project / ".claude" / "skills" / SKILL
    module = skill / "runtime_modules" / module_id
    (module / "schemas").mkdir(parents=True, exist_ok=True)
    (project / "designDoc").mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(f"# Quantity checks\n\nModules: `{SUMMARY}`, `{REVIEWER}`.\n", encoding="utf-8")
    (project / "designDoc" / "quantity.md").write_text(
        f"# Quantity owner\n\nOwns `{SUMMARY}` and `{REVIEWER}`.\n", encoding="utf-8")
    (module / "module_registration.json").write_text(json.dumps({
        "schema_version": "runtime_module_registration_v4", "skill_id": SKILL, "module_id": module_id,
        "owner_contract_path": "designDoc/quantity.md",
        "input_schema_ref": f"schema:{module_id}_input@v1", "input_schema_path": "schemas/input.schema.json",
        "output_schema_ref": f"schema:{module_id}_output@v1", "output_schema_path": "schemas/output.schema.json",
    }), encoding="utf-8")
    item = {"type": "object", "properties": {"sku": {"type": "string"}, "quantity": {"type": "integer"}},
            "required": ["sku", "quantity"], "additionalProperties": False}
    if module_id == SUMMARY:
        (module / "prompt.md").write_text(prompt or "Sum the item quantities and compare with declared_total.\n",
                                          encoding="utf-8")
        (module / "schemas" / "input.schema.json").write_text(_schema(
            f"schema:{module_id}_input@v1", {"items": {"type": "array", "items": item},
                                             "declared_total": {"type": "integer"}}, ["items", "declared_total"]),
            encoding="utf-8")
        (module / "schemas" / "output.schema.json").write_text(_schema(
            f"schema:{module_id}_output@v1", {"total_quantity": {"type": "integer"},
                                              "item_count": {"type": "integer"},
                                              "matches_declared": {"type": "boolean"}},
            ["total_quantity", "item_count", "matches_declared"]), encoding="utf-8")
    else:
        (module / "prompt.md").write_text(prompt or "Review the quantity summary against the material file.\n",
                                          encoding="utf-8")
        (module / "schemas" / "input.schema.json").write_text(_schema(
            f"schema:{module_id}_input@v1", {"summary": {"type": "object"}, "material": {"type": "string"}},
            ["summary", "material"]), encoding="utf-8")
        (module / "schemas" / "output.schema.json").write_text(
            _reviewer_output_schema(f"schema:{module_id}_output@v1"), encoding="utf-8")
    return module


def _double(monkeypatch, output, *, inspect_cwd=None, fail=False):
    """Claude process double: records each launch and returns the given structured output."""
    launches = []

    class Adapter(claude.ClaudeAdapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, process_runner=self._process)

        def _process(self, **fields):
            launches.append(fields)
            if inspect_cwd is not None:
                inspect_cwd(Path(fields["cwd"]))
            argv = fields["argv"]
            tools = [name for name in argv[argv.index("--tools") + 1].split(",") if name]
            events = [_init(tuple(tools)), _result(structured_output=output, is_error=fail,
                                                   **({"subtype": "error_during_execution"} if fail else {}))]
            lines = [json.dumps(event) for event in events]
            for line in lines:
                assert fields["on_stdout_line"](line)
            raw = "\n".join(lines).encode()
            completed = subprocess.CompletedProcess(argv, 0, raw.decode(), "")
            completed.stdout_bytes, completed.stderr_bytes = raw, b""
            return completed
    monkeypatch.setattr(claude, "ClaudeAdapter", Adapter)
    return launches


def _cli_json(main, argv, capsys):
    code = main([str(value) for value in argv])
    captured = capsys.readouterr()
    return code, (json.loads(captured.out) if captured.out.strip() else None), captured.err


def _no_machine_values(argv):
    """Operator arguments are paths, names, versions and flags: no hash, release ref or Runtime ID."""
    for value in map(str, argv):
        assert not _SHA256.search(value) and not value.startswith(("runtime-", "schema:")), value


# --- Ordinary Module and Reviewer: source -> registration -> selection -> run -> inspection -------

@pytest.mark.fake_run
def test_ordinary_module_runs_from_its_source_folder_without_a_typed_machine_field(root, tmp_path, monkeypatch, capsys):
    project = root / "Source Project"
    source = _write_source(project, SUMMARY)
    host, selection_file, record = root / "host", root / "summary selection.json", root / "summary record.json"
    register = ["register-module", "--root", host, "--source", source, "--preset", "structured_output",
                "--version", "v1", "--selection-out", selection_file]
    _no_machine_values(register)
    code, registered, err = _cli_json(registry_cli.main, register, capsys)
    assert code == 0, err
    assert registered["readback"] == "verified"
    selection = read_definition_selection(selection_file)
    assert registered["selection"] == selection.as_dict() and selection.root == str(host.resolve())
    assert (selection.workflow_id, selection.workflow_version, selection.module_id) == (SUMMARY, "v1", SUMMARY)
    # Parity with the existing compiler path for an unchanged source: same Module and Workflow releases.
    exported = Module.from_registration(project, skill_id=SKILL, module_id=SUMMARY,
        execution_requirements=module_requirements_preset("structured_output")).export(module_version="v1")
    assert selection.module_release_sha256 == exported.module_release.release_sha256
    assert selection.workflow_release_sha256 == Module.to_workflow(exported).export().workflow_release.release_sha256

    payload = root / "quantity input.json"
    payload.write_text(json.dumps({"items": [{"sku": "A", "quantity": 4}, {"sku": "B", "quantity": 6},
                                             {"sku": "C", "quantity": 3}], "declared_total": 13}), encoding="utf-8")
    launches = _double(monkeypatch, _SUMMARY_OUTPUT)
    run = ["--root", host, "--selection", selection_file, "--input", payload, "--record-out", record,
           "--cli-path", _fake_cli(tmp_path, monkeypatch)]
    _no_machine_values(run)
    code, executed, err = _cli_json(test_run_cli.main, run, capsys)
    assert code == 0 and len(launches) == 1, err
    assert json.loads(record.read_text(encoding="utf-8")) == executed
    assert executed["definition_selection"]["executed_matches_selection"] is True
    assert {key: executed[key] for key in ("workflow_release_ref", "workflow_release_sha256", "module_release_ref",
                                           "module_release_sha256")} == {
        key: getattr(selection, key) for key in ("workflow_release_ref", "workflow_release_sha256",
                                                 "module_release_ref", "module_release_sha256")}

    # Inspection reads the saved record; it launches nothing.
    monkeypatch.setattr(claude, "ClaudeAdapter", None)
    code, inspected, err = _cli_json(inspect_cli.main, ["--test-run-record", record, "--view", "output"], capsys)
    assert code == 0, err
    assert inspected["outcome"] == "completed" and inspected["output"] == _SUMMARY_OUTPUT
    assert inspected["persistence"] == "not_requested" and inspected["durable_history"] == "not_available"
    assert inspected["identity"]["workflow_execution_id"] == executed["workflow_execution_id"]
    assert [row["archive_exists"] for row in inspected["attempts"]] == [True]
    assert len(launches) == 1


@pytest.mark.fake_run
def test_reviewer_runs_from_its_source_folder_with_selected_material_files(root, tmp_path, monkeypatch, capsys):
    project = root / "Source Project"
    source = _write_source(project, REVIEWER)
    host, selection_file, record = root / "host", root / "review selection.json", root / "review record.json"
    code, registered, err = _cli_json(registry_cli.main, [
        "register-reviewer", "--root", host, "--source", source / "module_registration.json", "--version", "v1",
        "--selection-out", selection_file], capsys)
    assert code == 0, err
    selection = read_definition_selection(selection_file)
    assert selection.module_id == REVIEWER
    # The same source registered through the lower-level exact form gives the same releases.
    other = register_reviewer(root / "other host", source_root=project, skill_id=SKILL, module_id=REVIEWER,
                              module_version="v1")
    assert (other.submitted_bundle.workflows[0].release_sha256, other.submitted_bundle.modules[0].release_sha256) == (
        selection.workflow_release_sha256, selection.module_release_sha256)
    exported = ModuleReviewer.from_registration(project, skill_id=SKILL, module_id=REVIEWER).export(module_version="v1")
    assert exported.module_release.release_sha256 == selection.module_release_sha256

    materials = root / "Review materials"
    (materials / "notes").mkdir(parents=True)
    (materials / "notes" / "stock count ü.md").write_text("Counted 13 units.\n", encoding="utf-8")
    (materials / "unselected.md").write_text("not for this review\n", encoding="utf-8")
    payload = root / "review input.json"
    payload.write_text(json.dumps({"summary": _SUMMARY_OUTPUT, "material": "notes/stock count ü.md"}),
                       encoding="utf-8")
    seen = {}

    def materials_in_attempt(cwd):
        source_copy = cwd / "materials" / "source"
        seen["files"] = sorted(path.relative_to(source_copy).as_posix() for path in source_copy.rglob("*")
                               if path.is_file())
    launches = _double(monkeypatch, _REVIEW, inspect_cwd=materials_in_attempt)
    code, executed, err = _cli_json(test_run_cli.main, [
        "--root", host, "--selection", selection_file, "--input", payload, "--record-out", record,
        "--material-root", materials, "--material", Path("notes") / "stock count ü.md",
        "--cli-path", _fake_cli(tmp_path, monkeypatch)], capsys)
    assert code == 0 and len(launches) == 1, err
    assert seen["files"] == ["notes/stock count ü.md"]
    assert executed["output"] == _REVIEW and executed["definition_selection"]["executed_matches_selection"]
    code, inspected, err = _cli_json(inspect_cli.main, ["--test-run-record", record, "--view", "files"], capsys)
    assert code == 0, err
    (attempt,) = inspected["attempts"]
    copied = {row["path"]: row for row in attempt["files"]}
    material_row = next(row for path, row in copied.items() if path.endswith("source/notes/stock count ü.md"))
    # The frozen copy holds the selected file's actual bytes (CRLF where the host wrote CRLF).
    assert material_row["sha256"] == hashlib.sha256((materials / "notes" / "stock count ü.md").read_bytes()).hexdigest()
    assert any(path.endswith("archive/execution_record.json") for path in copied)


# --- Exact prior binding, edited/foreign selections, ambiguous registrations ------------------------

@pytest.mark.fake_run
def test_a_selection_keeps_its_version_after_a_newer_registration(root, tmp_path, monkeypatch):
    project = root / "Source Project"
    source = _write_source(project, SUMMARY)
    host = root / "host"
    requirements = module_requirements_preset("structured_output")
    first = selection_from_registration(register_module(host, source=source, module_version="v1",
                                                        execution_requirements=requirements), root=host)
    _write_source(project, SUMMARY, prompt="Sum the quantities; report item_count as well.\n")
    second = selection_from_registration(register_module(host, source=source, module_version="v2",
                                                         execution_requirements=requirements), root=host)
    assert load_runtime_registration(host, "workflow", SUMMARY).release.release_ref == second.workflow_release_ref
    assert first.workflow_release_sha256 != second.workflow_release_sha256
    launches = _double(monkeypatch, _SUMMARY_OUTPUT)
    record = run_local_workflow_test(host, selection=first, cli_path=_fake_cli(tmp_path, monkeypatch),
        input_payload={"items": [{"sku": "A", "quantity": 13}], "declared_total": 13})
    assert len(launches) == 1 and record["status"] == "completed"
    assert (record["workflow_release_sha256"], record["module_release_sha256"]) == (
        first.workflow_release_sha256, first.module_release_sha256)
    # Repeating the unchanged v1 registration reproduces the same selection; changed content needs a new version.
    _write_source(project, SUMMARY)
    again = selection_from_registration(register_module(host, source=source, module_version="v1",
                                                        execution_requirements=requirements), root=host)
    assert again == first
    with pytest.raises(ValueError, match="different execution requirements"):
        register_module(host, source=source, module_version="v1",
                        execution_requirements=module_requirements_preset("file_review"))
    _write_source(project, SUMMARY, prompt="A changed task.\n")
    with pytest.raises(ValueError, match="collision"):
        register_module(host, source=source, module_version="v1", execution_requirements=requirements)


@pytest.mark.deterministic
def test_load_with_a_selection_returns_that_versions_schemas_for_input_preparation(root, capsys):
    project = root / "Source Project"
    source = _write_source(project, SUMMARY)
    host = root / "host"
    first = root / "v1 selection.json"
    code, _, err = _cli_json(registry_cli.main, ["register-module", "--root", host, "--source", source, "--preset",
                                                 "structured_output", "--version", "v1", "--selection-out", first], capsys)
    assert code == 0, err
    # The owner adds an output field in v2 (with a new schema version); the v1 selection still describes v1.
    schema_path = source / "schemas" / "output.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    schema["properties"]["note"] = {"type": "string"}
    schema["$id"] = f"schema:{SUMMARY}_output@v2"
    schema_path.write_text(json.dumps(schema) + "\n", encoding="utf-8")
    declaration = json.loads((source / "module_registration.json").read_text(encoding="utf-8"))
    declaration["output_schema_ref"] = schema["$id"]
    (source / "module_registration.json").write_text(json.dumps(declaration), encoding="utf-8")
    code, _, err = _cli_json(registry_cli.main, ["register-module", "--root", host, "--source", source, "--preset",
                                                 "structured_output", "--version", "v2"], capsys)
    assert code == 0, err
    code, loaded, err = _cli_json(registry_cli.main, ["load", "--root", host, "--selection", first], capsys)
    assert code == 0, err
    selection = read_definition_selection(first)
    assert (loaded["release"]["release_ref"], loaded["release"]["release_sha256"]) == (
        selection.workflow_release_ref, selection.workflow_release_sha256)
    assert loaded["module"]["release_sha256"] == selection.module_release_sha256
    assert "note" not in loaded["output_schema"]["properties"]
    assert set(loaded["input_schema"]["properties"]) == {"items", "declared_total"}
    # The lower-level form keeps its meaning: without a version it loads the latest registration.
    code, latest, err = _cli_json(registry_cli.main, ["load", "--root", host, "--kind", "workflow", "--id", SUMMARY],
                                  capsys)
    assert code == 0 and latest["release"]["workflow_version"] == "v2", err
    for arguments in (["load", "--root", str(host)],
                      ["load", "--root", str(host), "--selection", str(first), "--kind", "workflow", "--id", SUMMARY]):
        with pytest.raises(SystemExit) as stop:
            registry_cli.main(arguments)
        assert stop.value.code == 2
    capsys.readouterr()


@pytest.mark.deterministic
def test_a_selection_runs_only_under_the_root_it_was_made_for(root, monkeypatch):
    project = root / "Source Project"
    host = root / "host"
    selection = selection_from_registration(register_module(host, source=_write_source(project, SUMMARY),
        module_version="v1", execution_requirements=module_requirements_preset("structured_output")), root=host)
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("refused before setup"))
    payload = {"items": [], "declared_total": 0}
    with pytest.raises(ValueError, match="made for root"):
        run_local_workflow_test(root / "another host", selection=selection, input_payload=payload)
    with pytest.raises(ValueError, match="no workflow_id, version"):
        run_local_workflow_test(host, SUMMARY, selection=selection, input_payload=payload)
    with pytest.raises(ValueError, match="expected_module_id"):
        run_local_workflow_test(host, selection=selection, expected_module_id=SUMMARY, input_payload=payload)


@pytest.mark.deterministic
def test_selection_needs_one_unambiguous_single_node_workflow(root, capsys):
    project = root / "Source Project"
    host = root / "host"
    requirements = module_requirements_preset("structured_output")
    summary = Module.from_source(_write_source(project, SUMMARY), execution_requirements=requirements)
    reviewer = Module.from_source(_write_source(project, REVIEWER), execution_requirements=requirements)
    bundles = [Module.to_workflow(item.export(module_version="v1")).export().origin_bundle
               for item in (summary, reviewer)]
    from dataclasses import fields as dataclass_fields
    from agent_runtime.registry import RuntimeReleaseBundle
    both = RuntimeReleaseBundle(**{field.name: tuple({record.release_ref: record for bundle in bundles
                                                      for record in getattr(bundle, field.name)}.values())
                                   for field in dataclass_fields(RuntimeReleaseBundle)})
    path = root / "two workflows.json"
    path.write_text(json.dumps(both.as_dict()), encoding="utf-8")
    # Neither a package label nor a selection is guessed for a bundle with two Workflows.
    code, _, err = _cli_json(registry_cli.main, ["register", "--root", host, "--bundle", path], capsys)
    assert code == 1 and "cannot be read unambiguously" in err
    code, _, err = _cli_json(registry_cli.main, ["register", "--root", host, "--bundle", path,
                                                 "--plugin-id", "quantity_package", "--plugin-version", "v1",
                                                 "--selection-out", root / "selection.json"], capsys)
    assert code == 1 and "exactly one Workflow" in err
    assert not (host / ".runtime" / "workflow").exists() and not (root / "selection.json").exists()
    code, output, err = _cli_json(registry_cli.main, ["register", "--root", host, "--bundle", path,
                                                      "--plugin-id", "quantity_package", "--plugin-version", "v1"], capsys)
    assert code == 0, err
    assert output["selection"] is None and "2 Workflows" in output["selection_unavailable"]
    assert output["plugin"] == {"plugin_id": "quantity_package", "plugin_version": "v1"}
    # One Workflow: the package label is read from the bundle and the selection is written.
    single = root / "one workflow.json"
    single.write_text(json.dumps(bundles[0].as_dict()), encoding="utf-8")
    code, output, err = _cli_json(registry_cli.main, ["register", "--root", root / "host two", "--bundle", single,
                                                      "--selection-out", root / "one selection.json"], capsys)
    assert code == 0, err
    assert output["plugin"] == {"plugin_id": SUMMARY, "plugin_version": "v1"}
    assert read_definition_selection(root / "one selection.json").as_dict() == output["selection"]
    # Explicit existing-object selection requires the exact version and reports a missing one.
    code, output, err = _cli_json(registry_cli.main, ["select", "--root", host, "--workflow", REVIEWER,
                                                      "--version", "v1"], capsys)
    assert code == 0 and output["selection"]["module_id"] == REVIEWER, err
    assert _cli_json(registry_cli.main, ["select", "--root", host, "--workflow", REVIEWER, "--version", "v9"],
                     capsys)[0] == 1
    with pytest.raises(SystemExit) as stop:
        registry_cli.main(["select", "--root", str(host), "--workflow", REVIEWER])
    assert stop.value.code == 2


@pytest.mark.deterministic
def test_an_existing_selection_or_record_file_is_refused_before_any_work(root, monkeypatch, capsys):
    existing = root / "existing.json"
    existing.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("refused before setup"))
    monkeypatch.setattr(claude, "ClaudeAdapter", None)
    for main, argv in (
            (registry_cli.main, ["register-module", "--root", root, "--source", root, "--preset", "structured_output",
                                 "--version", "v1", "--selection-out", existing]),
            (test_run_cli.main, ["--root", root, "--selection", root / "s.json", "--input", root / "i.json",
                                 "--record-out", existing]),
            (test_run_cli.main, ["--root", root, "--selection", root / "s.json", "--input", root / "i.json",
                                 "--record-out", root / "missing folder" / "record.json"])):
        with pytest.raises(SystemExit) as stop:
            main([str(value) for value in argv])
        assert stop.value.code == 2
    assert existing.read_text(encoding="utf-8") == "keep"
    capsys.readouterr()


# --- Material files selected by the operator, hashed by code -------------------------------------

def _materials(folder: Path) -> Path:
    (folder / "数据").mkdir(parents=True)
    (folder / "notes with spaces.md").write_text("Release code: RC-1\n", encoding="utf-8")
    (folder / "数据" / "客户 清单.csv").write_bytes("名称,数量\n甲,1\n".encode("utf-8"))
    (folder / "empty.txt").write_bytes(b"")
    (folder / "extra.bin").write_bytes(b"\x00\x01")
    return folder


def _agent_profile():
    from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
    return _execution_profile_for_requirements(module_requirements_preset("file_review"))


@pytest.mark.deterministic
def test_selected_material_files_are_hashed_from_actual_bytes(tmp_path):
    folder = _materials(tmp_path / "Task materials ü")
    native = str(Path("数据") / "客户 清单.csv")
    rows = prepare_material_files(folder, ("notes with spaces.md", native, str(folder / "empty.txt")))
    assert [row["relative_path"] for row in rows] == ["empty.txt", "notes with spaces.md", "数据/客户 清单.csv"]
    for row in rows:
        assert row["sha256"] == hashlib.sha256((folder / row["relative_path"]).read_bytes()).hexdigest()
        assert row["executable"] is (False if os.name == "nt" else os.access(folder / row["relative_path"], os.X_OK))
    assert prepare_material_files(folder, ()) == ()  # An empty selection stays empty.
    assert [row["relative_path"] for row in prepare_material_files(folder)] == [
        "empty.txt", "extra.bin", "notes with spaces.md", "数据/客户 清单.csv"]  # None: the whole folder.
    for bad, error in ((("notes with spaces.md", "notes with spaces.md"), LocalResourceError),
                       ((str(Path("..") / "outside.md"),), LocalResourceError),
                       ((str(tmp_path / "elsewhere.md"),), LocalResourceError),
                       (("数据",), (LocalResourceError, OSError)),
                       (("missing.md",), FileNotFoundError),
                       ("notes with spaces.md", LocalResourceError)):
        with pytest.raises(error):
            prepare_material_files(folder, bad)
    captured = capture_local_resources(profile=_agent_profile(), material_root=folder, material_files=rows)
    assert {row["relative_path"] for row in json.loads(captured)["material_files"]} == {
        "empty.txt", "notes with spaces.md", "数据/客户 清单.csv"}
    # A later change or removal is refused by the original capture check; the hash is never refreshed.
    (folder / "notes with spaces.md").write_text("Release code: RC-2\n", encoding="utf-8")
    with pytest.raises(LocalResourceError, match="differ"):
        capture_local_resources(profile=_agent_profile(), material_root=folder, material_files=rows)
    (folder / "empty.txt").unlink()
    with pytest.raises((LocalResourceError, OSError)):
        capture_local_resources(profile=_agent_profile(), material_root=folder, material_files=rows[:1])


@pytest.mark.deterministic
def test_written_resources_file_freezes_the_selection_for_a_later_run(tmp_path):
    folder = _materials(tmp_path / "materials")
    rows = prepare_material_files(folder, ("notes with spaces.md",))
    path = write_local_resources(tmp_path / "resources.json", material_root=folder, material_files=rows)
    parsed = _resource_arguments(path)
    assert parsed["material_files"] == rows and Path(parsed["material_root"]) == folder.resolve()
    with pytest.raises(FileExistsError):
        write_local_resources(path, material_root=folder, material_files=rows)
    (folder / "notes with spaces.md").write_text("changed after the resources file was written\n", encoding="utf-8")
    with pytest.raises(LocalResourceError, match="differ"):
        capture_local_resources(profile=_agent_profile(), material_root=parsed["material_root"],
                                material_files=parsed["material_files"])


def _link(link: Path, target: Path) -> None:
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link, target_is_directory=True)


@pytest.mark.deterministic
def test_whole_folder_selection_refuses_links_instead_of_following_or_skipping_them(tmp_path):
    folder = _materials(tmp_path / "materials")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("not a material\n", encoding="utf-8")
    _link(folder / "linked", outside)
    with pytest.raises(LocalResourceError, match="link or reparse point"):
        prepare_material_files(folder)
    with pytest.raises(LocalResourceError):
        prepare_material_files(folder, (str(Path("linked") / "secret.txt"),))
    assert [row["relative_path"] for row in prepare_material_files(folder, ("extra.bin",))] == ["extra.bin"]


@pytest.mark.deterministic
@pytest.mark.parametrize("arguments", [
    ["--material-root", "m"], ["--material", "a.md"], ["--all-materials"],
    ["--material-root", "m", "--material", "a.md", "--all-materials"],
    ["--material-root", "m", "--all-materials", "--resources", "r.json"],
])
def test_material_arguments_need_one_explicit_selection(root, monkeypatch, arguments):
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("usage error before setup"))
    with pytest.raises(SystemExit) as stop:
        test_run_cli.main(["--root", str(root), "--workflow", "w", "--input", "i.json", *arguments])
    assert stop.value.code == 2


@pytest.mark.fake_run
def test_a_material_changed_after_preparation_stops_the_run_before_the_provider(root, tmp_path, monkeypatch, capsys):
    project = root / "Source Project"
    host = root / "host"
    selection = selection_from_registration(register_reviewer(host, source=_write_source(project, REVIEWER),
                                                              module_version="v1"), root=host)
    materials = _materials(root / "materials")
    resources = write_local_resources(root / "resources.json", material_root=materials,
                                      material_files=prepare_material_files(materials, ("notes with spaces.md",)))
    (materials / "notes with spaces.md").write_text("drifted\n", encoding="utf-8")
    launches = _double(monkeypatch, _REVIEW)
    selection_file = root / "selection.json"
    selection_file.write_text(json.dumps(selection.as_dict()), encoding="utf-8")
    payload = root / "input.json"
    payload.write_text(json.dumps({"summary": {}, "material": "notes with spaces.md"}), encoding="utf-8")
    record = root / "record.json"
    code, _, err = _cli_json(test_run_cli.main, ["--root", host, "--selection", selection_file, "--input", payload,
        "--resources", resources, "--record-out", record, "--cli-path", _fake_cli(tmp_path, monkeypatch)], capsys)
    assert code == 1 and launches == [] and "differ" in err
    # The saved error document is inspected as such: no record, no invented Attempt or archive.
    inspected = inspect_test_run_record(record)
    assert inspected["outcome"] == "no_record" and inspected["attempts"] == []
    assert inspected["error"]["error_type"] == "LocalResourceError"


# --- Inspection of saved records: read-only, honest about what is missing -----------------------

def _summary_run(root, tmp_path, monkeypatch, *, fail=False):
    project = root / "Source Project"
    host = root / "host"
    selection = selection_from_registration(register_module(host, source=_write_source(project, SUMMARY),
        module_version="v1", execution_requirements=module_requirements_preset("structured_output")), root=host)
    _double(monkeypatch, _SUMMARY_OUTPUT, fail=fail)
    return run_local_workflow_test(host, selection=selection, cli_path=_fake_cli(tmp_path, monkeypatch),
        input_payload={"items": [{"sku": "A", "quantity": 13}], "declared_total": 13})


@pytest.mark.fake_run
def test_a_failed_run_record_is_shown_without_any_execution(root, tmp_path, monkeypatch, capsys):
    from agent_runtime import write_test_run_record
    from agent_runtime.registry import registry_plugin_registration
    failed = _summary_run(root, tmp_path, monkeypatch, fail=True)
    assert failed["status"] == "failed"
    path = write_test_run_record(root / "failed.json", failed)
    for name in ("register_runtime_module_plugin", "register_module", "register_reviewer"):
        monkeypatch.setattr(registry_plugin_registration, name, lambda *a, **k: pytest.fail("inspection registered"))
    monkeypatch.setattr(claude, "ClaudeAdapter", None)
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, *a, **k: pytest.fail("inspection opened PostgreSQL")))
    code, inspected, err = _cli_json(inspect_cli.main, ["--test-run-record", path, "--view", "output"], capsys)
    assert code == 0, err
    assert inspected["outcome"] == "failed" and inspected["output"] is None and inspected["failure_detail"]
    assert inspected["identity"]["workflow_execution_id"] == failed["workflow_execution_id"]
    (attempt,) = inspected["attempts"]
    assert attempt["archive_exists"] is True and attempt["workspace_exists"] is True
    # What is no longer on disk is shown as absent, not invented.
    shutil.rmtree(failed["attempt_workspaces"][0]["workspace_path"])
    gone = inspect_test_run_record(path, view="files")["attempts"][0]
    assert (gone["workspace_exists"], gone["archive_exists"], gone["files"]) == (False, False, None)


@pytest.mark.deterministic
def test_missing_corrupt_or_persistent_records_are_not_read_as_local_results(tmp_path, capsys):
    assert inspect_cli.main(["--test-run-record", str(tmp_path / "absent.json")]) == 1
    assert "FileNotFoundError" in capsys.readouterr().err
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    assert inspect_cli.main(["--test-run-record", str(corrupt)]) == 1
    assert "ValueError" in capsys.readouterr().err
    with pytest.raises(ValueError, match="persistence"):
        inspect_test_run_record({"status": "completed", "persistence": "postgres"})
    with pytest.raises(ValueError, match="neither status nor error_type"):
        inspect_test_run_record({"output": {}})
    with pytest.raises(ValueError, match="view"):
        inspect_test_run_record({"status": "completed", "persistence": "not_requested"}, view="rerun")
    with pytest.raises(SystemExit):
        inspect_cli.main(["bundle.json", "--output", "x.html", "--test-run-record", str(corrupt)])


@pytest.mark.deterministic
def test_returned_module_run_result_without_a_stored_execution_is_not_found():
    from agent_runtime.inspection import PostgresWorkflowInspectionRepository
    from agent_runtime.ledger import PostgresRuntimeExecutionQueryStore

    class Queries(PostgresRuntimeExecutionQueryStore):
        def __init__(self):
            self.asked = []

        def get_execution_descriptor(self, execution_id):
            self.asked.append(execution_id)
            return None

    class Run:
        workflow_execution_id, module_run_id = "workflow_execution_local", "module_run_local"

    class Result:
        module_run, outputs = Run(), ()
    queries = Queries()
    repository = PostgresWorkflowInspectionRepository(queries)
    with pytest.raises(FileNotFoundError, match="non-persistent"):
        repository.read_module_run_result(Result())
    assert queries.asked == ["workflow_execution_local"]
    with pytest.raises(TypeError):
        repository.read_module_run_result({"workflow_execution_id": "typed by hand"})


@pytest.mark.deterministic
def test_returned_module_run_result_reads_its_committed_outputs_through_the_query_port(tmp_path):
    """A real persistent-entry result read back through the query port (an in-memory double of its reads)."""
    from agent_runtime import read_execution_log
    from agent_runtime.inspection import PostgresWorkflowInspectionRepository
    from agent_runtime.ledger import PostgresRuntimeExecutionQueryStore
    from test_agent_runtime_registered_module_execution import _environment, _run
    env = _environment(tmp_path)
    result = _run(env)
    assert len(result.outputs) == 1

    class Queries(PostgresRuntimeExecutionQueryStore):
        def __init__(self):
            self.content_reads = []

        def get_execution_descriptor(self, execution_id):
            return {"workflow_execution_id": execution_id} if env.store.load_trace(execution_id).records else None

        def load_trace(self, execution_id):
            return env.store.load_trace(execution_id)

        def load_content(self, execution_id, content_ref):
            self.content_reads.append(content_ref)
            return env.contents.values.get((execution_id, content_ref))

    queries = Queries()
    read = PostgresWorkflowInspectionRepository(queries).read_module_run_result(result, include_private_content=True)
    (output,) = result.outputs
    assert read["outputs"] == [{"logical_name": output.logical_name, "output_ref": output.output_ref,
                                "output_sha256": output.output_sha256, "media_type": output.media_type,
                                "body": json.loads(env.cell.read_bytes(output.output_ref, output.output_sha256))}]
    assert read["outputs"][0]["body"] == {"value": "done"} and output.output_ref in queries.content_reads
    assert read["workflow_execution_id"] == result.module_run.workflow_execution_id
    assert read["execution_log"] == read_execution_log(result.module_run, attempts=result.attempts,
                                                      read_content=env.cell.read_bytes, include_private_content=True)
    assert len(env.calls) == 1  # Reading started no further Provider call.


def test_returned_persistent_result_reads_its_stored_trace_log_and_outputs(tmp_path, pg_stores):
    from agent_runtime.inspection import PostgresWorkflowInspectionRepository
    from test_agent_runtime_registered_module_execution import _environment, _run
    env = _environment(tmp_path, pg=pg_stores)
    result = _run(env)
    repository = PostgresWorkflowInspectionRepository(env.query())
    read = repository.read_module_run_result(result, include_private_content=True)
    execution_id = result.module_run.workflow_execution_id
    assert read["workflow_execution_id"] == execution_id
    assert read["trace"] == repository.load_trace(execution_id)
    assert read["execution_log"] == repository.read_execution_log(execution_id, include_private_content=True)
    assert [item["body"] for item in read["outputs"]] == [
        json.loads(env.cell.read_bytes(item.output_ref, item.output_sha256)) for item in result.outputs]


from test_agent_runtime_registered_module_execution import pg_stores  # noqa: E402,F401  (gated fixture)


# --- Configured bindings: release stores and MCP connections ------------------------------------

class _MemoryReleaseStore:
    """In-memory double of the release-store read port."""

    def __init__(self, registry):
        self.registry, self.loads = registry, 0

    def load_release_registry(self):
        self.loads += 1
        return self.registry


@pytest.mark.deterministic
@pytest.mark.parametrize("stores", [
    {"Team": {"database_url_env": "DSN", "schema": "s"}},
    {"team": {"database_url_env": "DSN"}},
    {"team": {"database_url_env": "DSN", "schema": "s", "dsn": "postgresql://x"}},
    {"team": {"database_url_env": "not a variable", "schema": "s"}},
    {"team": {"database_url_env": "DSN", "schema": "Bad-Schema"}},
])
def test_release_store_configuration_holds_only_variable_names_and_schemas(tmp_path, stores):
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "config.json").write_text(json.dumps({"release_stores": stores}), encoding="utf-8")
    with pytest.raises(ValueError, match="release store"):
        load_runtime_config(tmp_path)


@pytest.mark.fake_run
def test_postgres_selection_uses_the_configured_store_and_rechecks_its_locator(root, tmp_path, monkeypatch, capsys):
    project = root / "Source Project"
    published = root / "published"
    register_module(published, source=_write_source(project, SUMMARY), module_version="v1",
                    execution_requirements=module_requirements_preset("structured_output"))
    loaded = load_runtime_registration(published, "workflow", SUMMARY, "v1")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(_closure(loaded.registry, loaded.release))
    store, opened = _MemoryReleaseStore(registry), []
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, dsn, schema: opened.append((dsn, schema)) or store))
    host = root / "host"
    (host / ".runtime").mkdir(parents=True)
    config = host / ".runtime" / "config.json"
    config.write_text(json.dumps({"release_stores": {"team_releases": {
        "database_url_env": "MF_RELEASE_DSN", "schema": "agent_runtime_releases"}}}), encoding="utf-8")
    secret = "postgresql://reader:not-a-real-secret@db.invalid/runtime"
    monkeypatch.setenv("MF_RELEASE_DSN", secret)
    selection_file = root / "pg selection.json"
    code, output, err = _cli_json(registry_cli.main, ["select", "--root", host, "--workflow", SUMMARY, "--version", "v1",
                                                      "--release-store", "team_releases", "--selection-out",
                                                      selection_file], capsys)
    assert code == 0, err
    assert output["selection"]["release_store"] == "team_releases"
    assert secret not in selection_file.read_text(encoding="utf-8")
    assert opened == [(secret, "agent_runtime_releases")]
    launches = _double(monkeypatch, _SUMMARY_OUTPUT)
    record = run_local_workflow_test(host, selection=selection_file, cli_path=_fake_cli(tmp_path, monkeypatch),
        input_payload={"items": [{"sku": "A", "quantity": 13}], "declared_total": 13})
    assert len(launches) == 1 and record["definition_selection"]["executed_matches_selection"]
    assert record["workflow_release_sha256"] == loaded.release.release_sha256 and secret not in json.dumps(record)
    # The store name is resolved through the current configuration on each run; nothing is guessed.
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("refused before setup"))
    config.write_text(json.dumps({"release_stores": {"team_releases": {
        "database_url_env": "HTTPS_PROXY", "schema": "agent_runtime_releases"}}}), encoding="utf-8")
    with pytest.raises(ValueError, match="forwarded to Provider processes"):
        run_local_workflow_test(host, selection=selection_file, input_payload={})
    config.write_text(json.dumps({"release_stores": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="not configured"):
        run_local_workflow_test(host, selection=selection_file, input_payload={})


@pytest.mark.deterministic
def test_unconfigured_store_or_missing_dsn_variable_is_a_concrete_gap(root, monkeypatch):
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, *a, **k: pytest.fail("no connection is guessed")))
    with pytest.raises(ValueError, match="not configured"):
        select_registered_workflow(root, SUMMARY, version="v1", release_store="team_releases")
    (root / ".runtime").mkdir()
    (root / ".runtime" / "config.json").write_text(json.dumps({"release_stores": {"team_releases": {
        "database_url_env": "MF_ABSENT_DSN", "schema": "agent_runtime_releases"}}}), encoding="utf-8")
    monkeypatch.delenv("MF_ABSENT_DSN", raising=False)
    with pytest.raises(ValueError, match="MF_ABSENT_DSN"):
        select_registered_workflow(root, SUMMARY, version="v1", release_store="team_releases")


@pytest.mark.fake_run
def test_declared_mcp_connections_are_passed_exactly_or_reported_missing(root, tmp_path, monkeypatch):
    project = root / "Source Project"
    host = root / "host"
    selection = selection_from_registration(register_module(host, source=_write_source(project, SUMMARY),
        module_version="v1", execution_requirements=module_requirements_preset(
            "mcp_data", mcp_connections=("warehouse_data",))), root=host)
    (host / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {"claude_cli": {
        "other_service": {"config": {"command": sys.executable, "args": ["-c", "pass"]}, "environment_keys": []}}}}),
        encoding="utf-8")
    project_dir = root / "output"
    project_dir.mkdir()
    payload = {"items": [{"sku": "A", "quantity": 13}], "declared_total": 13}
    launches = _double(monkeypatch, _SUMMARY_OUTPUT)
    missing = run_local_workflow_test(host, selection=selection, cli_path=_fake_cli(tmp_path, monkeypatch),
                                      project_directory=project_dir, input_payload=payload)
    assert missing["status"] == "failed" and launches == []
    assert "warehouse_data" in json.dumps(missing["failure_detail"]) and "other_service" not in json.dumps(
        missing["provider_trace"] or {})
    (host / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {"claude_cli": {
        name: {"config": {"command": sys.executable, "args": ["-c", "pass"]}, "environment_keys": []}
        for name in ("other_service", "warehouse_data")}}}), encoding="utf-8")
    passed = run_local_workflow_test(host, selection=selection, cli_path=_fake_cli(tmp_path, monkeypatch),
                                     project_directory=project_dir, input_payload=payload)
    assert passed["status"] == "completed" and len(launches) == 1
    assert [entry["name"] for entry in passed["provider_trace"]["effective_configuration"]["mcp_connections"]] == [
        "warehouse_data"]
