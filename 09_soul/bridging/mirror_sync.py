#!/usr/bin/env python3
"""
Mirror sync tool for 09_soul/ → 09_<agent>/ projection layer.

Design principle: zero metadata in mirrored documents. All sync state lives
in 09_soul/bridging/mirror_manifest.json. Mirror files themselves are pure
source content (+ optional project-local addendum) so they cost no extra
tokens when loaded by the agent.

Manifest format (mirror_manifest.json):
    {
      "mirrors": [
        {
          "source": "09_soul/core/SOUL.md",
          "target": "09_claude/core/SOUL.md",
          "source_hash": "sha256:...",
          "last_synced": "YYYY-MM-DD",
          "addendum_marker": "## Project-Specific Addendum"
        },
        ...
      ]
    }

Addendum handling:
    If `addendum_marker` is non-null, the tool treats the first occurrence
    of that exact line in the target as the start of project-local content;
    everything from that line onwards is preserved across syncs. Source
    content must NOT contain the addendum_marker line.

Usage:
    python 09_soul/bridging/mirror_sync.py --check
    python 09_soul/bridging/mirror_sync.py --apply
    python 09_soul/bridging/mirror_sync.py --apply --path 09_claude/core/SOUL.md
    python 09_soul/bridging/mirror_sync.py --register \\
        --source 09_soul/core/SOUL.md --target 09_claude/core/SOUL.md \\
        [--addendum-marker "## Project-Specific Addendum"]
    python 09_soul/bridging/mirror_sync.py --list

Exit codes:
    0  no drift (or --apply succeeded)
    1  drift detected (--check) or sync/register failed
    2  invocation error
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / "09_soul" / "bridging" / "mirror_manifest.json"


@dataclass
class MirrorEntry:
    source: str
    target: str
    source_hash: str
    last_synced: str
    addendum_marker: str | None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "source_hash": self.source_hash,
            "last_synced": self.last_synced,
            "addendum_marker": self.addendum_marker,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MirrorEntry":
        return cls(
            source=d["source"],
            target=d["target"],
            source_hash=d.get("source_hash", ""),
            last_synced=d.get("last_synced", ""),
            addendum_marker=d.get("addendum_marker"),
        )


def sha256_of_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_manifest() -> list[MirrorEntry]:
    if not MANIFEST_PATH.exists():
        return []
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    return [MirrorEntry.from_dict(m) for m in data.get("mirrors", [])]


def save_manifest(mirrors: list[MirrorEntry]) -> None:
    payload = {"mirrors": [m.to_dict() for m in mirrors]}
    MANIFEST_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def split_addendum(target_text: str, marker: str | None) -> tuple[str, str]:
    """Return (head_to_drop, addendum_to_preserve).

    head_to_drop is everything before the marker (will be overwritten with source).
    addendum_to_preserve is the marker line and everything after.
    If marker is None or not found, addendum is empty.
    """
    if not marker:
        return target_text, ""
    lines = target_text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip("\n") == marker:
            head = "".join(lines[:i])
            addendum = "".join(lines[i:])
            return head, addendum
    return target_text, ""


def render_target(source_text: str, addendum: str) -> str:
    """Build new target file content = source + (blank line) + addendum."""
    body = source_text.rstrip()
    if addendum:
        return body + "\n\n" + addendum.rstrip() + "\n"
    return body + "\n"


def cmd_check(mirrors: list[MirrorEntry]) -> int:
    drift = 0
    in_sync = 0
    missing = 0
    print("Mirror sync drift report")
    print("-" * 60)
    if not mirrors:
        print("  (manifest is empty)")
        return 0
    for m in mirrors:
        source_path = REPO_ROOT / m.source
        target_path = REPO_ROOT / m.target
        if not source_path.exists():
            print(f"  MISSING SOURCE  {m.target}  (source: {m.source})")
            missing += 1
            continue
        if not target_path.exists():
            print(f"  MISSING TARGET  {m.target}  (run --apply to create)")
            drift += 1
            continue
        actual_hash = sha256_of_text(source_path.read_text(encoding="utf-8"))
        if actual_hash == m.source_hash:
            print(f"  in-sync         {m.target}  (last-synced {m.last_synced or 'unknown'})")
            in_sync += 1
        else:
            print(f"  DRIFT           {m.target}")
            print(f"                  source: {m.source}")
            print(f"                  last-synced: {m.last_synced or 'unknown'}")
            print(f"                  declared: {m.source_hash[:19]}...")
            print(f"                  actual:   {actual_hash[:19]}...")
            drift += 1
    print("-" * 60)
    print(
        f"Summary: {in_sync} in-sync / {drift} drift / {missing} missing-source / {len(mirrors)} total"
    )
    return 0 if drift == 0 and missing == 0 else 1


def cmd_apply(mirrors: list[MirrorEntry], path_filter: str | None) -> int:
    today = _dt.date.today().isoformat()
    applied = 0
    failed = 0
    skipped = 0
    for m in mirrors:
        if path_filter and m.target != path_filter:
            skipped += 1
            continue
        source_path = REPO_ROOT / m.source
        target_path = REPO_ROOT / m.target
        if not source_path.exists():
            print(f"  FAIL   {m.target}: source missing ({m.source})")
            failed += 1
            continue
        source_text = source_path.read_text(encoding="utf-8")
        source_hash = sha256_of_text(source_text)

        if m.addendum_marker and m.addendum_marker in source_text:
            print(
                f"  FAIL   {m.target}: source contains addendum marker "
                f"{m.addendum_marker!r} (would collide with project-local segment)"
            )
            failed += 1
            continue

        if target_path.exists():
            target_text = target_path.read_text(encoding="utf-8")
            _, addendum = split_addendum(target_text, m.addendum_marker)
        else:
            addendum = ""

        new_content = render_target(source_text, addendum)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(new_content, encoding="utf-8")

        if source_hash == m.source_hash and target_path.exists():
            verb = "REWRITE"
        else:
            verb = "SYNC"
        m.source_hash = source_hash
        m.last_synced = today
        applied += 1
        print(f"  {verb}  {m.target} ← {m.source}")

    if applied:
        save_manifest(mirrors)
    print("-" * 60)
    print(f"Summary: {applied} synced / {failed} failed / {skipped} filtered out")
    return 0 if failed == 0 else 1


def cmd_register(
    mirrors: list[MirrorEntry],
    source: str,
    target: str,
    addendum_marker: str | None,
) -> int:
    source_path = REPO_ROOT / source
    if not source_path.exists():
        print(f"FAIL: source does not exist: {source}", file=sys.stderr)
        return 2
    for m in mirrors:
        if m.target == target:
            print(f"FAIL: target {target} already registered", file=sys.stderr)
            return 2

    today = _dt.date.today().isoformat()
    source_text = source_path.read_text(encoding="utf-8")
    source_hash = sha256_of_text(source_text)
    if addendum_marker and addendum_marker in source_text:
        print(
            f"FAIL: source contains addendum marker {addendum_marker!r}; "
            f"choose a unique heading not present in source",
            file=sys.stderr,
        )
        return 2

    new_entry = MirrorEntry(
        source=source,
        target=target,
        source_hash=source_hash,
        last_synced=today,
        addendum_marker=addendum_marker,
    )
    mirrors.append(new_entry)

    target_path = REPO_ROOT / target
    if target_path.exists():
        target_text = target_path.read_text(encoding="utf-8")
        _, addendum = split_addendum(target_text, addendum_marker)
    else:
        addendum = ""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(render_target(source_text, addendum), encoding="utf-8")

    save_manifest(mirrors)
    print(f"REGISTER  {target} ← {source}  (addendum_marker={addendum_marker!r})")
    return 0


def cmd_list(mirrors: list[MirrorEntry]) -> int:
    if not mirrors:
        print("(manifest is empty)")
        return 0
    for m in mirrors:
        marker = m.addendum_marker or "(none)"
        print(f"  {m.target}")
        print(f"    source:           {m.source}")
        print(f"    addendum_marker:  {marker}")
        print(f"    last_synced:      {m.last_synced or 'unknown'}")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Sync 09_soul → 09_<agent> mirror files using external manifest."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Report drift only.")
    mode.add_argument("--apply", action="store_true", help="Rewrite mirrors.")
    mode.add_argument("--register", action="store_true", help="Add a new mirror to manifest.")
    mode.add_argument("--list", action="store_true", help="Show registered mirrors.")
    parser.add_argument("--source", help="Source path, repo-relative (with --register).")
    parser.add_argument("--target", help="Target path, repo-relative (with --register).")
    parser.add_argument(
        "--addendum-marker",
        default=None,
        help='Addendum heading that splits source vs project-local content (e.g. "## Project-Specific Addendum").',
    )
    parser.add_argument("--path", help="Filter --apply to a specific target path.")
    args = parser.parse_args(argv)

    mirrors = load_manifest()

    if args.check:
        return cmd_check(mirrors)
    if args.apply:
        return cmd_apply(mirrors, args.path)
    if args.list:
        return cmd_list(mirrors)
    if args.register:
        if not args.source or not args.target:
            print("FAIL: --register requires --source and --target", file=sys.stderr)
            return 2
        return cmd_register(mirrors, args.source, args.target, args.addendum_marker)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
