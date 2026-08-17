from __future__ import annotations

from pathlib import Path
import shutil

from agent_runtime.conformance import conformance_architecture_validation as conformance
from agent_runtime.conformance.conformance_architecture_manifest import (
    RUNTIME_DEPENDENCY_DEBT_HIGH_WATER,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _copy_runtime_source(tmp_path: Path) -> Path:
    shutil.copytree(REPO_ROOT / "src", tmp_path / "src")
    shutil.copytree(REPO_ROOT / "designDoc", tmp_path / "designDoc")
    return tmp_path


def test_current_runtime_architecture_passes_full_conformance() -> None:
    assert conformance.validate_runtime_architecture(REPO_ROOT) == ()


def test_dependency_debt_is_exact_and_reviewable() -> None:
    current_forbidden = {
        (observation.source_path, observation.imported_module)
        for observation in conformance.collect_runtime_imports(REPO_ROOT)
        if observation.target_owner_id
        not in (
            observation.source_owner_id,
            *conformance.RUNTIME_ALLOWED_DEPENDENCY_TARGETS[
                observation.source_owner_id
            ],
        )
    }

    assert current_forbidden == RUNTIME_DEPENDENCY_DEBT_HIGH_WATER


def test_new_forbidden_dependency_fails_conformance(tmp_path: Path) -> None:
    project_root = _copy_runtime_source(tmp_path)
    target = (
        project_root
        / "src/agent_runtime/registry/registry_release_retrieval.py"
    )
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nfrom agent_runtime.inspection import build_runtime_execution_inspection\n",
        encoding="utf-8",
    )

    errors = conformance.validate_runtime_architecture(project_root)

    assert any("new forbidden Runtime dependency" in error for error in errors)


def test_foundation_cannot_import_a_runtime_responsibility(tmp_path: Path) -> None:
    project_root = _copy_runtime_source(tmp_path)
    target = (
        project_root
        / "src/agent_runtime/foundation/foundation_contract_validation.py"
    )
    target.write_text(
        target.read_text(encoding="utf-8")
        + "\nfrom agent_runtime.registry import RuntimeReleaseRegistry\n",
        encoding="utf-8",
    )

    errors = conformance.validate_runtime_architecture(project_root)

    assert any("new forbidden Runtime dependency" in error for error in errors)


def test_public_surface_addition_fails_conformance(tmp_path: Path) -> None:
    project_root = _copy_runtime_source(tmp_path)
    target = project_root / "src/agent_runtime/foundation/__init__.py"
    source = target.read_text(encoding="utf-8")
    target.write_text(
        source.replace(
            '    "validate_usd_amount",\n]',
            '    "validate_usd_amount",\n    "unreviewed_export",\n]',
        ),
        encoding="utf-8",
    )

    errors = conformance.validate_runtime_architecture(project_root)

    assert (
        "agent_runtime.foundation: public surface differs from manifest"
        in errors
    )


def test_removed_dependency_debt_is_accepted(tmp_path: Path) -> None:
    project_root = _copy_runtime_source(tmp_path)
    target = (
        project_root
        / "src/agent_runtime/invocation/invocation_prompt_assembly.py"
    )
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "from ..contracts.execution_module_definition import ModuleInputBinding\n",
            "",
        ),
        encoding="utf-8",
    )

    errors = conformance.validate_runtime_architecture(project_root)

    assert not any("dependency debt" in error for error in errors)
    assert not any("new forbidden Runtime dependency" in error for error in errors)


def test_new_migration_debt_path_fails_conformance(monkeypatch) -> None:
    monkeypatch.setattr(
        conformance,
        "RUNTIME_MIGRATION_DEBT_PATHS",
        (*conformance.RUNTIME_MIGRATION_DEBT_PATHS, "src/agent_runtime/new_debt.py"),
    )

    errors = conformance._validate_migration_debt()

    assert errors == (
        "new Runtime migration debt path: src/agent_runtime/new_debt.py",
        "Runtime migration debt exceeds its high-water mark",
    )
