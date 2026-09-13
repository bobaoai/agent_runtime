"""Ordinary Module resource capture and prompt tests without Provider or PG."""
import base64
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import stat

from jsonschema import Draft202012Validator
import pytest

from agent_runtime.contracts.execution_module_definition import ModuleInputBinding
from agent_runtime.invocation import invocation_local_resource_preparation as resources
from agent_runtime.invocation.invocation_prompt_assembly import (
    build_inline_provider_prompt, NATIVE_STRUCTURED_OUTPUT, PROMPT_ONLY_JSON, OUTPUT_SCHEMA_MARKER,
)
import test_agent_runtime_native_structured_output as native


def profile(tmp_path, **overrides):
    fields = dict(executor_adapter_id="claude_cli_adapter", executor_adapter_revision="v2",
        transport_kind="claude_cli", provider_id="anthropic", execution_mode="agent",
        attempt_workspace_policy="own_draft_read_write", tool_policy=("read", "search", "shell"))
    fields.update(overrides)
    # This is an ordinary Module fixture with its own schema, not ModuleReviewer.
    return native._compile_native_module(tmp_path, **fields).execution_profile


def file_row(root, name, content=b"fixture", *, executable=False):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755 if executable else 0o644)
    return {"relative_path": name, "sha256": hashlib.sha256(content).hexdigest(), "executable": executable}


def command(**changes):
    return {"command_id": "unit", "argv": ["/bin/sh", "-c", "true"],
            "cwd": "source", "timeout_seconds": 10, **changes}


def package(tmp_path, **changes):
    root = tmp_path / "origin"
    root.mkdir()
    rows = (file_row(root, "pkg/__init__.py", b"from .value import VALUE\n"),
            file_row(root, "pkg/value.py", b"VALUE=7\n"),
            file_row(root, "run.sh", b"#!/bin/sh\nexit 0\n", executable=True),
            file_row(root, "data.bin", b"\x00\xff\x80\n"))
    file_row(root, "not_selected.txt", b"NOT_AUTHORIZED")
    fields = dict(profile=profile(tmp_path), material_root=root, material_files=rows)
    fields.update(changes)
    body = resources.capture_local_resources(**fields)
    destination = tmp_path / "materials"
    destination.mkdir()
    return body, root, destination


def test_capture_freezes_exact_binary_tree_modes_and_only_selected_files(tmp_path):
    body, origin, materials = package(tmp_path)
    data = resources.parse_local_resources(body)
    Draft202012Validator(resources.LOCAL_RESOURCES_SCHEMA).validate(data)
    assert hashlib.sha256(resources._canonical(resources.LOCAL_RESOURCES_SCHEMA)).hexdigest() == resources.LOCAL_RESOURCES_SCHEMA_SHA256
    assert body == resources._canonical(data)
    (origin / "data.bin").write_bytes(b"changed after capture")
    resources.materialize_local_resources(body, materials_root=materials)
    resources.assert_local_materials_unchanged(body, materials_root=materials)
    assert (materials / "source/data.bin").read_bytes() == b"\x00\xff\x80\n"
    assert (materials / "source/pkg/__init__.py").read_text() == "from .value import VALUE\n"
    assert not (materials / "source/not_selected.txt").exists()
    assert stat.S_IMODE((materials / "source/run.sh").stat().st_mode) == 0o555
    assert stat.S_IMODE((materials / "source/data.bin").stat().st_mode) == 0o444
    assert stat.S_IMODE((materials / "source/pkg").stat().st_mode) == 0o555
    resources.materialize_local_resources(body, materials_root=materials)


@pytest.mark.parametrize("binding", ["claude_old", "codex", "new_no_tools"])
def test_absent_resources_do_not_open_roots_or_change_old_envelopes(tmp_path, monkeypatch, binding):
    options = {"executor_adapter_revision": "v1"} if binding == "claude_old" else (
        {"executor_adapter_id": "codex_cli_agent_executor", "executor_adapter_revision": "v4",
         "transport_kind": "codex_cli", "provider_id": "openai", "execution_mode": "tool_free",
         "attempt_workspace_policy": "none", "tool_policy": ()} if binding == "codex" else
        {"execution_mode": "tool_free", "attempt_workspace_policy": "none", "tool_policy": ()})
    selected = profile(tmp_path, **options)
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("unused origin opened"))
    assert resources.capture_local_resources(profile=selected, material_root=tmp_path / "missing") is None
    description = resources.describe_local_resources(profile=selected, body=None)
    assert description == "" if binding != "new_no_tools" else "materials/task_input" in description


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/../b", "./file", "a//b", "a/", "a\\b", "a\x00b"])
def test_invalid_material_paths_rejected_before_file_io(tmp_path, monkeypatch, path):
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("unsafe path reached IO"))
    with pytest.raises(resources.LocalResourceError):
        resources.capture_local_resources(profile=profile(tmp_path), material_root=tmp_path,
            material_files=({"relative_path": path, "sha256": "1"*64, "executable": False},))


@pytest.mark.parametrize("kind", ["duplicate", "prefix_conflict", "hash", "mode", "extra_field"])
def test_bad_file_manifests_are_rejected(tmp_path, kind):
    root = tmp_path / "origin"
    root.mkdir()
    row = file_row(root, "file")
    rows = [row]
    if kind == "duplicate":
        rows.append(dict(row))
    elif kind == "prefix_conflict":
        rows.append({**row, "relative_path": "file/child"})
    elif kind == "hash":
        row["sha256"] = "1"*64
    elif kind == "mode":
        row["executable"] = True
    else:
        row["ignored"] = 1
    with pytest.raises(resources.LocalResourceError):
        resources.capture_local_resources(profile=profile(tmp_path), material_root=root, material_files=tuple(rows))


@pytest.mark.parametrize("location", ["parent", "file"])
def test_capture_never_follows_source_symlinks(tmp_path, location):
    real = tmp_path / "real"
    real.mkdir()
    row = file_row(real, "folder/file", b"PRIVATE_EXTERNAL")
    root = tmp_path / "root"
    root.mkdir()
    if location == "parent":
        (root / "folder").symlink_to(real / "folder", target_is_directory=True)
    else:
        (root / "folder").mkdir()
        (root / "folder/file").symlink_to(real / "folder/file")
    with pytest.raises((OSError, resources.LocalResourceError)):
        resources.capture_local_resources(profile=profile(tmp_path), material_root=root, material_files=(row,))


@pytest.mark.parametrize("kind", ["relative_parent", "explicit_alias", "system_alias"])
def test_explicit_material_root_is_resolved_once_before_relative_member_checks(tmp_path, monkeypatch, kind):
    import tempfile
    # macOS /tmp is an ordinary caller locator alias; inside-tree links still fail.
    with tempfile.TemporaryDirectory(prefix="crt-", dir="/tmp" if kind == "system_alias" else tmp_path) as directory:
        root = Path(directory) / "tree"
        root.mkdir()
        row = file_row(root, "member", b"exact input")
        if kind == "relative_parent":
            request_dir = Path(directory) / "request"
            request_dir.mkdir()
            monkeypatch.chdir(request_dir)
            locator = Path("../tree")
        elif kind == "explicit_alias":
            locator = Path(directory) / "alias"
            locator.symlink_to(root, target_is_directory=True)
        else:
            locator = root
            assert str(locator).startswith("/tmp/")
        body = resources.capture_local_resources(profile=profile(tmp_path), material_root=locator, material_files=(row,))
        assert resources.parse_local_resources(body)["material_root"] == str(root.resolve())
        linked = root / "linked"
        linked.symlink_to(root / "member")
        with pytest.raises(OSError):
            resources.capture_local_resources(profile=profile(tmp_path), material_root=locator,
                                               material_files=({**row, "relative_path": "linked"},))


def test_special_source_file_is_rejected_without_blocking(tmp_path):
    root = tmp_path / "origin"
    root.mkdir()
    os.mkfifo(root / "pipe")
    with pytest.raises(resources.LocalResourceError, match="regular"):
        resources.capture_local_resources(profile=profile(tmp_path), material_root=root,
            material_files=({"relative_path": "pipe", "sha256": "1"*64, "executable": False},))


@pytest.mark.parametrize("change", [
    {"command_id": ""}, {"argv": []}, {"argv": ["sh", "\x00"]}, {"argv": "sh"},
    {"cwd": "/tmp"}, {"cwd": "scratch/../source"}, {"cwd": "origin"},
    {"cwd": "source/missing"}, {"timeout_seconds": 0}, {"timeout_seconds": True},
    {"timeout_seconds": 9999}, {"extra": 1},
])
def test_command_structure_and_budget_are_checked_before_effects(tmp_path, change):
    with pytest.raises(resources.LocalResourceError):
        resources.capture_local_resources(profile=profile(tmp_path), commands=(command(**change),))


def test_command_id_unique_and_exact_argv_is_not_rewritten(tmp_path):
    with pytest.raises(resources.LocalResourceError, match="unique"):
        resources.capture_local_resources(profile=profile(tmp_path), commands=(command(), command()))
    item = command(command_id="x", argv=["/bin/sh", "-c", "printf '%s' \"$TMPDIR\" && true"], cwd="scratch/build")
    body = resources.capture_local_resources(profile=profile(tmp_path), commands=(item,))
    assert resources.parse_local_resources(body)["commands"] == [item]


@pytest.mark.parametrize("change", [
    {"execution_mode": "tool_free", "tool_policy": (), "attempt_workspace_policy": "none"},
    {"executor_adapter_revision": "v1"},
    {"transport_kind": "codex_cli", "provider_id": "openai", "executor_adapter_id": "codex_cli_agent_executor",
     "executor_adapter_revision": "v4", "execution_mode": "tool_free", "tool_policy": (), "attempt_workspace_policy": "none"},
    {"tool_policy": ("read",)}, {"attempt_workspace_policy": "none"},
])
def test_commands_never_add_missing_profile_capabilities(tmp_path, change):
    with pytest.raises(resources.LocalResourceError) as caught:
        resources.capture_local_resources(profile=profile(tmp_path, **change), commands=(command(),))
    assert caught.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


def test_dependencies_are_explicit_roots_not_recursive_snapshots(tmp_path):
    dependency = tmp_path / "dependency"
    dependency.mkdir()
    (dependency / "mutable-library").write_text("v1")
    body = resources.capture_local_resources(profile=profile(tmp_path), read_only_dependencies=(dependency,))
    assert resources.parse_local_resources(body)["read_only_dependencies"] == [str(dependency.resolve())]
    assert b"mutable-library" not in body and b'"v1"' not in body
    for roots in [(dependency, dependency), (Path("/"),)]:
        with pytest.raises(resources.LocalResourceError):
            resources.capture_local_resources(profile=profile(tmp_path), read_only_dependencies=roots)


@pytest.mark.parametrize("tamper", ["content", "mode", "extra", "delete", "symlink", "directory", "directory_mode"])
def test_material_drift_denies_output(tmp_path, tamper):
    body, origin, destination = package(tmp_path)
    resources.materialize_local_resources(body, materials_root=destination)
    source = destination / "source"
    source.chmod(0o755)
    target = source / "data.bin"
    if tamper == "content":
        target.chmod(0o644)
        target.write_bytes(b"different")
        target.chmod(0o444)
    elif tamper == "mode":
        target.chmod(0o555)
    elif tamper == "extra":
        (source / "extra").write_text("extra")
    elif tamper == "delete":
        target.unlink()
    elif tamper == "symlink":
        target.unlink()
        target.symlink_to(origin / "data.bin")
    elif tamper == "directory":
        (source / "empty-extra-dir").mkdir(mode=0o555)
    else:
        (source / "pkg").chmod(0o755)
    source.chmod(0o555)
    with pytest.raises(resources.LocalResourceError) as caught:
        resources.assert_local_materials_unchanged(body, materials_root=destination)
    assert caught.value.error_code == "ADAPTER_POLICY_VIOLATION"


def test_existing_different_material_tree_is_not_overwritten(tmp_path):
    body, _, materials = package(tmp_path)
    (materials / "source").mkdir()
    target = materials / "source/data.bin"
    target.write_bytes(b"keep this")
    with pytest.raises(resources.LocalResourceError):
        resources.materialize_local_resources(body, materials_root=materials)
    assert target.read_bytes() == b"keep this"


@pytest.mark.parametrize("field,value", [
    ("content_base64", "???"), ("content_base64", "YQ=="),
    ("relative_path", "../private"), ("sha256", "1"*64), ("executable", 1),
])
def test_parse_rejects_forged_control_content(tmp_path, field, value):
    body, _, _ = package(tmp_path)
    document = json.loads(body)
    document["material_files"][0][field] = value
    with pytest.raises(resources.LocalResourceError):
        resources.parse_local_resources(resources._canonical(document))


def binding(body, *, schema_ref=resources.LOCAL_RESOURCES_SCHEMA_REF, **overrides):
    fields = dict(logical_name=resources.LOCAL_RESOURCES_LOGICAL_NAME, input_ref="artifact:resources",
        input_sha256=hashlib.sha256(body).hexdigest(), schema_ref=schema_ref,
        schema_sha256=resources.LOCAL_RESOURCES_SCHEMA_SHA256, media_type=resources.LOCAL_RESOURCES_MEDIA_TYPE)
    fields.update(overrides)
    return ModuleInputBinding(**fields)


def prompt(inputs=(), **kwargs):
    return build_inline_provider_prompt(compiled_static_body='Return the value.' + OUTPUT_SCHEMA_MARKER + '{"type":"object"}',
        execution_specific_instructions="", inputs=inputs, output_constraint_mode=NATIVE_STRUCTURED_OUTPUT, **kwargs)


def test_prompt_only_contains_generated_index_not_private_control_bytes(tmp_path):
    body, origin, _ = package(tmp_path, commands=(command(),))
    description = resources.describe_local_resources(profile=profile(tmp_path), body=body)
    text = prompt(((binding(body), body),), resource_description=description)
    assert str(origin) not in text and "content_base64" not in text
    assert "from .value import VALUE" not in text and "../materials/source" in text
    assert "sandbox_command_execute" in text and '"command_id": "unit"' in text
    assert "## Task Input" not in text
    assert prompt() == "Return the value.\n"
    assert prompt(resource_description="") == prompt()


@pytest.mark.parametrize("overrides", [
    {"schema_sha256": "1"*64}, {"media_type": "text/plain"}, {"logical_name": "ordinary"},
])
def test_reserved_resource_schema_cannot_fall_back_to_visible_input(tmp_path, overrides):
    body, _, _ = package(tmp_path)
    with pytest.raises(ValueError, match="private local-resource"):
        prompt(((binding(body, **overrides), body),), resource_description="frozen description")


def test_bad_control_and_missing_description_are_not_silently_hidden(tmp_path):
    body, _, _ = package(tmp_path)
    with pytest.raises(ValueError, match="description"):
        prompt(((binding(body), body),))
    with pytest.raises(resources.LocalResourceError):
        prompt(((binding(b"{}"), b"{}"),), resource_description="frozen description")
    with pytest.raises(ValueError, match="binding"):
        prompt(((binding(body), body), (binding(body), body)), resource_description="frozen description")


def test_unrelated_schema_is_not_classified_by_the_same_logical_name():
    text = b"ordinary task text"
    result = prompt(((binding(text, schema_ref="schema:ordinary@v1"), text),))
    assert "### local_resources\nordinary task text" in result


@pytest.mark.parametrize("payload", [b"{}", b"[]", b"null", b"\xff", b'{"schema_version":NaN}',
    b'{"schema_version":"runtime_local_resources_v1","schema_version":"runtime_local_resources_v1"}'])
def test_malformed_private_packages_reject_without_filesystem_probing(payload, monkeypatch):
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("parser must not open paths"))
    with pytest.raises(resources.LocalResourceError):
        resources.parse_local_resources(payload)


def test_unsupported_platform_rejects_resources_before_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "unsupported-platform")
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("unsupported platform read files"))
    with pytest.raises(resources.LocalResourceError) as failure:
        resources.capture_local_resources(profile=profile(tmp_path), commands=(command(),))
    assert failure.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


def test_command_only_resource_tree_is_empty_and_task_input_is_not_tree_content(tmp_path):
    body = resources.capture_local_resources(profile=profile(tmp_path), commands=(command(cwd="scratch"),))
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "task_input").write_text("existing Adapter task")
    resources.materialize_local_resources(body, materials_root=materials)
    resources.assert_local_materials_unchanged(body, materials_root=materials)
    assert not list((materials / "source").iterdir())
    assert (materials / "task_input").read_text() == "existing Adapter task"


def test_source_child_cwd_and_relative_layout_description(tmp_path):
    body, origin, _ = package(tmp_path, commands=(command(cwd="source/pkg"),))
    description = resources.describe_local_resources(profile=profile(tmp_path), body=body)
    assert '"cwd": "source/pkg"' in description
    assert "mcp__runtime_commands__sandbox_command_execute" in description
    assert str(origin) not in description
    none = profile(tmp_path, attempt_workspace_policy="none", tool_policy=("read",))
    with pytest.raises(resources.LocalResourceError):
        resources.describe_local_resources(profile=none, body=body)
    assert "materials/task_input" in resources.describe_local_resources(profile=none, body=None)


def test_empty_file_and_boolean_execution_mode_are_preserved(tmp_path):
    root = tmp_path / "origin"
    root.mkdir()
    row = file_row(root, "empty", b"")
    body = resources.capture_local_resources(profile=profile(tmp_path), material_root=root, material_files=(row,))
    assert resources.parse_local_resources(body)["material_files"][0]["content_base64"] == ""
    with pytest.raises(resources.LocalResourceError):
        resources.capture_local_resources(profile=profile(tmp_path), material_root=root,
            material_files=({**row, "executable": 0},))


def test_destination_symlink_never_receives_material_writes(tmp_path):
    body, _, materials = package(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (materials / "source").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        resources.materialize_local_resources(body, materials_root=materials)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("mode", [NATIVE_STRUCTURED_OUTPUT, PROMPT_ONLY_JSON])
def test_no_resource_prompt_bytes_preserve_existing_order(mode):
    task = b'{"value":"original"}'
    item = binding(task, schema_ref="schema:ordinary@v1", logical_name="task_input")
    kwargs = dict(compiled_static_body="Task." + OUTPUT_SCHEMA_MARKER + '{"type":"object"}',
                  execution_specific_instructions="Exact extra instruction.", inputs=((item, task),),
                  output_constraint_mode=mode)
    original = build_inline_provider_prompt(**kwargs)
    assert build_inline_provider_prompt(**kwargs, resource_description="") == original
    augmented = build_inline_provider_prompt(**kwargs, resource_description="Fixed resource index.")
    assert "### task_input\n" + task.decode() in augmented
    assert augmented.count("Fixed resource index.") == 1
    assert augmented.index("### task_input") < augmented.index("Fixed resource index.")


@pytest.mark.parametrize("changes,error_code", [
    ({"tool_policy": ("read",)}, "ADAPTER_CAPABILITY_UNSUPPORTED"),
    ({"execution_mode": "tool_free", "tool_policy": (), "attempt_workspace_policy": "none"},
     "ADAPTER_CAPABILITY_UNSUPPORTED"),
    ({"timeout_seconds": 1}, "ADAPTER_REQUEST_INVALID"),
    ({"executor_adapter_id": "codex_cli_agent_executor", "executor_adapter_revision": "v4",
      "transport_kind": "codex_cli", "provider_id": "openai", "execution_mode": "tool_free",
      "tool_policy": (), "attempt_workspace_policy": "none"}, "ADAPTER_CAPABILITY_UNSUPPORTED"),
    ({"executor_adapter_revision": "v1"}, "ADAPTER_CAPABILITY_UNSUPPORTED"),
])
def test_captured_commands_cannot_borrow_the_original_profiles_capabilities(tmp_path, monkeypatch, changes, error_code):
    body, _, _ = package(tmp_path, commands=(command(),))
    narrower = profile(tmp_path, **changes)
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("validation cannot reopen origins"))
    with pytest.raises(resources.LocalResourceError) as caught:
        resources.validate_local_resources(profile=narrower, body=body)
    assert caught.value.error_code == error_code
    with pytest.raises(resources.LocalResourceError):
        resources.describe_local_resources(profile=narrower, body=body)


def test_validate_captured_resources_is_pure_for_same_profile_and_none(tmp_path, monkeypatch):
    selected = profile(tmp_path)
    body, _, _ = package(tmp_path, commands=(command(),))
    expected = resources.parse_local_resources(body)
    monkeypatch.setattr(resources, "_directory", lambda *_: pytest.fail("validation cannot reopen origins"))
    monkeypatch.setattr(Path, "resolve", lambda *_args, **_kwargs: pytest.fail("validation cannot resolve live resources"))
    assert resources.validate_local_resources(profile=selected, body=body) == expected
    assert resources.validate_local_resources(profile=selected, body=None) is None
