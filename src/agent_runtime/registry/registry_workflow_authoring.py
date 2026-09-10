"""Runtime-owned authoring interface for one immutable Workflow graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Self

from ..contracts.registry_release_definition import (
    WorkflowNodeKind,
    WorkflowRelease,
    WorkflowEdge,
)
from .registry_module_authoring import ModuleExport
from .registry_release_compilation import (
    WorkflowReleaseCandidate,
    WorkflowNodeReleaseCandidate,
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
    """Graph-backed authoring facade over the existing Workflow compiler."""

    candidate: WorkflowReleaseCandidate
    module_exports: tuple[ModuleExport, ...]

    @classmethod
    def for_reviewer(cls, exported: ModuleExport) -> Self:
        """Compose the fixed one-node review graph from an exact Module export.

        Workflow ID is <module_id>_review, its version is the Module version,
        and its sole node is review. The owner and operation declarations come
        from the Module source. No live authorization or model execution occurs.
        The result is a Workflow regardless of its node count.
        """
        module = exported.module_release
        workflow_id, version = module.module_id + "_review", module.module_version
        suffix = f"{workflow_id}@{version}"
        candidate = WorkflowReleaseCandidate(
            workflow_id=workflow_id, workflow_version=version, workflow_contract_version="v1",
            owner_contract_ref=exported.source.owner_contract_ref,
            owner_contract_content=exported.source.owner_contract_content,
            graph_ref="workflow-graph:" + suffix, initial_node_id="review",
            nodes=(WorkflowNodeReleaseCandidate(
                node_id="review", node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref, module_release_sha256=module.release_sha256,
                input_mapping_ref="input-mapping:" + suffix, input_mapping_document={"task_input": "payload"}),),
            edges=(WorkflowEdge("review", "complete", None, True),),
            authorization_manifest_ref="authorization-manifest:" + suffix,
            authorization_manifest_document={"module_release_ref": module.release_ref,
                "module_release_sha256": module.release_sha256, "operations": list(module.declared_operation_ids)},
            execution_binding_ref="execution-binding:" + suffix,
            execution_binding_document={"schema_version": "workflow_execution_binding_v1",
                "variant_policy_family": "execution_variant_policy", "workflow_id": workflow_id},
        )
        return cls.from_graph(candidate, module_exports=(exported,))

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
