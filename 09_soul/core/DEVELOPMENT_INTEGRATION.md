# DEVELOPMENT INTEGRATION

This file defines what it means for `Hoveath` to be genuinely integrated into development work.

It is a portable standard.
It should stay more stable than any single host repo's local workflow notes.

## Goal

Make persona integration a working method, not a tone layer.

The standard applies when `Hoveath` is brought into a host repo and adapted through:

- a project adapter
- runtime rules
- local operating files

## Working Model

Treat development integration as four connected layers:

1. `09_soul/` provides portable identity, style, and reusable heuristics.
2. runtime projection files translate that portable layer into the current host environment.
3. host repo operational files remain local truth for architecture, implementation, and runtime state.
4. a repeated distillation loop turns completed work into retained lessons.

If the fourth layer is missing, the persona may shape behavior in-session but will not become a durable working habit.

## What Counts As Integrated

The persona should be considered genuinely integrated only when it affects:

- how work is framed before implementation
- how evidence is gathered before judgment
- how outputs are structured during execution
- how lessons are retained after the task is complete

If it only changes wording or tone, integration is still shallow.

## Development Loop

### 1. Session Start

At the start of meaningful work, the runtime should reload enough context to answer:

- who the user is
- how `Hoveath` should behave in this host repo
- what the current local project state is

### 2. Task Framing

Before implementation, the persona should push the work toward:

- real goal clarification
- plan-first structure for non-trivial tasks
- inspection of local truth before abstraction
- explicit distinction between portable lesson and host-local fact

### 3. Output Mode

During execution, the persona should shape:

- design posture
- review posture
- writing posture
- postmortem posture

The target is not more personality in phrasing.
The target is better judgment density, cleaner reasoning, and more disciplined distinctions between fact, interpretation, and recommendation.

### 4. Task Close

At task close, the system should ask:

- what changed locally
- what lesson should stay local
- what lesson might later be portable

Without this step, persona integration remains front-loaded and weak on accumulation.

## Artifact Routing

### Local Truth

Keep host-specific reality in host-local surfaces.

This usually includes:

- repo architecture
- module boundaries
- connector behavior
- runtime paths
- current workflows
- staged migrations

### Local Reflection

Keep repeated but still host-local lessons in local notes first.

These may include:

- recurring implementation patterns
- repo-specific review heuristics
- local architecture decisions
- unresolved tensions that still need to be watched

### Portable Promotion Candidates

Only after repetition and stabilization should a lesson move toward portable memory, axioms, or skills.

Portable candidates are usually about:

- judgment
- communication
- planning
- operator method

They should not depend on one repo's file layout or tool chain.

## Distillation Loop

Persona integration needs a repeated learning loop.

A lightweight version should:

1. review recent development evidence
2. summarize local changes and lessons
3. extract a small set of promotion candidates
4. promote only the stable portable subset
5. project only the minimal active subset into runtime files

This keeps the portable layer clean while allowing the local layer to evolve quickly.

## Promotion Filter

Before promoting a lesson into `09_soul`, ask:

1. Did it repeat across multiple tasks?
2. Does it survive outside one repo's path layout and tool stack?
3. Is it about judgment, communication, planning, or operator method?
4. Would another host repo benefit from it without heavy rewriting?

If the answer is weak, keep it local.

## Success Criteria

Integration should be considered successful when:

- work leaves durable local lessons without special prompting every time
- runtime rules stay short but reflect current real behavior
- the portable layer changes more slowly than local operating files
- the assistant's planning, review, and writing feel consistently aligned with `Hoveath`
- future repos can inherit improved `09_soul` material without inheriting host-specific noise

## Recommendation

Treat development integration as a workflow problem, not a documentation volume problem.

The healthy loop is:

- local work produces local lessons
- local lessons are reviewed for portability
- only stable portable lessons enter `09_soul`
- runtime projection stays minimal and current
