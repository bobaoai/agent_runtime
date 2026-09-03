from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[6]
MODULE_PATH = (
    REPO_ROOT
    / "09_soul/governance/t0/validation/artifact_contracts/skill_artifact_contract.py"
)
SPEC = importlib.util.spec_from_file_location(
    "skill_artifact_contract", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)


def _candidate(
    *,
    name: str = "example-skill",
    skill_class: str = "primary_agent_development",
    authoring_section: bool = False,
    author_self_check: bool = False,
) -> bytes:
    metadata = [f"  skill_class: {skill_class}"]
    if skill_class == "primary_agent_development":
        metadata.extend(
            (
                "  primary_agent_entry_role: authoring",
                "  primary_agent_entry_subject: system_change_plan_step",
                "  first_authority_ref: designDoc/the_example.md",
            )
        )
    body = (
        "---\n"
        f"name: {name}\n"
        "description: Complete one stable example task.\n"
        "metadata:\n"
        + "\n".join(metadata)
        + "\n---\n\n"
        "# Example Skill\n\n"
    )
    if authoring_section:
        body += (
            "## 0. AI-facing Authoring Rules\n\n"
            "Use the registered common authoring instruction.\n\n"
        )
    candidate = (
        body
        + "## 1. Task\n\nComplete the stable task.\n\n"
        "## 2. Reader Gain\n\nThe Agent can decide whether the task is complete.\n\n"
        "## 3. Entry and Exit\n\nEnter with the required request and return blockers to the owner.\n\n"
        "## 4. Execution Contract\n\nThe contract fixes inputs and completion.\n\n"
        "### 4.1 Inputs and Authority\n\nUse the reviewed request and owning Design.\n\n"
        "### 4.2 Output and Completion\n\nReturn one complete candidate.\n\n"
        "## 5. Boundaries\n\nDo not claim Runtime or release authority.\n\n"
        "## 6. Method\n\nChoose the shortest path that satisfies the result.\n"
    )
    if author_self_check:
        candidate += (
            "\n### 6.1 Author Self-Check\n\n"
            "Use the registered Author Self-Check and canonical checklist.\n"
        )
    return candidate.encode("utf-8")


@pytest.mark.parametrize(
    "skill_class",
    [
        "primary_agent_development",
        "product_agentic",
        "product_hybrid",
        "projection_only",
    ],
)
def test_each_skill_class_uses_one_code_owned_artifact_contract(
    skill_class: str,
) -> None:
    payload = _candidate(skill_class=skill_class)

    result = contract.validate_artifact(payload)

    assert result.subject_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.skill_id == "example-skill"
    assert result.skill_class == skill_class
    assert "skill_required_section_schema" in result.validator_ids


def test_primary_agent_skill_requires_entry_metadata() -> None:
    payload = _candidate().replace(
        b"  first_authority_ref: designDoc/the_example.md\n", b""
    )

    with pytest.raises(
        contract.SkillArtifactContractError,
        match="metadata is incomplete",
    ) as caught:
        contract.validate_artifact(payload)

    assert caught.value.code == "GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID"


def test_required_skill_section_rename_is_rejected() -> None:
    payload = _candidate().replace(b"## 2. Reader Gain", b"## 2. Benefits", 1)

    with pytest.raises(
        contract.SkillArtifactContractError,
        match="required sections",
    ):
        contract.validate_artifact(payload)


def test_skill_section_number_gap_is_rejected() -> None:
    payload = _candidate().replace(b"## 5. Boundaries", b"## 7. Boundaries", 1)

    with pytest.raises(
        contract.SkillArtifactContractError,
        match="numbering must be continuous",
    ):
        contract.validate_artifact(payload)


def test_declared_authoring_common_resource_requires_and_accepts_section_zero() -> None:
    payload = _candidate(authoring_section=True)

    result = contract.validate_artifact(
        payload,
        embedded_resource_ids=("soul:bestpractice_ai_facing_writing",),
    )

    assert result.headings[0] == "AI-facing Authoring Rules"
    assert result.headings[1:] == (
        "Task",
        "Reader Gain",
        "Entry and Exit",
        "Execution Contract",
        "Boundaries",
        "Method",
    )


def test_undeclared_authoring_section_zero_is_rejected() -> None:
    with pytest.raises(
        contract.SkillArtifactContractError,
        match="continuous from one",
    ):
        contract.validate_artifact(_candidate(authoring_section=True))


def test_declared_authoring_common_resource_without_section_zero_is_rejected() -> None:
    with pytest.raises(
        contract.SkillArtifactContractError,
        match="continuous from zero",
    ):
        contract.validate_artifact(
            _candidate(),
            embedded_resource_ids=("soul:bestpractice_ai_facing_writing",),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(
            b"## 0. AI-facing Authoring Rules",
            b"## 0. Shared Writing Rules",
            1,
        ),
        lambda payload: payload.replace(
            b"Use the registered common authoring instruction.",
            b"",
            1,
        ),
        lambda payload: payload.replace(
            b"## 1. Task",
            b"## 0. AI-facing Authoring Rules\n\nDuplicate.\n\n## 1. Task",
            1,
        ),
        lambda payload: payload.replace(
            b"## 0. AI-facing Authoring Rules\n\n"
            b"Use the registered common authoring instruction.\n\n",
            b"",
            1,
        ).replace(
            b"## 2. Reader Gain",
            b"## 2. Reader Gain\n\n"
            b"## 0. AI-facing Authoring Rules\n\nLate.\n\n",
            1,
        ),
    ],
)
def test_declared_authoring_section_zero_must_be_exact_and_first(mutation) -> None:
    with pytest.raises(contract.SkillArtifactContractError):
        contract.validate_artifact(
            mutation(_candidate(authoring_section=True)),
            embedded_resource_ids=("soul:bestpractice_ai_facing_writing",),
        )


def test_execution_contract_subsections_are_required() -> None:
    payload = _candidate().replace(
        b"### 4.2 Output and Completion", b"### 4.2 Completion", 1
    )

    with pytest.raises(
        contract.SkillArtifactContractError,
        match="must occur exactly once",
    ):
        contract.validate_artifact(payload)


def test_skill_reviewer_prompt_contains_exact_projected_checklist() -> None:
    prompt = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "skill_candidate_reviewer/prompt.md"
    ).read_bytes()
    resource_id = "t0:skill_candidate_review_checklist"
    start = f"<!-- embedded-resource:{resource_id}:start -->\n".encode()
    end = f"<!-- embedded-resource:{resource_id}:end -->".encode()

    projected = prompt.split(start, 1)[1].split(end, 1)[0]

    assert projected == contract.render_reviewer_checklist()


def test_skill_authoring_contains_exact_projected_author_self_check_resources() -> None:
    authoring = (
        REPO_ROOT / "09_soul/governance/skills/the-skill-authoring/SKILL.md"
    ).read_bytes()

    def projected(resource_id: str) -> bytes:
        start = f"<!-- embedded-resource:{resource_id}:start -->\n".encode()
        end = f"<!-- embedded-resource:{resource_id}:end -->".encode()
        return authoring.split(start, 1)[1].split(end, 1)[0]

    assert projected("t0:skill_author_self_check") == (
        contract.render_author_self_check_instruction()
    )
    assert projected("t0:skill_candidate_review_checklist") == (
        contract.render_reviewer_checklist()
    )


def test_declared_author_self_check_requires_one_method_section_and_checklist() -> None:
    payload = _candidate(name="the-skill-authoring", author_self_check=True)

    result = contract.validate_artifact(
        payload,
        embedded_resource_ids=(
            "t0:skill_author_self_check",
            "t0:skill_candidate_review_checklist",
        ),
    )

    assert "skill_author_self_check_contract" in result.validator_ids


@pytest.mark.parametrize(
    ("payload", "resource_ids", "message"),
    [
        (
            _candidate(name="the-skill-authoring"),
            (
                "t0:skill_author_self_check",
                "t0:skill_candidate_review_checklist",
            ),
            "must occur exactly once",
        ),
        (
            _candidate(name="the-skill-authoring", author_self_check=True),
            ("t0:skill_author_self_check",),
            "does not match the registered Skill binding",
        ),
        (
            _candidate(name="the-skill-authoring", author_self_check=True),
            ("t0:skill_candidate_review_checklist",),
            "declarations must be complete",
        ),
        (
            _candidate(name="the-skill-authoring", author_self_check=True),
            (
                "t0:skill_author_self_check",
                "t0:skill_candidate_review_checklist",
                "t0:design_contract_review_checklist",
            ),
            "does not match the registered Skill binding",
        ),
    ],
)
def test_author_self_check_contract_rejects_incomplete_declarations(
    payload: bytes,
    resource_ids: tuple[str, ...],
    message: str,
) -> None:
    with pytest.raises(contract.SkillArtifactContractError, match=message):
        contract.validate_artifact(payload, embedded_resource_ids=resource_ids)


@pytest.mark.parametrize(
    ("skill_id", "wrong_checklist"),
    [
        ("the-system-change", "t0:design_contract_review_checklist"),
        ("the-design-authoring", "t0:skill_candidate_review_checklist"),
        ("the-skill-authoring", "t0:reviewer_prompt_review_checklist"),
        ("the-review-authoring", "t0:system_change_plan_review_checklist"),
    ],
)
def test_author_self_check_rejects_wrong_allowlisted_checklist(
    skill_id: str,
    wrong_checklist: str,
) -> None:
    with pytest.raises(
        contract.SkillArtifactContractError,
        match="does not match the registered Skill binding",
    ):
        contract.validate_artifact(
            _candidate(name=skill_id, author_self_check=True),
            embedded_resource_ids=(
                "t0:skill_author_self_check",
                wrong_checklist,
            ),
        )


def test_author_self_check_must_be_structurally_inside_method() -> None:
    payload = _candidate(
        name="the-skill-authoring",
        author_self_check=True,
    )
    self_check = (
        b"\n### 6.1 Author Self-Check\n\n"
        b"Use the registered Author Self-Check and canonical checklist.\n"
    )
    misplaced = payload.replace(self_check, b"").replace(
        b"## 6. Method",
        self_check + b"\n## 6. Method",
        1,
    )

    with pytest.raises(
        contract.SkillArtifactContractError,
        match="must be inside the Method section",
    ):
        contract.validate_artifact(
            misplaced,
            embedded_resource_ids=(
                "t0:skill_author_self_check",
                "t0:skill_candidate_review_checklist",
            ),
        )


def _self_check_rows(*check_ids: str) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "check_id": check_id,
            "exact_evidence": f"candidate evidence for {check_id}",
            "local_result": "locally closed",
            "unresolved_finding": None,
        }
        for check_id in check_ids
    )


def test_invocation_local_author_self_check_accepts_exact_closed_rows() -> None:
    candidate = b"exact candidate\n"
    check_ids = ("check_a", "check_b")

    result = contract.validate_author_self_check_submission(
        candidate,
        declared_candidate_sha256=hashlib.sha256(candidate).hexdigest(),
        required_check_ids=check_ids,
        rows=_self_check_rows(*check_ids),
    )

    assert result.candidate_sha256 == hashlib.sha256(candidate).hexdigest()
    assert result.required_check_ids == check_ids
    assert result.row_count == 2


@pytest.mark.parametrize(
    ("declared_sha256", "check_ids", "rows", "error_code"),
    [
        (
            "0" * 64,
            ("check_a",),
            _self_check_rows("check_a"),
            contract.AUTHOR_SELF_CHECK_CANDIDATE_MISMATCH,
        ),
        (
            hashlib.sha256(b"exact candidate\n").hexdigest(),
            ("check_a", "check_b"),
            _self_check_rows("check_b", "check_a"),
            contract.AUTHOR_SELF_CHECK_COVERAGE_INVALID,
        ),
        (
            hashlib.sha256(b"exact candidate\n").hexdigest(),
            ("check_a",),
            (
                {
                    "check_id": "check_a",
                    "exact_evidence": "",
                    "local_result": "locally closed",
                    "unresolved_finding": None,
                },
            ),
            contract.AUTHOR_SELF_CHECK_EVIDENCE_MISSING,
        ),
        (
            hashlib.sha256(b"exact candidate\n").hexdigest(),
            ("check_a",),
            (
                {
                    "check_id": "check_a",
                    "exact_evidence": "candidate evidence",
                    "local_result": "finding remains",
                    "unresolved_finding": "fix the candidate",
                },
            ),
            contract.AUTHOR_SELF_CHECK_UNRESOLVED,
        ),
    ],
)
def test_invocation_local_author_self_check_rejects_invalid_submission(
    declared_sha256: str,
    check_ids: tuple[str, ...],
    rows: tuple[dict[str, object], ...],
    error_code: str,
) -> None:
    with pytest.raises(contract.AuthorSelfCheckValidationError) as caught:
        contract.validate_author_self_check_submission(
            b"exact candidate\n",
            declared_candidate_sha256=declared_sha256,
            required_check_ids=check_ids,
            rows=rows,
        )

    assert caught.value.code == error_code
