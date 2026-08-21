"""Claude Agent SDK adapters for registered Agent Runtime Modules.

The adapters implement the canonical ``AuthorizedAgentExecutionAdapter``
protocol. The Gateway adapter exposes only the exact MCP read operations
declared by the selected Execution Profile; governed Source and Expertise
content stays behind the attempt-local Gateway session. No repository, local
input file, shell, browser, or direct network tool is visible to the model.
Expected provider failures return a typed failed result; exceptions are
adapter conformance failures. Outputs are staged through the host and become
authoritative only through Runtime finalization.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SdkMcpTool,
    TextBlock,
    CLIConnectionError,
    CLIJSONDecodeError,
    ProcessError,
    create_sdk_mcp_server,
    query,
)

from ..contracts.ledger_lineage_definition import ModuleUsageObservation
from ..contracts.invocation_adapter_definition import (
    AgentExecutionAdapterDescriptor,
    AgentExecutionResult,
    AuthorizedAgentExecutionHost,
    AuthorizedAgentExecutionRequest,
    OutputSubmission,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_tool_definition import (
    ModuleArtifactHost,
    ModuleProviderToolSessionFactory,
    ProviderToolDefinition,
    validate_provider_tool_set,
)
from .invocation_prompt_assembly import NATIVE_STRUCTURED_OUTPUT
from .invocation_context_preparation import (
    InvocationExecutionExpectation,
    prepare_registered_invocation_context,
)
from .invocation_result_assembly import (
    TerminalAdapterFailure,
    bounded_trace_text,
    commit_attempt_trace_json,
    completed_adapter_result,
    provider_adapter_descriptor,
    raise_terminal_failure,
)
from ..foundation.foundation_schema_traversal import transform_json_schema_nodes
from .invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
    lease_attempt_workspace,
    prepare_attempt_workspace,
)


_MCP_SERVER_NAME = "runtime_data_access"
_DRAFT_WORKSPACE_TOOLS = ("Read", "Write", "Edit")
_PROFILE_REFUSAL_LIMIT = 8
_PROFILE_REFUSAL_TOOL_CHARS = 128
_PROFILE_REFUSAL_REASON_CHARS = 1024
_PROFILE_REFUSAL_PATH_CHARS = 2048


def _exact_tool_matcher(tool_names: tuple[str, ...] | list[str]) -> str:
    """Return one anchored SDK hook matcher for exact exposed tool names."""

    if not tool_names:
        raise ValueError("tool matcher requires at least one tool name")
    return "^(?:" + "|".join(re.escape(name) for name in tool_names) + ")$"


def _bounded_profile_refusal(
    *,
    tool_name: object,
    reason: object,
    path: object,
) -> dict[str, str]:
    """Return one bounded policy-refusal trace member."""

    return {
        "tool_name": bounded_trace_text(str(tool_name))[
            :_PROFILE_REFUSAL_TOOL_CHARS
        ],
        "reason": bounded_trace_text(str(reason))[
            :_PROFILE_REFUSAL_REASON_CHARS
        ],
        "path": bounded_trace_text(str(path))[:_PROFILE_REFUSAL_PATH_CHARS],
    }


def _record_profile_refusal(
    refusals: list[dict[str, str]],
    *,
    tool_name: object,
    reason: object,
    path: object,
) -> None:
    """Append one bounded refusal until the trace row limit is reached."""

    if len(refusals) >= _PROFILE_REFUSAL_LIMIT:
        return
    refusals.append(
        _bounded_profile_refusal(
            tool_name=tool_name,
            reason=reason,
            path=path,
        )
    )


def _inside(root: Path, raw_path: str) -> bool:
    candidate = Path(raw_path)
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (root / candidate).resolve()
    )
    resolved_root = root.resolve()
    return resolved == resolved_root or resolved_root in resolved.parents


def _json_tool_result(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    dict(payload),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        ]
    }


async def _streaming_prompt(prompt: str) -> AsyncIterator[dict[str, Any]]:
    yield {
        "type": "user",
        "session_id": "",
        "message": {"role": "user", "content": prompt},
        "parent_tool_use_id": None,
    }


def _sandbox_options() -> dict[str, Any]:
    return {
        "enabled": True,
        "autoAllowBashIfSandboxed": False,
        "allowUnsandboxedCommands": False,
        "network": {
            "allowedDomains": [],
            "allowManagedDomainsOnly": True,
            "allowUnixSockets": [],
            "allowAllUnixSockets": False,
            "allowLocalBinding": False,
        },
    }


def _usage_value(usage: Mapping[str, Any], key: str) -> int | None:
    value = usage.get(key)
    return int(value) if value is not None else None


def _result_message(messages: list[Any]) -> ResultMessage | None:
    return next(
        (
            message
            for message in reversed(messages)
            if isinstance(message, ResultMessage)
        ),
        None,
    )


def _provider_text(
    messages: list[Any],
    result: ResultMessage | None,
) -> str:
    text = str(result.result or "").strip() if result is not None else ""
    if text:
        return text
    return "".join(
        block.text
        for message in messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, TextBlock) and block.text
    ).strip()


def _usage_observation(
    result: ResultMessage | None,
) -> ModuleUsageObservation:
    usage = result.usage or {} if result is not None else {}
    raw_input_tokens = _usage_value(usage, "input_tokens")
    cache_read_tokens = _usage_value(usage, "cache_read_input_tokens")
    cache_creation_tokens = _usage_value(
        usage, "cache_creation_input_tokens"
    )
    return ModuleUsageObservation(
        input_tokens=(
            raw_input_tokens
            + (cache_read_tokens or 0)
            + (cache_creation_tokens or 0)
            if raw_input_tokens is not None
            else None
        ),
        output_tokens=_usage_value(usage, "output_tokens"),
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )


def _usage_fields(usage: ModuleUsageObservation) -> dict[str, int | None]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_creation_tokens": usage.cache_creation_tokens,
    }


def _canonical_output(
    messages: list[Any],
    result: ResultMessage,
) -> bytes:
    payload: object = result.structured_output
    if payload is None:
        text = _provider_text(messages, result)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Claude Module final response is not valid JSON"
            ) from exc
    if not isinstance(payload, dict):
        raise ValueError("Claude Module final response must be one JSON object")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _structured_output_format(
    registered_output_schema: dict[str, object],
) -> dict[str, Any]:
    """Project the registered schema for provider structured output framing.

    Provider structured output is only a framing aid. Runtime still validates
    the committed object against the exact registered Module output schema.
    """

    unsupported_composition = {
        "allOf",
        "anyOf",
        "oneOf",
        "if",
        "then",
        "else",
    }

    provider_schema = transform_json_schema_nodes(
        registered_output_schema,
        lambda node: {
            key: value
            for key, value in node.items()
            if key not in unsupported_composition
        },
    )
    return {"type": "json_schema", "schema": provider_schema}


def _is_quota_response(provider_response: str) -> bool:
    normalized = provider_response.lower()
    return any(
        marker in normalized
        for marker in (
            "session limit",
            "usage limit",
            "quota exceeded",
            "credit balance",
        )
    )


def _claude_exception_failure_code(
    exc: Exception,
    *,
    provider_response: str = "",
) -> str:
    if _is_quota_response(provider_response):
        return "provider_quota_exhausted"
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "claude_sdk_timeout"
    if "error result: success" in str(exc).lower():
        return "claude_sdk_protocol_result_mismatch"
    if isinstance(exc, CLIJSONDecodeError):
        return "claude_cli_json_decode_error"
    if isinstance(exc, CLIConnectionError):
        return "claude_cli_connection_error"
    if isinstance(exc, ProcessError):
        return "claude_cli_process_error"
    return "claude_sdk_invocation_error"


class _ClaudeAgentSdkExecutorBase:
    """Shared Claude SDK execution for inline and Gateway input delivery."""

    executor_adapter_id = ""
    executor_adapter_revision = ""
    expected_execution_mode = "tool_free"
    expected_semantic_input_delivery_mode = "inline"
    expected_attempt_workspace_policy = "none"
    expected_network_policy = "denied"
    requires_gateway = False
    workspace_tools: tuple[str, ...] = ()
    descriptor_admission_state = "integration_tested"

    def __init__(
        self,
        *,
        release_registry: RuntimeReleaseRegistry,
        artifact_host: ModuleArtifactHost,
        tool_session_factory: ModuleProviderToolSessionFactory | None = None,
        workspace_root: Path,
        query_fn: Callable[..., Any] = query,
        max_turns: int = 12,
    ) -> None:
        required_artifact_methods = (
            "read_bytes",
            "commit_failure_detail",
            "commit_attempt_trace",
        )
        if any(
            not callable(getattr(artifact_host, method_name, None))
            for method_name in required_artifact_methods
        ):
            raise ValueError("artifact_host must implement the Module artifact boundary")
        if tool_session_factory is not None and not callable(
            getattr(tool_session_factory, "open_session", None)
        ):
            raise ValueError("tool_session_factory must implement open_session")
        if type(max_turns) is not int or max_turns < 1:
            raise ValueError("max_turns must be a positive integer")
        self._release_registry = release_registry
        self._artifact_host = artifact_host
        self._tool_session_factory = tool_session_factory
        self._workspace_root = workspace_root.resolve()
        self._query = query_fn
        self._max_turns = max_turns
        self._descriptor = provider_adapter_descriptor(
            adapter_id=self.executor_adapter_id,
            adapter_revision=self.executor_adapter_revision,
            provider_id="anthropic",
            transport_family="sdk",
            transport_kind="claude_agent_sdk",
            execution_mode=self.expected_execution_mode,
            input_delivery_mode=self.expected_semantic_input_delivery_mode,
            network_policy=self.expected_network_policy,
            supports_dynamic_operation_authorization=self.requires_gateway,
            admission_state=self.descriptor_admission_state,
        )

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        """Return immutable canonical adapter admission metadata."""

        return self._descriptor

    def execute(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
    ) -> AgentExecutionResult:
        prepared = prepare_registered_invocation_context(
            request=request,
            release_registry=self._release_registry,
            artifact_host=self._artifact_host,
            expectation=InvocationExecutionExpectation(
                executor_adapter_id=self.executor_adapter_id,
                executor_adapter_revision=self.executor_adapter_revision,
                transport_kind="claude_agent_sdk",
                execution_mode=self.expected_execution_mode,
                semantic_input_delivery_mode=(
                    self.expected_semantic_input_delivery_mode
                ),
                attempt_workspace_policy=self.expected_attempt_workspace_policy,
                network_policy=self.expected_network_policy,
                tool_policy=None if self.requires_gateway else (),
            ),
        )
        try:
            return self._execute_prepared(request, host, prepared)
        except TerminalAdapterFailure as failure:
            return failure.result

    def _execute_prepared(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
        prepared,
    ) -> AgentExecutionResult:
        module = prepared.module
        profile = prepared.profile
        registered_output_schema = prepared.registered_output_schema
        prompt = prepared.prompt

        session = None
        definitions: tuple[ProviderToolDefinition, ...] = ()
        if self.requires_gateway:
            if self._tool_session_factory is None:
                raise ValueError("Claude Gateway Executor requires a tool session factory")
            session = self._tool_session_factory.open_session(request)
            definitions = session.definitions
        declared_names = validate_provider_tool_set(
            definitions,
            profile.tool_policy,
        )

        full_names = tuple(
            f"mcp__{_MCP_SERVER_NAME}__{tool_name}"
            for tool_name in declared_names
        )
        tools: list[SdkMcpTool[Any]] = []
        for definition in definitions:
            async def handler(
                payload: dict[str, Any],
                *,
                tool_name: str = definition.tool_name,
            ) -> dict[str, Any]:
                assert session is not None
                intent = session.operation_intent(tool_name, payload)
                receipt = host.authorize_operation(intent)
                return _json_tool_result(
                    session.invoke(tool_name, payload, receipt)
                )

            tools.append(
                SdkMcpTool(
                    name=definition.tool_name,
                    description=definition.description,
                    input_schema=dict(definition.input_schema),
                    handler=handler,
                )
            )
        mcp_servers: dict[str, Any] = {}
        if tools:
            mcp_servers[_MCP_SERVER_NAME] = create_sdk_mcp_server(
                name=_MCP_SERVER_NAME,
                version="1.0.0",
                tools=tools,
            )
        async def can_use_tool(
            tool_name: str,
            tool_input: dict[str, Any],
            _context: Any,
        ):
            if tool_name in full_names:
                return PermissionResultAllow()
            if tool_name in self.workspace_tools:
                path_value = tool_input.get("file_path") or tool_input.get(
                    "path"
                )
                if not path_value or not _inside(workspace, str(path_value)):
                    return PermissionResultDeny(
                        message=(
                            f"tool {tool_name} path escapes the Attempt draft workspace"
                        ),
                        interrupt=True,
                    )
                return PermissionResultAllow()
            return PermissionResultDeny(
                message=f"tool {tool_name} is outside the Execution Profile",
                interrupt=True,
            )

        profile_policy_refused = False
        profile_policy_refusals: list[dict[str, str]] = []

        async def enforce_profile_tool(
            hook_input: dict[str, Any],
            _tool_use_id: str | None,
            _context: Any,
        ) -> dict[str, Any]:
            """Gate every exposed tool, including SDK-auto-approved Read calls."""

            nonlocal profile_policy_refused
            decision = await can_use_tool(
                hook_input["tool_name"],
                hook_input["tool_input"],
                None,
            )
            if isinstance(decision, PermissionResultAllow):
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "allow",
                    }
                }
            profile_policy_refused = True
            _record_profile_refusal(
                profile_policy_refusals,
                tool_name=hook_input.get("tool_name", ""),
                reason=decision.message,
                path=(
                    hook_input.get("tool_input", {}).get("file_path")
                    or hook_input.get("tool_input", {}).get("path")
                    or ""
                ),
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": decision.message,
                }
            }

        def current_tool_calls():
            return session.observations if session is not None else ()

        def tool_ref_ids() -> tuple[str, ...]:
            return tuple(
                observation.tool_call_id
                for observation in current_tool_calls()
            )

        try:
            workspace = prepare_attempt_workspace(
                workspace_root=self._workspace_root,
                attempt_identity={
                    "attempt_id": request.attempt_id,
                    "module_run_id": request.module_run_id,
                    "variant_id": request.variant_id,
                    "module_release_sha256": module.release_sha256,
                    "execution_profile_sha256": profile.release_sha256,
                    "prompt_envelope_sha256": request.prompt_envelope_sha256,
                },
            )
        except (OSError, AttemptWorkspaceConflictError) as exc:
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="dependency_unavailable",
                failure_code="claude_attempt_workspace_unavailable",
                message="Claude Attempt draft workspace could not be created",
                provider_response="",
                retry_disposition_id="retry_denied",
                trace={"stage": "workspace_preparation", "error": str(exc)},
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                cause=exc,
            )
        exposed_tools = [*self.workspace_tools, *full_names]
        hooks = (
            {
                "PreToolUse": [
                    HookMatcher(
                        matcher=_exact_tool_matcher(exposed_tools),
                        hooks=[enforce_profile_tool],
                    )
                ]
            }
            if exposed_tools
            else None
        )
        options = ClaudeAgentOptions(
            model=profile.model_id,
            effort=profile.reasoning_profile,
            cwd=workspace,
            tools=exposed_tools,
            allowed_tools=[],
            disallowed_tools=[],
            permission_mode=(
                "default" if exposed_tools else "dontAsk"
            ),
            can_use_tool=can_use_tool,
            hooks=hooks,
            max_turns=self._max_turns,
            setting_sources=[],
            skills=[],
            mcp_servers=mcp_servers,
            strict_mcp_config=True,
            sandbox=_sandbox_options(),
            output_format=(
                _structured_output_format(registered_output_schema)
                if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT
                else None
            ),
        )

        messages: list[Any] = []

        async def consume() -> None:
            # The SDK owns stdin lifecycle.  Its stream_input() consumes this
            # one-message iterable and then closes input immediately for
            # ordinary tool-free runs. Agent runs carry the PreToolUse hook
            # above (and Gateway runs also carry their registered SDK MCP
            # server), so the SDK itself keeps stdin open for callbacks until
            # ResultMessage. Holding our input iterable open duplicates that
            # protocol and can deadlock a one-shot CLI invocation waiting for
            # EOF.
            async for message in self._query(
                prompt=_streaming_prompt(prompt),
                options=options,
            ):
                messages.append(message)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError(
                "synchronous Claude SDK Executor cannot run inside an event loop"
            )
        try:
            with lease_attempt_workspace(workspace):
                asyncio.run(
                    asyncio.wait_for(consume(), timeout=profile.timeout_seconds)
                )
        except AttemptWorkspaceConflictError as exc:
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="dependency_unavailable",
                failure_code="claude_attempt_workspace_unavailable",
                message="Claude Attempt draft workspace is already leased",
                provider_response="",
                retry_disposition_id="retry_denied",
                trace={"stage": "workspace_lease", "error": str(exc)},
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                cause=exc,
            )
        except Exception as exc:
            partial_result = _result_message(messages)
            # Some Claude Code / Agent SDK versions yield a complete successful
            # ResultMessage and then raise a trailing ProcessError whose text is
            # misleadingly "error result: success".  The committed result is the
            # protocol boundary; only recover when that result itself is
            # explicitly successful.  Authentication, quota, timeout, and real
            # provider failures still take the normal failure path.
            if partial_result is None or partial_result.is_error:
                provider_response = _provider_text(
                    messages, partial_result
                )
                failure_code = _claude_exception_failure_code(
                    exc,
                    provider_response=provider_response,
                )
                if failure_code == "provider_quota_exhausted":
                    failure_class = "quota"
                    retry_disposition_id = "retry_denied"
                elif isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
                    failure_class = "timeout"
                    retry_disposition_id = "retry_allowed"
                else:
                    failure_class = "provider"
                    retry_disposition_id = "retry_allowed"
                raise_terminal_failure(
                    artifact_host=self._artifact_host,
                    request=request,
                    profile=profile,
                    failure_class=failure_class,
                    failure_code=failure_code,
                    message="Claude Agent SDK invocation failed",
                    provider_response=provider_response,
                    retry_disposition_id=retry_disposition_id,
                    trace={
                        "stage": "provider_invocation",
                        "error": str(exc),
                        "provider_response": bounded_trace_text(
                            provider_response
                        ),
                    },
                    tool_operation_ref_ids=tool_ref_ids(),
                    tool_observations=current_tool_calls(),
                    cause=exc,
                    **_usage_fields(_usage_observation(partial_result)),
                )
        result_message = _result_message(messages)
        if result_message is None:
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="provider",
                failure_code="claude_sdk_missing_result",
                message="Claude Agent SDK returned no ResultMessage",
                provider_response=_provider_text(messages, None),
                retry_disposition_id="retry_allowed",
                trace={
                    "stage": "provider_completion",
                    "error": "missing ResultMessage",
                },
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
            )
        assert result_message is not None
        usage = _usage_observation(result_message)
        provider_text = _provider_text(messages, result_message)
        trace = {
            "transport": "claude_agent_sdk",
            "message_count": len(messages),
            "is_error": bool(result_message.is_error),
            "provider_response": bounded_trace_text(provider_text),
            "profile_policy_refusals": profile_policy_refusals,
        }
        if profile_policy_refused:
            first_refusal = profile_policy_refusals[0]
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="policy_violation",
                failure_code="claude_profile_tool_refused",
                message=(
                    "Claude Profile refused tool "
                    f"{first_refusal['tool_name']}: "
                    f"{first_refusal['reason']}; "
                    f"path={first_refusal['path']!r}"
                ),
                provider_response=provider_text,
                retry_disposition_id="retry_denied",
                trace=trace,
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                **_usage_fields(usage),
            )
        if result_message.is_error:
            if _is_quota_response(provider_text):
                failure_class = "quota"
                failure_code = "provider_quota_exhausted"
                retry_disposition_id = "retry_denied"
            else:
                failure_class = "provider"
                failure_code = "claude_sdk_error_result"
                retry_disposition_id = "retry_allowed"
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class=failure_class,
                failure_code=failure_code,
                message="Claude Agent SDK returned an error result",
                provider_response=provider_text,
                retry_disposition_id=retry_disposition_id,
                trace=trace,
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                **_usage_fields(usage),
            )
        try:
            canonical_output = _canonical_output(messages, result_message)
        except (TypeError, ValueError) as exc:
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="schema",
                failure_code="claude_sdk_output_json_invalid",
                message=str(exc),
                provider_response=provider_text,
                retry_disposition_id="retry_allowed",
                trace=trace,
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                cause=exc,
                **_usage_fields(usage),
            )
        validation_errors = sorted(
            Draft202012Validator(registered_output_schema).iter_errors(
                json.loads(canonical_output)
            ),
            key=lambda error: tuple(str(item) for item in error.path),
        )
        if validation_errors:
            first = validation_errors[0]
            location = "/".join(str(item) for item in first.path) or "#"
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="schema",
                failure_code="claude_sdk_output_schema_violation",
                message=(
                    "Claude output violates the registered Module schema at "
                    f"{location}: {first.message}"
                ),
                provider_response=canonical_output.decode("utf-8"),
                retry_disposition_id="retry_allowed",
                trace=trace,
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                **_usage_fields(usage),
            )
        try:
            if session is not None:
                session.validate_completion()
        except Exception as exc:
            raise_terminal_failure(
                artifact_host=self._artifact_host,
                request=request,
                profile=profile,
                failure_class="policy_violation",
                failure_code="claude_gateway_completion_validation_failed",
                message="Claude Gateway Attempt failed completion validation",
                provider_response=canonical_output.decode("utf-8"),
                retry_disposition_id="retry_denied",
                trace=trace,
                tool_operation_ref_ids=tool_ref_ids(),
                tool_observations=current_tool_calls(),
                cause=exc,
                **_usage_fields(usage),
            )
        submission = OutputSubmission(
            output_slot_id="result",
            local_handle="output/result.json",
        )
        host.stage_output_bytes(submission, canonical_output)
        trace_ref, trace_sha256 = commit_attempt_trace_json(
            self._artifact_host, request, trace
        )
        return completed_adapter_result(
            profile=profile,
            request=request,
            outputs=(submission,),
            tool_operation_ref_ids=tool_ref_ids(),
            tool_observations=current_tool_calls(),
            trace_ref=trace_ref,
            trace_sha256=trace_sha256,
            **_usage_fields(usage),
        )


class ClaudeAgentSdkInlineModuleExecutor(_ClaudeAgentSdkExecutorBase):
    """Execute one fully inline, tool-free Claude SDK Attempt."""

    executor_adapter_id = "claude_agent_sdk_inline_executor"
    executor_adapter_revision = "v1"


class ClaudeAgentSdkInlineDraftWorkspaceModuleExecutor(
    _ClaudeAgentSdkExecutorBase
):
    """Execute an inline Module with only an isolated mutable draft root."""

    executor_adapter_id = (
        "claude_agent_sdk_inline_draft_workspace_executor"
    )
    executor_adapter_revision = "v1"
    expected_execution_mode = "agent"
    expected_attempt_workspace_policy = "own_draft_read_write"
    workspace_tools = _DRAFT_WORKSPACE_TOOLS


class ClaudeAgentSdkGatewayModuleExecutor(_ClaudeAgentSdkExecutorBase):
    """Execute an Agent Module with only its registered Gateway read tools."""

    executor_adapter_id = "claude_agent_sdk_gateway_executor"
    executor_adapter_revision = "v2"
    expected_execution_mode = "agent"
    expected_semantic_input_delivery_mode = "gateway_read"
    expected_network_policy = "gateway_only"
    requires_gateway = True


__all__ = [
    "ClaudeAgentSdkGatewayModuleExecutor",
    "ClaudeAgentSdkInlineDraftWorkspaceModuleExecutor",
    "ClaudeAgentSdkInlineModuleExecutor",
]
