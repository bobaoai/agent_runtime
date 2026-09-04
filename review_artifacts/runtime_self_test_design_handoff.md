# Runtime Self-Test Design Work

## User-authorized entry

The user explicitly authorized using the completed independent external plan
review as the starting basis for this Runtime-local Design work, and authorized
the same treatment for similar requests. This records a conversation decision;
it is not a new Portable governance rule or a registered Runtime review result.

The exact plan is
`review_artifacts/runtime_self_test_and_reviewer_system_change_plan.md`, SHA-256
`cf0305be721bc8e0e2bded71c1e87bf510d925b109972d505effa3a908a2b576`.
Its independent external content review has no material findings, as recorded in
`review_artifacts/runtime_self_test_plan_external_review.md`.

Formal plan preparation and registered System Change Reviewer execution remain
unestablished. The authorization permits Design work to begin; it does not
manufacture those results or waive downstream review, owner decisions,
implementation-basis approval, or release gates.

## Current bounded candidate

- Plan step: 1, Self-test domain boundary.
- Method: `the-design-authoring`.
- Accountable owner: Runtime domain owner.
- Subject: `designDoc/agent_runtime_00_execution_charter.md`.
- Kind and layer: material Design Intent revision, T1.
- Owned object: Agent Runtime domain.
- Parent: `designDoc/the_agent_runtime.md`.
- Required result: distinguish Runtime-hosted self-tests from external
  production request acceptance while retaining the six peer responsibilities.
- Pre-edit subject SHA-256:
  `0db60c384c90d73bc3ca4d7f88164bd49fc50ba6a410f00fe92e97ddeb1e331a`.
- Same-domain T1 peers: none; this is the existing sole Runtime domain root.
- Child partition: preserved; self-test delegation is clarified for External
  Authority Integration, Invocation and Agent Capability Verification.

The candidate changes self-test prerequisites, resource isolation and retention
boundaries, and corresponding flow, lifecycle, verification and T2 delegation.
It preserves release selection, execution identity, provider neutrality,
production protections, existing persistent Registry records, and the other
Runtime responsibilities. It does not create a second kernel or select a new
provider, database product, schema, or credential resolver.

Only this T1 source is edited in this step. The existing T2 08 and T2 11 drafts
remain unapproved. T2 09, Runtime code, Portable sources and projections, external
host repositories, generated Design bundles, Runtime releases and active
pointers remain unchanged. The legacy host-binding document is not moved here.

## Review and handoff status

The candidate must complete deterministic representation checks and independent
external Design content review before it is presented for the next owner
decision. That external result remains distinct from registered
`design_contract_reviewer` execution and formal Design admission.

Frozen inputs, predecessor bytes, candidate bytes and review execution evidence
for this step are retained under
`/private/tmp/runtime_self_test_t1_review.13dA3p/`.
No implementation or lifecycle admission is asserted by this handoff note.
