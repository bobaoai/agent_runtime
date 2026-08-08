from __future__ import annotations

import json

from agent_runtime.invocation.invocation_failure_recording import (
    PROVIDER_FAILURE_RESPONSE_MAX_BYTES,
    build_provider_failure_detail,
)


def test_provider_failure_detail_is_deterministic_and_byte_bounded() -> None:
    response = "x" + "€" * PROVIDER_FAILURE_RESPONSE_MAX_BYTES
    first = build_provider_failure_detail(
        failure_class="provider_failure",
        failure_code="provider_timeout",
        message="Provider invocation failed",
        provider_response=response,
        provider_error_message="timeout",
        transport_exit_code=70,
        retryable=True,
    )
    second = build_provider_failure_detail(
        failure_class="provider_failure",
        failure_code="provider_timeout",
        message="Provider invocation failed",
        provider_response=response,
        provider_error_message="timeout",
        transport_exit_code=70,
        retryable=True,
    )
    payload = json.loads(first)

    assert first == second
    assert payload["provider_response_byte_size"] == len(response.encode("utf-8"))
    assert payload["provider_response_truncated"] is True
    assert len(payload["provider_response"].encode("utf-8")) <= (
        PROVIDER_FAILURE_RESPONSE_MAX_BYTES
    )
