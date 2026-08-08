from __future__ import annotations

from agent_runtime.contracts.registry_release_definition import SkillPackageRelease
from agent_runtime.inspection.inspection_release_rendering import (
    build_runtime_release_inventory,
    render_runtime_release_markdown,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def test_release_inventory_renders_candidate_without_admission_record() -> None:
    package = SkillPackageRelease.build(
        skill_package_id="demo_package",
        skill_package_version="candidate_v1",
        release_ref="skill-package:demo_package@candidate_v1",
        owner_contract_ref="design-doc:demo_package@v1",
        owner_contract_sha256="a" * 64,
        module_exports=(),
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle(skill_packages=(package,)))

    inventory = build_runtime_release_inventory(registry)
    markdown = render_runtime_release_markdown(registry)

    assert inventory["skill_packages"][0]["latest_admission_state"] is None
    assert markdown.startswith("# Agent Runtime Release Inventory")
