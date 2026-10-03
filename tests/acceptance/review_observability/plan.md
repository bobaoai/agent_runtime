# Review progress liveness fixture

## Goal and subject

Validate one local, bounded command and let the host observe the Reviewer CLI while it runs. This is a test fixture, not a request to change application code or deploy anything. The exact submitted plan and the declared command are the whole subject; there is no repository, production data, database, network or additional context.

## Responsible actions

The test caller supplies the fixed command and checks Runtime's live notifications and final result. Runtime owns the command sandbox, capture and execution facts. The Reviewer runs the declared `liveness_probe` once, reads its actual response, and evaluates only the result requested here. The Reviewer does not add another command, choose a model, change permissions or infer application behavior.

## Input, output and failure

The command is `python -c "import time; time.sleep(3); print('liveness ok')"`, runs in scratch, has a 60-second command limit and denied network. The expected output is exit code zero with the exact marker `liveness ok`. If the command is unavailable, return `blocked` with the observed reason; if it executes and fails, do not return `passed`. No command execution evidence means the fixture has not passed.

## Verification and boundary

The Reviewer checks the actual command evidence. Separately, the caller checks that tool request and result notifications arrived while the same CLI process was alive and that only a compact validated review result was retained. The caller's liveness check is not a decision the Reviewer must make from its prompt. This fixture ends after that one check; it creates no release, persistent process log or follow-up task.
