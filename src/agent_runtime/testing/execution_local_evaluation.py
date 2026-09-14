"""Installed command for a bounded, non-persistent registered Workflow test."""
import argparse
import json
from pathlib import Path
import sys

from ..execution.execution_local_invocation import evaluate_local_workflow_module


def build_parser():
    """Declare the actual evaluation arguments used by help and generated docs."""
    parser = argparse.ArgumentParser(
        description="Evaluate one registered single-Module Workflow with its frozen requirements and an independent model. Supports empty or selected native tools. No PG or production authorization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Runtime ownership:\n"
                "  Registry: Module/ModuleReviewer requirements and Workflow versions.\n"
                "  Foundation: root setup and host resource locators.\n"
                "  Execution: exact Profile/Variant, Attempts, retries and technical completion.\n"
                "  Invocation: CLI assembly, native tools, declared commands and raw capture.\n"
                "  Ledger / Durability / Inspection: facts, recovery and read-only views.\n"
                "  Task owner: input meaning and business acceptance; no host-side Adapter.\n"
                f"Current Runtime Python (fixed default): {sys.executable}\n"
                "No Python selector or fallback. Read-only Python runtime support does not add tools.\n"
                "Full run profile and API fields: start at agent_runtime/README.md\n"
                "Portable review CLIs prepare/validate review objects; they are separate from Runtime CLIs."))
    parser.add_argument("--root", required=True, type=Path, help="Root containing .runtime definitions; not a model read root.")
    parser.add_argument("--workflow", required=True, help="Registered single-node Workflow ID.")
    parser.add_argument("--version", help="Exact version; omit for the latest registered new definition.")
    parser.add_argument("--input", required=True, type=Path, help="JSON input prepared under the Module's input schema.")
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


def main(argv=None):
    """Evaluate once and emit execution JSON on stdout without saving to PG.

    Exit 0: completed execution with schema-valid output, including a valid
    non_pass verdict. The subject owner applies its own semantic validator.
    Exit 1: input, execution or environment failure; stderr preserves the error
    type and available native error_code. Failed Attempts remain in stdout JSON.
    Exit 2: invalid arguments, including unsupported persistence options; no model
    is called. Exit 130: user interruption; captured Attempt logs remain in the
    returned execution JSON when invocation had started. No verdict is manufactured.
    Each invocation is a new temporary test. No cross-process recovery or stored
    history is promised. Explicit stdout capture belongs to the calling operator.
    Before evaluation, lightweight Runtime setup fills missing setup metadata
    and bundled operator Skills under the explicit root. Ready setup makes no
    writes; registered definitions and persistent execution stores are unchanged.
    """
    args = build_parser().parse_args(argv)
    try:
        from ..foundation.foundation_environment_setup import setup_runtime
        setup_runtime(args.root)
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        record = evaluate_local_workflow_module(args.root, args.workflow, input_payload=payload,
            version=args.version, transport_kind=args.transport, model_id=args.model,
            reasoning_profile=args.effort, cli_path=args.cli_path, **_resource_arguments(args.resources))
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0 if record["status"] == "completed" else 130 if record["status"] == "cancelled" else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "Evaluation interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
