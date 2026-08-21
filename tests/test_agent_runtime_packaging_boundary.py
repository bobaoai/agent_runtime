from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile

import pytest

from agent_runtime.registry.registry_architecture_registration import (
    RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS,
    RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS,
)

from tools.build_agent_runtime_design_contract_bundle import (
    CANONICAL_DOCUMENTS,
    EXTERNAL_AUTHORITY_LINK_TARGETS,
    RUNTIME_OWNED_AUTHORITY,
    build_design_contract_bundle,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "src" / "agent_runtime"
DOMAIN_PACKAGE_PREFIXES = (
    "src.analysis",
    "src.digestion",
    "src.ingestion",
    "src.operation",
    "src.research",
    "src.research_evidence_thesis_workflow",
    "src.research_source_evidence_thesis_workflow",
    "src.host_business_workflow",
    "src.trade",
)
PREDECESSOR_RUNTIME_PACKAGE_PREFIXES = (
    "agent_runtime/postgres/",
    "agent_runtime/provider/",
    "agent_runtime/review/",
)


def _run_isolated_python(source: str, *args: str, cwd: Path) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-I", "-S", "-c", textwrap.dedent(source), *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _run_isolated_python_with_dependencies(
    source: str,
    *args: str,
    cwd: Path,
) -> dict[str, object]:
    """Run an isolated installed-package probe with declared extras available."""

    completed = subprocess.run(
        [sys.executable, "-I", "-c", textwrap.dedent(source), *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _build_runtime_wheel(tmp_path: Path) -> Path:
    build_root = tmp_path / "source"
    package_root = build_root / "src"
    package_root.mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "pyproject.toml", build_root / "pyproject.toml")
    shutil.copytree(
        RUNTIME_ROOT,
        package_root / "agent_runtime",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )

    wheel_root = tmp_path / "wheel"
    wheel_root.mkdir()
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from setuptools.build_meta import build_wheel; "
                "import sys; print(build_wheel(sys.argv[1]))"
            ),
            str(wheel_root),
        ],
        cwd=build_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = sorted(wheel_root.glob("*.whl"))
    assert len(wheels) == 1, completed.stdout + completed.stderr
    return wheels[0]


def test_distribution_metadata_packages_only_the_runtime_namespace() -> None:
    configuration = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["project"]["name"] == "agent-runtime-core"
    assert configuration["project"]["readme"] == "README.md"
    assert configuration["project"]["dependencies"] == ["jsonschema>=4.23"]
    assert configuration["project"]["optional-dependencies"] == {
            "claude": ["claude-agent-sdk>=0.2.128"],
            "postgres": ["psycopg[binary]>=3.2"],
            "temporal": ["temporalio>=1.31"],
            "test": [
                "build>=1.2",
                "claude-agent-sdk>=0.2.128",
                "psycopg[binary]>=3.2",
                "pytest>=8",
                "setuptools>=77",
                "temporalio>=1.31",
            ],
        }
    assert configuration["project"]["license"] == "MIT"
    assert configuration["project"]["license-files"] == ["LICENSE"]
    assert configuration["project"]["urls"] == {
        "Repository": "https://github.com/bobaoai/agent_runtime"
    }
    assert configuration["project"]["scripts"] == {
        "agent-runtime-inspect": "agent_runtime.inspection.inspection_snapshot_exporting:main",
        "agent-runtime-live-inspect": "agent_runtime.inspection.inspection_http_serving:main",
    }
    assert configuration["tool"]["setuptools"]["packages"] == [
        "agent_runtime",
        "agent_runtime.conformance",
        "agent_runtime.contracts",
        "agent_runtime.design_contract",
        "agent_runtime.durability",
        "agent_runtime.execution",
        "agent_runtime.foundation",
        "agent_runtime.inspection",
        "agent_runtime.invocation",
        "agent_runtime.ledger",
        "agent_runtime.registry",
        "agent_runtime.testing",
    ]
    assert configuration["tool"]["setuptools"]["package-dir"]["agent_runtime"] == (
        "src/agent_runtime"
    )


def test_packaged_runtime_readme_matches_distribution_readme() -> None:
    assert (RUNTIME_ROOT / "README.md").read_bytes() == (
        REPO_ROOT / "README.md"
    ).read_bytes()


def test_runtime_source_imports_only_stdlib_or_runtime_owned_modules() -> None:
    violations: list[str] = []

    for path in sorted(RUNTIME_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_modules: tuple[str, ...]
            if isinstance(node, ast.Import):
                imported_modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = (node.module,)
            else:
                continue
            for module_name in imported_modules:
                if module_name == "src" or module_name.startswith("src."):
                    violations.append(
                        f"{path.relative_to(RUNTIME_ROOT)}:{node.lineno}:{module_name}"
                    )

    assert violations == []


def test_clean_wheel_import_uses_public_namespace_without_domain_packages(
    tmp_path: Path,
) -> None:
    wheel_path = _build_runtime_wheel(tmp_path)

    with zipfile.ZipFile(wheel_path) as wheel:
        members = tuple(sorted(wheel.namelist()))
    package_members = tuple(
        member for member in members if not member.startswith("agent_runtime_core-")
    )
    assert package_members
    assert all(
        member.startswith("agent_runtime/")
        for member in package_members
    )
    assert not any(member.startswith("src/") for member in members)
    assert not any(
        member.startswith(prefix.replace(".", "/"))
        for prefix in DOMAIN_PACKAGE_PREFIXES
        for member in members
    )
    assert not any(
        member.startswith(prefix)
        for prefix in PREDECESSOR_RUNTIME_PACKAGE_PREFIXES
        for member in members
    )
    assert "agent_runtime/README.md" in members
    assert (
        "agent_runtime/inspection/inspection_snapshot_definition.schema.json"
        in members
    )
    assert "agent_runtime/design_contract/manifest.json" in members
    for source_name in CANONICAL_DOCUMENTS:
        assert f"agent_runtime/design_contract/{Path(source_name).name}" in members

    result = _run_isolated_python(
        """
        import json
        import sys
        from pathlib import Path
        from types import MappingProxyType

        sys.path.insert(0, sys.argv[1])
        import agent_runtime
        import agent_runtime.conformance as runtime_conformance
        from agent_runtime.contracts import (
            WorkflowAdmissionState,
            WorkflowRuntimeRegistration,
        )
        from agent_runtime.inspection import (
            ReleaseInventorySelection,
            build_runtime_release_inventory,
        )
        from agent_runtime.inspection.inspection_architecture_rendering import (
            build_runtime_architecture_projection,
        )
        from agent_runtime.registry.registry_plugin_registration import DomainRuntimePlugin, register_runtime_plugin
        from agent_runtime.registry.registry_workflow_registration import WorkflowRuntimeRegistry
        from agent_runtime.registry.registry_release_registration import RuntimeReleaseRegistry

        OPAQUE_GRAPH = MappingProxyType({
            "state_alpha": frozenset({"state_omega"}),
            "state_omega": frozenset(),
        })
        OPAQUE_MANIFEST = MappingProxyType({
            "manifest_version": "opaque_manifest_v1",
            "contract_version": "opaque_contract_v1",
            "state_ids": ("state_alpha", "state_omega"),
            "module_ids": ("module_delta",),
            "artifact_kind_ids": ("artifact_sigma",),
            "evaluation_binding_ids": ("evaluation_tau",),
            "execution_profile_ids": ("profile_kappa",),
        })

        class OpaqueStore:
            pass

        def opaque_driver(**_):
            return {"status": "synthetic"}

        registration = WorkflowRuntimeRegistration(
            workflow_id="workflow_zeta",
            domain="domain_zeta",
            registration_version="registration_v1",
            contract_version="opaque_contract_v1",
            intent_ref="contract-ref:opaque-intent-v1",
            graph_authority_ref="__main__:OPAQUE_GRAPH",
            admission_state=WorkflowAdmissionState.SHADOW_EXECUTABLE,
            capabilities=frozenset({"artifact_lineage"}),
            entitlement_mode="frozen_for_execution",
            domain_manifest_ref="__main__:OPAQUE_MANIFEST",
            initial_state="state_alpha",
            driver_ref="__main__:opaque_driver",
            store_ref="__main__:OpaqueStore",
            default_backend_id="temporal",
            allowed_backend_ids=("temporal",),
        )
        registry = WorkflowRuntimeRegistry()
        register_runtime_plugin(
            registry,
            DomainRuntimePlugin(
                plugin_id="plugin_zeta",
                plugin_version="plugin_v1",
                registrations=(registration,),
            ),
        )
        driver_result = registry.resolve_driver("workflow_zeta")()

        selection = ReleaseInventorySelection.build(())
        inventory = build_runtime_release_inventory(
            snapshot=RuntimeReleaseRegistry().snapshot(),
            selection=selection,
        )
        architecture = build_runtime_architecture_projection()
        domain_modules = sorted(
            name
            for name in sys.modules
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in (
                    "src.analysis",
                    "src.digestion",
                    "src.ingestion",
                    "src.operation",
                    "src.research",
                    "src.research_evidence_thesis_workflow",
                    "src.research_source_evidence_thesis_workflow",
                    "src.host_business_workflow",
                    "src.trade",
                )
            )
        )
        print(json.dumps({
            "domain_modules": domain_modules,
            "schema_version": inventory["schema_version"],
            "selection_id": inventory["selection_id"],
            "public_namespace": agent_runtime.__name__,
            "conformance_namespace": runtime_conformance.__name__,
            "driver_result": driver_result,
            "release_count": sum(
                len(inventory[field_name])
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
            ),
            "architecture_schema_version": architecture["schema_version"],
            "responsibility_ids": [
                row["responsibility_id"]
                for row in architecture["logical_responsibilities"]
            ],
            "technology_ids": sorted({
                row["technology_id"]
                for row in architecture["implementation_bindings"]
            }),
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )

    assert result == {
        "domain_modules": [],
        "schema_version": "agent_runtime_release_inventory_v4",
        "selection_id": (
            "release_inventory_selection_"
            "1c6bb3a7362cb21194eaab02"
        ),
        "public_namespace": "agent_runtime",
        "conformance_namespace": "agent_runtime.conformance",
        "driver_result": {"status": "synthetic"},
        "release_count": 0,
        "architecture_schema_version": "agent_runtime_architecture_projection_v4",
        "responsibility_ids": list(RUNTIME_REQUIRED_LOGICAL_RESPONSIBILITY_IDS),
        "technology_ids": list(RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS),
    }


def test_clean_wheel_executes_target_release_registry_module_slice(
    tmp_path: Path,
) -> None:
    """Prove the published target release model without predecessor registration."""

    wheel_path = _build_runtime_wheel(tmp_path)
    result = _run_isolated_python_with_dependencies(
        """
        import json
        import sys
        from pathlib import Path

        sys.path.insert(0, sys.argv[1])
        from agent_runtime.contracts.execution_module_definition import (
            ModuleExecutionRequest,
            ModuleVariantRequest,
        )
        from agent_runtime.contracts.invocation_adapter_definition import (
            AdapterContextResult,
            AgentExecutionAdapterDescriptor,
            AgentExecutionResult,
            OutputSubmission,
        )
        from agent_runtime.contracts.registry_release_definition import (
            ModuleExecutionPurpose,
        )
        from agent_runtime.ledger.ledger_lineage_recording import (
            InMemoryModuleExecutionLedger,
        )
        from agent_runtime.execution.execution_content_staging import (
            InMemoryCellArtifactStore,
        )
        from agent_runtime.execution.execution_module_invocation import (
            AgentExecutionAdapterRegistry,
            run_module,
        )
        from agent_runtime.registry import (
            RuntimeReleaseRegistry,
            build_runtime_release_set,
            load_runtime_authoring_inventory,
            register_runtime_release_set,
        )

        HASH = "a" * 64
        TIME = "2026-08-08T12:00:00Z"
        authoring = Path("authoring")
        module_root = authoring / "modules" / "module_opaque_test"
        (module_root / "schemas").mkdir(parents=True)
        (authoring / "contracts").mkdir()
        (authoring / "policies").mkdir()
        (authoring / "profiles").mkdir()
        (authoring / "contracts" / "opaque.md").write_text(
            "# Opaque\\n\\nRegistered Module: `module_opaque_test`.\\n",
            encoding="utf-8",
        )
        (module_root / "executable.py").write_text(
            "def run(value): return value\\n", encoding="utf-8"
        )
        for name in ("input", "output"):
            ref = f"schema:opaque_{name}@v1"
            (module_root / "schemas" / f"{name}.schema.json").write_text(
                json.dumps({
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": ref,
                    "type": "object",
                }),
                encoding="utf-8",
            )
        module_manifest = {
            "schema_version": "runtime_module_registration_v3",
            "source_kind": "non_agent_module",
            "skill_id": None,
            "skill_projection_path": None,
            "module_id": "module_opaque_test",
            "module_version": "v1",
            "module_kind": "deterministic",
            "owner_contract_ref": "contract:opaque@v1",
            "owner_contract_path": "contracts/opaque.md",
            "instruction_source_ref": None,
            "input_schema_ref": "schema:opaque_input@v1",
            "input_schema_path": "schemas/input.schema.json",
            "output_schema_ref": "schema:opaque_output@v1",
            "output_schema_path": "schemas/output.schema.json",
            "executable_ref": "python:opaque.runtime.run",
            "executable_member_path": "executable.py",
            "declared_operation_ids": [],
            "compatible_transport_kinds": ["in_process_test"],
            "behavior_policy_ref": "behavior-policy:opaque_behavior@v1",
            "evaluation_policy_ref": "evaluation-policy:opaque_evaluation@v1",
            "retry_policy_ref": "retry-policy:opaque_retry@v1",
            "entry_policy": "standalone_allowed",
            "output_resolution_policy": "direct_single",
        }
        (module_root / "module_registration.json").write_text(
            json.dumps(module_manifest), encoding="utf-8"
        )
        source_documents = {
            "behavior.json": {
                "schema_version": "runtime_behavior_policy_source_v1",
                "policy_id": "opaque_behavior",
                "policy_version": "v1",
                "context_isolation": "workflow_execution_isolated",
            },
            "evaluation.json": {
                "schema_version": "runtime_evaluation_policy_source_v1",
                "policy_id": "opaque_evaluation",
                "policy_version": "v1",
                "evaluation_mode": "none",
            },
            "retry.json": {
                "schema_version": "runtime_retry_policy_source_v1",
                "policy_id": "opaque_retry",
                "policy_version": "v1",
                "max_attempts": 1,
            },
        }
        for name, document in source_documents.items():
            (authoring / "policies" / name).write_text(
                json.dumps(document), encoding="utf-8"
            )
        profile_document = {
            "schema_version": "runtime_execution_profile_source_v1",
            "execution_profile_id": "profile_opaque_test",
            "release_version": "v1",
            "executor_adapter_id": "executor_opaque_test",
            "executor_adapter_revision": "v1",
            "transport_kind": "in_process_test",
            "provider_id": "provider_opaque",
            "model_id": "model_opaque",
            "reasoning_profile": "none",
            "execution_mode": "tool_free",
            "semantic_input_delivery_mode": "inline",
            "attempt_workspace_policy": "none",
            "gateway_access_reasons": [],
            "output_constraint_mode": "prompt_only_json",
            "tool_policy": [],
            "network_policy": "denied",
            "timeout_seconds": 60,
        }
        (authoring / "profiles" / "profile.json").write_text(
            json.dumps(profile_document), encoding="utf-8"
        )
        inventory = {
            "schema_version": "runtime_authoring_inventory_v1",
            "inventory_id": "opaque_wheel",
            "inventory_version": "v1",
            "plugin_id": "opaque_wheel",
            "plugin_version": "v1",
            "sources": [
                {"source_kind":"behavior_policy","source_id":"opaque_behavior","manifest_path":"policies/behavior.json"},
                {"source_kind":"evaluation_policy","source_id":"opaque_evaluation","manifest_path":"policies/evaluation.json"},
                {"source_kind":"execution_profile","source_id":"profile_opaque_test","manifest_path":"profiles/profile.json"},
                {"source_kind":"non_agent_module","source_id":"module_opaque_test","manifest_path":"modules/module_opaque_test/module_registration.json"},
                {"source_kind":"retry_policy","source_id":"opaque_retry","manifest_path":"policies/retry.json"},
            ],
            "root_workflow_source_ids": [],
        }
        (authoring / "inventory.json").write_text(
            json.dumps(inventory), encoding="utf-8"
        )
        source_set = load_runtime_authoring_inventory(
            authoring, "inventory.json"
        )
        build_result = build_runtime_release_set(source_set)
        release_registry = RuntimeReleaseRegistry()
        report = register_runtime_release_set(release_registry, build_result)
        module = release_registry.snapshot().modules[0]
        profile = release_registry.snapshot().execution_profiles[0]
        assert report.plugin_id == "opaque_wheel"

        opaque_store = InMemoryCellArtifactStore()

        class OpaqueAdapter:
            @property
            def descriptor(self):
                return AgentExecutionAdapterDescriptor(
                    adapter_contract_version="v1",
                    adapter_id="executor_opaque_test",
                    adapter_revision="v1",
                    provider_id="provider_opaque",
                    transport_family="in_process",
                    transport_kind="in_process_test",
                    runtime_package_id="agent_runtime_core",
                    runtime_package_version="0.0.0",
                    supported_context_modes=("stateless",),
                    supported_output_constraint_modes=("prompt_only_json",),
                    supported_read_isolation_modes=("entitled_refs",),
                    supported_execution_modes=("tool_free",),
                    supported_input_delivery_modes=("inline",),
                    supported_network_policies=("denied",),
                    supports_dynamic_operation_authorization=False,
                    admission_state="in_process_test_double",
                )

            def execute(self, request, host):
                trace_ref, trace_sha256 = opaque_store.commit_attempt_trace(
                    module_run_id=request.module_run_id,
                    variant_id=request.variant_id,
                    attempt_id=request.attempt_id,
                    content=b'{"transport": "in_process_test"}',
                    media_type="application/json",
                )
                submission = OutputSubmission(
                    output_slot_id="result",
                    local_handle="output/result.json",
                )
                host.stage_output_bytes(submission, b'{"value": "opaque"}')
                return AgentExecutionResult(
                    terminal_status="completed",
                    provider_id="provider_opaque",
                    model_id="model_opaque",
                    runtime_version="0.0.0",
                    outputs=(submission,),
                    model_operation_ref_ids=(),
                    tool_operation_ref_ids=(),
                    input_tokens=1,
                    output_tokens=1,
                    cache_read_tokens=None,
                    cache_creation_tokens=None,
                    estimated_cost_usd=None,
                    provider_charge_usd=None,
                    context=AdapterContextResult(
                        disposition_id="stateless_closed",
                        context_ref=None,
                        compatibility_sha256=HASH,
                    ),
                    failure=None,
                    cell_local_trace_ref=trace_ref,
                    cell_local_trace_sha256=trace_sha256,
                )

        adapters = AgentExecutionAdapterRegistry()
        adapters.register(OpaqueAdapter())
        request = ModuleExecutionRequest.build(
            request_id="request_opaque_test",
            purpose=ModuleExecutionPurpose.TEST,
            module_release_ref=module.release_ref,
            module_release_sha256=module.release_sha256,
            isolated_scope_ref="scope:opaque@v1",
            isolated_scope_sha256=HASH,
            input_package_ref="package:opaque@v1",
            input_package_sha256=HASH,
            inputs=(),
            variants=(
                ModuleVariantRequest(
                    arm_key="opaque",
                    replicate_index=0,
                    execution_profile_ref=profile.release_ref,
                    execution_profile_sha256=profile.release_sha256,
                    prompt_envelope_ref=None,
                    prompt_envelope_sha256=None,
                ),
            ),
            idempotency_key="idempotency_opaque_test",
        )
        run = run_module(
            request,
            release_registry=release_registry,
            adapters=adapters,
            artifact_host=opaque_store,
            ledger=InMemoryModuleExecutionLedger(),
            clock=lambda: TIME,
        )
        domain_modules = sorted(
            name for name in sys.modules if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in (
                    "src.digestion",
                    "src.research",
                    "src.host_business_workflow",
                )
            )
        )
        print(json.dumps({
            "attempt_status": run.attempts[0].status,
            "domain_modules": domain_modules,
            "module_id": run.module_run.module_release_ref,
            "output_ref_schemes": [
                ref.split(":", 1)[0] for ref in run.attempts[0].output_refs
            ],
            "resolution_status": run.resolution.resolution_status,
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )

    assert result == {
        "attempt_status": "completed",
        "domain_modules": [],
        "module_id": "runtime-module:module_opaque_test@v1",
        "output_ref_schemes": ["cell-artifact"],
        "resolution_status": "resolved",
    }


def test_generated_design_contract_bundle_matches_canonical_docs() -> None:
    manifest = build_design_contract_bundle(check=True)

    assert manifest["runtime_release_version"] == "0.2.0.dev0"
    assert [row["source_path"] for row in manifest["documents"]] == list(
        CANONICAL_DOCUMENTS
    )


def test_design_contract_manifest_contains_only_runtime_authority() -> None:
    manifest = build_design_contract_bundle(check=True)
    authority_by_source = {
        row["source_path"]: row["authority"] for row in manifest["documents"]
    }

    assert authority_by_source == {
        source_name: RUNTIME_OWNED_AUTHORITY
        for source_name in CANONICAL_DOCUMENTS
    }
    for row in manifest["documents"]:
        assert row["external_references"] == sorted(row["external_references"])
        assert set(row["external_references"]) <= EXTERNAL_AUTHORITY_LINK_TARGETS


@pytest.mark.parametrize(
    "target",
    (
        "nonexistent_contract.md",
        "wrong/agent_runtime_00_execution_charter.md",
    ),
)
def test_design_contract_link_closure_rejects_undeclared_or_misrouted_links(
    tmp_path: Path,
    target: str,
) -> None:
    (tmp_path / "designDoc").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "probe"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    for source_name in CANONICAL_DOCUMENTS:
        (tmp_path / source_name).write_bytes(
            (REPO_ROOT / source_name).read_bytes()
        )
    first_doc = tmp_path / CANONICAL_DOCUMENTS[0]
    first_doc.write_text(
        first_doc.read_text(encoding="utf-8")
        + f"\nSee [broken contract]({target}).\n",
        encoding="utf-8",
    )

    with pytest.raises(
        RuntimeError,
        match="unresolvable Design Contract link",
    ):
        build_design_contract_bundle(
            project_root=tmp_path,
            output_root=tmp_path / "generated",
        )


_CONTRACT_PATH_FIELDS = {
    "implementation_surfaces",
    "truth_surfaces",
    "verification_hooks",
}
_REPOSITORY_PATH = re.compile(
    r"(?:src|tests|tools|designDoc)/[A-Za-z0-9_.@/-]+"
)


def _contract_capsule_paths(document: str) -> tuple[str, ...]:
    paths: list[str] = []
    active_field: str | None = None
    in_capsule = False
    for raw_line in document.splitlines():
        line = raw_line.strip()
        if line == "```yaml":
            in_capsule = True
            active_field = None
            continue
        if in_capsule and line == "```":
            break
        if not in_capsule or not line:
            continue
        if not raw_line[:1].isspace():
            key, separator, value = line.partition(":")
            active_field = key.strip() if separator else None
            if active_field in _CONTRACT_PATH_FIELDS and value.strip():
                paths.extend(_REPOSITORY_PATH.findall(value))
            continue
        if active_field in _CONTRACT_PATH_FIELDS and line.startswith("-"):
            paths.extend(_REPOSITORY_PATH.findall(line.removeprefix("-").strip()))
    return tuple(paths)


def test_canonical_runtime_truth_surface_paths_exist() -> None:
    declared: list[tuple[str, str]] = []
    for document_name in CANONICAL_DOCUMENTS:
        document = (REPO_ROOT / document_name).read_text(encoding="utf-8")
        declared.extend(
            (document_name, path) for path in _contract_capsule_paths(document)
        )

    missing = [
        f"{document_name}: {path}"
        for document_name, path in declared
        if not (REPO_ROOT / path).exists()
    ]

    assert len(declared) == 53
    assert missing == []


def test_runtime_wheel_owns_product_host_execution_api_without_platform_or_backend_sdk(
    tmp_path: Path,
) -> None:
    wheel_path = _build_runtime_wheel(tmp_path)
    result = _run_isolated_python(
        """
        import json
        import sys

        sys.path.insert(0, sys.argv[1])
        from agent_runtime.contracts.execution_host_definition import (
            AgentRuntimeProductHostApi,
            RuntimeWorkflowStartRequest,
        )

        forbidden = sorted(
            name for name in sys.modules
            if name == "agency_platform" or name.startswith("agency_platform.")
            or name == "dagster" or name.startswith("dagster.")
            or name == "temporalio" or name.startswith("temporalio.")
        )
        print(json.dumps({
            "host_api": AgentRuntimeProductHostApi.__name__,
            "start_request": RuntimeWorkflowStartRequest.__name__,
            "forbidden_loaded": forbidden,
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )
    assert result == {
        "host_api": "AgentRuntimeProductHostApi",
        "start_request": "RuntimeWorkflowStartRequest",
        "forbidden_loaded": [],
    }


def test_clean_wheel_temporal_descriptor_resolves_public_namespace(
    tmp_path: Path,
) -> None:
    wheel_path = _build_runtime_wheel(tmp_path)

    result = _run_isolated_python_with_dependencies(
        """
        import json
        import sys

        sys.path.insert(0, sys.argv[1])
        from agent_runtime.durability.durability_backend_registration import TEMPORAL_DESCRIPTOR
        from agent_runtime.registry.registry_workflow_registration import (
            resolve_registration_reference,
        )

        implementation = resolve_registration_reference(TEMPORAL_DESCRIPTOR.implementation_ref)
        print(json.dumps({
            "implementation_ref": TEMPORAL_DESCRIPTOR.implementation_ref,
            "implementation_module": implementation.__module__,
            "implementation_name": implementation.__name__,
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )

    assert result == {
        "implementation_ref": (
            "agent_runtime.durability.durability_backend_registration:"
            "load_temporal_workflow_release_adapter"
        ),
        "implementation_module": (
            "agent_runtime.durability.durability_backend_registration"
        ),
        "implementation_name": "load_temporal_workflow_release_adapter",
    }
