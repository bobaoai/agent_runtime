# Runtime self-test Execution Code Design amendment owner decision

## Decision

The exact amended Execution CodeDesignBasis is approved for implementation under the user's instruction to fix valid
in-scope review findings directly and the standing same-plan review authorization. This decision supersedes the earlier
Execution basis decision only for the exact CodeDesignBasis bytes named below; it does not alter the SystemChangePlan.

- plan SHA-256: `cf0305be721bc8e0e2bded71c1e87bf510d925b109972d505effa3a908a2b576`;
- plan step: `5. Execution Code Design`;
- amended basis: `review_artifacts/runtime_self_test_execution_code_design.md`;
- amended basis SHA-256: `f562700d97c6432dc97840a99a6c238633e15534a88ada5a18e3b027627d1ed7`;
- review evidence: `review_artifacts/runtime_execution_basis_opus_1m_amendment_final.json`;
- evidence SHA-256: `a2d8cc7d72738cb72ab9f835b6a9c2383072cbc5b08159974f4dbacf8bce87f0`;
- result: all eighteen Code Design completion requirements satisfied, zero findings.

The amendment adds one optional trusted `test_resource_factory` to the existing inline helper and makes it mutually
exclusive with the external `authority_factory`. It also gives each joint/live test one owner. The external Reviewer
supplied evidence and did not approve the subject; this user-owned record carries the implementation decision.

Implementation remains limited to the exact amended basis and every later deterministic, Invocation, Verification,
registration, live-provider and exact-commit gate. It grants no production publication, external Ledger configuration,
persistent Registry deletion, active-pointer promotion or cross-repository change.
