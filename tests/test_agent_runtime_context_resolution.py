from __future__ import annotations

import hashlib

import pytest
from jsonschema import ValidationError

from agent_runtime.execution.execution_context_resolution import (
    PromptContextContent,
    PromptContextSelector,
    TaskPromptContextResolution,
)


class _Resolver:
    def __init__(self, values: dict[tuple[str, str, str], str]) -> None:
        self._values = values

    def resolve_prompt_context_content(
        self,
        selector: PromptContextSelector,
    ) -> PromptContextContent:
        content = self._values[
            (selector.category, selector.content_key, selector.release_id)
        ]
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        release_sha256 = hashlib.sha256(
            selector.selector_ref.encode("utf-8")
        ).hexdigest()
        return PromptContextContent(
            category=selector.category,
            content_key=selector.content_key,
            release_id=selector.release_id,
            release_ref=f"context:{selector.selector_ref}",
            release_sha256=release_sha256,
            formatted_content=content,
            formatted_content_sha256=content_sha256,
        )


def _schema(*keys: str) -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "schema:test_task_prompt_context@v1",
        "type": "object",
        "additionalProperties": False,
        "required": list(keys),
        "properties": {
            key: (
                {
                    "type": "array",
                    "minItems": 1,
                    "items": {"type": "string"},
                }
                if key in {"lens", "risk_limits"}
                else {"type": "string"}
            )
            for key in keys
        },
    }


def test_expertise_and_lens_are_peer_task_keys() -> None:
    resolver = _Resolver(
        {
            ("expertise", "optical_interconnect", "v1"): "expertise body",
            ("lens", "optical_architecture_and_value_pool", "v3"): "lens body",
        }
    )
    resolution = TaskPromptContextResolution.build(
        task_schema=_schema("expertise", "lens"),
        slot_selectors={
            "expertise": PromptContextSelector(
                "expertise", "optical_interconnect", "v1"
            ),
            "lens": (
                PromptContextSelector(
                    "lens", "optical_architecture_and_value_pool", "v3"
                ),
            ),
        },
        resolver=resolver,
    )

    assert resolution.model_context(
        task_schema=_schema("expertise", "lens")
    ) == {"expertise": "expertise body", "lens": ["lens body"]}
    assert "optical_interconnect" not in str(
        resolution.model_context(task_schema=_schema("expertise", "lens"))
    )


def test_same_interface_accepts_unrelated_task_keys() -> None:
    resolver = _Resolver(
        {
            ("portfolio", "balanced_book", "v2"): "portfolio body",
            ("risk_limit", "institutional_limits", "v7"): "risk body",
        }
    )
    resolution = TaskPromptContextResolution.build(
        task_schema=_schema("portfolio", "risk_limits"),
        slot_selectors={
            "portfolio": PromptContextSelector(
                "portfolio", "balanced_book", "v2"
            ),
            "risk_limits": (
                PromptContextSelector(
                    "risk_limit", "institutional_limits", "v7"
                ),
            ),
        },
        resolver=resolver,
    )

    assert set(resolution.content_tree) == {"portfolio", "risk_limits"}


def test_task_schema_rejects_undeclared_or_missing_keys() -> None:
    resolver = _Resolver(
        {("expertise", "optical_interconnect", "v1"): "expertise body"}
    )
    with pytest.raises(ValidationError):
        TaskPromptContextResolution.build(
            task_schema=_schema("expertise", "lens"),
            slot_selectors={
                "expertise": PromptContextSelector(
                    "expertise", "optical_interconnect", "v1"
                )
            },
            resolver=resolver,
        )
