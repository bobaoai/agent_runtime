"""Save registered definitions under a host root and load exact versions.

This is registration-result persistence, not host configuration management.
Credentials, live authorization, adapters and execution stores remain host
inputs. Local loading never selects or changes an active pointer.
"""

from __future__ import annotations

import argparse
import inspect
from dataclasses import dataclass, fields
import json
from pathlib import Path
import sys
import tempfile
from urllib.parse import quote

from ..contracts.registry_release_definition import (
    ModuleRelease, WorkflowRelease, is_prompt_component_member_ref,
)
from ..foundation.foundation_contract_validation import validate_snake_case_name
from .registry_release_registration import (
    RuntimeReleaseBundle, RuntimeReleaseRegistrationResult, RuntimeReleaseRegistry,
)


_FORMAT = "local_runtime_registration_v1"


def _bundle(snapshot) -> RuntimeReleaseBundle:
    return RuntimeReleaseBundle(**{field.name: getattr(snapshot, field.name)
                                  for field in fields(RuntimeReleaseBundle)})


def _matching_variants(variants, target):
    origin = "workflow" if type(target) is WorkflowRelease else "standalone_module"
    return tuple(v for v in variants if (
        v.policy_document()["origin_kind"], v.policy_document()["origin_release_ref"],
        v.policy_document()["origin_release_sha256"]) == (origin, target.release_ref, target.release_sha256))


def _closure(registry, target, supplied_variants=None) -> RuntimeReleaseBundle:
    records = {field.name: {} for field in fields(RuntimeReleaseBundle)}

    def add(family, record):
        records[family][record.release_ref] = record
        return record

    def schema(ref, digest):
        add("schema_assets", registry.get_schema_asset(ref, digest))

    def policy(family, record):
        add(family, record)
        schema(record.policy_schema_ref, record.policy_schema_sha256)

    def module(record):
        add("modules", record)
        schema(record.input_schema_ref, record.input_schema_sha256)
        schema(record.output_schema_ref, record.output_schema_sha256)
        for family, get, ref, digest in (
            ("behavior_policies", registry.get_behavior_policy, record.behavior_policy_ref, record.behavior_policy_sha256),
            ("evaluation_policies", registry.get_evaluation_policy, record.evaluation_policy_ref, record.evaluation_policy_sha256),
            ("retry_policies", registry.get_retry_policy, record.retry_policy_ref, record.retry_policy_sha256),
        ):
            policy(family, get(ref, digest))
        if record.prompt_bundle_ref is not None:
            prompt = add("prompt_bundles", registry.get_prompt_bundle(record.prompt_bundle_ref, record.prompt_bundle_sha256))
            for member in prompt.members:
                if is_prompt_component_member_ref(member.member_ref):
                    component = add("prompt_components", registry.get_prompt_component(member.member_ref, member.member_sha256))
                    for source in component.source_members:
                        if source.member_ref.startswith("schema:"):
                            schema(source.member_ref, source.member_sha256)

    if type(target) is WorkflowRelease:
        add("workflows", target)
        for node in target.nodes:
            if node.module_release_ref is not None:
                module(registry.get_module(node.module_release_ref, node.module_release_sha256))
    else:
        module(target)

    variants = _matching_variants(registry.snapshot().execution_variant_policies, target) if supplied_variants is None else supplied_variants
    for variant in variants:
        policy("execution_variant_policies", variant)
        for binding in variant.policy_document()["bindings"]:
            add("execution_profiles", registry.get_execution_profile(
                binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"]))
    result = RuntimeReleaseBundle(**{family: tuple(values.values()) for family, values in records.items()})
    RuntimeReleaseRegistry().register_bundle(result)
    return result


def _directory(root, kind, subject_id):
    if kind not in {"module", "workflow"}:
        raise ValueError("kind must be module or workflow")
    validate_snake_case_name("subject_id", subject_id)
    return Path(root) / ".runtime" / kind / subject_id


def _read(path, kind, subject_id, version=None):
    document = json.loads(path.read_text(encoding="utf-8"))
    if (document["schema_version"] != _FORMAT or document["kind"] != kind
            or document["subject_id"] != subject_id
            or (version is not None and document["version"] != version)
            or type(document["registration_order"]) is not int or document["registration_order"] < 1):
        raise ValueError("saved registration identity or order is invalid")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle.from_dict(document["bundle"]))
    get = registry.get_module if kind == "module" else registry.get_workflow
    target = get(document["release_ref"], document["release_sha256"])
    if (getattr(target, kind + "_id"), getattr(target, kind + "_version")) != (subject_id, document["version"]):
        raise ValueError("saved target differs from requested identity")
    return document, target, registry


def _restore_registry(root):
    registry = RuntimeReleaseRegistry()
    for kind in ("module", "workflow"):
        for path in (Path(root) / ".runtime" / kind).glob("*/*.json"):
            existing = _read(path, kind, path.parent.name)
            registry.register_bundle(_bundle(existing[2].snapshot()))
    return registry


@dataclass(frozen=True)
class LoadedRuntimeRegistration:
    """An exact saved definition and its usable in-memory release closure.

    This contains no live host resources or new registration authority. Pass
    its records to the existing execution APIs; loading does not compile source
    or mutate any persistent Registry. Runtime validates the saved ref/hash and
    dependencies while restoring the in-memory lookup structure.
    """

    release: ModuleRelease | WorkflowRelease
    registry: RuntimeReleaseRegistry


def save_runtime_registration(root: Path, registration: RuntimeReleaseRegistrationResult) -> tuple[Path, ...]:
    """Save the actual registered Module/Workflow results under root/.runtime.

    Args:
        root: Host-provided root. Missing module/workflow/id directories are
            created; existing directories are used directly. No host config,
            credential, active pointer or resource session is managed here.
        registration: Successful result returned by the existing Registry.
            Saves only submitted objects and their exact dependency closures,
            including registered execution bindings. A one-node Workflow is
            always saved under workflow, independently of its Module records.
    Returns:
        Version file paths. Each object has its own folder and each version its
        own JSON file. Workflow snapshots include fixed Module dependencies.
    Raises:
        ValueError: Existing definition/version conflicts, or original Registry
            validation failure. OSError: Native directory/file errors. A file
            failure does not roll back an already committed PG registration;
            retry the same registration rather than inventing another version.
    Effects:
        Writes registered data only. The host serializes registration of the
        same object; no distributed file-lock service is provided. New versions
        receive a local increasing registration_order; repeated definitions
        retain their order. Explicitly registered binding updates do not change
        definition identity or make an old definition the latest version.
    """
    registration.validate()
    registry = _restore_registry(root)
    registry.register_bundle(_bundle(registration.catalog_snapshot))
    written = []
    for kind, targets in (("module", registration.submitted_bundle.modules),
                          ("workflow", registration.submitted_bundle.workflows)):
        for target in targets:
            subject_id, version = getattr(target, kind + "_id"), getattr(target, kind + "_version")
            folder = _directory(root, kind, subject_id)
            path = folder / (quote(version, safe="") + ".json")
            explicit = _matching_variants(registration.submitted_bundle.execution_variant_policies, target)
            closure = _closure(registry, target, explicit or None)
            prior = _read(path, kind, subject_id, version) if path.exists() else None
            if prior:
                document, old_target, old_registry = prior
                if old_target != target:
                    raise ValueError("local definition version has conflicting content")
                retained = old_registry.snapshot().execution_variant_policies
                # Check every immutable dependency before replacing the file,
                # even when this request explicitly supplies a new binding.
                old_registry.register_bundle(closure)
                closure = _closure(old_registry, target, explicit if explicit else retained)
                order = document["registration_order"]
            else:
                orders = [_read(p, kind, subject_id)[0]["registration_order"] for p in folder.glob("*.json")]
                order = max(orders, default=0) + 1
            document = {"schema_version": _FORMAT, "kind": kind, "subject_id": subject_id,
                        "version": version, "registration_order": order, "release_ref": target.release_ref,
                        "release_sha256": target.release_sha256, "bundle": closure.as_dict()}
            if not prior or document != prior[0]:
                folder.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, delete=False) as stream:
                        temporary = Path(stream.name)
                        json.dump(document, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
                        stream.write("\n")
                    temporary.replace(path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            written.append(path)
    return tuple(written)


def load_runtime_registration(root: Path, kind: str, subject_id: str,
                              version: str | None = None) -> LoadedRuntimeRegistration:
    """Load a specified version, or the most recently registered new version.

    Args:
        root: The same host root used for registration.
        kind: module or workflow; node count never changes this classification.
        subject_id: Registered object ID.
        version: Exact existing version label; None selects maximum stored
            registration_order, not file mtime and not an active/latest pointer.
    Returns:
        The exact release and validated dependency Registry. No source files,
        model choice, policy construction or persistent re-registration needed.
    Raises:
        FileNotFoundError: No saved object or requested version. ValueError:
            Bad JSON/identity/hash/dependencies/order. OSError: Native I/O error.
        Errors are reported, never hidden by choosing another version or root.
    Effects:
        Reads local files only; latest is resolved once before exact execution.
    """
    folder = _directory(root, kind, subject_id)
    if version is not None:
        row = _read(folder / (quote(version, safe="") + ".json"), kind, subject_id, version)
    else:
        rows = [_read(path, kind, subject_id) for path in folder.glob("*.json")]
        if not rows:
            raise FileNotFoundError(f"No registered {kind}: {subject_id}")
        row = max(rows, key=lambda item: item[0]["registration_order"])
    return LoadedRuntimeRegistration(row[1], row[2])


def build_parser() -> argparse.ArgumentParser:
    """Return the installed CLI parser; help is also used by the API renderer."""
    from .registry_module_authoring import MODULE_REQUIREMENT_PRESETS
    from .registry_plugin_registration import register_module, register_reviewer
    parser = argparse.ArgumentParser(description=__doc__, epilog="Root-based commands first ensure lightweight local Runtime setup; ready environments are not rewritten.")
    commands = parser.add_subparsers(dest="command", required=True)

    reviewer = commands.add_parser("register-reviewer", help="register approved source using Runtime Reviewer defaults",
        description=inspect.getdoc(register_reviewer).split("\n\nArgs:")[0],
        epilog=inspect.getdoc(main), formatter_class=argparse.RawDescriptionHelpFormatter)
    reviewer.add_argument("--root", required=True, type=Path, help="host root; writes .runtime/module and .runtime/workflow")
    reviewer.add_argument("--source", type=Path, help="the approved Module source directory "
                          "(.claude/skills/<skill>/runtime_modules/<module>) or its module_registration.json; the Skill "
                          "and Module identities are read from it. Excludes --source-root, --skill-id and --module-id")
    reviewer.add_argument("--source-root", type=Path, help="lower-level form: explicit source root; defaults to --root")
    reviewer.add_argument("--skill-id", help="lower-level form: exact kebab-case Skill identity, with --module-id")
    reviewer.add_argument("--module-id", help="lower-level form: exact snake_case Reviewer identity, with --skill-id")
    reviewer.add_argument("--workflow-id", help="explicit Workflow name; omitted means the same name as the Module; kinds remain distinct")
    reviewer.add_argument("--version", required=True, help="approved Module/Workflow definition version")
    reviewer.add_argument("--selection-out", type=Path, help="new file (its folder must exist) that receives the "
                        "code-written definition selection for agent-runtime-test-run --selection; never overwritten. "
                        "The same selection is printed under 'selection'.")
    module = commands.add_parser("register-module", help="register an ordinary approved Module source with a requirements preset",
        description=inspect.getdoc(register_module).split("\n\nArgs:")[0],
        epilog=inspect.getdoc(main), formatter_class=argparse.RawDescriptionHelpFormatter)
    module.add_argument("--root", required=True, type=Path, help="host root; writes .runtime/module and .runtime/workflow")
    module.add_argument("--source", required=True, type=Path, help="the approved Module source directory "
                        "(.claude/skills/<skill>/runtime_modules/<module>) or its module_registration.json")
    module.add_argument("--preset", required=True, choices=MODULE_REQUIREMENT_PRESETS,
                        help="the owner's execution requirements preset (module_requirements_preset), fixed with the version")
    module.add_argument("--mcp-connection", action="append", default=[], dest="mcp_connections",
                        help="repeatable host MCP connection name declared by the mcp_data preset")
    module.add_argument("--max-attempts", type=int, default=1, help="the preset's max_attempts (default 1)")
    module.add_argument("--workflow-id", help="explicit Workflow name; omitted means the same name as the Module")
    module.add_argument("--version", required=True, help="approved Module/Workflow definition version")
    module.add_argument("--selection-out", type=Path, help="new file (its folder must exist) that receives the "
                        "code-written definition selection for agent-runtime-test-run --selection; never overwritten. "
                        "The same selection is printed under 'selection'.")
    register = commands.add_parser("register", help="validate/register a compiled bundle and save its objects")
    register.add_argument("--root", required=True)
    register.add_argument("--bundle", required=True, type=Path)
    register.add_argument("--plugin-id", help="owner-chosen package label, with --plugin-version. Omitted, code reads it "
                          "from the bundle's single Workflow, or its single Module when it has no Workflow. The label "
                          "is validated, not saved")
    register.add_argument("--plugin-version", help="owner-chosen package version, with --plugin-id")
    register.add_argument("--selection-out", type=Path, help="new file (its folder must exist) that receives the "
                        "code-written definition selection for agent-runtime-test-run --selection; never overwritten. "
                        "The same selection is printed under 'selection'.")
    select = commands.add_parser("select", help="select an existing registered single-node Workflow version for Test Run")
    select.add_argument("--root", required=True, type=Path, help="host root containing .runtime or the store configuration")
    select.add_argument("--workflow", required=True, help="registered Workflow ID")
    select.add_argument("--version", required=True, help="exact registered version; no latest version is chosen")
    select.add_argument("--release-store", help="name of a PostgreSQL release store configured in "
                        "root/.runtime/config.json release_stores; omitted means root/.runtime")
    select.add_argument("--selection-out", type=Path, help="new file (its folder must exist) for the selection; "
                        "omitted only prints it")
    load = commands.add_parser("load", help="load exact version or latest registered version, or the definition a selection names")
    load.add_argument("--root", required=True, help="host root containing .runtime")
    load.add_argument("--selection", type=Path, help="selection file written by a registration or select command; "
                      "returns exactly its Workflow, Module and the Module's input and output schemas from root/.runtime "
                      "or its configured release store. Excludes --kind, --id and --version")
    load.add_argument("--kind", choices=("module", "workflow"), help="lower-level form: object kind, with --id")
    load.add_argument("--id", help="lower-level form: registered object ID, with --kind")
    load.add_argument("--version", help="exact version; omitted means latest successfully registered new version")
    return parser


def main(argv=None) -> int:
    """Register, select or load through the public APIs with stable CLI exit codes.

    Exit 0 means the requested operation completed. register-reviewer,
    register-module and register include saved-result readback and print the
    code-written definition selection under 'selection' when the registration
    submitted exactly one single-node Workflow (otherwise selection is null and
    selection_unavailable says why). Exit 1 means operation failure, with
    error_type, optional native error_code and detail on stderr. Exit 2 is
    argparse's command/argument usage error and includes usage on stderr,
    including an existing --selection-out file, refused before setup or
    registration. Success JSON goes to stdout. Failure does not imply that no
    writes occurred: inspect saved facts before repeating the same registration
    after an I/O failure. No code represents a Reviewer verdict; registration
    does not run a model. Root-based commands first perform lightweight Runtime
    setup, filling missing .runtime setup metadata and bundled operator Skills.
    Ready setup does not rewrite files. Loading and selecting leave registered
    definitions unchanged. load --selection prints the selection, the exact
    Workflow release, its Module release, the Module's input_schema and
    output_schema documents and the fixed dependency bundle, so input can be
    prepared for that definition without retyping its identity.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "register-reviewer":
        exact = (args.source_root, args.skill_id, args.module_id)
        if args.source is not None and any(value is not None for value in exact):
            parser.error("give --source, or the lower-level --skill-id with --module-id (and optional --source-root), not both")
        if args.source is None and (args.skill_id is None or args.module_id is None):
            parser.error("register-reviewer needs --source, or --skill-id with --module-id")
    if args.command == "load":
        if args.selection is not None and any(value is not None for value in (args.kind, args.id, args.version)):
            parser.error("give --selection, or --kind with --id (and optional --version), not both")
        if args.selection is None and (args.kind is None or args.id is None):
            parser.error("load needs --selection, or --kind with --id")
    if args.command == "register" and (args.plugin_id is None) != (args.plugin_version is None):
        parser.error("give --plugin-id with --plugin-version, or neither to read the label from the bundle")
    target = getattr(args, "selection_out", None)
    if target is not None and (target.exists() or not target.parent.is_dir()):
        parser.error(f"--selection-out must name a new file in an existing folder: {target}")
    try:
        from ..foundation.foundation_environment_setup import setup_runtime
        setup_runtime(Path(args.root))
        return _run_command(args)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


def _with_selection(root, result, output: dict, selection_path) -> dict:
    """Add the registration's code-written selection, or why none is unambiguous."""
    from .registry_definition_selection import selection_from_registration, write_definition_selection
    try:
        selection = selection_from_registration(result, root=Path(root))
    except ValueError as exc:
        if selection_path is not None:
            raise
        return {**output, "selection": None, "selection_unavailable": str(exc)}
    output = {**output, "selection": selection.as_dict()}
    if selection_path is not None:
        output["selection_path"] = str(write_definition_selection(selection_path, selection))
    return output


def _saved_files(root, bundle) -> list[str]:
    return [str((_directory(root, kind, getattr(record, kind + "_id")) /
                 (quote(getattr(record, kind + "_version"), safe="") + ".json")).resolve())
            for kind, records in (("module", bundle.modules), ("workflow", bundle.workflows)) for record in records]


def _run_command(args) -> int:
    if args.command in {"register-reviewer", "register-module"}:
        from .registry_plugin_registration import register_module, register_reviewer
        if args.command == "register-reviewer":
            result = register_reviewer(args.root, source=args.source, source_root=args.source_root,
                skill_id=args.skill_id, module_id=args.module_id, module_version=args.version,
                workflow_id=args.workflow_id)
        else:
            from .registry_module_authoring import module_requirements_preset
            requirements = module_requirements_preset(args.preset, mcp_connections=tuple(args.mcp_connections),
                                                      max_attempts=args.max_attempts)
            result = register_module(args.root, source=args.source, module_version=args.version,
                                     execution_requirements=requirements, workflow_id=args.workflow_id)
        bundle = result.submitted_bundle
        output = {
            "modules": [record.as_dict() for record in bundle.modules],
            "workflows": [record.as_dict() for record in bundle.workflows],
            "execution_profiles": [record.as_dict() for record in bundle.execution_profiles],
            "execution_variants": [record.as_dict() for record in bundle.execution_variant_policies],
            "root": str(args.root.resolve()), "readback": "verified", "files": _saved_files(args.root, bundle),
        }
        print(json.dumps(_with_selection(args.root, result, output, args.selection_out),
                         ensure_ascii=False, sort_keys=True))
    elif args.command == "register":
        from .registry_plugin_registration import RuntimeModulePlugin, register_runtime_module_plugin
        bundle = RuntimeReleaseBundle.from_dict(json.loads(args.bundle.read_text(encoding="utf-8")))
        plugin = RuntimeModulePlugin.from_bundle(bundle, plugin_id=args.plugin_id, plugin_version=args.plugin_version)
        if args.selection_out is not None and len(bundle.workflows) != 1:
            raise ValueError(f"--selection-out needs a bundle with exactly one Workflow; this one has "
                             f"{len(bundle.workflows)}")
        registry = _restore_registry(args.root)
        result = register_runtime_module_plugin(registry, plugin, root=Path(args.root))
        for kind, records in (("module", result.submitted_bundle.modules),
                              ("workflow", result.submitted_bundle.workflows)):
            for record in records:
                saved = load_runtime_registration(Path(args.root), kind, getattr(record, kind + "_id"),
                                                  getattr(record, kind + "_version"))
                if saved.release != record:
                    raise ValueError(f"saved {kind} {record.release_ref} differs from the registered record")
        output = {"modules": [r.release_ref for r in result.submitted_bundle.modules],
                  "workflows": [r.release_ref for r in result.submitted_bundle.workflows],
                  "plugin": {"plugin_id": plugin.plugin_id, "plugin_version": plugin.plugin_version},
                  "root": str(Path(args.root).resolve()), "readback": "verified",
                  "files": _saved_files(args.root, result.submitted_bundle)}
        print(json.dumps(_with_selection(args.root, result, output, args.selection_out),
                         ensure_ascii=False, sort_keys=True))
    elif args.command == "select":
        from .registry_definition_selection import select_registered_workflow, write_definition_selection
        selection = select_registered_workflow(args.root, args.workflow, version=args.version,
                                               release_store=args.release_store)
        output = {"selection": selection.as_dict()}
        if args.selection_out is not None:
            output["selection_path"] = str(write_definition_selection(args.selection_out, selection))
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    elif args.selection is not None:
        from .registry_definition_selection import read_definition_selection, resolve_definition_selection
        selection = read_definition_selection(args.selection)
        loaded = resolve_definition_selection(selection, root=Path(args.root))
        node = loaded.release.nodes[0]
        module = loaded.registry.get_module(node.module_release_ref, node.module_release_sha256)
        schemas = {direction: json.loads(loaded.registry.get_schema_asset(
            getattr(module, direction + "_schema_ref"), getattr(module, direction + "_schema_sha256")).canonical_schema_json)
            for direction in ("input", "output")}
        print(json.dumps({"selection": selection.as_dict(), "release": loaded.release.as_dict(),
                          "module": module.as_dict(), "input_schema": schemas["input"],
                          "output_schema": schemas["output"],
                          "bundle": _closure(loaded.registry, loaded.release, supplied_variants=()).as_dict()},
                         ensure_ascii=False, sort_keys=True))
    else:
        saved = load_runtime_registration(Path(args.root), args.kind, args.id, args.version)
        print(json.dumps({"release": saved.release.as_dict(), "bundle": _bundle(saved.registry.snapshot()).as_dict()},
                         ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
