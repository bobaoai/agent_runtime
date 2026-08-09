from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path

import pytest

from agent_runtime.contracts.execution_module_definition import (
    ModuleExecutorFailure,
    ModuleExecutorRequest,
)
from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.invocation.invocation_codex_module_invocation import (
    CodexCliInvocationResult,
    CodexCliModuleExecutor,
)
from agent_runtime.invocation.invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
)
from agent_runtime.invocation import invocation_codex_module_invocation as codex_module
from agent_runtime.invocation.invocation_prompt_assembly import (
    NATIVE_STRUCTURED_OUTPUT,
    OUTPUT_SCHEMA_MARKER,
    build_inline_provider_prompt,
    codex_native_output_schema,
)
from agent_runtime.invocation.invocation_schema_projection import (
    task_plane_output_schema,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseSpec,
    compile_agent_module_release,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "schema:native_output@v1",
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["value"],
    "additionalProperties": False,
}


def _compile_native_module(tmp_path: Path):
    owner = tmp_path / "designDoc" / "owner.md"
    owner.parent.mkdir()
    owner.write_text("# Owner\n", encoding="utf-8")
    schema_root = tmp_path / "schemas"
    schema_root.mkdir()
    (schema_root / "input.schema.json").write_text(
        json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "schema:native_input@v1",
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            }
        ),
        encoding="utf-8",
    )
    (schema_root / "output.schema.json").write_text(
        json.dumps(_OUTPUT_SCHEMA),
        encoding="utf-8",
    )
    module_root = (
        tmp_path
        / ".claude"
        / "skills"
        / "native-skill"
        / "runtime_modules"
        / "native_module"
    )
    module_root.mkdir(parents=True)
    (module_root / "prompt.md").write_text(
        "Produce the native result.\n",
        encoding="utf-8",
    )
    (module_root / "module_registration.json").write_text(
        json.dumps(
            {
                "schema_version": "runtime_module_registration_v1",
                "skill_package_id": "native_skill_package",
                "skill_id": "native-skill",
                "export_id": "native_module",
                "module_id": "native_module",
                "owner_contract_path": "designDoc/owner.md",
                "input_schema_ref": "schema:native_input@v1",
                "input_schema_path": "schemas/input.schema.json",
                "output_schema_ref": "schema:native_output@v1",
                "output_schema_path": "schemas/output.schema.json",
                "declared_operation_ids": ["invoke_model"],
                "compatible_transport_kinds": ["codex_cli"],
                "context_policy_ref": (
                    "context-policy:workflow_execution_isolated@v1"
                ),
                "evaluation_policy_ref": "evaluation-policy:native@v1",
                "retry_policy_ref": "retry-policy:bounded@v1",
                "entry_policy": "workflow_bound",
                "output_resolution_policy": "evaluated_single",
            }
        ),
        encoding="utf-8",
    )
    return compile_agent_module_release(
        tmp_path,
        AgentModuleReleaseSpec(
            module_id="native_module",
            skill_id="native-skill",
            skill_projection_path=(
                ".claude/skills/native-skill/runtime_modules/"
                "native_module/prompt.md"
            ),
            owner_contract_ref="repo-file:designDoc/owner.md",
            owner_contract_path="designDoc/owner.md",
            input_schema_ref="schema:native_input@v1",
            output_schema_ref="schema:native_output@v1",
            declared_operation_ids=("invoke_model",),
            execution_profile_id="native_profile",
            executor_adapter_id="codex_cli_agent_executor",
            executor_adapter_revision="v2",
            transport_kind="codex_cli",
            provider_id="openai",
            model_id="native_model",
            reasoning_profile="none",
            output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
            timeout_seconds=60,
            input_schema_path="schemas/input.schema.json",
            output_schema_path="schemas/output.schema.json",
            compatible_transport_kinds=("codex_cli",),
            evaluation_policy_ref="evaluation-policy:native@v1",
            retry_policy_ref="retry-policy:bounded@v1",
        ),
    )


def test_codex_native_structured_output_executes_end_to_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    compiled = _compile_native_module(tmp_path)
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
    envelope = build_inline_provider_prompt(
        compiled_static_body=compiled.prompt_bundle.compiled_static_body,
        execution_specific_instructions="",
        inputs=(),
        output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
    )
    prompt_ref = artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=envelope.encode("utf-8"),
        idempotency_key="prompt_envelope_native",
    )
    captured: dict[str, object] = {}
    lease_events: list[str] = []

    @contextmanager
    def tracked_lease(workspace: Path):
        lease_events.append(f"enter:{workspace.name}")
        try:
            yield workspace
        finally:
            lease_events.append(f"exit:{workspace.name}")

    monkeypatch.setattr(codex_module, "lease_attempt_workspace", tracked_lease)

    def invoker(
        *,
        argv: list[str],
        prompt: str,
        cwd: Path,
        timeout_seconds: int,
    ) -> CodexCliInvocationResult:
        assert lease_events == ["enter:attempt_native_001"]
        captured["argv"] = list(argv)
        captured["prompt"] = prompt
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        captured["schema"] = json.loads(
            schema_path.read_text(encoding="utf-8")
        )
        provider_payload = {"value": "ok", "note": None}
        stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(provider_payload),
                },
            }
        )
        return CodexCliInvocationResult(returncode=0, stdout=stdout, stderr="")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    result = executor.execute(
        ModuleExecutorRequest(
            module_run_id="module_run_native_001",
            variant_id="variant_native_001",
            attempt_id="attempt_native_001",
            module=compiled.module,
            execution_profile=compiled.execution_profile,
            input_package_ref="artifact-ref:input-package-001",
            input_package_sha256="2" * 64,
            inputs=(),
            prompt_envelope_ref=prompt_ref.artifact_ref,
            prompt_envelope_sha256=prompt_ref.artifact_sha256,
            isolated_scope_ref="scope-ref:native-001",
            isolated_scope_sha256="3" * 64,
        )
    )

    assert OUTPUT_SCHEMA_MARKER not in str(captured["prompt"])
    registry_canonical_schema = json.loads(
        json.dumps(_OUTPUT_SCHEMA, sort_keys=True)
    )
    assert captured["schema"] == codex_native_output_schema(
        task_plane_output_schema(registry_canonical_schema)
    )
    canonical = json.dumps(
        {"value": "ok"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert result.outputs[0].output_sha256 == hashlib.sha256(
        canonical
    ).hexdigest()
    assert lease_events == [
        "enter:attempt_native_001",
        "exit:attempt_native_001",
    ]

    @contextmanager
    def conflicting_lease(_workspace: Path):
        raise AttemptWorkspaceConflictError("live duplicate invocation")
        yield  # pragma: no cover

    monkeypatch.setattr(codex_module, "lease_attempt_workspace", conflicting_lease)
    with pytest.raises(ModuleExecutorFailure) as raised:
        executor.execute(
            ModuleExecutorRequest(
                module_run_id="module_run_native_001",
                variant_id="variant_native_001",
                attempt_id="attempt_native_001",
                module=compiled.module,
                execution_profile=compiled.execution_profile,
                input_package_ref="artifact-ref:input-package-001",
                input_package_sha256="2" * 64,
                inputs=(),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
                isolated_scope_ref="scope-ref:native-001",
                isolated_scope_sha256="3" * 64,
            )
        )
    failure = raised.value
    assert failure.failure_class == "workspace_initialization_failure"
    assert failure.failure_code == "codex_attempt_workspace_unavailable"
    assert failure.detail is not None
    detail = json.loads(
        artifact_host.read_bytes(
            failure.detail.detail_ref,
            failure.detail.detail_sha256,
        )
    )
    assert detail["retryable"] is False
    assert detail["failure_class"] == "workspace_initialization_failure"


def test_adapters_no_longer_reference_the_removed_prompt_bundle_local() -> None:
    invocation_root = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "agent_runtime"
        / "invocation"
    )
    for module_name in (
        "invocation_codex_module_invocation",
        "invocation_claude_module_invocation",
    ):
        tree = ast.parse(
            (invocation_root / f"{module_name}.py").read_text(
                encoding="utf-8"
            )
        )
        loaded_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        assert "prompt_bundle" not in loaded_names, module_name
