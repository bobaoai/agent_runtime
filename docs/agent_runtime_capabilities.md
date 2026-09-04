# Agent Runtime capabilities

Agent Runtime is a lightweight, provider-neutral assembler for versioned agentic workflows. It registers immutable Modules and Workflows, binds execution Profiles, runs model and tool adapters, coordinates durable progress, and records inspectable execution facts. Business roles, prompts, authorization policy, credentials, and domain data stay with the host.

The code-owned verification inventory contains 42 capabilities grouped into 9 executable cases. The [operator runbook](agent_runtime_capability_runbook.md) is generated from the same case definitions.

| Case | Evidence source | Capabilities |
| --- | --- | --- |
| `agent_invocation_case` | `executable_owner_case` | `inline_semantic_input`, `structured_output`, `tool_free_execution`, `runtime_hosted_self_test`, `authorized_gateway_read`, `attempt_workspace`, `context_isolation`, `network_enforcement`, `provider_failure_normalization` |
| `durable_backend_case` | `environment_gate` | `durable_backend` |
| `ledger_inspection_case` | `executable_owner_case` | `attempt_and_workflow_ledger`, `usage_truth`, `release_inspection`, `execution_inspection` |
| `live_module_transport_case` | `environment_gate` | `live_provider_adapter`, `registered_module_transport` |
| `module_execution_case` | `executable_owner_case` | `module_run`, `multiple_variants`, `evaluation_and_selection`, `retry_budget`, `idempotent_replay`, `operation_boundary_enforcement`, `cancellation` |
| `module_release_assembly_case` | `executable_owner_case` | `explicit_module_loading`, `path_free_release_identity`, `schema_closure`, `prompt_closure`, `policy_closure`, `profile_independence`, `generic_profile_compatibility`, `registered_module_transport` |
| `persistent_runtime_case` | `environment_gate` | `persistent_registry`, `persistent_ledger`, `persistent_inspection` |
| `public_package_case` | `referenced_peer_result` | `public_package` |
| `workflow_graph_case` | `executable_owner_case` | `graph_authoring`, `sequential_and_branch_routing`, `parallel_fan_out_and_join`, `revision_loop`, `wait_and_external_event`, `crash_recovery`, `portable_workflow_registration`, `per_registry_execution_binding` |

## Verification semantics

- A focused run proves only its selected cases.
- Environment-dependent cases report `not_run` when their explicit PostgreSQL, Temporal, or provider binding is unavailable.
- A rejected negative case may pass when rejection is its declared expected result.
- Provider success, process exit, and subject approval are separate facts.
- Complete verification requires every required case and peer result to bind the same Runtime subject and dependency closure.

Current pass, failure, and environment observations are generated verification evidence. They are intentionally absent from this stable capability catalog.
