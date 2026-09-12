"""Runtime-owned authoring interfaces for immutable Module releases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
import json
from pathlib import Path
from collections.abc import Mapping
from typing import Any, Literal, Self, TYPE_CHECKING

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleRelease,
    PromptComponentKind,
    RetryPolicyRelease,
    ReviewerDefaults,
    partition_module_operation_ids,
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
    runtime_owned_policy_schema_assets,
)
from .registry_release_registration import RuntimeReleaseBundle, RuntimeReleaseRegistry
from .registry_reviewer_defaults import content_version, resolve_reviewer_policy, reviewer_execution_profile

if TYPE_CHECKING:
    from .registry_workflow_authoring import Workflow


EXECUTION_PROFILE_UNAVAILABLE = "MODULE_EXECUTION_PROFILE_UNAVAILABLE"
MODULE_OPERATION_DECLARATION_INVALID = "MODULE_OPERATION_DECLARATION_INVALID"
MODULE_EXECUTION_PROFILE_INCOMPATIBLE = "MODULE_EXECUTION_PROFILE_INCOMPATIBLE"

_REVIEW_OUTPUT_FIELDS = {"verdict", "check_results", "findings", "safe_next_step"}
_REVIEW_CHECK_FIELDS = {"check_id", "disposition", "assessment", "finding_ids"}
_REVIEW_FINDING_FIELDS = {"finding_id", "severity", "evidence", "requirement", "impact", "accountable_owner_ref", "required_change"}
_REVIEW_EVIDENCE_FIELDS = {"source_ref", "locator", "observation"}
_REVIEW_SCHEMA_SCOPE_KEYS = {"$id", "$schema", "$defs", "$anchor", "$dynamicAnchor"}
_REVIEW_SCHEMA_ANNOTATION_KEYS = {"title", "description", "$comment"}


def _review_schema_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Reviewer output schema {label} must be an object")
    return value


def _review_schema_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"Reviewer output schema {label} fields differ from the common format")


def _review_schema_required(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    required = value.get("required")
    if (
        not isinstance(required, list)
        or not all(isinstance(name, str) for name in required)
        or set(required) != expected
    ):
        raise ValueError(f"Reviewer output schema {label} required fields differ from the common format")


def _review_schema_local_scope(value: Mapping[str, Any], label: str) -> None:
    if _REVIEW_SCHEMA_SCOPE_KEYS.intersection(value):
        raise ValueError(f"Reviewer output schema {label} cannot change local reference scope")


def _review_schema_closed_object(value: Mapping[str, Any], label: str) -> None:
    if (
        value.get("type") != "object"
        or value.get("additionalProperties") is not False
        or "patternProperties" in value
    ):
        raise ValueError(f"Reviewer output schema {label} must be one closed object")


def _review_schema_ref(value: Any, expected_ref: str, label: str) -> None:
    node = _review_schema_object(value, label)
    if node.get("$ref") != expected_ref or set(node) - ({"$ref"} | _REVIEW_SCHEMA_ANNOTATION_KEYS):
        raise ValueError(f"Reviewer output schema {label} must use the direct local ref {expected_ref}")


def _review_schema_string(value: Any, label: str) -> Mapping[str, Any]:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    if node.get("type") != "string" or node.get("minLength") != 1:
        raise ValueError(f"Reviewer output schema {label} must be a non-empty string")
    return node


def _review_schema_enum(value: Any, expected: tuple[str, ...], label: str) -> None:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    members = node.get("enum")
    if (
        node.get("type") != "string"
        or "$ref" in node
        or not isinstance(members, list)
        or not all(isinstance(member, str) for member in members)
        or set(members) != set(expected)
    ):
        raise ValueError(f"Reviewer output schema {label} enum differs from the common format")


def _review_schema_array(value: Any, item_ref: str | None, label: str) -> Mapping[str, Any]:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    if node.get("type") != "array" or "prefixItems" in node:
        raise ValueError(f"Reviewer output schema {label} must use one homogeneous array contract")
    items = _review_schema_object(node.get("items"), f"{label}.items")
    _review_schema_local_scope(items, f"{label}.items")
    if item_ref is not None:
        _review_schema_ref(items, item_ref, f"{label}.items")
    return node


def _validate_reviewer_output_schema(schema_document: Mapping[str, object]) -> None:
    schema = _review_schema_object(schema_document, "root")
    _review_schema_closed_object(schema, "root")
    properties = _review_schema_object(schema.get("properties"), "properties")
    _review_schema_fields(properties, _REVIEW_OUTPUT_FIELDS, "top-level")
    _review_schema_required(schema, _REVIEW_OUTPUT_FIELDS, "top-level")
    _review_schema_enum(properties["verdict"], ("passed", "non_pass", "blocked"), "verdict")
    _review_schema_string(properties["safe_next_step"], "safe_next_step")
    _review_schema_array(properties["check_results"], "#/$defs/check_result", "check_results")
    _review_schema_array(properties["findings"], "#/$defs/finding", "findings")

    definitions = _review_schema_object(schema.get("$defs"), "$defs")
    check = _review_schema_object(definitions.get("check_result"), "$defs.check_result")
    _review_schema_local_scope(check, "$defs.check_result")
    _review_schema_closed_object(check, "check_result")
    check_properties = _review_schema_object(check.get("properties"), "check_result.properties")
    _review_schema_fields(check_properties, _REVIEW_CHECK_FIELDS, "check_result")
    _review_schema_required(check, _REVIEW_CHECK_FIELDS, "check_result")
    _review_schema_string(check_properties["check_id"], "check_result.check_id")
    _review_schema_enum(check_properties["disposition"], ("passed", "finding", "not_applicable", "not_run"), "check_result.disposition")
    _review_schema_string(check_properties["assessment"], "check_result.assessment")
    finding_ids = _review_schema_array(check_properties["finding_ids"], None, "check_result.finding_ids")
    if finding_ids.get("uniqueItems") is not True:
        raise ValueError("Reviewer output schema finding_ids must contain unique values")
    finding_id_item = _review_schema_object(
        finding_ids["items"], "check_result.finding_ids.items"
    )
    if finding_id_item.get("type") != "string":
        raise ValueError("Reviewer output schema finding_ids items must be strings")

    finding = _review_schema_object(definitions.get("finding"), "$defs.finding")
    _review_schema_local_scope(finding, "$defs.finding")
    _review_schema_closed_object(finding, "finding")
    finding_properties = _review_schema_object(finding.get("properties"), "finding.properties")
    finding_required = finding.get("required")
    if (
        not _REVIEW_FINDING_FIELDS.issubset(finding_properties)
        or not isinstance(finding_required, list)
        or not all(isinstance(name, str) for name in finding_required)
        or not _REVIEW_FINDING_FIELDS.issubset(set(finding_required))
    ):
        raise ValueError("Reviewer output schema finding common fields are incomplete")
    _review_schema_string(finding_properties["finding_id"], "finding.finding_id")
    _review_schema_enum(finding_properties["severity"], ("block", "fix", "note"), "finding.severity")
    _review_schema_ref(finding_properties["evidence"], "#/$defs/evidence", "finding.evidence")
    for name in ("requirement", "impact", "accountable_owner_ref", "required_change"):
        _review_schema_string(finding_properties[name], f"finding.{name}")

    evidence = _review_schema_object(definitions.get("evidence"), "$defs.evidence")
    _review_schema_local_scope(evidence, "$defs.evidence")
    _review_schema_closed_object(evidence, "evidence")
    evidence_properties = _review_schema_object(evidence.get("properties"), "evidence.properties")
    _review_schema_fields(evidence_properties, _REVIEW_EVIDENCE_FIELDS, "evidence")
    _review_schema_required(evidence, _REVIEW_EVIDENCE_FIELDS, "evidence")
    for name in sorted(_REVIEW_EVIDENCE_FIELDS):
        _review_schema_string(evidence_properties[name], f"evidence.{name}")


class ModuleAuthoringError(ValueError):
    """An authoring failure with a machine-readable ``error_code``.

    ``error_code`` is the stable code; the exception message adds a diagnostic.
    ``MODULE_OPERATION_DECLARATION_INVALID`` means the source operation list
    cannot be partitioned into model and non-model operations. Correct that
    source through its owner. ``MODULE_EXECUTION_PROFILE_INCOMPATIBLE`` means
    the supplied Profile conflicts with the source transport or tool boundary.
    Supply an approved compatible Profile; changing the Module's declarations
    is a separate source change, not a way to bypass this check.

    ``EXECUTION_PROFILE_UNAVAILABLE`` is returned only by fully explicit legacy
    Reviewer export without a fixed default snapshot when no Profile is supplied.
    Ordinary definition-only Reviewer export has no Profile, no Variant and
    ``ModuleExport.execution_blocker_code=None``. Neither case prevents
    compilation of the fixed definition. Other source, schema, policy
    and Registry validation errors retain their own exception contracts;
    this class does not wrap every possible failure. No Registry write or
    provider invocation occurs during authoring.
    """

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


def _candidate(
    source: ModuleRegistrationSource,
    *,
    module_version: str,
    behavior_policy: BehaviorPolicyRelease,
    evaluation_policy: EvaluationPolicyRelease,
    retry_policy: RetryPolicyRelease,
) -> AgentModuleReleaseCandidate:
    if source.behavior_policy_ref is not None and source.behavior_policy_ref != behavior_policy.release_ref:
        raise ValueError("Module behavior_policy_ref differs from supplied release")
    if source.evaluation_policy_ref is not None and source.evaluation_policy_ref != evaluation_policy.release_ref:
        raise ValueError("Module evaluation_policy_ref differs from supplied release")
    if source.retry_policy_ref is not None and source.retry_policy_ref != retry_policy.release_ref:
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
    """Compiled Module definition and separately supplied execution binding.

    ``source`` is the loaded, path-free authoring content; ``candidate`` is
    the compiler input; ``compiled`` contains the immutable Module, Prompt
    and Schema records. ``behavior_policy``, ``evaluation_policy`` and
    ``retry_policy`` are the exact Module dependencies supplied to export.
    ``module_release`` exposes the resulting fixed definition.

    ``execution_profile`` and ``execution_variant`` are separate from that
    definition. ``execution_variant_candidate`` is the compiler input for
    the optional standalone binding. With no Profile, all three are None.
    Ordinary definition-only Reviewer export also has no blocker. The fully
    explicit legacy path without a fixed default snapshot retains
    EXECUTION_PROFILE_UNAVAILABLE. With a compatible Profile the blocker is None;
    this is an authoring check, not
    proof that an Adapter, provider login, storage or execution is available.

    ``origin_bundle`` contains only the Module definition and its immutable
    dependencies. Definition registration uses that bundle alone. The lower-level
    explicit binding path can combine separately selected Profile and Variant
    records with it. For a Workflow, bind the Workflow's exact node positions
    using a Workflow Variant Policy; the standalone Variant produced here
    cannot be substituted for a Workflow binding. Exporting creates in-memory
    records only; registration, activation and execution are separate actions.
    """

    source: ModuleRegistrationSource
    candidate: AgentModuleReleaseCandidate
    compiled: CompiledAgentModuleRelease
    behavior_policy: BehaviorPolicyRelease
    evaluation_policy: EvaluationPolicyRelease
    retry_policy: RetryPolicyRelease
    execution_profile: ExecutionProfileRelease | None
    execution_variant_candidate: ExecutionVariantPolicyReleaseCandidate | None
    execution_variant: ExecutionVariantPolicyRelease | None
    execution_blocker_code: str | None

    @property
    def module_release(self) -> ModuleRelease:
        """Return the compiled immutable ModuleRelease without reading a store.

        The ref and hash identify the fixed Module definition. Profile and
        Variant choices are excluded from this Module identity. This property
        does not prove that the release has been registered or activated.
        """
        return self.compiled.module

    @property
    def origin_bundle(self) -> RuntimeReleaseBundle:
        """Return the Module's dependency-closed RuntimeReleaseBundle.

        Contains policy schemas, input/output schemas, Prompt Components,
        Prompt Bundle, Behavior/Evaluation/Retry Policies and the Module.
        Execution Profiles and Variant Policies are intentionally omitted.
        The caller supplies them separately when forming an executable
        registration bundle. No store write or active-pointer change occurs.

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


class Module(ABC):
    """Author a fixed Module definition; execution consumes ModuleRelease.

    Implementations load explicit authoring sources, export immutable records
    and inspect registered facts. They do not constitute running Agents.
    Register an export through the public Registry API, then invoke the
    registered target through Execution. A new task input is a new execution
    of a selected definition, not a reason to reload or re-register its source.
    ModuleReviewer is the concrete implementation for Reviewer definitions.

    ``to_workflow`` composes an exact ModuleExport into a one-node Workflow.
    Subclasses inherit this common capability without inheriting Reviewer
    policies or output constraints. Workflow remains an independent graph
    object. Its default ID equals the Module ID; its kind distinguishes it
    from the Module. Explicit graph composition keeps its supplied names.
    """

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
    @abstractmethod
    def from_registration(
        cls,
        project_root: Path,
        *,
        skill_id: str,
        module_id: str,
    ) -> Self:
        """Load source under project_root for the exact skill_id and module_id.

        This is explicit authoring-time file access. Return a role-specific
        Module authoring object, without registering records or invoking a
        provider. See the concrete subclass for source and error details.
        """

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
        """Compile source and exact dependencies into a ModuleExport.

        module_version identifies the definition being compiled. The three
        policy arguments supply its immutable dependencies; execution_profile
        supplies an optional, separate execution choice. Export has no
        Registry or execution side effects. Concrete subclasses validate their
        role's schema and compatibility requirements.
        """

    @abstractmethod
    def project(
        self,
        registry: RuntimeReleaseRegistry,
        exported: ModuleExport,
    ) -> dict[str, Any]:
        """Read registry facts corresponding to exported and return a dict.

        The Registry must already contain the exact export and its required
        dependencies. This is a read-only projection, not registration,
        activation, source repair or evidence that a provider call succeeded.
        """


@dataclass(frozen=True)
class ModuleReviewer(Module):
    """Author and export one fixed Reviewer definition for a subject domain.

    Start here when preparing an approved Reviewer source for registration.
    ``source`` contains the exact instruction, owner contract, I/O schemas,
    operation and transport declarations, and policy references loaded by
    ``from_registration``. This frozen Python object is an authoring object,
    not a running Reviewer or a database registration. ``export`` validates
    the common Reviewer output format and compiles immutable release records;
    ``project`` reads the corresponding facts after registration.

    **Fixed definition and execution parameters**

    A ModuleRelease pins the owner, Prompt Bundle, schemas, operations,
    compatible transports, Behavior/Evaluation/Retry Policies and entry/output
    rules. The subject owner supplies review meaning and its semantic output
    validator. Runtime does not invent a checklist or turn a provider failure
    into a review verdict.

    Omitting Policy/Profile arguments uses Runtime's ReviewerDefaults without
    selecting a model. The fixed capability snapshot enters the Module hash;
    model selection happens independently during execution preparation. Explicit
    source restrictions are retained. Fully explicit legacy export calls keep
    their original definition shape and do not acquire new permissions. Runtime software upgrades never
    rewrite an already registered Module.

    An immutable ExecutionProfileRelease records the resolved provider, model,
    reasoning and fixed tool/network/workspace budget. An ExecutionVariantPolicyRelease
    binds that Profile to exact Module or Workflow positions. These two
    releases do not enter the Module release hash. Execution preparation combines
    a model choice with the fixed capabilities; a normal review supplies its
    candidate, goal, scope, context and prior findings as input, not as edits to fixed instructions
    or ad hoc provider parameters.

    **When versions change**

    New review material creates a new execution of the selected Module.
    Switching an approved compatible Profile preserves the same Module ref
    and hash when its version, source and Module dependencies are unchanged;
    the Profile and Variant binding have their own release identities.
    Changing instructions, schemas, policies or operation/transport
    declarations changes the Module definition and requires a new immutable
    Module release. In particular, adding transport compatibility is a source
    change, not a runtime override. The installed Runtime software version is
    separate from all of these registered definition/configuration versions.

    **Storage and invocation**

    The Skill Package owns editable authoring files. Compilation captures
    their content; the deployment-selected Registry stores published Module,
    Prompt, Schema, Policy and Profile records. Registered execution resolves
    those records rather than rebuilding a prompt from mutable Skill files.
    Each execution's frozen inputs, actual prompt, outputs, usage and failure
    evidence belong to Execution's record/content stores, not this object.
    The host supplies store locations, credentials and authorization interfaces;
    they are not embedded in the Reviewer source or selected by this class.

    Ordinary source registration uses ``register_reviewer`` or the installed
    ``agent-runtime-registry register-reviewer`` CLI. It resolves defaults,
    uses the inherited ``Module.to_workflow`` to create a one-node Workflow and
    saves its definition closure without
    a Profile or Variant. Use prepare_local_workflow_module to select the model
    for a new invocation, then supply the existing host ports to the kernel.
    The lower-level ``register_runtime_module_plugin`` still accepts an explicit
    bundle and store. Activation is a separate decision. For the
    existing single-node Workflow evaluation path, call
    ``run_registered_workflow_module`` using a registered Workflow and matching
    Variant Policy. That entry documents its actual limits and host inputs;
    successful export alone does not establish execution readiness.
    """

    source: ModuleRegistrationSource

    @classmethod
    def from_registration(
        cls,
        project_root: Path,
        *,
        skill_id: str,
        module_id: str,
    ) -> Self:
        """Load one exact Reviewer authoring source, without registration.

        Args:
            project_root: Explicit host source root. The loader reads the fixed
                `.claude/skills/<skill_id>/runtime_modules/<module_id>` closure,
                its Skill declaration and referenced owner Design within it.
            skill_id: Exact canonical kebab-case Skill identity.
            module_id: Exact canonical snake_case Reviewer Module identity.

        Returns:
            A ModuleReviewer containing validated, path-free source content.
            Later edits to source files do not update this captured object.

        Raises:
            ValueError: Invalid identity, path, source layout, registration,
                prompt or schema. Inspect the diagnostic and correct the exact
                source; the loader does not search for a replacement.
            OSError: File access failures not normalized by the source loader.

        Effects:
            Reads the explicit authoring closure only. Does not write files,
            register, activate, discover sibling Reviewers or invoke a model.
        """
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
        try:
            _, non_model_operations = partition_module_operation_ids(
                source.declared_operation_ids
            )
        except ValueError as exc:
            raise ModuleAuthoringError(
                MODULE_OPERATION_DECLARATION_INVALID,
                str(exc),
            ) from exc
        if execution_profile is None:
            return EXECUTION_PROFILE_UNAVAILABLE
        execution_profile.validate()
        if execution_profile.transport_kind not in source.compatible_transport_kinds:
            raise ModuleAuthoringError(
                MODULE_EXECUTION_PROFILE_INCOMPATIBLE,
                "Execution Profile transport is absent from Module compatibility",
            )
        native_workspace = (
            execution_profile.execution_mode == "agent"
            and execution_profile.semantic_input_delivery_mode == "inline"
            and execution_profile.attempt_workspace_policy == "own_draft_read_write"
            and not execution_profile.gateway_access_reasons
            and not non_model_operations
        )
        if not native_workspace and frozenset(execution_profile.tool_policy) != non_model_operations:
            raise ModuleAuthoringError(
                MODULE_EXECUTION_PROFILE_INCOMPATIBLE,
                "Execution Profile tool policy differs from Module non-model operations",
            )
        return None

    def export(
        self,
        *,
        module_version: str,
        behavior_policy: BehaviorPolicyRelease | None = None,
        evaluation_policy: EvaluationPolicyRelease | None = None,
        retry_policy: RetryPolicyRelease | None = None,
        execution_profile: ExecutionProfileRelease | None | Literal["runtime_default"] = None,
        release_registry: RuntimeReleaseRegistry | None = None,
        reviewer_defaults: ReviewerDefaults | None = None,
        model_id: str | None = None,
        reasoning_profile: str | None = None,
    ) -> ModuleExport:
        """Compile this captured source and validate optional Profile compatibility.

        Args:
            module_version: Version of the fixed Module definition. Keep it
                unchanged for unchanged Module content; a new input or Profile
                comparison does not by itself require a new Module version.
            behavior_policy: Optional exact override matching the source ref.
            evaluation_policy: Optional exact override. New v3 source omissions
                use entry-policy admission (evaluation_mode=none); an explicit
                module_candidate reference retains its candidate-only meaning.
            retry_policy: Optional exact override; Runtime defaults to three
                attempts including the first. This is a limit, not a scheduler.
            execution_profile: Omit or use None for definition-only export.
                Explicit runtime_default requests the legacy model-preset export.
                Fully explicit legacy calls retain their existing record shape.
            release_registry: Lookup for explicit non-default policy refs.
            reviewer_defaults: Previously frozen capability snapshot, normally
                supplied internally when registering the same version again.
            model_id: Independent model override for the default Claude path.
            reasoning_profile: Independent reasoning override; neither changes
                the Module capabilities. Explicit Profile and model overrides
                cannot be combined.

        Returns:
            ModuleExport with compiled Module/Prompt/Schema records and the
            supplied policies. A compatible Profile also produces a standalone
            Variant candidate/release. Definition-only default export produces no Variant and no blocker.
            Fully explicit legacy None calls retain EXECUTION_PROFILE_UNAVAILABLE. The
            standalone helper retains v1 for fully explicit legacy calls;
            new default-aware bindings use a deterministic content version.
            Exported bindings are candidates, not updates to a store.
            New Workflow execution preparation is separate from source registration.

        Raises:
            ModuleAuthoringError: MODULE_OPERATION_DECLARATION_INVALID for an
                invalid operation declaration; MODULE_EXECUTION_PROFILE_INCOMPATIBLE
                for an undeclared transport or incompatible tool boundary.
            ValueError: Invalid common Reviewer output schema, Profile, version,
                policy reference or compiler input. Definition-only export needs
                no Profile; only the legacy explicit path retains its missing-
                Profile blocker. JSON/schema errors retain their original types.

        Effects:
            Compiles in memory. No source reload, Registry write, activation,
            provider call or execution evidence is produced. Profile validation
            does not prove the corresponding Adapter can run in this environment.
        """
        _validate_reviewer_output_schema(
            json.loads(self.source.output_schema_document)
        )
        use_defaults = (execution_profile == "runtime_default" or reviewer_defaults is not None
                        or any(policy is None for policy in (behavior_policy, evaluation_policy, retry_policy)))
        if execution_profile != "runtime_default" and (model_id is not None or reasoning_profile is not None):
            raise ValueError("model overrides require the Runtime default Profile path")
        behavior_policy = resolve_reviewer_policy("behavior_policies", self.source.behavior_policy_ref, behavior_policy, release_registry)
        evaluation_policy = resolve_reviewer_policy("evaluation_policies", self.source.evaluation_policy_ref, evaluation_policy, release_registry)
        retry_policy = resolve_reviewer_policy("retry_policies", self.source.retry_policy_ref, retry_policy, release_registry)
        defaults = reviewer_defaults or (ReviewerDefaults() if use_defaults else None)
        if defaults is not None:
            defaults = replace(defaults, context_isolation=behavior_policy.policy_document()["context_isolation"],
                               max_attempts=retry_policy.policy_document()["max_attempts"])
            defaults.validate()
        if execution_profile == "runtime_default":
            execution_profile = reviewer_execution_profile(defaults, model_id=model_id, reasoning_profile=reasoning_profile)
        if execution_profile is not None and type(execution_profile) is not ExecutionProfileRelease:
            raise ValueError("execution_profile must be a Profile, None or runtime_default")
        blocker = self._profile_blocker(self.source, execution_profile)
        if defaults is not None and execution_profile is None:
            blocker = None  # A complete fixed definition does not require a model.
        if defaults is not None and execution_profile is not None:
            _, protected_operations = partition_module_operation_ids(self.source.declared_operation_ids)
            if protected_operations:
                raise ModuleAuthoringError(MODULE_EXECUTION_PROFILE_INCOMPATIBLE,
                    "Default native Reviewer requires exactly one model operation; "
                    "additional protected operations need a supported binding, not matching tool names")
            try:
                defaults.assert_profile(execution_profile)
            except ValueError as exc:
                raise ModuleAuthoringError(MODULE_EXECUTION_PROFILE_INCOMPATIBLE, str(exc)) from exc
        candidate = _candidate(
            self.source,
            module_version=module_version,
            behavior_policy=behavior_policy,
            evaluation_policy=evaluation_policy,
            retry_policy=retry_policy,
        )
        candidate = replace(candidate, reviewer_defaults=defaults)
        compiled = compile_agent_module_release(candidate)
        variant_candidate: ExecutionVariantPolicyReleaseCandidate | None = None
        variant: ExecutionVariantPolicyRelease | None = None
        if execution_profile is not None:
            variant_candidate = ExecutionVariantPolicyReleaseCandidate(
                policy_id=f"{self.source.module_id}_standalone_variant",
                policy_version=("v1" if defaults is None else content_version({
                    "module": compiled.module.release_sha256, "profile": execution_profile.release_sha256})),
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
            behavior_policy=behavior_policy,
            evaluation_policy=evaluation_policy,
            retry_policy=retry_policy,
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
        """Read exact registered facts for an export produced from this source.

        Args:
            registry: Loaded RuntimeReleaseRegistry containing the exact Module,
                its dependencies and any Profile/Variant included in exported.
            exported: ModuleExport whose source equals this object's source.

        Returns:
            A dict with Skill/Module identities, exact release/dependency refs
            and hashes, operation/transport/entry/output rules, active-pointer
            observation, optional Profile/Variant refs and execution blocker.
            Facts come from the supplied Registry snapshot; freshness against
            persistent storage is the caller's responsibility.

        Raises:
            ValueError: Export/source mismatch or invalid Reviewer provenance.
            Exception: Exact Registry lookup failures propagate from the Registry;
                inspect their native error instead of choosing a nearby version.

        Effects:
            Read-only. Does not register, set an active pointer, reload source,
            connect to a database itself or prove successful execution.
        """
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
    "MODULE_EXECUTION_PROFILE_INCOMPATIBLE",
    "MODULE_OPERATION_DECLARATION_INVALID",
    "Module",
    "ModuleAuthoringError",
    "ModuleExport",
    "ModuleReviewer",
]
