"""Load one explicit local Runtime authoring inventory into path-free values."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping

from ..contracts.registry_release_definition import (
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    WorkflowNodeKind,
    WorkflowParallelJoinPolicy,
)
from ..foundation.foundation_contract_validation import validate_snake_case_name
from .registry_release_compilation import (
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    RetryPolicyReleaseCandidate,
)


RUNTIME_AUTHORING_INVENTORY_SCHEMA_VERSION = "runtime_authoring_inventory_v1"
RUNTIME_MODULE_REGISTRATION_SCHEMA_VERSION = "runtime_module_registration_v3"

_SOURCE_KINDS = frozenset(
    {
        "agent_module",
        "behavior_policy",
        "evaluation_policy",
        "execution_profile",
        "execution_variant_policy",
        "non_agent_module",
        "retry_policy",
        "workflow",
    }
)
_INVENTORY_KEYS = frozenset(
    {
        "schema_version",
        "inventory_id",
        "inventory_version",
        "plugin_id",
        "plugin_version",
        "sources",
        "root_workflow_source_ids",
    }
)
_SOURCE_SELECTION_KEYS = frozenset(
    {"source_kind", "source_id", "manifest_path"}
)
_MODULE_KEYS = frozenset(
    {
        "schema_version",
        "source_kind",
        "skill_id",
        "skill_projection_path",
        "module_id",
        "module_version",
        "module_kind",
        "owner_contract_ref",
        "owner_contract_path",
        "instruction_source_ref",
        "input_schema_ref",
        "input_schema_path",
        "output_schema_ref",
        "output_schema_path",
        "executable_ref",
        "executable_member_path",
        "declared_operation_ids",
        "compatible_transport_kinds",
        "behavior_policy_ref",
        "evaluation_policy_ref",
        "retry_policy_ref",
        "entry_policy",
        "output_resolution_policy",
    }
)
_BEHAVIOR_POLICY_KEYS = frozenset(
    {"schema_version", "policy_id", "policy_version", "context_isolation"}
)
_EVALUATION_POLICY_KEYS = frozenset(
    {"schema_version", "policy_id", "policy_version", "evaluation_mode"}
)
_RETRY_POLICY_KEYS = frozenset(
    {"schema_version", "policy_id", "policy_version", "max_attempts"}
)
_EXECUTION_PROFILE_KEYS = frozenset(
    {
        "schema_version",
        "execution_profile_id",
        "release_version",
        "executor_adapter_id",
        "executor_adapter_revision",
        "transport_kind",
        "provider_id",
        "model_id",
        "reasoning_profile",
        "execution_mode",
        "semantic_input_delivery_mode",
        "attempt_workspace_policy",
        "gateway_access_reasons",
        "output_constraint_mode",
        "tool_policy",
        "network_policy",
        "timeout_seconds",
    }
)
_WORKFLOW_KEYS = frozenset(
    {
        "schema_version",
        "workflow_id",
        "workflow_version",
        "workflow_contract_version",
        "owner_contract_ref",
        "owner_contract_path",
        "graph_ref",
        "initial_node_id",
        "nodes",
        "edges",
        "parallel_groups",
        "authorization_manifest_ref",
        "authorization_manifest_document",
        "execution_binding_ref",
        "execution_binding_document",
    }
)
_WORKFLOW_NODE_KEYS = frozenset(
    {
        "node_id",
        "node_kind",
        "module_source_id",
        "input_mapping_ref",
        "input_mapping_document",
    }
)
_WORKFLOW_EDGE_KEYS = frozenset(
    {"source_node_id", "outcome_id", "target_node_id", "terminal"}
)
_PARALLEL_GROUP_KEYS = frozenset(
    {
        "group_id",
        "control_node_id",
        "branch_node_ids",
        "join_node_id",
        "completion_outcome_id",
        "join_policy",
    }
)
_VARIANT_POLICY_KEYS = frozenset(
    {
        "schema_version",
        "policy_id",
        "policy_version",
        "origin_kind",
        "origin_source_id",
        "bindings",
    }
)
_VARIANT_BINDING_KEYS = frozenset(
    {"position_id", "execution_profile_source_id"}
)
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_KEBAB_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_CONTRACT_REF_PATTERN = re.compile(
    r"^contract:[a-z][a-z0-9_]*@[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_AUTHORING_REF_PATTERN = re.compile(
    r"^authoring-source:[a-z][a-z0-9_]*@[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_PYTHON_REF_PATTERN = re.compile(
    r"^python:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)
_SERVICE_REF_PATTERN = re.compile(
    r"^service:[a-z][a-z0-9_]*@[A-Za-z0-9][A-Za-z0-9_.-]*$"
)


@dataclass(frozen=True)
class _SourceSelection:
    source_kind: str
    source_id: str
    manifest_path: str


@dataclass(frozen=True)
class _RuntimeAuthoringInventory:
    inventory_id: str
    inventory_version: str
    plugin_id: str
    plugin_version: str
    sources: tuple[_SourceSelection, ...]
    root_workflow_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class _AuthoringContentMember:
    member_ref: str
    member_sha256: str

    def as_dict(self) -> dict[str, str]:
        return {
            "member_ref": self.member_ref,
            "member_sha256": self.member_sha256,
        }


@dataclass(frozen=True)
class _ModuleSource:
    source_id: str
    module_id: str
    module_version: str
    module_kind: ModuleKind
    owner_contract_ref: str
    owner_contract_content: str
    instruction_source_ref: str | None
    instruction_text: str | None
    executable_ref: str | None
    executable_content: bytes | None
    input_schema_ref: str
    input_schema_document: str
    output_schema_ref: str
    output_schema_document: str
    declared_operation_ids: tuple[str, ...]
    compatible_transport_kinds: tuple[str, ...]
    behavior_policy_ref: str
    evaluation_policy_ref: str
    retry_policy_ref: str
    entry_policy: ModuleEntryPolicy
    output_resolution_policy: OutputResolutionPolicy


@dataclass(frozen=True)
class _WorkflowNodeSource:
    node_id: str
    node_kind: WorkflowNodeKind
    module_source_id: str | None
    input_mapping_ref: str | None
    input_mapping_document: Mapping[str, Any] | None


@dataclass(frozen=True)
class _WorkflowEdgeSource:
    source_node_id: str
    outcome_id: str
    target_node_id: str | None
    terminal: bool


@dataclass(frozen=True)
class _ParallelGroupSource:
    group_id: str
    control_node_id: str
    branch_node_ids: tuple[str, ...]
    join_node_id: str
    completion_outcome_id: str
    join_policy: WorkflowParallelJoinPolicy


@dataclass(frozen=True)
class _WorkflowSource:
    source_id: str
    workflow_id: str
    workflow_version: str
    workflow_contract_version: str
    owner_contract_ref: str
    owner_contract_content: str
    graph_ref: str
    initial_node_id: str
    nodes: tuple[_WorkflowNodeSource, ...]
    edges: tuple[_WorkflowEdgeSource, ...]
    parallel_groups: tuple[_ParallelGroupSource, ...]
    authorization_manifest_ref: str
    authorization_manifest_document: Mapping[str, Any]
    execution_binding_ref: str
    execution_binding_document: Mapping[str, Any]


@dataclass(frozen=True)
class _VariantBindingSource:
    position_id: str
    execution_profile_source_id: str


@dataclass(frozen=True)
class _VariantPolicySource:
    source_id: str
    policy_id: str
    policy_version: str
    origin_kind: str
    origin_source_id: str
    bindings: tuple[_VariantBindingSource, ...]


@dataclass(frozen=True)
class RuntimeAuthoringSourceSet:
    """Path-free immutable authoring content selected by one local inventory."""

    inventory_id: str
    inventory_version: str
    inventory_ref: str
    inventory_sha256: str
    plugin_id: str
    plugin_version: str
    content_members: tuple[_AuthoringContentMember, ...]
    behavior_policy_candidates: tuple[BehaviorPolicyReleaseCandidate, ...]
    evaluation_policy_candidates: tuple[EvaluationPolicyReleaseCandidate, ...]
    retry_policy_candidates: tuple[RetryPolicyReleaseCandidate, ...]
    execution_profile_specs: tuple[ExecutionProfileReleaseSpec, ...]
    module_sources: tuple[_ModuleSource, ...]
    workflow_sources: tuple[_WorkflowSource, ...]
    variant_policy_sources: tuple[_VariantPolicySource, ...]
    root_workflow_source_ids: tuple[str, ...]


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("authoring source must contain only JSON values") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_exact_keys(
    payload: Mapping[str, Any], expected: frozenset[str], *, label: str
) -> None:
    actual = set(payload)
    if actual != expected:
        raise ValueError(
            f"{label} has an invalid shape: "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
        )


def _string(label: str, value: Any) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_string(label: str, value: Any) -> str | None:
    if value is None:
        return None
    return _string(label, value)


def _token(label: str, value: Any) -> str:
    text = _string(label, value)
    if not _TOKEN_PATTERN.fullmatch(text):
        raise ValueError(f"{label} must be a stable version token")
    return text


def _sorted_unique_strings(
    label: str, value: Any, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if type(value) is not list or (not value and not allow_empty):
        raise ValueError(f"{label} must be a JSON array")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    result = tuple(value)
    if len(result) != len(set(result)) or result != tuple(sorted(result)):
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _relative_path(label: str, value: Any) -> str:
    text = _string(label, value)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{label} must stay inside the authoring root")
    if path.as_posix() != text:
        raise ValueError(f"{label} must use normalized POSIX syntax")
    return text


def _checked_entry(
    root: Path,
    relative_path: str | PurePosixPath,
    *,
    label: str,
    expected_kind: str,
) -> Path:
    logical = PurePosixPath(relative_path)
    candidate = root
    for component in logical.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing") from exc
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{label} escapes the authoring root")
    if expected_kind == "file" and not candidate.is_file():
        raise ValueError(f"{label} must be a file")
    if expected_kind == "directory" and not candidate.is_dir():
        raise ValueError(f"{label} must be a directory")
    return candidate


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} must be UTF-8 JSON") from exc
    if type(payload) is not dict:
        raise ValueError(f"{label} must be one JSON object")
    return payload


def _read_text(path: Path, *, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    if not text.strip() or "\x00" in text:
        raise ValueError(f"{label} must be non-empty UTF-8 text")
    return text


def _declares_identifier(text: str, identifier: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])", text
    ) is not None


def _contract_ref(value: Any) -> str:
    text = _string("owner_contract_ref", value)
    if not _CONTRACT_REF_PATTERN.fullmatch(text):
        raise ValueError("owner_contract_ref must use contract:<id>@<version>")
    return text


def _instruction_ref(value: Any) -> str:
    text = _string("instruction_source_ref", value)
    if not _AUTHORING_REF_PATTERN.fullmatch(text):
        raise ValueError(
            "instruction_source_ref must use authoring-source:<id>@<version>"
        )
    return text


def _executable_ref(module_kind: ModuleKind, value: Any) -> str:
    text = _string("executable_ref", value)
    pattern = (
        _PYTHON_REF_PATTERN
        if module_kind is ModuleKind.DETERMINISTIC
        else _SERVICE_REF_PATTERN
    )
    if not pattern.fullmatch(text):
        raise ValueError("executable_ref does not match module_kind")
    return text


def _policy_ref(label: str, value: Any, prefix: str) -> str:
    text = _string(label, value)
    if not text.startswith(f"{prefix}:") or "@" not in text:
        raise ValueError(f"{label} must use {prefix}:<id>@<version>")
    return text


def _member(members: list[_AuthoringContentMember], ref: str, content: bytes) -> None:
    item = _AuthoringContentMember(ref, _sha256_bytes(content))
    prior = next((member for member in members if member.member_ref == ref), None)
    if prior is not None and prior != item:
        raise ValueError(f"one authoring member ref names different content: {ref}")
    if prior is None:
        members.append(item)


def _load_module_source(
    root: Path,
    selection: _SourceSelection,
    manifest_path: Path,
    members: list[_AuthoringContentMember],
) -> _ModuleSource:
    payload = _read_json(manifest_path, label="Module registration")
    _require_exact_keys(payload, _MODULE_KEYS, label="Module registration")
    if payload["schema_version"] != RUNTIME_MODULE_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported Runtime Module registration schema_version")
    if payload["source_kind"] != selection.source_kind:
        raise ValueError("Module source_kind differs from inventory selection")
    module_id = _string("module_id", payload["module_id"])
    validate_snake_case_name("module_id", module_id)
    if module_id != selection.source_id:
        raise ValueError("Module module_id differs from source_id")
    try:
        module_kind = ModuleKind(payload["module_kind"])
        entry_policy = ModuleEntryPolicy(payload["entry_policy"])
        output_policy = OutputResolutionPolicy(payload["output_resolution_policy"])
    except ValueError as exc:
        raise ValueError("Module registration contains an invalid enum") from exc
    if selection.source_kind == "agent_module":
        if module_kind is not ModuleKind.AGENT:
            raise ValueError("agent_module selection requires module_kind=agent")
    elif module_kind not in {ModuleKind.DETERMINISTIC, ModuleKind.EXTERNAL_SERVICE}:
        raise ValueError(
            "runtime_authoring_inventory_v1 admits deterministic and "
            "external_service non-Agent Modules only"
        )

    module_dir = manifest_path.parent
    relative_module_dir = PurePosixPath(selection.manifest_path).parent
    owner_path_value = _relative_path(
        "owner_contract_path", payload["owner_contract_path"]
    )
    owner_path = _checked_entry(
        root, owner_path_value, label="Module owner contract", expected_kind="file"
    )
    owner_content = _read_text(owner_path, label="Module owner contract")
    if not _declares_identifier(owner_content, module_id):
        raise ValueError("Module owner contract does not declare module_id")
    owner_ref = _contract_ref(payload["owner_contract_ref"])
    _member(members, owner_ref, owner_content.encode("utf-8"))

    skill_id = _optional_string("skill_id", payload["skill_id"])
    skill_path_value = _optional_string(
        "skill_projection_path", payload["skill_projection_path"]
    )
    if (skill_id is None) != (skill_path_value is None):
        raise ValueError("skill_id and skill_projection_path must be paired")
    if module_kind is ModuleKind.AGENT and skill_id is None:
        raise ValueError("Agent Module requires one Skill projection")
    if skill_id is not None:
        if not _KEBAB_PATTERN.fullmatch(skill_id):
            raise ValueError("skill_id must use canonical kebab-case")
        skill_path = _checked_entry(
            root,
            _relative_path("skill_projection_path", skill_path_value),
            label="Skill projection",
            expected_kind="file",
        )
        skill_content = _read_text(skill_path, label="Skill projection")
        if not _declares_identifier(skill_content, module_id):
            raise ValueError("Skill projection does not declare module_id")
        _member(
            members,
            (
                f"authoring-source:{module_id}_skill_projection@"
                f"{payload['module_version']}"
            ),
            skill_content.encode("utf-8"),
        )

    def schema_document(label: str, path_key: str, ref_key: str) -> str:
        relative = PurePosixPath(_relative_path(path_key, payload[path_key]))
        if not relative.parts or relative.parts[0] != "schemas":
            raise ValueError(f"{path_key} must stay inside schemas")
        path = _checked_entry(
            root,
            relative_module_dir / relative,
            label=label,
            expected_kind="file",
        )
        text = _read_text(path, label=label)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label} must be valid JSON") from exc
        ref = _string(ref_key, payload[ref_key])
        if type(document) is not dict or document.get("$id") != ref:
            raise ValueError(f"{ref_key} differs from selected Schema $id")
        _member(members, ref, text.encode("utf-8"))
        return text

    input_document = schema_document(
        "Module input Schema", "input_schema_path", "input_schema_ref"
    )
    output_document = schema_document(
        "Module output Schema", "output_schema_path", "output_schema_ref"
    )

    instruction_ref: str | None = None
    instruction_text: str | None = None
    executable_ref: str | None = None
    executable_content: bytes | None = None
    allowed_entries = {
        manifest_path.name,
        "schemas",
    }
    tests_path = module_dir / "tests"
    if tests_path.exists() and (tests_path.is_symlink() or not tests_path.is_dir()):
        raise ValueError("Module tests entry must be a real directory")
    if tests_path.exists():
        allowed_entries.add("tests")
    if module_kind is ModuleKind.AGENT:
        if (
            payload["executable_ref"] is not None
            or payload["executable_member_path"] is not None
        ):
            raise ValueError("Agent Module cannot declare executable members")
        instruction_ref = _instruction_ref(payload["instruction_source_ref"])
        prompt_path = _checked_entry(
            root,
            relative_module_dir / "prompt.md",
            label="Module instruction",
            expected_kind="file",
        )
        instruction_text = _read_text(prompt_path, label="Module instruction")
        if not instruction_text.endswith("\n") or instruction_text.startswith("---\n"):
            raise ValueError(
                "Module instruction must be body-only text ending in newline"
            )
        _member(members, instruction_ref, instruction_text.encode("utf-8"))
        allowed_entries.add("prompt.md")
    else:
        if payload["instruction_source_ref"] is not None:
            raise ValueError("non-Agent Module cannot declare instruction_source_ref")
        executable_member = PurePosixPath(
            _relative_path("executable_member_path", payload["executable_member_path"])
        )
        if len(executable_member.parts) != 1:
            raise ValueError("executable_member_path must be a Module-root filename")
        executable_path = _checked_entry(
            root,
            relative_module_dir / executable_member,
            label="Module executable member",
            expected_kind="file",
        )
        executable_content = executable_path.read_bytes()
        if not executable_content:
            raise ValueError("Module executable member must be non-empty")
        executable_ref = _executable_ref(module_kind, payload["executable_ref"])
        _member(members, executable_ref, executable_content)
        allowed_entries.add(executable_member.name)

    actual_entries = {entry.name for entry in module_dir.iterdir()}
    if any(entry.is_symlink() for entry in module_dir.iterdir()):
        raise ValueError("Module root cannot contain symlinks")
    if actual_entries != allowed_entries:
        raise ValueError(
            "Module root closure differs from manifest: "
            f"missing={sorted(allowed_entries - actual_entries)}, "
            f"extra={sorted(actual_entries - allowed_entries)}"
        )
    schema_root = module_dir / "schemas"
    expected_schema_files = {
        PurePosixPath(payload["input_schema_path"]).name,
        PurePosixPath(payload["output_schema_path"]).name,
    }
    actual_schema_files = {
        entry.name for entry in schema_root.iterdir() if entry.is_file()
    }
    if any(
        entry.is_symlink() or not entry.is_file()
        for entry in schema_root.iterdir()
    ):
        raise ValueError("Module schemas directory may contain only regular files")
    if actual_schema_files != expected_schema_files:
        raise ValueError("Module schemas directory differs from manifest closure")

    return _ModuleSource(
        source_id=selection.source_id,
        module_id=module_id,
        module_version=_token("module_version", payload["module_version"]),
        module_kind=module_kind,
        owner_contract_ref=owner_ref,
        owner_contract_content=owner_content,
        instruction_source_ref=instruction_ref,
        instruction_text=instruction_text,
        executable_ref=executable_ref,
        executable_content=executable_content,
        input_schema_ref=_string("input_schema_ref", payload["input_schema_ref"]),
        input_schema_document=input_document,
        output_schema_ref=_string("output_schema_ref", payload["output_schema_ref"]),
        output_schema_document=output_document,
        declared_operation_ids=_sorted_unique_strings(
            "declared_operation_ids",
            payload["declared_operation_ids"],
            allow_empty=True,
        ),
        compatible_transport_kinds=_sorted_unique_strings(
            "compatible_transport_kinds", payload["compatible_transport_kinds"]
        ),
        behavior_policy_ref=_policy_ref(
            "behavior_policy_ref", payload["behavior_policy_ref"], "behavior-policy"
        ),
        evaluation_policy_ref=_policy_ref(
            "evaluation_policy_ref",
            payload["evaluation_policy_ref"],
            "evaluation-policy",
        ),
        retry_policy_ref=_policy_ref(
            "retry_policy_ref", payload["retry_policy_ref"], "retry-policy"
        ),
        entry_policy=entry_policy,
        output_resolution_policy=output_policy,
    )


def _load_policy_candidate(
    selection: _SourceSelection,
    payload: dict[str, Any],
) -> (
    BehaviorPolicyReleaseCandidate
    | EvaluationPolicyReleaseCandidate
    | RetryPolicyReleaseCandidate
):
    if selection.source_kind == "behavior_policy":
        _require_exact_keys(payload, _BEHAVIOR_POLICY_KEYS, label="Behavior Policy")
        if payload["schema_version"] != "runtime_behavior_policy_source_v1":
            raise ValueError("unsupported Behavior Policy source schema_version")
        candidate = BehaviorPolicyReleaseCandidate(
            policy_id=_string("policy_id", payload["policy_id"]),
            policy_version=_token("policy_version", payload["policy_version"]),
            context_isolation=_string(
                "context_isolation", payload["context_isolation"]
            ),
        )
    elif selection.source_kind == "evaluation_policy":
        _require_exact_keys(payload, _EVALUATION_POLICY_KEYS, label="Evaluation Policy")
        if payload["schema_version"] != "runtime_evaluation_policy_source_v1":
            raise ValueError("unsupported Evaluation Policy source schema_version")
        candidate = EvaluationPolicyReleaseCandidate(
            policy_id=_string("policy_id", payload["policy_id"]),
            policy_version=_token("policy_version", payload["policy_version"]),
            evaluation_mode=_string("evaluation_mode", payload["evaluation_mode"]),
        )
    else:
        _require_exact_keys(payload, _RETRY_POLICY_KEYS, label="Retry Policy")
        if payload["schema_version"] != "runtime_retry_policy_source_v1":
            raise ValueError("unsupported Retry Policy source schema_version")
        if type(payload["max_attempts"]) is not int:
            raise ValueError("max_attempts must be an integer")
        candidate = RetryPolicyReleaseCandidate(
            policy_id=_string("policy_id", payload["policy_id"]),
            policy_version=_token("policy_version", payload["policy_version"]),
            max_attempts=payload["max_attempts"],
        )
    if candidate.policy_id != selection.source_id:
        raise ValueError("Policy ID differs from inventory source_id")
    return candidate


def _load_execution_profile(
    selection: _SourceSelection, payload: dict[str, Any]
) -> ExecutionProfileReleaseSpec:
    _require_exact_keys(payload, _EXECUTION_PROFILE_KEYS, label="Execution Profile")
    if payload["schema_version"] != "runtime_execution_profile_source_v1":
        raise ValueError("unsupported Execution Profile source schema_version")
    profile_id = _string("execution_profile_id", payload["execution_profile_id"])
    if profile_id != selection.source_id:
        raise ValueError("Execution Profile ID differs from source_id")
    if type(payload["timeout_seconds"]) is not int:
        raise ValueError("timeout_seconds must be an integer")
    return ExecutionProfileReleaseSpec(
        execution_profile_id=profile_id,
        release_version=_token("release_version", payload["release_version"]),
        executor_adapter_id=_string(
            "executor_adapter_id", payload["executor_adapter_id"]
        ),
        executor_adapter_revision=_string(
            "executor_adapter_revision", payload["executor_adapter_revision"]
        ),
        transport_kind=_string("transport_kind", payload["transport_kind"]),
        provider_id=_string("provider_id", payload["provider_id"]),
        model_id=_string("model_id", payload["model_id"]),
        reasoning_profile=_string(
            "reasoning_profile", payload["reasoning_profile"]
        ),
        execution_mode=_string("execution_mode", payload["execution_mode"]),
        semantic_input_delivery_mode=_string(
            "semantic_input_delivery_mode",
            payload["semantic_input_delivery_mode"],
        ),
        attempt_workspace_policy=_string(
            "attempt_workspace_policy", payload["attempt_workspace_policy"]
        ),
        gateway_access_reasons=_sorted_unique_strings(
            "gateway_access_reasons",
            payload["gateway_access_reasons"],
            allow_empty=True,
        ),
        output_constraint_mode=_string(
            "output_constraint_mode", payload["output_constraint_mode"]
        ),
        tool_policy=_sorted_unique_strings(
            "tool_policy", payload["tool_policy"], allow_empty=True
        ),
        network_policy=_string("network_policy", payload["network_policy"]),
        timeout_seconds=payload["timeout_seconds"],
    )


def _mapping(label: str, value: Any) -> Mapping[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be one JSON object")
    _canonical_json(value)
    return value


def _load_workflow(
    root: Path,
    selection: _SourceSelection,
    payload: dict[str, Any],
    members: list[_AuthoringContentMember],
) -> _WorkflowSource:
    _require_exact_keys(payload, _WORKFLOW_KEYS, label="Workflow source")
    if payload["schema_version"] != "runtime_workflow_source_v1":
        raise ValueError("unsupported Workflow source schema_version")
    workflow_id = _string("workflow_id", payload["workflow_id"])
    if workflow_id != selection.source_id:
        raise ValueError("Workflow ID differs from source_id")
    owner_ref = _contract_ref(payload["owner_contract_ref"])
    owner_path = _checked_entry(
        root,
        _relative_path("owner_contract_path", payload["owner_contract_path"]),
        label="Workflow owner contract",
        expected_kind="file",
    )
    owner_content = _read_text(owner_path, label="Workflow owner contract")
    if not _declares_identifier(owner_content, workflow_id):
        raise ValueError("Workflow owner contract does not declare workflow_id")
    _member(members, owner_ref, owner_content.encode("utf-8"))
    if type(payload["nodes"]) is not list or not payload["nodes"]:
        raise ValueError("Workflow nodes must be a non-empty array")
    nodes: list[_WorkflowNodeSource] = []
    for row in payload["nodes"]:
        if type(row) is not dict:
            raise ValueError("Workflow node must be an object")
        _require_exact_keys(row, _WORKFLOW_NODE_KEYS, label="Workflow node")
        try:
            node_kind = WorkflowNodeKind(row["node_kind"])
        except ValueError as exc:
            raise ValueError("invalid Workflow node_kind") from exc
        module_source_id = _optional_string(
            "module_source_id", row["module_source_id"]
        )
        mapping_ref = _optional_string("input_mapping_ref", row["input_mapping_ref"])
        mapping_document = row["input_mapping_document"]
        if node_kind is WorkflowNodeKind.MODULE:
            if module_source_id is None or mapping_ref is None:
                raise ValueError("Module Workflow node requires Module and mapping")
            mapping_document = _mapping("input_mapping_document", mapping_document)
        elif any(
            value is not None
            for value in (module_source_id, mapping_ref, mapping_document)
        ):
            raise ValueError("Control Workflow node cannot declare Module mapping")
        nodes.append(
            _WorkflowNodeSource(
                node_id=_string("node_id", row["node_id"]),
                node_kind=node_kind,
                module_source_id=module_source_id,
                input_mapping_ref=mapping_ref,
                input_mapping_document=mapping_document,
            )
        )
    if type(payload["edges"]) is not list or not payload["edges"]:
        raise ValueError("Workflow edges must be a non-empty array")
    edges: list[_WorkflowEdgeSource] = []
    for row in payload["edges"]:
        if type(row) is not dict:
            raise ValueError("Workflow edge must be an object")
        _require_exact_keys(row, _WORKFLOW_EDGE_KEYS, label="Workflow edge")
        if type(row["terminal"]) is not bool:
            raise ValueError("Workflow edge terminal must be a boolean")
        edges.append(
            _WorkflowEdgeSource(
                source_node_id=_string("source_node_id", row["source_node_id"]),
                outcome_id=_string("outcome_id", row["outcome_id"]),
                target_node_id=_optional_string(
                    "target_node_id", row["target_node_id"]
                ),
                terminal=row["terminal"],
            )
        )
    if type(payload["parallel_groups"]) is not list:
        raise ValueError("parallel_groups must be an array")
    groups: list[_ParallelGroupSource] = []
    for row in payload["parallel_groups"]:
        if type(row) is not dict:
            raise ValueError("parallel group must be an object")
        _require_exact_keys(row, _PARALLEL_GROUP_KEYS, label="parallel group")
        try:
            join_policy = WorkflowParallelJoinPolicy(row["join_policy"])
        except ValueError as exc:
            raise ValueError("invalid parallel join_policy") from exc
        groups.append(
            _ParallelGroupSource(
                group_id=_string("group_id", row["group_id"]),
                control_node_id=_string(
                    "control_node_id", row["control_node_id"]
                ),
                branch_node_ids=_sorted_unique_strings(
                    "branch_node_ids", row["branch_node_ids"]
                ),
                join_node_id=_string("join_node_id", row["join_node_id"]),
                completion_outcome_id=_string(
                    "completion_outcome_id", row["completion_outcome_id"]
                ),
                join_policy=join_policy,
            )
        )
    return _WorkflowSource(
        source_id=selection.source_id,
        workflow_id=workflow_id,
        workflow_version=_token("workflow_version", payload["workflow_version"]),
        workflow_contract_version=_token(
            "workflow_contract_version", payload["workflow_contract_version"]
        ),
        owner_contract_ref=owner_ref,
        owner_contract_content=owner_content,
        graph_ref=_string("graph_ref", payload["graph_ref"]),
        initial_node_id=_string("initial_node_id", payload["initial_node_id"]),
        nodes=tuple(nodes),
        edges=tuple(edges),
        parallel_groups=tuple(groups),
        authorization_manifest_ref=_string(
            "authorization_manifest_ref", payload["authorization_manifest_ref"]
        ),
        authorization_manifest_document=_mapping(
            "authorization_manifest_document",
            payload["authorization_manifest_document"],
        ),
        execution_binding_ref=_string(
            "execution_binding_ref", payload["execution_binding_ref"]
        ),
        execution_binding_document=_mapping(
            "execution_binding_document", payload["execution_binding_document"]
        ),
    )


def _load_variant_policy(
    selection: _SourceSelection, payload: dict[str, Any]
) -> _VariantPolicySource:
    _require_exact_keys(payload, _VARIANT_POLICY_KEYS, label="Variant Policy")
    if payload["schema_version"] != "runtime_execution_variant_policy_source_v1":
        raise ValueError("unsupported Variant Policy source schema_version")
    policy_id = _string("policy_id", payload["policy_id"])
    if policy_id != selection.source_id:
        raise ValueError("Variant Policy ID differs from source_id")
    if payload["origin_kind"] not in {"workflow", "standalone_module"}:
        raise ValueError("invalid Variant Policy origin_kind")
    if type(payload["bindings"]) is not list or not payload["bindings"]:
        raise ValueError("Variant Policy bindings must be a non-empty array")
    bindings: list[_VariantBindingSource] = []
    for row in payload["bindings"]:
        if type(row) is not dict:
            raise ValueError("Variant Policy binding must be an object")
        _require_exact_keys(row, _VARIANT_BINDING_KEYS, label="Variant binding")
        bindings.append(
            _VariantBindingSource(
                position_id=_string("position_id", row["position_id"]),
                execution_profile_source_id=_string(
                    "execution_profile_source_id",
                    row["execution_profile_source_id"],
                ),
            )
        )
    positions = tuple(binding.position_id for binding in bindings)
    if len(positions) != len(set(positions)):
        raise ValueError("Variant Policy position_id values must be unique")
    return _VariantPolicySource(
        source_id=selection.source_id,
        policy_id=policy_id,
        policy_version=_token("policy_version", payload["policy_version"]),
        origin_kind=payload["origin_kind"],
        origin_source_id=_string(
            "origin_source_id", payload["origin_source_id"]
        ),
        bindings=tuple(bindings),
    )


def load_runtime_authoring_inventory(
    authoring_root: Path,
    inventory_path: str,
) -> RuntimeAuthoringSourceSet:
    """Load one explicit inventory and erase local paths at the return boundary."""

    if not isinstance(authoring_root, Path):
        raise TypeError("authoring_root must be a pathlib.Path")
    if authoring_root.is_symlink() or not authoring_root.is_dir():
        raise ValueError("authoring_root must be a real directory")
    root = authoring_root.resolve()
    inventory_relative = _relative_path("inventory_path", inventory_path)
    inventory_file = _checked_entry(
        root,
        inventory_relative,
        label="Runtime authoring inventory",
        expected_kind="file",
    )
    payload = _read_json(inventory_file, label="Runtime authoring inventory")
    _require_exact_keys(payload, _INVENTORY_KEYS, label="Runtime authoring inventory")
    if payload["schema_version"] != RUNTIME_AUTHORING_INVENTORY_SCHEMA_VERSION:
        raise ValueError("unsupported Runtime authoring inventory schema_version")
    inventory_id = _string("inventory_id", payload["inventory_id"])
    validate_snake_case_name("inventory_id", inventory_id)
    inventory_version = _token("inventory_version", payload["inventory_version"])
    plugin_id = _string("plugin_id", payload["plugin_id"])
    validate_snake_case_name("plugin_id", plugin_id)
    plugin_version = _token("plugin_version", payload["plugin_version"])
    if type(payload["sources"]) is not list or not payload["sources"]:
        raise ValueError("inventory sources must be a non-empty array")
    selections: list[_SourceSelection] = []
    for row in payload["sources"]:
        if type(row) is not dict:
            raise ValueError("inventory source selection must be an object")
        _require_exact_keys(row, _SOURCE_SELECTION_KEYS, label="source selection")
        source_kind = _string("source_kind", row["source_kind"])
        if source_kind not in _SOURCE_KINDS:
            raise ValueError(f"unsupported authoring source_kind: {source_kind}")
        source_id = _string("source_id", row["source_id"])
        validate_snake_case_name("source_id", source_id)
        selections.append(
            _SourceSelection(
                source_kind=source_kind,
                source_id=source_id,
                manifest_path=_relative_path("manifest_path", row["manifest_path"]),
            )
        )
    selection_tuple = tuple(selections)
    if selection_tuple != tuple(
        sorted(selection_tuple, key=lambda item: (item.source_kind, item.source_id))
    ):
        raise ValueError("inventory sources must use canonical kind/ID order")
    source_ids = tuple(item.source_id for item in selection_tuple)
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("inventory source_id values must be globally unique")
    root_workflow_source_ids = _sorted_unique_strings(
        "root_workflow_source_ids",
        payload["root_workflow_source_ids"],
        allow_empty=True,
    )
    inventory = _RuntimeAuthoringInventory(
        inventory_id=inventory_id,
        inventory_version=inventory_version,
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        sources=selection_tuple,
        root_workflow_source_ids=root_workflow_source_ids,
    )

    members: list[_AuthoringContentMember] = []
    behavior: list[BehaviorPolicyReleaseCandidate] = []
    evaluation: list[EvaluationPolicyReleaseCandidate] = []
    retry: list[RetryPolicyReleaseCandidate] = []
    profiles: list[ExecutionProfileReleaseSpec] = []
    modules: list[_ModuleSource] = []
    workflows: list[_WorkflowSource] = []
    variants: list[_VariantPolicySource] = []
    for selection in inventory.sources:
        manifest = _checked_entry(
            root,
            selection.manifest_path,
            label=f"{selection.source_kind} manifest",
            expected_kind="file",
        )
        manifest_bytes = manifest.read_bytes()
        manifest_ref = (
            f"authoring-source:{inventory_id}_{selection.source_id}_manifest@"
            f"{inventory_version}"
        )
        _member(members, manifest_ref, manifest_bytes)
        source_payload = _read_json(manifest, label=f"{selection.source_kind} manifest")
        if selection.source_kind in {"agent_module", "non_agent_module"}:
            modules.append(
                _load_module_source(root, selection, manifest, members)
            )
        elif selection.source_kind in {
            "behavior_policy",
            "evaluation_policy",
            "retry_policy",
        }:
            candidate = _load_policy_candidate(selection, source_payload)
            if isinstance(candidate, BehaviorPolicyReleaseCandidate):
                behavior.append(candidate)
            elif isinstance(candidate, EvaluationPolicyReleaseCandidate):
                evaluation.append(candidate)
            else:
                retry.append(candidate)
        elif selection.source_kind == "execution_profile":
            profiles.append(_load_execution_profile(selection, source_payload))
        elif selection.source_kind == "workflow":
            workflows.append(
                _load_workflow(root, selection, source_payload, members)
            )
        else:
            variants.append(_load_variant_policy(selection, source_payload))

    workflow_ids = {workflow.source_id for workflow in workflows}
    if not set(root_workflow_source_ids).issubset(workflow_ids):
        raise ValueError("root_workflow_source_ids must select Workflow sources")
    ordered_members = tuple(sorted(members, key=lambda member: member.member_ref))
    inventory_ref = f"runtime-authoring:{inventory_id}@{inventory_version}"
    inventory_hash_payload = {
        "inventory": payload,
        "content_members": [member.as_dict() for member in ordered_members],
    }
    inventory_sha256 = _sha256_bytes(
        _canonical_json(inventory_hash_payload).encode("utf-8")
    )
    return RuntimeAuthoringSourceSet(
        inventory_id=inventory_id,
        inventory_version=inventory_version,
        inventory_ref=inventory_ref,
        inventory_sha256=inventory_sha256,
        plugin_id=plugin_id,
        plugin_version=plugin_version,
        content_members=ordered_members,
        behavior_policy_candidates=tuple(behavior),
        evaluation_policy_candidates=tuple(evaluation),
        retry_policy_candidates=tuple(retry),
        execution_profile_specs=tuple(profiles),
        module_sources=tuple(modules),
        workflow_sources=tuple(workflows),
        variant_policy_sources=tuple(variants),
        root_workflow_source_ids=root_workflow_source_ids,
    )


__all__ = [
    "RUNTIME_AUTHORING_INVENTORY_SCHEMA_VERSION",
    "RUNTIME_MODULE_REGISTRATION_SCHEMA_VERSION",
    "RuntimeAuthoringSourceSet",
    "load_runtime_authoring_inventory",
]
