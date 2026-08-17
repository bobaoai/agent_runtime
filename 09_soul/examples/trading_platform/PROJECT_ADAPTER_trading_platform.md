# PROJECT ADAPTER - trading_platform

Use this file to explain how `Hoveath` should behave inside `trading_platform`.

## Current Read Of The Repo

`trading_platform` is a trading intelligence workspace that combines:
- portfolio and exposure monitoring
- historical market data and analytics
- research ingestion
- agent-oriented decision support
- design-doc writing for an AI-native hedge fund operating model

This repo is both a software project and a thinking workspace. A large share of the important work happens in architecture docs, strategy docs, and product narrative, not only in Python implementation files.

## Practical Center Of Gravity

- `designDoc/` for architecture, strategy, product narrative, and operating model truth
- `src/` for implementation of the trading platform runtime
- `data/` for runtime state, snapshots, archives, and generated outputs
- `README.md` for project framing and quick-start context
- `09_soul/` for the imported portable soul layer

## Local Truths

- `Hoveath` is the analysis, design, review, and memory layer. It is not the trading engine.
- The current code-level runtime remains local to this repo, especially `src/cli/tradectl.py`, `src/agents/base.py`, and `src/agents/orchestrator.py`.
- `designDoc/reconsolidation_v1.md` is the architecture anchor unless a newer local design doc explicitly supersedes it.
- This repo already prefers plan-first and docs-first work for meaningful changes.
- Runtime data paths stay local and canonical under `data/`, `token/`, and related config surfaces. Do not introduce fallback-path loops.
- Broker connectivity, market-data handling, analytics contracts, and agent runtime semantics stay repo-local unless a stable cross-project lesson emerges.

## When To Summon Hoveath

- architecture direction and repo-wide integration choices
- report writing and investor-facing narrative work
- design-doc drafting and restructuring
- review posture focused on logic, risks, regressions, and system fit
- prioritization and trade-off analysis
- deciding what lessons should remain local versus promoted into the portable soul layer

## What Stays Local

- Schwab-specific config and runtime behavior
- `src/` module boundaries and implementation details
- project-specific terminology around the AI-native hedge fund narrative
- local design documents, staged migrations, and temporary operational constraints
- any path, credential, or data convention that belongs specifically to this repo

## Promotion Filter

Promote a lesson into `09_soul/` only if it is:
- repeated across multiple tasks
- stable over time
- useful beyond trading-specific implementation details
- more about judgment, communication, planning, or operating method than local file layout
