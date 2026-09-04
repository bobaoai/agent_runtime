# Runtime Adapter T2 Candidate Status

## Local preparation

Plan step 3 is prepared under `the-design-authoring`:
`designDoc/agent_runtime_08_agent_execution_adapter_contract.md`.
Initial candidate SHA-256:
`4584bda312f69c708da2d83144c360121bbb46135a8b1bf99027487b0a77f601`.

The existing DDM representation validator passed its five checks. Additional
temporary operator checks passed for matching front matter/capsule identity,
nine flow/interface mappings, seven error-code definitions, local reference
resolution and absence of the obsolete host-binding filename. These are
representation checks, not formal Design Registry admission or semantic review.
They ran outside the execution sandbox.
The Design artifact validator focused suite also passed outside the sandbox:
21 tests in 0.04s.

Author self-review clarified that pre-call receipts authorize entry, while
actual effect and terminal grant observations follow the effect. They cannot
be circular prerequisites for that same operation. The predecessor's valid
input, isolation, usage, schema, Variant, failure and finalization requirements
were retained as described in `runtime_adapter_design_handoff.md`.

## External review execution

Claude authentication and the content-free isolation smoke succeeded outside
the sandbox. Initialization reported empty active tools, MCP servers, Skills
and slash commands. No material review verdict exists.

The execution approval reviewer rejected the material review command before
process creation. Its reason was insufficient explicit authorization naming
this particular internal-material payload and Anthropic Claude destination,
despite the user's authorization for similar external reviews.

The frozen payload contains 347,882 characters: the full adapter candidate,
full predecessor diff, complete core parent/Design authorities and selected
relevant clauses from auxiliary T0 contracts, plus declared Runtime code
excerpts. Full context source bytes and hashes remain in the evidence directory.
The material payload was not sent by the rejected command. No alternative or
indirect attempt was made after rejection.

The user then inspected the payload composition and was told its actual UTF-8
size, 416,760 bytes. After that disclosure, the user explicitly requested
“还行吧 你发给 fable 5.1 xhigh 试试”. This authorizes sending the same batch to
Anthropic using the requested Fable model; no alternate disclosure was inferred.

A content-free probe returned from `claude-fable-5-1` with `xhigh` requested;
its assistant response confirmed the exact model. All active tool, MCP, Skill
and slash-command sets were empty. The payload hash and all source hashes were
checked unchanged. The material review command was then approved and started.

It completed in 826,239 ms, but the returned text was not JSON and contained
tool-call-shaped text rather than a Design review. The log records one
automatic compaction from 195,250 to 6,951 tokens, zero native tool calls and
no tool-result events. No effective review verdict was obtained, and none of
the command-like output was executed. Details are retained in
`runtime_adapter_fable_review_attempt.md`.

Original frozen payload and preparation evidence:
`/private/tmp/runtime_adapter_t2_review.ZpztSR/`.
Fable invocation and copied exact payload:
`/private/tmp/runtime_adapter_fable_review.x345Ro/`.
Payload SHA-256:
`84f107f7a24cf16007ada4a9b687ded25d2bcfcae3d3a8f4d1fbc8556aa72b93`.

## Subsequent Opus 5 1M result

At the user's explicit request, the same payload was sent to
`claude-opus-5[1m]` with `xhigh` effort and a 1,000,000-token automatic-compaction
window. This call completed in 602,476 ms with no compaction and no tool use.
It returned valid JSON covering all eleven canonical checks: eight semantic
checks satisfied, two findings, and prose not run. The findings concern the
old PA-associated execution-decision wording and the subject-drift error
delivery mapping. The candidate remains unchanged and has not passed review.

Details: `runtime_adapter_opus_1m_review_result.md`.
Exact response: `runtime_adapter_opus_1m_external_review.json`.

## Direct correction and re-review

The user instructed the Primary Agent to fix in-scope review findings directly
and re-review without another confirmation. The author removed the old
PA-associated execution-decision wording and made the policy-violation delivery
explicit: inner callbacks reject their own operation; execute_agent_attempt
returns the typed failed result and preserves actual usage/effects. Conformance
failure remains distinct from successful policy enforcement.

Revised candidate SHA-256:
`66a7d20f6791176c1ba1aa7b5ea60b0765ebfc9efe7e1773329a71b4f72d2003`.
The same nine-interface/seven-error representation checks passed outside the
sandbox. The complete candidate and prior findings were frozen for re-review.

An initial redisclosure rejection was followed only by read-only scope checks.
They proved no new source paths, eighteen unchanged context files, and changes
only to the two-finding candidate revision and the handoff's record of the
user's new instruction. The provider configuration was identical. After this
evidence was supplied, the same direct review command was approved and started.
No alternative channel was used. Evidence and current review:
`/private/tmp/runtime_adapter_opus_1m_r2.RDmKf6/`.

The re-review completed and validated against unchanged source hashes. All
eleven canonical DDM checks are satisfied and findings are empty. Both prior
findings were re-evaluated and closed; prose review ran after all ten semantic
checks. The exact result is
`runtime_adapter_opus_1m_r2_external_review.json`. Optional typography notes
were retained without changing the reviewed candidate bytes.

The user's standing authorization supports continuing the local Design plan
from this externally reviewed result. No registered Design review, formal
Current admission or implementation approval is invented by that continuation.

## Unchanged scope

Portable governance commit `a4d61d7`, Runtime core, tests, accepted T1 and T2 09
bytes, T2 11, generated Design package, database state and releases are
unchanged. The host-binding document has not been moved to archive. This
adapter candidate remains uncommitted. Its external content review is complete;
registered review and formal Design admission remain unestablished.
