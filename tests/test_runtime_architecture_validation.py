from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import shutil

from agent_runtime.registry import registry_architecture_registration as architecture
from agent_runtime.inspection.inspection_architecture_rendering import (
    build_runtime_architecture_projection,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_architecture_registration_covers_every_python_source() -> None:
    projection = build_runtime_architecture_projection()
    assert projection["schema_version"] == "agent_runtime_architecture_projection_v4"


def test_target_source_names_match_logical_owner_module() -> None:
    for source in architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS:
        assert Path(source.source_path).name == source.expected_file_name
        assert source.expected_file_name.startswith(
            f"{source.logical_responsibility_id}_"
        )


def test_supporting_source_names_match_supporting_plane() -> None:
    for source in architecture.RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS:
        assert Path(source.source_path).name == source.expected_file_name
        assert source.expected_file_name.startswith(
            f"{source.supporting_plane_id}_"
        )


def test_architecture_axes_are_registered_independently() -> None:
    responsibility_ids = tuple(
        row.responsibility_id
        for row in architecture.RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
    )
    directory_ids = {
        row.source_directory_id
        for row in architecture.RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
    }
    technology_ids = {
        row.technology_id
        for row in architecture.RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
    }

    assert responsibility_ids == architecture.RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS
    assert {"contracts", "testing"}.issubset(directory_ids)
    assert {"foundation", "conformance"}.issubset(directory_ids)
    assert {"postgresql", "temporal", "claude_agent_sdk", "codex_cli", "html"}.issubset(
        technology_ids
    )
    assert not set(responsibility_ids).intersection(technology_ids)
    assert {"provider", "postgres", "review"}.isdisjoint(responsibility_ids)


def test_every_implementation_source_has_one_matching_binding() -> None:
    source_bindings = {
        row.source_path: row.implementation_binding_id
        for row in architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS
        if row.implementation_binding_id is not None
    }

    for binding in architecture.RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS:
        for source_path in binding.implementation_source_paths:
            assert source_bindings[source_path] == binding.implementation_binding_id


def test_technology_cannot_be_added_as_a_logical_responsibility(
    monkeypatch,
) -> None:
    original = architecture.RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
    technology = architecture.RuntimeLogicalResponsibilityRegistration(
        responsibility_id="postgres",
        responsibility="Concrete database implementation.",
        owner_contract_ref=(
            "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
        ),
    )
    monkeypatch.setattr(
        architecture,
        "RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS",
        (*original, technology),
    )

    errors = architecture.validate_registry_architecture_registration(REPO_ROOT)

    assert any(
        "logical responsibilities must be exactly" in error for error in errors
    )


def test_cross_responsibility_implementation_binding_fails_validation(
    monkeypatch,
) -> None:
    original = architecture.RUNTIME_SOURCE_FILE_REGISTRATIONS
    target_index = next(
        index
        for index, source in enumerate(original)
        if source.implementation_binding_id == "durability_temporal_coordination"
    )
    malformed = replace(
        original[target_index],
        logical_responsibility_id="execution",
    )
    monkeypatch.setattr(
        architecture,
        "RUNTIME_SOURCE_FILE_REGISTRATIONS",
        (*original[:target_index], malformed, *original[target_index + 1 :]),
    )

    errors = architecture.validate_registry_architecture_registration(REPO_ROOT)

    assert any(
        "implementation binding crosses logical responsibility" in error
        for error in errors
    )


def test_responsibility_id_cannot_be_used_as_a_technology_id(
    monkeypatch,
) -> None:
    original = architecture.RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
    malformed = replace(original[0], technology_id="ledger")
    monkeypatch.setattr(
        architecture,
        "RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS",
        (malformed, *original[1:]),
    )

    errors = architecture.validate_runtime_architecture_registration()

    assert any(
        "technology id cannot be a logical responsibility" in error
        for error in errors
    )


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
    for source in architecture.RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS:
        assert (REPO_ROOT / source.owner_contract_ref).is_file()
