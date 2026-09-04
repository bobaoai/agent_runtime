# Runtime self-test Invocation Code Design owner decision

## Decision

The exact Invocation CodeDesignBasis is approved for implementation under the user's standing decision recorded in
`review_artifacts/runtime_self_test_code_design_owner_decision.md`.

The standing decision authorizes a later CodeDesign candidate in steps 5a, 6, 7 or 7a of the same unchanged
SystemChangePlan after that exact candidate receives independent review with all completion requirements satisfied and
no material finding. The Invocation candidate satisfies those conditions.

Approved Invocation basis:

- plan: `review_artifacts/runtime_self_test_and_reviewer_system_change_plan.md`;
- full plan SHA-256: `cf0305be721bc8e0e2bded71c1e87bf510d925b109972d505effa3a908a2b576`;
- plan step: `5a. Invocation Code Design`;
- plan-step row SHA-256: `fa540e8958c6d103bdfeceb957d7ce547b045b15a7c68f859f2cd4a5d6f67a35`;
- basis: `review_artifacts/runtime_self_test_invocation_code_design.md`;
- exact basis SHA-256: `629f2159a420470a8e96977ce70904b74743f21ecc405f75fa9d896911926cf6`;
- standing decision: `review_artifacts/runtime_self_test_code_design_owner_decision.md`;
- standing-decision SHA-256: `02258fdfcacc8aedb7b9e8c85367d4ee787236b335d3dd812b5c3a1bd20b2446`;
- final external content-review evidence:
  `review_artifacts/runtime_invocation_basis_opus_1m_final.json`;
- evidence SHA-256: `836692c833e17b1ca792950fabbce048f89f8667be1e8e61b42f8193d1ccba1b`;
- review result: all eighteen Code Design completion requirements satisfied, zero findings.

The external Reviewer supplied evidence and did not approve its subject. This user-owned record applies the earlier
standing decision to the exact reviewed bytes without editing the proposed basis and invalidating its hash.

## Approval boundary

Implementation may proceed only within the exact modules, interfaces, errors, Slices, tests, compatibility rules and
rollback boundary declared by the approved basis. The decision preserves all later deterministic, joint-verification,
package, registration, live-Reviewer and exact-commit gates in the plan. It grants no production publication,
active-pointer promotion, external Ledger configuration, persistent Registry deletion or cross-repository change.
