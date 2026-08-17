# Agent Runtime T2 Source Architecture and Registry Review Brief

## Decision requested

Review the two T2 candidates as the first dependency slice under the accepted
Runtime root target. Return findings per candidate and decide whether each is
stable enough to join the later root-plus-ten-T2 admission subject.

This review does not replace canonical Design Docs, admit a Runtime release,
authorize implementation, or waive the peer-owner re-home prerequisites for
the current Runtime-prefixed 02, 05, and 10 documents.

## Candidate files

1. `agent_runtime_02_source_architecture_contract.md`
2. `agent_runtime_01_registry_contract.md`

Source Architecture is reviewed first because it defines the dependency and
public-surface rules Registry must obey. Registry is reviewed in the same slice
so the reviewer can test whether those rules are implementable rather than
merely decorative.

## Material decisions

### Source Architecture 02

- Logical responsibility, physical source placement, public exposure,
  dependency direction, repository plane, and current technology binding remain
  separate dimensions.
- Four code-owned manifests make ownership, import edges, public symbols, and
  migration debt deterministic. A derived cohesion report makes code-cohesion
  signals reproducible without turning numeric thresholds into architecture
  verdicts.
- The supporting foundation owns only pure shared primitives and one canonical
  schema traversal semantics.
- Runtime product imports no host, domain, authoring, Skill, business-database,
  build-tool, or optional-adapter implementation.
- Migration debt is an exact registered set with dispositions and exit evidence,
  not a resettable count.

### Registry 01

- A structured candidate bundle is transport only; it has no independent
  version, admission, owner, or lifecycle.
- One Skill source may export multiple independently owned and independently
  versioned Runtime Modules.
- Module semantic identity is separate from Execution Profile and Adapter
  identity, so provider/model A/B can reuse one Module Release.
- One release identity has one canonical payload and content hash domain.
- Admission, activation, deactivation, withdrawal, host selection, Product
  Authorization, and Software Delivery admission remain separate decisions.
- Registry publishes its lifecycle `ResourcePermissionManifest`, refuses
  undeclared actions and resources, and consumes separately registered Data
  Governance forward and rollback transitions for persistent-schema changes.
- Registry never discovers an authoring repository and never records execution
  state.

## Reviewer questions

1. Does either candidate create a seventh Runtime responsibility, central
   contracts facade, or duplicated architecture authority?
2. Do the Source Architecture machine contracts and derived cohesion report
   enforce every stable rule without becoming a mutable file inventory in
   prose?
3. Is the foundation narrow enough to avoid absorbing responsibility policy,
   while still preventing duplicated schema traversal and identity semantics?
4. Can one Skill source export several Modules without bundle-level lifecycle
   coupling or sibling release churn?
5. Is every behavior-bearing Module dependency content-bound while provider
   selection remains outside Module semantic identity?
6. Are Registry admission and lifecycle decisions complete and non-overlapping?
7. Can the next Ledger, Invocation, Durability, and Execution T2 candidates
   consume these contracts without inventing a missing public seam?

## Evidence status

- The Runtime root revision 12 remains a candidate; current canonical Runtime
  contracts remain active. Carried notes f22 and f25 are closed in that root's
  sections 2, 12, and 13 rather than in either T2 candidate.
- The first T2 advisory review blocked revision 1 on an incomplete source-owner
  class for T2 03 and T2 09 implementations, plus seven bounded inheritance,
  public-surface, projection, and persistence findings. Revision 2 binds both
  specialization implementations to Execution, labels repository planes,
  closes Product Authorization and Timestamp inheritance, and narrows each
  conformance owner without creating another Runtime responsibility.
- Revision 2 received `pass_with_fixes`. Revision 3 makes Registry writer the
  Policy Enforcement Point for its lifecycle resources, defines activation
  scope as `(release_family, stable_object_id)`, adds Data Governance
  inheritance, closes the missing Inspection-to-foundation edge, and carries
  the complete inherited-authority and predecessor-finding closure in the
  subject manifest.
- Revision 3 received `pass_with_fixes`. Revision 4 registers Registry's
  lifecycle permission manifest, binds persistent-schema changes to Data
  Governance forward and rollback transitions, makes Software Delivery's role
  explicit, uses one activation-scope term, and registers the cohesion signal
  report without giving it independent verdict authority.
- Revision 4 received `pass_with_fixes`. Revision 5 adds explicit conformance
  for atomic admission-unit persistence and independent sibling-closure
  admission, distinguishes Product Authorization scope forms from
  `RegistryActivationScope`, and updates the T1 root so T2 09 is explicitly
  limited to execution-time external-authority observations.
- Revision 5 received `pass_with_fixes`. Revision 6 makes Registry's
  conformance list an explicit minimum set backed by the complete section 7 to
  9 obligations, adds atomic lifecycle decision-ref evidence, closes the T1
  completion-evidence route for per-responsibility enforcement, and makes every
  cohesion signal reproducible from declared inputs.
- Revision 6 received `pass_with_fixes`. Revision 7 binds Registry lifecycle
  validity to Timestamp Semantics protected-predicate and distributed-clock
  evidence, applies the same inherited rule to every protected-resource owner
  in the then-current root, aligns repository-plane manifest scope, and repairs the
  frozen review-package lineage.
- Revision 7 received `pass_with_fixes`. Revision 8 closes the remaining T0
  release-law gap by requiring every withdrawal to append its reason and
  supporting evidence refs to Registry authority, and corrects the final
  carried-finding closure records.
- Revision 8 received `pass_with_fixes`. Revision 9 aligns the withdrawal
  public command and failure surface with the recorded reason/evidence law and
  mechanically verifies that Registry's conformance enumeration closes once.
- The downstream Inspection and Release Conformance review exposed the missing
  Registry read-side authorization seam. Revision 10 adds a storage-applicable
  `RegistryReadScope` distinct from `RegistryActivationScope`, with fail-closed
  query and conformance obligations before rows, counts, facets, or cursors
  leave storage.
- The complete revision-1 whole-system review exposed that Execution Variant
  Policy had no admitted Registry form and that Invocation could not import the
  Execution-owned authority port. Revision 11 registers the policy as an exact
  Behavior Policy form, assigns origin-coverage validation to Execution, and
  fixes Invocation on the host-provided authorization-validator pattern without
  adding a Runtime responsibility or reverse import edge.
- The complete revision-2 whole-system review returned `pass_with_fixes`.
  Revision 12 makes Execution Variant Policy one distinct Registry release
  family with its own activation identity, and normalizes the Source
  Architecture conformance enumeration without changing its obligations.
- The formal Runtime Design Review host still imports a retired Registry
  interface. Until that implementation is repaired, a direct Opus result is
  advisory evidence rather than a Runtime-recorded admission result.
- No Runtime implementation, test, generated Design Contract package, public
  import, persistent schema, or downstream consumer is changed by this slice.

## Next dependency slice after review

After accepted corrections, author Ledger 04, Invocation 08, and Durability 07
against the Source Architecture and Registry boundaries. External Authority 09,
Execution 10, and Event Ingress 03 wait for those foundation contracts rather
than inventing parallel records or ports.
