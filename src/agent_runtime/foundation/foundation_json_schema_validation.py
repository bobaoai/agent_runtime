"""Lazy JSON Schema validation shared by Runtime contract boundaries.

The base package may be imported without the validation extra. Operations that
create or register a new Schema Asset fail closed when validation is unavailable.
"""

from __future__ import annotations

from typing import Any, Mapping


def _draft_2020_12_validator() -> Any:
    """Return the optional validator or fail the validation operation."""

    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "JSON Schema validation requires the agent-runtime registry-validation extra"
        ) from exc
    return Draft202012Validator


def validate_json_schema_document(schema_document: Mapping[str, Any]) -> None:
    """Validate one new Draft 2020-12 Schema Asset document."""

    validator = _draft_2020_12_validator()
    try:
        validator.check_schema(dict(schema_document))
    except Exception as exc:
        raise ValueError("invalid Draft 2020-12 JSON Schema document") from exc


def validate_json_document_against_schema(
    document: Mapping[str, Any],
    schema_document: Mapping[str, Any],
) -> None:
    """Validate one exact JSON object against a registered schema document."""

    validator = _draft_2020_12_validator()
    try:
        validator.check_schema(dict(schema_document))
        validator(dict(schema_document)).validate(dict(document))
    except Exception as exc:
        raise ValueError("JSON document does not satisfy its registered schema") from exc


__all__ = [
    "validate_json_document_against_schema",
    "validate_json_schema_document",
]
