"""Code-owned capability cases and runbook for Runtime conformance."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import re
import shlex
from typing import Any, Callable

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_exact_record_instance,
    validate_exact_record_tuple,
    validate_id,
    validate_opaque_ref,
    validate_sha256,
    validate_string_tuple,
)


RUNBOOK_SCHEMA_VERSION = "agent_capability_runbook_v1"
AGENT_CAPABILITY_TEST_FAILED = "AGENT_CAPABILITY_TEST_FAILED"
AGENT_CAPABILITY_ENVIRONMENT_UNAVAILABLE = (
    "AGENT_CAPABILITY_ENVIRONMENT_UNAVAILABLE"
)
AGENT_CAPABILITY_COVERAGE_INCOMPLETE = "AGENT_CAPABILITY_COVERAGE_INCOMPLETE"
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")
_PYTEST_ARGV = ("python", "-B", "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider")


class AgentCapabilityVerificationError(ValueError):
    """Stable T2 11 verification failure returned to the accountable caller."""

    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(f"{error_code}: {message}")
        self.error_code = error_code


class AgentCapabilityEvidenceSourceKind(StrEnum):
    """The fixed source class for one capability verification result."""

    EXECUTABLE_OWNER_CASE = "executable_owner_case"
    ENVIRONMENT_GATE = "environment_gate"
    REFERENCED_PEER_RESULT = "referenced_peer_result"


def _validate_bounded_text(label: str, value: Any) -> None:
    if type(value) is not str or not value.strip() or len(value) > 1024:
        raise ValueError(f"{label} must be non-empty bounded text")


def _validate_command_argument(label: str, value: Any) -> None:
    _validate_bounded_text(label, value)
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"{label} must not contain an inline secret")
    if value.startswith(("/", "~/")):
        raise ValueError(f"{label} must not contain a host-private path")


def _validate_example_test_ref(label: str, value: Any) -> None:
    _validate_bounded_text(label, value)
    if re.fullmatch(
        r"tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+",
        value,
    ) is None:
        raise ValueError(f"{label} must be a repository-relative pytest function selector")


@dataclass(frozen=True)
class AgentCapabilityCommand:
    """One shell-free command definition referenced by a case and its runbook."""

    command_id: str
    argv: tuple[str, ...]
    working_directory_ref: str

    def validate(self) -> None:
        """Reject invalid identity, empty argv, or an unbounded working directory."""

        validate_id("command_id", self.command_id)
        if type(self.argv) is not tuple or not self.argv:
            raise ValueError("argv must be a non-empty exact tuple")
        for argument in self.argv:
            _validate_command_argument("argv", argument)
        validate_opaque_ref(
            "working_directory_ref",
            self.working_directory_ref,
        )

    def as_record(self) -> dict[str, object]:
        """Return the validated JSON-ready command without executing it."""

        self.validate()
        return {
            "command_id": self.command_id,
            "argv": list(self.argv),
            "working_directory_ref": self.working_directory_ref,
        }


@dataclass(frozen=True)
class AgentCapabilityTestCase:
    """One immutable capability case shared by a future runner and runbook."""

    case_id: str
    capability_ids: tuple[str, ...]
    owning_design_refs: tuple[str, ...]
    evidence_contract_refs: tuple[str, ...]
    evidence_source_kind: AgentCapabilityEvidenceSourceKind
    subject_requirements: tuple[str, ...]
    environment_prerequisites: tuple[str, ...]
    command: AgentCapabilityCommand
    expected_result: tuple[str, ...]
    evidence_outputs: tuple[str, ...]
    cleanup: tuple[str, ...]
    failure_routing: tuple[str, ...]
    rerun_boundary: tuple[str, ...]
    example_test_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        """Validate the complete T2 case closure before runner admission."""

        validate_id("case_id", self.case_id)
        for label, values in (
            ("capability_ids", self.capability_ids),
            ("owning_design_refs", self.owning_design_refs),
            ("evidence_contract_refs", self.evidence_contract_refs),
            ("subject_requirements", self.subject_requirements),
            ("environment_prerequisites", self.environment_prerequisites),
            ("expected_result", self.expected_result),
            ("evidence_outputs", self.evidence_outputs),
            ("cleanup", self.cleanup),
            ("failure_routing", self.failure_routing),
            ("rerun_boundary", self.rerun_boundary),
        ):
            validate_string_tuple(
                label,
                values,
                item_validator=_validate_bounded_text,
                require_non_empty=True,
            )
        if type(self.evidence_source_kind) is not AgentCapabilityEvidenceSourceKind:
            raise ValueError(
                "evidence_source_kind must be an exact "
                "AgentCapabilityEvidenceSourceKind"
            )
        validate_exact_record_instance(
            "command",
            self.command,
            expected_type=AgentCapabilityCommand,
        )
        self.command.validate()
        validate_string_tuple(
            "example_test_refs",
            self.example_test_refs,
            item_validator=_validate_example_test_ref,
            require_non_empty=False,
        )

    def as_runbook_record(self) -> dict[str, object]:
        """Project every case field into one deterministic runbook record."""

        self.validate()
        record = {
            "case_id": self.case_id,
            "capability_ids": list(self.capability_ids),
            "owning_design_refs": list(self.owning_design_refs),
            "evidence_contract_refs": list(self.evidence_contract_refs),
            "evidence_source_kind": self.evidence_source_kind.value,
            "subject_requirements": list(self.subject_requirements),
            "environment_prerequisites": list(
                self.environment_prerequisites
            ),
            "command": self.command.as_record(),
            "expected_result": list(self.expected_result),
            "evidence_outputs": list(self.evidence_outputs),
            "cleanup": list(self.cleanup),
            "failure_routing": list(self.failure_routing),
            "rerun_boundary": list(self.rerun_boundary),
        }
        if self.example_test_refs:
            record["example_test_refs"] = list(self.example_test_refs)
        return record


def render_agent_capability_runbook(
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render validated cases as byte-stable, operator-readable JSON."""

    validate_exact_record_tuple(
        "cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    payload = {
        "schema_version": RUNBOOK_SCHEMA_VERSION,
        "cases": [
            case.as_runbook_record()
            for case in sorted(cases, key=lambda item: item.case_id)
        ],
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


class AgentCapabilityVerificationScope(StrEnum):
    """The requested evidence boundary for one verification run."""

    FOCUSED = "focused"
    COMPLETE = "complete"


class AgentCapabilityResultState(StrEnum):
    """The only result states for one selected capability case."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class AgentCapabilityTransportCapability(StrEnum):
    """Optional transport support fact carried by a passed transport case."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class AgentCapabilityInventoryEntry:
    """One stable T2 11 capability and its required evidence cases."""

    capability_id: str
    owning_design_refs: tuple[str, ...]
    evidence_source_kinds: tuple[AgentCapabilityEvidenceSourceKind, ...]
    case_ids: tuple[str, ...]

    def validate(self) -> None:
        validate_id("capability_id", self.capability_id)
        validate_string_tuple(
            "owning_design_refs",
            self.owning_design_refs,
            item_validator=_validate_bounded_text,
            require_non_empty=True,
        )
        if (
            type(self.evidence_source_kinds) is not tuple
            or not self.evidence_source_kinds
        ):
            raise ValueError("evidence_source_kinds must be a non-empty exact tuple")
        if any(
            type(kind) is not AgentCapabilityEvidenceSourceKind
            for kind in self.evidence_source_kinds
        ):
            raise ValueError("invalid evidence_source_kinds")
        if len(set(self.evidence_source_kinds)) != len(self.evidence_source_kinds):
            raise ValueError("evidence_source_kinds must be unique")
        validate_string_tuple(
            "case_ids",
            self.case_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )


_REGISTRY_DESIGN = "designDoc/agent_runtime_01_module_contract_and_assembly.md"
_INVOCATION_DESIGN = "designDoc/agent_runtime_08_agent_execution_adapter_contract.md"
_EXECUTION_DESIGN = "designDoc/agent_runtime_00_execution_charter.md"
_DURABILITY_DESIGN = "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md"
_LEDGER_DESIGN = "designDoc/agent_runtime_00_execution_charter.md"
_INSPECTION_DESIGN = (
    "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md"
)
_RELEASE_DESIGN = "designDoc/agent_runtime_05_delivery_roadmap.md"

_MODULE_ASSEMBLY_CAPABILITIES = (
    "explicit_module_loading",
    "path_free_release_identity",
    "schema_closure",
    "prompt_closure",
    "policy_closure",
    "profile_independence",
    "generic_profile_compatibility",
    "registered_module_transport",
)
_INVOCATION_CAPABILITIES = (
    "inline_semantic_input",
    "structured_output",
    "tool_free_execution",
    "runtime_hosted_self_test",
    "authorized_gateway_read",
    "attempt_workspace",
    "context_isolation",
    "network_enforcement",
    "provider_failure_normalization",
)
_EXECUTION_CAPABILITIES = (
    "module_run",
    "multiple_variants",
    "evaluation_and_selection",
    "retry_budget",
    "idempotent_replay",
    "operation_boundary_enforcement",
    "cancellation",
)
_WORKFLOW_CAPABILITIES = (
    "graph_authoring",
    "sequential_and_branch_routing",
    "parallel_fan_out_and_join",
    "revision_loop",
    "wait_and_external_event",
    "crash_recovery",
    "portable_workflow_registration",
    "per_registry_execution_binding",
)
_EVIDENCE_CAPABILITIES = (
    "attempt_and_workflow_ledger",
    "usage_truth",
    "release_inspection",
    "execution_inspection",
)
_PERSISTENT_CAPABILITIES = (
    "persistent_registry",
    "persistent_ledger",
    "persistent_inspection",
)
_LIVE_TRANSPORT_CAPABILITIES = (
    "live_provider_adapter",
    "registered_module_transport",
)

_CASE_IDS_BY_CAPABILITY = {
    **{
        capability_id: ("module_release_assembly_case",)
        for capability_id in _MODULE_ASSEMBLY_CAPABILITIES
    },
    **{
        capability_id: ("agent_invocation_case",)
        for capability_id in _INVOCATION_CAPABILITIES
    },
    **{
        capability_id: ("module_execution_case",)
        for capability_id in _EXECUTION_CAPABILITIES
    },
    **{
        capability_id: ("workflow_graph_case",)
        for capability_id in _WORKFLOW_CAPABILITIES
    },
    **{
        capability_id: ("ledger_inspection_case",)
        for capability_id in _EVIDENCE_CAPABILITIES
    },
    **{
        capability_id: ("persistent_runtime_case",)
        for capability_id in _PERSISTENT_CAPABILITIES
    },
    "durable_backend": ("durable_backend_case",),
    "live_provider_adapter": ("live_module_transport_case",),
    "registered_module_transport": (
        "module_release_assembly_case",
        "live_module_transport_case",
    ),
    "public_package": ("public_package_case",),
}


def _inventory_entry(
    capability_id: str,
    owners: tuple[str, ...],
    kinds: tuple[AgentCapabilityEvidenceSourceKind, ...] = (
        AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
    ),
    case_ids: tuple[str, ...] | None = None,
) -> AgentCapabilityInventoryEntry:
    entry = AgentCapabilityInventoryEntry(
        capability_id=capability_id,
        owning_design_refs=owners,
        evidence_source_kinds=kinds,
        case_ids=case_ids or _CASE_IDS_BY_CAPABILITY[capability_id],
    )
    entry.validate()
    return entry


def required_agent_capability_inventory() -> tuple[AgentCapabilityInventoryEntry, ...]:
    """Return the complete, code-owned T2 11 capability inventory."""

    registry_ids = (
        "explicit_module_loading",
        "path_free_release_identity",
        "schema_closure",
        "prompt_closure",
        "policy_closure",
        "profile_independence",
        "generic_profile_compatibility",
        "portable_workflow_registration",
        "per_registry_execution_binding",
    )
    invocation_ids = (
        "inline_semantic_input",
        "structured_output",
        "tool_free_execution",
        "runtime_hosted_self_test",
        "authorized_gateway_read",
        "attempt_workspace",
        "context_isolation",
        "network_enforcement",
        "provider_failure_normalization",
    )
    execution_ids = (
        "module_run",
        "multiple_variants",
        "evaluation_and_selection",
        "retry_budget",
        "idempotent_replay",
        "operation_boundary_enforcement",
        "cancellation",
    )
    workflow_execution_ids = (
        "graph_authoring",
        "sequential_and_branch_routing",
        "parallel_fan_out_and_join",
        "revision_loop",
    )
    durability_ids = ("wait_and_external_event", "crash_recovery")
    evidence_owners = {
        "attempt_and_workflow_ledger": (_LEDGER_DESIGN,),
        "usage_truth": (_LEDGER_DESIGN,),
        "release_inspection": (_INSPECTION_DESIGN, _REGISTRY_DESIGN),
        "execution_inspection": (_INSPECTION_DESIGN, _LEDGER_DESIGN),
    }
    rows = [
        *(_inventory_entry(item, (_REGISTRY_DESIGN,)) for item in registry_ids),
        *(_inventory_entry(item, (_INVOCATION_DESIGN,)) for item in invocation_ids),
        *(_inventory_entry(item, (_EXECUTION_DESIGN,)) for item in execution_ids),
        *(
            _inventory_entry(item, (_EXECUTION_DESIGN,))
            for item in workflow_execution_ids
        ),
        *(_inventory_entry(item, (_DURABILITY_DESIGN,)) for item in durability_ids),
        *(_inventory_entry(item, owners) for item, owners in evidence_owners.items()),
    ]
    environment_specs = {
        "persistent_registry": (_REGISTRY_DESIGN,),
        "persistent_ledger": (_LEDGER_DESIGN,),
        "persistent_inspection": (_INSPECTION_DESIGN, _REGISTRY_DESIGN, _LEDGER_DESIGN),
        "durable_backend": (_DURABILITY_DESIGN,),
        "live_provider_adapter": (_INVOCATION_DESIGN,),
    }
    rows.extend(
        _inventory_entry(
            capability_id,
            owners,
            (AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,),
        )
        for capability_id, owners in environment_specs.items()
    )
    rows.append(
        _inventory_entry(
            "registered_module_transport",
            (_INVOCATION_DESIGN, _REGISTRY_DESIGN),
            (
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE,
                AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            ),
            _CASE_IDS_BY_CAPABILITY["registered_module_transport"],
        )
    )
    rows.append(
        _inventory_entry(
            "public_package",
            (_RELEASE_DESIGN,),
            (AgentCapabilityEvidenceSourceKind.REFERENCED_PEER_RESULT,),
        )
    )
    result = tuple(sorted(rows, key=lambda item: item.capability_id))
    if len(result) != 42 or len({item.capability_id for item in result}) != 42:
        raise ValueError(
            "required Agent capability inventory must contain 42 unique rows"
        )
    return result


def _capability_case(
    *,
    case_id: str,
    capability_ids: tuple[str, ...],
    evidence_source_kind: AgentCapabilityEvidenceSourceKind,
    test_paths: tuple[str, ...],
    environment_prerequisites: tuple[str, ...],
    owning_design_refs: tuple[str, ...],
    example_test_refs: tuple[str, ...],
    input_description: str,
    expected_result: tuple[str, ...],
) -> AgentCapabilityTestCase:
    case = AgentCapabilityTestCase(
        case_id=case_id,
        capability_ids=capability_ids,
        owning_design_refs=owning_design_refs,
        evidence_contract_refs=owning_design_refs,
        evidence_source_kind=evidence_source_kind,
        subject_requirements=(
            "本次 Runtime 候选的准确引用与 hash。",
            "本次测试代码的准确引用与 hash。",
            "本次依赖和配置的准确引用与 hash。",
            input_description,
        ),
        environment_prerequisites=environment_prerequisites,
        command=AgentCapabilityCommand(
            command_id=f"{case_id}_command",
            argv=(*_PYTEST_ARGV, *test_paths),
            working_directory_ref="repo-root:agent-runtime",
        ),
        expected_result=expected_result,
        evidence_outputs=(
            "pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。",
            "样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。",
        ),
        cleanup=("只清理本次用例创建的临时资源，保留需要交付的结果。",),
        failure_routing=(
            "测试装置故障由 Agent Capability Verification 负责人处理。",
            "Runtime 能力缺陷按其所属 Design 与错误含义处理。",
        ),
        rerun_boundary=(
            "Runtime 候选或测试代码变化后重跑。",
            "依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。",
        ),
        example_test_refs=example_test_refs,
    )
    case.validate()
    return case


def required_agent_capability_cases() -> tuple[AgentCapabilityTestCase, ...]:
    """Return the nine canonical executable cases behind the T2 11 inventory."""

    cases = (
        _capability_case(
            case_id="module_release_assembly_case",
            capability_ids=_MODULE_ASSEMBLY_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_module_authoring.py",
                "tests/test_agent_runtime_registry_candidate_compilation.py",
                "tests/test_agent_runtime_reviewer_output_format.py",
            ),
            environment_prerequisites=("安装测试依赖的 Runtime 源码 checkout；无需真实 Provider。",),
            owning_design_refs=(_REGISTRY_DESIGN,),
            example_test_refs=(
                "tests/test_agent_runtime_module_authoring.py::test_module_reviewer_exports_release_and_profile_independently",
                "tests/test_agent_runtime_reviewer_output_format.py::test_different_module_specific_schemas_share_the_format",
            ),
            input_description="测试 fixture 提供完整 Skill/Module source、schemas、policies 和两个不同模型的 Profile。",
            expected_result=(
                "ModuleReviewer.from_registration().export() 在只改 Profile 时保留相同 Module release hash，Variant hash 改变。",
                "不同 Reviewer 的 schema 使用共同输出格式；格式拒绝和兼容负例见同组测试。",
            ),
        ),
        _capability_case(
            case_id="agent_invocation_case",
            capability_ids=_INVOCATION_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_native_structured_output.py",
                "tests/test_agent_runtime_claude_native_tools.py",
                "tests/test_agent_runtime_public_adapter_contracts.py",
                "tests/test_agent_runtime_attempt_workspace.py",
            ),
            environment_prerequisites=("普通用例使用 Provider 测试替身；真实调用的开启方式见批量运行说明。",),
            owning_design_refs=(_INVOCATION_DESIGN,),
            example_test_refs=(
                "tests/test_agent_runtime_native_structured_output.py::test_tool_free_profile_rejects_undeclared_gateway_surface_before_provider",
                "tests/test_agent_runtime_native_structured_output.py::test_gateway_read_authorizes_each_resource_call_and_records_lineage",
            "tests/test_agent_runtime_claude_native_tools.py::test_profile_drives_one_cli_command_builder",
                "tests/test_agent_runtime_claude_native_tools.py::test_live_claude_native_tools_in_ab",
            ),
            input_description="fixture 提供无工具 Profile 或声明 Gateway 的 Profile，以及受控输入和工具响应。",
            expected_result=(
                "无工具 Profile 拒绝未声明 Gateway，Provider 不进入；允许的 Gateway 调用经过逐次授权并记录实际调用。",
                "structured output、workspace、隔离和错误路径由同组测试断言，真实模型结果只由 live 用例证明。",
                "Claude 原生工具配置与实际参数见 agent_runtime_claude_native_tools.md；AB 正向、越界及注册 Reviewer/PG 用例在同一测试文件。",
            ),
        ),
        _capability_case(
            case_id="module_execution_case",
            capability_ids=_EXECUTION_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_execution_records.py",
                "tests/test_agent_runtime_execution_authorization.py",
                "tests/test_agent_runtime_module_evaluation.py",
                "tests/test_agent_runtime_registered_module_execution.py",
                "tests/test_agent_runtime_terminal_evidence.py",
            ),
            environment_prerequisites=(
                "普通用例使用内存 stores 和 Provider 测试替身。",
                "本组的 PG 用例需要 AGENT_RUNTIME_TEST_DATABASE_URL；未提供时这些用例跳过。",
            ),
            owning_design_refs=(_EXECUTION_DESIGN,),
            example_test_refs=(
                "tests/test_agent_runtime_registered_module_execution.py::test_candidate_result_replay_preserves_policy_and_recorded_bytes",
                "tests/test_agent_runtime_registered_module_execution.py::test_invalid_input_stops_before_authorization_or_provider",
                "tests/test_agent_runtime_terminal_evidence.py::test_sequential_variants_use_their_own_attempt_start",
            ),
            input_description='fixture 注册一节点 Workflow；run_registered_workflow_module 接收 {"value": "example"} 和同一个 key。隔离 Module 的计时样例使用两个 Variant，每次预算 10 秒，耗时为 (6, 6) 或 (6, 11) 秒。',
            expected_result=(
                '首次执行输出 {"value": "done"}；同 key 重放返回相同 execution 和输出，Provider 替身只调用一次。',
                "evaluated_single 保持未决 candidate，没有伪造 Resolution；非法输入在授权或 Provider 调用前拒绝。",
                "隔离入口的每个 Variant 独立计时：预算各 10 秒、依次各用 6 秒时都完成；真实超时仍失败。",
            ),
        ),
        _capability_case(
            case_id="workflow_graph_case",
            capability_ids=_WORKFLOW_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_workflow_authoring.py",
                "tests/test_agent_runtime_parallel_workflow.py",
                "tests/test_agent_runtime_product_host_execution_api.py",
            ),
            environment_prerequisites=("本组使用内存 Registry 与受控执行替身，不启动真实 Temporal。",),
            owning_design_refs=(_REGISTRY_DESIGN, _EXECUTION_DESIGN),
            example_test_refs=(
                "tests/test_agent_runtime_workflow_authoring.py::test_same_workflow_export_registers_into_two_independent_registries",
                "tests/test_agent_runtime_parallel_workflow.py::test_parallel_group_dispatches_branches_concurrently_and_joins_once",
            ),
            input_description="同一 Workflow export 注册到两个独立 Registry；并行 fixture 声明两个 branch 和一个 join。",
            expected_result=(
                "同一 origin 可以复用，两个 Registry 的执行配置独立。",
                "两个 branch 并发执行，join 执行一次；恢复与失败路径见同组回归测试。",
            ),
        ),
        _capability_case(
            case_id="ledger_inspection_case",
            capability_ids=_EVIDENCE_CAPABILITIES,
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.EXECUTABLE_OWNER_CASE
            ),
            test_paths=(
                "tests/test_agent_runtime_execution_inspection.py",
                "tests/test_agent_runtime_release_inspection.py",
                "tests/test_workflow_execution_ledger_recording.py",
            ),
            environment_prerequisites=("使用固定的 execution trace 与 release fixtures，无需数据库或模型。",),
            owning_design_refs=(_LEDGER_DESIGN, _INSPECTION_DESIGN),
            example_test_refs=(
                "tests/test_agent_runtime_execution_inspection.py::test_execution_trace_projects_directly_to_portable_inspector_view",
                "tests/test_agent_runtime_release_inspection.py::test_release_inventory_projects_prompt_components_under_schema_v4",
            ),
            input_description="输入为 fixture 中已记录的 Workflow、Attempt、usage、输入输出引用及 Registry facts。",
            expected_result=(
                "Inspector 从记录生成只读视图；样例的 Module 已完成，但 Workflow 仍为 running，不能推断整体结束。",
                "usage 和 release 内容与原始记录一致，Inspection 不成为第二份 Ledger。",
            ),
        ),
        _capability_case(
            case_id="persistent_runtime_case",
            capability_ids=_PERSISTENT_CAPABILITIES,
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=(
                "tests/test_agent_runtime_postgres_release_store.py",
                "tests/test_agent_runtime_postgres_execution_ledger.py",
                "tests/test_agent_runtime_registered_module_execution.py",
                "tests/test_agent_runtime_terminal_evidence.py",
            ),
            environment_prerequisites=(
                "调用者显式提供 AGENT_RUNTIME_TEST_DATABASE_URL；现有 fixtures 创建和清理独立临时 schema。",
                "SQL_ASCII 数据库可配置 PGCLIENTENCODING=UTF8；不修改服务器编码。",
            ),
            owning_design_refs=(_REGISTRY_DESIGN, _LEDGER_DESIGN, _INSPECTION_DESIGN),
            example_test_refs=(
                "tests/test_agent_runtime_registered_module_execution.py::test_postgres_fresh_read_and_replay_preserve_candidate_policy",
                "tests/test_agent_runtime_registered_module_execution.py::test_postgres_concurrent_first_call_has_one_provider_entry",
                "tests/test_agent_runtime_terminal_evidence.py::test_postgres_attempt_clock_excludes_run_preparation",
                "tests/test_agent_runtime_terminal_evidence.py::test_postgres_existing_attempt_start_never_refreshes_budget",
            ),
            input_description="在临时 PG stores 执行一节点 Workflow，分别使用正常输出、Provider 失败和 schema 失败 fixture。",
            expected_result=(
                "新建查询连接能读取相同执行记录和内容 hash；重放不重复调用 Provider，不伪造 candidate Resolution。",
                "同 key 并发首次调用只有一个 Provider 入口；PG 是真实连接，Provider 仍是测试替身。",
                "Attempt 起点不包含 Run 准备时间；已有开始记录不刷新预算，已完成结果重放不再次调用。",
            ),
        ),
        _capability_case(
            case_id="durable_backend_case",
            capability_ids=("durable_backend",),
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=(
                "tests/test_agent_runtime_temporal_integration.py",
                "tests/test_agent_runtime_temporal_target_adapter.py",
            ),
            environment_prerequisites=(
                "设置 RUN_TEMPORAL_INTEGRATION=1，并安装 temporal 测试依赖；fixtures 启动本地 Temporal dev server。",
                "SDK 可能需要取得 server binary；先准备运行环境。未开启时真实集成用例跳过。",
            ),
            owning_design_refs=(_DURABILITY_DESIGN,),
            example_test_refs=(
                "tests/test_agent_runtime_temporal_integration.py::test_real_temporal_two_cell_durable_execution",
                "tests/test_agent_runtime_temporal_target_adapter.py::test_real_target_temporal_cancellation_is_replay_safe",
            ),
            input_description="fixtures 提供两个 Cell、独立 worker、事件及取消请求，并在本地测试 server 上运行。",
            expected_result=(
                "真实 Temporal 验证隔离、事件恢复、重放与 worker 恢复；取消重放不重复产生影响。",
                "测试结束按既有 fixture 关闭 worker 和 server；纯内存测试不能替代这些真实集成结果。",
            ),
        ),
        _capability_case(
            case_id="live_module_transport_case",
            capability_ids=_LIVE_TRANSPORT_CAPABILITIES,
            evidence_source_kind=AgentCapabilityEvidenceSourceKind.ENVIRONMENT_GATE,
            test_paths=(
                "tests/test_agent_runtime_native_structured_output.py",
                "tests/test_agent_runtime_managed_design_reviewer.py",
            ),
            environment_prerequisites=(
                "设置 RUN_PROVIDER_INTEGRATION=1，准备本节测试使用的 Codex CLI 和有效登录。Claude 原生工具用例的命令及环境要求见 `agent_runtime_claude_native_tools.md`，同样需要显式开启；Runtime 不依赖 Claude Agent SDK。",
                "真实调用可能计费并消耗额度；先检查测试中的模型、超时、工具和环境设置，不自动登录。",
            ),
            owning_design_refs=(_INVOCATION_DESIGN, _REGISTRY_DESIGN),
            example_test_refs=(
                "tests/test_agent_runtime_native_structured_output.py::test_live_codex_evaluation_runs_through_run_module",
                "tests/test_agent_runtime_managed_design_reviewer.py::test_registered_design_reviewer_runs_live_opus_5_through_runtime",
            ),
            input_description="已注册测试 Module、固定 prompt/input/schema 与 transport Profile；审核 smoke 消费受控 Design fixture。",
            expected_result=(
                "真实调用通过 Runtime 交付输入、验证输出并记录执行；Codex/Claude 的不同用例各自提供证据。",
                "Reviewer 返回合法 non_pass 或 blocked 不代表 transport 失败；Provider 退出零也不代表输出校验通过。",
            ),
        ),
        _capability_case(
            case_id="public_package_case",
            capability_ids=("public_package",),
            evidence_source_kind=(
                AgentCapabilityEvidenceSourceKind.REFERENCED_PEER_RESULT
            ),
            test_paths=(
                "tests/test_agent_runtime_packaging_boundary.py",
                "tests/test_agent_runtime_conformance_package.py",
            ),
            environment_prerequisites=("准备已有 build/test 依赖；测试在临时源码和安装目录中构建 wheel，不联网安装依赖。",),
            owning_design_refs=(_RELEASE_DESIGN,),
            example_test_refs=(
                "tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_import_uses_public_namespace_without_domain_packages",
                "tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_executes_target_release_registry_module_slice",
            ),
            input_description="从 Runtime 源码构建并安装临时 wheel，在独立解释器里使用 public API。",
            expected_result=(
                "wheel 的 public import 与注册执行样例不依赖宿主 domain package。",
                "随包 docs 与源码 docs 一致；这些测试结果不替代完整验证所需的独立 package conformance 结果。",
            ),
        ),
    )
    validate_exact_record_tuple(
        "required capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    return cases


def _validated_capability_case_map(
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    cases: tuple[AgentCapabilityTestCase, ...],
) -> dict[str, AgentCapabilityTestCase]:
    validate_exact_record_tuple(
        "capability inventory",
        inventory,
        expected_type=AgentCapabilityInventoryEntry,
        item_validator=lambda entry: entry.validate(),
        unique_key=lambda entry: entry.capability_id,
        unique_key_label="capability_id",
        require_non_empty=True,
    )
    validate_exact_record_tuple(
        "capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    cases_by_id = {case.case_id: case for case in cases}
    for entry in inventory:
        for case_id in entry.case_ids:
            case = cases_by_id.get(case_id)
            if case is None or entry.capability_id not in case.capability_ids:
                raise ValueError(
                    "capability inventory and case mapping must close exactly"
                )
    return cases_by_id


def _markdown_items(values: tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


_CASE_LABELS = {
    "module_release_assembly_case": "编译 Module 与检查 Reviewer 格式",
    "agent_invocation_case": "模型输入输出与工具边界",
    "module_execution_case": "调用已注册 Module 与重放",
    "workflow_graph_case": "Workflow 注册、并行与恢复",
    "ledger_inspection_case": "查看执行与注册记录",
    "persistent_runtime_case": "真实 PostgreSQL 写入与重新读取",
    "durable_backend_case": "真实 Temporal 恢复与取消",
    "live_module_transport_case": "真实 Provider 与 Reviewer 调用",
    "public_package_case": "独立安装与 public API",
}


def _case_sort_key(case_id: str) -> tuple[int, str]:
    order = tuple(_CASE_LABELS)
    return (order.index(case_id) if case_id in order else len(order), case_id)


def render_agent_capability_catalog_markdown(
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render the stable public capability catalog from code-owned truth."""

    cases_by_id = _validated_capability_case_map(inventory, cases)
    capability_count = len(inventory)
    rows = []
    for case_id in sorted(cases_by_id, key=_case_sort_key):
        case = cases_by_id[case_id]
        capabilities = ", ".join(
            f"`{capability_id}`" for capability_id in case.capability_ids
        )
        rows.append(
            f"| [{_CASE_LABELS.get(case.case_id, case.case_id)}]"
            f"(agent_runtime_capability_runbook.md#{case.case_id}) | "
            f"`{case.case_id}` | "
            f"{capabilities} |"
        )
    return (
        "# Agent Runtime 能力与样例索引\n\n"
        "Runtime 负责注册 Module 和 Workflow、执行模型与工具调用、协调恢复并记录执行事实。"
        "业务角色、prompt、授权规则和业务数据由宿主提供。\n\n"
        f"代码定义了 {capability_count} 项验证能力，分为 {len(cases_by_id)} 组。"
        "点击使用任务可直接找到样例的测试位置、输入、预期结果和命令。"
        "能力列是验证清单，不表示这些能力已全部完成或通过。\n\n"
        "## 1. 按任务找到样例\n\n"
        "| 使用任务 | Case | 验证能力 |\n"
        "| --- | --- | --- |\n"
        + "\n".join(rows)
        + "\n\n"
        "## 2. 运行与解释结果\n\n"
        "[Runbook](agent_runtime_capability_runbook.md) 提供单例、分组、全仓批量命令及环境要求。"
        "完整测试目录由 pytest 自动收集；上表只是面向使用者的能力分组，不能代替全仓回归。\n\n"
        "- focused 结果只证明所选用例；正确拒绝预先声明的负例可以算该用例通过。\n"
        "- 环境缺失或未开启的真实集成测试是未执行，不能计为 passed。\n"
        "- Provider 调用成功、输出有效与被审对象获准是不同结果。\n"
        "- 完整 T2 11 还要求 canonical Example、完整能力和所需环境证据；"
        "现有 pytest 结果不自动生成这一完成结论。\n\n"
        "当前通过数、失败和环境观察以本次实际测试报告为准，不在本索引手工填写。\n"
    )


def render_agent_capability_runbook_markdown(
    cases: tuple[AgentCapabilityTestCase, ...],
) -> str:
    """Render the operator runbook from the executable case definitions."""

    validate_exact_record_tuple(
        "capability cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    ordered = sorted(cases, key=lambda item: _case_sort_key(item.case_id))
    index = "\n".join(
        f"- [{_CASE_LABELS.get(case.case_id, case.case_id)}](#{case.case_id})"
        for case in ordered
    )
    sections = [
        "# Agent Runtime 测试与样例 runbook\n\n"
        "从 [README](../README.md) 或 [能力索引](agent_runtime_capabilities.md) 进入。"
        "本文由同一份 AgentCapabilityTestCase 生成，帮助使用者找到实际样例并重复运行测试。\n\n"
        "## 1. 阅读和运行环境\n\n"
        "wheel 随附本文档；完整测试和 fixtures 位于同版本 Runtime 源码 checkout。"
        "以下路径均相对源码根目录，命令中的 python 指安装了 Runtime 与测试依赖的解释器。"
        "测试依赖、可选 Provider/PG/Temporal 依赖以 pyproject.toml 为准。\n\n"
        "每组的样例 selector 指向真实测试函数。先读该函数及它直接使用的 fixture，"
        "再运行对应命令；fixture 中的 Provider 替身和测试授权对象用于验证，不是生产宿主配置。"
        "这些是已有能力的具体样例，尚未实现的完整 agent_capability_example Workflow 不在其中。\n\n"
        "### 1.1 Runtime 与宿主环境各准备什么\n\n"
        "宿主是使用 Runtime 的项目，例如 Analyst Billie。宿主决定执行什么、使用哪个环境；"
        "Runtime 提供执行、隔离、记录与读取能力。一次实际调用并不需要宿主另写一套执行器。\n\n"
        "| 内容 | Runtime 提供 | 宿主提供 |\n"
        "| --- | --- | --- |\n"
        "| 模型与工具执行 | 公共调用入口、Provider Adapter、参数组装、进程与结果处理 | "
        "Module/Profile 的选择、CLI 安装与登录、实际程序路径 |\n"
        "| 文件与运行依赖 | 按授权范围准备 Attempt 工作区、落实读写和网络限制、清理本次临时资源 | "
        "任务材料、可用工作区根、Python/Git 等只读依赖 |\n"
        "| 注册与执行记录 | Registry/Ledger 的表结构、安装与迁移 API、写入与查询实现 | "
        "PG 连接、独立 schema、数据访问授权；管理员按需调用安装/迁移 API |\n"
        "| 审核内容 | 执行已注册 Module、校验输出结构、保存真实结果 | "
        "所属 Skill Package 的 prompt/schema、审核任务及所属语义 validator |\n"
        "| 测试与验收 | 固定 sample、测试入口与执行证据 | "
        "本次实验目标、所选用例、环境配置与结果判断标准 |\n\n"
        "宿主可以保存配置并组合这些公开 API，不应复制 Claude 启动、超时处理、"
        "事件解析或 PG 记录代码。Runtime 的 PG 表结构与实现属于 Runtime；连接哪一个数据库、"
        "选择哪些 schema 属于宿主。注册接口不自动建库或变更管理员权限。\n\n"
        "环境缺件与 Runtime 缺陷分开处理：缺 CLI、登录、依赖目录或 PG 配置，补宿主环境；"
        "公共入口不能执行相容 Profile、失败日志没有保存或已保存记录无法回读，修 Runtime。"
        "测试授权替身只留在测试里，不能直接当作宿主正式授权实现。\n\n"
        "Claude 工具与 AB 持久审核的具体准备表见 "
        "[Claude sample 环境说明](agent_runtime_claude_native_tools.md#11-宿主需要准备的环境)。"
        "Module/Workflow 注册步骤见 [Registration Runbook](agent_runtime_registration_runbook.md)。\n\n"
        "### 1.2 自定义 ModuleExecutionLedger\n\n"
        "公开注册执行入口使用 Runtime 内置 Ledger。直接调用 run_module 或 run_workflow_module 并提供"
        "自定义 ModuleExecutionLedger 时，还需实现 record_attempt_start(started: ModuleAttemptStartedRecord)。"
        "Kernel 先用 begin 登记 Run/Variant（attempt_starts 为空），再于各 Attempt 实际开始时记录"
        "既有 StartRecord。缺少该接口会在新增执行记录前拒绝。相同 ID 的开始时间不可被刷新；"
        "Workflow 路径沿用 PG 中原已保存的 Runtime 时间。\n\n"
        "## 2. 单例、分组与全仓批量运行\n\n"
        "单例和分组命令见第 4 节。全仓使用 pytest 自动收集，不需要手工维护另一个测试列表。"
        "可以先列出所有实际测试，再执行：\n\n"
        "```sh\npython -B -m pytest --collect-only -q\n"
        f"{shlex.join(_PYTEST_ARGV)}\n```\n\n"
        "默认不开启真实 Provider 和 Temporal。为避免继承了已开启的环境变量，普通批次可显式设置：\n\n"
        "```sh\nRUN_PROVIDER_INTEGRATION=0 RUN_TEMPORAL_INTEGRATION=0 "
        f"{shlex.join(_PYTEST_ARGV)}\n```\n\n"
        "提供 AGENT_RUNTIME_TEST_DATABASE_URL 后，同一批次也会运行 PG 测试；"
        "不提供时真实 PG 用例跳过。只使用已授权的测试数据库，fixtures 负责临时 schema 的创建和清理。"
        "SQL_ASCII 测试数据库可在连接环境设置 PGCLIENTENCODING=UTF8，不改变数据库编码。\n\n"
        "需要真实环境时，按第 4 节准备依赖，再显式开启 RUN_TEMPORAL_INTEGRATION=1 或 "
        "RUN_PROVIDER_INTEGRATION=1。Provider 调用可能计费；后者会开启现有 Codex 和 Claude 用例，"
        "不是选择某一个模型。模型、工具、超时和登录要求以所选测试的 Profile/fixture 为准。"
        "公共文档不填写 DSN、token 或密码。\n\n"
        "向同一 pytest 命令添加 --junitxml=PATH 可导出机器可读结果。PATH 由调用者选择新的报告路径，"
        "避免覆盖旧报告；pytest 默认终端输出已含真实计数与 -rs 的跳过原因。\n\n"
        "## 3. 怎样判断这一批是否完成\n\n"
        "pytest 退出零表示实际执行的断言未失败，不表示所有用例都执行了。"
        "先核对本次选择、通过数、失败数和 skip 原因。必需用例仍未运行时，这一批的验证尚未完成。"
        "JUnit 中的 skipped 也不能算通过；CI 消费报告时要保留这一差异。\n\n"
        "分组中的测试文件可能含有其他回归或集成测试，同一文件也可能服务多个能力分组。"
        "若要一次运行全仓，直接使用全仓命令，避免把所有分组命令串起来重复执行。"
        "正确拒绝负例可以通过；模型返回合法的 non_pass 或 blocked 也可能满足 transport 测试。\n\n"
        "当前批次结果不能替代完整 T2 11 的 AgentCapabilityVerificationResult。"
        "完整 Example、所需真实环境或完整能力证据未齐时，保留未完成状态。\n\n"
        "## 4. 用例索引\n\n" + index + "\n"
    ]
    for number, case in enumerate(ordered, 1):
        capabilities = ", ".join(
            f"`{capability_id}`" for capability_id in case.capability_ids
        )
        sections.append(
            f'\n<a id="{case.case_id}"></a>\n\n'
            f"### 4.{number} {_CASE_LABELS.get(case.case_id, case.case_id)}\n\n"
            f"Case：`{case.case_id}`；证据来源：`{case.evidence_source_kind.value}`。\n\n"
            f"验证能力：{capabilities}\n\n"
            "输入与 fixtures：\n\n"
            f"{_markdown_items(case.subject_requirements)}\n\n"
            "前置环境：\n\n"
            f"{_markdown_items(case.environment_prerequisites)}\n\n"
            "样例代码位置及单例命令：\n\n"
            + (
                "\n\n".join(
                    f"`{ref}`\n\n```sh\n{shlex.join((*_PYTEST_ARGV, ref))}\n```"
                    for ref in case.example_test_refs
                ) if case.example_test_refs else "本 case 未提供样例导航；使用下方分组命令。"
            )
            + "\n\n整组命令：\n\n"
            f"```sh\n{shlex.join(case.command.argv)}\n```\n\n"
            "预期结果：\n\n"
            f"{_markdown_items(case.expected_result)}\n\n"
            "结果与证据：\n\n"
            f"{_markdown_items(case.evidence_outputs)}\n\n"
            "清理：\n\n"
            f"{_markdown_items(case.cleanup)}\n\n"
            "失败处理：\n\n"
            f"{_markdown_items(case.failure_routing)}\n\n"
            "需要重跑的变化：\n\n"
            f"{_markdown_items(case.rerun_boundary)}\n"
        )
    sections.append(
        "\n## 5. 更新用例与重新生成文档\n\n"
        "能力分组和样例导航在 conformance_agent_capability_verification.py 中维护。"
        "example_test_refs 指向真实测试函数；新增普通回归放入 tests 后由 pytest 自动收集，"
        "涉及能力分组时再更新对应 case。不要手工同时编辑 docs 与随包投影。"
        "Claude 环境说明维护在 src/agent_runtime/docs/agent_runtime_claude_native_tools.md，"
        "docs 下的同名页由下面的命令复制。\n\n"
        "从源码根目录调用现有 renderer 更新两份文档：\n\n"
        "```python\nfrom pathlib import Path\n"
        "from agent_runtime.testing.conformance_agent_capability_verification import (\n"
        "    required_agent_capability_cases, required_agent_capability_inventory,\n"
        "    render_agent_capability_catalog_markdown, render_agent_capability_runbook_markdown,\n)\n\n"
        "cases = required_agent_capability_cases()\n"
        "documents = {\n"
        '    "agent_runtime_capabilities.md": render_agent_capability_catalog_markdown(\n'
        "        required_agent_capability_inventory(), cases),\n"
        '    "agent_runtime_capability_runbook.md": render_agent_capability_runbook_markdown(cases),\n}\n'
        'documents["agent_runtime_claude_native_tools.md"] = Path(\n'
        '    "src/agent_runtime/docs/agent_runtime_claude_native_tools.md"\n'
        ').read_text(encoding="utf-8")\n'
        'for directory in (Path("docs"), Path("src/agent_runtime/docs")):\n'
        "    for name, body in documents.items():\n"
        '        (directory / name).write_text(body, encoding="utf-8")\n```\n\n'
        "生成后运行以下检查，再按第 2 节跑本次需要的全仓批次：\n\n"
        f"```sh\n{shlex.join((*_PYTEST_ARGV, 'tests/test_agent_runtime_capability_verification.py'))}\n```\n"
    )
    return "".join(sections)


@dataclass(frozen=True)
class AgentCapabilityVerificationRequest:
    """Exact subject, suite, scope and selection for one verification run."""

    request_id: str
    subject_ref: str
    subject_sha256: str
    suite_ref: str
    suite_sha256: str
    dependency_closure_ref: str
    dependency_closure_sha256: str
    scope: AgentCapabilityVerificationScope
    selected_case_ids: tuple[str, ...]
    standalone_conformance_ref: str | None = None
    standalone_conformance_sha256: str | None = None

    def validate(self) -> None:
        validate_id("request_id", self.request_id)
        for label, value in (
            ("subject_ref", self.subject_ref),
            ("suite_ref", self.suite_ref),
            ("dependency_closure_ref", self.dependency_closure_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("subject_sha256", self.subject_sha256),
            ("suite_sha256", self.suite_sha256),
            ("dependency_closure_sha256", self.dependency_closure_sha256),
        ):
            validate_sha256(label, value)
        if type(self.scope) is not AgentCapabilityVerificationScope:
            raise ValueError("scope must be an exact AgentCapabilityVerificationScope")
        validate_string_tuple(
            "selected_case_ids",
            self.selected_case_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )
        if (self.standalone_conformance_ref is None) != (
            self.standalone_conformance_sha256 is None
        ):
            raise ValueError("standalone conformance ref/hash must be paired")
        if self.standalone_conformance_ref is not None:
            validate_opaque_ref(
                "standalone_conformance_ref",
                self.standalone_conformance_ref,
            )
            validate_sha256(
                "standalone_conformance_sha256",
                self.standalone_conformance_sha256,
            )


@dataclass(frozen=True)
class AgentCapabilityCaseResult:
    """One exact case observation without implied sibling success."""

    case_id: str
    capability_ids: tuple[str, ...]
    state: AgentCapabilityResultState
    evidence_refs: tuple[str, ...]
    failure_code: str | None = None
    failure_owner_ref: str | None = None
    transport_capability: AgentCapabilityTransportCapability | None = None

    def validate(self) -> None:
        validate_id("case_id", self.case_id)
        validate_string_tuple(
            "capability_ids",
            self.capability_ids,
            item_validator=lambda label, value: validate_id(label, value),
            require_non_empty=True,
        )
        if type(self.state) is not AgentCapabilityResultState:
            raise ValueError("state must be an exact AgentCapabilityResultState")
        validate_string_tuple(
            "evidence_refs",
            self.evidence_refs,
            item_validator=_validate_bounded_text,
            require_non_empty=self.state is not AgentCapabilityResultState.NOT_RUN,
        )
        if (self.failure_code is None) != (self.failure_owner_ref is None):
            raise ValueError("failure code and owner must be paired")
        if (
            self.state is AgentCapabilityResultState.PASSED
            and self.failure_code is not None
        ):
            raise ValueError("passed case forbids failure")
        if (
            self.state is not AgentCapabilityResultState.PASSED
            and self.failure_code is None
        ):
            raise ValueError("failed or not_run case requires failure")
        if self.failure_owner_ref is not None:
            _validate_bounded_text("failure_owner_ref", self.failure_owner_ref)
            _validate_bounded_text("failure_code", self.failure_code)
        if self.transport_capability is not None:
            if self.state is not AgentCapabilityResultState.PASSED:
                raise ValueError("transport capability requires a passed case")
            if (
                type(self.transport_capability)
                is not AgentCapabilityTransportCapability
            ):
                raise ValueError("invalid transport_capability")


@dataclass(frozen=True)
class AgentCapabilityVerificationResult:
    """Completed focused or complete verification without false aggregation."""

    request_id: str
    subject_ref: str
    subject_sha256: str
    suite_ref: str
    suite_sha256: str
    dependency_closure_ref: str
    dependency_closure_sha256: str
    scope: AgentCapabilityVerificationScope
    case_results: tuple[AgentCapabilityCaseResult, ...]
    scope_completed: bool
    full_runtime_completed: bool

    def validate(self) -> None:
        validate_id("request_id", self.request_id)
        for label, value in (
            ("subject_ref", self.subject_ref),
            ("suite_ref", self.suite_ref),
            ("dependency_closure_ref", self.dependency_closure_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("subject_sha256", self.subject_sha256),
            ("suite_sha256", self.suite_sha256),
            ("dependency_closure_sha256", self.dependency_closure_sha256),
        ):
            validate_sha256(label, value)
        if type(self.scope) is not AgentCapabilityVerificationScope:
            raise ValueError("scope must be an exact AgentCapabilityVerificationScope")
        validate_exact_record_tuple(
            "case_results",
            self.case_results,
            expected_type=AgentCapabilityCaseResult,
            item_validator=lambda result: result.validate(),
            unique_key=lambda result: result.case_id,
            unique_key_label="case_id",
            require_non_empty=True,
        )
        validate_bool("scope_completed", self.scope_completed)
        validate_bool("full_runtime_completed", self.full_runtime_completed)
        if self.full_runtime_completed and (
            self.scope is not AgentCapabilityVerificationScope.COMPLETE
            or not self.scope_completed
        ):
            raise ValueError(
                "full Runtime completion requires completed complete scope"
            )


CaseExecutor = Callable[[AgentCapabilityTestCase], AgentCapabilityCaseResult]


def run_agent_capability_verification(
    request: AgentCapabilityVerificationRequest,
    *,
    cases: tuple[AgentCapabilityTestCase, ...],
    inventory: tuple[AgentCapabilityInventoryEntry, ...],
    execute_case: CaseExecutor,
) -> AgentCapabilityVerificationResult:
    """Run exactly the selected cases and compute completion without propagation."""

    request.validate()
    validate_exact_record_tuple(
        "cases",
        cases,
        expected_type=AgentCapabilityTestCase,
        item_validator=lambda case: case.validate(),
        unique_key=lambda case: case.case_id,
        unique_key_label="case_id",
        require_non_empty=True,
    )
    validate_exact_record_tuple(
        "inventory",
        inventory,
        expected_type=AgentCapabilityInventoryEntry,
        item_validator=lambda entry: entry.validate(),
        unique_key=lambda entry: entry.capability_id,
        unique_key_label="capability_id",
        require_non_empty=True,
    )
    cases_by_id = {case.case_id: case for case in cases}
    selected = tuple(request.selected_case_ids)
    if any(case_id not in cases_by_id for case_id in selected):
        raise AgentCapabilityVerificationError(
            AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
            "unknown selected case",
        )
    required_case_ids = {
        case_id for entry in inventory for case_id in entry.case_ids
    }
    if request.scope is AgentCapabilityVerificationScope.COMPLETE:
        if {entry.capability_id for entry in inventory} != {
            entry.capability_id for entry in required_agent_capability_inventory()
        }:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "complete inventory required",
            )
        if set(selected) != required_case_ids:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "complete case set required",
            )
        if request.standalone_conformance_ref is None:
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_COVERAGE_INCOMPLETE,
                "package result required",
            )
    results = []
    for case_id in selected:
        case = cases_by_id[case_id]
        result = execute_case(case)
        validate_exact_record_instance(
            "case result",
            result,
            expected_type=AgentCapabilityCaseResult,
        )
        result.validate()
        if result.case_id != case.case_id or set(result.capability_ids) != set(
            case.capability_ids
        ):
            raise AgentCapabilityVerificationError(
                AGENT_CAPABILITY_TEST_FAILED,
                "case result identity mismatch",
            )
        results.append(result)
    result_tuple = tuple(results)
    scope_completed = all(
        item.state is AgentCapabilityResultState.PASSED for item in result_tuple
    )
    # The current plan intentionally excludes the canonical Example and live
    # registered Module transport evidence. A complete run can execute its selected
    # cases, but this implementation must not claim full Runtime completion.
    full_runtime_completed = False
    verification_result = AgentCapabilityVerificationResult(
        request_id=request.request_id,
        subject_ref=request.subject_ref,
        subject_sha256=request.subject_sha256,
        suite_ref=request.suite_ref,
        suite_sha256=request.suite_sha256,
        dependency_closure_ref=request.dependency_closure_ref,
        dependency_closure_sha256=request.dependency_closure_sha256,
        scope=request.scope,
        case_results=result_tuple,
        scope_completed=scope_completed,
        full_runtime_completed=full_runtime_completed,
    )
    verification_result.validate()
    return verification_result


__all__ = [
    "AgentCapabilityCommand",
    "AgentCapabilityEvidenceSourceKind",
    "AgentCapabilityTestCase",
    "render_agent_capability_runbook",
]
