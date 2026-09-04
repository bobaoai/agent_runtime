"""Code-owned capability cases and runbook for Runtime conformance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import shlex
from typing import Any, Callable

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_exact_record_instance,
    validate_exact_record_tuple,
    validate_id,
    validate_opaque_ref,
    validate_sha256,
    validate_string_tuple,
)


RUNBOOK_SCHEMA_VERSION = "agent_capability_runbook_v1"
AGENT_CAPABILITY_TEST_FAILED = "AGENT_CAPABILITY_TEST_FAILED"
AGENT_CAPABILITY_ENVIRONMENT_UNAVAILABLE = (
    "AGENT_CAPABILITY_ENVIRONMENT_UNAVAILABLE"
)
AGENT_CAPABILITY_COVERAGE_INCOMPLETE = "AGENT_CAPABILITY_COVERAGE_INCOMPLETE"
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")


class AgentCapabilityVerificationError(ValueError):
    """Stable T2 11 verification failure returned to the accountable caller."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


class AgentCapabilityEvidenceSourceKind(StrEnum):
    """The fixed source class for one capability verification result."""

    EXECUTABLE_OWNER_CASE = "executable_owner_case"
    ENVIRONMENT_GATE = "environment_gate"
    REFERENCED_PEER_RESULT = "referenced_peer_result"


def _validate_bounded_text(label: str, value: Any) -> None:
    if type(value) is not str or not value.strip() or len(value) > 1024:
        raise ValueError(f"{label} must be non-empty bounded text")


def _validate_command_argument(label: str, value: Any) -> None:
    _validate_bounded_text(label, value)
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"{label} must not contain an inline secret")
    if value.startswith(("/", "~/")):
        raise ValueError(f"{label} must not contain a host-private path")


@dataclass(frozen=True)
class AgentCapabilityCommand:
    """One shell-free command definition referenced by a case and its runbook."""

    command_id: str
    argv: tuple[str, ...]
    working_directory_ref: str

    def validate(self) -> None:
        """Reject invalid identity, empty argv, or an unbounded working directory."""

        validate_id("command_id", self.command_id)
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("argv must be a non-empty exact tuple")
        for argument in self.argv:
            _validate_command_argument("argv", argument)
        validate_opaque_ref(
            "working_directory_ref",
            self.working_directory_ref,
        )

    def as_record(self) -> dict[str, object]:
        """Return the validated JSON-ready command without executing it."""

        self.validate()
        return {
            "command_id": self.command_id,
            "argv": list(self.argv),
            "working_directory_ref": self.working_directory_ref,
        }


@dataclass(frozen=True)
class AgentCapabilityTestCase:
    """One immutable capability case shared by a future runner and runbook."""

    case_id: str
    capability_ids: tuple[str, ...]
    owning_design_refs: tuple[str, ...]
    evidence_contract_refs: tuple[str, ...]
    evidence_source_kind: AgentCapabilityEvidenceSourceKind
    subject_requirements: tuple[str, ...]
    environment_prerequisites: tuple[str, ...]
    command: AgentCapabilityCommand
    expected_result: tuple[str, ...]
    evidence_outputs: tuple[str, ...]
    cleanup: tuple[str, ...]
    failure_routing: tuple[str, ...]
    rerun_boundary: tuple[str, ...]

    def validate(self) -> None:
        """Validate the complete T2 case closure before runner admission."""

        validate_id("case_id", self.case_id)
        for label, values in (
            ("capability_ids", self.capability_ids),
            ("owning_design_refs", self.owning_design_refs),
            ("evidence_contract_refs", self.evidence_contract_refs),
            ("subject_requirements", self.subject_requirements),
            ("environment_prerequisites", self.environment_prerequisites),
            ("expected_result", self.expected_result),
            ("evidence_outputs", self.evidence_outputs),
            ("cleanup", self.cleanup),
            ("failure_routing", self.failure_routing),
            ("rerun_boundary", self.rerun_boundary),
        ):
            validate_string_tuple(
                label,
                values,
                item_validator=_validate_bounded_text,
                require_non_empty=True,
            )
        if type(self.evidence_source_kind) is not AgentCapabilityEvidenceSourceKind:
            raise ValueError(
                "evidence_source_kind must be an exact "
                "AgentCapabilityEvidenceSourceKind"
            )
        validate_exact_record_instance(
            "command",
            self.command,
            expected_type=AgentCapabilityCommand,
        )
        self.command.validate()

    def as_runbook_record(self) -> dict[str, object]:
        """Project every case field into one deterministic runbook record."""

        self.validate()
        return {
            "case_id": self.case_id,
            "capability_ids": list(self.capability_ids),
            "owning_design_refs": list(self.owning_design_refs),
            "evidence_contract_refs": list(self.evidence_contract_refs),
            "evidence_source_kind": self.evidence_source_kind.value,
            "subject_requirements": list(self.subject_requirements),
            "environment_prerequisites": list(
                self.environment_prerequisites
            ),
            "command": self.command.as_record(),
            "expected_result": list(self.expected_result),
            "evidence_outputs": list(self.evidence_outputs),
            "cleanup": list(self.cleanup),
            "failure_routing": list(self.failure_routing),
            "rerun_boundary": list(self.rerun_boundary),
        }


def render_agent_capability_runbook(
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render validated cases as byte-stable, operator-readable JSON."""

    validate_exact_record_tuple(
        "cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    payload = {
        "schema_version": RUNBOOK_SCHEMA_VERSION,
        "cases": [
            case.as_runbook_record()
            for case in sorted(cases, key=lambda item: item.case_id)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


class AgentCapabilityVerificationScope(StrEnum):
    """The requested evidence boundary for one verification run."""

    FOCUSED = "focused"
    COMPLETE = "complete"


class AgentCapabilityResultState(StrEnum):
    """The only result states for one selected capability case."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class AgentCapabilityTransportCapability(StrEnum):
    """Optional transport support fact carried by a passed transport case."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AgentCapabilityInventoryEntry:
    """One stable T2 11 capability and its required evidence cases."""

    capability_id: str
    owning_design_refs: tuple[str, ...]
    evidence_source_kinds: tuple[AgentCapabilityEvidenceSourceKind, ...]
    case_ids: tuple[str, ...]

    def validate(self) -> None:
        validate_id("capability_id", self.capability_id)
        validate_string_tuple(
            "owning_design_refs",
            self.owning_design_refs,
            item_validator=_validate_bounded_text,
            require_non_empty=True,
        )
        if (
            type(self.evidence_source_kinds) is not tuple
            or not self.evidence_source_kinds
        ):
            raise ValueError("evidence_source_kinds must be a non-empty exact tuple")
        if any(
            type(kind) is not AgentCapabilityEvidenceSourceKind
            for kind in self.evidence_source_kinds
        ):
            raise ValueError("invalid evidence_source_kinds")
        if len(set(self.evidence_source_kinds)) != len(self.evidence_source_kinds):
            raise ValueError("evidence_source_kinds must be unique")
        validate_string_tuple(
            "case_ids",
            self.case_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )


_REGISTRY_DESIGN = "designDoc/agent_runtime_01_module_contract_and_assembly.md"
_INVOCATION_DESIGN = "designDoc/agent_runtime_08_agent_execution_adapter_contract.md"
_EXECUTION_DESIGN = "designDoc/agent_runtime_00_execution_charter.md"
_DURABILITY_DESIGN = "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md"
_LEDGER_DESIGN = "designDoc/agent_runtime_00_execution_charter.md"
_INSPECTION_DESIGN = (
    "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
)
_RELEASE_DESIGN = "designDoc/agent_runtime_05_delivery_roadmap.md"

_MODULE_ASSEMBLY_CAPABILITIES = (
    "explicit_module_loading",
    "path_free_release_identity",
    "schema_closure",
    "prompt_closure",
    "policy_closure",
    "profile_independence",
    "generic_profile_compatibility",
    "registered_module_transport",
)
_INVOCATION_CAPABILITIES = (
    "inline_semantic_input",
    "structured_output",
    "tool_free_execution",
    "runtime_hosted_self_test",
    "authorized_gateway_read",
    "attempt_workspace",
    "context_isolation",
    "network_enforcement",
    "provider_failure_normalization",
)
_EXECUTION_CAPABILITIES = (
    "module_run",
    "multiple_variants",
    "evaluation_and_selection",
    "retry_budget",
    "idempotent_replay",
    "operation_boundary_enforcement",
    "cancellation",
)
_WORKFLOW_CAPABILITIES = (
    "graph_authoring",
    "sequential_and_branch_routing",
    "parallel_fan_out_and_join",
    "revision_loop",
    "wait_and_external_event",
    "crash_recovery",
    "portable_workflow_registration",
    "per_registry_execution_binding",
)
_EVIDENCE_CAPABILITIES = (
    "attempt_and_workflow_ledger",
    "usage_truth",
    "release_inspection",
    "execution_inspection",
)
_PERSISTENT_CAPABILITIES = (
    "persistent_registry",
    "persistent_ledger",
    "persistent_inspection",
)
_LIVE_TRANSPORT_CAPABILITIES = (
    "live_provider_adapter",
    "registered_module_transport",
)

_CASE_IDS_BY_CAPABILITY = {
    **{
        capability_id: ("module_release_assembly_case",)
        for capability_id in _MODULE_ASSEMBLY_CAPABILITIES
    },
    **{
        capability_id: ("agent_invocation_case",)
        for capability_id in _INVOCATION_CAPABILITIES
    },
    **{
        capability_id: ("module_execution_case",)
        for capability_id in _EXECUTION_CAPABILITIES
    },
    **{
        capability_id: ("workflow_graph_case",)
        for capability_id in _WORKFLOW_CAPABILITIES
    },
    **{
        capability_id: ("ledger_inspection_case",)
        for capability_id in _EVIDENCE_CAPABILITIES
    },
    **{
        capability_id: ("persistent_runtime_case",)
        for capability_id in _PERSISTENT_CAPABILITIES
    },
    "durable_backend": ("durable_backend_case",),
    "live_provider_adapter": ("live_module_transport_case",),
    "registered_module_transport": (
        "module_release_assembly_case",
        "live_module_transport_case",
    ),
    "public_package": ("public_package_case",),
}


def _inventory_entry(
    capability_id: str,
    owners: tuple[str, ...],
    kinds: tuple[AgentCapabilityEvidenceSourceKind, ...] = (
        AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
    ),
    case_ids: tuple[str, ...] | None = None,
) -> AgentCapabilityInventoryEntry:
    entry = AgentCapabilityInventoryEntry(
        capability_id=capability_id,
        owning_design_refs=owners,
        evidence_source_kinds=kinds,
        case_ids=case_ids or _CASE_IDS_BY_CAPABILITY[capability_id],
    )
    entry.validate()
    return entry


def required_agent_capability_inventory() -> tuple[AgentCapabilityInventoryEntry, ...]:
    """Return the complete, code-owned T2 11 capability inventory."""

    registry_ids = (
        "explicit_module_loading",
        "path_free_release_identity",
        "schema_closure",
        "prompt_closure",
        "policy_closure",
        "profile_independence",
        "generic_profile_compatibility",
        "portable_workflow_registration",
        "per_registry_execution_binding",
    )
    invocation_ids = (
        "inline_semantic_input",
        "structured_output",
        "tool_free_execution",
        "runtime_hosted_self_test",
        "authorized_gateway_read",
        "attempt_workspace",
        "context_isolation",
        "network_enforcement",
        "provider_failure_normalization",
    )
    execution_ids = (
        "module_run",
        "multiple_variants",
        "evaluation_and_selection",
        "retry_budget",
        "idempotent_replay",
        "operation_boundary_enforcement",
        "cancellation",
    )
    workflow_execution_ids = (
        "graph_authoring",
        "sequential_and_branch_routing",
        "parallel_fan_out_and_join",
        "revision_loop",
    )
    durability_ids = ("wait_and_external_event", "crash_recovery")
    evidence_owners = {
        "attempt_and_workflow_ledger": (_LEDGER_DESIGN,),
        "usage_truth": (_LEDGER_DESIGN,),
        "release_inspection": (_INSPECTION_DESIGN, _REGISTRY_DESIGN),
        "execution_inspection": (_INSPECTION_DESIGN, _LEDGER_DESIGN),
    }
    rows = [
        *(_inventory_entry(item, (_REGISTRY_DESIGN,)) for item in registry_ids),
        *(_inventory_entry(item, (_INVOCATION_DESIGN,)) for item in invocation_ids),
        *(_inventory_entry(item, (_EXECUTION_DESIGN,)) for item in execution_ids),
        *(
            _inventory_entry(item, (_EXECUTION_DESIGN,))
            for item in workflow_execution_ids
        ),
        *(_inventory_entry(item, (_DURABILITY_DESIGN,)) for item in durability_ids),
        *(_inventory_entry(item, owners) for item, owners in evidence_owners.items()),
    ]
    environment_specs = {
        "persistent_registry": (_REGISTRY_DESIGN,),
        "persistent_ledger": (_LEDGER_DESIGN,),
        "persistent_inspection": (_INSPECTION_DESIGN, _REGISTRY_DESIGN, _LEDGER_DESIGN),
        "durable_backend": (_DURABILITY_DESIGN,),
        "live_provider_adapter": (_INVOCATION_DESIGN,),
    }
    rows.extend(
        _inventory_entry(
            capability_id,
            owners,
            (AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,),
        )
        for capability_id, owners in environment_specs.items()
    )
    rows.append(
        _inventory_entry(
            "registered_module_transport",
            (_INVOCATION_DESIGN, _REGISTRY_DESIGN),
            (
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            ),
            _CASE_IDS_BY_CAPABILITY["registered_module_transport"],
        )
    )
    rows.append(
        _inventory_entry(
            "public_package",
            (_RELEASE_DESIGN,),
            (AgentCapabilityEvidenceSourceKind.REFERENCED_PEER_RESULT,),
        )
    )
    result = tuple(sorted(rows, key=lambda item: item.capability_id))
    if len(result) != 42 or len({item.capability_id for item in result}) != 42:
        raise ValueError(
            "required Agent capability inventory must contain 42 unique rows"
        )
    return result


def _capability_case(
    *,
    case_id: str,
    capability_ids: tuple[str, ...],
    evidence_source_kind: AgentCapabilityEvidenceSourceKind,
    test_paths: tuple[str, ...],
    environment_prerequisites: tuple[str, ...],
    owning_design_refs: tuple[str, ...],
) -> AgentCapabilityTestCase:
    case = AgentCapabilityTestCase(
        case_id=case_id,
        capability_ids=capability_ids,
        owning_design_refs=owning_design_refs,
        evidence_contract_refs=owning_design_refs,
        evidence_source_kind=evidence_source_kind,
        subject_requirements=(
            "exact Runtime subject ref and hash",
            "exact verification suite ref and hash",
            "exact dependency closure ref and hash",
        ),
        environment_prerequisites=environment_prerequisites,
        command=AgentCapabilityCommand(
            command_id=f"{case_id}_command",
            argv=("python", "-m", "pytest", "-q", *test_paths),
            working_directory_ref="repo-root:agent-runtime",
        ),
        expected_result=(
            "command exits zero or records the declared not_run environment state",
            "every capability observation binds the exact Runtime subject",
        ),
        evidence_outputs=(
            "bounded test result",
            "capability evidence refs and hashes",
        ),
        cleanup=("remove only resources created by this test case",),
        failure_routing=(
            "harness failure returns Agent Capability Verification owner",
            "Runtime capability failure retains its owning Design and error code",
        ),
        rerun_boundary=(
            "rerun when Runtime subject or suite hash changes",
            "rerun when dependency or environment binding changes",
        ),
    )
    case.validate()
    return case


def required_agent_capability_cases() -> tuple[AgentCapabilityTestCase, ...]:
    """Return the nine canonical executable cases behind the T2 11 inventory."""

    cases = (
        _capability_case(
            case_id="module_release_assembly_case",
            capability_ids=_MODULE_ASSEMBLY_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_module_authoring.py",
                "tests/test_agent_runtime_registry_candidate_compilation.py",
            ),
            environment_prerequisites=("local Runtime checkout",),
            owning_design_refs=(_REGISTRY_DESIGN,),
        ),
        _capability_case(
            case_id="agent_invocation_case",
            capability_ids=_INVOCATION_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_native_structured_output.py",
                "tests/test_agent_runtime_public_adapter_contracts.py",
                "tests/test_agent_runtime_attempt_workspace.py",
            ),
            environment_prerequisites=("local Runtime checkout",),
            owning_design_refs=(_INVOCATION_DESIGN,),
        ),
        _capability_case(
            case_id="module_execution_case",
            capability_ids=_EXECUTION_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_execution_records.py",
                "tests/test_agent_runtime_execution_authorization.py",
                "tests/test_agent_runtime_module_evaluation.py",
            ),
            environment_prerequisites=("local Runtime checkout",),
            owning_design_refs=(_EXECUTION_DESIGN,),
        ),
        _capability_case(
            case_id="workflow_graph_case",
            capability_ids=_WORKFLOW_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_workflow_authoring.py",
                "tests/test_agent_runtime_parallel_workflow.py",
                "tests/test_agent_runtime_product_host_execution_api.py",
            ),
            environment_prerequisites=("local Runtime checkout",),
            owning_design_refs=(_REGISTRY_DESIGN, _EXECUTION_DESIGN),
        ),
        _capability_case(
            case_id="ledger_inspection_case",
            capability_ids=_EVIDENCE_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_execution_inspection.py",
                "tests/test_agent_runtime_release_inspection.py",
                "tests/test_workflow_execution_ledger_recording.py",
            ),
            environment_prerequisites=("local Runtime checkout",),
            owning_design_refs=(_LEDGER_DESIGN, _INSPECTION_DESIGN),
        ),
        _capability_case(
            case_id="persistent_runtime_case",
            capability_ids=_PERSISTENT_CAPABILITIES,
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=(
                "tests/test_agent_runtime_postgres_release_store.py",
                "tests/test_agent_runtime_postgres_execution_ledger.py",
            ),
            environment_prerequisites=(
                "explicit host-provided PostgreSQL test namespace",
            ),
            owning_design_refs=(_REGISTRY_DESIGN, _LEDGER_DESIGN, _INSPECTION_DESIGN),
        ),
        _capability_case(
            case_id="durable_backend_case",
            capability_ids=("durable_backend",),
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=("tests/test_agent_runtime_temporal_integration.py",),
            environment_prerequisites=(
                "explicit host-provided durable backend test binding",
            ),
            owning_design_refs=(_DURABILITY_DESIGN,),
        ),
        _capability_case(
            case_id="live_module_transport_case",
            capability_ids=_LIVE_TRANSPORT_CAPABILITIES,
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=("tests/test_agent_runtime_native_structured_output.py",),
            environment_prerequisites=(
                "exact registered Module Profile Variant and provider binding",
            ),
            owning_design_refs=(_INVOCATION_DESIGN, _REGISTRY_DESIGN),
        ),
        _capability_case(
            case_id="public_package_case",
            capability_ids=("public_package",),
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.REFERENCED_PEER_RESULT
            ),
            test_paths=(
                "tests/test_agent_runtime_packaging_boundary.py",
                "tests/test_agent_runtime_conformance_package.py",
            ),
            environment_prerequisites=("clean isolated package environment",),
            owning_design_refs=(_RELEASE_DESIGN,),
        ),
    )
    validate_exact_record_tuple(
        "required capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    return cases


def _validated_capability_case_map(
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    cases: tuple[AgentCapabilityTestCase, ...],
) -> dict[str, AgentCapabilityTestCase]:
    validate_exact_record_tuple(
        "capability inventory",
        inventory,
        expected_type=AgentCapabilityInventoryEntry,
        item_validator=lambda entry: entry.validate(),
        unique_key=lambda entry: entry.capability_id,
        unique_key_label="capability_id",
        require_non_empty=True,
    )
    validate_exact_record_tuple(
        "capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    cases_by_id = {case.case_id: case for case in cases}
    for entry in inventory:
        for case_id in entry.case_ids:
            case = cases_by_id.get(case_id)
            if case is None or entry.capability_id not in case.capability_ids:
                raise ValueError(
                    "capability inventory and case mapping must close exactly"
                )
    return cases_by_id


def _markdown_items(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


def render_agent_capability_catalog_markdown(
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render the stable public capability catalog from code-owned truth."""

    cases_by_id = _validated_capability_case_map(inventory, cases)
    capability_count = len(inventory)
    rows = []
    for case_id in sorted(cases_by_id):
        case = cases_by_id[case_id]
        capabilities = ", ".join(
            f"`{capability_id}`" for capability_id in case.capability_ids
        )
        rows.append(
            f"| `{case.case_id}` | `{case.evidence_source_kind.value}` | "
            f"{capabilities} |"
        )
    return (
        "# Agent Runtime capabilities\n\n"
        "Agent Runtime is a lightweight, provider-neutral assembler for "
        "versioned agentic workflows. It registers immutable Modules and "
        "Workflows, binds execution Profiles, runs model and tool adapters, "
        "coordinates durable progress, and records inspectable execution "
        "facts. Business roles, prompts, authorization policy, credentials, "
        "and domain data stay with the host.\n\n"
        f"The code-owned verification inventory contains {capability_count} "
        f"capabilities grouped into {len(cases_by_id)} executable cases. "
        "The [operator runbook](agent_runtime_capability_runbook.md) is "
        "generated from the same case definitions.\n\n"
        "| Case | Evidence source | Capabilities |\n"
        "| --- | --- | --- |\n"
        + "\n".join(rows)
        + "\n\n"
        "## Verification semantics\n\n"
        "- A focused run proves only its selected cases.\n"
        "- Environment-dependent cases report `not_run` when their explicit "
        "PostgreSQL, Temporal, or provider binding is unavailable.\n"
        "- A rejected negative case may pass when rejection is its declared "
        "expected result.\n"
        "- Provider success, process exit, and subject approval are separate "
        "facts.\n"
        "- Complete verification requires every required case and peer result "
        "to bind the same Runtime subject and dependency closure.\n\n"
        "Current pass, failure, and environment observations are generated "
        "verification evidence. They are intentionally absent from this stable "
        "capability catalog.\n"
    )


def render_agent_capability_runbook_markdown(
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render the operator runbook from the executable case definitions."""

    validate_exact_record_tuple(
        "capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    sections = [
        "# Agent Runtime capability runbook\n\n"
        "Each section below is generated from the same code-owned "
        "`AgentCapabilityTestCase` that drives verification. Run commands from "
        "the Agent Runtime repository root. Supply only the explicit "
        "environment binding named by the case.\n"
    ]
    for case in sorted(cases, key=lambda item: item.case_id):
        capabilities = ", ".join(
            f"`{capability_id}`" for capability_id in case.capability_ids
        )
        sections.append(
            f"\n## `{case.case_id}`\n\n"
            f"Evidence source: `{case.evidence_source_kind.value}`\n\n"
            f"Capabilities: {capabilities}\n\n"
            "Prerequisites:\n\n"
            f"{_markdown_items(case.environment_prerequisites)}\n\n"
            "Command:\n\n"
            f"```sh\n{shlex.join(case.command.argv)}\n```\n\n"
            "Expected result:\n\n"
            f"{_markdown_items(case.expected_result)}\n\n"
            "Evidence:\n\n"
            f"{_markdown_items(case.evidence_outputs)}\n\n"
            "Cleanup:\n\n"
            f"{_markdown_items(case.cleanup)}\n\n"
            "Failure routing:\n\n"
            f"{_markdown_items(case.failure_routing)}\n\n"
            "Rerun when:\n\n"
            f"{_markdown_items(case.rerun_boundary)}\n"
        )
    return "".join(sections)


@dataclass(frozen=True)
class AgentCapabilityVerificationRequest:
    """Exact subject, suite, scope and selection for one verification run."""

    request_id: str
    subject_ref: str
    subject_sha256: str
    suite_ref: str
    suite_sha256: str
    dependency_closure_ref: str
    dependency_closure_sha256: str
    scope: AgentCapabilityVerificationScope
    selected_case_ids: tuple[str, ...]
    standalone_conformance_ref: str | None = None
    standalone_conformance_sha256: str | None = None

    def validate(self) -> None:
        validate_id("request_id", self.request_id)
        for label, value in (
            ("subject_ref", self.subject_ref),
            ("suite_ref", self.suite_ref),
            ("dependency_closure_ref", self.dependency_closure_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("subject_sha256", self.subject_sha256),
            ("suite_sha256", self.suite_sha256),
            ("dependency_closure_sha256", self.dependency_closure_sha256),
        ):
            validate_sha256(label, value)
        if type(self.scope) is not AgentCapabilityVerificationScope:
            raise ValueError("scope must be an exact AgentCapabilityVerificationScope")
        validate_string_tuple(
            "selected_case_ids",
            self.selected_case_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )
        if (self.standalone_conformance_ref is None) != (
            self.standalone_conformance_sha256 is None
        ):
            raise ValueError("standalone conformance ref/hash must be paired")
        if self.standalone_conformance_ref is not None:
            validate_opaque_ref(
                "standalone_conformance_ref",
                self.standalone_conformance_ref,
            )
            validate_sha256(
                "standalone_conformance_sha256",
                self.standalone_conformance_sha256,
            )


@dataclass(frozen=True)
class AgentCapabilityCaseResult:
    """One exact case observation without implied sibling success."""

    case_id: str
    capability_ids: tuple[str, ...]
    state: AgentCapabilityResultState
    evidence_refs: tuple[str, ...]
    failure_code: str | None = None
    failure_owner_ref: str | None = None
    transport_capability: AgentCapabilityTransportCapability | None = None

    def validate(self) -> None:
        validate_id("case_id", self.case_id)
        validate_string_tuple(
            "capability_ids",
            self.capability_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )
        if type(self.state) is not AgentCapabilityResultState:
            raise ValueError("state must be an exact AgentCapabilityResultState")
        validate_string_tuple(
            "evidence_refs",
            self.evidence_refs,
            item_validator=_validate_bounded_text,
            require_non_empty=self.state is not AgentCapabilityResultState.NOT_RUN,
        )
        if (self.failure_code is None) != (self.failure_owner_ref is None):
            raise ValueError("failure code and owner must be paired")
        if (
            self.state is AgentCapabilityResultState.PASSED
            and self.failure_code is not None
        ):
            raise ValueError("passed case forbids failure")
        if (
            self.state is not AgentCapabilityResultState.PASSED
            and self.failure_code is None
        ):
            raise ValueError("failed or not_run case requires failure")
        if self.failure_owner_ref is not None:
            _validate_bounded_text("failure_owner_ref", self.failure_owner_ref)
            _validate_bounded_text("failure_code", self.failure_code)
        if self.transport_capability is not None:
            if self.state is not AgentCapabilityResultState.PASSED:
                raise ValueError("transport capability requires a passed case")
            if (
                type(self.transport_capability)
                is not AgentCapabilityTransportCapability
            ):
                raise ValueError("invalid transport_capability")


@dataclass(frozen=True)
class AgentCapabilityVerificationResult:
    """Completed focused or complete verification without false aggregation."""

    request_id: str
    subject_ref: str
    subject_sha256: str
    suite_ref: str
    suite_sha256: str
    dependency_closure_ref: str
    dependency_closure_sha256: str
    scope: AgentCapabilityVerificationScope
    case_results: tuple[AgentCapabilityCaseResult, ...]
    scope_completed: bool
    full_runtime_completed: bool

    def validate(self) -> None:
        validate_id("request_id", self.request_id)
        for label, value in (
            ("subject_ref", self.subject_ref),
            ("suite_ref", self.suite_ref),
            ("dependency_closure_ref", self.dependency_closure_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("subject_sha256", self.subject_sha256),
            ("suite_sha256", self.suite_sha256),
            ("dependency_closure_sha256", self.dependency_closure_sha256),
        ):
            validate_sha256(label, value)
        if type(self.scope) is not AgentCapabilityVerificationScope:
            raise ValueError("scope must be an exact AgentCapabilityVerificationScope")
        validate_exact_record_tuple(
            "case_results",
            self.case_results,
            expected_type=AgentCapabilityCaseResult,
            item_validator=lambda result: result.validate(),
            unique_key=lambda result: result.case_id,
            unique_key_label="case_id",
            require_non_empty=True,
        )
        validate_bool("scope_completed", self.scope_completed)
        validate_bool("full_runtime_completed", self.full_runtime_completed)
        if self.full_runtime_completed and (
            self.scope is not AgentCapabilityVerificationScope.COMPLETE
            or not self.scope_completed
        ):
            raise ValueError(
                "full Runtime completion requires completed complete scope"
            )


CaseExecutor = Callable[[AgentCapabilityTestCase], AgentCapabilityCaseResult]


def run_agent_capability_verification(
    request: AgentCapabilityVerificationRequest,
    *,
    cases: tuple[AgentCapabilityTestCase, ...],
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    execute_case: CaseExecutor,
) -> AgentCapabilityVerificationResult:
    """Run exactly the selected cases and compute completion without propagation."""

    request.validate()
    validate_exact_record_tuple(
        "cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    validate_exact_record_tuple(
        "inventory",
        inventory,
        expected_type=AgentCapabilityInventoryEntry,
        item_validator=lambda entry: entry.validate(),
        unique_key=lambda entry: entry.capability_id,
        unique_key_label="capability_id",
        require_non_empty=True,
    )
    cases_by_id = {case.case_id: case for case in cases}
    selected = tuple(request.selected_case_ids)
    if any(case_id not in cases_by_id for case_id in selected):
        raise AgentCapabilityVerificationError(
            AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
            "unknown selected case",
        )
    required_case_ids = {
        case_id for entry in inventory for case_id in entry.case_ids
    }
    if request.scope is AgentCapabilityVerificationScope.COMPLETE:
        if {entry.capability_id for entry in inventory} != {
            entry.capability_id for entry in required_agent_capability_inventory()
        }:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "complete inventory required",
            )
        if set(selected) != required_case_ids:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "complete case set required",
            )
        if request.standalone_conformance_ref is None:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "package result required",
            )
    results = []
    for case_id in selected:
        case = cases_by_id[case_id]
        result = execute_case(case)
        validate_exact_record_instance(
            "case result",
            result,
            expected_type=AgentCapabilityCaseResult,
        )
        result.validate()
        if result.case_id != case.case_id or set(result.capability_ids) != set(
            case.capability_ids
        ):
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_TEST_FAILED,
                "case result identity mismatch",
            )
        results.append(result)
    result_tuple = tuple(results)
    scope_completed = all(
        item.state is AgentCapabilityResultState.PASSED for item in result_tuple
    )
    # The current plan intentionally excludes the canonical Example and live
    # registered Module transport evidence. A complete run can execute its selected
    # cases, but this implementation must not claim full Runtime completion.
    full_runtime_completed = False
    verification_result = AgentCapabilityVerificationResult(
        request_id=request.request_id,
        subject_ref=request.subject_ref,
        subject_sha256=request.subject_sha256,
        suite_ref=request.suite_ref,
        suite_sha256=request.suite_sha256,
        dependency_closure_ref=request.dependency_closure_ref,
        dependency_closure_sha256=request.dependency_closure_sha256,
        scope=request.scope,
        case_results=result_tuple,
        scope_completed=scope_completed,
        full_runtime_completed=full_runtime_completed,
    )
    verification_result.validate()
    return verification_result


__all__ = [
    "AgentCapabilityCommand",
    "AgentCapabilityEvidenceSourceKind",
    "AgentCapabilityTestCase",
    "render_agent_capability_runbook",
]
