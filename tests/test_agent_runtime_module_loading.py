from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from agent_runtime.registry.registry_module_loading import (
    load_project_runtime_module_registrations,
    load_runtime_module_registration,
    load_skill_runtime_module_registrations,
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


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_skill_id_is_validated_before_path_resolution(tmp_path: Path) -> None:
    escaped_root = tmp_path / "escaped" / "runtime_modules"
    escaped_root.mkdir(parents=True)

    with pytest.raises(ValueError, match="invalid skill_id"):
        load_skill_runtime_module_registrations(
            tmp_path,
            skill_id="../../escaped",
        )


def test_portable_governance_module_resolves_module_relative_schemas() -> None:
    registration = load_runtime_module_registration(
        REPO_ROOT,
        skill_id="the-contract-audit",
        module_id="design_contract_reviewer",
    )

    assert registration.input_schema_path == (
        ".claude/skills/the-contract-audit/runtime_modules/"
        "design_contract_reviewer/schemas/input.schema.json"
    )
    assert registration.output_schema_path == (
        ".claude/skills/the-contract-audit/runtime_modules/"
        "design_contract_reviewer/schemas/output.schema.json"
    )


def test_module_compiler_uses_only_fixed_module_registration_files(tmp_path: Path) -> None:
    owner = tmp_path / "designDoc" / "owner.md"
    owner.parent.mkdir()
    owner.write_text(
        "# Owner\n\nRegistered Module: `synthetic_module`.\n",
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
    schema_root = module_root / "schemas"
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
    skill_projection = module_root.parent.parent / "SKILL.md"
    skill_projection.write_text(
        "# Synthetic Skill\n\nManaged Module: `synthetic_module`.\n",
        encoding="utf-8",
    )
    prompt_path = module_root / "prompt.md"
    prompt_path.write_text("Produce the synthetic result.\n", encoding="utf-8")
    registration = {
        "schema_version": "runtime_module_registration_v2",
        "skill_id": "synthetic-skill",
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
            input_schema_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "synthetic_module/schemas/input.schema.json"
            ),
            output_schema_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "synthetic_module/schemas/output.schema.json"
            ),
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
    skill_projection.write_text("## Task Instructions\nLegacy.\n", encoding="utf-8")
    with pytest.raises(ValueError, match="fixed prompt.md"):
        managed_skill_projection(skill_projection)


def _write_minimal_module_registration(
    tmp_path: Path,
    *,
    skill_text: str | None,
    owner_text: str,
) -> None:
    owner = tmp_path / "designDoc" / "owner.md"
    owner.parent.mkdir()
    owner.write_text(owner_text, encoding="utf-8")
    skill_root = tmp_path / ".claude" / "skills" / "synthetic-skill"
    module_root = skill_root / "runtime_modules" / "synthetic_module"
    module_root.mkdir(parents=True)
    schema_root = module_root / "schemas"
    schema_root.mkdir()
    for name in ("input", "output"):
        (schema_root / f"{name}.schema.json").write_text("{}\n", encoding="utf-8")
    if skill_text is not None:
        (skill_root / "SKILL.md").write_text(skill_text, encoding="utf-8")
    (module_root / "prompt.md").write_text("Do the work.\n", encoding="utf-8")
    (module_root / "module_registration.json").write_text(
        json.dumps(
            {
                "schema_version": "runtime_module_registration_v2",
                "skill_id": "synthetic-skill",
                "module_id": "synthetic_module",
                "owner_contract_path": "designDoc/owner.md",
                "input_schema_ref": "schema:synthetic_input@v1",
                "input_schema_path": "schemas/input.schema.json",
                "output_schema_ref": "schema:synthetic_output@v1",
                "output_schema_path": "schemas/output.schema.json",
                "declared_operation_ids": ["invoke_model"],
                "compatible_transport_kinds": ["codex_cli"],
                "context_policy_ref": "context-policy:synthetic@v1",
                "evaluation_policy_ref": "evaluation-policy:synthetic@v1",
                "retry_policy_ref": "retry-policy:bounded@v1",
                "entry_policy": "workflow_bound",
                "output_resolution_policy": "evaluated_single",
            }
        ),
        encoding="utf-8",
    )


def test_module_registration_requires_skill_registration_projection(tmp_path: Path) -> None:
    _write_minimal_module_registration(
        tmp_path,
        skill_text=None,
        owner_text="# Owner\n\nModule: `synthetic_module`.\n",
    )

    with pytest.raises(ValueError, match="Skill registration projection is missing"):
        load_skill_runtime_module_registrations(tmp_path, skill_id="synthetic-skill")


def test_module_registration_requires_exact_module_id_in_skill(tmp_path: Path) -> None:
    _write_minimal_module_registration(
        tmp_path,
        skill_text="# Skill\n\nModule: `synthetic_module_v2`.\n",
        owner_text="# Owner\n\nModule: `synthetic_module`.\n",
    )

    with pytest.raises(
        ValueError,
        match="Skill registration projection does not declare",
    ):
        load_skill_runtime_module_registrations(tmp_path, skill_id="synthetic-skill")


def test_module_registration_requires_exact_module_id_in_owner_doc(tmp_path: Path) -> None:
    _write_minimal_module_registration(
        tmp_path,
        skill_text="# Skill\n\nModule: `synthetic_module`.\n",
        owner_text="# Owner\n\nModule: `synthetic_module_v2`.\n",
    )

    with pytest.raises(ValueError, match="owner Design Doc does not declare"):
        load_skill_runtime_module_registrations(tmp_path, skill_id="synthetic-skill")


def test_project_loader_discovers_closed_module_registrations(tmp_path: Path) -> None:
    _write_minimal_module_registration(
        tmp_path,
        skill_text="# Skill\n\nModule: `synthetic_module`.\n",
        owner_text="# Owner\n\nModule: `synthetic_module`.\n",
    )

    registrations = load_project_runtime_module_registrations(tmp_path)

    assert tuple(item.module_id for item in registrations) == ("synthetic_module",)


def _write_v2_module_registration(
    tmp_path: Path,
    *,
    module_id: str,
) -> None:
    module_owner_path = f"designDoc/{module_id}_owner.md"
    module_owner = tmp_path / module_owner_path
    module_owner.parent.mkdir(parents=True, exist_ok=True)
    module_owner.write_text(
        f"# Module Owner\n\nRuntime Module: `{module_id}`.\n",
        encoding="utf-8",
    )
    skill_root = tmp_path / ".claude" / "skills" / "synthetic-skill"
    skill_root.mkdir(parents=True, exist_ok=True)
    skill_path = skill_root / "SKILL.md"
    existing_skill = skill_path.read_text(encoding="utf-8") if skill_path.exists() else "# Skill\n"
    skill_path.write_text(
        existing_skill + f"\nRuntime Module: `{module_id}`.\n",
        encoding="utf-8",
    )
    module_root = skill_root / "runtime_modules" / module_id
    module_root.mkdir(parents=True)
    schema_root = module_root / "schemas"
    schema_root.mkdir()
    for direction in ("input", "output"):
        schema_ref = f"schema:{module_id}_{direction}@v1"
        (schema_root / f"{module_id}_{direction}.schema.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": schema_ref,
                    "type": "object",
                    "additionalProperties": False,
                }
            ),
            encoding="utf-8",
        )
    (module_root / "prompt.md").write_text(
        f"Execute {module_id}.\n",
        encoding="utf-8",
    )
    (module_root / "module_registration.json").write_text(
        json.dumps(
            {
                "schema_version": "runtime_module_registration_v2",
                "skill_id": "synthetic-skill",
                "module_id": module_id,
                "owner_contract_path": module_owner_path,
                "input_schema_ref": f"schema:{module_id}_input@v1",
                "input_schema_path": f"schemas/{module_id}_input.schema.json",
                "output_schema_ref": f"schema:{module_id}_output@v1",
                "output_schema_path": f"schemas/{module_id}_output.schema.json",
                "declared_operation_ids": ["invoke_model"],
                "compatible_transport_kinds": ["codex_cli"],
                "context_policy_ref": "context-policy:synthetic@v1",
                "evaluation_policy_ref": "evaluation-policy:synthetic@v1",
                "retry_policy_ref": "retry-policy:bounded@v1",
                "entry_policy": "workflow_bound",
                "output_resolution_policy": "evaluated_single",
            }
        ),
        encoding="utf-8",
    )


def test_one_skill_allows_distinct_module_owner_contracts(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    _write_v2_module_registration(tmp_path, module_id="module_beta")

    registrations = load_skill_runtime_module_registrations(
        tmp_path,
        skill_id="synthetic-skill",
    )

    assert {item.owner_contract_path for item in registrations} == {
        "designDoc/module_alpha_owner.md",
        "designDoc/module_beta_owner.md",
    }


def test_single_module_load_does_not_validate_sibling_module(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    _write_v2_module_registration(tmp_path, module_id="module_beta")
    sibling_manifest = (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/module_beta/"
        "module_registration.json"
    )
    sibling_manifest.write_text("not json", encoding="utf-8")

    registration = load_runtime_module_registration(
        tmp_path,
        skill_id="synthetic-skill",
        module_id="module_alpha",
    )

    assert registration.module_id == "module_alpha"


def test_v2_module_registration_accepts_exact_module_owned_schema_closure(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")

    registration = load_skill_runtime_module_registrations(
        tmp_path,
        skill_id="synthetic-skill",
    )[0]

    assert registration.input_schema_path.endswith(
        "schemas/module_alpha_input.schema.json"
    )
    assert registration.output_schema_path.endswith(
        "schemas/module_alpha_output.schema.json"
    )


def test_v2_module_registration_rejects_undeclared_module_owned_schema(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    owned_schema_root = (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/"
        "module_alpha/schemas"
    )
    (owned_schema_root / "unused.schema.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="schema closure differs"):
        load_skill_runtime_module_registrations(
            tmp_path,
            skill_id="synthetic-skill",
        )


def test_v2_module_registration_rejects_schema_path_outside_module(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    manifest_path = (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/module_alpha/"
        "module_registration.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["input_schema_path"] = "designDoc/not_module_owned.schema.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="Module schemas directory"):
        load_runtime_module_registration(
            tmp_path,
            skill_id="synthetic-skill",
            module_id="module_alpha",
        )


def test_v2_module_registration_rejects_symlinked_prompt(tmp_path: Path) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    prompt_path = (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/module_alpha/prompt.md"
    )
    external_prompt = tmp_path / "external_prompt.md"
    external_prompt.write_text("External prompt.\n", encoding="utf-8")
    prompt_path.unlink()
    prompt_path.symlink_to(external_prompt)

    with pytest.raises(ValueError, match="root cannot contain symlinks"):
        load_runtime_module_registration(
            tmp_path,
            skill_id="synthetic-skill",
            module_id="module_alpha",
        )


def test_v2_module_registration_rejects_symlinked_module_directory(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    module_root = (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/module_alpha"
    )
    external_module_root = tmp_path / "external_module_alpha"
    module_root.rename(external_module_root)
    module_root.symlink_to(external_module_root, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        load_runtime_module_registration(
            tmp_path,
            skill_id="synthetic-skill",
            module_id="module_alpha",
        )


def test_v2_module_registration_rejects_symlinked_runtime_modules_directory(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    runtime_modules_root = (
        tmp_path / ".claude/skills/synthetic-skill/runtime_modules"
    )
    external_root = tmp_path / "external_runtime_modules"
    runtime_modules_root.rename(external_root)
    runtime_modules_root.symlink_to(external_root, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        load_skill_runtime_module_registrations(
            tmp_path,
            skill_id="synthetic-skill",
        )


def test_project_loader_rejects_symlinked_skill_directory(tmp_path: Path) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    skill_root = tmp_path / ".claude/skills/synthetic-skill"
    external_root = tmp_path / "external_skill"
    skill_root.rename(external_root)
    skill_root.symlink_to(external_root, target_is_directory=True)

    with pytest.raises(ValueError, match="cannot contain symlinks"):
        load_project_runtime_module_registrations(tmp_path)


def test_v2_module_registration_rejects_symlinked_skill_projection(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    skill_path = tmp_path / ".claude/skills/synthetic-skill/SKILL.md"
    external_skill = tmp_path / "external_skill.md"
    external_skill.write_text(
        "# Skill\n\nRuntime Module: `module_alpha`.\n",
        encoding="utf-8",
    )
    skill_path.unlink()
    skill_path.symlink_to(external_skill)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        load_runtime_module_registration(
            tmp_path,
            skill_id="synthetic-skill",
            module_id="module_alpha",
        )


def test_v2_module_registration_rejects_symlinked_owner_contract(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    owner_path = tmp_path / "designDoc/module_alpha_owner.md"
    external_owner = tmp_path / "external_owner.md"
    external_owner.write_text(
        "# Owner\n\nRuntime Module: `module_alpha`.\n",
        encoding="utf-8",
    )
    owner_path.unlink()
    owner_path.symlink_to(external_owner)

    with pytest.raises(ValueError, match="cannot traverse a symlink"):
        load_runtime_module_registration(
            tmp_path,
            skill_id="synthetic-skill",
            module_id="module_alpha",
        )


def test_v2_compiler_binds_only_selected_module_and_source_skill(
    tmp_path: Path,
) -> None:
    _write_v2_module_registration(tmp_path, module_id="module_alpha")
    _write_v2_module_registration(tmp_path, module_id="module_beta")

    spec = AgentModuleReleaseSpec(
            module_id="module_alpha",
            skill_id="synthetic-skill",
            skill_projection_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "module_alpha/prompt.md"
            ),
            owner_contract_ref=(
                "repo-file:designDoc/module_alpha_owner.md"
            ),
            owner_contract_path="designDoc/module_alpha_owner.md",
            input_schema_ref="schema:module_alpha_input@v1",
            output_schema_ref="schema:module_alpha_output@v1",
            declared_operation_ids=("invoke_model",),
            execution_profile_id="module_alpha_profile",
            executor_adapter_id="codex_cli",
            executor_adapter_revision="v1",
            transport_kind="codex_cli",
            provider_id="openai",
            model_id="synthetic_model",
            reasoning_profile="none",
            output_constraint_mode="prompt_only_json",
            timeout_seconds=60,
            input_schema_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "module_alpha/schemas/module_alpha_input.schema.json"
            ),
            output_schema_path=(
                ".claude/skills/synthetic-skill/runtime_modules/"
                "module_alpha/schemas/module_alpha_output.schema.json"
            ),
            release_version="candidate_v1",
            compatible_transport_kinds=("codex_cli",),
            context_policy_ref="context-policy:synthetic@v1",
            evaluation_policy_ref="evaluation-policy:synthetic@v1",
            retry_policy_ref="retry-policy:bounded@v1",
        )
    compiled = compile_agent_module_release(tmp_path, spec)

    assert compiled.module.owner_contract_ref == (
        "repo-file:designDoc/module_alpha_owner.md"
    )
    assert compiled.module.source_skill_id == "synthetic-skill"
    assert compiled.module.release_ref == (
        "runtime-module:module_alpha@candidate_v1"
    )

    with pytest.raises(
        ValueError,
        match="Module spec differs from module_registration.json",
    ):
        compile_agent_module_release(
            tmp_path,
            replace(spec, owner_contract_path="../../must_not_be_read"),
        )

    (
        tmp_path
        / ".claude/skills/synthetic-skill/runtime_modules/"
        "module_alpha/prompt.md"
    ).write_text("Execute revised module_alpha.\n", encoding="utf-8")
    revised = compile_agent_module_release(
        tmp_path,
        replace(spec, release_version="candidate_v2"),
    )

    assert revised.prompt_bundle != compiled.prompt_bundle
    assert revised.module != compiled.module
