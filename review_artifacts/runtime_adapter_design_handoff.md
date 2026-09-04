# Runtime Adapter Design Handoff

## Entry and scope

The user's “可以”, following the explanation of the completed external-authority
Design step and the next adapter step, accepts the exact preceding Design
meaning for local continuation. The later Portable-governance commit request
temporarily paused this step; the user's subsequent “继续” resumes it.

The user subsequently instructed that, after each review, the Primary Agent
should directly address in-scope findings and re-review without waiting for
another confirmation. The Reviewer remains read-only; the author independently
checks evidence and ownership before applying a correction. Scope expansion,
new material design choices and formal admission remain separate decisions.

Plan: `review_artifacts/runtime_self_test_and_reviewer_system_change_plan.md`.
SHA-256: `cf0305be721bc8e0e2bded71c1e87bf510d925b109972d505effa3a908a2b576`.
Step: 3, Adapter self-test contract.
Accountable owner: Invocation owner.
Method: `the-design-authoring`.
Subject: `designDoc/agent_runtime_08_agent_execution_adapter_contract.md`.
Layer and owned object: T2, provider-neutral Attempt invocation.

Accepted prerequisites:

- Runtime T1: `designDoc/agent_runtime_00_execution_charter.md`, SHA-256
  `243a821d7403bdaf9ff51b2e9aa5ea8f336c6d981cde50f0e1389349c638e5e9`.
- External Authority Integration:
  `designDoc/agent_runtime_09_authorization_integration_contract.md`, SHA-256
  `f0cab2805ae900d6b7656d1a626284f9da5ed54830056266ebac09c1961a91c0`.
- User-directed local archival scope from the exact plan; this step removes
  the adapter's current dependency, not the file itself.

The predecessor includes the existing same-lane adapter draft, SHA-256
`f00158e897a1be08a9654fdd3e22dc6d9f5acd5da63317dc826caf077a1427e3`.
Its bytes and pre-existing Git diff are retained under
`/private/tmp/runtime_adapter_t2_review.ZpztSR/`.

## Candidate result

Ordinary self-tests consume a verified test-resource boundary instead of
requiring production context, Product decision or synthetic approval. External
protected paths still require their real pre-call evidence. Post-effect
observations return in results and are not circular prerequisites for the same
call. Profile compatibility, begin receipts, complete input delivery, private
workspace isolation, bounded tools/network, canonical schema validation, usage
normalization, failure handling and fenced finalization remain required.

The material revision adopts the current T2 structure, aligns the parent and
layer with the existing T1 delegation, and leaves mutable capability inventories
and physical SDK/source mappings to code-owned inspection. Provider transport
is distinguished from model-visible network capability; it grants no extra
egress or tool access. Frozen repository review retains explicit resource and
command bounds, including pre/post subject-content verification.

Preservation map from the predecessor:

| Predecessor topic | Candidate sections |
| --- | --- |
| Adapter/Skill ownership and exact admission | 4, 6, 10.1, 12 |
| Request/result and usage contracts | 5 |
| Modes, input delivery, budget and workspace | 6, 7 |
| Dynamic host callbacks and resource receipts | 8 |
| Context, sibling variants and schema comparison | 9 |
| SDK, CLI and repository-review enforcement | 10 |
| Audit, finalization, failure and repair | 11, 13 |
| Conformance, package isolation and environment tests | 14 |

Only this adapter Design source and local review artifacts are changed by this
step. Runtime core, tests, accepted T1/T2 09 bytes, T2 11, generated packages,
database state, registrations and active pointers remain unchanged. Portable
governance was separately committed as `a4d61d7` and is not modified here.

## Qualification boundary

The user's authorization allows independent external content review as the
local Design starting basis while formal bindings are unavailable. No formal
SystemChangePlan preparation, registered Design Reviewer execution, Current
admission or implementation authorization is fabricated by this continuation.
The adapter remains a candidate pending its own checks, review and owner
decision; no new execution capability is claimed as implemented.
