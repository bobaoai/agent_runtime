"""Deterministic compiler helpers for candidate Runtime release bundles.

The compiler is business-neutral. Domain packages supply exact repository
members, schemas, operations, profiles, graph bindings, and owner contracts.
The resulting records are ordinary immutable public Runtime contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Mapping

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    ExecutionProfileRelease,
    EvaluationPolicyRelease,
    ExecutionVariantPolicyRelease,
    PromptComponentKind,
    PromptComponentRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    PromptBundleRelease,
    ReleaseAdmissionIntent,
    ReleaseAdmissionState,
    ReleaseMember,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowParallelGroupBinding,
    WorkflowRelease,
)
from ..foundation import (
    strict_output_schema_projection,
    validate_json_document_against_schema,
    validate_json_schema_document,
)
from ..foundation.foundation_retry_policy_validation import (
    RETRY_POLICY_MAX_ATTEMPTS,
    RETRY_POLICY_MIN_ATTEMPTS,
    validate_retry_attempt_budget,
)


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 of one exact UTF-8 string."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentModuleReleaseCandidate:
    """Path-free exact content for one Agent Module release."""

    module_id: str
    module_version: str
    owner_contract_ref: str
    owner_contract_content: str
    input_schema_ref: str
    input_schema_document: str
    output_schema_ref: str
    output_schema_document: str
    instruction_source_ref: str
    instruction_text: str
    declared_operation_ids: tuple[str, ...]
    compatible_transport_kinds: tuple[str, ...]
    behavior_policy_ref: str
    behavior_policy_sha256: str
    evaluation_policy_ref: str
    evaluation_policy_sha256: str
    retry_policy_ref: str
    retry_policy_sha256: str
    entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.WORKFLOW_BOUND
    output_resolution_policy: OutputResolutionPolicy = (
        OutputResolutionPolicy.EVALUATED_SINGLE
    )


@dataclass(frozen=True)
class NonAgentModuleReleaseCandidate:
    """Path-free exact content for one deterministic or service Module."""

    module_id: str
    module_version: str
    module_kind: ModuleKind
    owner_contract_ref: str
    owner_contract_content: str
    executable_ref: str
    executable_content: bytes
    input_schema_ref: str
    input_schema_document: str
    output_schema_ref: str
    output_schema_document: str
    declared_operation_ids: tuple[str, ...]
    compatible_transport_kinds: tuple[str, ...]
    behavior_policy_ref: str
    behavior_policy_sha256: str
    evaluation_policy_ref: str
    evaluation_policy_sha256: str
    retry_policy_ref: str
    retry_policy_sha256: str
    entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.WORKFLOW_BOUND
    output_resolution_policy: OutputResolutionPolicy = (
        OutputResolutionPolicy.DIRECT_SINGLE
    )


@dataclass(frozen=True)
class CompiledAgentModuleRelease:
    """Dependency-closed records produced for one Agent Module."""

    schema_assets: tuple[SchemaAssetRelease, ...]
    prompt_components: tuple[PromptComponentRelease, ...]
    prompt_bundle: PromptBundleRelease
    module: RuntimeModuleRelease


@dataclass(frozen=True)
class CompiledNonAgentModuleRelease:
    """Dependency-closed records produced for one non-Agent Module."""

    schema_assets: tuple[SchemaAssetRelease, ...]
    module: RuntimeModuleRelease


@dataclass(frozen=True)
class ExecutionProfileReleaseSpec:
    """Provider-specific profile inputs compiled independently of a Module."""

    execution_profile_id: str
    executor_adapter_id: str
    executor_adapter_revision: str
    transport_kind: str
    provider_id: str
    model_id: str
    reasoning_profile: str
    execution_mode: str
    semantic_input_delivery_mode: str
    attempt_workspace_policy: str
    gateway_access_reasons: tuple[str, ...]
    output_constraint_mode: str
    tool_policy: tuple[str, ...]
    network_policy: str
    timeout_seconds: int
    release_version: str = "candidate_v1"


@dataclass(frozen=True)
class BehaviorPolicyReleaseCandidate:
    """Repository-independent Context-isolation policy input."""

    policy_id: str
    policy_version: str
    context_isolation: str


@dataclass(frozen=True)
class EvaluationPolicyReleaseCandidate:
    """Repository-independent evaluation policy input."""

    policy_id: str
    policy_version: str
    evaluation_mode: str


@dataclass(frozen=True)
class RetryPolicyReleaseCandidate:
    """Repository-independent retry-limit policy input."""

    policy_id: str
    policy_version: str
    max_attempts: int


@dataclass(frozen=True)
class ExecutionVariantProfileBindingCandidate:
    """One ordered origin position bound to an exact Execution Profile."""

    position_id: str
    execution_profile_release_ref: str
    execution_profile_release_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "position_id": self.position_id,
            "execution_profile_release_ref": self.execution_profile_release_ref,
            "execution_profile_release_sha256": (
                self.execution_profile_release_sha256
            ),
        }


@dataclass(frozen=True)
class ExecutionVariantPolicyReleaseCandidate:
    """Repository-independent ordered profile selection for one exact origin."""

    policy_id: str
    policy_version: str
    origin_kind: str
    origin_release_ref: str
    origin_release_sha256: str
    bindings: tuple[ExecutionVariantProfileBindingCandidate, ...]


@dataclass(frozen=True)
class WorkflowNodeReleaseCandidate:
    """One path-free Workflow position before input-content hashing."""

    node_id: str
    node_kind: WorkflowNodeKind
    module_release_ref: str | None
    module_release_sha256: str | None
    input_mapping_ref: str | None
    input_mapping_document: Mapping[str, Any] | None


@dataclass(frozen=True)
class WorkflowReleaseCandidate:
    """Repository-independent exact content for one Workflow release."""

    workflow_id: str
    workflow_version: str
    workflow_contract_version: str
    owner_contract_ref: str
    owner_contract_content: str
    graph_ref: str
    initial_node_id: str
    nodes: tuple[WorkflowNodeReleaseCandidate, ...]
    edges: tuple[WorkflowEdge, ...]
    authorization_manifest_ref: str
    authorization_manifest_document: Mapping[str, Any]
    execution_binding_ref: str
    execution_binding_document: Mapping[str, Any]
    parallel_groups: tuple[WorkflowParallelGroupBinding, ...] = ()


def _canonical_json_payload(
    label: str,
    document: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    if not isinstance(document, Mapping):
        raise ValueError(f"{label} must be one JSON object")
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        canonical = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only JSON values") from exc
    if type(canonical) is not dict:
        raise ValueError(f"{label} must be one JSON object")
    return canonical, sha256_text(encoded)


def compile_workflow_release(
    candidate: WorkflowReleaseCandidate,
) -> WorkflowRelease:
    """Compile exact Workflow content without reading a host repository."""

    if type(candidate) is not WorkflowReleaseCandidate:
        raise ValueError("candidate must be a WorkflowReleaseCandidate")
    if not candidate.owner_contract_content:
        raise ValueError("owner_contract_content must not be empty")
    if type(candidate.nodes) is not tuple or not candidate.nodes:
        raise ValueError("Workflow candidate nodes must be a non-empty tuple")
    if type(candidate.edges) is not tuple or not candidate.edges:
        raise ValueError("Workflow candidate edges must be a non-empty tuple")
    if type(candidate.parallel_groups) is not tuple:
        raise ValueError("Workflow parallel_groups must be an immutable tuple")

    nodes: list[WorkflowNodeBinding] = []
    mapping_hashes: dict[str, str] = {}
    for node in candidate.nodes:
        if type(node) is not WorkflowNodeReleaseCandidate:
            raise ValueError("Workflow candidate contains an invalid node")
        if node.node_kind is WorkflowNodeKind.MODULE:
            if node.input_mapping_ref is None:
                raise ValueError("Module node requires input_mapping_ref")
            if node.input_mapping_document is None:
                raise ValueError("Module node requires input_mapping_document")
            _, mapping_sha256 = _canonical_json_payload(
                "input_mapping_document",
                node.input_mapping_document,
            )
            prior_hash = mapping_hashes.setdefault(
                node.input_mapping_ref,
                mapping_sha256,
            )
            if prior_hash != mapping_sha256:
                raise ValueError(
                    "one input_mapping_ref cannot name different content"
                )
        else:
            if (
                node.input_mapping_ref is not None
                or node.input_mapping_document is not None
            ):
                raise ValueError(
                    "control node cannot own input-mapping content"
                )
            mapping_sha256 = None
        nodes.append(
            WorkflowNodeBinding(
                node_id=node.node_id,
                node_kind=node.node_kind,
                module_release_ref=node.module_release_ref,
                module_release_sha256=node.module_release_sha256,
                input_mapping_ref=node.input_mapping_ref,
                input_mapping_sha256=mapping_sha256,
            )
        )

    _, authorization_sha256 = _canonical_json_payload(
        "authorization_manifest_document",
        candidate.authorization_manifest_document,
    )
    _, execution_binding_sha256 = _canonical_json_payload(
        "execution_binding_document",
        candidate.execution_binding_document,
    )
    node_tuple = tuple(nodes)
    graph_payload = {
        "initial_node_id": candidate.initial_node_id,
        "nodes": [node.as_dict() for node in node_tuple],
        "edges": [edge.as_dict() for edge in candidate.edges],
        "parallel_groups": [
            group.as_dict() for group in candidate.parallel_groups
        ],
    }
    graph_sha256 = sha256_text(
        json.dumps(
            graph_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return WorkflowRelease.build(
        workflow_id=candidate.workflow_id,
        workflow_version=candidate.workflow_version,
        workflow_contract_version=candidate.workflow_contract_version,
        release_ref=(
            f"runtime-workflow:{candidate.workflow_id}@"
            f"{candidate.workflow_version}"
        ),
        owner_contract_ref=candidate.owner_contract_ref,
        owner_contract_sha256=sha256_text(candidate.owner_contract_content),
        graph_ref=candidate.graph_ref,
        graph_sha256=graph_sha256,
        initial_node_id=candidate.initial_node_id,
        nodes=node_tuple,
        edges=candidate.edges,
        authorization_manifest_ref=candidate.authorization_manifest_ref,
        authorization_manifest_sha256=authorization_sha256,
        execution_release_ref=candidate.execution_binding_ref,
        execution_release_sha256=execution_binding_sha256,
        parallel_groups=candidate.parallel_groups,
    )


def _runtime_policy_schema_document(
    *,
    release_ref: str,
    properties: Mapping[str, Any],
    required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "$id": release_ref,
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
        "properties": dict(properties),
        "required": list(required),
        "type": "object",
    }


def runtime_owned_policy_schema_assets() -> tuple[SchemaAssetRelease, ...]:
    """Return the four code-owned closed schemas used by Runtime policies."""

    hash_schema = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    non_empty_string = {"type": "string", "minLength": 1}
    schemas = (
        (
            "runtime_behavior_policy",
            {
                "context_isolation": {
                    "type": "string",
                    "enum": ["workflow_execution_isolated"],
                }
            },
            ("context_isolation",),
        ),
        (
            "runtime_evaluation_policy",
            {
                "evaluation_mode": {
                    "type": "string",
                    "enum": [
                        "module_candidate",
                        "deterministic_candidate",
                        "none",
                    ],
                }
            },
            ("evaluation_mode",),
        ),
        (
            "runtime_retry_policy",
            {
                "max_attempts": {
                    "type": "integer",
                    "minimum": RETRY_POLICY_MIN_ATTEMPTS,
                    "maximum": RETRY_POLICY_MAX_ATTEMPTS,
                }
            },
            ("max_attempts",),
        ),
        (
            "runtime_execution_variant_policy",
            {
                "origin_kind": {
                    "type": "string",
                    "enum": ["workflow", "standalone_module"],
                },
                "origin_release_ref": non_empty_string,
                "origin_release_sha256": hash_schema,
                "bindings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "position_id": non_empty_string,
                            "execution_profile_release_ref": non_empty_string,
                            "execution_profile_release_sha256": hash_schema,
                        },
                        "required": [
                            "position_id",
                            "execution_profile_release_ref",
                            "execution_profile_release_sha256",
                        ],
                    },
                },
            },
            (
                "origin_kind",
                "origin_release_ref",
                "origin_release_sha256",
                "bindings",
            ),
        ),
    )
    releases: list[SchemaAssetRelease] = []
    for schema_asset_id, properties, required in schemas:
        release_ref = f"schema:{schema_asset_id}@v1"
        document = _runtime_policy_schema_document(
            release_ref=release_ref,
            properties=properties,
            required=required,
        )
        validate_json_schema_document(document)
        releases.append(
            SchemaAssetRelease.build(
                schema_asset_id=schema_asset_id,
                schema_asset_version="v1",
                release_ref=release_ref,
                schema_document=document,
            )
        )
    return tuple(releases)


def _compile_policy_release(
    *,
    release_type: type[
        BehaviorPolicyRelease
        | EvaluationPolicyRelease
        | RetryPolicyRelease
        | ExecutionVariantPolicyRelease
    ],
    policy_id: str,
    policy_version: str,
    schema_asset_id: str,
    policy_document: Mapping[str, Any],
) -> (
    BehaviorPolicyRelease
    | EvaluationPolicyRelease
    | RetryPolicyRelease
    | ExecutionVariantPolicyRelease
):
    schema = next(
        item
        for item in runtime_owned_policy_schema_assets()
        if item.schema_asset_id == schema_asset_id
    )
    validate_json_document_against_schema(
        policy_document,
        schema.schema_document(),
    )
    return release_type.build(
        policy_id=policy_id,
        policy_version=policy_version,
        release_ref=(
            f"{release_type.release_prefix}:{policy_id}@{policy_version}"
        ),
        policy_schema_ref=schema.release_ref,
        policy_schema_sha256=schema.schema_sha256,
        policy_document=policy_document,
    )


def compile_behavior_policy_release(
    candidate: BehaviorPolicyReleaseCandidate,
) -> BehaviorPolicyRelease:
    """Compile one closed Context-isolation policy."""

    return _compile_policy_release(
        release_type=BehaviorPolicyRelease,
        policy_id=candidate.policy_id,
        policy_version=candidate.policy_version,
        schema_asset_id="runtime_behavior_policy",
        policy_document={"context_isolation": candidate.context_isolation},
    )


def compile_evaluation_policy_release(
    candidate: EvaluationPolicyReleaseCandidate,
) -> EvaluationPolicyRelease:
    """Compile one closed evaluation policy."""

    return _compile_policy_release(
        release_type=EvaluationPolicyRelease,
        policy_id=candidate.policy_id,
        policy_version=candidate.policy_version,
        schema_asset_id="runtime_evaluation_policy",
        policy_document={"evaluation_mode": candidate.evaluation_mode},
    )


def compile_retry_policy_release(
    candidate: RetryPolicyReleaseCandidate,
) -> RetryPolicyRelease:
    """Compile one closed retry policy."""

    validate_retry_attempt_budget(candidate.max_attempts)
    return _compile_policy_release(
        release_type=RetryPolicyRelease,
        policy_id=candidate.policy_id,
        policy_version=candidate.policy_version,
        schema_asset_id="runtime_retry_policy",
        policy_document={"max_attempts": candidate.max_attempts},
    )


def compile_execution_variant_policy_release(
    candidate: ExecutionVariantPolicyReleaseCandidate,
) -> ExecutionVariantPolicyRelease:
    """Compile one closed ordered Execution Profile selection."""

    if type(candidate.bindings) is not tuple:
        raise ValueError("Execution Variant bindings must be an immutable tuple")
    if not candidate.bindings:
        raise ValueError("Execution Variant requires at least one profile binding")
    if any(
        type(binding) is not ExecutionVariantProfileBindingCandidate
        for binding in candidate.bindings
    ):
        raise ValueError("Execution Variant binding has an invalid type")
    positions = tuple(binding.position_id for binding in candidate.bindings)
    if len(positions) != len(set(positions)):
        raise ValueError("Execution Variant position_id values must be unique")
    return _compile_policy_release(
        release_type=ExecutionVariantPolicyRelease,
        policy_id=candidate.policy_id,
        policy_version=candidate.policy_version,
        schema_asset_id="runtime_execution_variant_policy",
        policy_document={
            "origin_kind": candidate.origin_kind,
            "origin_release_ref": candidate.origin_release_ref,
            "origin_release_sha256": candidate.origin_release_sha256,
            "bindings": [binding.as_dict() for binding in candidate.bindings],
        },
    )


def compile_prompt_bundle_release(
    *,
    prompt_bundle_id: str,
    prompt_bundle_version: str,
    compiler_version: str,
    components: tuple[PromptComponentRelease, ...],
) -> PromptBundleRelease:
    """Compile one ordered Prompt Bundle from persisted component releases."""

    if type(components) is not tuple or not components:
        raise ValueError("Prompt Bundle requires an immutable component tuple")
    for component in components:
        if type(component) is not PromptComponentRelease:
            raise ValueError(
                "Prompt Bundle components must be PromptComponentRelease values"
            )
        component.validate()
    members = tuple(
        ReleaseMember(
            member_ref=component.release_ref,
            member_sha256=component.release_sha256,
            media_type=component.media_type,
        )
        for component in components
    )
    return PromptBundleRelease.build(
        prompt_bundle_id=prompt_bundle_id,
        prompt_bundle_version=prompt_bundle_version,
        release_ref=f"prompt-bundle:{prompt_bundle_id}@{prompt_bundle_version}",
        compiler_version=compiler_version,
        members=members,
        compiled_static_body="".join(
            component.formatted_content for component in components
        ),
    )


def compile_execution_profile_release(
    spec: ExecutionProfileReleaseSpec,
) -> ExecutionProfileRelease:
    """Compile one provider profile without rebuilding its compatible Module."""

    return ExecutionProfileRelease.build(
        execution_profile_id=spec.execution_profile_id,
        execution_profile_version=spec.release_version,
        release_ref=(
            "execution-profile:"
            f"{spec.execution_profile_id}@{spec.release_version}"
        ),
        executor_adapter_id=spec.executor_adapter_id,
        executor_adapter_revision=spec.executor_adapter_revision,
        transport_kind=spec.transport_kind,
        provider_id=spec.provider_id,
        model_id=spec.model_id,
        reasoning_profile=spec.reasoning_profile,
        execution_mode=spec.execution_mode,
        semantic_input_delivery_mode=spec.semantic_input_delivery_mode,
        attempt_workspace_policy=spec.attempt_workspace_policy,
        gateway_access_reasons=spec.gateway_access_reasons,
        output_constraint_mode=spec.output_constraint_mode,
        tool_policy=spec.tool_policy,
        network_policy=spec.network_policy,
        timeout_seconds=spec.timeout_seconds,
    )


def _schema_asset_from_json(
    *,
    schema_ref: str,
    schema_document: str,
) -> tuple[SchemaAssetRelease, ReleaseMember]:
    """Compile one exact UTF-8 JSON Schema document without host path access."""

    if type(schema_document) is not str:
        raise ValueError("schema_document must be exact UTF-8 JSON text")
    try:
        payload = json.loads(schema_document)
    except json.JSONDecodeError as exc:
        raise ValueError("registered schema is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("registered schema must be one JSON object")
    if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError("registered schema must declare Draft 2020-12")
    if payload.get("$id") != schema_ref:
        raise ValueError("registered schema $id differs from release ref")
    schema_name, separator, schema_version = schema_ref.removeprefix(
        "schema:"
    ).rpartition("@")
    if not schema_ref.startswith("schema:") or not separator or not schema_name:
        raise ValueError(f"registered schema ref has no version: {schema_ref}")
    validate_json_schema_document(payload)
    schema_asset = SchemaAssetRelease.build(
        schema_asset_id=schema_name,
        schema_asset_version=schema_version,
        release_ref=schema_ref,
        schema_document=payload,
    )
    member = ReleaseMember(
        member_ref=schema_asset.release_ref,
        member_sha256=schema_asset.schema_sha256,
        media_type="application/schema+json",
    )
    member.validate()
    return schema_asset, member


def compile_agent_module_release(
    candidate: AgentModuleReleaseCandidate,
) -> CompiledAgentModuleRelease:
    """Compile one path-free Agent candidate into immutable Runtime releases."""

    if type(candidate) is not AgentModuleReleaseCandidate:
        raise ValueError("candidate must be an AgentModuleReleaseCandidate")
    if not candidate.owner_contract_content:
        raise ValueError("owner_contract_content must not be empty")
    if not candidate.instruction_source_ref:
        raise ValueError("instruction_source_ref must not be empty")
    if not candidate.instruction_text.strip():
        raise ValueError("instruction_text must not be empty")
    instruction_member = ReleaseMember(
        member_ref=candidate.instruction_source_ref,
        member_sha256=sha256_text(candidate.instruction_text),
        media_type="text/markdown",
    )
    input_schema_asset, _ = _schema_asset_from_json(
        schema_ref=candidate.input_schema_ref,
        schema_document=candidate.input_schema_document,
    )
    output_schema_asset, output_schema_member = _schema_asset_from_json(
        schema_ref=candidate.output_schema_ref,
        schema_document=candidate.output_schema_document,
    )
    instruction_component = PromptComponentRelease.build(
        prompt_component_id=f"{candidate.module_id}_task_instruction",
        prompt_component_version=candidate.module_version,
        release_ref=(
            "prompt-component:"
            f"{candidate.module_id}_task_instruction@{candidate.module_version}"
        ),
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="skill_instruction_formatter",
        formatter_version="v1",
        source_members=(instruction_member,),
        formatted_content=candidate.instruction_text,
    )
    output_constraint_content = (
        "\n## Required Output Shape\n\n"
        + json.dumps(
            strict_output_schema_projection(output_schema_asset.schema_document()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    output_constraint_component = PromptComponentRelease.build(
        prompt_component_id=f"{candidate.module_id}_output_constraint",
        prompt_component_version=candidate.module_version,
        release_ref=(
            "prompt-component:"
            f"{candidate.module_id}_output_constraint@{candidate.module_version}"
        ),
        component_kind=PromptComponentKind.OUTPUT_CONSTRAINT,
        media_type="text/markdown",
        formatter_id="json_schema_output_formatter",
        formatter_version="v1",
        source_members=(output_schema_member,),
        formatted_content=output_constraint_content,
    )
    prompt_components = (
        instruction_component,
        output_constraint_component,
    )
    schema_assets = (input_schema_asset, output_schema_asset)
    prompt_id = f"{candidate.module_id}_prompt_bundle"
    prompt = compile_prompt_bundle_release(
        prompt_bundle_id=prompt_id,
        prompt_bundle_version=candidate.module_version,
        compiler_version="task_plane_module_prompt_v3",
        components=prompt_components,
    )
    module = RuntimeModuleRelease.build(
        module_id=candidate.module_id,
        module_version=candidate.module_version,
        release_ref=(
            f"runtime-module:{candidate.module_id}@{candidate.module_version}"
        ),
        module_kind=ModuleKind.AGENT,
        owner_contract_ref=candidate.owner_contract_ref,
        owner_contract_sha256=sha256_text(candidate.owner_contract_content),
        executable_ref=None,
        executable_sha256=None,
        input_schema_ref=candidate.input_schema_ref,
        input_schema_sha256=input_schema_asset.schema_sha256,
        output_schema_ref=candidate.output_schema_ref,
        output_schema_sha256=output_schema_asset.schema_sha256,
        prompt_bundle_ref=prompt.release_ref,
        prompt_bundle_sha256=prompt.release_sha256,
        declared_operation_ids=candidate.declared_operation_ids,
        behavior_policy_ref=candidate.behavior_policy_ref,
        behavior_policy_sha256=candidate.behavior_policy_sha256,
        evaluation_policy_ref=candidate.evaluation_policy_ref,
        evaluation_policy_sha256=candidate.evaluation_policy_sha256,
        retry_policy_ref=candidate.retry_policy_ref,
        retry_policy_sha256=candidate.retry_policy_sha256,
        compatible_transport_kinds=candidate.compatible_transport_kinds,
        entry_policy=candidate.entry_policy,
        output_resolution_policy=candidate.output_resolution_policy,
    )
    return CompiledAgentModuleRelease(
        schema_assets=schema_assets,
        prompt_components=prompt_components,
        prompt_bundle=prompt,
        module=module,
    )


def compile_non_agent_module_release(
    candidate: NonAgentModuleReleaseCandidate,
) -> CompiledNonAgentModuleRelease:
    """Compile one path-free deterministic or service Module candidate."""

    if type(candidate) is not NonAgentModuleReleaseCandidate:
        raise ValueError("candidate must be a NonAgentModuleReleaseCandidate")
    if candidate.module_kind is ModuleKind.AGENT:
        raise ValueError("compile_non_agent_module_release rejects Agent Modules")
    if not candidate.owner_contract_content:
        raise ValueError("owner_contract_content must not be empty")
    if type(candidate.executable_content) is not bytes or not candidate.executable_content:
        raise ValueError("executable_content must be non-empty bytes")
    input_schema_asset, _ = _schema_asset_from_json(
        schema_ref=candidate.input_schema_ref,
        schema_document=candidate.input_schema_document,
    )
    output_schema_asset, _ = _schema_asset_from_json(
        schema_ref=candidate.output_schema_ref,
        schema_document=candidate.output_schema_document,
    )
    module = RuntimeModuleRelease.build(
        module_id=candidate.module_id,
        module_version=candidate.module_version,
        release_ref=(
            f"runtime-module:{candidate.module_id}@{candidate.module_version}"
        ),
        module_kind=candidate.module_kind,
        owner_contract_ref=candidate.owner_contract_ref,
        owner_contract_sha256=sha256_text(candidate.owner_contract_content),
        executable_ref=candidate.executable_ref,
        executable_sha256=hashlib.sha256(candidate.executable_content).hexdigest(),
        input_schema_ref=candidate.input_schema_ref,
        input_schema_sha256=input_schema_asset.schema_sha256,
        output_schema_ref=candidate.output_schema_ref,
        output_schema_sha256=output_schema_asset.schema_sha256,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=candidate.declared_operation_ids,
        behavior_policy_ref=candidate.behavior_policy_ref,
        behavior_policy_sha256=candidate.behavior_policy_sha256,
        evaluation_policy_ref=candidate.evaluation_policy_ref,
        evaluation_policy_sha256=candidate.evaluation_policy_sha256,
        retry_policy_ref=candidate.retry_policy_ref,
        retry_policy_sha256=candidate.retry_policy_sha256,
        compatible_transport_kinds=candidate.compatible_transport_kinds,
        entry_policy=candidate.entry_policy,
        output_resolution_policy=candidate.output_resolution_policy,
    )
    return CompiledNonAgentModuleRelease(
        schema_assets=(input_schema_asset, output_schema_asset),
        module=module,
    )


def candidate_admission_intent(
    record: Any,
    *,
    evidence_members: tuple[ReleaseMember, ...] = (),
) -> ReleaseAdmissionIntent:
    """Build one timestamp-free candidate intent for an exact release."""

    if isinstance(record, SchemaAssetRelease):
        kind = ReleaseSubjectKind.SCHEMA_ASSET
        subject_id = record.schema_asset_id
    elif isinstance(record, PromptComponentRelease):
        kind = ReleaseSubjectKind.PROMPT_COMPONENT
        subject_id = record.prompt_component_id
    elif isinstance(record, PromptBundleRelease):
        kind = ReleaseSubjectKind.PROMPT_BUNDLE
        subject_id = record.prompt_bundle_id
    elif isinstance(record, BehaviorPolicyRelease):
        kind = ReleaseSubjectKind.BEHAVIOR_POLICY
        subject_id = record.policy_id
    elif isinstance(record, EvaluationPolicyRelease):
        kind = ReleaseSubjectKind.EVALUATION_POLICY
        subject_id = record.policy_id
    elif isinstance(record, RetryPolicyRelease):
        kind = ReleaseSubjectKind.RETRY_POLICY
        subject_id = record.policy_id
    elif isinstance(record, ExecutionVariantPolicyRelease):
        kind = ReleaseSubjectKind.EXECUTION_VARIANT_POLICY
        subject_id = record.policy_id
    elif isinstance(record, ExecutionProfileRelease):
        kind = ReleaseSubjectKind.EXECUTION_PROFILE
        subject_id = record.execution_profile_id
    elif isinstance(record, RuntimeModuleRelease):
        kind = ReleaseSubjectKind.RUNTIME_MODULE
        subject_id = record.module_id
    else:
        from ..contracts.registry_release_definition import WorkflowRelease

        if not isinstance(record, WorkflowRelease):
            raise ValueError("candidate admission received an unknown release type")
        kind = ReleaseSubjectKind.WORKFLOW
        subject_id = record.workflow_id
    return ReleaseAdmissionIntent.build(
        admission_id=(
            f"admission_{kind.value}_{subject_id}_"
            f"{record.release_sha256[:16]}"
        ),
        subject_kind=kind,
        subject_id=subject_id,
        release_ref=record.release_ref,
        release_sha256=record.release_sha256,
        state=ReleaseAdmissionState.CANDIDATE,
        evidence_members=evidence_members,
    )


__all__ = [
    "AgentModuleReleaseCandidate",
    "BehaviorPolicyReleaseCandidate",
    "CompiledAgentModuleRelease",
    "CompiledNonAgentModuleRelease",
    "EvaluationPolicyReleaseCandidate",
    "ExecutionProfileReleaseSpec",
    "ExecutionVariantPolicyReleaseCandidate",
    "ExecutionVariantProfileBindingCandidate",
    "NonAgentModuleReleaseCandidate",
    "RetryPolicyReleaseCandidate",
    "WorkflowNodeReleaseCandidate",
    "WorkflowReleaseCandidate",
    "candidate_admission_intent",
    "compile_agent_module_release",
    "compile_behavior_policy_release",
    "compile_evaluation_policy_release",
    "compile_execution_profile_release",
    "compile_execution_variant_policy_release",
    "compile_non_agent_module_release",
    "compile_prompt_bundle_release",
    "compile_retry_policy_release",
    "compile_workflow_release",
    "sha256_text",
    "runtime_owned_policy_schema_assets",
]
