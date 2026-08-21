from __future__ import annotations

from dataclasses import fields, is_dataclass
import json
from pathlib import Path
import shutil
from typing import Any

import pytest

from agent_runtime.contracts.registry_release_definition import (
    ReleaseAdmissionState,
)
from agent_runtime.registry.registry_authoring_inventory_loading import (
    RUNTIME_AUTHORING_INVENTORY_SCHEMA_VERSION,
    RUNTIME_MODULE_REGISTRATION_SCHEMA_VERSION,
    RuntimeAuthoringSourceSet,
    load_runtime_authoring_inventory,
)
from agent_runtime.registry.registry_authoring_release_building import (
    RuntimeReleaseBuildResult,
    build_runtime_release_set,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_RELATIVE = Path("examples/runtime_authoring_reference")
INVENTORY_RELATIVE = (
    REFERENCE_RELATIVE / "runtime_authoring_inventory.json"
).as_posix()


def _copy_reference(tmp_path: Path, *, container: str = "checkout") -> Path:
    root = tmp_path / container
    target = root / REFERENCE_RELATIVE
    target.parent.mkdir(parents=True)
    shutil.copytree(REPO_ROOT / REFERENCE_RELATIVE, target)
    return root


def _load(root: Path = REPO_ROOT) -> RuntimeAuthoringSourceSet:
    return load_runtime_authoring_inventory(root, INVENTORY_RELATIVE)


def _build(root: Path = REPO_ROOT) -> RuntimeReleaseBuildResult:
    return build_runtime_release_set(_load(root))


def _manifest(root: Path, module_id: str) -> Path:
    return (
        root
        / REFERENCE_RELATIVE
        / "modules"
        / module_id
        / "module_registration.json"
    )


def _rewrite_json(path: Path, update) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    update(payload)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _assert_path_free_shape(value: Any) -> None:
    if is_dataclass(value):
        for field in fields(value):
            assert not field.name.endswith("_path")
            _assert_path_free_shape(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            _assert_path_free_shape(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            assert not str(key).endswith("_path")
            _assert_path_free_shape(item)
    else:
        assert not isinstance(value, Path)


def test_reference_inventory_builds_all_release_families_candidate_only() -> None:
    source_set = _load()
    result = build_runtime_release_set(source_set)
    bundle = result.plugin.release_bundle

    assert RUNTIME_AUTHORING_INVENTORY_SCHEMA_VERSION == (
        "runtime_authoring_inventory_v1"
    )
    assert RUNTIME_MODULE_REGISTRATION_SCHEMA_VERSION == (
        "runtime_module_registration_v3"
    )
    assert source_set.inventory_ref == "runtime-authoring:opaque_review@v1"
    assert len(bundle.schema_assets) == 10
    assert len(bundle.prompt_components) == 2
    assert len(bundle.prompt_bundles) == 1
    assert len(bundle.behavior_policies) == 1
    assert len(bundle.evaluation_policies) == 1
    assert len(bundle.retry_policies) == 1
    assert len(bundle.execution_variant_policies) == 1
    assert len(bundle.execution_profiles) == 1
    assert len(bundle.modules) == 3
    assert len(bundle.workflows) == 1
    assert {intent.state for intent in bundle.admission_intents} == {
        ReleaseAdmissionState.CANDIDATE
    }
    assert result.root_workflows == bundle.workflows
    _assert_path_free_shape(source_set)
    _assert_path_free_shape(result)


def test_identical_inventory_rebuild_is_byte_stable() -> None:
    first = _build()
    second = _build()

    assert first == second
    assert first.release_identities() == second.release_identities()


def test_same_bytes_under_another_authoring_root_keep_release_identity(
    tmp_path: Path,
) -> None:
    first_root = _copy_reference(tmp_path, container="first")
    second_root = _copy_reference(tmp_path, container="second")

    first = _build(first_root)
    second = _build(second_root)

    assert first.inventory_sha256 == second.inventory_sha256
    assert first.release_identities() == second.release_identities()


def test_unselected_file_is_inert(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    before = _build(root)
    unselected = root / REFERENCE_RELATIVE / "notes" / "ignored.md"
    unselected.parent.mkdir()
    unselected.write_text("Not selected.\n", encoding="utf-8")

    after = _build(root)

    assert after == before


def test_selected_instruction_changes_only_dependent_release_closure(
    tmp_path: Path,
) -> None:
    root = _copy_reference(tmp_path)
    before = _build(root)
    prompt = (
        root
        / REFERENCE_RELATIVE
        / "modules"
        / "opaque_writer"
        / "prompt.md"
    )
    prompt.write_text(
        "Return one revised opaque result that conforms to the schema.\n",
        encoding="utf-8",
    )
    after = _build(root)
    before_map = dict(before.release_identities())
    after_map = dict(after.release_identities())
    changed = {
        ref
        for ref in set(before_map) | set(after_map)
        if before_map.get(ref) != after_map.get(ref)
    }

    assert changed == {
        "prompt-bundle:opaque_writer_prompt_bundle@v1",
        "prompt-component:opaque_writer_task_instruction@v1",
        "runtime-module:opaque_writer@v1",
        "runtime-workflow:opaque_review@v1",
        "execution-variant-policy:opaque_review_default@v1",
    }
    assert before_map["behavior-policy:opaque_behavior@v1"] == after_map[
        "behavior-policy:opaque_behavior@v1"
    ]
    assert before_map["execution-profile:opaque_profile@v1"] == after_map[
        "execution-profile:opaque_profile@v1"
    ]


def test_builder_performs_no_filesystem_read_after_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_set = _load()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("builder attempted filesystem access")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    result = build_runtime_release_set(source_set)

    assert result.plugin.release_bundle.workflows[0].workflow_id == "opaque_review"


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda payload: payload.__setitem__(
                "owner_contract_ref", "repo-file:contracts/opaque_review.md"
            ),
            "owner_contract_ref",
        ),
        (
            lambda payload: payload.__setitem__(
                "instruction_source_ref", "file:prompt.md"
            ),
            "instruction_source_ref",
        ),
    ),
)
def test_path_shaped_runtime_refs_fail_at_loader(
    tmp_path: Path,
    mutation,
    message: str,
) -> None:
    root = _copy_reference(tmp_path)
    _rewrite_json(_manifest(root, "opaque_writer"), mutation)

    with pytest.raises(ValueError, match=message):
        _load(root)


def test_module_root_rejects_undeclared_member(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    extra = _manifest(root, "opaque_writer").parent / "ambient.md"
    extra.write_text("Ambient.\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Module root closure"):
        _load(root)


def test_module_schemas_reject_undeclared_member(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    extra = _manifest(root, "opaque_writer").parent / "schemas" / "ambient.json"
    extra.write_text('{"$id": "schema:ambient@v1"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="schemas directory differs"):
        _load(root)


@pytest.mark.parametrize(
    ("schema_name", "expected_message"),
    (
        ("input.schema.json", "input_schema_ref differs"),
        ("output.schema.json", "output_schema_ref differs"),
    ),
)
def test_schema_id_must_match_declared_module_ref(
    tmp_path: Path,
    schema_name: str,
    expected_message: str,
) -> None:
    root = _copy_reference(tmp_path)
    schema_path = _manifest(root, "opaque_writer").parent / "schemas" / schema_name
    _rewrite_json(
        schema_path,
        lambda payload: payload.__setitem__("$id", "schema:wrong_owner@v1"),
    )

    with pytest.raises(ValueError, match=expected_message):
        _load(root)


def test_agent_module_requires_skill_while_non_agent_skill_is_optional(
    tmp_path: Path,
) -> None:
    invalid_root = _copy_reference(tmp_path, container="agent_without_skill")
    _rewrite_json(
        _manifest(invalid_root, "opaque_writer"),
        lambda payload: payload.update(
            {"skill_id": None, "skill_projection_path": None}
        ),
    )

    with pytest.raises(ValueError, match="Agent Module requires one Skill"):
        _load(invalid_root)

    valid_root = _copy_reference(tmp_path, container="non_agent_without_skill")
    source_set = _load(valid_root)
    non_agent_sources = {
        source.module_id: source
        for source in source_set.module_sources
        if source.module_id in {"opaque_sink", "opaque_transform"}
    }

    assert set(non_agent_sources) == {"opaque_sink", "opaque_transform"}


def test_module_tests_are_evidence_outside_build_members(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    before = _build(root)
    tests_root = _manifest(root, "opaque_writer").parent / "tests"
    tests_root.mkdir()
    evidence = tests_root / "test_prompt_contract.md"
    evidence.write_text("First local test fixture.\n", encoding="utf-8")

    first = _build(root)
    evidence.write_text("Revised local test fixture.\n", encoding="utf-8")
    second = _build(root)

    assert first == before
    assert second == before


def test_non_agent_executable_binding_and_content_sensitivity(
    tmp_path: Path,
) -> None:
    invalid_root = _copy_reference(tmp_path, container="invalid_binding")
    _rewrite_json(
        _manifest(invalid_root, "opaque_transform"),
        lambda payload: payload.__setitem__(
            "executable_ref", "service:opaque_transform@v1"
        ),
    )

    with pytest.raises(ValueError, match="executable_ref does not match"):
        _load(invalid_root)

    sensitivity_root = _copy_reference(tmp_path, container="content_sensitivity")
    before = _build(sensitivity_root)
    executable = _manifest(sensitivity_root, "opaque_transform").parent / "executable.py"
    executable.write_text(
        "def transform(value):\n    return {'opaque': value, 'revision': 2}\n",
        encoding="utf-8",
    )
    after = _build(sensitivity_root)
    before_map = dict(before.release_identities())
    after_map = dict(after.release_identities())
    changed = {
        ref
        for ref in set(before_map) | set(after_map)
        if before_map.get(ref) != after_map.get(ref)
    }

    assert changed == {
        "execution-variant-policy:opaque_review_default@v1",
        "runtime-module:opaque_transform@v1",
        "runtime-workflow:opaque_review@v1",
    }


@pytest.mark.parametrize(
    ("target", "replacement", "message"),
    (
        (
            "examples/runtime_authoring_reference/skills/opaque_writer/SKILL.md",
            "# Opaque Writer\n\nNo registered Module.\n",
            "Skill projection does not declare module_id",
        ),
        (
            "examples/runtime_authoring_reference/contracts/opaque_review.md",
            "# Opaque Review Contract\n\nNo registered objects.\n",
            "owner contract does not declare module_id",
        ),
    ),
)
def test_module_identity_must_close_in_skill_and_owner(
    tmp_path: Path,
    target: str,
    replacement: str,
    message: str,
) -> None:
    root = _copy_reference(tmp_path)
    (root / target).write_text(replacement, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        _load(root)


def test_schema_path_cannot_leave_module_schemas(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    _rewrite_json(
        _manifest(root, "opaque_writer"),
        lambda payload: payload.__setitem__(
            "input_schema_path", "../opaque_writer_input.schema.json"
        ),
    )

    with pytest.raises(ValueError, match="stay inside the authoring root"):
        _load(root)


def test_unselected_broken_module_is_not_read(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    selected = _manifest(root, "opaque_writer").parent
    unselected = selected.parent / "unselected_broken"
    shutil.copytree(selected, unselected)
    (unselected / "prompt.md").unlink()
    (unselected / "ambient.md").write_text("Broken.\n", encoding="utf-8")

    result = _build(root)

    assert "runtime-module:opaque_writer@v1" in dict(result.release_identities())


def test_selected_symlink_is_refused(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    prompt = _manifest(root, "opaque_writer").parent / "prompt.md"
    external = root / "external_prompt.md"
    external.write_text("External.\n", encoding="utf-8")
    prompt.unlink()
    prompt.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        _load(root)


def test_human_task_is_rejected_by_inventory_v1(tmp_path: Path) -> None:
    root = _copy_reference(tmp_path)
    manifest = _manifest(root, "opaque_transform")
    _rewrite_json(
        manifest,
        lambda payload: payload.__setitem__("module_kind", "human_task"),
    )

    with pytest.raises(ValueError, match="admits deterministic and external_service"):
        _load(root)


def test_inventory_uses_explicit_selection_without_fixed_skill_root(
    tmp_path: Path,
) -> None:
    root = _copy_reference(tmp_path)
    assert not (root / ".claude").exists()
    assert not (root / ".agents").exists()

    source_set = _load(root)

    assert {source.module_id for source in source_set.module_sources} == {
        "opaque_sink",
        "opaque_transform",
        "opaque_writer",
    }


def test_runtime_execution_imports_no_authoring_client() -> None:
    execution_root = REPO_ROOT / "src" / "agent_runtime" / "execution"
    import_markers = (
        "registry_authoring_inventory_loading",
        "registry_authoring_release_building",
        "registry_authoring_release_submission",
    )

    offenders = []
    for path in sorted(execution_root.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if any(marker in text for marker in import_markers):
            offenders.append(path.name)
    assert offenders == []
