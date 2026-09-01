# Slice 2B Revision 2 Independent Review Findings

Reviewer: Claude Opus 5, xhigh.

Subject: frozen 33-file pre-commit candidate based on `1152cb4`.

Result: `non_pass`; Software Delivery readiness: `changes_required`.

## Closed prior findings

- revision 11 Design Basis and Work Package closure;
- wrong release-hash and Schema Asset hash rejection;
- immutable ref, version, admission, and transition conflicts;
- Registry-level idempotent replay and populated-registry atomic rollback;
- exact Bundle family-slot enforcement;
- four modified package facades included in reproduction closure;
- candidate admission across all ten release families;
- Workflow-origin and standalone-Module-origin Variant success paths.

## Current findings

1. `block`: `registry/__init__.py` prematurely exported ten Slice 2C migration
   surfaces. Slice 2B must expose only its own public Registry API.
2. `fix`: Execution Profile Context/retry absence lacked exact field-set tests.
3. `fix`: predecessor `WorkflowExecutionProfileSelection` and
   `WorkflowNodeExecutionProfileBinding` retirement had conflicting 2B versus
   2E ownership and no public-surface absence test; README prose remained stale.
4. `note`: empty Variant bindings were tested only at compiler entry, not at
   direct Registry registration.
5. `note`: Admission activation, replacement, supersession, retirement, and
   active-pointer behavior had no assigned implementation-Slice gate.
6. `note`: whitespace validation covered only tracked paths plus one untracked
   test.
7. `note`: revision-8 prose remained in the revision-11 basis; policy-family
   ref-prefix separation lacked a direct test.

## Revision 13 disposition

- Slice 2C facade exports stay outside the pre-persistence release cut.
- Profile field-set, family-prefix, Variant direct-registration, admission
  lifecycle, and predecessor-retirement tests are now part of Slice 2B.
- the profile-selection types retire in Slice 2B because Execution Variant
  Policy is their single Registry replacement;
- host authoring and product cutover remain Slice 2E;
- isolated full-suite evidence additionally proves that Slice 2D must join the
  dependency-closed release cut before the public Profile record can merge.
