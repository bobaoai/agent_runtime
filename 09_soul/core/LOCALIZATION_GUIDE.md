# LOCALIZATION GUIDE

This guide explains how to bring `Hoveath` into a new repo while keeping the digital self portable and the project adapter local.

## Goal

Clone `09_soul/` into a new workspace, then localize only the files that should reflect the new project and the new user environment.

## Localization Order

### 1. Localize First

These files should be reviewed immediately in every new repo:

- `09_soul/core/USER.md`
- `09_soul/core/DEVELOPMENT_INTEGRATION.md`
- `09_soul/core/PROJECT_ADAPTER_TEMPLATE.md` -> rename for the host repo
- `AGENTS.md`
- `.cursor/rules/01_soul_router.mdc`

Reason:
- `USER.md` defines who Hoveath is helping
- `DEVELOPMENT_INTEGRATION.md` defines the portable standard for embedding Hoveath into day-to-day work
- the project adapter defines how Hoveath should behave in this repo
- `AGENTS.md` and runtime rules are the local Cursor projection

### 2. Usually Keep As-Is At First

- `09_soul/core/SOUL.md`
- `09_soul/core/COMMUNICATION.md`
- `09_soul/axioms/`
- `09_soul/skills/`

Reason:
- these are the imported manager baseline and long-lived style layer
- they should change only after enough local usage reveals a stable mismatch

### 3. Localize Opportunistically

- `09_soul/skills/INDEX.md`
- any skill with repo-specific paths
- memory automation docs under `09_soul/periodic_jobs/`
- tool-related docs that assume external credentials or side projects

## Promotion Rule

When a new repo teaches Hoveath something:

1. Keep the lesson local first.
2. Add it to the project adapter or local notes.
3. If it repeats and feels portable, promote it into:
   - `09_soul/memory/`
   - `09_soul/axioms/`
   - `09_soul/skills/`
4. Before promotion, remove repo-local naming, path assumptions, and tool-surface specifics so the lesson stays portable.
5. Only project the minimal active subset into runtime files.

## Deprecation Rule

If an imported skill depends on missing local projects or tooling:

1. keep the file
2. add a `Local Status` note
3. mark it as deprecated but preserved
4. leave enough footprint for future restoration

## New Repo Checklist

1. Copy `09_soul/`
2. Create a project-specific adapter from `PROJECT_ADAPTER_TEMPLATE.md`
3. Update `USER.md`
4. Update root `AGENTS.md`
5. Add or adjust a soul router rule
6. Localize only the skills that the new repo will actually use

## Naming Rule

Keep the folder name as `09_soul/` for portability.
Treat `Hoveath` as the digital self inside that layer.
