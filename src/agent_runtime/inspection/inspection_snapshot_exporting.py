"""Command-line entry point for the offline Workflow review page."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inspection_snapshot_rendering import render_workflow_review_html


def main() -> int:
    """Render the requested bundle and return a process exit status."""

    parser = argparse.ArgumentParser(
        description="Render one Agent Runtime Workflow review bundle as HTML"
    )
    parser.add_argument("bundle", type=Path, help="workflow_review_bundle JSON file")
    parser.add_argument("--output", required=True, type=Path, help="HTML output path")
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    html = render_workflow_review_html(bundle)
    args.output.write_text(html, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
