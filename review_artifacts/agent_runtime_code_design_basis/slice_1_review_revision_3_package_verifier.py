"""Read-only verifier for one frozen Engineering Change Review package."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


def git(*args: str, binary: bool = False) -> bytes | str:
    return subprocess.check_output(["git", *args], text=not binary)


def main() -> int:
    package = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    base_ref = package["base_ref"]
    commit_ref = package["commit_ref"]
    observed: list[dict[str, str]] = []
    status_by_code = {"A": "added", "M": "modified", "D": "deleted"}
    for line in str(
        git("diff", "--name-status", "--no-renames", f"{base_ref}..{commit_ref}")
    ).splitlines():
        status_code, source_path = line.split("\t", maxsplit=1)
        state = status_by_code[status_code]
        ref = base_ref if state == "deleted" else commit_ref
        content = git("show", f"{ref}:{source_path}", binary=True)
        observed.append(
            {
                "path": source_path,
                "state": state,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    diff = git("diff", "--binary", f"{base_ref}..{commit_ref}", binary=True)
    errors: list[str] = []
    if observed != package["path_manifest"]:
        errors.append("path manifest or per-path SHA-256 differs")
    diff_sha256 = hashlib.sha256(diff).hexdigest()
    if diff_sha256 != package["candidate_diff_sha256"]:
        errors.append("candidate diff SHA-256 differs")
    print(
        json.dumps(
            {
                "candidate_diff_sha256": diff_sha256,
                "path_count": len(observed),
                "errors": errors,
            },
            sort_keys=True,
        )
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
