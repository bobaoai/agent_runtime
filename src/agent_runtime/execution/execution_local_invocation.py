"""Prepare saved definitions for an independently selected model execution."""

from dataclasses import replace
from pathlib import Path

from ..contracts.registry_release_definition import (
    ExecutionVariantPolicyRelease, ModuleExecutionPurpose, WorkflowNodeKind,
)
from ..registry.registry_local_persistence import (
    LoadedRuntimeRegistration, _closure, load_runtime_registration,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..registry.registry_release_compilation import (
    ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
    compile_execution_variant_policy_release, runtime_owned_policy_schema_assets,
)
from ..registry.registry_reviewer_defaults import content_version, reviewer_execution_profile
from .execution_module_invocation import (
    _assert_admitted_test_evaluation_profile, run_registered_workflow_module,
)


def prepare_local_workflow_module(
    root: Path, workflow_id: str, *, version: str | None = None,
    transport_kind: str | None = None, model_id: str | None = None,
    reasoning_profile: str | None = None, release_store=None,
) -> tuple[LoadedRuntimeRegistration, ExecutionVariantPolicyRelease]:
    """Resolve a fixed single-node Reviewer Workflow and this invocation's model.

    Args:
        root: Registered host root. Only saved definitions are read; this is
            neither a model choice nor permission to read the whole project.
        workflow_id: Saved Workflow object ID.
        version: Exact definition version, or None for the latest registered
            new definition. Resolved once before preparing exact releases.
        transport_kind: Independent model transport; None uses Runtime's model
            preset. Currently only claude_cli is supported by this preparation.
            Model names are never used to infer another transport or provider.
        model_id: Independent model override; None uses claude-opus-5[1m].
        reasoning_profile: Independent effort override; None uses xhigh.
            Omitted model fields use the Runtime preset, never saved bindings.
            Tools/network/workspace/budgets come from frozen ReviewerDefaults.
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
            Reviewer capabilities, unsupported graph or Profile/Module boundary.
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
    if module.reviewer_defaults is None:
        raise ValueError("Independent model preparation requires frozen Reviewer capabilities")
    saved.registry.assert_module_execution_allowed(module, ModuleExecutionPurpose.EVALUATION)
    saved.registry.assert_workflow_execution_allowed(workflow, ModuleExecutionPurpose.EVALUATION)
    fixed = _closure(saved.registry, workflow, supplied_variants=())
    profile = reviewer_execution_profile(module.reviewer_defaults, transport_kind=transport_kind,
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
