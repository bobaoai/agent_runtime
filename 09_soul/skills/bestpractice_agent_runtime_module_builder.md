# Agent Runtime Module Builder

## Metadata

- **Type**: BestPractice
- **Applies to**: Runtime Module and Workflow registration; Skill Package
  export binding; provider, durable-backend, and persistence adapters; conversion
  of direct model workers into auditable Runtime executions
- **Canonical system boundary**: `designDoc/the_agent_runtime.md`

## Result

Produce provider-neutral immutable releases whose identity, inputs, outputs,
authorization boundary, execution history, evaluation, and admission can be
inspected without relying on a provider conversation, terminal history, mutable
Skill file, or a `latest` lookup.

The target object chain is:

```text
Skill Package Release
  -> zero-to-many Module exports
    -> Runtime Module Releases
      -> zero-to-many Workflow Release bindings

Module Run
  -> Execution Variant
    -> Attempt
      -> immutable output refs
        -> Evaluation / Selection / Resolution
```

The Runtime Module Registry is the sole executable-unit authority. There is no
parallel workflow-position Registry: a workflow-local `node_id` only locates an
exact Module Release inside one graph.

## Ownership Boundary

| Fact | Owner |
| --- | --- |
| Business role, edge meaning, content rubric, revision, terminal outcome | Domain contract and typed graph |
| Skill Package content and export declaration | Skill Governance |
| Module and Workflow release, execution lineage, Context, Evaluation mechanics, telemetry | Agent Runtime |
| Principal, Entitlement, policy, delegation, decision, grant | Product Authorization |
| Model invocation and native continuation | Agent Execution Adapter |
| Timers, signals, cursor, worker coordination | Durable Backend Adapter |
| Physical placement, retention, residency | Data Governance and persistence binding |
| Canonical domain mutation | Domain service |
| Build, admission, deployment, rollback | Software Delivery |

Runtime core treats domain identifiers and artifact meaning as opaque. It
validates identity, exact hashes, closure, compatibility, admission, isolation,
and execution mechanics.

## Release Registration

A product-facing Skill Package may export zero, one, or many independently
executable Modules. Each export selects only its closed instruction-member set.
An Agent Module Release binds exactly one Skill Package release and export plus
its Prompt Bundle, schemas, operations, Context, Evaluation, retry, compatible
transport, entry, and output-resolution policies.

A Workflow Release references exact Module Release refs and hashes. It never
points to a mutable Skill path or copies Module purpose, Prompt, schemas, and
permissions into a node.

Registration is explicit and dependency-closed. Host composition installs a
`RuntimeModulePlugin`; discovery is not execution authority. Release and
admission rows are append-only. Active-pointer replacement and predecessor
supersession occur atomically.

## Prompt and Input Assembly

Compile tenant-free static instructions into an immutable Prompt Bundle
Release. Bind authorized dynamic input, optional revision packet, output
contract, tool policy, and final translated provider request in a Cell-local
Prompt Envelope.

Keep goal, allowed evidence, required judgment, uncertainty behavior, and
output schema in the task plane. Keep routing, authorization mechanics,
provider selection, and package-construction explanation in the control plane.
Record refs and hashes for the full input closure and Prompt artifacts. Bare
paths are audit metadata, not task content.

## Execution Identity

A Module Run freezes one logical execution of one exact Module Release against
one immutable input and authorization closure. A behavior-changing profile,
provider/model adapter revision, Prompt Envelope, tool policy, or Context policy
creates a sibling Execution Variant. A retry creates a new Attempt under the
same Variant.

Multiple eligible Variants require the declared Evaluation coverage and
immutable Selection before Module output Resolution. Downstream execution
consumes only resolved output refs. Resolution does not establish domain
quality, Artifact readiness, publication authority, or canonical admission.

Provider-native continuation may be reused only inside one compatible Variant
and isolation scope. Cross-provider or incompatible work reconstructs from
immutable input refs, resolved prior output, continuity state, and a typed task
or revision packet. Sibling Variants never share mutable provider state.

## Runtime-generated Audit

Code records Module Run, Variant, Attempt, output, Evaluation, Selection,
Resolution, Context, operation, timing, failure, and usage lineage. Agents do
not author or repair those records.

Before a protected invocation, bind exact externally issued authorization
evidence and persist the Attempt start. Commit invocation results idempotently.
Replay returns committed results instead of repeating provider calls or side
effects. Preserve `unknown` when trustworthy token or cost data is unavailable.

Shared observability carries only permitted identities, refs, hashes, timings,
usage, and bounded failure classes. Content-bearing inputs, Prompt Envelopes,
outputs, credentials, and authorization-filtered results stay in governed
Cell-local stores.

## Adapter Rules

An Agent Execution Adapter translates one frozen Variant request and returns a
normalized immutable result. It does not choose graph edges, reinterpret
business verdicts, mutate domain state, or grant authority.

A Durable Backend Adapter coordinates scheduling and durable continuity using
content-free state. A Persistence Adapter enforces immutable rows, exact-hash
loads, atomic promotion, isolation, and recovery semantics. Runtime core
imports neither domain plugins nor optional database/provider clients.

## Verification

Use opaque synthetic fixtures for shared Runtime tests. Verify:

- package-export-Module and Workflow-Module closure;
- exact hashes, duplicate rejection, and atomic registration;
- independent direct Module tests and A/B Variants;
- Attempt idempotency and crash-window recovery;
- output Evaluation, Selection, and Resolution;
- Prompt Bundle and Cell-local Prompt Envelope separation;
- authorization, operation, tenant, and Cell negative paths;
- usage completeness and explicit unknowns;
- release promotion, rollback, and pinned execution;
- clean optional-adapter packaging and no domain imports;
- generated inspection equal to code truth.

A shadow/test slice must say so explicitly. It cannot be called production
ready until target authorization, provider adapters, durable lifecycle,
persistence, and required conformance gates are implemented and admitted.

## Silent Violation Signals

- Runtime core imports a Research, Digestion, Trade, or host package.
- A Workflow node owns a second Module-like registry record.
- Production execution resolves a mutable Skill file or `latest`.
- A provider call lacks Module Run, Variant, Attempt, output, and usage lineage.
- A sibling A/B Variant shares opaque Context.
- An Agent writes its own audit, search, token, authorization, or billing log.
- A raw or losing output advances without Resolution.
- An authorization grant is treated as content approval.
- A Runtime output is presented as canonical domain state.
- Active immutable release bytes change in place.

## Acceptance

The declared slice is ready only when its release contracts, registration,
dependency closure, persistence behavior, execution lineage, authorization and
data boundary, adapter bindings, tests, and generated inspection agree.
Missing truth stays missing; it is never reconstructed from comments, terminal
history, or provider state.
