# Agent Runtime T2 Ledger, Invocation, and Durability Review Brief

## Decision requested

Review the three T2 candidates as the second dependency slice under the Runtime
root and the corrected Source Architecture and Registry candidates. Decide
whether each contract has one cohesive responsibility, preserves the target
dependency direction, and is complete enough for External Authority and
Execution to consume without inventing parallel records, retries, or provider
state.

This review does not admit canonical Design Docs, authorize implementation,
select a provider or durable backend, or waive the declared filename-owner and
formal-review-host blockers.

## Candidate files

1. `agent_runtime_04_execution_ledger_contract.md`
2. `agent_runtime_08_invocation_contract.md`
3. `agent_runtime_07_durability_contract.md`

Ledger is first because Invocation and Durability return observations and
acknowledgements that Execution later commits through Ledger. Invocation and
Durability remain peers and do not import one another.

## Material decisions

### Ledger 04

- One canonical Ledger replaces overlapping execution-fact families and dual
  writes.
- Execution supplies decisions; Invocation, Durability, Gateways, and External
  Authority supply observations; Ledger validates and commits facts.
- Stable identity excludes timestamps, worker identity, random workspace paths,
  and retry-local values.
- Start, Attempt claim, protected-operation intent, finalization, and backend
  acknowledgement have explicit atomic batch semantics.
- Fence comparison occurs inside terminal finalization without claiming a
  transaction across Product Authorization and Ledger.
- Content lineage remains immutable while body custody follows Data Governance.

### Invocation 08

- One provider-neutral formatter defines the complete Runtime-authored model
  Context; Adapters return provider-native projections as bounded observations,
  Ledger records both surfaces, and Inspection projects committed facts.
- Required semantic Context remains present when Gateway access is enabled.
  Gateway supplies authorized optional or exploratory content.
- Execution Profiles, rather than prompt text, pin model, provider, tools,
  network, shell, workspace, environment, output, timeout, and context behavior.
- Tool-free, own-draft, Gateway, managed attachment, and authorized repository
  execution are declared capability classes. Admission depends on enforceable
  Adapter conformance.
- Native structured output and prompt-only JSON remain explicit modes with no
  silent fallback.
- Provider sessions and workspaces are recoverable optimizations, never
  canonical task state.

### Durability 07

- Durability receives frozen idempotent commands and performs no Registry lookup.
- Backend history contains bounded control state; Ledger retains canonical
  execution history.
- Backend retries and delivery counts never mint Runtime Attempt identity.
- Start, event, cancellation, Outcome, timer, and checkpoint commands use one
  intent-to-acknowledgement recovery pattern.
- Host topology and backend selection remain outside Durability types.
- Temporal is one replaceable Adapter with real replay, rollover, and recovery
  conformance requirements.

## Reviewer questions

1. Does Ledger own records and atomicity without absorbing Execution decisions,
   Product Authorization policy, or Data Governance custody?
2. Can one persistent store implement every required batch and query boundary
   without inventing a second record authority?
3. Does Invocation describe the exact Context seen by the model while keeping
   Runtime-private metadata, credentials, and record construction outside it?
4. Are capability profiles flexible enough for real Agent work, including an
   authorized repository and shell, while every enforcement claim is testable?
5. Can provider/model A/B reuse one Module semantic release without hiding
   task-shaping differences?
6. Does Durability preserve recoverable progress without importing Registry,
   host topology, Invocation, or complete Ledger history?
7. Do crash windows converge through idempotency and reconciliation rather than
   a false cross-system atomicity claim?
8. Can the later Execution T2 consume all three ports without adding a shadow
   retry loop, result Ledger, durable cursor family, or direct provider path?

## Evidence status

- These are Design Intent candidates over an unfrozen implementation worktree.
- Revision 1 was an internal draft freeze only. It received no independent
  verdict and produced no actionable review findings.
- Revision 2 received an Opus 5 xhigh advisory `pass_with_fixes`. Revision 3
  applies all eight required fixes and both notes to the candidate contracts,
  this brief, and the subject manifest; it does not represent those corrections
  as independently re-reviewed.
- The downstream External Authority, Execution, and Event Ingress revision-1
  advisory exposed one shared start-contract gap. Revision 4 makes the Ledger
  start batch distinguish a Workflow-origin execution from an independent
  Module origin without inventing a Workflow identity.
- The complete revision-1 whole-system review exposed that the common
  Runtime Execution identity and Invocation's independent Module origin were
  incompletely propagated. Revision 5 aligns Ledger and Invocation to the
  common root and the host-provided authorization-validator boundary.
- The complete revision-2 whole-system review returned `pass_with_fixes`.
  Revision 6 commits the independent Module root Module Run in the start batch,
  makes Execution the provider of Invocation's request-bound host bridge for
  pre-effect intent commits, removes named backend mechanics from stable
  Durability intent, and normalizes the conformance enumerations.
- Source Architecture and Registry revision 12 are the current upstream
  candidate and bind the matching identity, policy, and dependency rules.
- Current code evidence comes from
  `review_artifacts/agent_runtime_code_architecture_audit.md`; its counts and
  current-file observations are evidence inputs, not stable prose truth.
- The formal Runtime review host still imports a retired Registry interface, so
  a direct Opus result remains advisory rather than Runtime-recorded admission.
- No implementation, persistent schema, generated Design Contract bundle,
  public import, or downstream consumer is changed by this slice.

## Next dependency slice after review

After accepted corrections, author External Authority 09, Execution 10, and
Event Ingress 03 together so the fence, finalization, event, cancellation, and
reconciliation boundaries close as one system. Inspection 06 and Release
Conformance 05 follow the completed write-side contract set.
