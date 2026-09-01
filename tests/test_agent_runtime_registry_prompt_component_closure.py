from __future__ import annotations

import pytest

from agent_runtime.contracts.registry_release_definition import (
    PromptBundleRelease,
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseMember,
    SchemaAssetRelease,
)
from agent_runtime.registry.registry_release_compilation import (
    compile_prompt_bundle_release,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def _component(
    component_id: str,
    kind: PromptComponentKind,
    content: str,
) -> PromptComponentRelease:
    return PromptComponentRelease.build(
        prompt_component_id=component_id,
        prompt_component_version="v1",
        release_ref=f"prompt-component:{component_id}@v1",
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


def test_prompt_components_register_before_their_prompt_bundle() -> None:
    instruction = _component(
        "example_task_instruction",
        PromptComponentKind.TASK_INSTRUCTION,
        "Do the task.\n",
    )
    output = _component(
        "example_output_constraint",
        PromptComponentKind.OUTPUT_CONSTRAINT,
        "Return one object.\n",
    )
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="example_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(instruction, output),
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            prompt_components=(instruction, output),
            prompt_bundles=(bundle,),
        )
    )

    assert registry.snapshot().prompt_components == (output, instruction)


def test_prompt_bundle_rejects_an_unregistered_component_member() -> None:
    component = _component(
        "missing_output_constraint",
        PromptComponentKind.OUTPUT_CONSTRAINT,
        "Missing.\n",
    )
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="missing_component_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(component,),
    )

    with pytest.raises(KeyError, match="unknown Prompt Component"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(prompt_bundles=(bundle,))
        )


def test_prompt_bundle_body_must_equal_ordered_component_content() -> None:
    component = _component(
        "ordered_task_instruction",
        PromptComponentKind.TASK_INSTRUCTION,
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
                prompt_components=(component,),
                prompt_bundles=(invalid_bundle,),
            )
        )


def test_prompt_component_requires_exact_registered_schema_source() -> None:
    schema = SchemaAssetRelease.build(
        schema_asset_id="prompt_output",
        schema_asset_version="v1",
        release_ref="schema:prompt_output@v1",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "schema:prompt_output@v1",
            "type": "object",
        },
    )

    def component(schema_sha256: str) -> PromptComponentRelease:
        return PromptComponentRelease.build(
            prompt_component_id="schema_bound_output_constraint",
            prompt_component_version="v1",
            release_ref="prompt-component:schema_bound_output_constraint@v1",
            component_kind=PromptComponentKind.OUTPUT_CONSTRAINT,
            media_type="text/markdown",
            formatter_id="json_schema_output_formatter",
            formatter_version="v1",
            source_members=(
                ReleaseMember(
                    member_ref=schema.release_ref,
                    member_sha256=schema_sha256,
                    media_type="application/schema+json",
                ),
            ),
            formatted_content="Return the registered shape.\n",
        )

    with pytest.raises(KeyError, match="unknown Schema Asset"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(prompt_components=(component(schema.schema_sha256),))
        )

    with pytest.raises(ValueError, match="Schema Asset hash mismatch"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(schema,),
                prompt_components=(component("f" * 64),),
            )
        )

    registry = RuntimeReleaseRegistry()
    exact = component(schema.schema_sha256)
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            prompt_components=(exact,),
        )
    )
    assert registry.get_prompt_component(
        exact.release_ref,
        exact.release_sha256,
    ) == exact
