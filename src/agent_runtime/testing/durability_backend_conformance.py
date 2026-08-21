"""Backend-neutral fixtures for durable Agent Runtime conformance tests.

The fixture contains synthetic identities and artifact references only.  Backend
adapters project it into their native workflow engine while local ledgers remain
the authority for customer content, entitlement, attempts, and artifacts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import ClassVar

from ..contracts.durability_execution_definition import (
    ArtifactRef,
    DurableExecutionBinding,
    EntitlementSnapshot,
    ExecutionEnvelope,
    ModuleVariantBinding,
    assert_ref_only_backend_payload,
)


CONFORMANCE_CONTRACT_VERSION = "agent_runtime_backend_conformance_v4"
ALPHA_PRIVATE_SENTINEL = "ALPHA_PRIVATE_SENTINEL"
BETA_PRIVATE_SENTINEL = "BETA_PRIVATE_SENTINEL"


class ConformanceWorkload(StrEnum):
    """Synthetic workload behaviors a durable backend must preserve."""

    REVISION_EVENT = "revision_event"
    CRASH_RECOVERY = "crash_recovery"


class ConformanceCursorState(StrEnum):
    """Opaque synthetic states used to prove backend graph portability."""

    INITIAL = "synthetic_state_a"
    ACTIVE = "synthetic_state_b"
    WAITING = "synthetic_state_c"
    TERMINAL_SUCCESS = "synthetic_terminal_success"
    TERMINAL_FAILURE = "synthetic_terminal_failure"


CONFORMANCE_STATE_TRANSITIONS = MappingProxyType({
    ConformanceCursorState.INITIAL: frozenset({
        ConformanceCursorState.ACTIVE,
        ConformanceCursorState.TERMINAL_FAILURE,
    }),
    ConformanceCursorState.ACTIVE: frozenset({
        ConformanceCursorState.WAITING,
        ConformanceCursorState.TERMINAL_FAILURE,
    }),
    ConformanceCursorState.WAITING: frozenset({
        ConformanceCursorState.ACTIVE,
        ConformanceCursorState.TERMINAL_SUCCESS,
        ConformanceCursorState.TERMINAL_FAILURE,
    }),
    ConformanceCursorState.TERMINAL_SUCCESS: frozenset(),
    ConformanceCursorState.TERMINAL_FAILURE: frozenset(),
})


class ConformanceModulePhase(StrEnum):
    """Backend-neutral control phases understood by the conformance driver."""

    PRE_EVENT = "pre_event"
    REVISION_EVENT = "revision_event"


class ConformanceEffectKind(StrEnum):
    """Whether a synthetic module exercises the local idempotency boundary."""

    NONE = "none"
    IDEMPOTENT = "idempotent"


_CONFORMANCE_ID = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ConformanceModuleBinding:
    """One projected Module Run with its own Variant and Attempt namespace."""

    workflow_execution_id: str
    module_key: str
    phase: ConformanceModulePhase
    module_run_id: str
    variant_id: str
    attempt_base_id: str
    execution_profile_ref: str
    effect_kind: ConformanceEffectKind = ConformanceEffectKind.NONE

    def validate(self, case: "ConformanceCase") -> None:
        """Enforce ModuleRun -> Variant -> Attempt identity ownership."""

        for label, value in (
            ("module_key", self.module_key),
            ("attempt_base_id", self.attempt_base_id),
        ):
            if not _CONFORMANCE_ID.fullmatch(value):
                raise ValueError(f"invalid conformance {label}: {value!r}")
        if self.workflow_execution_id != case.envelope.workflow_execution_id:
            raise ValueError("conformance module crossed workflow execution")
        ModuleVariantBinding(
            workflow_execution_id=self.workflow_execution_id,
            module_run_id=self.module_run_id,
            variant_id=self.variant_id,
            backend_id=case.binding.backend_id,
            execution_profile_ref=self.execution_profile_ref,
            entitlement_snapshot_hash=case.entitlement.snapshot_hash,
        ).validate()
        if case.binding.backend_id not in self.variant_id:
            raise ValueError("conformance module Variant does not identify its backend")

    def start_plan_payload(self) -> dict[str, str]:
        """Return workflow-start control data with no dispatch identity."""

        return {
            "module_key": self.module_key,
            "phase": self.phase.value,
            "effect_kind": self.effect_kind.value,
        }

    def dispatch_payload(self) -> dict[str, str]:
        """Return identities created when the workflow dispatches this module."""

        return {
            "workflow_execution_id": self.workflow_execution_id,
            "module_key": self.module_key,
            "phase": self.phase.value,
            "module_run_id": self.module_run_id,
            "variant_id": self.variant_id,
            "attempt_base_id": self.attempt_base_id,
            "execution_profile_ref": self.execution_profile_ref,
            "effect_kind": self.effect_kind.value,
        }


@dataclass(frozen=True)
class ConformanceCase:
    """One synthetic institution Cell and its frozen execution input."""

    binding: DurableExecutionBinding
    entitlement: EntitlementSnapshot
    envelope: ExecutionEnvelope
    modules: tuple[ConformanceModuleBinding, ...]
    private_sentinel: str
    workload: ConformanceWorkload
    requires_revision_event: bool
    requires_worker_failure: bool
    expected_side_effect_commits: ClassVar[int] = 1

    def validate(self) -> None:
        """Validate identity, scope, payload, and scenario consistency."""

        self.binding.validate()
        self.entitlement.validate()
        self.envelope.validate(self.binding)
        if self.entitlement.tenant_id != self.binding.tenant_id:
            raise ValueError("conformance entitlement does not match Cell tenant")
        if (
            self.envelope.entitlement_snapshot_id != self.entitlement.snapshot_id
            or self.envelope.entitlement_snapshot_hash
            != self.entitlement.snapshot_hash
        ):
            raise ValueError("conformance envelope does not bind entitlement")
        if not self.modules:
            raise ValueError("conformance case requires projected modules")
        for module in self.modules:
            module.validate(self)
        for label, values in (
            ("module_run_id", [module.module_run_id for module in self.modules]),
            ("variant_id", [module.variant_id for module in self.modules]),
            ("attempt_base_id", [module.attempt_base_id for module in self.modules]),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"conformance {label} must be unique per module")
        pre_event_modules = tuple(
            module for module in self.modules
            if module.phase is ConformanceModulePhase.PRE_EVENT
        )
        revision_event_modules = tuple(
            module for module in self.modules
            if module.phase is ConformanceModulePhase.REVISION_EVENT
        )
        idempotent_effects = tuple(
            module for module in self.modules
            if module.effect_kind is ConformanceEffectKind.IDEMPOTENT
        )
        if len(pre_event_modules) != 4 or len(revision_event_modules) != 1:
            raise ValueError(
                "conformance plan requires four pre-event modules and one "
                "revision-event module"
            )
        if len(idempotent_effects) != 1:
            raise ValueError("conformance plan requires one idempotent effect module")
        if self.workload is ConformanceWorkload.REVISION_EVENT:
            if not self.requires_revision_event or self.requires_worker_failure:
                raise ValueError("invalid revision-event workload requirements")
        elif self.workload is ConformanceWorkload.CRASH_RECOVERY:
            if self.requires_revision_event or not self.requires_worker_failure:
                raise ValueError("invalid crash-recovery workload requirements")
        payload = self._backend_payload()
        assert_ref_only_backend_payload(
            payload,
            forbidden_sentinels=(ALPHA_PRIVATE_SENTINEL, BETA_PRIVATE_SENTINEL),
        )

    def backend_payload(self) -> dict[str, object]:
        """Return the validated ref-only payload sent to a workflow backend."""

        self.validate()
        return self._backend_payload()

    def _backend_payload(self) -> dict[str, object]:
        """Build the payload after the caller has validated this case."""

        execution = self.envelope.to_backend_payload(self.binding)
        payload: dict[str, object] = {
            "execution": execution,
            "module_plan": [module.start_plan_payload() for module in self.modules],
        }
        assert_ref_only_backend_payload(
            payload,
            forbidden_sentinels=(ALPHA_PRIVATE_SENTINEL, BETA_PRIVATE_SENTINEL),
        )
        return payload


@dataclass(frozen=True)
class TwoCellConformanceFixture:
    """The same two-institution input used for every backend bake-off."""

    backend_id: str
    alpha: ConformanceCase
    beta: ConformanceCase

    def validate(self) -> None:
        """Assert binding isolation and complete execution-input validity."""

        if self.alpha.binding.backend_id != self.backend_id:
            raise ValueError("Alpha Cell uses the wrong backend")
        if self.beta.binding.backend_id != self.backend_id:
            raise ValueError("Beta Cell uses the wrong backend")
        for field in (
            "tenant_id",
            "cell_id",
            "backend_namespace",
            "backend_endpoint_ref",
            "backend_persistence_ref",
        ):
            if getattr(self.alpha.binding, field) == getattr(
                self.beta.binding,
                field,
            ):
                raise ValueError(f"conformance bindings share {field}")
        self.alpha.validate()
        self.beta.validate()

    def cases(self) -> tuple[ConformanceCase, ConformanceCase]:
        """Return cases in deterministic institution order."""

        self.validate()
        return (self.alpha, self.beta)


def _binding(
    *,
    tenant_id: str,
    cell_id: str,
    backend_id: str,
) -> DurableExecutionBinding:
    return DurableExecutionBinding(
        tenant_id=tenant_id,
        cell_id=cell_id,
        backend_id=backend_id,
        backend_namespace=f"ns_{cell_id}",
        backend_endpoint_ref=f"endpoint-ref:{backend_id}:{cell_id}",
        backend_persistence_ref=f"persistence-ref:{backend_id}:{cell_id}",
    )


def _case(
    *,
    binding: DurableExecutionBinding,
    principal_id: str,
    suffix: str,
    capabilities: tuple[str, ...],
    private_sentinel: str,
    workload: ConformanceWorkload,
) -> ConformanceCase:
    entitlement = EntitlementSnapshot.build(
        tenant_id=binding.tenant_id,
        snapshot_id=f"entitlement_{suffix}_v1",
        capabilities=capabilities,
    )
    artifact_digest = sha256(private_sentinel.encode("utf-8")).hexdigest()
    envelope = ExecutionEnvelope(
        tenant_id=binding.tenant_id,
        cell_id=binding.cell_id,
        principal_ref=f"principal-ref:{principal_id}",
        workflow_id="agent_runtime_conformance",
        workflow_contract_version=CONFORMANCE_CONTRACT_VERSION,
        workflow_execution_id=f"execution_{suffix}_001",
        entitlement_snapshot_id=entitlement.snapshot_id,
        entitlement_snapshot_hash=entitlement.snapshot_hash,
        artifacts=(
            ArtifactRef(
                artifact_id=f"synthetic_input_{suffix}",
                uri_ref=f"artifact-ref:{binding.cell_id}:synthetic_input",
                sha256=artifact_digest,
            ),
        ),
    )
    module_specs = (
        (
            "synthetic_module_a",
            ConformanceModulePhase.PRE_EVENT,
            ConformanceEffectKind.NONE,
        ),
        (
            "synthetic_module_b",
            ConformanceModulePhase.PRE_EVENT,
            ConformanceEffectKind.NONE,
        ),
        (
            "synthetic_module_c",
            ConformanceModulePhase.PRE_EVENT,
            ConformanceEffectKind.NONE,
        ),
        (
            "synthetic_module_d",
            ConformanceModulePhase.PRE_EVENT,
            ConformanceEffectKind.IDEMPOTENT,
        ),
        (
            "synthetic_module_e",
            ConformanceModulePhase.REVISION_EVENT,
            ConformanceEffectKind.NONE,
        ),
    )
    modules = tuple(
        build_conformance_module_dispatch(
            workflow_execution_id=envelope.workflow_execution_id,
            backend_id=binding.backend_id,
            module_key=module_key,
            phase=phase,
            effect_kind=effect_kind,
        )
        for module_key, phase, effect_kind in module_specs
    )
    case = ConformanceCase(
        binding=binding,
        entitlement=entitlement,
        envelope=envelope,
        modules=modules,
        private_sentinel=private_sentinel,
        workload=workload,
        requires_revision_event=workload is ConformanceWorkload.REVISION_EVENT,
        requires_worker_failure=workload is ConformanceWorkload.CRASH_RECOVERY,
    )
    case.validate()
    return case


def build_conformance_module_dispatch(
    *,
    workflow_execution_id: str,
    backend_id: str,
    module_key: str,
    phase: ConformanceModulePhase,
    effect_kind: ConformanceEffectKind,
) -> ConformanceModuleBinding:
    """Create deterministic Module, Variant, and Attempt identity at dispatch."""

    if not _CONFORMANCE_ID.fullmatch(workflow_execution_id):
        raise ValueError("invalid conformance workflow_execution_id")
    if not _CONFORMANCE_ID.fullmatch(backend_id):
        raise ValueError("invalid backend_id")
    if not _CONFORMANCE_ID.fullmatch(module_key):
        raise ValueError("invalid conformance module_key")
    return ConformanceModuleBinding(
        workflow_execution_id=workflow_execution_id,
        module_key=module_key,
        phase=phase,
        module_run_id=f"{module_key}_{workflow_execution_id}",
        variant_id=(
            f"variant_{backend_id}_{module_key}_{workflow_execution_id}"
        ),
        attempt_base_id=f"attempt_{module_key}_{workflow_execution_id}",
        execution_profile_ref="profile-ref:synthetic_worker",
        effect_kind=effect_kind,
    )


def build_two_cell_conformance_fixture(
    backend_id: str,
) -> TwoCellConformanceFixture:
    """Build the canonical two-institution fixture for one backend adapter."""

    alpha_binding = _binding(
        tenant_id="institution_alpha",
        cell_id="cell_alpha_us",
        backend_id=backend_id,
    )
    beta_binding = _binding(
        tenant_id="institution_beta",
        cell_id="cell_beta_cn",
        backend_id=backend_id,
    )
    fixture = TwoCellConformanceFixture(
        backend_id=backend_id,
        alpha=_case(
            binding=alpha_binding,
            principal_id="alpha_operator",
            suffix="alpha",
            capabilities=("fixture.capability_a",),
            private_sentinel=ALPHA_PRIVATE_SENTINEL,
            workload=ConformanceWorkload.REVISION_EVENT,
        ),
        beta=_case(
            binding=beta_binding,
            principal_id="beta_operator",
            suffix="beta",
            capabilities=("fixture.capability_a", "fixture.capability_b"),
            private_sentinel=BETA_PRIVATE_SENTINEL,
            workload=ConformanceWorkload.CRASH_RECOVERY,
        ),
    )
    fixture.validate()
    return fixture
