# Skill Review Module

## CUSTOMIZE_MODULE

Review target type: `skill_review`

Review a SKILL.md file for completeness, clarity, and contract quality.

### Required review questions

1. Does the skill clearly state what it does and what result it owns?
2. Can a new agent, reading only this skill, determine whether the task is complete?
3. Are the boundaries explicit — what the skill does NOT do, and which adjacent skills handle those cases?
4. Are invariants (canonical path, identity fields, upstream freshness, output format) pinned, while implementation paths are left open?
5. Does every key boundary have both a prohibitive rule AND a detection signal (what silent violation looks like)?
6. Is the completion standard testable by the agent itself?
7. Is the skill written as enabling guidance (goal + constraints + verification), not as an SOP (step 1, step 2, step 3)?
8. Are there unnecessary process steps, background knowledge dumps, or padding that don't change the agent's success rate?
9. Does the projection establish the task, object, or operational problem before asking a cold reader to retain new abstract names?
10. Can a cold reader recover what each explanatory section describes, why it exists, and what execution or decision changes?
11. Does any wording improvement alter identity, authority, compatibility, causal direction, uncertainty, failure, or stop semantics from the governing source?

### Severity mapping

- BLOCKING: missing or contradictory contract (e.g., completion standard impossible to verify, boundary that conflicts with stated goal, output schema that doesn't match what downstream consumes)
- MEDIUM: important contract gap (e.g., adjacent skill disambiguation missing, detection-side boundary missing for a key rule, completion standard vague enough to pass trivially)
- LOW: improvement opportunity (e.g., section could be tighter, example would help, ordering could be clearer)
- NIT: cosmetic (naming, formatting, minor wording)

### Finding format

Each finding must quote the exact target text, cite which review requirement it violates, and give a concrete patch direction.

Use defect polarity throughout: a finding means a defect is present; no finding means the requirement passes. For questions phrased as a positive reader capability, report only the missing capability and the exact passage that causes the failure. Do not emit a positive capability as a finding.

### Out-of-scope

- Runtime correctness of referenced code paths (e.g., whether `src/macro_data/public_sources.py` actually exposes the listed APIs)
- Whether the skill's domain logic is correct (e.g., whether the three verification checks are the right checks for claim review)
- Prose style of the skill itself beyond contract clarity

## DATA_DEPENDENT_MODULE

### Allowed evidence

- The target SKILL.md file (embedded)
- `bestpractice_skill_writing.md` (embedded as reference): the meta-skill that defines what a good skill looks like
- `bestpractice_doc_self_review.md` (embedded as reference): the self-review procedure, for context on how skills get reviewed
- `bestpractice_prompt_boundary.md` (embedded as reference): task-plane vs control-plane separation
- `bestpractice_agent_module_general_module.md` (embedded as general module): the worker charter

### Local references that define the contract

The primary review contract is `bestpractice_skill_writing.md`. Its six principles and verification checklist define what "good skill" means:

1. Result certainty over process certainty (P1)
2. Enabling guidance, not SOP (P2)
3. AI-facing doc: contract first, compression second (P3)
4. Pin invariants, not implementation paths (P4)
5. Boundaries have two sides: prohibitive + detection (P5)
6. Organize concepts in cold-reader dependency order and preserve governing semantics (P6)

### Source-check labeling

- Quote from target → label as `[TARGET]`
- Requirement from bestpractice_skill_writing → label as `[SKILL-BP]`
- Requirement from bestpractice_prompt_boundary → label as `[PROMPT-BP]`
- Requirement from general module → label as `[GENERAL]`

### Target-specific caveats

- This is a NEW skill (not a rewrite). It has no dogfood history yet, so the "known pitfalls" section may legitimately be empty.
- The skill references local macro data infrastructure (`src/macro_data/public_sources.py`, PostgreSQL store). The reviewer should check whether these references are specific enough for the agent to use, but should NOT verify whether the code actually works.
- The skill is part of the digestion layer and sits between expert claim production and thesis consumption. Adjacent skill relationships matter.
