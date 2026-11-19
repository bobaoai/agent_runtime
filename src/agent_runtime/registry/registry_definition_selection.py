"""Code-written selection of one exact registered single-node Workflow for execution.

A selection is the handoff between registration (or an explicit choice of an
existing object) and execution. Code writes it from an actual registration
result or an exact readback, so nobody retypes a Workflow ID, version, ref or
hash. It is a reference, not a copy of a definition: each run reads the
authoritative store again (root/.runtime, or a PostgreSQL release store named
in the root's configuration) through the existing exact ref/hash lookup, so a
later registered version never replaces the selected one.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
import os
from pathlib import Path
from typing import Any, ClassVar

from ..contracts.registry_release_definition import WorkflowNodeKind, WorkflowRelease
from ..foundation.foundation_contract_validation import validate_snake_case_name


DEFINITION_SELECTION_SCHEMA_VERSION = "runtime_definition_selection_v1"


@dataclass(frozen=True)
class RuntimeDefinitionSelection:
    """One exact single-node Workflow and its Module, in the store that holds them.

    Fields:
        root: Absolute host root the selection was made for. It locates setup,
            configuration and execution parameter files; for a local selection
            it is also the definition store (root/.runtime).
        release_store: None for the root's local definitions, otherwise the
            name of a PostgreSQL release store configured in
            root/.runtime/config.json release_stores. Each run resolves the name
            through the current configuration and reads the exact refs and
            hashes there; the DSN itself is never recorded.
        workflow_id, workflow_version, workflow_release_ref,
        workflow_release_sha256: The exact registered Workflow.
        module_id, module_version, module_release_ref, module_release_sha256:
            The Workflow's single Module node as registered.

    Create it with selection_from_registration or select_registered_workflow
    and pass it (or the file write_definition_selection wrote) to the run entry.
    """

    record_type: ClassVar[str] = "runtime_definition_selection"

    root: str
    release_store: str | None
    workflow_id: str
    workflow_version: str
    workflow_release_ref: str
    workflow_release_sha256: str
    module_id: str
    module_version: str
    module_release_ref: str
    module_release_sha256: str

    def as_dict(self) -> dict:
        """JSON form written by write_definition_selection."""
        return {"schema_version": DEFINITION_SELECTION_SCHEMA_VERSION,
                **{field.name: getattr(self, field.name) for field in fields(self)}}

    @classmethod
    def from_dict(cls, document: Any) -> "RuntimeDefinitionSelection":
        """Read the JSON form back; another document shape is refused."""
        names = {field.name for field in fields(cls)}
        if (type(document) is not dict or set(document) != names | {"schema_version"}
                or document["schema_version"] != DEFINITION_SELECTION_SCHEMA_VERSION):
            raise ValueError("not a runtime_definition_selection_v1 document")
        if any(type(document[name]) is not str for name in names - {"release_store"}) or not (
                document["release_store"] is None or type(document["release_store"]) is str):
            raise ValueError("definition selection fields must be strings")
        return cls(**{name: document[name] for name in names})


def configured_release_store(root: Path, name: str) -> dict:
    """Return {database_url_env, schema} for a release store named in root/.runtime/config.json.

    Raises ValueError when the name is not configured; no other store, default
    schema or environment variable is guessed. Reads configuration only.
    """
    from ..foundation.foundation_environment_setup import load_runtime_config
    stores = load_runtime_config(Path(root))["release_stores"]
    if name not in stores:
        raise ValueError(f"release store {name!r} is not configured in root/.runtime/config.json release_stores "
                         f"(configured: {sorted(stores) or 'none'})")
    return dict(stores[name])


def open_configured_release_store(root: Path, name: str):
    """Open the PostgreSQL release store that root's configuration names, reading its DSN variable.

    Returns a PostgresRuntimeReleaseStore. The DSN is read from the configured
    environment variable only and is not returned or recorded. Raises
    ValueError for an unconfigured name or a missing/empty variable. Opening
    performs no query; the caller reads only what it needs.
    """
    from .registry_postgres_persistence import PostgresRuntimeReleaseStore
    locator = configured_release_store(root, name)
    database_url = os.environ.get(locator["database_url_env"])
    if not database_url:
        raise ValueError(f"Environment variable {locator['database_url_env']} for release store {name!r} is "
                         "missing or empty; no DSN fallback")
    return PostgresRuntimeReleaseStore.from_dsn(database_url, schema=locator["schema"])


def _single_module(workflow, registry):
    if (type(workflow) is not WorkflowRelease or len(workflow.nodes) != 1
            or workflow.nodes[0].node_kind is not WorkflowNodeKind.MODULE
            or workflow.initial_node_id != workflow.nodes[0].node_id):
        raise ValueError("a definition selection requires a single-node Module Workflow")
    node = workflow.nodes[0]
    return registry.get_module(node.module_release_ref, node.module_release_sha256)


def _build(root, release_store, workflow, module) -> RuntimeDefinitionSelection:
    return RuntimeDefinitionSelection(
        root=str(Path(root).resolve()), release_store=release_store,
        workflow_id=workflow.workflow_id, workflow_version=workflow.workflow_version,
        workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        module_id=module.module_id, module_version=module.module_version,
        module_release_ref=module.release_ref, module_release_sha256=module.release_sha256)


def selection_from_registration(result, *, root: Path, release_store: str | None = None) -> RuntimeDefinitionSelection:
    """Select the single Workflow a registration just submitted, from an exact readback.

    Args:
        result: The RuntimeReleaseRegistrationResult returned by a source
            registration entry (such as register_module) or by
            register_runtime_module_plugin.
            Its submitted bundle must contain exactly one single-node Workflow.
        root: The host root. For local registration it is the root the result
            was saved under; for PostgreSQL it holds the store configuration.
        release_store: None for root/.runtime, or the configured name of the
            PostgreSQL release store the result was registered into.
    Returns:
        RuntimeDefinitionSelection read back from that store.
    Raises:
        ValueError: The bundle has no or several Workflows, or the readback
            differs from the registration result.
        FileNotFoundError / KeyError: The store lacks the registered target.
    Effects:
        Reads the selected store. Registers, writes and runs nothing.
    """
    workflows = result.submitted_bundle.workflows
    if len(workflows) != 1:
        raise ValueError(f"the registration submitted {len(workflows)} Workflows; a selection needs exactly one. "
                         "Choose an existing Workflow explicitly with select_registered_workflow")
    (submitted,) = workflows
    selection = select_registered_workflow(root, submitted.workflow_id, version=submitted.workflow_version,
                                           release_store=release_store)
    if (selection.workflow_release_ref, selection.workflow_release_sha256) != (submitted.release_ref,
                                                                               submitted.release_sha256):
        raise ValueError("the store's readback differs from the registration result")
    return selection


def select_registered_workflow(root: Path, workflow_id: str, *, version: str,
                               release_store: str | None = None) -> RuntimeDefinitionSelection:
    """Select an existing registered Workflow by its explicit ID and exact version.

    Args:
        root: Host root containing .runtime (local) or the store configuration.
        workflow_id: Registered Workflow ID chosen by the caller.
        version: Exact version; required, so no latest version is chosen here.
        release_store: None for root/.runtime, or a configured PostgreSQL
            release store name (root/.runtime/config.json release_stores).
    Returns:
        RuntimeDefinitionSelection with the refs and hashes read from the store.
    Raises:
        FileNotFoundError: No such Workflow version.
        ValueError: Not a single-node Workflow, or an unconfigured store or
            missing DSN variable.
    Effects:
        Reads root/.runtime or the configured store only. Registers nothing.
    """
    from .registry_local_persistence import load_runtime_registration
    validate_snake_case_name("workflow_id", workflow_id)
    if type(version) is not str or not version:
        raise ValueError("version must name the exact registered version")
    if release_store is None:
        saved = load_runtime_registration(Path(root), "workflow", workflow_id, version)
        return _build(root, None, saved.release, _single_module(saved.release, saved.registry))
    registry = open_configured_release_store(root, release_store).load_release_registry()
    matches = [record for record in registry.snapshot().workflows
               if (record.workflow_id, record.workflow_version) == (workflow_id, version)]
    if not matches:
        raise FileNotFoundError(f"release store {release_store!r} has no Workflow {workflow_id} version {version}")
    return _build(root, release_store, matches[0], _single_module(matches[0], registry))


def write_definition_selection(path: Path, selection: RuntimeDefinitionSelection) -> Path:
    """Write the selection as UTF-8 JSON to a new file (exclusive create; the folder must exist).

    Returns the resolved path. An existing file is never overwritten.
    """
    body = json.dumps(selection.as_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path = Path(path)
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(body)
    return path.resolve()


def read_definition_selection(path: Path) -> RuntimeDefinitionSelection:
    """Read a selection file written by write_definition_selection (UTF-8, with or without BOM).

    Raises FileNotFoundError for a missing file and ValueError for a file that
    is not a selection.
    """
    try:
        document = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("definition selection must be UTF-8 JSON") from exc
    return RuntimeDefinitionSelection.from_dict(document)


def resolve_definition_selection(selection: RuntimeDefinitionSelection, *, root: Path, release_store=None):
    """Read exactly the selected definition from its store for one execution.

    Args:
        selection: A RuntimeDefinitionSelection created by code.
        root: This call's host root; it must be the root the selection names.
        release_store: For a PostgreSQL selection, an already opened store for
            its configured name; None opens it here.
    Returns:
        LoadedRuntimeRegistration of the exact selected Workflow and a Registry
        holding its Module.
    Raises:
        ValueError: Another root, or the store's record under the selected
            version is not the selected ref and hash.
        FileNotFoundError / KeyError: The selected record is absent.
    Effects:
        Reads the selected store only; never chooses a latest or other version.
    """
    from .registry_local_persistence import LoadedRuntimeRegistration, load_runtime_registration
    current = str(Path(root).resolve())
    if os.path.normcase(os.path.normpath(current)) != os.path.normcase(os.path.normpath(selection.root)):
        raise ValueError(f"the selection was made for root {selection.root}, not {current}; "
                         "select or register under this root instead")
    if selection.release_store is None:
        saved = load_runtime_registration(Path(root), "workflow", selection.workflow_id, selection.workflow_version)
        workflow, registry = saved.release, saved.registry
        if (workflow.release_ref, workflow.release_sha256) != (selection.workflow_release_ref,
                                                               selection.workflow_release_sha256):
            raise ValueError(f"root/.runtime holds a different Workflow {selection.workflow_id} "
                             f"{selection.workflow_version} than the one selected")
    else:
        if release_store is None:
            release_store = open_configured_release_store(root, selection.release_store)
        registry = release_store.load_release_registry()
        workflow = registry.get_workflow(selection.workflow_release_ref, selection.workflow_release_sha256)
    _single_module(workflow, registry)
    return LoadedRuntimeRegistration(workflow, registry)


__all__ = [
    "DEFINITION_SELECTION_SCHEMA_VERSION",
    "RuntimeDefinitionSelection",
    "configured_release_store",
    "open_configured_release_store",
    "read_definition_selection",
    "resolve_definition_selection",
    "select_registered_workflow",
    "selection_from_registration",
    "write_definition_selection",
]
