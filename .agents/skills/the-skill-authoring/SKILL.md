---
name: the-skill-authoring
description: Authors or revises one exact Skill candidate under Skill Management. Use only when an active Skill Work Package changes Skill instructions, registration, projection, classification, migration state, or retirement; exit upstream when the governed subject is not the Skill itself.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: skill_work_package
  first_authority_ref: designDoc/the_skill_management.md
---

# Skill Authoring

## Identity

This is the portable Primary Agent authoring method for one repository Skill.
Its first governing authority is the peer T0
`designDoc/the_skill_management.md`. Design Doc Management governs the
lifecycle of the owning Design Contract; it does not own the Skill artifact or
its lifecycle.

Its Primary Agent role is `authoring`. It may orchestrate a separately bound
independent reviewer, but it never supplies that review identity or verdict.

This Skill owns Skill authoring and migration work. It does not own the product
or domain workflow, Product Authorization, deterministic workflow execution, Agent Runtime
execution, Claude SDK/Skill adapters, other provider adapters, software release
admission, or a canonical business write.

Use it when:

- creating a new repository Skill;
- materially changing a Skill's purpose, input, output, workflow, policy, or boundary;
- classifying a Skill as a Primary Agent tool or product workflow projection;
- advancing `developing`, `migration_planned`, `managed`, or `direct_entry_retired` state;
- repairing drift between an owning Design Contract, code registration, and `SKILL.md`;
- preparing one Skill for deterministic or Independent Review.

Cosmetic edits may bypass this workflow when identity, behavior, class,
migration state, and execution routing remain unchanged. They still bind the
lightweight `SystemChangeCase`, `SystemChangePlan`, and Skill Work Package
required by the owning T0.

Registry data updates also bypass this workflow. Updating an Expertise Profile,
Release, Lens, query profile, or active pointer does not become a Skill change
because a Skill or Runtime workflow consumes that data. Enter this workflow
only when the repository Skill artifact or its own lifecycle changes.

Registry-owned data projections embedded beside a Skill are the narrow generic
exception: the registered generator may refresh them byte-for-byte from an
active data release when the owning Design Contract declares them to be
inspection or recovery projections rather than Skill semantic assets or
production inputs. A change to their layout, generator, or surrounding
`SKILL.md` still enters this workflow.

## Applicability Gate

Before reading the target as an authoring subject or changing any file, verify:

1. the exact active `SystemChangeWorkPackage` names Skill Management as the
   specialist owner and `skill_work_package` as the entry-subject class;
2. the requested result changes the Skill artifact, its code-owned
   registration, host projection, classification, migration state, or
   retirement; and
3. accepting the requested result would require a new Skill candidate or
   lifecycle decision.

A target name, T0 title, Skill ID, directory, `SKILL.md` path, prompt, Reviewer,
or implementation component is locating evidence only. It cannot establish
this Skill's applicability.

If any check fails, stop before authoring or review. Return
`not_a_skill_change` with the observed subject kind, governed layer, likely
accountable owner, and the evidence that disproved Skill applicability. The
caller returns that disposition to the current System Change Scope Assessment
and Plan. Do not continue because this Skill was explicitly named, reinterpret
the adjacent subject as a Skill change, or fall through to another authoring or
review method.

## Required Inputs

Resolve and read:

1. exact current `SystemChangeCase`, `SystemChangePlan`, and Skill Work Package
   refs/hashes;
2. `designDoc/the_skill_management.md`;
3. the installed Soul-layer A14 Prompt Boundary Hygiene resource;
4. the installed Soul-layer A21 Skill and Agent Boundary resource;
5. the owning T0 or T1 Design Contract;
6. the code-owned Skill registration and generated inspection, when present;
7. the existing target `SKILL.md` and every declared
   `runtime_modules/<module_id>/module_registration.json` plus `prompt.md`,
   when present;
8. the target host's Skill interface constraints;
9. the registered deterministic integration or Agent Runtime workflow and
   migration evidence when the Skill is product-facing;
10. for each product Agent Module export, the proposed `module_id`, exact input
   and output schema assets, operation set, context/evaluation/retry/output
   policies, workflow node binding, and positive/negative/schema-drift fixtures;
11. the applicable Contract Audit profile when Independent Review is required.

The two Axioms are the provider-neutral authoring judgment basis: A14 keeps
control-plane explanation out of the execution prompt, while A21 defines the
Skill capability contract and separates Tool, Skill, Workflow, and Agent. They
do not replace the governing Design Contract or code truth. Host-specific
guidance supplements this package only for interface format and validation.

Missing ownership, conflicting class, or an unresolved product execution target
blocks product migration. Do not infer applicability or authority from a target
name, directory, prompt, provider session, or working CLI command.

## Classification

Assign exactly one class:

| Class | Execution boundary |
| --- | --- |
| `primary_agent_development` | Primary Agent may invoke it directly for repository design, coding, migration, testing, engineering review, or external-review coordination |
| `product_deterministic` | Product execution occurs through the admitted deterministic execution integration |
| `product_agentic` | Product execution occurs through Agent Runtime |
| `product_hybrid` | The admitted deterministic integration owns the outer graph and every model-backed Module enters Agent Runtime |
| `projection_only` | The Skill explains or invokes an already managed target and is not a direct executor |

A Primary Agent development Skill cannot process tenant product work. A product
Skill cannot invoke a provider, unmanaged subprocess, or canonical writer
directly.

## Owned Workflow

```mermaid
flowchart LR
    GATE["Verify exact Skill Work Package applicability"] --> RESOLVE["Resolve owner, class, and workflow need"]
    GATE -->|"not applicable"| EXIT["Return not_a_skill_change and owner route"]
    RESOLVE --> WRITE["Write Skill or Module-source candidate"]
    WRITE --> CHECK["Deterministic registration and Skill checks"]
    CHECK --> REVIEW["Independent review when required"]
    REVIEW -->|"accepted"| REGISTER["Admit or update the Skill artifact"]
    REGISTER --> EXPORT{"Runtime Module export changed?"}
    EXPORT -->|"yes"| BUILDER["Handoff to project-supplied Runtime registration capability"]
    EXPORT -->|"no"| DONE["Complete Skill lifecycle change"]
    REVIEW -->|"revision"| PACKET["Immutable revision packet"]
    PACKET --> WRITE
    REVIEW -->|"upstream conflict"| OWNER["Return to owning Design Contract or workflow"]
```

1. Freeze Skill Management, A14, A21, the owner, class, intended behavior,
   existing code truth, target workflow, and host constraints by
   reference and hash where available.
   Identify the Skill candidate with separate `skill_id`,
   `candidate_revision`, and `candidate_sha256` fields. Never
   serialize a Skill candidate as `<skill_id>@candidate_vN`; that typed form is
   reserved for Runtime Module and Workflow Releases.
2. For every product Agent Module, create exactly one fixed
   `runtime_modules/<module_id>/` directory. Its
   `module_registration.json` declares the exact Module identity, owner,
   concrete schema files, operations, policies, and compatibility; its single
   `prompt.md` is the complete provider-neutral static instruction source. A
   symbolic schema ref, alternate prompt filename, or example JSON is not a
   closed registration.
3. Create one candidate `SKILL.md` that identifies the Skill, declared
   Modules, managed entry, and current code-owned registration. Do not repeat
   any exported Module prompt in `SKILL.md`, a host projection, or domain code.
   Keep governance and Axiom explanation in the authoring evidence rather than
   copying it into the task-plane prompt. The prompt may only require and emit
   fields declared by the exact registered schemas.
4. Run syntax, identity, reference, class, forbidden-direct-entry, schema,
   registration-closure, positive, negative, and schema-drift checks.
5. Run Independent Review when the registered profile requires it. The reviewer
   receives the frozen Skill review input and exact candidate, reports findings,
   and does not edit the candidate.
6. Apply findings through a new candidate and preserve revision lineage.
7. Submit the exact candidate to the project's admitted Skill writer and
   accountable Skill owner for acceptance, then regenerate Skill inspection
   only after that external acceptance decision.
   When a product-facing Module export is added or changed, hand the approved
   Module registration source to the project-supplied Agent Runtime
   registration capability. That capability is an external operator binding,
   not a portable Skill supplied by this package. Skill Management does not
   register or admit the Runtime Module Release.
8. Advance migration state only when the state-specific evidence is complete.

## Migration Policy

### `developing`

The existing direct implementation may remain within its declared development
or comparison scope. Product expansion waits for an approved owning T1 workflow
and execution binding.

### `migration_planned`

This is an executable automated test and evaluation stage. Agent Runtime runs
Module tests, evaluations, sibling model or provider Variants, replay,
failure injection, authorization negatives, and observability checks. Fixed and
hybrid workflows also validate the admitted deterministic-integration binding. The direct entry remains
the production comparison path during this stage.

### `managed`

Product routing uses the admitted deterministic integration or Agent Runtime target. The Skill
remains active as the managed entry and Agent-facing semantic projection. Its
text references the managed target and contains no direct provider call,
unmanaged process, or canonical write path.

### `direct_entry_retired`

Keep the canonical managed Skill. Remove the legacy direct runner, embedded
prompt, compatibility alias, and direct write path from active and archived
discovery roots. Code retains a non-executable tombstone containing the old
entry identity, replacement workflow, final hash, reason, and retirement time.
Git history or an authorized audit store preserves historical recovery.

## Review and Completion

Before accepting any authored or revised `SKILL.md`, apply the installed Soul
`COMMUNICATION` resource together with the installed Soul resources
`bestpractice_skill_writing` and `bestpractice_doc_self_review`. Use defect polarity: a finding
means a defect is present; no finding means the check passes. Treat Skill ID,
class, owner, authority, canonical path, compatibility, schema references,
migration state, completion semantics, failure semantics, stop conditions, and
Runtime binding as protected meaning. Clarity edits that could change them
return to Skill Management or the owning Design Contract.

A completed change identifies:

- exact System Change Case, Plan, and Skill Work Package refs/hashes;
- Skill ID, candidate revision, owner, class, and target path;
- owning Design Contract and code registration;
- migration state and managed workflow binding when applicable;
- candidate hash and deterministic check results;
- Independent Review result and revision packets when required;
- direct-entry disposition and tombstone when retiring a legacy path;
- focused tests and regenerated inspection.

Stop with one of these outcomes:

- `accepted`: the exact Skill candidate passed required checks, independent
  review, admitted writing, and accountable owner acceptance;
- `revision_required`: findings return to authoring as a new candidate revision;
- `blocked_owner`: no unique owning Design Contract or accountable owner exists;
- `blocked_boundary`: the Skill, Runtime, Workflow, authorization, or release
  boundary is contradictory;
- `blocked_reproducibility`: candidate, source, projection, or review identity
  cannot be reproduced; or
- `not_a_skill_change`: return the observed subject and actual owner route.

## Invariants

- The owning Design Contract defines behavior.
- Entry requires an exact Skill Work Package whose subject is the Skill itself;
  a named target or nearby file never substitutes for that applicability check.
- A failed applicability check exits before authoring or review and returns to
  the current System Change Scope Assessment and Plan.
- Code registration defines current class, binding, and migration truth.
- Every material Skill change loads Skill Management plus A14 and A21;
  host authoring guidance may supplement but cannot replace that basis.
- Governance and Axiom prose remains authoring control-plane evidence unless a
  specific instruction materially changes task execution.
- A Skill cannot grant Product Authorization, Runtime admission, release
  admission, or canonical-write authority.
- Updating a Registry-owned data object does not trigger Skill Management and
  cannot mutate a Skill, Agent, Module, Workflow, or Team artifact.
- Every model-backed product Module enters Agent Runtime.
- Product-agentic Skill drafting is registration-first: a concrete Module
  candidate and its schema assets exist before task-plane instructions are
  accepted.
- Every product-agentic Module export uses the fixed
  `runtime_modules/<module_id>/module_registration.json` and `prompt.md` read
  channel; the directory name, `export_id`, and `module_id` are identical.
- A portable governance Skill owns its stable `SKILL.md` and any fixed
  governance Module prompt, semantic schema, and provider-neutral Module
  registration source in the installed governance distribution. Those files
  form one hash-bound authoring closure and are projected exactly into each
  consuming project. A project binding may choose Workflow topology,
  authorization, Execution Profile, release version, Code Projection, and
  Runtime admission, but it cannot fork or duplicate the portable prompt or
  semantic schema.
- `prompt.md` is the only editable prompt source. The registered Runtime store
  retains the immutable copy used by production; `SKILL.md`, host projections,
  Runtime code, domain code, and Adapter code do not duplicate its body.
- Example output JSON never overrides the registered output schema, and a
  Reviewer cannot require fields the Producer Module is not allowed to emit.
- The managed Skill remains available after legacy direct-entry retirement.
- Retired direct-execution instructions remain outside active and archived Skill
  discovery.
- Provider, model, adapter, deployment, and current binding inventories come
  from code or generated inspection, not hand-maintained Skill prose.
- Skill review uses separate `skill_id`, `candidate_revision`, and
  `candidate_sha256` fields. It never uses
  `<skill_id>@candidate_vN` and never contributes a Skill candidate revision to
  Runtime Module or Workflow identity.
- Claude SDK, Claude Skill, Codex CLI, and future provider-facing adapters are
  Agent Runtime registrations and never Skill Management implementations.

## Observable Violations

| Boundary | Observable violation |
| --- | --- |
| Applicability before authoring | The method edits or reviews a target after the Work Package owner or entry-subject class fails to match, or it treats a target name or path as sufficient routing evidence. |
| Exit on mismatch | The method keeps working, chooses a nearby Skill, or returns an accepted candidate instead of `not_a_skill_change`. |
| Authoring only | The method supplies its own independent verdict, Runtime admission, software release, or canonical business write. |
| Skill is the governed object | A Registry data update, Runtime release, Workflow release, provider profile, or active pointer is rewritten as a Skill candidate without changing the Skill artifact or lifecycle. |
