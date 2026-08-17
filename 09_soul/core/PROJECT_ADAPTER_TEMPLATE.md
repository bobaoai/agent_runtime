# PROJECT ADAPTER TEMPLATE

Use this file to explain how `Hoveath` should behave inside a specific host repo.

Rename it to match the target project, for example:
- `PROJECT_ADAPTER_my_app.md`
- `PROJECT_ADAPTER_research_workspace.md`
- `PROJECT_ADAPTER_personal_os.md`

## Current Read Of The Repo

Describe what the repo is actually used for today.

Examples:
- software product repo
- research workspace
- personal operations workspace
- documentation and writing workspace

## Practical Center Of Gravity

List the working surfaces that matter most in this repo.

Examples:
- `src/` for implementation
- `docs/` for design and specs
- `plans/` for planning artifacts
- `data/` for analysis inputs

## Local Truths

Record constraints that Hoveath should not override.

Examples:
- which files are operational truth
- which workflows are mandatory
- whether plan-only behavior is preferred
- when external actions require confirmation

## When To Summon Hoveath

Describe when the soul layer should take a larger role.

Examples:
- architecture decisions
- prioritization and trade-off analysis
- review posture
- promotion of lessons into portable heuristics

## What Stays Local

List things that should remain repo-specific unless they repeat elsewhere.

Examples:
- local file conventions
- one-off operational workflows
- temporary migrations
- project-specific terminology

## Promotion Filter

Promote a lesson into `09_soul/` only if it is:
- repeated
- stable over time
- likely to matter in another repo
- more about work style or judgment than local file layout
