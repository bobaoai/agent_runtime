"""Command-line entry point for offline inspection: a Workflow review page or a saved Test Run record."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .inspection_snapshot_rendering import render_workflow_review_html


def build_parser() -> argparse.ArgumentParser:
    """Return the agent-runtime-inspect argument grammar."""
    from .inspection_test_run_reading import TEST_RUN_INSPECTION_VIEWS
    parser = argparse.ArgumentParser(
        prog="agent-runtime-inspect",
        description="Render one Agent Runtime Workflow review bundle as HTML, or inspect a saved local Test Run "
                    "record and the Attempt archives Runtime wrote for it. Read-only: nothing is registered, run, "
                    "retried or queried in PostgreSQL.",
        epilog="--test-run-record prints runtime_test_run_inspection_v1 JSON. Exit 0: the saved record was read, "
               "whatever the run's outcome (completed, failed, cancelled, or no_record for the error document of a "
               "run that returned no record). Exit 1: the record is missing or unreadable (error on stderr). "
               "Exit 2: invalid arguments.")
    parser.add_argument("bundle", nargs="?", type=Path, help="workflow_review_bundle JSON file (with --output)")
    parser.add_argument("--output", type=Path, help="HTML output path for the bundle")
    parser.add_argument("--test-run-record", type=Path,
                        help="record saved by agent-runtime-test-run --record-out (or captured stdout)")
    parser.add_argument("--view", choices=TEST_RUN_INSPECTION_VIEWS, default="summary",
                        help="summary (default), output, log or files; used with --test-run-record")
    return parser


def main(argv=None) -> int:
    """Render the requested bundle or inspect a saved Test Run record and return a process exit status."""
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.test_run_record is not None:
        if args.bundle is not None or args.output is not None:
            parser.error("give a bundle with --output, or --test-run-record, not both")
        from .inspection_test_run_reading import inspect_test_run_record
        try:
            result = inspect_test_run_record(args.test_run_record, view=args.view)
        except (OSError, ValueError) as exc:
            print(json.dumps({"error_type": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False),
                  file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.bundle is None or args.output is None:
        parser.error("give a bundle with --output, or --test-run-record")
    bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
    html = render_workflow_review_html(bundle)
    args.output.write_text(html, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
