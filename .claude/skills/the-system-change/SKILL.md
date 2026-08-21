---
name: the-system-change
description: Authors and advances one governed System Change Case after Task Routing selects system_change_intake. It owns coordination records only and never authors, reviews, approves, admits, releases, or deploys an affected subject.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: system_change_request
  first_authority_ref: designDoc/the_system_change_governance.md
---

# System Change Coordination

## Identity

This is the portable Primary Agent authoring method for one
`SystemChangeCase`. Its authority comes from
`designDoc/the_system_change_governance.md`. It begins only after Task Routing
selects `system_change_intake`.

The Skill coordinates the change. Every affected Charter, T0, T1, T2, Skill,
Runtime, Data, Audit, and Software Delivery owner retains its own decision,
authoring, review, admission, execution, and release authority.

## Objective

Produce and advance one coherent Case whose Scope Assessment, Plan, Work
Packages, Candidate Set, external results, admissions, and Closure Record all
refer to the same current heads. A Primary Agent that reads the result can start
the exact next specialist Work Package without reconstructing authority from
filenames, chat history, or nearby Skills.

## Required Inputs

Resolve before authoring:

1. the authorized request and immutable `RoutingDecision` selecting
   `system_change_intake`;
2. the previous admitted System Change Governance release or the explicit
   single-use bootstrap decision;
3. `designDoc/the_charter.md` and the current peer-T0 topology;
4. registered identities, owners, dependencies, and current evidence for every
   potentially affected subject;
5. current Case head when the Case already exists; and
6. the installed Soul `COMMUNICATION` resource; and
7. prior findings and external decisions only as immutable evidence.

An unresolved owner, layer, operation, peer group, or current head stops before
specialist authoring. A target name never selects a Work Package by itself.

## Owned Result

The Skill may form or request writes for exactly the six System Change record
families:

- `SystemChangeCase`;
- `SystemChangeScopeAssessment`;
- `SystemChangePlan`;
- `SystemChangeWorkPackage`;
- `SystemChangeCandidateSet`; and
- `SystemChangeClosureRecord`.

It stores typed refs and hashes for external decisions and artifacts. It does
not copy or relabel Design approvals, Skill candidates, Runtime releases,
`AuditResult`, `CodeDesignBasis`, `ChangeSetManifest`, Data decisions,
Release manifests, deployment records, or rollback records.

## Authoring Contract

The current Scope Assessment states the changed subject, affected subjects,
layers, owners, operation, scope, materiality, authority impacts,
implementation impacts, peer context, unresolved ambiguity, and evidence refs.
Case-level materiality may add an omitted owner or require a result already
declared by that owner's registered policy. It cannot downgrade or invent the
subject owner's disposition or gate.

Structural operations bind three separately owned refs: the complete
`PeerRegistrySnapshotRef`, Contract-Audit-owned
`StructureReviewResultRef`, and accountable
`ParentAuthorityDecisionRef`.

The Plan creates one Work Package per affected subject or implementation
surface and binds it to the exact specialist owner, input contract, candidate
or terminal-evidence contract, and completion condition. Independent Review is
an external result requirement, never an authoring Work Package.

Select the specialist from the changed subject kind, governed layer, operation,
and accountable owner. A target name, directory, filename, or nearby Skill is
only locating evidence. Every specialist must validate its exact Work Package
and declared entry-subject class before it acts. If a specialist returns an
applicability rejection, create a new Scope Assessment and Plan head; do not
keep invoking that method or substitute a similarly named Skill.

The Candidate Set contains every current candidate-producing Work Package and
no evidence-only Work Package. A zero-member Candidate Set is valid when the
Plan has no candidate-producing Work Packages. Deployment, recovery,
retirement, and other evidence-only Work Packages close only in the Closure
Record join.

## Completion and Failure

Return one coordination result containing:

- current Case, Scope Assessment, and Plan refs/hashes;
- current Work Package bindings and accountable owners;
- Candidate Set ref/hash when frozen;
- unresolved authority, assurance, admission, dependency, or recovery refs;
- exact next allowed state and next specialist Work Package; and
- Closure Record ref/hash only when every required terminal result resolves.

Use `blocked` only with one bounded reason, recovery condition, and
`resume_state`. A scope- or owner-invalidating result returns to a new Scope
Assessment. A candidate defect returns to its specialist owner. A review or
admission result cannot be converted into approval by this Skill.

## Boundaries and Observable Violations

| Boundary | Observable violation |
| --- | --- |
| Coordination only | The output authors or edits a Design, Skill, Runtime, code, schema, policy, release, or deployment candidate. |
| One intake | A governed mutation starts a specialist branch without the exact Case, Plan, and Work Package. |
| One owner per decision | The Plan supplies an approval, Audit verdict, admission, or release decision instead of an external ref. |
| Complete impact closure | A changed governed surface has no Work Package or declared external result. |
| Specialist applicability | A named Skill begins work despite a Work Package owner or entry-subject mismatch, or its rejection is ignored. |
| Candidate coherence | A candidate-producing Work Package is missing, or an evidence-only Work Package is fabricated as a candidate. |
| Current-head lineage | A stale Scope, Plan, Candidate Set, finding, or admission result advances the Case. |
| No self-admission | A candidate System Change release validates or admits itself without the previous release or explicit bootstrap closure. |

## Completion Standard

The method is complete when the Case has one current Scope Assessment and Plan,
every impact has one owner, every specialist can begin from its exact Work
Package, revisions preserve immutable lineage, and closure verifies rather than
replaces all external terminal decisions. Its human-readable handoff also
passes the installed `COMMUNICATION` rules without changing registered meaning.
