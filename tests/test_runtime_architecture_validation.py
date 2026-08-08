from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

from agent_runtime.registry import registry_architecture_registration as architecture


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_architecture_registration_covers_every_python_source() -> None:
    assert architecture.validate_registry_architecture_registration(REPO_ROOT) == ()


def test_target_source_names_match_logical_owner_module() -> None:
    for source in architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS:
        assert Path(source.source_path).name == source.expected_file_name
        assert source.expected_file_name.startswith(f"{source.module_id}_")


def test_predecessor_workflow_registry_is_debt_not_target() -> None:
    predecessor = (
        "src/agent_runtime/registry/registry_workflow_registration.py"
    )
    assert predecessor in architecture.RUNTIME_MIGRATION_DEBT_PATHS
    assert predecessor not in {
        source.source_path
        for source in architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS
    }


def test_migration_debt_set_is_explicit_and_reviewable() -> None:
    assert architecture.RUNTIME_MIGRATION_DEBT_PATHS == (
        "src/agent_runtime/contracts/execution_operation_definition.py",
        "src/agent_runtime/contracts/registry_workflow_definition.py",
        "src/agent_runtime/execution/execution_event_ingestion.py",
        "src/agent_runtime/execution/execution_operation_authorization.py",
        "src/agent_runtime/registry/registry_workflow_registration.py",
    )


def test_unregistered_runtime_source_fails_validation(tmp_path: Path) -> None:
    shutil.copytree(
        REPO_ROOT / "src" / "agent_runtime",
        tmp_path / "src" / "agent_runtime",
    )
    unexpected = tmp_path / "src" / "agent_runtime" / "execution" / "execution_fake_creation.py"
    unexpected.write_text("VALUE = 1\n", encoding="utf-8")

    errors = architecture.validate_registry_architecture_registration(tmp_path)

    assert any(
        error == (
            "unregistered Runtime source file: "
            "src/agent_runtime/execution/execution_fake_creation.py"
        )
        for error in errors
    )


def test_misplaced_registered_source_fails_validation(monkeypatch) -> None:
    original = architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS
    misplaced = replace(original[0], source_directory_id="contracts")
    monkeypatch.setattr(
        architecture,
        "RUNTIME_SOURCE_FILE_REGISTRATIONS",
        (misplaced, *original[1:]),
    )

    errors = architecture.validate_registry_architecture_registration(REPO_ROOT)

    assert any("not contained by registered source directory" in error for error in errors)


def test_duplicate_detached_source_name_fails_validation(monkeypatch) -> None:
    original = architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS
    duplicate = replace(
        original[0],
        source_path=(
            "src/agent_runtime/contracts/"
            f"{original[0].expected_file_name}"
        ),
        source_directory_id="contracts",
    )
    monkeypatch.setattr(
        architecture,
        "RUNTIME_SOURCE_FILE_REGISTRATIONS",
        (*original, duplicate),
    )

    errors = architecture.validate_registry_architecture_registration(REPO_ROOT)

    assert any("duplicate detached Runtime source name" in error for error in errors)


def test_every_target_owner_contract_exists() -> None:
    for source in architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS:
        assert (REPO_ROOT / source.owner_contract_ref).is_file()
