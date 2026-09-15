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
| Provider 与工具调用 | 上一步已经固定的 Profile 和明确资源 | Invocation 的 Adapter 组装 CLI、执行声明资源接口，取得基本 metadata、最终结果并归档原始流；不分析工具日志判断整次行为 |
| Attempt、重试与输出提交 | 准确请求、预算及实际执行事实 | Execution 管理生命周期和技术完成条件；命令非零/正常权限拒绝不是自动的整体失败；任务 owner 判断业务是否通过 |
| 日志、恢复与查询 | 宿主明确提供存储与授权；普通自测不需要 PG | Ledger 保留全部 Attempt 原始事实，Durability 按既有记录协调恢复；Inspection 仅在明确查询时解析详细视图，不改运行状态 |

调用顺序：任务 source → Registry 的 Module/Workflow → Execution.prepare 的具体配置 →
Invocation 的 CLI/工具调用 → Execution 接受技术结果 → Ledger 返回原始事实。
显式 Evaluation 再组合 Inspection 取得详细日志，由任务 owner 判断业务结果；无持久存储时在清理前
返回当次原文，不承诺进程结束后恢复。

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
    return _prepare_loaded_workflow(saved, transport_kind=transport_kind, model_id=model_id,
        reasoning_profile=reasoning_profile, release_store=release_store)


def prepare_local_workflow(
    root: Path, workflow_id: str, *, version: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, release_store=None,
) -> tuple[LoadedRuntimeRegistration, ExecutionVariantPolicyRelease]:
    """Prepare every Agent node through the same model conversion as one node.

    Arguments and store effects follow prepare_local_workflow_module. Reads the
    root/latest selection once, preserves the exact graph and creates bindings
    only for its Module nodes. Each node must have frozen execution requirements;
    zero-operation deterministic nodes use existing lower-level explicit
    Profile/Adapter assembly, not a fabricated model binding. No provider call,
    registration-source reload, production Gateway support or .runtime write.
    Returns the same (LoadedRuntimeRegistration, exact Variant Policy) pair.
    """
    saved = load_runtime_registration(root, "workflow", workflow_id, version)
    return _prepare_loaded_workflow(saved, transport_kind=transport_kind, model_id=model_id,
        reasoning_profile=reasoning_profile, release_store=release_store)


def _prepare_loaded_workflow(saved, *, transport_kind, model_id, reasoning_profile, release_store):
    workflow = saved.release
    saved.registry.assert_workflow_execution_allowed(workflow, ModuleExecutionPurpose.EVALUATION)
    fixed = _closure(saved.registry, workflow, supplied_variants=())
    profiles, bindings = {}, []
    for node in workflow.nodes:
        if node.node_kind is not WorkflowNodeKind.MODULE:
            continue
        module = saved.registry.get_module(node.module_release_ref, node.module_release_sha256)
        requirements = module.get_execution_requirements()
        if requirements is None:
            raise ValueError("Independent model preparation requires frozen Module execution requirements; historical Profiles are not defaults")
        saved.registry.assert_module_execution_allowed(module, ModuleExecutionPurpose.EVALUATION)
        profile = _execution_profile_for_requirements(requirements, transport_kind=transport_kind,
            model_id=model_id, reasoning_profile=reasoning_profile)
        try:
            _assert_admitted_test_evaluation_profile(module, profile)
        except NotImplementedError as exc:
            raise ValueError(str(exc)) from exc
        profiles[profile.release_ref] = profile
        bindings.append(ExecutionVariantProfileBindingCandidate(node.node_id, profile.release_ref, profile.release_sha256))
    if not bindings:
        raise ValueError("model preparation requires at least one Agent Module node")
    identity = ({"workflow": workflow.release_sha256, "profile": profile.release_sha256} if len(bindings) == 1 else
                {"workflow": workflow.release_sha256, "profiles": [asdict(item) for item in bindings]})
    variant = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id=workflow.workflow_id + "_variant",
        policy_version=content_version(identity),
        origin_kind="workflow", origin_release_ref=workflow.release_ref,
        origin_release_sha256=workflow.release_sha256,
        bindings=tuple(bindings),
    ))
    schemas = {item.release_ref: item for item in fixed.schema_assets}
    schema = next(item for item in runtime_owned_policy_schema_assets()
                  if item.release_ref == variant.policy_schema_ref
                  and item.schema_sha256 == variant.policy_schema_sha256)
    schemas[schema.release_ref] = schema
    bundle = replace(fixed, schema_assets=tuple(schemas.values()),
                     execution_profiles=tuple(profiles.values()), execution_variant_policies=(variant,))
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


def _run_prepared_workflow_node(
    *, registry, workflow, selection, input_payload, idempotency_key,
    artifact_host, ledger, workspace_root, adapter, node_id=None,
    workflow_execution_id=None, dispatch_id=None, module_run_id=None,
    workflow_resources=None, local_resources=None, attempt_ordinal=1, parent_attempt_id=None,
    tool_session_factory=None, user_cancel_requested=None, resource_cancel_requested=None,
):
    """Execute an exact prepared node using shared stores and a private workspace.

    The graph host supplies all four node/execution/dispatch/run identities and
    live workflow_resources. That parent must implement require_active() and
    check_node_scope(request, workflow, variant, registry, artifact_host, ledger,
    workspace_root) as keyword arguments, checking the admitted dispatch and
    exact shared objects. No root lookup, model selection or registration occurs.
    Omit all identities only for the existing single-node convenience entry.

    Returns (ModuleRunResult, JSON-compatible facts with every Attempt's raw
    archive). execution_log preserves provider content and actual Gateway facts;
    complete is None until Inspection is explicitly requested. No native-tool
    parsing or behavior evaluation occurs here.
    Closes this node's resource binding after capturing its archive; the caller
    owns the workspace and shared-store lifecycle. No PG or production grant.
    """
    from ..ledger.ledger_execution_logging import _read_execution_archive
    from ..invocation.invocation_tool_definition import tool_definition_records

    for name, callback in (("user_cancel_requested", user_cancel_requested), ("resource_cancel_requested", resource_cancel_requested)):
        if callback is not None and not callable(callback):
            raise ValueError(name + " must be a trusted callable or None")

    if workflow_resources is not None:
        workflow_resources.require_active()
    target = workflow.initial_node_id if node_id is None else node_id
    nodes = [item for item in workflow.nodes if item.node_id == target]
    if len(nodes) != 1:
        raise ValueError("prepared execution must select an exact Workflow node")
    module = registry.get_module(nodes[0].module_release_ref, nodes[0].module_release_sha256)
    definitions = tool_definition_records(tool_session_factory.definitions) if tool_session_factory is not None else []
    binding = next(item for item in selection.policy_document()["bindings"] if item["position_id"] == target)
    selected_profile = registry.get_execution_profile(binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
    callbacks = set(selected_profile.tool_policy) - {"read", "search", "shell"}
    if {item["name"] for item in definitions} != callbacks:
        raise ValueError("callback definitions must match the exact non-native Profile tools")
    if tool_session_factory is not None and not callbacks:
        raise ValueError("unused callback factory is not permitted")
    if tool_session_factory is not None:
        adapter.bind_tool_session_factory(tool_session_factory, definitions)
    request, module, profile, _, _ = _prepare_registered_workflow_module(
        module_id=module.module_id, input_payload=input_payload, idempotency_key=idempotency_key,
        release_registry=registry, workflow=workflow, variant_policy=selection,
        artifact_host=artifact_host, local_resources=local_resources,
        workflow_node_id=node_id, workflow_execution_id=workflow_execution_id,
        dispatch_id=dispatch_id, module_run_id=module_run_id,
        attempt_ordinal=attempt_ordinal, parent_attempt_id=parent_attempt_id, tool_definitions=definitions)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    _assert_registered_module_adapter(module, profile, adapters)
    resources = ModuleSelfTestResources(request=request, workflow=workflow, variant=selection,
        registry=registry, adapter=adapter, artifact_host=artifact_host, ledger=ledger,
        workspace_root=workspace_root, workflow_resources=workflow_resources,
        tool_session_factory=tool_session_factory, tool_definitions=definitions,
        user_cancel_requested=user_cancel_requested, resource_cancel_requested=resource_cancel_requested)
    try:
        result = run_workflow_module(request, release_registry=registry, adapters=adapters,
            artifact_host=artifact_host, ledger=ledger, self_test=resources)
        attempt = result.attempts[-1]
        def content(ref, digest):
            return json.loads(artifact_host.read_bytes(ref, digest))
        output = None
        if attempt.status == "completed":
            if len(result.outputs) != 1:
                raise ValueError("Module evaluation requires one schema-valid output")
            item = result.outputs[0]
            output = content(item.output_ref, item.output_sha256)
        record = {"module_release_ref": module.release_ref, "module_release_sha256": module.release_sha256,
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
                "execution_log": _read_execution_archive(result.module_run, attempts=result.attempts, read_content=artifact_host.read_bytes,
                                                     include_private_content=True),
                "self_test_binding": json.loads(resources._body),
                "provider_trace": content(attempt.provider_trace_ref, attempt.provider_trace_sha256)
                    if attempt.provider_trace_ref is not None else None}
        return result, record
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
