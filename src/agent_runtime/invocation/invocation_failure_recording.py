"""Record bounded provider failure detail behind a Runtime artifact ref."""

from __future__ import annotations

import json


PROVIDER_FAILURE_RESPONSE_MAX_BYTES = 96 * 1024


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
    """Return deterministic UTF-8 JSON with bounded provider diagnostics."""

    response_bytes = provider_response.encode("utf-8")
    bounded = response_bytes[:PROVIDER_FAILURE_RESPONSE_MAX_BYTES]
    return json.dumps(
        {
            "failure_class": failure_class,
            "failure_code": failure_code,
            "message": message,
            "provider_error_code": None,
            "provider_error_message": provider_error_message,
            "transport_exit_code": transport_exit_code,
            "retryable": retryable,
            "provider_response": bounded.decode("utf-8", errors="ignore"),
            "provider_response_byte_size": len(response_bytes),
            "provider_response_truncated": len(bounded) != len(response_bytes),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "PROVIDER_FAILURE_RESPONSE_MAX_BYTES",
    "build_provider_failure_detail",
]
