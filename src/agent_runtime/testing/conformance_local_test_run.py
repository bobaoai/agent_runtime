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
import time
from pathlib import Path
import uuid

from ..invocation.invocation_host_platform_selection import HOST_PLATFORM

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


def _forwarded_variable(name: str) -> bool:
    from ..invocation.invocation_claude_cli_execution import _ENVIRONMENT_KEYS as claude_keys
    from ..invocation.invocation_codex_module_invocation import _ENVIRONMENT_KEYS as codex_keys
    return name in claude_keys | codex_keys


def _definition_source(*, workflow_id, version, release_store, release_database_url_env, release_schema,
                       workflow_release_ref, workflow_release_sha256, selection=None,
                       expected_module_id=None) -> str:
    """Return local, postgres or selection after checking that exactly one complete target was given."""
    postgres = (release_store, release_database_url_env, release_schema, workflow_release_ref, workflow_release_sha256)
    if selection is not None:
        if workflow_id is not None or version is not None or any(value is not None for value in postgres):
            raise ValueError("A selection already fixes the exact definition and its store; give no workflow_id, "
                             "version or PostgreSQL target with it")
        if expected_module_id is not None:
            raise ValueError("A selection already fixes the exact Module; expected_module_id is not used with it")
        return "selection"
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
    if _forwarded_variable(release_database_url_env):
        raise ValueError(f"release_database_url_env {release_database_url_env} is forwarded to Provider processes; "
                         "keep the DSN in a variable no Adapter passes through")
    if type(release_schema) is not str or not release_schema:
        raise ValueError("release_schema must be a non-empty string")
    return "postgres"


def run_local_workflow_test(
    root: Path, workflow_id: str | None = None, *, input_payload: dict,
    selection=None, expected_module_id: str | None = None, version: str | None = None,
    release_store=None, release_database_url_env: str | None = None, release_schema: str | None = None,
    workflow_release_ref: str | None = None, workflow_release_sha256: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, run_timeout_seconds: int | None = None, parent_run=None,
    cli_path: Path | str | None = None,
    resources_path: Path | None = None,
    material_root: Path | None = None, material_files: tuple[dict, ...] = (),
    read_only_dependencies: tuple[Path, ...] | None = None, commands: tuple[dict, ...] = (),
    tool_session_factory=None, user_cancel_requested=None, resource_cancel_requested=None,
    progress_observer=None, project_directory: Path | None = None, command_permits: tuple[str, ...] = (),
    codex_execution_mode: str = "configured",
) -> dict:
    """Test-run a registered single-node Workflow with retained Attempt folders.

    Args:
        root: Required in every definition mode. Locates Runtime setup,
            root/.runtime/config.json resource defaults, local definitions and
            the execution parameter files; never a model/tool read root.
        workflow_id: Local definition: Workflow to load from root/.runtime,
            including single-Module Workflows. Excludes every PostgreSQL field.
        input_payload: Exact JSON input validated against the registered schema.
        selection: The ordinary definition handoff: a RuntimeDefinitionSelection
            returned by selection_from_registration/select_registered_workflow,
            or the path of a file written by write_definition_selection (the
            registry CLI's --selection-out). It must name this root. Runtime
            re-reads its store (root/.runtime, or the PostgreSQL release store
            the selection names, opened through its current entry in
            root/.runtime/config.json release_stores) and runs only the
            recorded Workflow and Module refs and hashes; a later registered
            version never replaces it. Another root, an unconfigured store or a
            missing DSN variable is refused before setup; a missing or different
            record is refused before materials or a Provider. Excludes
            workflow_id, version, expected_module_id and the PostgreSQL fields.
            The record's definition_selection reports the selection and whether
            the executed identity matched it.
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
            default. claude_cli (v4) maps the recorded tools exactly (read: Read;
            search: Grep, Glob; edit: Edit, Write; shell: Bash; mcp__<name>: a
            declared host connection) with no write area, a private draft or an
            explicit project directory. codex_cli supports tool_free (v5) and
            agent Modules (workspace v5): Shell-capable tools in a private draft
            or project directory, or only declared MCP connections with the
            shell tool off; both take denied or provider_managed network. It
            requires explicit model_id and effort; unsupported requirements are
            rejected, never reduced to fit.
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
        run_timeout_seconds: Total synchronous work budget in seconds through the four layers; default 1200.
        parent_run: Optional live context issued by the active managed parent host.
            JSON, arbitrary deadlines and cross-process contexts are rejected.
        cli_path: Explicit installed provider executable; otherwise use the
            selected transport's root/.runtime/config.json provider_cli_paths
            value, then host PATH when that value is absent. No login or
            installation is performed. Model selection does not come from config.
            Relative paths are resolved from the caller's working directory.
            Both definition modes use the same root configuration. On Windows
            the PATH lookup finds the native program (claude.exe, codex.exe);
            a .cmd/.bat/.ps1 launcher is not started because it would re-parse
            the exact arguments. Each CLI uses the host's own login and
            configuration, never credentials from task JSON.
        resources_path: Declarative resource JSON with material_root,
            material_files, read_only_dependencies and commands; relative paths
            resolve against that file. Excludes the four decomposed resource
            arguments below; giving both is rejected before any setup.
        material_root: Explicit source directory for the frozen file list below,
            never implicitly the host root. Only listed ordinary files are read.
        material_files: Tuple of dictionaries with relative_path, sha256 and
            executable, normally the value prepare_material_files computed from
            the caller's explicitly selected files (an empty tuple stays empty).
            Bytes and executable bits must match the supplied list;
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
            exact non-native tool_policy set is accepted. A session may implement
            bind_parent_run(context); Runtime supplies its active parent binding
            after open_session. A managed nested run passes that object as
            parent_run, never a deadline from model arguments. No import locator or
            serialized factory is read from task data, config or resources JSON.
        user_cancel_requested: Optional trusted callback for real user/workflow
            cancellation, also passed to the underlying running CLI process.
        resource_cancel_requested: Optional trusted callback for parent resource
            closure. Stops the process as resource_closed, not user cancellation.
        progress_observer: Optional callable for volatile live display. It is
            attached only when the resolved transport (call, Workflow or
            workspace parameter file, or Runtime default) is claude_cli; for
            other transports it is accepted and never called, so no progress
            is shown and the run is unchanged. It receives CliProcessProgress
            snapshots on a Runtime thread and should return promptly: process
            facts plus one safe current-event summary, published when an init,
            model activity, tool request or result, final result or declared
            command is received, and as a heartbeat after 10 s without one
            (repeating the last event). Delivery is in order through a buffer
            of at most 16 snapshots; updates_dropped reports any that were lost.
            Snapshots are display-only, never records; zero bytes does not mean
            the model made no progress. Observation never changes the result.
        project_directory: The caller's existing writable directory (checkout,
            worktree, task copy or output directory) for a Module whose
            attempt_workspace_policy is explicit_project_read_write; required
            exactly for that policy. Runtime never deletes, seals or cleans it.
        command_permits: Host-prepared Shell command permits for this call, in
            Claude Bash rule syntax (for example "python -m pytest:*"), usually
            chosen from earlier calls' recorded tool use and this task's needs.
            Claude renders them as Bash(<permit>) allow rules under dontAsk: they
            pre-approve those commands, Claude's built-in read-only commands still
            run, and other commands are denied. Without permits Runtime adds no
            Bash allow rule. Codex cannot express per-call permits and refuses them.
        codex_execution_mode: Per-call host resource choice, configured (default)
            preserves the existing CLI permission configuration. host selects
            Codex native host execution only for agent, explicit_project_read_write,
            provider_managed and exactly read/search/edit/shell. It uses host
            permissions; cwd is task scope, not an OS filesystem boundary.
            No material package, additional dependencies or recorded commands
            are accepted in host mode. Unknown values and null are rejected.
            Parameter files and Module definitions do not select this option.
            Actual mode, argv and directory are retained in provider_trace.
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
        A private temporary directory kept because the started CLI process was not
        confirmed stopped gives claude_cli_cleanup_failed naming its location
        while preserving the received trace. User cancellation uses claude_cli_interrupted
        and denies retry. Interrupted capture retains prior_stop_reason; a failure
        already received by the Adapter remains in provider_log.adapter_failure.
        Each execution_log Attempt includes its final private failure_detail.
        Codex uses codex_cli_interrupted/codex_cli_cleanup_failed for the same
        interruption/cleanup distinction. attempt_workspaces lists, per Attempt,
        attempt_id, workspace_path, archive_path (the record saved as
        archive/execution_record.json, or None), project_directory (or None),
        sealed and seal_skipped_reason (archive_not_saved,
        process_stop_unconfirmed, execution_not_ended or None). A folder is
        sealed only when its execution ended, its started process was confirmed
        stopped and its archive was saved; otherwise it stays as the recovery
        scene. maintain_attempt_workspaces later cleans expired sealed folders.
    Raises:
        FileNotFoundError: Missing registration, version or provider executable.
        KeyError: The exact PostgreSQL Workflow release is absent.
        ValueError: Conflicting definition targets or resources, a missing or
            empty DSN variable, hash mismatch, a different expected Module, a
            selection for another root or an unconfigured release store,
            invalid parameter file, mixed transports, unsupported graph/Profile,
            input or resource configuration. Target and resource conflicts are
            rejected before setup, a PostgreSQL connection or a Provider call.
        jsonschema.exceptions.ValidationError: Input violates its registered schema.
        PermissionError: The requested operation is outside bounded test resources.
        TypeError: progress_observer is not callable, checked before setup.
        Exception: Existing provider/environment errors retain their contracts.
    Effects:
        Runs lightweight setup_runtime(root), reads fixed definitions (reading
        PostgreSQL only for a PostgreSQL definition), stages input in memory,
        calls the admitted Adapter through the existing Workflow Module kernel,
        and returns facts. Does not write PostgreSQL, register definitions,
        write execution parameters, manufacture production authorization, or
        persist a request receipt.
        Attempt folders are retained under root/.runtime/config.json
        attempt_workspace_root (default root/.runtime/attempt_workspaces); each
        holds its materials, scratch and the archived record. Caller project
        directories are never included in Runtime cleanup.
    """
    entered = time.monotonic()
    from ..invocation.invocation_codex_module_invocation import (
        validate_codex_execution_mode, CodexCapabilityUnsupportedError,
    )
    validate_codex_execution_mode(codex_execution_mode)
    if progress_observer is not None and not callable(progress_observer):
        raise TypeError("progress_observer must be callable")
    if parent_run is not None:
        from ..execution.execution_run_budget import ParentRunContext
        if type(parent_run) is not ParentRunContext:
            raise TypeError("parent_run must be a live Runtime parent context")
        parent_run.require_active()
    source = _definition_source(workflow_id=workflow_id, version=version, release_store=release_store,
        release_database_url_env=release_database_url_env, release_schema=release_schema,
        workflow_release_ref=workflow_release_ref, workflow_release_sha256=workflow_release_sha256,
        selection=selection, expected_module_id=expected_module_id)
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
    selected_store = None
    if source == "selection":
        selection, selected_store = _checked_selection(selection, root)

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
    choice = dict(transport_kind=transport_kind, model_id=model_id, reasoning_profile=reasoning_profile,
                  run_timeout_seconds=run_timeout_seconds)
    if source == "local":
        saved, variant, parameters = _prepare_local_workflow_module_with_sources(
            root, workflow_id, version=version, **choice)
    elif source == "selection":
        from ..registry.registry_definition_selection import resolve_definition_selection
        loaded = resolve_definition_selection(selection, root=root, release_store=selected_store)
        saved, variant, parameters = _prepare_loaded_workflow(loaded, root=root, release_store=None, **choice)
    else:
        if release_store is None:
            from ..registry.registry_postgres_persistence import PostgresRuntimeReleaseStore
            release_store = PostgresRuntimeReleaseStore.from_dsn(database_url, schema=release_schema)
        registry = release_store.load_release_registry()
        loaded = registry.get_workflow(workflow_release_ref, workflow_release_sha256)
        _require_single_module_entry(loaded)
        saved, variant, parameters = _prepare_loaded_workflow(
            LoadedRuntimeRegistration(loaded, registry), root=root, release_store=None, **choice)
    from ..execution.execution_run_budget import RunBudget
    budget = RunBudget(parameters.run_timeout_seconds, started=entered, parent_run=parent_run)
    record = None
    try:
        budget.require_active()
        workflow = saved.release
        node = workflow.nodes[0]
        module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
        if expected_module_id is not None and module.module_id != expected_module_id:
            raise ValueError(f"Selected Workflow runs Module {module.module_id}, not the expected {expected_module_id}")
        binding = variant.policy_document()["bindings"][0]
        selected_profile = saved.registry.get_execution_profile(
            binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
        validate_codex_execution_mode(codex_execution_mode, profile=selected_profile)
        config = load_runtime_config(root)
        dependencies = read_only_dependencies
        if dependencies is None:
            dependencies = config["read_only_dependencies"] if selected_profile.tool_policy else ()
        if type(dependencies) is not tuple:
            raise ValueError("read_only_dependencies must be a tuple or None for host defaults")
        if codex_execution_mode == "host" and (material_root is not None or material_files or dependencies or commands):
            raise CodexCapabilityUnsupportedError(
                "host execution takes no material package, extra dependencies or command extension")
        resource_body = capture_local_resources(profile=selected_profile, material_root=material_root,
            material_files=material_files, read_only_dependencies=dependencies, commands=commands)
        dependencies = (() if resource_body is None else
            tuple(Path(value) for value in parse_local_resources(resource_body)["read_only_dependencies"]))
        program = {"claude_cli": "claude", "codex_cli": "codex"}[selected_profile.transport_kind]
        executable = cli_path if cli_path is not None else config["provider_cli_paths"].get(selected_profile.transport_kind)
        if executable is None:
            executable = HOST_PLATFORM.resolve_cli_program(program, os.environ)
        if executable is None:
            raise FileNotFoundError(f"{program} CLI executable is unavailable; provide cli_path, provider_cli_paths "
                                    "or a native program on host PATH")
        executable = Path(executable).resolve(strict=True)
        artifacts = InMemoryCellArtifactStore()
        ledger = InMemoryModuleExecutionLedger()
        budget.require_active()
        workspace = Path(config["attempt_workspace_root"])
        workspace.mkdir(parents=True, exist_ok=True)
        workspace = workspace.resolve()
        connections = config["mcp_servers"].get(selected_profile.transport_kind, {})
        resources = dict(mcp_servers=connections, project_directory=project_directory,
                         command_permits=tuple(command_permits))
        if selected_profile.transport_kind == "claude_cli":
            observation = {} if progress_observer is None else {"progress_observer": progress_observer}
            adapter = ClaudeAdapter(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, cli_path=executable, read_only_dependencies=dependencies,
                adapter_binding=(selected_profile.executor_adapter_id, selected_profile.executor_adapter_revision),
                **observation, **resources)
        elif (selected_profile.executor_adapter_id, selected_profile.executor_adapter_revision) == (
                CodexCliAgentWorkspaceModuleExecutor.executor_adapter_id,
                CodexCliAgentWorkspaceModuleExecutor.executor_adapter_revision):
            adapter = CodexCliAgentWorkspaceModuleExecutor(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, codex_bin=str(executable), read_only_dependencies=dependencies,
                codex_execution_mode=codex_execution_mode, **resources)
        else:
            adapter = CodexCliModuleExecutor(release_registry=saved.registry, artifact_host=artifacts,
                workspace_root=workspace, codex_bin=str(executable), **resources)
        result, record = _run_prepared_workflow_node(registry=saved.registry, workflow=workflow,
            selection=variant, input_payload=input_payload, idempotency_key="self_test_"+uuid.uuid4().hex,
            artifact_host=artifacts, ledger=ledger, workspace_root=workspace, adapter=adapter,
            local_resources=resource_body, tool_session_factory=tool_session_factory,
            user_cancel_requested=user_cancel_requested, resource_cancel_requested=resource_cancel_requested, run_budget=budget)
        record["execution_log"] = read_execution_log(
            result.module_run, attempts=result.attempts,
            read_content=artifacts.read_bytes, include_private_content=True,
        )
        record["execution_parameter_sources"] = parameters.as_record()
        if source == "selection":
            record["definition_selection"] = _selection_record(selection, record)
        record["execution_budget"] = budget.as_record()
        record["attempt_workspaces"] = _archive_and_seal(record, workspace_root=workspace,
                                                         project_directory=project_directory)
        return record
    finally:
        if record is not None:
            record["execution_budget"] = budget.as_record()
        budget.close()


def _checked_selection(selection, root):
    """Read the selection, check its root and open its configured store before setup."""
    from ..registry.registry_definition_selection import (
        RuntimeDefinitionSelection, configured_release_store, open_configured_release_store,
        read_definition_selection,
    )
    if type(selection) is not RuntimeDefinitionSelection:
        if not isinstance(selection, (str, os.PathLike)):
            raise ValueError("selection must be a RuntimeDefinitionSelection or the path of a saved selection")
        selection = read_definition_selection(Path(selection))
    current = str(Path(root).resolve())
    if os.path.normcase(os.path.normpath(current)) != os.path.normcase(os.path.normpath(selection.root)):
        raise ValueError(f"the selection was made for root {selection.root}, not {current}")
    if selection.release_store is None:
        return selection, None
    variable = configured_release_store(root, selection.release_store)["database_url_env"]
    if _forwarded_variable(variable):
        raise ValueError(f"release store {selection.release_store!r} keeps its DSN in {variable}, which is "
                         "forwarded to Provider processes; keep the DSN in a variable no Adapter passes through")
    return selection, open_configured_release_store(root, selection.release_store)


def _selection_record(selection, record) -> dict:
    """The selection used and whether the executed Workflow/Module equal it, compared by code."""
    names = ("workflow_release_ref", "workflow_release_sha256", "module_release_ref", "module_release_sha256")
    return {**selection.as_dict(), "executed_matches_selection": all(
        record.get(name) == getattr(selection, name) for name in names)}


def write_test_run_record(path: Path, document: dict) -> Path:
    """Save one Test Run record, or the error document of a run that returned none, as a new UTF-8 file.

    Exclusive create in an existing folder: an existing file is never
    overwritten. The saved JSON is evidence for inspect_test_run_record; it is
    not a Ledger, a recovery receipt or a handle for a later rerun.
    Returns the resolved path.
    """
    if type(document) is not dict:
        raise ValueError("a Test Run record must be a JSON object")
    body = json.dumps(document, ensure_ascii=False, allow_nan=False) + "\n"
    path = Path(path)
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
    return path.resolve()


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _archive_and_seal(record: dict, *, workspace_root: Path, project_directory) -> list[dict]:
    """Test-host step: archive the returned record in each Attempt folder, then seal it.

    The record (without this field) is written once per Attempt to
    archive/execution_record.json with exclusive create, only inside the
    Attempt's own ordinary archive folder (write_attempt_archive); a link or
    reparse point there is refused, reported in archive_error and leaves the
    scene as found. A folder is sealed with
    the current UTC instant only when the run ended, the Attempt's started CLI
    process was confirmed stopped (no cleanup_error in its provider log and no
    *_cleanup_failed failure) and the archive was saved; otherwise it stays as
    the recovery scene with seal_skipped_reason. A seal that the workspace
    itself refuses is reported in seal_error. Caller project directories are
    reported, never archived into, sealed or cleaned.
    """
    from ..invocation.invocation_workspace_preparation import (
        seal_attempt_workspace, utc_now_instant, write_attempt_archive,
    )
    snapshot = json.dumps(record, ensure_ascii=False, allow_nan=False).encode("utf-8")
    ended = record.get("status") in _TERMINAL_STATUSES
    rows = []
    for attempt in (record.get("execution_log") or {}).get("attempts", ()):
        attempt_id = attempt.get("attempt_id")
        folder = Path(workspace_root) / str(attempt_id)
        provider_log = attempt.get("provider_log") or {}
        unconfirmed = bool(provider_log.get("cleanup_error")) or "_cleanup_failed" in json.dumps(
            attempt.get("failure_detail"), ensure_ascii=False, default=str)
        archive_path, archive_error = None, None
        try:
            # Only inside this Attempt's own ordinary archive folder; a link or
            # reparse point there is refused and the scene is left as found.
            archive_path = str(write_attempt_archive(workspace_root=workspace_root, attempt_id=attempt_id,
                                                     file_name="execution_record.json", content=snapshot))
        except (OSError, RuntimeError, ValueError) as exc:
            archive_error = f"{type(exc).__name__}: {exc}"
        reason = ("execution_not_ended" if not ended else "process_stop_unconfirmed" if unconfirmed
                  else "archive_not_saved" if archive_path is None else None)
        row = {"attempt_id": attempt_id, "workspace_path": str(folder), "archive_path": archive_path,
               "project_directory": None if project_directory is None else str(Path(project_directory).resolve()),
               "sealed": False, "seal_skipped_reason": reason}
        if archive_error is not None:
            row["archive_error"] = archive_error
        if reason is None:
            try:
                seal_attempt_workspace(workspace_root=workspace_root, attempt_id=attempt_id,
                                       effective_at_utc=utc_now_instant())
                row["sealed"] = True
            except (OSError, RuntimeError, ValueError) as exc:
                row["seal_error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows


def _cli_materials(args):
    """material_root/material_files computed from --material-root with --material or --all-materials."""
    if args.material_root is None:
        return {}
    from ..invocation.invocation_local_resource_preparation import prepare_material_files
    selected = None if args.all_materials else tuple(args.materials)
    return {"material_root": args.material_root,
            "material_files": prepare_material_files(args.material_root, selected)}


def _execute_registered(args):
    """Test-run once and emit execution JSON on stdout; --record-out also saves it.

    Exit 0: completed execution with schema-valid output, including a valid
    non_pass verdict. The subject owner applies its own semantic validator.
    Exit 1: input, definition, execution or environment failure; stderr
    preserves the error type and available native error_code. Failed Attempts
    remain in stdout JSON. Exit 130: user interruption; captured Attempt logs
    remain in the returned execution JSON when invocation had started. No
    verdict is manufactured. Each invocation is a new temporary test. No
    cross-process recovery or stored history is promised. Explicit stdout
    capture belongs to the calling operator; --record-out saves the same JSON
    (or, when no record was returned, the stderr error document) to a new file.
    Setup runs inside run_local_workflow_test: ready setup makes no writes, and
    registered definitions and persistent execution stores are unchanged.
    """
    document = None
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        record = run_local_workflow_test(args.root, args.workflow, input_payload=payload,
            selection=args.selection, expected_module_id=args.expected_module_id, version=args.version,
            release_database_url_env=args.release_database_url_env, release_schema=args.release_schema,
            workflow_release_ref=args.workflow_ref, workflow_release_sha256=args.workflow_sha256,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort, run_timeout_seconds=args.run_timeout_seconds,
            cli_path=args.cli_path, resources_path=args.resources,
            project_directory=None if args.project_directory is None else args.project_directory.resolve(),
            command_permits=tuple(args.command_permits), codex_execution_mode=args.codex_execution_mode,
            **_cli_materials(args))
        document = record
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        status = 0 if record["status"] == "completed" else 130 if record["status"] == "cancelled" else 1
    except KeyboardInterrupt:
        document = {"error_type": "KeyboardInterrupt", "detail": "Test Run interrupted"}
        print(json.dumps(document), file=sys.stderr)
        status = 130
    except Exception as exc:
        document = {"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                    "detail": str(exc)}
        print(json.dumps(document, ensure_ascii=False), file=sys.stderr)
        status = 1
    if args.record_out is not None:
        try:
            write_test_run_record(args.record_out, document)
        except OSError as exc:
            print(json.dumps({"error_type": type(exc).__name__, "error_code": None,
                              "detail": f"record was not saved to {args.record_out}: {exc}"},
                             ensure_ascii=False), file=sys.stderr)
            return status or 1
    return status



def build_parser():
    """Compose the Test Run argument grammar with the fixed example choices."""
    parser = _execution_parser(require_workflow=False, require_input=False)
    parser.prog = "agent-runtime-test-run"
    parser.description = ("Test-run a registered single-Module Workflow, from root/.runtime or read-only from an exact "
                          "PostgreSQL release, or a packaged multi-Agent example. Select exactly one target: --selection "
                          "(the file a registry command wrote), --workflow, --workflow-ref with --workflow-sha256, or "
                          "--example. No production authorization; nothing is persisted except an explicit --record-out file.")
    parser.epilog += ("\n\nPackaged --example graphs register fixed definitions and run a fresh execution. Saved JSON is "
                      "evidence, not cross-process resume. They use explicitly selected callback tools: host-limited "
                      "advanced examples for macOS/Linux with the cli_tools extra; Windows reports the callback extension "
                      "unsupported before a session opens. The four ordinary usage patterns (structured_output, "
                      "file_review, code_change, mcp_data) run on every host with: python -m "
                      "agent_runtime.testing.conformance_pattern_examples run --pattern PATTERN --transport TRANSPORT "
                      "--root ROOT. See docs/agent_runtime_capability_runbook.md for example use and result "
                      "interpretation.")
    parser.add_argument("--example", choices=("agent_capability_example", "agent_evaluation_example"),
        help="Register and run a packaged multi-Agent callback example (host-limited: macOS/Linux with cli_tools; not Windows). capability covers query/writer/parallel reviews; evaluation covers Agent-requested child review and independent evaluation. Definitions are saved under root; execution uses temporary folders removed afterwards. --input is optional and accepts exactly task and required_facts; omission uses the packaged finite fixture.")
    parser.add_argument("--scenario", choices=("accepted", "revision", "wait"),
        help="Capability example only: first selector decision. revision returns to the Writer; wait records WAIT then supplies one matching material_ready fixture event in the same process. Default accepted; not cross-process resume.")
    return parser


def main(argv=None):
    """Run the selected target and print its actual execution records as JSON.

    Exactly one target is required: --selection (the code-written selection
    from a registry command; the ordinary handoff), --workflow (local
    definition), --workflow-ref with --workflow-sha256 (read-only PostgreSQL
    definition, also requiring --release-database-url-env and
    --release-schema), or --example. Registered definitions require --input;
    their arguments go to run_local_workflow_test, which runs setup, reads the
    resource file and resolves execution parameters. --material-root with
    --material or --all-materials is turned into the frozen file list by
    prepare_material_files first. An existing --record-out file or its missing
    folder is a usage error before setup. --example owns fixed definitions
    and fixture resources, so --version, --resources, materials, --selection,
    --record-out, PostgreSQL arguments and
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
    if sum((args.selection is not None, args.workflow is not None, uses_postgres, args.example is not None)) != 1:
        parser.error("select exactly one target: --selection, --workflow, --workflow-ref with --workflow-sha256, or --example")
    if args.materials is not None and args.all_materials:
        parser.error("select materials with --material or the whole folder with --all-materials, not both")
    if args.material_root is None and (args.materials is not None or args.all_materials):
        parser.error("--material and --all-materials select files under --material-root")
    if args.material_root is not None:
        if args.resources is not None:
            parser.error("give --resources or --material-root, not both")
        if args.materials is None and not args.all_materials:
            parser.error("--material-root needs --material FILE (repeatable) or --all-materials")
    if args.record_out is not None and (args.record_out.exists() or not args.record_out.parent.is_dir()):
        parser.error(f"--record-out must name a new file in an existing folder: {args.record_out}")
    if args.example is None:
        if args.input is None:
            parser.error("a registered definition requires --input")
        if args.selection is not None and (args.version is not None or args.expected_module_id is not None):
            parser.error("--selection already fixes the exact version and Module")
        if args.scenario is not None:
            parser.error("--scenario requires --example")
        if uses_postgres:
            missing = [flag for flag, value in postgres.items() if value is None]
            if missing:
                parser.error("a PostgreSQL definition also requires " + ", ".join(missing))
            if args.version is not None:
                parser.error("--version selects a local definition; a PostgreSQL definition uses --workflow-ref and --workflow-sha256")
        return _execute_registered(args)
    if (args.version is not None or args.resources is not None or args.expected_module_id is not None
            or args.material_root is not None or args.record_out is not None or args.codex_execution_mode != "configured"):
        parser.error("packaged examples own their fixed versions, fixture resources and Modules")
    if args.example == "agent_evaluation_example" and args.scenario not in (None, "accepted"):
        parser.error("agent_evaluation_example does not use selector scenarios")
    try:
        payload = None if args.input is None else json.loads(args.input.read_text(encoding="utf-8"))
        record = run_agent_example(args.root, args.example, scenario=args.scenario or "accepted", input_payload=payload,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort, run_timeout_seconds=args.run_timeout_seconds, cli_path=args.cli_path)
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
