# Agent Runtime T2 External Authority, Execution, and Event Ingress Review Brief

## Decision requested

Review these three candidates as one write-side coordination slice. Decide
whether document 10 is the single Runtime execution composition root, document
09 is limited to execution-time external-authority observations and fence
meaning, and document 03 owns external-event identity and legal-wait validation
without creating another execution engine, Ledger, durable coordinator, or
authorization service.

This review does not admit canonical Design Docs, authorize implementation,
approve current code, or waive the declared upstream and filename-owner
blockers.

## Candidate files

1. `agent_runtime_09_external_authority_integration_contract.md`
2. `agent_runtime_10_execution_contract.md`
3. `agent_runtime_03_external_event_ingress_contract.md`

They are reviewed together because authority fence meaning, finalization
timing, cancellation, external-event application, and late-result quarantine
must form one closed interaction while retaining separate owners.

## Material decisions

### External Authority 09

- Runtime consumes bounded provider-neutral observations and never becomes an
  identity, Entitlement, policy, credential, database-access, or central PEP
  service.
- Each protected-resource owner publishes and enforces its own
  `ResourcePermissionManifest`.
- User authority and registered Module permission are both required at the
  owning enforcement point; Data Governance owns resource-local preconditions.
- Scope expansion never widens a running execution; reduction, expiry,
  revocation, mismatch, or unprovable cross-clock evidence closes its fence.

### Execution 10

- One `RuntimeExecutionService` owns start, status, cancellation, external
  event, reconciliation, and recovery ordering.
- Workflow and independently executable Module origins use the same Runtime
  identity, authority, Ledger, Invocation, and Durability law.
- Execution owns Attempt policy, Evaluation, Selection, Resolution, and
  cross-responsibility reconciliation; it owns none of the implementing peers.
- The old Agency Platform execution-binding document is not reused as Runtime
  Execution authority.

### External Event Ingress 03

- Authorization and exact legal-wait matching are independent predicates.
- Ingress owns event identity and validation; Execution owns application and
  state advancement; Ledger owns commits; Durability owns acknowledgement.
- Human roles, UI, approval meaning, and domain content are absent from the
  Runtime protocol.

## Reviewer questions

1. Does document 09 avoid centralizing every Runtime resource permission while
   still giving Execution enough typed authority and data-scope evidence?
2. Are the monotonic fence and cross-clock semantics implementable without a
   cross-service transaction or bare wall-clock comparison?
3. Is document 10 one complete composition root rather than another host
   binding service or thin wrapper over predecessor coordinators?
4. Are retry, A/B Variants, review loops, Evaluation, Selection, Resolution,
   cancellation, and late results distinguished correctly?
5. Can every start, finalization, event, cancellation, Outcome, and durable
   acknowledgement crash window converge idempotently?
6. Does document 03 validate the event without deciding the Workflow's next
   state or calling a concrete backend?
7. Do the three contracts preserve the dependency direction fixed by Source
   Architecture and the public ports fixed by Registry, Ledger, Invocation, and
   Durability?
8. Is enough completion evidence stated to freeze a later Code Design Basis
   without inventing missing authority or execution semantics?

## Evidence status

- These are Design Intent candidates over an unfrozen implementation worktree.
- Revision 1 received an Opus 5 xhigh advisory `pass_with_fixes`. Revision 2
  applies all eight required fixes and five notes to the three contracts, the
  shared Ledger start boundary, this brief, and the manifests. The corrections
  are not represented as independently re-reviewed.
- The complete revision-1 whole-system review exposed common Runtime Execution,
  Variant Policy coverage, and Ingress inheritance gaps. Revision 3 aligns the
  Execution and Ingress contracts to the corrected Runtime root, Registry, and
  Invocation boundaries.
- The complete revision-2 whole-system review returned `pass_with_fixes`.
  Revision 4 names the host-provided Product Authorization decision validator
  for every Execution lifecycle action, makes Execution supply the
  request-bound `InvocationHost`, and fixes dynamic protected-operation intent
  ordering before resource entry.
- The Runtime root is revision 12, Source Architecture and Registry are
  revision 12, and Ledger, Invocation, and Durability are revision 6. All await
  the same whole-system revision-3 review rather than separate acceptance.
- Current-code observations come from
  `review_artifacts/agent_runtime_code_architecture_audit.md`; they are evidence,
  not stable prose truth.
- The formal Runtime review host remains broken, so this review is advisory.

## Next dependency slice after review

After accepted corrections, author Inspection 06 and Standalone Release
Conformance 05. Then review the complete Runtime root and all ten T2 candidates
as one admission subject before any canonical replacement or Code Design Basis.
