"""Generate the Reviewer API reference from source, without importing Runtime.

Run this tool from a source checkout after changing the selected public API
docstrings. The same Markdown is shipped in the wheel. --check is read-only
and exits nonzero for incomplete source or stale/missing generated documents.
The selection below defines documentation scope, not another API Registry.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path
import re
import sys
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_SOURCES = {
    "src/agent_runtime/registry/registry_module_authoring.py": (
        "Module", "ModuleReviewer", "ModuleExport", "ModuleAuthoringError",
    ),
    "src/agent_runtime/execution/execution_module_invocation.py": (
        "run_registered_workflow_module",
    ),
    "src/agent_runtime/registry/registry_local_persistence.py": (
        "LoadedRuntimeRegistration", "save_runtime_registration", "load_runtime_registration",
    ),
    "src/agent_runtime/registry/registry_release_registration.py": ("RuntimeReleaseBundle",),
    "src/agent_runtime/registry/registry_plugin_registration.py": ("register_runtime_module_plugin", "register_reviewer"),
    "src/agent_runtime/contracts/registry_release_definition.py": (
        "ModuleExecutionRequirements", "ModuleRelease", "ReviewerDefaults",
    ),
    "src/agent_runtime/registry/registry_module_loading.py": ("load_reviewer_registration",),
    "src/agent_runtime/registry/registry_workflow_authoring.py": ("Workflow",),
    "src/agent_runtime/execution/execution_local_invocation.py": (
        "prepare_local_workflow_module", "evaluate_local_workflow_module", "run_local_workflow_module",
    ),
    "src/agent_runtime/testing/execution_local_evaluation.py": (),
    "src/agent_runtime/foundation/foundation_environment_setup.py": ("setup_runtime",),
    "src/agent_runtime/ledger/ledger_execution_logging.py": ("read_execution_log",),
    "src/agent_runtime/invocation/invocation_cli_logging.py": ("parse_cli_log",),
    "src/agent_runtime/inspection/inspection_postgres_querying.py": ("PostgresWorkflowInspectionRepository",),
}
ERROR_CONSTANTS = (
    "EXECUTION_PROFILE_UNAVAILABLE",
    "MODULE_OPERATION_DECLARATION_INVALID",
    "MODULE_EXECUTION_PROFILE_INCOMPATIBLE",
)
OUTPUT_PATHS = (
    "docs/agent_runtime_reviewer_api.md",
    "src/agent_runtime/docs/agent_runtime_reviewer_api.md",
)


def _doc(node: ast.AST, qualified_name: str) -> str:
    value = ast.get_docstring(node)
    if not value:
        raise ValueError(f"missing public API docstring: {qualified_name}")
    # Present Google-style source sections as Markdown, without duplicating
    # their prose or inferring parameter/error semantics.
    result: list[str] = []
    section = ""
    for line in value.splitlines():
        if line in {"Args:", "Returns:", "Raises:", "Effects:"}:
            section = line[:-1]
            result.extend([f"**{section}**", ""])
        elif section in {"Args", "Raises"} and (match := re.fullmatch(r"    ([\w.]+): (.*)", line)):
            result.append(f"- `{match[1]}`: {match[2]}")
        elif section in {"Args", "Raises"} and line.startswith("        "):
            result.append("  " + line[8:])
        elif section in {"Returns", "Effects"} and line.startswith("    "):
            result.append(line[4:])
        else:
            result.append(line)
    return "\n".join(result)


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults = [None] * (len(positional) - len(args.defaults)) + list(args.defaults)
    parameters = []
    for index, (arg, default) in enumerate(zip(positional, defaults)):
        parameters.append(ast.unparse(arg) + ("=" + ast.unparse(default) if default is not None else ""))
        if args.posonlyargs and index + 1 == len(args.posonlyargs):
            parameters.append("/")
    if args.vararg:
        parameters.append("*" + ast.unparse(args.vararg))
    elif args.kwonlyargs:
        parameters.append("*")
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        parameters.append(ast.unparse(arg) + ("=" + ast.unparse(default) if default is not None else ""))
    if args.kwarg:
        parameters.append("**" + ast.unparse(args.kwarg))
    lines = ["@" + ast.unparse(value) for value in node.decorator_list]
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    lines.append(f"{prefix}def {node.name}(")
    lines.extend("    " + value + "," for value in parameters)
    returns = " -> " + ast.unparse(node.returns) if node.returns is not None else ""
    lines.append(")" + returns + ":")
    return "\n".join(lines)


def _section(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
             definitions: dict[str, ast.AST] | None = None) -> list[str]:
    name = node.name
    module = "agent_runtime.inspection" if name == "PostgresWorkflowInspectionRepository" else "agent_runtime"
    lines = [f'## {name}', "", f"Public import: `from {module} import {name}`", ""]
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        decorators = ["@" + ast.unparse(value) for value in node.decorator_list]
        fields = [ast.unparse(item) for item in node.body if isinstance(item, ast.AnnAssign)]
        declaration = [*decorators, f"class {name}({bases}):" if bases else f"class {name}:"]
        declaration.extend("    " + field for field in (fields or ["..."]))
        lines.extend(["```python", *declaration, "```", "", _doc(node, name), ""])
        for method in node.body:
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and not method.name.startswith("_"):
                qualified = f"{name}.{method.name}"
                lines.extend([f"### {qualified}", "", "```python", _signature(method),
                              "```", "", _doc(method, qualified), ""])
        # Preserve working method links while documenting actual inheritance,
        # not a second implementation or a copied signature.
        own_methods = {method.name for method in node.body
                       if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for base in node.bases:
            parent = (definitions or {}).get(base.id) if isinstance(base, ast.Name) else None
            if isinstance(parent, ast.ClassDef):
                for method in parent.body:
                    if (isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not method.name.startswith("_") and method.name not in own_methods):
                        qualified = f"{parent.name}.{method.name}"
                        lines.extend([f"### {name}.{method.name}", "",
                                      f"Inherited from [{qualified}](#{qualified.lower().replace('.', '')}).", ""])
    else:
        lines.extend(["```python", _signature(node), "```", "", _doc(node, name), ""])
    return lines


def _cli_sections(tree: ast.Module, standalone: str | None = None) -> list[str]:
    """Read literal argument/help declarations from the real CLI parser AST."""
    parser = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_parser")
    commands: dict[str, tuple[str, list[tuple[str, str, str]]]] = {}
    if standalone is not None:
        commands["parser"] = (standalone, [])
    for statement in parser.body:
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "add_parser":
                commands[statement.targets[0].id] = (ast.literal_eval(call.args[0]), [])
        elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if (isinstance(call.func, ast.Attribute) and call.func.attr == "add_argument"
                    and isinstance(call.func.value, ast.Name) and call.func.value.id in commands):
                options = {item.arg: item.value for item in call.keywords}
                required = ast.literal_eval(options["required"]) if "required" in options else False
                help_text = ast.literal_eval(options["help"]) if "help" in options else ""
                commands[call.func.value.id][1].append((", ".join(ast.literal_eval(arg) for arg in call.args),
                                                       "required" if required else "optional", help_text))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    result = ["## Evaluation CLI" if standalone else "## CLI commands", "", "Generated from the installed parser's argument declarations.", "",
              _doc(main, "CLI main"), ""]
    for command, arguments in commands.values():
        title = command if standalone else "agent-runtime-registry " + command
        result.extend([f"### {title}", "", "| Argument | Required | Help |", "| --- | --- | --- |"])
        result.extend(f"| `{flag}` | {required} | {help_text} |" for flag, required, help_text in arguments)
        result.append("")
    return result


def render_api_reference(project_root: Path = PROJECT_ROOT) -> bytes:
    """Read selected sources and return deterministic Markdown; perform no writes."""
    version = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    lines = [
        "# Reviewer API reference", "",
        "<!-- Generated by tools/build_agent_runtime_api_reference.py. Edit source docstrings, not this file. -->",
        "", f"Runtime package version: `{version}`.", "",
        "Scope: Module authoring/export, Reviewer source checks, local Runtime setup,",
        "versioned registration/loading and single-node evaluation.",
        "Other Runtime APIs are outside this reference. Signatures, fields, descriptions and error",
        "constant values below come directly from this source tree; no Runtime modules are executed.",
        "", "For registration steps, see the [Registration runbook](agent_runtime_registration_runbook.md).",
        "", "## Contents", "",
    ]
    symbols = [name for names in API_SOURCES.values() for name in names]
    lines.extend(f"- [{name}](#{name.lower()})" for name in symbols)
    lines.extend(["- [CLI commands](#cli-commands)", "- [Evaluation CLI](#evaluation-cli)", "- [Error constants](#error-constants)", ""])
    constants: dict[str, str] = {}
    for source, selected in API_SOURCES.items():
        tree = ast.parse((project_root / source).read_text(encoding="utf-8"), filename=source)
        definitions = {node.name: node for node in tree.body
                       if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in selected:
            if name not in definitions:
                raise ValueError(f"missing public API symbol: {source}:{name}")
            lines.extend(_section(definitions[name], definitions))
        if source.endswith("registry_local_persistence.py"):
            lines.extend(_cli_sections(tree))
        elif source.endswith("execution_local_evaluation.py"):
            lines.extend(_cli_sections(tree, standalone="agent-runtime-evaluate"))
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ERROR_CONSTANTS:
                        value = ast.literal_eval(node.value)
                        if not isinstance(value, str):
                            raise ValueError(f"error constant must be a string: {target.id}")
                        constants[target.id] = value
    lines.extend(["## Error constants", "", "```python"])
    for name in ERROR_CONSTANTS:
        if name not in constants:
            raise ValueError(f"missing public error constant: {name}")
        lines.append(f"{name} = {constants[name]!r}")
    lines.extend(["```", ""])
    return "\n".join(lines).encode("utf-8")


def build_api_reference(*, project_root: Path = PROJECT_ROOT, check: bool = False) -> tuple[Path, ...]:
    """Generate the two declared documents, or verify them without any writes."""
    project_root = project_root.absolute()
    content = render_api_reference(project_root)
    targets = tuple(project_root / relative for relative in OUTPUT_PATHS)
    # Reject symlinks before writing either artifact, including parent directories.
    for target in targets:
        current = target
        while current != project_root:
            if current.is_symlink():
                raise ValueError(f"generated document path is a symlink: {current}")
            current = current.parent
    if check:
        stale = [str(path.relative_to(project_root)) for path in targets
                 if not path.is_file() or path.read_bytes() != content]
        if stale:
            raise ValueError("generated Reviewer API reference is missing or stale: " + ", ".join(stale))
    else:
        for target in targets:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated documents without writing")
    args = parser.parse_args(argv)
    try:
        targets = build_api_reference(check=args.check)
    except (OSError, ValueError, SyntaxError, KeyError) as exc:
        print(f"Reviewer API documentation: {exc}", file=sys.stderr)
        return 1
    for target in targets:
        print(target.relative_to(PROJECT_ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
