"""Dependency-closed registries for immutable Agent Runtime releases.

The registry is the target registration authority used by ``run_module`` and
PostgreSQL release persistence. Registration is lock-protected, exception-
atomic, duplicate-safe, and exact-hash based; no lookup resolves a mutable
``latest`` value during execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from types import MappingProxyType
from typing import Any, ClassVar, Mapping, TypeVar

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    ExecutionProfileRelease,
    EvaluationPolicyRelease,
    ExecutionVariantPolicyRelease,
    PromptComponentRelease,
    ModuleEntryPolicy,
    ModuleExecutionPurpose,
    PromptBundleRelease,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    ModuleRelease,
    SchemaAssetRelease,
    WorkflowNodeKind,
    WorkflowRelease,
    is_prompt_component_member_ref,
)
from ..foundation.foundation_json_schema_validation import (
    validate_json_document_against_schema,
    validate_json_schema_document,
)


_ReleaseT = TypeVar(
    "_ReleaseT",
    PromptComponentRelease,
    PromptBundleRelease,
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    RetryPolicyRelease,
    ExecutionVariantPolicyRelease,
    ExecutionProfileRelease,
    ModuleRelease,
    WorkflowRelease,
)


_RELEASE_BUNDLE_FIELD_TYPES: tuple[tuple[str, type[Any]], ...] = (
    ("schema_assets", SchemaAssetRelease),
    ("prompt_components", PromptComponentRelease),
    ("prompt_bundles", PromptBundleRelease),
    ("behavior_policies", BehaviorPolicyRelease),
    ("evaluation_policies", EvaluationPolicyRelease),
    ("retry_policies", RetryPolicyRelease),
    ("execution_variant_policies", ExecutionVariantPolicyRelease),
    ("execution_profiles", ExecutionProfileRelease),
    ("modules", ModuleRelease),
    ("workflows", WorkflowRelease),
)


@dataclass(frozen=True)
class RuntimeReleaseBundle:
    """One atomic registration batch across all dependency-ordered release kinds."""

    record_type: ClassVar[str] = "runtime_release_bundle"

    schema_assets: tuple[SchemaAssetRelease, ...] = ()
    prompt_components: tuple[PromptComponentRelease, ...] = ()
    prompt_bundles: tuple[PromptBundleRelease, ...] = ()
    behavior_policies: tuple[BehaviorPolicyRelease, ...] = ()
    evaluation_policies: tuple[EvaluationPolicyRelease, ...] = ()
    retry_policies: tuple[RetryPolicyRelease, ...] = ()
    execution_variant_policies: tuple[ExecutionVariantPolicyRelease, ...] = ()
    execution_profiles: tuple[ExecutionProfileRelease, ...] = ()
    modules: tuple[ModuleRelease, ...] = ()
    workflows: tuple[WorkflowRelease, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Serialize existing release records without source files or host state."""
        return {name: [record.as_dict() for record in getattr(self, name)]
                for name, _ in _RELEASE_BUNDLE_FIELD_TYPES}

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RuntimeReleaseBundle:
        """Decode release records; Registry still validates their full closure."""
        names = {name for name, _ in _RELEASE_BUNDLE_FIELD_TYPES}
        if type(payload) is not dict or set(payload) - names:
            raise ValueError("invalid Runtime release bundle fields")
        if any(type(value) is not list for value in payload.values()):
            raise ValueError("release bundle fields must be arrays")
        return cls(**{name: tuple(kind.from_dict(row) for row in payload.get(name, []))
                      for name, kind in _RELEASE_BUNDLE_FIELD_TYPES})

    def is_empty(self) -> bool:
        """Return whether the bundle carries no immutable release records."""

        return not any(
            (
                self.schema_assets,
                self.prompt_components,
                self.prompt_bundles,
                self.behavior_policies,
                self.evaluation_policies,
                self.retry_policies,
                self.execution_variant_policies,
                self.execution_profiles,
                self.modules,
                self.workflows,
            )
        )


@dataclass(frozen=True)
class RuntimeReleaseRegistrySnapshot:
    """Deterministic immutable view used by projections and PostgreSQL persistence."""

    record_type: ClassVar[str] = "runtime_release_registry_snapshot"

    schema_assets: tuple[SchemaAssetRelease, ...]
    prompt_components: tuple[PromptComponentRelease, ...]
    prompt_bundles: tuple[PromptBundleRelease, ...]
    behavior_policies: tuple[BehaviorPolicyRelease, ...]
    evaluation_policies: tuple[EvaluationPolicyRelease, ...]
    retry_policies: tuple[RetryPolicyRelease, ...]
    execution_variant_policies: tuple[ExecutionVariantPolicyRelease, ...]
    execution_profiles: tuple[ExecutionProfileRelease, ...]
    modules: tuple[ModuleRelease, ...]
    workflows: tuple[WorkflowRelease, ...]
    active_release_refs: Mapping[str, str]


@dataclass(frozen=True)
class RuntimeActiveReleasePointerResult:
    """Exact result of setting, clearing, or reading one entry pointer."""

    record_type: ClassVar[str] = "runtime_active_release_pointer_result"

    subject_kind: ReleaseSubjectKind
    subject_id: str
    active_release_ref: str | None
    active_release_sha256: str | None


@dataclass(frozen=True)
class RuntimeReleaseRegistrationResult:
    """Immutable success value shared by in-memory and persistent registration."""

    record_type: ClassVar[str] = "runtime_release_registration_result"

    submitted_bundle: RuntimeReleaseBundle
    catalog_snapshot: RuntimeReleaseRegistrySnapshot

    def validate(self) -> None:
        if type(self.submitted_bundle) is not RuntimeReleaseBundle:
            raise ValueError("submitted_bundle must be RuntimeReleaseBundle")
        if self.submitted_bundle.is_empty():
            raise ValueError("registration result cannot contain an empty bundle")
        if type(self.catalog_snapshot) is not RuntimeReleaseRegistrySnapshot:
            raise ValueError(
                "catalog_snapshot must be RuntimeReleaseRegistrySnapshot"
            )


class RuntimeReleaseRegistry:
    """In-memory target-model registry for immutable Runtime release objects."""

    service_id: ClassVar[str] = "runtime_release_registry"

    def __init__(self) -> None:
        self._registration_lock = RLock()
        self._schema_assets: dict[str, SchemaAssetRelease] = {}
        self._schema_version_keys: dict[tuple[str, str], str] = {}
        self._prompt_components: dict[
            str, PromptComponentRelease
        ] = {}
        self._prompt_bundles: dict[str, PromptBundleRelease] = {}
        self._behavior_policies: dict[str, BehaviorPolicyRelease] = {}
        self._evaluation_policies: dict[str, EvaluationPolicyRelease] = {}
        self._retry_policies: dict[str, RetryPolicyRelease] = {}
        self._execution_variant_policies: dict[
            str, ExecutionVariantPolicyRelease
        ] = {}
        self._execution_profiles: dict[str, ExecutionProfileRelease] = {}
        self._modules: dict[str, ModuleRelease] = {}
        self._workflows: dict[str, WorkflowRelease] = {}
        self._version_keys: dict[tuple[ReleaseSubjectKind, str, str], str] = {}
        self._active_release_refs: dict[
            tuple[ReleaseSubjectKind, str], str
        ] = {}

    def register_bundle(
        self,
        bundle: RuntimeReleaseBundle,
    ) -> RuntimeReleaseRegistrationResult:
        """Validate and atomically install one dependency-closed release bundle."""

        with self._registration_lock:
            self._register_bundle_unlocked(bundle, validate_schema_content=True)
            result = RuntimeReleaseRegistrationResult(
                submitted_bundle=bundle,
                catalog_snapshot=self.snapshot(),
            )
            result.validate()
            return result

    def _restore_persisted_bundle(self, bundle: RuntimeReleaseBundle) -> None:
        """Restore already-admitted records without rerunning authoring validators."""

        with self._registration_lock:
            self._register_bundle_unlocked(bundle, validate_schema_content=False)

    def _register_bundle_unlocked(
        self,
        bundle: RuntimeReleaseBundle,
        *,
        validate_schema_content: bool,
    ) -> None:
        """Install one bundle while the registration lock is held."""

        if type(bundle) is not RuntimeReleaseBundle or bundle.is_empty():
            raise ValueError("register_bundle requires a non-empty RuntimeReleaseBundle")
        for field_name, expected_type in _RELEASE_BUNDLE_FIELD_TYPES:
            records = getattr(bundle, field_name)
            if type(records) is not tuple:
                raise ValueError(f"release bundle {field_name} must be an immutable tuple")
            if any(type(record) is not expected_type for record in records):
                raise ValueError(
                    f"release bundle {field_name} must contain exact "
                    f"{expected_type.__name__} values"
                )

        staged = self._clone()
        for record in bundle.schema_assets:
            staged._register_schema_asset(
                record,
                validate_schema_content=validate_schema_content,
            )
        for record in bundle.prompt_components:
            staged._validate_prompt_component_closure(record)
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.PROMPT_COMPONENT,
                stable_id=record.prompt_component_id,
                version=record.prompt_component_version,
                target=staged._prompt_components,
            )
        for record in bundle.prompt_bundles:
            staged._validate_prompt_bundle_closure(record)
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.PROMPT_BUNDLE,
                stable_id=record.prompt_bundle_id,
                version=record.prompt_bundle_version,
                target=staged._prompt_bundles,
            )
        for record in bundle.behavior_policies:
            staged._validate_policy_closure(
                record,
                validate_schema_content=validate_schema_content,
            )
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.BEHAVIOR_POLICY,
                stable_id=record.policy_id,
                version=record.policy_version,
                target=staged._behavior_policies,
            )
        for record in bundle.evaluation_policies:
            staged._validate_policy_closure(
                record,
                validate_schema_content=validate_schema_content,
            )
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.EVALUATION_POLICY,
                stable_id=record.policy_id,
                version=record.policy_version,
                target=staged._evaluation_policies,
            )
        for record in bundle.retry_policies:
            staged._validate_policy_closure(
                record,
                validate_schema_content=validate_schema_content,
            )
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.RETRY_POLICY,
                stable_id=record.policy_id,
                version=record.policy_version,
                target=staged._retry_policies,
            )
        for record in bundle.execution_profiles:
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.EXECUTION_PROFILE,
                stable_id=record.execution_profile_id,
                version=record.execution_profile_version,
                target=staged._execution_profiles,
            )
        for record in bundle.modules:
            staged._validate_module_closure(record)
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.RUNTIME_MODULE,
                stable_id=record.module_id,
                version=record.module_version,
                target=staged._modules,
            )
        for record in bundle.workflows:
            staged._validate_workflow_closure(record)
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.WORKFLOW,
                stable_id=record.workflow_id,
                version=record.workflow_version,
                target=staged._workflows,
            )
        for record in bundle.execution_variant_policies:
            staged._validate_execution_variant_policy_closure(
                record,
                validate_schema_content=validate_schema_content,
            )
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.EXECUTION_VARIANT_POLICY,
                stable_id=record.policy_id,
                version=record.policy_version,
                target=staged._execution_variant_policies,
            )
        self._replace_with(staged)

    def get_prompt_bundle(
        self, release_ref: str, release_sha256: str
    ) -> PromptBundleRelease:
        """Resolve one exact Prompt Bundle Release."""

        return self._get_exact(
            self._prompt_bundles,
            release_ref,
            release_sha256,
            "Prompt Bundle",
        )

    def get_prompt_component(
        self, release_ref: str, release_sha256: str
    ) -> PromptComponentRelease:
        """Resolve one exact model-ready Context Component Release."""

        return self._get_exact(
            self._prompt_components,
            release_ref,
            release_sha256,
            "Prompt Component",
        )

    def get_schema_asset(
        self, release_ref: str, schema_sha256: str
    ) -> SchemaAssetRelease:
        """Resolve one exact schema body by its logical ref and content hash."""

        with self._registration_lock:
            try:
                record = self._schema_assets[release_ref]
            except KeyError as exc:
                raise KeyError(
                    f"unknown Schema Asset release: {release_ref}"
                ) from exc
            if record.schema_sha256 != schema_sha256:
                raise ValueError(f"Schema Asset hash mismatch: {release_ref}")
            record.validate()
            return record

    def get_execution_profile(
        self, release_ref: str, release_sha256: str
    ) -> ExecutionProfileRelease:
        """Resolve one exact Execution Profile Release."""

        return self._get_exact(
            self._execution_profiles,
            release_ref,
            release_sha256,
            "Execution Profile",
        )

    def get_behavior_policy(
        self, release_ref: str, release_sha256: str
    ) -> BehaviorPolicyRelease:
        """Resolve one exact Behavior Policy Release."""

        return self._get_exact(
            self._behavior_policies,
            release_ref,
            release_sha256,
            "Behavior Policy",
        )

    def get_evaluation_policy(
        self, release_ref: str, release_sha256: str
    ) -> EvaluationPolicyRelease:
        """Resolve one exact Evaluation Policy Release."""

        return self._get_exact(
            self._evaluation_policies,
            release_ref,
            release_sha256,
            "Evaluation Policy",
        )

    def get_retry_policy(
        self, release_ref: str, release_sha256: str
    ) -> RetryPolicyRelease:
        """Resolve one exact Retry Policy Release."""

        return self._get_exact(
            self._retry_policies,
            release_ref,
            release_sha256,
            "Retry Policy",
        )

    def get_execution_variant_policy(
        self, release_ref: str, release_sha256: str
    ) -> ExecutionVariantPolicyRelease:
        """Resolve one exact Execution Variant Policy Release."""

        return self._get_exact(
            self._execution_variant_policies,
            release_ref,
            release_sha256,
            "Execution Variant Policy",
        )

    def get_module(
        self, release_ref: str, release_sha256: str
    ) -> ModuleRelease:
        """Resolve one exact Module Release."""

        return self._get_exact(
            self._modules,
            release_ref,
            release_sha256,
            "Runtime Module",
        )

    def resolve_registered_module_release(
        self,
        release_ref: str,
        release_sha256: str,
    ) -> ModuleRelease:
        """Resolve the exact Module Release created by Runtime registration."""

        return self.get_module(release_ref, release_sha256)

    def get_workflow(
        self, release_ref: str, release_sha256: str
    ) -> WorkflowRelease:
        """Resolve one exact Workflow Release."""

        return self._get_exact(
            self._workflows,
            release_ref,
            release_sha256,
            "Workflow",
        )

    def set_active_release(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
        release_ref: str,
        release_sha256: str,
    ) -> RuntimeActiveReleasePointerResult:
        """Atomically point one Module or Workflow identity at an exact release."""

        with self._registration_lock:
            record = self._entry_release(
                subject_kind,
                release_ref,
                release_sha256,
            )
            if self._stable_id(record) != subject_id:
                raise ValueError("active pointer subject_id differs from release")
            self._active_release_refs[(subject_kind, subject_id)] = release_ref
            return self._active_pointer_result(subject_kind, subject_id)

    def clear_active_release(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
        *,
        expected_release_ref: str,
        expected_release_sha256: str,
    ) -> RuntimeActiveReleasePointerResult:
        """Clear one pointer only when its exact current target still matches."""

        with self._registration_lock:
            expected = self._entry_release(
                subject_kind,
                expected_release_ref,
                expected_release_sha256,
            )
            if self._stable_id(expected) != subject_id:
                raise ValueError("active pointer subject_id differs from release")
            key = (subject_kind, subject_id)
            current = self._active_release_refs.get(key)
            if current is None:
                return self._active_pointer_result(subject_kind, subject_id)
            if current != expected_release_ref:
                raise ValueError("active pointer current target differs from expected")
            del self._active_release_refs[key]
            return self._active_pointer_result(subject_kind, subject_id)

    def resolve_active_release(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
    ) -> ModuleRelease | WorkflowRelease:
        """Resolve the exact immutable release selected by one entry pointer."""

        with self._registration_lock:
            result = self._active_pointer_result(subject_kind, subject_id)
            if result.active_release_ref is None or result.active_release_sha256 is None:
                raise KeyError(
                    f"no active {subject_kind.value} release: {subject_id}"
                )
            return self._entry_release(
                subject_kind,
                result.active_release_ref,
                result.active_release_sha256,
            )

    def assert_module_execution_allowed(
        self,
        module: ModuleRelease,
        purpose: ModuleExecutionPurpose,
    ) -> None:
        """Require an active standalone entry and preserve Module entry policy."""

        if type(module) is not ModuleRelease:
            raise ValueError("module must be an exact ModuleRelease")
        if type(purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        if (
            purpose is ModuleExecutionPurpose.STANDALONE
            and module.entry_policy is ModuleEntryPolicy.WORKFLOW_BOUND
        ):
            raise PermissionError("workflow-bound Module cannot run as a product entry")
        if purpose is ModuleExecutionPurpose.STANDALONE:
            try:
                active = self.resolve_active_release(
                    ReleaseSubjectKind.RUNTIME_MODULE,
                    module.module_id,
                )
            except KeyError as exc:
                raise PermissionError("Module has no active standalone entry") from exc
            if active != module:
                raise PermissionError("Module release is not the active standalone entry")

    def assert_workflow_execution_allowed(
        self,
        workflow: WorkflowRelease,
        purpose: ModuleExecutionPurpose,
    ) -> None:
        """Require the active Workflow for ordinary Workflow execution."""

        if type(workflow) is not WorkflowRelease:
            raise ValueError("workflow must be an exact WorkflowRelease")
        if type(purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        if purpose is ModuleExecutionPurpose.WORKFLOW:
            try:
                active = self.resolve_active_release(
                    ReleaseSubjectKind.WORKFLOW,
                    workflow.workflow_id,
                )
            except KeyError as exc:
                raise PermissionError("Workflow has no active entry") from exc
            if active != workflow:
                raise PermissionError("Workflow release is not the active entry")

    def snapshot(self) -> RuntimeReleaseRegistrySnapshot:
        """Return a deterministic immutable registry snapshot."""

        with self._registration_lock:
            active = {
                f"{kind.value}:{subject_id}": release_ref
                for (kind, subject_id), release_ref in sorted(
                    self._active_release_refs.items(),
                    key=lambda item: (item[0][0].value, item[0][1]),
                )
            }
            return RuntimeReleaseRegistrySnapshot(
                schema_assets=tuple(
                    self._schema_assets[key] for key in sorted(self._schema_assets)
                ),
                prompt_components=tuple(
                    self._prompt_components[key]
                    for key in sorted(self._prompt_components)
                ),
                prompt_bundles=tuple(
                    self._prompt_bundles[key]
                    for key in sorted(self._prompt_bundles)
                ),
                behavior_policies=tuple(
                    self._behavior_policies[key]
                    for key in sorted(self._behavior_policies)
                ),
                evaluation_policies=tuple(
                    self._evaluation_policies[key]
                    for key in sorted(self._evaluation_policies)
                ),
                retry_policies=tuple(
                    self._retry_policies[key]
                    for key in sorted(self._retry_policies)
                ),
                execution_variant_policies=tuple(
                    self._execution_variant_policies[key]
                    for key in sorted(self._execution_variant_policies)
                ),
                execution_profiles=tuple(
                    self._execution_profiles[key]
                    for key in sorted(self._execution_profiles)
                ),
                modules=tuple(self._modules[key] for key in sorted(self._modules)),
                workflows=tuple(
                    self._workflows[key] for key in sorted(self._workflows)
                ),
                active_release_refs=MappingProxyType(active),
            )

    def _clone(self) -> "RuntimeReleaseRegistry":
        staged = RuntimeReleaseRegistry()
        staged._schema_assets = dict(self._schema_assets)
        staged._schema_version_keys = dict(self._schema_version_keys)
        staged._prompt_components = dict(
            self._prompt_components
        )
        staged._prompt_bundles = dict(self._prompt_bundles)
        staged._behavior_policies = dict(self._behavior_policies)
        staged._evaluation_policies = dict(self._evaluation_policies)
        staged._retry_policies = dict(self._retry_policies)
        staged._execution_variant_policies = dict(
            self._execution_variant_policies
        )
        staged._execution_profiles = dict(self._execution_profiles)
        staged._modules = dict(self._modules)
        staged._workflows = dict(self._workflows)
        staged._version_keys = dict(self._version_keys)
        staged._active_release_refs = dict(self._active_release_refs)
        return staged

    def _replace_with(self, staged: "RuntimeReleaseRegistry") -> None:
        self._schema_assets = staged._schema_assets
        self._schema_version_keys = staged._schema_version_keys
        self._prompt_components = staged._prompt_components
        self._prompt_bundles = staged._prompt_bundles
        self._behavior_policies = staged._behavior_policies
        self._evaluation_policies = staged._evaluation_policies
        self._retry_policies = staged._retry_policies
        self._execution_variant_policies = staged._execution_variant_policies
        self._execution_profiles = staged._execution_profiles
        self._modules = staged._modules
        self._workflows = staged._workflows
        self._version_keys = staged._version_keys
        self._active_release_refs = staged._active_release_refs

    def _register_release(
        self,
        record: _ReleaseT,
        *,
        kind: ReleaseSubjectKind,
        stable_id: str,
        version: str,
        target: dict[str, _ReleaseT],
    ) -> None:
        if type(record) not in {
            PromptComponentRelease,
            PromptBundleRelease,
            BehaviorPolicyRelease,
            EvaluationPolicyRelease,
            RetryPolicyRelease,
            ExecutionVariantPolicyRelease,
            ExecutionProfileRelease,
            ModuleRelease,
            WorkflowRelease,
        }:
            raise ValueError("release bundle contains an unsupported record type")
        record.validate()
        existing = target.get(record.release_ref)
        if existing is not None:
            if existing != record:
                raise ValueError(f"release_ref collision: {record.release_ref}")
            return
        version_key = (kind, stable_id, version)
        prior_ref = self._version_keys.get(version_key)
        if prior_ref is not None and prior_ref != record.release_ref:
            raise ValueError(
                f"release version already registered with another ref: {stable_id}@{version}"
            )
        target[record.release_ref] = record
        self._version_keys[version_key] = record.release_ref

    def _register_schema_asset(
        self,
        record: SchemaAssetRelease,
        *,
        validate_schema_content: bool,
    ) -> None:
        if type(record) is not SchemaAssetRelease:
            raise ValueError("schema_assets must contain SchemaAssetRelease values")
        record.validate()
        if validate_schema_content:
            validate_json_schema_document(record.schema_document())
        existing = self._schema_assets.get(record.release_ref)
        if existing is not None:
            if existing != record:
                raise ValueError(
                    f"Schema Asset release_ref collision: {record.release_ref}"
                )
            return
        version_key = (record.schema_asset_id, record.schema_asset_version)
        prior_ref = self._schema_version_keys.get(version_key)
        if prior_ref is not None and prior_ref != record.release_ref:
            raise ValueError(
                "Schema Asset version already registered with another ref: "
                f"{record.schema_asset_id}@{record.schema_asset_version}"
            )
        self._schema_assets[record.release_ref] = record
        self._schema_version_keys[version_key] = record.release_ref

    def _validate_prompt_component_closure(
        self,
        component: PromptComponentRelease,
    ) -> None:
        """Resolve every Registry-addressed source member by its exact hash."""

        component.validate()
        for member in component.source_members:
            if member.member_ref.startswith("schema:"):
                schema = self.get_schema_asset(
                    member.member_ref,
                    member.member_sha256,
                )
                if member.media_type != "application/schema+json":
                    raise ValueError(
                        "Prompt Component Schema member media type differs from "
                        "the registered Schema Asset"
                    )

    def _validate_prompt_bundle_closure(
        self, prompt_bundle: PromptBundleRelease
    ) -> None:
        """Require exact closure for every registered Context Component member."""

        prompt_bundle.validate()
        resolved_components: list[PromptComponentRelease] = []
        for member in prompt_bundle.members:
            if is_prompt_component_member_ref(member.member_ref):
                component = self.get_prompt_component(
                    member.member_ref,
                    member.member_sha256,
                )
                if component.media_type != member.media_type:
                    raise ValueError(
                        "Prompt Bundle member media type differs from Context Component"
                    )
                resolved_components.append(component)
        if resolved_components:
            if len(resolved_components) != len(prompt_bundle.members):
                raise ValueError(
                    "Prompt Bundle cannot mix Context Components with legacy members"
                )
            expected_body = "".join(
                component.formatted_content
                for component in resolved_components
            )
            if prompt_bundle.compiled_static_body != expected_body:
                raise ValueError(
                    "Prompt Bundle body differs from its ordered Context Components"
                )

    def _validate_policy_closure(
        self,
        policy: (
            BehaviorPolicyRelease
            | EvaluationPolicyRelease
            | RetryPolicyRelease
            | ExecutionVariantPolicyRelease
        ),
        *,
        validate_schema_content: bool,
    ) -> None:
        """Resolve policy schema exactly and validate new policy documents."""

        if type(policy) not in {
            BehaviorPolicyRelease,
            EvaluationPolicyRelease,
            RetryPolicyRelease,
            ExecutionVariantPolicyRelease,
        }:
            raise ValueError("unsupported policy release type")
        policy.validate()
        schema = self.get_schema_asset(
            policy.policy_schema_ref,
            policy.policy_schema_sha256,
        )
        if validate_schema_content:
            validate_json_document_against_schema(
                policy.policy_document(),
                schema.schema_document(),
            )

    def _validate_execution_variant_policy_closure(
        self,
        policy: ExecutionVariantPolicyRelease,
        *,
        validate_schema_content: bool,
    ) -> None:
        """Validate exact origin and profile closure for one Variant Policy."""

        if type(policy) is not ExecutionVariantPolicyRelease:
            raise ValueError(
                "execution_variant_policies must contain exact "
                "ExecutionVariantPolicyRelease values"
            )
        self._validate_policy_closure(
            policy,
            validate_schema_content=validate_schema_content,
        )
        document = policy.policy_document()
        if not document["bindings"]:
            raise ValueError(
                "Execution Variant Policy requires at least one profile binding"
            )
        origin_kind = document["origin_kind"]
        origin_module = None
        origin_workflow = None
        if origin_kind == "workflow":
            origin_workflow = self.get_workflow(
                document["origin_release_ref"],
                document["origin_release_sha256"],
            )
        elif origin_kind == "standalone_module":
            origin_module = self.get_module(
                document["origin_release_ref"],
                document["origin_release_sha256"],
            )
        else:  # pragma: no cover - schema validation guards this branch
            raise ValueError("invalid Execution Variant origin_kind")
        positions: set[str] = set()
        for binding in document["bindings"]:
            position_id = binding["position_id"]
            if position_id in positions:
                raise ValueError(
                    "Execution Variant position_id values must be unique"
                )
            positions.add(position_id)
            profile = self.get_execution_profile(
                binding["execution_profile_release_ref"],
                binding["execution_profile_release_sha256"],
            )
            target = origin_module
            if origin_workflow is not None:
                node = next((node for node in origin_workflow.nodes if node.node_id == position_id), None)
                if node is None or node.node_kind is not WorkflowNodeKind.MODULE:
                    raise ValueError("Variant position must identify an exact Workflow Module node")
                target = self.get_module(node.module_release_ref, node.module_release_sha256)
            if target.execution_requirements is not None:
                target.execution_requirements.assert_profile(profile)
            elif target.reviewer_defaults is not None:
                # Historical bindings retain their original shape constraints;
                # source transport lists do not decide new Agent compatibility.
                target.reviewer_defaults.assert_profile(profile)

    def _validate_module_closure(self, module: ModuleRelease) -> None:
        module.validate()
        if module.prompt_bundle_ref is not None:
            if module.prompt_bundle_sha256 is None:
                raise ValueError("Module Prompt Bundle hash is missing")
            self.get_prompt_bundle(
                module.prompt_bundle_ref,
                module.prompt_bundle_sha256,
            )
        behavior = self.get_behavior_policy(
            module.behavior_policy_ref,
            module.behavior_policy_sha256,
        )
        self.get_evaluation_policy(
            module.evaluation_policy_ref,
            module.evaluation_policy_sha256,
        )
        retry = self.get_retry_policy(
            module.retry_policy_ref,
            module.retry_policy_sha256,
        )
        # Restoring a historical definition does not ask whether today's
        # executor can run it. In particular, do not call the execution getter
        # for every old snapshot while restoring a whole catalog.
        requirements = module.execution_requirements
        if requirements is None:
            requirements = module.reviewer_defaults
        if requirements is not None:
            if (requirements.context_isolation != behavior.policy_document()["context_isolation"]
                    or requirements.max_attempts != retry.policy_document()["max_attempts"]):
                raise ValueError("Module requirements differ from the exact Module policies")
        self.get_schema_asset(
            module.input_schema_ref,
            module.input_schema_sha256,
        )
        self.get_schema_asset(
            module.output_schema_ref,
            module.output_schema_sha256,
        )

    def _validate_workflow_closure(self, workflow: WorkflowRelease) -> None:
        workflow.validate()
        for node in workflow.nodes:
            if node.node_kind is WorkflowNodeKind.MODULE:
                if (
                    node.module_release_ref is None
                    or node.module_release_sha256 is None
                ):
                    raise ValueError(
                        "MODULE workflow node requires module_release_ref and sha256"
                    )
                self.get_module(
                    node.module_release_ref,
                    node.module_release_sha256,
                )

    def _entry_release(
        self,
        subject_kind: ReleaseSubjectKind,
        release_ref: str,
        release_sha256: str,
    ) -> ModuleRelease | WorkflowRelease:
        if subject_kind is ReleaseSubjectKind.RUNTIME_MODULE:
            return self.get_module(release_ref, release_sha256)
        if subject_kind is ReleaseSubjectKind.WORKFLOW:
            return self.get_workflow(release_ref, release_sha256)
        raise ValueError("active pointer supports only Module or Workflow")

    def _active_pointer_result(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
    ) -> RuntimeActiveReleasePointerResult:
        if subject_kind not in {
            ReleaseSubjectKind.RUNTIME_MODULE,
            ReleaseSubjectKind.WORKFLOW,
        }:
            raise ValueError("active pointer supports only Module or Workflow")
        release_ref = self._active_release_refs.get((subject_kind, subject_id))
        if release_ref is None:
            return RuntimeActiveReleasePointerResult(
                subject_kind=subject_kind,
                subject_id=subject_id,
                active_release_ref=None,
                active_release_sha256=None,
            )
        table: Mapping[str, ModuleRelease | WorkflowRelease]
        if subject_kind is ReleaseSubjectKind.RUNTIME_MODULE:
            table = self._modules
        else:
            table = self._workflows
        release = table[release_ref]
        return RuntimeActiveReleasePointerResult(
            subject_kind=subject_kind,
            subject_id=subject_id,
            active_release_ref=release.release_ref,
            active_release_sha256=release.release_sha256,
        )

    @staticmethod
    def _stable_id(record: Any) -> str:
        for field_name in (
            "prompt_component_id",
            "prompt_bundle_id",
            "schema_asset_id",
            "policy_id",
            "execution_profile_id",
            "module_id",
            "workflow_id",
        ):
            if hasattr(record, field_name):
                return getattr(record, field_name)
        raise TypeError("release record has no stable identity")

    def _get_exact(
        self,
        table: Mapping[str, _ReleaseT],
        release_ref: str,
        release_sha256: str,
        label: str,
    ) -> _ReleaseT:
        with self._registration_lock:
            try:
                record = table[release_ref]
            except KeyError as exc:
                raise KeyError(f"unknown {label} release: {release_ref}") from exc
            if record.release_sha256 != release_sha256:
                raise ValueError(f"{label} release hash mismatch: {release_ref}")
            return record


__all__ = [
    "RuntimeActiveReleasePointerResult",
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistrationResult",
    "RuntimeReleaseRegistry",
    "RuntimeReleaseRegistrySnapshot",
]
