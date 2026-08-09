from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_runtime.registry.registry_module_exporting import (
    load_skill_runtime_module_exports,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseSpec,
    compile_agent_module_release,
    managed_skill_projection,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)
from agent_runtime.contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionRequest,
)
from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.invocation.invocation_context_preparation import (
    InvocationExecutionExpectation,
    prepare_registered_invocation_context,
)


def test_skill_package_id_is_validated_before_path_resolution(tmp_path: Path) -> None:
    escaped_root = tmp_path / "escaped" / "runtime_modules"
    escaped_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="invalid skill_id"):
        load_skill_runtime_module_exports(
            tmp_path,
            skill_id="../../escaped",
        )


def test_module_compiler_uses_only_fixed_module_export_files(tmp_path: Path) -> None:
    owner = tmp_path / "designDoc" / "owner.md"
    owner.parent.mkdir()
    owner.write_text("# Owner\n", encoding="utf-8")
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    for name in ("input", "output"):
        schema_ref = f"schema:synthetic_{name}@v1"
        (schema_root / f"{name}.schema.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": schema_ref,
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )
    module_root = (
        tmp_path
        / ".claude"
        / "skills"
        / "synthetic-skill"
        / "runtime_modules"
        / "synthetic_module"
    )
    module_root.mkdir(parents=True)
    prompt_path = module_root / "prompt.md"
    prompt_path.write_text("Produce the synthetic result.\n", encoding="utf-8")
    registration = {
        "schema_version": "runtime_module_registration_v1",
        "skill_package_id": "synthetic_skill_package",
        "skill_id": "synthetic-skill",
        "export_id": "synthetic_module",
        "module_id": "synthetic_module",
        "owner_contract_path": "designDoc/owner.md",
        "input_schema_ref": "schema:synthetic_input@v1",
        "input_schema_path": "schemas/input.schema.json",
        "output_schema_ref": "schema:synthetic_output@v1",
        "output_schema_path": "schemas/output.schema.json",
        "declared_operation_ids": ["invoke_model"],
        "compatible_transport_kinds": ["codex_cli"],
        "context_policy_ref": "context-policy:workflow_execution_isolated@v1",
        "evaluation_policy_ref": "evaluation-policy:synthetic@v1",
        "retry_policy_ref": "retry-policy:bounded@v1",
        "entry_policy": "workflow_bound",
        "output_resolution_policy": "evaluated_single",
    }
    (module_root / "module_registration.json").write_text(
        json.dumps(registration),
        encoding="utf-8",
    )
    compiled = compile_agent_module_release(
        tmp_path,
        AgentModuleReleaseSpec(
            module_id="synthetic_module",
            skill_id="synthetic-skill",
            skill_projection_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "synthetic_module/prompt.md"
            ),
            owner_contract_ref="repo-file:designDoc/owner.md",
            owner_contract_path="designDoc/owner.md",
            input_schema_ref="schema:synthetic_input@v1",
            output_schema_ref="schema:synthetic_output@v1",
            declared_operation_ids=("invoke_model",),
            execution_profile_id="synthetic_profile",
            executor_adapter_id="codex_cli",
            executor_adapter_revision="v1",
            transport_kind="codex_cli",
            provider_id="openai",
            model_id="synthetic_model",
            reasoning_profile="none",
            output_constraint_mode="prompt_only_json",
            timeout_seconds=60,
            input_schema_path="schemas/input.schema.json",
            output_schema_path="schemas/output.schema.json",
            compatible_transport_kinds=("codex_cli",),
            evaluation_policy_ref="evaluation-policy:synthetic@v1",
            retry_policy_ref="retry-policy:bounded@v1",
        ),
    )

    assert compiled.prompt_bundle.compiled_static_body.startswith(
        "Produce the synthetic result.\n"
    )
    assert tuple(asset.release_ref for asset in compiled.schema_assets) == (
        "schema:synthetic_input@v1",
        "schema:synthetic_output@v1",
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            skill_packages=(compiled.skill_package,),
            schema_assets=compiled.schema_assets,
            prompt_components=compiled.prompt_components,
            prompt_bundles=(compiled.prompt_bundle,),
            execution_profiles=(compiled.execution_profile,),
            modules=(compiled.module,),
        )
    )
    artifact_host = InMemoryCellArtifactStore()
    prompt_bytes = compiled.prompt_bundle.compiled_static_body.encode("utf-8")
    prompt_ref = artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=prompt_bytes,
        idempotency_key="prompt_envelope_synthetic",
    )
    prepared = prepare_registered_invocation_context(
        request=AuthorizedAgentExecutionRequest.build(
            workflow_execution_id=None,
            isolated_scope_ref="scope-ref:synthetic-001",
            isolated_scope_sha256="3" * 64,
            module_run_id="module_run_synthetic_001",
            variant_id="variant_synthetic_001",
            attempt_id="attempt_synthetic_001",
            module_id=compiled.module.module_id,
            module_release_ref=compiled.module.release_ref,
            module_release_sha256=compiled.module.release_sha256,
            execution_profile_id=compiled.execution_profile.execution_profile_id,
            execution_profile_ref=compiled.execution_profile.release_ref,
            execution_profile_sha256=compiled.execution_profile.release_sha256,
            attempt_begin_receipt_ref="attempt-begin:attempt_synthetic_001",
            attempt_begin_receipt_sha256="4" * 64,
            prompt_envelope_ref=prompt_ref.artifact_ref,
            prompt_envelope_sha256=prompt_ref.artifact_sha256,
            output_schema_ref=compiled.module.output_schema_ref,
            output_schema_sha256=compiled.module.output_schema_sha256,
            execution_authorization_binding_ref=(
                "runtime-authorization:binding_synthetic_001"
            ),
            execution_authorization_binding_sha256="5" * 64,
            protected_operation_intent_ref=(
                "runtime-authorization:intent_synthetic_001"
            ),
            protected_operation_intent_sha256="6" * 64,
            product_operation_decision_ref="product-decision:synthetic_001",
            product_operation_decision_sha256="7" * 64,
            gateway_authorization_observation_ref=(
                "runtime-authorization:observation_synthetic_001"
            ),
            gateway_authorization_observation_sha256="8" * 64,
            operation_grant_ref=None,
            operation_grant_sha256=None,
            grant_disposition_ref=None,
            input_closure_sha256=hashlib.sha256(b"[]").hexdigest(),
            data_use_purpose_id="module_test_execution",
            authorized_inputs=(),
            idempotency_key="idempotency_synthetic_001",
        ),
        release_registry=registry,
        artifact_host=artifact_host,
        expectation=InvocationExecutionExpectation(
            executor_adapter_id="codex_cli",
            executor_adapter_revision="v1",
            transport_kind="codex_cli",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            network_policy="denied",
            tool_policy=(),
        ),
    )
    assert prepared.prompt == compiled.prompt_bundle.compiled_static_body
    assert prepared.registered_output_schema["type"] == "object"
    skill_projection = module_root.parent.parent / "SKILL.md"
    skill_projection.write_text("## Task Instructions\nLegacy.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fixed prompt.md"):
        managed_skill_projection(skill_projection)
