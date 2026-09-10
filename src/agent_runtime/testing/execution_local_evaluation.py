"""Installed command for a bounded, non-persistent registered Workflow test."""
import argparse
import json
from pathlib import Path
import sys

from ..execution.execution_local_invocation import evaluate_local_workflow_module


def build_parser():
    """Declare the actual evaluation arguments used by help and generated docs."""
    parser = argparse.ArgumentParser(description="Evaluate one registered Workflow; no PG or production authorization.")
    parser.add_argument("--root", required=True, type=Path, help="Root containing .runtime definitions; not a model read root.")
    parser.add_argument("--workflow", required=True, help="Registered single-node Workflow ID.")
    parser.add_argument("--version", help="Exact version; omit for the latest registered new definition.")
    parser.add_argument("--input", required=True, type=Path, help="JSON input prepared under the Module's input schema.")
    parser.add_argument("--transport", help="Independent transport; omit for Runtime default. Currently claude_cli only.")
    parser.add_argument("--model", help="Independent concrete model ID, verified against the response; omit for Runtime default.")
    parser.add_argument("--effort", help="Independent reasoning effort; omit for Runtime default.")
    parser.add_argument("--cli-path", type=Path, help="Installed provider executable; omit to resolve claude from PATH.")
    return parser


def main(argv=None):
    """Evaluate once and emit execution JSON on stdout without saving to PG.

    Exit 0: completed execution with schema-valid output, including a valid
    non_pass verdict. The subject owner applies its own semantic validator.
    Exit 1: input, execution or environment failure; stderr preserves the error
    type and available native error_code. Failed Attempts remain in stdout JSON.
    Exit 2: invalid arguments, including unsupported persistence options; no model
    is called. Exit 130: user interruption; no subject verdict is manufactured.
    Each invocation is a new temporary test. No cross-process recovery or stored
    history is promised. Explicit stdout capture belongs to the calling operator.
    """
    args = build_parser().parse_args(argv)
    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        record = evaluate_local_workflow_module(args.root, args.workflow, input_payload=payload,
            version=args.version, transport_kind=args.transport, model_id=args.model,
            reasoning_profile=args.effort, cli_path=args.cli_path)
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0 if record["status"] == "completed" else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "Evaluation interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
