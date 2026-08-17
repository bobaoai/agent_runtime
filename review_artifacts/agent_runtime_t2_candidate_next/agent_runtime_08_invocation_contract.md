---
title: Agent Runtime Invocation Contract
status: candidate
layer: T2
canonical_owner: designDoc/agent_runtime_08_invocation_contract.md
parent: designDoc/agent_runtime_00_runtime_domain_contract.md
owned_system_object: provider-neutral Agent invocation and model-visible Context
language: en
reader_persona:
  - Runtime Maintainer
  - Provider Adapter Maintainer
  - Domain Plugin Owner
  - Security Reviewer
---

# Agent Runtime Invocation Contract

**Purpose**: Define how one claimed Attempt becomes an exact model-visible
Context, an admitted provider or tool session, and bounded provider-neutral
observations without giving Invocation authority over retry, Workflow state,
Runtime records, or business data.

**Required reader gain**: A maintainer can assemble the complete Context seen by
the model, admit tool-free and agentic capability modes, compare providers under
the same Module semantics, normalize outputs and failures, and determine which
filesystem, network, Gateway, and provider-session behaviors are enforceable.

## 0. Contract Capsule

```yaml
layer: T2
status: candidate
canonical_owner: designDoc/agent_runtime_08_invocation_contract.md
parent: designDoc/agent_runtime_00_runtime_domain_contract.md
owned_system_object: provider-neutral Agent invocation and model-visible Context
inherits:
  - designDoc/the_charter.md
  - designDoc/the_agent_runtime.md
  - designDoc/the_product_authorization.md
  - designDoc/the_data_governance.md
  - designDoc/the_timestamp_semantic.md
scope:
  - provider-neutral Invocation request, result, and observation protocols
  - deterministic semantic Context assembly and complete delivered-Context capture
  - execution-profile capability enforcement
  - inline, authorized Gateway, and declared workspace delivery
  - tool session, network, environment, timeout, and output bounds
  - provider-native structured-output projection and canonical validation
  - provider context creation, resume, reconstruction, and invalidation
  - provider adapter registration and conformance
non_goals:
  - Module or Workflow semantic identity, release admission, or host selection
  - Attempt claims, retries, Evaluation, Selection, Resolution, or Workflow advancement
  - Product Authorization, Entitlement, governed-data policy, or resource credentials
  - durable scheduling, canonical Ledger writes, inspection presentation, or billing price
outputs:
  - InvocationAdapter
  - InvocationHost
  - InvocationRequest and InvocationResult
  - InvocationObservation family
  - ModelContextEnvelope and provider projection evidence
  - Invocation ResourcePermissionManifest
  - provider adapter conformance evidence
truth_surfaces:
  - designDoc/agent_runtime_08_invocation_contract.md
  - admitted Prompt, Execution Profile, Adapter, and Schema releases
  - code-owned Invocation protocols and adapter registrations
  - canonical Ledger refs to bounded Invocation observations
```

## 1. Owned Result

Invocation performs one bounded external computation for one exact Attempt. It
receives a claimed Attempt, immutable Module and Profile closure, authorized
semantic inputs, and narrow host capabilities. It returns raw output bytes or a
typed failure plus bounded observations. It does not commit the Attempt or
decide whether another Attempt may run.

The same Invocation boundary supports a simple structured model call, an Agent
that rereads and revises its own draft, an Agent using entitlement-filtered
semantic search, and an engineering reviewer with an explicitly authorized
repository and shell. These are distinct Execution Profiles, not prompt-time
permission choices.

## 2. Input and Output Protocol

An `InvocationRequest` binds the exact:

- Runtime Execution, Module Run, Variant, Attempt claim, and protected
  model-call intent, plus Workflow Execution only for Workflow origin;
- Module, Prompt Bundle, input schema, output schema, Behavior Policy,
  Execution Profile, and Adapter release refs and hashes;
- semantic input slots and their delivery plan;
- external authorization and governed-data observation refs required by the
  declared capabilities;
- output byte budget, time budget, provider-turn budget, environment policy,
  network policy, workspace policy, tool policy, and context mode; and
- stale-result fence token used only by Execution and Ledger at finalization.

The request contains no database credential, provider credential, private
Registry row, pricing decision, or canonical Ledger writer.

An `InvocationResult` contains:

- terminal Invocation status and typed failure when present;
- raw output submission or declared output slots;
- bounded provider usage quantities, with unavailable values represented as
  unknown rather than zero;
- bounded model-call and authorized tool-call observations;
- exact provider context disposition;
- delivered Context and provider-native output-constraint evidence refs; and
- bounded diagnostic refs suitable for governed trace storage.

These are observations. Execution decides finalization; Ledger creates
canonical records. Provider-supplied instants are observational evidence only.
Invocation budgets and deadlines use the governed instant and duration forms;
Invocation never assigns the Ledger's authoritative commit instant.

## 3. Semantic Context Assembly

One Runtime formatter assembles a provider-neutral `ModelContextEnvelope` from
registered Prompt Components and the exact semantic input slots. It is the
complete Runtime-authored Context delivered for that invocation, even when an
Adapter maps its parts into provider-native system, user, tool, attachment, or
response-format fields.

The envelope contains only information that can change the Module answer:

1. stable role and task instruction;
2. task-specific semantic policy such as Expertise and Lens content;
3. required semantic inputs in declared order;
4. tool descriptions and usage constraints when the Profile admits tools; and
5. output semantics that are not already carried by an admitted native
   structured-output field.

Runtime-private IDs, hashes, authorization metadata, storage paths, billing
fields, admission decisions, and record-construction instructions remain
outside model-visible Context unless the Module schema declares a semantic ID
whose value changes the answer. Runtime code joins model output back to private
lineage after canonical validation.

`InvocationResult` returns the exact complete envelope and every
provider-native projection as bounded observations and content refs. Execution
submits those observations for commit, Ledger owns their canonical record
family and content class, and Inspection only projects the already-committed
facts. A projection does not display prompt fragments as if they were
independent invocations.

```mermaid
sequenceDiagram
    participant E as Execution
    participant L as Ledger
    participant I as Invocation PEP
    participant A as External Authority port
    participant H as Execution-supplied InvocationHost
    participant P as Provider Adapter
    participant R as Admitted resource
    participant Q as Inspection

    E->>L: commit protected-operation intent
    E->>I: InvocationRequest + exact Profile
    I->>H: declared model-call operation
    H->>A: authorize_protected_operation
    A-->>H: ProtectedOperationAuthorityObservation
    H-->>I: exact authority observation
    I->>P: complete ModelContextEnvelope + admitted capabilities
    P->>I: dynamic operation request
    I->>H: exact dynamic request
    H->>A: authorize_protected_operation
    A-->>H: ProtectedOperationAuthorityObservation
    H-->>I: exact authority observation
    I->>H: accepted request + observation
    H->>L: atomically verify claim and fence; commit exact intent and observation refs
    L-->>H: intent commit receipt
    H->>R: invoke admitted resource
    R-->>H: bounded resource receipt
    H-->>I: bounded operation result
    P-->>I: provider-native projection + bounded observations
    I-->>E: InvocationResult
    E->>L: submit observations and finalization batch
    Q->>L: authorized bounded query
    L-->>Q: already-committed facts and content refs
```

## 4. Semantic Input Delivery

Input delivery and capability access are separate decisions.

| Delivery | Use | Requirement |
| --- | --- | --- |
| Inline | The required semantic closure fits the admitted context budget | Complete required content is embedded in the envelope |
| Authorized Gateway | A corpus is entitlement-specific, mutable at query time, exploratory, or too large to freeze in full | Model receives the required base Context plus narrow query tools; every query is authorized and bounded |
| Managed attachment or read-only mount | A provider interface can enforce exact admitted files and hashes | Only the declared closed package is visible and every byte is content-verified |
| Attempt draft workspace | The Agent must write, reread, and revise its own output during one Attempt | Reads and writes are confined to declared Attempt-local draft paths |

Gateway use does not permit Runtime to omit the Module's required base Context.
The Gateway supplies authorized optional or exploratory content that cannot be
known or embedded safely at assembly time. It is not a second prompt formatter.

Governed content is retrieved through a host-provided enforcing data-access
capability. Its concrete store or service binding is outside stable Runtime
Design Intent. An Agent never
receives a database credential. Local files are permitted only when the exact
Profile and authorization boundary intentionally expose the complete mounted
closure, such as an isolated engineering-review checkout. A shell-capable
Adapter cannot claim per-file enforcement that its sandbox cannot prove.

## 5. Execution Profile Capability Law

The immutable Execution Profile pins every behavior-changing Invocation option:

- provider, model, transport, Adapter release, reasoning level, and provider-turn ceiling;
- timeout, cancellation, output-byte, stdout, stderr, and trace budgets;
- execution mode, semantic-input delivery, context mode, and structured-output mode;
- workspace roots and read/write policy;
- tool identities, operation classes, Gateway policy, and network policy;
- environment allowlist and credential injection mechanism; and
- failure normalization and output normalization releases.

Prompt text cannot grant a tool, network, filesystem, repository, database, or
secret capability. The Adapter fails before invocation when it cannot enforce
the selected Profile. Runtime admits only combinations proven by Adapter
conformance; it does not assume that every provider supports every combination.

The Adapter process receives the minimum environment required for its provider
binding. Ambient worker environment, shell profile, repository root, home
directory, and unrelated credentials are excluded unless each is deliberately
admitted by the Profile and a current decision from the host-provided Product
Authorization validator. Invocation does not import document 09 or Execution
private source to obtain that decision.

Invocation is the Policy Enforcement Point for the workspace, repository,
shell, network, environment, and provider-credential-injection resources that
it owns. It publishes one code-owned `ResourcePermissionManifest` declaring
their resource types, atomic actions, scope forms, conditions, and grant
requirement class. Ordinary internal model calls and authorized reads require
a current decision at this enforcement point. Any Invocation operation that
publishes, externally sends, sensitively exports, trades, or performs an
asynchronous cross-service canonical mutation requires the bounded
`OperationGrant` class defined by Product Authorization. Prompt text and
provider tool metadata cannot change that classification.

## 6. Tool and Gateway Boundary

Execution supplies one request-bound implementation of the Invocation-owned
`InvocationHost` protocol for the active Attempt. This is dependency inversion,
not an Invocation-to-Execution import: Invocation declares and calls the
protocol; Execution implements it from public Ledger and external-authority
ports during composition. Invocation imports neither Execution private source
nor Ledger.

An `InvocationHost` provides only request-bound capabilities:

- read one authorized semantic input handle;
- stage one output body under the Attempt;
- resolve one exact declared dynamic-operation request through document 09's
  `authorize_protected_operation` port and return its exact
  `ProtectedOperationAuthorityObservation`;
- commit its protected-operation intent through the Execution-owned Ledger
  writer while the Attempt claim and durable fence remain active; and
- invoke the admitted resource only after the intent commit succeeds.

For each dynamic tool call, Invocation first checks the exact action, resource,
scope form, condition, and grant class against its own
`ResourcePermissionManifest`. It then asks the request-bound `InvocationHost`
to obtain exactly one `ProtectedOperationAuthorityObservation` through
document 09's `authorize_protected_operation` port. Invocation remains the
Policy Enforcement Point: it applies that observation to its own manifest and
fails closed on denial, mismatch, staleness, or unverifiable evidence.

Invocation returns the exact request and accepted observation to
`InvocationHost`. Execution does not make a second authorization decision; its
host implementation verifies that the observation refs and hashes match the
active execution binding, Attempt, operation, resource, action, scope, and
required grant class. The Ledger intent transaction atomically verifies the
active Attempt claim and current durable fence and commits an operation intent
binding those authority-observation refs, Attempt lineage, action, resource,
input hash, Timestamp-governed deadline and duration budget, and idempotency
identity. Only the commit receipt permits Invocation or its Gateway to enter
the admitted resource callable. The Gateway returns a bounded receipt. Provider
timestamps remain observations and cannot satisfy the governed deadline or
become a Ledger commit instant. A denied call or failed intent commit never
enters the resource implementation.

An Adapter without a reliable before-operation callback may run only a Profile
whose complete composite capability window is authorized in advance and whose
filesystem, network, tool, and output boundaries are enforceable by the
selected sandbox. Provider traces cannot be converted into retroactive
authorization evidence.

## 7. Workspace and Repository Access

An Attempt workspace is a capability boundary and temporary working surface.
Its policy may admit:

- no filesystem access;
- write and reread of the Agent's own Attempt-local drafts;
- a hash-bound read-only input package;
- a deliberately authorized repository checkout with bounded shell access; or
- another explicitly registered and tested workspace class.

Workspace identity includes the Attempt and a random or host-local locator, but
the locator never enters logical Attempt identity or idempotency. Publication
uses atomic marker creation and exclusive lease semantics. Recovery can
distinguish a complete workspace from a partially prepared one. Cleanup follows
the registered retention policy.

A provider session or workspace may accelerate resume. It is never the sole
copy of task state, prior output, or revision history.

## 8. Structured Output and Canonical Validation

The canonical output schema belongs to the Module release. A Profile selects
one declared delivery mode:

- `native_structured_output`: the Adapter projects the canonical schema into
  the provider-native constraint field; or
- `prompt_only_json`: the formatter includes the provider-neutral output shape
  in the model-visible envelope for an explicitly declared Evaluation profile.

The Adapter records the exact submitted provider projection and validates its
inverse-normalized result against the complete canonical Module schema.
Unsupported schema shapes fail before provider invocation. Container maps such
as `properties` and `$defs` are traversed with the shared schema semantics from
Source Architecture; provider projection cannot silently create a competing
schema traversal.

No mode silently falls back to the other. Production profile policy may require
native structured output for an admitted provider. Cross-provider comparison
declares every Provider, Adapter, transport, and constraint-delivery difference
instead of claiming hidden provider Context is byte-identical.

## 9. Context Continuity

Context modes are `stateless`, `create`, `resume`, and `reconstruct`.

Native resume requires equality of the complete registered compatibility key,
including provider, model, Adapter, context type, isolation class, Module and
Prompt closure, authorization scope, and parent Variant. A mismatch invalidates
native resume.

Cross-provider, cross-model, cross-Adapter, cross-authorization, or sibling A/B
work reconstructs from the exact semantic inputs, prior resolved output,
continuity state, and revision packet. It does not copy an opaque provider
workspace. A PM-requested rewrite may continue the same logical task through a
compatible context or a registered reconstruction. Acceptance closes the task
context; later work starts a new execution unless the owning Workflow creates a
new authorized revision request.

## 10. Adapter Registration and A/B

Registry admits Adapter and Execution Profile releases. Invocation resolves the
exact Adapter implementation for the Profile and validates its declared
capabilities before any provider call.

One Module Release may run as sibling Variants under different Profiles,
providers, models, reasoning levels, or Adapter releases. The Module's semantic
Prompt, input schema, output schema, Behavior Policy, and frozen semantic input
closure remain equal when the experiment claims a provider/model comparison.
Each Variant retains its own Attempt, context, workspace, usage, failure, and
output observations.

Changing Module semantics creates a new Module Release or a separately named
experiment. Changing only provider execution configuration creates a new
Profile or Adapter release and Variant, not a duplicate business Workflow.

## 11. Failure Semantics

Expected provider outcomes use bounded classes such as authentication, quota,
rate limit, timeout, transport, cancellation, context limit, tool denial,
schema violation, and provider-declared error. Adapter or Runtime programming
defects remain internal faults and do not become retryable provider failures.

Invocation captures output and diagnostics under streaming or hard byte
budgets. It never buffers unbounded stdout, stderr, tool results, or provider
events before truncation. A parser failure preserves the bounded original
diagnostic ref and returns a typed failed result.

Only Execution applies the registered retry policy. Invocation never changes
provider, model, profile, or Attempt identity on failure.

## 12. Conformance and Completion Evidence

Invocation conformance proves:

1. base Runtime imports without any optional provider SDK or CLI;
2. the complete delivered model Context contains all required semantic content
   and no undeclared Runtime-private metadata;
3. tool-free, Gateway, workspace, repository, network, and shell profiles each
   enforce exactly their declared capability boundary;
4. a denied protected operation never enters the resource callable;
5. environment, output, trace, timeout, process-group, and cleanup bounds hold;
6. native schema projection and inverse normalization preserve canonical
   validation for every admitted schema shape;
7. failures are classified without converting Runtime defects into provider retry;
8. provider sessions and workspaces can be discarded and reconstructed from
   canonical Runtime inputs;
9. sibling provider/model Variants share the declared semantic closure and no
   private context or workspace;
10. real opt-in provider tests record exact provider, model, Adapter, Profile,
    usage availability, failure class, and output-validation evidence;
11. every Invocation-owned protected resource resolves through the admitted
    `ResourcePermissionManifest`, obtains one exact document-09
    `ProtectedOperationAuthorityObservation`, records its refs and hashes in
    the prior intent, and fails closed before the operation on missing, denied,
    stale, mismatched, or unverifiable authority;
12. provider-supplied time cannot become a Ledger commit instant or widen a
    governed deadline or duration budget; and
13. the same Invocation protocol executes both Workflow-origin and independent
    Module-origin Attempts, with Workflow Execution absent from the latter.

## 13. Change Boundary

This contract does not select a default model, approve a current Adapter, or
freeze exact Python fields. After the complete Runtime Design Contract set is
accepted, a Code Design Basis must define the public Invocation protocols,
Profile and Adapter schemas, formatter components, capability matrix,
predecessor Adapter disposition, conformance fixtures, consumer closure, and
rollback before implementation changes.

## References

- [Agent Runtime Charter](../agent_runtime_t0_t1_candidate/the_charter.md)
- [Agent Runtime T0](../agent_runtime_t0_t1_candidate/the_agent_runtime.md)
- [Agent Runtime Domain Contract](../agent_runtime_t0_t1_candidate/agent_runtime_00_runtime_domain_contract.md)
- [Registry Contract](../agent_runtime_t2_candidate/agent_runtime_01_registry_contract.md)
- [Source Architecture](../agent_runtime_t2_candidate/agent_runtime_02_source_architecture_contract.md)
- [Execution Ledger](agent_runtime_04_execution_ledger_contract.md)
- [Product Authorization](../../designDoc/the_product_authorization.md)
- [Data Governance](../../designDoc/the_data_governance.md)
- [Timestamp Semantics](../../designDoc/the_timestamp_semantic.md)
