# Plan review R3: Windows Attempt workspace lease

- Subject: CODE_DESIGN.md, sha256 68d019bc09b009fdd6412a65617e381a50b00344c1b716739972d29b637ae3d2 (bound by the reviewer; recomputed in full by the Primary Agent)
- Baseline: main 2f3915876f12 (verified by the reviewer with git rev-parse)
- Purpose: code_design
- Date: 2026-11-05
- Executor: `claude -p --model opus` with read-only git and hash commands (substitute for the Runtime engineering_change_reviewer)
- Result: passed, three notes. Notes are not implemented in this batch; N3 is answered by running check 4 before the implementation review.

## Reviewer output, verbatim

**Verdict: `passed`.** There are no `fix` or `block` findings. There are three notes, and none of them is a condition for passing.

**Binding:** the SHA-256 of `CODE_DESIGN.md` is `68D019BC…29B637AE3D2`, which matches the expected hash. `git rev-parse main` returns `2f3915876f12…`, the same as the plan's baseline `2f39158a`. I read the baseline with `git show main:`. `sha256sum` needed approval in this session, so I used `Get-FileHash`, which is read-only, instead.

### Check results

| check_id | disposition | assessment | finding_ids |
|---|---|---|---|
| 1 | passed | The subject is the bound plan and the baseline is `main` 2f39158a. The goal and scope match the authorization. The working-tree draft and the R1/R2 records were used as background only. R2 B1 is resolved: both the hash and the baseline are verified here. | — |
| 2 | passed | No file in `designDoc/` mentions POSIX, Windows, flock or a workspace lease. The plan keeps the lease contract unchanged: exclusive, released on crash, outside the workspace. The Windows asynchronous release does not change any Design meaning. `the_agent_runtime.md` §7 says that "a crash before invocation commitment may create a new Attempt". A new Attempt has a new `attempt_id`, so it gets a different lease file, and the release window does not touch the Design recovery path. The plan also discloses the difference to the owner. This is pure engineering. | — |
| 3 | passed | The scope, exclusions and order are consistent. The cited facts match `main`: the call sites are at Claude line 542 and Codex line 850. Claude's `/tmp` is at line 543. `symlink_to` and `readlink` are in `invocation_codex_environment.py` lines 64/72, and `codex_auth_directories` calls `readlink` at line 234. The `sandbox-exec`/darwin gate is at local_command line 74. The claim that 5 of 10 lease tests fail on Windows on `main` matches the test file: the same-process, outside-directory and crash tests, plus the two adapter cases. | N3 |
| 4 | passed | The public function, error class and conflict messages are unchanged. `LK_NBLCK` contention raises `EACCES`, which the existing branch already handles. A failed `os.lseek` goes through the same mapping. Without any OS lock the code still fails closed, and the message still contains "POSIX". The failure handling during acquisition and release is stated. | — |
| 5 | passed | The plan states only the final design. The choice of a far byte offset is justified, and no rejected alternatives or compatibility leftovers remain. | — |
| 6 | passed | Two private helpers go in the module that already owns the lease. There is no new module, wrapper or hidden dependency, since `msvcrt` is in the standard library. | — |
| 7 | passed | R2 F2 is resolved. The crash test retries recovery every 50 ms for at most 10 s and still fails if the lock is never released. The tests cover exclusivity per handle, contention between processes on the shared offset, PID readability and fail-closed. The adapter-case exception is grounded: in Codex, `prepare_codex_environment` (stage `provider_environment`) runs after the lease, and the fixture passes a real `auth_file`. POSIX is openly reported as not verified, with an owner gate before merge. That is outside acceptance criteria (a) and (b). | N2 |
| 8 | passed | The text is factual and keeps the causes and limits. One wording mismatch is listed under N1. | N1 |
| 9 | passed | The verdict, the check results and the notes are consistent. | — |

### Findings

**N1 (note)**
- **Location:** `CODE_DESIGN.md` §1 first paragraph, compared with the §4 guarantee table, "Released on crash" row.
- **Evidence:** §1 promises "the same guarantees it has on POSIX … released by the operating system when the holding process exits or crashes". §4 then says Windows releases these locks asynchronously, so an immediate retry can be refused for a short time.
- **Requirement:** Check 8 (facts and uncertainty are kept).
- **Consequence:** None for this batch. The table states the difference and routes it to the owner.
- **Optional improvement:** Qualify "same guarantees" in §1 so that the outcome statement and the table agree.

**N2 (note)**
- **Location:** §6 test table, the `test_workspace_lock_is_released_when_holder_process_crashes` row.
- **Evidence:** The 10 s bound is based on observation; Windows documents no upper bound for the release. The plan does not say which errors the retry loop accepts.
- **Requirement:** Check 7.
- **Consequence:** None now. F2's requirement is met because a lock that is never released still fails the test.
- **Optional improvement:** Retry only on the "live duplicate" conflict, so that any other lease error fails the test immediately instead of being absorbed by the loop.

**N3 (note)**
- **Location:** §7 steps 1–3, compared with §6 checks 3 and 4.
- **Evidence:** The order names checks 1 and 2 before the commit. Check 4 (the real-path run in a disposable environment) is not placed in that order. The plan also does not say whether its evidence goes to the implementation review.
- **Requirement:** Check 3.
- **Consequence:** None for acceptance, since (a) and (b) map to checks 2 and 1.
- **Optional improvement:** State when check 4 runs and where its evidence goes.

**Prior findings re-judged on this candidate:**
- **R2 B1:** resolved by the binding above.
- **R2 F2:** resolved in §6.
- **R2 notes:** all resolved:
  - The explicit seek before release is in §4 item 2.
  - The POSIX owner gate is in §6 item 3.
  - The `/tmp` follow-up is in §1.
  - The real-path environment and the expected `provider_environment`/WinError 1314 stage are in §6 item 4.
- **R1 N1–N5:** each is covered by one of these resolutions. I found none that still applies.

### Safe next step

The plan is a sufficient basis for implementation. The working-tree draft can be checked against it, and then the checks in §6 and the order in §7 apply. This review does not authorize the commit, the push or the merge to `main`; those stay with the Runtime owner. The POSIX suite run is still the owner's gate before merging to `main`.
