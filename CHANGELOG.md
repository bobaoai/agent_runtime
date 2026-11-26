# Changelog

## 0.2.0.dev22

- Claude: when the CLI writes only an error result with a structured `startup_failure_reason` and no init event, the failure message now names that reason and the result's `errors`, for example `CLI initialization is missing: Claude reported startup failure sandbox_required_unavailable: Sandbox required but unavailable: ...`. The failure class (`provider`), code (`claude_cli_result_missing_or_invalid`), retry disposition (`retry_denied`), exit code, usage and raw streams are unchanged. A stream without a startup failure reason keeps the generic message; multiple terminal results and the authentication, authorization, quota and rate-limit categories keep their precedence. Stderr text is not parsed. Saved definitions, CLI arguments and settings are unchanged.

## 0.2.0.dev21

- New explicit post-run step `agent-runtime-archive` (API `save_test_run_archive`, `read_test_run_archive`, `cleanup_test_run_archive`). `save` copies one finished Test Run's record and the ordinary files of its own Attempt folders into the central Runtime archive, commits, then reads the manifest and every body back on a new connection and checks the path set, sizes and SHA-256. The code-written receipt keeps `run_status` (the run's own outcome, never changed) apart from `archive_status` (`verified`, `write_failed`, `readback_failed`). A failed save leaves the scene as it was. `read` verifies again from the database and returns the archived record (output, failure, full execution log), the file index and, with `--extract-to`, the restored files. `cleanup` verifies again (the receipt's status is not trusted), checks the folder still equals the archived index, and passes only the run's own Attempt folders to `maintain_attempt_workspaces`. Default Test Run behavior and records are unchanged.
- Storage: `run_archive_ddl(schema="runtime_archive", grant_role=None)` returns the schema with two insert-only tables, `run_archive` (one row per saved run: manifest text and its SHA-256, the run's status and execution id) and `archive_content` (path, size, SHA-256, bytes). The database owner applies it; saving never runs DDL. `PostgresTestRunArchiveStore(connection_factory)` and `.from_dsn(...)` open a new connection for every write and read. Archive rows are kept; there is no purge.
- Binding: root `.runtime/config.json` accepts one `run_archive` object (`host`, `port`, `database`, `user`, `schema`, `password_credential`), separate from `release_stores`. Each connection reads the named password through the installed central credential client `wscred.exe get <name>` and passes it only to the database connection; client failures report their error code. Database error messages are not echoed (type and SQLSTATE only).
- Scene rules: Runtime marker and seal files are skipped; links, reparse points and special files are not followed and mark that Attempt `incomplete`, which cleanup keeps (`scene_not_fully_archived`). A file added after saving keeps the folder (`scene_changed_since_archive`). A live lease, `--hold`, an unsealed folder and unconfirmed process stop keep it through the existing maintenance checks. The caller's project directory, record file, business data and archive rows are never deleted. Save-time Python, OS and installed Runtime version are recorded as `archive_host`, apart from the run facts taken from the record.
- `maintain_attempt_workspaces` now accepts `retention_seconds=0` (no extra wait once the seal applies); `None` still means no automatic deletion and negative values are still rejected.
- Docs: Design 08 §3.5.6; README "中央留档与本次清理" and a Start-by-task row; registration runbook `archive-test-run` with a runnable native-project sample (`archive-test-run-example`: one selected Python, the `run_archive` binding from an already configured root, save, readback, owned cleanup and restore, stopping at the first failed step); the `agent-runtime-test-run` and `agent-runtime-usage` Skills.

## 0.2.0.dev20

- New per-call option `native_project` (`run_local_workflow_test(..., native_project=True)`, `agent-runtime-test-run --native-project`). It runs one independent `claude -p` or `codex exec` in the caller's `project_directory` with the project's own instructions and skills. Each call is a fresh session that is never resumed (Claude `--no-session-persistence`, Codex `--ephemeral`). Default `False`: every existing argv, setting and record is unchanged.
- Accepted shapes are the existing `code_change` and `mcp_data` presets (agent, inline input, `explicit_project_read_write`, `provider_managed`, read + edit + shell, optional search, declared `mcp__<name>` connections, no callbacks). Codex also needs the explicit `codex_execution_mode="host"`. A missing project directory, a material package, `resources` commands, additional read-only dependencies (pass `()` to clear host defaults), `--command-permit`, a callback factory, other shapes and Antigravity are refused before any Provider with `ADAPTER_CAPABILITY_UNSUPPORTED` (`NativeProjectUnsupportedError`). Nothing is downgraded or switched to another Provider.
- Claude (`claude_cli_adapter@v4`): no `--safe-mode` and no `--disable-slash-commands`, so CLAUDE.md and user/project skills load by Claude's own rules. `--tools` adds `Skill`, `PowerShell` beside `Bash` for shell (Claude offers it where its tool exists) and, with declared MCP, `ListMcpResourcesTool`, `ReadMcpResourceTool` and `ToolSearch`. Allow rules under `dontAsk` cover these tools, `Edit(./**)` and each declared `mcp__<name>`; user, project and managed deny/ask rules keep their precedence. Settings are only `autoMemoryEnabled=false` and `disableAllHooks=true`. MCP stays the strict private `mcp.json` of declared connections.
- Codex (`codex_cli_agent_workspace_executor@v5`, host): the host command without `project_doc_max_bytes=0`, `skills.include_instructions=false` and `features.skip_host_skill_discovery=true`, so AGENTS.md and `.agents/skills` load by Codex's own discovery. Declared MCP connections are rendered, and undeclared enabled host servers are disabled per call as before. Agents, web search and the other disabled features stay off.
- Neither CLI gets bypassPermissions or a skip-permissions flag. Shell runs with host permissions; the project directory is task scope, not an OS boundary. Credentials stay in each CLI's own mechanism: a root connection with the native name and URL finds the existing login. Python runs as the project's instructions direct; the Runtime Python still leads PATH. No Adapter revision changes; the binding plus the recorded option identify the translation (Design 08 §3.4.7).
- Records: the trace and `effective_configuration` add `native_project: true` and `context_enabled` (configuration intent: Claude `claude_md, skills`; Codex `agents_md, skills`). `context_suppressed`, tools, allow rules, `command_scope=host` and `filesystem_scope=task_directed` are recorded as rendered. MCP identity stays name plus config hash. What was discovered and used is read from the raw events (Claude's init lists tools, skills, plugins and connection status).
- Example: `python -m agent_runtime.testing.conformance_pattern_examples run --pattern native_project --transport claude_cli|codex_cli`. It generates a project with CLAUDE.md, AGENTS.md, two skills for each CLI, a Python script and the MCP fixture. Checks cover the instruction marker in the report, Claude's init skill list, the skill invocation or SKILL.md read in the log, the fixture audit, the script's artifact with its interpreter, and the structured output. Real case: `tests/test_agent_runtime_pattern_examples.py -k native_project` with `AGENT_RUNTIME_REAL_RUN=1`.
- Docs: Design 08 §3.4.7; native-tools doc section 6; README "原生项目模式"; the `agent-runtime-usage` and `agent-runtime-test-run` Skills. README install examples now name 0.2.0.dev20.

## 0.2.0.dev19

- Antigravity: when a run ends with exit 0 and `SUCCESS` but no `structured_output`, and agy's result reports `denied_actions`, the failure message now starts with those native denials and the failed tool steps, for example `Antigravity CLI denied native actions [{"action": "command", "display_name": "Bash"}] (failed tool steps: run_command #8); its result carries no structured_output and response text is not parsed`. The failure class (`schema`), code (`antigravity_cli_structured_output_missing`), retry disposition (`retry_denied`) and the complete raw streams are unchanged. The trace also keeps the native list as `native_denied_actions`. A valid `structured_output` after an earlier denial still completes. An invalid one is still `ADAPTER_OUTPUT_INVALID`. A missing one without `denied_actions` keeps the generic message. Stderr text is not parsed.
- `antigravity_command_permit` and the adapter's `command_permits` no longer refuse `)`. `re.escape` escapes parentheses, and agy accepted that rule for an interpreter path with spaces, non-ASCII text and parentheses, ran it and exited 0. Permits must still be non-empty and single-line, and now must not contain NUL.
- The helper only escapes the string it is given. It does not resolve `python` to an interpreter, normalize path separators or quotes, or combine separately permitted commands. Callers build each canonical command once (for example with the selected Runtime Python), put exactly that string in the task input, derive its permit from the same value and ask for one tool call per command.
- Docs: the registration runbook gains `antigravity-command-permits` with this caller pattern. The native-tools doc (section 5) and Design 08 §3.10 describe the permit limits and the denial diagnostic. The `agent-runtime-test-run` and `agent-runtime-usage` Skills point to the runbook section.
- Claude and Codex behavior, saved definitions, records and replay are unchanged.

## 0.2.0.dev18

- Add the Antigravity CLI (`agy`) as a third transport, `antigravity_cli` (adapter `antigravity_cli_adapter@v1`, provider `google`). This first binding runs exactly two requirement shapes:
  - `structured_output`: tool-free, inline input, workspace `none`, no tools, network `denied`.
  - `file_review`: agent, inline input, `own_draft_read_write`, exactly read/search/edit/shell, network `provider_managed`.
  - Both need native structured output and an explicit model and effort (no default). Every other shape (explicit project or `code_change`, MCP or `mcp_data`, callbacks, recorded commands, prompt-only JSON, Gateway input) fails with `ADAPTER_CAPABILITY_UNSUPPORTED` during Profile preparation, before any folder, process or model.
- Per call Runtime writes three files and starts agy once, with the argv the passing native probes used (`--input-format stream-json --output-format stream-json --json-schema FILE --agent agent-runtime-task --model M --effort E --settings FILE --disable-slash-commands`, no `-p`):
  - a custom primary agent in `<cwd>/.agents/agents/agent-runtime-task.md`. `structured_output` lists only `finish`, the CLI's structured-result mechanism. `file_review` lists `view_file`, `write_to_file`, `replace_file_content`, `run_command`, `command_status` and `finish`; search goes through the native Shell, and `grep_search` is not used.
  - session-only settings passed with `--settings`; the global agy settings are never written. `structured_output` denies every file, command, MCP and URL operation. `file_review` accepts edits in its private scratch cwd, allows reads of the scratch, the read-only materials and selected dependencies, and allows exactly the host's `command_permits`, rendered as `command(<permit>)`. It denies writes to materials and to the agent folder. No permit means no command allow rule. `antigravity_command_permit(command)` returns the live-tested body: `regex:` plus `re.escape` of the complete command.
  - the canonical registered output schema, unchanged.
  - stdin is one stream-json user event (the frozen envelope, or the existing Invocation Locations section plus the envelope for `file_review`), then EOF. The trace keeps both `actual_prompt` and the exact `stdin_jsonl`.
- Success needs exit 0, one `init` and one `result` event, `result.status` `SUCCESS`, the generated agent and the requested model in `init`, and a `result.structured_output` object valid against the canonical schema. Response text is never parsed. Each failure has its own code (`antigravity_cli_structured_output_missing`, `_error_result` with the actual status, `_agent_not_applied`, `_model_identity_mismatch`, `_nonzero_exit`, `_result_missing_or_invalid`, `_usage_invalid`, `_timeout`, `_interrupted`, `_cleanup_failed`). A tool request the CLI soft-denies in headless mode stays a failed tool step and a stderr notice; by itself it does not fail the Attempt.
- Usage: `output_tokens` is taken as reported (agy's `thinking_tokens` are already part of it and are never added). Whether agy's `input_tokens` includes `cache_read_tokens` is not established, so input total, cache read, cache creation and ordinary input stay unknown; the raw usage row is kept in the trace.
- Inspection's provider log pairs agy tool steps by `step_index`. A read that the stream reports only as a summary (`N lines, M bytes`) and a write without content are explicit content limitations. `finish` and the `init.tools` catalog are never counted as task tools.
- `run_local_workflow_test`, `agent-runtime-test-run --transport antigravity_cli`, `provider_cli_paths.antigravity_cli` and the packaged `structured_output` and `file_review` examples accept the new transport. Program lookup: explicit path, configured path, native `agy.exe` on PATH, then `%LOCALAPPDATA%\agy\bin\agy.exe`.
- `agent-runtime-setup --provider antigravity [--antigravity-path EXE]` records the program and installs the Skills. It runs only `agy --version`; login is reported `not_verified` because agy has no documented status command, and no auth file or model listing is read. `--provider both` still means Claude plus Codex. `--mcp` is refused for Antigravity.
- The architecture registry now also lists `conformance_host_setup.py`, which 0.2.0.dev17 had left unregistered.
- Docs: Design 08 gains §3.10 Antigravity CLI Adaptation (the former §3.10 Output and Execution Records is now §3.11; its anchors are unchanged) plus login and verified-scope notes. T0 §5.1 and Design 05 §7 summarize the binding and the setup rules. README (provider and token tables, setup, examples, limits), the native-tools doc (new section 5), the registration runbook (`project-setup`), and the test-run and usage Skills describe the two-shape limit and the native login boundary. Package mirrors and the design bundle are regenerated.
- Claude and Codex behavior, saved definitions, records and replay are unchanged.

## 0.2.0.dev17

- Add the `agent-runtime-setup --root DIR --provider claude|codex|both [--claude-path EXE] [--codex-path EXE] [--mcp NAME ...] [--mcp-config FILE]` console entry. It prepares a project in one command.
  - It installs the operator Skills through the unchanged `setup_runtime`.
  - It records the native CLI program in `provider_cli_paths`. Precedence: explicit path, then the configured program, then the native exe on PATH, then fixed npm package locations beside the PATH launcher. A configured program that cannot start is kept and reported, not replaced.
  - It adds only the selected MCP connections. The sources are an explicit Runtime config or Claude `mcpServers` file, or `codex mcp get NAME --json`.
- A connection with settings that cannot be expressed as credential-free configuration is refused and its existing entry kept: a literal env or header value, a headers helper, `oauth`, or an unknown field. The report names the field, never its value.
- Codex project choices (`enabled`, `enabled_tools`, `disabled_tools`, `default_tools_approval_mode`) survive a native refresh. Setup never adds `approve`; an absent approval is reported for information only.
- Setup runs only `--version` and the CLI's own login-status command. For a Codex connection selected without `--mcp-config` it also reads that connection's configuration (`codex mcp get NAME --json`). It never starts a login or an MCP health check, and never makes a model or data call.
- The JSON report separates preparation from verification. `missing` lists items a human or owner must handle, and `next_steps` gives copyable commands. Exit 0 means nothing is missing, 1 means items are missing or setup failed, 2 means a usage error.
- All other `.runtime/config.json` content stays as written. Nothing else is stored.
- README now uses this command after `pip install`, replacing the long Python one-liner; programmatic `setup_runtime` is unchanged. Other docs updated:
  - The registration runbook gains the detailed reference (`#project-setup`).
  - The API reference gains a generated Setup CLI section.
  - The usage and test-run Skills point to the command.
  - Design 05 §7, Design 08 §3.5.1/§3.5.5 and the T0 authority table state the split: setup prepares and reports, humans complete Provider and MCP login, consent or approval, and preparation is not verification.
- No change to execution-time program resolution, saved definitions, records or replay.

## 0.2.0.dev16

- The Claude native output-schema projection keeps `anyOf` instead of silently dropping it. Previously a nullable object such as `{"anyOf": [<object with required fields>, {"type": "null"}]}` reached `claude --json-schema` as `{}`, so Claude never saw the object's fields and the canonical check then rejected its output.
  - `anyOf` and each of its branches go through the same recursive projection as other supported keywords, preserving nullable objects (either branch order), local `$ref` branches and ordinary object alternatives.
  - Unsupported keywords inside a branch are still pruned. `allOf`, `oneOf` and `not` are unchanged.
  - Claude's documented subset rejects `anyOf` in the same schema object as structural keywords such as `type` or `properties`. That combination now fails before any CLI process with `native_output_schema_projection_unsupported`; `title` and `description` may accompany `anyOf`.
- Unchanged: the canonical post-run validation, the Codex projection, registered schemas and prompts, `claude_cli_adapter@v4`, and replay. The trace's actual `--json-schema` and this package version record the projection used.
- Two test expectations that were already stale at `eed754b` now match current behavior while keeping their purpose. The historical Codex workspace v2 Profile still never reaches a Provider; the current executor is v5. The CLI forwards `codex_execution_mode="configured"` along with the unchanged PostgreSQL locator and constraints.

## 0.2.0.dev15

- Claude and Codex agent calls now receive the locations Runtime prepared for the Attempt. Their stdin is a code-generated `## Runtime Invocation Locations` section, then a blank line, then the unchanged frozen Prompt Envelope.
  - The section is JSON with sorted keys. It gives the working and writable directories and the Attempt's read-only materials folder (task_input, and source when materials are frozen). It also gives the selected read-only dependencies and, for Shell-capable calls, the Runtime Python that leads PATH. Temporary files are named only by their variables.
  - It never contains the origin `material_root`, Attempt root or archive, Claude's private `crt-` folder (which holds `mcp.json`), CLI homes, connection configuration or credentials.
  - Tool-free calls send the envelope alone.
- Both adapters record the exact stdin as the provider trace's `actual_prompt`; Codex now records it too. They also record the section's values as `effective_configuration.invocation_locations`. Inspection's provider log returns both.
  - The envelope bytes, its ref and hash, request identities, the frozen v4 resource descriptions and committed replay are unchanged.
  - `prompt_envelope_sha256` identifies the envelope, not the whole stdin.
  - Design T0 §7 and Design 08 §3.6.2 state this, and the packaged Design Contract mirror is regenerated.
- In the CLI child environment only, `TEMP` and `TMP` now name the same per-call folder as `TMPDIR`:
  - Claude: the private `crt-` folder, which is also `CLAUDE_CODE_TMPDIR`.
  - Codex workspace calls: the Attempt `work/tmp` or `scratch/.tmp`.

  Windows native and .NET tools therefore use the prepared folder instead of the host temp folder. No global variable or access control is changed.
- Ordinary Claude calls no longer set `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, so a project's installed pytest plugins load as they do under Codex. The macOS declared-command environment is unchanged.
- The registration runbook gains §4.2 (anchor `skill-task-base`): how a source owner builds a Module's `prompt.md` as the complete execution prompt for one independent task taken from a Skill, with a destination table and a business-neutral worked example. The packaged `agent-runtime-registration` Skill points to it from its Inputs. No registration path, tool or Skill is added.
- No database migration, registry format or saved-definition change.

## 0.2.0.dev13

- Code owns the ordinary machine-field handoff (design T0 §6.3). `agent-runtime-registry register-reviewer --source DIR` and the new `register-module --source DIR --preset P [--mcp-connection N] --version V` read the Skill and Module identities from the selected source folder (`locate_module_registration`, `Module.from_source`, `register_module`); the lower-level `--source-root/--skill-id/--module-id` form remains. `register --bundle` may omit `--plugin-id/--plugin-version`: the validated, unsaved package label is read from the bundle's single Workflow (or single Module), and ambiguous bundles need the owner's label.
- Add `RuntimeDefinitionSelection` (`runtime_definition_selection_v1`): registration commands print it under `selection` and write it with `--selection-out`; `agent-runtime-registry select --workflow ID --version V [--release-store NAME]` selects an existing exact version, and `load --selection FILE` returns the selected Workflow, its Module and the Module's input/output schemas for input preparation. `agent-runtime-test-run --selection FILE` and `run_local_workflow_test(selection=...)` run exactly that Workflow and Module from root/.runtime or a named store, so a later registered version never replaces it; the record adds `definition_selection` with `executed_matches_selection`. Python: `selection_from_registration`, `select_registered_workflow`, `write_definition_selection`, `read_definition_selection`.
- Add optional `release_stores` to `root/.runtime/config.json` (`{"name": {"database_url_env": VAR, "schema": SCHEMA}}`); a PostgreSQL selection records only the store name, and each run opens it through the current configuration and reads the exact ref/hash. DSNs stay in the named variable.
- Add `prepare_material_files(material_root, selected)` and `write_local_resources`, and Test Run `--material-root DIR` with repeatable `--material FILE` or an explicit `--all-materials`: relative paths, SHA-256 and executable flags come from the actual bytes; an empty selection stays empty; links and reparse points are refused. The existing capture still refuses later changes or missing files.
- Add `--record-out FILE` (new file only, checked before setup) and `write_test_run_record`, plus `inspect_test_run_record` and `agent-runtime-inspect --test-run-record FILE --view summary|output|log|files`, a read-only reader of the saved record and its Attempt folders (no registration, rerun or PostgreSQL). Persistent results: `PostgresWorkflowInspectionRepository.read_module_run_result(result)` reads the trace, log and outputs of a returned `ModuleRunResult`.
- The four usage-pattern examples now go through this path: an ordinary v4 source folder, `register_module`, a written `selection.json`, code-computed `resources.json`, `--selection` and `--record-out`. `code_change` no longer asks the model to restate the checker line; pass evidence is the host rerun plus the logged command result that code attaches. Example definitions in fresh run folders change accordingly (prompt files end with a newline, the owner document lists the Modules).
- Existing release and record bytes, identity schemes, hash domains, replay and saved definitions are unchanged; the low-level exact arguments keep their meaning. No database migration.

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
