# Notes On `context-infrastructure` Article

Source article: [为什么AI只会说正确的废话，以及怎么把它逼出舒适区](https://yage.ai/context-infrastructure.html)

## Core Thesis

Better model intelligence alone does not produce deeper judgment. Once models are good enough, the limiting factor becomes context density: the personal frameworks, habits, biases, and lessons that shape interpretation.

## Key Ideas

### 1. Consensus Is The Default
- LLMs are trained toward likely and broadly acceptable outputs.
- This makes them good at safe summaries and weak at distinctive judgment.

### 2. Context Is The Leverage Point
- Personal context can push the model away from generic consensus and toward more insightful output.
- Context becomes a strategic asset because it is unique and accumulates over time.

### 3. Capture -> Distill -> Load
- Capture raw behavior and decisions.
- Distill repeated patterns into more stable memory.
- Load only the relevant subset for the current task.

### 4. Stable Patterns Matter More Than Raw Facts
- Facts such as preferences are useful, but deeper leverage comes from durable judgment patterns.
- The article emphasizes promoting repeated, stable lessons instead of keeping only flat user facts.

## Three-Layer Memory Model

- L1 Observer: records meaningful observations from day-to-day activity
- L2 Reflector: merges, deduplicates, and finds patterns
- L3 Axiom: promotes only the most stable patterns into durable principles

## Reusable Lessons For This Repo

- Treat context as a system, not as a single prompt.
- Keep the always-on runtime layer small.
- Store richer identity and heuristics in portable markdown.
- Promote only lessons that feel stable enough to carry into the next project.

## Implication For `bokan_assistant`

The most useful adaptation is not to copy the entire reference repo, but to create a portable operator layer that:
- learns from project work
- distills repeated lessons
- projects only the minimal active behavior into Cursor runtime files

That is the role of `09_soul/`.
