# Slice 2 Dirty-Change Registration Audit

Status: design input; not release evidence.

## 1. Result

The current worktree cannot be reviewed, committed, or described as one Slice.
It contains the accepted Slice 2A candidate together with implementation for
later Registry, PostgreSQL, Runtime-execution, and host-cutover work.

Slice 2A is independently accepted. Its frozen subject is the reviewed revision
10 content, not the mutable revision-11 paths now present in the worktree:

- Code Design Basis Markdown SHA-256
  `890b90a10a35e6b1169af742351fd23e8cd17ed33bcd993140b29aee366ca897`;
- machine basis SHA-256
  `3dd9ac0c948695ea35a83bbbc6c6283522383d7ed301890480035f333bcd0e6d`;
- fixture SHA-256
  `85a0aa5b7c20aaa743b4c8e4a527f7cfe941630bd557d6ab029f9549dc230295`;
- Module I/O test SHA-256
  `baa9ef4c8cff11c2305988a2302ea7075d72dc0c8083c05059bac4b62d9040a6`;
- Opus 5 xhigh verdict `ACCEPT`; and
- the 2A-owned symbols in the source files named below.

The remaining dirty code is candidate material, not accepted implementation.
Commit state does not change that disposition.

## 2. Code registration

Test ownership follows the contract or integration seam asserted by the test,
not every fixture, compiler, or dependency used to arrange the case. A test
that asserts one later integration may construct already-registered lower-layer
objects without becoming jointly owned. If one file contains tests for several
independent contracts, split those tests into owner-named files before review.
Tests are never projections.

| Owner | Current paths or symbols | Disposition before review |
| --- | --- | --- |
| Slice 2A — Agent Module candidate and I/O | `foundation/foundation_json_schema_validation.py`; `foundation/foundation_schema_traversal.py::strict_output_schema_projection`; `invocation/invocation_schema_projection.py` delegation; Agent/Behavior/Evaluation/Retry/Prompt symbols in `registry/registry_release_compilation.py`; corresponding Policy and Module record fields in `contracts/registry_release_definition.py`; `tests/digestion_case/**` | Accepted by Opus 5 xhigh after `7 passed`; freeze as the 2A subject |
| Slice 2B — remaining release compilation and in-memory Registry closure | remaining candidate/compiler symbols in `registry_release_compilation.py`; policy/variant/workflow records plus predecessor profile-selection retirement in `registry_release_definition.py`; 2B-only facade exports; `registry_release_registration.py`; `tests/test_agent_runtime_prompt_component_compilation.py`; `tests/test_agent_runtime_registry_candidate_compilation.py`; `tests/test_agent_runtime_registry_policy_release.py`; `tests/test_agent_runtime_registry_module_policy_closure.py`; `tests/test_agent_runtime_registry_prompt_component_closure.py`; `tests/test_agent_runtime_registry_release_catalog.py`; `tests/test_agent_runtime_workflow_release_compilation.py` | Approved revision-18 implementation subject; candidate compilation, exact registration, Module/Workflow active-pointer behavior, retirement, and each Registry closure have owner-named test files |
| Slice 2C — migration candidate and PostgreSQL persistence | `registry_migration_candidate_set.py`; `registry_schema_migration.py`; `registry_postgres_persistence.py`; `tools/registry_schema_migration_planning.py`; `tests/test_agent_runtime_registry_migration_candidate_set.py`; `tests/test_agent_runtime_registry_schema_migration.py`; `tests/test_agent_runtime_registry_postgres_prompt_component.py`; `tests/test_registry_schema_migration_planning_tool.py`; `tests/test_agent_runtime_postgres_release_store.py` | Registered design candidate; lower-layer compilers and Registry objects used as fixtures do not change test ownership |
| New Slice 2D — registered Policy consumption in Execution, Ledger, and Inspection | `contracts/execution_module_definition.py`; policy-resolution portions of `execution/execution_module_invocation.py`; retry-policy binding in `ledger/ledger_workflow_module_recording.py`; `inspection/inspection_release_rendering.py`; `tests/test_agent_runtime_authorization.py`; `tests/test_agent_runtime_execution_authorization.py`; `tests/test_agent_runtime_external_event_ingress.py`; `tests/test_agent_runtime_native_structured_output.py`; `tests/test_agent_runtime_parallel_workflow.py` | Missing from revision 10 Slice decomposition; lower-layer release setup in these tests is fixture construction, while assertions own Execution/Ledger/Inspection behavior |
| Slice 2E — Runtime distribution and registered-host cutover | retirement of `registry_module_loading.py` and its architecture registration after the host adapter replaces it; `tests/test_agent_runtime_packaging_boundary.py`; `tests/test_governance_reviewer_module_registration.py`; real host authoring, assembly, import, composition, and PostgreSQL gates remain in the registered host repository | Former Slice 2D; local tests are distribution/downstream conformance gates, not shared ownership of lower-layer compilers |
| Public façades and generated outputs | `src/agent_runtime/__init__.py`; `contracts/__init__.py`; `registry/__init__.py`; `conformance/conformance_architecture_manifest.py`; `registry/registry_architecture_registration.py`; root and package `README.md` | Each façade export follows the one owner of the exported interface; conformance/architecture outputs have one code-owned validator/projector; README has one package-documentation owner and mirror-parity gate. None creates shared code ownership. |
| Prior or cross-Slice evidence | Slice 1 downstream-gate records, systematic-rebuild assessment, design reviews, and `runtime_module_owned_schema_code_design.md` | Evidence only; not product implementation |

## 3. Files that must be split before later review

The misleading predecessor `tests/test_agent_runtime_module_loading.py` is
removed. Its redundant 2A path/schema cases remain covered by
`tests/digestion_case`; its Profile, non-Agent, Policy, and Registry closure
cases now live in `tests/test_agent_runtime_registry_candidate_compilation.py`
as 2B evidence.

`tests/test_agent_runtime_native_structured_output.py` contains Registry setup,
Execution retry, crash recovery, provider invocation, and native-output
integration. Its asserted result belongs to the 2D Execution seam; Registry and
Module construction are admitted test fixtures, not additional owners.

`tests/test_governance_reviewer_module_registration.py` and
`tests/test_agent_runtime_packaging_boundary.py` prove local distribution and
downstream conformance and therefore belong to 2E. Their lower-layer setup does
not grant 2E ownership of the compiler or Registry contracts.

The former `tests/test_agent_runtime_prompt_component.py` is split into
owner-named compiler, Registry-closure, and PostgreSQL files. The PostgreSQL
store, external-event, and parallel-workflow suites already have one primary
contract owner despite using lower-layer setup.

`registry_release_compilation.py` and
`contracts/registry_release_definition.py` contain symbols owned by more than
one Slice. Review subjects therefore bind exact symbols or line-independent
public interface IDs, not the whole file.

## 4. Design correction

Revision 18 of the Slice 2 Code Design Basis now:

1. preserve the accepted 2A result and evidence;
2. add one bounded 2D result for registered Module Policy resolution,
   Execution application, Ledger retry enforcement, and release inspection;
3. move registered-host authoring/composition/downstream conformance to 2E;
4. assign each public façade or generated output one projector/conformance
   owner and deterministic regeneration gate, without shared ownership;
5. require every changed production symbol, schema, migration, test, and
   generated/public projection to resolve to one owning Slice or a declared
   multi-Slice projection; and
6. prohibit a later Slice's already-present code from being counted as evidence
   for an earlier Slice.

No later-Slice code is accepted merely because it is already present or because
an aggregate test passes.

## 6. Predecessor field retirement

The immutable approved root basis revision 3 still contains the predecessor
field `shared_projection_paths`. It remains untouched because prior review
evidence binds its hash. The current Slice 2 basis does not consume or reproduce that field.
The next root-basis revision must remove it and assign
`inspection_architecture_rendering.py` to Runtime Conformance and
`contracts/__init__.py` to its compatibility-facade retirement owner. Historical
evidence is not silently rewritten to make the new rule appear retroactive.

## 5. Reusable design lessons

- A Slice inventory must close over actual code exports and actual dirty
  change units, not only the interfaces remembered by the author.
- Every ref/hash edge is proved by resolving the referenced artifact and
  comparing the exact hash value; checking only the field name, ref, or hash
  format permits false-green closure.
- When absence is a contract claim, fence the complete candidate, result, and
  serialized-record field sets rather than denylisting expected bad names.
- A behavioral guard needs a live positive control; a guard that never fires is
  not evidence.
- A reviewer should try a concrete defect injection: identify one production
  mutation that would make the declared result false, then ask which test must
  fail. If none fails, the Slice is not complete.
- Before Slice acceptance, compare every changed code unit with the approved
  design. Undeclared code is removed, assigned to a later Slice, or returned to
  Code Design; it is never silently carried as convenient context.
