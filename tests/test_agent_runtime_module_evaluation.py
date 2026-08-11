from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from agent_runtime.contracts.execution_module_definition import ModuleInputBinding
from agent_runtime.testing import execution_module_evaluation as subject


HASH = "a" * 64


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _ReleaseRegistry:
    def __init__(self) -> None:
        self.module = SimpleNamespace(
            release_ref="runtime-module:test_writer@v1",
            release_sha256="b" * 64,
            prompt_bundle_ref="prompt-bundle:test_writer@v1",
            prompt_bundle_sha256="c" * 64,
            output_schema_ref="schema:test_writer_output@v1",
        )
        self.profile = SimpleNamespace(
            release_ref="execution-profile:test_inline@v1",
            release_sha256="d" * 64,
            semantic_input_delivery_mode="inline",
            network_policy="denied",
            output_constraint_mode="prompt_only_json",
        )
        self.prompt = SimpleNamespace(
            compiled_static_body=(
                "Write one JSON object.\n\n"
                "## Required Output Shape\n"
                '{"type":"object","properties":{"value":{"type":"string"}},'
                '"required":["value"],"additionalProperties":false}'
            )
        )

    def get_module(self, release_ref: str, release_sha256: str):
        assert (release_ref, release_sha256) == (
            self.module.release_ref,
            self.module.release_sha256,
        )
        return self.module

    def get_execution_profile(self, release_ref: str, release_sha256: str):
        assert (release_ref, release_sha256) == (
            self.profile.release_ref,
            self.profile.release_sha256,
        )
        return self.profile

    def get_prompt_bundle(self, release_ref: str, release_sha256: str):
        assert (release_ref, release_sha256) == (
            self.module.prompt_bundle_ref,
            self.module.prompt_bundle_sha256,
        )
        return self.prompt


class _AdapterRegistry:
    def __init__(self) -> None:
        self.adapter = None

    def register(self, adapter) -> None:
        self.adapter = adapter


class _Usage:
    def as_dict(self) -> dict[str, int]:
        return {"input_tokens": 11, "output_tokens": 5}


def test_registered_module_evaluation_is_runtime_owned_and_host_authorized(
    monkeypatch,
) -> None:
    registry = _ReleaseRegistry()
    input_content = b'{"task":"write"}'
    projected = (
        ModuleInputBinding(
            logical_name="task_input",
            input_ref="model-input:test_writer",
            input_sha256=_sha256(input_content),
            schema_ref="schema:test_writer_input@v1",
            schema_sha256=HASH,
            media_type="application/json",
        ),
        input_content,
    )
    captured: dict[str, object] = {}

    def run_module(request, **kwargs):
        captured.update(kwargs)
        store = kwargs["artifact_host"]
        output = store.put_bytes(
            artifact_kind_id="module_semantic_output",
            schema_version="module_output_v1",
            schema_ref=registry.module.output_schema_ref,
            schema_sha256=HASH,
            media_type="application/json",
            content=b'{"value":"done"}',
            idempotency_key="test_output",
            logical_name="result",
        )
        variant = SimpleNamespace(
            execution_profile_ref=registry.profile.release_ref,
            execution_profile_sha256=registry.profile.release_sha256,
            prompt_envelope_ref=request.variants[0].prompt_envelope_ref,
            prompt_envelope_sha256=request.variants[0].prompt_envelope_sha256,
        )
        attempt = SimpleNamespace(
            attempt_id="attempt_test_writer",
            status="completed",
            failure_class=None,
            failure_detail_ref=None,
            failure_detail_sha256=None,
            usage=_Usage(),
            period_start_at_utc="2026-08-11T00:00:00Z",
            period_end_at_utc="2026-08-11T00:00:01Z",
        )
        return SimpleNamespace(
            module_run=SimpleNamespace(module_run_id="module_run_test_writer"),
            variants=(variant,),
            attempts=(attempt,),
            outputs=(
                SimpleNamespace(
                    output_ref=output.artifact_ref,
                    output_sha256=output.artifact_sha256,
                ),
            ),
        )

    monkeypatch.setattr(subject, "AgentExecutionAdapterRegistry", _AdapterRegistry)
    monkeypatch.setattr(subject, "run_module", run_module)

    result = subject.run_registered_inline_module_evaluation(
        release_registry=registry,
        module_release_ref=registry.module.release_ref,
        module_release_sha256=registry.module.release_sha256,
        execution_profile_ref=registry.profile.release_ref,
        execution_profile_sha256=registry.profile.release_sha256,
        projected_input=projected,
        evaluation_key="test_writer",
        adapter_factory=lambda _registry, _store: object(),
        authority_factory=lambda request, _registry: (
            "host_authority",
            request.request_id,
        ),
        clock=lambda: "2026-08-11T00:00:00Z",
    )

    assert result.output == {"value": "done"}
    assert result.result.attempts[0].status == "completed"
    assert "Write one JSON object" in result.prompt
    assert result.as_record()["output"] == {"value": "done"}
    assert captured["authority"][0] == "host_authority"
    assert callable(captured["clock"])


def test_registered_module_evaluation_rejects_non_inline_profile() -> None:
    registry = _ReleaseRegistry()
    registry.profile.semantic_input_delivery_mode = "gateway"
    input_content = b"{}"

    with pytest.raises(ValueError, match="inline delivery only"):
        subject.run_registered_inline_module_evaluation(
            release_registry=registry,
            module_release_ref=registry.module.release_ref,
            module_release_sha256=registry.module.release_sha256,
            execution_profile_ref=registry.profile.release_ref,
            execution_profile_sha256=registry.profile.release_sha256,
            projected_input=(
                ModuleInputBinding(
                    logical_name="task_input",
                    input_ref="model-input:test_writer",
                    input_sha256=_sha256(input_content),
                    schema_ref="schema:test_writer_input@v1",
                    schema_sha256=HASH,
                    media_type="application/json",
                ),
                input_content,
            ),
            evaluation_key="test_writer",
            adapter_factory=lambda _registry, _store: object(),
            authority_factory=lambda _request, _registry: object(),
        )
