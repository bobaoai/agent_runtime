"""Codex CLI Executors for registered Agent Runtime Modules.

The adapters understand Runtime Module and Execution Profile contracts only.
Both send one complete, precommitted Prompt Envelope assembled from authorized
inputs.  The tool-free adapter consumes the final response directly.  The
Agent-workspace adapter gives Codex its standard workspace-write and shell
permissions so it can draft, reread, revise, and validate inside the isolated
Attempt workspace.  Neither adapter gives the provider a database connection
or network-enabled tool execution.  Domain packages remain responsible for the
semantic schema and downstream admission of the validated JSON output.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

from jsonschema import Draft202012Validator

from ..contracts.registry_release_definition import ModuleKind
from ..contracts.ledger_lineage_definition import ModuleUsageObservation
from ..contracts.execution_module_definition import (
    ModuleExecutorFailure,
    ModuleExecutorRequest,
    ModuleExecutorResult,
    ModuleOutputBinding,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_tool_definition import ModuleArtifactHost
from .invocation_prompt_assembly import (
    NATIVE_STRUCTURED_OUTPUT,
    codex_native_output_schema,
    normalize_codex_native_output,
    validate_prompt_output_constraint,
    validate_registered_output_schema,
)


_APP_BUNDLE_BIN = "/Applications/Codex.app/Contents/Resources/codex"
_FAILURE_RESPONSE_MAX_BYTES = 96 * 1024


@dataclass(frozen=True)
class CodexCliInvocationResult:
    """Raw process result returned by the injected shell-free invoker."""

    returncode: int
    stdout: str
    stderr: str


CodexCliInvoker = Callable[..., CodexCliInvocationResult]


def _resolve_codex_bin() -> str:
    explicit = os.environ.get("CODEX_CLI_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    if Path(_APP_BUNDLE_BIN).is_file():
        return _APP_BUNDLE_BIN
    raise RuntimeError("Codex CLI is not installed or visible on PATH")


def _default_invoke(
    *,
    argv: list[str],
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
) -> CodexCliInvocationResult:
    process = subprocess.run(
        argv,
        input=prompt,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout_seconds,
        check=False,
    )
    return CodexCliInvocationResult(
        returncode=process.returncode,
        stdout=process.stdout or "",
        stderr=process.stderr or "",
    )


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parse_usage(stdout: str) -> ModuleUsageObservation:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        if usage.get("input_tokens") is not None:
            input_tokens = int(usage["input_tokens"])
        if usage.get("output_tokens") is not None:
            output_tokens = int(usage["output_tokens"])
        cached = usage.get("cached_input_tokens")
        if cached is None:
            cached = usage.get("cache_read_tokens")
        if cached is not None:
            cache_read_tokens = int(cached)
        created = usage.get("cache_creation_tokens")
        if created is not None:
            cache_creation_tokens = int(created)
    result = ModuleUsageObservation(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    result.validate()
    return result


def _parse_final_agent_message(stdout: str) -> bytes:
    """Extract the last complete agent-message body from Codex JSONL events."""

    messages: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "agent_message":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            messages.append(text)
    if not messages:
        raise ValueError("Codex CLI did not return a final agent message")
    try:
        payload = json.loads(messages[-1])
    except json.JSONDecodeError as exc:
        raise ValueError("Codex Module final response is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Codex Module final response must be one JSON object")
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _failure_detail_bytes(
    *,
    failure_class: str,
    failure_code: str,
    message: str,
    provider_response: str,
    provider_error_message: str | None,
    transport_exit_code: int | None,
    retryable: bool,
) -> bytes:
    response_bytes = provider_response.encode("utf-8")
    bounded = response_bytes[:_FAILURE_RESPONSE_MAX_BYTES]
    return json.dumps(
        {
            "failure_class": failure_class,
            "failure_code": failure_code,
            "message": message,
            "provider_error_code": None,
            "provider_error_message": provider_error_message,
            "transport_exit_code": transport_exit_code,
            "retryable": retryable,
            "provider_response": bounded.decode("utf-8", errors="replace"),
            "provider_response_byte_size": len(response_bytes),
            "provider_response_truncated": len(bounded) != len(response_bytes),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _failed_process_response(result: CodexCliInvocationResult) -> str:
    """Preserve both Codex JSONL errors and process diagnostics for audit."""

    sections = []
    if result.stdout:
        sections.append("stdout:\n" + result.stdout)
    if result.stderr:
        sections.append("stderr:\n" + result.stderr)
    return "\n\n".join(sections)


class _CodexCliExecutorBase:
    """Shared exact-envelope execution for one admitted Codex CLI mode."""

    executor_adapter_id = ""
    executor_adapter_revision = ""
    expected_execution_mode = "tool_free"
    expected_semantic_input_delivery_mode = "inline"
    expected_attempt_workspace_policy = "none"
    expected_tool_policy: tuple[str, ...] = ()
    expected_network_policy = "denied"
    shell_tool_enabled = False
    sandbox_mode = "read-only"

    def __init__(
        self,
        *,
        release_registry: RuntimeReleaseRegistry,
        artifact_host: ModuleArtifactHost,
        workspace_root: Path,
        invoker: CodexCliInvoker = _default_invoke,
        codex_bin: str | None = None,
    ) -> None:
        required_artifact_methods = (
            "read_bytes",
            "commit_output",
            "commit_failure_detail",
        )
        if any(
            not callable(getattr(artifact_host, method_name, None))
            for method_name in required_artifact_methods
        ):
            raise ValueError("artifact_host must implement the Module artifact boundary")
        self._release_registry = release_registry
        self._artifact_host = artifact_host
        self._workspace_root = workspace_root.resolve()
        self._invoker = invoker
        self._codex_bin = codex_bin

    def execute(self, request: ModuleExecutorRequest) -> ModuleExecutorResult:
        """Run one exact Variant Attempt in a newly isolated local workspace."""

        if type(request) is not ModuleExecutorRequest:
            raise ValueError("request must be an exact ModuleExecutorRequest")
        module = request.module
        profile = request.execution_profile
        module.validate()
        profile.validate()
        if module.module_kind is not ModuleKind.AGENT:
            raise ValueError("Codex CLI Executor accepts only Agent Modules")
        if profile.executor_adapter_id != self.executor_adapter_id:
            raise ValueError("Execution Profile targets another Executor adapter")
        if profile.executor_adapter_revision != self.executor_adapter_revision:
            raise ValueError("Execution Profile targets another Executor revision")
        if profile.transport_kind != "codex_cli":
            raise ValueError("Codex CLI Executor requires codex_cli transport")
        if profile.transport_kind not in module.compatible_transport_kinds:
            raise ValueError("Execution Profile transport is incompatible with Module")
        if profile.execution_mode != self.expected_execution_mode:
            raise ValueError("Execution Profile execution mode differs from Executor mode")
        if (
            profile.semantic_input_delivery_mode
            != self.expected_semantic_input_delivery_mode
        ):
            raise ValueError(
                "Execution Profile semantic input delivery differs from Executor mode"
            )
        if (
            profile.attempt_workspace_policy
            != self.expected_attempt_workspace_policy
        ):
            raise ValueError(
                "Execution Profile workspace policy differs from Executor mode"
            )
        if profile.tool_policy != self.expected_tool_policy:
            raise ValueError("Execution Profile tool policy differs from Executor mode")
        if profile.network_policy != self.expected_network_policy:
            raise ValueError("Execution Profile network policy differs from Executor mode")
        if module.prompt_bundle_ref is None or module.prompt_bundle_sha256 is None:
            raise ValueError("Agent Module lacks an exact Prompt Bundle")
        prompt_bundle = self._release_registry.get_prompt_bundle(
            module.prompt_bundle_ref,
            module.prompt_bundle_sha256,
        )
        output_schema_asset = self._release_registry.get_schema_asset(
            module.output_schema_ref,
            module.output_schema_sha256,
        )
        registered_output_schema = validate_registered_output_schema(
            compiled_static_body=prompt_bundle.compiled_static_body,
            canonical_schema=output_schema_asset.schema_document(),
        )

        workspace = self._workspace_root / request.attempt_id
        workspace.mkdir(parents=True, exist_ok=False)
        if request.prompt_envelope_ref is None:
            raise ValueError("Codex CLI Agent execution requires a final Prompt Envelope")
        if request.prompt_envelope_sha256 is None:
            raise ValueError("Prompt Envelope hash is missing")
        prompt_bytes = self._artifact_host.read_bytes(
            request.prompt_envelope_ref,
            request.prompt_envelope_sha256,
        )
        if _sha256(prompt_bytes) != request.prompt_envelope_sha256:
            raise ValueError("Prompt Envelope content hash mismatch")
        try:
            prompt = prompt_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Prompt Envelope must be exact UTF-8 provider text") from exc
        validate_prompt_output_constraint(
            prompt=prompt,
            output_constraint_mode=profile.output_constraint_mode,
        )
        argv = [
            self._codex_bin or _resolve_codex_bin(),
            "exec",
            "-m",
            profile.model_id,
            "-c",
            f'model_reasoning_effort="{profile.reasoning_profile}"',
            "-c",
            "project_doc_max_bytes=0",
            "-c",
            'approval_policy="never"',
            "-c",
            f"features.shell_tool={'true' if self.shell_tool_enabled else 'false'}",
            "-c",
            "features.apps=false",
            "-c",
            "features.browser_use=false",
            "-c",
            "features.computer_use=false",
            "-c",
            "features.image_generation=false",
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.plugins=false",
            "-c",
            "features.plugin_sharing=false",
            "-c",
            "features.tool_suggest=false",
            "-c",
            "features.workspace_dependencies=false",
            "-c",
            'web_search="disabled"',
        ]
        if self.sandbox_mode == "workspace-write":
            argv.extend(
                [
                    "-c",
                    "sandbox_workspace_write.network_access=false",
                    "-c",
                    "sandbox_workspace_write.exclude_slash_tmp=true",
                    "-c",
                    "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                ]
            )
        argv.extend(
            [
                "-C",
                str(workspace),
                "-s",
                self.sandbox_mode,
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--strict-config",
                "--ephemeral",
                "--json",
                "-",
            ]
        )
        try:
            with ExitStack() as stack:
                if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
                    schema_directory = Path(
                        stack.enter_context(
                            tempfile.TemporaryDirectory(
                                prefix=f".{request.attempt_id}_schema_",
                                dir=self._workspace_root,
                            )
                        )
                    )
                    schema_path = schema_directory / "output_schema.json"
                    schema_path.write_text(
                        json.dumps(
                            codex_native_output_schema(
                                prompt_bundle.compiled_static_body
                            ),
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        encoding="utf-8",
                    )
                    argv[-1:-1] = ["--output-schema", str(schema_path)]
                result = self._invoker(
                    argv=argv,
                    prompt=prompt,
                    cwd=workspace,
                    timeout_seconds=profile.timeout_seconds,
                )
        except Exception as exc:
            self._raise_failure(
                request=request,
                failure_class=(
                    "provider_timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "provider_failure"
                ),
                failure_code=(
                    "codex_cli_timeout"
                    if isinstance(exc, subprocess.TimeoutExpired)
                    else "codex_cli_invocation_error"
                ),
                message="Codex CLI invocation failed",
                provider_response=str(exc),
                usage=ModuleUsageObservation(None, None, None, None),
                cause=exc,
            )
        if type(result) is not CodexCliInvocationResult:
            raise TypeError("Codex CLI invoker returned an invalid result")
        if result.returncode != 0:
            self._raise_failure(
                request=request,
                failure_class="provider_failure",
                failure_code="codex_cli_nonzero_exit",
                message=(
                    f"Codex CLI failed with return code {result.returncode}"
                ),
                provider_response=_failed_process_response(result),
                usage=_parse_usage(result.stdout),
                transport_exit_code=result.returncode,
            )

        try:
            canonical_output = _parse_final_agent_message(result.stdout)
        except ValueError as exc:
            self._raise_failure(
                request=request,
                failure_class="output_parse_failure",
                failure_code="codex_cli_output_json_invalid",
                message=str(exc),
                provider_response=result.stdout,
                usage=_parse_usage(result.stdout),
                cause=exc,
            )
        canonical_payload = json.loads(canonical_output)
        if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
            canonical_payload = normalize_codex_native_output(
                payload=canonical_payload,
                canonical_schema=registered_output_schema,
            )
        validation_errors = sorted(
            Draft202012Validator(registered_output_schema).iter_errors(
                canonical_payload
            ),
            key=lambda error: tuple(str(item) for item in error.path),
        )
        if validation_errors:
            first = validation_errors[0]
            location = "/".join(str(item) for item in first.path) or "#"
            self._raise_failure(
                request=request,
                failure_class="output_validation_failure",
                failure_code="codex_cli_output_schema_violation",
                message=(
                    "Codex output violates the registered Module schema at "
                    f"{location}: {first.message}"
                ),
                provider_response=canonical_output.decode("utf-8"),
                usage=_parse_usage(result.stdout),
            )
        if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
            canonical_output = json.dumps(
                canonical_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        output = self._artifact_host.commit_output(
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            logical_name="result",
            content=canonical_output,
            schema_ref=module.output_schema_ref,
            schema_sha256=module.output_schema_sha256,
            media_type="application/json",
        )
        output.validate()
        if (
            output.output_sha256 != _sha256(canonical_output)
            or output.schema_ref != module.output_schema_ref
            or output.schema_sha256 != module.output_schema_sha256
        ):
            raise ValueError("Module artifact host returned a mismatched output binding")
        normalized = ModuleExecutorResult(
            outputs=(output,),
            usage=_parse_usage(result.stdout),
        )
        normalized.validate()
        return normalized

    def _raise_failure(
        self,
        *,
        request: ModuleExecutorRequest,
        failure_class: str,
        failure_code: str,
        message: str,
        provider_response: str,
        usage: ModuleUsageObservation,
        transport_exit_code: int | None = None,
        cause: Exception | None = None,
    ) -> None:
        detail = self._artifact_host.commit_failure_detail(
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            failure_class=failure_class,
            content=_failure_detail_bytes(
                failure_class=failure_class,
                failure_code=failure_code,
                message=message,
                provider_response=provider_response,
                provider_error_message=(
                    str(cause) if cause is not None else None
                ),
                transport_exit_code=transport_exit_code,
                retryable=failure_class in {
                    "provider_failure",
                    "provider_timeout",
                },
            ),
            media_type="application/json",
        )
        failure = ModuleExecutorFailure(
            message,
            failure_class=failure_class,
            failure_code=failure_code,
            usage=usage,
            detail=detail,
        )
        if cause is None:
            raise failure
        raise failure from cause


class CodexCliModuleExecutor(_CodexCliExecutorBase):
    """Execute a tool-free JSON-output Module through Codex CLI."""

    executor_adapter_id = "codex_cli_agent_executor"
    executor_adapter_revision = "v2"


class CodexCliAgentWorkspaceModuleExecutor(_CodexCliExecutorBase):
    """Execute one Agent Module with an isolated mutable draft workspace."""

    executor_adapter_id = "codex_cli_agent_workspace_executor"
    executor_adapter_revision = "v1"
    expected_execution_mode = "agent"
    expected_attempt_workspace_policy = "own_draft_read_write"
    expected_tool_policy = ()
    shell_tool_enabled = True
    sandbox_mode = "workspace-write"


__all__ = [
    "CodexCliInvocationResult",
    "CodexCliInvoker",
    "CodexCliAgentWorkspaceModuleExecutor",
    "CodexCliModuleExecutor",
    "ModuleArtifactHost",
]
