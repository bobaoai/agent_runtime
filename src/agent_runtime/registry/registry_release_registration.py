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
    ExecutionProfileRelease,
    PromptComponentRelease,
    ModuleEntryPolicy,
    ModuleExecutionPurpose,
    PromptBundleRelease,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    SkillPackageRelease,
    WorkflowNodeKind,
    WorkflowRelease,
)


_ReleaseT = TypeVar(
    "_ReleaseT",
    SkillPackageRelease,
    PromptComponentRelease,
    PromptBundleRelease,
    ExecutionProfileRelease,
    RuntimeModuleRelease,
    WorkflowRelease,
)


def _allowed_admission_states(
    purpose: ModuleExecutionPurpose,
) -> frozenset[ReleaseAdmissionState]:
    """Return the shared release-admission matrix for one execution purpose."""

    if purpose in {
        ModuleExecutionPurpose.TEST,
        ModuleExecutionPurpose.EVALUATION,
    }:
        return frozenset(
            {
                ReleaseAdmissionState.CANDIDATE,
                ReleaseAdmissionState.SHADOW_EXECUTABLE,
                ReleaseAdmissionState.PRODUCTION_CANARY,
                ReleaseAdmissionState.ACTIVE,
            }
        )
    if purpose is ModuleExecutionPurpose.REPLAY:
        return frozenset(
            {
                ReleaseAdmissionState.CANDIDATE,
                ReleaseAdmissionState.SHADOW_EXECUTABLE,
                ReleaseAdmissionState.PRODUCTION_CANARY,
                ReleaseAdmissionState.ACTIVE,
                ReleaseAdmissionState.SUPERSEDED,
            }
        )
    return frozenset(
        {
            ReleaseAdmissionState.PRODUCTION_CANARY,
            ReleaseAdmissionState.ACTIVE,
        }
    )


@dataclass(frozen=True)
class RuntimeReleaseBundle:
    """One atomic registration batch across all dependency-ordered release kinds."""

    record_type: ClassVar[str] = "runtime_release_bundle"

    skill_packages: tuple[SkillPackageRelease, ...] = ()
    schema_assets: tuple[SchemaAssetRelease, ...] = ()
    prompt_components: tuple[PromptComponentRelease, ...] = ()
    prompt_bundles: tuple[PromptBundleRelease, ...] = ()
    execution_profiles: tuple[ExecutionProfileRelease, ...] = ()
    modules: tuple[RuntimeModuleRelease, ...] = ()
    workflows: tuple[WorkflowRelease, ...] = ()
    admissions: tuple[ReleaseAdmissionRecord, ...] = ()

    def is_empty(self) -> bool:
        """Return whether the bundle carries no release or admission records."""

        return not any(
            (
                self.skill_packages,
                self.schema_assets,
                self.prompt_components,
                self.prompt_bundles,
                self.execution_profiles,
                self.modules,
                self.workflows,
                self.admissions,
            )
        )


@dataclass(frozen=True)
class RuntimeReleaseRegistrySnapshot:
    """Deterministic immutable view used by projections and PostgreSQL persistence."""

    record_type: ClassVar[str] = "runtime_release_registry_snapshot"

    skill_packages: tuple[SkillPackageRelease, ...]
    schema_assets: tuple[SchemaAssetRelease, ...]
    prompt_components: tuple[PromptComponentRelease, ...]
    prompt_bundles: tuple[PromptBundleRelease, ...]
    execution_profiles: tuple[ExecutionProfileRelease, ...]
    modules: tuple[RuntimeModuleRelease, ...]
    workflows: tuple[WorkflowRelease, ...]
    admissions: tuple[ReleaseAdmissionRecord, ...]
    active_release_refs: Mapping[str, str]


class RuntimeReleaseRegistry:
    """In-memory target-model registry for immutable Runtime release objects."""

    service_id: ClassVar[str] = "runtime_release_registry"

    def __init__(self) -> None:
        self._registration_lock = RLock()
        self._skill_packages: dict[str, SkillPackageRelease] = {}
        self._schema_assets: dict[str, SchemaAssetRelease] = {}
        self._schema_version_keys: dict[tuple[str, str], str] = {}
        self._prompt_components: dict[
            str, PromptComponentRelease
        ] = {}
        self._prompt_bundles: dict[str, PromptBundleRelease] = {}
        self._execution_profiles: dict[str, ExecutionProfileRelease] = {}
        self._modules: dict[str, RuntimeModuleRelease] = {}
        self._workflows: dict[str, WorkflowRelease] = {}
        self._version_keys: dict[tuple[ReleaseSubjectKind, str, str], str] = {}
        self._admissions_by_id: dict[str, ReleaseAdmissionRecord] = {}
        self._admission_history: dict[
            tuple[ReleaseSubjectKind, str], list[ReleaseAdmissionRecord]
        ] = {}
        self._active_release_refs: dict[
            tuple[ReleaseSubjectKind, str], str
        ] = {}

    def register_bundle(self, bundle: RuntimeReleaseBundle) -> None:
        """Validate and atomically install one dependency-closed release bundle."""

        with self._registration_lock:
            self._register_bundle_unlocked(bundle)

    def _register_bundle_unlocked(self, bundle: RuntimeReleaseBundle) -> None:
        """Install one bundle while the registration lock is held."""

        if type(bundle) is not RuntimeReleaseBundle or bundle.is_empty():
            raise ValueError("register_bundle requires a non-empty RuntimeReleaseBundle")
        for field_name in (
            "skill_packages",
            "schema_assets",
            "prompt_components",
            "prompt_bundles",
            "execution_profiles",
            "modules",
            "workflows",
            "admissions",
        ):
            if type(getattr(bundle, field_name)) is not tuple:
                raise ValueError(f"release bundle {field_name} must be an immutable tuple")

        staged = self._clone()
        for record in bundle.schema_assets:
            staged._register_schema_asset(record)
        for record in bundle.skill_packages:
            staged._register_release(
                record,
                kind=ReleaseSubjectKind.SKILL_PACKAGE,
                stable_id=record.skill_package_id,
                version=record.skill_package_version,
                target=staged._skill_packages,
            )
        for record in bundle.prompt_components:
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
        for admission in bundle.admissions:
            staged._register_admission(admission)

        self._replace_with(staged)

    def get_skill_package(
        self, release_ref: str, release_sha256: str
    ) -> SkillPackageRelease:
        """Resolve one exact Skill Package Release."""

        return self._get_exact(
            self._skill_packages,
            release_ref,
            release_sha256,
            "Skill Package",
        )

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

    def get_module(
        self, release_ref: str, release_sha256: str
    ) -> RuntimeModuleRelease:
        """Resolve one exact Runtime Module Release."""

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
    ) -> RuntimeModuleRelease:
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

    def get_admission_state(
        self,
        subject_kind: ReleaseSubjectKind,
        release_ref: str,
    ) -> ReleaseAdmissionState:
        """Return the latest append-only admission state for one exact release."""

        with self._registration_lock:
            history = self._admission_history.get((subject_kind, release_ref))
            if not history:
                raise RuntimeError(f"release has no admission record: {release_ref}")
            return history[-1].state

    def active_release_ref(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
    ) -> str:
        """Return the active pointer for inspection or new-execution binding."""

        with self._registration_lock:
            try:
                return self._active_release_refs[(subject_kind, subject_id)]
            except KeyError as exc:
                raise KeyError(
                    f"no active {subject_kind.value} release: {subject_id}"
                ) from exc

    def assert_module_execution_allowed(
        self,
        module: RuntimeModuleRelease,
        purpose: ModuleExecutionPurpose,
    ) -> None:
        """Fail closed when admission or entry policy does not allow execution."""

        if type(module) is not RuntimeModuleRelease:
            raise ValueError("module must be an exact RuntimeModuleRelease")
        if type(purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        state = self.get_admission_state(
            ReleaseSubjectKind.RUNTIME_MODULE,
            module.release_ref,
        )
        allowed = _allowed_admission_states(purpose)
        if state not in allowed:
            raise PermissionError(
                f"Module release is not admitted for {purpose.value}: {state.value}"
            )
        if (
            purpose is ModuleExecutionPurpose.STANDALONE
            and module.entry_policy is ModuleEntryPolicy.WORKFLOW_BOUND
        ):
            raise PermissionError("workflow-bound Module cannot run as a product entry")

    def assert_workflow_execution_allowed(
        self,
        workflow: WorkflowRelease,
        purpose: ModuleExecutionPurpose,
    ) -> None:
        """Fail closed when Workflow admission does not allow this execution."""

        if type(workflow) is not WorkflowRelease:
            raise ValueError("workflow must be an exact WorkflowRelease")
        if type(purpose) is not ModuleExecutionPurpose:
            raise ValueError("purpose must be a ModuleExecutionPurpose")
        state = self.get_admission_state(
            ReleaseSubjectKind.WORKFLOW,
            workflow.release_ref,
        )
        allowed = _allowed_admission_states(purpose)
        if state not in allowed:
            raise PermissionError(
                f"Workflow release is not admitted for {purpose.value}: {state.value}"
            )

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
                skill_packages=tuple(
                    self._skill_packages[key] for key in sorted(self._skill_packages)
                ),
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
                execution_profiles=tuple(
                    self._execution_profiles[key]
                    for key in sorted(self._execution_profiles)
                ),
                modules=tuple(self._modules[key] for key in sorted(self._modules)),
                workflows=tuple(
                    self._workflows[key] for key in sorted(self._workflows)
                ),
                admissions=tuple(
                    self._admissions_by_id[key]
                    for key in sorted(self._admissions_by_id)
                ),
                active_release_refs=MappingProxyType(active),
            )

    def _clone(self) -> "RuntimeReleaseRegistry":
        staged = RuntimeReleaseRegistry()
        staged._skill_packages = dict(self._skill_packages)
        staged._schema_assets = dict(self._schema_assets)
        staged._schema_version_keys = dict(self._schema_version_keys)
        staged._prompt_components = dict(
            self._prompt_components
        )
        staged._prompt_bundles = dict(self._prompt_bundles)
        staged._execution_profiles = dict(self._execution_profiles)
        staged._modules = dict(self._modules)
        staged._workflows = dict(self._workflows)
        staged._version_keys = dict(self._version_keys)
        staged._admissions_by_id = dict(self._admissions_by_id)
        staged._admission_history = {
            key: list(history) for key, history in self._admission_history.items()
        }
        staged._active_release_refs = dict(self._active_release_refs)
        return staged

    def _replace_with(self, staged: "RuntimeReleaseRegistry") -> None:
        self._skill_packages = staged._skill_packages
        self._schema_assets = staged._schema_assets
        self._schema_version_keys = staged._schema_version_keys
        self._prompt_components = staged._prompt_components
        self._prompt_bundles = staged._prompt_bundles
        self._execution_profiles = staged._execution_profiles
        self._modules = staged._modules
        self._workflows = staged._workflows
        self._version_keys = staged._version_keys
        self._admissions_by_id = staged._admissions_by_id
        self._admission_history = staged._admission_history
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
            SkillPackageRelease,
            PromptComponentRelease,
            PromptBundleRelease,
            ExecutionProfileRelease,
            RuntimeModuleRelease,
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

    def _register_schema_asset(self, record: SchemaAssetRelease) -> None:
        if type(record) is not SchemaAssetRelease:
            raise ValueError("schema_assets must contain SchemaAssetRelease values")
        record.validate()
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

    def _validate_prompt_bundle_closure(
        self, prompt_bundle: PromptBundleRelease
    ) -> None:
        """Require exact closure for every registered Context Component member."""

        prompt_bundle.validate()
        resolved_components: list[PromptComponentRelease] = []
        for member in prompt_bundle.members:
            if member.member_ref.startswith("prompt-component:"):
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

    def _validate_module_closure(self, module: RuntimeModuleRelease) -> None:
        module.validate()
        if module.source_skill_package_ref is not None:
            if module.source_skill_package_sha256 is None:
                raise ValueError("Module source Skill Package hash is missing")
            package = self.get_skill_package(
                module.source_skill_package_ref,
                module.source_skill_package_sha256,
            )
            matching_exports = tuple(
                export
                for export in package.module_exports
                if export.export_id == module.source_export_id
            )
            if len(matching_exports) != 1:
                raise ValueError("Module source export is absent or ambiguous")
            if matching_exports[0].module_id != module.module_id:
                raise ValueError("Module source export targets another module_id")
        if module.prompt_bundle_ref is not None:
            if module.prompt_bundle_sha256 is None:
                raise ValueError("Module Prompt Bundle hash is missing")
            self.get_prompt_bundle(
                module.prompt_bundle_ref,
                module.prompt_bundle_sha256,
            )
        schema_asset_bound = (
            module.input_schema_ref in self._schema_assets
            or module.output_schema_ref in self._schema_assets
        )
        if schema_asset_bound:
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

    def _register_admission(self, admission: ReleaseAdmissionRecord) -> None:
        if type(admission) is not ReleaseAdmissionRecord:
            raise ValueError("admissions must be exact ReleaseAdmissionRecord values")
        admission.validate()
        existing = self._admissions_by_id.get(admission.admission_id)
        if existing is not None:
            if existing.as_dict() != admission.as_dict():
                raise ValueError(f"admission_id collision: {admission.admission_id}")
            return

        record = self._release_for_admission(admission)
        record_id = self._stable_id(record)
        if record_id != admission.subject_id:
            raise ValueError("admission subject_id does not match the release")
        if (
            admission.subject_kind is ReleaseSubjectKind.RUNTIME_MODULE
            and admission.state
            in {
                ReleaseAdmissionState.PRODUCTION_CANARY,
                ReleaseAdmissionState.ACTIVE,
            }
        ):
            if not isinstance(record, RuntimeModuleRelease):
                raise TypeError(
                    "production Module admission must resolve to RuntimeModuleRelease"
                )
            if (
                record.input_schema_ref not in self._schema_assets
                or record.output_schema_ref not in self._schema_assets
            ):
                raise ValueError(
                    "production Module admission requires registered input and "
                    "output Schema Assets"
                )
            self.get_schema_asset(
                record.input_schema_ref,
                record.input_schema_sha256,
            )
            self.get_schema_asset(
                record.output_schema_ref,
                record.output_schema_sha256,
            )
        history_key = (admission.subject_kind, admission.release_ref)
        history = self._admission_history.setdefault(history_key, [])
        prior_state = history[-1].state if history else None
        self._validate_admission_transition(prior_state, admission.state)

        active_key = (admission.subject_kind, admission.subject_id)
        current_active = self._active_release_refs.get(active_key)
        if admission.state is ReleaseAdmissionState.ACTIVE:
            if current_active is not None and current_active != admission.release_ref:
                old_history = self._admission_history.get(
                    (admission.subject_kind, current_active), []
                )
                old_state = old_history[-1].state if old_history else None
                if old_state not in {
                    ReleaseAdmissionState.SUPERSEDED,
                    ReleaseAdmissionState.RETIRED,
                }:
                    raise ValueError(
                        "activating a replacement requires the old release to be "
                        "superseded before the replacement activation is applied"
                    )
            self._active_release_refs[active_key] = admission.release_ref
        elif admission.state in {
            ReleaseAdmissionState.SUPERSEDED,
            ReleaseAdmissionState.RETIRED,
        } and current_active == admission.release_ref:
            del self._active_release_refs[active_key]

        history.append(admission)
        self._admissions_by_id[admission.admission_id] = admission

    def _release_for_admission(self, admission: ReleaseAdmissionRecord) -> Any:
        table: Mapping[str, Any]
        if admission.subject_kind is ReleaseSubjectKind.SKILL_PACKAGE:
            table = self._skill_packages
        elif admission.subject_kind is ReleaseSubjectKind.PROMPT_COMPONENT:
            table = self._prompt_components
        elif admission.subject_kind is ReleaseSubjectKind.PROMPT_BUNDLE:
            table = self._prompt_bundles
        elif admission.subject_kind is ReleaseSubjectKind.EXECUTION_PROFILE:
            table = self._execution_profiles
        elif admission.subject_kind is ReleaseSubjectKind.RUNTIME_MODULE:
            table = self._modules
        elif admission.subject_kind is ReleaseSubjectKind.WORKFLOW:
            table = self._workflows
        else:  # pragma: no cover - exhaustive enum guard
            raise ValueError("unsupported admission subject kind")
        return self._get_exact(
            table,
            admission.release_ref,
            admission.release_sha256,
            admission.subject_kind.value,
        )

    @staticmethod
    def _stable_id(record: Any) -> str:
        for field_name in (
            "skill_package_id",
            "prompt_component_id",
            "prompt_bundle_id",
            "execution_profile_id",
            "module_id",
            "workflow_id",
        ):
            if hasattr(record, field_name):
                return getattr(record, field_name)
        raise TypeError("release record has no stable identity")

    @staticmethod
    def _validate_admission_transition(
        prior: ReleaseAdmissionState | None,
        target: ReleaseAdmissionState,
    ) -> None:
        allowed: dict[ReleaseAdmissionState | None, set[ReleaseAdmissionState]] = {
            None: {ReleaseAdmissionState.CANDIDATE},
            ReleaseAdmissionState.CANDIDATE: {
                ReleaseAdmissionState.SHADOW_EXECUTABLE,
                ReleaseAdmissionState.ACTIVE,
                ReleaseAdmissionState.RETIRED,
            },
            ReleaseAdmissionState.SHADOW_EXECUTABLE: {
                ReleaseAdmissionState.PRODUCTION_CANARY,
                ReleaseAdmissionState.ACTIVE,
                ReleaseAdmissionState.RETIRED,
            },
            ReleaseAdmissionState.PRODUCTION_CANARY: {
                ReleaseAdmissionState.ACTIVE,
                ReleaseAdmissionState.RETIRED,
            },
            ReleaseAdmissionState.ACTIVE: {
                ReleaseAdmissionState.SUPERSEDED,
                ReleaseAdmissionState.RETIRED,
            },
            ReleaseAdmissionState.SUPERSEDED: {ReleaseAdmissionState.RETIRED},
            ReleaseAdmissionState.RETIRED: set(),
        }
        if target not in allowed[prior]:
            prior_label = "unregistered" if prior is None else prior.value
            raise ValueError(
                f"illegal release admission transition: {prior_label} -> {target.value}"
            )

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
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistry",
    "RuntimeReleaseRegistrySnapshot",
]
