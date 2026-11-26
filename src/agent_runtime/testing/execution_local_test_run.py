"""Argument and resource parsing shared by the installed Test Run CLI and API."""
import argparse
import json
from pathlib import Path
import sys



def build_parser(*, require_workflow=True, require_input=True):
    """Declare Test Run arguments; a composed CLI may supply its own target check.

    Defaults retain the registered single-node command. A composing caller that
    disables either required argument must validate its own mutually exclusive
    target before invoking an execution function. This builder performs no IO.
    """
    parser = argparse.ArgumentParser(
        description="Test-run one registered single-Module Workflow with its frozen requirements and an independently selected model. The definition comes from root/.runtime or, read-only, from an exact PostgreSQL release. Supports empty or selected native tools. No production authorization; results are not persisted.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=("Runtime ownership:\n"
                "  Registry: Module/ModuleReviewer requirements and Workflow versions.\n"
                "  Foundation: root setup and host resource locators.\n"
                "  Execution: execution parameters, exact Profile/Variant, Attempts, retries and technical completion.\n"
                "  Invocation: CLI assembly, basic metadata/final result and raw capture.\n"
                "  Ledger: raw facts and original content from every Attempt.\n"
                "  Durability: recovery under the existing execution contract.\n"
                "  Inspection: private detailed views on request; no execution-state changes.\n"
                "  Test Run: execute, inspect and return facts for owner judgment.\n"
                "  Task owner: input meaning and business acceptance; no host-side Adapter.\n"
                "Execution parameters (--transport, --model, --effort, --run-timeout-seconds) are taken per parameter from this call,\n"
                "  then root/.runtime/execution_parameters/workflows/<workflow_id>.json, then\n"
                "  root/.runtime/execution_parameters/workspace.json, then the Runtime default.\n"
                "  Parameter files never choose a definition version.\n"
                f"Current Runtime Python (fixed default): {sys.executable}\n"
                "No Python selector or fallback. Read-only Python runtime support does not add tools.\n"
                "Full run profile and API fields: start at agent_runtime/README.md\n"
                "Portable review CLIs prepare/validate review objects; they are separate from Runtime CLIs."))
    parser.add_argument("--root", required=True, type=Path, help="Root containing .runtime setup, definitions and execution parameter files; not a model read root. Required for local and PostgreSQL definitions.")
    parser.add_argument("--selection", type=Path, help="Ordinary definition handoff: the selection file an agent-runtime-registry registration or select command wrote with --selection-out. Runs exactly its Workflow and Module refs and hashes from root/.runtime or its configured PostgreSQL release store; a later registered version never replaces it. Excludes --workflow, --version, --expected-module-id and the PostgreSQL arguments.")
    parser.add_argument("--workflow", required=require_workflow, help="Local definition: registered single-node Workflow ID under root/.runtime.")
    parser.add_argument("--version", help="Local definition only: exact version; omit for the latest registered new definition.")
    parser.add_argument("--workflow-ref", help="PostgreSQL definition: exact Workflow release_ref. Requires --workflow-sha256, --release-database-url-env and --release-schema; excludes --workflow and --version.")
    parser.add_argument("--workflow-sha256", help="PostgreSQL definition: exact Workflow release_sha256.")
    parser.add_argument("--release-database-url-env", help="PostgreSQL definition: name of the environment variable holding the DSN. The DSN itself is never an argument, input field or record value. The store is read only; nothing is registered or written.")
    parser.add_argument("--release-schema", help="PostgreSQL definition: existing release schema to read.")
    parser.add_argument("--expected-module-id", help="Optional exact Module ID the selected Workflow must run; checked before materials are captured or a model is called.")
    parser.add_argument("--input", required=require_input, type=Path, help="JSON input prepared under the selected Module or example input schema.")
    parser.add_argument("--transport", help="This call's transport: claude_cli, codex_cli or antigravity_cli (structured_output and file_review shapes only). Omit to use the parameter files or the claude_cli default.")
    parser.add_argument("--model", help="This call's concrete model ID. Omit to use the parameter files or the default; codex_cli and antigravity_cli have no default model.")
    parser.add_argument("--effort", help="This call's reasoning effort. Omit to use the parameter files or the default; codex_cli and antigravity_cli have no default effort.")
    parser.add_argument("--run-timeout-seconds", type=int,
        help="Total synchronous work budget (1..86400 seconds); four-layer default 1200. Managed children cannot extend the parent deadline.")
    parser.add_argument("--cli-path", type=Path, help="Installed native executable; overrides root/.runtime/config.json provider_cli_paths, then the native program on PATH is used if unconfigured. Each CLI uses the host's own login and configuration.")
    parser.add_argument("--resources", type=Path, help="Optional resource JSON: material_root, material_files [{relative_path,sha256,executable}], read_only_dependencies, commands [{command_id,argv,cwd,timeout_seconds}], normally written by write_local_resources from selected files. Resource paths are relative to this file; command cwd is source/scratch-relative. python/python3 use current Runtime Python; other arguments are not interpolated. Empty dependencies clear additional host defaults, not the current Python runtime. No models, credentials or production grants here. Excludes --material-root.")
    parser.add_argument("--material-root", type=Path, help="Folder holding this call's task materials. Select files in it with --material or the whole folder with --all-materials; Runtime computes each file's relative path, SHA-256 and executable flag from the actual bytes and freezes them for the run. Excludes --resources.")
    parser.add_argument("--material", action="append", default=None, dest="materials",
        help="Repeatable file under --material-root to give the model, as a path relative to it (or an absolute path inside it). Links, reparse points and paths leaving the folder are refused.")
    parser.add_argument("--all-materials", action="store_true", help="Select every ordinary file below --material-root. A link, reparse point or special file anywhere below it is refused rather than skipped.")
    parser.add_argument("--project-directory", type=Path, help="Caller-owned existing writable directory (checkout, worktree, task copy or output directory) for a Module whose workspace policy is explicit_project_read_write. Runtime never deletes it.")
    parser.add_argument("--codex-execution-mode", choices=("configured", "host"), default="configured",
        help="Per-call Codex configuration: configured preserves the default; host uses native host permissions for code_change with an explicit project directory. cwd is task scope, not an OS boundary. No material package, extra dependencies or command extension in host mode, and no MCP unless --native-project is also given.")
    parser.add_argument("--native-project", action="store_true",
        help="Run one independent CLI call inside --project-directory with the project's native context: Claude loads "
             "CLAUDE.md and its skills (Skill tool; PowerShell beside Bash; allow rules under dontAsk; auto-memory and "
             "hooks off), Codex loads AGENTS.md and .agents/skills and needs --codex-execution-mode host. Only declared "
             "MCP connections are passed; credentials stay with each CLI. Shell uses host permissions; the directory is "
             "task scope, not an OS boundary. Only code_change/mcp_data-shaped Modules; no materials, --resources "
             "commands or dependencies, --command-permit, or Antigravity. Each call is one fresh session that is never resumed (Claude --no-session-persistence, Codex --ephemeral).")
    parser.add_argument("--command-permit", action="append", default=[], dest="command_permits",
        help="Repeatable Shell command permit for this call. Claude: a Bash rule body, e.g. \"python -m pytest:*\"; Claude's own read-only commands still run; other commands are denied. Antigravity: an agy command rule body rendered as command(<permit>); the live-tested form is regex: plus Python re.escape of the complete command. Codex refuses per-call permits.")
    parser.add_argument("--record-out", type=Path, help="New file (its folder must exist) that receives the returned record, or the error document when no record was returned; an existing file is refused before setup or any model call. Read it later with agent-runtime-inspect --test-run-record.")
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
