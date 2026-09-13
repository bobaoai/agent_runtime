"""Runtime-owned Reviewer defaults; no host or provider SDK configuration."""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.registry_release_definition import ReviewerDefaults
from .registry_release_compilation import (
    BehaviorPolicyReleaseCandidate, EvaluationPolicyReleaseCandidate,
    RetryPolicyReleaseCandidate,
    compile_behavior_policy_release, compile_evaluation_policy_release,
    compile_retry_policy_release,
)


_REVIEW_OUTPUT_FIELDS = {"verdict", "check_results", "findings", "safe_next_step"}
_REVIEW_CHECK_FIELDS = {"check_id", "disposition", "assessment", "finding_ids"}
_REVIEW_FINDING_FIELDS = {"finding_id", "severity", "evidence", "requirement", "impact", "accountable_owner_ref", "required_change"}
_REVIEW_EVIDENCE_FIELDS = {"source_ref", "locator", "observation"}
_REVIEW_SCHEMA_SCOPE_KEYS = {"$id", "$schema", "$defs", "$anchor", "$dynamicAnchor"}
_REVIEW_SCHEMA_ANNOTATION_KEYS = {"title", "description", "$comment"}


def _review_schema_object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Reviewer output schema {label} must be an object")
    return value


def _review_schema_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"Reviewer output schema {label} fields differ from the common format")


def _review_schema_required(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    required = value.get("required")
    if (
        not isinstance(required, list)
        or not all(isinstance(name, str) for name in required)
        or set(required) != expected
    ):
        raise ValueError(f"Reviewer output schema {label} required fields differ from the common format")


def _review_schema_local_scope(value: Mapping[str, Any], label: str) -> None:
    if _REVIEW_SCHEMA_SCOPE_KEYS.intersection(value):
        raise ValueError(f"Reviewer output schema {label} cannot change local reference scope")


def _review_schema_closed_object(value: Mapping[str, Any], label: str) -> None:
    if (
        value.get("type") != "object"
        or value.get("additionalProperties") is not False
        or "patternProperties" in value
    ):
        raise ValueError(f"Reviewer output schema {label} must be one closed object")


def _review_schema_ref(value: Any, expected_ref: str, label: str) -> None:
    node = _review_schema_object(value, label)
    if node.get("$ref") != expected_ref or set(node) - ({"$ref"} | _REVIEW_SCHEMA_ANNOTATION_KEYS):
        raise ValueError(f"Reviewer output schema {label} must use the direct local ref {expected_ref}")


def _review_schema_string(value: Any, label: str) -> Mapping[str, Any]:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    if node.get("type") != "string" or node.get("minLength") != 1:
        raise ValueError(f"Reviewer output schema {label} must be a non-empty string")
    return node


def _review_schema_enum(value: Any, expected: tuple[str, ...], label: str) -> None:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    members = node.get("enum")
    if (
        node.get("type") != "string"
        or "$ref" in node
        or not isinstance(members, list)
        or not all(isinstance(member, str) for member in members)
        or set(members) != set(expected)
    ):
        raise ValueError(f"Reviewer output schema {label} enum differs from the common format")


def _review_schema_array(value: Any, item_ref: str | None, label: str) -> Mapping[str, Any]:
    node = _review_schema_object(value, label)
    _review_schema_local_scope(node, label)
    if node.get("type") != "array" or "prefixItems" in node:
        raise ValueError(f"Reviewer output schema {label} must use one homogeneous array contract")
    items = _review_schema_object(node.get("items"), f"{label}.items")
    _review_schema_local_scope(items, f"{label}.items")
    if item_ref is not None:
        _review_schema_ref(items, item_ref, f"{label}.items")
    return node


def _validate_reviewer_output_schema(schema_document: Mapping[str, object]) -> None:
    schema = _review_schema_object(schema_document, "root")
    _review_schema_closed_object(schema, "root")
    properties = _review_schema_object(schema.get("properties"), "properties")
    _review_schema_fields(properties, _REVIEW_OUTPUT_FIELDS, "top-level")
    _review_schema_required(schema, _REVIEW_OUTPUT_FIELDS, "top-level")
    _review_schema_enum(properties["verdict"], ("passed", "non_pass", "blocked"), "verdict")
    _review_schema_string(properties["safe_next_step"], "safe_next_step")
    _review_schema_array(properties["check_results"], "#/$defs/check_result", "check_results")
    _review_schema_array(properties["findings"], "#/$defs/finding", "findings")

    definitions = _review_schema_object(schema.get("$defs"), "$defs")
    check = _review_schema_object(definitions.get("check_result"), "$defs.check_result")
    _review_schema_local_scope(check, "$defs.check_result")
    _review_schema_closed_object(check, "check_result")
    check_properties = _review_schema_object(check.get("properties"), "check_result.properties")
    _review_schema_fields(check_properties, _REVIEW_CHECK_FIELDS, "check_result")
    _review_schema_required(check, _REVIEW_CHECK_FIELDS, "check_result")
    _review_schema_string(check_properties["check_id"], "check_result.check_id")
    _review_schema_enum(check_properties["disposition"], ("passed", "finding", "not_applicable", "not_run"), "check_result.disposition")
    _review_schema_string(check_properties["assessment"], "check_result.assessment")
    finding_ids = _review_schema_array(check_properties["finding_ids"], None, "check_result.finding_ids")
    if finding_ids.get("uniqueItems") is not True:
        raise ValueError("Reviewer output schema finding_ids must contain unique values")
    finding_id_item = _review_schema_object(
        finding_ids["items"], "check_result.finding_ids.items"
    )
    if finding_id_item.get("type") != "string":
        raise ValueError("Reviewer output schema finding_ids items must be strings")

    finding = _review_schema_object(definitions.get("finding"), "$defs.finding")
    _review_schema_local_scope(finding, "$defs.finding")
    _review_schema_closed_object(finding, "finding")
    finding_properties = _review_schema_object(finding.get("properties"), "finding.properties")
    finding_required = finding.get("required")
    if (
        not _REVIEW_FINDING_FIELDS.issubset(finding_properties)
        or not isinstance(finding_required, list)
        or not all(isinstance(name, str) for name in finding_required)
        or not _REVIEW_FINDING_FIELDS.issubset(set(finding_required))
    ):
        raise ValueError("Reviewer output schema finding common fields are incomplete")
    _review_schema_string(finding_properties["finding_id"], "finding.finding_id")
    _review_schema_enum(finding_properties["severity"], ("block", "fix", "note"), "finding.severity")
    _review_schema_ref(finding_properties["evidence"], "#/$defs/evidence", "finding.evidence")
    for name in ("requirement", "impact", "accountable_owner_ref", "required_change"):
        _review_schema_string(finding_properties[name], f"finding.{name}")

    evidence = _review_schema_object(definitions.get("evidence"), "$defs.evidence")
    _review_schema_local_scope(evidence, "$defs.evidence")
    _review_schema_closed_object(evidence, "evidence")
    evidence_properties = _review_schema_object(evidence.get("properties"), "evidence.properties")
    _review_schema_fields(evidence_properties, _REVIEW_EVIDENCE_FIELDS, "evidence")
    _review_schema_required(evidence, _REVIEW_EVIDENCE_FIELDS, "evidence")
    for name in sorted(_REVIEW_EVIDENCE_FIELDS):
        _review_schema_string(evidence_properties[name], f"evidence.{name}")


def reviewer_policy_defaults():
    """Return Runtime's exact policies, including the retained candidate policy."""
    return {
        "behavior_policies": (
            compile_behavior_policy_release(BehaviorPolicyReleaseCandidate(
                "workflow_execution_isolated", "v1", "workflow_execution_isolated")),
        ),
        "evaluation_policies": (
            compile_evaluation_policy_release(EvaluationPolicyReleaseCandidate(
                "reviewer_entry", "v1", "none")),
            compile_evaluation_policy_release(EvaluationPolicyReleaseCandidate(
                "module_candidate", "v1", "module_candidate")),
        ),
        "retry_policies": (
            compile_retry_policy_release(RetryPolicyReleaseCandidate(
                "bounded_candidate", "v1", ReviewerDefaults().max_attempts)),
        ),
    }


def resolve_reviewer_policy(family, reference, supplied, registry):
    """Respect an explicit source dependency; resolve omissions from Runtime."""
    if supplied is not None:
        supplied.validate()
        if reference is not None and reference != supplied.release_ref:
            raise ValueError(f"source {family} reference differs from supplied policy")
        return supplied
    if reference is not None and registry is not None:
        for record in getattr(registry.snapshot(), family):
            if record.release_ref == reference:
                return record
    defaults = reviewer_policy_defaults()[family]
    if reference is None:
        return defaults[0]
    for record in defaults:
        if record.release_ref == reference:
            return record
    raise ValueError(f"Unresolved exact Reviewer policy: {reference}")
