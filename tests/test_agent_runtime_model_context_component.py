from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.registry_release_definition import (
    ModelContextComponentKind,
    ModelContextComponentRelease,
    PromptBundleRelease,
    ReleaseMember,
)
from agent_runtime.registry.registry_postgres_persistence import (
    postgres_release_ddl,
    serialize_registry_tables,
)
from agent_runtime.registry.registry_release_compilation import (
    compile_prompt_bundle_release,
    project_prompt_bundle_markdown,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def _component(
    component_id: str,
    kind: ModelContextComponentKind,
    content: str,
) -> ModelContextComponentRelease:
    return ModelContextComponentRelease.build(
        context_component_id=component_id,
        context_component_version="v1",
        release_ref=f"model-context-component:{component_id}@v1",
        component_kind=kind,
        media_type="text/markdown",
        formatter_id="test_context_formatter",
        formatter_version="v1",
        source_members=(
            ReleaseMember(
                member_ref=f"domain-release:{component_id}@v1",
                member_sha256="a" * 64,
                media_type="application/json",
            ),
        ),
        formatted_content=content,
    )


def test_context_components_are_registered_before_their_prompt_bundle() -> None:
    instruction = _component(
        "example_task_instruction",
        ModelContextComponentKind.TASK_INSTRUCTION,
        "Do the task.\n",
    )
    domain = _component(
        "example_domain_context",
        ModelContextComponentKind.DOMAIN_CONTEXT,
        "## Domain Context\n\nUse the registered lens.\n",
    )
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="example_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(instruction, domain),
    )
    registry = RuntimeReleaseRegistry()

    registry.register_bundle(
        RuntimeReleaseBundle(
            model_context_components=(instruction, domain),
            prompt_bundles=(bundle,),
        )
    )

    snapshot = registry.snapshot()
    assert snapshot.model_context_components == (domain, instruction)
    assert project_prompt_bundle_markdown(bundle) == (
        "Do the task.\n## Domain Context\n\nUse the registered lens.\n"
    )
    assert serialize_registry_tables(snapshot)[
        "model_context_component_release"
    ][0]["payload"]["formatted_content"]


def test_prompt_bundle_rejects_an_unregistered_component_member() -> None:
    component = _component(
        "missing_domain_context",
        ModelContextComponentKind.DOMAIN_CONTEXT,
        "Missing.\n",
    )
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="missing_component_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(component,),
    )

    with pytest.raises(KeyError, match="unknown Model Context Component"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(prompt_bundles=(bundle,))
        )


def test_prompt_bundle_body_must_equal_ordered_component_content() -> None:
    component = _component(
        "ordered_domain_context",
        ModelContextComponentKind.DOMAIN_CONTEXT,
        "Registered body.\n",
    )
    invalid_bundle = PromptBundleRelease.build(
        prompt_bundle_id="drifted_prompt_bundle",
        prompt_bundle_version="v1",
        release_ref="prompt-bundle:drifted_prompt_bundle@v1",
        compiler_version="test_prompt_formatter_v1",
        members=(
            ReleaseMember(
                member_ref=component.release_ref,
                member_sha256=component.release_sha256,
                media_type=component.media_type,
            ),
        ),
        compiled_static_body="Different body.\n",
    )

    with pytest.raises(ValueError, match="differs from its ordered"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(
                model_context_components=(component,),
                prompt_bundles=(invalid_bundle,),
            )
        )


def test_context_component_rejects_content_mutation_in_place() -> None:
    component = _component(
        "immutable_domain_context",
        ModelContextComponentKind.DOMAIN_CONTEXT,
        "Original.\n",
    )

    with pytest.raises(ValueError, match="content hash mismatch"):
        replace(component, formatted_content="Changed.\n").validate()


def test_postgres_ddl_has_dedicated_context_component_table() -> None:
    ddl = "\n".join(postgres_release_ddl())

    assert "agent_runtime_control.model_context_component_release" in ddl
    assert "payload JSONB NOT NULL" in ddl
