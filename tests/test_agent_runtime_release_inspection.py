from __future__ import annotations

from agent_runtime.contracts.registry_release_definition import (
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseMember,
    SkillPackageRelease,
)
from agent_runtime.inspection.inspection_release_rendering import (
    RELEASE_INVENTORY_SCHEMA_VERSION,
    build_runtime_release_inventory,
    render_runtime_release_markdown,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def test_release_inventory_projects_prompt_components_under_schema_v2() -> None:
    component = PromptComponentRelease.build(
        prompt_component_id="inventory_task_instruction",
        prompt_component_version="v1",
        release_ref="prompt-component:inventory_task_instruction@v1",
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="test_context_formatter",
        formatter_version="v1",
        source_members=(
            ReleaseMember(
                member_ref="domain-release:inventory_task_instruction@v1",
                member_sha256="a" * 64,
                media_type="application/json",
            ),
        ),
        formatted_content="Do the inventory task.\n",
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(prompt_components=(component,))
    )

    inventory = build_runtime_release_inventory(registry)
    markdown = render_runtime_release_markdown(registry)

    assert RELEASE_INVENTORY_SCHEMA_VERSION == "agent_runtime_release_inventory_v2"
    assert inventory["schema_version"] == RELEASE_INVENTORY_SCHEMA_VERSION
    assert inventory["prompt_components"] == [
        {
            "prompt_component_id": "inventory_task_instruction",
            "version": "v1",
            "release_ref": "prompt-component:inventory_task_instruction@v1",
            "release_sha256": component.release_sha256,
            "component_kind": "task_instruction",
            "media_type": "text/markdown",
            "formatter_id": "test_context_formatter",
            "formatter_version": "v1",
            "formatted_content_sha256": component.formatted_content_sha256,
            "latest_admission_state": None,
        }
    ]
    assert "## Prompt Components" in markdown
    assert "inventory_task_instruction" in markdown


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
