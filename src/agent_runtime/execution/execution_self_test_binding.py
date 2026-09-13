"""Request-scoped temporary resources for trusted Runtime evaluation hosts.

This is a process-local resource boundary, not a Product permission, reusable
environment session, or persistent execution record. Task JSON cannot supply it.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from ..contracts.execution_module_definition import WorkflowModuleExecutionRequest
from ..contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from ..contracts.registry_release_definition import ModuleExecutionPurpose, MODEL_INVOCATION_OPERATION_IDS
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from .execution_content_staging import InMemoryCellArtifactStore


class ModuleSelfTestResources:
    """Bind one Workflow request to actual temporary stores and one Adapter.

    The trusted test host creates this object after normal registered request
    preparation. Closing it serializes with finalization on the same in-memory
    Ledger. References in an Adapter request must resolve to this live object;
    knowledge of their bytes alone does not confer access.
    """

    def __init__(self, *, request, workflow, variant, registry, adapter,
                 artifact_host, ledger, workspace_root):
        if type(request) is not WorkflowModuleExecutionRequest:
            raise ValueError("self-test requires a Workflow Module request")
        request.validate()
        if request.purpose not in {ModuleExecutionPurpose.TEST, ModuleExecutionPurpose.EVALUATION}:
            raise PermissionError("self-test cannot execute a production purpose")
        if type(artifact_host) is not InMemoryCellArtifactStore or type(ledger) is not InMemoryModuleExecutionLedger:
            raise ValueError("self-test requires its temporary artifact store and memory Ledger")
        if len(request.variants) != 1 or request.attempt_ordinal != 1:
            raise ValueError("temporary evaluation accepts one initial Variant/Attempt")
        module = registry.get_module(request.module_release_ref, request.module_release_sha256)
        if len(module.declared_operation_ids) != 1 or module.declared_operation_ids[0] not in MODEL_INVOCATION_OPERATION_IDS:
            raise PermissionError("self-test does not expose external Gateway operations")
        selected = request.variants[0]
        profile = registry.get_execution_profile(selected.execution_profile_ref, selected.execution_profile_sha256)
        profile.validate()
        requirements = module.get_execution_requirements()
        if requirements is not None:
            requirements.assert_profile(profile)
        if (profile.network_policy != "denied"
                or profile.gateway_access_reasons or profile.semantic_input_delivery_mode != "inline"
                or profile.execution_mode not in {"agent", "tool_free"}
                or profile.attempt_workspace_policy not in {"none", "own_draft_read_write"}):
            raise PermissionError("Profile is not admitted for temporary inline evaluation")
        if (registry.get_workflow(workflow.release_ref, workflow.release_sha256) != workflow
                or registry.get_execution_variant_policy(variant.release_ref, variant.release_sha256) != variant
                or len(workflow.nodes) != 1 or workflow.initial_node_id != request.workflow_node_id):
            raise ValueError("self-test Workflow closure mismatch")
        node = workflow.nodes[0]
        if (node.module_release_ref, node.module_release_sha256) != (module.release_ref, module.release_sha256):
            raise ValueError("self-test Module differs from Workflow node")
        policy = variant.policy_document()
        if policy != {"origin_kind": "workflow", "origin_release_ref": workflow.release_ref,
                      "origin_release_sha256": workflow.release_sha256, "bindings": [{
                          "position_id": request.workflow_node_id,
                          "execution_profile_release_ref": profile.release_ref,
                          "execution_profile_release_sha256": profile.release_sha256}]}:
            raise ValueError("self-test Variant differs from Workflow selection")
        self._request = request
        self._profile = profile
        self._registry = registry
        self._adapter = adapter
        self._artifacts = artifact_host
        self._ledger = ledger
        self._workspace = Path(workspace_root).resolve(strict=True)
        from ..invocation.invocation_local_resource_preparation import (
            LOCAL_RESOURCES_SCHEMA_REF, LOCAL_RESOURCES_SCHEMA_SHA256,
            LOCAL_RESOURCES_MEDIA_TYPE, LOCAL_RESOURCES_LOGICAL_NAME, validate_local_resources,
        )
        resource_inputs = [item for item in request.inputs if item.schema_ref == LOCAL_RESOURCES_SCHEMA_REF]
        if len(resource_inputs) > 1:
            raise ValueError("self-test must bind one local resources input at most")
        self._read_only_dependencies = ()
        if resource_inputs:
            resource, = resource_inputs
            if (resource.schema_sha256, resource.media_type, resource.logical_name) != (
                    LOCAL_RESOURCES_SCHEMA_SHA256, LOCAL_RESOURCES_MEDIA_TYPE, LOCAL_RESOURCES_LOGICAL_NAME):
                raise ValueError("self-test local resources input has a different type")
            data = artifact_host.read_bytes(resource.input_ref, resource.input_sha256)
            if hashlib.sha256(data).hexdigest() != resource.input_sha256:
                raise ValueError("self-test local resources content changed")
            parsed = validate_local_resources(profile=profile, body=data)
            self._read_only_dependencies = tuple(Path(item) for item in parsed["read_only_dependencies"])
        self._active = True
        body = json.dumps({"request_sha256": request.request_sha256,
            "workflow_release_ref": workflow.release_ref, "workflow_release_sha256": workflow.release_sha256,
            "variant_release_ref": variant.release_ref, "variant_release_sha256": variant.release_sha256,
            "adapter": asdict(adapter.descriptor), "workspace_root": str(self._workspace),
            "record_store": "temporary_memory", "artifact_store": "temporary_memory"},
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        self._body = body
        self.binding_sha256 = hashlib.sha256(body).hexdigest()
        ref = artifact_host.put_bytes(artifact_kind_id="self_test_binding", schema_version="v1",
            schema_ref="schema:self_test_binding@v1", schema_sha256=hashlib.sha256(b"self_test_binding_v1").hexdigest(),
            media_type="application/json", content=body, idempotency_key=request.request_id+"_test_resources")
        self.binding_ref = ref.artifact_ref

    def require_active(self):
        if not self._active or not self._workspace.is_dir():
            raise SelfTestResourceUnavailableError("self-test resources are closed or unavailable")
        if self._artifacts.read_bytes(self.binding_ref, self.binding_sha256) != self._body:
            raise SelfTestResourceUnavailableError("self-test resource evidence changed")

    def check_scope(self, *, request, registry, adapters, artifact_host, ledger):
        self.require_active()
        if (request != self._request or registry is not self._registry
                or artifact_host is not self._artifacts or ledger is not self._ledger
                or adapters.resolve(self._profile.executor_adapter_id, self._profile.executor_adapter_revision) is not self._adapter):
            raise SelfTestResourceUnavailableError("self-test resources belong to another execution")

    def check_invocation(self, request, *, adapter, artifact_host, workspace_root, read_only_dependencies=()):
        self.require_active()
        source = self._request
        selected = source.variants[0]
        expected_inputs = tuple((item.logical_name, item.input_ref, item.input_sha256,
            item.schema_ref, item.schema_sha256, item.media_type) for item in source.inputs)
        actual_inputs = tuple((item.logical_name, item.input_ref, item.input_sha256,
            item.schema_ref, item.schema_sha256, item.media_type) for item in request.authorized_inputs)
        if (request.self_test_binding_ref != self.binding_ref or request.self_test_binding_sha256 != self.binding_sha256
                or request.has_operation_evidence or request.workflow_execution_id != source.workflow_execution_id
                or request.module_run_id != source.module_run_id
                or request.module_release_ref != source.module_release_ref
                or request.module_release_sha256 != source.module_release_sha256
                or request.execution_profile_ref != selected.execution_profile_ref
                or request.execution_profile_sha256 != selected.execution_profile_sha256
                or request.prompt_envelope_ref != selected.prompt_envelope_ref
                or request.prompt_envelope_sha256 != selected.prompt_envelope_sha256
                or request.input_closure_sha256 != source.input_closure_sha256
                or expected_inputs != actual_inputs or adapter is not self._adapter
                or artifact_host is not self._artifacts or Path(workspace_root).resolve() != self._workspace
                or tuple(Path(item).resolve() for item in read_only_dependencies) != self._read_only_dependencies):
            raise SelfTestResourceUnavailableError("self-test invocation differs from its live resource binding")

    def guarded(self, operation):
        """Order a bounded input read, process launch or finalization with close."""
        def guarded():
            self.require_active()
            return operation()
        return self._ledger.guarded(guarded)

    def close(self):
        def close():
            self._active = False
        self._ledger.guarded(close)
