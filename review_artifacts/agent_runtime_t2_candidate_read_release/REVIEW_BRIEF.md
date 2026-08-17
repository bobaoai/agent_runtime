# Agent Runtime T2 Inspection and Standalone Release Conformance Review Brief

## Decision requested

Review the final two Runtime T2 candidates. Decide whether Inspection 06 is a
strictly authorized, bounded, read-only projection over Registry and Ledger,
and whether Standalone Release Conformance 05 validates one frozen Runtime
software subject without becoming Software Delivery, Contract Audit, a delivery
roadmap, or a second Runtime authority.

This review does not admit canonical Design Docs, authorize implementation, or
claim the upstream Runtime candidate set has completed independent review.

## Candidate files

1. `agent_runtime_06_inspection_contract.md`
2. `agent_runtime_05_standalone_release_conformance_contract.md`

## Material decisions

### Inspection 06

- Registry and Ledger remain the only source authorities.
- Authorization resolves before query; scope filtering occurs inside the
  storage query boundary before rows, counts, facets, or cursors leave storage.
- Summary, metadata, content-body, diagnostic, and export permissions remain
  separate.
- Live HTML, JSON, CLI, host projection, and offline export consume one service
  result and create no second truth.

### Release Conformance 05

- One immutable subject binds code, complete Design Contract closure,
  dependencies, schemas, adapters, persistence, migrations, test profile, and
  evidence denominator.
- Conformance validates but does not release or deploy software.
- The base distribution remains free of provider, durable-backend, host,
  trading-platform, customer-content, local-path, and credential dependencies.
- Real declared persistent-store, durable-backend, provider, host, plugin, and
  Inspection fixtures support only the claims in their explicit denominator.

## Reviewer questions

1. Can Inspection prevent cross-scope disclosure before counts and rows leave
   storage while remaining independent of Product Authorization internals?
2. Are complete invocation Context, output, usage, failure, retry, evaluation,
   resolution, and acknowledgement lineage visible without making Inspection a
   record authority?
3. Are content unavailability, redaction, denial, and integrity failure
   distinct and implementable?
4. Does offline export remain an explicit projection rather than operational
   truth?
5. Does Release Conformance freeze every behavior-bearing release input and
   invalidate evidence on any subject change?
6. Are deterministic validation, independent semantic review, Runtime-specific
   integration evidence, and Software Delivery admission kept separate?
7. Can a clean external host install and use Runtime without trading-platform
   code, provider SDKs in the base install, or ambient credentials?
8. Is the evidence denominator strict enough to prevent skipped or optional
   tests from becoming a false full-release claim?

## Evidence status

- These are Design Intent candidates over an unfrozen implementation worktree.
- Revision 1 received an Opus 5 xhigh advisory `pass_with_fixes`. Revision 2
  applies all eight required fixes and five notes to Inspection, Release
  Conformance, the upstream Registry read boundary, this brief, and manifests;
  those corrections are not represented as independently re-reviewed.
- The complete revision-1 whole-system review exposed the missing independent
  Module lineage view, one host-specific portability rule, and an incorrectly
  persistent export interpretation. Revision 3 renders both origins from the
  common Runtime Execution root, keeps Context strictly Ledger-committed, makes
  export a non-persistent derived response, and generalizes conformance rules.
- The complete revision-2 whole-system review returned `pass_with_fixes` and
  found no new defect in Inspection or Standalone Release Conformance.
  Revision 4 refreshes only the exact upstream candidate lineage for the
  complete revision-3 subject; the two owned contracts are byte-identical to
  revision 3.
- The root and prior eight T2 documents remain candidates at their revised
  whole-system correction levels.
- Current-code observations come from
  `review_artifacts/agent_runtime_code_architecture_audit.md`.
- The formal Runtime review host remains broken, so a direct Opus result is
  advisory.

## Next step after review

After accepted corrections, freeze the complete T0/T1 plus ten-T2 candidate as
one subject, run a whole-system design review, and only then write the Runtime
Code Design Basis and implementation migration plan.
