"""Shared Retry Policy attempt-budget validation."""

from __future__ import annotations

from typing import Final


RETRY_POLICY_MIN_ATTEMPTS: Final[int] = 1
RETRY_POLICY_MAX_ATTEMPTS: Final[int] = 100


def validate_retry_attempt_budget(value: object) -> int:
    """Return one exact bounded Retry Policy attempt budget."""

    if type(value) is not int or not (
        RETRY_POLICY_MIN_ATTEMPTS <= value <= RETRY_POLICY_MAX_ATTEMPTS
    ):
        raise ValueError(
            "Retry Policy max_attempts must be an integer between "
            f"{RETRY_POLICY_MIN_ATTEMPTS} and {RETRY_POLICY_MAX_ATTEMPTS}"
        )
    return value


__all__ = [
    "RETRY_POLICY_MAX_ATTEMPTS",
    "RETRY_POLICY_MIN_ATTEMPTS",
    "validate_retry_attempt_budget",
]
