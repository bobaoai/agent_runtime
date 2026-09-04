# Agent Runtime capability runbook

Each section below is generated from the same code-owned `AgentCapabilityTestCase` that drives verification. Run commands from the Agent Runtime repository root. Supply only the explicit environment binding named by the case.

## `agent_invocation_case`

Evidence source: `executable_owner_case`

Capabilities: `inline_semantic_input`, `structured_output`, `tool_free_execution`, `runtime_hosted_self_test`, `authorized_gateway_read`, `attempt_workspace`, `context_isolation`, `network_enforcement`, `provider_failure_normalization`

Prerequisites:

- local Runtime checkout

Command:

```sh
python -m pytest -q tests/test_agent_runtime_native_structured_output.py tests/test_agent_runtime_public_adapter_contracts.py tests/test_agent_runtime_attempt_workspace.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `durable_backend_case`

Evidence source: `environment_gate`

Capabilities: `durable_backend`

Prerequisites:

- explicit host-provided durable backend test binding

Command:

```sh
python -m pytest -q tests/test_agent_runtime_temporal_integration.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `ledger_inspection_case`

Evidence source: `executable_owner_case`

Capabilities: `attempt_and_workflow_ledger`, `usage_truth`, `release_inspection`, `execution_inspection`

Prerequisites:

- local Runtime checkout

Command:

```sh
python -m pytest -q tests/test_agent_runtime_execution_inspection.py tests/test_agent_runtime_release_inspection.py tests/test_workflow_execution_ledger_recording.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `live_module_transport_case`

Evidence source: `environment_gate`

Capabilities: `live_provider_adapter`, `registered_module_transport`

Prerequisites:

- exact registered Module Profile Variant and provider binding

Command:

```sh
python -m pytest -q tests/test_agent_runtime_native_structured_output.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `module_execution_case`

Evidence source: `executable_owner_case`

Capabilities: `module_run`, `multiple_variants`, `evaluation_and_selection`, `retry_budget`, `idempotent_replay`, `operation_boundary_enforcement`, `cancellation`

Prerequisites:

- local Runtime checkout

Command:

```sh
python -m pytest -q tests/test_agent_runtime_execution_records.py tests/test_agent_runtime_execution_authorization.py tests/test_agent_runtime_module_evaluation.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `module_release_assembly_case`

Evidence source: `executable_owner_case`

Capabilities: `explicit_module_loading`, `path_free_release_identity`, `schema_closure`, `prompt_closure`, `policy_closure`, `profile_independence`, `generic_profile_compatibility`, `registered_module_transport`

Prerequisites:

- local Runtime checkout

Command:

```sh
python -m pytest -q tests/test_agent_runtime_module_authoring.py tests/test_agent_runtime_registry_candidate_compilation.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `persistent_runtime_case`

Evidence source: `environment_gate`

Capabilities: `persistent_registry`, `persistent_ledger`, `persistent_inspection`

Prerequisites:

- explicit host-provided PostgreSQL test namespace

Command:

```sh
python -m pytest -q tests/test_agent_runtime_postgres_release_store.py tests/test_agent_runtime_postgres_execution_ledger.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `public_package_case`

Evidence source: `referenced_peer_result`

Capabilities: `public_package`

Prerequisites:

- clean isolated package environment

Command:

```sh
python -m pytest -q tests/test_agent_runtime_packaging_boundary.py tests/test_agent_runtime_conformance_package.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes

## `workflow_graph_case`

Evidence source: `executable_owner_case`

Capabilities: `graph_authoring`, `sequential_and_branch_routing`, `parallel_fan_out_and_join`, `revision_loop`, `wait_and_external_event`, `crash_recovery`, `portable_workflow_registration`, `per_registry_execution_binding`

Prerequisites:

- local Runtime checkout

Command:

```sh
python -m pytest -q tests/test_agent_runtime_workflow_authoring.py tests/test_agent_runtime_parallel_workflow.py tests/test_agent_runtime_product_host_execution_api.py
```

Expected result:

- command exits zero or records the declared not_run environment state
- every capability observation binds the exact Runtime subject

Evidence:

- bounded test result
- capability evidence refs and hashes

Cleanup:

- remove only resources created by this test case

Failure routing:

- harness failure returns Agent Capability Verification owner
- Runtime capability failure retains its owning Design and error code

Rerun when:

- rerun when Runtime subject or suite hash changes
- rerun when dependency or environment binding changes
