# Runtime self-test Code Design owner decision

## Decision

The user explicitly approved implementation of the exact reviewed Execution CodeDesignBasis and authorized a standing
decision for later CodeDesign candidates in the same unchanged SystemChangePlan: after each exact candidate receives
independent review with no unresolved material finding, the Primary Agent may proceed directly to its assigned
implementation step without requesting another per-basis owner decision.

User decision text:

> 批准按此方案实施，并允许同一已审计划内后续 CodeDesign 在独立审核通过后直接实施

Approved Execution basis:

- plan: `review_artifacts/runtime_self_test_and_reviewer_system_change_plan.md`;
- full plan SHA-256: `cf0305be721bc8e0e2bded71c1e87bf510d925b109972d505effa3a908a2b576`;
- plan step: `5. Execution Code Design`;
- basis: `review_artifacts/runtime_self_test_execution_code_design.md`;
- exact basis SHA-256: `4e03b451b7c36776144ac92e57c238f7fe103f16834a7fb47b440482ab03855b`;
- final external content-review evidence:
  `review_artifacts/runtime_execution_basis_opus_1m_r3_external_review.json`;
- review result: all eighteen Code Design completion requirements satisfied, zero findings.

This user-owned decision is independent of the author and external Reviewer. The Reviewer supplied evidence and did
not approve its subject. The proposal bytes remain frozen; this control record carries the owner decision instead of
editing the reviewed candidate and invalidating its hash.

## Standing decision boundary

The standing decision applies only when all of these conditions hold:

1. The later candidate belongs to steps 5a, 6, 7 or 7a of the same exact plan hash above.
2. Its prerequisites, owner, intended result, affected surfaces and exclusions remain those in that plan.
3. All bound Design and predecessor-basis hashes remain exact.
4. The candidate is complete under `engineering-code-design`, has no unresolved decision, and receives independent
   external review with every required completion requirement satisfied and no material finding.
5. Any accepted finding is fixed and the new exact bytes are independently re-reviewed before implementation.
6. Implementation stays inside the exact reviewed candidate. New scope, owner, order, external effect, production
   access, irreversible data action or unresolved architecture decision returns to the user or System Change owner.
7. The decision does not authorize production publication, active-pointer promotion, external Ledger configuration,
   deletion of persistent Registry records, or changes in another repository.

The standing decision does not convert external content review into registered Runtime review, formal plan preparation,
Design admission, release admission or deployment authority. Those qualifications remain explicit. It authorizes local
implementation under the reviewed bases for this plan and preserves every later deterministic and exact-commit gate.
