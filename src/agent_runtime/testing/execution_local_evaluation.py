"""Argument and resource parsing shared by the installed evaluation CLI."""
import argparse
import json
from pathlib import Path
import sys



def build_parser(*, require_workflow=True, require_input=True):
    """Declare execution arguments; a composed test CLI may supply its own target.

    Defaults retain the registered single-node command. A composing caller that
    disables either required argument must validate its own mutually exclusive
    target before invoking an execution function. This builder performs no IO.
    """
    parser = argparse.ArgumentParser(
        description="Evaluate one registered single-Module Workflow with its frozen requirements and an independent model. Supports empty or selected native tools. No PG or production authorization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Runtime ownership:\n"
                "  Registry: Module/ModuleReviewer requirements and Workflow versions.\n"
                "  Foundation: root setup and host resource locators.\n"
                "  Execution: exact Profile/Variant, Attempts, retries and technical completion.\n"
                "  Invocation: CLI assembly, basic metadata/final result and raw capture.\n"
                "  Ledger: raw facts and original content from every Attempt.\n"
                "  Durability: recovery under the existing execution contract.\n"
                "  Inspection: private detailed views on request; no execution-state changes.\n"
                "  Explicit evaluation: execute, inspect and return facts for owner judgment.\n"
                "  Task owner: input meaning and business acceptance; no host-side Adapter.\n"
                f"Current Runtime Python (fixed default): {sys.executable}\n"
                "No Python selector or fallback. Read-only Python runtime support does not add tools.\n"
                "Full run profile and API fields: start at agent_runtime/README.md\n"
                "Portable review CLIs prepare/validate review objects; they are separate from Runtime CLIs."))
    parser.add_argument("--root", required=True, type=Path, help="Root containing .runtime definitions; not a model read root.")
    parser.add_argument("--workflow", required=require_workflow, help="Registered single-node Workflow ID.")
    parser.add_argument("--version", help="Exact version; omit for the latest registered new definition.")
    parser.add_argument("--input", required=require_input, type=Path, help="JSON input prepared under the selected Module or example input schema.")
    parser.add_argument("--transport", help="Independent transport: claude_cli (default), or codex_cli for tool-free inline Modules.")
    parser.add_argument("--model", help="Independent concrete model ID; required for codex_cli, otherwise omit for Runtime default.")
    parser.add_argument("--effort", help="Independent reasoning effort; required for codex_cli, otherwise omit for Runtime default.")
    parser.add_argument("--cli-path", type=Path, help="Installed executable; overrides root/.runtime/config.json provider_cli_paths, then PATH is used if unconfigured. Codex uses the host's standard file-based login.")
    parser.add_argument("--resources", type=Path, help="Optional resource JSON: material_root, material_files [{relative_path,sha256,executable}], read_only_dependencies, commands [{command_id,argv,cwd,timeout_seconds}]. Resource paths are relative to this file; command cwd is source/scratch-relative. python/python3 use current Runtime Python; other arguments are not interpolated. Empty dependencies clear additional host defaults, not the current Python runtime. No models, credentials or production grants here.")
    return parser


def _resource_arguments(path: Path | None) -> dict:
    if path is None:
        return {}
    path = path.resolve(strict=True)
    resource = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"material_root", "material_files", "read_only_dependencies", "commands"}
    if type(resource) is not dict or set(resource) - allowed:
        raise ValueError("Resource JSON accepts only material_root, material_files, read_only_dependencies and commands")
    def locator(value):
        if type(value) is not str or not value.strip() or "\x00" in value:
            raise ValueError("Resource locator must be a non-empty path")
        candidate = Path(value)
        return candidate if candidate.is_absolute() else path.parent / candidate
    result = {}
    if resource.get("material_root") is not None:
        result["material_root"] = locator(resource["material_root"])
    for field in ("material_files", "commands", "read_only_dependencies"):
        if field not in resource:
            continue
        value = resource[field]
        if field == "read_only_dependencies" and value is None:
            result[field] = None
        elif type(value) is not list:
            raise ValueError(f"{field} must be a JSON array")
        else:
            result[field] = tuple(locator(item) for item in value) if field == "read_only_dependencies" else tuple(value)
    return result
