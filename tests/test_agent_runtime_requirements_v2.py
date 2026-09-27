"""Current Module capabilities and exact historical timeout snapshots."""

from dataclasses import replace
import hashlib
import json

import pytest

from agent_runtime import Module
from agent_runtime.contracts.registry_release_definition import (
    ModuleExecutionRequirements, ModuleRelease, ReviewerDefaults,
)
from agent_runtime.registry import RuntimeReleaseRegistry, compile_agent_module_release

from test_agent_runtime_module_authoring import _requirements, _task_project, SKILL_ID
from test_agent_runtime_registry_candidate_compilation import _agent_candidate
from test_agent_runtime_registry_module_policy_closure import (
    _modern_export, _profile_for_requirements, _variant_delta,
)


pytestmark = pytest.mark.deterministic


def _historical_payload():
    payload = _requirements().as_dict()
    payload.pop("schema_version")
    payload["timeout_seconds"] = 1200
    return payload


def test_current_requirements_have_one_closed_shape_and_no_fixed_timeout():
    requirements = _requirements()
    payload = requirements.as_dict()
    assert payload["schema_version"] == "module_execution_requirements_v2"
    assert "timeout_seconds" not in payload
    assert ModuleExecutionRequirements.from_dict(payload) == requirements
    with pytest.raises(ValueError, match="run_timeout_seconds"):
        replace(requirements, timeout_seconds=3600)
    with pytest.raises(ValueError, match="module_execution_requirements_v2"):
        replace(requirements, schema_version=None)
    for invalid in (
        {**payload, "timeout_seconds": 3600},
        {name: value for name, value in payload.items() if name != "schema_version"},
        {**payload, "schema_version": "module_execution_requirements_v3"},
    ):
        with pytest.raises(ValueError):
            ModuleExecutionRequirements.from_dict(invalid)


def test_historical_requirements_roundtrip_without_new_marker_or_hash_change(tmp_path):
    old = ModuleExecutionRequirements.from_dict(_historical_payload())
    assert old.schema_version is None and old.timeout_seconds == 1200
    assert old.as_dict() == _historical_payload()
    current = _modern_export(tmp_path).module_release.as_dict()
    current["execution_requirements"] = old.as_dict()
    body = {name: value for name, value in current.items() if name != "release_sha256"}
    current["release_sha256"] = hashlib.sha256(json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    loaded = ModuleRelease.from_dict(current)
    loaded.validate()
    assert loaded.as_dict() == current
    with pytest.raises(ValueError, match="historical requirements are read-only"):
        ModuleRelease.build(**{name: value for name, value in loaded.__dict__.items()
                               if name != "release_sha256"})


def test_v2_profile_accepts_selected_timeouts_but_legacy_keeps_exact_match(tmp_path):
    current = _modern_export(tmp_path).module_release
    requirements = current.execution_requirements
    assert requirements is not None
    profile_1200 = _profile_for_requirements(requirements)
    profile_3600 = _profile_for_requirements(requirements, timeout_seconds=3600)
    requirements.assert_profile(profile_1200)
    requirements.assert_profile(profile_3600)
    assert profile_1200.release_sha256 != profile_3600.release_sha256

    registry = RuntimeReleaseRegistry()
    exported = _modern_export(tmp_path / "registered")
    registry.register_bundle(exported.origin_bundle)
    before_module = exported.module_release.as_dict()
    registry.register_bundle(_variant_delta(exported.module_release,
        _profile_for_requirements(exported.module_release.execution_requirements,
                                  timeout_seconds=3600)))
    assert registry.get_module(exported.module_release.release_ref,
                               exported.module_release.release_sha256).as_dict() == before_module

    historical = ModuleExecutionRequirements.from_dict(_historical_payload())
    historical.assert_profile(_profile_for_requirements(historical))
    with pytest.raises(ValueError, match="Profile differs"):
        historical.assert_profile(_profile_for_requirements(historical, timeout_seconds=3600))


def test_historical_requirements_cannot_enter_new_authoring_or_compilation(tmp_path):
    historical = ModuleExecutionRequirements.from_dict(_historical_payload())
    source = _task_project(tmp_path, module_id="summarize_note")
    with pytest.raises(ValueError, match="historical requirements are read-only"):
        Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
                                 execution_requirements=historical)
    with pytest.raises(ValueError, match="module_execution_requirements_v2"):
        compile_agent_module_release(replace(_agent_candidate(), compatible_transport_kinds=(),
                                             execution_requirements=historical))


@pytest.mark.parametrize("changes", [
    {"execution_requirements": None},
    {"execution_requirements": None, "reviewer_defaults": ReviewerDefaults()},
    {"execution_requirements": None, "compatible_transport_kinds": ("claude_cli",)},
    {"reviewer_defaults": ReviewerDefaults()},
    {"compatible_transport_kinds": ("claude_cli",)},
])
def test_current_compiler_rejects_each_legacy_agent_candidate_shape(changes):
    with pytest.raises(ValueError, match="module_execution_requirements_v2"):
        compile_agent_module_release(replace(_agent_candidate(), **changes))
