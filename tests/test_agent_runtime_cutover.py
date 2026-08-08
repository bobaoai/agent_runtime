from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.testing.registry_migration_validation import inspect_python_sources, python_files_under


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_cutover_checker_accepts_one_hash_complete_target_definition(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path,
        "records.py",
        """
class ModuleRunRecord:
    module_run_id: str
    module_release_ref: str
    module_release_sha256: str
""",
    )

    assert inspect_python_sources((source,)) == ()


def test_cutover_checker_rejects_removed_symbol_and_field(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "removed.py",
        """
class StepRunRecord:
    step_run_id: str
""",
    )

    violations = inspect_python_sources((source,))

    assert {(row.code, row.detail) for row in violations} == {
        ("forbidden_symbol", "StepRunRecord"),
        ("forbidden_field", "step_run_id"),
    }


def test_cutover_checker_rejects_removed_plugin_and_provider_surfaces(
    tmp_path: Path,
) -> None:
    source = _write(
        tmp_path,
        "removed_sdk.py",
        """
class DomainRuntimePlugin:
    pass

class WorkflowRuntimeRegistry:
    pass

class LegacyAgentExecutionRequest:
    pass
""",
    )

    violations = inspect_python_sources((source,))

    assert {row.detail for row in violations} == {
        "DomainRuntimePlugin",
        "LegacyAgentExecutionRequest",
        "WorkflowRuntimeRegistry",
    }


@pytest.mark.parametrize(
    "legacy_symbol",
    (
        "LegacyModuleRunRecord",
        "LegacyModuleExecutionVariantRecord",
        "LegacyAttemptRecord",
        "LegacyRuntimeArtifactRecord",
        "LegacyExecutionEntitlementSnapshot",
        "LegacyModuleCapabilityGrant",
        "LegacyRuntimeRecordBatch",
        "LegacyAttemptBeginBatch",
        "LegacyOperationGrantBatch",
        "LegacyOperationGrantReceipt",
        "WorkflowAttemptRecord",
        "WorkflowAttemptStartedRecord",
        "WorkflowModuleExecutionVariantRecord",
        "WorkflowModuleRunRecord",
    ),
)
def test_cutover_checker_rejects_renamed_predecessor_classes(
    tmp_path: Path,
    legacy_symbol: str,
) -> None:
    source = _write(
        tmp_path,
        "renamed_predecessor.py",
        f"class {legacy_symbol}:\n    pass\n",
    )

    violations = inspect_python_sources((source,))

    assert any(
        row.code == "forbidden_symbol" and row.detail == legacy_symbol
        for row in violations
    )


def test_cutover_checker_rejects_import_alias_assignment_alias_and_export(
    tmp_path: Path,
) -> None:
    imported = _write(
        tmp_path,
        "imports.py",
        "from old_runtime import StepRunRecord as RenamedRun\n",
    )
    assigned = _write(
        tmp_path,
        "aliases.py",
        "WorkflowRuntimeRegistry = object\n",
    )
    exported = _write(
        tmp_path,
        "exports.py",
        '__all__ = ["LegacyAgentExecutionRequest"]\n',
    )

    violations = inspect_python_sources((imported, assigned, exported))

    assert {(row.code, row.detail) for row in violations} == {
        ("forbidden_import", "StepRunRecord"),
        ("forbidden_alias", "WorkflowRuntimeRegistry"),
        ("forbidden_export", "LegacyAgentExecutionRequest"),
    }


@pytest.mark.parametrize(
    ("target_name", "body", "missing_field"),
    (
        (
            "ModuleExecutionVariantRecord",
            "module_run_id: str\n    variant_id: str\n    execution_profile_ref: str",
            "execution_profile_sha256",
        ),
        (
            "ModuleDispatchRequest",
            "workflow_execution_id: str\n    dispatch_id: str\n    module_release_ref: str",
            "module_release_sha256",
        ),
        (
            "ModuleOutputResolutionRecord",
            "module_output_resolution_id: str",
            "source_module_run_id",
        ),
    ),
)
def test_cutover_checker_rejects_each_incomplete_target_contract(
    tmp_path: Path,
    target_name: str,
    body: str,
    missing_field: str,
) -> None:
    source = _write(
        tmp_path,
        "incomplete_target.py",
        f"class {target_name}:\n    {body}\n",
    )

    violations = inspect_python_sources((source,))

    assert len(violations) == 1
    assert violations[0].code == "missing_target_fields"
    assert missing_field in violations[0].detail


def test_cutover_checker_rejects_renamed_but_incomplete_target(tmp_path: Path) -> None:
    source = _write(
        tmp_path,
        "incomplete.py",
        """
class ModuleRunRecord:
    module_run_id: str
    module_id: str
""",
    )

    violations = inspect_python_sources((source,))

    assert len(violations) == 1
    assert violations[0].code == "missing_target_fields"
    assert "module_release_ref" in violations[0].detail
    assert "module_release_sha256" in violations[0].detail


def test_cutover_checker_rejects_duplicate_target_record_owners(
    tmp_path: Path,
) -> None:
    body = """
class ModuleRunRecord:
    module_run_id: str
    module_release_ref: str
    module_release_sha256: str
"""
    first = _write(tmp_path, "first.py", body)
    second = _write(tmp_path, "second.py", body)

    violations = inspect_python_sources((first, second))

    assert len(violations) == 2
    assert {row.code for row in violations} == {"duplicate_target_record"}


def test_cutover_source_membership_is_recursive_sorted_and_python_only(
    tmp_path: Path,
) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    second = _write(nested, "b.py", "value = 2\n")
    first = _write(tmp_path, "a.py", "value = 1\n")
    _write(tmp_path, "ignored.txt", "value = 0\n")

    assert python_files_under(tmp_path) == tuple(sorted((first, second)))


def test_runtime_source_has_one_complete_owner_for_each_target_record() -> None:
    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    violations = inspect_python_sources(python_files_under(runtime_root))

    structural = tuple(
        row
        for row in violations
        if row.code in {"duplicate_target_record", "missing_target_fields"}
    )
    assert structural == ()
