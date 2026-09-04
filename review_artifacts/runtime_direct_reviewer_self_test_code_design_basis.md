# Direct managed Reviewer self-test CodeDesignBasis

Status: approved for implementation under the user's explicit instruction to
execute the single-Profile path and directly close valid registered review
findings.

## Intended result

Run one exact registered Test/Evaluation Module and provider Profile through the
existing Module kernel with an explicit trusted Runtime test authority. The
test authority binds request, execution scope, Module, Profile, input closure,
resource scope and allowed operations by hash. It contains no Product
principal, tenant, Entitlement, decision, grant or credential field.

## Primary flow

```text
registered Module and Profile
  -> exact ModuleExecutionRequest
  -> trusted test_resource_factory
  -> RuntimeTestExecutionAuthority
  -> canonical Adapter request with exact test-binding pair
  -> Invocation preflight validates that pair through the request-bound host
  -> provider and declared test operations
  -> host revalidation and atomic finalization callback
  -> existing ModuleRunResult and Attempt evidence
```

The external path continues to use `ModuleExecutionAuthority`. Exactly one of
the external authority and Runtime test authority may be present.

## Interfaces and ownership

- Invocation contract owns `RuntimeTestOperationIntent`,
  `RuntimeTestOperationReceipt`, `RuntimeTestExecutionHost`, and the paired
  `test_execution_binding_ref` and `test_execution_binding_sha256` request
  fields.
- Execution owns `RuntimeTestExecutionAuthority`, request/profile/resource
  closure checks, dynamic operation dispatch and finalization ordering.
- The existing evaluation helper accepts either the existing external
  `authority_factory` or an explicit trusted `test_resource_factory`. It invokes
  the resource factory before constructing the Adapter so one exact resource
  set supplies both authority and provider tools.
- Claude Gateway dispatches external and test intents by exact type. Codex and
  every existing external request retain their previous path and identity.

## Failure and compatibility

- Missing, mixed, partial, hash-mismatched or cross-Attempt test bindings fail
  before provider or resource effect.
- A test operation must be declared by both Module and Profile, and its receipt
  must bind the exact intent, payload, resource, action, idempotency key and test
  binding.
- Legacy Adapter-request hashes omit only the two new keys when both are null;
  every previous key and null remains in the existing identity domain.
- Resource revalidation runs before provider entry and inside the host's
  finalization callback. The trusted host owns any concrete lock or temporary
  resource lifecycle.
- Provider output, usage, trace, schema validation, retry and Module Ledger
  behavior remain unchanged.

## Exact implementation and tests

Implementation paths:

- `src/agent_runtime/contracts/invocation_adapter_definition.py`
- `src/agent_runtime/invocation/invocation_context_preparation.py`
- `src/agent_runtime/invocation/invocation_tool_definition.py`
- `src/agent_runtime/invocation/invocation_claude_module_invocation.py`
- `src/agent_runtime/invocation/invocation_codex_module_invocation.py`
- `src/agent_runtime/execution/execution_module_invocation.py`
- `src/agent_runtime/testing/execution_module_evaluation.py`

Tests cover legacy external requests, missing/mixed evidence, exact intent hash,
test-resource factory ordering, Gateway provider entry, declared operation and
receipt closure, architecture, wheel content, PostgreSQL, Temporal and all live
provider paths. The exact registered Reviewer remains the final integration
gate.

Rollback reverts these request fields, intent/receipt types and the explicit
test-authority branch together. It changes no Runtime Registry row, persistent
schema, active pointer or external Product authority record.
