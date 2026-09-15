"""Installed evaluation CLI composing registered execution and capability examples.

The command name remains agent-runtime-evaluate. This explicit evaluation
combines execution with requested Inspection before temporary content closes.
Preparation and ordinary execution remain in Execution; examples do not add
business judgment to the execution kernel.
"""
import json
import sys
from pathlib import Path
import shutil
import tempfile
import uuid

from ..execution.execution_local_invocation import prepare_local_workflow_module, _run_prepared_workflow_node
from ..execution.execution_content_staging import InMemoryCellArtifactStore
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger

from .execution_local_evaluation import build_parser as _execution_parser, _resource_arguments
from .conformance_agent_execution import run_agent_example


def evaluate_local_workflow_module(
    root: Path, workflow_id: str, *, input_payload: dict, version: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, cli_path: Path | str | None = None,
    material_root: Path | None = None, material_files: tuple[dict, ...] = (),
    read_only_dependencies: tuple[Path, ...] | None = None, commands: tuple[dict, ...] = (),
    tool_session_factory=None, user_cancel_requested=None, resource_cancel_requested=None,
) -> dict:
    """Evaluate a registered single-node Workflow using temporary test resources.

    Args:
        root: Root containing .runtime definitions; never a model/tool read root.
        workflow_id: Workflow to load, including single-Module Workflows.
        input_payload: Exact JSON input validated against the registered schema.
        version: Exact version; None resolves the latest new definition once.
        transport_kind: Independent execution transport; None uses Runtime's
            default. claude_cli supports empty or selected native tool sets,
            inline input, and no write area or a private draft as defined.
            codex_cli supports tool_free, inline, empty tools, workspace none
            and denied tool network. It requires explicit model_id and effort;
            unsupported requirements are rejected, never reduced to fit.
        model_id: Independent concrete model ID; None uses the Claude default.
            Codex requires an explicit model. Claude verifies observed model
            identity, allowing its known CLI [1m] selector. Codex retains the
            requested identity and available Provider facts without inventing
            an unreported actual response model. Model names do not select transport.
        reasoning_profile: Independent effort; None uses Runtime's default.
            Required for codex_cli; no default is inferred from another Provider.
        cli_path: Explicit installed provider executable; otherwise use the
            selected transport's root/.runtime/config.json provider_cli_paths
            value, then host PATH when that value is absent. No login or
            installation is performed. Model selection does not come from config.
            Relative paths are resolved from the caller's working directory
            before entering the temporary Attempt directory.
            Codex uses file-based auth from the host's standard CODEX_HOME/auth.json
            (default ~/.codex/auth.json), never from task JSON or a fallback account.
        material_root: Explicit source directory for the frozen file list below,
            never implicitly the host root. Only listed ordinary files are read.
        material_files: Tuple of dictionaries with relative_path, sha256 and
            executable. Bytes and executable bits must match the supplied list;
            relative structure is preserved in the Attempt's read-only source
            copy. Paths cannot traverse symlinks or escape material_root.
            These auxiliary files are not silently appended as inline task text.
        read_only_dependencies: Explicit tuple of trusted dependency directories,
            including an empty tuple to clear host defaults. None uses the
            optional config's read_only_dependencies for a Module with tools.
            Unused defaults are not exposed to tool-free Modules. Explicit
            dependencies still require the selected Adapter's actual support.
            These are additional libraries. Shell-capable execution always uses
            the current Runtime Python environment as read-only runtime support;
            no Python selection parameter or fallback environment is provided.
        commands: Tuple of command_id, argv, cwd and timeout_seconds dictionaries.
            IDs are unique within this call. cwd selects source or scratch and
            their relative subdirectories. argv is fixed by the caller; timeout
            cannot exceed the Module budget. A relative executable path in
            argv[0] resolves against that cwd; a bare program name uses the
            command's fixed PATH. python/python3 use the current sys.executable, preserving its
            virtual-environment entry rather than replacing it with a symlink target.
            Claude's local command tool records actual process results for these IDs; ordinary native Bash remains
            available independently. Required/expected business outcomes belong
            to the task input and its validator, not these resource definitions.
        tool_session_factory: Optional trusted in-process factory with read-only
            definitions and open_session(request). Definitions are frozen into
            the prompt and live resources before opening the session. Only the
            exact non-native tool_policy set is accepted. No import locator or
            serialized factory is read from task data, config or resources JSON.
        user_cancel_requested: Optional trusted callback for real user/workflow
            cancellation, also passed to the underlying running CLI process.
        resource_cancel_requested: Optional trusted callback for parent resource
            closure. Stops the process as resource_closed, not user cancellation.
    Returns:
        JSON-compatible execution facts and output. provider_trace retains the
        observed response models and diagnostics. persistence is not_requested;
        execution_log contains every Attempt's full private provider trace,
        original encoded streams, per-call view and explicit completeness issues,
        assembled by read_execution_log before temporary resources are cleared.
        input_bindings and input_closure_sha256 identify the exact staged task
        and optional resource package; their temporary content refs are not
        cross-process recovery handles. Actual command and Provider observations
        remain separate facts, correlated only where the returned IDs prove it.
        Missing or unpaired events never become a successful empty tool list.
        execution_trace is the actual in-memory Run/Variant/Attempt result, not
        a durable Workflow Ledger. The subject owner still validates its verdict.
        Each call is a new test; there is no cross-process replay/history promise.
        Failed Attempts retain failure_detail.failure_code: model mismatch or
        missing model evidence uses claude_cli_model_identity_mismatch or
        claude_cli_model_identity_unavailable; closed resources use
        self_test_resources_unavailable. Executable/dependency faults retain
        ADAPTER_BINDING_UNAVAILABLE rather than pretending resources expired.
        Temporary-resource cleanup failure uses claude_cli_cleanup_failed while
        preserving the received trace. User cancellation uses claude_cli_interrupted
        and denies retry. Interrupted capture retains prior_stop_reason; a failure
        already received by the Adapter remains in provider_log.adapter_failure.
        Each execution_log Attempt includes its final private failure_detail.
        Codex uses codex_cli_interrupted/codex_cli_cleanup_failed for the same
        interruption/cleanup distinction. If CLI authentication replaces its
        temporary auth reference, cleanup fails and preserves the separate
        private state; its recovery locator is in the private failure detail.
        The caller must inspect that state before reuse. This is local temporary
        recovery, not durable credential backup or automatic writeback.
    Raises:
        FileNotFoundError: Missing registration, version or provider executable.
        ValueError: Unsupported graph/Profile, input or resource configuration.
        jsonschema.exceptions.ValidationError: Input violates its registered schema.
        PermissionError: The requested operation is outside bounded test resources.
        Exception: Existing provider/environment errors retain their contracts.
    Effects:
        Reads fixed definitions, stages input in memory, calls the admitted
        Adapter through the existing Workflow Module kernel, and returns facts.
        Does not access PostgreSQL, discover storage credentials, write .runtime,
        manufacture production authorization, or persist a request receipt.
        Its private temporary workspace is removed on exit. The caller may
        explicitly save the returned result; Runtime does not save it by default.
    """
    from ..invocation.invocation_claude_cli_execution import ClaudeAdapter
    from ..invocation.invocation_codex_module_invocation import CodexCliModuleExecutor
    from ..inspection.inspection_execution_logging import read_execution_log
    from ..foundation.foundation_environment_setup import load_runtime_config
    from ..invocation.invocation_local_resource_preparation import capture_local_resources, parse_local_resources

    saved, selection = prepare_local_workflow_module(root, workflow_id, version=version,
        transport_kind=transport_kind, model_id=model_id, reasoning_profile=reasoning_profile)
    workflow = saved.release
    node = workflow.nodes[0]
    module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
    binding = selection.policy_document()["bindings"][0]
    selected_profile = saved.registry.get_execution_profile(
        binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
    config = load_runtime_config(root)
    dependencies = read_only_dependencies
    if dependencies is None:
        dependencies = config["read_only_dependencies"] if selected_profile.tool_policy else ()
    if type(dependencies) is not tuple:
        raise ValueError("read_only_dependencies must be a tuple or None for host defaults")
    resource_body = capture_local_resources(profile=selected_profile, material_root=material_root,
        material_files=material_files, read_only_dependencies=dependencies, commands=commands)
    dependencies = (() if resource_body is None else
        tuple(Path(value) for value in parse_local_resources(resource_body)["read_only_dependencies"]))
    program = {"claude_cli": "claude", "codex_cli": "codex"}[selected_profile.transport_kind]
    executable = cli_path if cli_path is not None else config["provider_cli_paths"].get(selected_profile.transport_kind)
    if executable is None:
        executable = shutil.which(program)
    if executable is None:
        raise FileNotFoundError(f"{program} CLI executable is unavailable; provide cli_path or host PATH")
    executable = Path(executable).resolve(strict=True)
    artifacts = InMemoryCellArtifactStore()
    ledger = InMemoryModuleExecutionLedger()
    with tempfile.TemporaryDirectory(prefix="agent-runtime-self-test-") as directory:
        workspace = Path(directory).resolve()
        if selected_profile.transport_kind == "claude_cli":
            adapter = ClaudeAdapter(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, cli_path=executable, read_only_dependencies=dependencies,
                adapter_binding=(selected_profile.executor_adapter_id, selected_profile.executor_adapter_revision))
        else:
            adapter = CodexCliModuleExecutor(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, codex_bin=str(executable))
        result, record = _run_prepared_workflow_node(registry=saved.registry, workflow=workflow,
            selection=selection, input_payload=input_payload, idempotency_key="self_test_"+uuid.uuid4().hex,
            artifact_host=artifacts, ledger=ledger, workspace_root=workspace, adapter=adapter,
            local_resources=resource_body, tool_session_factory=tool_session_factory,
            user_cancel_requested=user_cancel_requested, resource_cancel_requested=resource_cancel_requested)
        record["execution_log"] = read_execution_log(
            result.module_run, attempts=result.attempts,
            read_content=artifacts.read_bytes, include_private_content=True,
        )
        return record


def _execute_registered(argv=None):
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
    args = _execution_parser().parse_args(argv)
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



def build_parser():
    """Compose the real execution argument grammar with the fixed example choices."""
    parser = _execution_parser(require_workflow=False, require_input=False)
    parser.description = "Evaluate a registered single-Module Workflow or a packaged multi-Agent example. Select exactly one of --workflow and --example. No PG or production authorization."
    parser.epilog += "\n\nPackaged examples register fixed definitions and run a fresh execution. Saved JSON is evidence, not cross-process resume. See docs/agent_runtime_capability_runbook.md for example use and result interpretation."
    parser.add_argument("--example", choices=("agent_capability_example", "agent_evaluation_example"),
        help="Register and run a packaged example. capability covers query/writer/parallel reviews; evaluation covers Agent-requested child review and independent evaluation. Definitions are saved under root; execution is temporary. --input is optional and accepts exactly task and required_facts; omission uses the packaged finite fixture.")
    parser.add_argument("--scenario", choices=("accepted", "revision", "wait"),
        help="Capability example only: first selector decision. revision returns to the Writer; wait records WAIT then supplies one matching material_ready fixture event in the same process. Default accepted; not cross-process resume.")
    return parser


def main(argv=None):
    """Run the selected path and print its actual execution records as JSON.

    Exactly one of --workflow and --example is required. --workflow also requires
    --input and delegates unchanged arguments to the existing execution command.
    --example owns fixed definitions and fixture resources, so --version and
    --resources are rejected. Optional --input contains task and required_facts.
    Exit 0 means technical completion, not business acceptance or full Runtime
    conformance. Exit 1 means input/environment/execution/cleanup failure or an
    unfinished graph; captured facts remain in returned JSON when available.
    Exit 2 means invalid command arguments before setup or model execution.
    Exit 130 means user cancellation, retaining captured records when started.
    Examples register their exact source and create temporary execution resources;
    the normal --workflow path retains its existing setup and no-registration effects.

    For --example, a caught graph error is printed in the execution JSON on stdout:
    status=failed, stop_reason=execution_error, failure={error_type, detail}. Errors
    escaping setup/execution/cleanup are printed on stderr as error_type,
    error_code (null when absent), and detail. These are different output shapes.
    Read these results with the node logs; a last running snapshot is not proof
    that resources remain usable. Preserve exception types and diagnostics.
    --scenario wait automatically supplies a fixture event; there is no interactive
    resume command. A new CLI invocation starts another execution.
    """
    arguments = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(arguments)
    if bool(args.workflow) == bool(args.example):
        parser.error("select exactly one of --workflow and --example")
    if args.workflow:
        if args.input is None:
            parser.error("--workflow requires --input")
        if args.scenario is not None:
            parser.error("--scenario requires --example")
        return _execute_registered(arguments)
    if args.version is not None or args.resources is not None:
        parser.error("packaged examples own their fixed versions and fixture resources")
    if args.example == "agent_evaluation_example" and args.scenario not in (None, "accepted"):
        parser.error("agent_evaluation_example does not use selector scenarios")
    try:
        payload = None if args.input is None else json.loads(args.input.read_text(encoding="utf-8"))
        record = run_agent_example(args.root, args.example, scenario=args.scenario or "accepted", input_payload=payload,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort, cli_path=args.cli_path)
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
