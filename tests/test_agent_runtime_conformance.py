"""Backend-neutral conformance fixture tests."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from agent_runtime.testing.durability_backend_conformance import (
    ALPHA_PRIVATE_SENTINEL,
    BETA_PRIVATE_SENTINEL,
    CONFORMANCE_CONTRACT_VERSION,
    ConformanceWorkload,
    build_two_cell_conformance_fixture,
)


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_two_cell_fixture_is_backend_portable_and_ref_only(backend_id: str) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    assert CONFORMANCE_CONTRACT_VERSION == "agent_runtime_backend_conformance_v4"
    assert [case.binding.tenant_id for case in fixture.cases()] == [
        "institution_alpha",
        "institution_beta",
    ]
    encoded_payloads = json.dumps(
        [case.backend_payload() for case in fixture.cases()],
        sort_keys=True,
    )
    assert ALPHA_PRIVATE_SENTINEL not in encoded_payloads
    assert BETA_PRIVATE_SENTINEL not in encoded_payloads
    for dispatch_identity in (
        "module_run_id",
        "variant_id",
        "attempt_base_id",
        "execution_profile_ref",
    ):
        assert dispatch_identity not in encoded_payloads
    for case in fixture.cases():
        assert len({module.module_run_id for module in case.modules}) == len(case.modules)
        assert len({module.variant_id for module in case.modules}) == len(case.modules)
        assert len({module.attempt_base_id for module in case.modules}) == len(case.modules)
        assert all(
            module.workflow_execution_id == case.envelope.workflow_execution_id
            for module in case.modules
        )


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_two_cell_fixture_preserves_distinct_scenarios(backend_id: str) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    assert fixture.alpha.workload is ConformanceWorkload.REVISION_EVENT
    assert fixture.alpha.requires_revision_event is True
    assert fixture.alpha.requires_worker_failure is False
    assert fixture.beta.workload is ConformanceWorkload.CRASH_RECOVERY
    assert fixture.beta.requires_revision_event is False
    assert fixture.beta.requires_worker_failure is True
    assert fixture.alpha.expected_side_effect_commits == 1
    assert fixture.beta.expected_side_effect_commits == 1


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_shared_fixture_payload_uses_opaque_synthetic_vocabulary(
    backend_id: str,
) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    for case in fixture.cases():
        payload = json.dumps(case.backend_payload(), sort_keys=True).lower()
        assert all(module.module_key.startswith("synthetic_module_") for module in case.modules)
        for domain_token in (
            "research",
            "theme",
            "writer",
            "verifier",
            "reviewer",
            "debater",
            "draft",
            "pm",
        ):
            assert re.search(
                rf"(?<![a-z]){re.escape(domain_token)}(?![a-z])",
                payload,
            ) is None


@pytest.mark.parametrize(
        "runtime_path",
        [
            "src/agent_runtime/testing/durability_backend_conformance.py",
            "src/agent_runtime/testing/durability_temporal_conformance.py",
        ],
)
def test_shared_fixture_modules_define_no_domain_vocabulary(
    runtime_path: str,
) -> None:
    source = (
        Path(__file__).resolve().parents[1] / runtime_path
    ).read_text(encoding="utf-8").lower()

    for domain_token in (
        "research",
        "theme",
        "writer",
        "verifier",
        "reviewer",
        "debater",
        "draft",
        "candidate",
        "quality",
        "pm",
    ):
        assert re.search(
            rf"(?<![a-z]){re.escape(domain_token)}(?![a-z])",
            source,
        ) is None


def _role_check_text(runtime_path: Path, source: str) -> str:
    if runtime_path != Path("testing/conformance_agent_capability_verification.py"):
        return source.lower()

    tree = ast.parse(source)
    descriptions: set[ast.Constant] = set()

    def mark_text(node: ast.AST) -> None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            descriptions.add(node)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for item in node.elts:
                mark_text(item)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            mark_text(node.left)
            mark_text(node.right)
        elif isinstance(node, ast.JoinedStr):
            for item in node.values:
                if isinstance(item, ast.Constant):
                    mark_text(item)

    def render_body_nodes(node: ast.AST):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        yield node
        for child in ast.iter_child_nodes(node):
            yield from render_body_nodes(child)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_capability_case":
            for keyword in node.keywords:
                if keyword.arg in {
                    "test_paths", "example_test_refs", "input_description",
                    "expected_result", "environment_prerequisites",
                }:
                    mark_text(keyword.value)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "_CASE_LABELS" for target in node.targets
        ) and isinstance(node.value, ast.Dict):
            for value in node.value.values:
                mark_text(value)
        if isinstance(node, ast.FunctionDef) and node.name in {
            "render_agent_capability_catalog_markdown", "render_agent_capability_runbook_markdown",
        }:
            for item in (child for statement in node.body for child in render_body_nodes(statement)):
                if isinstance(item, ast.Return) and item.value is not None:
                    mark_text(item.value)
                elif isinstance(item, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == "sections" for target in item.targets
                ):
                    mark_text(item.value)
                elif isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and (
                    isinstance(item.func.value, ast.Name) and item.func.value.id == "sections"
                    and item.func.attr == "append"
                ):
                    for argument in item.args:
                        mark_text(argument)
    for node in descriptions:
        node.value = ""
    return ast.unparse(tree).lower()


def _assert_generic_role_vocabulary(runtime_path: Path, source: str, *, reviewer_capability: bool = False) -> None:
    checked = _role_check_text(runtime_path, source)
    for role in ("theme", "writer", "verifier", "reviewer", "debater", "pm"):
        if role == "reviewer" and reviewer_capability:
            continue
        assert re.search(rf"(?<![a-z]){role}(?![a-z])", checked) is None, runtime_path


@pytest.mark.parametrize("source", (
    '_CASE_LABELS = {"case": "Reviewer 文档"}',
    '_capability_case(test_paths=("tests/test_reviewer.py",), expected_result=("Reviewer 输出",))',
    'def render_agent_capability_runbook_markdown():\n    sections = ["Reviewer 文档"]\n    sections.append(f"Reviewer: {value}")\n    return "Reviewer 索引"',
))
def test_capability_navigation_literals_are_not_executable_roles(source: str) -> None:
    _assert_generic_role_vocabulary(Path("testing/conformance_agent_capability_verification.py"), source)


@pytest.mark.parametrize("source", (
    'if kind == "reviewer":\n    execute()',
    'dispatch = {"reviewer": execute}',
    'import reviewer',
    'from module import reviewer',
    'reviewer.run()',
    '_capability_case(expected_result=("reviewer" if condition else "value",))',
    'def render_agent_capability_runbook_markdown():\n    return f"Label: {kind == \'reviewer\'}"',
    'def render_agent_capability_runbook_markdown():\n    sections = [lookup("reviewer")]',
    'def render_agent_capability_runbook_markdown():\n    def role_name():\n        return "reviewer"\n    if kind == role_name():\n        execute()\n    return "document"',
    'def render_agent_capability_runbook_markdown():\n    async def role_name():\n        return "reviewer"\n    return "document"',
    'def render_agent_capability_runbook_markdown():\n    class Handler:\n        def role_name(self):\n            return "reviewer"\n    return "document"',
))
def test_capability_navigation_does_not_hide_role_decisions_or_imports(source: str) -> None:
    with pytest.raises(AssertionError):
        _assert_generic_role_vocabulary(Path("testing/conformance_agent_capability_verification.py"), source)


def test_capability_navigation_exemption_does_not_apply_to_other_runtime_files() -> None:
    for path in ("execution/execution_module_invocation.py", "testing/other.py"):
        with pytest.raises(AssertionError):
            _assert_generic_role_vocabulary(Path(path), '_CASE_LABELS = {"case": "Reviewer 文档"}')


def test_runtime_role_vocabulary_is_confined_to_review_capability_owners() -> None:
    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    role_source = runtime_root / "registry/registry_module_authoring.py"
    # Registry owns fixed defaults; Execution prepares and enforces that snapshot.
    # This exact surface list does not admit other roles or host business meaning.
    capability_paths = {
        "__init__.py", "contracts/registry_release_definition.py",
        "registry/registry_reviewer_defaults.py", "registry/registry_plugin_registration.py",
        "registry/registry_local_persistence.py", "registry/registry_module_loading.py",
        "registry/registry_workflow_authoring.py", "registry/registry_release_compilation.py",
        "registry/registry_release_registration.py", "registry/registry_architecture_registration.py",
        "execution/execution_module_invocation.py", "conformance/conformance_architecture_manifest.py",
        "execution/execution_local_invocation.py",
    }

    for runtime_path in sorted(runtime_root.rglob("*.py")):
        source = runtime_path.read_text(encoding="utf-8")
        if runtime_path == role_source:
            for domain_token in ("research", "theme", "thesis", "pm"):
                assert re.search(
                    rf"(?<![a-z]){domain_token}(?![a-z])",
                    source.lower(),
                ) is None, runtime_path
            continue
        relative = runtime_path.relative_to(runtime_root)
        _assert_generic_role_vocabulary(relative, source, reviewer_capability=relative.as_posix() in capability_paths)


def test_reviewer_defaults_do_not_admit_other_roles_or_unrelated_surfaces():
    for word in ("theme", "writer", "verifier", "debater", "pm"):
        with pytest.raises(AssertionError):
            _assert_generic_role_vocabulary(Path("contracts/registry_release_definition.py"), word, reviewer_capability=True)
    with pytest.raises(AssertionError):
        _assert_generic_role_vocabulary(Path("execution/unrelated.py"), "reviewer")


def test_fixture_rejects_an_invalid_backend_identity() -> None:
    with pytest.raises(ValueError, match="invalid backend_id"):
        build_two_cell_conformance_fixture("Temporal Cloud")


def test_fixture_rejects_one_variant_shared_by_multiple_module_runs() -> None:
    fixture = build_two_cell_conformance_fixture("temporal")
    first, second, *remaining = fixture.alpha.modules
    invalid_second = replace(second, variant_id=first.variant_id)
    invalid_case = replace(
        fixture.alpha,
        modules=(first, invalid_second, *remaining),
    )

    with pytest.raises(ValueError, match="variant_id must be unique per module"):
        invalid_case.validate()


def test_runtime_wall_clock_strings_carry_the_utc_suffix() -> None:
    """Every string-typed instant declares its semantics as `*_at_utc`.

    Aware ``datetime`` objects carry their semantics in the type and stay
    unsuffixed; only the canonical string form is naming-constrained.
    """

    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    unsuffixed_instant = re.compile(
        r"^\s+\w*(?:_at|_time|_timestamp|_date): str\b",
        re.MULTILINE,
    )

    for runtime_path in sorted(runtime_root.rglob("*.py")):
        source = runtime_path.read_text(encoding="utf-8")
        assert unsuffixed_instant.search(source) is None, runtime_path
