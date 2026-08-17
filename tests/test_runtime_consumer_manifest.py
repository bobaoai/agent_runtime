from __future__ import annotations

from pathlib import Path

from agent_runtime.conformance.conformance_consumer_manifesting import (
    build_downstream_consumer_manifest,
    validate_downstream_consumer_manifest,
)


def _write_consumer(path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_consumer_manifest_records_each_imported_symbol(tmp_path: Path) -> None:
    _write_consumer(
        tmp_path / "src/product/runtime_client.py",
        "from agent_runtime.registry import RuntimeReleaseRegistry\n"
        "from agent_runtime.contracts.registry_workflow_definition import (\n"
        "    WorkflowRuntimeRegistration,\n"
        ")\n",
    )

    manifest = build_downstream_consumer_manifest(
        consumer_id="synthetic_host",
        consumer_root=tmp_path,
        consumer_git_commit="a" * 40,
    )

    assert manifest["summary"] == {
        "site_count": 2,
        "source_file_count": 1,
        "disposition_counts": {"keep": 0, "replace": 1, "retire": 1},
    }
    assert [site["imported_symbol"] for site in manifest["sites"]] == [
        "RuntimeReleaseRegistry",
        "WorkflowRuntimeRegistration",
    ]
    assert [site["disposition"] for site in manifest["sites"]] == [
        "replace",
        "retire",
    ]
    assert validate_downstream_consumer_manifest(
        manifest,
        consumer_root=tmp_path,
    ) == ()


def test_consumer_manifest_detects_symbol_drift(tmp_path: Path) -> None:
    target = tmp_path / "src/product/runtime_client.py"
    _write_consumer(
        target,
        "from agent_runtime.registry import RuntimeReleaseRegistry\n",
    )
    manifest = build_downstream_consumer_manifest(
        consumer_id="synthetic_host",
        consumer_root=tmp_path,
        consumer_git_commit="b" * 40,
    )
    target.write_text(
        "from agent_runtime.registry import RuntimeReleaseBundle\n",
        encoding="utf-8",
    )

    assert validate_downstream_consumer_manifest(
        manifest,
        consumer_root=tmp_path,
    ) == ("downstream Runtime import surface differs from frozen manifest",)
