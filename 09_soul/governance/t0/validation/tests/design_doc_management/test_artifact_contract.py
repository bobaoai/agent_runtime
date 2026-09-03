from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[6]
MODULE_PATH = (
    REPO_ROOT
    / "09_soul/governance/t0/validation/artifact_contracts/design_artifact_contract.py"
)
SPEC = importlib.util.spec_from_file_location(
    "design_artifact_contract", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
contract = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = contract
SPEC.loader.exec_module(contract)


def _candidate(
    layer: str,
    *,
    extra_heading: str | None = None,
    extra_headings: tuple[str, ...] = (),
    outside_heading: str | None = None,
) -> bytes:
    definition = contract.load_contract()["layers"][layer]
    headings = list(definition["protected_headings"])
    extensions = list(extra_headings)
    if extra_heading is not None:
        extensions.append(extra_heading)
    insertion_index = headings.index(definition["extension_slot"]["before"])
    headings[insertion_index:insertion_index] = extensions
    if outside_heading is not None:
        headings.insert(1, outside_heading)
    rows = ["---", f"layer: {definition['layer_value']}", "---", "", "# Candidate", ""]
    for index, heading in enumerate(headings):
        rows.append(f"## {index}. {heading}")
        rows.append("")
        if heading == "Intent Capsule":
            rows.extend(("```yaml", f"layer: {definition['layer_value']}", "status: candidate", "```"))
        else:
            rows.append(f"Complete meaning for {heading}.")
        rows.append("")
    return "\n".join(rows).encode("utf-8")


def _candidate_with_review_completion(
    layer: str,
    *,
    trailing_extension: bool = False,
) -> bytes:
    extensions = (
        ("审查与完成", "Later Owner Detail")
        if trailing_extension
        else ("审查与完成",)
    )
    payload = _candidate(layer, extra_headings=extensions)
    text = payload.decode("utf-8")
    heading_line = next(
        line
        for line in text.splitlines()
        if line.endswith(". 审查与完成")
    )
    parent_index = heading_line.split(".", 1)[0].removeprefix("## ")
    subsection_body = "\n\n".join(
        f"### {parent_index}.{index} {heading}\n\nComplete {heading}."
        for index, heading in enumerate(
            ("确定性检查", "语义审查", "表达审查", "完成条件"),
            start=1,
        )
    )
    return text.replace(
        "Complete meaning for 审查与完成.", subsection_body, 1
    ).encode("utf-8")


def _replace_once(payload: bytes, old: str, new: str) -> bytes:
    text = payload.decode("utf-8")
    assert text.count(old) == 1
    return text.replace(old, new, 1).encode("utf-8")


@pytest.mark.parametrize("layer", ["charter", "t0", "t1", "t2"])
def test_each_design_layer_uses_one_code_owned_required_section_contract(
    layer: str,
) -> None:
    payload = _candidate(layer, extra_heading="Owner-specific Detail")

    result = contract.validate_artifact(payload, layer=layer)

    assert result.subject_sha256 == hashlib.sha256(payload).hexdigest()
    assert result.layer == layer
    assert "design_required_section_schema" in result.validator_ids
    assert "design_optional_review_completion_section" not in result.validator_ids


@pytest.mark.parametrize("layer", ["charter", "t0", "t1", "t2"])
def test_missing_protected_heading_is_rejected(layer: str) -> None:
    payload = _candidate(layer)
    heading = contract.load_contract()["layers"][layer]["protected_headings"][2]
    payload = _replace_once(
        payload,
        f"## 2. {heading}",
        f"## 2. Renamed {heading}",
    )

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="must occur exactly once",
    ) as caught:
        contract.validate_artifact(payload, layer=layer)

    assert caught.value.code == "DESIGN_REPRESENTATION_INCOMPLETE"


def test_protected_heading_order_is_rejected() -> None:
    payload = _candidate("t0")
    payload = _replace_once(payload, "## 2. User Intent", "## 2. Reader Gain")
    payload = _replace_once(payload, "## 3. Reader Gain", "## 3. User Intent")

    with pytest.raises(contract.DesignArtifactContractError, match="out of order"):
        contract.validate_artifact(payload, layer="t0")


def test_empty_protected_section_is_rejected() -> None:
    payload = _candidate("t2")
    payload = _replace_once(
        payload,
        "## 2. User Intent\n\nComplete meaning for User Intent.",
        "## 2. User Intent\n",
    )

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="empty or placeholder-only",
    ):
        contract.validate_artifact(payload, layer="t2")


def test_owner_extension_outside_registered_slot_is_rejected() -> None:
    payload = _candidate("t1", outside_heading="Unexpected Detail")

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="outside its layer-owned slot",
    ):
        contract.validate_artifact(payload, layer="t1")


@pytest.mark.parametrize("layer", ["charter", "t0", "t1", "t2"])
def test_optional_review_completion_section_is_validated_when_present(
    layer: str,
) -> None:
    payload = _candidate_with_review_completion(layer)

    result = contract.validate_artifact(payload, layer=layer)

    assert "design_optional_review_completion_section" in result.validator_ids


def test_optional_review_completion_section_must_be_last_in_extension_slot() -> None:
    payload = _candidate_with_review_completion(
        "t0", trailing_extension=True
    )

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="must be last",
    ):
        contract.validate_artifact(payload, layer="t0")


def test_optional_review_completion_subsections_are_required_and_ordered() -> None:
    payload = _candidate_with_review_completion("t1")
    text = payload.decode("utf-8")
    payload = text.replace("语义审查", "Missing Semantic Review", 1).encode(
        "utf-8"
    )

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="missing, renamed, or out of order",
    ):
        contract.validate_artifact(payload, layer="t1")


def test_optional_review_completion_subsections_use_parent_section_number() -> None:
    payload = _candidate_with_review_completion("t2")
    text = payload.decode("utf-8")
    heading_line = next(
        line for line in text.splitlines() if line.endswith(". 审查与完成")
    )
    parent_index = heading_line.split(".", 1)[0].removeprefix("## ")
    payload = text.replace(
        f"### {parent_index}.1 确定性检查",
        "### 99.1 确定性检查",
        1,
    ).encode("utf-8")

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="missing, renamed, or out of order",
    ):
        contract.validate_artifact(payload, layer="t2")


def test_optional_review_completion_subsections_reject_reordered_headings() -> None:
    payload = _candidate_with_review_completion("charter")
    text = payload.decode("utf-8")
    first = "### 6.1 确定性检查"
    second = "### 6.2 语义审查"
    assert first in text and second in text
    payload = text.replace(first, "### 6.1 语义审查", 1).replace(
        second, "### 6.2 确定性检查", 1
    ).encode("utf-8")

    with pytest.raises(
        contract.DesignArtifactContractError,
        match="missing, renamed, or out of order",
    ):
        contract.validate_artifact(payload, layer="charter")


def test_design_reviewer_prompt_contains_exact_projected_checklist() -> None:
    prompt = (
        REPO_ROOT
        / "09_soul/governance/skills/the-design-authoring/runtime_modules/"
        "design_contract_reviewer/prompt.md"
    ).read_bytes()
    resource_id = "t0:design_contract_review_checklist"
    start = f"<!-- embedded-resource:{resource_id}:start -->\n".encode()
    end = f"<!-- embedded-resource:{resource_id}:end -->".encode()

    projected = prompt.split(start, 1)[1].split(end, 1)[0]

    assert projected == contract.render_reviewer_checklist()


def test_design_authoring_skill_does_not_duplicate_reviewer_checklist() -> None:
    authoring = (
        REPO_ROOT / "09_soul/governance/skills/the-design-authoring/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "skill-specific-review-checklist:design_contract_reviewer" not in authoring
    assert "## 7. Design Reviewer Checklist Source" not in authoring
