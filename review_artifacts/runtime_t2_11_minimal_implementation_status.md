# Runtime T2 11 minimal implementation status

## Scope decision

The user stopped further external CodeDesign review and directed the Primary Agent to implement only the most important
T2 11 behavior. The previously reviewed 12-module, 68-test proposal is preserved as historical evidence and is not the
implementation target.

The selected implementation contains:

- the exact 42-capability code-owned inventory;
- focused and complete request/result records;
- one callback-driven runner that preserves `passed`, `failed` and `not_run` without sibling propagation;
- fail-closed complete scope until the canonical Example and exact registered transport evidence are attached to one result;
- stable T2 11 error codes for coverage and harness failure;
- concrete static closure for the Portable `engineering_change_reviewer` v4 registration, schemas and declared operations;
- no new public facade, CLI, database schema, persistent store, external sink or generic environment framework.

Runtime core names the generic capability `registered_module_transport`. The concrete
`engineering_change_reviewer` remains an owner-supplied test case, so Runtime does not acquire a Reviewer subsystem or
role vocabulary.

## Exact files

- `designDoc/agent_runtime_11_agent_capability_verification.md`, SHA-256
  `cf6ad622ce4b3475c642ff29fe7911cd7a4059a3013b31d1446460961acdc5d1`;
- `src/agent_runtime/testing/conformance_agent_capability_verification.py`, SHA-256
  `2a616e1b82cb314b631acd9bfe9850dfa56b606ab30ca2ca29dae3458ea2339e`;
- `tests/test_agent_runtime_capability_verification.py`, SHA-256
  `a5a434107a64bb0f5e417955f35ddd9d269978cf1e5a3236a83e8b529984f8e2`.

The pre-reduction external content review is retained at
`review_artifacts/runtime_verification_basis_opus_1m_pre_reduction_final.json`, SHA-256
`d88f2f2a314e4c676337f2b731fb8c96dca35bcbb96f5a59687c16cee8de256d`. It validates only the superseded larger
proposal and is not represented as review of this implementation.

## Non-sandbox verification

- final complete environment suite: `645 passed`, zero skipped, zero failed, in 482.70 seconds;
- PostgreSQL: `42 passed` against the dedicated UTF-8 `agent_runtime_test` database; each test created and removed its
  UUID-named Runtime schema;
- Temporal: `3 passed` with test-owned local dev servers started and stopped by the gate;
- live provider: `4 passed` across Codex, Claude inline, Claude draft workspace and Claude Gateway execution;
- package and naming: `25 passed`; T2 11 is in the generated bundle and the old T2 10 local copy is archived with its
  original SHA-256 `9faf8078cb69fee6de5fd4131c9e5ba310d463fd0eb291ab43fb5f2584a37fd4`;
- Runtime architecture: `validate_runtime_architecture() == ()`; no dependency-debt allowlist was added.
- public-doc and wheel-doc parity, package and architecture focused gate: `72 passed`.

## Canonical case execution

The 42 inventory rows resolve to nine canonical cases from one code-owned definition and one generated runbook:

- generated runbook: `review_artifacts/runtime_t2_11_capability_runbook.json`, SHA-256
  `339f9034af9aed242b8d9df063f7bf4c3487256d27d88b42ee15212ae8c9cf81`; deterministic parity passed;

- `module_release_assembly_case`: `23 passed` using only generic Runtime Module assembly tests;
- `agent_invocation_case`: passed with all four live provider tests enabled;
- `module_execution_case`: `32 passed`;
- `workflow_graph_case`: `22 passed`;
- `ledger_inspection_case`: `24 passed`;
- `persistent_runtime_case`: `42 passed`;
- `durable_backend_case`: `3 passed`;
- `live_module_transport_case`: passed, including four real provider paths;
- `public_package_case`: passed; the larger package/naming gate is `25 passed`.

All nine canonical cases now have passing execution evidence. No skip or partial result was promoted to pass.

## Public documentation

The code-owned inventory and case definitions generate two byte-identical repository and wheel projections:

- public capability catalog: `docs/agent_runtime_capabilities.md`, SHA-256
  `5add1d66b011408db2270e3ef5fc87adfd880e6804f4950994321c38c8be6580`;
- operator runbook: `docs/agent_runtime_capability_runbook.md`, SHA-256
  `2e3ebe45fb6ccd3fd5c160eab0c082a844fd8e8cdf5456c009081a2a24cebbf2`.

The same bytes are shipped under `agent_runtime/docs/`. Repository and package README files are byte-identical and link
to both documents. Current pass/fail counts stay in verification evidence rather than the stable public catalog.

## Remaining plan work

The broader Runtime plan still has separate work for the production-independent Execution/Invocation self-test seam and
the exact registered `engineering_change_reviewer` managed transport. The nine T2 11 cases and repository-wide environment
suite are green; this record does not claim that the remaining registration-specific lineage is complete.
