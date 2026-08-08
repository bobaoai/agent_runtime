"""Deterministic source checks for the Module-model hard cutover.

The checker is intentionally syntax-only: it can run against a source tree or
an unpacked wheel without importing provider, database, backend, host, or
domain dependencies.  Software Delivery decides whether a release is admitted;
this module only reports exact removal and target-shape violations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


FORBIDDEN_PUBLIC_SYMBOLS = frozenset(
    {
        "CellStepDispatchContext",
        "ComponentInputProjection",
        "ComponentInvocationRequest",
        "ComponentInvocationResult",
        "ComponentProjectionContract",
        "DomainRuntimePlugin",
        "LegacyAgentExecutionAdapter",
        "LegacyAgentExecutionHost",
        "LegacyAgentExecutionRequest",
        "LegacyAttemptBeginBatch",
        "LegacyAttemptRecord",
        "LegacyAuthorizationLedgerRecord",
        "LegacyCommittedOperationGrant",
        "LegacyExecutionEntitlementSnapshot",
        "LegacyModuleCapabilityGrant",
        "LegacyModuleExecutionVariantRecord",
        "LegacyModuleRunRecord",
        "LegacyOperationGrantBatch",
        "LegacyOperationGrantReceipt",
        "LegacyRuntimeArtifactRecord",
        "LegacyRuntimeRecordBatch",
        "StepDispatchAdmissionRecord",
        "StepDispatchRequest",
        "StepOutcome",
        "StepOutcomeDisposition",
        "StepOutputResolutionRecord",
        "StepRunCreationRequest",
        "StepRunCreationResult",
        "StepRunRecord",
        "StepVariantRecord",
        "WorkflowRuntimeRegistration",
        "WorkflowRuntimeRegistry",
        "WorkflowAttemptRecord",
        "WorkflowAttemptStartedRecord",
        "WorkflowModuleExecutionVariantRecord",
        "WorkflowModuleRunRecord",
    }
)

FORBIDDEN_PUBLIC_FIELDS = frozenset(
    {
        "component_id",
        "component_release_ref",
        "component_release_sha256",
        "source_step_run_id",
        "step_dispatch_admission_id",
        "step_output_resolution_id",
        "step_run_id",
    }
)

REQUIRED_TARGET_FIELDS = {
    "ModuleRunRecord": frozenset(
        {"module_run_id", "module_release_ref", "module_release_sha256"}
    ),
    "ModuleExecutionVariantRecord": frozenset(
        {
            "module_run_id",
            "variant_id",
            "execution_profile_ref",
            "execution_profile_sha256",
        }
    ),
    "ModuleDispatchRequest": frozenset(
        {
            "workflow_execution_id",
            "dispatch_id",
            "module_release_ref",
            "module_release_sha256",
        }
    ),
    "ModuleOutputResolutionRecord": frozenset(
        {"module_output_resolution_id", "source_module_run_id"}
    ),
}


@dataclass(frozen=True, order=True)
class CutoverViolation:
    """One deterministic hard-cutover violation in Python source."""

    path: str
    line: int
    code: str
    detail: str


def inspect_python_sources(paths: Iterable[Path]) -> tuple[CutoverViolation, ...]:
    """Inspect exact Python files for removed names and target record closure."""

    violations: list[CutoverViolation] = []
    target_definitions: dict[str, list[tuple[Path, ast.ClassDef]]] = {
        name: [] for name in REQUIRED_TARGET_FIELDS
    }
    for path in sorted({Path(item) for item in paths}, key=lambda item: str(item)):
        if path.suffix != ".py" or not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in FORBIDDEN_PUBLIC_SYMBOLS:
                    violations.append(
                        CutoverViolation(
                            str(path),
                            node.lineno,
                            "forbidden_symbol",
                            node.name,
                        )
                    )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for imported in node.names:
                    imported_name = imported.name.rsplit(".", 1)[-1]
                    if imported_name in FORBIDDEN_PUBLIC_SYMBOLS:
                        violations.append(
                            CutoverViolation(
                                str(path),
                                node.lineno,
                                "forbidden_import",
                                imported_name,
                            )
                        )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id in FORBIDDEN_PUBLIC_SYMBOLS
                    ):
                        violations.append(
                            CutoverViolation(
                                str(path),
                                node.lineno,
                                "forbidden_alias",
                                target.id,
                            )
                        )
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        exported_names = (
                            node.value.elts
                            if isinstance(node.value, (ast.List, ast.Tuple))
                            else ()
                        )
                        for exported in exported_names:
                            if (
                                isinstance(exported, ast.Constant)
                                and isinstance(exported.value, str)
                                and exported.value in FORBIDDEN_PUBLIC_SYMBOLS
                            ):
                                violations.append(
                                    CutoverViolation(
                                        str(path),
                                        exported.lineno,
                                        "forbidden_export",
                                        exported.value,
                                    )
                                )
            if isinstance(node, ast.ClassDef) and node.name in target_definitions:
                target_definitions[node.name].append((path, node))
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if node.target.id in FORBIDDEN_PUBLIC_SYMBOLS:
                    violations.append(
                        CutoverViolation(
                            str(path),
                            node.lineno,
                            "forbidden_alias",
                            node.target.id,
                        )
                    )
                if node.target.id in FORBIDDEN_PUBLIC_FIELDS:
                    violations.append(
                        CutoverViolation(
                            str(path),
                            node.lineno,
                            "forbidden_field",
                            node.target.id,
                        )
                    )
    for class_name, definitions in target_definitions.items():
        if len(definitions) > 1:
            for path, node in definitions:
                violations.append(
                    CutoverViolation(
                        str(path),
                        node.lineno,
                        "duplicate_target_record",
                        class_name,
                    )
                )
        for path, node in definitions:
            fields = {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign)
                and isinstance(child.target, ast.Name)
            }
            missing = REQUIRED_TARGET_FIELDS[class_name] - fields
            if missing:
                violations.append(
                    CutoverViolation(
                        str(path),
                        node.lineno,
                        "missing_target_fields",
                        f"{class_name}: {', '.join(sorted(missing))}",
                    )
                )
    return tuple(sorted(violations))


def python_files_under(*roots: Path) -> tuple[Path, ...]:
    """Return deterministic Python source membership for explicit roots."""

    files: set[Path] = set()
    for root in roots:
        exact = Path(root)
        if exact.is_file() and exact.suffix == ".py":
            files.add(exact)
        elif exact.is_dir():
            files.update(exact.rglob("*.py"))
    return tuple(sorted(files, key=lambda item: str(item)))


__all__ = [
    "CutoverViolation",
    "FORBIDDEN_PUBLIC_FIELDS",
    "FORBIDDEN_PUBLIC_SYMBOLS",
    "REQUIRED_TARGET_FIELDS",
    "inspect_python_sources",
    "python_files_under",
]
