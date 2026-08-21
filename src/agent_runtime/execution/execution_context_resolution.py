"""Resolve schema-bound task context without interpreting domain categories.

Domain stores own and authorize the selected content. Agent Runtime validates
the returned immutable releases, freezes their provenance, and projects only
the registered semantic tree into model-visible context.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Mapping, Protocol, Sequence

from jsonschema import Draft202012Validator


_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def task_context_schema_sha256(schema_document: Mapping[str, object]) -> str:
    """Return the canonical content hash used by Runtime Schema Assets."""

    return _sha256(_canonical_json(dict(schema_document)))


def _bounded_token(label: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or len(value) > 512
    ):
        raise ValueError(f"{label} must be one bounded token")
    return value


@dataclass(frozen=True)
class PromptContextSelector:
    """One domain-owned content release requested for a task-context slot."""

    category: str
    content_key: str
    release_id: str

    def validate(self) -> None:
        if not _NAME.fullmatch(self.category):
            raise ValueError("context category must be snake_case")
        _bounded_token("context content_key", self.content_key)
        _bounded_token("context release_id", self.release_id)

    @property
    def selector_ref(self) -> str:
        self.validate()
        return f"{self.category}:{self.content_key}@{self.release_id}"


@dataclass(frozen=True)
class PromptContextContent:
    """Generic semantic content returned by an authorized domain store."""

    category: str
    content_key: str
    release_id: str
    release_ref: str
    release_sha256: str
    formatted_content: str
    formatted_content_sha256: str

    def validate(self, selector: PromptContextSelector | None = None) -> None:
        PromptContextSelector(
            category=self.category,
            content_key=self.content_key,
            release_id=self.release_id,
        ).validate()
        _bounded_token("context release_ref", self.release_ref)
        if not _SHA256.fullmatch(self.release_sha256):
            raise ValueError("context release_sha256 must be lowercase SHA-256")
        if not isinstance(self.formatted_content, str) or not self.formatted_content:
            raise ValueError("context formatted_content is required")
        if self.formatted_content_sha256 != _sha256(self.formatted_content):
            raise ValueError("context formatted content hash mismatch")
        if selector is not None:
            selector.validate()
            if (
                self.category != selector.category
                or self.content_key != selector.content_key
                or self.release_id != selector.release_id
            ):
                raise ValueError("context store resolved a different selector")


class PromptContextContentResolver(Protocol):
    """Authorized PG/domain adapter used by the generic task assembler."""

    def resolve_prompt_context_content(
        self,
        selector: PromptContextSelector,
    ) -> PromptContextContent:
        """Resolve one exact immutable content release."""


@dataclass(frozen=True)
class TaskPromptContextBinding:
    """Control-plane receipt for one value placed in the semantic tree."""

    slot_key: str
    ordinal: int
    selector: PromptContextSelector
    release_ref: str
    release_sha256: str
    formatted_content_sha256: str

    def as_dict(self) -> dict[str, object]:
        if not _NAME.fullmatch(self.slot_key):
            raise ValueError("task context slot_key must be snake_case")
        if not isinstance(self.ordinal, int) or self.ordinal < 0:
            raise ValueError("task context ordinal must be non-negative")
        self.selector.validate()
        for label, value in (
            ("release_sha256", self.release_sha256),
            ("formatted_content_sha256", self.formatted_content_sha256),
        ):
            if not _SHA256.fullmatch(value):
                raise ValueError(f"task context {label} is invalid")
        return {
            "slot_key": self.slot_key,
            "ordinal": self.ordinal,
            "category": self.selector.category,
            "content_key": self.selector.content_key,
            "release_id": self.selector.release_id,
            "release_ref": self.release_ref,
            "release_sha256": self.release_sha256,
            "formatted_content_sha256": self.formatted_content_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "TaskPromptContextBinding":
        record = cls(
            slot_key=str(value.get("slot_key", "")),
            ordinal=value.get("ordinal", -1),
            selector=PromptContextSelector(
                category=str(value.get("category", "")),
                content_key=str(value.get("content_key", "")),
                release_id=str(value.get("release_id", "")),
            ),
            release_ref=str(value.get("release_ref", "")),
            release_sha256=str(value.get("release_sha256", "")),
            formatted_content_sha256=str(
                value.get("formatted_content_sha256", "")
            ),
        )
        record.as_dict()
        return record


@dataclass(frozen=True)
class TaskPromptContextResolution:
    """Frozen task-specific semantic tree plus Runtime-private provenance."""

    task_schema_ref: str
    task_schema_sha256: str
    content_tree: Mapping[str, object]
    bindings: tuple[TaskPromptContextBinding, ...]
    content_tree_sha256: str
    resolution_sha256: str

    @classmethod
    def build(
        cls,
        *,
        task_schema: Mapping[str, object],
        slot_selectors: Mapping[
            str,
            PromptContextSelector | Sequence[PromptContextSelector],
        ],
        resolver: PromptContextContentResolver,
    ) -> "TaskPromptContextResolution":
        """Resolve arbitrary task keys through the same category/key interface."""

        schema = dict(task_schema)
        Draft202012Validator.check_schema(schema)
        task_schema_ref = schema.get("$id")
        if not isinstance(task_schema_ref, str) or not task_schema_ref:
            raise ValueError("task Prompt context schema requires $id")
        tree: dict[str, object] = {}
        bindings: list[TaskPromptContextBinding] = []
        for slot_key, selection in slot_selectors.items():
            if not _NAME.fullmatch(slot_key):
                raise ValueError("task context keys must be snake_case")
            is_collection = not isinstance(selection, PromptContextSelector)
            selectors = tuple(selection) if is_collection else (selection,)
            if not selectors:
                raise ValueError("task context collection cannot be empty")
            values: list[str] = []
            for ordinal, selector in enumerate(selectors):
                if not isinstance(selector, PromptContextSelector):
                    raise ValueError("task context selector has invalid type")
                content = resolver.resolve_prompt_context_content(selector)
                content.validate(selector)
                values.append(content.formatted_content)
                bindings.append(
                    TaskPromptContextBinding(
                        slot_key=slot_key,
                        ordinal=ordinal,
                        selector=selector,
                        release_ref=content.release_ref,
                        release_sha256=content.release_sha256,
                        formatted_content_sha256=(
                            content.formatted_content_sha256
                        ),
                    )
                )
            tree[slot_key] = values if is_collection else values[0]
        Draft202012Validator(schema).validate(tree)
        content_tree_sha256 = _sha256(_canonical_json(tree))
        body = {
            "task_schema_ref": task_schema_ref,
            "task_schema_sha256": task_context_schema_sha256(schema),
            "content_tree": tree,
            "bindings": [binding.as_dict() for binding in bindings],
            "content_tree_sha256": content_tree_sha256,
        }
        record = cls(
            task_schema_ref=task_schema_ref,
            task_schema_sha256=body["task_schema_sha256"],
            content_tree=tree,
            bindings=tuple(bindings),
            content_tree_sha256=content_tree_sha256,
            resolution_sha256=_sha256(_canonical_json(body)),
        )
        record.validate(task_schema=schema)
        return record

    def validate(self, *, task_schema: Mapping[str, object]) -> None:
        schema = dict(task_schema)
        Draft202012Validator.check_schema(schema)
        if schema.get("$id") != self.task_schema_ref:
            raise ValueError("task context crossed its registered schema")
        if task_context_schema_sha256(schema) != self.task_schema_sha256:
            raise ValueError("task context schema hash mismatch")
        tree = dict(self.content_tree)
        Draft202012Validator(schema).validate(tree)
        if self.content_tree_sha256 != _sha256(_canonical_json(tree)):
            raise ValueError("task context content-tree hash mismatch")
        binding_rows = [binding.as_dict() for binding in self.bindings]
        body = {
            "task_schema_ref": self.task_schema_ref,
            "task_schema_sha256": self.task_schema_sha256,
            "content_tree": tree,
            "bindings": binding_rows,
            "content_tree_sha256": self.content_tree_sha256,
        }
        if self.resolution_sha256 != _sha256(_canonical_json(body)):
            raise ValueError("task context resolution hash mismatch")

    @classmethod
    def from_dict(
        cls,
        value: Mapping[str, object],
        *,
        task_schema: Mapping[str, object],
    ) -> "TaskPromptContextResolution":
        if value.get("schema_version") != "task_prompt_context_resolution_v1":
            raise ValueError("unsupported task Prompt context resolution schema")
        content_tree = value.get("content_tree")
        raw_bindings = value.get("bindings")
        if not isinstance(content_tree, Mapping):
            raise ValueError("task Prompt context content_tree must be an object")
        if not isinstance(raw_bindings, list) or any(
            not isinstance(binding, Mapping) for binding in raw_bindings
        ):
            raise ValueError("task Prompt context bindings must be objects")
        record = cls(
            task_schema_ref=str(value.get("task_schema_ref", "")),
            task_schema_sha256=str(value.get("task_schema_sha256", "")),
            content_tree=dict(content_tree),
            bindings=tuple(
                TaskPromptContextBinding.from_dict(binding)
                for binding in raw_bindings
            ),
            content_tree_sha256=str(value.get("content_tree_sha256", "")),
            resolution_sha256=str(value.get("resolution_sha256", "")),
        )
        record.validate(task_schema=task_schema)
        return record

    def as_dict(self, *, task_schema: Mapping[str, object]) -> dict[str, object]:
        self.validate(task_schema=task_schema)
        return {
            "schema_version": "task_prompt_context_resolution_v1",
            "task_schema_ref": self.task_schema_ref,
            "task_schema_sha256": self.task_schema_sha256,
            "content_tree": dict(self.content_tree),
            "bindings": [binding.as_dict() for binding in self.bindings],
            "content_tree_sha256": self.content_tree_sha256,
            "resolution_sha256": self.resolution_sha256,
        }

    def model_context(self, *, task_schema: Mapping[str, object]) -> dict[str, object]:
        """Return only semantic keys; selectors, refs, and hashes stay private."""

        self.validate(task_schema=task_schema)
        return json.loads(_canonical_json(dict(self.content_tree)))

    def selector_keys(self, slot_key: str) -> tuple[str, ...]:
        return tuple(
            binding.selector.content_key
            for binding in self.bindings
            if binding.slot_key == slot_key
        )


__all__ = [
    "PromptContextContent",
    "PromptContextContentResolver",
    "PromptContextSelector",
    "TaskPromptContextBinding",
    "TaskPromptContextResolution",
    "task_context_schema_sha256",
]
