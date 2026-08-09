---
title: Agent Runtime Module Registration and Workflow Assembly
status: proposal
layer: T1
canonical_owner: designDoc/agent_runtime_01_module_contract_and_assembly.md
parent: designDoc/the_agent_runtime.md
reader_persona:
  - Workflow Designer
  - Runtime Maintainer
  - Skill Package Owner
  - Evaluation Engineer
  - Engineering Reviewer
---

# Agent Runtime Module Registration and Workflow Assembly

**Purpose**: Define how a Skill Package exports independently executable
Runtime Modules, how Workflow Releases connect registered Module Releases, how
Prompt and execution configuration are versioned, and how every Module Run is
tested, evaluated, audited, and reproduced.

**Required reader gain**: A reader can register one Skill Package that exports
one or many Runtime Modules, run each Module independently, assemble those
Modules into a workflow without creating a second Step or Component registry,
and identify which facts belong in Git, Postgres, a Cell-local execution store,
or generated inspection.

## 0. Contract Capsule

```yaml
layer: T1
status: proposal
canonical_owner: designDoc/agent_runtime_01_module_contract_and_assembly.md
parent: designDoc/the_agent_runtime.md
scope:
  - Skill Package Release and zero-to-many Module export binding
  - Runtime Module identity, release, admission, and direct execution
  - Workflow Release assembly from exact Module Release references
  - Prompt Bundle and Execution Profile release binding
  - Module Run, Execution Variant, Attempt, output, Evaluation, Selection, and Resolution lineage
  - provider-neutral release admission and generated inspection
non_goals:
  - domain content semantics, graph meaning, quality rubric, or terminal business decision
  - Skill authoring quality or Skill lifecycle policy, owned by Skill Governance
  - Principal, Entitlement, authorization decision, delegation, or grant issuance
  - provider SDK, API, CLI, or durable-backend implementation details
  - physical data residency, retention, or canonical domain writes
inputs:
  - designDoc/the_agent_runtime.md
  - designDoc/agent_runtime_00_execution_charter.md
  - designDoc/the_skill_management.md
  - owning T1 Intent Contract and typed Workflow Graph
outputs:
  - skill_package_release and module_export_binding
  - runtime_module_release, prompt_bundle_release, and execution_profile_release
  - workflow_release and release_admission_record
  - runtime_release_bundle, runtime_module_plugin, and runtime_release_registry
  - module_run, module_execution_variant, attempt, and module_output_resolution lineage
truth_surfaces:
  - designDoc/agent_runtime_01_module_contract_and_assembly.md
  - src/agent_runtime/contracts/execution_module_definition.py
  - src/agent_runtime/contracts/registry_release_definition.py
  - src/agent_runtime/contracts/registry_package_definition.py
  - src/agent_runtime/registry/registry_release_registration.py
  - src/agent_runtime/registry/registry_plugin_registration.py
  - src/agent_runtime/registry/registry_postgres_persistence.py
  - src/agent_runtime/execution/execution_module_invocation.py
  - Postgres control-plane and Cell-local execution records
generated_projection_surfaces:
  - agent_runtime.inspection.inspection_release_rendering:build_runtime_release_inventory
  - agent_runtime.inspection.inspection_release_rendering:render_runtime_release_markdown
review_gate: design approval before implementation; independent engineering review after implementation
```

## 1. Structure Index

The design uses four distinct views. Each section stays within one view.

| View | Question | Canonical objects |
| --- | --- | --- |
| Definition and release | What can execute? | Skill Package Release, Runtime Module Release, Prompt Bundle Release, Execution Profile Release, Workflow Release |
| Graph assembly | How are executable units connected? | Workflow node bindings, edges, input mappings, waits, terminal conditions |
| Execution | What happened in one run? | Workflow Execution, Module Run, Execution Variant, Attempt, output, Evaluation, Resolution |
| Persistence and authority | Where does each fact live? | Git authoring source, Postgres registries, Cell-local execution store, generated inspection |

These views interact through immutable references and hashes. They do not
create authority rank. A graph node references a Module Release. A Module Run
records one execution of that release. Neither fact changes the Skill Package
that supplied its instructions.

## 2. Canonical Object Model

```mermaid
flowchart TB
    SP["Skill Package Release"] --> E1["Module Export A"]
    SP --> E2["Module Export B"]
    SP --> EN["Module Export N"]

    E1 --> M1["Runtime Module Release A"]
    E2 --> M2["Runtime Module Release B"]
    EN --> MN["Runtime Module Release N"]

    M1 --> W1["Workflow Release 1"]
    M2 --> W1
    M2 --> W2["Workflow Release 2"]

    M1 --> R1["Module Run"]
    R1 --> V1["Execution Variant"]
    V1 --> A1["Attempt"]
```

The cardinality is intentional:

- one Skill Package Release exports zero, one, or many Runtime Modules;
- one Runtime Module Release has one owning Skill export when its Executor is
  an Agent;
- one Runtime Module Release may be referenced by many Workflow Releases;
- one Workflow Release references many Runtime Module Releases;
- one Module Run may have one or many sibling Execution Variants;
- one Execution Variant may have one or many immutable Attempts.

There is no separate Step Registry or Component Registry. `node_id` is a
workflow-local graph-position identifier used only when the same Module appears
more than once or a stable position identity is required.

## 3. Skill Package and Module Export Contract

### 3.1 Skill Package role

A Skill Package is an authoring, review, and distribution unit governed by
Skill Governance. It may contain:

- one provider-neutral `SKILL.md` entry;
- shared instruction assets and examples;
- zero-to-many Module export declarations;
- Module-specific instruction assets;
- local development projections for Codex, Claude, or another Agent interface;
- optional workflow-entry projections that route to an admitted Workflow
  Release.

The package itself grants no execution authority. Runtime admits each Module
export independently.

### 3.2 Module export manifest

A product-facing Skill Package declares each executable export through one
fixed directory and one machine-readable manifest:

```text
.claude/skills/<skill_id>/runtime_modules/<module_id>/
├── module_registration.json
├── prompt.md          # generated human-review projection
└── tests/
```

The Runtime registration builder discovers only
`runtime_modules/*/module_registration.json`. The directory name, `export_id`,
and `module_id` must be identical `snake_case` values. `prompt.md` is a
generated UTF-8 projection of the registered Prompt Bundle for human review
and repository recovery. It is not imported as production authority. Ambient
Markdown, sibling Module files, `SKILL.md` sections, and strings embedded in
Adapter code are not instruction members.

The logical manifest shape is:

```yaml
schema_version: runtime_module_registration_v1
skill_package_id: research_theme_writing
skill_id: research-theme-writing
export_id: research_theme_report_writer
module_id: research_theme_report_writer
owner_contract_path: designDoc/research_13_theme_report_writing_pipeline.md
input_schema_ref: schema:research_theme_report_writer_input@v1
input_schema_path: src/research_theme_report_workflow/schemas/writer_input.schema.json
output_schema_ref: schema:research_theme_report_writer_output@v1
output_schema_path: src/research_theme_report_workflow/schemas/writer_output.schema.json
declared_operation_ids: [model_execute]
compatible_transport_kinds: [claude_agent_sdk, codex_cli]
context_policy_ref: context-policy:workflow_execution_isolated@v1
evaluation_policy_ref: evaluation-policy:module_candidate@v1
retry_policy_ref: retry-policy:bounded_candidate@v1
entry_policy: workflow_bound
output_resolution_policy: evaluated_single
```

`owner_contract_path`, `input_schema_path`, and `output_schema_path` are
registration-time authoring locators. The compiler resolves them inside the
authoring repository, validates their declared identities, and imports their
exact bytes into immutable Runtime releases. They are never persisted as
production read instructions and are never resolved by a Module Run.

`export_id` is unique inside one Skill Package Release and equals `module_id`,
so one global executable identity survives Skill packaging without an alias
map. `module_id` is the stable identity carried by every release of that Module
in the Runtime Release Registry. Prompt membership is the ordered set of exact
Prompt Component Release refs and hashes. Runtime rejects an unregistered
component, another root-level Markdown authority, a path escaping the Module
directory, or an ambient package file absent from the export closure.

Changing one Module export creates a new Skill Package Release. The admission
compiler compares export closures independently. An unchanged export may keep
its existing Runtime Module Release when its selected instruction content,
dependencies, contract, and hashes remain byte-identical.

### 3.3 Skill classification and Runtime effect

| Skill class | Module export rule | Runtime entry |
| --- | --- | --- |
| `primary_agent_development` | No product Module export | Direct development use under repository policy |
| `product_agentic` | One or more Agent Module exports | Runtime Module or admitted Workflow |
| `product_deterministic` | Usually a workflow-entry projection; deterministic Modules are code-owned | Dagster or Runtime binding selected by product design |
| `product_hybrid` | Every Agent execution is a Runtime Module export | Fixed outer graph plus Runtime Module execution |
| `projection_only` | No Executor export | Routes to an already admitted Module or Workflow |

A workflow-entry Skill points to `workflow_release_ref`. It does not become an
orchestrator Module merely because it helps a user start the workflow.

### 3.4 Runtime Module kinds

| `module_kind` | Required executable binding | Skill requirement |
| --- | --- | --- |
| `agent` | One exact Skill export and Prompt Bundle loaded by an Agent Executor Adapter | One exact Skill export binding |
| `deterministic` | Immutable code entry ref and code-release hash | Optional explanatory Skill projection |
| `human_task` | Immutable human-task service entry ref and service-release hash | Optional task instruction release |
| `external_service` | Immutable Gateway operation entry ref and Gateway-release hash | Optional explanatory Skill projection |

Module kind changes create a new Module identity. They are not release updates
of the same executable contract.

## 4. Runtime Module Release

### 4.1 Stable identity and immutable release

`runtime_module_release` is append-only and binds one stable `module_id`, one
version, and one behavior-complete contract. `release_admission_record` owns its
append-only admission history. `runtime_release_registry` derives the active
release pointer for new bindings. There is no separate mutable Module
registration DTO.

The logical Module Release contract contains:

```text
module_id
module_version
module_release_ref
module_release_sha256
module_kind
owner_contract_ref and hash
source_skill_package_ref and hash when module_kind=agent
source_export_id and export instruction ref/hash when module_kind=agent
executable_ref and executable_sha256 when module_kind is not agent
input_schema refs/hashes
output_schema refs/hashes
prompt_bundle_release ref/hash when module_kind=agent
declared operation IDs
Context policy ref/hash
Evaluation policy ref/hash
default retry and timeout policy refs/hashes
compatible Executor Adapter kinds
entry_policy
release lifecycle
admission state
rollback target
```

Exact field schemas, enums, indexes, and validators are code-owned. Generated
inspection renders them for humans.

A model-backed Module is complete only when these release dimensions are
closed independently:

| Release dimension | Exact Runtime binding |
| --- | --- |
| Behavior | Runtime Module Release |
| Static instructions | Prompt Bundle Release |
| Input contract | Input Schema Asset Release ref and content hash |
| Output contract | Output Schema Asset Release ref and content hash |
| Provider execution | Execution Profile Release selected per Variant |
| Skill provenance | Skill Package Release and export ID |
| Runtime policy | Context, Evaluation, retry, entry, and output-resolution refs and hashes |

The dimensions remain separate so a provider or model A/B test can change only
the Execution Profile Release while holding the Module, Prompt Bundle, Schema
Assets, semantic input, and policies constant. A schema change creates a new
Schema Asset Release and recompiles every dependent Module or Prompt Bundle;
it is not a mutable edit to an existing release.

### 4.2 Entry policy

Each Module Release declares one production entry policy:

- `workflow_bound`: production invocation requires an admitted Workflow node
  binding. Test, Evaluation, A/B, and replay purposes may execute the Module in
  isolation under their own authorization and side-effect restrictions.
- `standalone_allowed`: an authorized caller may start a production Module Run
  without inventing a Workflow.

Entry policy is part of the immutable Module Release. A test harness cannot
turn a workflow-bound Module into a standalone product action.

### 4.3 Module release admission

| Admission state | Execution meaning |
| --- | --- |
| `candidate` | Registered for validation; execution blocked outside isolated fixtures |
| `shadow_executable` | Cell-isolated candidate execution; canonical side effects blocked |
| `production_canary` | Limited production scope under explicit release and rollback policy |
| `active` | Eligible for authorized production binding |
| `superseded` | New execution blocked; pinned recovery allowed by policy |
| `retired` | Execution blocked; audit lookup retained |

Admission is atomic. Activating a release updates the registry's derived active
pointer. Replacing an active release requires the prior active release to be
superseded in the same atomic bundle. An in-flight Module Run remains pinned
to its original release.

## 5. Prompt and Skill Release Management

### 5.1 System of record

Managed execution uses the following authority split:

| Fact | System of record |
| --- | --- |
| Product and architecture intent | Design Doc |
| Schema authoring file, validator, compiler, and seed declaration | Code and Git |
| Skill Package and Module instruction candidate | Skill Management authoring workflow |
| Registered Schema Asset, Skill Package, Prompt Component, Module, Prompt Bundle, Execution Profile, and Workflow release | Postgres control-plane registry |
| Active release pointer and admission state | Postgres control-plane registry |
| Local `.claude/skills/<skill_id>/runtime_modules/<module_id>` files | Registration manifest plus generated human-review and recovery projections; no post-registration authority |
| Local `.claude/skills/*/SKILL.md` files | Skill Package authoring candidate; never duplicate Module prompt text or override a registered release |
| Local `.agents/skills/*/SKILL.md` files | Codex host-interface projections; never own Module registration or duplicate Module prompt text |
| Authorized dynamic task input and final provider request | Cell-local execution store |
| Shared execution ledger | IDs, refs, hashes, status, timing, and usage only |

Runtime never reads a mutable working-tree Skill, prompt, or schema file as
production authority after the target Module enters `managed`.

The Git Module directory is a registration manifest plus generated review and
recovery projections. PostgreSQL Prompt Component, Prompt Bundle, and
Schema Asset Releases are the immutable registered records and sole production
execution authority. `SKILL.md`, Runtime code, and Adapter code contain no
duplicate model-ready Context or schema body. A registered hash mismatch
requires a new release; Runtime never reconciles the database by rereading Git
during execution.

Registration makes the PostgreSQL release canonical within its recorded
lifecycle state. Admission controls where that release may execute; it does not
return authority to the Git candidate.

Registration and update tooling reads the current Runtime Release Registry
first. It resolves the registered Module, Prompt Bundle, Schema Assets, and
Execution Profiles as the version baseline, then accepts a structured proposed
change from the owning registration workflow and computes new immutable
component and bundle releases. It regenerates the Git review projection from
that result. A missing, stale, or edited local projection cannot replace
registered content.

The Primary Agent participates only in authoring, review, registration, and
release update. A production execution resolves the immutable Module Release,
Prompt Bundle, Execution Profile, and authorized input closure directly from
Runtime authorities. It does not ask the Primary Agent to rediscover the Skill,
recheck its prose, or rebuild the Prompt for each run. A semantic instruction
change returns to the authoring and registration path and creates new immutable
release records before it can affect production.

### 5.2 Prompt Components and Prompt Bundle

A Prompt is managed as ordered immutable component releases and one compiled
bundle rather than one mutable string.

```mermaid
flowchart LR
    SI["Structured Task Instruction"] --> F["Registered Formatters"]
    OS["Canonical Output Schema"] --> F
    F --> MC["Prompt Component Releases"]
    MC --> C["Deterministic Prompt Compiler"]
    C --> PB["Prompt Bundle Release"]
    PB --> MD["Generated Markdown Review Projection"]

    PB --> PE["Cell-local Prompt Envelope"]
    DI["Authorized Dynamic Input"] --> PE
    DC["Execution-selected Domain Context"] --> DI
    RV["Optional Revision Packet"] --> PE
```

`prompt_component_release` stores one exact model-ready static body,
component kind, media type, Formatter identity and version, source-member refs
and hashes, content hash, and release hash. The initial kinds are
`task_instruction` and `output_constraint`.

`prompt_bundle_release` stores the ordered component refs and release hashes,
compiler version, complete compiled static body, body hash, and release hash.
Neither record contains tenant data, user query, entitled search result,
Source content, prior draft, credential, or provider session. Runtime
inspection reads the registered rows; it never opens a Markdown path.

An Expertise, Lens, tenant policy, retrieved knowledge selection, or other
execution-selected domain context is not a Prompt Component. Its owning domain
persists and versions it, then the authorized Runtime caller resolves the exact
body and supplies it as a hashed `module_input_binding` after routing. It is
frozen in the Module input closure and final Prompt Envelope for that Run. It
does not require a specialized Module or Prompt Bundle release.

`prompt_envelope` binds the exact Prompt Bundle, authorized dynamic inputs,
revision packet when present, output-constraint mode, tool policy, and final
application-controlled provider text for one Execution Variant. It is stored
inside the execution's Cell because it may contain protected customer content.
Shared telemetry stores its ref and hash rather than its body.

For text-based provider transports, the Prompt Envelope body is the complete
application-controlled provider text, not an instruction fragment or a recipe
for rebuilding that text. Runtime commits it before the Attempt starts; the
selected Executor sends the same UTF-8 body unchanged. No Adapter may add a
private text prefix, suffix, file instruction, output instruction, or revision
instruction after the Prompt Envelope hash is frozen. Prompt Bundle members
and structured input views remain useful decompositions, but they are not
substitutes for the recorded final text.

The complete invocation is larger than the Prompt Envelope whenever the
Execution Profile uses provider-native tools or output constraints. Runtime
therefore exposes the Prompt Envelope together with the exact submitted tool
definitions, output-schema projection, and their transport locations. A UI
must not label the Prompt Envelope alone as the complete model context.

The Module declares a deterministic `module_input_projection` between its
Runtime-private input closure and its provider request. The private closure
retains refs, hashes, release identities, authorization evidence, scope, and
lineage. The projection produces semantic inline segments, registered Gateway
capabilities, and exceptional managed attachments selected by the frozen
`input_delivery_plan`. It contains only semantic content and the minimum
semantic IDs needed to produce the answer. Provider-readable workspaces do not
contain Runtime input manifests or governed research directories.

The projection first creates one complete `model_semantic_context` independent
of transport. Inline and Gateway delivery are alternative projections of that
same semantic context; a Gateway cannot expose a different raw domain object or
let the model choose which required semantic slots exist. Each Module declares
which slots are `required_complete`. Inline places every required slot in the
Prompt Envelope. Gateway exposes every required slot through exact read tools,
records the model-visible responses, and fails Attempt completion when any
required slot was skipped or only partially read. Pagination may transport an
oversized required slot, but it cannot convert complete-context work into
selective retrieval.

Output-shape rendering removes schema-control annotations such as `$id`. The
exact registered schema remains private Runtime authority and validates every
returned object. Each Execution Profile pins exactly one
`output_constraint_mode`:

| Mode | Application-controlled Prompt | Native provider field | Intended use |
| --- | --- | --- | --- |
| `prompt_only_json` | Contains one task-plane schema projection | Disabled | controlled Prompt-level cross-provider Evaluation |
| `native_structured_output` | Contains no schema projection | Contains one provider-compatible schema projection | production reliability and full-stack Evaluation |

Both modes use the same canonical Module schema and the same deterministic
task-plane projection. Runtime rejects an invocation that places the schema in
both locations or neither location. For `native_structured_output`, the Adapter
maps the projection to the provider's actual interface, such as Claude SDK
`output_format` or Codex CLI `--output-schema`. Unsupported provider
composition keywords may be omitted from the provider projection only because
Runtime still validates the committed output against the complete canonical
Module schema and all registered dynamic domain constraints.

Provider projection is Adapter-owned code, not a second domain schema. When a
native interface requires every object property to be present, the Adapter may
project a canonical optional property as required and nullable. Before output
commit, the same Adapter removes only its synthetic optional-null placeholders
and validates the result against the unchanged canonical Module schema. An
unsupported schema shape fails conformance before provider invocation; the
Adapter never asks a Module owner to rewrite business semantics around one
provider's schema subset.

`prompt_only_json` freezes the same Prompt Envelope bytes across compared
providers when input delivery is also held constant. It disables native
structured-output controls so the application-controlled text is the only
application-supplied output-shape instruction. This is a controlled
Prompt-level comparison, not proof that provider-owned hidden context is
identical.
`native_structured_output` intentionally evaluates the complete provider
execution stack. Provider-generated hidden instructions or grammar compilation
remain provider behavior and must not be represented as byte-identical
cross-provider context.

For a text-only Module with `execution_mode = tool_free`, the projection is
fully inline: the Prompt Envelope contains the static instructions, complete
authorized semantic input bodies, and output requirement. Its Executor exposes
no shell, filesystem, app, browser, computer-use, image, multi-agent, search, or
network capability and parses the final response directly. Admission fails
before invocation if the complete request exceeds the frozen inline budget. It
does not stage input files or request a writable output file. Prompt
prohibitions are not substitutes for these executable controls.

An `agent` profile may give the provider exact admitted tools and one
isolated Attempt root. Runtime may keep small semantic inputs inline; governed
research reads and searches use registered PG-backed Gateway operations. A
Gateway-delivered required input still exposes the complete registered semantic
slot, not the source table row, Runtime manifest, or an open-ended search result. The
Agent writes only under its writable work root. It may write an initial
candidate, read it, revise it, and validate it inside the same invocation. The
Attempt root is not an ambient repository read surface, PG connection, or
Runtime audit surface. Only the declared final submission enters output
validation.

When Evaluation compares one-pass generation with within-invocation Agent
self-revision, both sibling Variants begin from the same byte-identical
`module_input_closure`, Prompt Bundle, output contract, provider, model, and
effort. The experiment must also state whether input delivery is held constant.
When the closure fits inline, one Variant may be `tool_free` and the other
`agent`; the treatment measures the complete execution-mode effect. For a long
closure, both Variants use the same Gateway-read Agent delivery plan and differ only
in the explicit draft-reread-revise behavior. Runtime records each Variant's
initial request, delivery plan, observed Gateway reads, output, latency,
usage, tool observations, and workspace observations. It does not represent
one Agent invocation as two model calls.

Network is orthogonal to both modes. The profile pins `denied`, `gateway_only`,
or `direct_sandboxed`; a Module needing external verification may use a
network-capable Agent profile, while a Module whose full corpus is already
frozen remains offline.

This separation is asymmetric by design: Runtime can always prove which exact
objects produced a task projection, while the model cannot see or reproduce
control-plane identifiers it does not need.

### 5.3 Provider projection

Skill and Prompt releases remain provider-neutral. An admitted Executor
Adapter translates one frozen Prompt Envelope into the provider request shape.
The Execution Variant pins the Adapter revision and the exact translated
request hash. A local Codex or Claude Skill projection may be distributed for
development, but that projection does not replace the Postgres release or
authorize execution.

### 5.4 Update meaning

| Change | Required new release or record |
| --- | --- |
| Shared Skill instruction changes | Skill Package Release; recompile affected Module exports |
| One Module instruction changes | Skill Package Release and affected Prompt Bundle and Module Release |
| Input JSON Schema changes | Input Schema Asset Release plus every dependent Runtime Module Release |
| Output JSON Schema changes | Output Schema Asset Release plus every dependent Prompt Bundle and Runtime Module Release |
| Operation, Context, Evaluation, retry, entry, or output-resolution contract changes | Runtime Module Release |
| Provider, model, effort, Adapter, or tool implementation changes | Execution Profile Release and new Execution Variant |
| Dynamic task input changes | Module Run |
| Workflow node, edge, loop, wait, or terminal condition changes | Workflow Release |

An active Prompt, Skill, Module, profile, or Workflow Release is never patched
in place.

## 6. Workflow Release and Graph Assembly

### 6.1 Workflow authority

The owning T1 defines graph meaning. Runtime validates identity, closure,
release compatibility, and edge legality while treating Module and outcome
semantics as opaque.

`workflow_release` binds:

```text
workflow identity and version
workflow contract version, distinct from the release version
owning Intent Contract ref/hash
graph ref/hash
exact Runtime Module Release refs/hashes
workflow-local node bindings
input-mapping refs/hashes
legal edges, loops, waits, and terminal conditions
authorization manifest ref/hash
execution release ref/hash
lifecycle and admission
```

### 6.2 Graph node binding

A graph node is a workflow-local position, not another registered executable.
`node_kind=module` binds one exact Runtime Module Release. `node_kind=control`
represents a non-executable wait or routing position owned by the exact domain
graph. A control node has no Module ref, Prompt, provider, lifecycle, or second
Registry.

The bindings are deliberately small:

```yaml
node_id: source_verification
node_kind: module
module_release_ref: runtime-module:research_theme_report_source_verifier@1
module_release_sha256: <sha256>
input_mapping_ref: runtime-input-map:theme_source_verification@1
input_mapping_sha256: <sha256>

---
node_id: awaiting_pm
node_kind: control
module_release_ref: null
module_release_sha256: null
input_mapping_ref: null
input_mapping_sha256: null
```

Module purpose, instructions, schemas, permissions, Context, Executor,
Evaluation, retry defaults, and Prompt are resolved from the Module Release and
are not copied into a Module node. Control-node meaning and legal transitions
come from the exact graph ref/hash bound by the Workflow Release.

When a Module appears once and its identity is sufficient, `node_id` may equal
a stable local alias. When it appears multiple times, distinct local node IDs
bind the same Module Release. `node_id` has no independent lifecycle or
Registry.

### 6.3 Workflow shape

```mermaid
flowchart LR
    R["Runtime Module Release: Router"] --> G["Runtime Module Release: Generator"]
    G --> V["Runtime Module Release: Verifier"]
    V -->|revision outcome| G
    V -->|pass outcome| Q["Runtime Module Release: Quality Reviewer"]
```

Wait and routing positions are `control` nodes rather than fake executable
Modules. Terminal outcomes remain terminal edges. A real human-task service is
a `module` node bound to a `human_task` Module Release. A PM decision that
arrives through authorized external-event ingress remains a control wait and
event transition.

### 6.4 One-Module Workflow

A one-Module Workflow is valid when it owns an independent product trigger,
authorization, lifecycle, terminal output, and workflow-level evidence. An
independent Engineering Project Review is one example. A Source Verifier
embedded in Source-to-Evidence remains a Module in that parent workflow even
though test and Evaluation tools can invoke it independently.

## 7. Module Execution Model

```mermaid
flowchart LR
    ORIGIN["Workflow node or direct Module request"] --> RUN["Module Run"]
    RUN --> VA["Execution Variant A"]
    RUN --> VB["Execution Variant B"]
    VA --> A1["Attempt 1"]
    VA --> A2["Retry Attempt"]
    VB --> B1["Attempt 1"]
    A1 --> O1["Attempt Output Bundle"]
    A2 --> O2["Attempt Output Bundle"]
    B1 --> O3["Attempt Output Bundle"]
    O1 --> E["Evaluation Set"]
    O2 --> E
    O3 --> E
    E --> S["Selection when required"]
    S --> R["Module Output Resolution"]
```

### 7.1 Module Run

`module_run` is the independently testable logical work unit. It binds:

- exact Module Release;
- execution purpose: `workflow`, `standalone`, `evaluation`, `test`, or
  `replay`;
- parent Workflow Execution and local node ID when present;
- tenant, Cell, authorization closure, and data scope;
- immutable input refs and `module_input_closure` hash;
- required output and Evaluation policy;
- creation and terminal status lineage.

A different input closure creates a new Module Run. A retry does not.

#### 7.1.1 Canonical execution-record ownership

`agent_runtime.contracts.execution` is the only public schema owner for the
Module Run, Execution Variant, Attempt start, terminal Attempt, output bundle,
Evaluation, Selection, and Module Output Resolution record families. The
Execution Kernel creates and commits those records. Adapters return normalized
invocation observations; they do not define Runtime ledger records. Inspection
code joins committed records and registered releases; it does not define a
second execution model.

The normalized identity boundary is:

| Record | Own fields | Joined authority |
| --- | --- | --- |
| `module_run` | run ID, request ID and hash, origin discriminator, execution purpose, exact Module Release ref and hash, input package ref and hash, input-closure hash, isolated-scope ref and hash, optional Workflow origin group, authorization-context-binding ref and hash, Runtime Release ref and hash, `recorded_at_utc` | Module semantics and output policy come from the pinned Module Release; tenant, Cell, and Principal identity come from the pinned authorization binding |
| `module_execution_variant` | Module Run ID, Variant ID, arm key, replicate index, exact Execution Profile ref and hash, optional Prompt Envelope ref and hash, input-closure hash, `recorded_at_utc` | provider, model, reasoning, adapter, tools, network, workspace, timeout, Context, and output mode come from the pinned Execution Profile |
| `attempt_started` | Module Run ID, Variant ID, Attempt ID, parent Attempt, ordinal, exact invocation-request hash, claim-token hash, timeout, `recorded_at_utc` | execution and authorization identity are reached through the parent Module Run and Variant |
| `attempt` | Module Run ID, Variant ID, Attempt ID, terminal infrastructure status, output-bundle ref and hash, bounded failure classification and diagnostic ref and hash, interval timestamps, `recorded_at_utc` | model calls, tool calls, and usage remain separate Attempt-scoped child records |

For `origin_kind=workflow_graph`, `workflow_execution_id`, `workflow_node_id`,
and `module_dispatch_id` are all present. They are all absent for a root direct
Module Run. No record fabricates a Workflow identity for direct test,
Evaluation, replay, or standalone execution.

`module_run_result` is a non-persisted service response assembled from those
committed records. It may make a direct invocation convenient for a caller, but
it is not another ledger schema or source of truth.

### 7.2 Execution Variant

One `module_execution_variant` freezes every behavior-affecting execution fact
that may vary without changing the logical Module contract:

- Execution Profile Release;
- provider, model, effort, Executor Adapter and revision;
- Prompt Bundle and Prompt Envelope hashes;
- execution mode, frozen input delivery plan, tool, network, sandbox, timeout,
  and Context policy resolutions;
- output-normalization release when required;
- authorization binding and Runtime version.

Provider or model A/B creates sibling Variants under the same Module Run. Each
Variant receives the byte-identical Module input closure and canonical output
schema. Variants never share mutable provider workspaces.

Changing the Module contract, Skill instructions, static Prompt Bundle, input
closure, or authorization scope creates a new Module Release, Module Run, or
Workflow Execution at the appropriate boundary rather than mutating a Variant.

### 7.3 Attempt

An Attempt is one immutable invocation try under one Variant. Retry appends an
Attempt. A committed provider call, external search, tool operation, or side
effect is not repeated during replay.

Runtime code creates Module Run, Variant, Attempt, output, Context, operation,
usage, and failure records. Agents never author their own audit or billing
logs.

Usage, tool-call, authorization, and failure metadata are derived by Runtime
code from the Adapter result and enforcing Gateway observations. The Agent
returns semantic output only. A field that Runtime already knows from the
request, registered release, active claim, or provider response must never be
requested from the model and copied back into the ledger.

The Attempt exists once the provider invocation begins, regardless of whether
the returned body satisfies the Module output contract. Parsing,
normalization, schema validation, and dynamic domain validation are Attempt
finalization stages. A failure in any of them commits a failed Attempt with
provider usage, observed Gateway calls, and a bounded Cell-local diagnostic
reference. It cannot erase the invocation or leave only an outer Workflow
failure.

Output repair appends another Attempt under the same Variant. Runtime may add a
registered repair packet containing the exact validation finding and allowed
semantic values. The repair cannot change the frozen input closure, Prompt
Bundle, Execution Profile, provider, model, or authorization context. A change
to any of those facts creates the corresponding new Variant or Module Run.

### 7.4 Evaluation and Resolution

Each Module Release declares one output-resolution policy:

- `direct_single`;
- `evaluated_single`;
- `selected`.

Multiple eligible Variants require complete Evaluation coverage and immutable
Selection. `module_output_resolution_record` is the sole Runtime authority for
passing one execution output to the next Module or returning it to the caller.
It grants no domain admission, Artifact readiness, publication permission, or
canonical-write authority.

## 8. Direct Module Execution API

The public Runtime service exposes one execution operation. The code-owned
request schema carries all exact refs and hashes:

```python
run_module(
    request: ModuleExecutionRequest,
    *,
    release_registry: RuntimeReleaseRegistry,
    adapters: AgentExecutionAdapterRegistry,
    ledger: ModuleExecutionLedger,
    authority: ModuleExecutionAuthority | None,
)
```

These `PascalCase` names are Python projections. Their canonical contract IDs
remain `module_execution_request`, `runtime_release_registry`,
`agent_execution_adapter_registry`, `module_execution_ledger`, and
`module_execution_authority`. `authority` carries the `agent_runtime_09`
execution authorization controller, context binding, and Product operation
authorization port; it is required whenever the Module declares a protected
operation and admissible as absent only for the operation-free `in_process`
Test/Evaluation conjunction defined in `agent_runtime_08`.

The service performs these ordered safety operations because changing their
order changes authority or replay behavior:

1. resolve the exact admitted Module Release and entry policy;
2. validate authorization before resolving protected inputs;
3. validate the input package and Module input closure;
4. create the Module Run and requested Execution Variants;
5. assemble and persist Cell-local Prompt Envelopes;
6. commit each protected-operation intent and carry the execution authorization context;
7. bind a Product grant only when the action is registered as high risk;
8. execute through admitted Executor Adapters or resource Gateways;
9. commit decision/effect observations, Attempts, outputs, usage, Evaluation, Selection, and Resolution;
10. return immutable output refs and lineage.

Workflow orchestration, module test, Evaluation, A/B, and replay all call this
same service. Direct provider invocation has no conformant ledger path.

## 9. Authorization and Data Boundary

Three different controls participate in one execution and must not be
collapsed:

| Control | Question | Owner |
| --- | --- | --- |
| Product authorization | May this Product Principal start this Workflow or Module and request this resource operation? | Product Authorization |
| Data Access Gateway | Which governed PG-backed objects may enter this exact execution input or dynamic retrieval result? | Enforcing data service under Data Governance |
| Execution capability profile | What may the provider process do after the task context has been assembled? | Runtime Execution Profile plus admitted Adapter |

The Module Release declares required operation classes and its semantic input
schema. Product Authorization decides whether a Principal may invoke the Module
and protected operations. The Data Access Gateway resolves only authorized
objects into the Module input closure. The Execution Profile pins
`execution_mode`, `tool_policy`, `network_policy`, sandbox, and Context policy.
Skill text, Module metadata, graph membership, test status, or output quality
never grants any of these permissions.

Runtime validates authorization before protected artifact resolution. Every
Module Run is pinned to one tenant, Cell, initiating Product Principal,
authenticated workload actor, execution authorization context, data scope, and
input package. A changed Entitlement or widened data scope requires a new
authorized execution.

Canonical Sources, Evidence, drafts, and background materials are normally
resolved from their governed PG services before provider invocation. Runtime
freezes the exact refs, hashes, schemas, and semantic values in
`module_input_closure`, then compiles only model-relevant semantic content into
an exact delivery plan. Small closed content is inlined. Large closed text,
documents, bounded packages, and broad background retrieval use an admitted
`gateway_only` profile and an entitlement-filtered PG-backed Gateway. Exact
binary objects may use managed attachments only when the registered read API
cannot carry them. The Agent does not receive a raw PG credential or scan an
ambient local knowledge folder. Required inputs are never silently truncated
to fit a model window.

Global control-plane registries contain platform and product instruction
releases only. Tenant Sources, evidence, drafts, search results, final Prompt
Envelopes, provider outputs, and credentials stay in Cell-local governed
storage. Shared telemetry contains bounded refs, hashes, timings, usage, and
failure classifications.

## 10. Postgres Logical Registry

The control-plane schema contains these code-owned table families:

```text
skill_package_release
schema_asset_release
prompt_bundle_release
execution_profile_release
runtime_module_release
workflow_release
skill_module_export_binding
workflow_node_binding
workflow_edge
release_admission
active_release_pointer
```

The Cell-local execution schema contains:

```text
workflow_execution
workflow_execution_profile_selection
module_run
module_execution_variant
attempt
prompt_envelope
execution_output
evaluation_run
evaluation_set
selection
module_output_resolution
context_event
usage_event
```

The exact physical schema is implementation-owned. Required invariants are:

- immutable release and execution rows;
- unique stable identity and version pairs plus exact release hashes;
- atomic active-pointer promotion and prior active-release supersession;
- atomic registration that locks the Registry, loads current PG authority,
  merges and validates one candidate bundle, and persists the complete result
  in the same transaction;
- exact ref and hash validation when loading every dependency;
- no `latest` resolution inside a pinned Workflow Execution or Module Run;
- every Agent node resolves its Module from the exact Workflow node binding and
  its profile from one hash-bound execution profile selection;
- Cell isolation and fail-closed authorization before content reads;
- deterministic generated inspection from the persisted Release Registry.

## 11. Release Admission Workflow

Release admission preserves this order:

```mermaid
flowchart LR
    SKILL["Skill Package Release"] --> EXPORT["Module export candidates"]
    EXPORT --> COMPILE["Schema import plus Module and Prompt compilation"]
    COMPILE --> BUNDLE["runtime_release_bundle"]
    BUNDLE --> CANDIDATE["Candidate registry records"]
    CANDIDATE --> MODULE_TEST["Direct Module tests and Evaluation"]
    MODULE_TEST --> SHADOW["Workflow shadow execution"]
    SHADOW --> ACTIVE["Atomic activation"]
    ACTIVE --> ROUTE["Managed product routing"]
```

1. Skill Management admits or identifies one immutable Skill Package Release.
2. The Package exports zero-to-many Module candidates with closed instruction
   assets.
3. The Module compiler validates the owner contract, imports exact input and
   output files as immutable Schema Asset Releases, and validates operations,
   Context, Evaluation, entry policy, and Skill export closure.
4. The Prompt compiler creates immutable Prompt Bundle Releases from the same
   registered output Schema Asset projection.
5. One `runtime_module_plugin` submits a dependency-closed
   `runtime_release_bundle`. The PostgreSQL Release Store locks registration,
   loads the complete current Registry, merges and validates the bundle, and
   persists the result atomically without changing active pointers merely
   because a candidate exists. A caller cannot replace the store with a
   partial in-memory Registry.
6. Test and Evaluation run each Module directly in isolated execution scopes
   with representative authorized data. Review inspects the compiled static
   instructions, dynamic semantic inputs, output shape, and exact final
   provider request; source files alone are insufficient release evidence.
   A model-backed Module must produce one valid real-provider output under its
   candidate profile before its downstream Module is evaluated. Deterministic
   fixtures and schema tests establish conformance but do not establish model
   behavior quality.
7. Shadow workflows bind exact candidate Module Releases and run graph,
   authorization, replay, Context, telemetry, and output-resolution tests.
8. Release admission atomically activates accepted Module and Workflow
   Releases. A Module cannot enter `production_canary` or `active` unless both
   exact input and output Schema Asset bodies are registered and hash-closed.
9. Product routing moves to the managed entry.
10. Skill Governance retires legacy direct execution while preserving managed
    Skill Package and local development projections.

Failure at any gate leaves the current active releases unchanged.

## 12. Conformance Package

Every managed model-backed Workflow Release binds one provider-neutral
`workflow_release_conformance_manifest`. It is an index of immutable refs and
hashes rather than a prompt or a second workflow definition.

The manifest covers:

```text
intent
workflow_graph
module_release_index
module_input_projection_index
artifact_schema_index
skill_package_and_export_index
prompt_bundle_index
execution_profile_index
evaluation_binding_index
authorization_manifest
context_and_data_boundary
telemetry_contract
recovery_and_replay_policy
test_and_evaluation_suite
release_admission_evidence
```

Each Module Run receives only its declared `module_input_projection`: the exact
authorized task materials, operations, and prior outputs required by that
Module. Registering a complete workflow package does not authorize copying all
materials into every Agent context.

## 13. Theme Report Registration Example

One Skill Package may export the Theme Agent Modules, or existing focused Skill
Packages may each export one Module. The Workflow contract is unchanged by
that packaging choice. The minimum Agent Module set is:

```text
research_theme_report_context_curator
research_theme_report_writer
research_theme_report_source_verifier
research_theme_report_debater
research_theme_report_reviewer
research_theme_report_prose_reviewer
```

The required non-Agent Modules are
`research_theme_report_task_intake`,
`research_theme_report_package_freezer`, and
`research_theme_report_revision_continuity_checker`. PM review remains an
authorized external wait and event transition rather than a PM Module. A
terminal Workflow edge invokes Runtime-owned terminal trace and raw-usage
closure; usage finalization is not a business Module. The diagram below shows
the principal content loop; the exact registered graph, including task intake,
continuity routing, and terminal edges, is owned by
`research_18_theme_report_agent_runtime_binding.md` and code truth.

```mermaid
flowchart LR
    C["Context Curator"] --> F["Package Freezer"]
    F --> W["Writer"]
    W --> S["Source Verifier"]
    S -->|revision| W
    S -->|pass| D["Debater"]
    D -->|revision| W
    D -->|pass| R["Reviewer"]
    R -->|revision| W
    R -->|pass| P["Prose Reviewer"]
    P -->|revision| W
    P -->|pass| H["Authorized PM wait"]
```

Theme is admitted only after every graph node resolves an
exact Module Release, every Agent Module resolves an admitted Skill export and
Prompt Bundle, and provider calls enter through `run_module()`.

## 14. Risks and Required Controls

| Risk | Failure signal | Required control |
| --- | --- | --- |
| Skill Package becomes execution authority | Runtime starts a Skill path without an admitted Module Release | Module Release and authorization are mandatory for every invocation |
| Skill and Prompt become two editable truths | Adapter Prompt differs from the admitted Skill export | Deterministic Prompt compiler and exact member hashes |
| Package sharing leaks irrelevant instructions | Writer receives verifier or debater instructions | Closed Module export and Prompt Bundle membership validation |
| Active release mutates | The same release ID loads different bytes | Append-only releases and content-addressed validation |
| Workflow adopts latest Module | Replay changes behavior after a Module promotion | Exact Module Release refs in every Workflow Release and execution |
| Global Prompt release store receives customer content | Source or draft text appears in a control-plane row | Static release validator plus Cell-local Prompt Envelope storage |
| Model receives Runtime bookkeeping | Provider prompt or readable input contains refs, hashes, release/schema identity, Entitlement evidence, tenant/Cell, execution, or billing fields | Declared task-plane projection, no provider-readable manifest, and representative-data prompt inspection |
| A/B arms share mutable context | One provider workspace affects a sibling Variant | Variant-scoped Context identity and isolation tests |
| Direct execution bypasses product entry policy | A workflow-bound Module runs as a production product action | Purpose and entry-policy validation before Module Run creation |
| Module count expands without governance | Duplicate Modules differ only by names | Contract-hash comparison and duplicate-export review |
| Package update causes unnecessary release churn | Unchanged exports are rebuilt without semantic change | Export-closure hashing and reuse of byte-identical Module Releases |

## 15. Canonical Runtime Vocabulary

Canonical object names are `snake_case` across Design Docs, serialized
discriminators, schemas, persistence, generated inspection, and cross-language
adapter contracts. Runtime-owned filenames, directories, variables, functions,
fields, table names, event names, and stable IDs follow the same rule. Python
classes use `PascalCase` only as language-native projections of these canonical
objects. They never define a second platform vocabulary.

The only admitted execution hierarchy is:

```text
workflow_release
  -> runtime_module_release
    -> module_run
      -> module_execution_variant
        -> attempt
```

All public contracts, registries, codecs, persistence rows, adapters, generated
inspection, and product workflows use this hierarchy. Runtime admission fails
when any executable surface bypasses an exact Module Release, creates a Module
Run outside `run_module()`, or makes output consumable without an exact
`module_output_resolution_record`.

## 16. Completion Gates

The design is implemented only when all of the following hold:

- one Skill Package exports at least two independently admitted Modules;
- each exported Module runs directly through `run_module()` with complete
  authorization, Prompt, Context, usage, output, and Attempt lineage;
- one Workflow Release chains those exact Module Releases and exercises a
  revision loop;
- the same Module Release is reused by a second Workflow or standalone test;
- Codex and Claude profiles execute as sibling Variants with isolated Context;
- Prompt Bundle and Schema Asset releases load from Postgres, while dynamic
  Prompt Envelopes remain Cell-local;
- missing Skill export, Prompt Bundle, schema, operation declaration,
  authorization, or release hash fails closed;
- replay never resolves `latest` or repeats a committed provider call;
- generated inspection reproduces the active Skill Package, Module, Prompt,
  profile, Workflow, admission, and active-release bindings;
- deterministic inspection proves that `runtime_release_registry` is the only
  Module, Workflow, admission, and active-release registration authority.

## References

- [Agent Runtime Contract](the_agent_runtime.md)
- [Agent Runtime Execution Charter](agent_runtime_00_execution_charter.md)
- [Agent Execution Adapter Contract](agent_runtime_08_agent_execution_adapter_contract.md)
- [Authorization Integration Contract](agent_runtime_09_authorization_integration_contract.md)
- [Skill Governance](the_skill_management.md)
- [Theme Report Agent Runtime Binding](research_18_theme_report_agent_runtime_binding.md)
