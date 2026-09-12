"""Runtime-owned Reviewer defaults; no host or provider SDK configuration."""

from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json

from ..contracts.registry_release_definition import ReviewerDefaults
from .registry_release_compilation import (
    BehaviorPolicyReleaseCandidate, EvaluationPolicyReleaseCandidate,
    RetryPolicyReleaseCandidate, ExecutionProfileReleaseSpec,
    compile_behavior_policy_release, compile_evaluation_policy_release,
    compile_retry_policy_release, compile_execution_profile_release,
)


def content_version(document) -> str:
    """Deterministic version for compiler-owned execution bindings."""
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256_" + hashlib.sha256(encoded.encode()).hexdigest()


def reviewer_policy_defaults():
    """Return Runtime's exact policies, including the retained candidate policy."""
    return {
        "behavior_policies": (
            compile_behavior_policy_release(BehaviorPolicyReleaseCandidate(
                "workflow_execution_isolated", "v1", "workflow_execution_isolated")),
        ),
        "evaluation_policies": (
            compile_evaluation_policy_release(EvaluationPolicyReleaseCandidate(
                "reviewer_entry", "v1", "none")),
            compile_evaluation_policy_release(EvaluationPolicyReleaseCandidate(
                "module_candidate", "v1", "module_candidate")),
        ),
        "retry_policies": (
            compile_retry_policy_release(RetryPolicyReleaseCandidate(
                "bounded_candidate", "v1", ReviewerDefaults().max_attempts)),
        ),
    }


def resolve_reviewer_policy(family, reference, supplied, registry):
    """Respect an explicit source dependency; resolve omissions from Runtime."""
    if supplied is not None:
        supplied.validate()
        if reference is not None and reference != supplied.release_ref:
            raise ValueError(f"source {family} reference differs from supplied policy")
        return supplied
    if reference is not None and registry is not None:
        for record in getattr(registry.snapshot(), family):
            if record.release_ref == reference:
                return record
    defaults = reviewer_policy_defaults()[family]
    if reference is None:
        return defaults[0]
    for record in defaults:
        if record.release_ref == reference:
            return record
    raise ValueError(f"Unresolved exact Reviewer policy: {reference}")


def reviewer_execution_profile(defaults: ReviewerDefaults, *, transport_kind=None, model_id=None, reasoning_profile=None):
    """Compile the fixed capabilities with an independent Claude model selection.

    Model preset v1 is claude-opus-5[1m], xhigh over claude_cli. Explicit model
    or reasoning values never change tool/network/workspace permissions.
    The content-derived Profile version is deterministic, not a new Module version.
    """
    defaults.validate()
    if transport_kind is not None and transport_kind != "claude_cli":
        raise ValueError(f"Unsupported Reviewer model transport: {transport_kind}; no automatic fallback")
    spec = ExecutionProfileReleaseSpec(
        execution_profile_id="reviewer_claude_cli",
        executor_adapter_id="claude_cli_native_tools_executor", executor_adapter_revision="v2",
        transport_kind="claude_cli", provider_id="anthropic",
        model_id="claude-opus-5[1m]" if model_id is None else model_id,
        reasoning_profile="xhigh" if reasoning_profile is None else reasoning_profile,
        execution_mode="agent", semantic_input_delivery_mode="inline",
        attempt_workspace_policy=defaults.attempt_workspace_policy, gateway_access_reasons=(),
        output_constraint_mode="native_structured_output", tool_policy=defaults.tool_policy,
        network_policy=defaults.network_policy, timeout_seconds=defaults.timeout_seconds,
        model_defaults_version="v1" if model_id is None and reasoning_profile is None else None,
    )
    return compile_execution_profile_release(replace(spec, release_version=content_version(asdict(spec))))
