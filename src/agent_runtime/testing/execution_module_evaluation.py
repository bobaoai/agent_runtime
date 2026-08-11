"""Execute one registered inline Module for isolated Test or Evaluation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping

from ..contracts.execution_module_definition import (
    ModuleExecutionRequest,
    ModuleInputBinding,
    ModuleRunResult,
    ModuleVariantRequest,
)
from ..contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionAdapter,
)
from ..contracts.registry_release_definition import ModuleExecutionPurpose
from ..execution.execution_content_staging import InMemoryCellArtifactStore
from ..execution.execution_module_invocation import (
    AgentExecutionAdapterRegistry,
    ModuleExecutionAuthority,
    run_module,
)
from ..invocation.invocation_prompt_assembly import build_inline_provider_prompt
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from ..registry.registry_release_registration import RuntimeReleaseRegistry


AdapterFactory = Callable[
    [RuntimeReleaseRegistry, InMemoryCellArtifactStore],
    AuthorizedAgentExecutionAdapter,
]
AuthorityFactory = Callable[
    [ModuleExecutionRequest, RuntimeReleaseRegistry],
    ModuleExecutionAuthority,
]


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class RegisteredModuleEvaluation:
    """Complete inspectable result of one isolated Runtime evaluation."""

    request: ModuleExecutionRequest
    result: ModuleRunResult
    prompt: str
    output: Mapping[str, object] | None
    failure_detail: Mapping[str, object] | None

    def as_record(self) -> dict[str, object]:
        """Project the execution result without inventing missing usage facts."""

        attempt = self.result.attempts[0]
        variant = self.result.variants[0]
        return {
            "schema_version": "registered_module_evaluation_v1",
            "request_id": self.request.request_id,
            "module_run_id": self.result.module_run.module_run_id,
            "module_release_ref": self.request.module_release_ref,
            "module_release_sha256": self.request.module_release_sha256,
            "execution_profile_ref": variant.execution_profile_ref,
            "execution_profile_sha256": variant.execution_profile_sha256,
            "attempt_id": attempt.attempt_id,
            "status": attempt.status,
            "failure_class": attempt.failure_class,
            "input_closure_sha256": self.request.input_closure_sha256,
            "prompt_envelope_ref": variant.prompt_envelope_ref,
            "prompt_envelope_sha256": variant.prompt_envelope_sha256,
            "prompt": self.prompt,
            "output": dict(self.output) if self.output is not None else None,
            "failure_detail": (
                dict(self.failure_detail)
                if self.failure_detail is not None
                else None
            ),
            "usage": attempt.usage.as_dict(),
            "period_start_at_utc": attempt.period_start_at_utc,
            "period_end_at_utc": attempt.period_end_at_utc,
        }


def run_registered_inline_module_evaluation(
    *,
    release_registry: RuntimeReleaseRegistry,
    module_release_ref: str,
    module_release_sha256: str,
    execution_profile_ref: str,
    execution_profile_sha256: str,
    projected_input: tuple[ModuleInputBinding, bytes],
    evaluation_key: str,
    adapter_factory: AdapterFactory,
    authority_factory: AuthorityFactory,
    artifact_store: InMemoryCellArtifactStore | None = None,
    clock: Callable[[], str] | None = None,
) -> RegisteredModuleEvaluation:
    """Run one registered inline Module/Profile pair through Runtime authority.

    The host supplies the Product authorization adapter through
    ``authority_factory``. Runtime owns staging, Prompt assembly, invocation,
    ledgering, and result projection; the helper never manufactures a Product
    entitlement or bypasses the execution authorization fence.
    """

    module = release_registry.get_module(
        module_release_ref,
        module_release_sha256,
    )
    profile = release_registry.get_execution_profile(
        execution_profile_ref,
        execution_profile_sha256,
    )
    if profile.semantic_input_delivery_mode != "inline":
        raise ValueError("registered evaluation runner accepts inline delivery only")
    if profile.network_policy != "denied":
        raise ValueError("registered inline evaluation must remain network denied")
    binding, input_content = projected_input
    binding.validate()
    if _sha256(input_content) != binding.input_sha256:
        raise ValueError("projected model input hash mismatch")

    store = artifact_store or InMemoryCellArtifactStore(
        artifact_kind_by_schema_ref={
            module.output_schema_ref: "module_semantic_output"
        }
    )
    staged_input = store.put_bytes(
        artifact_kind_id="module_semantic_input",
        schema_version="module_semantic_input_v1",
        schema_ref=binding.schema_ref,
        schema_sha256=binding.schema_sha256,
        media_type=binding.media_type,
        content=input_content,
        idempotency_key=_stable_id("semantic_input", evaluation_key),
        logical_name=binding.logical_name,
    )
    staged_binding = ModuleInputBinding(
        logical_name=binding.logical_name,
        input_ref=staged_input.artifact_ref,
        input_sha256=staged_input.artifact_sha256,
        schema_ref=binding.schema_ref,
        schema_sha256=binding.schema_sha256,
        media_type=binding.media_type,
    )
    prompt_bundle = release_registry.get_prompt_bundle(
        module.prompt_bundle_ref,
        module.prompt_bundle_sha256,
    )
    prompt = build_inline_provider_prompt(
        compiled_static_body=prompt_bundle.compiled_static_body,
        execution_specific_instructions="",
        inputs=((staged_binding, input_content),),
        output_constraint_mode=profile.output_constraint_mode,
    )
    prompt_artifact = store.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256=_sha256(b"schema:prompt_envelope@v1"),
        media_type="text/plain",
        content=prompt.encode("utf-8"),
        idempotency_key=_stable_id("prompt_envelope", evaluation_key),
        logical_name="prompt_envelope",
    )
    input_package_sha256 = _sha256(
        json.dumps(
            [staged_binding.as_dict()],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    isolated_scope_ref = f"runtime-scope:{_stable_id('evaluation_scope', evaluation_key)}"
    isolated_scope_sha256 = _sha256(isolated_scope_ref.encode("utf-8"))
    request = ModuleExecutionRequest.build(
        request_id=_stable_id("module_request", evaluation_key),
        purpose=ModuleExecutionPurpose.EVALUATION,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        isolated_scope_ref=isolated_scope_ref,
        isolated_scope_sha256=isolated_scope_sha256,
        input_package_ref=f"evaluation-package:{_stable_id('package', evaluation_key)}",
        input_package_sha256=input_package_sha256,
        inputs=(staged_binding,),
        variants=(
            ModuleVariantRequest(
                arm_key="default",
                replicate_index=0,
                execution_profile_ref=profile.release_ref,
                execution_profile_sha256=profile.release_sha256,
                prompt_envelope_ref=prompt_artifact.artifact_ref,
                prompt_envelope_sha256=prompt_artifact.artifact_sha256,
            ),
        ),
        idempotency_key=_stable_id("module_idempotency", evaluation_key),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter_factory(release_registry, store))
    invocation_arguments = {
        "release_registry": release_registry,
        "adapters": adapters,
        "artifact_host": store,
        "ledger": InMemoryModuleExecutionLedger(),
        "authority": authority_factory(request, release_registry),
    }
    if clock is not None:
        invocation_arguments["clock"] = clock
    result = run_module(request, **invocation_arguments)
    attempt = result.attempts[0]
    output: Mapping[str, object] | None = None
    failure_detail: Mapping[str, object] | None = None
    if attempt.status == "completed":
        if len(result.outputs) != 1:
            raise ValueError("completed evaluation requires exactly one output")
        raw_output = json.loads(
            store.read_bytes(
                result.outputs[0].output_ref,
                result.outputs[0].output_sha256,
            )
        )
        if not isinstance(raw_output, dict):
            raise ValueError("Module output must be one JSON object")
        output = raw_output
    elif (
        attempt.failure_detail_ref is not None
        and attempt.failure_detail_sha256 is not None
    ):
        raw_detail = json.loads(
            store.read_bytes(
                attempt.failure_detail_ref,
                attempt.failure_detail_sha256,
            )
        )
        if isinstance(raw_detail, dict):
            failure_detail = raw_detail
    return RegisteredModuleEvaluation(
        request=request,
        result=result,
        prompt=prompt,
        output=output,
        failure_detail=failure_detail,
    )


__all__ = [
    "AdapterFactory",
    "AuthorityFactory",
    "RegisteredModuleEvaluation",
    "run_registered_inline_module_evaluation",
]
