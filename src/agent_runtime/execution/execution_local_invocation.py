"""Load saved definitions before entering the existing recorded evaluation API."""

from pathlib import Path

from ..registry.registry_local_persistence import load_runtime_registration
from .execution_module_invocation import run_registered_workflow_module


def run_local_workflow_module(root: Path, workflow_id: str, *, version: str | None = None,
                              input_payload: dict, idempotency_key: str, **host):
    """Run a saved single-node Workflow through the existing Evaluation entry.

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
