# Complete Agent Runtime Design Contract Review Brief

## Decision requested

Review the complete candidate Runtime contract as one system: portable T0,
Runtime T1 domain root, and all ten T2 specializations. Decide whether the set
defines one implementable, independently publishable Agent Runtime without
duplicate authority, hidden host or product ownership, unresolved interaction
ordering, or prose that a later Code Design Basis would have to reinterpret.

This review does not admit canonical Design Docs, authorize implementation,
release software, or waive declared migration and formal-review-host blockers.

## Complete candidate set

1. T0 `the_agent_runtime.md` and project Charter projection.
2. T1 `agent_runtime_00_runtime_domain_contract.md`.
3. T2 Source Architecture 02.
4. T2 Registry 01.
5. T2 Execution Ledger 04.
6. T2 Invocation 08.
7. T2 Durability 07.
8. T2 External Authority Integration 09.
9. T2 Execution 10.
10. T2 External Event Ingress 03.
11. T2 Inspection 06.
12. T2 Standalone Release Conformance 05.

T2 numbering is human navigation, not authority rank or execution order.

## Whole-system review questions

1. Is each logical responsibility owned exactly once, and are supporting T2s
   clearly distinguishable from the six peer Runtime responsibilities?
2. Does dependency direction remain acyclic and enforceable from source files,
   public exports, ports, and concrete Adapter placement?
3. Do release, top-level execution, Workflow, Module Run, Variant, Attempt,
   invocation, Evaluation, Resolution, Outcome, content, and durable identities
   have one owner and one immutable lineage?
4. Is Execution the only coordinator while Registry, Ledger, Invocation,
   Durability, Inspection, External Authority, and Event Ingress retain narrow
   implementable contracts?
5. Does each Runtime protected-resource owner act as its own Policy Enforcement
   Point and publish its own ResourcePermissionManifest without turning Runtime
   into Product Authorization or Data Governance?
6. Do start, dispatch, protected operation, finalization, wait, event,
   cancellation, invalidation, recovery, and acknowledgement orderings converge
   idempotently across every crash and race window?
7. Are Context assembly, provider-native projection, tools, Gateway, workspace,
   repository, shell, structured output, session continuity, usage, and failure
   observations complete and provider-neutral?
8. Are timestamp roles, store-assigned commit instants, distributed clock
   profiles, health evidence, monotonic durations, and protected comparisons
   assigned to their correct owners?
9. Is Inspection an authorized bounded projection only, with storage-level
   filtering and separate content permissions?
10. Does Standalone Release Conformance validate one frozen software subject
    without becoming Software Delivery, Contract Audit, or an operational
    roadmap?
11. Can an external host and domain plugin use Runtime without importing
    host-product, domain-plugin, business-database, or customer-content code or
    exposing host semantics inside Runtime?
12. Is the set minimal enough to implement and maintain, or does any object,
    port, release, policy, or evidence family exist only because the documents
    became over-engineered?

## Evidence status

- Every slice is a frozen candidate with exact manifest hashes and recorded
  Opus 5 xhigh advisory lineage.
- Whole-system revision 1 received an Opus 5 xhigh `block` with three upstream
  ownership or implementability defects, six local fixes, and three notes.
  Revision 2 closes all twelve findings in their existing owners: common
  Runtime Execution identity, legal Invocation authorization dependency,
  registered Execution Variant Policy, both execution origins, non-persistent
  Inspection export, uniform inheritance, and host-neutral conformance prose.
- These post-review corrections are candidate revisions and are not represented
  as admitted before this revision-2 re-review.
- Whole-system revision 2 returned `pass_with_fixes`: five local corrections
  and three notes, with no new responsibility or structural block. Revision 3
  gives Execution lifecycle authorization an explicit decision source, assigns
  the request-bound `InvocationHost` and dynamic intent ordering, fixes the
  Module-origin start batch, makes Execution Variant Policy a distinct release
  family, declares portable-source-first T0 adoption, and removes the remaining
  enumeration, registry-term, and backend-name ambiguities.
- Whole-system revision 3 returned `pass_with_fixes` with three local fixes and
  two communication notes, and no structural block. Revision 4 assigns the one
  dynamic-operation authority observation, makes the protected-operation
  intent transaction itself verify claim and fence, exposes pushed authority
  invalidation only through Execution's authorized service action, completes
  the root dependency diagram, and removes issuer wording from Variant Policy.
- Revision 4 is the current complete candidate. Revision-3 findings are closed
  in their existing owners; canonical admission and code implementation remain
  separate later actions.
- Existing canonical Runtime Design Docs and implementation remain active.
- Current-code observations are evidence inputs, not stable Design Intent.
- The formal Runtime review host is a declared blocker, so this whole-system
  external result remains advisory.

## Required disposition

Return `block` for any unresolved owner collision, impossible transaction or
recovery claim, missing public boundary, security leak, cross-clock safety gap,
or contradiction that would force implementation to invent semantics. Return
`pass_with_fixes` only for corrections local to an existing owner. Do not add a
new layer or process unless the present responsibility set genuinely cannot own
the result.
