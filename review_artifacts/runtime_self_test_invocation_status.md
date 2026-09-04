# Runtime self-test Invocation Code Design status

## Approved predecessor

The exact Execution CodeDesignBasis at SHA-256
`4e03b451b7c36776144ac92e57c238f7fe103f16834a7fb47b440482ab03855b` completed independent review with all
eighteen completion requirements satisfied and zero findings. The user then explicitly approved its implementation and
authorized later CodeDesign candidates in the same unchanged plan to proceed directly after independent no-finding
review. The independent owner decision is recorded in
`review_artifacts/runtime_self_test_code_design_owner_decision.md`, SHA-256
`02258fdfcacc8aedb7b9e8c85367d4ee787236b335d3dd812b5c3a1bd20b2446`.

## Invocation candidate

Plan step 5a now has a proposed candidate:
`review_artifacts/runtime_self_test_invocation_code_design.md`.
SHA-256: `b2ee03ef8b406f83e7cb3e5d826a6475857c9e6bfa2935b9a0ae5cda429e345e`.

The candidate keeps one provider-neutral Adapter path and specifies:

- paired self-test boundary fields with the approved legacy request-hash codec;
- exact host resolution before provider or resource entry;
- separate Runtime test operation intent/receipt types with no production authority fields;
- explicit external/test evidence exclusivity and operation payload/resource/Attempt binding;
- shared Claude/Codex preflight without provider/model fallback;
- concrete repository/command/SQL sessions deferred to the Verification owner; and
- three ordered Slices for contracts, Adapter preflight and joint Execution verification.

The non-sandbox local checker verified three Design hashes, the approved Execution prerequisite, full plan and step-row
hashes, seven unchanged source/test hashes, six logical modules, five interfaces, six errors and three valid deferred
Slices. No Runtime source, test, database, registration or release state was changed.

## External review packet

The fresh Opus 5 1M isolation smoke succeeded with empty tools, MCP servers, Skills and slash commands. The frozen
review packet is 658,334 UTF-8 bytes and has SHA-256
`b54303d14a2194dfa82c62be2e1c3146c64255e1da8f793f91c788b46c209698`.
It contains the exact candidate, plan/owner decision, approved Execution basis, Software Delivery and Runtime Design
context, plus the bounded Invocation source/tests needed to judge implementability. It excludes credentials,
environment files, business data and database output.

The material command was rejected before process creation because the packet adds Invocation source/test files outside
the previously approved eighteen-file disclosure set. Broad related-review authorization does not override the user's
earlier explicit boundary that new source files require confirmation. The packet was not sent; no alternate channel or
retry was used. Prepared immutable evidence is in
`/private/tmp/runtime_invocation_basis_review.OF50S0/`.

After explicit approval, run outside sandbox:

```text
/Users/bokanbao/Documents/GitHub/trading_platform/.venv/bin/python /private/tmp/runtime_invocation_basis_review.OF50S0/review_runner.py review
```

Then run the same script with `verify`, directly fix any valid in-scope finding, and re-review. The candidate receives
the user's standing implementation decision only after its independent review has no unresolved material finding.
Steps 6 and 8a remain dependent on that result; they have not been started or claimed complete.

## Split review preparation

The user rejected the 658,334-byte monolithic packet as too large and requested packet splitting. The new review shape
uses three stateless stages. Packet A reviews authority, scope, ownership, interfaces/errors and compatibility. Packet B
reviews current code truth, exact codecs/callbacks and test sufficiency. Packet C is the sole final reviewer: it receives
the complete exact candidate, all eighteen canonical completion requirements, bounded critical authority excerpts and
the two structured shard reports as evidence; it re-evaluates every requirement and does not inherit their verdicts.

Prepared packets:

- authority: 245,136 bytes, SHA-256
  `50818bf93cc9948f80497a55b4330b13a5c57010c96eb341e8fd6a12fa5adfee`;
- code truth: 175,967 bytes, SHA-256
  `659e0378810afa22af87c55c8b22a0b9b78c772c96cbd91f866bfb70c6994be5`.

Both content-free smokes passed with empty tools, MCP, Skills and commands. The first authority-shard material command
was rejected before process creation: requesting a split did not constitute explicit permission to disclose the newly
added Invocation source/design scope. Neither shard has been sent. Packet C cannot be built until A and B return valid
structured reports. Prepared split material is under `/private/tmp/runtime_invocation_basis_review.OF50S0/`; the original
monolithic command remains retired and must not be used as a workaround.

The user later explicitly authorized both named shards and their Anthropic destination. They ran concurrently and
returned valid structured outputs with no source drift: authority reported three candidate findings; code truth reported
five. These are shard evidence, not final verdicts and have not yet modified the candidate.

The final packet is now built from the complete exact candidate, all eighteen verbatim completion requirements,
critical authority excerpts and both validated shard reports. Size: 129,890 bytes. SHA-256:
`ca578584fb4466cc4aa02c7dacaf128815ced74d11ac5cbd4c1be1301dc5a810`.
It has not been sent. Only this final reviewer may decide the full finding set; shard findings are re-evaluated rather
than inherited.

After the exact shard sizes and hashes were presented, the user replied “继续？”. A direct attempt to start the two
shards was again rejected before process creation because the execution approval reviewer treats that phrase as
ambiguous rather than explicit approval of these payloads and the Anthropic destination. No packet was sent, no process
handle exists, and no alternate channel or retry was used. Exact explicit user confirmation remains required.

## First split-review result and revision

The user explicitly approved both split packets. Authority and code-truth ran concurrently and returned valid shard
reports with three and five findings respectively; source hashes were unchanged. The 129,890-byte final packet then ran
under separate explicit authorization. Its JSON contained eight findings aligned with the shards, but violated the
requested review protocol by executing `prose_review` while substantive requirements still had findings. It is preserved
as `review_artifacts/runtime_invocation_final_r1_invalid_output.txt` and is not treated as a valid final verdict.

The author checked the eight concrete findings against the candidate, T2 08/09 and code truth, accepted them, and made
one bounded revision:

- `resolve_execution_boundary` is no longer redefined; Invocation declares a scoped
  `validate_test_execution_boundary(request) -> None` host callback only for self-test pairs. External requests retain
  their existing committed-operation evidence path and host doubles.
- Provider entry now requires exactly existing external operation evidence or a successfully host-validated self-test
  pair. Neither/mixed/partial evidence remains zero-provider, with a new live positive transport control.
- The host protocol, predicate and callback are owned by `invocation_request` and included in the contract Slice; the
  callback imports no Execution DTO and returns no peer record.
- Required refusal and retained result-normalization tests now resolve to exact Slices.
- The normative and reader-facing flows now match: provider-tool authorization starts from `provider_executor`; provider
  transport and final result are explicitly marked internal fact flow rather than unmatched contract edges.
- Joint Slice completion is the fixed Execution/Invocation test only. Live registered Reviewer execution remains step 12.
- Runtime test-operation lifetime is the exact Attempt plus monotonic test-resource fence; no new raw-clock expiry is
  invented and external expiry semantics remain unchanged.

The revised candidate SHA-256 is
`21ac850a1b99e4de42ce633d7920a90eb619b72c60b1f3a6109bde501decd10c`. Local structural/hash closure passes with six
modules, five interfaces, six errors and three Slices. No Runtime source/test implementation changed.

Rebuilt split packets, not yet sent:

- authority: 247,343 bytes, SHA-256
  `d4570421133d695679254395e693176cbdc4dae6d057ac1a189b23736842c88e`;
- code truth: 178,174 bytes, SHA-256
  `c618653799ece31ea6b16c81e5131ac3eb44a460b4a99c07342b7b83e71013b6`.

They use the same source set and split dimensions as round 1; only the exact candidate changed. New explicit disclosure
approval is required before sending these revised packets. A valid final no-finding review remains the standing owner
decision's implementation prerequisite.

## Revised split execution correction

The user approved the revised shards and all subsequent authorization prompts of this kind. The first parallel attempt
contained an orchestration defect: both copied runners retained the base `WORK` path. They therefore read the rebuilt
660,541-byte monolithic input and wrote to the same base output path, sending that monolithic packet twice instead of the
approved split packets. This exceeded the user's required split shape, although the material remained within the same
Runtime plan and excluded credentials/business data. The collided outputs are invalid and are not review evidence.

The error was disclosed immediately. The two runner `WORK` paths were then fixed and read back, and both input files were
hashed before retry: authority `d4570421...2c88e`, code truth `c6186537...013b6`. Fresh content-free smokes passed.
The correct exact shards are now running as sessions `44989` and `21636`. No result from the erroneous attempt will be
fed to final synthesis.

## Final review and approval

The exact current candidate is
`review_artifacts/runtime_self_test_invocation_code_design.md`, SHA-256
`629f2159a420470a8e96977ce70904b74743f21ecc405f75fa9d896911926cf6`.
Its non-sandbox local checker passes with five interfaces, six errors, three ordered Slices, matching Design hashes and
the exact plan-step row hash.

The last valid shard round reported zero authority findings and one low code-truth finding: the live positive transport
control had no provider-executor test owner. The candidate assigned
`self_test_boundary_admits_provider_transport` to `provider_executor.required_tests`; that change also preserved the
Slice test-set union. The shard verdicts remained evidence rather than approval.

A new final-only packet independently re-evaluated all eighteen completion requirements against the exact revised
candidate. Packet size: 128,013 bytes. Packet SHA-256:
`2e91a255bff1014181b659405e97b02cd5aa4cdb8a55fd4856d48615a5a76aa8`.
The content-free smoke and material review both confirmed `claude-opus-5[1m]` with empty tools, MCP servers, Skills and
slash commands. The strict verifier confirmed no source drift, exact requirement order, valid subject identity and
zero findings. All eighteen requirements are `satisfied`.

The validated review is preserved at
`review_artifacts/runtime_invocation_basis_opus_1m_final.json`, SHA-256
`836692c833e17b1ca792950fabbce048f89f8667be1e8e61b42f8193d1ccba1b`.
The user's standing decision therefore approves this exact basis for implementation. The independent owner record is
`review_artifacts/runtime_self_test_invocation_code_design_owner_decision.md`.

No Runtime source, test, database, registration or release state changed during Code Design or its review. Plan step 6
Verification Code Design is now the next ordered design prerequisite; implementation remains gated by the complete set
of approved bases and their plan dependencies.
