# Batch 1 plan review R2

- Subject: CODE_DESIGN_BATCH1.md, sha256 81488656db5fda1e93f3697769f52c62ebc9fab39ad2d3f6283af0c416974445 (bound by the reviewer)
- Baseline file: baselines/windows_main_2f39158_failed.txt, sha256 ef9ada1fb1e464b6e2c1c0fe68009220023b55000191e9e1c80f5c4bf1e17331
- Purpose: code_design
- Date: 2026-11-05
- Executor: `claude -p --model opus` with read-only git and hash commands (substitute for the Runtime engineering_change_reviewer). A first R2 run was interrupted by a session restart and produced no output; this is the complete rerun with the same prompt.
- Result: passed, two notes (not implemented). The output ends with a repeated sentence fragment, kept as produced.

## Reviewer output, verbatim

**Verdict: `passed`.** The plan is ready to implement, with two optional notes. This verdict does not authorise implementation, a commit or a push.

**Binding:** The working-tree file `docs/proposals/windows_host_platform/CODE_DESIGN_BATCH1.md` hashes to `81488656…16974445`, which matches the expected value. The baseline file hashes to `ef9ada1f…e17331`, which also matches, and it holds 520 node IDs. HEAD is 8704f37 and `main` is 2f39158. The only untracked change is the proposal folder.

### Check results

| check_id | disposition | assessment |
|---|---|---|
| 1 | passed | The subject is the bound plan. The goal, criteria (a)–(c) and scope are explicit: batch 1 only, batches 2–5 excluded. `README.md`, the R1 record and the earlier lease plan were read as background only. |
| 2 | passed | The basis is sufficient: the owner's decision is recorded and no Design meaning changes. Design 02 §5 keeps Foundation for stateless deterministic primitives, so an OS lock belongs with Invocation (Design 08). That is the same owner as `invocation_workspace_preparation` (registry line 663). No Design in 02, 06 or 08 states anything about host platforms. |
| 3 | passed | Scope, order and delivery agree: one commit on 8704f37, no history rewrite, and merge left to the owner. The one observable change is stated and causes no harm: the lock file is now opened before the refusal on an unsupported host. |
| 4 | passed | Checked against 8704f37 lines 90–124: `EACCES`/`EAGAIN` still mean "live duplicate", any other `OSError` still means "cannot be acquired", and the unsupported host still gets the same refusal text (it moves to the selection module). The Windows release still seeks to the far offset before `LK_UNLCK`. Callers import only `lease_attempt_workspace`, `prepare_attempt_workspace` and `AttemptWorkspaceConflictError`, all unchanged. `invocation_local_resource_preparation.py:422` imports only the error class. Only the test file imports `_WINDOWS_LEASE_LOCK_OFFSET`. |
| 5 | passed | The final design is stated once. The fcntl→msvcrt fallback and the module-level guard are removed entirely, with no compatibility wrapper left behind. |
| 6 | passed | Registering the new files as plain `_source("invocation", …)` entries fits the registry. A binding needs a technology in `RUNTIME_REQUIRED_IMPLEMENTATION_TECHNOLOGY_IDS` (lines 35–42), and the validator rejects any change to that set (around lines 994–1006). The three stems match `_SOURCE_NAME` and `expected_file_name`. The architecture validation parses source with the AST and never imports it (`conformance_architecture_validation.py:161`). The public-surface check covers only manifested package `__all__` lists. So a module that can only be imported on one host breaks no check on either host. The import direction is now stated (resolves R1 F3), and no cycle is possible. |
| 7 | passed | **Selection on one host:** feasible. The test records the module name that selection asks to import, so both mappings are checked on any host (resolves R1 F2). **Fail-closed test:** the modified test still works. The `import msvcrt` statement inside the Windows module goes through `builtins.__import__` with that module's `__name__`, so the importer filter catches it. `importlib.import_module` itself is not affected, and the resulting `ImportError` produces `UnsupportedHostPlatform`. **Criterion (a):** now compared by failed test identity against two committed baselines (resolves R1 F1). A candidate failure is accepted only if it is in both baseline sets. Drift between the runs can therefore only produce false alarms, never hide a regression. A collection error shows up as an `ERROR` line that is not in either baseline, so it is caught too. **Criteria (b) and (c):** covered by the dispatch-source test and command 1. The baseline confirms that the two Codex adapter cases fail on `main`, and that the architecture and naming tests pass there. POSIX is honestly left as the owner's gate. |
| 8 | passed | The text is clear and keeps facts, causes and stopping conditions intact. The review history in §8 is appropriate for a plan record. |
| 9 | passed | The verdict, check results and findings agree. The notes below are not pass conditions. |

### Findings

**N1 — note** (check 7)
- **Location:** `CODE_DESIGN_BATCH1.md` §6, item 2, the `main` baseline bullet.
- **Evidence:** The stated provenance covers the host, date, clean worktree and totals. It names the command only through "Each run uses …", and gives no Python or pytest version and no worktree path. On Windows, path length affects some failures.
- **Requirement:** check 7, sufficient provenance.
- **Consequence:** None for correctness. The subset-of-both rule is conservative, and the 8704f37 baseline is freshly produced on the same host, so a mismatch can only cause a false regression for the author to investigate.
- **Optional improvement:** Say explicitly that the `main` baseline used the same command, and record the interpreter version. This saves time if an environment-only difference shows up. It is not a pass condition.

**N2 — note** (check 7)
- **Location:** §6, item 2, "in parallel with the candidate run".
- **Evidence:** Two full suites running at once on one host may compete for shared temporary space, ports or the process table.
- **Consequence:** That could add failures to both runs at the same time. Masking still requires the test to fail in the `main` baseline as well, which was run alone, so criterion (a) is not weakened. At worst it costs time investigating false alarms.
- **Optional improvement:** Run the two suites one after the other if spurious failures appear. It is not a pass condition.

### Re-judged prior findings
- **R1 F1 (fix): resolved.** Failures are now compared by node ID against two committed baselines, and new tests must pass.
- **R1 F2 (note): taken up.** The selection test asserts the requested module name.
- **R1 F3 (note): taken up.** §4 says the implementation modules import only the standard library, not the selection module.

### Safe next step
The plan may go forward as the pre-implementation basis for batch 1, following §7. This verdict grants no permission to implement, commit or push. Before the implementation review, attach the evidence for checks 1, 2 and 4 together with the committed 8704f37 baseline.
owner's gate. The implementation review must still check the actual code, both baseline files, the failed-set comparison and the check 4 evidence.
