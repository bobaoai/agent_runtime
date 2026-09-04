# SystemChangePlan: Reviewer Execution and Agent Capability Verification Completion

## Requested Result

Make the registered `engineering_change_reviewer@v4` executable through exact,
provider-specific Runtime profiles without changing its provider-neutral review
semantics, then complete the Slice 11B capability inventory and Slice 11C
verification runner delegated by
`agent_runtime_11_agent_capability_verification.md`. The completed verification
surface must prove each declared Reviewer transport against the same frozen
review input and preserve `supported`, `unsupported`, `failed`, and `not_run`
as distinct evidence. The canonical Example Workflow remains the separate
Slice 11D successor after the runner produces subject-bound results.

## Frozen Upstream Prerequisites

- `designDoc/the_agent_runtime.md` remains the portable Runtime T0 authority.
- `designDoc/agent_runtime_00_execution_charter.md` remains the Runtime T1 root.
- `designDoc/agent_runtime_01_module_contract_and_assembly.md` continues to own
  immutable release registration and exact Profile and Variant bindings.
- `designDoc/agent_runtime_08_agent_execution_adapter_contract.md` continues to
  own provider Adapter capability translation and pre-provider rejection.
- `designDoc/agent_runtime_11_agent_capability_verification.md` continues to own
  capability inventory, executable cases, aggregate verification, and runbook
  projection.
- `review_artifacts/agent_capability_verification_slice_11a_code_design.md` and
  commit `4622ff2` freeze the completed case-definition and runbook-projection
  slice. This plan does not rewrite Slice 11A.
- The Runtime PostgreSQL store has registered
  `runtime-module:engineering_change_reviewer@v4` with SHA-256
  `6524f5f8f637da1adbe0786c410ffb92dc700c672b5bbf5b5fa367507484645c`.
  It currently has no Execution Profile or Variant Policy binding.

## Affected Surfaces

| Surface | Class | Layer | Accountable owner | Required change | Reason |
| --- | --- | --- | --- | --- | --- |
| `designDoc/agent_runtime_08_agent_execution_adapter_contract.md` | Design | T2 | Agent Execution Adapter owner | Clarify how provider-native repository read, search, and sandbox command capabilities are represented, admitted, and rejected for workspace-capable Adapters | The Codex workspace Adapter currently requires an empty `tool_policy`, while the registered Reviewer declares repository and command operations |
| `designDoc/agent_runtime_11_agent_capability_verification.md` | Design | T2 | Agent Capability Verification owner | Bind registered Reviewer transport verification to exact Module, Profile, Variant, Adapter, input, output, and environment evidence without embedding current inventory rows | The current contract requires live provider evidence but does not fully close the supported-versus-unsupported result boundary for one multi-transport Reviewer |
| `review_artifacts/agent_capability_verification_slice_11b_code_design.md` | Code | Code | Agent Capability Verification implementation owner | Define the code-owned capability inventory and exact Reviewer transport case set | Slice 11A deliberately excluded inventory rows |
| `review_artifacts/agent_capability_verification_slice_11c_code_design.md` | Code | Code | Agent Capability Verification implementation owner | Define runner, environment inspection, subject-bound result, and aggregate completion | Slice 11A deliberately excluded execution and result aggregation |
| `review_artifacts/engineering_reviewer_transport_binding_code_design.md` | Code | Code | Agent Execution Adapter implementation owner | Define the smallest Adapter and Profile-binding change that closes declared Reviewer operations for each transport | Registration cannot invent an operation mapping that the Adapter contract and code do not implement |
| `review_artifacts/agent_capability_design_bundle_code_design.md` | Code | Code | Standalone Release conformance owner | Define the bounded T2 11 source-registration and mechanical package-projection change | Package projection is owned independently from the capability inventory it publishes |
| `src/agent_runtime/testing/agent_capability_verification.py` and its public projection | Code | implementation | Agent Capability Verification implementation owner | Add the approved inventory, runner, result, and inspection behavior in bounded slices | Current code only defines immutable cases and runbook projection |
| `tools/build_agent_runtime_design_contract_bundle.py`, generated Design bundle, and package-boundary tests | Code | Code | Standalone Release conformance owner | Register T2 11 in the unique canonical Design source inventory and regenerate its package projection mechanically | T1 already references T2 11, but the current bundle rejects that link because T2 11 is absent from `CANONICAL_DOCUMENTS` |
| Codex and Claude invocation, execution admission, and focused tests selected by the approved code design | Code | implementation | Agent Execution Adapter implementation owner | Implement only the operation mapping and admission behavior approved by T2 08 and its Code Design Basis | Reviewer execution must use registered capabilities and fail before provider entry when unsupported |
| Runtime PostgreSQL Profile and Variant releases for `engineering_change_reviewer@v4` | Runtime | registration | Runtime Registry operator | Register only exact Profiles whose Adapter capability gates pass, then bind them through one standalone Variant Policy | Module registration alone does not establish executable provider binding |
| Exact implementation commits | Release | review and admission | Software Delivery owner | Run deterministic gates and independent `engineering_change_reviewer@v4` review for each exact commit | Design, implementation, registration, and release decisions remain separate |

## Excluded Surfaces

| Surface | Exclusion reason |
| --- | --- |
| Portable Governance T0, Skill, prompt, schema, and Reviewer meaning | The approved v4 semantics are frozen inputs; this plan only consumes them |
| Trading Platform Charter, T1, task routing, workflows, and business code | The host supplies environment and registration input but does not own Runtime capability semantics |
| PostgreSQL credential parsing or secret storage | The host environment supplies the DSN; Runtime receives it opaquely |
| Runtime release-store schema redesign | The existing `agent_runtime_control_v2` store already registered and reloaded the exact Module closure |
| Reviewer checklist, severity, verdict, or output-schema redesign | Software Delivery owns those semantics and they are unchanged by transport enablement |
| Canonical Example Workflow implementation from Slice 11D | This plan is explicitly bounded to Reviewer transport plus Slices 11B and 11C; Slice 11D follows only after the runner produces subject-bound results and requires a successor SystemChangePlan and approved Code Design Basis |
| Production deployment or active-pointer promotion | This plan ends at local registration evidence and reviewed release candidates |

## Ordered Steps

| Step | Prerequisite | Final accountable owner | Method | Output object type | Review gate | Completion condition |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Adapter capability Design revision | Frozen prerequisites above | Agent Execution Adapter owner | `the-design-authoring` | exact T2 08 Design candidate | deterministic Design validation, `design_contract_reviewer`, and owner decision | Provider-native repository operations, registered capability mapping, pre-provider failure, and inspection boundary are complete without choosing a provider as semantic owner |
| 2. Verification Design revision | Accepted step 1 candidate | Agent Capability Verification owner | `the-design-authoring` | exact T2 11 Design candidate | deterministic Design validation, `design_contract_reviewer`, and owner decision | Multi-transport Reviewer verification has exact input, evidence, supported-versus-unsupported, failure, rerun, and aggregate completion semantics |
| 3. Reviewer transport Code Design | Accepted steps 1 and 2 | Agent Execution Adapter implementation owner | `engineering-code-design` | `CodeDesignBasis` | deterministic basis validation and Software Delivery owner decision | Adapter changes, Profile shape, tests, compatibility, rollback, and registration handoff are bounded and testable |
| 4. Capability inventory Code Design | Accepted step 2 | Agent Capability Verification implementation owner | `engineering-code-design` | Slice 11B `CodeDesignBasis` | deterministic basis validation and Software Delivery owner decision | Inventory ownership, uniqueness, case mapping, generated inspection, tests, and rollback are complete |
| 5. Capability runner Code Design | Accepted step 4 basis | Agent Capability Verification implementation owner | `engineering-code-design` | Slice 11C `CodeDesignBasis` | deterministic basis validation and Software Delivery owner decision | Runner, environment inspection, result schema, aggregate completion, cleanup, failure routing, and tests are complete |
| 5a. Design bundle Code Design | Accepted step 2 candidate | Standalone Release conformance owner | `engineering-code-design` | Design bundle `CodeDesignBasis` | deterministic basis validation and Software Delivery owner decision | The T2 11 source inventory entry, mechanical regeneration, package tests, exact hash closure, and rollback are bounded independently from capability inventory behavior |
| 6. Reviewer transport implementation | Approved step 3 basis | Agent Execution Adapter implementation owner | implementation under the approved basis | exact engineering change candidate | focused Adapter, admission, zero-provider, and dual-transport tests | Each declared transport produces exact supported or unsupported evidence; unsupported capability never reaches the provider |
| 7. Capability inventory implementation | Approved step 4 basis and frozen step 6 behavior | Agent Capability Verification implementation owner | implementation under Slice 11B basis | exact engineering change candidate | inventory, mapping, inspection, and focused tests | Every required capability resolves to one owner-qualified case or declared environment gate, and inventory inspection is generated from the same code truth |
| 7a. Design bundle registration and projection | Approved step 5a basis | Standalone Release conformance owner | implementation under the Design bundle basis | exact engineering change candidate | mechanical Design-bundle regeneration, package-boundary tests, and exact source/package hash comparison | T2 11 is registered in the unique canonical source inventory and is present byte-for-byte in the generated package; no allowlist bypass or hand-edited projection exists |
| 8. Capability runner implementation | Approved step 5 basis and completed steps 7 and 7a | Agent Capability Verification implementation owner | implementation under Slice 11C basis | exact engineering change candidate | runner positive, negative, unavailable-environment, cleanup, aggregation, and full-suite tests | Subject-bound results preserve peer failures, distinguish `failed` from `not_run`, and compute completion from exact required evidence |
| 9. Runtime Profile and Variant registration | Passed steps 6 through 8 | Runtime Registry operator | `agent-runtime-registration` | dependency-closed Runtime Profile and Variant release bundle | source/hash/admission gate and PostgreSQL round-trip | Only capability-passed Profiles are transactionally registered and one exact standalone Variant Policy resolves the v4 Module to them |
| 10. Exact commit and independent review | Steps 6 through 9 bind the same source and release hashes | Software Delivery owner | exact-path commits followed by independent `engineering_change_reviewer@v4` execution | immutable reviewed engineering commits | registered output-schema validation and Software Delivery decision | Every commit has one parent, exact approved basis, commit-bound test evidence, valid Reviewer output, and no unrelated dirty-worktree paths |

## Unresolved Decisions

`[]`
