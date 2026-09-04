# Reviewer Canonical Schema Provider Projection — Code Design Basis

```yaml
CodeDesignBasis:
  status: approved
  approved_decision_ref: user-decision:runtime-canonical-schema-before-portable-t0
  requested_result: >-
    Make every registered Reviewer canonical output schema project deterministically
    into admitted Claude and Codex native structured-output subsets while preserving
    the complete canonical schema as Runtime's final validation authority.
  owning_design_refs:
    - design_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      design_sha256: e9eb813a77ff64d39811ab50993bfe03bfa10ba2bac054fec73fae616f336e76
      code_projection_ref: null
      code_projection_sha256: null
  system_change_plan_step_ref: review_artifacts/runtime_reviewer_schema_projection_system_change_plan.md#adapter_cutover_code_design
  system_change_plan_step_sha256: 71de0a6510584766be028391f83919363813d35eeade0f53988e25d3a4fe83b0
  architecture_disposition: refactor
  primary_flow:
    diagram_type: flowchart
    diagram: |
      flowchart LR
          C[Registered canonical output schema] --> T[Task-plane schema]
          T --> P{Selected Adapter revision}
          P -->|Claude| CP[Claude native projection]
          P -->|Codex| XP[Codex native projection]
          CP --> M[Provider structured output]
          XP --> M
          CP -->|unsupported shape| F[NATIVE_OUTPUT_SCHEMA_PROJECTION_UNSUPPORTED]
          XP -->|unsupported shape| F
          M --> N[Adapter-specific inverse normalization]
          N --> V[Complete canonical Runtime validation]
          V -->|valid| O[Committed Module output]
          V -->|invalid| E[Existing provider output schema violation]
    edges:
      - from: task_plane_schema
        to: provider_native_projection
        interface_id: provider_native_output_schema_project
        error_code: null
      - from: provider_native_projection
        to: provider_invocation
        interface_id: provider_native_output_schema_submit
        error_code: null
      - from: provider_native_projection
        to: projection_failed
        interface_id: null
        error_code: native_output_schema_projection_unsupported
      - from: provider_output
        to: canonical_validation
        interface_id: registered_output_schema_validate
        error_code: null
  interface_contracts:
    - interface_id: provider_native_output_schema_project
      identity_mode: declared
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: invocation_schema_projection
      input: exact registered task-plane schema plus exact provider projection kind
      output: deterministic provider-compatible schema projection
      effects: none
      error_codes: [native_output_schema_projection_unsupported]
    - interface_id: provider_native_output_schema_submit
      identity_mode: referenced
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: claude_module_invocation_or_codex_module_invocation
      input: exact projected schema and selected Adapter revision
      output: provider transport receives that exact projection
      effects: provider invocation may begin only after projection succeeds
      error_codes: [native_output_schema_projection_unsupported]
    - interface_id: registered_output_schema_validate
      identity_mode: referenced
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: claude_module_invocation_or_codex_module_invocation
      input: normalized provider JSON object and complete registered task-plane schema
      output: canonical-valid object eligible for Runtime output commit
      effects: none before normal Runtime finalization
      error_codes: [claude_sdk_output_schema_violation, codex_cli_output_schema_violation]
  error_contracts:
    - error_code: native_output_schema_projection_unsupported
      identity_mode: declared
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: invocation_schema_projection
      condition: >-
        The exact registered task-plane schema cannot be represented by the selected
        Adapter revision's admitted native structured-output subset.
      meaning: no provider-compatible schema was produced and provider invocation did not begin
      caller_action: register a compatible Adapter revision or select an explicitly admitted Profile; never weaken the canonical schema or silently fall back
    - error_code: claude_sdk_output_schema_violation
      identity_mode: referenced
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: claude_module_invocation
      condition: Claude returned an object that fails complete registered schema validation
      meaning: no canonical Module output was committed
      caller_action: return the schema failure to the Module/Adapter owner under the selected retry policy
    - error_code: codex_cli_output_schema_violation
      identity_mode: referenced
      semantic_owner_ref: designDoc/agent_runtime_08_agent_execution_adapter_contract.md
      owner_module_id: codex_module_invocation
      condition: Codex returned an object that fails complete registered schema validation
      meaning: no canonical Module output was committed
      caller_action: return the schema failure to the Module/Adapter owner under the selected retry policy
  logical_modules:
    - module_id: invocation_schema_projection
      responsibility: compile one registered task-plane schema into one exact provider-native schema subset
      owned_resources: [provider_projection_rules]
      public_interfaces: [provider_native_output_schema_project]
      allowed_dependencies: [foundation_schema_traversal]
      prohibited_dependencies: [provider SDK, Runtime Registry state, Reviewer semantics, Portable T0 package]
      failure_and_recovery: [fail before provider invocation on an unsupported shape]
      required_tests: [generic projection tests, five-Reviewer schema matrix, canonical revalidation negative controls]
      future_capabilities: [additional provider projection rules through a new Adapter revision]
      implementation_bindings: [src/agent_runtime/invocation/invocation_schema_projection.py]
      disposition: refactor
    - module_id: invocation_prompt_assembly
      responsibility: assemble provider-neutral prompts and retain Codex inverse normalization only
      owned_resources: [prompt_envelope_formatter]
      public_interfaces: []
      allowed_dependencies: [invocation_schema_projection]
      prohibited_dependencies: [Claude SDK, Reviewer schemas, provider selection]
      failure_and_recovery: [preserve exact canonical schema marker rules]
      required_tests: [prompt-only and native-output marker separation]
      future_capabilities: []
      implementation_bindings: [src/agent_runtime/invocation/invocation_prompt_assembly.py]
      disposition: refactor
    - module_id: claude_module_invocation
      responsibility: submit the Claude projection and canonically validate Claude output
      owned_resources: [claude_adapter_revision]
      public_interfaces: [provider_native_output_schema_submit, registered_output_schema_validate]
      allowed_dependencies: [invocation_schema_projection, Claude Agent SDK]
      prohibited_dependencies: [Codex projection rule, Reviewer-specific schema logic]
      failure_and_recovery: [typed pre-provider projection failure, existing post-provider schema failure]
      required_tests: [captured output_format schema, no-provider-call negative control, live Reviewer smoke]
      future_capabilities: [managed attachment through a separate approved Slice]
      implementation_bindings: [src/agent_runtime/invocation/invocation_claude_module_invocation.py]
      disposition: refactor
    - module_id: codex_module_invocation
      responsibility: submit the Codex projection, inverse-normalize Adapter placeholders, and canonically validate Codex output
      owned_resources: [codex_adapter_revision]
      public_interfaces: [provider_native_output_schema_submit, registered_output_schema_validate]
      allowed_dependencies: [invocation_schema_projection, Codex CLI invocation boundary]
      prohibited_dependencies: [Claude projection rule, Reviewer-specific schema logic]
      failure_and_recovery: [typed pre-provider projection failure, existing post-provider schema failure]
      required_tests: [captured output-schema bytes, no-process-call negative control, live Reviewer smoke]
      future_capabilities: [Codex toolful profiles through a separate approved Slice]
      implementation_bindings: [src/agent_runtime/invocation/invocation_codex_module_invocation.py]
      disposition: refactor
  implementation_slices:
    - slice_id: reviewer_schema_projection_p1
      intended_result: >-
        One canonical projection module produces deterministic Claude and Codex
        schemas for every current Reviewer canonical output schema.
      included_surfaces:
        - src/agent_runtime/invocation/invocation_schema_projection.py
        - src/agent_runtime/invocation/invocation_prompt_assembly.py
        - src/agent_runtime/invocation/invocation_codex_module_invocation.py
        - tests/test_agent_runtime_schema_projection.py
        - tests/test_agent_runtime_native_structured_output.py
        - tests/test_governance_reviewer_schema_projection.py
      excluded_surfaces:
        - Claude and Codex executor behavior
        - Adapter revision values
        - live provider calls
        - Portable T0 and Reviewer Module sources
      deferred_integrations:
        - surface_id: claude_codex_adapter_cutover
          owning_slice_id: reviewer_schema_projection_p2
          completion_gate: both Adapters submit only the new projection and normalize projection failures before provider invocation
        - surface_id: live_transport_matrix
          owning_slice_id: reviewer_schema_projection_p3
          completion_gate: every Reviewer has exact live evidence for every declared compatible transport
      required_tests:
        - finite positional arrays with one common item ref project deterministically
        - heterogeneous positional arrays fail before provider use
        - all five Reviewer schemas project for both providers
        - canonical validation still rejects wrong checklist order and conditional inconsistency
      completion_gate: P1 focused tests pass and no old Codex projection implementation remains in prompt assembly
    - slice_id: reviewer_schema_projection_p2
      intended_result: Claude and Codex Adapters use the shared projection interface and new exact Adapter revisions
      included_surfaces:
        - src/agent_runtime/invocation/invocation_claude_module_invocation.py
        - src/agent_runtime/invocation/invocation_codex_module_invocation.py
        - src/agent_runtime/execution/execution_module_invocation.py
        - tests/test_agent_runtime_native_structured_output.py
        - tests/test_agent_runtime_module_authoring.py
        - tests/test_agent_runtime_managed_design_reviewer.py
        - tests/test_agent_runtime_workflow_authoring.py
        - Adapter fake-transport tests
      excluded_surfaces:
        - live provider matrix
        - Reviewer canonical schema changes
        - Runtime registration releases
      deferred_integrations:
        - surface_id: live_transport_matrix
          owning_slice_id: reviewer_schema_projection_p3
          completion_gate: exact live Claude/Codex results bind P2 bytes
      required_tests:
        - submitted Claude output_format equals the shared Claude projection
        - written Codex output-schema bytes equal the shared Codex projection
        - unsupported schema yields native_output_schema_projection_unsupported and zero provider/process calls
        - complete canonical post-validation remains unchanged
      completion_gate: P2 focused and Runtime invocation regression suites pass on exact bytes
    - slice_id: reviewer_schema_projection_p3
      intended_result: every registered Reviewer transport claim is backed by exact static and live conformance evidence
      included_surfaces:
        - provider-gated Reviewer matrix tests
        - subject-bound conformance evidence
      excluded_surfaces:
        - Portable T0 changes
        - Reviewer prompt/schema migration
        - Runtime registration release mutation
      deferred_integrations: []
      required_tests:
        - five Reviewer schemas through Claude native structured output
        - five Reviewer schemas through Codex native structured output
        - canonical schema applied after each provider result
      completion_gate: every compatible_transport_kind has supported evidence or is returned to its registration owner for removal
  cross_module_seams:
    - registered task-plane schema to provider projection
    - provider projection to exact Adapter revision
    - provider output to Adapter normalization and canonical validation
  migration_and_compatibility:
    - hard-cut the internal codex_native_output_schema implementation into invocation_schema_projection; update every in-repo import and retain no duplicate wrapper
    - bump every Claude and Codex Adapter revision whose native projection bytes change
    - canonical Module schema refs and hashes do not change in Runtime P1-P3
    - prompt_only_json remains an explicitly selected Evaluation Profile and is never a native-mode fallback
    - future Reviewer schema changes rerun the full static matrix before registration and live matrix before compatibility claims
  rollback_boundary: >-
    Revert P1-P2 code and Adapter revision changes together. Existing canonical
    Module schemas and outputs remain unchanged; no persisted Runtime record or
    active pointer is migrated by these Slices.
  acceptance_criteria:
    - one canonical task-plane schema is the sole input to both provider projection rules
    - no provider-specific projection mutates or replaces the registered canonical schema
    - all current Reviewer canonical output schemas project without unsupported provider keywords
    - wrong positional order can pass the weakened provider frame but must fail canonical Runtime validation
    - unsupported positional shapes fail before provider invocation with native_output_schema_projection_unsupported
    - Claude/Codex Adapter revision changes are explicit and every affected Profile fixture is updated
    - P2/P3 introduce no new Runtime-only failure relative to the frozen P1 baseline
    - the three predecessor blocker families are routed to a Runtime baseline-closure successor before Portable T0 work
    - P3 live evidence covers every declared Reviewer compatible transport
    - Runtime returns canonical-valid Reviewer outputs without interpreting owner-specific semantic-validator meaning
    - the exact candidate commit contains only declared Runtime source and test paths; its parent, changed paths, content and test evidence are mechanically recoverable without a separate ChangeSetManifest
  unresolved_decisions: []
```

## Projection Rule

The canonical schema remains the exact registered task-plane schema. Provider projection may remove constraints only
from the provider framing copy. Runtime always validates the returned object against the complete canonical schema.

Finite positional arrays are admitted only when every `prefixItems` entry resolves to one common local item-contract
`$ref` and the canonical tail is `items: false`. The provider framing replaces that positional list with the common
`items` contract and retains `minItems`/`maxItems`; canonical validation continues to enforce position-specific
`check_id` values, order, uniqueness and conditional result rules. A positional array without one common contract fails
before provider invocation rather than projecting to an unconstrained array.

After unsupported composition branches are removed, the provider framing also removes `$defs` entries that are no
longer reachable from the submitted schema. This changes only provider schema bytes; it does not remove a definition
from the canonical schema and therefore requires the Adapter revision updates declared for P2.

Claude and Codex each own an explicit supported-key set. Unknown future keywords fail the projection compiler. The
Adapter never discovers support by sending a candidate schema to a live provider and interpreting the resulting error.
