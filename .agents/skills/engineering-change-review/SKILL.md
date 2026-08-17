---
name: engineering-change-review
description: "Independently reviews a frozen pre-commit engineering candidate or an isolated commit against an approved design decision, exact scope, reproducible gates, and repository contracts. Use before commit to decide ready_to_commit, or after commit to verify what actually landed. This Skill never edits the reviewed subject."
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: review
  primary_agent_entry_subject: engineering_change_candidate
  first_authority_ref: designDoc/the_software_delivery.md
---

# Engineering Change Review

## Identity

- class: `primary_agent_development`
- Primary Agent role: `review`
- Skill governance owner: `designDoc/the_skill_management.md`
- workflow owner: the project-bound engineering-change specialization under Software Delivery
- assurance rules reused from: `designDoc/the_contract_audit.md`
- task: independently judge one frozen engineering change
- output: a reproducible verdict, findings, and a prioritized punch list

This Skill is the portable review method for a project-bound Engineering Change
Governance workflow. It applies Contract Audit's independence and finding
rules, but Contract Audit does not own the workflow and Design Doc Management
does not own its as-built verdict. This Skill is not an authoring guide or a
release gate. Do not read it as the method for creating a candidate.

This Skill reviews implementation. Every material code candidate must already
have an approved, machine-readable `CodeDesignBasis` that states the owning logical
module, intended result, resource and interface boundaries, allowed
dependencies, implementation paths, required tests, rollback, and acceptance
criteria. An existing Design Contract or approved decision may satisfy the
basis; a change that does not alter system shape does not need a new Design Doc.
The Reviewer checks implementation against that basis and never reconstructs or
redesigns the product from the diff.

The review subject has exactly one mode:

| Mode | Subject | Normal decision |
| --- | --- | --- |
| `pre_commit_candidate` | one frozen candidate based on an exact Git base | `ready_to_commit`, `changes_required`, or `not_reproducible` |
| `commit_ref` | one isolated commit or declared commit range | `accepted`, `changes_required`, or `not_reproducible` |

A branch is not a review requirement. Isolation comes from freezing the subject,
not from placing the same diff on another branch.

## Execution Environment

This Skill is the bootstrap and host projection for the managed
`engineering_change_reviewer` Runtime Module. The target execution environment
is a repository-review Execution Profile admitted by Agent Runtime. Runtime
supplies one exact frozen repository snapshot or commit checkout as a declared
read-only input surface. It may expose repository `Read`, `Glob`, `Grep`, and
`Bash`; `Bash` is a protected capability, not a forbidden Runtime concept. It
may execute only commands named by the frozen `SandboxCommandPlan`, with exact
cwd, environment, timeout, network, and writable temp/output roots.

Runtime uses inline-first context delivery. The complete review instruction,
frozen-subject identity, ChangeSet manifest, `CodeDesignBasis`, acceptance criteria,
prior findings, and output contract are placed directly in the model context.
Repository reads and commands verify that brief against as-built code and test
results; they are not a mechanism for asking the reviewer to reconstruct its
assignment by searching the tree.

The Adapter and sandbox must deny subject edits, staging, commits, pushes,
release actions, undeclared commands, and undeclared network access
technically. Runtime records model calls, tool calls, test outcomes, usage,
failures, retries, and lineage. It verifies the subject hash before and after
execution and returns `not_reproducible` if the subject moved.

Until that Runtime capability slice passes Adapter conformance and managed
admission, an isolated Primary Agent SDK/CLI run is only a bootstrap and
comparison path. It must use the same frozen package and capability policy, and
must not be presented as a managed Runtime execution. The intended migration
ends with the Runtime Module as the formal review entry and the direct Provider
entry retired.

A tool-free semantic reviewer may review Design meaning, boundary, or state
semantics. It cannot satisfy this Skill's implementation-review gate because it
cannot independently reproduce the candidate diff, dependency closure, or
declared tests.

## Registered Runtime Module

This Skill authors one portable Runtime Module source:

| `module_id` | semantic owner | purpose |
| --- | --- | --- |
| `engineering_change_reviewer` | `designDoc/the_software_delivery.md` | Independently review one frozen implementation ChangeSet against its approved `CodeDesignBasis`, declared gates, and carried predecessor findings |

Its canonical static task instruction is the `prompt.md` file in the canonical
`engineering_change_reviewer` Module source; its input and output contracts are
the sibling registered schemas. Registry projects the declared Module assets
only to their registered Runtime host.
Provider, model, reasoning, repository tool grants, command allowlist, timeout,
and writable roots remain Execution Profile or per-run control-plane records
rather than Skill or Prompt content.

## When To Use

Use this Skill when a peer Agent or human has finished an engineering candidate
and an independent verdict is required before commit, or when a landed commit
needs post-commit verification.

Typical subjects include:

- package, architecture, source-layout, schema, or contract changes;
- Skill clusters, registries, validators, migrations, and recovery work;
- workflow, Runtime, data-access, persistence, or release-boundary changes;
- a fix-up candidate that claims to close earlier findings.

Use a domain content Reviewer for reports, Thesis, Evidence, or other analytical
prose. Use the security-review workflow for a security audit. Use
`the-contract-audit` when the requested subject is an immutable
`ContractSubjectManifest` evaluated under a declared `ConformanceProfile` for
contract admission. A schema or registry inside an engineering diff remains
part of this Engineering Project Review subject rather than triggering both
review Skills.

## Required Review Package

Every review begins from a closed package. It contains:

1. the validated `EngineeringChangeRoute`, including the unique primary owner,
   complete affected-surface closure, authoring route, and assurance route;
2. `subject_mode`;
3. `base_ref` and, for post-commit review, the exact `commit_ref` or range;
4. an exact path manifest with one state per path: `added`, `modified`,
   `deleted`, or `kept_at_base`;
5. the frozen candidate diff or committed diff and its SHA-256;
6. content hashes for added or otherwise untracked candidate files;
7. the approved machine-readable `CodeDesignBasis` and its owning contract or
   decision references;
8. for migration work, the exact Migration Guidance refs and the append-only
   Migration Log entry that records the actual guidance, Design Doc, registry,
   Code Design, test, and review delta;
9. every claimed validation command with its working directory, material
   environment switches, expected result, and known baseline failures;
10. the current working-tree inventory, recorded separately from the subject;
11. prior findings when the change claims to close them.

The `CodeDesignBasis` binds exactly one repository subject and contains, directly or
through code-owned module records:

- `package_id` and `primary_module_id`;
- exact module paths and declared compatibility slices;
- the changed resources and interfaces;
- allowed package-internal module edges and external dependency roots;
- shared projection paths and every module they affect;
- focused test paths, rollback boundary, and acceptance gates;
- exact `migration_guidance_refs`, `intended_result`,
  `changed_resource_ids`, `future_capability_impact`, and `migration_log_ref`
  when the subject is migration work.

For ordered cross-repository work, each repository has its own frozen candidate,
`CodeDesignBasis`, subject hash, gates, and rollback. A commit, branch, stash, target
directory, or current implementation is evidence; none is a Code Design Basis.
Missing or unapproved `CodeDesignBasis` makes the subject `not_reproducible`. Stop
before reading the diff semantically; the Reviewer must not write the missing
design for the author.

A claimed gate without an exact command, working directory, and scope is not
reproducible. Record it as `not_reproducible`; do not infer the author's filter.

For `pre_commit_candidate`, hash the candidate before review and verify the same
hash at handoff. A live dirty working tree is context, never the review subject.
If the candidate changes during review, stop and require a new candidate.

For recovery or migration work, the package also contains:

- the exact recovery source, such as a stash commit, worktree snapshot, or old
  package release;
- a disposition for every discovered path: `reuse`, `rewrite`, `retire`, or
  `keep_at_base`;
- a three-way comparison: `base` versus `recovery source` versus `candidate`.

When a migration is governed by a code-owned assessment registry, the package
also contains the exact assessment snapshot, selected logical module and target
package records, the registry validator command, and its reproduced result. A
claim that one logical module may enter implementation includes the
module-scoped migration-readiness result. A claim that a package may cut over
includes the stricter package-scoped result. Missing or stale registry closure
makes the review subject `not_reproducible`; prose dispositions cannot
substitute for the registered records.

The Migration Log is decision and evidence lineage, not current-state
authority. For migration work, verify that its entry names the actual Guidance,
Design Doc, registry, Code Design, test, and review delta. When no owning design
semantics changed, the entry must state `design_change: none`; silence is not a
decision. A log entry cannot repair a stale registry, a missing Code Design Basis, or
an unapproved design change.

Any claimed registry or conformance command brings its executable import and
test-input closure into the frozen package. A copied registry without the
validator code, referenced schemas, source records, or sibling manifests needed
by the command is not reproducible evidence.

Commit status does not decide semantic authority. Uncommitted and stash-only
work may be valid source material, while committed code may be obsolete. The
approved disposition and current contracts decide what enters the candidate.

## Review Workflow

### 1. Verify Subject Closure

- Resolve the approved `CodeDesignBasis` before interpreting code. Verify that its
  package, primary module, compatibility slices, resources, paths, dependencies,
  tests, and rollback close over exactly this repository subject.
- For migration work, verify that the exact Migration Guidance refs, refurbished
  owning Design Docs, Code Design Basis, registry snapshot, and Migration Log entry
  describe the same scope. Require an explicit `design_change: none` when the
  work claims no semantic design delta.
- Recompute the path set, path states, content hashes, and candidate diff hash.
- Compare claimed file count with the exact manifest count.
- Reject scope additions, missing paths, and generated or mirror files that
  were not declared.
- For recovery work, verify every source path has one explicit disposition and
  that no older implementation silently replaces a newer contract.
- For registry-managed migration, verify the assessment snapshot is current and
  its selected logical-module, package, source, and dependency records close
  over the frozen candidate. Verify the review package's test and rollback
  evidence separately. Reproduce module readiness when implementation entry is
  claimed and package readiness when cutover eligibility is claimed.
- Reject a path with no declared module or shared-projection owner, a resource
  with two active owners, and a shared projection that does not name every
  affected module.

If subject closure fails, stop with `not_reproducible`. Findings about a moving
or incomplete subject are not reliable.

### 2. Reproduce Claimed Gates

Run each claimed command verbatim with `./.venv/bin/python` where applicable.
Record the author's expected result and the reproduced result side by side.

Run the same focused gate against the frozen candidate or commit subject. Run a
broader repository gate only when the author claimed it or the owning contract
requires it. Separate failures into:

- subject-induced;
- dirty-working-tree-induced;
- pre-existing baseline;
- infrastructure or unavailable dependency.

An unrelated known failure may be excluded only when the base and candidate
produce the same failure and the exact evidence is recorded.

### 3. Read The Actual Change

Read the full diff and the post-change form of structural files. Compare them
with the approved decision table and the author's scope statement.

Review each declared logical-module slice independently before reviewing the
cross-module and package closure. For every changed requirement, identify:

- the logical owner;
- the executable or persisted surface it changes;
- the validator or test that enforces it;
- the downstream interface that consumes it;
- the failure and rollback behavior.

Code placed by directory convenience, current technology, legacy location, or
copy source rather than by the approved responsibility owner is a boundary
defect even when its local tests pass. Conversely, do not demand legacy file
parity when the approved `CodeDesignBasis` classifies the source as `rewrite`.

Instruction-only promises do not count as implemented L1 enforcement when the
condition is deterministically testable.

### 4. Apply Relevant Cross-Cutting Checks

Run only checks triggered by the subject:

#### Scope and dependency closure

- package imports stay inside the declared dependency boundary;
- no hidden filesystem, ambient session, or sibling-repository dependency is
  required for normal execution;
- registries, schemas, code bindings, tests, and generated projections agree;
- deleted or retired entries are absent from active discovery paths.

#### Data and time contracts

- schema enums resolve to real stores or registered objects;
- timestamp fields follow `designDoc/the_timestamp_semantic.md` and its
  code-owned matrix;
- data reads and writes use the owning gateway and declared authorization
  boundary;
- fixture and integration tests cover happy, denial, and drift paths.

#### Skill and host projection closure

`designDoc/the_skill_management.md` defines the installed projection
relationship. The portable Governance Skill release owns the canonical method;
each registered host projection must match that source exactly unless the
governing contract explicitly admits a typed host-specific delta. A host
projection may include `runtime_modules/` only when the Registry declares each
projected file, its hash, and its target.

Missing projection pairs, byte drift, undeclared or orphaned Runtime Module
assets, and stale release hashes are findings. A future host-specific delta
requires an explicit change to the governing contract, Registry, projector,
and tests before review may accept it.

#### Architecture classification integrity

When the subject changes architecture, inspect three views separately:

1. logical responsibility;
2. physical source organization;
3. implementation binding.

One peer list or diagram uses one view. Each arrow declares one meaning, such
as runtime call, data flow, or source import. A directory or current technology
cannot silently become a peer logical responsibility.

Also verify boundary coherence across the change:

- semantic owner;
- authoring authority;
- operator or execution surface;
- independent review method;
- persistence or data owner;
- implementation binding; and
- approval or admission authority.

These dimensions may interact but must not be presented as interchangeable
peer modules, roles, or decisions. Treat an ambiguity as material when it can
change who decides, writes, executes, reviews, persists, or admits the subject.
Report the conflict to the accountable owner; do not repair the design inside
the review turn.

For modular subjects, also verify:

- each module owns one coherent capability and one stable resource set;
- peer modules use the same classification axis;
- source paths, fixtures, tests, and interfaces have one owner or declared
  consumer closure;
- actual imports are a subset of the approved acyclic module graph;
- each module can be tested, reviewed, and rolled back without reading a
  sibling module's private implementation;
- compatibility slices have explicit retirement gates.

#### Recovery integrity

- useful behavior is selected by the approved disposition, not by commit state;
- copied code retains its required tests and dependencies;
- obsolete WIP does not overwrite newer schemas, registrations, or hashes;
- rewritten code is judged against the new package contract rather than legacy
  implementation parity;
- retired material is removed from active discovery and recorded through the
  governing retirement mechanism.

## Severity

Use exactly four levels:

- `BLOCKING`: breaks an approved boundary, subject closure, deterministic gate,
  next-phase entry condition, or canonical data safety.
- `MEDIUM`: creates semantic or enforcement drift that should be resolved in
  this candidate or explicitly deferred by the owner.
- `LOW`: bounded hygiene or maintainability debt that can ride with a later
  change.
- `NIT`: phrasing or style that does not justify a separate fix.

Do not promote a cross-project or historical process weakness into a blocking
finding against one author. Put longitudinal observations in a separate
section with their actual owner.

## Output

Emit in this order:

1. **Subject closure**: mode, base, subject hash, claimed and observed path
   count, dirty-tree separation, and closure verdict.
2. **Gate reproduction**: exact command, expected result, reproduced result,
   and attribution for every mismatch.
3. **Prior-finding follow-up** when applicable.
4. **Vertical findings** for this subject, severity ordered, each with path and
   evidence.
5. **Longitudinal observations** only when a cross-change pattern materially
   matters.
6. **Prioritized punch list** with blocking status and coarse effort.
7. **Terminal verdict**: one value allowed by the selected subject mode.

Each finding states what is wrong, why it matters, the evidence, and the
smallest correct resolution. The Reviewer reports defects and never rewrites
the candidate.

## Boundaries

| Boundary | Detectable violation |
| --- | --- |
| Review only; never edit the subject | The review turn writes, stages, commits, amends, or pushes a reviewed path. |
| Review one frozen subject | A file hash or path set changes between review start and verdict. |
| Do not invent gates | The report contains a command absent from the review package but presents it as an author claim. |
| Keep design authority upstream | The Reviewer replaces an approved design decision instead of checking conformance or returning an owner-routed conflict. |
| Preserve boundary coherence | The candidate conflates semantic owner, author, operator, reviewer, persistence owner, implementation technology, or admission authority. |
| Separate dirty-tree effects | A failure is attributed to the subject without reproducing it against the base or frozen candidate. |
| Preserve recovery provenance | A recovered file enters the candidate without a recovery source and explicit disposition. |
| Preserve migration-registry closure | A registry-managed migration is accepted from prose or Git state without a current assessment snapshot and the applicable reproduced module-entry or package-cutover readiness result. |
| Preserve migration-supervision closure | Migration Guidance, owning Design Docs, Code Design Basis, registry snapshot, or Migration Log describe different scopes, or the log is used as current-state authority. |
| Require design before code review | A material code candidate is semantically reviewed without an approved `CodeDesignBasis` that assigns responsibility, boundaries, dependencies, paths, tests, and rollback. |
| Review module slices before package closure | A multi-module candidate is accepted from aggregate tests while one module slice, shared projection, resource owner, or dependency edge remains undeclared. |

## Completion

The review is complete only when the subject remains hash-stable, every claimed
gate has a reproducibility disposition, every finding cites actual evidence,
and one terminal verdict is issued. Any subsequent edit creates a new candidate
and requires a new review.
