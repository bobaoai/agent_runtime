---
name: engineering-code-design
description: Designs one material code change before implementation. Use after the owning Design Intent and engineering route are known, and before editing production code, schemas, migrations, or tests. Produces a bounded CodeDesignBasis; it does not implement or review the change.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: engineering_change_candidate
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

Several physical files may implement one logical module. One physical file may
host several small logical responsibilities only when their boundaries remain
independently testable and reviewable. Physical proximity never proves shared
ownership.

## Required Inputs

Resolve, in order:

1. the approved requested result and exact `EngineeringChangeRoute`;
2. the owning T0/T1/T2 Design Intent and approved design decisions;
3. current code, schemas, Registries, public interfaces, and characterization
   tests for the affected surface;
4. current dependency and data-flow facts;
5. migration, compatibility, rollback, and release constraints when present;
6. prior review findings only as evidence, never as authoring authority.

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
4. Define the smallest complete target set of logical modules. Compare it with
   the current architecture; explicitly choose `retain`, `refactor`, `rewrite`,
   `split`, `merge`, or `retire` for each affected responsibility.
5. Design interfaces and dependency direction before assigning files or
   technologies.
6. Define module-local tests, seam tests, migration checks, rollback, and
   completion criteria.
7. Bind the approved logical design to current implementation paths and freeze
   the candidate as one `CodeDesignBasis`.

## Output Contract

```yaml
CodeDesignBasis:
  status: proposed | approved | superseded
  requested_result: bounded outcome
  owning_design_refs: []
  engineering_change_route_ref: exact ref
  architecture_disposition: retain | refactor | rewrite
  logical_modules:
    - module_id: stable snake_case id
      responsibility: one bounded responsibility
      owned_resources: []
      public_interfaces: []
      allowed_dependencies: []
      prohibited_dependencies: []
      failure_and_recovery: []
      required_tests: []
      future_capabilities: []
      implementation_bindings: []
      disposition: retain | refactor | rewrite | split | merge | retire
  cross_module_seams: []
  migration_and_compatibility: []
  rollback_boundary: bounded recovery statement
  acceptance_criteria: []
  unresolved_decisions: []
```

`implementation_bindings` describe current physical realization. They do not
change logical ownership and must be replaceable without rewriting the module's
responsibility.

## Completion Standard

The candidate is ready for approval only when:

- the intended result is testable and all affected responsibilities have one
  clear owner;
- each logical module can be reviewed and tested independently;
- dependency direction, data access, external effects, failure, recovery, and
  rollback are explicit;
- implementation paths are closed over the design but kept distinct from the
  logical module map;
- future capability needs are named without speculative framework building;
- there is no hidden fallback, parallel authority, or ad hoc bypass;
- an independent reviewer can compare the future `ChangeSetManifest` with this
  exact basis.

Any material correction produces a new candidate and new hash. Implementation
starts only after the applicable design owner approves the basis.

## Observable Failure Signals

The design is incomplete when any of these conditions is visible:

- production implementation changes exist before an approved basis;
- a module map uses directories, frameworks, databases, or providers as peer
  responsibilities without first defining the logical responsibility;
- two modules can change the same durable resource without one declared owner;
- a dependency appears in code but not in the allowed seam map;
- tests cover internal functions while leaving a public seam or recovery path
  unproved;
- the independent reviewer must infer the intended result, acceptance
  criteria, or rollback boundary from the diff.

Return these failures to Code Design. Adding prose to the review package after
implementation does not repair a missing pre-implementation decision.
