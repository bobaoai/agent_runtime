from dataclasses import fields, replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime import (
    RuntimeModulePlugin, register_runtime_module_plugin, load_runtime_registration,
    run_local_workflow_module,
)
from agent_runtime.registry import (
    RuntimeReleaseBundle, RuntimeReleaseRegistry, compile_workflow_release,
    compile_execution_variant_policy_release, ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
)
from test_agent_runtime_registered_module_execution import _environment


def _bundle(snapshot):
    return RuntimeReleaseBundle(**{field.name: getattr(snapshot, field.name) for field in fields(RuntimeReleaseBundle)})


def _versions(tmp_path):
    env = _environment(tmp_path)
    first = _bundle(env.registry.snapshot())
    workflow = compile_workflow_release(replace(env.workflow_candidate, workflow_version="v2",
        execution_binding_ref="execution-binding:single_module@v2"))
    variant = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="single_module_profile", policy_version="v2", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate("run", env.profile.release_ref, env.profile.release_sha256),),
    ))
    second = RuntimeReleaseBundle(workflows=(workflow,), execution_variant_policies=(variant,))
    return env, first, second


def _register(registry, root, bundle, version="v1"):
    return register_runtime_module_plugin(registry, RuntimeModulePlugin("local_sample", version, bundle), root=root)


def test_register_load_latest_explicit_version_and_no_active(tmp_path):
    env, first, second = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    _register(env.registry, root, second, "v2")
    folder = root / ".runtime/workflow/single_module"
    assert {path.name for path in folder.iterdir()} == {"v1.json", "v2.json"}
    assert (root / ".runtime/module/native_module/candidate_v1.json").is_file()
    assert load_runtime_registration(root, "workflow", "single_module").release.workflow_version == "v2"
    assert load_runtime_registration(root, "workflow", "single_module", "v1").release.workflow_version == "v1"
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    _register(env.registry, root, first)
    assert {p: p.read_bytes() for p in root.rglob("*.json")} == before
    assert not list(root.rglob("*active*")) and not list(root.rglob("*latest*"))
    assert not env.registry.snapshot().active_release_refs
    loaded = load_runtime_registration(root, "workflow", "single_module", "v1")
    assert len(loaded.registry.snapshot().workflows) == 1
    assert loaded.registry.get_module(env.module.release_ref, env.module.release_sha256) == env.module
    assert loaded.registry.get_execution_profile(env.profile.release_ref, env.profile.release_sha256) == env.profile


def test_actual_local_execution_uses_saved_workflow_and_binding(tmp_path):
    env, first, second = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    _register(env.registry, root, second, "v2")
    resources = {k: v for k, v in env.kwargs.items() if k not in {"workflow", "variant_policy", "release_registry"}}
    for version in ("v1", None):
        loaded = load_runtime_registration(root, "workflow", "single_module", version)
        env.host.workflow = loaded.release
        result = run_local_workflow_module(root, "single_module", version=version,
            input_payload={"value": "from_saved"}, idempotency_key="local_" + (version or "latest"), **resources)
        assert json.loads(env.cell.read_bytes(result.outputs[0].output_ref, result.outputs[0].output_sha256)) == {"value": "done"}
    assert len(env.calls) == 2


def test_module_binding_survives_workflow_dependency_registration(tmp_path):
    env, first, _ = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    prior = load_runtime_registration(root, "module", env.module.module_id, env.module.module_version)
    # A fresh registration context may carry this same Module only as a graph dependency.
    without_module_binding = replace(first, execution_variant_policies=tuple(
        v for v in first.execution_variant_policies if v.policy_document()["origin_kind"] == "workflow"))
    _register(RuntimeReleaseRegistry(), root, without_module_binding)
    after = load_runtime_registration(root, "module", env.module.module_id, env.module.module_version)
    assert after.registry.snapshot() == prior.registry.snapshot()


def test_missing_corrupt_or_tampered_definition_is_not_replaced_with_another(tmp_path):
    env, first, _ = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    with pytest.raises(FileNotFoundError):
        load_runtime_registration(root, "workflow", "single_module", "v9")
    path = root / ".runtime/workflow/single_module/v1.json"
    data = json.loads(path.read_text())
    data["release_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_runtime_registration(root, "workflow", "single_module", "v1")
    path.write_text("bad json")
    with pytest.raises(ValueError):
        load_runtime_registration(root, "workflow", "single_module")


def test_bundle_codec_preserves_canonical_releases(tmp_path):
    _, bundle, _ = _versions(tmp_path)
    decoded = RuntimeReleaseBundle.from_dict(json.loads(json.dumps(bundle.as_dict())))
    assert decoded == bundle
    RuntimeReleaseRegistry().register_bundle(decoded)
    with pytest.raises(ValueError):
        RuntimeReleaseBundle.from_dict({"unrelated": []})


def test_file_failure_is_reported_after_registration_without_new_version(tmp_path, monkeypatch):
    env, first, _ = _versions(tmp_path / "source")
    def fail(*args, **kwargs):
        raise OSError("write failure")
    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        _register(env.registry, tmp_path / "host", first)
    assert env.registry.get_workflow(env.kwargs["workflow"].release_ref, env.kwargs["workflow"].release_sha256)


def test_cli_cold_register_and_load(tmp_path):
    env, first, second = _versions(tmp_path / "source")
    env.registry.register_bundle(second)
    combined = _bundle(env.registry.snapshot())
    root = tmp_path / "host"
    cli = [sys.executable, "-c", "from agent_runtime.registry.registry_local_persistence import main; raise SystemExit(main())"]
    process_env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    paths = []
    # Register each complete version in a separate CLI process.
    for version, bundle in (("v1", first), ("v2", combined)):
        path = tmp_path / (version + "_bundle.json")
        path.write_text(json.dumps(bundle.as_dict()))
        paths.append(path)
        subprocess.run([*cli, "register", "--root", str(root), "--bundle", str(path),
                        "--plugin-id", "local_sample", "--plugin-version", version],
                       env=process_env, cwd=tmp_path, check=True, capture_output=True, text=True)
    for extra, expected in (([], "v2"), (["--version", "v1"], "v1")):
        output = subprocess.check_output([*cli, "load", "--root", str(root), "--kind", "workflow",
                                          "--id", "single_module", *extra], env=process_env, cwd=tmp_path, text=True)
        assert json.loads(output)["release"]["workflow_version"] == expected
    # Definition input files are no longer available to the execution process.
    for path in paths:
        path.unlink()
    driver = Path(__file__).with_name("local_registration_cli.py")
    process_env["PYTHONPATH"] += os.pathsep + str(driver.parent)
    for extra, expected in (([], "v2"), (["--version", "v1"], "v1")):
        output = subprocess.check_output([sys.executable, str(driver), "--root", str(root),
                                          "--workflow", "single_module", *extra], env=process_env, cwd=tmp_path, text=True)
        actual = json.loads(output)
        assert actual["workflow_version"] == expected
        assert actual["output"] == {"value": "loaded_" + expected}
        assert actual["provider_calls"] == 1


def test_multinode_workflow_is_saved_as_workflow_not_module(tmp_path):
    from agent_runtime.contracts.registry_release_definition import WorkflowEdge
    env, _, _ = _versions(tmp_path / "source")
    node = env.workflow_candidate.nodes[0]
    graph = replace(env.workflow_candidate, workflow_id="two_nodes", workflow_version="v1",
        execution_binding_ref="execution-binding:two_nodes@v1",
        execution_binding_document={"schema_version": "workflow_execution_binding_v1",
            "variant_policy_family": "execution_variant_policy", "workflow_id": "two_nodes"},
        nodes=(node, replace(node, node_id="second")),
        edges=(WorkflowEdge("run", "complete", "second", False), WorkflowEdge("second", "complete", None, True)))
    workflow = compile_workflow_release(graph)
    _register(env.registry, tmp_path / "host", RuntimeReleaseBundle(workflows=(workflow,)))
    loaded = load_runtime_registration(tmp_path / "host", "workflow", "two_nodes")
    assert len(loaded.release.nodes) == 2
    assert not (tmp_path / "host/.runtime/module/two_nodes").exists()


def test_same_definition_version_conflict_preserves_saved_file(tmp_path):
    env, first, _ = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    path = root / ".runtime/workflow/single_module/v1.json"
    before = path.read_bytes()
    changed = compile_workflow_release(replace(env.workflow_candidate, owner_contract_content="Changed owner"))
    fresh = RuntimeReleaseRegistry()
    fresh.register_bundle(replace(first, workflows=(), execution_variant_policies=tuple(
        v for v in first.execution_variant_policies if v.policy_document()["origin_kind"] != "workflow")))
    with pytest.raises(ValueError, match="collision"):
        _register(fresh, root, RuntimeReleaseBundle(workflows=(changed,)))
    assert path.read_bytes() == before


def test_plain_reregistration_preserves_explicitly_updated_binding(tmp_path):
    from agent_runtime import ExecutionProfileRelease
    env, first, _ = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    workflow = env.kwargs["workflow"]
    profile = ExecutionProfileRelease.build(**{field.name: getattr(env.profile, field.name)
        for field in fields(env.profile) if field.name not in {
            "release_sha256", "release_ref", "execution_profile_version", "model_id"}},
        release_ref=env.profile.release_ref.rsplit("@", 1)[0] + "@v2",
        execution_profile_version="v2", model_id="different-model")
    updated = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="single_module_profile", policy_version="v2", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate("run", profile.release_ref, profile.release_sha256),)))
    _register(env.registry, root, RuntimeReleaseBundle(workflows=(workflow,), execution_variant_policies=(updated,),
        execution_profiles=(profile,)))
    path = root / ".runtime/workflow/single_module/v1.json"
    before = path.read_bytes()
    _register(env.registry, root, RuntimeReleaseBundle(workflows=(workflow,)))
    assert path.read_bytes() == before
    saved = load_runtime_registration(root, "workflow", "single_module")
    assert saved.registry.snapshot().execution_variant_policies == (updated,)
    assert saved.registry.snapshot().execution_profiles == (profile,)


@pytest.mark.parametrize("workflow_version", ["v1", "v2"])
@pytest.mark.parametrize("entry", ["cli", "sdk"])
def test_cold_registration_rejects_conflicting_existing_profile(tmp_path, workflow_version, entry):
    from agent_runtime import ExecutionProfileRelease
    env, first, second = _versions(tmp_path / "source")
    root = tmp_path / "host"
    _register(env.registry, root, first)
    profile = ExecutionProfileRelease.build(**{field.name: getattr(env.profile, field.name)
        for field in fields(env.profile) if field.name not in {"release_sha256", "model_id"}}, model_id="different-model")
    workflow = env.kwargs["workflow"] if workflow_version == "v1" else second.workflows[0]
    variant = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="changed_profile_binding", policy_version="v1", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate("run", profile.release_ref, profile.release_sha256),)))
    changed = replace(first, workflows=(workflow,), execution_profiles=(profile,), execution_variant_policies=(variant,))
    RuntimeReleaseRegistry().register_bundle(changed)
    before = {p: p.read_bytes() for p in root.rglob("*.json")}
    path = tmp_path / "conflicting_bundle.json"
    path.write_text(json.dumps(changed.as_dict()))
    code = "from agent_runtime.registry.registry_local_persistence import main; raise SystemExit(main())" if entry == "cli" else (
        "import json,sys; from pathlib import Path; "
        "from agent_runtime import RuntimeModulePlugin,register_runtime_module_plugin; "
        "from agent_runtime.registry import RuntimeReleaseBundle,RuntimeReleaseRegistry; "
        "bundle=RuntimeReleaseBundle.from_dict(json.loads(Path(sys.argv[4]).read_text())); "
        "register_runtime_module_plugin(RuntimeReleaseRegistry(),RuntimeModulePlugin('local_sample','v2',bundle),root=Path(sys.argv[2]))")
    process_env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    arguments = ["register", "--root", str(root), "--bundle", str(path), "--plugin-id", "local_sample", "--plugin-version", "v2"]
    if entry == "sdk":
        arguments = ["--root", str(root), "--bundle", str(path)]
    process = subprocess.run([sys.executable, "-c", code, *arguments],
        cwd=tmp_path, env=process_env, capture_output=True, text=True)
    assert process.returncode != 0
    assert "collision" in process.stderr
    assert {p: p.read_bytes() for p in root.rglob("*.json")} == before
    # Rejection must leave the local catalog usable by the next fresh CLI.
    path.write_text(json.dumps(first.as_dict()))
    subprocess.run([sys.executable, "-c", "from agent_runtime.registry.registry_local_persistence import main; raise SystemExit(main())",
        "register", "--root", str(root), "--bundle", str(path), "--plugin-id", "local_sample", "--plugin-version", "v1"],
        cwd=tmp_path, env=process_env, capture_output=True, text=True, check=True)
    assert {p: p.read_bytes() for p in root.rglob("*.json")} == before
