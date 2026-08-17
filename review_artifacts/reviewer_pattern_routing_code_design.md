# Reviewer Pattern Routing Code Design Basis

Status: approved from the current Primary Manager direction.

## Requested result

Make recurring review failures improve future Design Contract and engineering
change reviews without creating one generic Reviewer, copying candidate-specific
findings into permanent prompts, or leaving deterministic rules as prose-only
instructions.

## Owning design refs

- `designDoc/the_design_doc_management.md`
- `designDoc/the_contract_audit.md`
- `designDoc/the_software_delivery.md`
- `designDoc/the_skill_management.md`

## Architecture disposition

`refactor`

The existing `design_contract_reviewer` remains the semantic Design Intent
Reviewer. The existing `engineering-change-review` method gains its missing
registered `engineering_change_reviewer` Runtime Module source. No aggregate
Reviewer or review-pattern service is introduced. The candidate also aligns
the shared T0 review authority needed by both Reviewer methods; that Design
authority work is a separate documentation axis, not a fourth Runtime logical
module.

## Pattern disposition

| Pattern kind | Durable destination | Excluded destination |
| --- | --- | --- |
| Repeated Design Intent or authority defect requiring model judgment | `design_contract_reviewer/prompt.md` | Engineering prompt and deterministic validator |
| Repeated as-built architecture, implementation, migration, or recovery defect requiring model judgment | `engineering_change_reviewer/prompt.md` | Design prompt |
| Machine-decidable invariant | Validator and focused test | Prompt-only enforcement |
| One candidate's unresolved defect | Frozen `prior_findings` input | Global prompt |
| Weak or unconfirmed cross-change observation | Review artifact until repeated and owner-confirmed | Any admitted prompt or validator |

## Shared Design-authority slice

This slice owns stable review boundaries, not executable Reviewer behavior.

- canonical sources:
  `09_soul/governance/t0/the_contract_audit.md` and
  `09_soul/governance/t0/the_software_delivery.md`;
- release authority:
  `09_soul/governance/governance_t0_manifest.json`;
- generated projections:
  `designDoc/the_contract_audit.md` and
  `designDoc/the_software_delivery.md`;
- change reason: predecessor finding
  `contract_audit.bootstrap_self_review_undefined` required a reviewer method
  outside the candidate, while predecessor finding
  `engineering_change_reviewer_prompt.unimplemented_verdict_mapping` required
  Software Delivery to own its terminal-result interpretation;
- validation: T0 release projection parity and the full Runtime test suite;
- rollback unit: both canonical T0 bodies, the T0 manifest hashes, and both
  generated projections move together.

## Logical modules

### `design_contract_review_instruction`

- responsibility: judge one frozen T0, T1, or T2 Design Intent candidate and
  its semantic closure;
- owned resources: the registered task instruction and existing semantic input
  and output schemas for `design_contract_reviewer`;
- public interfaces: the existing Runtime Module registration source;
- allowed dependencies: Design Doc Management semantic checks and Contract
  Audit finding rules;
- prohibited dependencies: source diffs, repository commands, test execution,
  provider choice, release admission, and candidate-specific history not
  supplied as `prior_findings`;
- failure and recovery: missing semantic closure blocks review; correction
  creates a new frozen candidate;
- required tests: all registered check IDs remain covered exactly once; the
  prompt contains no implementation-review or provider control-plane work;
- future capabilities: new semantic check patterns may be added only when they
  are recurrent, owner-confirmed, and not machine-decidable;
- implementation bindings:
  `09_soul/governance/skills/the-contract-audit/runtime_modules/design_contract_reviewer/`;
- disposition: `refactor`.

### `engineering_change_review_instruction`

- responsibility: judge one frozen implementation ChangeSet against its
  approved `CodeDesignBasis` and reproduce its declared gates;
- owned resources: the canonical `engineering-change-review` Skill method plus
  one canonical Module registration, task prompt, input schema, and output
  schema for `engineering_change_reviewer`; the `.claude` and `.agents` Skill
  files are generated projections of that semantic content;
- public interfaces: one Runtime Module registration source owned by Software
  Delivery and authored by the `engineering-change-review` Skill;
- allowed dependencies: Software Delivery implementation-review semantics,
  Contract Audit independence/finding rules, frozen repository input, and a
  declared sandbox command plan;
- prohibited dependencies: Design Intent authoring, release admission,
  provider/model selection in prompt text, undeclared commands, candidate
  edits, and ambient repository state as authority;
- failure and recovery: incomplete subject or moving hash returns
  `not_reproducible`; actionable findings return to the implementation owner;
- required tests: registration closure, schema validation, prompt boundary,
  subject-mode verdict constraints, required check coverage, prior-finding
  follow-up, and loader/compiler compatibility;
- future capabilities: additional repository-review tools may be supplied by a
  registered Execution Profile without changing the task instruction;
- implementation bindings:
  `09_soul/governance/skills/engineering-change-review/SKILL.md`,
  `09_soul/governance/skills/engineering-change-review/runtime_modules/engineering_change_reviewer/`;
- disposition: `rewrite` from Skill-only prose into one registered Module
  source.

### `governance_skill_projection`

- responsibility: project each canonical governance Skill and declared Module
  source to its registered host targets and reject drift or orphan assets;
- owned resources: `09_soul/governance/governance_skill_manifest.json`, the
  deterministic governance release projector, and byte projection of the
  canonical Skill to `.claude/skills/engineering-change-review/SKILL.md` and
  `.agents/skills/engineering-change-review/SKILL.md` plus the declared Module
  assets under `.claude/skills/`;
- public interfaces: manifest validation, `--apply`, and `--check`;
- allowed dependencies: canonical governance source files and declared host
  roots;
- prohibited dependencies: manual host-file edits and production Runtime
  database authority;
- failure and recovery: hash or projection mismatch fails closed; regenerate
  from canonical sources;
- required tests: exact source hashes, projection parity, and orphan sweep;
- future capabilities: new host projections must be declared explicitly;
- implementation bindings:
  `09_soul/governance/governance_skill_manifest.json`,
  `09_soul/governance/governance_skill_release.py`;
- disposition: `retain`.

## Cross-module seams

1. Skill Governance authors each static Module source; Agent Runtime registers
   and executes it.
2. Design Doc Management supplies semantic design checks to
   `design_contract_reviewer`.
3. Software Delivery supplies implementation-review semantics to
   `engineering_change_reviewer`.
4. Contract Audit supplies shared independence, evidence, and finding-severity
   rules without becoming the owner of either reviewed subject or the
   engineering terminal-result vocabulary.
5. The governance projector copies declared canonical assets; it never edits
   prompt meaning.

## Migration and compatibility

- Existing `design_contract_reviewer` input and output schema versions remain
  compatible; this change tightens its static review method only.
- The Primary Agent `engineering-change-review` entry remains available during
  bootstrap. The new Runtime Module is an additional managed execution source,
  not an immediate direct-entry retirement.
- Existing candidate-specific findings remain inputs and are not rewritten as
  global instructions.

## Rollback boundary

Revert the two canonical T0 contract bodies, their T0 manifest and generated
projections, the canonical
`09_soul/governance/skills/engineering-change-review/SKILL.md`, its `.claude`
and `.agents` projections, the Reviewer prompts, Module source, Governance
Skill manifest, and other host projections as one source-controlled candidate.
No admitted Runtime release or persistent execution record is modified by this
authoring change.

## Acceptance criteria

1. Both Reviewer prompts have disjoint, explicit semantic responsibilities.
2. Recurrent design and implementation patterns appear only in their owning
   prompt.
3. Every machine-decidable obligation introduced by this change has a focused
   test or existing deterministic projector gate.
4. `engineering_change_reviewer` loads and compiles through the same Runtime
   Module source path as `design_contract_reviewer`.
5. Governance source hashes and generated projections are clean.
6. The complete Runtime test suite passes.
7. Opus 5 xhigh independently passes the Design Intent candidate and then the
   frozen implementation candidate.

## Unresolved decisions

Runtime admission and replacement of the bootstrap direct review path are
later operational decisions, not hidden completion claims. The registration
refs `context-policy:repository_review_isolated@v1` and
`evaluation-policy:engineering_change_review@v1` are candidate identities for
that later Runtime admission slice. The former must declare repository read
tools, the frozen command allowlist, no network, and bounded writable temporary
roots. The latter must declare evaluation and output-resolution behavior.
Neither ref claims an admitted or executable policy in this candidate.
