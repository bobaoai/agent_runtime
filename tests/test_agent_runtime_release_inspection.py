from __future__ import annotations

from agent_runtime.contracts.registry_release_definition import (
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseMember,
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


def test_release_inventory_projects_prompt_components_under_schema_v4() -> None:
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

    assert RELEASE_INVENTORY_SCHEMA_VERSION == "agent_runtime_release_inventory_v4"
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
        }
    ]
    assert "## Prompt Components" in markdown
    assert "inventory_task_instruction" in markdown


def test_release_inventory_has_no_skill_release_family() -> None:
    registry = RuntimeReleaseRegistry()

    inventory = build_runtime_release_inventory(registry)
    markdown = render_runtime_release_markdown(registry)

    assert "skill_packages" not in inventory
    assert "Skill Package" not in markdown
    assert markdown.startswith("# Agent Runtime Release Inventory")
