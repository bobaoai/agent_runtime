"""Record bounded provider failure detail behind a Runtime artifact ref."""

from __future__ import annotations

import json


PROVIDER_FAILURE_RESPONSE_MAX_BYTES = 96 * 1024
PROVIDER_FAILURE_DETAIL_MAX_BYTES = 96 * 1024
_ERROR_MESSAGE_INITIAL_MAX_BYTES = 16 * 1024
_MESSAGE_INITIAL_MAX_BYTES = 4 * 1024


def _bounded_utf8(text: str, max_bytes: int) -> tuple[str, int, bool]:
    raw = text.encode("utf-8")
    bounded = raw[: max(max_bytes, 0)]
    return (
        bounded.decode("utf-8", errors="ignore"),
        len(raw),
        len(bounded) != len(raw),
    )


def build_provider_failure_detail(
    *,
    failure_class: str,
    failure_code: str,
    message: str,
    provider_response: str,
    provider_error_message: str | None,
    transport_exit_code: int | None,
    retryable: bool,
) -> bytes:
    """Return deterministic UTF-8 JSON bounded to one total record byte cap.

    Every caller-supplied text field is truncated independently, and the
    encoded record as a whole never exceeds PROVIDER_FAILURE_DETAIL_MAX_BYTES,
    so an exception string that embeds a huge provider payload cannot bypass
    the bound through message or provider_error_message.
    """

    def encode(
        response_budget: int,
        error_budget: int,
        message_budget: int,
    ) -> bytes:
        response_text, response_size, response_truncated = _bounded_utf8(
            provider_response, response_budget
        )
        if provider_error_message is None:
            error_text: str | None = None
            error_size = 0
            error_truncated = False
        else:
            error_text, error_size, error_truncated = _bounded_utf8(
                provider_error_message, error_budget
            )
        message_text, message_size, message_truncated = _bounded_utf8(
            message, message_budget
        )
        return json.dumps(
            {
                "failure_class": failure_class,
                "failure_code": failure_code,
                "message": message_text,
                "message_byte_size": message_size,
                "message_truncated": message_truncated,
                "provider_error_code": None,
                "provider_error_message": error_text,
                "provider_error_message_byte_size": error_size,
                "provider_error_message_truncated": error_truncated,
                "transport_exit_code": transport_exit_code,
                "retryable": retryable,
                "provider_response": response_text,
                "provider_response_byte_size": response_size,
                "provider_response_truncated": response_truncated,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    budgets = [
        PROVIDER_FAILURE_RESPONSE_MAX_BYTES,
        _ERROR_MESSAGE_INITIAL_MAX_BYTES,
        _MESSAGE_INITIAL_MAX_BYTES,
    ]
    record = encode(*budgets)
    while len(record) > PROVIDER_FAILURE_DETAIL_MAX_BYTES:
        overage = len(record) - PROVIDER_FAILURE_DETAIL_MAX_BYTES
        for index, budget in enumerate(budgets):
            if budget > 0:
                budgets[index] = max(budget - overage, 0)
                break
        else:
            raise AssertionError(
                "failure detail exceeds the cap with all text budgets empty"
            )
        record = encode(*budgets)
    return record


__all__ = [
    "PROVIDER_FAILURE_DETAIL_MAX_BYTES",
    "PROVIDER_FAILURE_RESPONSE_MAX_BYTES",
    "build_provider_failure_detail",
]
