from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
import re
import shutil
import zipfile

import pytest

from tools.build_agent_runtime_api_reference import (
    API_SOURCES, ERROR_CONSTANTS, OUTPUT_PATHS,
    build_api_reference, main, render_api_reference,
)


ROOT = Path(__file__).resolve().parents[1]
AUTHORING = next(iter(API_SOURCES))


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", root / "pyproject.toml")
    for path in API_SOURCES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target)
    return root


def _files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def test_checked_in_documents_equal_source():
    build_api_reference(project_root=ROOT, check=True)
    body = render_api_reference(ROOT).decode()
    for fragment in (
        "not a running Reviewer", "same Module ref", "adding transport compatibility",
        "intentionally omitted", "execution_blocker_code", "idempotency_key",
        "single-node", "authorize", "PermissionError", "origin_bundle",
    ):
        assert fragment in body
    assert all(name in body for name in ERROR_CONSTANTS)
    assert "/Users/" not in body and "/private/tmp/" not in body


def test_generation_is_repeatable_and_only_writes_declared_outputs(source):
    sentinel = source / "docs/keep.md"
    sentinel.parent.mkdir()
    sentinel.write_text("user-owned", encoding="utf-8")
    before = _files(source)
    build_api_reference(project_root=source)
    first = _files(source)
    assert set(first) - set(before) == set(OUTPUT_PATHS)
    assert all(first[path] == digest for path, digest in before.items())
    build_api_reference(project_root=source)
    assert _files(source) == first
    build_api_reference(project_root=source, check=True)
    assert _files(source) == first


@pytest.mark.parametrize("target", OUTPUT_PATHS)
def test_check_rejects_missing_or_stale_without_writing(source, target):
    before = _files(source)
    with pytest.raises(ValueError, match="missing or stale"):
        build_api_reference(project_root=source, check=True)
    assert _files(source) == before
    build_api_reference(project_root=source)
    path = source / target
    path.write_text("stale", encoding="utf-8")
    before = _files(source)
    with pytest.raises(ValueError, match="missing or stale"):
        build_api_reference(project_root=source, check=True)
    assert _files(source) == before
    path.unlink()
    with pytest.raises(ValueError, match="missing or stale"):
        build_api_reference(project_root=source, check=True)
    assert not path.exists()


@pytest.mark.parametrize("before,after,expected", [
    ("module_version: str,", "module_version: str = 'probe_version',", "module_version: str='probe_version'"),
    ("Author and export one fixed Reviewer definition", "Author and export a documented probe definition", "documented probe definition"),
    ('"MODULE_EXECUTION_PROFILE_UNAVAILABLE"', '"PROBE_PROFILE_UNAVAILABLE"', "PROBE_PROFILE_UNAVAILABLE"),
])
def test_source_changes_are_exported_and_invalidate_old_docs(source, before, after, expected):
    build_api_reference(project_root=source)
    path = source / AUTHORING
    text = path.read_text(encoding="utf-8")
    assert before in text
    path.write_text(text.replace(before, after), encoding="utf-8")
    with pytest.raises(ValueError, match="missing or stale"):
        build_api_reference(project_root=source, check=True)
    assert expected in render_api_reference(source).decode()
    build_api_reference(project_root=source)
    build_api_reference(project_root=source, check=True)


@pytest.mark.parametrize("change,expected", [
    ("symbol", "missing public API symbol"),
    ("docstring", "missing public API docstring"),
    ("method_docstring", "missing public API docstring"),
    ("constant", "missing public error constant"),
])
def test_incomplete_source_fails_before_any_write(source, change, expected):
    path = source / AUTHORING
    tree = ast.parse(path.read_text())
    reviewer = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "ModuleReviewer")
    if change == "symbol":
        reviewer.name = "UndocumentedReviewer"
    elif change == "docstring":
        reviewer.body.pop(0)
    elif change == "method_docstring":
        next(node for node in reviewer.body if isinstance(node, ast.FunctionDef) and node.name == "export").body.pop(0)
    else:
        tree.body = [node for node in tree.body if not (
            isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == ERROR_CONSTANTS[0] for t in node.targets))]
    path.write_text(ast.unparse(tree), encoding="utf-8")
    before = _files(source)
    with pytest.raises(ValueError, match=expected):
        build_api_reference(project_root=source)
    assert _files(source) == before


def test_source_is_parsed_without_importing_or_executing(source):
    path = source / AUTHORING
    before = render_api_reference(source)
    with path.open("a", encoding="utf-8") as stream:
        stream.write('\nraise RuntimeError("SOURCE MUST NOT EXECUTE")\n')
    assert render_api_reference(source) == before


def test_symlink_output_is_rejected_without_writing_other_target(source, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("protected")
    target = source / OUTPUT_PATHS[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        build_api_reference(project_root=source)
    assert outside.read_text() == "protected"
    assert not (source / OUTPUT_PATHS[0]).exists()


def test_reference_public_imports_resolve():
    import agent_runtime
    from agent_runtime.registry import registry_module_authoring
    for names in API_SOURCES.values():
        for name in names:
            assert getattr(agent_runtime, name) is not None
    for name in ERROR_CONSTANTS:
        assert getattr(agent_runtime, name) == getattr(registry_module_authoring, name)


def test_signature_rendering_preserves_all_argument_kinds():
    from tools.build_agent_runtime_api_reference import _signature
    definition = ast.parse("async def probe(a: int, /, b=2, *values: str, c: bool=True, **options) -> int:\n    pass").body[0]
    generated = ast.parse(_signature(definition) + "\n    pass").body[0]
    assert ast.dump(generated) == ast.dump(definition)


def test_generated_parameter_sections_are_readable_markdown():
    body = render_api_reference(ROOT).decode()
    assert "**Args**\n\n- `project_root`:" in body
    assert "**Raises**\n\n- `ValueError`:" in body


def test_cli_failure_is_nonzero_and_check_is_readonly(source, monkeypatch, capsys):
    from tools import build_agent_runtime_api_reference as tool
    monkeypatch.setattr(tool, "build_api_reference", lambda **kwargs: build_api_reference(project_root=source, **kwargs))
    before = _files(source)
    assert main(["--check"]) == 1
    assert "missing or stale" in capsys.readouterr().err
    assert _files(source) == before


def test_wheel_contains_source_generated_reference_and_valid_document_links(tmp_path):
    # Reuse the existing package build fixture, not a second wheel builder.
    spec = importlib.util.spec_from_file_location("reference_package_test", ROOT / "tests/test_agent_runtime_packaging_boundary.py")
    packaging = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packaging)
    wheel = packaging._build_runtime_wheel(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        content = archive.read("agent_runtime/docs/agent_runtime_reviewer_api.md")
        assert content == render_api_reference(ROOT)
        for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", content.decode()):
            assert "agent_runtime/docs/" + target in archive.namelist()
        for name in ("README.md", "docs/agent_runtime_registration_runbook.md"):
            assert archive.read("agent_runtime/" + name) == (ROOT / name).read_bytes()


def _example(name):
    document = (ROOT / "docs/agent_runtime_registration_runbook.md").read_text()
    match = re.search(r"<!-- example:" + name + r":start -->\s*```python\n(.*?)\n```\s*<!-- example:" + name + r":end -->", document, re.S)
    assert match is not None, name
    return compile(match[1], "registration_runbook:" + name, "exec")


def test_task_navigation_and_local_links_are_closed():
    readme = (ROOT / "README.md").read_text()
    runbook = (ROOT / "docs/agent_runtime_registration_runbook.md").read_text()
    for query in ("注册新的 Reviewer", "register a new reviewer", "test a reviewer", "inspect a review"):
        assert query in readme
    for target in ("new-reviewer", "prepare-reviewer", "register-reviewer", "test-reviewer", "inspect-reviewer"):
        assert f'<a id="{target}"></a>' in runbook
    for path in (ROOT / "README.md", ROOT / "docs/agent_runtime_registration_runbook.md"):
        body = path.read_text()
        # Only this task's local document links, not unrelated framework URLs.
        for link in re.findall(r"\]\(([^)]+)\)", body):
            if "://" in link or not ("agent_runtime_" in link or link.startswith("#")):
                continue
            filename, _, anchor = link.partition("#")
            destination = path.parent / filename if filename else path
            assert destination.is_file(), link
            target_text = destination.read_text()
            if anchor:
                headings = re.findall(r"^#+ (.+)$", target_text, re.M)
                slugs = {re.sub(r"[^\w\- ]", "", item.lower()).replace(" ", "-") for item in headings}
                explicit = set(re.findall(r'<a id="([^"]+)"', target_text))
                assert anchor in slugs | explicit, link
        assert path.read_bytes() == (ROOT / "src/agent_runtime" / path.relative_to(ROOT)).read_bytes()


def test_task_runbook_preserves_environment_and_execution_boundaries():
    body = (ROOT / "docs/agent_runtime_registration_runbook.md").read_text()
    for phrase in ("不重新编译或注册", "Source owner", "profile_binding", "execution_schema",
                   "要求持久执行却找不到实际授权/存储接入时", "不生成 Workflow", "不会自动建表或迁移",
                   "origin_bundle", "相同 key", "授权替身", "输出校验", "持久回读"):
        assert phrase in body
    assert "persistence=not_requested" in body and "agent-runtime-evaluate --root" in body
    for phrase in ("prepare_local_workflow_module", "误传退出 2", "不读取旧文件的模型选择",
                   "不会成为这个新入口的默认", "明确更换模型使用新 key"):
        assert phrase in body


@pytest.mark.parametrize("ready", [True, False])
def test_documented_registration_uses_public_api_and_explicit_inputs(tmp_path, monkeypatch, ready):
    # Fixture supplies only test-owned inputs and persistence ports. The actual
    # source loader, compiler, Registry and plugin registration remain real.
    import test_agent_runtime_module_authoring as fixture
    from agent_runtime.registry import PostgresRuntimeReleaseStore, RuntimeReleaseRegistry, RuntimeReleaseBundle, runtime_owned_policy_schema_assets
    from types import SimpleNamespace
    registry = RuntimeReleaseRegistry()
    behavior, evaluation, retry = fixture._policies()
    profile = fixture._profile()
    registry.register_bundle(RuntimeReleaseBundle(
        schema_assets=runtime_owned_policy_schema_assets(), behavior_policies=(behavior,),
        evaluation_policies=(evaluation,), retry_policies=(retry,), execution_profiles=(profile,),
    ))
    calls = []
    class PersistencePort:
        def installed_schema_release(self):
            return SimpleNamespace(state="ready" if ready else "unknown")
        def load_release_registry(self):
            return registry
        def register_bundle(self, bundle):
            calls.append("register")
            assert not bundle.execution_profiles and not bundle.execution_variant_policies
            return registry.register_bundle(bundle)
    def from_dsn(database_url, *, schema):
        assert database_url == "test-connection" and schema == "test_registry"
        calls.append("connection")
        return PersistencePort()
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn", staticmethod(from_dsn))
    project = fixture._project(tmp_path)
    before = _files(project)
    variables = dict(database_url="test-connection", registry_schema="test_registry",
        project_root=project, skill_id=fixture.SKILL_ID, module_id=fixture.MODULE_ID, module_version="v1",
        plugin_id="documented_reviewer", plugin_version="v1",
        behavior_binding=(behavior.release_ref, behavior.release_sha256),
        evaluation_binding=(evaluation.release_ref, evaluation.release_sha256),
        retry_binding=(retry.release_ref, retry.release_sha256),
        profile_binding=(profile.release_ref, profile.release_sha256))
    if not ready:
        with pytest.raises(RuntimeError, match="schema is not ready"):
            exec(_example("register-reviewer"), variables)
        assert calls == ["connection"]
    else:
        exec(_example("register-reviewer"), variables)
        assert calls == ["connection", "register", "connection"]
        assert variables["resolved"] == variables["exported"].module_release
        assert not registry.snapshot().active_release_refs
        snapshot = registry.snapshot()
        exec(_example("register-reviewer"), variables)
        assert registry.snapshot() == snapshot
    assert _files(project) == before


@pytest.mark.parametrize("has_records", [True, False])
def test_documented_query_respects_actual_public_signature_and_empty_trace(monkeypatch, has_records):
    from types import SimpleNamespace
    from unittest.mock import create_autospec
    from agent_runtime.ledger import PostgresRuntimeExecutionQueryStore
    query = create_autospec(PostgresRuntimeExecutionQueryStore, instance=True)
    query.load_trace.return_value = SimpleNamespace(records=("test-record",) if has_records else ())
    query.list_content_metadata.return_value = ({"content_ref":"test-content"},)
    factory = create_autospec(PostgresRuntimeExecutionQueryStore.from_dsn, return_value=query)
    monkeypatch.setattr(PostgresRuntimeExecutionQueryStore, "from_dsn", factory)
    variables = dict(database_url="test-connection", execution_schema="test_execution", execution_id="execution_test")
    if has_records:
        exec(_example("inspect-reviewer"), variables)
        query.list_content_metadata.assert_called_once_with("execution_test")
    else:
        with pytest.raises(RuntimeError, match="No committed records"):
            exec(_example("inspect-reviewer"), variables)
        query.list_content_metadata.assert_not_called()
    factory.assert_called_once_with("test-connection", schema="test_execution")
    query.load_trace.assert_called_once_with("execution_test")
