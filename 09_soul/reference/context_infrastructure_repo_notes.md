# Notes On `context-infrastructure` Repo

Reference repo: [grapeot/context-infrastructure](https://github.com/grapeot/context-infrastructure)

## What The Reference Repo Shows Well

- A root `AGENTS.md` that acts as the session router
- A `rules/` directory that separates identity, user context, communication, workspace routing, axioms, and skills
- A memory model that combines observations, reflection, and promotion
- A clear distinction between reusable templates and personal accumulated content

## What Is Reusable

- The idea of a source layer for persona and user context
- The split between runtime routing and deeper knowledge stores
- The observer -> reflector -> axiom promotion pattern
- The practice of loading only task-relevant context

## What Should Not Be Copied Blindly

- The author's concrete axioms
- The author's concrete skills
- The author's directory names and workflow assumptions
- Any part of the system that reflects their accumulated life context rather than yours

## Mapping To This Repo

### Reference Repo
- `rules/SOUL.md`
- `rules/USER.md`
- `rules/axioms/`
- `rules/skills/`
- `contexts/memory/OBSERVATIONS.md`

### This Repo
- `09_soul/core/SOUL.md`
- `09_soul/core/USER.md`
- `09_soul/axioms/`
- `09_soul/skills/`
- `09_soul/memory/OBSERVATIONS.md`

## Important Difference

The reference repo is designed around a broad context infrastructure from the start.
This repo already has an operational system and a live working history.

So the right move here is:
- keep local truth where it already lives
- add `09_soul/` as a portable source layer
- patch only the minimal runtime behavior into `AGENTS.md` and `.cursor/rules/`
