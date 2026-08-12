"""Backend-neutral conformance fixture tests."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from agent_runtime.testing.durability_backend_conformance import (
    ALPHA_PRIVATE_SENTINEL,
    BETA_PRIVATE_SENTINEL,
    CONFORMANCE_CONTRACT_VERSION,
    ConformanceWorkload,
    build_two_cell_conformance_fixture,
)


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_two_cell_fixture_is_backend_portable_and_ref_only(backend_id: str) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    assert CONFORMANCE_CONTRACT_VERSION == "agent_runtime_backend_conformance_v4"
    assert [case.binding.tenant_id for case in fixture.cases()] == [
        "institution_alpha",
        "institution_beta",
    ]
    encoded_payloads = json.dumps(
        [case.backend_payload() for case in fixture.cases()],
        sort_keys=True,
    )
    assert ALPHA_PRIVATE_SENTINEL not in encoded_payloads
    assert BETA_PRIVATE_SENTINEL not in encoded_payloads
    for dispatch_identity in (
        "module_run_id",
        "variant_id",
        "attempt_base_id",
        "execution_profile_ref",
    ):
        assert dispatch_identity not in encoded_payloads
    for case in fixture.cases():
        assert len({module.module_run_id for module in case.modules}) == len(case.modules)
        assert len({module.variant_id for module in case.modules}) == len(case.modules)
        assert len({module.attempt_base_id for module in case.modules}) == len(case.modules)
        assert all(
            module.workflow_execution_id == case.envelope.workflow_execution_id
            for module in case.modules
        )


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_two_cell_fixture_preserves_distinct_scenarios(backend_id: str) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    assert fixture.alpha.workload is ConformanceWorkload.REVISION_EVENT
    assert fixture.alpha.requires_revision_event is True
    assert fixture.alpha.requires_worker_failure is False
    assert fixture.beta.workload is ConformanceWorkload.CRASH_RECOVERY
    assert fixture.beta.requires_revision_event is False
    assert fixture.beta.requires_worker_failure is True
    assert fixture.alpha.expected_side_effect_commits == 1
    assert fixture.beta.expected_side_effect_commits == 1


@pytest.mark.parametrize("backend_id", ["hatchet", "temporal"])
def test_shared_fixture_payload_uses_opaque_synthetic_vocabulary(
    backend_id: str,
) -> None:
    fixture = build_two_cell_conformance_fixture(backend_id)

    for case in fixture.cases():
        payload = json.dumps(case.backend_payload(), sort_keys=True).lower()
        assert all(module.module_key.startswith("synthetic_module_") for module in case.modules)
        for domain_token in (
            "research",
            "theme",
            "writer",
            "verifier",
            "reviewer",
            "debater",
            "draft",
            "pm",
        ):
            assert re.search(
                rf"(?<![a-z]){re.escape(domain_token)}(?![a-z])",
                payload,
            ) is None


@pytest.mark.parametrize(
        "runtime_path",
        [
            "src/agent_runtime/testing/durability_backend_conformance.py",
            "src/agent_runtime/testing/durability_temporal_conformance.py",
        ],
)
def test_shared_fixture_modules_define_no_domain_vocabulary(
    runtime_path: str,
) -> None:
    source = (
        Path(__file__).resolve().parents[1] / runtime_path
    ).read_text(encoding="utf-8").lower()

    for domain_token in (
        "research",
        "theme",
        "writer",
        "verifier",
        "reviewer",
        "debater",
        "draft",
        "candidate",
        "quality",
        "pm",
    ):
        assert re.search(
            rf"(?<![a-z]){re.escape(domain_token)}(?![a-z])",
            source,
        ) is None


def test_runtime_core_defines_no_domain_role_vocabulary() -> None:
    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"

    for runtime_path in sorted(runtime_root.rglob("*.py")):
        source = runtime_path.read_text(encoding="utf-8").lower()
        for domain_role_token in (
            "theme",
            "writer",
            "verifier",
            "reviewer",
            "debater",
            "pm",
        ):
            assert re.search(
                rf"(?<![a-z]){domain_role_token}(?![a-z])",
                source,
            ) is None, runtime_path


def test_fixture_rejects_an_invalid_backend_identity() -> None:
    with pytest.raises(ValueError, match="invalid backend_id"):
        build_two_cell_conformance_fixture("Temporal Cloud")


def test_fixture_rejects_one_variant_shared_by_multiple_module_runs() -> None:
    fixture = build_two_cell_conformance_fixture("temporal")
    first, second, *remaining = fixture.alpha.modules
    invalid_second = replace(second, variant_id=first.variant_id)
    invalid_case = replace(
        fixture.alpha,
        modules=(first, invalid_second, *remaining),
    )

    with pytest.raises(ValueError, match="variant_id must be unique per module"):
        invalid_case.validate()


def test_runtime_wall_clock_strings_carry_the_utc_suffix() -> None:
    """Every string-typed instant declares its semantics as `*_at_utc`.

    Aware ``datetime`` objects carry their semantics in the type and stay
    unsuffixed; only the canonical string form is naming-constrained.
    """

    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    unsuffixed_instant = re.compile(
        r"^\s+\w*(?:_at|_time|_timestamp|_date): str\b",
        re.MULTILINE,
    )

    for runtime_path in sorted(runtime_root.rglob("*.py")):
        source = runtime_path.read_text(encoding="utf-8")
        assert unsuffixed_instant.search(source) is None, runtime_path
