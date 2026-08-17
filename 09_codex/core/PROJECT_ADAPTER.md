# PROJECT ADAPTER - Agent Runtime

## Current Read Of The Repo

`the_agent_runtime` is an independently publishable, domain-neutral Agent
Runtime. It registers immutable Module and Workflow releases, executes them
through provider-neutral interfaces, coordinates durable progress, records an
authoritative Execution Ledger, and exposes authorized inspection.

It is not the trading platform, an Agency Platform control plane, a business
workflow library, or an Entitlement authority.

## Practical Center Of Gravity

- `designDoc/` owns product and architecture intent.
- `designDoc/the_*.md` is the complete project-facing T0 authority surface.
- `src/agent_runtime/` and `tests/` own current implementation truth.
- `src/agent_runtime/design_contract/` is generated package projection, not an
  authoring surface.
- `09_claude/` and `09_codex/` are session-interface projections, not Design
  authority.
- `CURRENT_HANDOFF.md` states the current bounded task.

## Local Truths

- Runtime has six peer logical responsibilities: Registry, Execution,
  Invocation, Durability, Ledger, and Inspection.
- Physical source directories and concrete bindings such as PostgreSQL,
  Temporal, Claude, Codex, and HTML are different architectural axes.
- Runtime core must not import a host product, domain Skill tree, business
  workflow semantics, or governed domain-data implementation.
- `agent_runtime_00_*` is the T1 root; same-domain non-`00` Design Contracts are
  T2. Metadata cannot override that filename-defined parent.
- Correctness is more important than ceremonial completion. Repair the owning
  design and code instead of adding a shadow path around a root defect.
- Existing dirty files belong to their current work lane. Read
  `CURRENT_HANDOFF.md` before changing or staging them.
- Reuse `../trading_platform/.venv/bin/python` for Runtime validation when this
  repository has no local virtual environment.
- When validation needs a representative trading-platform instance, first
  state the exact Runtime contract or behavior under test and the instance
  selection criteria, then choose the matching instance deliberately.

## When To Summon Hoveath

- architecture and responsibility-boundary review;
- Design Contract restructuring;
- module independence and cross-dependency analysis;
- root-cause review of release, execution, durability, ledger, or inspection
  defects;
- deciding which lesson is portable and which remains Runtime-specific.

## What Stays Local

- Runtime source layout and public Python API;
- provider, PostgreSQL, Temporal, and renderer implementations;
- package build, migration debt, release status, and current test evidence;
- task-specific review findings and temporary handoffs.

## Promotion Filter

Escalate a lesson to portable-governance maintenance only when it has repeated
beyond this Runtime, is independent of this repository's files and
technologies, and improves cross-project judgment or governance. Project work
does not need to locate or edit the portable distribution source.
