# Opus 5 1M Adapter Review Result

## Execution result

The user-directed switch to Opus 5 with a one-million-token context produced
a valid external review JSON on the same frozen payload. The CLI reported
`claude-opus-5[1m]` with a 1,000,000-token window; the assistant response model
was `claude-opus-5`. Effort remained `xhigh`, and the automatic-compaction
window was explicitly set to 1,000,000 tokens.

- Material duration: 602,476 ms, approximately 10 min 2 sec.
- Material turns: one.
- Automatic compaction events: zero.
- Native tool calls: zero; all active tool/MCP/Skill/command sets were empty.
- CLI list-price estimate: $2.847833, not an actual-deduction record.

Authentication, isolation probe, material invocation and result validation
ran outside the execution sandbox. The user's previously disclosed and
authorized 416,760-byte payload was neither reduced nor replaced.

## Review result

Subject: `designDoc/agent_runtime_08_agent_execution_adapter_contract.md`.
Candidate SHA-256:
`4584bda312f69c708da2d83144c360121bbb46135a8b1bf99027487b0a77f601`.
Payload SHA-256:
`84f107f7a24cf16007ada4a9b687ded25d2bcfcae3d3a8f4d1fbc8556aa72b93`.

All source hashes remained unchanged. JSON structure, exact subject, all
eleven canonical check IDs and their order, finding quotes and line locations
passed validation. Eight semantic checks were satisfied, two had findings,
and the prose check was correctly not run.

1. Medium, section 10.3, line 482: the negative statement referring to a
   Product Authorization `provider_sandbox_execute` decision retains an old
   non-database permission label under PA. The Reviewer asks for wording that
   names the actual external/resource owner through T2 09. This is a Design
   wording/ownership finding, not evidence that Runtime code added a PA power.
2. Low, section 13, line 569: frozen-subject drift is classified as
   `ADAPTER_POLICY_VIOLATION`, but the execution interface does not explicitly
   declare how that condition is delivered. The error/interface mapping must
   distinguish the typed failed result from Adapter conformance failure.

The exact parsed response is
`review_artifacts/runtime_adapter_opus_1m_external_review.json`.
Raw output, initialization, copied payload, candidate and source hashes remain
at `/private/tmp/runtime_adapter_opus_1m_review.bIvMOI/`.

## Boundary and next step

This is valid independent external content-review evidence, not registered
Runtime Reviewer execution, formal Design admission or implementation approval.
Unlike the Fable attempt, this invocation returned the requested review object
without automatic compaction. It still did not accept the candidate.

The candidate was not edited in response to this review. Addressing the two
findings requires a newly frozen candidate and another independent review.
Runtime code, Portable commit `a4d61d7`, other Design drafts, database state,
registrations and the pending archive remain unchanged.
