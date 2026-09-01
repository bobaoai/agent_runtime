from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from agent_runtime import ModuleReviewer


REPO_ROOT = Path(__file__).resolve().parents[1]

REVIEWER_REGISTRATIONS = (
    (
        "the-design-authoring",
        "design_contract_reviewer",
        "designDoc/the_design_doc_management.md",
        ("model_execute",),
    ),
    (
        "the-system-change",
        "system_change_plan_reviewer",
        "designDoc/the_system_change_governance.md",
        ("model_execute",),
    ),
    (
        "the-skill-authoring",
        "skill_candidate_reviewer",
        "designDoc/the_skill_management.md",
        ("model_execute",),
    ),
    (
        "the-review-authoring",
        "reviewer_reviewer",
        "designDoc/the_review_contract.md",
        ("model_execute",),
    ),
    (
        "engineering-change-review",
        "engineering_change_reviewer",
        "designDoc/the_software_delivery.md",
        (
            "model_execute",
            "repository_read",
            "repository_search",
            "sandbox_command_execute",
        ),
    ),
)


@pytest.mark.parametrize(
    ("skill_id", "module_id", "owner_contract_path", "operation_ids"),
    REVIEWER_REGISTRATIONS,
)
def test_current_governance_reviewer_registration_loads_through_runtime(
    skill_id: str,
    module_id: str,
    owner_contract_path: str,
    operation_ids: tuple[str, ...],
) -> None:
    reviewer = ModuleReviewer.from_registration(
        REPO_ROOT,
        skill_id=skill_id,
        module_id=module_id,
    )

    assert reviewer.source.skill_id == skill_id
    assert reviewer.source.module_id == module_id
    owner_content = (REPO_ROOT / owner_contract_path).read_text(encoding="utf-8")
    assert reviewer.source.owner_contract_ref == (
        "owner-contract-sha256:"
        + hashlib.sha256(owner_content.encode("utf-8")).hexdigest()
    )
    assert reviewer.source.declared_operation_ids == operation_ids
    Draft202012Validator.check_schema(
        json.loads(reviewer.source.input_schema_document)
    )
    Draft202012Validator.check_schema(
        json.loads(reviewer.source.output_schema_document)
    )
