"""Bounded local setup and the existing command entry points."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from agent_runtime import setup_runtime
from agent_runtime.foundation import foundation_environment_setup as setup


def _snapshot(root):
    return {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def package(tmp_path, monkeypatch):
    root = tmp_path / "package"
    for name in setup._SKILLS:
        target = root / "skills" / name / "SKILL.md"
        target.parent.mkdir(parents=True)
        target.write_text("package content: " + name)
    monkeypatch.setattr(setup, "files", lambda _: root)
    return root


def test_first_setup_and_ready_path_only_touch_fixed_local_resources(tmp_path, package, monkeypatch):
    root = tmp_path / "host"
    definition = root / ".runtime/module/existing/v1.json"
    definition.parent.mkdir(parents=True)
    definition.write_text("opaque registered definition")
    other = root / ".claude/skills/user/SKILL.md"
    other.parent.mkdir(parents=True)
    other.write_text("user content")
    before = _snapshot(root)
    written = setup_runtime(root)
    assert len(written) == 5
    assert definition.read_text() == "opaque registered definition"
    assert other.read_text() == "user content"
    for host in setup._HOSTS:
        for name in setup._SKILLS:
            assert (root / host / "skills" / name / "SKILL.md").read_bytes() == (
                package / "skills" / name / "SKILL.md").read_bytes()
    current = _snapshot(root)
    assert all(current[path] == value for path, value in before.items())
    monkeypatch.setattr(Path, "glob", lambda *a, **k: pytest.fail("setup must not scan"))
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: pytest.fail("setup must not scan"))
    monkeypatch.setattr(setup, "_write", lambda *a: pytest.fail("ready setup must not write"))
    for _ in range(5):
        assert setup_runtime(root) == ()
    assert all((root / path).read_bytes() == body and (root / path).stat().st_mtime_ns == modified
               for path, (body, modified) in current.items())


def test_setup_creates_absent_root_and_repairs_only_missing_resource(tmp_path, package):
    root = tmp_path / "new/host"
    assert len(setup_runtime(root)) == 5
    missing = root / ".agents/skills/agent-runtime-evaluation/SKILL.md"
    missing.unlink()
    assert setup_runtime(root) == (missing,)


def test_managed_resource_upgrade_and_same_content_adoption(tmp_path, package):
    root = tmp_path / "host"
    setup_runtime(root)
    source = package / "skills/agent-runtime-registration/SKILL.md"
    source.write_text("next reviewed registration method")
    assert len(setup_runtime(root)) == 3
    metadata = root / ".runtime/setup.json"
    metadata.unlink()
    assert setup_runtime(root) == (metadata,)


def test_known_predecessor_uses_exact_content_not_name(tmp_path, package, monkeypatch):
    root = tmp_path / "host"
    target = root / ".agents/skills/agent-runtime-registration/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"reviewed predecessor")
    monkeypatch.setattr(setup, "_LEGACY_SKILL_SHA256", {
        "agent-runtime-registration": {hashlib.sha256(target.read_bytes()).hexdigest()}})
    assert len(setup_runtime(root)) == 5
    target.write_bytes(b"locally modified predecessor")
    before = _snapshot(root)
    with pytest.raises(ValueError, match="local Skill content"):
        setup_runtime(root)
    assert _snapshot(root) == before


def test_unknown_content_preflight_writes_nothing(tmp_path, package):
    root = tmp_path / "host"
    target = root / ".claude/skills/agent-runtime-evaluation/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("user's different Skill")
    before = _snapshot(root)
    with pytest.raises(ValueError, match=str(target)):
        setup_runtime(root)
    assert _snapshot(root) == before
    assert not (root / ".runtime").exists()


@pytest.mark.parametrize("value", ["{", "null", "[]", '{"format":"wrong","skill_sha256":{}}',
    '{"format":"agent_runtime_setup_v1","skill_sha256":{"unknown":"a"}}',
    '{"format":"agent_runtime_setup_v1","skill_sha256":{"agent-runtime-registration":2}}'])
def test_invalid_setup_metadata_is_not_replaced(tmp_path, package, value):
    root = tmp_path / "host"
    target = root / ".runtime/setup.json"
    target.parent.mkdir(parents=True)
    target.write_text(value)
    before = _snapshot(root)
    with pytest.raises(ValueError, match="Invalid Runtime setup metadata"):
        setup_runtime(root)
    assert _snapshot(root) == before


@pytest.mark.parametrize("relative", [".runtime", ".runtime/setup.json", ".agents", ".agents/skills",
    ".agents/skills/agent-runtime-registration", ".agents/skills/agent-runtime-registration/SKILL.md",
    ".claude/skills/agent-runtime-evaluation/SKILL.md"])
def test_fixed_target_symlinks_do_not_escape(tmp_path, package, relative):
    root = tmp_path / "host"
    target = root / relative
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "keep"
    protected.write_text("protected")
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        setup_runtime(root)
    assert protected.read_text() == "protected"
    assert sorted(p.name for p in outside.iterdir()) == ["keep"]


def test_native_file_error_does_not_replace_directory(tmp_path, package):
    root = tmp_path / "host"
    target = root / ".agents/skills/agent-runtime-registration/SKILL.md"
    target.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a regular file"):
        setup_runtime(root)
    assert target.is_dir() and not (root / ".runtime").exists()


def test_metadata_write_failure_can_finish_with_same_package(tmp_path, package, monkeypatch):
    root = tmp_path / "host"
    original = setup._write
    def interrupted(path, content):
        if path.name == "setup.json":
            raise OSError("setup metadata write failed")
        original(path, content)
    monkeypatch.setattr(setup, "_write", interrupted)
    with pytest.raises(OSError, match="metadata write failed"):
        setup_runtime(root)
    assert (root / ".claude/skills/agent-runtime-evaluation/SKILL.md").is_file()
    monkeypatch.setattr(setup, "_write", original)
    assert setup_runtime(root) == (root / ".runtime/setup.json",)
    assert setup_runtime(root) == ()


def test_atomic_write_failure_cleans_own_temporary_file(tmp_path, package, monkeypatch):
    root = tmp_path / "host"
    def refuse(*args):
        raise OSError("replace denied")
    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="replace denied"):
        setup_runtime(root)
    assert _snapshot(root) == {}


@pytest.mark.parametrize("command", ["load", "register", "register-reviewer"])
def test_registration_commands_setup_before_business_dispatch(tmp_path, monkeypatch, command):
    from agent_runtime.registry import registry_local_persistence as cli
    events = []
    monkeypatch.setattr(setup, "setup_runtime", lambda root: events.append(("setup", root)))
    monkeypatch.setattr(cli, "_run_command", lambda args: events.append(("operation", args.command)) or 0)
    arguments = {"load": ["--kind", "module", "--id", "sample"],
        "register": ["--bundle", "bundle.json", "--plugin-id", "sample", "--plugin-version", "v1"],
        "register-reviewer": ["--skill-id", "sample", "--module-id", "sample", "--version", "v1"]}
    assert cli.main([command, "--root", str(tmp_path), *arguments[command]]) == 0
    assert events == [("setup", tmp_path), ("operation", command)]


def test_evaluation_setup_before_invocation_and_failure_stops(tmp_path, monkeypatch, capsys):
    from agent_runtime.testing import execution_local_evaluation as cli
    events = []
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    def prepare(root):
        events.append("setup")
    monkeypatch.setattr(setup, "setup_runtime", prepare)
    monkeypatch.setattr(cli, "evaluate_local_workflow_module", lambda *a, **k:
        events.append("evaluation") or {"status": "completed"})
    args = ["--root", str(tmp_path), "--workflow", "sample", "--input", str(payload)]
    assert cli.main(args) == 0 and events == ["setup", "evaluation"]
    assert json.loads(capsys.readouterr().out) == {"status": "completed"}
    def fail(root):
        raise ValueError("setup conflict")
    monkeypatch.setattr(setup, "setup_runtime", fail)
    events.clear()
    assert cli.main(args) == 1 and events == []
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["detail"] == "setup conflict"


def test_argument_errors_and_help_do_not_run_setup(tmp_path, monkeypatch):
    from agent_runtime.registry import registry_local_persistence as registry
    from agent_runtime.testing import execution_local_evaluation as evaluation
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("must parse first"))
    for main in (registry.main, evaluation.main):
        with pytest.raises(SystemExit) as help_result:
            main(["--help"])
        assert help_result.value.code == 0
        with pytest.raises(SystemExit) as bad:
            main(["--unknown"])
        assert bad.value.code == 2


def test_setup_failure_stops_registration_dispatch(tmp_path, monkeypatch, capsys):
    from agent_runtime.registry import registry_local_persistence as cli
    def conflict(root):
        raise ValueError("local setup conflict")
    monkeypatch.setattr(setup, "setup_runtime", conflict)
    monkeypatch.setattr(cli, "_run_command", lambda *a: pytest.fail("must not register/load"))
    assert cli.main(["load", "--root", str(tmp_path), "--kind", "module", "--id", "test"]) == 1
    result = capsys.readouterr()
    assert not result.out and json.loads(result.err)["detail"] == "local setup conflict"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO probe")
@pytest.mark.parametrize("relative", [".runtime/setup.json", ".agents/skills/agent-runtime-registration/SKILL.md"])
def test_fifo_is_rejected_without_opening_its_content(tmp_path, package, monkeypatch, relative):
    root = tmp_path / "host"
    target = root / relative
    target.parent.mkdir(parents=True)
    os.mkfifo(target)
    original = Path.read_bytes
    def checked(path):
        if path == target:
            pytest.fail("FIFO must not be opened for reading")
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", checked)
    with pytest.raises(ValueError, match="not a regular file"):
        setup_runtime(root)
    assert not (root / ".claude").exists()


def test_literal_tilde_root_has_the_same_meaning_for_setup_and_load(tmp_path, package, monkeypatch, capsys):
    from agent_runtime.registry import registry_local_persistence as cli
    monkeypatch.chdir(tmp_path)
    # A regression must not accidentally prepare the real user's home.
    monkeypatch.setattr(Path, "expanduser", lambda self: pytest.fail("no implicit tilde expansion"))
    seen = []
    load = cli.load_runtime_registration
    def observed(root, *args):
        seen.append(root.resolve())
        return load(root, *args)
    monkeypatch.setattr(cli, "load_runtime_registration", observed)
    assert cli.main(["load", "--root", "~/host", "--kind", "module", "--id", "absent"]) == 1
    target = tmp_path / "~/host"
    assert (target / ".runtime/setup.json").is_file()
    assert (target / ".agents/skills/agent-runtime-registration/SKILL.md").is_file()
    assert seen == [target]
    assert json.loads(capsys.readouterr().err)["detail"] == "No registered module: absent"
