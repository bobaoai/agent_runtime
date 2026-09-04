# Capability Verification T2 Candidate Status

## Local result

Plan step 4 candidate:
`designDoc/agent_runtime_11_agent_capability_verification.md`.
Final candidate SHA-256:
`096ee22c40d3864e1029f6a3fc3cbd350ea354a7b0d25ce229411a76596bf01e`.

The candidate now distinguishes focused case evidence from complete Runtime
verification, preserves full-scope package and environment obligations, and
specifies temporary resources, explicit external evidence delivery, truthful
Reviewer/transport results and expected negative cases. Existing capability
owners and the canonical Example graph are retained. No runner or later
Example implementation was performed.

The DDM representation validator and temporary operator checks passed outside
the sandbox: identity, one run interface, three local errors and local links.
These are not independent semantic review or formal Design admission.
The Design-validator and existing capability case/runbook tests passed outside
the sandbox on the final candidate: 40 passed in 0.11s. This does not claim the
new runner behavior is implemented.

## Independent external review completed

The configured review is `claude-opus-5[1m]`, `xhigh`, with a one-million-token
automatic-compaction window. Authentication and content-free isolation smoke
succeeded; active tools, MCP, Skills and slash commands were empty.

First-round frozen payload: 396,074 UTF-8 bytes, 324,857 characters.
SHA-256: `5db2e6d7bf8eb370a66cd3fca0e86a929f3aa12ba49e065e7a741d1ec0a1f76b`.
It contains the complete T2 11 candidate and diff, required Runtime Design
context, the 05 package contract, selected governance clauses and bounded
Runtime code context. It contains no credential or business-data export.

The initial permission attempts were rejected before process creation. After
the user explicitly authorized this payload and Anthropic destination with
“授权”, the first material review executed outside the sandbox. It returned one
medium finding: the public run interface did not explicitly carry the
owner-qualified host environment-availability inspection ref/hash already
required by the inputs, flow and environment failure contract.

The author checked the finding against the exact subject and step 4, accepted
its ownership, then directly repaired the input, output and flow references.
This follows the user's standing instruction to fix valid findings and
re-review without another per-finding confirmation. No resource policy,
capability owner or completion requirement changed.

The second material review used the same provider and isolation settings. Its
payload contained 372,307 UTF-8 bytes and 306,566 characters, SHA-256
`fd1adf29f50175c6de333b6f38aee386095d47cb467ae1b665f6bab73a7f30fb`.
A pre-send comparison confirmed the same context-source set, 21 unchanged
context files, and only the T2 11 subject changed. No credential or business
data was included.

The final review satisfied all eleven DDM checks and returned zero findings.
Local verification checked the output structure, canonical checklist order,
subject identity and unchanged source hashes. Optional prose observations were
not applied, so the exact reviewed bytes remain frozen.

Final parsed output:
`review_artifacts/runtime_capability_opus_1m_r2_external_review.json`.
First-round evidence: `/private/tmp/runtime_capability_t2_review.XXv1Q4/`.
Final evidence: `/private/tmp/runtime_capability_t2_r2.kj89Vk/`, including
frozen sources, payload, source hashes, candidate checks and isolation records.

This is independent external content review, not execution through a registered
Runtime Reviewer. Formal plan preparation, registered Design review, owner
admission and Software Delivery Code Design approval are separate facts; this
report creates none of them.

## Unchanged boundaries

The preceding adapter's two findings were automatically fixed and its re-review
satisfied all eleven DDM checks with no findings. Its exact accepted local
input is unchanged. Portable commit `a4d61d7`, Runtime core, tests, generated
bundles, database records, registered releases, active pointers and the pending
archive are unchanged. T2 11 now has its own independent external review with
zero findings and remains a locally reviewed Design candidate. Implementation
has not started.

## Next planned boundary

Step 5 assigns the existing Execution kernel and inline evaluation helper to
`engineering-code-design`. Step 5a separately assigns context preparation and
provider Adapters to the Invocation owner. The already-observed production
authority coupling crosses that exact seam; it cannot be repaired by changing
only the helper's default argument. No proposed CodeDesignBasis or approval is
manufactured by this status update. The next authoring invocation must resolve
the exact Design and owner-decision prerequisites, bind the current code and
tests, and return a proposed basis before any implementation edit.
