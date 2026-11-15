# Changelog

## 0.2.0.dev12

- Add `module_requirements_preset(preset_id, *, mcp_connections=(), max_attempts=1)` and `MODULE_REQUIREMENT_PRESETS` (`structured_output`, `file_review`, `code_change`, `mcp_data`): explicit `ModuleExecutionRequirements` values for the four common usage patterns, passed as an ordinary Module's `execution_requirements`. `file_review` uses `ModuleReviewer`'s capability fields; `ModuleReviewer` keeps its fixed preset (3 attempts). Presets add no registry, Profile or policy; transport, model, effort and time stay per call, and saved definitions keep their saved tool and network policies.
- Add four directly runnable usage-pattern examples, packaged in the wheel: `python -m agent_runtime.testing.conformance_pattern_examples run|prepare|check --pattern PRESET --transport claude_cli|codex_cli --root ROOT`. Each run creates a fresh folder, registers one ordinary Module through the public registration API, runs it through `run_local_workflow_test`, and checks actual file effects, the returned Inspection log, an independent host re-run (code_change) and the audit of a packaged credential-free stdio MCP fixture (`mcp_data`, needs `cli_tools`). A model's self-report is never counted as evidence.
- Add the third packaged operator Skill `agent-runtime-usage` (choose a pattern, inspect its preset, run its example, then use ordinary registration and Test Run). `setup_runtime` installs it with the other two; a root prepared by an earlier version receives it on its next setup. Packaged Skills are kept with LF bytes.
- `agent_capability_example` is now v3: its draft writer's tools include `edit` for the scratch draft its instruction asks for; its other Modules and `agent_evaluation_example` keep their v2 definitions and bytes. Both `--example` graphs are documented as host-limited advanced callback examples (macOS/Linux with `cli_tools`; Windows reports the extension unsupported). No Windows callback IPC is added.
- Update README, runbooks, operator Skills and the generated API reference: Codex workspace/file/MCP capability, `--project-directory`, Claude `--command-permit` versus macOS-only recorded `commands`, the `AGENT_RUNTIME_REAL_RUN` and `RUN_PROVIDER_INTEGRATION` live-run gates, Attempt retention versus the callback examples' temporary folders, and the token fields (unknown ordinary input is omitted when stored, so only events with a known value carry the extra key).
- Execution behavior of existing entry points and stored record bytes are unchanged; no database migration.

## 0.2.0.dev11

- Add `ordinary_input_tokens`: Provider-reported input that is neither cache read nor cache creation (Claude today; unknown for Codex). `input_tokens` keeps its meaning, the cache-inclusive total; unknown stays unknown and ordinary input is never derived by subtracting cache from the total.
- Attempt records, usage events, replay, Test Run usage, `aggregate_model_usage` and Inspector views carry it. Inspector Attempt cards and workflow summary metrics show "Input total (incl. cache)", "Ordinary input", "Cache read" and "Cache creation", with output separate.
- Existing stored records and their hashes are unchanged. New usage events that carry a known ordinary count store one additional key. Runtime `0.2.0.dev10` or older cannot byte-verify those new events, so read new ledgers with `0.2.0.dev11` or later.
- No database migration or schema change is needed. None is run.
- Regenerate the packaged Design Contract mirror for the Design 08 §11.2 usage amendment.

## 0.2.0.dev10

- Add optional `progress_observer` to `run_local_workflow_test` for volatile live observation of Claude CLI runs: process liveness, elapsed time, byte counts and one safe current-event summary (init, model activity, tool request or result with a safe category, final result, declared command start/finish by `command_id`). Updates are delivered on receipt through a bounded in-order buffer of 16 with an explicit `updates_dropped` count, plus a heartbeat after 10 s without output; nothing is persisted and no content, arguments or output are exposed.
- Observation is attached only when the resolved transport is `claude_cli`; Codex runs accept and ignore the observer. Callers that omit it keep identical arguments, threads, records and failure mapping. Observation faults and blocked observers never change capture, deadlines, cleanup or results.
- Regenerate the packaged Design Contract mirror for the committed Design 08 live-observation boundary.

## 0.2.0.dev9

- Add one consumer upgrade procedure for existing Reviewers, with installation, new definition registration, exact readback and Test Run verification.
- Route README and packaged operator Skills to that procedure; fix the local registration link that led to the PostgreSQL-specific section.
- Clarify package, requirements encoding and definition version names; execution behavior is unchanged from 0.2.0.dev8.

## 0.2.0.dev8

- Add four-layer `run_timeout_seconds` and `--run-timeout-seconds` for synchronous Test Run and packaged examples.
- Bound managed same-process child work by the parent's remaining deadline, retaining requested/effective budget observations.
- New Module authoring uses `module_execution_requirements_v2`, separating fixed capabilities from per-call time. Register approved sources once under new versions before new execution; historical records retain their original encoding and hashes.
- Existing exact-Profile APIs keep their explicit Attempt cap. Cross-process deadline propagation and persisted Workflow lifetime budgets remain outside this release.


Notable changes to the standalone development line are recorded here. The
current package version is declared in pyproject.toml. Earlier entries describe
historical implementations, not the current executable adapter support matrix.

## Unreleased

### Codex workspace v3 for the Reviewer default environment

- A Module with the Reviewer default environment (agent, tools including
  shell, a private draft, denied tool network, inline input) can run on
  `codex_cli`: preparation binds `codex_cli_agent_workspace_executor@v3`, while
  tool-free Modules keep `codex_cli_agent_executor@v4` byte for byte. The v2
  workspace candidate stays readable and is refused by binding. A tool policy
  with read or search but no shell is reported as `ADAPTER_CAPABILITY_UNSUPPORTED`.
- Each Attempt has one main folder, its private scratch: the cwd, the only
  writable location and the home of `TMPDIR`. A Codex permission profile
  generated per call denies user trees, external volumes, `/tmp`, the host
  temporary root, the Provider private state, the credential directory and the
  rest of the workspace root, and grants materials, read-only dependencies,
  Runtime Python and the Codex program files for reading. Traces record
  `main_folder`, `codex_permission_profile` and
  `isolation_gaps: ["system_directories_readable"]`.
- Read grants that would reopen a denied or credential location, and declared
  commands that could read a protected location through the command sandbox,
  are refused with PermissionError before the Provider starts.
- Frozen resources and declared commands work on the Codex path through the
  existing command session; Inspection pairs Codex MCP results with the
  Runtime command records. Material staging is shared with the Claude adapter.

### Test Run entry and execution parameter layers

- The installed command is now `agent-runtime-test-run` and the Python entry
  `run_local_workflow_test`. `agent-runtime-evaluate` and
  `evaluate_local_workflow_module` are removed without aliases; Behavior
  Evaluation keeps its meaning. Setup replaces the bundled
  `agent-runtime-evaluation` operator Skill with `agent-runtime-test-run` when
  the old Skill has exactly its packaged or previously recorded content.
- Test Run reads a definition either from `root/.runtime` or, read-only, from an
  exact PostgreSQL Workflow ref/hash located through a DSN environment variable.
  `root` is required in both modes; nothing is written to PostgreSQL.
- `resources_path` and `expected_module_id` are accepted by the public API; the
  CLI passes `--resources` and `--expected-module-id` through unchanged and no
  longer runs setup separately.
- Transport, model and effort are resolved per parameter from this call, the
  Workflow parameter file, the workspace parameter file and the Runtime
  default. Parameter files never select a version. Records report each value's
  source layer and file hash in `execution_parameter_sources`.

### 0.2.0.dev5: local multi-Agent evaluation

- Common model preparation for registered Agent graphs; the existing single-node
  entry retains its exact preparation behavior.
- Process-local graph execution through the existing Coordinator and Module
  kernel, including parallel joins, revision, matching wait events, replay and
  cancellation with retained node facts. This is not cross-process durability.
- Claude CLI adapter v3 exposes only explicitly bound self-test callback tools
  through a strict request-scoped MCP bridge. Native tools and fixed command
  execution remain separate capabilities; ordinary tool refusal is a per-call
  result. Production Gateway remains unavailable, and no Claude SDK is used.
- The same agent-runtime-evaluate command can run packaged capability and
  independent-evaluation examples. Agent-requested child review is distinguished
  from outer graph orchestration; actual logs and child results are returned.
- Public API, CLI help and capability runbook describe the same execution
  interfaces. Historical Profile bytes and committed results are not rewritten.

## Earlier standalone development line

### Claude CLI execution and client API

- Public `Module`, `ModuleReviewer` and `Workflow` authoring/export APIs, with
  PostgreSQL registration, exact release selection and callable registered Modules.
- Direct Claude CLI execution with Profile-selected model, effort and native
  Read/Grep/Bash tools; isolated attempt materials and scratch, bounded process
  output, real failure classification and complete provider trace capture.
- Success, failure and timeout evidence is persisted by Runtime and readable
  through the PostgreSQL query API. Optional evidence fields preserve historical
  record payloads; recording a late failure does not extend execution permission.
- Packaged registration, capability and Claude CLI host-setup runbooks, with
  repeatable tests and separately enabled host/provider integration samples.

The source-level authoring inventory API has been replaced by explicit Module
and Workflow classes. Development stores must use the documented Registry
schema preflight/migration API before registration; ordinary calls do not run DDL.

### Added

- PostgreSQL authorities for immutable Runtime releases and the authoritative
  execution ledger, including byte-preserving canonical payloads.
- An authorized, read-only Live Inspector for Agent Workflow executions and
  separately authorized execution content.
- Architecture projections, clean-wheel checks, and packaged Design Contracts
  for the standalone distribution.
- Immutable `all_required` Workflow parallel groups with concurrent branch
  dispatch, branch-local retry recovery, one durable join transition,
  PostgreSQL registration, and Inspector projection.
- Domain-neutral Module/Workflow authoring examples in the capability runbook
  and their executable tests, independent of host business code.
- A code-owned public-repository manifest and validator that reject private
  governance deployment, host-domain fixtures, undeclared tracked paths, and
  forbidden content in the current tree and reachable Git history.

### Fixed

- Exact content and external-ingress retries now converge after a crash even
  when clock-derived timestamps, admission, wait state, or authority windows
  have changed.
- PostgreSQL reads use one repeatable snapshot, schema initialization is
  serialized, and historical ledger restoration validates the complete prefix
  once instead of replaying every prefix quadratically.
- Inspector content downloads are sandboxed attachments and list pagination
  uses a stable keyset cursor.
- Attempt workspace leases are importable on Windows and lease collisions are
  classified as non-retryable workspace failures by provider adapters.
- Parallel groups return replayable blocked progress for invalid committed
  branch results, reject permanently undersized dispatch budgets, bound
  concurrent bridge calls, and render their topology in the Live Inspector.
- Claude Agent SDK tool hooks now match exact registered tool names, retain
  bounded refusal diagnostics, and avoid substring collisions with undeclared
  SDK tools.

### Breaking development-line changes

- Durability topology identifiers now use the same minimum three-character
  syntax as the rest of Runtime. One- and two-character prototype IDs must be
  migrated.
- Product-host UTC timestamps must use the canonical `Z` suffix; the equivalent
  `+00:00` spelling is no longer accepted at that boundary.
- The retired envelope-shaped Temporal workflow-start payload is no longer
  decoded. No deployment or in-flight execution used that prototype shape.
- Architecture and inventory projections use schema version `v3`, and public
  exports use the responsibility-oriented `registry`, `invocation`, `ledger`,
  and `inspection` namespaces rather than predecessor host package names.
- Release inventory projection advances to
  `agent_runtime_release_inventory_v3` to expose Workflow parallel groups.
- Development PostgreSQL ledger schemas created before canonical payload byte
  columns were added must be dropped and initialized again. No tagged release
  or supported in-place database upgrade predates this change.
- Runtime durability contracts now consume `DurableExecutionBinding` and no
  longer publish Principal routing, Region, model-supply, credential, or
  data-placement types. Host platforms own those concerns.
- The packaged Design Contract bundle contains only Runtime-owned contracts.
  Agency Platform topology, host binding, publication transactions, and mutable
  Software Delivery roadmaps ship with their owning products instead.
