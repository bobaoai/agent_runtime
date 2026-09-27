"""Installed Test Run CLI and API composing registered execution and capability examples.

The command is agent-runtime-test-run and the Python entry is
run_local_workflow_test. A Test Run executes one registered definition with
temporary test resources, requests Inspection before temporary content closes
and returns facts. It does not judge behavior: the subject owner validates
output, and Behavior Evaluation remains a separate Workflow capability.
Preparation and ordinary execution remain in Execution; examples do not add
business judgment to the execution kernel.
"""
import json
import os
import re
import sys
from pathlib import Path
import shutil
import tempfile
import uuid

from ..execution.execution_local_invocation import (
    _prepare_local_workflow_module_with_sources, _prepare_loaded_workflow,
    _require_single_module_entry, _run_prepared_workflow_node,
)
from ..execution.execution_content_staging import InMemoryCellArtifactStore
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from ..registry.registry_local_persistence import LoadedRuntimeRegistration

from .execution_local_test_run import build_parser as _execution_parser, _resource_arguments
from .conformance_agent_execution import run_agent_example


_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _definition_source(*, workflow_id, version, release_store, release_database_url_env, release_schema,
                       workflow_release_ref, workflow_release_sha256) -> str:
    """Return local or postgres after checking that exactly one complete target was given."""
    postgres = (release_store, release_database_url_env, release_schema, workflow_release_ref, workflow_release_sha256)
    if workflow_id is not None:
        if any(value is not None for value in postgres):
            raise ValueError("Select either a local workflow_id or an exact PostgreSQL Workflow ref/hash, not both")
        if type(workflow_id) is not str or not workflow_id:
            raise ValueError("workflow_id must be a non-empty string")
        return "local"
    if workflow_release_ref is None and workflow_release_sha256 is None:
        raise ValueError("Test Run requires workflow_id, or workflow_release_ref with workflow_release_sha256")
    for name, value in (("workflow_release_ref", workflow_release_ref), ("workflow_release_sha256", workflow_release_sha256)):
        if type(value) is not str or not value:
            raise ValueError(f"A PostgreSQL definition requires {name}")
    if version is not None:
        raise ValueError("version selects a local definition; a PostgreSQL definition uses its exact ref/hash")
    if release_store is not None:
        if release_database_url_env is not None or release_schema is not None:
            raise ValueError("Give either a trusted release_store object or release_database_url_env with release_schema, not both")
        if not callable(getattr(release_store, "load_release_registry", None)):
            raise ValueError("release_store must provide load_release_registry")
        return "postgres"
    if release_database_url_env is None or release_schema is None:
        raise ValueError("A PostgreSQL definition requires both release_database_url_env and release_schema, or a release_store object")
    if type(release_database_url_env) is not str or not _ENVIRONMENT_NAME.fullmatch(release_database_url_env):
        raise ValueError("release_database_url_env must be an environment variable name")
    from ..invocation.invocation_claude_cli_execution import _ENVIRONMENT_KEYS as claude_keys
    from ..invocation.invocation_codex_environment import _ENVIRONMENT_KEYS as codex_keys
    if release_database_url_env in claude_keys | codex_keys:
        raise ValueError(f"release_database_url_env {release_database_url_env} is forwarded to Provider processes; "
                         "keep the DSN in a variable no Adapter passes through")
    if type(release_schema) is not str or not release_schema:
        raise ValueError("release_schema must be a non-empty string")
    return "postgres"


def run_local_workflow_test(
    root: Path, workflow_id: str | None = None, *, input_payload: dict,
    expected_module_id: str | None = None, version: str | None = None,
    release_store=None, release_database_url_env: str | None = None, release_schema: str | None = None,
    workflow_release_ref: str | None = None, workflow_release_sha256: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, cli_path: Path | str | None = None,
    resources_path: Path | None = None,
    material_root: Path | None = None, material_files: tuple[dict, ...] = (),
    read_only_dependencies: tuple[Path, ...] | None = None, commands: tuple[dict, ...] = (),
    tool_session_factory=None, user_cancel_requested=None, resource_cancel_requested=None,
) -> dict:
    """Test-run a registered single-node Workflow using temporary test resources.

    Args:
        root: Required in both definition modes. Locates Runtime setup,
            root/.runtime/config.json resource defaults, local definitions and
            the execution parameter files; never a model/tool read root.
        workflow_id: Local definition: Workflow to load from root/.runtime,
            including single-Module Workflows. Excludes every PostgreSQL field.
        input_payload: Exact JSON input validated against the registered schema.
        expected_module_id: Optional exact Module ID the selected Workflow must
            run. Checked before materials are captured or a model is called.
        version: Local definition only: exact version; None resolves the latest
            registered new definition once. Parameter files never choose it.
        release_store: PostgreSQL definition: trusted in-process store providing
            load_release_registry, such as PostgresRuntimeReleaseStore. Read
            only: the exact Workflow is loaded and this call's Profile/Variant
            stay in memory; nothing is registered or written. This differs from
            prepare_local_workflow_module, whose release_store writes releases.
        release_database_url_env: PostgreSQL definition: name of the environment
            variable holding the DSN, used with release_schema instead of a store
            object. The DSN is read from that variable only and never enters
            input, records or any Provider process, including CLI preflight.
            A variable name that either Adapter passes through to its Provider
            environment, such as LOGNAME or HTTPS_PROXY, is rejected.
        release_schema: PostgreSQL definition: existing release schema to read.
        workflow_release_ref: PostgreSQL definition: exact Workflow release_ref.
        workflow_release_sha256: PostgreSQL definition: exact release_sha256. A
            missing, mismatched or multi-node release is rejected; there is no
            fallback to a same-named local definition.
        transport_kind: This call's transport. None takes the Workflow parameter
            file, then the workspace parameter file, then Runtime's claude_cli
            default. claude_cli supports empty or selected native tool sets,
            inline input, and no write area or a private draft as defined.
            codex_cli supports tool_free, inline, empty tools, workspace none
            and denied tool network (v4), and the agent workspace
            environment: tools including shell, inline input, a private
            draft and denied tool network (workspace v3, which also takes
            the frozen resources and commands). It requires explicit
            model_id and effort; unsupported requirements are rejected,
            never reduced to fit.
        model_id: This call's concrete model ID; None falls back through the same
            layers to the Claude default.
            Codex requires an explicit model. Claude verifies observed model
            identity, allowing its known CLI [1m] selector. Codex retains the
            requested identity and available Provider facts without inventing
            an unreported actual response model. Model names do not select transport.
        reasoning_profile: This call's effort; None falls back through the same
            layers to Runtime's default. A model or effort written in a layer
            whose transport differs from the resolved transport is rejected.
            Required for codex_cli; no default is inferred from another Provider.
        cli_path: Explicit installed provider executable; otherwise use the
            selected transport's root/.runtime/config.json provider_cli_paths
            value, then host PATH when that value is absent. No login or
            installation is performed. Model selection does not come from config.
            Relative paths are resolved from the caller's working directory
            before entering the temporary Attempt directory. Both definition
            modes use the same root configuration.
            Codex uses file-based auth from the host's standard CODEX_HOME/auth.json
            (default ~/.codex/auth.json), never from task JSON or a fallback account.
        resources_path: Declarative resource JSON with material_root,
            material_files, read_only_dependencies and commands; relative paths
            resolve against that file. Excludes the four decomposed resource
            arguments below; giving both is rejected before any setup.
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
        JSON-compatible execution facts and output. execution_parameter_sources
        gives each parameter's source layer (call, workflow_file,
        workspace_file or runtime_default), file path and file SHA-256; it is
        record-only and never enters the Profile. provider_trace retains the
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
        KeyError: The exact PostgreSQL Workflow release is absent.
        ValueError: Conflicting definition targets or resources, a missing or
            empty DSN variable, hash mismatch, a different expected Module,
            invalid parameter file, mixed transports, unsupported graph/Profile,
            input or resource configuration. Target and resource conflicts are
            rejected before setup, a PostgreSQL connection or a Provider call.
        jsonschema.exceptions.ValidationError: Input violates its registered schema.
        PermissionError: The requested operation is outside bounded test resources.
        Exception: Existing provider/environment errors retain their contracts.
    Effects:
        Runs lightweight setup_runtime(root), reads fixed definitions (reading
        PostgreSQL only for a PostgreSQL definition), stages input in memory,
        calls the admitted Adapter through the existing Workflow Module kernel,
        and returns facts. Does not write PostgreSQL, register definitions,
        write execution parameters, manufacture production authorization, or
        persist a request receipt.
        Its private temporary workspace is removed on exit. The caller may
        explicitly save the returned result; Runtime does not save it by default.
    """
    source = _definition_source(workflow_id=workflow_id, version=version, release_store=release_store,
        release_database_url_env=release_database_url_env, release_schema=release_schema,
        workflow_release_ref=workflow_release_ref, workflow_release_sha256=workflow_release_sha256)
    if expected_module_id is not None and (type(expected_module_id) is not str or not expected_module_id):
        raise ValueError("expected_module_id must be a non-empty string when given")
    if resources_path is not None and (material_root is not None or material_files
                                       or read_only_dependencies is not None or commands):
        raise ValueError("Give resources_path or the decomposed resource arguments, not both")
    database_url = None
    if source == "postgres" and release_store is None:
        database_url = os.environ.get(release_database_url_env)
        if not database_url:
            raise ValueError(f"Environment variable {release_database_url_env} is missing or empty; no DSN fallback")

    from ..invocation.invocation_claude_cli_execution import ClaudeAdapter
    from ..invocation.invocation_codex_module_invocation import (
        CodexCliAgentWorkspaceModuleExecutor, CodexCliModuleExecutor,
    )
    from ..inspection.inspection_execution_logging import read_execution_log
    from ..foundation.foundation_environment_setup import load_runtime_config, setup_runtime
    from ..invocation.invocation_local_resource_preparation import capture_local_resources, parse_local_resources

    setup_runtime(root)
    if resources_path is not None:
        resources = _resource_arguments(Path(resources_path))
        material_root = resources.get("material_root")
        material_files = resources.get("material_files", ())
        read_only_dependencies = resources.get("read_only_dependencies")
        commands = resources.get("commands", ())
    choice = dict(transport_kind=transport_kind, model_id=model_id, reasoning_profile=reasoning_profile)
    if source == "local":
        saved, selection, parameters = _prepare_local_workflow_module_with_sources(
            root, workflow_id, version=version, **choice)
    else:
        if release_store is None:
            from ..registry.registry_postgres_persistence import PostgresRuntimeReleaseStore
            release_store = PostgresRuntimeReleaseStore.from_dsn(database_url, schema=release_schema)
        registry = release_store.load_release_registry()
        loaded = registry.get_workflow(workflow_release_ref, workflow_release_sha256)
        _require_single_module_entry(loaded)
        saved, selection, parameters = _prepare_loaded_workflow(
            LoadedRuntimeRegistration(loaded, registry), root=root, release_store=None, **choice)
    workflow = saved.release
    node = workflow.nodes[0]
    module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
    if expected_module_id is not None and module.module_id != expected_module_id:
        raise ValueError(f"Selected Workflow runs Module {module.module_id}, not the expected {expected_module_id}")
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
        elif (selected_profile.executor_adapter_id, selected_profile.executor_adapter_revision) == (
                CodexCliAgentWorkspaceModuleExecutor.executor_adapter_id,
                CodexCliAgentWorkspaceModuleExecutor.executor_adapter_revision):
            adapter = CodexCliAgentWorkspaceModuleExecutor(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, codex_bin=str(executable), read_only_dependencies=dependencies)
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
        record["execution_parameter_sources"] = parameters.as_record()
        return record


def _execute_registered(args):
    """Test-run once and emit execution JSON on stdout without saving it.

    Exit 0: completed execution with schema-valid output, including a valid
    non_pass verdict. The subject owner applies its own semantic validator.
    Exit 1: input, definition, execution or environment failure; stderr
    preserves the error type and available native error_code. Failed Attempts
    remain in stdout JSON. Exit 130: user interruption; captured Attempt logs
    remain in the returned execution JSON when invocation had started. No
    verdict is manufactured. Each invocation is a new temporary test. No
    cross-process recovery or stored history is promised. Explicit stdout
    capture belongs to the calling operator. Setup runs inside
    run_local_workflow_test: ready setup makes no writes, and registered
    definitions and persistent execution stores are unchanged.
    """
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        record = run_local_workflow_test(args.root, args.workflow, input_payload=payload,
            expected_module_id=args.expected_module_id, version=args.version,
            release_database_url_env=args.release_database_url_env, release_schema=args.release_schema,
            workflow_release_ref=args.workflow_ref, workflow_release_sha256=args.workflow_sha256,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort,
            cli_path=args.cli_path, resources_path=args.resources)
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0 if record["status"] == "completed" else 130 if record["status"] == "cancelled" else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "Test Run interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1



def build_parser():
    """Compose the Test Run argument grammar with the fixed example choices."""
    parser = _execution_parser(require_workflow=False, require_input=False)
    parser.prog = "agent-runtime-test-run"
    parser.description = ("Test-run a registered single-Module Workflow, from root/.runtime or read-only from an exact "
                          "PostgreSQL release, or a packaged multi-Agent example. Select exactly one target: --workflow, "
                          "--workflow-ref with --workflow-sha256, or --example. No production authorization; nothing is persisted.")
    parser.epilog += "\n\nPackaged examples register fixed definitions and run a fresh execution. Saved JSON is evidence, not cross-process resume. See docs/agent_runtime_capability_runbook.md for example use and result interpretation."
    parser.add_argument("--example", choices=("agent_capability_example", "agent_evaluation_example"),
        help="Register and run a packaged example. capability covers query/writer/parallel reviews; evaluation covers Agent-requested child review and independent evaluation. Definitions are saved under root; execution is temporary. --input is optional and accepts exactly task and required_facts; omission uses the packaged finite fixture.")
    parser.add_argument("--scenario", choices=("accepted", "revision", "wait"),
        help="Capability example only: first selector decision. revision returns to the Writer; wait records WAIT then supplies one matching material_ready fixture event in the same process. Default accepted; not cross-process resume.")
    return parser


def main(argv=None):
    """Run the selected target and print its actual execution records as JSON.

    Exactly one target is required: --workflow (local definition),
    --workflow-ref with --workflow-sha256 (read-only PostgreSQL definition,
    also requiring --release-database-url-env and --release-schema), or
    --example. Registered definitions require --input; all their arguments go
    unchanged to run_local_workflow_test, which runs setup, reads the resource
    file and resolves execution parameters. --example owns fixed definitions
    and fixture resources, so --version, --resources, PostgreSQL arguments and
    --expected-module-id are rejected; its model choice uses the same
    execution parameter layers. Optional --input contains task and
    required_facts. Exit 0 means technical completion, not business acceptance
    or full Runtime conformance. Exit 1 means input/definition/environment/
    execution/cleanup failure or an unfinished graph; captured facts remain in
    returned JSON when available. Exit 2 means invalid command arguments before
    setup or model execution. Exit 130 means user cancellation, retaining
    captured records when started.

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
    postgres = {"--workflow-ref": args.workflow_ref, "--workflow-sha256": args.workflow_sha256,
                "--release-database-url-env": args.release_database_url_env, "--release-schema": args.release_schema}
    uses_postgres = any(value is not None for value in postgres.values())
    if sum((args.workflow is not None, uses_postgres, args.example is not None)) != 1:
        parser.error("select exactly one target: --workflow, --workflow-ref with --workflow-sha256, or --example")
    if args.example is None:
        if args.input is None:
            parser.error("a registered definition requires --input")
        if args.scenario is not None:
            parser.error("--scenario requires --example")
        if uses_postgres:
            missing = [flag for flag, value in postgres.items() if value is None]
            if missing:
                parser.error("a PostgreSQL definition also requires " + ", ".join(missing))
            if args.version is not None:
                parser.error("--version selects a local definition; a PostgreSQL definition uses --workflow-ref and --workflow-sha256")
        return _execute_registered(args)
    if args.version is not None or args.resources is not None or args.expected_module_id is not None:
        parser.error("packaged examples own their fixed versions, fixture resources and Modules")
    if args.example == "agent_evaluation_example" and args.scenario not in (None, "accepted"):
        parser.error("agent_evaluation_example does not use selector scenarios")
    try:
        payload = None if args.input is None else json.loads(args.input.read_text(encoding="utf-8"))
        record = run_agent_example(args.root, args.example, scenario=args.scenario or "accepted", input_payload=payload,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort, cli_path=args.cli_path)
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0 if record["status"] == "completed" else 130 if record["status"] == "cancelled" else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "Test Run interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
