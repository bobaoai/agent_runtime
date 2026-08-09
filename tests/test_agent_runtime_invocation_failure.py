from __future__ import annotations

import json

from agent_runtime.invocation.invocation_failure_recording import (
    PROVIDER_FAILURE_DETAIL_MAX_BYTES,
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


def test_provider_failure_detail_bounds_the_complete_record() -> None:
    huge = "e" * (3 * PROVIDER_FAILURE_DETAIL_MAX_BYTES)
    build = lambda: build_provider_failure_detail(  # noqa: E731
        failure_class="provider_failure",
        failure_code="codex_cli_invocation_error",
        message=huge,
        provider_response=huge,
        provider_error_message=huge,
        transport_exit_code=None,
        retryable=True,
    )
    record = build()
    payload = json.loads(record)

    assert record == build()
    assert len(record) <= PROVIDER_FAILURE_DETAIL_MAX_BYTES
    assert payload["message_truncated"] is True
    assert payload["provider_error_message_truncated"] is True
    assert payload["provider_response_truncated"] is True
    assert payload["provider_error_message_byte_size"] == len(
        huge.encode("utf-8")
    )


def test_provider_failure_detail_error_message_alone_cannot_bypass_cap() -> None:
    record = build_provider_failure_detail(
        failure_class="provider_failure",
        failure_code="claude_agent_sdk_process_error",
        message="Claude Agent SDK invocation failed",
        provider_response="",
        provider_error_message="stderr " * (PROVIDER_FAILURE_DETAIL_MAX_BYTES // 4),
        transport_exit_code=1,
        retryable=True,
    )

    assert len(record) <= PROVIDER_FAILURE_DETAIL_MAX_BYTES
    assert json.loads(record)["provider_error_message_truncated"] is True
