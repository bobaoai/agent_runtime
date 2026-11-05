# Batch 1 plan review R1

- Subject: CODE_DESIGN_BATCH1.md, sha256 36c0cc95746e4ad6fcfa1ab115507df74da32cd02d2a59dc814f5945c6defcb7
- Purpose: code_design
- Date: 2026-11-05
- Executor: `claude -p --model opus` with read-only git and hash commands (substitute for the Runtime engineering_change_reviewer)
- Result: non_pass. F1 (fix): acceptance (a) needs per-test failed sets, not counts. F2 and F3 are notes. Answered by the next revision.

## Reviewer output, verbatim

**Verdict: `non_pass`** — the plan is sound and close to ready, but one verification gap needs fixing in this batch.

**Binding:** the working-tree file `docs/proposals/windows_host_platform/CODE_DESIGN_BATCH1.md` hashes to `36c0cc95…6defcb7`, which matches the expected value. HEAD is 8704f37 and `main` is 2f39158. The only untracked change is the proposal folder.

### Check results

| check_id | disposition | assessment |
|---|---|---|
| 1 | passed | The subject is the bound plan. The goal, acceptance criteria (a)–(c) and scope (batch 1 only; batches 2–5 excluded) are explicit. `README.md` and the earlier lease proposal were used as background only. |
| 2 | passed | The basis is sufficient. The owner decision is recorded and no Design meaning changes. Design 02 §4 says exact path and module layout are code truth. Design 05 excludes operating-system selection from its scope. Invocation ownership resolves to Design 08, the same owner as the existing `invocation_workspace_preparation` source (registry line 663). |
| 3 | passed | Scope, order and delivery are consistent: one commit on 8704f37, no history rewrite, and merge left to the owner. The one observable change (the lock file is now opened before refusal on an unsupported host) is declared and harmless. |
| 4 | passed | I checked the inputs, outputs and failure mapping against 8704f37. `EACCES`/`EAGAIN` still mean "live duplicate", any other `OSError` still means "cannot be acquired", and the unsupported host still fails closed with an unchanged message that contains "POSIX". The public API and the codex monkeypatch targets (`lease_attempt_workspace`) are unchanged. The only importer of `_WINDOWS_LEASE_LOCK_OFFSET` is the lease test file, as the plan says. |
| 5 | passed | The final design is stated once. The fcntl→msvcrt fallback is removed in full, not kept as a compatibility path. |
| 6 | passed | Plain `_source("invocation", …)` registration is consistent with the registry. Bindings exist only for the six required technologies, and the validator rejects any change to that set (registry lines 998–1006). An OS host is not a registered technology, so a binding is not required and would in fact fail validation. All three names match `_SOURCE_NAME` and `expected_file_name`. No conformance check or test imports every source module, so a module that can only be imported on one platform does not break the suite on either host. |
| 7 | fix | Single-host testing of the selection logic is feasible: intercepting `importlib` can record which module name was requested. The lease and dispatch tests also cover criteria (b) and (c). Criterion (a), however, cannot be shown from the baseline the plan names (F1). |
| 8 | not_run | Not run, because a semantic fix is open. |
| 9 | passed | The verdict, check results and findings agree with each other. |

### Findings

**F1 — fix** (relates to check 7)
- **Location:** `CODE_DESIGN_BATCH1.md` §6, item 2 (line 78).
- **Evidence:** The baseline is given as counts "already recorded on this host": `main` 520 failed / 1350 passed / 92 skipped, and 8704f37 513 failed / 1358 passed (skipped not given). The acceptance rule, though, is per test: "no test that passes on `main` or on 8704f37 fails on the candidate". No per-test failed set for either baseline is recorded in the repository. The earlier lease plan (`windows_attempt_workspace_lease/CODE_DESIGN.md:82`) did require comparing failed-test sets.
- **Requirement:** criterion (a) and check 7. The verification must be able to prove the claimed result.
- **Consequence:** Count totals can hide a regression. For example, if one test that passes on 8704f37 breaks while another starts passing, the totals stay the same. Criterion (a) could then be reported as met when it is not.
- **Must recover:** the plan must compare failed-test identities (node IDs) for `main`, 8704f37 and the candidate on the same host. That needs a per-test baseline, either kept from earlier runs or produced by re-running both baselines. A count comparison is not enough.
- **Batch:** this batch. Criterion (a) is this batch's acceptance gate.

**F2 — note** (relates to check 7)
- **Location:** §6, test table row `test_selection_maps_windows_and_other_hosts`.
- **Evidence:** On Windows, `select_host_platform("linux")` returns `UnsupportedHostPlatform`, because importing the POSIX module fails on `fcntl`. The reverse happens on POSIX. The returned type therefore cannot show which module was chosen.
- **Optional improvement:** state that the test checks the module name that selection asked to import, through the import hook. That makes the "any host" claim self-evidently true. It is not a pass condition, since the hook mechanism the plan names already allows it.

**F3 — note** (relates to check 6)
- **Location:** §4, item 1 (the `importlib` selection) and items 2–3.
- **Evidence:** Design 02 §6 forbids hiding an import cycle behind a lazy import. The responsibility-level cycle check (`conformance_architecture_validation.py:221`) cannot see edges created by dynamic imports. The plan does not say that the two host operation modules must not import the selection module. No cycle is needed today, because `HostPlatform` is a structural `Protocol`.
- **Optional improvement:** say explicitly that the implementation modules do not import the selection module. This keeps a later batch from quietly adding a hidden cycle. It does not affect this batch's result.

### Safe next step

Revise §6 item 2 so it compares per-test failed sets, then send the plan back for re-review. This verdict does not authorise implementation or a commit.
