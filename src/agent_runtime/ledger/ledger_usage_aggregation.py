"""Provider-neutral model usage aggregation.

Per-call records remain authoritative.  An aggregate is emitted only when every
module is known; otherwise its value is ``None`` and the number of unknown
modules is explicit.  Provider dollar charges are optional and never inferred
from token usage inside this module.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any


TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
)
COST_FIELDS = ("estimated_cost_usd", "provider_charge_usd")


def complete_optional_int_sum(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> tuple[int | None, int]:
    """Sum integer modules only when every source reports a value."""

    unknown_count = sum(row.get(field) is None for row in rows)
    if unknown_count:
        return None, unknown_count
    return sum(int(row[field]) for row in rows), 0


def complete_optional_usd_sum(
    rows: Sequence[Mapping[str, Any]],
    field: str,
) -> tuple[str | None, int]:
    """Sum canonical USD-string costs only when every source reports a value.

    Costs are fixed-3-decimal strings, never floats; summing with Decimal keeps
    the total exact and re-emits it in the same canonical form.
    """

    unknown_count = sum(row.get(field) is None for row in rows)
    if unknown_count:
        return None, unknown_count
    total = sum((Decimal(row[field]) for row in rows), Decimal("0"))
    return f"{total:.3f}", 0


def aggregate_model_usage(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int | str | None]:
    """Aggregate token and optional cost fields without converting unknown to zero."""

    result: dict[str, int | str | None] = {}
    for field in TOKEN_FIELDS:
        value, unknown_count = complete_optional_int_sum(rows, field)
        result[field] = value
        result[f"{field}_unknown_module_count"] = unknown_count
    for field in COST_FIELDS:
        cost, unknown_count = complete_optional_usd_sum(rows, field)
        result[field] = cost
        result[f"{field}_unknown_module_count"] = unknown_count
    return result
