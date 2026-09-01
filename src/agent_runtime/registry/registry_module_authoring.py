"""Runtime-owned authoring interfaces for immutable Module releases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleRelease,
    PromptComponentKind,
    RetryPolicyRelease,
)
from .registry_module_loading import (
    ModuleRegistrationSource,
    load_module_registration,
)
from .registry_release_compilation import (
    AgentModuleReleaseCandidate,
    CompiledAgentModuleRelease,
    ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
    compile_agent_module_release,
    compile_execution_variant_policy_release,
)
from .registry_release_registration import RuntimeReleaseRegistry


TOOL_FREE_OPERATION_IDS = ("model_execute",)
EXECUTION_PROFILE_UNAVAILABLE = "MODULE_EXECUTION_PROFILE_UNAVAILABLE"


def _candidate(
    source: ModuleRegistrationSource,
    *,
    module_version: str,
    behavior_policy: BehaviorPolicyRelease,
    evaluation_policy: EvaluationPolicyRelease,
    retry_policy: RetryPolicyRelease,
) -> AgentModuleReleaseCandidate:
    if source.behavior_policy_ref != behavior_policy.release_ref:
        raise ValueError("Module behavior_policy_ref differs from supplied release")
    if source.evaluation_policy_ref != evaluation_policy.release_ref:
        raise ValueError("Module evaluation_policy_ref differs from supplied release")
    if source.retry_policy_ref != retry_policy.release_ref:
        raise ValueError("Module retry_policy_ref differs from supplied release")
    return AgentModuleReleaseCandidate(
        module_id=source.module_id,
        module_version=module_version,
        owner_contract_ref=source.owner_contract_ref,
        owner_contract_content=source.owner_contract_content,
        input_schema_ref=source.input_schema_ref,
        input_schema_document=source.input_schema_document,
        output_schema_ref=source.output_schema_ref,
        output_schema_document=source.output_schema_document,
        instruction_source_ref=source.instruction_source_ref,
        instruction_text=source.instruction_text,
        declared_operation_ids=source.declared_operation_ids,
        compatible_transport_kinds=source.compatible_transport_kinds,
        behavior_policy_ref=behavior_policy.release_ref,
        behavior_policy_sha256=behavior_policy.release_sha256,
        evaluation_policy_ref=evaluation_policy.release_ref,
        evaluation_policy_sha256=evaluation_policy.release_sha256,
        retry_policy_ref=retry_policy.release_ref,
        retry_policy_sha256=retry_policy.release_sha256,
        entry_policy=source.entry_policy,
        output_resolution_policy=source.output_resolution_policy,
    )


@dataclass(frozen=True)
class ModuleExport:
    """Dependency-closed records exported from one Module authoring source."""

    source: ModuleRegistrationSource
    candidate: AgentModuleReleaseCandidate
    compiled: CompiledAgentModuleRelease
    execution_profile: ExecutionProfileRelease | None
    execution_variant_candidate: ExecutionVariantPolicyReleaseCandidate | None
    execution_variant: ExecutionVariantPolicyRelease | None
    execution_blocker_code: str | None

    @property
    def module_release(self) -> ModuleRelease:
        return self.compiled.module


class Module(ABC):
    """Common authoring-time interface; execution consumes only ModuleRelease."""

    @classmethod
    @abstractmethod
    def from_registration(
        cls,
        project_root: Path,
        *,
        skill_id: str,
        module_id: str,
    ) -> Self:
        """Load one exact host registration source."""

    @abstractmethod
    def export(
        self,
        *,
        module_version: str,
        behavior_policy: BehaviorPolicyRelease,
        evaluation_policy: EvaluationPolicyRelease,
        retry_policy: RetryPolicyRelease,
        execution_profile: ExecutionProfileRelease | None,
    ) -> ModuleExport:
        """Compile one immutable Module release closure."""

    @abstractmethod
    def project(
        self,
        registry: RuntimeReleaseRegistry,
        exported: ModuleExport,
    ) -> dict[str, Any]:
        """Return exact registered facts without writing authoring sources."""


@dataclass(frozen=True)
class ModuleReviewer(Module):
    """Reviewer role reusable across every subject domain."""

    source: ModuleRegistrationSource

    @classmethod
    def from_registration(
        cls,
        project_root: Path,
        *,
        skill_id: str,
        module_id: str,
    ) -> Self:
        return cls(
            source=load_module_registration(
                project_root,
                skill_id=skill_id,
                module_id=module_id,
            )
        )

    @staticmethod
    def _profile_blocker(
        source: ModuleRegistrationSource,
        execution_profile: ExecutionProfileRelease | None,
    ) -> str | None:
        if execution_profile is None:
            return EXECUTION_PROFILE_UNAVAILABLE
        if source.declared_operation_ids != TOOL_FREE_OPERATION_IDS:
            raise ValueError(
                "tool-requiring Reviewer cannot bind a tool-free Execution Profile"
            )
        if execution_profile.execution_mode != "tool_free":
            raise ValueError("Reviewer requires execution_mode=tool_free")
        if execution_profile.semantic_input_delivery_mode != "inline":
            raise ValueError("Reviewer requires inline semantic input")
        if execution_profile.tool_policy != ():
            raise ValueError("tool-free Reviewer Profile cannot grant tools")
        if execution_profile.network_policy != "denied":
            raise ValueError("tool-free Reviewer network_policy must be denied")
        if execution_profile.transport_kind not in source.compatible_transport_kinds:
            raise ValueError("Reviewer does not declare the Profile transport")
        return None

    def export(
        self,
        *,
        module_version: str,
        behavior_policy: BehaviorPolicyRelease,
        evaluation_policy: EvaluationPolicyRelease,
        retry_policy: RetryPolicyRelease,
        execution_profile: ExecutionProfileRelease | None,
    ) -> ModuleExport:
        blocker = self._profile_blocker(self.source, execution_profile)
        candidate = _candidate(
            self.source,
            module_version=module_version,
            behavior_policy=behavior_policy,
            evaluation_policy=evaluation_policy,
            retry_policy=retry_policy,
        )
        compiled = compile_agent_module_release(candidate)
        variant_candidate: ExecutionVariantPolicyReleaseCandidate | None = None
        variant: ExecutionVariantPolicyRelease | None = None
        if execution_profile is not None:
            variant_candidate = ExecutionVariantPolicyReleaseCandidate(
                policy_id=f"{self.source.module_id}_standalone_variant",
                policy_version="v1",
                origin_kind="standalone_module",
                origin_release_ref=compiled.module.release_ref,
                origin_release_sha256=compiled.module.release_sha256,
                bindings=(
                    ExecutionVariantProfileBindingCandidate(
                        position_id=self.source.module_id,
                        execution_profile_release_ref=execution_profile.release_ref,
                        execution_profile_release_sha256=(
                            execution_profile.release_sha256
                        ),
                    ),
                ),
            )
            variant = compile_execution_variant_policy_release(variant_candidate)
        return ModuleExport(
            source=self.source,
            candidate=candidate,
            compiled=compiled,
            execution_profile=execution_profile,
            execution_variant_candidate=variant_candidate,
            execution_variant=variant,
            execution_blocker_code=blocker,
        )

    @staticmethod
    def _registered_skill_id(
        registry: RuntimeReleaseRegistry,
        module: ModuleRelease,
    ) -> str:
        if module.prompt_bundle_ref is None or module.prompt_bundle_sha256 is None:
            raise ValueError("Reviewer ModuleRelease has no Prompt Bundle")
        prompt_bundle = registry.get_prompt_bundle(
            module.prompt_bundle_ref,
            module.prompt_bundle_sha256,
        )
        instruction_components = []
        for member in prompt_bundle.members:
            component = registry.get_prompt_component(
                member.member_ref,
                member.member_sha256,
            )
            if component.component_kind is PromptComponentKind.TASK_INSTRUCTION:
                instruction_components.append(component)
        if len(instruction_components) != 1:
            raise ValueError("Reviewer Prompt Bundle has no unique task instruction")
        source_members = instruction_components[0].source_members
        if len(source_members) != 1:
            raise ValueError("Reviewer task instruction has no unique source")
        prefix = "skill-instruction:"
        source_ref = source_members[0].member_ref
        if not source_ref.startswith(prefix):
            raise ValueError("Reviewer instruction source is not Skill-owned")
        skill_id, separator, module_id = source_ref.removeprefix(prefix).rpartition(
            ":"
        )
        if not separator or module_id != module.module_id or not skill_id:
            raise ValueError("Reviewer instruction source identity is invalid")
        return skill_id

    def project(
        self,
        registry: RuntimeReleaseRegistry,
        exported: ModuleExport,
    ) -> dict[str, Any]:
        if exported.source != self.source:
            raise ValueError("Module export belongs to a different source")
        module = registry.get_module(
            exported.module_release.release_ref,
            exported.module_release.release_sha256,
        )
        input_schema = registry.get_schema_asset(
            module.input_schema_ref,
            module.input_schema_sha256,
        )
        output_schema = registry.get_schema_asset(
            module.output_schema_ref,
            module.output_schema_sha256,
        )
        prompt_bundle = registry.get_prompt_bundle(
            module.prompt_bundle_ref or "",
            module.prompt_bundle_sha256 or "",
        )
        behavior = registry.get_behavior_policy(
            module.behavior_policy_ref,
            module.behavior_policy_sha256,
        )
        evaluation = registry.get_evaluation_policy(
            module.evaluation_policy_ref,
            module.evaluation_policy_sha256,
        )
        retry = registry.get_retry_policy(
            module.retry_policy_ref,
            module.retry_policy_sha256,
        )
        profile_projection = None
        if exported.execution_profile is not None:
            profile = registry.get_execution_profile(
                exported.execution_profile.release_ref,
                exported.execution_profile.release_sha256,
            )
            profile_projection = {
                "release_ref": profile.release_ref,
                "release_sha256": profile.release_sha256,
            }
        variant_projection = None
        if exported.execution_variant is not None:
            variant = registry.get_execution_variant_policy(
                exported.execution_variant.release_ref,
                exported.execution_variant.release_sha256,
            )
            variant_projection = {
                "release_ref": variant.release_ref,
                "release_sha256": variant.release_sha256,
            }
        active_release_ref = registry.snapshot().active_release_refs.get(
            f"runtime_module:{module.module_id}"
        )
        return {
            "skill_id": self._registered_skill_id(registry, module),
            "module_id": module.module_id,
            "module_release_ref": module.release_ref,
            "module_release_sha256": module.release_sha256,
            "owner_contract_ref": module.owner_contract_ref,
            "owner_contract_sha256": module.owner_contract_sha256,
            "input_schema_ref": input_schema.release_ref,
            "input_schema_sha256": input_schema.schema_sha256,
            "output_schema_ref": output_schema.release_ref,
            "output_schema_sha256": output_schema.schema_sha256,
            "prompt_bundle_ref": prompt_bundle.release_ref,
            "prompt_bundle_sha256": prompt_bundle.release_sha256,
            "declared_operation_ids": list(module.declared_operation_ids),
            "compatible_transport_kinds": list(module.compatible_transport_kinds),
            "behavior_policy_ref": behavior.release_ref,
            "behavior_policy_sha256": behavior.release_sha256,
            "evaluation_policy_ref": evaluation.release_ref,
            "evaluation_policy_sha256": evaluation.release_sha256,
            "retry_policy_ref": retry.release_ref,
            "retry_policy_sha256": retry.release_sha256,
            "entry_policy": module.entry_policy.value,
            "output_resolution_policy": module.output_resolution_policy.value,
            "active": active_release_ref == module.release_ref,
            "execution_profile": profile_projection,
            "execution_variant": variant_projection,
            "execution_blocker_code": exported.execution_blocker_code,
        }


__all__ = [
    "EXECUTION_PROFILE_UNAVAILABLE",
    "Module",
    "ModuleExport",
    "ModuleReviewer",
    "TOOL_FREE_OPERATION_IDS",
]
