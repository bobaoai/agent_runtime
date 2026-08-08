# Remediation Status for Whole-Repository Review

## Current decision

The remediation candidate resolves all 15 release blockers reported against
baseline commit `0652c05`. It also resolves verified correctness defects that
could corrupt identity, authorization, time ordering, or registered execution
inputs. The candidate is eligible for independent post-remediation review; it
is not declared production-admitted by this artifact.

## Resolved release-blocker surfaces

| Surface | Current invariant |
| --- | --- |
| JSON Schema handling | Traversal distinguishes schema nodes from container maps, and Codex normalization resolves local references before required-null handling. |
| Durable contract | One public backend protocol reuses the canonical host commands and topology records; the removed legacy Temporal adapter and start DTO no longer form a second contract family. |
| Temporal cancellation | Cancellation is a typed, idempotent, acknowledged Update with a queryable terminal snapshot and event projection. |
| Replay identity | Exact external-event, authorization-binding, fence, and Attempt-workspace retries return or recover the original logical result. |
| External events | Authorized terminal edges complete the Runtime, and application cross-checks the snapshot against its token. |
| Public boundary | Contract modules no longer import execution implementations; public `ExternalEvent` is the durable backend event. |
| Host time | Host timestamps use the shared canonical `Z`-suffixed UTC validator. |
| Provider tools | Tool definitions are validated as an exact set rather than an accidental tuple order. |
| Provider context | Claude and Codex now share one code-owned registered-context preparation path; only their admitted Execution Profiles and provider invocation implementations differ. |
| Workspaces | Attempt IDs are path-safe, exact retries reuse only their own marker-bound workspace, and foreign workspaces fail closed. |
| Release inspection | Candidate releases without an admission record render as not yet admitted rather than crashing inspection. |
| Optional Temporal install | Base installs resolve the Temporal descriptor without importing the optional SDK. |
| Initial standalone cutover | The first `0.1.0.dev0` release explicitly requires host import migration and does not recreate deleted physical package layouts. |
| Design bundle | All documents referenced by the standalone contract index are present in the deterministic packaged bundle. |

## Additional correctness closure

- Module Attempt periods reject an end before their start.
- Cell artifact idempotency uses tuple-unambiguous stable identities.
- Skill IDs are validated before filesystem path construction.
- Module compilation accepts only the fixed
  `runtime_modules/<module_id>/module_registration.json`, `prompt.md`, and
  concrete Schema Asset channel; it no longer extracts guessed Skill headings.
- In-memory authorization ledgers and Runtime Release Registry reads are
  synchronized with their publication paths.
- Runtime architecture inspection automatically performs repository closure
  checks in a source checkout while retaining wheel-safe internal validation.
- PostgreSQL release persistence has focused transaction, projection, and
  identifier tests and is exported from the Registry public surface.
- Provider failure-detail encoding is shared and byte-bounded.

## Verification evidence

| Gate | Result |
| --- | --- |
| Base test suite | `238 passed, 3 skipped` |
| Optional Temporal environment | `242 passed, 3 skipped` |
| Real Temporal recovery, isolation, replay, and cancellation | `6 passed` |
| Deterministic Design Contract bundle check | Passed |
| Clean-wheel and base-install boundary | Passed inside the base test suite |
| Diff whitespace validation | Passed |

## Explicit non-blocking debt

These findings do not represent hidden completion. They remain separately
visible because their correct resolution needs a benchmark or a public data
contract decision rather than another release-blocker patch.

| Review IDs | Required follow-up |
| --- | --- |
| `011`, `012`, `013`, `031`, `032`, `050`, `054`, `057` | Consolidate repeated validation, time, stable-ID, content-addressing, and hash helpers without coupling target contracts back to predecessor ledgers. |
| `015`, `038`, `049`, `056` | Benchmark and remove repeated serialization or full-trace/full-registry validation only where measured execution size justifies an incremental index. |
| `037` | Define workspace retention and secure cleanup separately from exact-Attempt retry semantics. |
| `040` | Pin Codex JSONL usage-event semantics before choosing cumulative-last or per-event sum behavior. |
| `051` | Replace optional floating-point cost estimates only with an admitted decimal or integer-minor-unit public contract; token accounting remains authoritative and unaffected. |

The immutable baseline findings remain in `verified_findings.json`; this file
records current disposition without rewriting the historical review result.
