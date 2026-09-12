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
    from .registry_plugin_registration import register_reviewer
    parser = argparse.ArgumentParser(description=__doc__, epilog="Root-based commands first ensure lightweight local Runtime setup; ready environments are not rewritten.")
    commands = parser.add_subparsers(dest="command", required=True)
    reviewer = commands.add_parser("register-reviewer", help="register approved source using Runtime Reviewer defaults",
        description=inspect.getdoc(register_reviewer).split("\n\nArgs:")[0],
        epilog=inspect.getdoc(main), formatter_class=argparse.RawDescriptionHelpFormatter)
    reviewer.add_argument("--root", required=True, type=Path, help="host root; writes .runtime/module and .runtime/workflow")
    reviewer.add_argument("--source-root", type=Path, help="explicit source root; defaults to --root")
    reviewer.add_argument("--skill-id", required=True, help="exact kebab-case Skill identity")
    reviewer.add_argument("--module-id", required=True, help="exact snake_case Reviewer identity")
    reviewer.add_argument("--version", required=True, help="approved Module/Workflow definition version")
    register = commands.add_parser("register", help="validate/register a bundle and save its objects")
    register.add_argument("--root", required=True)
    register.add_argument("--bundle", required=True, type=Path)
    register.add_argument("--plugin-id", required=True)
    register.add_argument("--plugin-version", required=True)
    load = commands.add_parser("load", help="load exact version or latest registered version")
    load.add_argument("--root", required=True, help="host root containing .runtime")
    load.add_argument("--kind", choices=("module", "workflow"), required=True)
    load.add_argument("--id", required=True)
    load.add_argument("--version", help="exact version; omitted means latest successfully registered new version")
    return parser


def main(argv=None) -> int:
    """Register or load through the public APIs with stable CLI exit codes.

    Exit 0 means the requested operation completed; register-reviewer includes
    saved-result readback. Exit 1 means operation failure, with error_type,
    optional native error_code and detail on stderr. Exit 2 is argparse's
    command/argument usage error and includes usage on stderr. Success JSON
    goes to stdout. Failure does not imply that no writes occurred: inspect
    saved facts before repeating the same registration after an I/O failure.
    No code represents a Reviewer verdict; registration does not run a model.
    Root-based commands first perform lightweight Runtime setup, filling missing
    .runtime setup metadata and bundled operator Skills. Ready setup does not
    rewrite files. Loading still leaves registered definitions unchanged.
    """
    args = build_parser().parse_args(argv)
    try:
        from ..foundation.foundation_environment_setup import setup_runtime
        setup_runtime(Path(args.root))
        return _run_command(args)
    except (ValueError, OSError, KeyError) as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


def _run_command(args) -> int:
    if args.command == "register-reviewer":
        from .registry_plugin_registration import register_reviewer
        result = register_reviewer(args.root, source_root=args.source_root, skill_id=args.skill_id,
            module_id=args.module_id, module_version=args.version)
        bundle = result.submitted_bundle
        print(json.dumps({
            "modules": [record.as_dict() for record in bundle.modules],
            "workflows": [record.as_dict() for record in bundle.workflows],
            "execution_profiles": [record.as_dict() for record in bundle.execution_profiles],
            "execution_variants": [record.as_dict() for record in bundle.execution_variant_policies],
            "root": str(args.root.resolve()), "readback": "verified",
            "files": [str((_directory(args.root, kind, getattr(record, kind + "_id")) /
                           (quote(getattr(record, kind + "_version"), safe="") + ".json")).resolve())
                      for kind, records in (("module", bundle.modules), ("workflow", bundle.workflows))
                      for record in records],
        }, ensure_ascii=False, sort_keys=True))
    elif args.command == "register":
        from .registry_plugin_registration import RuntimeModulePlugin, register_runtime_module_plugin
        bundle = RuntimeReleaseBundle.from_dict(json.loads(args.bundle.read_text(encoding="utf-8")))
        registry = _restore_registry(args.root)
        result = register_runtime_module_plugin(registry,
            RuntimeModulePlugin(args.plugin_id, args.plugin_version, bundle), root=Path(args.root))
        print(json.dumps({"modules": [r.release_ref for r in result.submitted_bundle.modules],
                          "workflows": [r.release_ref for r in result.submitted_bundle.workflows]}))
    else:
        saved = load_runtime_registration(Path(args.root), args.kind, args.id, args.version)
        print(json.dumps({"release": saved.release.as_dict(), "bundle": _bundle(saved.registry.snapshot()).as_dict()},
                         ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
