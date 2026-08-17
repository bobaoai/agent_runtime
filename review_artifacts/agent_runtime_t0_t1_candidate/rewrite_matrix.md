# Agent Runtime T0 and T1 Rewrite Matrix

Candidate status: architecture review subject. This directory does not replace
the canonical Design Contract surface.

## 1. Frozen inputs

| Input | Role |
| --- | --- |
| `review_artifacts/agent_runtime_design_system_review.md` | Target authority map and required rewrite order |
| `review_artifacts/agent_runtime_code_architecture_audit.md` | Code evidence, correctness findings, target dependency graph, and production-lifecycle gaps |
| `designDoc/the_design_doc_management.md` | T0, T1, T2 naming, lifecycle, material-change, and review law |
| `CURRENT_HANDOFF.md` | Protected worktree lanes, package-parity blocker, and sequencing constraints |

The candidate is intentionally separate from `designDoc/` so the complete root
rewrite can be reviewed before canonical replacement. Design Doc Management
owns the authoring, freezing, and replacement method; this change introduces
no additional peer-decision or portfolio object.

## 2. Target authority split

| Candidate | Owns | Does not own |
| --- | --- | --- |
| `the_charter.md` | Project-specific standalone product constitution, adopted Runtime boundary, external authorities, and product completion conditions | Portable Runtime responsibility definitions, detailed mechanics, source layout, technology selection, current status |
| `the_agent_runtime.md` | Portable provider-neutral Runtime law, the six responsibility definitions, identity, immutability, isolation, authority, recovery, Resolution, and audit invariants; accepted at the portable governance source and mechanically projected | Project identity, current paths, T2 inventory, provider or backend status, database selection, delivery plan |
| `agent_runtime_00_runtime_domain_contract.md` | Single Runtime T1 outcome, public handoffs, shared identities, delegation map, and cross-responsibility ordering | Detailed Registry, Ledger, Invocation, Durability, Inspection, external-authority, or Execution mechanics |

## 3. Current Charter disposition

| Current section | Candidate disposition |
| --- | --- |
| Intent Capsule | Keep and tighten around product constitution |
| Product Result | Keep |
| Runtime Responsibility Boundary | Keep the six peers; state that foundation primitives are supporting code rather than a seventh responsibility |
| External Authorities | Keep and add content-lifecycle and authorized-query handoffs |
| Design Contract Hierarchy | Recast as this project's mapping under Design Doc Management; do not duplicate hierarchy or material-change law |
| Intent and Code Truth | Keep and distinguish Runtime wheel, build tooling, governance source, interface projections, and generated Design Contract projection |
| Correctness Priority | Keep |
| Product Completion Conditions | Keep; add schema compatibility, bounded authorized inspection, content disposition, and frozen release evidence |

## 4. Current Runtime T0 disposition

| Current material | Target owner |
| --- | --- |
| Stable product outcome and external authority separation | Retain in Runtime T0 |
| Canonical filenames, source-directory registration, import rules | T2 document 02, Source Architecture |
| Skill-path discovery and authoring repository import | External build and authoring tooling |
| Agentic Workflow Conformance Package assembly | T2 documents 01, 05, 08, and 10 |
| Workflow Execution, Module Run, Variant, Attempt, Evaluation, Selection, and Resolution mechanics | T2 document 10, with facts in document 04 |
| Prompt projection, Context, workspace, tool, filesystem, network, and provider behavior | T2 document 08 |
| Authorization observations, operation intent, fence, invalidation, and governed-data handoff | T2 document 09 |
| Checkpoint, acknowledgement, replay, and backend recovery | T2 documents 04, 07, and 10 |
| Testing and standalone distribution detail | T2 document 05 |
| Current provider, PostgreSQL, Temporal, package, and implementation status | Code-owned inspection and Software Delivery evidence |
| Manually maintained T2 inventory | T1 delegation map and code-owned Design Contract registration |

## 5. Current document 00 disposition

| Current material | Candidate disposition |
| --- | --- |
| Runtime outcome and domain-neutral entries | Retain at T1 root |
| Detailed execution identity and lifecycle | Move to T2 document 10; retain only shared identity law |
| Nine-subsystem index | Replace with the six Charter responsibilities and their target T2 owners |
| Workflow driver, bridge, dispatch, and state-machine mechanics | Move to T2 documents 07 and 10 |
| Authorization binding, operation grants, and invalidation algorithm | Move to T2 document 09 |
| Context lifecycle and provider compatibility tuple | Move to T2 document 08 |
| Record families, telemetry, checkpoints, and transaction ordering | Move to T2 document 04, with coordination ordering in 10 |
| Evaluation and Resolution mechanics | Move to T2 document 10; facts remain in 04 |
| Test harness and release conformance | Move to T2 document 05 |
| Module release and Registry update mechanics | Move to T2 documents 01 and 05 |
| Detailed failure table | Move typed provider failures to 08, durable failures to 07, and execution state transitions to 10 |
| Current source paths and commands | Remove from Design Intent; emit through code-owned inspection and evidence packages |

## 6. Cross-document invariants retained at the root

1. One release version has one canonical payload and one identity domain.
2. One canonical Ledger owns execution facts; a provider result or backend
   history is not a parallel authority.
3. Runtime consumes external authorization and governed-data decisions without
   issuing or widening them.
4. Execution compares the current durable fence in the same transaction that
   finalizes authority-sensitive work, using the registered protected predicate,
   distributed-clock profile, and clock-health evidence when clock domains differ.
5. A committed provider invocation or protected effect is never repeated during
   recovery.
6. Immutable lineage is distinct from governed content-body custody and
   disposition.
7. Inspection receives an authorized query scope before retrieval and exposes
   bounded, paged detail.
8. Runtime startup refuses an unsupported or incomplete persistent-schema
   release.
9. Runtime core imports neither a host product, domain package, authoring or
   Skill tree, nor a business-database implementation.
10. Public-surface removal requires symbol-level downstream consumer closure;
    it does not require a permanent compatibility facade.

## 7. Existing-content disposition

| Current Runtime-numbered content | Disposition before replacement |
| --- | --- |
| Document 01 Module contract and assembly | Preserve immutable release families, dependency closure, Module/Workflow assembly, and independent Module ownership in Registry 01; move provider projection to Invocation 08, execution facts to Ledger 04 and Execution 10, authorization to 09, and repository discovery to external authoring tooling |
| Document 02 Agency Platform topology | Preserve in a host-owned `trading_platform` Agency Platform candidate, then replace Runtime 02 with Source Architecture |
| Document 03 authorized external-event ingress | Preserve separation of authorization, ingress receipt, application, acknowledgement, idempotency, and crash windows in Event Ingress 03; replace host-specific authority refs with the 09 port and leave durable submission and state advancement to Execution 10 |
| Document 04 publication transaction | Preserve reusable protected-effect rules in Runtime 09 and 10; move domain publication semantics to the owning host or domain contract, then replace Runtime 04 with Ledger |
| Document 05 delivery roadmap | Move stable build and release rules to Software Delivery, move current status to code-owned inspection, retire the roadmap, then replace Runtime 05 with Release Conformance |
| Document 06 standalone package and lifecycle | Split source architecture to 02, distribution closure to 05, authorized read projections to Inspection 06, execution lifecycle to 10, and append-only facts and persistence to Ledger 04 |
| Document 07 Temporal durable adapter | Preserve the provider-neutral durable port, ref-only state, acknowledgement, replay, and recovery contract in Durability 07; move the current backend binding and status to code-owned inspection |
| Document 08 Agent execution adapter | Preserve Context, capability, workspace, tool, network, schema projection, normalization, and provider-neutral Invocation in 08; move execution finalization to 10 and 04 and authority semantics to 09 |
| Document 09 authorization integration | Preserve external authorization-context consumption, operation intents, enforcing-Gateway handoff, fencing, invalidation, and late-result law in External Authority 09; remove host persistence and concrete-controller authority |
| Document 10 host Workflow binding | Preserve exact Runtime intake validation in Runtime 10; move product routing, Dagster, deployment scope, and host binding to `trading_platform` Agency Platform before replacing the remaining content with Runtime Execution |

No current document is deleted before the listed semantics have an explicit
destination and the resulting diff is reviewed.

## 8. Review and adoption gates

This candidate may advance as the target architecture for T2 authoring only
after:

1. independent architecture review of the three-file subject;
2. Project Owner acceptance of the exact reviewed candidate;
3. the existing-content disposition in section 7 is preserved while each T2
   candidate is authored under `review_artifacts/`;
4. the complete root-plus-ten-T2 subject receives independent review; and
5. the Agency Platform and Software Delivery owners release the Runtime-prefixed
   02, 10, and 05 filenames through reviewed re-home or retirement changes; and
6. the portable Runtime T0 source and manifest are updated before projection,
   then the old canonical root and all ten T2 contracts are replaced atomically,
   followed by mechanical Design Contract bundle regeneration.

The current canonical root and T2 set remain active throughout candidate
authoring. Registry must never expose two active document-00 roots or a root
whose specialization closure is unresolved.

No Runtime implementation, test, generated Design Contract package, or
downstream consumer import is changed by this candidate.
