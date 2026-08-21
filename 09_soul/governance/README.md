# Hoveath Governance Foundation

`09_soul/governance/` is the portable upstream for the complete T0 governance
system that Hoveath installs into a project. It carries reusable governance
intent, scaffolds, and deterministic release rules. The Charter is instantiated
for the target project during deployment; Hoveath does not impose one product's
Charter on another project.

## Deployment model

```mermaid
flowchart LR
    L["Portable law<br/>T0 manifest"] --> R["Governance Release"]
    M["Portable operating methods<br/>Governance Skill manifest"] --> R
    C["Project-specific Charter"] --> R
    R --> D["designDoc/the_*.md"]
    R --> H["Claude/Codex governance Skill projections"]
    B["Project-local bindings<br/>Registry, Runtime Modules, validators"] --> P["Governed project"]
    D --> P
    H --> P
```

The reusable governance rules are reviewed when they change in Hoveath. A
project installation does not reclassify every T0 as product-specific and does
not create an aggregate governance-bundle review or announcement.
Project deployment generates the project Charter and performs deterministic
compatibility, projection, and enforcement checks.

Each released `the_*.md` file is portable law only. It may name logical
Registry, validator, inspection, and enforcement surfaces, but it must not
embed a consuming project's `src/`, test, temporary-review, repository, or
deployment paths. Project-local implementation truth stays in code-owned
registries and generated inspections. The project Charter states which parts
of the portable law are product-owned, externally enforced, or inapplicable.

## Boundary

Hoveath owns:

- reusable top-level governance intent;
- the complete reusable T0 baseline and scaffold used to instantiate a
  project's T0 system;
- projection and drift-checking rules shared across projects;
- portable authoring and review methods for design, code, Skill, data, Runtime,
  and software-delivery changes.

Each project owns:

- its product Charter;
- its instantiated T0 release as governed by that Charter;
- local T0 Registry bindings and Code Projections;
- product and domain specializations;
- implementation, deployment, and current operational state.

This is the Design Intent / Code Projection split at T0: Hoveath fixes the
portable law; local code and generated inspection fix the current facts. A
project does not edit portable law to make its present implementation look
complete.

## Release rule

Deploy `09_soul` first. Then the project adapter generates or supplies the
project-specific Charter before the release tool projects the full project T0
system from the installed Hoveath governance foundation. The portable release
tool validates and projects; it never authors the Charter.
A portable governance change is corrected upstream and re-projected; it is not
independently rewritten inside every consuming project.

Hash and compatibility checks prevent drift. They are mechanical deployment
checks, not a second semantic approval system.

Fresh-install order is fixed: the project adapter supplies the Charter, the T0
release applies and validates `designDoc/the_*.md`, then the Skill release
applies and validates host projections whose `first_authority_ref` now resolves.

Universal leakage guards reject user paths, temporary Design paths,
implementation source and test paths, and virtual-environment paths. Each
project adapter may add product or repository identities through
`governance_bindings/governance_release_policy.json`; portable release code
does not hard-code one consuming project's name.

## Three governed dimensions

The Governance Release keeps three dimensions distinct:

1. **Portable law**: `governance_t0_manifest.json` releases the reusable T0
   Design Intent into the project's `designDoc/the_*.md` authority surface.
2. **Portable operating methods**: `governance_skill_manifest.json` releases
   the Primary Agent methods used to route, design, author, and review governed
   changes. These Skills implement the law but do not become a second authority.
3. **Project-local bindings**: local Registries, validators, Runtime Modules,
   provider profiles, Skill binding addenda, and generated inspections state
   how the project currently enforces the installed law and methods.

One dimension may refer to another through typed IDs and hashes. They are never
flattened into one peer list: a Design Contract is not a Skill, a Skill is not
a Runtime Module, and a current implementation binding is not portable law.

The portable governance method release contains:

| Skill | Entry role | Responsibility |
| --- | --- | --- |
| `the-task-routing` | routing | Select one authorized semantic mainline |
| `the-system-change` | authoring | Author and advance one System Change Case without taking subject authority |
| `the-design-authoring` | authoring | Author one Charter, T0, T1, or T2 Design Intent candidate for independent review |
| `the-skill-authoring` | authoring | Author one exact Skill candidate after validating the Skill Work Package |
| `the-contract-audit` | review | Review one immutable contract subject |
| `engineering-code-design` | authoring | Freeze a reviewable Code Design Basis before implementation |
| `engineering-change-review` | review | Judge one frozen as-built engineering candidate |

`agent-runtime-registration` remains an Agent Runtime operator capability, and
`support-session-handoff` remains a session-support capability. Software
release, deployment, rollback, roll-forward, retirement admission, and their
terminal evidence remain a project-supplied Software Delivery capability.
Prose and Communication Review remains a project-supplied, profile-bound
reviewer capability when an Audit Profile marks that layer required; otherwise
the profile records `not_required` explicitly.
These capabilities are not silently promoted into this governance method
release merely because the current project routes to them. A consuming project
must bind and verify them before a System Change Case that requires those
Work Packages may close.

The default Work Package method map is:

| Work Package kind | Portable method or declared external capability |
| --- | --- |
| Charter amendment | `the-design-authoring`, then the registered Design reviewer, plus the accountable Charter decision |
| Design Intent | `the-design-authoring`, then the registered Design reviewer |
| Skill | `the-skill-authoring`, then its registered independent reviewer |
| Engineering implementation | `engineering-code-design` followed by the implementation owner |
| Independent Engineering Review | `engineering-change-review` and its registered Runtime Module |
| Independent Structure Change Review | `structure_change_reviewer` under the `the-contract-audit` Skill Package |
| Governance Release install or upgrade | project adapter operator action: run T0 apply, then Skill apply, then inspect and remove only the exact reported retired roots; no portable authoring Skill |
| Agency Platform, Product Authorization, Artifact Graph, Data, Timestamp, or Audit policy | `the-design-authoring`; add Engineering methods only when code, schema, migration, or tests change |
| Runtime registration | project-supplied Agent Runtime registration capability |
| Release, deployment, rollback, roll-forward, or retirement | project-supplied Software Delivery capability |

The complete `09_soul` distribution is installed before the Governance
Release. Governance Skills may require stable Soul-layer Axiom and
best-practice resources such as A14, A21, `bestpractice_skill_writing`, and
`bestpractice_doc_self_review` without copying them into a Skill Package.
Primary Agents resolve those stable IDs through the installed Soul index; the
Governance Skill manifest does not duplicate or re-release Soul resources as
task-plane package files. User-facing authoring and review also consume the
installed Soul `COMMUNICATION` resource; project-specific communication detail
belongs in the project adapter rather than a second governance contract.

## Module boundary

```text
09_soul/governance/
  governance_t0_manifest.json       portable T0 source/target/hash declarations
  governance_t0_release.py          stdlib-only check and projection module
  governance_skill_manifest.json    portable governance Skill declarations
  governance_skill_release.py       stdlib-only host projection and drift check
  t0/                               portable T0 Design Intent sources
  skills/                            portable Primary Agent governance methods
  tests/                            module-local deterministic tests

<project>/
  designDoc/the_charter.md          project-specific Charter
  designDoc/the_*.md                released T0 Design Intent projections
  governance_bindings/              hash-bound project Skill binding addenda
  .claude/skills/                   Claude governance Skill projections
  .agents/skills/                   Codex governance Skill projections
  <project code>                    local Registry, Code Projection and enforcement
```

`governance_t0_release.py` owns only the portable T0 release mechanics:

1. parse and validate the portable manifest;
2. reject missing, hash-drifted, absolute, escaping, or duplicate paths;
3. reject a portable source that embeds project-local implementation paths and
   check whether a project's T0 law projections equal their Hoveath sources;
4. validate active and retired T0 reference closure in portable sources and the
   project Charter;
5. report undeclared, retired, missing, or drifted source and target members;
6. write exact projections when explicitly run in apply mode.

It does not author a Charter, run project-specific validators, edit a Registry,
select T0 meaning, or announce a release. The consuming project's adapter owns
those local actions and must supply a project-specific Charter before treating
the released files as a complete local T0 system.

`governance_skill_release.py` validates Skill identity, role, subject, T0
dependency closure, every declared package-file hash, host target, and exact
projection bytes. `SKILL.md` projects to both Primary Agent hosts. A portable
governance Runtime Module may additionally release its fixed prompt, semantic
schemas, and provider-neutral Module registration under the canonical Claude
Skill Package surface; those files remain part of the same portable package,
not project-local copies. A project may declare a hash-bound addendum in
`governance_bindings/governance_skill_binding_manifest.json`; the release
mechanically composes the portable method followed by that project binding.
This keeps a local Runtime Module ID or workflow entry out of portable Hoveath
while leaving both host projections reproducible. The addendum may explain
reachability and binding; it cannot duplicate the model-ready prompt or grant
execution authority. A new or materially changed addendum enters a Skill Work
Package and receives the registered `skill_candidate_reviewer` judgment before
its hash enters the binding manifest; an approved bootstrap review records its
limitation and successor cross-review obligation.

The release module does not register product Skills, admit Runtime releases,
choose a provider, or copy project-local business instructions into Hoveath.
Workflow topology, authorization, Execution Profiles, release versions, Code
Projections, and data-store admission remain consuming-project bindings.

In a Module registration, `skill_package_owner_contract_path` names the
contract that owns the Skill entry and subject lifecycle;
`module_owner_contract_path` names the contract that owns the Module's
semantic method. They may differ. Contract Audit can package a Design or Skill
reviewer whose method remains owned by Design Doc Management or Skill
Management, while a Software-Delivery-facing Engineering Skill can export a
Contract-Audit-owned reviewer method.

A Governance Release is mechanically clean only when both release modules
report clean. Their manifests remain separate because law and operating method
are separate governed objects.

The Skill release apply mode is intentionally non-destructive: it writes only
declared projections. The project adapter handles findings by code:

- `governance_skill_retired_projection_present` and
  `governance_skill_undeclared_package_member` require inspection and removal of
  only the exact reported obsolete path;
- `governance_skill_retired_projection_reference` identifies an active,
  project-owned Skill that still names a retired identity. It returns to that
  Skill's owner through a Skill Work Package for reference migration. The
  adapter must not delete the referencing Skill;
- `governance_skill_undeclared_source_member` and
  `governance_skill_undeclared_source_directory` identify drift inside the
  installed portable source package. They return to the Governance Release
  owner for source/manifest correction rather than project-owned path deletion.

This separation prevents an automatic projector from deleting user-owned files
merely because a manifest or referenced identity changed.

Independent release review evaluates the project Charter, complete portable T0
set, complete portable governance Skill set, both manifests, release tools,
and deterministic evidence as one layered package. T0 contracts remain peers;
Skills remain operating-method projections and never enter the T0 peer table.

The manifest carries every reusable peer T0 contract; the project-specific
Charter remains outside it.
