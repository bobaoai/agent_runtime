# Review observability: live Claude CLI process state without persisted process data

## 1. Outcome, authority and scope

A formal engineering review runs one Reviewer through the Agent Runtime Test Run and the Claude CLI. Today an operator sees nothing until the run ends, so a one-hour run with zero bytes looks the same as a slow review. The final review JSON then embeds the complete private process record. This change has two aims.

**While the CLI runs**, the review entry receives safe live state as it happens:

- process facts: PID, whether the process is alive, elapsed time and bytes per stream;
- one current-event summary, delivered when the event is received rather than on the next timer tick. It distinguishes:
  - an observed init;
  - model activity;
  - a tool request and a tool result, each with a safe tool category;
  - the final result;
  - a declared review command starting and finishing, with its existing `command_id`;
- a heartbeat while nothing is received, so a 0-byte stall stays visible.

**When the review ends**, the result file keeps only the verdict, short terminal facts, and the identity and validation evidence needed to accept the verdict.

Raw streams stay in the existing bounded in-memory capture and are released with the process. No process data, event history or tool output is written to files, a database or a history.

This document is the single CodeDesignBasis for both repositories in this goal. It is submitted for independent plan review and authorizes no implementation.

| Item | Baseline |
| --- | --- |
| Runtime source | `adb9c1b4324eb2708c4ad4888f3d15ab3f3c202c` (parent `191af930dcb3120a8635357721f6ea3212c8f6e4`; only Design 08 and its review record changed) |
| Diagnosis (read-only) | `ENGINEERING_REVIEW_OBSERVABILITY_DIAGNOSIS.md`, sha256 `b1e9dc2e181ec91d256bef544424cddc00b9f86bf654330cb487cb11611b777f`. Its five cited Runtime files are unchanged at this baseline, so its line numbers apply |
| Portable review code (read-only) | source commit `46f533e` |

**Owners.**

- Runtime Invocation (Design 08) owns:
  - the process host (`invocation/invocation_process_execution.py`);
  - the Claude Adapter (`invocation/invocation_claude_cli_execution.py`);
  - the declared-command session (`invocation/invocation_local_command_execution.py`).
- Runtime Test Run (`testing/conformance_local_test_run.py`) owns the synchronous self-test API and its record.
- Portable Software Delivery owns, all under `09_soul/governance/t0/validation`:
  - the review entry and the Test Run binding: `software_delivery/engineering_review.py`, `runtime_review.py`;
  - the semantic validator: `software_delivery/engineering_review_output.py`;
  - acceptance of a saved plan review: `software_delivery/engineering_review_input.py`.
- TP consumes installed copies of both packages, updated only through their official installation paths.

**Whole outcome.** The authorized outcome needs all three parts:

| Part | Content | Implemented in |
| --- | --- | --- |
| Part A | Runtime live observation | this repository |
| Part B | Portable final projection, saved-record acceptance and live display wiring | Portable |
| Usable formal entry | Portable's `engineering_review.py`, installed with a Part A Runtime in the review environment and proven by the §8.2 installed-entry liveness gate | review environment |

Part A alone is a staged result. Both repositories implement from this one reviewed basis, and each gets its own exact-commit implementation review (§10).

**Route and materiality.** Both parts change public contracts: optional Runtime parameters, and the formal review output. They are therefore `structural`.

**Design authority.**

- **Committed Design 08 revision.** Design 08 at `adb9c1b4` (file sha256 `3d8d883210cabcb7a4c18fa502678c9cc07e036e7f799bde9f47f8dd273a04c3`) adds §9 and §11.2 passages. They say:
  - only when the host explicitly requests live observation of the Claude CLI, Invocation may deliver a volatile safe summary. The summary distinguishes process liveness, received output volume, init, tool request and tool result;
  - a result's category may come only from correlating actually received structured requests and results in bounded per-call memory;
  - a declared command's `command_id` must match the frozen command plan and the actual resource invocation;
  - the summary has no thinking, text, tool arguments or output, keeps no history, forms no detailed tool view, and decides nothing;
  - ordinary runs keep the current handling;
  - categories not received or not correlatable stay unknown.
- **Review of that revision.** It passed the registered `design_contract_reviewer` on `claude-opus-5-5` `xhigh`: semantic validation passed, verdict passed, two optional prose notes. The compact result is `docs/proposals/review_observability/DESIGN08_REVIEW_R1.compact.json`, sha256 `d8acd3f6db4553666d56667ea481ad9d22ad0f769f0181ec17c345a5423b6660`, with no raw process data.
- **Other Design 08 sections used:**
  - §5.4 identifies the structured-output mechanism separately from task tools;
  - §8.1 maps the logical read, search and shell capabilities to Read, Grep and Bash;
  - §8.2 leaves command results to the owning validator;
  - §6.1 keeps secrets out of shared output;
  - §11.2 keeps raw capture and records;
  - §12 leaves exact signatures to code.
- **Inspection (Design 06) is not used.** §7 limits it to committed Ledger facts. Live state is a display-only notification, not an Inspection view or record.
- **T0 guards:**
  - `elapsed_seconds` is a process-local monotonic duration, never persisted, not an instant;
  - `process_id` is an OS-local display identifier, valid while the process lives;
  - `command_id` is the existing frozen-input identifier, shown only when Runtime bound it.

**Excluded:**

- stdout or stderr files, persisted or unbounded event queues, event histories, database or ledger writes, replay or recovery. The only buffer is the in-flight delivery buffer of at most 16 snapshots (§4.1);
- orchestration-tool integration;
- first-byte or init deadlines, SIGTERM/SIGHUP handling;
- permission, model-default or timeout changes;
- Codex live observation, and a Test Run CLI flag. Formal reviews resolved to `codex_cli` keep running unobserved, exactly as today (§4.5, §5.5);
- projection for the other six Portable Reviewers;
- deletion or rewriting of historical review files;
- one-hour reruns.

## 2. Current behavior and the gap

**Process host.** `_run_cli_process` starts `Popen(start_new_session=True)` (:252). It drains both pipes into a shared 16 MiB in-memory capture (:262–304), calling `on_stdout_line` outside the capture lock (:286), and polls every 20 ms for deadline, cancellation and resource closure (:327–348). After cleanup it returns the exact bytes or a typed exception (:380–407). It exposes nothing while waiting.

**Claude Adapter.** `observe` (:378–400), passed as `on_stdout_line` (:510–513), keeps only the init, the result and the response models. It ignores content blocks.

**Declared commands.** `LocalCommandSession.invoke` runs in the Runtime parent; the stdio MCP proxy only forwards to it. `invoke` binds `command_id` against the frozen command table before running anything (`invocation_local_command_execution.py:278–280`). It has no live hook.

**Test Run.** `run_local_workflow_test` returns only at the end. Its record holds the following (`execution_local_invocation.py:377–395`, `conformance_local_test_run.py:344`):

- `execution_log`;
- `provider_trace`: `argv`, `settings`, `environment`, `actual_prompt`, `raw_streams`, `error`;
- `failure_detail`: a timeout's `provider_error_message` embeds the full argv;
- `execution_trace`, `self_test_binding`, `usage`.

**Portable review.**

1. `run_review_test` calls Runtime.
2. `bind_review_record` checks the Reviewer and the `task_input` hash.
3. `review_engineering` checks identity, purpose, input and subject, then validates the complete in-memory record.
4. `engineering_review.main` writes that complete record to `--output` (:233–236).

`_validate_plan_review` later re-validates a saved plan review, including command evidence from `execution_log`.

**Gap.** In the diagnosis case the run took 3600.1 s, received 0 bytes, and ended `claude_cli_timeout` with exit code −9. Nothing was visible until the end, and the output then kept the private trace. A timer-only display would still miss short Read, Grep or Bash calls whose request and result both fall within one tick.

## 3. Flowmap

```mermaid
flowchart TD
    E["Portable engineering_review.main"] -->|progress_observer: stderr line printer| B["Portable runtime_review.run_review_test"]
    B -->|progress_observer; compatibility error before Runtime if unsupported| T["Runtime run_local_workflow_test"]
    T -->|claude_cli only| A["ClaudeAdapter(progress_observer)"]
    A -->|creates| CH["CliProgressChannel<br/>≤16 undelivered snapshots, relay thread"]
    A -->|on_stdout_line = observe; progress_channel| P["run_cli_process"]
    P -->|Popen| C["claude -p --output-format stream-json"]
    C -->|stdout and stderr bytes| P
    P -->|drain thread: each stdout line| OB["observe → _ClaudeEventProgress.record<br/>(bounded pending map)"]
    OB -->|recognized transition: event()| CH
    C -->|MCP proxy forwards command_id| L["LocalCommandSession.invoke<br/>after the frozen-plan check"]
    L -->|declared command started / finished: event()| CH
    P -->|process started, heartbeat after silence, process finished| CH
    CH -->|daemon relay, in order, never under a lock| O["observer: one JSON line to stderr"]
    P -->|unchanged bounded capture, trace, result| T
    T -->|unchanged complete in-memory record| B
    B --> V["review_engineering: binding checks and<br/>validate_engineering_review_output on the complete record"]
    V --> J["project_engineering_review_record: allowlist only"]
    J --> F["--output (new file, mode x)"]
    V -.->|never serialized| X["released at process exit"]
```

## 4. Part A: Runtime changes in this repository

### 4.1 `CliProgressChannel` and the process host (`invocation/invocation_process_execution.py`)

Add two frozen dataclasses, `CliProcessProgress` and `CliEventSummary`, and one class, `CliProgressChannel`, and export all three in `__all__`. `run_cli_process` and `_run_cli_process` gain one keyword parameter, `progress_channel: CliProgressChannel | None = None`. With `None` there is no channel, no extra thread and no behavior change.

**What the channel holds.** Only:

- the current `CliEventSummary | None`, and an `events_stopped` flag;
- a delivery buffer of at most `_PROGRESS_PENDING_LIMIT = 16` undelivered `CliProcessProgress` snapshots (private constant, not a parameter). Each entry leaves the buffer when it is delivered or dropped, so the buffer is not a history;
- a count of snapshots dropped since the last delivery;
- the time of the last publication;
- a stop flag, one `threading.Lock`, one `threading.Event` and one daemon relay thread.

**Producer method.** `event(summary: CliEventSummary)` is called by the Adapter's stdout observer and by the declared-command hook. Under the channel lock it:

1. stores `summary` as current;
2. builds one immutable snapshot from that summary plus the live process facts, read at this moment;
3. appends that snapshot to the buffer and sets the Event. If 16 snapshots are already waiting, it first drops the oldest and counts it.

It never calls the observer or does I/O. The summary that triggered a snapshot is therefore captured inside it: a later event creates a new snapshot and cannot change an earlier one. Before the process starts, `event` only stores the summary. After the process finishes, or once `events_stopped` is set, it is a no-op.

**Stop method.** `stop_events()` is called only by the Adapter summarizer after an internal fault (§4.3). Under the lock it:

- sets `events_stopped`;
- clears the current summary;
- publishes one `observation_stopped` snapshot, unless the process has finished.

Later heartbeats and `process_finished` carry `current_cli_event=None`.

**Host-only methods.** `_run_cli_process` drives the channel at three points:

- `_attach(process, started_monotonic, byte_counts)` right after the workers start (:320). It publishes `process_started` and starts the relay. `byte_counts` reads both capture lengths under the existing capture lock;
- `_heartbeat_if_quiet(now)` in the existing poll loop. It publishes `heartbeat` when nothing has been published for `_PROGRESS_HEARTBEAT_SECONDS = 10.0`, a private constant and not a parameter;
- `_finish()` once after cleanup and worker join, before the existing return or raise (:380–382). It publishes `process_finished` and closes the channel to new updates, then returns without waiting for the observer or relay. The daemon relay may finish delivering already queued updates after `run_cli_process` returns; it is never part of the Attempt's deadline or completion decision.

If Popen never happens, the channel is never attached and no snapshot is published.

**Delivery and dropping.** The relay thread waits on the Event. Under the lock it pops the oldest buffered snapshot and stamps it with the count of snapshots dropped since the previous delivery (`updates_dropped`), then resets that count. It calls the observer outside the lock.

- An observer that keeps up receives every snapshot in publication order. This includes bursts of up to 16 events written in one stdout chunk, which a single latest-value slot would have merged.
- A slow or blocked observer falls behind. When more than 16 snapshots wait, the oldest are dropped. The next delivered snapshot reports how many in `updates_dropped`, and dropped snapshots are never delivered later. A display with `updates_dropped > 0` is therefore visibly incomplete; it never claims to be a full replay.
- At `_finish()` the buffer may still hold snapshots. The daemon relay may deliver them after the process runner returns, but no execution path waits for it. If the one-shot host exits while updates are still queued or the observer is blocked, they may be lost; the final Runtime result and Portable review summary carry the terminal facts. This live display is not a complete replay.
- If the observer raises `Exception`, the channel stops calling it for that process. Execution, capture, deadline, cleanup and result are unchanged, and nothing is recorded.

**Locks and threads.** `event` may run concurrently on the stdout drain thread and the command-session thread. The channel lock serializes them, and each call builds its snapshot from its own summary, so fields of different events never mix.

The lock order is always channel lock, then capture lock. The drain thread holds the capture lock only while appending bytes and calls `on_stdout_line` after releasing it (:277–291), so it never holds it when it enters the channel. Neither lock is held while the observer runs. The drain loop, poll loop, deadline and cleanup therefore never wait for the observer.

Display order follows arrival at the channel. It is not a transcript, and stdout buffering may place a request after the command start that it caused.

**Isolation from execution.** Observation must never change a run. Every channel method that an execution path calls, `event`, `stop_events`, `_attach`, `_heartbeat_if_quiet` and `_finish`, catches any `Exception` from the channel's own work and returns normally. That work includes building a snapshot, reading byte counts, buffer handling and starting the relay thread. `_finish` never joins the relay, so a blocked display cannot consume the active RunBudget or Attempt deadline after the CLI has finished.

On such a fault the channel marks itself failed. It publishes nothing more for that process, not even `process_finished`, and its relay exits. The observer then simply stops receiving updates.

A channel fault therefore never reaches:

- the drain thread's `except Exception` that records `stream_error` (:299–302);
- the poll loop's `except Exception` that records `resource_closed` (:352–354);
- cleanup, or the returned result or exception.

Only `Exception` is isolated. `KeyboardInterrupt` and other `BaseException` keep their existing process-runner paths; there is no new relay join in that path. Exceptions raised by the observer itself are handled in the relay, as described under "Delivery and dropping".

### 4.2 Fields

`CliProcessProgress` is what the observer receives.

| Field | Question it answers | Values and meaning | Producer | Downstream handling | Verification |
| --- | --- | --- | --- | --- | --- |
| `update_trigger` | Why was this snapshot published? | `process_started`: the CLI process started. `event_received`: a recognized transition (§4.3, §4.4) was just received; `current_cli_event` is that event. `heartbeat`: nothing was published for 10 s; `current_cli_event` repeats the last event and is not a new one. `process_finished`: Runtime finished waiting and cleanup; it is published as the last snapshot, with delivery best-effort if the observer is blocked. `observation_stopped`: the Adapter's event summarizer hit an internal fault (§4.3). `current_cli_event` is `None` from here on, while process facts, heartbeats and `process_finished` continue | channel | The printer prints delivered snapshots; a repeated heartbeat is not a new event. The final Runtime record remains authoritative even if a blocked observer misses the last snapshot | real_run per value; injected fault for `observation_stopped` |
| `updates_dropped` | Were earlier updates lost before this one was delivered? | Integer ≥ 0: snapshots dropped since the previous delivery because more than 16 were waiting. `0` means none were lost | channel relay | The printer prints it, which makes loss visible | real_run: blocked observer gives ≥ 1; an observer that keeps up gives 0 |
| `process_id` | Which OS process leads the CLI? | Positive `Popen.pid`; host-local; may be reused after exit | process host | The operator correlates a stalled run with `ps`/`lsof`, as the diagnosis requires | real_run: equals the PID the child prints |
| `process_running` | Was the leader alive when the snapshot was built? | `true`: `poll()` returned `None`. `false`: the process has exited and been reaped. A final `true` occurs only when cleanup failed | process host | Display only | real_run |
| `elapsed_seconds` | How long has Runtime waited on this process? | Finite non-negative monotonic seconds; not rounded up | process host | Display only | real_run: non-decreasing |
| `stdout_byte_count`, `stderr_byte_count` | How much output has arrived? | Capture lengths, within the shared bound. `0` means no observable output, not lack of model progress | process host | Display only | real_run: final values equal the delivered bytes |
| `current_cli_event` | What did the review do last? | `None` when no recognized event has been received yet; otherwise the `CliEventSummary` below | Adapter | Display only | as below |

`CliEventSummary` is immutable and has three fields.

| Field | Question it answers | Values and meaning | Producer | Downstream handling | Verification |
| --- | --- | --- | --- | --- | --- |
| `phase` | Which activity was received? | `init_observed`: `system/init`. `model_activity_observed`: a `system/thinking_tokens` event, or an assistant event with only text or thinking blocks; content and token values are never read. `tool_requested`: an assistant event with a `tool_use` block. `tool_result_observed`: a user event with a `tool_result` block. `final_result_observed`: `result`. `declared_command_started` / `declared_command_finished`: from the §4.4 hook. Any other event, such as `rate_limit_event` or a new CLI type, is not recognized and publishes nothing | Adapter `_ClaudeEventProgress`; §4.4 hook | Display only | deterministic table; real-stream replay; real_run |
| `tool_category` | Which kind of tool? | `None` for non-tool phases. **Request:** from the last `tool_use` block's exact `name`. `read`, `search` and `shell` come from the inverse of `NATIVE_TOOLS`. `declared_command` is `LOCAL_COMMAND_CLI_TOOL_NAME`. `declared_callback` is a name in this Attempt's frozen `ProviderToolSessionBridge.cli_tools`. `structured_output` is the CLI's `StructuredOutput` tool, under native structured output only. `other_tool` is any other name. **Result:** the category held for the last result block's `tool_use_id` in the bounded map (§4.3), else `unknown`. **Declared-command phases:** `declared_command` | Adapter | Display only; never guessed from text, order or result body | deterministic table; bounded-correlation tests |
| `command_id` | Which declared command is running? | Only in declared-command phases: the ID `LocalCommandSession.invoke` has just bound in its frozen table. `None` everywhere else; tool arguments and output are never read | §4.4 hook | Display only; never a validation input | real_run |

**Never in a snapshot:**
- prompt, argv, settings, environment, cwd and paths;
- raw bytes, event bodies, model text, thinking and token values;
- tool arguments, shell commands, outputs and results;
- `tool_use_id`, session IDs, usage and result payloads;
- any list of earlier events.

### 4.3 Claude Adapter (`invocation/invocation_claude_cli_execution.py`)

Add an internal per-Attempt class `_ClaudeEventProgress(channel, *, native_structured_output)`, released with the Attempt.

**What it holds:**

- a pending map of request ID → category, for requests whose result has not arrived. Each entry is removed by its result. At most 64 entries are held; the oldest is dropped on overflow, and its result later shows `unknown`. The map is touched only by the stdout drain thread and is never copied anywhere;
- the last `model_activity_observed` flag, so repeated thinking-token events publish only when activity starts. Every init, tool request, tool result and final result publishes, even when identical to the previous one, because each is a real received event.

**Methods:**

- `bind_callback_tools(names)`: receives the frozen bridge names before the CLI starts.
- `record(event)`: reads only `type`, `subtype`, block `type`, `tool_use.id`, `tool_use.name` and `tool_result.tool_use_id`. It updates the map, builds a `CliEventSummary` and calls `channel.event(summary)` for recognized transitions.
- `declared_command(command_id, finished)`: builds the declared-command summary and calls `channel.event`.

**Malformed structure is handled by explicit checks, not exceptions.** `decode_cli_event` guarantees only a top-level dict, so `record` checks each nested level:

- a `message` that is not a dict, or `content` that is not a list, counts as no blocks. An assistant event then counts as model activity; a user event publishes nothing;
- a block that is not a dict is skipped;
- a `tool_use` whose `id` is not a non-empty `str` still publishes `tool_requested`, but is not entered in the pending map;
- a `name` that is not a `str` gives `other_tool`;
- a `tool_result` whose `tool_use_id` is not a non-empty `str` gives `unknown`.

Only `str` values are used as map keys or compared with tool-name sets, so unhashable values never reach a set or dict.

**Isolation boundary.** The bodies of `record` and `declared_command` each run inside `try/except Exception`. On an unexpected fault the object marks itself stopped and calls `channel.stop_events()` (§4.1), which cannot raise. Every later call then returns at once. Neither method ever raises `Exception` to its caller.

**Wiring in `ClaudeAdapter`:**

- `ClaudeAdapter.__init__` gains `progress_observer: Callable[[CliProcessProgress], None] | None = None`, and rejects a non-callable value with `TypeError`.
- Only when an observer is set, `_execute`, before the CLI starts:
  1. creates `CliProgressChannel(progress_observer)` and `_ClaudeEventProgress` before local-command and callback preparation (:448–456);
  2. passes `on_declared_command=events.declared_command` to `LocalCommandSession` (:450);
  3. calls `events.bind_callback_tools(provider_tools.cli_tools)` after the bridge exists (:455);
  4. adds `progress_channel=channel` to the `self._run` call.
- **Placement in `observe`.** All existing statements stay, in their existing order. `events.record(event)` is called only after they have updated `trace` (`initialization`, `result`, `response_models`), `result` and `event_error`, immediately before the existing `return event_error is None`. Lines that fail to decode are never passed to it. The callback's return value, and with it the existing `observer_stopped` and `stream_error` handling, therefore depends only on existing code.
- Without an observer, `process_runner` and `LocalCommandSession` receive exactly today's arguments, so existing test doubles keep working.
- Trace, result, failure mapping, model checks and output validation are unchanged.

### 4.4 Declared-command hook (`invocation/invocation_local_command_execution.py`)

`LocalCommandSession.__init__` gains `on_declared_command: Callable[[str, bool], None] | None = None`. `invoke` calls it at two points:

- `(command_id, False)` right after `command = self._commands[command_id]` succeeds (:280), that is, after the frozen-plan check;
- `(command_id, True)` once in the existing `finally`, only when the started call happened.

An unknown ID fails the existing check first, so it never reaches the display. The hook call is wrapped in `try/except Exception: pass`, the Adapter's `declared_command` is itself non-raising (§4.3), and it only enters the channel lock briefly. Responses, records, sandboxing and process handling are unchanged.

### 4.5 Test Run (`testing/conformance_local_test_run.py`)

`run_local_workflow_test` gains `progress_observer=None`. It is checked in this order:

1. Before setup, with the `parent_run` check: `TypeError` unless the value is `None` or callable.
2. It is passed to `ClaudeAdapter` only when it is not `None` and the resolved transport is `claude_cli`. The transport may come from the call, the Workflow or workspace parameter file, or the Runtime default; only the resolved value matters.
3. For any other resolved transport (today `codex_cli`) the observer is accepted, never attached and never called. The Codex executors receive exactly today's arguments, and the call, record and errors are identical to a call without an observer. No Claude progress is claimed for a Codex run.

The record and CLI are unchanged. The docstring states:

- the observer runs on a Runtime thread and should return promptly;
- delivery is in order through a buffer of at most 16 snapshots, and `updates_dropped` reports any that were lost;
- heartbeats repeat the last event;
- the observer is called only for `claude_cli`; for other transports it is never called, so no progress is shown;
- snapshots are display-only, not records;
- zero bytes does not mean the model made no progress.

### 4.6 Interfaces and compatibility

| Surface | Change | Compatibility |
| --- | --- | --- |
| `run_cli_process` | optional `progress_channel` | `None` means identical behavior and threads |
| `CliProgressChannel`, `CliProcessProgress`, `CliEventSummary` | new exports | Used only when an observer is requested |
| `LocalCommandSession.__init__` | optional `on_declared_command` | Omitted means identical behavior |
| `ClaudeAdapter.__init__` | optional `progress_observer` | Omitted means today's arguments to `process_runner` and `LocalCommandSession` |
| `run_local_workflow_test` | optional `progress_observer` | Omitted means same validation, record and errors. With a non-Claude transport it is ignored, and the run is identical to an unobserved one |
| Record, CLI, Codex executors, `run_agent_example` | none | unchanged; Codex reviews keep working with or without an observer |
| Generated API reference (both copies) | regenerated from docstrings | checked by `test_checked_in_documents_equal_source` |
| Packaged Design Contract mirror (`src/agent_runtime/design_contract/agent_runtime_08_agent_execution_adapter_contract.md`, `manifest.json`) | regenerated with the existing `tools/build_agent_runtime_design_contract_bundle.py`; never hand-copied | A mechanical projection of the reviewed Design 08 at `adb9c1b4`. The mirror is stale at the baseline: `--check` reports Design 08 and `manifest.json`. It is checked by `--check` and `test_generated_design_contract_bundle_matches_canonical_docs` |
| Package | next dev version and a CHANGELOG entry | Installation is a separate delivery step |

## 5. Part B: Portable projection and display (same basis)

### 5.1 Validation stays on the complete in-memory record

`run_review_test`, `bind_review_record`, `review_engineering` and `validate_engineering_review_output` keep their inputs and checks:

- the `task_input` binding;
- identity and purpose;
- subject re-preparation;
- checklist and verdict rules;
- required-command evidence from the selected Attempt's `execution_log`.

`review_engineering` still returns the complete record to programmatic callers. Only `engineering_review.main` changes what it writes: after validation it calls `project_engineering_review_record(record)` and writes that. The complete record is never serialized and is released at process exit.

### 5.2 Projection allowlist

| Kind | Keys |
| --- | --- |
| Copied unchanged when present | the 16 identity scalars in `_PLAN_REVIEW_RUNTIME_IDENTITY`; `status`, `module_id`, `review_purpose`, `semantic_validation`, `semantic_input`, `output`; `failure_class`, `execution_budget` |
| Derived | `failure_code`, `provider_process`; `command_evidence`, only when commands are declared and validation passed |
| Omitted (not exhaustive) | `execution_log`, `provider_trace`, `execution_trace`, the `failure_detail` body, `self_test_binding`, `usage`, `input_bindings`, `source_sha256`, `execution_parameter_sources`; live snapshots are never written |

`semantic_input` is the frozen review input, not process data. Saved-plan re-validation compares its body.

The probe in §10 shows what `_validate_plan_review` strictly needs: `status`, `module_id`, `review_purpose`, `semantic_validation`, `semantic_input` and `output`. The identity scalars are optional when a zero-command plan review is accepted, but they feed the implementation Reviewer's `runtime_identity` view. When `command_evidence` is present, three of them are required (§5.4).

### 5.3 Derived fields

| Field | Question it answers | Values and meaning | Producer | Downstream handling | Verification |
| --- | --- | --- | --- | --- | --- |
| `failure_code` | Which Runtime failure code ended a run that did not complete? | `null`, or `failure_detail.failure_code` unchanged | projection | The reader applies the Design 08 §13.2 meaning; no Portable branch | deterministic per status |
| `provider_process` | How did the Provider process end? | `null` without `provider_trace`. Otherwise `exit_code`, `stop_reason`, `output_complete` and the decoded stdout/stderr byte counts; each `null` when absent | projection | The reader tells "no output, then timeout" from a partial run; no branch | deterministic: completed, timeout, cancelled, pre-start failure |
| `command_evidence.log_complete` | Was the selected Attempt's log complete? | boolean | `engineering_command_evidence` | A required command needs `true` | deterministic |
| `command_evidence.commands[]` | What is each declared command's validated outcome? | One row per declared command: `command_id` and one `disposition`. `reported_unavailable`: Runtime reported it unavailable at least once. `completed_exit_nonzero`: a completed run exited non-zero, and it was never unavailable. `completed_exit_zero`: it completed, and every completed run exited 0. `not_observed`: neither. The first match in that order applies. No return codes, counts or output | same | For a required command: `not_observed` → reject; `reported_unavailable` → verdict must be `blocked`; `completed_exit_nonzero` → not `passed`; `completed_exit_zero` → no constraint | deterministic matrix equal to the full-log result |

### 5.4 Saved-record acceptance

Split `_validate_command_evidence` into **extraction**, which turns a Runtime log into per-command observations with every current structural check, and **acceptance**, which applies the current rules. Expose `engineering_command_evidence(review_input, execution_record)` so the projection uses the same extraction.

| Record | Path |
| --- | --- |
| `execution_log`, no `command_evidence` | Current path, unchanged |
| `command_evidence`, no `execution_log` | The top-level identity scalars `attempt_id` and `module_run_id` must be non-empty, and `managed_runtime` must be `True`. All three are copied by §5.2 from the Test Run record (`execution_local_invocation.py:381–386`) and are required here, not optional. Then each declared command exactly once, none undeclared, dispositions from the closed set, and the §5.3 rules. `command_evidence` itself holds only `log_complete` and `commands` |
| Both | Rejected |
| Neither | Current behavior: passes only when no command is required |

Without this split, probe [4] in §10 shows that a projected plan review declaring a required command is rejected.

### 5.5 Live display wiring

- `runtime_review.run_review_test(..., progress_observer=None)`:
  - reserves `progress_observer` against `runtime_kwargs`;
  - forwards it to a Runtime whose `run_local_workflow_test` has the parameter, whatever transport the `runtime_kwargs` or parameter files select. Portable does not pin the transport today, and does not resolve or check it here. That stays Runtime's job (§4.5);
  - when an observer is requested but the installed `run_local_workflow_test` lacks the parameter, raises a compatibility error before calling Runtime. The message names the missing parameter, the installed version and the required order (Runtime Part A before Portable B3). The TP environment has `0.2.0.dev9`. This is an unsupported pairing that rollout order prevents (§7 C1 before C2), so it fails for any transport, because the entry cannot know the resolved transport before Runtime resolves it;
  - without an observer, it calls Runtime exactly as today, so the other six Reviewers and other callers are unaffected.
- `engineering_review.main` always passes a printer. The printer outputs depend on the pairing:

  | Pairing | Outcome |
  | --- | --- |
  | New Runtime, `claude_cli` | Progress lines, then the result |
  | New Runtime, `codex_cli` | No progress lines, and the review completes exactly as today. That is not a stall signal; the projected result is the same kind of file |
  | Old Runtime | The compatibility error above: stderr error JSON, exit 2, no output file |
- **stderr line shapes.** Each delivered snapshot becomes one stderr line whose only top-level key is `runtime_process_progress`, holding `dataclasses.asdict(snapshot)`: `update_trigger`, `updates_dropped`, process facts and the nested event. The existing failure JSON keeps its top-level keys `error`, `error_type` and `error_code`. Consumers tell the two apart by top-level key, not by assuming the error is always the last line: a slow relay may deliver a queued progress line after it. The printer catches its own exceptions and adds no interpretation.
- **Post-review display drain.** Only after `review_engineering` has returned with the Runtime terminal result and completed its semantic checks, the Portable entry may wait up to 1 s for a `process_finished` line if its printer observed `process_started`. The printer signals an in-memory Event after that line is written. A Codex run or a call that never started Claude skips this wait. A blocked printer times out and the entry proceeds with the unchanged result; this wait is outside Runtime's RunBudget and Attempt completion path and creates no process log.

## 6. Failure handling

| Situation | Behavior |
| --- | --- |
| Observer raises | Not called again for that process; execution, capture, deadline, cleanup and result unchanged; nothing recorded |
| Observer slow or blocked, even past process exit | Only the daemon relay thread waits; at most 16 buffered snapshots; the oldest are dropped beyond that, and the next delivery carries `updates_dropped` if delivered. `_finish` publishes its last snapshot without joining the relay, so `run_cli_process` and the Attempt terminal decision do not wait. The Portable entry's optional 1 s display drain happens only after the Runtime result and semantic checks have returned; timeout there never changes status or verdict |
| Many events in a burst | Each builds one snapshot under a short lock; an observer that keeps up gets all of them in order |
| Drain thread and command thread publish at once | Serialized by the channel lock; each snapshot is internally consistent; order is arrival order |
| Process never started | No snapshots; existing exception |
| Hook raises, or the model requests an unknown `command_id` | Swallowed; or no hook call. The command result is unchanged either way |
| Unrecognized event | Nothing published; the summary is unchanged |
| Malformed nested event: non-list `content`, non-dict block, missing, non-string or unhashable `id`, `tool_use_id` or `name` | Handled by §4.3 checks: publishes what is safely known (`other_tool`, `unknown`) or nothing. Execution unchanged |
| Unexpected fault inside the Adapter summarizer or declared-command summary | One `observation_stopped` update, then no further event summaries for that Attempt. The existing `observe` recording, callback return value, trace, result, failure mapping and verdict are unchanged |
| Unexpected fault inside a channel method (`event`, `stop_events`, `_attach`, `_heartbeat_if_quiet`, `_finish`) | Channel disabled and updates stop. Never `stream_error`, `resource_closed`, a cleanup error or a changed result |
| Ordinary stream or Provider errors, e.g. a duplicate `result`, missing init, non-zero exit, timeout or read error | Existing handling, identical with and without an observer. Nothing is suppressed |
| `KeyboardInterrupt` or user cancellation | Existing paths and `claude_cli_interrupted` mapping, identical with and without an observer |
| Result without a held request | Category `unknown` |
| Non-callable observer | `TypeError` before setup |
| Observer with a resolved `codex_cli` transport, explicit or from a parameter file | Never attached or called; the Codex executor gets today's arguments; run, record and errors identical to unobserved; no progress lines |
| Observing Portable entry on an old Runtime (unsupported pairing) | Compatibility error before Runtime or a Provider, for any transport; exit 2, no output file. Prevented by installing Part A first |
| Projection input missing (e.g. no `provider_trace`) | `null`; nothing invented |
| `command_evidence` extraction fails after validation passed | stderr error JSON, exit 2, no output file; not a verdict |

## 7. Implementation order

- **P0 (done).** Design 08 §9/§11.2 committed at `adb9c1b4` and passed review (§1). Its packaged mirror is regenerated in A3.
- **Runtime:**
  - A1: channel, dataclasses and process-host attach, heartbeat and finish, with their tests;
  - A2: the declared-command hook, `_ClaudeEventProgress` and Adapter wiring; existing Adapter, local-command and Test Run suites pass without edits;
  - A3: the Test Run parameter and docstrings; regenerate the API reference, and the Design Contract mirror with `python tools/build_agent_runtime_design_contract_bundle.py`;
  - A4: version and CHANGELOG, the full suite, the gated Provider test, then an exact commit for its own implementation review.
- **Portable:**
  - B1 and B2 together (projection, compact command evidence and saved-record acceptance; they do not depend on Part A);
  - B3, the display wiring. It is never installed anywhere before a Part A Runtime is installed there;
  - then an exact commit for its own implementation review.
- **Delivery, each step with its own authorization:**
  - C1: install the reviewed Runtime artifact in the TP review environment through normal delivery;
  - C2: update TP's Portable copy only with `install.py --source-root <Portable> --target-root <TP> --update`;
  - C3: run the mandatory installed-entry liveness gate (§8.2) through the installed entry. It must pass.

## 8. Verification

- **Markers:** each new or changed test carries exactly one registered marker: `deterministic` or `real_run`, or `fake_run` for the §8.1 observer-on/observer-off comparison tests only.
- **Local processes:** Runtime local-process `real_run` tests start only small Python children through the real `run_cli_process`, or declared commands through a real `LocalCommandSession`. They need no Provider gate.
- **Provider tests:** these need `AGENT_RUNTIME_REAL_RUN=1` or `PORTABLE_REVIEW_REAL_RUN=1` and use `claude-opus-5-5` `xhigh` with a tiny task. The Runtime-stage Provider case (§8.1) may be reported unverified for Stage A. The installed-entry liveness gate (§8.2) is mandatory for the whole outcome. If it is not run, fails, or is inconclusive, the outcome is incomplete, and it is never reported as passed.
- **Stand-ins:** Runtime's observer-on/off and Test Run attachment comparisons (§8.1) use a named scripted local Claude executable; the Codex comparison uses the existing executor-replacement pattern (`tests/test_agent_runtime_codex_self_test.py:58`). They are marked `fake_run`, as Runtime's `pyproject.toml` registers it, and are not evidence for real Claude streaming; the mandatory installed-entry `real_run` remains separate. Portable's signature and record-shape tests use deterministic stubs, not Provider evidence. Real CLIs cannot produce malformed nested events or injected faults on demand.
- **Compatibility:** the existing suites, unedited, prove that callers who do not observe are unaffected.

### 8.1 Runtime tests (new `tests/test_agent_runtime_cli_progress.py`)

| Requirement | Test and marker | Defect it detects |
| --- | --- | --- |
| Short tool activity is delivered on receipt | real_run, default 10 s heartbeat, recording observer that returns at once. The child prints `system/init`, a `tool_use` named `Bash`, then waits 0.3 s, prints the matching `tool_result`, waits 0.3 s, prints `result`, waits 0.3 s more, and exits (≈1.2 s total). Expected: `event_received` snapshots for `init_observed`, `tool_requested`/`shell`, `tool_result_observed`/`shell` and `final_result_observed`, in that order, each with `process_running=true` (the child stays alive after `result`) and received within 2 s of start, all with `updates_dropped=0`; no heartbeat; then `process_finished` | a request or result overwritten before delivery (the current defect) |
| Burst in one stdout chunk | real_run: the child writes `system/init`, a `tool_use` named `Grep` and its `tool_result` in a single write, then exits. All three `event_received` snapshots are delivered in order with `updates_dropped=0` | merging of near-simultaneous events |
| Stalled 0-byte heartbeat | real_run with `_PROGRESS_HEARTBEAT_SECONDS` patched to 0.1: a silent child for 1.5 s gives at least 3 `heartbeat` snapshots, all running, 0/0 bytes, rising elapsed, `current_cli_event=None`, and no `event_received` | an invisible stall |
| No observer wait after process exit | real_run: the observer blocks on an Event at its first call and is released only after `run_cli_process` has returned or raised. Three cases: (a) normal exit, a child emitting a few events and exiting 0; (b) timeout, a silent child with timeout 1 s; (c) failure, output limit. Each also runs with `progress_channel=None` as a baseline. For each case the return code, captured bytes, exception type and `stop_reason` match; `run_cli_process` returns or raises without waiting for the Event or a fixed relay-join interval. The observer is then released for test cleanup | `_finish` blocking on the observer; display changing termination |
| Explicit loss under a blocked observer | real_run: the observer blocks on an Event at its first call while the child emits 30 request/result events; it is released before the child exits. Delivered snapshots are a subsequence of publication order. The first delivery after release has `updates_dropped ≥ 1`, and the delivered count plus the sum of `updates_dropped` equals the number published. Capture, return value and deadline are unchanged, and `run_cli_process` never waits more than 1 s for the observer | silent loss; masquerading as complete replay; display blocking execution |
| Producer interleaving | real_run: while a child streams tool events, a second thread calls `channel.event` with declared-command summaries. Every delivered `current_cli_event` equals exactly one submitted summary, byte counts never decrease, and the run completes within its timeout | mixed fields, deadlock |
| Lifecycle | real_run: early exit 3 with no output; normal exit with both streams; exit 1; output limit; timeout 1 s; user cancellation. Each publishes `process_started` and `process_finished` (`process_running=false`, counts equal to delivered bytes) with the existing result or exception unchanged. A promptly returning test observer can await delivery after `run_cli_process` returns without extending its deadline; a blocked observer is allowed to miss the final display update | observation altering results; inaccurate terminal snapshot |
| Declared-command binding | real_run on the existing `LocalCommandSession` pattern (`tests/test_agent_runtime_local_commands.py:101–123`) with a recording hook: a declared ID gives exactly `(id, False)` then `(id, True)`; an unknown ID gives no call; a raising hook leaves responses and records as they are without a hook | unbound `command_id`; display changing commands |
| Malformed nested events | deterministic: `record` on events with non-list `content`, a string or `None` block, a `tool_use` with a missing, integer or list `id`, an integer or list `name`, and a `tool_result` with a dict `tool_use_id`. It never raises; it publishes only the §4.3 result for each case; the pending map gains no entry from an unusable ID | a malformed event crashing the drain path |
| Summarizer and channel faults are isolated | deterministic: `record` and `declared_command` with an injected internal `RuntimeError` publish one `observation_stopped` update, then nothing, and never raise. real_run: with injected faults in the channel's `event`, `stop_events`, `_attach`, `_heartbeat_if_quiet` and `_finish` against a real child, `run_cli_process` returns or raises exactly as with `progress_channel=None`: same return code, captured bytes, exception type and `stop_reason`; never `stream_error` or `resource_closed` | observation faults becoming execution failures |
| Same run with or without an observer | fake_run, substitute named: a scripted local Claude executable answering the existing preflight like `_fake_cli` and printing a fixed stream on `-p`, through the real `ClaudeAdapter` and the real `run_cli_process`. Each stream runs once without an observer and once with a recording observer. The streams: (a) valid init, model, malformed nested events and a valid result; (b) the same with an injected summarizer fault; (c) the same with an injected channel fault; (d) existing errors: duplicate `result`, missing init, non-zero exit with an error result; (e) user cancellation. For each, the two runs have equal terminal status, failure class and code, output, usage, and normalized trace. The trace is compared after dropping per-run paths and identities; it covers `initialization`, `result`, response models, exit code, `stop_reason`, `event_error` and raw stream bytes. (d) keeps its existing failure in both modes; (e) gives `claude_cli_interrupted` in both | observation changing a terminal result, trace or failure mapping; existing errors suppressed |
| Category and phase table | deterministic: Read/Grep/Bash → read/search/shell; the local-command name → `declared_command` with no ID; the frozen callback name → `declared_callback`; `StructuredOutput` → `structured_output` only under native output; other names → `other_tool`; the last block decides; repeated thinking-token events publish once; `rate_limit_event` and unknown types publish nothing | guessed categories; flooding |
| Bounded correlation | deterministic: parallel requests answered in reverse order each get their own category; a missing or evicted request (after 64 newer ones) → `unknown`; the map is empty at the end; no `tool_use_id` in any snapshot | invented categories; unbounded state |
| Nothing raw | real_run: sentinels in `tool_use.input`, command argv and output, assistant text and thinking, `tool_result` content and stderr appear in no delivered snapshot's `repr`; deterministic: the dataclass field sets equal §4.2 | content leakage |
| Observer raises | real_run: raises on its first call → never called again; the result equals the run without an observer | display fault changing execution |
| No observer | real_run: `progress_channel=None` → no extra thread and an identical result; the existing process, local-command, Claude native-tools, local-workflow, self-test, budget and API-reference suites pass unedited | compatibility regression |
| Test Run validation and Codex | deterministic: a non-callable observer → `TypeError` with no `.runtime` created. fake_run comparison: a local Codex definition run through the real `run_local_workflow_test`, with the Codex executor replaced by a recorder (pattern at `tests/test_agent_runtime_codex_self_test.py:58`). It runs once with explicit `transport_kind="codex_cli"` and once with `codex_cli` from a workspace execution-parameter file (helpers in `tests/test_agent_runtime_execution_parameters.py`). With and without an observer, the recorder receives identical constructor and invoke arguments, the records are equal after normalization, no `ValueError` is raised, and the observer is never called | Codex reviews broken or altered by observation; Claude progress claimed on Codex |
| Test Run attaches Claude observation after transport resolution | fake_run: a tiny registered one-node Module and a scripted local Claude executable run through the real `run_local_workflow_test`, ClaudeAdapter and `run_cli_process`. Supply a recording observer in four cases: explicit `transport_kind="claude_cli"`, `claude_cli` resolved from a Workflow parameter file, `claude_cli` resolved from a workspace parameter file, and omitted transport resolved to Runtime's Claude default. Each case must deliver `process_started` and at least one safe `event_received` before return; the same run without an observer keeps its terminal result and record after normalizing per-run identities. This is mandatory Stage A evidence for the public Test Run wiring, and remains labelled fake because the Provider executable is scripted | Test Run dropping the observer, or forwarding it only for an explicit transport |
| Near-deadline completion is not converted to timeout by display | fake_run with the same real Test Run, ClaudeAdapter and process runner plus a scripted local Claude executable and controlled RunBudget/Attempt clock: the CLI completes successfully with less than 1 s of effective budget left, while the observer remains blocked through the process runner's return. Compare observer-on and observer-off runs after normalizing per-run identities: both are `completed`, with the same output, trace-relevant fields and failure code (`None`); the observation path never yields `provider_completed_after_deadline` or `run_budget_expired_before_commit`. The test controls the clock around this boundary rather than relying on wall-time sleeps | a relay join consuming the remaining RunBudget and changing a valid verdict to timeout |
| Actual Claude path | real_run gated by `AGENT_RUNTIME_REAL_RUN=1`, `claude-opus-5-5` `xhigh`. A tiny agent Test Run with read and shell tools and one declared command reads one material and runs that command once. Expected: `event_received` snapshots, all with `process_running=true` and received before `process_finished`, including `tool_requested` with a native category and the matching `tool_result_observed` with the same category. `declared_command_started`/`finished` lines with the declared ID are also present; no cross-producer order is asserted. Record keys are unchanged. Stage A may report this case unverified; the whole outcome relies on the §8.2 gate | wiring broken against the installed CLI; events delivered only at exit |

### 8.2 Portable tests (same basis)

| Requirement | Test and marker | Defect it detects |
| --- | --- | --- |
| Nothing raw in the final file | deterministic: sentinels are planted in `execution_log` streams; the `provider_trace` fields `argv`, `settings`, `actual_prompt`, `error` and `raw_streams`; the `failure_detail` text; `self_test_binding`; `execution_trace`; and `usage`. None appears in the serialized projection, and the key set equals §5.2. No key-name substring scan is used, because plan text may mention those names | raw data surviving projection |
| Terminal facts | deterministic: completed, timeout (0 bytes, −9), cancelled and pre-start failure records give the expected `failure_code` and `provider_process` | wrong or invented facts |
| Plan-review reuse | deterministic: a projected zero-command record is accepted by `_validate_plan_review`; removing any of the six necessary keys is rejected; a historical full record is still accepted | compact output not reusable; regression |
| Command semantics | deterministic matrix over fixed logs (none, success, non-zero, unavailable, unavailable plus non-zero, repeats, incomplete): the full-log and compact forms give equal results for every verdict. Also rejected: an unknown or missing ID, a disposition outside the closed set, `managed_runtime` not true, both forms present. Rows hold only `command_id` and `disposition` | weakened rules or hidden history |
| Display wiring | deterministic: `progress_observer` is reserved against `runtime_kwargs`. With a stub Runtime signature that has the parameter, an observing call forwards it unchanged for `--transport claude_cli`, for `--transport codex_cli` and with no transport argument. With a stub lacking the parameter, an observing call raises the compatibility error before Runtime is called, for every transport, while a non-observing call proceeds. Each progress line has only the `runtime_process_progress` key with exactly the snapshot fields; consumers distinguish any error JSON by its top-level key, not its line position | smuggled arguments; Codex reviews rejected on a new Runtime; ambiguous stderr |
| Codex-resolved formal review | deterministic: a fixed Codex-profile record with no snapshots delivered is projected and accepted like a Claude record, with an empty progress stream. Runtime-side Codex behavior is proven in §8.1 | Codex result handled differently by the entry |
| Installed-entry liveness gate (mandatory for the whole outcome) | real_run, delivery step C3; specified below | live tool activity not shown on the real path; a raw or invalid final file |

**Installed-entry liveness gate.** This is the real-path proof of the goal. It runs once through the installed entry and cannot be replaced by local, `fake_run` or replay evidence.

- **Gate:** `PORTABLE_REVIEW_REAL_RUN=1`, delivery step C3. It uses the TP review environment after C1–C2 and the installed `engineering_review.main`, with `--transport claude_cli --model claude-opus-5-5 --effort xhigh` and the existing run budget.
- **Fixture:** a plan review of a tiny plan of at most 40 lines, with no extra context documents. `--commands` declares exactly one required command:

  | Key | Value |
  | --- | --- |
  | `command_id` | `liveness_probe` |
  | `argv` | `["python", "-c", "import time; time.sleep(3); print('liveness ok')"]` |
  | `cwd` | `scratch` |
  | `timeout_seconds` | 60 |
  | `network_policy` | `denied` |
  | `expected_result` | "exit 0 and prints liveness ok" |

  Plan reviews already carry declared commands without a repository (`write_review_resources`). The existing validator rejects a review whose required command has no execution evidence, so a Reviewer that never calls the tool fails the gate.
- **Observation:** the harness runs under the configured TP Python, where `psutil` is already a Runtime dependency. It starts the entry as a subprocess and reads its stderr line by line as it arrives. It records only the needed line categories and monotonic arrival times in memory, and parses lines by their top-level key (§5.5). From `process_started`, it records the reported CLI `process_id` and reads that process's creation time with `psutil`. On receipt of each required tool event line, it immediately checks that the same PID still has that creation time, is running and is not a zombie. A snapshot built while alive but delivered after the CLI exited therefore cannot satisfy the gate. A failed or unavailable OS check leaves this real-path gate unverified, not passed. The entry's post-review display drain (§5.5) allows a responsive observer to deliver `process_finished` after Runtime has fixed the terminal result; it cannot change that result.
- **Mandatory assertions (all must hold):**
  1. a `runtime_process_progress` line with `update_trigger=event_received`, phase `tool_requested`, category `declared_command` and `process_running=true`, with the independent CLI-process liveness check succeeding when the line is read;
  2. a later line with phase `tool_result_observed`, category `declared_command` (correlated, not `unknown`) and `process_running=true`, with that same independent check succeeding when this line is read;
  3. lines with phases `declared_command_started` and `declared_command_finished`, each carrying `command_id="liveness_probe"`, from the trusted `LocalCommandSession` binding. Only their presence is asserted. Their order relative to the stdout-derived lines is not, because the two producers' arrival order is not a transcript (§4.1);
  4. lines 1 and 2 are each read from stderr before the `process_finished` line, with a positive arrival interval and `elapsed_seconds` below that line's. These are ordering checks; the independent live-PID checks in 1 and 2 prove actual liveness at observer receipt rather than trusting an earlier queued snapshot;
  5. the entry exits 0 with stdout `output_validation.status == "passed"` and `verdict == "passed"`. The output file:
     - has exactly the §5.2 key set;
     - includes `command_evidence` with `log_complete=true` and `liveness_probe` as `completed_exit_zero`;
     - has no `execution_log`, `provider_trace`, `execution_trace`, `failure_detail` or snapshot data;
     - is accepted by `_validate_plan_review` through the compact path.
- **Outcome:** the gate passes only when all five hold. It is failed or unverified, never passed, in any of these cases:
  - the Reviewer skips the command (validation fails);
  - progress shows only init and final events;
  - events arrive only at exit (1, 2 or 4 fails);
  - a snapshot was built while the CLI lived but delivered after exit (the independent liveness check in 1 or 2 fails);
  - any other assertion fails;
  - the gate is not run.
- **Cost:** one tiny subject, one 3 s command and one run. A rerun follows only a failure whose cause has been identified.

**Commands.**

- Runtime: `python -B -m pytest -q -p no:cacheprovider tests/test_agent_runtime_cli_progress.py -m deterministic`, then the same with `-m real_run` (Provider cases skip without the gate), then the full `python -B -m pytest -q -p no:cacheprovider` `python tools/build_agent_runtime_api_reference.py --check` and `python tools/build_agent_runtime_design_contract_bundle.py --check`. The last one fails at the baseline because the mirror is stale; after A3 it must pass.
- Portable: run its configured pytest with the same marker selection.
- Report deterministic, local real_run and Provider results separately, and keep pre-existing failures separate.

## 9. Completion

- **Stage A (staged only):**
  - A1–A4 are done;
  - §8.1 passes, including the non-Provider positive Test Run attachment cases for explicit, Workflow-file, workspace-file and default Claude selection; only the Provider case may be reported unverified at this staged result;
  - the existing suites pass unedited;
  - the API reference and the packaged Design Contract mirror match their sources;
  - the Runtime commit has passed its implementation review.
- **Stage B:**
  - B1–B3 are done;
  - the deterministic part of §8.2 passes;
  - the Portable commit has passed its implementation review against this basis and plan review.
- **Whole outcome:** A and B, then C1–C3 under their own delivery authorizations. The outcome is complete only when the §8.2 installed-entry liveness gate has passed with all five assertions. None of these completes it:
  - C3 having run;
  - progress showing only init and final events;
  - a gate that is unverified, failed or inconclusive.

  Stage A alone may report its own Provider case unverified; the whole outcome may not.
- **Follow-up boundaries:** Codex observation; projection for other Portable Reviewers; any decision on existing saved review files, which are not edited, deleted or relabelled.

## 10. Review admission without raw persistence

**One basis, two implementation reviews.** Portable binds a plan review to later implementation reviews only by content (`engineering_review_input._validate_plan_review`). Each later review must supply:

- the same plan bytes and sha256, wherever the file is;
- exactly the same `acceptance_criteria`;
- a completed, passed, input-v6, zero-subject plan-review record.

Nothing ties the basis to the repository under review. TP's route checker only requires a non-empty `design_basis_ref` (`src/audit/engineering_change_routing.py:273–274`). Each repository's exact commit is reviewed separately, with the same plan bytes, plan-review record and criteria.

**Model and subject bounds.** Every formal review runs the registered `engineering_change_reviewer` on `claude_cli`, with `--model claude-opus-5-5 --effort xhigh` given explicitly. No lower model or effort is used. Cost is controlled by subject size:

- **Plan review:** this plan; context limited to Design 06, Design 08 at `adb9c1b4`, and the diagnosis; zero commands.
- **Implementation reviews:** the exact commit; `--read` only for unchanged collaborators named in §4–§5; `--commands` only for that repository's affected tests and the API check.
- **Run budget:** the existing four-layer resolution.

**No raw persistence before B1.** Until B1 lands, `engineering_review.main` writes complete records. Reviews of this change therefore use the existing in-memory path:

1. `review_engineering(executor=...)`, with `main`'s own executor composition (`write_review_resources` into a temporary directory, then `run_review_test`);
2. validation of the complete record in memory;
3. a new file holding only the 22 keys of §5.2: the identity scalars plus `status`, `module_id`, `review_purpose`, `semantic_validation`, `semantic_input`, `output`.

No exception, framework or committed code is involved.

**No-model evidence.** Both checks ran in memory, with nothing written.

- **Compact probe** on the passed plan review `CODE_DESIGN_REVIEW_R7.json` (sha256 `c325de8b1cad3cd84af5d760e8b044e561cbc9cd173b718f319af875fb64a668`, zero commands):
  - **[1]** the historical full record is accepted;
  - **[2]** the 22-key compact record is accepted;
  - **[3]** removing any of the six necessary keys is rejected, while removing an identity scalar is accepted;
  - **[4]** the compact form with one declared required command is rejected ("Required commands have no actual execution evidence"), which B2 addresses.
- **Event replay.** The §4.2–§4.3 rules were run over two saved real `claude-opus-5-5` review streams:
  - every result was correlated to its request's category: `shell`, `read`, `declared_command` or `structured_output`;
  - the pending map peaked at 2 and ended empty;
  - with event-triggered publication, the streams produced 19 and 41 `event_received` updates for 156 and 258 events. Every tool request and result was published individually, so a display line per event is modest;
  - only closed values were produced;
  - the replay found `system/thinking_tokens`, `rate_limit_event` (between a request and its result) and `StructuredOutput`, which the rules now handle.
- **Limits:** these prove reuse and mapping rules on real shapes, not a new review run or the live wiring. The wiring is covered by the gated tests.

## 11. Cross-repository ownership under one basis

**Portable Software Delivery** implements Part B (§5, §6, §7 B1–B3, §8.2) from this basis, source `46f533e`. If this basis misses a needed module, interface, test or dependency, Portable returns that as a finding against this plan rather than writing a second basis.

**Runtime provides:**

- `run_local_workflow_test(..., progress_observer=None)`: attached for `claude_cli`, accepted and ignored for other transports;
- `CliProcessProgress` and `CliEventSummary`, exactly as in §4.2;
- event-triggered, in-order delivery through a 16-snapshot buffer, with explicit `updates_dropped` when an observer falls behind, and heartbeats after silence;
- `command_id` only from the `LocalCommandSession` binding;
- observation faults isolated from execution (§4.1, §4.3), visible as `observation_stopped` or as updates stopping;
- nothing raw, and an unchanged record;
- all of it in the next Runtime dev version after its implementation review.

**TP operator:**

- installs the reviewed Runtime first, then updates Portable only with `install.py --update` (§7 C1–C2);
- never edits installed files by hand.

Runtime changes no Portable code, schema, prompt or historical file.
