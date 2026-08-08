from __future__ import annotations

import ast
import json
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
import tomllib
import zipfile

from tools.build_agent_runtime_design_contract_bundle import (
    CANONICAL_DOCUMENTS,
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
    "src.research_theme_report_workflow",
    "src.trade",
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
        "test": ["pytest>=8"],
    }
    assert configuration["project"]["scripts"] == {
        "agent-runtime-review": "agent_runtime.review.review_snapshot_exporting:main",
    }
    assert configuration["tool"]["setuptools"]["packages"] == [
        "agent_runtime",
        "agent_runtime.contracts",
        "agent_runtime.durability",
        "agent_runtime.execution",
        "agent_runtime.postgres",
        "agent_runtime.provider",
        "agent_runtime.registry",
        "agent_runtime.review",
        "agent_runtime.testing",
    ]
    assert configuration["tool"]["setuptools"]["package-dir"]["agent_runtime"] == (
        "src/agent_runtime"
    )


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
    assert "agent_runtime/README.md" in members
    assert (
        "agent_runtime/review/review_snapshot_definition.schema.json"
        in members
    )
    assert "agent_runtime/design_contract/manifest.json" in members
    for source_name in CANONICAL_DOCUMENTS:
        assert f"agent_runtime/design_contract/{Path(source_name).name}" in members

    result = _run_isolated_python(
        """
        import json
        import sys
        from types import MappingProxyType

        sys.path.insert(0, sys.argv[1])
        import agent_runtime
        from agent_runtime.contracts import (
            WorkflowAdmissionState,
            WorkflowRuntimeRegistration,
        )
        from agent_runtime.review.review_release_rendering import (
            build_runtime_inventory,
        )
        from agent_runtime.registry.registry_plugin_registration import DomainRuntimePlugin, register_runtime_plugin
        from agent_runtime.registry.registry_workflow_registration import WorkflowRuntimeRegistry

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

        inventory = build_runtime_inventory(registry=registry)
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
                    "src.research_theme_report_workflow",
                    "src.trade",
                )
            )
        )
        print(json.dumps({
            "domain_modules": domain_modules,
            "schema_version": inventory["schema_version"],
            "selected_backend_id": inventory["selected_backend_id"],
            "public_namespace": agent_runtime.__name__,
            "driver_result": driver_result,
            "workflow_ids": [row["workflow_id"] for row in inventory["workflows"]],
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )

    assert result == {
        "domain_modules": [],
        "schema_version": "agent_runtime_inventory_v3",
        "selected_backend_id": "temporal",
        "public_namespace": "agent_runtime",
        "driver_result": {"status": "synthetic"},
        "workflow_ids": ["workflow_zeta"],
    }


def test_clean_wheel_executes_target_release_registry_module_slice(
    tmp_path: Path,
) -> None:
    """Prove the published target release model without predecessor registration."""

    wheel_path = _build_runtime_wheel(tmp_path)
    result = _run_isolated_python(
        """
        import json
        import sys

        sys.path.insert(0, sys.argv[1])
        from agent_runtime.contracts.execution_lineage_definition import (
            ModuleUsageObservation,
        )
        from agent_runtime.contracts.execution_module_definition import (
            ModuleExecutionRequest,
            ModuleExecutorResult,
            ModuleOutputBinding,
            ModuleVariantRequest,
        )
        from agent_runtime.contracts.registry_release_definition import (
            ExecutionProfileRelease,
            ModuleEntryPolicy,
            ModuleExecutionPurpose,
            ModuleKind,
            OutputResolutionPolicy,
            ReleaseAdmissionRecord,
            ReleaseAdmissionState,
            ReleaseSubjectKind,
            RuntimeModuleRelease,
        )
        from agent_runtime.execution.execution_lineage_recording import (
            InMemoryModuleExecutionLedger,
        )
        from agent_runtime.execution.execution_module_invocation import (
            ModuleExecutorRegistry,
            run_module,
        )
        from agent_runtime.registry.registry_release_registration import (
            RuntimeReleaseBundle,
            RuntimeReleaseRegistry,
        )

        HASH = "a" * 64
        TIME = "2026-08-08T12:00:00Z"
        profile = ExecutionProfileRelease.build(
            execution_profile_id="profile_opaque_test",
            execution_profile_version="v1",
            release_ref="execution-profile:profile-opaque-test@v1",
            executor_adapter_id="executor_opaque_test",
            executor_adapter_revision="v1",
            transport_kind="in_process_test",
            provider_id="provider_opaque",
            model_id="model_opaque",
            reasoning_profile="none",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            gateway_access_reasons=(),
            output_constraint_mode="prompt_only_json",
            tool_policy=(),
            network_policy="denied",
            context_policy_ref="context-policy:opaque@v1",
            context_policy_sha256=HASH,
            timeout_seconds=60,
        )
        module = RuntimeModuleRelease.build(
            module_id="module_opaque_test",
            module_version="v1",
            release_ref="runtime-module:module-opaque-test@v1",
            module_kind=ModuleKind.DETERMINISTIC,
            owner_contract_ref="contract:opaque@v1",
            owner_contract_sha256=HASH,
            source_skill_package_ref=None,
            source_skill_package_sha256=None,
            source_export_id=None,
            executable_ref="callable:opaque@v1",
            executable_sha256=HASH,
            input_schema_ref="schema:opaque_input@v1",
            input_schema_sha256=HASH,
            output_schema_ref="schema:opaque_output@v1",
            output_schema_sha256=HASH,
            prompt_bundle_ref=None,
            prompt_bundle_sha256=None,
            declared_operation_ids=(),
            context_policy_ref="context-policy:opaque@v1",
            context_policy_sha256=HASH,
            evaluation_policy_ref="evaluation-policy:opaque@v1",
            evaluation_policy_sha256=HASH,
            retry_policy_ref="retry-policy:opaque@v1",
            retry_policy_sha256=HASH,
            compatible_transport_kinds=("in_process_test",),
            entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
            output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        )

        def admission(admission_id, kind, subject_id, release_ref, release_sha256):
            return ReleaseAdmissionRecord.build(
                admission_id=admission_id,
                subject_kind=kind,
                subject_id=subject_id,
                release_ref=release_ref,
                release_sha256=release_sha256,
                state=ReleaseAdmissionState.CANDIDATE,
                evidence_members=(),
                recorded_at_utc=TIME,
            )

        release_registry = RuntimeReleaseRegistry()
        release_registry.register_bundle(
            RuntimeReleaseBundle(
                execution_profiles=(profile,),
                modules=(module,),
                admissions=(
                    admission(
                        "admission_profile_opaque",
                        ReleaseSubjectKind.EXECUTION_PROFILE,
                        profile.execution_profile_id,
                        profile.release_ref,
                        profile.release_sha256,
                    ),
                    admission(
                        "admission_module_opaque",
                        ReleaseSubjectKind.RUNTIME_MODULE,
                        module.module_id,
                        module.release_ref,
                        module.release_sha256,
                    ),
                ),
            )
        )

        class OpaqueExecutor:
            def execute(self, request):
                return ModuleExecutorResult(
                    outputs=(
                        ModuleOutputBinding(
                            logical_name="result",
                            output_ref="artifact:opaque-output@v1",
                            output_sha256=HASH,
                            schema_ref=request.module.output_schema_ref,
                            schema_sha256=request.module.output_schema_sha256,
                            media_type="application/json",
                        ),
                    ),
                    usage=ModuleUsageObservation(
                        input_tokens=1,
                        output_tokens=1,
                        cache_read_tokens=None,
                        cache_creation_tokens=None,
                    ),
                )

        executors = ModuleExecutorRegistry()
        executors.register("executor_opaque_test", OpaqueExecutor())
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
            executors=executors,
            ledger=InMemoryModuleExecutionLedger(),
            clock=lambda: TIME,
        )
        domain_modules = sorted(
            name for name in sys.modules if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in (
                    "src.digestion",
                    "src.research",
                    "src.research_theme_report_workflow",
                )
            )
        )
        print(json.dumps({
            "attempt_status": run.attempts[0].status,
            "domain_modules": domain_modules,
            "module_id": run.module_run.module_release_ref,
            "output_refs": list(run.attempts[0].output_refs),
            "resolution_status": run.resolution.resolution_status,
        }))
        """,
        str(wheel_path),
        cwd=tmp_path,
    )

    assert result == {
        "attempt_status": "completed",
        "domain_modules": [],
        "module_id": "runtime-module:module-opaque-test@v1",
        "output_refs": ["artifact:opaque-output@v1"],
        "resolution_status": "resolved",
    }


def test_generated_design_contract_bundle_matches_canonical_docs() -> None:
    manifest = build_design_contract_bundle(check=True)

    assert manifest["runtime_release_version"] == "0.1.0.dev0"
    assert [row["source_path"] for row in manifest["documents"]] == list(
        CANONICAL_DOCUMENTS
    )


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
            "agent_runtime.testing.durability_temporal_conformance:TemporalConformanceWorkflow"
        ),
        "implementation_module": "agent_runtime.testing.durability_temporal_conformance",
        "implementation_name": "TemporalConformanceWorkflow",
    }
