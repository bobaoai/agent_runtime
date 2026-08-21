"""Validate the exact public Agent Runtime repository boundary."""

from __future__ import annotations

import argparse
from fnmatch import fnmatchcase
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "public_repository_manifest.json"


def load_public_repository_manifest(
    path: Path = DEFAULT_MANIFEST,
) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "public_repository_manifest_v2":
        raise ValueError("unsupported public repository manifest schema")
    for field in (
        "required_paths",
        "allowed_exact_paths",
        "allowed_path_prefixes",
        "allowed_path_patterns",
        "forbidden_path_segment_sha256",
    ):
        values = payload.get(field)
        if not isinstance(values, list) or not all(
            isinstance(value, str) and value for value in values
        ):
            raise ValueError(f"invalid public repository manifest field: {field}")
    if any(
        not re.fullmatch(r"[0-9a-f]{64}", digest)
        for digest in payload["forbidden_path_segment_sha256"]
    ):
        raise ValueError("invalid forbidden path-segment fingerprint")
    fingerprints = payload.get("forbidden_content_fingerprints")
    if not isinstance(fingerprints, list):
        raise ValueError(
            "invalid public repository manifest field: "
            "forbidden_content_fingerprints"
        )
    for fingerprint in fingerprints:
        if (
            not isinstance(fingerprint, dict)
            or set(fingerprint) != {"length", "sha256"}
            or not isinstance(fingerprint["length"], int)
            or fingerprint["length"] < 1
            or not isinstance(fingerprint["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint["sha256"])
        ):
            raise ValueError("invalid forbidden content fingerprint")
    return payload


def tracked_repository_paths(root: Path) -> tuple[str, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return tuple(
        sorted(
            path
            for path in completed.stdout.decode("utf-8").split("\0")
            if path
        )
    )


def reachable_repository_objects(
    root: Path,
) -> tuple[tuple[str, str, str | None], ...]:
    """Return every content-bearing object reachable from local Git refs."""

    completed = subprocess.run(
        ["git", "rev-list", "--objects", "--all", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    object_paths: dict[str, set[str]] = {}
    object_ids: set[str] = set()
    for line in completed.stdout.splitlines():
        object_id, separator, path = line.partition(" ")
        object_ids.add(object_id)
        if separator and path:
            object_paths.setdefault(object_id, set()).add(path)
    commit_inventory = subprocess.run(
        ["git", "rev-list", "--all", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    for commit_id in commit_inventory.stdout.splitlines():
        tree_inventory = subprocess.run(
            ["git", "ls-tree", "-r", "-z", commit_id],
            cwd=root,
            check=True,
            capture_output=True,
        )
        for record in tree_inventory.stdout.decode("utf-8").split("\0"):
            if not record:
                continue
            metadata, path = record.split("\t", maxsplit=1)
            _mode, _declared_type, object_id = metadata.split(" ", maxsplit=2)
            object_ids.add(object_id)
            object_paths.setdefault(object_id, set()).add(path)
    if not object_ids:
        return ()

    sorted_object_ids = tuple(sorted(object_ids))
    type_check = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objectname) %(objecttype)"],
        cwd=root,
        check=True,
        capture_output=True,
        input="\n".join(sorted_object_ids) + "\n",
        text=True,
    )
    object_types = {
        object_id: object_type
        for line in type_check.stdout.splitlines()
        for object_id, object_type in (line.split(" ", maxsplit=1),)
    }
    content_objects: list[tuple[str, str, str | None]] = []
    for object_id, object_type in object_types.items():
        if object_type == "blob":
            content_objects.extend(
                (object_id, object_type, path)
                for path in object_paths.get(object_id, ())
            )
        elif object_type in {"commit", "tag"}:
            content_objects.append((object_id, object_type, None))
            content_objects.extend(
                (object_id, object_type, path)
                for path in object_paths.get(object_id, ())
            )
        elif object_type == "missing":
            content_objects.extend(
                (object_id, object_type, path)
                for path in object_paths.get(object_id, ())
            )
    return tuple(
        sorted(
            content_objects,
            key=lambda item: (item[0], item[1], item[2] or ""),
        )
    )


def _casefolded_sha256(value: str) -> str:
    return hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()


_PATHLIKE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_./-]+")


def _matching_content_fingerprints(
    text: str,
    *,
    manifest: Mapping[str, object],
) -> tuple[str, ...]:
    fingerprints_by_length: dict[int, set[str]] = {}
    for row in manifest["forbidden_content_fingerprints"]:
        fingerprints_by_length.setdefault(row["length"], set()).add(
            row["sha256"]
        )
    matches: set[str] = set()
    for token in _PATHLIKE_TOKEN_PATTERN.findall(text.casefold()):
        for length, fingerprints in fingerprints_by_length.items():
            if len(token) < length:
                continue
            for offset in range(len(token) - length + 1):
                digest = _casefolded_sha256(token[offset : offset + length])
                if digest in fingerprints:
                    matches.add(digest)
    return tuple(sorted(matches))


def public_path_is_allowed(
    path: str,
    *,
    manifest: Mapping[str, object],
) -> bool:
    exact = set(manifest["allowed_exact_paths"])
    prefixes = tuple(manifest["allowed_path_prefixes"])
    patterns = tuple(manifest["allowed_path_patterns"])

    def matches_pattern(pattern: str) -> bool:
        path_parts = PurePosixPath(path).parts
        pattern_parts = PurePosixPath(pattern).parts
        return len(path_parts) == len(pattern_parts) and all(
            fnmatchcase(part, pattern_part)
            for part, pattern_part in zip(path_parts, pattern_parts)
        )

    return (
        path in exact
        or path.startswith(prefixes)
        or any(matches_pattern(pattern) for pattern in patterns)
    )


def validate_public_repository_tree(
    *,
    root: Path,
    tracked_paths: Iterable[str],
    manifest: Mapping[str, object],
) -> tuple[str, ...]:
    paths = tuple(sorted(set(tracked_paths)))
    path_set = set(paths)
    findings: list[str] = []
    for required_path in manifest["required_paths"]:
        if required_path not in path_set:
            findings.append(f"missing required public path: {required_path}")
    forbidden_segment_sha256 = set(manifest["forbidden_path_segment_sha256"])
    for path in paths:
        blocked_segment_hashes = forbidden_segment_sha256.intersection(
            _casefolded_sha256(part) for part in PurePosixPath(path).parts
        )
        if blocked_segment_hashes:
            findings.append(
                "tracked path contains forbidden private segment: "
                f"{path}: sha256={sorted(blocked_segment_hashes)[0]}"
            )
            continue
        if not public_path_is_allowed(path, manifest=manifest):
            findings.append(f"tracked path is outside public boundary: {path}")
            continue
        absolute_path = root / path
        if absolute_path.is_symlink():
            findings.append(f"public repository cannot track a symlink: {path}")

    for path in paths:
        absolute_path = root / path
        if not absolute_path.is_file() or absolute_path.is_symlink():
            continue
        try:
            body = absolute_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(f"declared text path is not UTF-8: {path}")
            continue
        for fingerprint in _matching_content_fingerprints(
            body,
            manifest=manifest,
        ):
            findings.append(
                "public path contains forbidden private reference: "
                f"{path}: sha256={fingerprint}"
            )
    return tuple(sorted(findings))


def validate_public_repository_history(
    *,
    root: Path,
    manifest: Mapping[str, object],
    reachable_objects: Iterable[tuple[str, str, str | None]] | None = None,
) -> tuple[str, ...]:
    """Reject forbidden paths and text from every reachable Git object."""

    objects = tuple(
        reachable_repository_objects(root)
        if reachable_objects is None
        else reachable_objects
    )
    forbidden_segment_sha256 = set(manifest["forbidden_path_segment_sha256"])
    body_cache: dict[str, bytes] = {}
    findings: set[str] = set()

    for object_id, object_type, path in objects:
        if path is not None:
            blocked_segment_hashes = forbidden_segment_sha256.intersection(
                _casefolded_sha256(part) for part in PurePosixPath(path).parts
            )
            if blocked_segment_hashes:
                findings.add(
                    "reachable history path contains forbidden private segment: "
                    f"{path}: sha256={sorted(blocked_segment_hashes)[0]}"
                )
                continue
            if not public_path_is_allowed(path, manifest=manifest):
                findings.add(
                    f"reachable history path is outside public boundary: {path}"
                )
                continue
        if object_type == "missing":
            findings.add(
                "reachable history path references a missing object: "
                f"{path or '<metadata>'}: {object_id}"
            )
            continue
        body = body_cache.get(object_id)
        if body is None:
            body = subprocess.run(
                ["git", "cat-file", object_type, object_id],
                cwd=root,
                check=True,
                capture_output=True,
            ).stdout
            body_cache[object_id] = body
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            findings.add(
                "reachable historical text object is not UTF-8: "
                f"{object_type}:{object_id}:{path or '<metadata>'}"
            )
            continue
        for fingerprint in _matching_content_fingerprints(
            text,
            manifest=manifest,
        ):
            findings.add(
                "reachable history object contains forbidden private reference: "
                f"{object_type}:{object_id}:{path or '<metadata>'}: "
                f"sha256={fingerprint}"
            )
    return tuple(sorted(findings))


def validate_public_repository(
    *,
    root: Path = PROJECT_ROOT,
    manifest_path: Path | None = None,
) -> tuple[str, ...]:
    manifest = load_public_repository_manifest(
        manifest_path or root / "public_repository_manifest.json"
    )
    tree_findings = validate_public_repository_tree(
        root=root,
        tracked_paths=tracked_repository_paths(root),
        manifest=manifest,
    )
    history_findings = validate_public_repository_history(
        root=root,
        manifest=manifest,
    )
    return tuple(sorted(set(tree_findings + history_findings)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest_path = args.manifest.resolve()
    manifest = load_public_repository_manifest(manifest_path)
    if (
        not manifest["forbidden_path_segment_sha256"]
        or not manifest["forbidden_content_fingerprints"]
    ):
        print(
            "public release validation requires a non-empty private policy"
        )
        return 1
    findings = validate_public_repository(
        root=args.root.resolve(),
        manifest_path=manifest_path,
    )
    if findings:
        for finding in findings:
            print(finding)
        return 1
    print("public repository boundary: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
