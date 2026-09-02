"""Code-owned capability case and runbook projection for Runtime verification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Any

from ..foundation.foundation_contract_validation import (
    validate_exact_record_instance,
    validate_exact_record_tuple,
    validate_id,
    validate_opaque_ref,
    validate_string_tuple,
)


RUNBOOK_SCHEMA_VERSION = "agent_capability_runbook_v1"
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")


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


__all__ = [
    "AgentCapabilityCommand",
    "AgentCapabilityEvidenceSourceKind",
    "AgentCapabilityTestCase",
    "render_agent_capability_runbook",
]
