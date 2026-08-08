# Whole-Repository Review of Agent Runtime

## Release Decision

Commit `0652c05` remains blocked for release. The independent review
retained 64 findings: 60 confirmed defects and 4 plausible defects
whose mechanisms are present but whose triggers depend on concurrency
or host environment. One candidate was refuted.

The full agent conversations remain in the external workflow store.
This directory keeps only stable findings and verification evidence.

## Remediation Order

1. Repair schema traversal and reference-aware normalization.
2. Converge the durability type families into one public contract.
3. Repair replay identity, terminal events, and snapshot-token binding.
4. Close base-install, public import, inspection, and host-boundary gaps.
5. Re-run clean-wheel, base-install, provider, and real Temporal conformance.

## Fifteen Release Blockers

### 1. `src/agent_runtime/invocation/invocation_schema_projection.py:56`

_validate_strict_schema_keyword_types.walk recurses into the `properties` (and `$defs`) container maps as if they were schema nodes, so any registered output schema containing a property named items/required/properties/additionalProperties/minItems/maxItems/uniqueItems is falsely rejected. [same root cause also at: src/agent_runtime/invocation/invocation_schema_projection.py:42, src/agent_runtime/invocation/invocation_schema_projection.py:15, src/agent_runtime/invocation/invocation_prompt_assembly.py:156, src/agent_runtime/invocation/invocation_claude_module_invocation.py:214]

Failure scenario:

Reproduced: task_plane_output_schema({'type':'object','properties':{'items':{'type':'array','items':{'type':'string'}}},'required':['items']}) raises ValueError 'strict output schema needs type array at #/properties' for a fully valid Draft 2020-12 schema. Because task_plane_output_schema runs inside compile_agent_module_release and inside every executor's validate_registered_output_schema, a module whose output schema has a common property name like 'items' can never be compiled, registered, or executed — every attempt fails with this ValueError.

### 2. `src/agent_runtime/invocation/invocation_prompt_assembly.py:298`

normalize_codex_native_output.normalize() looks up nested property schemas with property_schemas.get(key, {}) without resolving $ref, so for objects defined via $ref to $defs it sees empty required/properties and strips null values from properties that are required-and-nullable in the canonical schema.

Failure scenario:

Reproduced: canonical schema {'properties':{'result':{'$ref':'#/$defs/verdict'}},'required':['result'],'$defs':{'verdict':{'type':'object','properties':{'score':{'type':['integer','null']},'note':{'type':'string'}},'required':['score','note']}}} with correct Codex output {'result':{'score': None,'note':'fine'}} normalizes to {'result':{'note':'fine'}}; the subsequent Draft202012Validator pass fails with "'score' is a required property", so the Codex executor raises a codex_cli_output_schema_violation ModuleExecutorFailure and the attempt is recorded failed even though the provider returned schema-valid output.

### 3. `src/agent_runtime/contracts/durability_topology_definition.py:25`

The durable-topology _ID pattern ^[a-z][a-z0-9_]*$ forbids '-' and '.', while the public host contract (RuntimeWorkflowStartRequest.validate via registry_contract_validation.ID_PATTERN ^[a-z][a-z0-9_.-]{2,159}$) explicitly admits them for workflow_execution_id, so IDs valid at the host boundary fail BackendExecutionRef.validate/ExecutionSnapshot.validate after the backend execution has already started.

Failure scenario:

A host calls DurableExecutionCoordinator.drive with workflow_execution_id 'wf-exec-001' (valid per RuntimeWorkflowStartRequest.validate). TemporalWorkflowReleaseBackendAdapter.start successfully starts the Temporal workflow, then _snapshot_from_payload -> ExecutionSnapshot.validate (and drive's execution.validate at durability_workflow_coordination.py:182) raises ValueError "invalid workflow_execution_id: 'wf-exec-001'". The durable execution is started and billed in Temporal but every subsequent start/query/signal through the adapter fails, leaving a permanently unreachable orphan execution.

### 4. `src/agent_runtime/execution/execution_event_ingestion.py:686`

prepare_ingress bakes the caller-supplied claim_at_utc into the ingress record (recorded_at_utc, hashed into ingress_record_sha256) and then requires full equality with the stored record for idempotent replay, so any retry of the identical request at a later timestamp raises instead of returning the committed record. [same root cause also at: src/agent_runtime/execution/execution_authorization_coordination.py:500]

Failure scenario:

A Product Gateway submits an authorized external event, the response is lost to a network failure, and it retries prepare_ingress with the exact same ExternalEventIngressRequest and evidence but a fresh claim_at_utc (current time). The rebuilt record differs only in recorded_at_utc, so `existing != record` and the call raises ValueError 'external-event ingress idempotency conflict'; the retry path can never claim the wait even though apply() is written to be retry-safe, permanently stalling the waiting workflow transition for that request.

### 5. `src/agent_runtime/execution/execution_event_ingestion.py:640`

prepare_ingress rejects any requested event whose unique workflow edge is terminal (target_node_id is None), so an authorized external decision can never complete a waiting execution, even though project_workflow_release_graph supports terminal transitions via the 'completed' control state.

Failure scenario:

A Workflow Release with a waiting review node whose 'approved' outcome edge is terminal=True (target_node_id None) is registered and admitted; when the Product Gateway submits the authorized approval event, prepare_ingress raises PermissionError('external event has no unique nonterminal graph edge'). The execution stays in 'waiting' forever — the human approval that should finish the workflow is rejected, while the durability layer's graph projection would have accepted the same transition to its 'completed' terminal state.

### 6. `src/agent_runtime/durability/durability_temporal_coordination.py:218`

Shipped Temporal adapters implement the duplicate durability_topology_definition protocol (start/signal/cancel with binding/envelope/graph shapes), not the exported public contract agent_runtime.contracts.DurableBackendAdapter (start/apply_external_event/request_cancellation with the flat StartExecutionRequest and domain_state_id-style ExecutionSnapshot), so no shipped adapter satisfies the published Runtime-owned backend contract. [same root cause also at: src/agent_runtime/__init__.py:94, src/agent_runtime/contracts/durability_backend_definition.py:154]

Failure scenario:

A host codes against the public surface: `from agent_runtime.contracts import DurableBackendAdapter, StartExecutionRequest`, builds the flat canonical StartExecutionRequest and calls TemporalDurableBackendAdapter.start with it — request.validate() passes, then line 257 `request.binding` raises AttributeError and no workflow ever starts; an admission check `isinstance(adapter, DurableBackendAdapter)` returns False and rejects the only shipped backend adapter; and host code reading the contract's snapshot fields (snapshot.domain_state_id, snapshot.transition_sequence) from what the adapter actually returns (topology ExecutionSnapshot with current_state/applied_events) crashes with AttributeError.

### 7. `src/agent_runtime/durability/durability_temporal_coordination.py:358`

TemporalWorkflowReleaseBackendAdapter (the target-release-model backend adapter) forwards no cancel or list_events to the wrapped Temporal client, although the topology DurableBackendAdapter protocol declares both, the sibling legacy adapter forwards them, and the host-facing AgentRuntimeProductHostApi.request_cancellation contract requires a cancellation path. [same root cause also at: src/agent_runtime/durability/durability_temporal_coordination.py:357]

Failure scenario:

An operator submits an authorized RuntimeCancellationRequest for a target-model Workflow Execution running through TemporalWorkflowReleaseBackendAdapter; the host's request_cancellation implementation calls adapter.cancel(execution, reason) and crashes with AttributeError ('TemporalWorkflowReleaseBackendAdapter' object has no attribute 'cancel'), and because isinstance(adapter, DurableBackendAdapter) is also False the adapter cannot be wired where the full backend contract is required — the Temporal execution keeps running and dispatching Modules with no Runtime-owned way to stop it.

### 8. `src/agent_runtime/registry/registry_postgres_persistence.py:291`

Commit 175b379 deleted the predecessor public import surfaces (agent_runtime.postgres, agent_runtime.provider, agent_runtime.review packages, and RuntimeExecutionRecordStore/InMemoryRuntimeExecutionRecordStore exports off agent_runtime.execution) with no compatibility re-export, while the same commit deleted the contract-06 promise that structural initializers 'may temporarily re-export those predecessor symbols for existing callers'; PostgresRuntimeReleaseStore is now reachable only via the full module path agent_runtime.registry.registry_postgres_persistence and is exported from no package __init__.

Failure scenario:

An existing consumer of agent-runtime-core (e.g. the host product this repo was extracted from) upgrades the package; `from agent_runtime.postgres import PostgresRuntimeReleaseStore` or `from agent_runtime.execution import RuntimeExecutionRecordStore` raises ModuleNotFoundError/ImportError at process startup, a hard outage with no deprecation window — and the wheel tests now assert the predecessor packages are absent, so no shim can be added without changing tests.

### 9. `src/agent_runtime/durability/durability_backend_registration.py:26`

TEMPORAL_DESCRIPTOR.implementation_ref points at agent_runtime.testing.durability_temporal_conformance, which imports temporalio at module scope, so resolving the selected backend candidate's implementation ref crashes on a base install without the optional temporal extra. [same root cause also at: tests/test_agent_runtime_temporal_integration.py:13]

Failure scenario:

Confirmed by running the suite in a base environment (no temporalio): resolve_registration_reference(TEMPORAL_DESCRIPTOR.implementation_ref) raises ModuleNotFoundError('temporalio'), and the repo's own mandated packaging test test_clean_wheel_temporal_descriptor_resolves_public_namespace fails (1 failed, 208 passed). Any consumer that resolves the backend candidate's implementation binding (inventory/inspection tooling, admission checks) crashes unless the optional agent-runtime-core[temporal] extra happens to be installed, despite temporal_sdk_available() existing as the gate.

### 10. `src/agent_runtime/inspection/inspection_release_rendering.py:127`

build_runtime_release_inventory calls release_registry.get_admission_state for every release, but register_bundle legally accepts bundles with zero admission records, and get_admission_state raises RuntimeError for such releases.

Failure scenario:

Reproduced: registry.register_bundle(RuntimeReleaseBundle(skill_packages=(package,))) succeeds with no admissions; build_runtime_release_inventory(registry) (and render_runtime_release_markdown) then crashes with RuntimeError('release has no admission record: skill-package:demo_package@candidate_v1'). An operator inspecting a registry containing any not-yet-admitted release gets a stack trace instead of the read-only inventory.

### 11. `src/agent_runtime/invocation/invocation_claude_module_invocation.py:419`

The Claude Gateway executor requires the tool session's definitions tuple to equal profile.tool_policy in exact order, but the ModuleProviderToolSession protocol in invocation_tool_definition.py documents only 'every and only tool exposed' with no ordering precondition.

Failure scenario:

A host implements ModuleProviderToolSessionFactory per the protocol and returns the exact declared tool set sorted alphabetically while the registered Execution Profile lists tool_policy in registration order (ExecutionProfileRelease.validate imposes no sort on tool_policy, unlike gateway_access_reasons). declared_names != profile.tool_policy is an ordered tuple comparison, so every Gateway attempt fails with PermissionError('Gateway tool session differs from the selected Execution Profile') — all Gateway module runs for that profile fail at execution despite a correctly registered, correctly scoped tool surface.

### 12. `src/agent_runtime/invocation/invocation_codex_module_invocation.py:307`

workspace.mkdir(parents=True, exist_ok=False) uses the deterministic attempt_id minted by run_module, so re-executing the same request against a persistent workspace_root raises unguarded FileExistsError before invocation (the Claude adapter maps the same collision to a non-retryable failure). [same root cause also at: src/agent_runtime/invocation/invocation_codex_module_invocation.py:255, src/agent_runtime/invocation/invocation_claude_module_invocation.py:480]

Failure scenario:

run_module derives attempt_id deterministically from the request (_stable_id('module_attempt', variant_id, '1')). A host re-runs an identical evaluation request after an infra crash with a fresh ledger but the same workspace_root: the Codex executor's mkdir raises FileExistsError, which escapes the adapter's _raise_failure taxonomy and is recorded by run_module as a bare 'executor_failure' attempt with no failure detail artifact; the Claude adapter records claude_attempt_workspace_unavailable with retryable=False. Either way the retried evaluation deterministically fails without ever invoking the provider.

### 13. `src/agent_runtime/execution/execution_event_ingestion.py:708`

apply() validates the snapshot token against the event's expectations and the snapshot's domain_state/runtime_status separately, but unlike prepare_ingress it never cross-checks that current_snapshot and current_snapshot_token describe the same snapshot (token.domain_state_id/runtime_status_id/workflow_execution_id vs the snapshot's fields).

Failure scenario:

A coordinator bug passes the correct prepare-time token together with an ExecutionSnapshot from a different (or outdated) execution that happens to be in the same waiting domain state: every check in apply() passes (token matches event expectations; the foreign snapshot matches expected_domain_state and 'waiting'), so an ExternalEventApplicationRecord is committed asserting the target execution's wait was atomically revalidated when it was not — corrupting the sole authoritative application evidence that downstream acknowledge() then binds into the ledger.

### 14. `src/agent_runtime/contracts/execution_host_definition.py:21`

A contracts module imports ExternalEventAcknowledgement/ExternalEventIngressRequest from the execution implementation package, inverting the contracts-implementation boundary that CLAUDE.md requires be kept separate. [same root cause also at: src/agent_runtime/contracts/execution_host_definition.py:21]

Failure scenario:

A product host that imports only the host API contract (agent_runtime.contracts.execution_host_definition) transitively imports agent_runtime.execution and agent_runtime.registry implementation modules (via execution_event_ingestion and its registry_release_registration import); any import-time failure or future optional dependency added to those implementation packages breaks contracts-only consumers with an ImportError at startup, and the AgentRuntimeProductHostApi protocol's submit_external_event signature is now defined by implementation-package types rather than contract-package types.

### 15. `src/agent_runtime/contracts/execution_host_definition.py:36`

_validate_utc ends with `parsed.astimezone(timezone.utc)` whose return value is discarded — a no-op statement — so unlike validate_utc_timestamp used by every other Runtime contract (which requires a trailing Z and a zero offset), the product-host boundary accepts timestamps with any non-UTC offset. [same root cause also at: src/agent_runtime/contracts/execution_host_definition.py:27]

Failure scenario:

A host submits RuntimeWorkflowStartRequest (or RuntimeCancellationRequest / RuntimeExecutionView) with recorded_at_utc="2026-08-08T12:00:00+05:30"; validation passes and the value is durably embedded in immutable start-request payloads, receipts, and canonical hashes even though the field contract and every other record in the system require Z-suffixed UTC. Consumers that assume the uniform grammar break: lexicographic ordering of recorded_at_utc across records no longer matches chronological order, and any component that later revalidates the copied value with validate_utc_timestamp rejects it ("UTC timestamp ending in Z required"), failing an execution that was already admitted.

## Full Verified Finding Index

The machine-readable file contains complete failure scenarios and
verification evidence for every row.

| ID | Verdict | Location | Summary |
| --- | --- | --- | --- |
| `whole_repo_review_001` | `CONFIRMED` | `src/agent_runtime/__init__.py:94` | The package facade exports DurableWorkflowCursor and DurableExecutionCoordinator but binds the name ExternalEvent to the ingress-record type from execution_event_ingestion, while the exported cursor protocol's signal() consumes durability_topology_definition.ExternalEvent (event_id/expected_state/target_state/evidence_ref), which is exported nowhere publicly (contracts.__init__ exports yet a third ExternalEvent from registry_workflow_definition). |
| `whole_repo_review_002` | `CONFIRMED` | `src/agent_runtime/contracts/durability_backend_definition.py:154` | Same-named but shape-incompatible public classes exist in sibling modules: ExecutionSnapshot, StartExecutionRequest, BackendExecutionRef, BackendEvent, DurableBackendAdapter each defined twice (here and durability_topology_definition), ExternalEvent three times, ExternalEventApplicationRecord and OperationGrantBindingRecord twice |
| `whole_repo_review_003` | `CONFIRMED` | `src/agent_runtime/contracts/durability_topology_definition.py:25` | The durable-topology _ID pattern ^[a-z][a-z0-9_]*$ forbids '-' and '.', while the public host contract (RuntimeWorkflowStartRequest.validate via registry_contract_validation.ID_PATTERN ^[a-z][a-z0-9_.-]{2,159}$) explicitly admits them for workflow_execution_id, so IDs valid at the host boundary fail BackendExecutionRef.validate/ExecutionSnapshot.validate after the backend execution has already started. |
| `whole_repo_review_004` | `CONFIRMED` | `src/agent_runtime/contracts/durability_topology_definition.py:67` | PrincipalRole hardcodes product role vocabulary (admin/analyst/viewer) into Runtime contracts, and the PrincipalContext.role field is never read anywhere (TenantRouter.route ignores it) |
| `whole_repo_review_005` | `CONFIRMED` | `src/agent_runtime/contracts/durability_topology_definition.py:847` | WorkflowGraphProjection.allowed_targets calls self.validate() — a full graph traversal plus canonical-JSON serialization and SHA-256 of the whole projection — and a linear state scan on every single lookup |
| `whole_repo_review_006` | `CONFIRMED` | `src/agent_runtime/contracts/execution_host_definition.py:21` | A contracts module imports ExternalEventAcknowledgement/ExternalEventIngressRequest from the execution implementation package, inverting the contracts-implementation boundary that CLAUDE.md requires be kept separate. |
| `whole_repo_review_007` | `CONFIRMED` | `src/agent_runtime/contracts/execution_host_definition.py:21` | A contracts-layer module imports ExternalEventAcknowledgement/ExternalEventIngressRequest from the execution implementation package (execution_event_ingestion), inverting the contracts-to-implementation dependency direction |
| `whole_repo_review_008` | `CONFIRMED` | `src/agent_runtime/contracts/execution_host_definition.py:27` | _validate_utc re-implements the shared validate_utc_timestamp helper (already imported from registry_contract_validation) with weaker semantics that accept non-UTC offsets, plus a dead `parsed.astimezone(timezone.utc)` statement whose result is discarded |
| `whole_repo_review_009` | `CONFIRMED` | `src/agent_runtime/contracts/execution_host_definition.py:36` | _validate_utc ends with `parsed.astimezone(timezone.utc)` whose return value is discarded — a no-op statement — so unlike validate_utc_timestamp used by every other Runtime contract (which requires a trailing Z and a zero offset), the product-host boundary accepts timestamps with any non-UTC offset. |
| `whole_repo_review_010` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_lineage_definition.py:245` | ModuleAttemptRecord.validate — the target-model rewrite of the legacy WorkflowAttemptRecord — validates period_start_at_utc, period_end_at_utc, and recorded_at_utc only individually and dropped the legacy record's interval guard (ledger_record_definition.py:530-537 raises when period_end precedes period_start). |
| `whole_repo_review_011` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_record_definition.py:86` | Private _validate_id/_validate_sha/_validate_ref/_validate_utc and the _ID/_SHA256/_OPAQUE_REF/_SECRET_MARKERS constants re-implement validate_id/validate_sha256/validate_opaque_ref/validate_utc_timestamp and the identical patterns already exported by contracts/registry_contract_validation.py |
| `whole_repo_review_012` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_record_definition.py:111` | _validate_utc parses timestamps with datetime.fromisoformat but every consumer that needs the value (deadline_at, expires_at, lease checks at lines 482, 530-535, 962-967, 1047-1052, 1244) re-parses the identical string again after validate() already parsed it |
| `whole_repo_review_013` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_record_definition.py:842` | The content-addressing pattern (_identity_payload popping the hash field + build() constructing a provisional record with '0'*64 then rebuilding with sha256_json) is copy-pasted 13 times in this file and 5 more times as _payload/build in execution_event_ingestion.py |
| `whole_repo_review_014` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_record_definition.py:1684` | ContextBinding.validate and ContextBinding.as_dict each carry two stacked docstrings copy-pasted from EvaluationCoverageBinding: the effective docstring describes the wrong record ('candidate, evaluator, and result edge') and the correct description is a dead string statement (also line 1700-1703) |
| `whole_repo_review_015` | `CONFIRMED` | `src/agent_runtime/contracts/ledger_record_definition.py:2690` | RuntimeRecordBatch.as_dict validates every record via runtime_record_as_dict inside self.validate(), then serializes each record again in the comprehension; transaction_sha256 re-invokes as_dict, and AttemptFinalizationBatch.validate/as_record_batch each recompute _canonical_output_bundle (lines 3020, 3026) |
| `whole_repo_review_016` | `CONFIRMED` | `src/agent_runtime/contracts/registry_release_definition.py:60` | _validate_utc is byte-identical to validate_utc_timestamp in registry_contract_validation.py, which this module already imports seven other validators from |
| `whole_repo_review_017` | `CONFIRMED` | `src/agent_runtime/durability/durability_backend_registration.py:26` | TEMPORAL_DESCRIPTOR.implementation_ref points at agent_runtime.testing.durability_temporal_conformance, which imports temporalio at module scope, so resolving the selected backend candidate's implementation ref crashes on a base install without the optional temporal extra. |
| `whole_repo_review_018` | `CONFIRMED` | `src/agent_runtime/durability/durability_temporal_coordination.py:218` | Shipped Temporal adapters implement the duplicate durability_topology_definition protocol (start/signal/cancel with binding/envelope/graph shapes), not the exported public contract agent_runtime.contracts.DurableBackendAdapter (start/apply_external_event/request_cancellation with the flat StartExecutionRequest and domain_state_id-style ExecutionSnapshot), so no shipped adapter satisfies the published Runtime-owned backend contract. |
| `whole_repo_review_020` | `CONFIRMED` | `src/agent_runtime/durability/durability_temporal_coordination.py:357` | TemporalWorkflowReleaseBackendAdapter copy-pastes ~90 lines from TemporalDurableBackendAdapter (__post_init__, build_worker, _validate_execution, signal, query, recover are verbatim duplicates); only start() differs |
| `whole_repo_review_021` | `CONFIRMED` | `src/agent_runtime/durability/durability_temporal_coordination.py:358` | TemporalWorkflowReleaseBackendAdapter (the target-release-model backend adapter) forwards no cancel or list_events to the wrapped Temporal client, although the topology DurableBackendAdapter protocol declares both, the sibling legacy adapter forwards them, and the host-facing AgentRuntimeProductHostApi.request_cancellation contract requires a cancellation path. |
| `whole_repo_review_022` | `CONFIRMED` | `src/agent_runtime/durability/durability_workflow_coordination.py:333` | _validate_snapshot re-runs graph.validate() (full canonical-JSON hash of the immutable projection) and execution.validate() on every dispatch iteration of drive(), although both objects were validated once at drive() entry and never change |
| `whole_repo_review_023` | `PLAUSIBLE` | `src/agent_runtime/execution/execution_authorization_coordination.py:53` | InMemoryExecutionAuthorizationLedger (and its sibling InMemoryAuthorizationLedger in execution_operation_authorization.py:60) performs check-then-insert duplicate/conflict detection (commit_binding, commit_fence, commit_intent, commit_observation) over plain dicts with no lock, while every other in-memory store in the package (InMemoryModuleExecutionLedger, InMemoryCellArtifactStore, InMemoryRuntimeExecutionRecordStore, RuntimeReleaseRegistry) guards the same pattern with an RLock. |
| `whole_repo_review_024` | `CONFIRMED` | `src/agent_runtime/execution/execution_authorization_coordination.py:500` | Fence/binding records embed a fresh clock timestamp under a timestamp-free ref, so retrying revalidate() or bind_execution_context() with identical inputs raises a ref-conflict ValueError instead of returning the duplicate-safe result the ledger promises. |
| `whole_repo_review_025` | `CONFIRMED` | `src/agent_runtime/execution/execution_content_staging.py:157` | record_execution_output spells out hashlib.sha256(schema_ref.encode("utf-8")).hexdigest() while the module's own _sha256 helper exists and is used for the identical operation at line 235 |
| `whole_repo_review_026` | `CONFIRMED` | `src/agent_runtime/execution/execution_content_staging.py:203` | commit_output (and commit_failure_detail at line 239) builds the artifact idempotency key by underscore-joining module_run_id, variant_id, attempt_id, and logical_name, but the runtime ID grammar ([a-z][a-z0-9_.-]{2,159}) allows underscores inside each component, so distinct lineage tuples can join to the identical key — the ambiguous-delimiter pitfall the file's own _stable_id avoids with "\x1f" joins. |
| `whole_repo_review_027` | `CONFIRMED` | `src/agent_runtime/execution/execution_event_ingestion.py:640` | prepare_ingress rejects any requested event whose unique workflow edge is terminal (target_node_id is None), so an authorized external decision can never complete a waiting execution, even though project_workflow_release_graph supports terminal transitions via the 'completed' control state. |
| `whole_repo_review_028` | `CONFIRMED` | `src/agent_runtime/execution/execution_event_ingestion.py:662` | Unreachable sentinel fallback `snapshot.wait_policy_ref or "wait-policy:missing"` when building an immutable ExternalEvent — _validate_prepare_closure (line 859-860) has already rejected a None wait_policy_ref before this line runs |
| `whole_repo_review_029` | `CONFIRMED` | `src/agent_runtime/execution/execution_event_ingestion.py:686` | prepare_ingress bakes the caller-supplied claim_at_utc into the ingress record (recorded_at_utc, hashed into ingress_record_sha256) and then requires full equality with the stored record for idempotent replay, so any retry of the identical request at a later timestamp raises instead of returning the committed record. |
| `whole_repo_review_030` | `CONFIRMED` | `src/agent_runtime/execution/execution_event_ingestion.py:708` | apply() validates the snapshot token against the event's expectations and the snapshot's domain_state/runtime_status separately, but unlike prepare_ingress it never cross-checks that current_snapshot and current_snapshot_token describe the same snapshot (token.domain_state_id/runtime_status_id/workflow_execution_id vs the snapshot's fields). |
| `whole_repo_review_031` | `CONFIRMED` | `src/agent_runtime/execution/execution_module_invocation.py:59` | _stable_id is the fifth identical private copy (also execution_event_ingestion.py:51, execution_content_staging.py:31, execution_operation_authorization.py:29, execution_authorization_coordination.py:37) of the public stable_runtime_id helper in contracts/ledger_record_definition.py:54, minus its prefix validation |
| `whole_repo_review_032` | `CONFIRMED` | `src/agent_runtime/execution/execution_operation_authorization.py:25` | _utc_now and _as_datetime are copy-pasted across three execution modules (also execution_authorization_coordination.py:28/32, execution_module_invocation.py:80, execution_event_ingestion.py:56) with drift: this module's _as_datetime skips validation entirely and execution_module_invocation's _utc_now adds a microseconds timespec |
| `whole_repo_review_033` | `CONFIRMED` | `src/agent_runtime/inspection/inspection_architecture_rendering.py:38` | Commit 0652c05 removed the always-on repository audit from build_runtime_architecture_projection(): the no-argument path now runs only registry-internal validation, dropping the file-presence, unregistered-file, and owner-contract checks that the previous default (project_root = parents[3]) enforced for source checkouts. |
| `whole_repo_review_034` | `CONFIRMED` | `src/agent_runtime/inspection/inspection_release_rendering.py:127` | build_runtime_release_inventory calls release_registry.get_admission_state for every release, but register_bundle legally accepts bundles with zero admission records, and get_admission_state raises RuntimeError for such releases. |
| `whole_repo_review_035` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_claude_module_invocation.py:214` | _structured_output_format.project has the same homogeneous container recursion: it deletes keys in {allOf, anyOf, oneOf, if, then, else} from every dict including `properties` maps, so a property named after any composition keyword vanishes from the provider structured-output framing schema sent to the Claude SDK. |
| `whole_repo_review_036` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_claude_module_invocation.py:419` | The Claude Gateway executor requires the tool session's definitions tuple to equal profile.tool_policy in exact order, but the ModuleProviderToolSession protocol in invocation_tool_definition.py documents only 'every and only tool exposed' with no ordering precondition. |
| `whole_repo_review_037` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_claude_module_invocation.py:480` | Per-attempt workspace directories (workspace_root / attempt_id) are created with exist_ok=False in both Claude and Codex executors and never removed after the attempt completes or fails |
| `whole_repo_review_038` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_claude_module_invocation.py:638` | Committed output round-trips through JSON three times: _canonical_output parses provider text and serializes to bytes, then json.loads(canonical_output) immediately re-parses those bytes for schema validation (same pattern in the Codex adapter at lines 450-461) |
| `whole_repo_review_039` | `PLAUSIBLE` | `src/agent_runtime/invocation/invocation_codex_module_invocation.py:82` | _default_invoke calls subprocess.run(argv, input=prompt, text=True, ...) without encoding="utf-8", so prompt encoding and stdout/stderr decoding use locale.getpreferredencoding(), unlike every other byte boundary in the adapters which pins UTF-8 (prompt bytes are validated as exact UTF-8 at line 318-321 before being handed to the locale-dependent pipe). |
| `whole_repo_review_040` | `PLAUSIBLE` | `src/agent_runtime/invocation/invocation_codex_module_invocation.py:118` | _parse_usage overwrites input/output/cache token counts with each successive JSONL event that carries a usage object (last-event-wins) instead of summing, so multi-turn Codex agent-workspace runs record only the final turn's usage if the CLI reports per-turn usage. |
| `whole_repo_review_041` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_codex_module_invocation.py:172` | _failure_detail_bytes is a byte-identical copy of the same function in invocation_claude_module_invocation.py:229, along with duplicated _sha256 helpers and _FAILURE_RESPONSE_MAX_BYTES constants |
| `whole_repo_review_042` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_codex_module_invocation.py:255` | Codex execute() repeats the same ~50-line profile/module compatibility gauntlet as the Claude executor (module kind, adapter id/revision, transport, execution/delivery/workspace/network policy, prompt-bundle and schema resolution) with copy-paste drift in workspace creation |
| `whole_repo_review_043` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_codex_module_invocation.py:307` | workspace.mkdir(parents=True, exist_ok=False) uses the deterministic attempt_id minted by run_module, so re-executing the same request against a persistent workspace_root raises unguarded FileExistsError before invocation (the Claude adapter maps the same collision to a non-retryable failure). |
| `whole_repo_review_044` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_prompt_assembly.py:156` | codex_native_output_schema.project filters _CODEX_NATIVE_UNSUPPORTED_SCHEMA_KEYS homogeneously into the `properties` and `$defs` container maps, so an object property named "not", "if", "then", "else", "allOf", "uniqueItems", "dependentRequired", or "dependentSchemas" is silently deleted from both the projected properties and the recomputed required list. |
| `whole_repo_review_045` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_prompt_assembly.py:298` | normalize_codex_native_output.normalize() looks up nested property schemas with property_schemas.get(key, {}) without resolving $ref, so for objects defined via $ref to $defs it sees empty required/properties and strips null values from properties that are required-and-nullable in the canonical schema. |
| `whole_repo_review_046` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_schema_projection.py:15` | task_plane_output_schema.project strips the hidden annotation keys ($schema, $id, $comment, title) homogeneously from every dict, including the `properties` container maps, so a schema property literally named "title" (or "$id"/"$comment"/"$schema") is silently deleted from the projection while `required` still lists it. |
| `whole_repo_review_047` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_schema_projection.py:42` | _validate_strict_schema_keyword_types.walk recurses homogeneously into every dict, so the `properties` (and `$defs`) container mappings are themselves checked as schema nodes, and any property NAME that equals a schema keyword (items, required, properties, additionalProperties, minItems, maxItems, uniqueItems) triggers a spurious type-object/type-array ValueError. |
| `whole_repo_review_048` | `CONFIRMED` | `src/agent_runtime/invocation/invocation_schema_projection.py:56` | _validate_strict_schema_keyword_types.walk recurses into the `properties` (and `$defs`) container maps as if they were schema nodes, so any registered output schema containing a property named items/required/properties/additionalProperties/minItems/maxItems/uniqueItems is falsely rejected. |
| `whole_repo_review_049` | `CONFIRMED` | `src/agent_runtime/ledger/ledger_record_persistence.py:369` | Every commit revalidates the entire accumulated execution trace: _validate_candidate iterates existing + batch.records, rebuilding all unique-maps and cross-record lineage joins from scratch |
| `whole_repo_review_050` | `CONFIRMED` | `src/agent_runtime/ledger/ledger_usage_aggregation.py:36` | complete_optional_float_sum is a copy of complete_optional_int_sum differing only in the cast; one parametrized helper covers both |
| `whole_repo_review_051` | `CONFIRMED` | `src/agent_runtime/ledger/ledger_usage_aggregation.py:45` | complete_optional_float_sum aggregates the dollar-amount fields estimated_cost_usd and provider_charge_usd with binary-float sum(float(...)) — the classic float-for-money pitfall. |
| `whole_repo_review_052` | `CONFIRMED` | `src/agent_runtime/registry/registry_module_exporting.py:281` | load_skill_runtime_module_exports builds and lists project_root/.claude/skills/<skill_id>/runtime_modules before any skill_id validation — the _SKILL_ID_PATTERN guard lives only in load_runtime_module_export, which runs after root.is_dir() and root.iterdir() have already executed on the unvalidated path. |
| `whole_repo_review_053` | `CONFIRMED` | `src/agent_runtime/registry/registry_postgres_persistence.py:1` | The 600+ line PostgreSQL release-store implementation was moved/renamed from the deleted agent_runtime.postgres package into registry/ without any focused test being re-established: no test module in the repo imports PostgresRuntimeReleaseStore or registry_postgres_persistence, violating the CLAUDE.md rule that every contract change gets focused tests. |
| `whole_repo_review_054` | `CONFIRMED` | `src/agent_runtime/registry/registry_postgres_persistence.py:44` | Eleventh private copy of the canonical-JSON-to-SHA-256 helper (_canonical_sha256/_canonical_hash/_canonical_payload_sha256 in execution_operation_definition, execution_module_definition, execution_authorization_definition, registry_package_definition, registry_workflow_definition, registry_release_definition, durability_topology_definition, durability_temporal_coordination, execution_event_ingestion, execution_module_invocation) while public sha256_json/canonical_json are exported by contracts/ledger_record_definition |
| `whole_repo_review_055` | `CONFIRMED` | `src/agent_runtime/registry/registry_postgres_persistence.py:291` | Commit 175b379 deleted the predecessor public import surfaces (agent_runtime.postgres, agent_runtime.provider, agent_runtime.review packages, and RuntimeExecutionRecordStore/InMemoryRuntimeExecutionRecordStore exports off agent_runtime.execution) with no compatibility re-export, while the same commit deleted the contract-06 promise that structural initializers 'may temporarily re-export those predecessor symbols for existing callers'; PostgresRuntimeReleaseStore is now reachable only via the full module path agent_runtime.registry.registry_postgres_persistence and is exported from no package __init__. |
| `whole_repo_review_056` | `CONFIRMED` | `src/agent_runtime/registry/registry_postgres_persistence.py:444` | _write_release_registry re-serializes and re-INSERTs every release, export, node, edge, and admission row in the whole registry on each register_bundle call, and each child row calls as_dict() twice (row_sha256 + payload in serialize_registry_tables) |
| `whole_repo_review_057` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_compilation.py:38` | sha256_text duplicates the identically named public helper already exported by contracts/ledger_record_definition.py (and imported from there by ledger_record_persistence.py) |
| `whole_repo_review_058` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_compilation.py:63` | managed_skill_projection sniffs three legacy heading markers in sequence ('## Task Instructions' → '## Managed Runtime Contract' → '## Managed Execution Boundary') to find the managed projection inside a Skill file |
| `whole_repo_review_059` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_compilation.py:263` | For fixed-prompt Modules, compile_agent_module_release calls load_skill_runtime_module_exports — reading and validating every module directory and prompt in the Skill Package — just to locate the one target module, after managed_skill_projection already read the same prompt file |
| `whole_repo_review_060` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_compilation.py:308` | The both-or-neither schema-path guard uses truthiness (any()/all(), treating "" as absent) while the executing branch at line 314 tests `is not None`, and line 324 coerces None with `or ""` — the classic falsy-empty-string vs None mismatch. |
| `whole_repo_review_061` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_registration.py:341` | assert_workflow_execution_allowed duplicates the entire purpose-to-allowed-admission-states matrix from assert_module_execution_allowed (lines 293-339); only the extra STANDALONE entry-policy check differs |
| `whole_repo_review_062` | `PLAUSIBLE` | `src/agent_runtime/registry/registry_release_registration.py:438` | _replace_with publishes a registration by reassigning 11 dict attributes one-by-one while all read paths (get_module, get_admission_state, _get_exact, active_release_ref) read those attributes without acquiring the registration lock, so a concurrent reader can observe a torn registry state. |
| `whole_repo_review_063` | `CONFIRMED` | `src/agent_runtime/registry/registry_release_registration.py:471` | _register_release detects duplicate registrations by fully serializing both records (existing.as_dict() != record.as_dict()) instead of comparing the frozen dataclasses or their release_sha256 fields |
| `whole_repo_review_064` | `CONFIRMED` | `tests/test_agent_runtime_temporal_integration.py:13` | Module-level 'from temporalio.api.history.v1 import History' has no importorskip guard, so pytest collection errors (not skips) in any environment without the optional temporal extra. |
| `whole_repo_review_065` | `CONFIRMED` | `tools/build_agent_runtime_design_contract_bundle.py:21` | CANONICAL_DOCUMENTS excludes contracts 02, 05, and 10 from the packaged Design Contract bundle, but the bundle still ships documents that reference them 13 times, including relative markdown links in the packaged the_agent_runtime.md contract-index table ([agent_runtime_02], [agent_runtime_05], [agent_runtime_10]) that resolve inside design_contract/ where the files do not exist. |

## Refuted Candidate

- `src/agent_runtime/durability/durability_temporal_coordination.py:331`: TemporalDurableBackendAdapter.cancel calls `await handle.cancel(reason=reason)`, but temporalio's WorkflowHandle.cancel() accepts no `reason` parameter (only rpc_metadata/rpc_timeout; `reason` exists on terminate()), so the call raises TypeError.
