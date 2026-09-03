"""Validate Engineering Reviewer output against Software Delivery meaning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from jsonschema import Draft202012Validator, ValidationError


ENGINEERING_REVIEW_RESULT_INVALID = "ENGINEERING_REVIEW_RESULT_INVALID"
OUTPUT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[5]
    / "09_soul/governance/skills/engineering-change-review/runtime_modules"
    / "engineering_change_reviewer/schemas/output.schema.json"
)


class EngineeringReviewOutputError(ValueError):
    """One invalid Engineering Reviewer result."""

    error_code = ENGINEERING_REVIEW_RESULT_INVALID


def validate_engineering_review_output(
    payload: Mapping[str, object],
    *,
    schema_path: Path = OUTPUT_SCHEMA_PATH,
) -> None:
    """Fail closed on schema or Software Delivery semantic inconsistency."""

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(dict(payload))
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise EngineeringReviewOutputError(str(exc)) from exc

    disposition = payload["engineering_layer_disposition"]
    readiness = payload["software_delivery_readiness"]
    expected = {
        "passed": "accepted",
        "non_pass": "changes_required",
        "blocked": "not_reproducible",
    }
    if expected.get(disposition) != readiness:
        raise EngineeringReviewOutputError(
            "engineering_layer_disposition and software_delivery_readiness disagree"
        )
    findings = payload["findings"]
    assert isinstance(findings, list)
    blocking_findings = [
        item
        for item in findings
        if isinstance(item, dict) and item.get("severity") in {"block", "fix"}
    ]
    if disposition == "passed" and blocking_findings:
        raise EngineeringReviewOutputError(
            "passed result cannot contain block or fix findings"
        )
    if disposition == "non_pass" and not blocking_findings:
        raise EngineeringReviewOutputError(
            "non_pass result requires at least one block or fix finding"
        )
    gates = payload["gate_results"]
    assert isinstance(gates, list)
    failed_gates = [
        item
        for item in gates
        if isinstance(item, dict) and item.get("disposition") in {"failed", "blocked"}
    ]
    if disposition == "passed" and failed_gates:
        raise EngineeringReviewOutputError(
            "passed result cannot contain failed or blocked gate results"
        )


__all__ = [
    "ENGINEERING_REVIEW_RESULT_INVALID",
    "EngineeringReviewOutputError",
    "OUTPUT_SCHEMA_PATH",
    "validate_engineering_review_output",
]
