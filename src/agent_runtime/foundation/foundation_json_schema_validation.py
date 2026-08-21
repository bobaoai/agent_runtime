"""Packaged JSON Schema validation shared by Runtime contract boundaries."""

from __future__ import annotations

from typing import Any, Mapping


def _draft_2020_12_validator() -> Any:
    """Return the packaged validator or report a broken installation."""

    try:
        from jsonschema import Draft202012Validator
    except ModuleNotFoundError as exc:  # pragma: no cover - environment specific
        raise RuntimeError(
            "agent-runtime-core requires its packaged jsonschema dependency"
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
    """Validate one exact JSON object against an admitted schema document."""

    validator = _draft_2020_12_validator()
    try:
        validator.check_schema(dict(schema_document))
        validator(dict(schema_document)).validate(dict(document))
    except Exception as exc:
        raise ValueError("JSON document does not satisfy its admitted schema") from exc


__all__ = [
    "validate_json_document_against_schema",
    "validate_json_schema_document",
]
