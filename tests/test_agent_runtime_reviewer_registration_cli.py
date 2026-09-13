from dataclasses import fields, replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from agent_runtime import Module, ModuleReviewer, ReviewerDefaults, RuntimeModulePlugin, register_runtime_module_plugin, register_reviewer, load_runtime_registration, prepare_local_workflow_module
from agent_runtime.registry import (RuntimeReleaseRegistry, RuntimeReleaseBundle, compile_execution_variant_policy_release,
    load_module_registration, AgentModuleReleaseCandidate, compile_agent_module_release, runtime_owned_policy_schema_assets)
from agent_runtime.contracts.registry_release_definition import ExecutionProfileRelease
from test_agent_runtime_module_authoring import _project, _task_project, _requirements, _policies, _profile, SKILL_ID, MODULE_ID
from test_agent_runtime_postgres_release_store import postgres_release_test_schema


ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-c", "from agent_runtime.registry.registry_local_persistence import main; raise SystemExit(main())"]


def _source(tmp_path, *, version="v4"):
    """New task source by default; legacy versions are explicit decoder fixtures."""
    root = _task_project(tmp_path) if version == "v4" else _project(tmp_path)
    registration = root / ".claude/skills" / SKILL_ID / "runtime_modules" / MODULE_ID / "module_registration.json"
    document = json.loads(registration.read_text())
    document["schema_version"] = "runtime_module_registration_" + version
    if version == "v3":
        for name in ("behavior_policy_ref", "evaluation_policy_ref", "retry_policy_ref"):
            document.pop(name)
    registration.write_text(json.dumps(document))
    return root, registration


def _legacy_export(source_root, *, version="legacy_v1", defaults=None):
    """Explicit old producer fixture; never reintroduces an old production API."""
    from agent_runtime.registry.registry_module_authoring import ModuleExport
    source = load_module_registration(source_root, skill_id=SKILL_ID, module_id=MODULE_ID)
    behavior, evaluation, retry = _policies()
    candidate = AgentModuleReleaseCandidate(
        module_id=MODULE_ID, module_version=version,
        owner_contract_ref=source.owner_contract_ref, owner_contract_content=source.owner_contract_content,
        input_schema_ref=source.input_schema_ref, input_schema_document=source.input_schema_document,
        output_schema_ref=source.output_schema_ref, output_schema_document=source.output_schema_document,
        instruction_source_ref=source.instruction_source_ref, instruction_text=source.instruction_text,
        declared_operation_ids=("model_execute",),
        compatible_transport_kinds=("claude_agent_sdk", "codex_cli"),
        behavior_policy_ref=behavior.release_ref, behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref, evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref, retry_policy_sha256=retry.release_sha256,
        reviewer_defaults=defaults,
    )
    return ModuleExport(source=source, candidate=candidate,
        compiled=compile_agent_module_release(candidate), behavior_policy=behavior,
        evaluation_policy=evaluation, retry_policy=retry)


def _register(root, source, version="v1", **kwargs):
    return register_reviewer(root, source_root=source, skill_id=SKILL_ID, module_id=MODULE_ID,
                             module_version=version, **kwargs)



def _prepared_bundle(root, **kwargs):
    prepared, _ = prepare_local_workflow_module(root, MODULE_ID, **kwargs)
    return RuntimeReleaseBundle(**{f.name: getattr(prepared.registry.snapshot(), f.name)
                                  for f in fields(RuntimeReleaseBundle)})

def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _cli(root, source, version="v1", *extra):
    return subprocess.run([*CLI, "register-reviewer", "--root", str(root), "--source-root", str(source),
                           "--skill-id", SKILL_ID, "--module-id", MODULE_ID, "--version", version, *extra],
        cwd=root.parent, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        capture_output=True, text=True)


def test_source_registration_defaults_and_exact_workflow_closure(tmp_path):
    source, _ = _source(tmp_path / "source")
    original = _files(source)
    root = tmp_path / "host"
    result = _register(root, source)
    bundle = result.submitted_bundle
    module, workflow = bundle.modules[0], bundle.workflows[0]
    assert module.reviewer_defaults is None
    assert module.execution_requirements == _requirements()
    assert bundle.execution_profiles == bundle.execution_variant_policies == ()
    assert bundle.evaluation_policies[0].policy_document()["evaluation_mode"] == "none"
    assert workflow.workflow_id == MODULE_ID
    assert workflow.release_ref != module.release_ref
    assert workflow.initial_node_id == "module"
    assert workflow.nodes[0].module_release_ref == module.release_ref
    loaded = load_runtime_registration(root, "workflow", workflow.workflow_id)
    assert loaded.release == workflow
    assert not loaded.registry.snapshot().execution_profiles
    assert set(_files(root)) == {
        f".runtime/module/{MODULE_ID}/v1.json", f".runtime/workflow/{MODULE_ID}/v1.json"}
    assert _files(source) == original
    assert not result.catalog_snapshot.active_release_refs


def test_named_workflow_cli_and_both_identity_namespaces(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    process = _cli(root, source, "v1", "--workflow-id", "explicit_pipeline")
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["modules"][0]["module_id"] == MODULE_ID
    assert result["workflows"][0]["workflow_id"] == "explicit_pipeline"
    assert load_runtime_registration(root, "module", MODULE_ID, "v1").release.release_ref == result["modules"][0]["release_ref"]
    assert load_runtime_registration(root, "workflow", "explicit_pipeline", "v1").release.release_ref == result["workflows"][0]["release_ref"]
    before = _files(root)
    assert _cli(root, source, "v1", "--workflow-id", "explicit_pipeline").returncode == 0
    assert _files(root) == before
    assert _cli(root, source, "v1", "--workflow-id", "").returncode == 1
    assert _files(root) == before
    assert _cli(root, source, "v1", "--workflow-id").returncode == 2
    assert _files(root) == before


def test_existing_suffix_workflow_re_registration_preserves_original_bytes(tmp_path):
    from agent_runtime import Workflow, register_runtime_module_plugin, RuntimeModulePlugin
    from agent_runtime.contracts.registry_release_definition import WorkflowEdge

    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    reviewer = ModuleReviewer.from_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID)
    exported = reviewer.export(module_version="scope1")
    graph = reviewer.to_workflow(exported).candidate
    old_id = MODULE_ID + "_review"
    suffix = old_id + "@scope1"
    old_graph = replace(graph, workflow_id=old_id, graph_ref="workflow-graph:" + suffix,
        initial_node_id="review",
        nodes=(replace(graph.nodes[0], node_id="review", input_mapping_ref="input-mapping:" + suffix),),
        edges=(WorkflowEdge("review", "complete", None, True),),
        authorization_manifest_ref="authorization-manifest:" + suffix,
        execution_binding_ref="execution-binding:" + suffix,
        execution_binding_document={**graph.execution_binding_document, "workflow_id": old_id})
    old = Workflow.from_graph(old_graph, module_exports=(exported,)).export()
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("legacy_fixture", "scope1", old.origin_bundle), root=root)
    before = _files(root)
    assert f".runtime/workflow/{MODULE_ID}/scope1.json" not in before
    current = _register(root, source, version="scope1").submitted_bundle
    assert current.modules == old.origin_bundle.modules
    assert current.workflows[0].workflow_id == MODULE_ID
    assert current.workflows[0].nodes[0].module_release_sha256 == exported.module_release.release_sha256
    after = _files(root)
    assert all(after[path] == body for path, body in before.items())
    assert set(after) - set(before) == {f".runtime/workflow/{MODULE_ID}/scope1.json"}
    assert load_runtime_registration(root, "workflow", old_id, "scope1").release == old.workflow_release
    assert load_runtime_registration(root, "module", MODULE_ID).release == exported.module_release
    assert load_runtime_registration(root, "workflow", MODULE_ID).release == current.workflows[0]
    _register(root, source, version="scope1")
    assert _files(root) == after


def test_v2_explicit_candidate_policy_remains_candidate_only(tmp_path):
    source, _ = _source(tmp_path / "source")
    old = _legacy_export(source)
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("legacy_fixture", "v1", old.origin_bundle), root=root)
    result = _register(root, source, version="legacy_v1")
    assert result.submitted_bundle.modules == (old.module_release,)
    assert result.submitted_bundle.evaluation_policies[0].policy_document()["evaluation_mode"] == "module_candidate"
    assert result.submitted_bundle.modules[0].execution_requirements is None


def test_legacy_explicit_export_and_codec_keep_original_payload_shape(tmp_path):
    source, _ = _source(tmp_path)
    exported = _legacy_export(source)
    module, profile = exported.module_release, _profile()
    assert module.reviewer_defaults is None and "reviewer_defaults" not in module.as_dict()
    assert "execution_requirements" not in module.as_dict()
    assert profile.model_defaults_version is None and "model_defaults_version" not in profile.as_dict()
    assert type(module).from_dict(module.as_dict()).as_dict() == module.as_dict()
    assert type(profile).from_dict(profile.as_dict()).as_dict() == profile.as_dict()


def test_same_registration_retains_frozen_defaults_and_bytes(tmp_path, monkeypatch):
    from agent_runtime.registry import registry_module_authoring as authoring
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    first = _register(root, source).submitted_bundle
    before = _files(root)
    def forbidden(*args, **kwargs):
        pytest.fail("repeat registration must reuse exact definitions and dependencies")
    monkeypatch.setattr(ModuleReviewer, "_default_execution_requirements", _requirements(timeout_seconds=999))
    monkeypatch.setattr(authoring, "compile_agent_module_release", forbidden)
    monkeypatch.setattr(authoring, "runtime_owned_policy_schema_assets", forbidden)
    second = _register(root, source).submitted_bundle
    assert second.as_dict() == first.as_dict()
    assert _files(root) == before


def test_model_selection_is_separate_and_partial_override_uses_runtime_preset(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    definition = _register(root, source).submitted_bundle
    before = _files(root)
    first = _prepared_bundle(root)
    second = _prepared_bundle(root, model_id="another-claude-model")
    third = _prepared_bundle(root, reasoning_profile="high")
    assert first.modules == second.modules == definition.modules
    assert first.workflows == second.workflows == definition.workflows
    assert first.execution_profiles[0].release_ref != second.execution_profiles[0].release_ref
    assert first.execution_profiles[0].tool_policy == second.execution_profiles[0].tool_policy
    assert third.execution_profiles[0].model_id == first.execution_profiles[0].model_id
    assert third.execution_profiles[0].reasoning_profile == "high"
    assert _files(root) == before


def test_source_cli_runs_without_precompiled_bundle_and_can_load_after_source_removed(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    for version in ("v1", "v2"):
        process = _cli(root, source, version)
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result["readback"] == "verified"
        assert all(Path(path).is_file() for path in result["files"])
    before = _files(root)
    assert _cli(root, source, "v1").returncode == 0
    assert _files(root) == before
    # This is disposable test source, not an AB/production source package.
    source.rename(source.with_name("authoring_unavailable"))
    for extra, expected in (([], "v2"), (["--version", "v1"], "v1")):
        process = subprocess.run([*CLI, "load", "--root", str(root), "--kind", "workflow",
                                  "--id", MODULE_ID, *extra],
            cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            capture_output=True, text=True, check=True)
        assert json.loads(process.stdout)["release"]["workflow_version"] == expected


@pytest.mark.parametrize("fault", ["transport", "operation", "tool_named_operations", "policy"])
def test_registration_preserves_declarations_and_execution_checks_compatibility(tmp_path, fault):
    source, path = _source(tmp_path / "source")
    document = json.loads(path.read_text())
    field, value = {
        "transport": ("compatible_transport_kinds", ["claude_cli"]),
        "operation": ("declared_operation_ids", ["model_execute", "repository_read"]),
        "tool_named_operations": ("declared_operation_ids", ["model_execute", "read", "search", "shell"]),
        "policy": ("retry_policy_ref", "retry-policy:missing_owner_policy@v1"),
    }[fault]
    document[field] = value
    path.write_text(json.dumps(document))
    before = _files(source)
    root = tmp_path / "host"
    process = _cli(root, source)
    assert process.returncode == 1 and "invalid v4 shape" in process.stderr
    assert not (root / ".runtime/module").exists()
    assert not (root / ".runtime/workflow").exists()
    assert _files(source) == before


def test_source_conflict_keeps_existing_registration(tmp_path):
    source, path = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    (path.parent / "prompt.md").write_text("A different approved task instruction.\n")
    with pytest.raises(ValueError, match="collision"):
        _register(root, source)
    assert _files(root) == before


def test_v2_still_requires_its_original_policy_fields(tmp_path):
    source, path = _source(tmp_path / "source", version="v2")
    document = json.loads(path.read_text())
    document.pop("retry_policy_ref")
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="invalid v2 shape"):
        _register(tmp_path / "host", source)


def test_frozen_capabilities_and_hash_are_checked_at_registry_and_execution(tmp_path):
    from agent_runtime.execution.execution_module_invocation import _assert_admitted_test_evaluation_profile
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    bundle = _prepared_bundle(root)
    module, profile = bundle.modules[0], bundle.execution_profiles[0]
    payload = module.as_dict()
    payload["execution_requirements"]["timeout_seconds"] += 1
    with pytest.raises(ValueError, match="hash mismatch"):
        type(module).from_dict(payload).validate()
    bad_profile = ExecutionProfileRelease.build(**{f.name: getattr(profile, f.name) for f in fields(profile)
        if f.name not in {"release_sha256", "timeout_seconds"}}, timeout_seconds=profile.timeout_seconds + 1)
    with pytest.raises(ValueError, match="Module execution requirements"):
        _assert_admitted_test_evaluation_profile(module, bad_profile)
    variant = bundle.execution_variant_policies[0]
    document = variant.policy_document()
    document["bindings"][0]["execution_profile_release_sha256"] = bad_profile.release_sha256
    altered = type(variant).build(policy_id=variant.policy_id, policy_version=variant.policy_version,
        release_ref=variant.release_ref, policy_schema_ref=variant.policy_schema_ref,
        policy_schema_sha256=variant.policy_schema_sha256, policy_document=document)
    with pytest.raises(ValueError, match="Module execution requirements"):
        RuntimeReleaseRegistry().register_bundle(replace(bundle, execution_profiles=(bad_profile,),
                                                        execution_variant_policies=(altered,)))


def test_help_exposes_source_cli_and_no_active_or_profile_assembly_options():
    process = subprocess.run([*CLI, "register-reviewer", "--help"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True, check=True)
    assert "--source-root" in process.stdout and "--version" in process.stdout
    for required in ("v4", "never selects a model", "existing version", "original dependencies",
                     "Does not install software", "run a model", "Exit 0", "Exit 1", "Exit 2"):
        assert required in process.stdout
    for forbidden in ("--active", "--profile", "--bundle", "--retry-policy", "--tool", "--model-id", "--reasoning-profile"):
        assert forbidden not in process.stdout


def test_cli_exit_codes_and_output_channels(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    success = _cli(root, source)
    assert success.returncode == 0 and json.loads(success.stdout)["readback"] == "verified"
    assert success.stderr == ""
    invalid = subprocess.run([*CLI, "register-reviewer", "--root", str(root)],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert invalid.returncode == 2 and invalid.stdout == "" and "usage:" in invalid.stderr
    failed = subprocess.run([*CLI, "load", "--root", str(root), "--kind", "workflow", "--id", "missing"],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")}, capture_output=True, text=True)
    assert failed.returncode == 1 and failed.stdout == ""
    error = json.loads(failed.stderr)
    assert error["error_type"] == "FileNotFoundError" and error["error_code"] is None and error["detail"]


def test_two_reviewers_share_one_default_base_without_model_profiles(tmp_path):
    first_source, _ = _source(tmp_path / "first")
    second_source, registration = _source(tmp_path / "second")
    other_id = "another_test_reviewer"
    for path in second_source.rglob("*"):
        if path.is_file():
            path.write_text(path.read_text().replace(MODULE_ID, other_id))
    (registration.parent / "prompt.md").write_text("Review a different frozen subject and return the required typed JSON.\n")
    registration.parent.rename(registration.parent.with_name(other_id))
    root = tmp_path / "host"
    first = _register(root, first_source).submitted_bundle
    second = register_reviewer(root, source_root=second_source, skill_id=SKILL_ID,
                               module_id=other_id, module_version="v1").submitted_bundle
    assert first.modules[0].execution_requirements == second.modules[0].execution_requirements == _requirements()
    assert first.execution_profiles == second.execution_profiles == ()
    assert first.modules[0].release_ref != second.modules[0].release_ref
    assert first.workflows[0].workflow_id != second.workflows[0].workflow_id


def test_new_and_legacy_defaults_roundtrip_through_postgres(tmp_path, postgres_release_test_schema):
    from agent_runtime.registry import PostgresRuntimeReleaseStore
    store = PostgresRuntimeReleaseStore.from_dsn(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
                                                schema=postgres_release_test_schema)
    store.create_schema(installed_at_utc="2026-08-17T19:59:59Z")
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    first = _prepared_bundle(root, release_store=store)
    changed = _prepared_bundle(root, model_id="another-claude-model", release_store=store)
    legacy_source = _project(tmp_path / "legacy")
    legacy = _legacy_export(legacy_source)
    legacy_profile = _profile()
    store.register_bundle(replace(legacy.origin_bundle, execution_profiles=(legacy_profile,)))
    reopened = PostgresRuntimeReleaseStore.from_dsn(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
                                                   schema=postgres_release_test_schema).load_release_registry()
    for module in (first.modules[0], legacy.module_release):
        assert reopened.get_module(module.release_ref, module.release_sha256).as_dict() == module.as_dict()
    for profile in (first.execution_profiles[0], changed.execution_profiles[0], legacy_profile):
        assert reopened.get_execution_profile(profile.release_ref, profile.release_sha256).as_dict() == profile.as_dict()
    assert not reopened.snapshot().active_release_refs


@pytest.mark.parametrize("snapshot", [42, {**ReviewerDefaults().as_dict(), "tool_policy": None}])
def test_invalid_default_snapshot_is_a_validation_error(snapshot):
    with pytest.raises(ValueError):
        ReviewerDefaults.from_dict(snapshot)


def test_installed_console_cli_registers_source_without_checkout_import(tmp_path):
    from test_agent_runtime_packaging_boundary import _build_runtime_wheel
    wheel = _build_runtime_wheel(tmp_path / "build")
    target = tmp_path / "installed"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
                    "--disable-pip-version-check", "--target", str(target), str(wheel)],
                   cwd=tmp_path, check=True, capture_output=True, text=True)
    process_env = {**os.environ, "PYTHONPATH": str(target)}
    probe = subprocess.check_output([sys.executable, "-c", "import agent_runtime; print(agent_runtime.__file__)"],
                                    cwd=tmp_path, env=process_env, text=True).strip()
    assert Path(probe).is_relative_to(target)
    source, _ = _source(tmp_path / "authoring")
    root = tmp_path / "host"
    cli = target / "bin/agent-runtime-registry"
    result = subprocess.run([str(cli), "register-reviewer", "--root", str(root), "--source-root", str(source),
        "--skill-id", SKILL_ID, "--module-id", MODULE_ID, "--version", "v1"],
        cwd=tmp_path, env=process_env, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["readback"] == "verified"
    assert json.loads(result.stdout)["execution_profiles"] == []
    assert json.loads(result.stdout)["execution_variants"] == []
    assert json.loads(result.stdout)["modules"][0]["module_id"] == MODULE_ID
    assert json.loads(result.stdout)["workflows"][0]["workflow_id"] == MODULE_ID
    loaded = subprocess.run([str(cli), "load", "--root", str(root), "--kind", "workflow", "--id", MODULE_ID],
        cwd=tmp_path, env=process_env, check=True, capture_output=True, text=True)
    assert json.loads(loaded.stdout)["release"]["workflow_version"] == "v1"
    fixed_bytes = _files(root)
    prepared = subprocess.run([sys.executable, "-c",
        "import json,sys; from pathlib import Path; from agent_runtime import prepare_local_workflow_module; "
        "p,v=prepare_local_workflow_module(Path(sys.argv[1]),sys.argv[2],model_id='installed-model'); "
        "print(json.dumps({'model':p.registry.snapshot().execution_profiles[0].model_id,'workflow':p.release.workflow_version}))",
        str(root), MODULE_ID], cwd=tmp_path, env=process_env,
        check=True, capture_output=True, text=True)
    assert json.loads(prepared.stdout) == {"model": "installed-model", "workflow": "v1"}
    assert _files(root) == fixed_bytes

    named = subprocess.run([str(cli), "register-reviewer", "--root", str(root), "--source-root", str(source),
        "--skill-id", SKILL_ID, "--module-id", MODULE_ID, "--version", "v1", "--workflow-id", "installed_pipeline"],
        cwd=tmp_path, env=process_env, check=True, capture_output=True, text=True)
    assert json.loads(named.stdout)["modules"] == json.loads(result.stdout)["modules"]
    assert json.loads(named.stdout)["workflows"][0]["workflow_id"] == "installed_pipeline"
    for kind, identity in (("module", MODULE_ID), ("workflow", MODULE_ID), ("workflow", "installed_pipeline")):
        exact = subprocess.run([str(cli), "load", "--root", str(root), "--kind", kind, "--id", identity, "--version", "v1"],
            cwd=tmp_path, env=process_env, check=True, capture_output=True, text=True)
        assert json.loads(exact.stdout)["release"][kind + "_id"] == identity


@pytest.mark.parametrize("new_record", ["module_defaults", "model_source"])
def test_exact_pre_defaults_client_rejects_mixed_catalog(tmp_path, postgres_release_test_schema, monkeypatch, new_record):
    from agent_runtime.registry import PostgresRuntimeReleaseStore
    import test_agent_runtime_packaging_boundary as packaging

    # Exact reviewed predecessor, before either optional field existed.
    predecessor = "9edc0496db0d11087151f1247177a9d78ced5399"
    archive = subprocess.check_output(["git", "archive", predecessor, "pyproject.toml", "src/agent_runtime"], cwd=ROOT)
    old_checkout = tmp_path / "predecessor"
    old_checkout.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as contents:
        contents.extractall(old_checkout, filter="data")
    with monkeypatch.context() as scoped:
        scoped.setattr(packaging, "REPO_ROOT", old_checkout)
        scoped.setattr(packaging, "RUNTIME_ROOT", old_checkout / "src/agent_runtime")
        wheel = packaging._build_runtime_wheel(tmp_path / "old_build")
    installed = tmp_path / "old_client"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
        "--disable-pip-version-check", "--target", str(installed), str(wheel)],
        cwd=tmp_path, check=True, capture_output=True, text=True)

    store = PostgresRuntimeReleaseStore.from_dsn(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],
                                                schema=postgres_release_test_schema)
    store.create_schema(installed_at_utc="2026-08-17T19:59:59Z")
    legacy_source = _project(tmp_path / "legacy")
    legacy = _legacy_export(legacy_source)
    legacy_profile = _profile()
    store.register_bundle(legacy.origin_bundle)
    code = '''import json,os,sys,agent_runtime
from agent_runtime.registry import PostgresRuntimeReleaseStore
try:
    registry=PostgresRuntimeReleaseStore.from_dsn(os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"],schema=sys.argv[1]).load_release_registry()
except (ValueError, KeyError) as exc:
    print(json.dumps({"origin":agent_runtime.__file__,"error":str(exc)}))
    raise SystemExit(1)
print(json.dumps({"origin":agent_runtime.__file__,"modules":len(registry.snapshot().modules)}))
'''
    def read_with_old_client():
        result = subprocess.run([sys.executable, "-c", code, postgres_release_test_schema], cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(installed)}, capture_output=True, text=True)
        document = json.loads(result.stdout)
        assert Path(document["origin"]).is_relative_to(installed)
        return result, document
    healthy, facts = read_with_old_client()
    assert healthy.returncode == 0 and facts["modules"] == 1

    source, _ = _source(tmp_path / "new")
    modern = _register(tmp_path / "host", source, version="new_v1").submitted_bundle
    if new_record == "model_source":
        store.register_bundle(RuntimeReleaseBundle(execution_profiles=
            _prepared_bundle(tmp_path / "host").execution_profiles))
    else:
        store.register_bundle(modern)
    rejected, facts = read_with_old_client()
    assert rejected.returncode == 1
    assert ("hash mismatch" in facts["error"] or "compatible_transport_kinds" in facts["error"])
    assert not store.load_release_registry().snapshot().active_release_refs


def test_ordinary_module_registers_and_cold_reads_without_reviewer_or_model(tmp_path, monkeypatch):
    from agent_runtime.registry import registry_reviewer_defaults
    source = _task_project(tmp_path / "source", module_id="summarize_note")
    def forbidden(*args, **kwargs):
        pytest.fail("generic registration must not run a Reviewer source gate")
    monkeypatch.setattr(registry_reviewer_defaults, "_validate_reviewer_output_schema", forbidden)
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
                                      execution_requirements=_requirements())
    exported = module.export(module_version="v1")
    workflow = Module.to_workflow(exported).export()
    host = tmp_path / "host"
    result = register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("ordinary_fixture", "v1", workflow.origin_bundle), root=host)
    assert result.submitted_bundle.execution_profiles == result.submitted_bundle.execution_variant_policies == ()
    for kind in ("module", "workflow"):
        loaded = load_runtime_registration(host, kind, "summarize_note")
        expected = exported.module_release if kind == "module" else workflow.workflow_release
        assert loaded.release == expected
        cold = subprocess.run([*CLI, "load", "--root", str(host), "--kind", kind,
                               "--id", "summarize_note", "--version", "v1"],
                              cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
                              capture_output=True, text=True, check=True)
        assert json.loads(cold.stdout)["release"] == expected.as_dict()


@pytest.mark.parametrize("snapshot", [None, ReviewerDefaults(), ReviewerDefaults(version="v2"),
                                     ReviewerDefaults(timeout_seconds=86401)])
def test_old_module_can_gain_workflow_without_current_policy_schema_or_export(tmp_path, monkeypatch, snapshot):
    from agent_runtime.registry import registry_module_authoring as authoring
    source, _ = _source(tmp_path / "source")
    old = _legacy_export(source, defaults=snapshot)
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("legacy_fixture", "v1", old.origin_bundle), root=root)
    module_path = root / ".runtime/module" / MODULE_ID / "legacy_v1.json"
    before = module_path.read_bytes()
    loaded = load_runtime_registration(root, "module", MODULE_ID, "legacy_v1")
    original_schemas = loaded.registry.snapshot().schema_assets
    def forbidden(*args, **kwargs):
        pytest.fail("reuse must not access a current compiler or Policy Schema factory")
    monkeypatch.setattr(Module, "export", forbidden)
    monkeypatch.setattr(authoring, "compile_agent_module_release", forbidden)
    monkeypatch.setattr(authoring, "runtime_owned_policy_schema_assets", forbidden)
    result = _register(root, source, version="legacy_v1")
    assert result.submitted_bundle.modules == (old.module_release,)
    assert set(result.submitted_bundle.schema_assets) == set(original_schemas)
    assert module_path.read_bytes() == before
    workflow = result.submitted_bundle.workflows[0]
    assert workflow.workflow_id == MODULE_ID
    assert workflow.nodes[0].module_release_sha256 == old.module_release.release_sha256
    if snapshot is not None and snapshot.timeout_seconds > 86400:
        with pytest.raises(ValueError, match="86400"):
            old.module_release.get_execution_requirements()
    all_files = _files(root)
    assert _register(root, source, version="legacy_v1").submitted_bundle == result.submitted_bundle
    assert _files(root) == all_files


@pytest.mark.parametrize("failed_kind", ["module", "workflow"])
def test_registration_retries_partial_file_save_without_changing_module(tmp_path, monkeypatch, failed_kind):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    target = root / ".runtime" / failed_kind / MODULE_ID / "v1.json"
    original_replace = Path.replace
    def fail_file(path, destination):
        if Path(destination) == target:
            raise OSError("injected exact version-file failure")
        return original_replace(path, destination)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_file)
        with pytest.raises(OSError, match="injected"):
            _register(root, source)
    module_path = root / ".runtime/module" / MODULE_ID / "v1.json"
    if failed_kind == "workflow":
        assert module_path.is_file()
        before = module_path.read_bytes()
        before_document = json.loads(before)
        # Changing the software preset must not change the already saved Module.
        monkeypatch.setattr(ModuleReviewer, "_default_execution_requirements", _requirements(timeout_seconds=999))
    result = _register(root, source)
    assert load_runtime_registration(root, "workflow", MODULE_ID, "v1").release == result.submitted_bundle.workflows[0]
    if failed_kind == "workflow":
        assert module_path.read_bytes() == before
        after_document = json.loads(module_path.read_bytes())
        for key in ("release_ref", "release_sha256", "registration_order"):
            assert after_document[key] == before_document[key]
        assert result.submitted_bundle.modules[0].execution_requirements.timeout_seconds == 1200


@pytest.mark.parametrize("change", ["owner", "input_schema", "output_schema", "instruction", "skill_source"])
def test_repeated_version_detects_actual_source_changes(tmp_path, change):
    source, path = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    if change == "owner":
        owner = source / "designDoc/test_design.md"
        owner.write_text(owner.read_text() + "\nChanged authority.\n")
    elif change in {"input_schema", "output_schema"}:
        schema = path.parent / ("schemas/input.schema.json" if change == "input_schema" else "schemas/output.schema.json")
        document = json.loads(schema.read_text())
        document["description"] = "Changed schema meaning"
        schema.write_text(json.dumps(document))
    elif change == "instruction":
        (path.parent / "prompt.md").write_text("A changed task.\n")
    else:
        from agent_runtime.registry import registry_plugin_registration as registration
        # Exact id and same bytes cannot hide a different source-member ref.
        loaded = load_module_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID)
        prior = load_runtime_registration(root, "module", MODULE_ID, "v1")
        with pytest.raises(ValueError, match="collision"):
            registration._export_from_registered_source(replace(loaded, skill_id="another-skill"), prior.release, prior.registry)
        assert _files(root) == before
        return
    with pytest.raises(ValueError, match="collision"):
        _register(root, source)
    assert _files(root) == before


def test_schema_key_order_and_whitespace_preserve_existing_definition(tmp_path):
    source, path = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    for direction in ("input", "output"):
        schema = path.parent / f"schemas/{direction}.schema.json"
        document = json.loads(schema.read_text())
        schema.write_text(json.dumps(dict(reversed(list(document.items()))), indent=4) + "\n")
    _register(root, source)
    assert _files(root) == before


def test_existing_exact_single_node_graph_keeps_its_original_node_name(tmp_path):
    from agent_runtime import Workflow
    from agent_runtime.contracts.registry_release_definition import WorkflowEdge
    source, _ = _source(tmp_path / "source")
    exported = ModuleReviewer.from_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID).export(module_version="v1")
    candidate = Module.to_workflow(exported).candidate
    candidate = replace(candidate, initial_node_id="original_node",
        nodes=(replace(candidate.nodes[0], node_id="original_node"),),
        edges=(WorkflowEdge("original_node", "complete", None, True),))
    original = Workflow.from_graph(candidate, module_exports=(exported,)).export()
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("explicit_graph", "v1", original.origin_bundle), root=root)
    before = _files(root)
    result = _register(root, source)
    assert result.submitted_bundle.workflows == (original.workflow_release,)
    assert _files(root) == before


@pytest.mark.parametrize("wrong_kind", ["wrong_target", "multi_node"])
def test_existing_wrong_graph_target_is_not_overwritten(tmp_path, wrong_kind):
    from agent_runtime import Workflow
    from agent_runtime.contracts.registry_release_definition import WorkflowEdge
    source, _ = _source(tmp_path / "source")
    if wrong_kind == "wrong_target":
        other_source = _task_project(tmp_path / "other", module_id="summarize_note")
        other = Module.from_registration(other_source, skill_id=SKILL_ID, module_id="summarize_note",
            execution_requirements=_requirements()).export(module_version="v1")
        wrong = Module.to_workflow(other, workflow_id=MODULE_ID).export()
        message = "exact Module target"
    else:
        exported = ModuleReviewer.from_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID).export(module_version="v1")
        candidate = Module.to_workflow(exported).candidate
        second = replace(candidate.nodes[0], node_id="second",
                         input_mapping_ref="input-mapping:second@v1")
        candidate = replace(candidate, nodes=(*candidate.nodes, second), edges=(
            WorkflowEdge("module", "complete", "second", False),
            WorkflowEdge("second", "complete", None, True)))
        wrong = Workflow.from_graph(candidate, module_exports=(exported,)).export()
        message = "one exact Module node"
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("wrong_target", "v1", wrong.origin_bundle), root=root)
    before = _files(root)
    with pytest.raises(ValueError, match=message):
        _register(root, source)
    assert _files(root) == before
