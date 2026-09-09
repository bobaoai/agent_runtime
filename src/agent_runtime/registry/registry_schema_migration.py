"""Content-only migration plan for one side-by-side Registry schema cutover."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, ClassVar

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    ExecutionProfileRelease,
    EvaluationPolicyRelease,
    ExecutionVariantPolicyRelease,
    PromptBundleRelease,
    PromptComponentRelease,
    RetryPolicyRelease,
    ModuleRelease,
    SchemaAssetRelease,
    WorkflowRelease,
)
from ..foundation.foundation_contract_validation import validate_utc_timestamp
from .registry_release_registration import RuntimeReleaseBundle


_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PATH_KEYS = frozenset(
    {
        "path",
        "project_root",
        "repository_root",
        "prompt_path",
        "schema_path",
        "owner_contract_path",
    }
)


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _release_payload(record: Any) -> dict[str, Any]:
    if not hasattr(record, "as_dict"):
        raise ValueError("migration bundle contains an unsupported record")
    payload = record.as_dict()
    if type(payload) is not dict:
        raise ValueError("migration release payload must be a dictionary")
    _reject_path_bearing_content(payload)
    return payload


def _bundle_payload(bundle: RuntimeReleaseBundle) -> dict[str, Any]:
    if type(bundle) is not RuntimeReleaseBundle:
        raise ValueError("target_bundle must be a RuntimeReleaseBundle")
    return {
        field_name: [
            _release_payload(record) for record in getattr(bundle, field_name)
        ]
        for field_name in (
            "schema_assets",
            "prompt_components",
            "prompt_bundles",
            "behavior_policies",
            "evaluation_policies",
            "retry_policies",
            "execution_variant_policies",
            "execution_profiles",
            "modules",
            "workflows",
        )
    }


def _bundle_from_payload(payload: Any) -> RuntimeReleaseBundle:
    if type(payload) is not dict:
        raise ValueError("migration target_bundle must be one JSON object")
    decoders = {
        "schema_assets": SchemaAssetRelease.from_dict,
        "prompt_components": PromptComponentRelease.from_dict,
        "prompt_bundles": PromptBundleRelease.from_dict,
        "behavior_policies": BehaviorPolicyRelease.from_dict,
        "evaluation_policies": EvaluationPolicyRelease.from_dict,
        "retry_policies": RetryPolicyRelease.from_dict,
        "execution_variant_policies": ExecutionVariantPolicyRelease.from_dict,
        "execution_profiles": ExecutionProfileRelease.from_dict,
        "modules": ModuleRelease.from_dict,
        "workflows": WorkflowRelease.from_dict,
    }
    if set(payload) != set(decoders):
        raise ValueError("migration target_bundle has an invalid shape")
    return RuntimeReleaseBundle(
        **{
            field_name: tuple(
                decoder(item) for item in payload[field_name]
            )
            for field_name, decoder in decoders.items()
        }
    )


def _reject_path_bearing_content(value: Any, *, key: str | None = None) -> None:
    if key in _PATH_KEYS:
        raise ValueError("Registry migration candidates cannot contain host paths")
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            _reject_path_bearing_content(child_value, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_path_bearing_content(child)


@dataclass(frozen=True)
class RegistryReleaseIdentityDisposition:
    """Complete owner decision for one immutable predecessor release row."""

    source_table: str
    source_release_ref: str
    source_release_sha256: str
    disposition: str
    target_release_ref: str | None
    target_release_sha256: str | None

    def validate(self) -> None:
        if not _ID_PATTERN.fullmatch(self.source_table):
            raise ValueError("invalid source release table")
        if not self.source_release_ref:
            raise ValueError("source_release_ref is required")
        if not _SHA_PATTERN.fullmatch(self.source_release_sha256):
            raise ValueError("invalid source release hash")
        if self.disposition not in {"unchanged", "reissued", "retired"}:
            raise ValueError("invalid release identity disposition")
        if self.disposition == "retired":
            if (
                self.target_release_ref is not None
                or self.target_release_sha256 is not None
            ):
                raise ValueError("retired release cannot name a target")
            return
        if not self.target_release_ref or not self.target_release_sha256:
            raise ValueError("retained release disposition requires a target")
        if not _SHA_PATTERN.fullmatch(self.target_release_sha256):
            raise ValueError("invalid target release hash")
        if self.disposition == "unchanged" and (
            self.source_release_ref != self.target_release_ref
            or self.source_release_sha256 != self.target_release_sha256
        ):
            raise ValueError("unchanged disposition must preserve exact identity")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_table": self.source_table,
            "source_release_ref": self.source_release_ref,
            "source_release_sha256": self.source_release_sha256,
            "disposition": self.disposition,
            "target_release_ref": self.target_release_ref,
            "target_release_sha256": self.target_release_sha256,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "RegistryReleaseIdentityDisposition":
        record = cls(**payload)
        record.validate()
        return record


@dataclass(frozen=True)
class RegistryAdmissionIdentityDisposition:
    """Exact migration decision for one predecessor admission row."""

    source_admission_id: str
    source_admission_sha256: str
    disposition: str
    target_admission_id: str | None
    target_admission_sha256: str | None

    def validate(self) -> None:
        if not _ID_PATTERN.fullmatch(self.source_admission_id):
            raise ValueError("invalid source admission_id")
        if not _SHA_PATTERN.fullmatch(self.source_admission_sha256):
            raise ValueError("invalid source admission hash")
        if self.disposition != "removed":
            raise ValueError("legacy admission rows must be removed")
        if (
            self.target_admission_id is not None
            or self.target_admission_sha256 is not None
        ):
            raise ValueError("removed admission cannot name a target")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "source_admission_id": self.source_admission_id,
            "source_admission_sha256": self.source_admission_sha256,
            "disposition": self.disposition,
            "target_admission_id": self.target_admission_id,
            "target_admission_sha256": self.target_admission_sha256,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "RegistryAdmissionIdentityDisposition":
        record = cls(**payload)
        record.validate()
        return record


@dataclass(frozen=True)
class RegistryActivePointerDisposition:
    """Exact migration decision for one predecessor active pointer."""

    subject_kind: str
    subject_id: str
    source_release_ref: str
    source_release_sha256: str
    disposition: str
    target_release_ref: str | None
    target_release_sha256: str | None

    def validate(self) -> None:
        if not _ID_PATTERN.fullmatch(self.subject_kind):
            raise ValueError("invalid active-pointer subject_kind")
        if self.subject_kind not in {"runtime_module", "workflow"}:
            raise ValueError("active pointer supports only Module or Workflow")
        if not _ID_PATTERN.fullmatch(self.subject_id):
            raise ValueError("invalid active-pointer subject_id")
        if not self.source_release_ref:
            raise ValueError("source active-pointer release_ref is required")
        if not _SHA_PATTERN.fullmatch(self.source_release_sha256):
            raise ValueError("invalid source active-pointer release hash")
        if self.disposition not in {"copied", "replaced", "removed"}:
            raise ValueError("invalid active-pointer disposition")
        if self.disposition == "removed":
            if (
                self.target_release_ref is not None
                or self.target_release_sha256 is not None
            ):
                raise ValueError("removed active pointer cannot name a target")
            return
        if not self.target_release_ref or not self.target_release_sha256:
            raise ValueError("retained active pointer requires a target")
        if not _SHA_PATTERN.fullmatch(self.target_release_sha256):
            raise ValueError("invalid target active-pointer release hash")
        if self.disposition == "copied" and (
            self.source_release_ref != self.target_release_ref
            or self.source_release_sha256 != self.target_release_sha256
        ):
            raise ValueError("copied active pointer must preserve exact release")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "source_release_ref": self.source_release_ref,
            "source_release_sha256": self.source_release_sha256,
            "disposition": self.disposition,
            "target_release_ref": self.target_release_ref,
            "target_release_sha256": self.target_release_sha256,
        }

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "RegistryActivePointerDisposition":
        record = cls(**payload)
        record.validate()
        return record


@dataclass(frozen=True)
class RegistrySchemaMigrationPlan:
    """Hash-pinned, path-free input to the explicit PostgreSQL migration."""

    record_type: ClassVar[str] = "registry_schema_migration_plan"

    migration_id: str
    source_schema: str
    target_schema: str
    source_structure_sha256: str
    source_row_identity_sha256: str
    target_schema_release_ref: str
    target_schema_release_sha256: str
    target_bundle: RuntimeReleaseBundle
    release_dispositions: tuple[RegistryReleaseIdentityDisposition, ...]
    admission_dispositions: tuple[
        RegistryAdmissionIdentityDisposition, ...
    ]
    active_pointer_dispositions: tuple[
        RegistryActivePointerDisposition, ...
    ]
    planned_at_utc: str
    plan_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "migration_id": self.migration_id,
            "source_schema": self.source_schema,
            "target_schema": self.target_schema,
            "source_structure_sha256": self.source_structure_sha256,
            "source_row_identity_sha256": self.source_row_identity_sha256,
            "target_schema_release_ref": self.target_schema_release_ref,
            "target_schema_release_sha256": self.target_schema_release_sha256,
            "target_bundle": _bundle_payload(self.target_bundle),
            "release_dispositions": [
                disposition.as_dict()
                for disposition in self.release_dispositions
            ],
            "admission_dispositions": [
                disposition.as_dict()
                for disposition in self.admission_dispositions
            ],
            "active_pointer_dispositions": [
                disposition.as_dict()
                for disposition in self.active_pointer_dispositions
            ],
            "planned_at_utc": self.planned_at_utc,
        }

    def validate(self) -> None:
        if not _ID_PATTERN.fullmatch(self.migration_id):
            raise ValueError("invalid Registry migration_id")
        for label, schema in (
            ("source_schema", self.source_schema),
            ("target_schema", self.target_schema),
        ):
            if not _SCHEMA_PATTERN.fullmatch(schema):
                raise ValueError(f"invalid {label}")
        if self.source_schema == self.target_schema:
            raise ValueError("Registry migration requires side-by-side schemas")
        for label, value in (
            ("source_structure_sha256", self.source_structure_sha256),
            ("source_row_identity_sha256", self.source_row_identity_sha256),
            ("target_schema_release_sha256", self.target_schema_release_sha256),
            ("plan_sha256", self.plan_sha256),
        ):
            if not _SHA_PATTERN.fullmatch(value):
                raise ValueError(f"invalid {label}")
        if not self.target_schema_release_ref:
            raise ValueError("target_schema_release_ref is required")
        if type(self.release_dispositions) is not tuple:
            raise ValueError("release_dispositions must be an immutable tuple")
        disposition_keys: set[tuple[str, str]] = set()
        for disposition in self.release_dispositions:
            if type(disposition) is not RegistryReleaseIdentityDisposition:
                raise ValueError("invalid release disposition record")
            disposition.validate()
            key = (disposition.source_table, disposition.source_release_ref)
            if key in disposition_keys:
                raise ValueError("duplicate predecessor release disposition")
            disposition_keys.add(key)
        if type(self.admission_dispositions) is not tuple:
            raise ValueError("admission_dispositions must be an immutable tuple")
        admission_ids: set[str] = set()
        for disposition in self.admission_dispositions:
            if type(disposition) is not RegistryAdmissionIdentityDisposition:
                raise ValueError("invalid admission disposition record")
            disposition.validate()
            if disposition.source_admission_id in admission_ids:
                raise ValueError("duplicate predecessor admission disposition")
            admission_ids.add(disposition.source_admission_id)
        if type(self.active_pointer_dispositions) is not tuple:
            raise ValueError(
                "active_pointer_dispositions must be an immutable tuple"
            )
        pointer_keys: set[tuple[str, str]] = set()
        for disposition in self.active_pointer_dispositions:
            if type(disposition) is not RegistryActivePointerDisposition:
                raise ValueError("invalid active-pointer disposition record")
            disposition.validate()
            key = (disposition.subject_kind, disposition.subject_id)
            if key in pointer_keys:
                raise ValueError("duplicate predecessor active-pointer disposition")
            pointer_keys.add(key)
        validate_utc_timestamp("planned_at_utc", self.planned_at_utc)
        if self.target_bundle.is_empty():
            raise ValueError("migration target bundle must not be empty")
        if self.plan_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Registry migration plan hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        return {**self._payload(), "plan_sha256": self.plan_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "RegistrySchemaMigrationPlan":
        provisional = cls(**fields, plan_sha256="0" * 64)
        record = cls(
            **fields,
            plan_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
    ) -> "RegistrySchemaMigrationPlan":
        expected = {
            "migration_id",
            "source_schema",
            "target_schema",
            "source_structure_sha256",
            "source_row_identity_sha256",
            "target_schema_release_ref",
            "target_schema_release_sha256",
            "target_bundle",
            "release_dispositions",
            "admission_dispositions",
            "active_pointer_dispositions",
            "planned_at_utc",
            "plan_sha256",
        }
        if type(payload) is not dict or set(payload) != expected:
            raise ValueError("Registry migration plan has an invalid shape")
        record = cls(
            migration_id=payload["migration_id"],
            source_schema=payload["source_schema"],
            target_schema=payload["target_schema"],
            source_structure_sha256=payload["source_structure_sha256"],
            source_row_identity_sha256=payload[
                "source_row_identity_sha256"
            ],
            target_schema_release_ref=payload["target_schema_release_ref"],
            target_schema_release_sha256=payload[
                "target_schema_release_sha256"
            ],
            target_bundle=_bundle_from_payload(payload["target_bundle"]),
            release_dispositions=tuple(
                RegistryReleaseIdentityDisposition.from_dict(item)
                for item in payload["release_dispositions"]
            ),
            admission_dispositions=tuple(
                RegistryAdmissionIdentityDisposition.from_dict(item)
                for item in payload["admission_dispositions"]
            ),
            active_pointer_dispositions=tuple(
                RegistryActivePointerDisposition.from_dict(item)
                for item in payload["active_pointer_dispositions"]
            ),
            planned_at_utc=payload["planned_at_utc"],
            plan_sha256=payload["plan_sha256"],
        )
        record.validate()
        return record


__all__ = [
    "RegistryActivePointerDisposition",
    "RegistryAdmissionIdentityDisposition",
    "RegistryReleaseIdentityDisposition",
    "RegistrySchemaMigrationPlan",
]
