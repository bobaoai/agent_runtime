from __future__ import annotations

from dataclasses import replace
import ast
import json
from pathlib import Path
import zipfile

import pytest

from agent_runtime.testing.conformance_agent_capability_verification import (
    AgentCapabilityCommand,
    AgentCapabilityEvidenceSourceKind,
    AgentCapabilityTestCase,
    render_agent_capability_runbook,
)
from agent_runtime.testing import conformance_agent_capability_verification as subject


REPO_ROOT = Path(__file__).resolve().parents[1]


def _case(
    case_id: str = "module_registration_case",
) -> AgentCapabilityTestCase:
    return AgentCapabilityTestCase(
        case_id=case_id,
        capability_ids=("explicit_module_loading", "schema_closure"),
        owning_design_refs=(
            "designDoc/agent_runtime_01_module_contract_and_assembly.md",
        ),
        evidence_contract_refs=(
            "runtime_module_export",
            "runtime_release_register",
        ),
        evidence_source_kind=(
            AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
        ),
        subject_requirements=(
            "exact Runtime source ref and hash",
            "exact Reviewer registration source",
        ),
        environment_prerequisites=("local Runtime checkout",),
        command=AgentCapabilityCommand(
            command_id="module_registration_case_command",
            argv=(
                "python",
                "-m",
                "pytest",
                "tests/test_agent_runtime_module_authoring.py",
                "-q",
            ),
            working_directory_ref="repo-root:agent-runtime",
        ),
        expected_result=("command exits zero", "registered Module resolves"),
        evidence_outputs=("pytest result", "registered release refs and hashes"),
        cleanup=("remove disposable in-memory Registry",),
        failure_routing=(
            "harness failure returns Agent Capability Verification owner",
            "Registry capability failure retains 01 owner",
        ),
        rerun_boundary=(
            "rerun when Runtime subject hash changes",
            "rerun when Reviewer registration closure changes",
        ),
    )


def test_case_validates_and_projects_every_contract_field() -> None:
    case = _case()

    case.validate()
    record = case.as_runbook_record()

    assert record == {
        "case_id": "module_registration_case",
        "capability_ids": ["explicit_module_loading", "schema_closure"],
        "owning_design_refs": [
            "designDoc/agent_runtime_01_module_contract_and_assembly.md"
        ],
        "evidence_contract_refs": [
            "runtime_module_export",
            "runtime_release_register",
        ],
        "evidence_source_kind": "executable_owner_case",
        "subject_requirements": [
            "exact Runtime source ref and hash",
            "exact Reviewer registration source",
        ],
        "environment_prerequisites": ["local Runtime checkout"],
        "command": {
            "command_id": "module_registration_case_command",
            "argv": [
                "python",
                "-m",
                "pytest",
                "tests/test_agent_runtime_module_authoring.py",
                "-q",
            ],
            "working_directory_ref": "repo-root:agent-runtime",
        },
        "expected_result": ["command exits zero", "registered Module resolves"],
        "evidence_outputs": [
            "pytest result",
            "registered release refs and hashes",
        ],
        "cleanup": ["remove disposable in-memory Registry"],
        "failure_routing": [
            "harness failure returns Agent Capability Verification owner",
            "Registry capability failure retains 01 owner",
        ],
        "rerun_boundary": [
            "rerun when Runtime subject hash changes",
            "rerun when Reviewer registration closure changes",
        ],
    }


@pytest.mark.parametrize(
    "field_name",
    (
        "capability_ids",
        "owning_design_refs",
        "evidence_contract_refs",
        "subject_requirements",
        "environment_prerequisites",
        "expected_result",
        "evidence_outputs",
        "cleanup",
        "failure_routing",
        "rerun_boundary",
    ),
)
def test_case_rejects_an_empty_required_field_group(field_name: str) -> None:
    with pytest.raises(ValueError, match="must be non-empty"):
        replace(_case(), **{field_name: ()}).validate()


def test_case_rejects_invalid_and_duplicate_identity() -> None:
    with pytest.raises(ValueError, match="invalid case_id"):
        replace(_case(), case_id="INVALID").validate()
    with pytest.raises(ValueError, match="capability_ids must be unique"):
        replace(
            _case(),
            capability_ids=("schema_closure", "schema_closure"),
        ).validate()
    with pytest.raises(ValueError, match="evidence_source_kind"):
        replace(
            _case(),
            evidence_source_kind="environment_gate",  # type: ignore[arg-type]
        ).validate()


def test_command_rejects_empty_argv() -> None:
    with pytest.raises(ValueError, match="argv must be a non-empty exact tuple"):
        replace(
            _case(),
            command=replace(_case().command, argv=()),
        ).validate()


def test_command_allows_repeated_arguments() -> None:
    command = replace(_case().command, argv=("python", "same", "same"))

    command.validate()


@pytest.mark.parametrize(
    "argument",
    (
        "--token=secret-value",
        "/Users/example/private-command",
        "~/private-command",
    ),
)
def test_command_rejects_inline_secret_and_host_private_path(
    argument: str,
) -> None:
    command = replace(_case().command, argv=("python", argument))

    with pytest.raises(ValueError, match="inline secret|host-private path"):
        command.validate()


def test_runbook_projection_is_stable_across_input_order() -> None:
    alpha = _case("alpha_case")
    beta = _case("beta_case")

    first = render_agent_capability_runbook((beta, alpha))
    second = render_agent_capability_runbook((alpha, beta))

    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == "agent_capability_runbook_v1"
    assert [case["case_id"] for case in payload["cases"]] == [
        "alpha_case",
        "beta_case",
    ]


def test_runbook_projection_rejects_duplicate_case_ids() -> None:
    with pytest.raises(ValueError, match="unique case_id"):
        render_agent_capability_runbook((_case(), _case()))


def test_example_navigation_is_optional_for_existing_cases() -> None:
    case = _case()
    case.validate()
    assert case.example_test_refs == ()
    assert "example_test_refs" not in case.as_runbook_record()
    assert "本 case 未提供样例导航" in subject.render_agent_capability_runbook_markdown((case,))


def test_example_navigation_projects_without_changing_the_group_command() -> None:
    selector = "tests/test_agent_runtime_module_authoring.py::test_runtime_loads_one_exact_module_registration"
    case = replace(_case(), example_test_refs=(selector,))
    assert case.as_runbook_record()["example_test_refs"] == [selector]
    assert case.command == _case().command
    markdown = subject.render_agent_capability_runbook_markdown((case,))
    assert selector in markdown
    assert "## 4. 用例索引" in markdown
    assert "python -B -m pytest -q -rs -p no:cacheprovider " + selector in markdown


@pytest.mark.parametrize("selectors", (
    None,
    ["tests/test_case.py::test_example"],
    ("/tmp/test_case.py::test_example",),
    ("tests/../test_case.py::test_example",),
    ("tests//test_case.py::test_example",),
    ("tests/test_case.py::test_example;echo",),
    ("tests/test_case.py::test_example[param]",),
    ("tests/test_case.py",),
    (3,),
    ("tests/test_case.py::test_example", "tests/test_case.py::test_example"),
))
def test_example_navigation_rejects_invalid_or_duplicate_selectors(selectors) -> None:
    with pytest.raises(ValueError):
        replace(_case(), example_test_refs=selectors).validate()


def test_example_navigation_validation_does_not_require_a_checkout() -> None:
    case = replace(_case(), example_test_refs=("tests/test_not_installed.py::test_example",))
    case.validate()
    assert "test_not_installed.py" in subject.render_agent_capability_runbook_markdown((case,))


def test_all_builtin_examples_exist_in_their_group_test_files() -> None:
    for case in subject.required_agent_capability_cases():
        assert case.example_test_refs, case.case_id
        for selector in case.example_test_refs:
            path, function = selector.split("::")
            assert path in case.command.argv, (case.case_id, selector)
            tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
            assert function in {
                node.name for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }, selector
        assert case.subject_requirements[-1]
        assert "command exits zero" not in " ".join(case.expected_result)


def test_capability_groups_include_new_execution_format_and_environment_tests() -> None:
    cases = {case.case_id: case for case in subject.required_agent_capability_cases()}
    for case_id, paths in {
        "module_release_assembly_case": ("tests/test_agent_runtime_reviewer_output_format.py",),
        "module_execution_case": ("tests/test_agent_runtime_registered_module_execution.py",),
        "persistent_runtime_case": ("tests/test_agent_runtime_registered_module_execution.py",),
        "durable_backend_case": ("tests/test_agent_runtime_temporal_integration.py",
                                 "tests/test_agent_runtime_temporal_target_adapter.py"),
        "live_module_transport_case": ("tests/test_agent_runtime_native_structured_output.py",
                                       "tests/test_agent_runtime_managed_design_reviewer.py"),
    }.items():
        assert set(paths) <= set(cases[case_id].command.argv)


def test_runbook_index_batch_commands_and_result_boundaries_are_explicit() -> None:
    cases = subject.required_agent_capability_cases()
    runbook = subject.render_agent_capability_runbook_markdown(cases)
    catalog = subject.render_agent_capability_catalog_markdown(
        subject.required_agent_capability_inventory(), cases,
    )
    for case in cases:
        assert f'(#{case.case_id})' in runbook
        assert f'<a id="{case.case_id}"></a>' in runbook
        assert f'(agent_runtime_capability_runbook.md#{case.case_id})' in catalog
        assert case.command.argv[:len(subject._PYTEST_ARGV)] == subject._PYTEST_ARGV
    assert "python -B -m pytest --collect-only -q" in runbook
    assert "RUN_PROVIDER_INTEGRATION=0 RUN_TEMPORAL_INTEGRATION=0" in runbook
    assert "AGENT_RUNTIME_TEST_DATABASE_URL" in runbook
    assert "PGCLIENTENCODING=UTF8" in runbook
    assert "--junitxml=PATH" in runbook
    assert "pytest 退出零表示实际执行的断言未失败，不表示所有用例都执行了" in runbook
    assert "完整测试和 fixtures 位于同版本 Runtime 源码 checkout" in runbook
    assert "--example agent_capability_example" in runbook
    assert "--example agent_evaluation_example" in runbook
    assert "生产Gateway和跨进程恢复保留各自独立准入" in runbook
    assert "不表示这些能力已全部完成或通过" in catalog
    assert "run_agent_capability_verification(" not in runbook
    assert subject.render_agent_capability_runbook_markdown(tuple(reversed(cases))) == runbook


def _focused_case() -> AgentCapabilityTestCase:
    return replace(
        _case(),
        case_id="verify_explicit_module_loading",
        capability_ids=("explicit_module_loading",),
    )


def _request(
    *,
    scope: subject.AgentCapabilityVerificationScope = (
        subject.AgentCapabilityVerificationScope.FOCUSED
    ),
    selected_case_ids: tuple[str, ...] = ("verify_explicit_module_loading",),
    standalone: bool = False,
) -> subject.AgentCapabilityVerificationRequest:
    return subject.AgentCapabilityVerificationRequest(
        request_id="verify_runtime_candidate",
        subject_ref="runtime-subject:commit-test",
        subject_sha256="a" * 64,
        suite_ref="verification-suite:runtime-v1",
        suite_sha256="b" * 64,
        dependency_closure_ref="verification-closure:runtime-v1",
        dependency_closure_sha256="c" * 64,
        scope=scope,
        selected_case_ids=selected_case_ids,
        standalone_conformance_ref=(
            "standalone-conformance:runtime-v1" if standalone else None
        ),
        standalone_conformance_sha256=("d" * 64 if standalone else None),
    )


def _passed(case: AgentCapabilityTestCase) -> subject.AgentCapabilityCaseResult:
    return subject.AgentCapabilityCaseResult(
        case_id=case.case_id,
        capability_ids=case.capability_ids,
        state=subject.AgentCapabilityResultState.PASSED,
        evidence_refs=(f"evidence:{case.case_id}",),
    )


def test_required_inventory_contains_exactly_42_unique_capabilities() -> None:
    inventory = subject.required_agent_capability_inventory()

    assert len(inventory) == 42
    assert {entry.capability_id for entry in inventory} == {
        "explicit_module_loading",
        "path_free_release_identity",
        "schema_closure",
        "prompt_closure",
        "policy_closure",
        "profile_independence",
        "generic_profile_compatibility",
        "inline_semantic_input",
        "structured_output",
        "tool_free_execution",
        "runtime_hosted_self_test",
        "authorized_gateway_read",
        "attempt_workspace",
        "context_isolation",
        "network_enforcement",
        "provider_failure_normalization",
        "module_run",
        "multiple_variants",
        "evaluation_and_selection",
        "retry_budget",
        "idempotent_replay",
        "operation_boundary_enforcement",
        "cancellation",
        "graph_authoring",
        "sequential_and_branch_routing",
        "parallel_fan_out_and_join",
        "revision_loop",
        "wait_and_external_event",
        "crash_recovery",
        "portable_workflow_registration",
        "per_registry_execution_binding",
        "attempt_and_workflow_ledger",
        "usage_truth",
        "release_inspection",
        "execution_inspection",
        "persistent_registry",
        "persistent_ledger",
        "persistent_inspection",
        "durable_backend",
        "live_provider_adapter",
        "registered_module_transport",
        "public_package",
    }
    reviewer = next(
        entry
        for entry in inventory
        if entry.capability_id == "registered_module_transport"
    )
    assert reviewer.evidence_source_kinds == (
        AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
        AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
    )
    package = next(
        entry for entry in inventory if entry.capability_id == "public_package"
    )
    assert package.evidence_source_kinds == (
        AgentCapabilityEvidenceSourceKind.REFERENCED_PEER_RESULT,
    )


def test_nine_canonical_cases_close_the_inventory_and_runbook() -> None:
    inventory = subject.required_agent_capability_inventory()
    cases = subject.required_agent_capability_cases()
    cases_by_id = {case.case_id: case for case in cases}

    assert len(cases) == 9
    assert {
        case_id for entry in inventory for case_id in entry.case_ids
    } == set(cases_by_id)
    for entry in inventory:
        for case_id in entry.case_ids:
            assert entry.capability_id in cases_by_id[case_id].capability_ids
            assert (
                cases_by_id[case_id].evidence_source_kind
                in entry.evidence_source_kinds
            )
    for case in cases:
        for argument in case.command.argv:
            if argument.endswith(".py"):
                assert (REPO_ROOT / argument).is_file()

    runbook = json.loads(render_agent_capability_runbook(cases))
    assert len(runbook["cases"]) == 9
    assert [case["case_id"] for case in runbook["cases"]] == sorted(cases_by_id)


def test_generated_public_capability_docs_match_code_owned_cases() -> None:
    inventory = subject.required_agent_capability_inventory()
    cases = subject.required_agent_capability_cases()

    assert (
        REPO_ROOT / "docs/agent_runtime_capabilities.md"
    ).read_text(encoding="utf-8") == subject.render_agent_capability_catalog_markdown(
        inventory,
        cases,
    )
    assert (
        REPO_ROOT / "docs/agent_runtime_capability_runbook.md"
    ).read_text(encoding="utf-8") == subject.render_agent_capability_runbook_markdown(
        cases
    )
    assert (
        REPO_ROOT / "src/agent_runtime/docs/agent_runtime_capabilities.md"
    ).read_text(encoding="utf-8") == subject.render_agent_capability_catalog_markdown(
        inventory,
        cases,
    )
    assert (
        REPO_ROOT / "src/agent_runtime/docs/agent_runtime_capability_runbook.md"
    ).read_text(encoding="utf-8") == subject.render_agent_capability_runbook_markdown(
        cases
    )


def test_wheel_contains_the_current_generated_capability_docs(tmp_path: Path) -> None:
    from test_agent_runtime_packaging_boundary import _build_runtime_wheel

    wheel_path = _build_runtime_wheel(tmp_path)
    with zipfile.ZipFile(wheel_path) as wheel:
        for name in ("agent_runtime_capabilities.md", "agent_runtime_capability_runbook.md"):
            assert wheel.read(f"agent_runtime/docs/{name}") == (REPO_ROOT / "docs" / name).read_bytes()


def test_engineering_reviewer_static_capability_closure() -> None:
    import os
    source_root = Path(os.environ.get("AGENT_RUNTIME_REVIEWER_SOURCE_ROOT", REPO_ROOT))
    module_root = (
        source_root
        / "09_soul/governance/skills/engineering-change-review/runtime_modules"
        / "engineering_change_reviewer"
    )
    if not module_root.is_dir():
        pytest.skip("host integration: set AGENT_RUNTIME_REVIEWER_SOURCE_ROOT to the installed reviewer package")
    registration = json.loads(
        (module_root / "module_registration.json").read_text(encoding="utf-8")
    )
    input_schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (module_root / "schemas/output.schema.json").read_text(encoding="utf-8")
    )

    assert registration["schema_version"] == "runtime_module_registration_v2"
    assert registration["module_id"] == "engineering_change_reviewer"
    assert registration["input_schema_ref"] == input_schema["$id"]
    assert registration["output_schema_ref"] == output_schema["$id"]
    assert registration["declared_operation_ids"] == [
        "model_execute",
        "repository_read",
        "repository_search",
        "sandbox_command_execute",
    ]


def test_verification_request_rejects_partial_package_evidence() -> None:
    request = replace(
        _request(),
        standalone_conformance_ref="standalone-conformance:runtime-v1",
    )

    with pytest.raises(ValueError, match="must be paired"):
        request.validate()


def test_focused_runner_passes_only_the_selected_scope() -> None:
    result = subject.run_agent_capability_verification(
        _request(),
        cases=(_focused_case(),),
        inventory=(
            subject.AgentCapabilityInventoryEntry(
                capability_id="explicit_module_loading",
                owning_design_refs=(
                    "designDoc/agent_runtime_01_module_contract_and_assembly.md",
                ),
                evidence_source_kinds=(
                    AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                ),
                case_ids=("verify_explicit_module_loading",),
            ),
        ),
        execute_case=_passed,
    )

    assert result.scope_completed is True
    assert result.full_runtime_completed is False
    assert result.case_results[0].state is subject.AgentCapabilityResultState.PASSED


@pytest.mark.parametrize(
    ("state", "failure_code"),
    (
        (subject.AgentCapabilityResultState.FAILED, "ADAPTER_OUTPUT_INVALID"),
        (
            subject.AgentCapabilityResultState.NOT_RUN,
            "AGENT_CAPABILITY_ENVIRONMENT_UNAVAILABLE",
        ),
    ),
)
def test_non_passing_case_never_propagates_completion(
    state: subject.AgentCapabilityResultState,
    failure_code: str,
) -> None:
    def execute(case: AgentCapabilityTestCase) -> subject.AgentCapabilityCaseResult:
        evidence_refs = (
            ("diagnostic:adapter-output",)
            if state is subject.AgentCapabilityResultState.FAILED
            else ()
        )
        return subject.AgentCapabilityCaseResult(
            case_id=case.case_id,
            capability_ids=case.capability_ids,
            state=state,
            evidence_refs=evidence_refs,
            failure_code=failure_code,
            failure_owner_ref=(
                "designDoc/agent_runtime_08_agent_execution_adapter_contract.md"
            ),
        )

    result = subject.run_agent_capability_verification(
        _request(),
        cases=(_focused_case(),),
        inventory=(
            subject.AgentCapabilityInventoryEntry(
                capability_id="explicit_module_loading",
                owning_design_refs=(
                    "designDoc/agent_runtime_01_module_contract_and_assembly.md",
                ),
                evidence_source_kinds=(
                    AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                ),
                case_ids=("verify_explicit_module_loading",),
            ),
        ),
        execute_case=execute,
    )

    assert result.scope_completed is False
    assert result.full_runtime_completed is False
    assert result.case_results[0].failure_code == failure_code


def test_complete_scope_requires_full_inventory_and_package_result() -> None:
    with pytest.raises(
        subject.AgentCapabilityVerificationError,
        match="complete inventory required",
    ) as raised:
        subject.run_agent_capability_verification(
            _request(
                scope=subject.AgentCapabilityVerificationScope.COMPLETE,
                standalone=True,
            ),
            cases=(_focused_case(),),
            inventory=(
                subject.AgentCapabilityInventoryEntry(
                    capability_id="explicit_module_loading",
                    owning_design_refs=(
                        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
                    ),
                    evidence_source_kinds=(
                        AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                    ),
                    case_ids=("verify_explicit_module_loading",),
                ),
            ),
            execute_case=_passed,
        )
    assert raised.value.error_code == subject.AGENT_CAPABILITY_COVERAGE_INCOMPLETE


def test_runner_rejects_a_case_result_for_another_case() -> None:
    def wrong_result(
        case: AgentCapabilityTestCase,
    ) -> subject.AgentCapabilityCaseResult:
        return replace(_passed(case), case_id="verify_schema_closure")

    with pytest.raises(
        subject.AgentCapabilityVerificationError,
        match="case result identity mismatch",
    ) as raised:
        subject.run_agent_capability_verification(
            _request(),
            cases=(_focused_case(),),
            inventory=(
                subject.AgentCapabilityInventoryEntry(
                    capability_id="explicit_module_loading",
                    owning_design_refs=(
                        "designDoc/agent_runtime_01_module_contract_and_assembly.md",
                    ),
                    evidence_source_kinds=(
                        AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                    ),
                    case_ids=("verify_explicit_module_loading",),
                ),
            ),
            execute_case=wrong_result,
        )
    assert raised.value.error_code == subject.AGENT_CAPABILITY_TEST_FAILED


def test_complete_case_execution_still_does_not_claim_full_runtime() -> None:
    inventory = subject.required_agent_capability_inventory()
    cases = subject.required_agent_capability_cases()
    selected = tuple(case.case_id for case in cases)

    result = subject.run_agent_capability_verification(
        _request(
            scope=subject.AgentCapabilityVerificationScope.COMPLETE,
            selected_case_ids=selected,
            standalone=True,
        ),
        cases=cases,
        inventory=inventory,
        execute_case=_passed,
    )

    assert result.scope_completed is True
    assert result.full_runtime_completed is False
