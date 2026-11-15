# Plan review R1: Windows Attempt workspace lease

- Subject: `CODE_DESIGN.md`, sha256 `6a3aa0337c8f58b7653695a6fbe52342fec759954f4007ec5f78fd371c7fa4bf`
- Purpose: code_design (before implementation)
- Date: 2026-11-05
- Reviewer: independent agent session using the `engineering_change_reviewer` prompt, checklist 1-9 and Software Delivery. Substitute review: Agent Runtime cannot execute the registered Reviewer on this Windows host (see CODE_DESIGN.md §8). It is not a Runtime `engineering_change_reviewer` verdict.
- Author self-check: 9 of 9 rows completed, accepted by `engineering_review.py --check-only --self-check`.

## Verdict

passed. Checks 1-9 passed. No fix or block findings. The reviewer judged the change a pure engineering change: no Runtime Design states supported operating systems, and the roadmap lists OS selection as a non-goal.

The reviewer verified the msvcrt mechanism with a scratch probe on this host (Python 3.11.18): a same-process second handle and another process are both refused with errno 13; the lock is re-acquired after the holder is killed; closing the handle releases the lock; another handle can read the PID; the far-offset lock does not extend the file; `LK_UNLCK` without seeking back to the lock offset fails with EACCES.

## Notes and disposition

| Note | Content | Disposition |
| --- | --- | --- |
| N1 | Plan does not say release must seek back to the lock offset before `LK_UNLCK`. | Implemented: `_release_lease_lock` seeks to `_WINDOWS_LEASE_LOCK_OFFSET` before unlocking; the same-process test fails if it does not. |
| N2 | Crash test could flake because Windows releases locks asynchronously. | Implemented after evidence. In the full Windows suite under parallel load the crash test failed: re-acquisition right after `holder.wait()` raised EACCES. The test now polls recovery for up to 10 seconds and still fails if the lock is never released (test-only change; 15 of 15 isolated runs passed). Runtime behavior difference recorded for the owner: on Windows, a retry immediately after a crashed holder can be refused as a live duplicate for a short time; POSIX releases synchronously. Production retry handling is unchanged in this batch. |
| N3 | Real-path check does not name its environment. | The check runs in a disposable virtual environment, not the host's working Runtime install. |
| N4 | No CI exists to run the POSIX suite. | POSIX verification is an owner gate before merge to `main`; not part of this batch's acceptance. |
| N5 | Claude path has further Windows gaps (`/tmp` temporary directory, macOS sandbox paths). | Recorded as a follow-up alongside the Codex auth symlink. A Claude real-path result is read with this in mind. |
