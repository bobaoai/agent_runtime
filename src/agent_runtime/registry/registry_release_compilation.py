"""Deterministic compiler helpers for candidate Runtime release bundles.

The compiler is business-neutral. Domain packages supply exact repository
members, schemas, operations, profiles, graph bindings, and owner contracts.
The resulting records are ordinary immutable public Runtime contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    PromptComponentKind,
    PromptComponentRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    PromptBundleRelease,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseMember,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    SkillModuleExport,
    SkillPackageRelease,
)
from .registry_module_exporting import (
    MODULE_PROMPT_FILENAME,
    load_skill_runtime_module_exports,
)
from ..invocation.invocation_schema_projection import task_plane_output_schema


def sha256_text(value: str) -> str:
    """Return the lowercase SHA-256 of one exact UTF-8 string."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase SHA-256 of one exact repository file."""

    if not path.is_file():
        raise ValueError(f"release source file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def managed_skill_projection(path: Path) -> str:
    """Load one fixed Module prompt source."""

    if not path.is_file():
        raise ValueError(f"Module prompt is missing: {path}")
    if path.name != MODULE_PROMPT_FILENAME:
        raise ValueError("Module prompt must use the fixed prompt.md read channel")
    text = path.read_text(encoding="utf-8")
    if not text.strip() or not text.endswith("\n"):
        raise ValueError(f"fixed Module prompt is malformed: {path}")
    return text


def _instruction_member_ref(
    *,
    skill_package_id: str,
    module_id: str,
    release_version: str,
    fragment: str | None = None,
) -> str:
    """Return a repository-independent instruction identity."""

    ref = (
        f"skill-instruction:{skill_package_id}/{module_id}"
        f"@{release_version}"
    )
    return f"{ref}#{fragment}" if fragment is not None else ref


@dataclass(frozen=True)
class AgentModuleReleaseSpec:
    """Complete candidate inputs for one Agent Module release."""

    module_id: str
    skill_id: str
    skill_projection_path: str
    owner_contract_ref: str
    owner_contract_path: str
    input_schema_ref: str
    output_schema_ref: str
    declared_operation_ids: tuple[str, ...]
    execution_profile_id: str
    executor_adapter_id: str
    executor_adapter_revision: str
    transport_kind: str
    provider_id: str
    model_id: str
    reasoning_profile: str
    output_constraint_mode: str
    timeout_seconds: int
    input_schema_path: str
    output_schema_path: str
    execution_mode: str = "tool_free"
    semantic_input_delivery_mode: str = "inline"
    attempt_workspace_policy: str = "none"
    gateway_access_reasons: tuple[str, ...] = ()
    tool_policy: tuple[str, ...] = ()
    network_policy: str = "denied"
    compatible_transport_kinds: tuple[str, ...] = ()
    release_version: str = "candidate_v1"
    context_policy_ref: str = "context-policy:workflow_execution_isolated@v1"
    evaluation_policy_ref: str = "evaluation-policy:module_candidate@v1"
    retry_policy_ref: str = "retry-policy:bounded_candidate@v1"
    entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.WORKFLOW_BOUND
    output_resolution_policy: OutputResolutionPolicy = (
        OutputResolutionPolicy.EVALUATED_SINGLE
    )


@dataclass(frozen=True)
class CompiledAgentModuleRelease:
    """Dependency-closed records produced for one Agent Module."""

    skill_package: SkillPackageRelease
    schema_assets: tuple[SchemaAssetRelease, ...]
    prompt_components: tuple[PromptComponentRelease, ...]
    prompt_bundle: PromptBundleRelease
    execution_profile: ExecutionProfileRelease
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
    max_attempts: int = 1
    release_version: str = "candidate_v1"
    context_policy_ref: str = "context-policy:workflow_execution_isolated@v1"


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
        context_policy_ref=spec.context_policy_ref,
        context_policy_sha256=sha256_text(spec.context_policy_ref),
        timeout_seconds=spec.timeout_seconds,
        max_attempts=spec.max_attempts,
    )


def _registered_schema_asset(
    project_root: Path,
    *,
    schema_ref: str,
    schema_path: str,
) -> tuple[Path, SchemaAssetRelease, ReleaseMember]:
    """Validate and bind one concrete Draft 2020-12 schema asset."""

    path = project_root / schema_path
    if not path.is_file():
        raise ValueError(f"registered schema asset is missing: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"registered schema is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"registered schema must be one JSON object: {path}")
    if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        raise ValueError(
            f"registered schema must declare Draft 2020-12: {schema_path}"
        )
    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError:
        # Generated inventory must remain importable in the dependency-free
        # standalone-package check. Registration and admission environments
        # install jsonschema and therefore run the complete meta-schema check.
        pass
    else:
        Draft202012Validator.check_schema(payload)
    if payload.get("$id") != schema_ref:
        raise ValueError(
            f"registered schema $id differs from release ref: {schema_path}"
        )
    schema_name, separator, schema_version = schema_ref.removeprefix(
        "schema:"
    ).rpartition("@")
    if not schema_ref.startswith("schema:") or not separator:
        raise ValueError(f"registered schema ref has no version: {schema_ref}")
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
    return path, schema_asset, member


def compile_agent_module_release(
    project_root: Path,
    spec: AgentModuleReleaseSpec,
) -> CompiledAgentModuleRelease:
    """Compile one exact Skill projection into candidate Runtime releases."""

    owner_path = project_root / spec.owner_contract_path
    owner_sha256 = sha256_file(owner_path)
    package_sources = load_skill_runtime_module_exports(
        project_root,
        skill_id=spec.skill_id,
    )
    target_sources = tuple(
        source
        for source in package_sources
        if source.module_id == spec.module_id
    )
    if len(target_sources) != 1:
        raise ValueError("Module prompt has no unique package export")
    target_source = target_sources[0]
    if target_source.prompt_path != spec.skill_projection_path:
        raise ValueError("Module spec does not use the fixed prompt read channel")
    package_id = target_source.skill_package_id
    instructions = target_source.prompt_text
    instruction_member = ReleaseMember(
        member_ref=_instruction_member_ref(
            skill_package_id=package_id,
            module_id=spec.module_id,
            release_version=spec.release_version,
        ),
        member_sha256=sha256_text(instructions),
        media_type="text/markdown",
    )
    _, input_schema_asset, _ = _registered_schema_asset(
        project_root,
        schema_ref=spec.input_schema_ref,
        schema_path=spec.input_schema_path,
    )
    _, output_schema_asset, output_schema_member = _registered_schema_asset(
        project_root,
        schema_ref=spec.output_schema_ref,
        schema_path=spec.output_schema_path,
    )
    instruction_component = PromptComponentRelease.build(
        prompt_component_id=f"{spec.module_id}_task_instruction",
        prompt_component_version=spec.release_version,
        release_ref=(
            "prompt-component:"
            f"{spec.module_id}_task_instruction@{spec.release_version}"
        ),
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="skill_instruction_formatter",
        formatter_version="v1",
        source_members=(instruction_member,),
        formatted_content=instructions,
    )
    output_constraint_content = (
        "\n## Required Output Shape\n\n"
        + json.dumps(
            task_plane_output_schema(output_schema_asset.schema_document()),
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    output_constraint_component = PromptComponentRelease.build(
        prompt_component_id=f"{spec.module_id}_output_constraint",
        prompt_component_version=spec.release_version,
        release_ref=(
            "prompt-component:"
            f"{spec.module_id}_output_constraint@{spec.release_version}"
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
    input_schema_sha256 = input_schema_asset.schema_sha256
    output_schema_sha256 = output_schema_asset.schema_sha256

    expected_registration = {
        "owner_contract_ref": spec.owner_contract_ref,
        "owner_contract_path": spec.owner_contract_path,
        "input_schema_ref": spec.input_schema_ref,
        "input_schema_path": spec.input_schema_path,
        "output_schema_ref": spec.output_schema_ref,
        "output_schema_path": spec.output_schema_path,
        "declared_operation_ids": spec.declared_operation_ids,
        "compatible_transport_kinds": spec.compatible_transport_kinds,
        "context_policy_ref": spec.context_policy_ref,
        "evaluation_policy_ref": spec.evaluation_policy_ref,
        "retry_policy_ref": spec.retry_policy_ref,
        "entry_policy": spec.entry_policy,
        "output_resolution_policy": spec.output_resolution_policy,
    }
    observed_registration = {
        "owner_contract_ref": target_source.owner_contract_ref,
        "owner_contract_path": target_source.owner_contract_path,
        "input_schema_ref": target_source.input_schema_ref,
        "input_schema_path": target_source.input_schema_path,
        "output_schema_ref": target_source.output_schema_ref,
        "output_schema_path": target_source.output_schema_path,
        "declared_operation_ids": target_source.declared_operation_ids,
        "compatible_transport_kinds": target_source.compatible_transport_kinds,
        "context_policy_ref": target_source.context_policy_ref,
        "evaluation_policy_ref": target_source.evaluation_policy_ref,
        "retry_policy_ref": target_source.retry_policy_ref,
        "entry_policy": target_source.entry_policy,
        "output_resolution_policy": target_source.output_resolution_policy,
    }
    if expected_registration != observed_registration:
        raise ValueError("Module spec differs from module_registration.json")
    module_exports = tuple(
        SkillModuleExport(
            export_id=source.export_id,
            module_id=source.module_id,
            instruction_members=(
                ReleaseMember(
                    member_ref=_instruction_member_ref(
                        skill_package_id=package_id,
                        module_id=source.module_id,
                        release_version=spec.release_version,
                    ),
                    member_sha256=sha256_text(source.prompt_text),
                    media_type="text/markdown",
                ),
            ),
        )
        for source in package_sources
    )
    package = SkillPackageRelease.build(
        skill_package_id=package_id,
        skill_package_version=spec.release_version,
        release_ref=f"skill-package:{package_id}@{spec.release_version}",
        owner_contract_ref=spec.owner_contract_ref,
        owner_contract_sha256=owner_sha256,
        module_exports=module_exports,
    )
    prompt_id = f"{spec.module_id}_prompt_bundle"
    prompt = compile_prompt_bundle_release(
        prompt_bundle_id=prompt_id,
        prompt_bundle_version=spec.release_version,
        compiler_version="task_plane_module_prompt_v3",
        components=prompt_components,
    )
    context_sha256 = sha256_text(spec.context_policy_ref)
    profile = compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=spec.execution_profile_id,
            executor_adapter_id=spec.executor_adapter_id,
            executor_adapter_revision=spec.executor_adapter_revision,
            transport_kind=spec.transport_kind,
            provider_id=spec.provider_id,
            model_id=spec.model_id,
            reasoning_profile=spec.reasoning_profile,
            execution_mode=spec.execution_mode,
            semantic_input_delivery_mode=(
                spec.semantic_input_delivery_mode
            ),
            attempt_workspace_policy=spec.attempt_workspace_policy,
            gateway_access_reasons=spec.gateway_access_reasons,
            output_constraint_mode=spec.output_constraint_mode,
            tool_policy=spec.tool_policy,
            network_policy=spec.network_policy,
            context_policy_ref=spec.context_policy_ref,
            timeout_seconds=spec.timeout_seconds,
            release_version=spec.release_version,
        )
    )
    module = RuntimeModuleRelease.build(
        module_id=spec.module_id,
        module_version=spec.release_version,
        release_ref=f"runtime-module:{spec.module_id}@{spec.release_version}",
        module_kind=ModuleKind.AGENT,
        owner_contract_ref=spec.owner_contract_ref,
        owner_contract_sha256=owner_sha256,
        source_skill_package_ref=package.release_ref,
        source_skill_package_sha256=package.release_sha256,
        source_export_id=spec.module_id,
        executable_ref=None,
        executable_sha256=None,
        input_schema_ref=spec.input_schema_ref,
        input_schema_sha256=input_schema_sha256,
        output_schema_ref=spec.output_schema_ref,
        output_schema_sha256=output_schema_sha256,
        prompt_bundle_ref=prompt.release_ref,
        prompt_bundle_sha256=prompt.release_sha256,
        declared_operation_ids=spec.declared_operation_ids,
        context_policy_ref=spec.context_policy_ref,
        context_policy_sha256=context_sha256,
        evaluation_policy_ref=spec.evaluation_policy_ref,
        evaluation_policy_sha256=sha256_text(spec.evaluation_policy_ref),
        retry_policy_ref=spec.retry_policy_ref,
        retry_policy_sha256=sha256_text(spec.retry_policy_ref),
        compatible_transport_kinds=(
            spec.compatible_transport_kinds
            or (spec.transport_kind,)
        ),
        entry_policy=spec.entry_policy,
        output_resolution_policy=spec.output_resolution_policy,
    )
    return CompiledAgentModuleRelease(
        skill_package=package,
        schema_assets=schema_assets,
        prompt_components=prompt_components,
        prompt_bundle=prompt,
        execution_profile=profile,
        module=module,
    )


def compile_non_agent_module_release(
    *,
    project_root: Path,
    module_id: str,
    module_kind: ModuleKind,
    owner_contract_ref: str,
    owner_contract_path: str,
    executable_ref: str,
    executable_path: str,
    input_schema_ref: str,
    output_schema_ref: str,
    declared_operation_ids: tuple[str, ...],
    compatible_transport_kind: str = "in_process",
    release_version: str = "candidate_v1",
    context_policy_ref: str = "context-policy:workflow_execution_isolated@v1",
    evaluation_policy_ref: str = "evaluation-policy:deterministic_candidate@v1",
    retry_policy_ref: str = "retry-policy:bounded_candidate@v1",
) -> RuntimeModuleRelease:
    """Compile one code-owned deterministic or service Module release."""

    if module_kind is ModuleKind.AGENT:
        raise ValueError("compile_non_agent_module_release rejects Agent Modules")
    return RuntimeModuleRelease.build(
        module_id=module_id,
        module_version=release_version,
        release_ref=f"runtime-module:{module_id}@{release_version}",
        module_kind=module_kind,
        owner_contract_ref=owner_contract_ref,
        owner_contract_sha256=sha256_file(
            project_root / owner_contract_path
        ),
        source_skill_package_ref=None,
        source_skill_package_sha256=None,
        source_export_id=None,
        executable_ref=executable_ref,
        executable_sha256=sha256_file(project_root / executable_path),
        input_schema_ref=input_schema_ref,
        input_schema_sha256=sha256_text(input_schema_ref),
        output_schema_ref=output_schema_ref,
        output_schema_sha256=sha256_text(output_schema_ref),
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=declared_operation_ids,
        context_policy_ref=context_policy_ref,
        context_policy_sha256=sha256_text(context_policy_ref),
        evaluation_policy_ref=evaluation_policy_ref,
        evaluation_policy_sha256=sha256_text(evaluation_policy_ref),
        retry_policy_ref=retry_policy_ref,
        retry_policy_sha256=sha256_text(retry_policy_ref),
        compatible_transport_kinds=(compatible_transport_kind,),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


def candidate_admission_record(
    record: Any,
    *,
    recorded_at_utc: str,
    evidence_members: tuple[ReleaseMember, ...] = (),
) -> ReleaseAdmissionRecord:
    """Build one initial candidate admission for an exact release record."""

    if isinstance(record, SkillPackageRelease):
        kind = ReleaseSubjectKind.SKILL_PACKAGE
        subject_id = record.skill_package_id
    elif isinstance(record, PromptComponentRelease):
        kind = ReleaseSubjectKind.PROMPT_COMPONENT
        subject_id = record.prompt_component_id
    elif isinstance(record, PromptBundleRelease):
        kind = ReleaseSubjectKind.PROMPT_BUNDLE
        subject_id = record.prompt_bundle_id
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
    return ReleaseAdmissionRecord.build(
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
        recorded_at_utc=recorded_at_utc,
    )


__all__ = [
    "AgentModuleReleaseSpec",
    "CompiledAgentModuleRelease",
    "ExecutionProfileReleaseSpec",
    "candidate_admission_record",
    "compile_agent_module_release",
    "compile_execution_profile_release",
    "compile_non_agent_module_release",
    "compile_prompt_bundle_release",
    "managed_skill_projection",
    "task_plane_output_schema",
    "sha256_file",
    "sha256_text",
]
