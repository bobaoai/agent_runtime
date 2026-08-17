---
title: Timestamp Semantic Contract
status: candidate
layer: T0
t0_layer_id: the_timestamp_semantic
canonical_owner: designDoc/the_timestamp_semantic.md
owned_system_object: time-bearing data semantics
language: en
reader_persona:
  - Domain Architect
  - Schema Owner
  - Runtime Maintainer
  - Distributed Systems Engineer
---

# Timestamp Semantic Contract

**Purpose**: Define stable product-wide timestamp roles, storage forms,
clock-domain semantics, comparison law, daylight-saving-time handling, and
distributed-clock safety without embedding object-class inventories,
scheduler policy, market-specific helpers, or implementation history.

**Required reader gain**: A reader can name a time field by what it means,
choose a storage form without inventing precision, decide whether a comparison
is legal, and understand when distributed clock evidence is required.

## 0. Contract Capsule

```yaml
layer: T0
t0_layer_id: the_timestamp_semantic
status: candidate
canonical_owner: designDoc/the_timestamp_semantic.md
owned_system_object: time-bearing data semantics
scope:
  - stable timestamp role vocabulary
  - stable instant, calendar-day, and market-session storage forms
  - persisted-record and mutable-record time invariants
  - clock-domain ownership and comparison law
  - IANA timezone and daylight-saving-time law
  - distributed-clock profile, health evidence, and conservative validity law
  - code-owned per-class time-role registration and generated projection boundary
non_goals:
  - per-object-class role matrix rows in Design Doc prose
  - artifact freshness thresholds or domain lifecycle policy
  - scheduler timezone, cron expression, task cadence, or trading-session anchor policy
  - exchange-calendar helper names or market-data refresh behavior
  - legacy migration inventory, implementation history, or changelog
  - non-time schema and domain semantics
inputs:
  - designDoc/the_charter.md
outputs:
  - timestamp role and storage vocabulary
  - comparison and clock-domain invariants
  - per-class registration contract
truth_surfaces:
  - designDoc/the_timestamp_semantic.md
  - logical:timestamp_semantic_registry
  - logical:timestamp_comparison_validator
runtime_triggers: none; schema validators consume the code-owned registry
downstream_consumers:
  - every schema, API, event, Artifact, decision, and Runtime record with time-bearing fields
  - domain freshness and scheduling contracts
  - Product Authorization and Agent Runtime clock-fencing contracts
open_decisions:
  - release and compatibility model for exact persisted-class registrations
  - service ownership and versioning of semantic-role and distributed-clock predicate registries
  - public generated-inspection schema for class, predicate, and clock-profile coverage
review_gate: design_doc_review and independent time-semantics conformance review
runtime_surface_ledger: code-owned registrations and generated inspection are the only allowed owners of implementation and admission facts for roles, storage forms, exact classes, predicates, converters, calendars, and distributed-clock profiles; this contract carries no current coverage inventory
verification_hooks:
  - role, storage, exact-class, predicate, converter, calendar, DST, and distributed-clock conformance
```

## 1. Two-axis Field Model

Every time-bearing field combines two independent dimensions:

```text
semantic role + storage form
```

The field name combines a semantic role token with a storage suffix. Roles
ending in `_at` or `_date` drop that ending when the suffix already carries the
same instant or date meaning. For example:

```text
observed_at_utc
period_start_at_utc
effective_session_date_market
expiry_at_utc
recorded_at_utc
horizon_calendar_day_utc
```

The containing object supplies object identity. Do not repeat the object name
inside the time field. Bare or context-dependent names such as `date`,
`timestamp`, `as_of`, `generated_at`, or `report_date` are not admitted in new
contracts.

A time value must never imply more precision than the source provides. When an
external Source supplies only a date, the owning domain uses an admitted date
storage or an explicitly documented conservative projection with precision and
timezone metadata. It must not present that projection as the exact event
instant.

## 2. Stable Role Vocabulary

The product recognizes nine roles representing eight concepts. Interval start
and end are separate roles but one paired concept.

| Role | Meaning | Boundary |
| --- | --- | --- |
| `observed_at` | When an event occurred or material became observable in the external or domain world | Not when the local system stored it |
| `period_start_at` | Inclusive start of a covered interval | Paired with `period_end_at` |
| `period_end_at` | End of a covered interval under the owning interval policy | Paired with `period_start_at`; ordering is mandatory |
| `effective_at` / `effective_date` | When a rule, policy, appointment, decision, or state begins to apply | Not when it was announced or recorded |
| `expiry_at` / `expiry_date` | When an authority, rule, grant, request, or validity window ceases to apply | Not the time at which future work is scheduled |
| `recorded_at` | When the authoritative store atomically committed this record | Assigned by that store, not by a caller, worker, or provider |
| `updated_at` | When a mutable record or projection was last changed | Forbidden on immutable event or archive records |
| `horizon_date` | The latest date through which the content or data is substantively current | Not publication or commit time |
| `scheduled_for_at` | When the system intends to perform future work | Not expiry, deadline, or validity end |

Every persisted record has `recorded_at_utc`. An embedded immutable value that
is not independently persisted may rely on its containing record and must not
copy that commit time as if it owned a second record identity.

Immutable facts append new records and do not use `updated_at`. Mutable
projections may use `updated_at_utc`, but their source event chain remains the
authority. `received`, `issued`, `requested`, `committed`, `projected`, and
`built` describe actions, not additional timestamp roles. The owning record's
`recorded_at_utc` represents its authoritative commit.

## 3. Stable Storage Forms

| Storage suffix | Meaning | Required representation |
| --- | --- | --- |
| `_at_utc` | An instant on the UTC timeline | RFC 3339 / ISO 8601 value with UTC offset, canonically `Z` |
| `_calendar_day_utc` | A UTC calendar-day slice, typically for a 24/7 domain | ISO date `YYYY-MM-DD` |
| `_session_date_et` | A declared New York market-session date | ISO date `YYYY-MM-DD` |
| `_session_date_ct` | A declared Chicago market-session date | ISO date `YYYY-MM-DD` |
| `_session_date_market` | A domain or asset-specific market-session date | ISO date plus sibling IANA `market_tz` and owning calendar policy |

Role and storage must be compatible:

- `recorded_at`, `updated_at`, and `scheduled_for_at` use `_at_utc` only.
- `period_start_at` and `period_end_at` use the same storage family and appear
  together. Instant intervals use `_at_utc`; session intervals use one common
  session-date form.
- `effective` and `expiry` use `*_at_utc` when precision is an instant and a
  calendar or session date form when precision is a date.
- `horizon_date` uses a calendar or session date form, not a fabricated
  instant.
- `observed_at` normally uses `_at_utc`; date-precision observations use an
  admitted date representation or an explicitly typed conservative projection.

An instant and a date are not interchangeable. Midnight, end-of-day, market
close, and session membership are domain conversions requiring an explicit
calendar, timezone, precision policy, and typed converter.

## 4. Code-owned Per-class Registration

The exact class-by-role matrix is owned by the Timekeeping Class Registry
[Time-Registry]. Each registered class assigns every role one state:

- `REQ`: one compatible field for the role is required;
- `OPT`: a compatible field is allowed but not required; or
- `FORBIDDEN`: the role must not occur on the class.

Unregistered persisted classes and unauthorized time fields fail validation.
Nested records use exact typed class IDs. Registration lookup is exact. A
wildcard class identifier is not a registration and must be rejected.
Every concrete persisted class therefore requires its own exact registration.
A future closed-family mechanism would require a typed member set, one owner,
and explicit validator support; a string containing `*` never supplies that
authority.

An ordinary class registration or `REQ`/`OPT`/`FORBIDDEN` row change is a
code-and-schema contract change. It requires domain-owner review, compatibility
analysis, tests, and regenerated inspection, but it is not a Charter amendment
and does not require editing this T0 document. A new semantic role, storage
form, comparison meaning, or clock invariant requires T0 review. It requires a
Charter amendment only if it changes a constitutional commitment.

The generated matrix projection is the human-readable current inventory. A
copied table in a Design Doc, schema comment, Skill, or UI is not registration
authority.

## 5. Clock Domains

A clock domain identifies which authority produced a time claim and what error
or ordering guarantees apply.

| Clock or chronology | Permitted authority |
| --- | --- |
| Authoritative store clock | Assigns that store's `recorded_at_utc` at atomic commit |
| External Source clock | Supports `observed_at` with provenance and declared precision; never substitutes for local commit time |
| Worker or process wall clock | May schedule or observe local work under an admitted profile; does not assign another store's authoritative commit time |
| Provider timestamps | Observational execution evidence only unless a typed domain projection admits them |
| Durable backend history time | Infrastructure chronology only; not automatically a domain event, Artifact freshness anchor, or Runtime ledger commit time |
| Monotonic process clock | Measures local durations and timeouts; never persists as a cross-process business timestamp |

UTC representation does not make two clocks identical. Two services may both
emit UTC while carrying different uncertainty and health. Ordering across
authoritative ledgers should use immutable causal references and monotonic
versions or high-water marks where available; wall-clock time does not replace
causal order.

## 6. Comparison Law

The default legal comparison has the same semantic role, compatible storage,
declared calendar where applicable, and an admitted clock-domain relationship.
Raw comparisons outside that case are forbidden.

1. Instant comparison occurs on the UTC timeline.
2. Calendar-day comparison requires the same calendar meaning.
3. Session-date comparison requires the same market timezone and calendar
   policy.
4. Date-to-instant or session-to-instant comparison requires a named typed
   conversion; no caller may invent midnight or close locally.
5. Cross-role comparison requires a registered predicate with one fixed
   business meaning and typed operands.
6. Validity intervals are half-open unless their owning contract explicitly
   registers another semantics: `effective <= t < expiry`.
7. A semantic mismatch, missing converter, unknown calendar, or incompatible
   clock profile raises a typed error and never degrades silently to a boolean,
   `UNKNOWN`, or nearby fallback.

### 6.7 Protected Predicates

A protected cross-role or cross-clock decision uses one code-registered named predicate. Its registration binds operand roles, storage forms, calendars,
clock domains, distributed-clock profile, health-evidence requirements,
comparison meaning, and permitted operation purposes.

This heading is the stable `Timestamp §6.7` contract anchor used by dependent
authorization and Runtime contracts. Cross-clock predicates also apply the
conservative validity-window law in §8.

Exact predicate IDs and current coverage belong to the code-owned predicate
registry and generated inspection. Callers consume a registered predicate
rather than reproducing comparison logic in SQL, adapters, prompts, or local
utilities. A storage-wrapper helper that validates only timestamp syntax cannot
authorize a protected decision.

## 7. Timezone and DST Law

Local civil time uses IANA timezone identifiers. Fixed offsets such as `-05:00`
cannot stand in for a region whose offset changes with daylight saving time.

- Persist instants in UTC and retain the IANA timezone or calendar identity
  needed to interpret local wall-clock intent.
- Resolve market-session dates through the owning exchange or domain calendar,
  including holidays, early closes, and session boundaries.
- Resolve a nonexistent spring-forward time or ambiguous fall-back time only
  through an explicit registered policy. The result records the timezone and
  disambiguation decision or rejects the input.
- Measure physical-hour lookbacks using UTC instants or a monotonic duration,
  not local wall-clock arithmetic.
- Compute business days and sessions through the owning calendar, not by
  adding or subtracting a fixed number of days.
- Delegate scheduler timezone, task cadence, and market anchor choices to the
  owning T1 scheduler or domain contract.

Parsers should rely on standard timezone and ISO 8601 implementations, then
translate parser failures into typed semantic errors. Hand-written timezone
allowlists, fixed-offset substitutions, or partial timestamp regexes are not
equivalent validation.

## 8. Distributed Clock Safety

When an authorization, grant, lease, execution fence, or other protected action
compares instants committed by different clock domains, the operation pins one
immutable `DistributedClockProfile`. The profile identifies:

- the participating clock domains and admitted time sources;
- maximum uncertainty for each domain;
- conservative safety margin;
- required clock-health evidence and freshness policy;
- the predicates and operation purposes for which it is valid; and
- fail-closed behavior.

Each participating service supplies fresh, immutable `ClockHealthEvidence`
with source, measured offset and uncertainty, health result, observation time,
validity end, authoritative commit time, and profile identity. The protected
record references the profile and evidence by exact IDs and hashes.

Unless a stronger online Product-owned protocol eliminates the cross-clock
assertion, an authoritative commit is accepted only inside the conservative
half-open window:

```text
effective_at_utc + safety_margin
    <= recorded_at_utc
    < expiry_at_utc - safety_margin
```

The deployment profile supplies measured numeric bounds. This T0 contract does
not invent one universal skew constant. Missing, expired, unhealthy, regressed,
mismatched, or unverifiable profile or health evidence fails closed.

Monotonic authority versions and event high-water marks establish causal
ordering. They do not prove clock health or validity. Conversely, synchronized
clocks do not prove causal order, current authorization, or single-writer
fencing. Protected distributed decisions require every form of evidence named
by their owning contract.

No protected cross-clock action may claim conformance from storage-wrapper
helpers alone.

## 9. Conformance Invariants

Time semantics are non-conformant when:

- a bare or object-prefixed time field has context-dependent meaning;
- a caller, worker, provider, or durable backend assigns another store's
  `recorded_at_utc`;
- an immutable event or archive record uses `updated_at` instead of appending a
  new record;
- an instant is fabricated from a date without precision, timezone, calendar,
  and conversion policy;
- interval endpoints use different storage or clocks without an admitted
  conversion;
- raw code compares different roles, calendars, or clock domains;
- a fixed offset, local machine timezone, or fixed-day arithmetic stands in for
  IANA timezone and calendar behavior;
- timestamps are used as a substitute for causal versions, or causal versions
  as a substitute for clock validity;
- an unregistered class writes time-bearing records; or
- a manually copied per-class matrix is treated as current registration truth.

## References

- `[T0-Charter]` [Product Charter](the_charter.md)
- `[T0-Artifact]` [Artifact Graph Contract](the_artifact_graph.md)
- `[T0-Authz]` [Product Authorization and Entitlement Governance Contract](the_product_authorization.md)
- `[T0-Runtime]` [Agent Runtime Contract](the_agent_runtime.md)
- `[Time-Registry]` project-local code-owned timestamp semantic registry
