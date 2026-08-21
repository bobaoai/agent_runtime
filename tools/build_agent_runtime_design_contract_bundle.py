"""Generate the Design Contract bundle shipped with Agent Runtime.

Canonical contracts stay in ``designDoc``.  This build tool copies their exact
bytes into the standalone distribution and writes a deterministic manifest.
The generated directory is a release artifact and must never be edited by
hand.

The manifest contains only Runtime-owned contracts and records, per document,
every relative Markdown link that leaves the bundle toward a declared external
authority document. Any other unresolvable relative link fails the build.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import posixpath
import re
import tomllib


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "src" / "agent_runtime" / "design_contract"
BUNDLE_SCHEMA_VERSION = "agent_runtime_design_contract_bundle_v2"
RUNTIME_OWNED_AUTHORITY = "agent_runtime"
CANONICAL_DOCUMENTS = (
    "designDoc/the_agent_runtime.md",
    "designDoc/agent_runtime_00_execution_charter.md",
    "designDoc/agent_runtime_01_module_contract_and_assembly.md",
    "designDoc/agent_runtime_03_authorized_external_event_ingress.md",
    "designDoc/agent_runtime_06_standalone_package_and_lifecycle_contract.md",
    "designDoc/agent_runtime_07_temporal_durable_adapter_contract.md",
    "designDoc/agent_runtime_08_agent_execution_adapter_contract.md",
    "designDoc/agent_runtime_09_authorization_integration_contract.md",
)
EXTERNAL_AUTHORITY_LINK_TARGETS = frozenset(
    {
        "agency_platform_13_execution_visibility_contract.md",
        "data_governance_10_data_access_gateway_contract.md",
        "product_authorization_00_service_and_persistence_contract.md",
        "the_agency_platform.md",
        "the_artifact_graph.md",
        "the_charter.md",
        "the_contract_audit.md",
        "the_data_governance.md",
        "the_product_authorization.md",
        "the_skill_management.md",
        "the_software_delivery.md",
        "the_task_routing.md",
        "the_timestamp_semantic.md",
    }
)
_MARKDOWN_LINK = re.compile(r"\]\(([^)#\s]+?\.md)(?:#[^)]*)?\)")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _runtime_release_version(project_root: Path) -> str:
    configuration = tomllib.loads(
        (project_root / "pyproject.toml").read_text(encoding="utf-8")
    )
    return str(configuration["project"]["version"])


def _document_external_references(
    source_name: str,
    body_text: str,
    packaged_names: frozenset[str],
) -> list[str]:
    """Validate link closure and return declared out-of-bundle references."""

    external: set[str] = set()
    for match in _MARKDOWN_LINK.finditer(body_text):
        target = match.group(1)
        if target.startswith(("http://", "https://")):
            continue
        normalized_target = posixpath.normpath(target)
        if normalized_target in packaged_names:
            continue
        if normalized_target in EXTERNAL_AUTHORITY_LINK_TARGETS:
            external.add(normalized_target)
            continue
        raise RuntimeError(
            f"unresolvable Design Contract link in {source_name}: {target}"
        )
    return sorted(external)


def build_design_contract_bundle(
    *,
    project_root: Path = PROJECT_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    check: bool = False,
) -> dict[str, object]:
    """Generate or verify the deterministic standalone contract bundle."""

    packaged_names = frozenset(
        Path(source_name).name for source_name in CANONICAL_DOCUMENTS
    )
    documents: list[dict[str, object]] = []
    expected_files: dict[Path, bytes] = {}
    for source_name in CANONICAL_DOCUMENTS:
        source_path = project_root / source_name
        if not source_path.is_file():
            raise FileNotFoundError(f"missing canonical Runtime contract: {source_name}")
        body = source_path.read_bytes()
        package_name = Path(source_name).name
        expected_files[output_root / package_name] = body
        documents.append(
            {
                "contract_name": package_name.removesuffix(".md"),
                "source_path": source_name,
                "package_path": f"agent_runtime/design_contract/{package_name}",
                "sha256": _sha256_bytes(body),
                "authority": RUNTIME_OWNED_AUTHORITY,
                "external_references": _document_external_references(
                    source_name,
                    body.decode("utf-8"),
                    packaged_names,
                ),
            }
        )

    manifest_payload: dict[str, object] = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "runtime_release_version": _runtime_release_version(project_root),
        "documents": documents,
    }
    manifest = {
        **manifest_payload,
        "bundle_sha256": _sha256_bytes(_canonical_json(manifest_payload)),
    }
    expected_files[output_root / "manifest.json"] = _canonical_json(manifest)

    if check:
        actual_files = {
            path
            for path in output_root.iterdir()
            if path.is_file()
        } if output_root.is_dir() else set()
        if actual_files != set(expected_files):
            raise RuntimeError("generated Agent Runtime Design Contract file set is stale")
        stale = [
            path
            for path, expected in expected_files.items()
            if path.read_bytes() != expected
        ]
        if stale:
            names = ", ".join(path.name for path in stale)
            raise RuntimeError(f"generated Agent Runtime Design Contract is stale: {names}")
        return manifest

    output_root.mkdir(parents=True, exist_ok=True)
    for path in output_root.iterdir():
        if path.is_file() and path not in expected_files:
            path.unlink()
    for path, body in expected_files.items():
        path.write_bytes(body)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the Agent Runtime Design Contract bundle"
    )
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    manifest = build_design_contract_bundle(
        output_root=args.output,
        check=args.check,
    )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
