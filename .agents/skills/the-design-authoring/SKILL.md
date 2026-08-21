---
name: the-design-authoring
description: Authors, revises, supersedes, or retires one Charter, T0, T1, or T2 Design Intent candidate under Design Doc Management and an exact System Change Design Work Package. It freezes a semantic candidate for independent review and never approves, reviews, implements, or admits it.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: design_work_package
  first_authority_ref: designDoc/the_design_doc_management.md
---

# Design Intent Authoring

## Identity

This is the portable Primary Agent method for authoring, revising,
superseding, or retiring one governed Design Intent candidate. Design Doc
Management owns the Design artifact class,
scaffold, lifecycle, and authoring law. The owning Charter, T0, T1, or T2 owner
owns the candidate's meaning.

The Skill receives an exact System Change Design Work Package. It does not
create a System Change Plan, select another Work Package, perform independent
review, approve implementation, write production code, or admit the Design.

## Objective

Produce the minimum sufficient Charter, T0, T1, or T2 Design Intent candidate that lets
the accountable owner make the intended decision, lets an independent
registered reviewer matching the exact subject kind judge the frozen
semantics, and lets a later
Engineering Code Design form a `CodeDesignBasis` without inventing authority,
behavior, state, failure meaning, or handoffs.

## Applicability Gate

Before reading the target as an authoring subject, verify that the exact active
`SystemChangeWorkPackage` names the Design authoring owner, declares
`design_work_package` as its entry-subject class, and requests a change to one
Charter, T0, T1, or T2 Design Intent. A Design title, path, or nearby code file
is locating evidence only.

If any check fails, stop before editing or reviewing the target. Return the
observed subject kind, governed layer, likely accountable owner, and mismatch
evidence to the current System Change Scope Assessment and Plan. Do not choose
a nearby Design or authoring method as a fallback.

## Required Inputs

Resolve before writing:

1. exact System Change Case, Plan, and Design Work Package refs/hashes;
2. accountable owner intent and requested material decision;
3. `designDoc/the_charter.md`, parent, same-level peer, dependency, and
   inherited-T0 closure;
4. for a manifest-declared portable T0, the exact portable source, target, and
   release-manifest binding; the project projection is never the authoring
   source;
5. the installed Soul `COMMUNICATION` and `bestpractice_doc_self_review`
   resources by stable ID;
6. current Design release and predecessor lineage when revising;
7. current code, Registry, schema, persistent-state, and generated inspection
   only as evidence of as-built truth;
8. for a structural operation, exact `PeerRegistrySnapshotRef`,
   `StructureReviewResultRef`, and `ParentAuthorityDecisionRef`; and
9. prior review findings only as evidence, never as authoring authority.

An unresolved owner, parent, layer, owned object, peer overlap, or accountable
decision returns to the System Change owner. The Design author cannot settle it
by choosing a convenient filename or implementation.

## Authoring Contract

Every candidate states the reader result, owned object, authority, scope,
non-goals, stable invariants, peer boundaries, public inputs and outputs,
failure and completion meaning, machine-contract handoff, and material open
decisions appropriate to its layer.

Keep seven dimensions distinguishable:

1. semantic owner;
2. authoring authority;
3. operator or execution surface;
4. independent review method;
5. persistence or data owner;
6. implementation binding; and
7. approval or admission authority.

The candidate contains stable intent. Exact identifiers, schemas, paths,
providers, models, commands, current bindings, implementation status, test
results, and release state remain in code-owned or generated surfaces. A
portable T0 also omits project-local child-path and adjacent-contract
inventories.

T0 owns one system-wide object or decision. T1 is the one `00` domain root.
Same-domain non-`00` Design Contracts are T2 under that root. Cross-domain
references are dependencies rather than additional parents.

## Output

When the Applicability Gate rejects entry, return only the observed subject
kind, governed layer, likely accountable owner, mismatch evidence, and current
System Change return target. Do not return a Design candidate or authoring
result.

Return one frozen authoring result containing:

- exact Design Work Package ref/hash;
- candidate path, kind (Charter, T0, T1, or T2), layer, stable identity,
  owner, parent, and owned object;
- complete candidate body and content hash;
- affected Design dependency refs;
- declared machine-contract and implementation handoff;
- unresolved owner decisions, if any;
- deterministic authoring-check results; and
- requested independent Design review profile and reviewer subject kind.

The model does not create candidate hashes, approval records, review evidence,
or lifecycle records. The host computes and binds that control-plane metadata.

## Boundaries and Observable Violations

| Boundary | Observable violation |
| --- | --- |
| Authoring only | The turn reviews, approves, admits, implements, or releases its own candidate. |
| Owning intent controls meaning | The candidate invents product behavior or authority because code currently has a convenient structure. |
| System Change parent required | A governed Design mutation has no exact Case, Plan, and Design Work Package. |
| Entry applicability | The method authors or reviews a target after the Work Package owner or `design_work_package` entry-subject class fails to match. |
| Intent versus code truth | Current paths, versions, providers, test counts, or implementation inventory become manually maintained Design authority. |
| Portable source versus project projection | A manifest-declared portable T0 is authored by editing `designDoc/the_*.md` instead of its portable source and exact re-projection. |
| Layer integrity | A T0 owns several unrelated objects, a non-00 file acts as a T1 root, or a dependency is presented as a second parent. |
| Boundary coherence | One table or diagram presents owner, service, database, reviewer, technology, and approval authority as peers without labeling the dimensions. |
| Independent review | The author edits the candidate in the reviewer role, treats self-review as the external verdict, or hard-codes a nearby reviewer instead of resolving the registered subject-kind binding. |

## Completion Standard

An applicability rejection completes before authoring when it returns the
mismatch evidence to the current System Change Scope Assessment and Plan and
produces no candidate.

The candidate is ready for owner decision and independent semantic review only
when its layer, owner, parent, peer closure, authority, stable interfaces,
failure and completion semantics, code-as-truth boundary, and downstream
handoff are explicit; an implementer can proceed without redesign; and no
current implementation fact has become a second Design authority. The final
candidate also passes the installed `COMMUNICATION` and
`bestpractice_doc_self_review` checks without changing protected meaning.
