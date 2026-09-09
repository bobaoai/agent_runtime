from __future__ import annotations

from agent_runtime.contracts.registry_release_definition import (
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseMember,
)
from agent_runtime.registry.registry_postgres_persistence import (
    postgres_release_ddl,
    serialize_registry_tables,
)
from agent_runtime.registry.registry_release_compilation import (
    compile_prompt_bundle_release,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def _component() -> PromptComponentRelease:
    return PromptComponentRelease.build(
        prompt_component_id="postgres_prompt_component",
        prompt_component_version="v1",
        release_ref="prompt-component:postgres_prompt_component@v1",
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="test_context_formatter",
        formatter_version="v1",
        source_members=(
            ReleaseMember(
                member_ref="domain-release:postgres_prompt_component@v1",
                member_sha256="a" * 64,
                media_type="application/json",
            ),
        ),
        formatted_content="Persist this Prompt Component.\n",
    )


def test_registry_postgres_serialization_preserves_prompt_component_content() -> None:
    component = _component()
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="postgres_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(component,),
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            prompt_components=(component,),
            prompt_bundles=(bundle,),
        )
    )

    assert serialize_registry_tables(registry.snapshot())[
        "prompt_component_release"
    ][0]["payload"]["formatted_content"] == component.formatted_content


def test_postgres_ddl_has_dedicated_prompt_component_table() -> None:
    ddl = "\n".join(postgres_release_ddl())

    assert "agent_runtime_control_v2.prompt_component_release" in ddl
    assert "payload JSONB NOT NULL" in ddl
