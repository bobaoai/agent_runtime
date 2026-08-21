"""Cell-local immutable staging boundary for Runtime execution content.

The Runtime ledger stores refs and hashes only. This in-memory implementation
stages the bytes behind those refs for execution and tests; formal recorded-
content persistence belongs to a registered PostgreSQL implementation. Domain
host composition supplies schema-to-artifact-kind mappings for typed outputs.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from threading import RLock
from typing import Mapping

from ..contracts.registry_workflow_definition import (
    ExecutionOutputRegistrationRequest,
    ExecutionOutputRegistrationResult,
    ResolvedArtifactRef,
)
from ..contracts.execution_module_definition import (
    ModuleFailureDetailBinding,
    ModuleOutputBinding,
)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


@dataclass(frozen=True)
class StoredCellArtifact:
    """Immutable content plus the metadata required by execution adapters."""

    resolved: ResolvedArtifactRef
    content: bytes
    schema_ref: str
    schema_sha256: str
    media_type: str


class InMemoryCellArtifactStore:
    """Duplicate-safe Cell artifact store for conformance and shadow runs."""

    def __init__(
        self,
        *,
        artifact_kind_by_schema_ref: Mapping[str, str] | None = None,
    ) -> None:
        self._kind_by_schema = dict(artifact_kind_by_schema_ref or {})
        self._lock = RLock()
        self._artifacts_by_ref: dict[str, StoredCellArtifact] = {}
        self._ref_by_idempotency: dict[str, str] = {}

    def put_bytes(
        self,
        *,
        artifact_kind_id: str,
        schema_version: str,
        schema_ref: str,
        schema_sha256: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        logical_name: str | None = None,
    ) -> ResolvedArtifactRef:
        """Commit exact bytes once and return their content-addressed identity."""

        if type(content) is not bytes:
            raise ValueError("Cell artifact content must be exact bytes")
        content_sha256 = _sha256(content)
        artifact_id = _stable_id(
            "cell_artifact",
            artifact_kind_id,
            idempotency_key,
        )
        artifact_ref = f"cell-artifact:{artifact_id}"
        resolved = ResolvedArtifactRef(
            artifact_id=artifact_id,
            artifact_kind_id=artifact_kind_id,
            artifact_ref=artifact_ref,
            artifact_sha256=content_sha256,
            schema_version=schema_version,
            logical_name=logical_name,
        )
        resolved.validate()
        value = StoredCellArtifact(
            resolved=resolved,
            content=content,
            schema_ref=schema_ref,
            schema_sha256=schema_sha256,
            media_type=media_type,
        )
        with self._lock:
            prior_ref = self._ref_by_idempotency.get(idempotency_key)
            if prior_ref is not None:
                prior = self._artifacts_by_ref[prior_ref]
                if prior != value:
                    raise ValueError("Cell artifact idempotency conflict")
                return prior.resolved
            prior = self._artifacts_by_ref.get(artifact_ref)
            if prior is not None and prior != value:
                raise ValueError("Cell artifact identity conflict")
            self._artifacts_by_ref[artifact_ref] = value
            self._ref_by_idempotency[idempotency_key] = artifact_ref
        return resolved

    def resolve_artifact_ref(self, artifact_ref: str) -> ResolvedArtifactRef:
        """Resolve one immutable Cell-local artifact by opaque ref."""

        with self._lock:
            try:
                return self._artifacts_by_ref[artifact_ref].resolved
            except KeyError as exc:
                raise KeyError(f"unknown Cell artifact: {artifact_ref}") from exc

    def read_artifact_bytes(self, artifact_ref: str) -> bytes:
        """Return exact bytes for an already admitted Cell-local ref."""

        with self._lock:
            try:
                return self._artifacts_by_ref[artifact_ref].content
            except KeyError as exc:
                raise KeyError(f"unknown Cell artifact: {artifact_ref}") from exc

    def read_bytes(self, artifact_ref: str, artifact_sha256: str) -> bytes:
        """Resolve bytes only when the caller supplies the exact content hash."""

        resolved = self.resolve_artifact_ref(artifact_ref)
        if resolved.artifact_sha256 != artifact_sha256:
            raise PermissionError("Cell artifact hash mismatch")
        return self.read_artifact_bytes(artifact_ref)

    def artifact(self, artifact_ref: str) -> StoredCellArtifact:
        """Return complete local metadata for Runtime input assembly."""

        with self._lock:
            try:
                return self._artifacts_by_ref[artifact_ref]
            except KeyError as exc:
                raise KeyError(f"unknown Cell artifact: {artifact_ref}") from exc

    def record_execution_output(
        self,
        request: ExecutionOutputRegistrationRequest,
        content: bytes,
    ) -> ExecutionOutputRegistrationResult:
        """Commit a deterministic or domain output behind a Runtime ref."""

        request.validate()
        schema_ref = f"schema:{request.output_type_id}@{request.schema_version}"
        schema_sha256 = _sha256(schema_ref.encode("utf-8"))
        resolved = self.put_bytes(
            artifact_kind_id=request.output_type_id,
            schema_version=request.schema_version,
            schema_ref=schema_ref,
            schema_sha256=schema_sha256,
            media_type=request.media_type,
            content=content,
            idempotency_key=request.idempotency_key,
            logical_name=request.logical_name,
        )
        result = ExecutionOutputRegistrationResult(
            execution_output_id=f"execution_output_{resolved.artifact_id}",
            execution_output_ref=resolved.artifact_ref,
            execution_output_sha256=resolved.artifact_sha256,
            output_resolution_ref=None,
        )
        result.validate()
        return result

    def commit_output(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        logical_name: str,
        content: bytes,
        schema_ref: str,
        schema_sha256: str,
        media_type: str,
    ) -> ModuleOutputBinding:
        """Commit one provider output under exact Module lineage."""

        artifact_kind_id = self._kind_by_schema.get(
            schema_ref,
            "module_output",
        )
        resolved = self.put_bytes(
            artifact_kind_id=artifact_kind_id,
            schema_version="module_output_v1",
            schema_ref=schema_ref,
            schema_sha256=schema_sha256,
            media_type=media_type,
            content=content,
            idempotency_key=_stable_id(
                "module_output_commit",
                module_run_id,
                variant_id,
                attempt_id,
                logical_name,
            ),
            logical_name=logical_name,
        )
        output = ModuleOutputBinding(
            logical_name=logical_name,
            output_ref=resolved.artifact_ref,
            output_sha256=resolved.artifact_sha256,
            schema_ref=schema_ref,
            schema_sha256=schema_sha256,
            media_type=media_type,
        )
        output.validate()
        return output

    def commit_failure_detail(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        failure_class: str,
        content: bytes,
        media_type: str,
    ) -> ModuleFailureDetailBinding:
        """Commit one bounded diagnostic outside the Module output contract."""

        schema_ref = "schema:module_execution_failure_detail@v1"
        resolved = self.put_bytes(
            artifact_kind_id="module_execution_failure_detail",
            schema_version="module_execution_failure_detail_v1",
            schema_ref=schema_ref,
            schema_sha256=_sha256(schema_ref.encode("utf-8")),
            media_type=media_type,
            content=content,
            idempotency_key=_stable_id(
                "module_failure_commit",
                module_run_id,
                variant_id,
                attempt_id,
                failure_class,
            ),
            logical_name="failure_detail",
        )
        detail = ModuleFailureDetailBinding(
            detail_ref=resolved.artifact_ref,
            detail_sha256=resolved.artifact_sha256,
            media_type=media_type,
        )
        detail.validate()
        return detail

    def commit_attempt_trace(
        self,
        *,
        module_run_id: str,
        variant_id: str,
        attempt_id: str,
        content: bytes,
        media_type: str,
    ) -> tuple[str, str]:
        """Commit one bounded Cell-local provider trace for an Attempt."""

        schema_ref = "schema:module_attempt_trace@v1"
        resolved = self.put_bytes(
            artifact_kind_id="module_attempt_trace",
            schema_version="module_attempt_trace_v1",
            schema_ref=schema_ref,
            schema_sha256=_sha256(schema_ref.encode("utf-8")),
            media_type=media_type,
            content=content,
            idempotency_key=_stable_id(
                "module_attempt_trace_commit",
                module_run_id,
                variant_id,
                attempt_id,
            ),
            logical_name="attempt_trace",
        )
        return (resolved.artifact_ref, resolved.artifact_sha256)


__all__ = [
    "InMemoryCellArtifactStore",
    "StoredCellArtifact",
]
