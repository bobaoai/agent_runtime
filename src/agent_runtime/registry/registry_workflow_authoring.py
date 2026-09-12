"""Runtime-owned authoring interface for one immutable Workflow graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Self

from ..contracts.registry_release_definition import (
    WorkflowNodeKind,
    WorkflowRelease,
)
from .registry_module_authoring import ModuleExport
from .registry_release_compilation import (
    WorkflowReleaseCandidate,
    compile_workflow_release,
)
from .registry_release_registration import RuntimeReleaseBundle


WORKFLOW_MODULE_CLOSURE_INVALID = "WORKFLOW_MODULE_CLOSURE_INVALID"


class WorkflowAuthoringError(ValueError):
    """Stable Workflow authoring failure returned before Registry mutation."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


def _deduplicate_releases(records: Iterable[Any]) -> tuple[Any, ...]:
    by_ref: dict[str, Any] = {}
    for record in records:
        release_ref = record.release_ref
        prior = by_ref.get(release_ref)
        if prior is not None and prior != record:
            raise WorkflowAuthoringError(
                WORKFLOW_MODULE_CLOSURE_INVALID,
                f"one release_ref carries conflicting content: {release_ref}",
            )
        by_ref[release_ref] = record
    return tuple(by_ref[release_ref] for release_ref in sorted(by_ref))


@dataclass(frozen=True)
class WorkflowExport:
    """One Workflow Release and its target-independent origin closure."""

    candidate: WorkflowReleaseCandidate
    workflow_release: WorkflowRelease
    origin_bundle: RuntimeReleaseBundle


@dataclass(frozen=True)
class Workflow:
    """Author an independent graph over exact Module exports.

    Use from_graph for an explicitly named graph, with any admitted node count.
    Module.to_workflow supplies the common single-node construction: its default
    Workflow ID equals the Module ID and can be explicitly overridden. Module
    and Workflow identities remain distinct by kind. Graph compilation and
    dependency validation happen in export; registration and execution are
    separate operations. No Reviewer-specific construction belongs in this class.
    """

    candidate: WorkflowReleaseCandidate
    module_exports: tuple[ModuleExport, ...]

    @classmethod
    def from_graph(
        cls,
        candidate: WorkflowReleaseCandidate,
        *,
        module_exports: tuple[ModuleExport, ...],
    ) -> Self:
        """Capture an explicit graph candidate and its exact Module exports.

        Compilation and dependency matching occur in export. This constructor
        does not infer a business graph or register/execute any node.
        """
        if type(candidate) is not WorkflowReleaseCandidate:
            raise WorkflowAuthoringError(
                WORKFLOW_MODULE_CLOSURE_INVALID,
                "candidate must be an exact WorkflowReleaseCandidate",
            )
        if type(module_exports) is not tuple or any(
            type(exported) is not ModuleExport for exported in module_exports
        ):
            raise WorkflowAuthoringError(
                WORKFLOW_MODULE_CLOSURE_INVALID,
                "module_exports must contain exact ModuleExport values",
            )
        return cls(candidate=candidate, module_exports=module_exports)

    def _module_exports_by_ref(self) -> dict[str, ModuleExport]:
        by_ref: dict[str, ModuleExport] = {}
        for exported in self.module_exports:
            release = exported.module_release
            prior = by_ref.get(release.release_ref)
            if prior is not None and prior.origin_bundle != exported.origin_bundle:
                raise WorkflowAuthoringError(
                    WORKFLOW_MODULE_CLOSURE_INVALID,
                    f"one Module release ref carries conflicting closure: "
                    f"{release.release_ref}",
                )
            by_ref[release.release_ref] = exported
        return by_ref

    def _resolve_graph_exports(self) -> tuple[ModuleExport, ...]:
        by_ref = self._module_exports_by_ref()
        graph_refs: dict[str, str] = {}
        for node in self.candidate.nodes:
            if node.node_kind is not WorkflowNodeKind.MODULE:
                continue
            if node.module_release_ref is None or node.module_release_sha256 is None:
                raise WorkflowAuthoringError(
                    WORKFLOW_MODULE_CLOSURE_INVALID,
                    f"Module node lacks an exact release: {node.node_id}",
                )
            prior_hash = graph_refs.setdefault(
                node.module_release_ref,
                node.module_release_sha256,
            )
            if prior_hash != node.module_release_sha256:
                raise WorkflowAuthoringError(
                    WORKFLOW_MODULE_CLOSURE_INVALID,
                    f"one Module release ref carries multiple graph hashes: "
                    f"{node.module_release_ref}",
                )
        if set(graph_refs) != set(by_ref):
            raise WorkflowAuthoringError(
                WORKFLOW_MODULE_CLOSURE_INVALID,
                "module_exports must equal the Workflow graph Module closure",
            )
        for release_ref, release_sha256 in graph_refs.items():
            exported = by_ref[release_ref]
            if exported.module_release.release_sha256 != release_sha256:
                raise WorkflowAuthoringError(
                    WORKFLOW_MODULE_CLOSURE_INVALID,
                    f"Workflow Module hash mismatch: {release_ref}",
                )
        return tuple(by_ref[release_ref] for release_ref in sorted(graph_refs))

    def export(self) -> WorkflowExport:
        """Compile the graph and merge its exact target-independent closure."""

        module_exports = self._resolve_graph_exports()
        workflow_release = compile_workflow_release(self.candidate)
        bundles = tuple(exported.origin_bundle for exported in module_exports)
        origin_bundle = RuntimeReleaseBundle(
            schema_assets=_deduplicate_releases(
                record for bundle in bundles for record in bundle.schema_assets
            ),
            prompt_components=_deduplicate_releases(
                record for bundle in bundles for record in bundle.prompt_components
            ),
            prompt_bundles=_deduplicate_releases(
                record for bundle in bundles for record in bundle.prompt_bundles
            ),
            behavior_policies=_deduplicate_releases(
                record for bundle in bundles for record in bundle.behavior_policies
            ),
            evaluation_policies=_deduplicate_releases(
                record for bundle in bundles for record in bundle.evaluation_policies
            ),
            retry_policies=_deduplicate_releases(
                record for bundle in bundles for record in bundle.retry_policies
            ),
            modules=_deduplicate_releases(
                record for bundle in bundles for record in bundle.modules
            ),
            workflows=(workflow_release,),
        )
        return WorkflowExport(
            candidate=self.candidate,
            workflow_release=workflow_release,
            origin_bundle=origin_bundle,
        )


__all__ = [
    "WORKFLOW_MODULE_CLOSURE_INVALID",
    "Workflow",
    "WorkflowAuthoringError",
    "WorkflowExport",
]
