# Changelog

All notable changes to Agent Runtime are recorded here. The project remains at
`0.2.0.dev0`; entries below describe the first standalone development line and
may require a clean development database before the first public release.

## Unreleased

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
- A domain-neutral authoring inventory example that compiles deterministic and
  Agent Modules plus one Workflow without a host Skill tree.
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
