---
title: Agent Runtime Registry Contract
status: candidate
layer: T2
canonical_owner: designDoc/agent_runtime_01_registry_contract.md
parent: designDoc/agent_runtime_00_runtime_domain_contract.md
owned_system_object: immutable Agent Runtime release identity and dependency closure
language: en
reader_persona:
  - Runtime Maintainer
  - Domain Plugin Owner
  - Authoring Tool Owner
  - Engineering Reviewer
---

# Agent Runtime Registry Contract

**Purpose**: Define how Agent Runtime receives structured release candidates,
creates one immutable identity for each executable or behavior-bearing release,
validates exact dependency closure, and separates admission, activation,
deactivation, withdrawal, and host selection.

**Required reader gain**: A maintainer can determine what Runtime versions,
which source change creates a new release, how one Skill source may export
multiple independently owned Modules, and why authoring layout, provider
selection, execution state, and host routing do not belong to Registry
authority.

## 0. Contract Capsule

```yaml
layer: T2
status: candidate
canonical_owner: designDoc/agent_runtime_01_registry_contract.md
parent: designDoc/agent_runtime_00_runtime_domain_contract.md
owned_system_object: immutable Agent Runtime release identity and dependency closure
inherits:
  - designDoc/the_charter.md
  - designDoc/the_agent_runtime.md
  - designDoc/the_product_authorization.md
  - designDoc/the_data_governance.md
  - designDoc/the_timestamp_semantic.md
  - designDoc/the_software_delivery.md
scope:
  - immutable Runtime release identity and canonical serialization
  - structured candidate validation and dependency closure
  - Module, Workflow, schema, prompt, policy, profile, and adapter release registration
  - admission, activation, deactivation, withdrawal, and exact retrieval
  - Registry persistence schema and migration evidence
  - Registry public reader and writer ports
non_goals:
  - Skill classification, authoring-folder governance, or working-tree discovery
  - provider invocation, model choice, tool execution, Context delivery, or workspace behavior
  - Workflow advancement, retry execution, Evaluation, Resolution, or execution facts
  - Product Authorization, host routing, domain acceptance, or software release admission
  - current releases, active pointers, provider bindings, database product, or migration status
outputs:
  - immutable Runtime release records
  - exact dependency closures
  - Registry admission and lifecycle records
  - Registry lifecycle ResourcePermissionManifest
  - narrow Registry read and candidate-admission ports
truth_surfaces:
  - designDoc/agent_runtime_01_registry_contract.md
  - code-owned Runtime Registry schemas and validators
  - canonical Registry persistent records
  - generated Registry inspection
```

## 1. Owned Result

Registry turns one structured, content-addressed candidate submission into
individually identified immutable releases and one exact dependency closure. It
answers whether a release is structurally and contractually valid for Runtime,
which exact dependencies it binds, and which release is active for one
`RegistryActivationScope` as defined in section 7.

Registry does not decide whether a user may run the release, which product
workflow should be selected, which model or provider should execute a Module,
whether an output is good, or whether software may enter production.

## 2. Release Identity Law

Every release identity contains one release family, stable object ID, explicit
version, canonical payload schema version, and SHA-256 of the canonical payload.
For one `(family, object_id, version)`, exactly one canonical payload is legal.

Rules:

1. every field affecting behavior, compatibility, dependency, or interpretation
   is inside the canonical payload or an exact content-bound dependency;
2. every `*_sha256` binds the exact canonical bytes or canonical value named by
   its ref, not the ref string alone;
3. decoding a predecessor representation never changes the canonical shape of
   the current release;
4. legacy decoding and data conversion live in an external migration reader,
   not in the current Runtime public contract;
5. an admitted release is never patched in place; and
6. byte-identical, identity-identical content is reused rather than reissued to
   create artificial sibling churn.

A mutable alias, `latest` lookup, authoring path, Git ref, Skill ID, provider
session, or database row sequence is not a release identity.

## 3. Release Families and Boundaries

Registry admits the following logical families. Exact record fields and any
future closed subfamily belong to code-owned schemas.

| Release family | Owns | Does not own |
| --- | --- | --- |
| Schema Asset | Exact input, output, or intermediate data contract bytes and compatibility identity | Prompt prose or provider projection |
| Prompt Component and Prompt Bundle | Static model instruction content and its deterministic ordered assembly | Execution-specific source content, Expertise/Lens data, or provider invocation |
| Behavior Policy | Immutable Context, Evaluation, retry, entry, operation, or output-resolution rule referenced by a Module or Workflow | The execution-time decision or result |
| Execution Variant Policy | One distinct release family selected by the exact host execution binding; owns ordered sibling Variant IDs, exact admitted Execution Profile refs, and their origin or Workflow-node coverage declaration | Host routing, an ambient provider default, or an execution-time selection invented outside the admitted release |
| Runtime Module | One independently executable semantic task, exact input/output schemas, static prompt bundle, required operation classes, compatible transport/capability constraints, and exact behavior-policy refs | Provider/model choice, Workflow node identity, Attempt state, or domain acceptance |
| Workflow | One immutable graph whose executable nodes bind exact Module Releases and whose control nodes use registered graph semantics | Product routing, user entitlement, provider execution, or domain output acceptance |
| Execution Profile | Provider/model/effort/adapter identity and behavior-bearing tool, network, workspace, timeout, output-mode, and adapter-option configuration | Module semantic task, prompt content, or evaluation rubric |
| Adapter | One provider-neutral port implementation descriptor, capability bounds, optional dependency, and conformance identity | Current deployment selection or ambient default |

Every behavior-bearing policy is either an immutable content-bound release or
an inline canonical value inside its owning release. A named ref whose content
is not bound cannot satisfy closure.

## 4. Skill and Authoring Boundary

Skill Governance owns Skill classification, authoring source, folder layout,
migration, projection, and retirement. An external authoring adapter reads that
source and submits one structured `RuntimeReleaseCandidateBundle` containing
canonical candidate bytes and declared relationships.

The candidate bundle is a transport and validation envelope only. The bundle is
not independently versioned or admitted and does not force its member releases
to share an owner or lifecycle.

One Skill source may export zero, one, or many Runtime Modules. Each Module has
its own:

- `module_id`, release version, semantic owner, and owner-contract ref;
- input and output Schema Asset refs;
- static prompt and behavior-policy closure;
- operation and capability requirements;
- entry and output-resolution policy; and
- admission and lifecycle history.

Changing one Module does not re-version an unchanged sibling. Changing Skill
documentation that is not part of a Module's canonical registered content does
not change the Module Release. Skill-level review metadata may be used by Skill
Governance but is not injected into normal Module execution identity.

Registry never discovers a repository checkout, mutable Markdown, or a
vendor-specific Skill or agent-interface path. It validates only the submitted
structured candidate bytes and exact declared refs.

## 5. Runtime Module Release

A Module Release represents one semantic task independently of provider choice
and Workflow placement. Its canonical closure binds:

- semantic task identity and semantic-owner contract;
- exact input and output Schema Asset releases;
- exact static Prompt Bundle release;
- required operation classes and capability constraints;
- exact Context, Evaluation, retry, entry, and output-resolution policies;
- allowed independent or Workflow-bound origins; and
- compatibility and supersession semantics.

The Module may declare transport and capability requirements such as native
structured output, repository-read review, Gateway access, or no-tool
execution. It does not pin a model merely because one current Execution Profile
satisfies those requirements.

An Execution Variant binds one exact admitted Execution Profile at run time.
Provider or model A/B variants may therefore execute the same Module Release
with the same semantic input, formatter, and output schema. A model, effort,
adapter, tool implementation, environment, or provider option change creates a
new Execution Profile or Adapter Release, not a new Module Release, unless the
Module's semantic task or compatibility requirement also changes.

## 6. Workflow Release

A Workflow Release binds one immutable graph and exact Module Release refs. A
graph node ID is placement inside that graph; it has no independent release
lifecycle and does not duplicate the Module contract.

Registry validates structural graph closure:

- every executable node resolves one exact admitted Module Release;
- every referenced schema and behavior policy resolves by exact ref and hash;
- graph entry, terminal, branch, loop, wait, and human-event forms are legal
  under the registered graph schema;
- no node requests an entry origin its Module forbids; and
- no graph ref resolves through `latest` or a mutable authoring surface.

Registry does not interpret business edge meaning, quality rubrics, Scenario
logic, human approval, or terminal domain acceptance. Those remain opaque
domain values consumed by Execution under the exact Workflow Release.

A one-Module Workflow is valid when an external product owner requires an
independent Workflow lifecycle. Independently executing a Module does not by
itself create a Workflow Release.

An Execution Variant Policy is admitted independently from a Workflow, Module,
or Behavior Policy and is referenced by exact release identity from the host
execution binding. Its `release_family` is
`execution_variant_policy`; it does not share the `behavior_policy` family or
activation scope. Registry validates its canonical schema, unique ordered
Variant IDs, and exact admitted Execution Profile refs. Execution document 10
validates at start that its coverage exactly matches every executable node in
the pinned Workflow graph, or the one independent Module origin, before any
execution fact or protected operation is committed. Neither owner may fill a
missing node or Profile from an ambient default.

## 7. Candidate Validation and Admission

Candidate admission follows one direction:

1. receive one bounded structured candidate bundle;
2. validate canonical serialization and content hashes;
3. validate each release schema and family identity;
4. resolve every declared dependency by exact ref and hash from the candidate
   or the canonical Registry;
5. reject missing, mutable, cyclic, incompatible, or owner-conflicting closure;
6. compile deterministic derived graph and compatibility projections;
7. atomically persist each dependency-closed admission unit and its admission
   result, reusing byte-identical shared dependencies; and
8. expose the result through generated inspection.

The Registry writer does not activate a release merely because admission
passes. Admission means valid for Runtime use. A `RegistryActivationScope` is
exactly one `(release_family, stable_object_id)` logical object. It contains no
tenant, host, product route, environment, Cell, region, deployment, or provider
identity. Activation selects at most one current admitted version in that
scope. Deactivation removes that selection without changing the admitted
release. Withdrawal makes an admitted release ineligible for new selection
while preserving historical execution validity. The host's exact execution
binding and Product Authorization remain separate decisions.

A withdrawal lifecycle record appends its reason and supporting evidence refs
to Registry authority alongside the resolved Product Authorization decision
ref and, when validity spans clock domains, the registered predicate, profile,
and clock-health evidence refs. Exact reason vocabulary and evidence schemas
belong to code-owned Registry registrations.

One candidate failure rejects the affected release closure and every Workflow
candidate that depends on it, and identifies the exact defect. It does not
block an independent sibling closure merely because both arrived in one
transport bundle, does not partially activate anything, and does not repair
authoring bytes.

## 8. Public Registry Ports

Registry exposes narrow responsibility-owned ports:

- a candidate-admission writer accepting only structured canonical bytes;
- an exact release reader by family, stable ID, version, and hash;
- an exact dependency-closure reader;
- lifecycle commands for activation, deactivation, and withdrawal that require
  an already-resolved external authorization decision ref from the caller;
  withdrawal also requires its reason and supporting evidence refs; and
- bounded Registry query projections for Inspection that receive a
  storage-applicable `RegistryReadScope` resolved outside Registry and filter
  release rows, counts, facets, and cursors before they leave storage.

`RegistryReadScope` is the release-visibility scope form. It binds the allowed
Principal, tenant, Cell, release families, stable object IDs, lifecycle states,
and actions for one query. It is distinct from `RegistryActivationScope`, which
is only the current-release selection key `(release_family, stable_object_id)`.

The ports return Registry records and refs, not concrete in-memory Registry
implementations, provider projections, Durability graph types, Execution
records, or business-domain objects. Registry has no ambient provider or
backend default.

Product Authorization owns the lifecycle permission decision. The Registry
writer is the Policy Enforcement Point for Registry lifecycle resources. It
publishes a `ResourcePermissionManifest` for activation, deactivation, and
withdrawal. Before mutation, it resolves the exact lifecycle action and release
resource server-side and calls a host-provided Product Authorization decision
validator. The validator must confirm that the decision is current, unexpired,
unrevoked, and bound to that action, resource, Principal, and trusted context.
When that validity judgment compares clock domains, the writer consumes one
registered Timestamp Semantics protected predicate, pins its immutable
`DistributedClockProfile`, requires fresh `ClockHealthEvidence`, and accepts
the authoritative commit instant only inside the profile's conservative
half-open validity window. The writer records the predicate, profile, evidence,
and opaque Product Authorization decision refs with the lifecycle fact. It
fails closed on missing or unverifiable evidence, neither issues nor widens
permission, and declares no dependency on the T2 09 External Authority
Integration ports.

## 9. Persistence and Migration

Registry owns Registry record meaning, schema meaning, intended writer
semantics, and ordered physical-schema release compatibility. Data Governance
registers the Registry record families as managed Data Assets and binds the one
System of Record and writer boundary. A change of persistent-schema authority
uses separately registered Data Governance forward and rollback transitions
with writer fencing, consumer switch, reconciliation, and an explicit rollback
window. Data Governance owns migration legality. Software Delivery admits the
migration implementation and deployment.

Service startup reads one exact installed Registry schema release and refuses
unsupported or incomplete migration state. Ordinary service startup performs
no implicit persistent-schema creation or mutation. SQL patterns such as
`CREATE TABLE IF NOT EXISTS` are one example of the forbidden behavior, not the
store-specific definition of the rule.

Every persisted Registry record family follows Timestamp Semantics. The
authoritative store assigns `recorded_at_utc`; every concrete persisted class
has an exact timestamp registration; and lifecycle, validity, and migration
rollback windows use the registered instant and interval semantics. A storage
helper that validates timestamp syntax cannot authorize a lifecycle decision.

Migration evidence covers clean creation, ordered forward upgrade,
interrupted-migration recovery, compatibility refusal, reconciliation, writer
fencing, explicit rollback window, and the documented restore or forward-repair
path. Safe reverse DDL is declared when supported and is never assumed.

## 10. Failure Semantics

| Failure | Registry result |
| --- | --- |
| Non-canonical or hash-mismatched payload | Reject before persistence |
| Missing, mutable, cyclic, or incompatible dependency | Reject the candidate closure |
| Execution Variant Policy contains a duplicate Variant ID, unresolved or unadmitted Execution Profile ref, or invalid coverage declaration | Reject the policy release before admission |
| Host start binds an Execution Variant Policy whose coverage does not exactly match the pinned Workflow graph or independent Module origin | Execution rejects the start before committing execution facts or protected work |
| Duplicate identity with different payload | Reject as immutable identity conflict |
| Owner-contract conflict | Reject and route to the declared semantic owners |
| Working-tree or Skill-path dependency | Reject the authoring adapter output; Registry does not read the path |
| Lifecycle command without the required resolved authorization decision ref | Reject before Registry mutation |
| Lifecycle decision expired, revoked, or bound to another action, resource, Principal, or context | Reject before Registry mutation |
| Activation would create a second current release in one RegistryActivationScope | Reject as an active-selection conflict |
| Lifecycle action or release resource is absent from the published ResourcePermissionManifest | Registry writer rejects before Product Authorization evaluation or Registry mutation |
| Lifecycle fact would commit without its resolved Product Authorization decision ref | Reject or roll back the lifecycle transaction; expose no unauthorized or unauditable mutation |
| Protected lifecycle validity lacks a registered predicate or has missing, expired, unhealthy, regressed, mismatched, or unverifiable clock profile or health evidence | Reject before Registry mutation |
| Withdrawal command or lifecycle fact lacks its reason or supporting evidence refs | Reject before Registry mutation |
| Registry query lacks a current storage-applicable RegistryReadScope or requests rows, counts, facets, or cursors outside it | Reject before storage disclosure and return no partial query result |
| Admission transaction would expose a release without its complete dependency closure or admission result | Roll back or reconcile the admission unit; expose no partial admission |
| Persistent-schema migration lacks the registered forward or rollback transition | Refuse migration and preserve the current writer and consumer binding |
| Unsupported persistent-schema release | Refuse startup or affected operation |
| Withdrawn release selected for new work | Refuse selection while preserving historical exact retrieval |
| Missing optional provider or backend dependency | Base Registry remains importable; only the requesting Adapter release is unavailable |

Registry failure never becomes provider retry, Workflow failure, domain
rejection, or software-release admission.

## 11. Conformance and Completion Evidence

Registry conformance proves at least the following results. The ordering,
atomicity, isolation, and evidence-recording rules in sections 7, 8, and 9 are
conformance obligations even when a later code-owned profile decomposes them
into more granular checks.

1. one payload and one hash domain per release identity;
2. content hashes bind exact schema, prompt, policy, and adapter bytes;
3. each Module has one semantic owner and independently versioned closure;
4. one Skill source can export multiple Modules without shared lifecycle or
   sibling re-versioning;
5. model/profile A/B reuses one Module Release when semantic content is equal;
6. Workflow graphs resolve exact Module Releases without mutable aliases;
7. admission, activation, deactivation, withdrawal, host selection, and Product
   Authorization remain distinct decisions;
8. lifecycle mutation validates a current Product Authorization decision for
   the exact action and release resource through the Registry writer's
   enforcement boundary;
9. the published lifecycle `ResourcePermissionManifest` declares the resource
   type, authorization scope form, condition schema, and grant requirement
   class required by Product Authorization for activation, deactivation, and
   withdrawal, and the Registry writer rejects an action or release resource
   absent from that manifest; the authorization scope form is distinct from
   the release-selection key `RegistryActivationScope`;
10. at most one admitted release is active for each
   `(release_family, stable_object_id)` scope;
11. Registry imports no authoring tree, provider implementation, Durability
   implementation, Execution state, or business database;
12. the public Registry namespace matches its Source Architecture manifest;
13. every persisted Registry class has its required Timestamp and Data Asset
    registrations, plus separately registered forward and rollback transitions
    when persistent-schema authority changes;
14. real persistent-store tests prove schema-release, writer fencing, consumer
    switch, reconciliation, rollback-window, and migration behavior;
15. one dependency-closed admission unit and its admission result commit
    atomically, so a crash or concurrent writer exposes no torn or partial
    admission;
16. failure of one candidate closure rejects that closure and its dependents
    while an independent sibling closure delivered in the same transport
    bundle remains eligible for admission;
17. one lifecycle mutation and its resolved Product Authorization decision ref
    commit atomically, so activation, deactivation, or withdrawal never survives
    without its enforcement evidence;
18. every protected cross-clock lifecycle validity judgment uses a registered
    predicate, pinned `DistributedClockProfile`, fresh `ClockHealthEvidence`,
    conservative half-open acceptance window, and exact evidence refs recorded
    with the lifecycle fact;
19. withdrawal never commits without its recorded reason and supporting
    evidence refs;
20. every Registry query applies one current `RegistryReadScope` at the storage
    boundary before rows, counts, facets, or cursors leave storage, and that
    release-visibility scope never substitutes for `RegistryActivationScope`;
    and
21. every Execution Variant Policy resolves exact admitted Execution Profiles,
    while Execution rejects a host start whose policy does not exactly cover
    the pinned Workflow graph or independent Module origin.

Missing real integration evidence is validation debt and cannot be represented
as a passing production release gate. Registry supplies its conformance
evidence; Software Delivery decides release admission.

## 12. Change Boundary

This contract does not define exact fields or migrate current Registry data.
After the complete Runtime Design Contract set is accepted, a Code Design Basis
freezes the release-family schemas, current-to-target identity migration,
authoring-adapter seam, public API cutover, consumer closure, persistent-schema
migration, tests, and rollback before Registry implementation changes.

## References

- [Agent Runtime Charter](../agent_runtime_t0_t1_candidate/the_charter.md)
- [Agent Runtime T0](../agent_runtime_t0_t1_candidate/the_agent_runtime.md)
- [Agent Runtime Domain Contract](../agent_runtime_t0_t1_candidate/agent_runtime_00_runtime_domain_contract.md)
- [Source Architecture](agent_runtime_02_source_architecture_contract.md)
- [Design Doc Management](../../designDoc/the_design_doc_management.md)
- [Skill Governance](../../designDoc/the_skill_management.md)
- [Product Authorization](../../designDoc/the_product_authorization.md)
- [Timestamp Semantics](../../designDoc/the_timestamp_semantic.md)
- [Data Governance](../../designDoc/the_data_governance.md)
- [Software Delivery](../../designDoc/the_software_delivery.md)
