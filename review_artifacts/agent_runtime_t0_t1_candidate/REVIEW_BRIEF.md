# Agent Runtime T0 and T1 Candidate Review Brief

## Decision requested

Review the three-file candidate as one authority subject and return one of:

- approve the authority split for canonical replacement;
- revise with exact finding, owner, and required correction; or
- reject with the conflicting T0 authority or product boundary.

Approval of this review subject does not change canonical Design Docs and does
not authorize Runtime implementation.

## Subject files

1. `the_charter.md`
2. `the_agent_runtime.md`
3. `agent_runtime_00_runtime_domain_contract.md`

`rewrite_matrix.md` is authoring rationale and is not Design Intent. The
machine-readable file hashes are in `subject_manifest.json`.

## Material decisions in the candidate

1. Charter, Runtime T0, and Runtime T1 have distinct owned objects: the
   project-specific product constitution, portable Runtime law, and the Runtime
   domain delegation root. A conflict returns to the semantic owner instead of
   being resolved by document precedence.
2. Runtime T0 defines exactly six peer responsibilities. The Charter adopts
   that definition without creating a second authority. Cross-cutting source
   architecture and release conformance are T2 contracts, not extra Runtime
   responsibilities.
3. Document 00 becomes the root delegation and cross-responsibility contract;
   detailed Execution mechanics move to document 10.
4. Documents 01 through 10 receive one target owner each. Current externally
   owned Agency Platform and Software Delivery material must move before those
   Runtime numbers are reused.
5. One canonical Registry and one canonical Ledger own release and execution
   authority.
6. Execution is the composition root. Invocation, Durability, Ledger, Registry,
   Inspection, and external-authority ports remain narrow peers or dependencies.
7. Persistent-schema lifecycle, authorized query scope, governed content-body
   disposition, bounded Trace retrieval, and downstream API consumer closure
   are release requirements rather than deferred operational notes.
8. T2 09 owns execution-time external-authority observations and fences. Each
   Runtime responsibility that owns a protected resource remains the Policy
   Enforcement Point for its own operations and publishes its own permission
   manifest.

## Reviewer questions

1. Does any candidate paragraph let Charter, T0, or T1 claim the same semantic
   decision at two layers?
2. Is every target T2 responsibility cohesive, and is any required owner
   missing?
3. Does the dependency graph permit the required composition without a reverse
   peer edge or central contracts facade?
4. Are external authorization, governed-data, content-custody, Artifact, host,
   and delivery authorities kept external without leaving Runtime unable to
   enforce its own execution boundary?
5. Are commit, recovery, reconciliation, and late-result laws strong enough
   without claiming unavailable cross-system atomicity?
6. Are schema migration, bounded Inspection, content disposition, and consumer
   closure stated at the correct layer without prescribing one technology?
7. Is the candidate sufficiently stable to replace the current T0 and T1
   canonical intent without forcing a T2 implementer to redesign the root?

## Review evidence status

The formal Design Review host currently imports the retired Runtime
`registry_module_exporting` interface while the Runtime exposes
`registry_module_loading`. Until that integration is repaired against the
approved Runtime design, Opus review evidence is advisory and cannot be
represented as a Runtime-recorded admission result.

The complete revision-2 whole-system advisory returned `pass_with_fixes`.
Revision 12 qualifies the Design Doc contract registry explicitly so it cannot
be confused with the Runtime Registry responsibility. Portable T0 acceptance
still follows governance-source-first projection order under the governance
distribution owner.

## Author self-review

- All three Design Intent candidates have the required frontmatter, purpose,
  reader gain, Contract Capsule, authority boundary, machine contract, and
  review boundary.
- Prose lint reports zero hard violations. Its remaining warnings are the
  mechanical first occurrences of T0, T1, and T2 labels.
- Markdown link resolution passes for every explicit link.
- Whitespace validation passes.
- Candidate prose contains no current provider, database, durable-backend,
  implementation-status, delivery-status, or test-result inventory.
- Canonical Design Docs, Runtime code, tests, generated package files, and
  downstream imports were not changed for this candidate.

## Adoption sequence after approval

1. Complete independent semantic review over the exact root subject hashes.
2. Record governance distribution owner acceptance of the portable Runtime T0
   target and Project Owner acceptance of the Charter and T1 target as the basis
   for T2 candidate authoring; neither decision replaces canonical intent.
3. Rewrite T2 candidates under `review_artifacts/` in dependency order, while
   the current canonical root and T2 set remain active: Source Architecture 02;
   Registry 01; Ledger 04; Invocation 08; Durability 07; External Authority 09;
   Execution 10; External Event 03; Inspection 06; Release Conformance 05.
4. Review the complete root-plus-T2 subject and preserve every disposition in
   `rewrite_matrix.md`.
5. Re-home the Agency Platform documents currently occupying Runtime numbers 02
   and 10 and retire or re-home the Software Delivery roadmap occupying number
   05, with their owning T0 references updated before Runtime reuses the names.
6. Apply the accepted portable Runtime T0 to
   `09_soul/governance/t0/the_agent_runtime.md`, update its manifest hash, and
   mechanically project it to `designDoc/the_agent_runtime.md`; never hand-edit
   only the installed target.
7. Atomically replace the old canonical Runtime root and all ten Runtime T2
   contracts. Never expose two active document-00 roots or an unresolved
   specialization closure.
8. Freeze the resulting Code Design Basis and reassign code ownership without
   behavior changes.
9. Regenerate the Design Contract package mechanically.
10. Run deterministic architecture, package, contract, downstream consumer,
   and full repository validation before implementation restructuring. Rewrite
   candidate-only `../../designDoc/` links to canonical sibling links during
   projection and validate links again from the canonical target locations.
