"""Installed evaluation CLI composing registered execution and capability examples.

The command name remains agent-runtime-evaluate. Execution keeps its generic
single-node entry; the verification frontend owns example selection and never
adds example knowledge to the execution kernel.
"""
import json
import sys

from .execution_local_evaluation import build_parser as _execution_parser, main as _execute_registered
from .conformance_agent_execution import run_agent_example


def build_parser():
    """Compose the real execution argument grammar with the fixed example choices."""
    parser = _execution_parser(require_workflow=False, require_input=False)
    parser.description = "Evaluate a registered single-Module Workflow or a packaged multi-Agent example. Select exactly one of --workflow and --example. No PG or production authorization."
    parser.epilog += "\n\nPackaged examples register fixed definitions and run a fresh execution. Saved JSON is evidence, not cross-process resume. See docs/agent_runtime_capability_runbook.md for example use and result interpretation."
    parser.add_argument("--example", choices=("agent_capability_example", "agent_evaluation_example"),
        help="Register and run a packaged example. capability covers query/writer/parallel reviews; evaluation covers Agent-requested child review and independent evaluation. Definitions are saved under root; execution is temporary. --input is optional and accepts exactly task and required_facts; omission uses the packaged finite fixture.")
    parser.add_argument("--scenario", choices=("accepted", "revision", "wait"),
        help="Capability example only: first selector decision. revision returns to the Writer; wait records WAIT then supplies one matching material_ready fixture event in the same process. Default accepted; not cross-process resume.")
    return parser


def main(argv=None):
    """Run the selected path and print its actual execution records as JSON.

    Exactly one of --workflow and --example is required. --workflow also requires
    --input and delegates unchanged arguments to the existing execution command.
    --example owns fixed definitions and fixture resources, so --version and
    --resources are rejected. Optional --input contains task and required_facts.
    Exit 0 means technical completion, not business acceptance or full Runtime
    conformance. Exit 1 means input/environment/execution/cleanup failure or an
    unfinished graph; captured facts remain in returned JSON when available.
    Exit 2 means invalid command arguments before setup or model execution.
    Exit 130 means user cancellation, retaining captured records when started.
    Examples register their exact source and create temporary execution resources;
    the normal --workflow path retains its existing setup and no-registration effects.

    For --example, a caught graph error is printed in the execution JSON on stdout:
    status=failed, stop_reason=execution_error, failure={error_type, detail}. Errors
    escaping setup/execution/cleanup are printed on stderr as error_type,
    error_code (null when absent), and detail. These are different output shapes.
    Read these results with the node logs; a last running snapshot is not proof
    that resources remain usable. Preserve exception types and diagnostics.
    --scenario wait automatically supplies a fixture event; there is no interactive
    resume command. A new CLI invocation starts another execution.
    """
    arguments = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(arguments)
    if bool(args.workflow) == bool(args.example):
        parser.error("select exactly one of --workflow and --example")
    if args.workflow:
        if args.input is None:
            parser.error("--workflow requires --input")
        if args.scenario is not None:
            parser.error("--scenario requires --example")
        return _execute_registered(arguments)
    if args.version is not None or args.resources is not None:
        parser.error("packaged examples own their fixed versions and fixture resources")
    if args.example == "agent_evaluation_example" and args.scenario not in (None, "accepted"):
        parser.error("agent_evaluation_example does not use selector scenarios")
    try:
        payload = None if args.input is None else json.loads(args.input.read_text(encoding="utf-8"))
        record = run_agent_example(args.root, args.example, scenario=args.scenario or "accepted", input_payload=payload,
            transport_kind=args.transport, model_id=args.model, reasoning_profile=args.effort, cli_path=args.cli_path)
        print(json.dumps(record, ensure_ascii=False, allow_nan=False))
        return 0 if record["status"] == "completed" else 130 if record["status"] == "cancelled" else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "Evaluation interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps({"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
                          "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
