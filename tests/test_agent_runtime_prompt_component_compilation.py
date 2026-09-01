from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.registry_release_definition import (
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseMember,
)
from agent_runtime.registry.registry_release_compilation import (
    compile_prompt_bundle_release,
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


def test_prompt_bundle_compilation_preserves_ordered_component_content() -> None:
    instruction = _component(
        "example_task_instruction",
        PromptComponentKind.TASK_INSTRUCTION,
        "Do the task.\n",
    )
    output = _component(
        "example_output_constraint",
        PromptComponentKind.OUTPUT_CONSTRAINT,
        "## Output Constraint\n\nReturn one object.\n",
    )
    bundle = compile_prompt_bundle_release(
        prompt_bundle_id="example_prompt_bundle",
        prompt_bundle_version="v1",
        compiler_version="test_prompt_formatter_v1",
        components=(instruction, output),
    )

    assert tuple(member.member_ref for member in bundle.members) == (
        instruction.release_ref,
        output.release_ref,
    )
    assert bundle.compiled_static_body == (
        "Do the task.\n## Output Constraint\n\nReturn one object.\n"
    )


def test_prompt_component_rejects_content_mutation_in_place() -> None:
    component = _component(
        "immutable_task_instruction",
        PromptComponentKind.TASK_INSTRUCTION,
        "Original.\n",
    )

    with pytest.raises(ValueError, match="content hash mismatch"):
        replace(component, formatted_content="Changed.\n").validate()
