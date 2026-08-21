from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.inspection import (
    ReviewContent,
    build_workflow_review_bundle,
    render_workflow_review_html,
    validate_workflow_review_bundle,
)


HASH = "a" * 64


def _inspection() -> dict[str, object]:
    return {
        "trace": {
            "workflow": {
                "workflow_id": "source_to_evidence",
                "workflow_execution_id": "execution_review_001",
                "status": "completed",
            },
            "usage": {"input_tokens": 123, "output_tokens": 45},
        },
        "workflow_release": {
            "workflow_id": "source_to_evidence",
            "workflow_version": "v1",
            "nodes": [
                {
                    "node_id": "route_source",
                    "module_release_ref": "runtime-module:route_source@v1",
                }
            ],
            "edges": [
                {
                    "source_node_id": "route_source",
                    "outcome_id": "completed",
                    "target_node_id": None,
                    "terminal": True,
                }
            ],
        },
        "modules": [
            {
                "module_run": {
                    "module_run_id": "module_run_review_001",
                    "workflow_node_id": "route_source",
                    "module_release_ref": "runtime-module:route_source@v1",
                    "input_closure_sha256": HASH,
                    "status": "completed",
                    "source_ledger_position": 1,
                },
                "node_occurrence_index": 1,
                "module_release": {
                    "module_id": "route_source",
                    "release_ref": "runtime-module:route_source@v1",
                },
                "variants": [
                    {
                        "module_run_id": "module_run_review_001",
                        "variant_id": "variant_review_001",
                        "execution_profile_ref": "execution-profile:fable_5_max@v1",
                        "execution_profile_sha256": HASH,
                        "prompt_envelope_ref": "prompt-envelope:review_001",
                        "prompt_envelope_sha256": hashlib.sha256(
                            b"complete model context"
                        ).hexdigest(),
                        "execution_profile": {
                            "provider_id": "anthropic",
                            "model_id": "fable_5",
                        },
                    }
                ],
                "attempts": [
                    {
                        "variant_id": "variant_review_001",
                        "attempt_id": "attempt_review_001",
                        "status": "completed",
                        "input_tokens": 123,
                        "output_tokens": 45,
                        "failure_class": None,
                    }
                ],
                "artifacts": [],
            }
        ],
        "projection_boundary": {},
    }


def test_review_bundle_is_content_addressed_and_html_is_offline() -> None:
    body = "complete model context"
    bundle = build_workflow_review_bundle(
        _inspection(),
        contents=(
            ReviewContent(
                content_ref="prompt-envelope:review_001",
                content_sha256=hashlib.sha256(body.encode()).hexdigest(),
                media_type="text/plain",
                redaction_state="content_read_granted",
                body=body,
            ),
        ),
    )

    validate_workflow_review_bundle(bundle)
    html = render_workflow_review_html(bundle)

    assert bundle["workflow_execution_id"] == "execution_review_001"
    assert bundle["bundle_id"].startswith(
        "workflow-review-bundle:execution_review_001:"
    )
    assert "complete model context" in html
    assert "Evaluation &amp; Resolution" in html
    assert "fetch(" not in html
    assert "retry" not in html.lower()
    assert "approve" not in html.lower()


def test_review_bundle_requires_explicit_redaction_reason() -> None:
    with pytest.raises(ValueError, match="redaction reason"):
        build_workflow_review_bundle(
            _inspection(),
            contents=(
                ReviewContent(
                    content_ref="prompt-envelope:review_001",
                    content_sha256=HASH,
                    media_type="text/plain",
                    redaction_state="metadata_only",
                ),
            ),
        )


def test_review_bundle_rejects_duplicate_execution_record_ids() -> None:
    inspection = _inspection()
    modules = inspection["modules"]
    assert isinstance(modules, list)
    modules.append(modules[0])

    with pytest.raises(ValueError, match="module_run_id"):
        build_workflow_review_bundle(inspection)


def test_review_bundle_detects_tampering() -> None:
    bundle = build_workflow_review_bundle(_inspection())
    bundle["inspection"]["trace"]["workflow"]["status"] = "failed"

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_workflow_review_bundle(bundle)


def test_review_page_renders_registered_parallel_group() -> None:
    inspection = _inspection()
    workflow_release = inspection["workflow_release"]
    assert isinstance(workflow_release, dict)
    workflow_release["parallel_groups"] = [
        {
            "group_id": "overview_review_group",
            "control_node_id": "review_parallel",
            "branch_node_ids": ["fidelity_review", "reader_gain_review"],
            "join_node_id": "review_aggregate",
            "completion_outcome_id": "all_completed",
            "join_policy": "all_required",
        }
    ]

    html = render_workflow_review_html(build_workflow_review_bundle(inspection))

    assert "overview_review_group" in html
    assert "parallel_groups" in html
    assert "PARALLEL" in html


def test_review_cli_renders_bundle_to_one_offline_html(tmp_path: Path) -> None:
    bundle_path = tmp_path / "workflow_review_bundle.json"
    output_path = tmp_path / "workflow_review.html"
    bundle_path.write_text(
        json.dumps(build_workflow_review_bundle(_inspection())),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agent_runtime.inspection.inspection_snapshot_exporting",
            str(bundle_path),
            "--output",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == str(output_path)
    html = output_path.read_text(encoding="utf-8")
    assert "Agent Runtime Workflow Review" in html
    assert "execution_review_001" in html
    assert "fetch(" not in html
