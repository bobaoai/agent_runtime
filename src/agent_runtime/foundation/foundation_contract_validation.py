"""Responsibility-neutral validation primitives for Agent Runtime records.

Public DTO validation is also a serialization boundary.  These helpers reject
Python coercions that can compare or stringify successfully while producing a
different JSON type, including ``bool`` as ``int`` and non-finite floats.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any, Pattern, TypeVar


_USD_AMOUNT = re.compile(r"^(0|[1-9][0-9]*)\.[0-9]{3}$")


ID_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{2,159}$")
SNAKE_CASE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OPAQUE_REF_PATTERN = re.compile(r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$")
SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")


_RecordT = TypeVar("_RecordT")


def validate_exact_record_instance(
    label: str,
    value: Any,
    *,
    expected_type: type[_RecordT],
) -> None:
    """Require one exact public record type without accepting subclasses."""

    if type(value) is not expected_type:
        raise ValueError(f"{label} must be an exact {expected_type.__name__} record")


def validate_pattern_string(
    label: str,
    value: Any,
    *,
    pattern: Pattern[str],
    requirement: str,
) -> None:
    """Require a real string that matches one complete public-field pattern."""

    if type(value) is not str or not pattern.fullmatch(value):
        raise ValueError(f"invalid {label}: {requirement}")


def validate_id(label: str, value: Any) -> None:
    """Require one bounded Runtime identifier without implicit string coercion."""

    validate_pattern_string(
        label,
        value,
        pattern=ID_PATTERN,
        requirement=f"bounded identifier required, got {value!r}",
    )


def validate_snake_case_name(label: str, value: Any) -> None:
    """Require one platform-owned canonical name in ``snake_case``."""

    validate_pattern_string(
        label,
        value,
        pattern=SNAKE_CASE_NAME_PATTERN,
        requirement=f"snake_case name required, got {value!r}",
    )


def validate_sha256(label: str, value: Any) -> None:
    """Require one lowercase hexadecimal SHA-256 string."""

    validate_pattern_string(
        label,
        value,
        pattern=SHA256_PATTERN,
        requirement=f"lowercase SHA-256 required, got {value!r}",
    )


def validate_token(
    label: str,
    value: Any,
    *,
    pattern: Pattern[str],
) -> None:
    """Require one bounded token using the caller's declared grammar."""

    validate_pattern_string(
        label,
        value,
        pattern=pattern,
        requirement=f"bounded token required, got {value!r}",
    )


def validate_opaque_ref(label: str, value: Any) -> None:
    """Require a bounded, secret-free, non-inline opaque reference string."""

    if type(value) is not str or not OPAQUE_REF_PATTERN.fullmatch(value):
        raise ValueError(f"invalid {label}: bounded opaque ref required")
    if value.startswith("data:") or any(
        marker in value.lower() for marker in SECRET_MARKERS
    ):
        raise ValueError(f"invalid {label}: bounded opaque ref required")


def validate_bool(label: str, value: Any) -> None:
    """Require an exact JSON boolean."""

    if type(value) is not bool:
        raise ValueError(f"{label} must be boolean")


def validate_enum_string(
    label: str,
    value: Any,
    *,
    allowed: Collection[str],
) -> None:
    """Require an exact JSON string from one closed public enumeration."""

    if type(value) is not str or value not in allowed:
        raise ValueError(f"invalid {label}")


def validate_int(
    label: str,
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> None:
    """Require an exact JSON integer and optional inclusive bounds."""

    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be at most {maximum}")


_CANONICAL_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


def validate_utc_timestamp(label: str, value: Any) -> None:
    """Require one canonical ISO 8601 UTC timestamp: ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``.

    ``fromisoformat`` alone tolerates space separators, compact forms, and
    missing seconds; a non-canonical instant committed verbatim into immutable
    records breaks lexicographic ordering and replay string equality, so the
    canonical shape is enforced before parsing.
    """

    if type(value) is not str or not value.endswith("Z"):
        raise ValueError(f"invalid {label}: UTC timestamp ending in Z required")
    if not _CANONICAL_UTC_TIMESTAMP.fullmatch(value):
        raise ValueError(f"invalid {label}: UTC timestamp required")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"invalid {label}: UTC timestamp required") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError(f"invalid {label}: UTC timestamp required")


def parse_utc_timestamp(label: str, value: Any) -> datetime:
    """Validate one canonical UTC timestamp and return its aware instant."""

    validate_utc_timestamp(label, value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


def format_utc_timestamp(instant: datetime) -> str:
    """Format one aware UTC instant into the canonical ``Z``-suffixed string."""

    if instant.tzinfo is None or instant.utcoffset().total_seconds() != 0:
        raise ValueError("format_utc_timestamp requires an aware UTC instant")
    return instant.isoformat().replace("+00:00", "Z")


def validate_usd_amount(label: str, value: Any) -> None:
    """Require a canonical fixed-3-decimal USD string (non-negative, no exponent).

    Monetary amounts are recorded as text, never floats: a float round-trips
    through jsonb ambiguously (exponent rewriting, -0.0, NaN), so cost is stored
    as e.g. '0.000' or '1.250'.
    """

    if type(value) is not str or not _USD_AMOUNT.fullmatch(value):
        raise ValueError(f"{label} must be a canonical USD amount like '0.000'")


def format_usd_amount(value: int | float | str | Decimal) -> str:
    """Format a non-negative amount as a canonical fixed-3-decimal USD string.

    A positive amount below the 0.001 minimum floors to '0.001' so a paid call
    is never recorded as free; exact zero stays '0.000'.
    """

    amount = Decimal(str(value))
    if amount < 0:
        raise ValueError("USD amount must be non-negative")
    quantized = amount.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if quantized == 0 and amount > 0:
        quantized = Decimal("0.001")
    return f"{quantized:.3f}"


def validate_string_tuple(
    label: str,
    values: Any,
    *,
    item_validator: Callable[[str, Any], None],
    require_non_empty: bool,
) -> None:
    """Require an immutable, duplicate-free tuple of validated strings."""

    if type(values) is not tuple:
        raise ValueError(f"{label} must be an exact tuple")
    if require_non_empty and not values:
        raise ValueError(f"{label} must be non-empty")
    for value in values:
        item_validator(label, value)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def validate_exact_record_tuple(
    label: str,
    values: Any,
    *,
    expected_type: type[_RecordT],
    item_validator: Callable[[_RecordT], None],
    unique_key: Callable[[_RecordT], str],
    unique_key_label: str,
    require_non_empty: bool,
) -> None:
    """Require an exact tuple of exact record types with unique stable keys."""

    if type(values) is not tuple:
        raise ValueError(f"{label} must be an exact tuple")
    if require_non_empty and not values:
        raise ValueError(f"{label} must be non-empty")
    seen_keys: set[str] = set()
    for value in values:
        if type(value) is not expected_type:
            raise ValueError(
                f"{label} must contain exact {expected_type.__name__} records"
            )
        item_validator(value)
        key = unique_key(value)
        if type(key) is not str:
            raise ValueError(
                f"{label} {unique_key_label} must be an exact string"
            )
        if key in seen_keys:
            raise ValueError(
                f"{label} must have unique {unique_key_label} values"
            )
        seen_keys.add(key)


__all__ = [
    "format_utc_timestamp",
    "parse_utc_timestamp",
    "validate_bool",
    "validate_enum_string",
    "validate_exact_record_instance",
    "validate_exact_record_tuple",
    "validate_usd_amount",
    "format_usd_amount",
    "validate_id",
    "validate_int",
    "validate_opaque_ref",
    "validate_pattern_string",
    "validate_sha256",
    "validate_snake_case_name",
    "validate_string_tuple",
    "validate_token",
    "validate_utc_timestamp",
]
