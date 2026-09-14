"""Runtime run profile

从库外调用 Runtime，先看一次运行的完整组成，不需要逐个搜索实现文件。
这里的总览不是新的 Profile 对象；ExecutionProfileRelease 只是其中的执行配置。

| 运行部分 | 谁提供或决定 | Runtime 负责人及边界 |
| --- | --- | --- |
| Module：任务 prompt、输入/输出 schema、任务归属 | 任务 owner 提供 source；普通 Module 明确自己的运行要求 | Registry 验证、导出、注册版本，不判断业务结果 |
| ModuleReviewer：固定运行底座 | Runtime 的 ModuleReviewer 提供默认 ModuleExecutionRequirements；不同 Reviewer 提供不同任务内容 | Registry 保留通用 Module 的继承行为；下游不按 Reviewer 名称重新组装或增加配置 |
| Workflow：节点、版本与连接 | 调用者组装；单节点默认与 Module 同名，也可明确命名 | Registry 保存图；Execution 固定本次准确版本，未指定版本时使用最新注册定义 |
| root、Python、程序与资源 | 宿主给 root、现有登录及明确材料/额外依赖；Python 固定为启动 Runtime 的 sys.executable | Foundation 做轻量 setup/配置读取；Invocation 使用当前 Python 环境，只读开放其运行库，不另选解释器；root 本身不是模型读取许可 |
| ExecutionProfileRelease 与 Variant | 调用者可给模型、transport、effort；未给时用 Runtime 默认；固定能力来自 Module | Execution.prepare 生成本次具体 Profile 与节点绑定；Registry 承载准确内容；调用者不手拼 Profile，root 不固定绑定模型 |
| Provider 与工具调用 | 上一步已经固定的 Profile 和明确资源 | Invocation 的 Adapter 组装 CLI、映射工具、执行声明命令并采集原始流；不重新选择任务或模型，不接管 Provider 自带系统提示 |
| Attempt、重试与输出提交 | 准确请求、预算及实际执行事实 | Execution 管理生命周期和技术完成条件；命令非零/正常权限拒绝不是自动的整体失败；任务 owner 判断业务是否通过 |
| 日志、恢复与查询 | 宿主明确提供存储与授权；普通自测不需要 PG | Ledger 保存事实，Durability 按既有记录协调恢复，Inspection 只读呈现；无持久存储时返回本次完整记录，不承诺进程结束后恢复 |

调用顺序：任务 source → Registry 的 Module/Workflow → Execution.prepare 的具体配置 →
Invocation 的 CLI/工具调用 → Execution 接受技术结果 → Ledger/Inspection 返回事实 → 任务 owner 校验业务结果。

完整参数在本页的 Module、ModuleReviewer、ModuleExecutionRequirements、ExecutionProfileRelease、
ExecutionVariantPolicyRelease、prepare_local_workflow_module、evaluate_local_workflow_module 和 CLI 章节中，
由各自真实定义导出。ModuleReviewer 的默认字段也直接取自该类，不从历史 ReviewerDefaults 猜测当前配置。

Runtime CLI 负责 setup、注册、执行和查询。Portable CLI 负责准备审核对象并校验审核结果；宿主接入只
提供资源与调用入口，不复制 Adapter、模型选择或日志解析。注册定义与本次执行配置分开保存。
"""

from dataclasses import asdict, replace
import importlib.metadata
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from ..contracts.registry_release_definition import (
    ExecutionVariantPolicyRelease, ModuleExecutionPurpose, WorkflowNodeKind,
    ExecutionProfileRelease, ModuleExecutionRequirements,
)
from ..registry.registry_local_persistence import (
    LoadedRuntimeRegistration, _closure, load_runtime_registration,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..registry.registry_release_compilation import (
    ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
    compile_execution_variant_policy_release, runtime_owned_policy_schema_assets,
    ExecutionProfileReleaseSpec, compile_execution_profile_release, content_version,
)
from .execution_module_invocation import (
    _assert_admitted_test_evaluation_profile, _prepare_registered_workflow_module,
    _assert_registered_module_adapter,
    AgentExecutionAdapterRegistry, run_registered_workflow_module, run_workflow_module,
)
from .execution_content_staging import InMemoryCellArtifactStore
from .execution_self_test_binding import ModuleSelfTestResources
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger


def _execution_profile_for_requirements(
    requirements: ModuleExecutionRequirements, *, transport_kind: str | None = None,
    model_id: str | None = None, reasoning_profile: str | None = None,
) -> ExecutionProfileRelease:
    """Derive one current invocation Profile from frozen general requirements.

    Model selection happens here, independently of task definition and saved
    historical bindings. None uses claude_cli, claude-opus-5[1m], xhigh; explicit
    empty/unsupported values fail. codex_cli requires explicit model and effort;
    its Adapter validates supported requirements. No executable/resources opened.
    """
    requirements.validate()
    transport = "claude_cli" if transport_kind is None else transport_kind
    if transport == "claude_cli":
        from ..invocation.invocation_claude_cli_execution import _execution_expectation, _CURRENT_BINDING
        model = "claude-opus-5[1m]" if model_id is None else model_id
        effort = "xhigh" if reasoning_profile is None else reasoning_profile
        adapter_id, revision = _CURRENT_BINDING
        provider = "anthropic"
        defaults = "v1" if model_id is None and reasoning_profile is None else None
    elif transport == "codex_cli":
        if not model_id or not reasoning_profile:
            raise ValueError("codex_cli requires explicit model_id and reasoning_profile; no Claude defaults apply")
        from ..invocation.invocation_codex_module_invocation import _execution_expectation, CodexCliModuleExecutor
        model, effort, provider = model_id, reasoning_profile, "openai"
        adapter_id, revision = CodexCliModuleExecutor.executor_adapter_id, CodexCliModuleExecutor.executor_adapter_revision
        defaults = None
    else:
        raise ValueError(f"Unsupported model transport: {transport_kind}; no automatic fallback")
    spec = ExecutionProfileReleaseSpec(
        execution_profile_id=transport,
        executor_adapter_id=adapter_id, executor_adapter_revision=revision,
        transport_kind=transport, provider_id=provider, model_id=model, reasoning_profile=effort,
        **{name: getattr(requirements, name) for name in ModuleExecutionRequirements._profile_fields},
        model_defaults_version=defaults,
    )
    profile = compile_execution_profile_release(replace(spec, release_version=content_version(asdict(spec))))
    requirements.assert_profile(profile)
    _execution_expectation(profile)
    return profile


def prepare_local_workflow_module(
    root: Path, workflow_id: str, *, version: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, release_store=None,
) -> tuple[LoadedRuntimeRegistration, ExecutionVariantPolicyRelease]:
    """Resolve a fixed single-node Module Workflow and this invocation's model.

    Args:
        root: Registered host root. Only saved definitions are read; this is
            neither a model choice nor permission to read the whole project.
        workflow_id: Saved Workflow object ID.
        version: Exact definition version, or None for the latest registered
            new definition. Resolved once before preparing exact releases.
        transport_kind: Independent model transport; None uses Runtime's model
            preset. codex_cli supports its admitted tool-free requirements and
            requires explicit model_id and reasoning_profile.
            Model names are never used to infer another transport or provider.
        model_id: Independent model override; None uses claude-opus-5[1m] for
            claude_cli. codex_cli requires an explicit concrete model.
        reasoning_profile: Independent effort override; None uses xhigh for
            claude_cli. codex_cli requires an explicit supported effort.
            Omitted model fields use the Runtime preset, never saved bindings.
            Tools/network/workspace/budgets come from frozen Module requirements.
        release_store: Optional explicit existing store providing register_bundle
            and load_release_registry, such as PostgresRuntimeReleaseStore.
            Its schema must already be ready. Writes and verifies exact releases
            for durable execution, with no local-file update. Shared stores
            require readers that understand the selected release format.
    Returns:
        A pair (LoadedRuntimeRegistration, ExecutionVariantPolicyRelease).
        The first contains the fixed Workflow and the exact invocation Registry;
        the second selects this invocation's Profile at its Module node.
        Build host adapters/authorization using this Registry and Profile, then
        pass this same Workflow/Registry/Variant to run_registered_workflow_module.
        Do not reload the root or resolve the model again before invoking.
    Raises:
        FileNotFoundError: Missing saved Workflow/version.
        ValueError: Invalid saved data, unsupported transport, missing fixed
            Module requirements, unsupported graph or Profile/Module boundary.
        ModuleAuthoringError: Native Registry compatibility failure where raised.
        Exception: Store failures retain their native contract. No model has
            run when preparation fails; prior store writes may have committed.
    Effects:
        Does not load authoring source, call a provider, write .runtime, change
        defaults, create credentials/schemas or issue authorization. Existing
        saved Profile/Variant records are excluded from new model selection.
        Without release_store only the in-memory closure is prepared; this is
        not proof that exact releases were durably saved. The execution kernel
        records inputs, actual configuration, outputs and failures in its Ledger.
        History is queried by execution ID, not by this preparation function.
    """
    saved = load_runtime_registration(root, "workflow", workflow_id, version)
    workflow = saved.release
    if (len(workflow.nodes) != 1 or workflow.nodes[0].node_kind is not WorkflowNodeKind.MODULE
            or workflow.initial_node_id != workflow.nodes[0].node_id):
        raise ValueError("local preparation requires one Workflow Module entry node")
    node = workflow.nodes[0]
    module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
    requirements = module.get_execution_requirements()
    if requirements is None:
        raise ValueError("Independent model preparation requires frozen Module execution requirements; historical Profiles are not defaults")
    saved.registry.assert_module_execution_allowed(module, ModuleExecutionPurpose.EVALUATION)
    saved.registry.assert_workflow_execution_allowed(workflow, ModuleExecutionPurpose.EVALUATION)
    fixed = _closure(saved.registry, workflow, supplied_variants=())
    profile = _execution_profile_for_requirements(requirements, transport_kind=transport_kind,
        model_id=model_id, reasoning_profile=reasoning_profile)
    try:
        _assert_admitted_test_evaluation_profile(module, profile)
    except NotImplementedError as exc:
        raise ValueError(str(exc)) from exc
    variant = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id=workflow.workflow_id + "_variant",
        policy_version=content_version({"workflow": workflow.release_sha256, "profile": profile.release_sha256}),
        origin_kind="workflow", origin_release_ref=workflow.release_ref,
        origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate(node.node_id, profile.release_ref, profile.release_sha256),),
    ))
    schemas = {item.release_ref: item for item in fixed.schema_assets}
    schema = next(item for item in runtime_owned_policy_schema_assets()
                  if item.release_ref == variant.policy_schema_ref
                  and item.schema_sha256 == variant.policy_schema_sha256)
    schemas[schema.release_ref] = schema
    bundle = replace(fixed, schema_assets=tuple(schemas.values()),
                     execution_profiles=(profile,), execution_variant_policies=(variant,))
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(bundle)
    if release_store is not None:
        if not callable(getattr(release_store, "load_release_registry", None)):
            raise ValueError("release_store must provide exact fresh Registry readback")
        release_store.register_bundle(bundle)
        restored = release_store.load_release_registry()
        actual = _closure(restored, restored.get_workflow(workflow.release_ref, workflow.release_sha256),
                          supplied_variants=(restored.get_execution_variant_policy(
                              variant.release_ref, variant.release_sha256),))
        if actual.as_dict() != bundle.as_dict():
            raise ValueError("Stored execution preparation differs from selected exact releases")
        # Do not let unrelated shared-store selections enter this invocation.
        registry = RuntimeReleaseRegistry()
        registry.register_bundle(actual)
    return LoadedRuntimeRegistration(workflow, registry), variant


def evaluate_local_workflow_module(
    root: Path, workflow_id: str, *, input_payload: dict, version: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, cli_path: Path | str | None = None,
    material_root: Path | None = None, material_files: tuple[dict, ...] = (),
    read_only_dependencies: tuple[Path, ...] | None = None, commands: tuple[dict, ...] = (),
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
    from ..ledger.ledger_execution_logging import read_execution_log
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
        adapters = AgentExecutionAdapterRegistry()
        adapters.register(adapter)
        request, module, profile, _, _ = _prepare_registered_workflow_module(
            module_id=module.module_id, input_payload=input_payload, idempotency_key="self_test_"+uuid.uuid4().hex,
            release_registry=saved.registry, workflow=workflow, variant_policy=selection,
            artifact_host=artifacts, local_resources=resource_body)
        _assert_registered_module_adapter(module, profile, adapters)
        resources = ModuleSelfTestResources(request=request, workflow=workflow, variant=selection,
            registry=saved.registry, adapter=adapter, artifact_host=artifacts, ledger=ledger, workspace_root=workspace)
        try:
            result = run_workflow_module(request, release_registry=saved.registry, adapters=adapters,
                artifact_host=artifacts, ledger=ledger, self_test=resources)
            attempt = result.attempts[-1]
            def content(ref, digest):
                return json.loads(artifacts.read_bytes(ref, digest))
            output = None
            if attempt.status == "completed":
                if len(result.outputs) != 1:
                    raise ValueError("Module evaluation requires one schema-valid output")
                item = result.outputs[0]
                output = content(item.output_ref, item.output_sha256)
            return {"module_release_ref": module.release_ref, "module_release_sha256": module.release_sha256,
                "workflow_release_ref": workflow.release_ref, "workflow_release_sha256": workflow.release_sha256,
                "execution_profile_ref": profile.release_ref, "execution_profile_sha256": profile.release_sha256,
                "execution_variant_ref": selection.release_ref, "execution_variant_sha256": selection.release_sha256,
                "workflow_execution_id": request.workflow_execution_id, "module_run_id": result.module_run.module_run_id,
                "attempt_id": attempt.attempt_id, "model": profile.model_id, "effort": profile.reasoning_profile,
                "input_bindings": [asdict(item) for item in request.inputs],
                "input_closure_sha256": request.input_closure_sha256,
                "runtime_version": importlib.metadata.version("agent-runtime-core"),
                "execution": "run_workflow_module", "managed_runtime": True, "persistence": "not_requested",
                "status": attempt.status, "output": output, "failure_class": attempt.failure_class,
                "failure_detail": content(attempt.failure_detail_ref, attempt.failure_detail_sha256)
                    if attempt.failure_detail_ref is not None else None,
                "usage": attempt.usage.as_dict(), "execution_trace": asdict(result),
                "execution_log": read_execution_log(result.module_run, attempts=result.attempts, read_content=artifacts.read_bytes,
                                                     include_private_content=True),
                "self_test_binding": json.loads(resources._body),
                "provider_trace": content(attempt.provider_trace_ref, attempt.provider_trace_sha256)
                    if attempt.provider_trace_ref is not None else None}
        finally:
            resources.close()


def run_local_workflow_module(root: Path, workflow_id: str, *, version: str | None = None,
                              input_payload: dict, idempotency_key: str, **host):
    """Legacy entry: execute a single-node Workflow's explicitly saved binding.

    Retains the existing saved-binding behavior for old callers. New source
    registrations are definition-only: use prepare_local_workflow_module and
    the existing run_registered_workflow_module kernel for new invocations.

    Args:
        root: Host root containing the registered workflow folder.
        workflow_id: Workflow object ID, including for a one-Module graph.
        version: Exact saved version, or None for the latest registered version.
        input_payload: This invocation's input, checked by its registered schema.
        idempotency_key: The host's logical request key for the existing replay rule.
        host: Existing run_registered_workflow_module live keyword ports: adapters,
            artifact_host, authorize, context_client, operation_client,
            enforcing_gateway_id, environment_id, record_store, content_store,
            claim_token_secret and optional clock. These are host resources,
            not serialized definitions or a new environment configuration service.
    Returns:
        The original ModuleRunResult with its execution/output evidence.
    Raises:
        FileNotFoundError: Requested saved definition is absent. ValueError:
            Invalid saved data, not a single-node graph, or no unique saved
            Workflow Variant. Other execution/authorization errors are unchanged.
    Effects:
        Loads exact saved Workflow, Module and binding; never recompiles source,
        re-registers persistent data, changes active or creates host credentials.
        Provider calls and recording use the original Runtime execution kernel.
        Multi-node graphs can be saved/loaded, but use their existing graph runner.
    """
    saved = load_runtime_registration(root, "workflow", workflow_id, version)
    workflow = saved.release
    if len(workflow.nodes) != 1 or workflow.nodes[0].module_release_ref is None:
        raise ValueError("local Module invocation requires one Workflow Module node")
    variants = [record for record in saved.registry.snapshot().execution_variant_policies
                if record.policy_document()["origin_kind"] == "workflow"
                and record.policy_document()["origin_release_ref"] == workflow.release_ref
                and record.policy_document()["origin_release_sha256"] == workflow.release_sha256]
    if len(variants) != 1:
        raise ValueError("saved Workflow requires one unambiguous execution binding")
    node = workflow.nodes[0]
    module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
    return run_registered_workflow_module(release_registry=saved.registry, workflow=workflow,
        module_id=module.module_id, variant_policy=variants[0], input_payload=input_payload,
        idempotency_key=idempotency_key, **host)
