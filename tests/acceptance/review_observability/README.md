# Installed Engineering Review live-progress gate

This directory is a reusable acceptance fixture for [Runtime Design 08](../../../designDoc/agent_runtime_08_agent_execution_adapter_contract.md) and the [review-observability CodeDesignBasis](../../../docs/proposals/review_observability/CODE_DESIGN.md). It is not a production wrapper, a new review channel, or a Provider transcript store. It calls the already-installed Portable `engineering_review.py` entry.

Run it only after the reviewed Runtime is installed in the host Python environment and the reviewed Portable source is installed through its normal installer. The host root must already contain the registered `engineering_change_reviewer` single-node Workflow. Confirm its exact version with `agent-runtime-registry load`, and confirm authentication on the same Claude executable outside any sandbox before sending material. The fixture itself contains no business data, but it invokes `claude-opus-5-5` at `xhigh` and executes one three-second, network-denied local command, so a real Provider test authorization is required.

From this Runtime repository, using the host's configured Python:

```sh
/path/to/host/.venv/bin/python -B tests/acceptance/review_observability/verify_installed_review.py \
  --root /path/to/host \
  --entry /path/to/host/09_soul/governance/t0/validation/software_delivery/engineering_review.py \
  --workflow-version EXACT_REGISTERED_VERSION \
  --cli-path /path/to/authenticated/claude \
  --review-output /path/to/new-compact-review.json \
  --verification-output /path/to/new-verification.json
```

Both output paths must be new and different. `--run-timeout-seconds` defaults to 1200 for this bounded test; it does not change workspace execution parameters. The script uses the adjacent `plan.md` and `commands.json` and no repository context. It reads stderr as the installed entry emits each safe progress line, checks the same CLI PID and creation time are still live when both declared-command request and correlated result lines are received, and separately checks command ID notifications, event order, the owning semantic validator, final verdict and compact saved-record reuse. It writes only the compact review JSON from the Portable entry and a small Boolean/count verification JSON. No raw stdout/stderr, tool arguments, output, prompt, event history or Provider trace is saved by this harness.

`status=passed` requires every verification check. A failure or inconclusive OS liveness check is not a pass. Inspect the safe verification file and the installed entry's actual terminal state; do not automatically rerun a failed or timed-out Provider call. A new deliberate run needs new output paths. This gate does not register definitions, write PG or prove unrelated workflows.
