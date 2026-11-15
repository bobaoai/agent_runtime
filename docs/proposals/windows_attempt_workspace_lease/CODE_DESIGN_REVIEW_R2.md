# Plan review R2: Windows Attempt workspace lease

- Subject: CODE_DESIGN.md, expected sha256 6a3aa0337c8f58b7653695a6fbe52342fec759954f4007ec5f78fd371c7fa4bf (not verified by the reviewer)
- Purpose: code_design
- Date: 2026-11-05
- Executor: `claude -p --model opus --tools Read,Grep,Glob,Bash` (substitute for the Runtime engineering_change_reviewer). In print mode the session had no usable shell.
- Result: blocked (B1), with one fix (F2) and four notes. Answered by the next plan revision.

## Reviewer output, verbatim except local host paths replaced with `[local host path redacted]`

**Verdict: `blocked`**

The task said to stop if the plan's sha256 differs from the expected value. I couldn't check it either way. This session only had Read, Grep and Glob, with no shell, so I could neither compute the hash nor run `git show main:<path>`. The checkout is on `feat/windows-attempt-workspace-lease` (`.git/HEAD`), not `main`. As a stand-in for `main` I read the installed copy at `[local host path redacted]`, but I can't prove it is commit 2f39158a. The review below is complete apart from that check. If the binding holds, the verdict would be `non_pass`, because of F2.

### Check results

| check_id | disposition | assessment | finding_ids |
|---|---|---|---|
| 1 | finding | The candidate's sha256 and the `main` baseline are both unverified. Goal and scope are clear, and no other material was reviewed as part of the subject. | B1 |
| 2 | passed | No file in designDoc/ mentions POSIX, fcntl, flock, Windows or macOS (grep found nothing). The POSIX-only rule exists only as a code comment (installed copy, lines 53–55). The lease contract (exclusive, released on crash, lock file outside the workspace) is unchanged. This is pure engineering and doesn't change Runtime Design meaning. | — |
| 3 | passed | Scope, exclusions and order match the goal. Call sites are as claimed: Claude line 542, Codex line 850. Codex prepares its environment at line 886, after the lease, so the claim that "the provider adapter cases fail at the symlink, not the lease" holds. | N3 |
| 4 | passed | The public function, error class and messages stay the same. On contention, `msvcrt.locking` with `LK_NBLCK` raises an `OSError` with `EACCES`, which the existing `EACCES`/`EAGAIN` branch already handles. The fail-closed message still contains "POSIX". | N1 |
| 5 | passed | The plan states only the final design. The choice of a far byte offset is justified, and no leftover compatibility path remains. | — |
| 6 | passed | Two private helpers in the module that already owns the lease. No new module or hidden dependency. | — |
| 7 | finding | The test table and the four commands mostly cover the goal. However, the plan's crash-release basis is not enough to keep the crash test reliable. POSIX is not verified, and the plan says so openly. | F2, N2, N4 |
| 8 | not_run | Skipped because a semantic fix is still open. | — |
| 9 | passed | Verdict, check lines and findings are consistent. | — |

### Findings

**B1 (block)**
- **Location:** CODE_DESIGN.md as a whole, and the baseline `main` 2f39158a.
- **Evidence:** No hash or git tool was available in this session. The working tree is on the feature branch, so `main` could not be read directly.
- **Requirement:** Check 1 (the reviewed object must be the bound, verified subject), plus the task's own condition to stop if the hash doesn't match.
- **Consequence:** Without it, this review can't be bound to the exact plan or the exact baseline.
- **What's needed:** The Primary Agent supplies `Get-FileHash -Algorithm SHA256` output and `git show main:` copies of the two in-scope files, or gives the reviewer a read-only shell. Then rerun.
- **Batch:** Must be resolved before any verdict counts.

**F2 (fix)**
- **Location:** §4 guarantee table, "Released on crash" row.
- **Evidence:** The plan admits Windows releases a dead process's locks asynchronously, then says the test "waits for the holder process to exit before re-acquiring." Waiting for the process to exit doesn't cover a release that happens after exit. (The working-tree test already retries for up to 10 s, but the plan is what's under review.)
- **Requirement:** Checks 4 and 7 (failure handling, and a verification arrangement that proves the result).
- **Consequence:** A test built to the plan as written can fail at random on Windows. That puts acceptance criterion (b) at risk, and a future implementation review could flag the retry as a departure from the plan.
- **What's needed:** The plan should state that recovery is retried within a fixed time limit, and that a lock never released still fails the test.
- **Batch:** Fix in this batch.

**N1 (note), R1 N1 re-judged**
- **Location:** §4 item 2.
- **Evidence:** "seeks … and calls … LK_NBLCK to acquire and LK_UNLCK to release" can be read as seeking only before the acquire. Writing the PID moves the file position, so a release without a fresh seek would unlock the wrong byte and raise inside `finally`.
- **Status:** The working tree seeks before both calls (lines 49 and 57).
- **Optional improvement:** Say explicitly that the release also seeks to the offset. Not a condition for passing.

**N2 (note), R1 N4 re-judged**
- **Location:** §6 item 3.
- **Evidence:** The POSIX path runs the same `flock` calls in the same order, and `_msvcrt` is `None` there. The plan openly says POSIX is not verified, and both acceptance criteria are Windows-only.
- **Optional improvement:** Name who runs the POSIX suite and when, before merge. Not a condition for passing.

**N3 (note), R1 N5 re-judged**
- **Location:** §1 list of exclusions.
- **Evidence:** `invocation_claude_cli_execution.py:543` uses `TemporaryDirectory(dir="/tmp")`, and `invocation_local_command_execution.py:112` uses `/tmp` too. Neither is in the exclusion list, even though §6 item 4 offers the Claude transport for the real-path check.
- **Consequence:** None for this batch, since the expected result is only "fails after the lease."
- **Optional improvement:** List the `/tmp` gap as a follow-up so the next Windows failure isn't a surprise. Not a condition for passing.

**N4 (note), R1 N3 re-judged**
- **Location:** §6 item 4.
- **Evidence:** "reinstall the candidate into a host" doesn't name the environment, for example `[local host path redacted]`.
- **Optional improvement:** Name it and record the failure stage you expect to see. Not a condition for passing.

**Other R1 points:** R1 N2 (the flaky crash test) is now F2 and has been raised to a fix, for the reason given there. The remaining points were checked against the code and are resolved:
- Same-process fencing holds: byte-range locks conflict between two handles even in one process.
- The fail-closed test hides `fcntl` and `msvcrt` only from the lease module, so `subprocess` still imports.
- The PID test reads only up to the end of the file, which is far below the locked byte.

### Next step

Supply the hash and the `main` evidence for B1, and revise §4 for F2. Then send the plan back for review. This review does not authorize implementation, commit or push.
