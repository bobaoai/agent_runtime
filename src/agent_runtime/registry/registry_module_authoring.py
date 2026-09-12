"""Runtime-owned authoring interfaces for immutable Module definitions."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import Any, ClassVar, Self, TYPE_CHECKING

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ModuleEntryPolicy,
    ModuleExecutionRequirements,
    ModuleRelease,
    OutputResolutionPolicy,
    PromptComponentKind,
    RetryPolicyRelease,
    partition_module_operation_ids,
)
from .registry_module_loading import (
    MODULE_REGISTRATION_SCHEMA_VERSION,
    ModuleRegistrationSource,
    load_module_registration,
)
from .registry_release_compilation import (
    AgentModuleReleaseCandidate,
    CompiledAgentModuleRelease,
    _compile_module_policies,
    compile_agent_module_release,
    runtime_owned_policy_schema_assets,
)
from .registry_release_registration import RuntimeReleaseBundle, RuntimeReleaseRegistry

if TYPE_CHECKING:
    from .registry_workflow_authoring import Workflow


# Kept for callers interpreting historical authoring errors. New export has
# no model/Profile arguments and does not emit a missing-Profile blocker.
EXECUTION_PROFILE_UNAVAILABLE = "MODULE_EXECUTION_PROFILE_UNAVAILABLE"
MODULE_OPERATION_DECLARATION_INVALID = "MODULE_OPERATION_DECLARATION_INVALID"
MODULE_EXECUTION_PROFILE_INCOMPATIBLE = "MODULE_EXECUTION_PROFILE_INCOMPATIBLE"


class ModuleAuthoringError(ValueError):
    """An authoring failure with a stable error_code and a diagnostic.

    MODULE_OPERATION_DECLARATION_INVALID identifies an invalid model/operation
    declaration. Other source, schema, requirement and Registry errors retain
    their native ValueError or lookup exception. Export never tests executor
    availability, registers records or invokes a provider.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


@dataclass(frozen=True)
class ModuleExport:
    """One compiled definition and its immutable technical Policy dependencies.

    source is the captured task source; candidate is the compiler input;
    compiled contains the Schema, Prompt and Module records. Policies are
    derived by Runtime from this definition's requirements. module_release
    returns the definition and origin_bundle supplies its exact dependency
    closure. Neither includes a model selection, Profile or Variant.

    This is an in-memory authoring result. Registration and execution use
    their existing public APIs; export is not proof of either action.
    """

    source: ModuleRegistrationSource
    candidate: AgentModuleReleaseCandidate
    compiled: CompiledAgentModuleRelease
    behavior_policy: BehaviorPolicyRelease
    evaluation_policy: EvaluationPolicyRelease
    retry_policy: RetryPolicyRelease

    @property
    def module_release(self) -> ModuleRelease:
        """Return the compiled immutable ModuleRelease without reading a store.

        The ref and hash identify the fixed Module definition. Profile and
        Variant choices are excluded from this Module identity. This property
        does not prove that the release has been registered or executed.
        """
        return self.compiled.module

    @property
    def origin_bundle(self) -> RuntimeReleaseBundle:
        """Return the Module's dependency-closed RuntimeReleaseBundle.

        Contains policy schemas, input/output schemas, Prompt Components,
        Prompt Bundle, Behavior/Evaluation/Retry Policies and the Module.
        Execution Profiles and Variant Policies are intentionally omitted.
        Runtime's public execution preparation resolves model bindings later;
        callers of this authoring API do not assemble them. The lower-level
        Registry may still accept explicitly prepared binding records through
        its separate bundle API. No store write or active-pointer change occurs.

        Raises RuntimeError if required Runtime-owned policy schema assets
        cannot be resolved. Restore a consistent Runtime installation instead
        of substituting hand-written schema records.
        """

        policy_refs = {
            self.behavior_policy.policy_schema_ref,
            self.evaluation_policy.policy_schema_ref,
            self.retry_policy.policy_schema_ref,
        }
        policy_schema_assets = tuple(
            asset
            for asset in runtime_owned_policy_schema_assets()
            if asset.release_ref in policy_refs
        )
        if {asset.release_ref for asset in policy_schema_assets} != policy_refs:
            raise RuntimeError("Runtime-owned Module Policy schemas are incomplete")
        return RuntimeReleaseBundle(
            schema_assets=(*policy_schema_assets, *self.compiled.schema_assets),
            prompt_components=self.compiled.prompt_components,
            prompt_bundles=(self.compiled.prompt_bundle,),
            behavior_policies=(self.behavior_policy,),
            evaluation_policies=(self.evaluation_policy,),
            retry_policies=(self.retry_policy,),
            modules=(self.compiled.module,),
        )


@dataclass(frozen=True)
class Module:
    """Author a fixed Agent Module using the existing compiler and Registry.

    Args:
        source: Captured runtime_module_registration_v4 task source. Legacy
            v2/v3 sources must be migrated explicitly; their configuration is
            never silently ignored. Persisted old releases have separate codecs.
        execution_requirements: Explicit requirements for ordinary Module.
            A subclass with a fixed class preset uses it when this is None and
            rejects a caller override. Ordinary Module has no default tools.
        declared_operation_ids: Exactly one model operation and any real domain
            operations. Native read/search/shell are requirements, not automatic
            authority for declared domain effects.
        entry_policy: Existing independent-Module entry rule. Defaults to
            standalone_allowed; callers can retain workflow_bound.
        output_resolution_policy: Existing Agent single-result resolution
            rule. Defaults to evaluated_single, not a Reviewer output format.
    Raises:
        ValueError: Invalid source format, requirements or task rules.
        ModuleAuthoringError: Invalid operation declaration.
    Effects:
        Construction captures values in memory. It never reads files, registers
        records, chooses a model or grants runtime access to host resources.
        High-level non-Agent authoring is not added by this class; existing
        NonAgentModuleReleaseCandidate and compiler remain its separate path.
    """

    source: ModuleRegistrationSource
    _: KW_ONLY
    execution_requirements: ModuleExecutionRequirements | None = None
    declared_operation_ids: tuple[str, ...] = ("model_execute",)
    entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.STANDALONE_ALLOWED
    output_resolution_policy: OutputResolutionPolicy = OutputResolutionPolicy.EVALUATED_SINGLE
    _default_execution_requirements: ClassVar[ModuleExecutionRequirements | None] = None

    def __post_init__(self) -> None:
        if type(self.source) is not ModuleRegistrationSource:
            raise ValueError("source must be a ModuleRegistrationSource")
        if self.source.schema_version != MODULE_REGISTRATION_SCHEMA_VERSION:
            raise ValueError("New Module authoring requires a migrated v4 task source")
        if (self.source.declared_operation_ids != ()
                or self.source.compatible_transport_kinds != ()
                or any(value is not None for value in (
                    self.source.behavior_policy_ref, self.source.evaluation_policy_ref,
                    self.source.retry_policy_ref, self.source.entry_policy,
                    self.source.output_resolution_policy))):
            raise ValueError("v4 task source cannot carry legacy execution configuration")
        preset = type(self)._default_execution_requirements
        requirements = self.execution_requirements
        if preset is not None:
            if requirements is not None:
                raise ValueError("Fixed Module environment cannot be overridden")
            requirements = preset
        if type(requirements) is not ModuleExecutionRequirements:
            raise ValueError("Module requires explicit ModuleExecutionRequirements")
        requirements.validate()
        object.__setattr__(self, "execution_requirements", requirements)
        try:
            partition_module_operation_ids(self.declared_operation_ids)
        except ValueError as exc:
            raise ModuleAuthoringError(MODULE_OPERATION_DECLARATION_INVALID, str(exc)) from exc
        if type(self.entry_policy) is not ModuleEntryPolicy:
            raise ValueError("entry_policy must be a ModuleEntryPolicy")
        if type(self.output_resolution_policy) is not OutputResolutionPolicy:
            raise ValueError("output_resolution_policy must be an OutputResolutionPolicy")

    @staticmethod
    def to_workflow(
        exported: ModuleExport, *, workflow_id: str | None = None,
    ) -> Workflow:
        """Compose an exact Module export into a one-node Workflow without IO.

        Args:
            exported: Exact ModuleExport already compiled by the author. This
                static method consumes that value, not an author's instance
                state; it never calls export or reloads authoring source.
            workflow_id: Explicit snake_case graph name. Only None selects the
                Module ID as the default. The Workflow version is the Module
                version; its sole node is named module. Explicit graphs with
                different nodes or mappings use Workflow.from_graph instead.
        Returns:
            Workflow authoring object retaining the exact Module export and
            declared operations. Call its export method to compile and validate
            the dependency closure, then use the public Registry to register it.
            A single node is still a Workflow, in a separate identity namespace.
        Raises:
            WorkflowAuthoringError: WORKFLOW_MODULE_CLOSURE_INVALID if exported
                is not an exact ModuleExport. Graph/dependency validation in
                Workflow.export retains the same existing error contract.
            ValueError: workflow_id is empty or not a valid snake_case name.
        Effects:
            Constructs in memory only. No source read, Module recompilation,
            default resolution, Registry write, model selection or execution.
            Reviewer subclasses inherit this method unchanged. Ordinary Modules
            acquire no Reviewer rules or permissions by using it.
        """
        from ..contracts.registry_release_definition import WorkflowEdge, WorkflowNodeKind
        from ..foundation.foundation_contract_validation import validate_snake_case_name
        from .registry_release_compilation import WorkflowReleaseCandidate, WorkflowNodeReleaseCandidate
        from .registry_workflow_authoring import (
            Workflow, WorkflowAuthoringError, WORKFLOW_MODULE_CLOSURE_INVALID,
        )

        if type(exported) is not ModuleExport:
            raise WorkflowAuthoringError(
                WORKFLOW_MODULE_CLOSURE_INVALID, "exported must be an exact ModuleExport",
            )
        module = exported.module_release
        workflow_id = module.module_id if workflow_id is None else workflow_id
        validate_snake_case_name("workflow_id", workflow_id)
        version = module.module_version
        suffix = f"{workflow_id}@{version}"
        candidate = WorkflowReleaseCandidate(
            workflow_id=workflow_id, workflow_version=version, workflow_contract_version="v1",
            owner_contract_ref=exported.source.owner_contract_ref,
            owner_contract_content=exported.source.owner_contract_content,
            graph_ref="workflow-graph:" + suffix, initial_node_id="module",
            nodes=(WorkflowNodeReleaseCandidate(
                node_id="module", node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref, module_release_sha256=module.release_sha256,
                input_mapping_ref="input-mapping:" + suffix,
                input_mapping_document={"task_input": "payload"}),),
            edges=(WorkflowEdge("module", "complete", None, True),),
            authorization_manifest_ref="authorization-manifest:" + suffix,
            authorization_manifest_document={"module_release_ref": module.release_ref,
                "module_release_sha256": module.release_sha256, "operations": list(module.declared_operation_ids)},
            execution_binding_ref="execution-binding:" + suffix,
            execution_binding_document={"schema_version": "workflow_execution_binding_v1",
                "variant_policy_family": "execution_variant_policy", "workflow_id": workflow_id},
        )
        return Workflow.from_graph(candidate, module_exports=(exported,))

    @classmethod
    def from_registration(
        cls,
        project_root: Path,
        *,
        skill_id: str,
        module_id: str,
        execution_requirements: ModuleExecutionRequirements | None = None,
        declared_operation_ids: tuple[str, ...] = ("model_execute",),
        entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.STANDALONE_ALLOWED,
        output_resolution_policy: OutputResolutionPolicy = OutputResolutionPolicy.EVALUATED_SINGLE,
    ) -> Self:
        """Load exactly one source then construct cls with the same task rules.

        Reads the declared source once through load_module_registration, with
        its exact-file and path checks. Subclasses inherit this method and
        supply only their fixed environment. This generic path does not assert
        a role's output format: Reviewer source authoring/registration uses
        load_reviewer_registration before constructing the preset Module.

        Args:
            project_root: Explicit source root; only the declared Skill/Module
                source closure is read, never sibling modules.
            skill_id: Exact Skill directory identity.
            module_id: Exact Module directory identity.
            execution_requirements: Explicit ordinary requirements, or None
                to consume the subclass's fixed environment.
            declared_operation_ids: Model operation and real domain operations.
            entry_policy: Independent or Workflow-bound entry rule.
            output_resolution_policy: Existing output-resolution rule.
        Returns:
            An instance of cls holding captured source and resolved requirements.
        Raises:
            ValueError: Invalid source, environment or task rules.
            ModuleAuthoringError: Invalid model/operation declaration.

        Raises the loader's ValueError or constructor error. No source write,
        registration, model choice or provider call occurs.
        """
        source = load_module_registration(
            project_root, skill_id=skill_id, module_id=module_id,
        )
        return cls(source, execution_requirements=execution_requirements,
                   declared_operation_ids=declared_operation_ids,
                   entry_policy=entry_policy,
                   output_resolution_policy=output_resolution_policy)

    def export(self, *, module_version: str) -> ModuleExport:
        """Compile this captured task and requirements into one fixed export.

        Only module_version is selected here. Behavior/Retry derive from the
        requirements; Evaluation uses the existing entry-based mode none.
        Input/output schemas and Prompt compilation use the existing generic
        compiler. A valid non-review schema is accepted without Reviewer checks.

        Returns a ModuleExport with no Profile, Variant or execution blocker.
        Invalid version/schema/compiler inputs retain their native errors.
        No source reload, Registry IO, model resolution or execution occurs.
        """
        requirements = self.execution_requirements
        behavior, evaluation, retry = _compile_module_policies(requirements)
        source = self.source
        candidate = AgentModuleReleaseCandidate(
            module_id=source.module_id, module_version=module_version,
            owner_contract_ref=source.owner_contract_ref,
            owner_contract_content=source.owner_contract_content,
            input_schema_ref=source.input_schema_ref,
            input_schema_document=source.input_schema_document,
            output_schema_ref=source.output_schema_ref,
            output_schema_document=source.output_schema_document,
            instruction_source_ref=source.instruction_source_ref,
            instruction_text=source.instruction_text,
            declared_operation_ids=self.declared_operation_ids,
            compatible_transport_kinds=(),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref, retry_policy_sha256=retry.release_sha256,
            entry_policy=self.entry_policy,
            output_resolution_policy=self.output_resolution_policy,
            execution_requirements=requirements,
        )
        return ModuleExport(source=source, candidate=candidate,
                            compiled=compile_agent_module_release(candidate),
                            behavior_policy=behavior, evaluation_policy=evaluation,
                            retry_policy=retry)

    @staticmethod
    def _registered_skill_id(
        registry: RuntimeReleaseRegistry,
        module: ModuleRelease,
    ) -> str:
        if module.prompt_bundle_ref is None or module.prompt_bundle_sha256 is None:
            raise ValueError("Module ModuleRelease has no Prompt Bundle")
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
            raise ValueError("Module Prompt Bundle has no unique task instruction")
        source_members = instruction_components[0].source_members
        if len(source_members) != 1:
            raise ValueError("Module task instruction has no unique source")
        prefix = "skill-instruction:"
        source_ref = source_members[0].member_ref
        if not source_ref.startswith(prefix):
            raise ValueError("Module instruction source is not Skill-owned")
        skill_id, separator, module_id = source_ref.removeprefix(prefix).rpartition(
            ":"
        )
        if not separator or module_id != module.module_id or not skill_id:
            raise ValueError("Module instruction source identity is invalid")
        return skill_id

    def project(
        self, registry: RuntimeReleaseRegistry, exported: ModuleExport,
    ) -> dict[str, Any]:
        """Read the exact registered definition and dependencies as a plain dict.

        Returns Skill/Module identity, owner/schema/Prompt/Policy ref/hash pairs,
        declared operations, entry/output rules and execution_requirements.
        Source and captured task settings must match this Module; exact Registry
        lookup failures propagate. The supplied Registry snapshot owns facts,
        not this instance's defaults. No active pointer or execution choice is
        resolved and no source, store or provider is changed.
        """
        if type(exported) is not ModuleExport or exported.source != self.source:
            raise ValueError("Module export belongs to a different source")
        expected = {
            "execution_requirements": self.execution_requirements,
            "declared_operation_ids": self.declared_operation_ids,
            "entry_policy": self.entry_policy,
            "output_resolution_policy": self.output_resolution_policy,
        }
        if any(getattr(exported.candidate, name) != value
               or getattr(exported.module_release, name) != value
               for name, value in expected.items()):
            raise ValueError("Module export differs from captured task requirements")
        module = registry.get_module(
            exported.module_release.release_ref, exported.module_release.release_sha256,
        )
        input_schema = registry.get_schema_asset(module.input_schema_ref, module.input_schema_sha256)
        output_schema = registry.get_schema_asset(module.output_schema_ref, module.output_schema_sha256)
        prompt_bundle = registry.get_prompt_bundle(module.prompt_bundle_ref or "", module.prompt_bundle_sha256 or "")
        behavior = registry.get_behavior_policy(module.behavior_policy_ref, module.behavior_policy_sha256)
        evaluation = registry.get_evaluation_policy(module.evaluation_policy_ref, module.evaluation_policy_sha256)
        retry = registry.get_retry_policy(module.retry_policy_ref, module.retry_policy_sha256)
        requirements = module.get_execution_requirements()
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
            "behavior_policy_ref": behavior.release_ref,
            "behavior_policy_sha256": behavior.release_sha256,
            "evaluation_policy_ref": evaluation.release_ref,
            "evaluation_policy_sha256": evaluation.release_sha256,
            "retry_policy_ref": retry.release_ref,
            "retry_policy_sha256": retry.release_sha256,
            "entry_policy": module.entry_policy.value,
            "output_resolution_policy": module.output_resolution_policy.value,
            "execution_requirements": requirements.as_dict() if requirements is not None else None,
        }


class ModuleReviewer(Module):
    """Supply a fixed environment and inherit Module authoring unchanged.

    Runtime fixes isolated context, read/search/shell, private scratch, denied
    tool network, inline native output, a 1200-second budget and three attempts.
    Model choice and concrete host resources are resolved at execution time.
    The specialized load_reviewer_registration entry checks common output
    format; inherited generic loading/export does not grant that guarantee.
    """

    _default_execution_requirements = ModuleExecutionRequirements(
        context_isolation="workflow_execution_isolated",
        execution_mode="agent", semantic_input_delivery_mode="inline",
        attempt_workspace_policy="own_draft_read_write",
        tool_policy=("read", "search", "shell"), gateway_access_reasons=(),
        network_policy="denied", output_constraint_mode="native_structured_output",
        timeout_seconds=1200, max_attempts=3,
    )


__all__ = [
    "EXECUTION_PROFILE_UNAVAILABLE",
    "MODULE_EXECUTION_PROFILE_INCOMPATIBLE",
    "MODULE_OPERATION_DECLARATION_INVALID",
    "Module",
    "ModuleAuthoringError",
    "ModuleExport",
    "ModuleReviewer",
]
