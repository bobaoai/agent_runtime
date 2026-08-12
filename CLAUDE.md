# Agent Runtime repository contract

## Product boundary

Agent Runtime registers immutable Module and Workflow releases, executes them
through provider-neutral interfaces, coordinates durable progress, records an
authoritative execution ledger, and exposes authorized inspection surfaces.

It does not own business workflow meaning, user entitlement policy, governed
business data, or Agency Platform control-plane behavior.

## Engineering rules

- Treat `designDoc/` as intent and stable contract; executable code and tests
  are the implementation truth.
- Keep logical responsibilities, physical source organization, and concrete
  implementation bindings separate in documents, diagrams, and registries.
- Use `snake_case`. Runtime source files use
  `module_subject_nominalized_action.py`.
- Every wall-clock instant is named `*_at_utc` and carries the canonical UTC
  form `YYYY-MM-DDTHH:MM:SS[.ffffff]Z` (`Z` only, never an offset), enforced
  through `validate_utc_timestamp` / `parse_utc_timestamp` /
  `format_utc_timestamp` in `contracts/registry_contract_validation.py`;
  durations are `*_seconds` integers. Timestamps never enter idempotency
  identity: deterministic records derive `recorded_at_utc` from durable
  facts, not caller clocks. Hosts project their own time semantics to this
  form at the Runtime API boundary.
- Provider SDKs, CLIs, PostgreSQL, and Temporal are replaceable
  implementations behind Runtime-owned contracts.
- Do not add a dependency on a host product or domain plugin.
- Add or update focused tests for every contract or behavior change.
- Preserve immutable release and execution records; projections may be rebuilt
  from their authoritative records.

Before committing, run the focused tests for the changed surface and the
architecture/packaging tests.
