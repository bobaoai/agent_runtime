"""Content-addressed selection contract for Runtime Release inspection."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, ClassVar, Mapping

from ..foundation.foundation_contract_validation import (
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ReleaseInventorySelection:
    """One deterministic set of exact Release refs selected for projection.

    This value carries no Principal, Entitlement, authorization decision, or
    grant. An authorized caller may construct it, but the selection itself is
    only a content-addressed projection input.
    """

    record_type: ClassVar[str] = "release_inventory_selection"

    selection_id: str
    selected_release_refs: tuple[str, ...]
    selection_sha256: str

    @staticmethod
    def _selection_id(selected_release_refs: tuple[str, ...]) -> str:
        refs_sha256 = _canonical_sha256(
            {"selected_release_refs": list(selected_release_refs)}
        )
        return f"release_inventory_selection_{refs_sha256[:24]}"

    def _payload(self) -> dict[str, Any]:
        return {
            "selection_id": self.selection_id,
            "selected_release_refs": list(self.selected_release_refs),
        }

    def validate(self) -> None:
        """Validate canonical order, derived identity, and content hash."""

        validate_snake_case_name("selection_id", self.selection_id)
        if type(self.selected_release_refs) is not tuple:
            raise ValueError("selected_release_refs must be an immutable tuple")
        for release_ref in self.selected_release_refs:
            validate_opaque_ref("selected_release_ref", release_ref)
        if tuple(sorted(self.selected_release_refs)) != self.selected_release_refs:
            raise ValueError("selected_release_refs must use canonical sorted order")
        if len(set(self.selected_release_refs)) != len(self.selected_release_refs):
            raise ValueError("selected_release_refs must be unique")
        expected_selection_id = self._selection_id(self.selected_release_refs)
        if self.selection_id != expected_selection_id:
            raise ValueError("selection_id must be derived from selected_release_refs")
        validate_sha256("selection_sha256", self.selection_sha256)
        if self.selection_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("release inventory selection hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible selection."""

        self.validate()
        return {**self._payload(), "selection_sha256": self.selection_sha256}

    @classmethod
    def build(
        cls,
        selected_release_refs: tuple[str, ...],
    ) -> "ReleaseInventorySelection":
        """Build one sorted selection while refusing duplicate input refs."""

        if type(selected_release_refs) is not tuple:
            raise ValueError("selected_release_refs must be an immutable tuple")
        refs = selected_release_refs
        if any(type(release_ref) is not str for release_ref in refs):
            raise ValueError("selected_release_refs must contain strings")
        if len(set(refs)) != len(refs):
            raise ValueError("selected_release_refs must be unique")
        canonical_refs = tuple(sorted(refs))
        selection_id = cls._selection_id(canonical_refs)
        provisional = cls(
            selection_id=selection_id,
            selected_release_refs=canonical_refs,
            selection_sha256="0" * 64,
        )
        selection = cls(
            selection_id=selection_id,
            selected_release_refs=canonical_refs,
            selection_sha256=_canonical_sha256(provisional._payload()),
        )
        selection.validate()
        return selection

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any],
    ) -> "ReleaseInventorySelection":
        """Reconstruct and validate one serialized selection."""

        if type(payload) is not dict or set(payload) != {
            "selection_id",
            "selected_release_refs",
            "selection_sha256",
        }:
            raise ValueError("release inventory selection fields are invalid")
        selected_release_refs = payload["selected_release_refs"]
        if type(selected_release_refs) is not list:
            raise ValueError("selected_release_refs must be a JSON array")
        selection = cls(
            selection_id=payload["selection_id"],
            selected_release_refs=tuple(selected_release_refs),
            selection_sha256=payload["selection_sha256"],
        )
        selection.validate()
        return selection


__all__ = ["ReleaseInventorySelection"]
