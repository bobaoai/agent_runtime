from __future__ import annotations

from pathlib import Path

from agent_runtime.conformance.conformance_consumer_manifesting import (
    build_downstream_consumer_manifest,
    main,
    validate_downstream_consumer_manifest,
    validate_downstream_consumer_retirement_readiness,
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
        "disposition_source_counts": {
            "derived_default": 2,
            "owner_decision": 0,
        },
    }
    assert [site["imported_symbol"] for site in manifest["sites"]] == [
        "RuntimeReleaseRegistry",
        "WorkflowRuntimeRegistration",
    ]
    assert [site["disposition"] for site in manifest["sites"]] == [
        "replace",
        "retire",
    ]
    assert {
        site["disposition_source"] for site in manifest["sites"]
    } == {"derived_default"}
    assert validate_downstream_consumer_manifest(
        manifest,
        consumer_root=tmp_path,
    ) == ()
    assert validate_downstream_consumer_retirement_readiness(manifest) == (
        "downstream Runtime consumer dispositions still require owner decisions: "
        + ", ".join(sorted(site["site_id"] for site in manifest["sites"])),
    )


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


def test_owner_decisions_are_required_before_consumer_retirement(
    tmp_path: Path,
) -> None:
    _write_consumer(
        tmp_path / "src/product/runtime_client.py",
        "from agent_runtime.registry import RuntimeReleaseRegistry\n",
    )
    manifest = build_downstream_consumer_manifest(
        consumer_id="synthetic_host",
        consumer_root=tmp_path,
        consumer_git_commit="c" * 40,
    )
    for site in manifest["sites"]:
        site["disposition"] = "keep"
        site["disposition_reason"] = "synthetic owner decision"
        site["disposition_source"] = "owner_decision"
    manifest["summary"]["disposition_counts"] = {
        "keep": 1,
        "replace": 0,
        "retire": 0,
    }
    manifest["summary"]["disposition_source_counts"] = {
        "derived_default": 0,
        "owner_decision": 1,
    }

    assert validate_downstream_consumer_manifest(
        manifest,
        consumer_root=tmp_path,
    ) == ()
    assert validate_downstream_consumer_retirement_readiness(manifest) == ()


def test_consumer_manifest_cli_builds_and_checks_the_same_artifact(
    tmp_path: Path,
) -> None:
    _write_consumer(
        tmp_path / "src/product/runtime_client.py",
        "from agent_runtime.registry import RuntimeReleaseRegistry\n",
    )
    output = tmp_path / "manifest.json"
    common = [
        "--consumer-id",
        "synthetic_host",
        "--consumer-root",
        str(tmp_path),
        "--consumer-git-commit",
        "d" * 40,
    ]

    assert main([*common, "--output", str(output)]) == 0
    assert main([*common, "--check-manifest", str(output)]) == 0
    wrong_commit = [*common]
    wrong_commit[-1] = "e" * 40
    assert main([*wrong_commit, "--check-manifest", str(output)]) == 1
