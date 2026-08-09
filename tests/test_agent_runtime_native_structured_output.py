from __future__ import annotations

import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

import pytest

from agent_runtime.contracts.execution_module_definition import (
    ModuleExecutionRequest,
    ModuleExecutorFailure,
    ModuleExecutorRequest,
    ModuleVariantRequest,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
)
from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.execution.execution_module_invocation import (
    ModuleExecutorRegistry,
    run_module,
)
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
from agent_runtime.ledger.ledger_lineage_recording import (
    InMemoryModuleExecutionLedger,
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


def _compile_native_module(
    tmp_path: Path,
    *,
    declared_operation_ids: tuple[str, ...] = ("invoke_model",),
    output_resolution_policy: OutputResolutionPolicy = (
        OutputResolutionPolicy.EVALUATED_SINGLE
    ),
    execution_profile_id: str = "native_profile",
    executor_adapter_id: str = "codex_cli_agent_executor",
    executor_adapter_revision: str = "v2",
    transport_kind: str = "codex_cli",
    provider_id: str = "openai",
    model_id: str = "native_model",
    reasoning_profile: str = "none",
    timeout_seconds: int = 60,
):
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
                "declared_operation_ids": list(declared_operation_ids),
                "compatible_transport_kinds": [transport_kind],
                "context_policy_ref": (
                    "context-policy:workflow_execution_isolated@v1"
                ),
                "evaluation_policy_ref": "evaluation-policy:native@v1",
                "retry_policy_ref": "retry-policy:bounded@v1",
                "entry_policy": "workflow_bound",
                "output_resolution_policy": output_resolution_policy.value,
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
            declared_operation_ids=declared_operation_ids,
            execution_profile_id=execution_profile_id,
            executor_adapter_id=executor_adapter_id,
            executor_adapter_revision=executor_adapter_revision,
            transport_kind=transport_kind,
            provider_id=provider_id,
            model_id=model_id,
            reasoning_profile=reasoning_profile,
            output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
            timeout_seconds=timeout_seconds,
            input_schema_path="schemas/input.schema.json",
            output_schema_path="schemas/output.schema.json",
            compatible_transport_kinds=(transport_kind,),
            evaluation_policy_ref="evaluation-policy:native@v1",
            retry_policy_ref="retry-policy:bounded@v1",
            output_resolution_policy=output_resolution_policy,
        ),
    )


_TEST_TIME = "2026-08-09T12:00:00Z"
_RUN_PROVIDER_INTEGRATION = os.environ.get("RUN_PROVIDER_INTEGRATION") == "1"


def _register_compiled_for_evaluation(compiled) -> RuntimeReleaseRegistry:
    admitted_releases = [
        (
            ReleaseSubjectKind.SKILL_PACKAGE,
            compiled.skill_package.skill_package_id,
            compiled.skill_package.release_ref,
            compiled.skill_package.release_sha256,
        ),
        *(
            (
                ReleaseSubjectKind.PROMPT_COMPONENT,
                component.prompt_component_id,
                component.release_ref,
                component.release_sha256,
            )
            for component in compiled.prompt_components
        ),
        (
            ReleaseSubjectKind.PROMPT_BUNDLE,
            compiled.prompt_bundle.prompt_bundle_id,
            compiled.prompt_bundle.release_ref,
            compiled.prompt_bundle.release_sha256,
        ),
        (
            ReleaseSubjectKind.EXECUTION_PROFILE,
            compiled.execution_profile.execution_profile_id,
            compiled.execution_profile.release_ref,
            compiled.execution_profile.release_sha256,
        ),
        (
            ReleaseSubjectKind.RUNTIME_MODULE,
            compiled.module.module_id,
            compiled.module.release_ref,
            compiled.module.release_sha256,
        ),
    ]
    admissions = tuple(
        ReleaseAdmissionRecord.build(
            admission_id=f"admission_native_evaluation_{index:02d}",
            subject_kind=kind,
            subject_id=subject_id,
            release_ref=release_ref,
            release_sha256=release_sha256,
            state=ReleaseAdmissionState.CANDIDATE,
            evidence_members=(),
            recorded_at_utc=_TEST_TIME,
        )
        for index, (kind, subject_id, release_ref, release_sha256) in enumerate(
            admitted_releases
        )
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
            admissions=admissions,
        )
    )
    return registry


def _evaluation_prompt(artifact_host, compiled, *, suffix: str):
    envelope = build_inline_provider_prompt(
        compiled_static_body=compiled.prompt_bundle.compiled_static_body,
        execution_specific_instructions="",
        inputs=(),
        output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
    )
    return artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=envelope.encode("utf-8"),
        idempotency_key=f"prompt_envelope_{suffix}",
    )


def _evaluation_request(
    compiled,
    prompt_ref,
    *,
    suffix: str,
    purpose: ModuleExecutionPurpose = ModuleExecutionPurpose.EVALUATION,
) -> ModuleExecutionRequest:
    return ModuleExecutionRequest.build(
        request_id=f"request_native_{suffix}",
        purpose=purpose,
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        isolated_scope_ref=f"scope-ref:native-{suffix}",
        isolated_scope_sha256="2" * 64,
        input_package_ref=f"artifact-ref:input-package-{suffix}",
        input_package_sha256="3" * 64,
        inputs=(),
        variants=(
            ModuleVariantRequest(
                arm_key=f"native_{suffix}",
                replicate_index=0,
                execution_profile_ref=compiled.execution_profile.release_ref,
                execution_profile_sha256=(
                    compiled.execution_profile.release_sha256
                ),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key=f"idempotency_native_{suffix}",
    )


def _assert_completed_provider_run(run, artifact_host) -> dict[str, object]:
    assert run.module_run.purpose is ModuleExecutionPurpose.EVALUATION
    assert len(run.attempts) == 1
    attempt = run.attempts[0]
    if attempt.status != "completed":
        detail = None
        if (
            attempt.failure_detail_ref is not None
            and attempt.failure_detail_sha256 is not None
        ):
            detail = json.loads(
                artifact_host.read_bytes(
                    attempt.failure_detail_ref,
                    attempt.failure_detail_sha256,
                )
            )
        pytest.fail(
            f"Provider Attempt failed: {attempt.failure_class}; detail={detail!r}"
        )
    assert len(run.outputs) == 1
    assert run.resolution is not None
    assert run.resolution.resolution_status == "resolved"
    return json.loads(
        artifact_host.read_bytes(
            run.outputs[0].output_ref,
            run.outputs[0].output_sha256,
        )
    )


def _emit_live_provider_evidence(run, compiled) -> None:
    print(
        "LIVE_PROVIDER_EVIDENCE="
        + json.dumps(
            {
                "module_run_id": run.module_run.module_run_id,
                "attempt_id": run.attempts[0].attempt_id,
                "provider_id": compiled.execution_profile.provider_id,
                "model_id": compiled.execution_profile.model_id,
                "transport_kind": compiled.execution_profile.transport_kind,
                "output_sha256": run.outputs[0].output_sha256,
                "usage": run.attempts[0].usage.as_dict(),
            },
            sort_keys=True,
        )
    )


def test_run_module_evaluation_executes_registered_codex_transport(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_stub")
    captured: dict[str, object] = {}

    def invoker(**fields) -> CodexCliInvocationResult:
        captured.update(fields)
        stdout = "\n".join(
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps({"value": "through_run_module"}),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "cached_input_tokens": 3,
                        },
                    }
                ),
            )
        )
        return CodexCliInvocationResult(returncode=0, stdout=stdout, stderr="")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    run = run_module(
        _evaluation_request(compiled, prompt_ref, suffix="codex_stub"),
        release_registry=registry,
        executors=executors,
        ledger=InMemoryModuleExecutionLedger(),
        clock=lambda: _TEST_TIME,
    )

    assert _assert_completed_provider_run(run, artifact_host) == {
        "value": "through_run_module"
    }
    assert run.attempts[0].usage.input_tokens == 11
    assert run.attempts[0].usage.output_tokens == 7
    assert captured["argv"][1] == "exec"


def test_run_module_records_registered_codex_transport_failure(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_failure")

    def invoker(**_fields) -> CodexCliInvocationResult:
        return CodexCliInvocationResult(
            returncode=9,
            stdout=json.dumps(
                {
                    "type": "turn.failed",
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                }
            ),
            stderr="provider unavailable",
        )

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    run = run_module(
        _evaluation_request(compiled, prompt_ref, suffix="codex_failure"),
        release_registry=registry,
        executors=executors,
        ledger=InMemoryModuleExecutionLedger(),
        clock=lambda: _TEST_TIME,
    )

    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "provider_failure"
    assert run.attempts[0].failure_detail_ref is not None
    assert run.attempts[0].usage.input_tokens == 5
    assert run.resolution is None


def test_run_module_provider_transport_still_rejects_protected_module(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        declared_operation_ids=("invoke_model", "read_protected_source"),
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="protected")
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("protected Module reached Provider")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    with pytest.raises(NotImplementedError, match="protected Module operations"):
        run_module(
            _evaluation_request(compiled, prompt_ref, suffix="protected"),
            release_registry=registry,
            executors=executors,
            ledger=InMemoryModuleExecutionLedger(),
        )

    assert entered is False


def test_run_module_provider_transport_still_rejects_production_purpose(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="production")
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("production request reached Provider")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    with pytest.raises(NotImplementedError, match="production Module execution"):
        run_module(
            _evaluation_request(
                compiled,
                prompt_ref,
                suffix="production",
                purpose=ModuleExecutionPurpose.WORKFLOW,
            ),
            release_registry=registry,
            executors=executors,
            ledger=InMemoryModuleExecutionLedger(),
        )

    assert entered is False


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_codex_evaluation_runs_through_run_module(tmp_path: Path) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        model_id=os.environ.get("AGENT_RUNTIME_TEST_CODEX_MODEL", "gpt-5.6-sol"),
        reasoning_profile="low",
        timeout_seconds=300,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_live")
    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    run = run_module(
        _evaluation_request(compiled, prompt_ref, suffix="codex_live"),
        release_registry=registry,
        executors=executors,
        ledger=InMemoryModuleExecutionLedger(),
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert isinstance(output["value"], str)
    assert run.attempts[0].usage.input_tokens is not None
    assert run.attempts[0].usage.output_tokens is not None
    _emit_live_provider_evidence(run, compiled)


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_claude_evaluation_runs_through_run_module(tmp_path: Path) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="native_claude_profile",
        executor_adapter_id="claude_agent_sdk_inline_executor",
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id=os.environ.get(
            "AGENT_RUNTIME_TEST_CLAUDE_MODEL",
            "claude-fable-5",
        ),
        reasoning_profile="low",
        timeout_seconds=300,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="claude_live")
    executor = claude_module.ClaudeAgentSdkInlineModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
    )
    executors = ModuleExecutorRegistry()
    executors.register(executor.executor_adapter_id, executor)

    run = run_module(
        _evaluation_request(compiled, prompt_ref, suffix="claude_live"),
        release_registry=registry,
        executors=executors,
        ledger=InMemoryModuleExecutionLedger(),
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert isinstance(output["value"], str)
    assert run.attempts[0].usage.output_tokens is not None
    _emit_live_provider_evidence(run, compiled)


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
