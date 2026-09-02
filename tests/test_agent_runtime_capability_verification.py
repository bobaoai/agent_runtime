from __future__ import annotations

from dataclasses import replace
import json

import pytest

from agent_runtime.testing import (
    AgentCapabilityCommand,
    AgentCapabilityEvidenceSourceKind,
    AgentCapabilityTestCase,
    render_agent_capability_runbook,
)


def _case(
    case_id: str = "module_registration_case",
) -> AgentCapabilityTestCase:
    return AgentCapabilityTestCase(
        case_id=case_id,
        capability_ids=("explicit_module_loading", "schema_closure"),
        owning_design_refs=(
            "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        ),
        evidence_contract_refs=(
            "runtime_module_export",
            "runtime_release_register",
        ),
        evidence_source_kind=(
            AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
        ),
        subject_requirements=(
            "exact Runtime source ref and hash",
            "exact Reviewer registration source",
        ),
        environment_prerequisites=("local Runtime checkout",),
        command=AgentCapabilityCommand(
            command_id="module_registration_case_command",
            argv=(
                "python",
                "-m",
                "pytest",
                "tests/test_agent_runtime_module_authoring.py",
                "-q",
            ),
            working_directory_ref="repo-root:agent-runtime",
        ),
        expected_result=("command exits zero", "registered Module resolves"),
        evidence_outputs=("pytest result", "registered release refs and hashes"),
        cleanup=("remove disposable in-memory Registry",),
        failure_routing=(
            "harness failure returns Agent Capability Verification owner",
            "Registry capability failure retains 01 owner",
        ),
        rerun_boundary=(
            "rerun when Runtime subject hash changes",
            "rerun when Reviewer registration closure changes",
        ),
    )


def test_case_validates_and_projects_every_contract_field() -> None:
    case = _case()

    case.validate()
    record = case.as_runbook_record()

    assert record == {
        "case_id": "module_registration_case",
        "capability_ids": ["explicit_module_loading", "schema_closure"],
        "owning_design_refs": [
            "designDoc/agent_runtime_01_module_contract_and_assembly.md"
        ],
        "evidence_contract_refs": [
            "runtime_module_export",
            "runtime_release_register",
        ],
        "evidence_source_kind": "executable_owner_case",
        "subject_requirements": [
            "exact Runtime source ref and hash",
            "exact Reviewer registration source",
        ],
        "environment_prerequisites": ["local Runtime checkout"],
        "command": {
            "command_id": "module_registration_case_command",
            "argv": [
                "python",
                "-m",
                "pytest",
                "tests/test_agent_runtime_module_authoring.py",
                "-q",
            ],
            "working_directory_ref": "repo-root:agent-runtime",
        },
        "expected_result": ["command exits zero", "registered Module resolves"],
        "evidence_outputs": [
            "pytest result",
            "registered release refs and hashes",
        ],
        "cleanup": ["remove disposable in-memory Registry"],
        "failure_routing": [
            "harness failure returns Agent Capability Verification owner",
            "Registry capability failure retains 01 owner",
        ],
        "rerun_boundary": [
            "rerun when Runtime subject hash changes",
            "rerun when Reviewer registration closure changes",
        ],
    }


@pytest.mark.parametrize(
    "field_name",
    (
        "capability_ids",
        "owning_design_refs",
        "evidence_contract_refs",
        "subject_requirements",
        "environment_prerequisites",
        "expected_result",
        "evidence_outputs",
        "cleanup",
        "failure_routing",
        "rerun_boundary",
    ),
)
def test_case_rejects_an_empty_required_field_group(field_name: str) -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        replace(_case(), **{field_name: ()}).validate()


def test_case_rejects_invalid_and_duplicate_identity() -> None:
    with pytest.raises(ValueError, match="invalid case_id"):
        replace(_case(), case_id="INVALID").validate()
    with pytest.raises(ValueError, match="capability_ids must be unique"):
        replace(
            _case(),
            capability_ids=("schema_closure", "schema_closure"),
        ).validate()
    with pytest.raises(ValueError, match="evidence_source_kind"):
        replace(
            _case(),
            evidence_source_kind="environment_gate",  # type: ignore[arg-type]
        ).validate()


def test_command_rejects_empty_argv() -> None:
    with pytest.raises(ValueError, match="argv must be a non-empty exact tuple"):
        replace(
            _case(),
            command=replace(_case().command, argv=()),
        ).validate()


def test_command_allows_repeated_arguments() -> None:
    command = replace(_case().command, argv=("python", "same", "same"))

    command.validate()


@pytest.mark.parametrize(
    "argument",
    (
        "--token=secret-value",
        "/Users/example/private-command",
        "~/private-command",
    ),
)
def test_command_rejects_inline_secret_and_host_private_path(
    argument: str,
) -> None:
    command = replace(_case().command, argv=("python", argument))

    with pytest.raises(ValueError, match="inline secret|host-private path"):
        command.validate()


def test_runbook_projection_is_stable_across_input_order() -> None:
    alpha = _case("alpha_case")
    beta = _case("beta_case")

    first = render_agent_capability_runbook((beta, alpha))
    second = render_agent_capability_runbook((alpha, beta))

    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == "agent_capability_runbook_v1"
    assert [case["case_id"] for case in payload["cases"]] == [
        "alpha_case",
        "beta_case",
    ]


def test_runbook_projection_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValueError, match="unique case_id"):
        render_agent_capability_runbook((_case(), _case()))
