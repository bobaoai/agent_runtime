# Windows host platform program

This folder tracks how Agent Runtime gains a Windows host implementation. It records the structure and batch order agreed with the Runtime owner, and the owner decisions that later batches depend on. Each batch has its own CodeDesignBasis and review records.

## Structure

Every operating-system difference sits behind one host platform interface with two implementations: one for POSIX hosts (macOS and Linux) and one for Windows. The implementation is selected once, from `sys.platform`, when the selection module is imported. Callers use the interface and contain no platform branches. When a capability has no correct implementation on a host, that implementation refuses explicitly; it never degrades silently.

## Platform differences and batches

| Batch | Difference | Current POSIX mechanism | Windows gap |
| --- | --- | --- | --- |
| 1 | Attempt workspace lease | `fcntl.flock` | Implemented in commit 8704f37 inside one function with branches; batch 1 moves it behind the interface. |
| 2 | Private temporary directories and protected locations | `/tmp`; `/Users`, `/Volumes`, `/private/tmp`, `~/Library/Keychains` | Paths do not exist or protect nothing on Windows. |
| 3 | Codex credential mount | Symlink to `auth.json`, `readlink` to find the source | WinError 1314 without symlink privilege. |
| 4 | Process tree termination | `start_new_session` and `killpg`; ownership check on POSIX only | Child processes can outlive a timeout or cancellation. |
| 5 | Command and tool isolation | macOS `sandbox-exec`; resource preparation gated to Darwin | No equivalent OS sandbox. |

Batches 1, 2 and 4 are engineering changes. Batches 3 and 5 change meaning owned by Runtime Design 08 and require a Design revision through the design authoring and review process before their engineering plans.

## Owner decisions, 2026-11-05

- **Codex credential on Windows (batch 3):** copy `auth.json` into the invocation's private Provider state, and delete the copy when the invocation ends. A token refreshed during the invocation is not written back to the source file.
- **Isolation on Windows (batch 5):** tool-using Modules may run on Windows with the tool allowlist as the only restriction. The execution record must state that isolation is weaker than on hosts with an OS sandbox.

## Review execution

Until Agent Runtime can execute the registered `engineering_change_reviewer` on this Windows host, plan and implementation reviews run through `claude -p --model opus` with the same reviewer prompt, schemas and checklist. They are recorded as substitute reviews.
