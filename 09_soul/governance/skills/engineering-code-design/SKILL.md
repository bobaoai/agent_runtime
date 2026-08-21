---
name: engineering-code-design
description: Designs one material code change before implementation. Use after the owning Design Intent and engineering route are known, and before editing production code, schemas, migrations, or tests. Produces a bounded CodeDesignBasis; it does not implement or review the change.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: engineering_work_package
  first_authority_ref: designDoc/the_software_delivery.md
---

# Engineering Code Design

## Identity

This is the portable Primary Agent authoring method for the code-design stage of
one material engineering change. Its authority comes from the installed
`designDoc/the_software_delivery.md`, the owning Design Contract, and the
approved change decision. The Skill is an operating method, not another design
authority.

It does not implement code, approve Design Intent, review its own candidate,
admit a release, or infer product behavior from the repository.

## Objective

Produce one approved `CodeDesignBasis` that makes the intended implementation
reviewable before production code changes begin. The basis must be sufficient
for an author to implement the result and for an independent reviewer to judge
whether the implementation stayed inside the approved boundary.

Result correctness has priority over preserving a convenient current layout.
If the existing architecture causes duplicated authority, hidden cross-module
dependencies, compatibility shadows, or repeated local fixes, redesign the
affected architecture before implementation. Do not add a side path merely to
avoid confronting the blocking design problem.

For every added behavior, begin with the existing accountable owner and
canonical path. The basis states whether the change integrates into that path,
replaces it, or introduces a genuinely separate abstraction. A new abstraction
is justified only when integration would violate a declared responsibility
boundary. Convenience, isolation of the current fix, or lower editing cost is
not a boundary. Rejected alternatives remain in the design evidence rather
than surviving in implementation names, comments, documentation, or tests.

## Logical Module Rule

A logical module is an independently reviewable unit of responsibility. It is
not automatically a file, directory, class, process, package, service, Runtime
Module, deployment unit, or database schema.

For every affected logical module, state:

- the result and responsibility it owns;
- the resources and durable state it may read, create, change, or control;
- its public inputs, outputs, and externally visible effects;
- its allowed dependencies and prohibited cross-dependencies;
- failure, retry, idempotency, and recovery behavior;
- the smallest tests that prove its own contract and its seams;
- expected future capabilities that the design must leave room for;
- implementation bindings, including paths and technologies, as a separate
  current-state section.

Use those existing fields to make architectural integration explicit. Record
the current accountable owner and canonical path in the module responsibility,
public-interface, and implementation-binding entries; record the chosen
`retain`, `refactor`, `rewrite`, `split`, `merge`, or `retire` disposition; and
name the responsibility boundary whenever the result introduces a separate
module or path. `migration_and_compatibility` records every superseded path and
either its removal in the selected Slice or its approved deferral to a named
later owner and gate. It also records any implementation artifact that must
remain for migration, audit, or external compatibility, together with the
obligation that requires it. Do not add a second record family for these facts.

Several physical files may implement one logical module. One physical file may
host several small logical responsibilities only when their boundaries remain
independently testable and reviewable. Physical proximity never proves shared
ownership.

## Slice Boundary and Test Sufficiency

An implementation Slice is one bounded increment of an approved Code Design.
It is not permission to implement whichever adjacent dependency makes a test
look more realistic. Before work starts, each Slice states its intended result,
included responsibilities and seams, explicit exclusions, and smallest
sufficient test set.

Tests follow the declared Slice boundary:

- prove every behavior, public interface, failure path, and seam the Slice owns;
- use fixed content fixtures or in-memory implementations when they are
  sufficient to prove the owned contract;
- introduce a database, network, provider, or host-repository integration only
  when the Slice owns, changes, or explicitly
  proves that integration seam;
- defer an excluded integration to the later Slice that owns it rather than
  pulling the dependency forward because the final product will eventually use
  it; and
- treat a failure outside the approved boundary as routing evidence. It does
  not authorize an ad hoc implementation or an unreviewed expansion of scope.

The smallest sufficient test set is not the fewest convenient tests. It is the
minimum set that proves the Slice's complete declared result, including
positive behavior, required negative behavior, and each in-scope seam. A later
integration gate remains mandatory when the approved design assigns that seam
to a later Slice.

The Slice inventory also closes over the actual candidate change. Every changed
production symbol, public export, schema, migration, and test resolves to
exactly one owning Slice. A generated/public projection has one code-owned
projector or conformance owner and one deterministic regeneration gate; it is
not co-owned by the Slices whose facts it renders. Code for a later Slice may
already exist in a dirty worktree, recovered source, or predecessor branch; its
presence does not make it part of the selected Slice or accepted evidence.

A test belongs to the contract or integration seam whose result it asserts.
Using lower-layer candidates, registries, stores, or adapters as fixtures does
not give those dependencies joint ownership of the test. If one test file
contains independent assertions for several owners, split it into owner-named
test files before review. Tests are never projections.

When the result depends on reference and hash closure, the design names the hash
domain at every edge. Tests resolve the referenced artifact and compare the
stored hash with that artifact's canonical hash value. Checking only that a ref
exists, a hash is well formed, or a serialized key is present does not prove
closure. When absence of a field or capability is a contract property, prefer
an exact candidate/result/record field-set fence over a denylist of expected bad
names. Any behavioral guard used as evidence has a live positive control that
proves the guard can fire.

## Required Inputs

Resolve, in order:

1. the approved requested result and exact current
   `SystemChangeWorkPackage` ref/hash;
2. the owning T0/T1/T2 Design Intent and approved design decisions;
3. current code, schemas, Registries, public interfaces, and characterization
   tests for the affected surface;
4. current dependency and data-flow facts;
5. migration, compatibility, rollback, and release constraints when present;
6. prior review findings only as evidence, never as authoring authority.

Before interpreting implementation, verify that the exact active
`SystemChangeWorkPackage` names the Engineering Code Design owner, declares
`engineering_work_package` as its entry-subject class, and requests a material
code-design result. A package, path, language, framework, or failing test is
locating evidence only. If any check fails, stop without designing or editing
code and return the observed subject kind, governed layer, likely accountable
owner, and mismatch evidence to the current System Change Scope Assessment and
Plan.

If the owner, intended result, or peer responsibility boundary is unresolved,
return to the owning design workflow. Do not solve an authority question by
inventing a code module.

## Workflow

1. Restate the intended result and acceptance boundary without prescribing an
   implementation.
2. Map the current responsibilities, resources, interfaces, dependencies,
   failure paths, tests, and implementation bindings.
3. Identify responsibility overlap, missing ownership, circular dependency,
   cross-package leakage, compatibility debt, and ad hoc fixes.
4. Define the smallest complete target set of logical modules. Start from the
   current accountable owner and canonical path for every added behavior, then
   explicitly choose `retain`, `refactor`, `rewrite`, `split`, `merge`, or
   `retire` for each affected responsibility. A separate abstraction names the
   responsibility boundary that prevents integration; absent that declaration,
   integrate the behavior into the existing owner.
5. Design interfaces and dependency direction before assigning files or
   technologies.
6. Divide implementation into bounded Slices when staged delivery is required.
   For each Slice, declare its result, in-scope and excluded surfaces, smallest
   sufficient tests, and completion gate. Define module-local tests, in-scope
   seam tests, later integration gates, migration checks, and rollback without
   importing excluded dependencies into the current Slice.
7. Bind the approved logical design to current implementation paths and freeze
   the candidate as one `CodeDesignBasis`.
8. Before Slice admission, perform a change-registration audit against the
   actual candidate path and symbol inventory. Remove undeclared code, assign it
   to its real later Slice, route a generated output through its single
   code-owned projector, or return to Code Design. Never carry it as unowned
   context.

## Output Contract

When the entry applicability check rejects, return only the observed subject
kind, governed layer, likely accountable owner, mismatch evidence, and current
System Change return target. Do not emit a `CodeDesignBasis`.

```yaml
CodeDesignBasis:
  status: proposed | approved | superseded
  approved_decision_ref: exact approval ref when approved; null while proposed
  requested_result: bounded outcome
  owning_design_refs: []
  system_change_work_package_ref: exact ref
  system_change_work_package_sha256: exact hash
  architecture_disposition: retain | refactor | rewrite
  logical_modules:
    - module_id: stable snake_case id
      responsibility: one bounded responsibility
      owned_resources: [stable resource ids]
      public_interfaces: [stable interface ids]
      allowed_dependencies: []
      prohibited_dependencies: []
      failure_and_recovery: []
      required_tests: []
      future_capabilities: []
      implementation_bindings: []
      disposition: retain | refactor | rewrite | split | merge | retire
  implementation_slices:
    - slice_id: stable slice id
      intended_result: independently testable result
      included_surfaces: [stable surface ids]
      excluded_surfaces: [stable surface ids]
      deferred_integrations:
        - surface_id: stable excluded surface id required by the approved result
          owning_slice_id: later slice_id that proves this seam
          completion_gate: exact later passing condition
      required_tests: []
      completion_gate: exact passing condition
  change_registration_audit_ref: exact audit artifact for the current candidate
  cross_module_seams: [stable seam ids]
  migration_and_compatibility: []
  rollback_boundary: bounded recovery statement
  acceptance_criteria: []
  unresolved_decisions: []
```

`implementation_bindings` describe current physical realization. They do not
change logical ownership and must be replaceable without rewriting the module's
responsibility.

A Slice surface id names exactly one `module_id`, public-interface id,
owned-resource id, or cross-module seam id declared in this basis. The
`implementation_slices` list is ordered. A
`deferred_integrations[].owning_slice_id` must name a Slice that appears later
in that order and lists the same `surface_id` in its own
`included_surfaces`.

## Completion Standard

An applicability rejection completes before code design when it returns the
mismatch evidence to the current System Change Scope Assessment and Plan and
produces no basis.

The candidate is ready for approval only when:

- the intended result is testable and all affected responsibilities have one
  clear owner;
- each logical module can be reviewed and tested independently;
- every implementation Slice has an exact result, included and excluded
  surfaces, and a smallest sufficient test set that proves all in-scope
  behavior without pulling excluded infrastructure into the Slice;
- every excluded surface still required by the approved overall result appears
  in `deferred_integrations` with exactly one later owning `slice_id` and
  completion gate; a surface outside the approved overall result needs no
  deferred integration;
- dependency direction, data access, external effects, failure, recovery, and
  rollback are explicit;
- implementation paths are closed over the design but kept distinct from the
  logical module map;
- every actual changed symbol, export, schema, migration, and test is registered
  to one Slice; every generated/public projection has one projector or
  conformance owner and deterministic regeneration gate; and later-Slice code
  already present in the worktree remains explicitly unaccepted;
- every load-bearing ref/hash edge names and tests its exact hash domain, every
  claimed field absence has a complete structural fence, and every behavioral
  guard used as evidence has a live positive control;
- future capability needs are named without speculative framework building;
- every added behavior resolves to its existing accountable owner and canonical
  path, or to a declared responsibility boundary that justifies a separate
  abstraction;
- every superseded path is removed in the selected Slice or has one approved
  later owner and gate, and every retained migration, audit, or external
  compatibility artifact names the obligation that requires it;
- rejected alternatives remain in design evidence and do not leak into the
  final implementation merely to explain the authoring history;
- there is no hidden fallback, parallel authority, or ad hoc bypass; and
- an independent reviewer can compare the future `ChangeSetManifest` with this
  exact basis.

Any material correction produces a new candidate and new hash. Implementation
starts only after the applicable design owner approves the basis.

## Observable Failure Signals

The design is incomplete when any of these conditions is visible:

- production implementation changes exist before an approved basis;
- the method designs or reviews code after the Work Package owner or
  `engineering_work_package` entry-subject class fails to match;
- a module map uses directories, frameworks, databases, or providers as peer
  responsibilities without first defining the logical responsibility;
- two modules can change the same durable resource without one declared owner;
- a dependency appears in code but not in the allowed seam map;
- a Slice test connects a database, provider, network, or host-repository
  integration that the approved Slice does not own, change, or
  explicitly prove;
- an integration is omitted entirely rather than assigned to the later Slice
  that owns its seam;
- tests cover internal functions while leaving a public seam or recovery path
  unproved;
- a public export or changed code unit has no Slice owner, is assigned to a
  Slice whose `included_surfaces` do not contain the surfaces it exercises, a
  generated/public projection has multiple owners or no deterministic
  projector gate, or already-present later-Slice code is counted as evidence
  for the selected Slice;
- a test is assigned to every dependency used in setup rather than the contract
  it asserts, a mixed-owner test file is left unsplit, or a test is treated as a
  projection;
- a ref/hash test checks only shape, ref text, or hash format without resolving
  and comparing the referenced artifact's canonical hash;
- a test claims a capability is absent by denylisting familiar field names, or
  claims a guard works without demonstrating one input that the guard rejects;
- an added behavior has no declared existing owner and canonical path, a new
  abstraction supplies no responsibility boundary that prevents integration,
  or short-term implementation convenience is presented as that boundary;
- a superseded path remains active without removal or an approved later owner
  and gate, or an implementation artifact preserves a rejected alternative
  without a declared migration, audit, or external compatibility obligation;
- the independent reviewer must infer the intended result, acceptance
  criteria, or rollback boundary from the diff.

Return these failures to Code Design. Adding prose to the review package after
implementation does not repair a missing pre-implementation decision.
