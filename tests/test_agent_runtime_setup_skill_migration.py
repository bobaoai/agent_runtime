"""Setup replaces the retired agent-runtime-evaluation Skill with agent-runtime-test-run."""
import hashlib
import json
from importlib.resources import files
from pathlib import Path

import pytest

from agent_runtime.foundation import foundation_environment_setup as setup


FIXTURES = Path(__file__).parent / "fixtures/operator_skills_dev7"
OLD = "agent-runtime-evaluation"
NEW = "agent-runtime-test-run"
REGISTRATION = "agent-runtime-registration"
HOSTS = (".agents", ".claude")


def _digest(body):
    return hashlib.sha256(body).hexdigest()


def _dev7_root(root, *, evaluation=None, registration=None, metadata=True):
    """Lay out a root exactly as 0.2.0.dev7 setup left it, optionally with local edits."""
    evaluation = (FIXTURES / f"{OLD}.SKILL.md").read_bytes() if evaluation is None else evaluation
    registration = (FIXTURES / f"{REGISTRATION}.SKILL.md").read_bytes() if registration is None else registration
    for host in HOSTS:
        for name, body in ((OLD, evaluation), (REGISTRATION, registration)):
            path = root / host / "skills" / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    if metadata:
        state = root / ".runtime/setup.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"format": "agent_runtime_setup_v1", "skill_sha256": {
            OLD: _digest(evaluation), REGISTRATION: _digest(registration)}}, sort_keys=True, indent=2) + "\n")
    return root


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _packaged(name):
    return files("agent_runtime").joinpath("skills", name, "SKILL.md").read_bytes()


def _assert_migrated(root):
    for host in HOSTS:
        assert (root / host / "skills" / NEW / "SKILL.md").read_bytes() == _packaged(NEW)
        assert (root / host / "skills" / REGISTRATION / "SKILL.md").read_bytes() == _packaged(REGISTRATION)
        assert not (root / host / "skills" / OLD).exists()
    state = json.loads((root / ".runtime/setup.json").read_text())
    assert state["skill_sha256"] == {NEW: _digest(_packaged(NEW)), REGISTRATION: _digest(_packaged(REGISTRATION))}


@pytest.mark.deterministic
def test_exact_dev7_skills_are_migrated_and_the_next_setup_writes_nothing(tmp_path):
    root = _dev7_root(tmp_path)
    written = setup.setup_runtime(root)
    _assert_migrated(root)
    assert {str(Path(p).relative_to(root)) for p in written} >= {f"{host}/skills/{OLD}/SKILL.md" for host in HOSTS}
    assert setup.setup_runtime(root) == ()


@pytest.mark.deterministic
def test_dev7_skills_without_metadata_are_still_recognized_by_exact_hash(tmp_path):
    root = _dev7_root(tmp_path, metadata=False)
    setup.setup_runtime(root)
    _assert_migrated(root)


@pytest.mark.deterministic
def test_a_retired_skill_with_its_recorded_hash_is_removed(tmp_path):
    body = b"---\nname: agent-runtime-evaluation\n---\nrecorded by an earlier setup\n"
    root = _dev7_root(tmp_path, evaluation=body)
    setup.setup_runtime(root)
    _assert_migrated(root)


@pytest.mark.deterministic
def test_a_locally_changed_retired_skill_blocks_every_write(tmp_path):
    root = _dev7_root(tmp_path, metadata=True)
    (root / ".claude/skills" / OLD / "SKILL.md").write_bytes(b"local notes the operator kept\n")
    before = _files(root)
    with pytest.raises(ValueError, match="local content in a retired Skill"):
        setup.setup_runtime(root)
    assert _files(root) == before


@pytest.mark.deterministic
def test_an_occupied_new_skill_target_blocks_every_write(tmp_path):
    root = _dev7_root(tmp_path)
    target = root / ".agents/skills" / NEW / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone else's skill\n")
    before = _files(root)
    with pytest.raises(ValueError, match="local Skill content"):
        setup.setup_runtime(root)
    assert _files(root) == before


def _operations(monkeypatch, root, *, fail_at=None):
    order = []
    write, remove = setup._write, setup._remove
    def guard(kind, path):
        if fail_at is not None and len(order) == fail_at:
            raise OSError("interrupted")
        order.append((kind, str(Path(path).relative_to(root))))
    monkeypatch.setattr(setup, "_write", lambda path, content: (guard("write", path), write(path, content)))
    monkeypatch.setattr(setup, "_remove", lambda path: (guard("remove", path), remove(path)))
    return order


@pytest.mark.deterministic
def test_new_skills_are_written_before_retired_files_are_removed_and_metadata_is_last(tmp_path, monkeypatch):
    root = _dev7_root(tmp_path)
    order = _operations(monkeypatch, root)
    setup.setup_runtime(root)
    kinds = [kind for kind, _ in order]
    new_writes = [i for i, (kind, path) in enumerate(order) if kind == "write" and f"/{NEW}/" in path]
    removals = [i for i, (kind, _) in enumerate(order) if kind == "remove"]
    assert len(new_writes) == 2 and len(removals) == 2 and max(new_writes) < min(removals)
    assert order[-1] == ("write", ".runtime/setup.json") and kinds.count("write") == len(order) - 2


@pytest.mark.deterministic
@pytest.mark.parametrize("fail_at", range(7))
def test_an_interrupted_migration_converges_when_setup_is_repeated(tmp_path, monkeypatch, fail_at):
    root = _dev7_root(tmp_path)
    with monkeypatch.context() as patch:
        _operations(patch, root, fail_at=fail_at)
        with pytest.raises(OSError, match="interrupted"):
            setup.setup_runtime(root)
    setup.setup_runtime(root)
    _assert_migrated(root)
    assert setup.setup_runtime(root) == ()


@pytest.mark.deterministic
def test_a_retired_skill_left_under_new_metadata_is_still_removed(tmp_path):
    root = tmp_path
    setup.setup_runtime(root)
    residual = root / ".claude/skills" / OLD / "SKILL.md"
    residual.parent.mkdir(parents=True)
    residual.write_bytes((FIXTURES / f"{OLD}.SKILL.md").read_bytes())
    assert setup.setup_runtime(root) == (residual.resolve(),)
    assert not residual.parent.exists()


@pytest.mark.deterministic
def test_an_emptied_retired_folder_is_removed_on_the_next_setup(tmp_path):
    root = tmp_path
    setup.setup_runtime(root)
    folder = root / ".agents/skills" / OLD
    folder.mkdir(parents=True)
    setup.setup_runtime(root)
    assert not folder.exists()
    assert setup.setup_runtime(root) == ()
