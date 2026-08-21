from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
import inspect
from pathlib import Path
import re

import pytest

from agent_runtime.execution import (
    execution_event_ingestion,
    execution_operation_authorization as authorization,
)
from agent_runtime.contracts import (
    durability_backend_definition as durable_contracts,
    execution_operation_definition as authorization_contracts,
    registry_release_definition as module_contracts,
)
from agent_runtime.foundation.foundation_contract_validation import validate_snake_case_name
from agent_runtime.registry import (
    registry_plugin_registration as plugin_sdk,
    registry_release_registration as release_registry,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "src" / "agent_runtime"
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
PYTHON_FILE_NAME = re.compile(
    r"^(?:__init__|_?[a-z][a-z0-9]*(?:_[a-z0-9]+)*)$"
)
PYTHON_SYMBOL = re.compile(
    r"^(?:__[a-z][a-z0-9_]*__|_?[a-z][a-z0-9]*(?:_[a-z0-9]+)*)$"
)
TARGET_RECORD_MODULES = (
    module_contracts,
    authorization_contracts,
    durable_contracts,
    execution_event_ingestion,
    release_registry,
    plugin_sdk,
    authorization,
)
RUNTIME_DESIGN_DOCS = (
    "designDoc/the_agent_runtime.md",
    "designDoc/agent_runtime_00_execution_charter.md",
    "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    "designDoc/agent_runtime_03_authorized_external_event_ingress.md",
    "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    "designDoc/agent_runtime_09_authorization_integration_contract.md",
)


def _owned_dataclasses(module: object) -> tuple[type[object], ...]:
    return tuple(
        candidate
        for _, candidate in inspect.getmembers(module, inspect.isclass)
        if candidate.__module__ == module.__name__ and is_dataclass(candidate)
    )


def test_runtime_owned_files_functions_and_parameters_use_snake_case() -> None:
    failures: list[str] = []
    for path in sorted(RUNTIME_ROOT.rglob("*.py")):
        relative = path.relative_to(RUNTIME_ROOT)
        if any(part == "__pycache__" for part in relative.parts):
            continue
        if not PYTHON_FILE_NAME.fullmatch(path.stem):
            failures.append(f"file:{relative}")
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not PYTHON_SYMBOL.fullmatch(node.name):
                    failures.append(f"function:{relative}:{node.lineno}:{node.name}")
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                ):
                    if not PYTHON_SYMBOL.fullmatch(argument.arg):
                        failures.append(
                            f"parameter:{relative}:{argument.lineno}:{argument.arg}"
                        )
    assert not failures, "\n".join(failures)


def test_target_runtime_records_publish_explicit_snake_case_type_ids() -> None:
    failures: list[str] = []
    for module in TARGET_RECORD_MODULES:
        for record_class in _owned_dataclasses(module):
            record_type = getattr(record_class, "record_type", None)
            if not isinstance(record_type, str) or not SNAKE_CASE.fullmatch(
                record_type
            ):
                failures.append(
                    f"{record_class.__module__}.{record_class.__name__}:"
                    f"{record_type!r}"
                )
            for field in fields(record_class):
                if not SNAKE_CASE.fullmatch(field.name):
                    failures.append(
                        f"{record_class.__module__}.{record_class.__name__}."
                        f"{field.name}"
                    )
    assert not failures, "\n".join(failures)


def test_runtime_service_ids_are_snake_case() -> None:
    for service in (
        release_registry.RuntimeReleaseRegistry,
        authorization.InMemoryAuthorizationLedger,
        authorization.RuntimeAuthorizationCoordinator,
        execution_event_ingestion.InMemoryExternalEventIngress,
    ):
        assert SNAKE_CASE.fullmatch(service.service_id)


@pytest.mark.parametrize(
    "invalid_name",
    (
        "research.theme.writer",
        "research-theme-writer",
        "ResearchThemeWriter",
        "researchThemeWriter",
    ),
)
def test_canonical_runtime_names_reject_non_snake_case(
    invalid_name: str,
) -> None:
    with pytest.raises(ValueError, match="snake_case name required"):
        validate_snake_case_name("module_id", invalid_name)


def test_runtime_design_docs_use_snake_case_canonical_inline_names() -> None:
    allowed_python_projection_names = {
        "`AuthorizationAdapter`",
        "`PascalCase`",
        "`Z1`",
        "`Z2`",
        "`Z3`",
        "`Z4`",
        "`Z5`",
        "`Z6`",
        "`Z7`",
    }
    failures: list[str] = []
    pattern = re.compile(r"`[A-Z][A-Za-z0-9]+`")
    for relative in RUNTIME_DESIGN_DOCS:
        text = (REPO_ROOT / relative).read_text(encoding="utf-8")
        for match in pattern.findall(text):
            if match not in allowed_python_projection_names:
                failures.append(f"{relative}:{match}")
    assert not failures, "\n".join(failures)
