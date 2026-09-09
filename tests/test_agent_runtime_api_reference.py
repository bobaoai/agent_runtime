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
