"""Validate Runtime source ownership, dependencies, exports, and debt bounds."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .conformance_architecture_manifest import (
    RUNTIME_ALLOWED_DEPENDENCY_TARGETS,
    RUNTIME_DEPENDENCY_DEBT_HIGH_WATER,
    RUNTIME_MIGRATION_DEBT_HIGH_WATER,
    RUNTIME_MIGRATION_DEBT_OWNERS,
    RUNTIME_PUBLIC_SURFACE_MANIFEST,
    RUNTIME_STRUCTURAL_MODULE_OWNERS,
)
from ..registry.registry_architecture_registration import (
    RUNTIME_MIGRATION_DEBT_PATHS,
    RUNTIME_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_STRUCTURAL_SOURCE_PATHS,
    RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS,
    validate_registry_architecture_registration,
)


@dataclass(frozen=True, order=True)
class RuntimeImportObservation:
    """One Runtime-owned import edge observed in a source checkout."""

    source_path: str
    source_owner_id: str
    imported_module: str
    target_owner_id: str


def _module_name_for_source_path(source_path: str) -> str:
    relative = Path(source_path).relative_to("src").with_suffix("")
    if relative.name == "__init__":
        return ".".join(relative.parts[:-1])
    return ".".join(relative.parts)


def _package_name_for_source_path(source_path: str) -> str:
    relative = Path(source_path).relative_to("src").with_suffix("")
    return ".".join(relative.parts[:-1])


def _structural_source_path(module_name: str) -> str:
    if module_name == "agent_runtime":
        return "src/agent_runtime/__init__.py"
    return f"src/{module_name.replace('.', '/')}/__init__.py"


def _source_owner_map() -> dict[str, str]:
    owners = {
        row.source_path: row.logical_responsibility_id
        for row in RUNTIME_SOURCE_FILE_REGISTRATIONS
    }
    owners.update(
        {
            row.source_path: row.supporting_plane_id
            for row in RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS
        }
    )
    owners.update(RUNTIME_MIGRATION_DEBT_OWNERS)
    owners.update(
        {
            _structural_source_path(module_name): owner_id
            for module_name, owner_id in RUNTIME_STRUCTURAL_MODULE_OWNERS.items()
        }
    )
    return owners


def _module_owner_map() -> dict[str, str]:
    return {
        _module_name_for_source_path(source_path): owner_id
        for source_path, owner_id in _source_owner_map().items()
    }


def _is_type_checking_guard(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Name)
        and node.id == "TYPE_CHECKING"
    ) or (
        isinstance(node, ast.Attribute)
        and node.attr == "TYPE_CHECKING"
    )


def _collect_import_nodes(
    tree: ast.AST,
) -> tuple[ast.Import | ast.ImportFrom, ...]:
    imports: list[ast.Import | ast.ImportFrom] = []

    def collect(node: ast.AST) -> None:
        if isinstance(node, ast.If) and _is_type_checking_guard(node.test):
            for child in node.orelse:
                collect(child)
            return
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.append(node)
            return
        for child in ast.iter_child_nodes(node):
            collect(child)

    collect(tree)
    return tuple(imports)


def _resolve_imported_modules(
    source_path: str,
    node: ast.Import | ast.ImportFrom,
) -> tuple[str, ...]:
    if isinstance(node, ast.Import):
        return tuple(alias.name for alias in node.names)
    if node.level == 0:
        return (node.module or "",)

    package_name = _package_name_for_source_path(source_path)
    package_parts = package_name.split(".")
    parent_hops = node.level - 1
    if parent_hops >= len(package_parts):
        return ("",)
    base = ".".join(package_parts[: len(package_parts) - parent_hops])
    if node.module:
        return (f"{base}.{node.module}",)
    return tuple(f"{base}.{alias.name}" for alias in node.names)


def _resolve_target_owner(
    imported_module: str,
    module_owners: dict[str, str],
) -> str | None:
    if imported_module in module_owners:
        return module_owners[imported_module]
    candidates = (
        (module_name, owner_id)
        for module_name, owner_id in module_owners.items()
        if module_name != "agent_runtime"
        if imported_module.startswith(module_name + ".")
    )
    try:
        return max(candidates, key=lambda item: len(item[0]))[1]
    except ValueError:
        return None


def collect_runtime_imports(project_root: Path) -> tuple[RuntimeImportObservation, ...]:
    """Collect classified Runtime-to-Runtime import edges outside type hints."""

    project_root = project_root.resolve()
    source_owners = _source_owner_map()
    module_owners = _module_owner_map()
    observations: set[RuntimeImportObservation] = set()
    for source_path, source_owner_id in sorted(source_owners.items()):
        path = project_root / source_path
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=source_path)
        for node in _collect_import_nodes(tree):
            for imported_module in _resolve_imported_modules(source_path, node):
                if not imported_module.startswith("agent_runtime"):
                    continue
                target_owner_id = _resolve_target_owner(
                    imported_module,
                    module_owners,
                )
                if target_owner_id is None:
                    target_owner_id = "unclassified"
                observations.add(
                    RuntimeImportObservation(
                        source_path=source_path,
                        source_owner_id=source_owner_id,
                        imported_module=imported_module,
                        target_owner_id=target_owner_id,
                    )
                )
    return tuple(sorted(observations))


def _validate_allowed_graph() -> tuple[str, ...]:
    errors: list[str] = []
    expected_owner_ids = {
        "foundation",
        "registry",
        "invocation",
        "durability",
        "ledger",
        "execution",
        "inspection",
        "conformance",
        "public_facade",
        "compatibility_facade",
    }
    if set(RUNTIME_ALLOWED_DEPENDENCY_TARGETS) != expected_owner_ids:
        errors.append("allowed dependency manifest has incomplete owner closure")
    for owner_id, target_ids in RUNTIME_ALLOWED_DEPENDENCY_TARGETS.items():
        unknown = set(target_ids) - expected_owner_ids
        if unknown:
            errors.append(
                f"{owner_id}: unknown allowed dependency targets: "
                + ", ".join(sorted(unknown))
            )
        if owner_id in target_ids:
            errors.append(f"{owner_id}: self dependency must remain implicit")

    product_graph_ids = expected_owner_ids - {
        "conformance",
        "public_facade",
        "compatibility_facade",
    }
    visit_state: dict[str, str] = {}

    def visit(owner_id: str, path: tuple[str, ...]) -> None:
        state = visit_state.get(owner_id)
        if state == "complete":
            return
        if state == "active":
            cycle_start = path.index(owner_id)
            errors.append(
                "allowed responsibility graph contains cycle: "
                + " -> ".join((*path[cycle_start:], owner_id))
            )
            return
        visit_state[owner_id] = "active"
        for target_id in sorted(RUNTIME_ALLOWED_DEPENDENCY_TARGETS[owner_id]):
            if target_id in product_graph_ids:
                visit(target_id, (*path, owner_id))
        visit_state[owner_id] = "complete"

    for owner_id in sorted(product_graph_ids):
        visit(owner_id, ())
    return tuple(errors)


def _validate_dependencies(project_root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    current_debt: set[tuple[str, str]] = set()
    for observation in collect_runtime_imports(project_root):
        if observation.target_owner_id == "unclassified":
            errors.append(
                f"{observation.source_path}: unclassified Runtime import target "
                f"{observation.imported_module}"
            )
            continue
        allowed_targets = RUNTIME_ALLOWED_DEPENDENCY_TARGETS.get(
            observation.source_owner_id
        )
        if allowed_targets is None:
            errors.append(
                f"{observation.source_path}: unclassified source owner "
                f"{observation.source_owner_id}"
            )
            continue
        if (
            observation.target_owner_id == observation.source_owner_id
            or observation.target_owner_id in allowed_targets
        ):
            continue
        debt_id = (observation.source_path, observation.imported_module)
        current_debt.add(debt_id)
        if debt_id not in RUNTIME_DEPENDENCY_DEBT_HIGH_WATER:
            errors.append(
                f"new forbidden Runtime dependency: {observation.source_path} "
                f"[{observation.source_owner_id}] -> "
                f"{observation.imported_module} "
                f"[{observation.target_owner_id}]"
            )
    if len(current_debt) > len(RUNTIME_DEPENDENCY_DEBT_HIGH_WATER):
        errors.append("Runtime dependency debt exceeds its high-water mark")
    return tuple(errors)


def _read_literal_all(path: Path) -> tuple[str, ...] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if isinstance(target, ast.Name) and target.id == "__all__" and value:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
            if type(parsed) is not list or not all(type(item) is str for item in parsed):
                return None
            return tuple(parsed)
    return None


def _read_top_level_public_bindings(path: Path) -> tuple[str, ...]:
    """Return importable non-private names bound by one package initializer."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", maxsplit=1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return tuple(sorted(name for name in names if not name.startswith("_")))


def _validate_structural_closure() -> tuple[str, ...]:
    registered_paths = set(RUNTIME_STRUCTURAL_SOURCE_PATHS)
    owned_paths = {
        _structural_source_path(module_name)
        for module_name in RUNTIME_STRUCTURAL_MODULE_OWNERS
    }
    errors: list[str] = []
    for source_path in sorted(registered_paths - owned_paths):
        errors.append(f"structural Runtime source lacks an owner: {source_path}")
    for source_path in sorted(owned_paths - registered_paths):
        errors.append(
            f"structural Runtime owner lacks a registered source: {source_path}"
        )
    known_owner_ids = set(RUNTIME_ALLOWED_DEPENDENCY_TARGETS)
    for module_name, owner_id in sorted(RUNTIME_STRUCTURAL_MODULE_OWNERS.items()):
        if owner_id not in known_owner_ids:
            errors.append(
                f"structural Runtime module has an unknown owner: "
                f"{module_name} -> {owner_id}"
            )
    return tuple(errors)


def _validate_public_surfaces(project_root: Path) -> tuple[str, ...]:
    errors: list[str] = []
    if set(RUNTIME_PUBLIC_SURFACE_MANIFEST) != set(RUNTIME_STRUCTURAL_MODULE_OWNERS):
        errors.append("public surface manifest differs from structural module closure")
    for module_name, expected_symbols in RUNTIME_PUBLIC_SURFACE_MANIFEST.items():
        source_path = _structural_source_path(module_name)
        actual_symbols = _read_literal_all(project_root / source_path)
        if actual_symbols is None:
            errors.append(f"{module_name}: __all__ must be one literal string list")
            continue
        if actual_symbols != expected_symbols:
            errors.append(f"{module_name}: public surface differs from manifest")
        if len(actual_symbols) != len(set(actual_symbols)):
            errors.append(f"{module_name}: public surface contains duplicates")
        actual_bindings = _read_top_level_public_bindings(
            project_root / source_path
        )
        if set(actual_bindings) != set(expected_symbols):
            errors.append(
                f"{module_name}: importable package names differ from public surface"
            )
    return tuple(errors)


def _validate_migration_debt() -> tuple[str, ...]:
    current = frozenset(RUNTIME_MIGRATION_DEBT_PATHS)
    errors: list[str] = []
    for source_path in sorted(current - RUNTIME_MIGRATION_DEBT_HIGH_WATER):
        errors.append(f"new Runtime migration debt path: {source_path}")
    if len(current) > len(RUNTIME_MIGRATION_DEBT_HIGH_WATER):
        errors.append("Runtime migration debt exceeds its high-water mark")
    return tuple(errors)


def validate_runtime_architecture(project_root: Path) -> tuple[str, ...]:
    """Return every deterministic Runtime architecture violation."""

    project_root = project_root.resolve()
    errors: list[str] = []
    errors.extend(validate_registry_architecture_registration(project_root))
    errors.extend(_validate_allowed_graph())
    errors.extend(_validate_structural_closure())
    errors.extend(_validate_dependencies(project_root))
    errors.extend(_validate_public_surfaces(project_root))
    errors.extend(_validate_migration_debt())
    return tuple(dict.fromkeys(errors))


__all__ = [
    "RuntimeImportObservation",
    "collect_runtime_imports",
    "validate_runtime_architecture",
]
