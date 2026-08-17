# Session Handoff

## Current Mainline

Format and review the complete Runtime Design Contract system before changing
implementation architecture. The review starts from the project-facing T0
authority in `designDoc/the_*.md` and must optimize for a correct, maintainable
Runtime, not for preserving the current document split.

## Completed Since Last Handoff

- Installed the portable governance release in commit `790601f`, including ten
  project-facing T0 contracts and five Governance Skills projected to Claude
  and Codex.
- Installed the complete portable Soul source and generated 118 Claude and
  Codex core and Axiom mirrors.
- Added the Runtime-specific Charter, Claude and Codex project adapters, and a
  complete `CLAUDE.md` entry contract with Identity, startup, First Principles,
  Runtime rules, routing, and authority pointers.
- Verified that a normal fresh Claude Code session discovers and invokes the
  five project Governance Skills. Existing sessions require `/reload-skills`.

## Current Git State

- Branch: `codex/workflow_execution_services`.
- The repository baseline contains the portable Governance Release and the
  complete session deployment.
- The Runtime Charter, entry contracts, Claude and Codex project adapters, and
  this handoff belong to that session deployment rather than the protected
  Runtime implementation lane.
- Pre-existing dirty implementation lane:
  `src/agent_runtime/contracts/registry_release_definition.py`,
  `src/agent_runtime/registry/registry_module_exporting.py`,
  `tests/test_agent_runtime_module_exporting.py`, and
  `tests/test_agent_runtime_native_structured_output.py`.
- Pre-existing generated-file edit:
  `src/agent_runtime/design_contract/agent_runtime_01_module_contract_and_assembly.md`.
- Pre-existing review material:
  `review_artifacts/systematic_rebuild_e8366e7/`.

## Validation Gates

- Project T0 installation check: PASS; ten T0 contracts and the Runtime Charter
  are present and byte-consistent with the installed release.
- Portable governance tests: PASS, 23 tests.
- Claude/Codex session-projection check: PASS, 118 projections in sync.
- Claude project-Skill discovery: PASS in a normal fresh Claude Code session;
  `--bare` and `--safe-mode` are not valid project-customization acceptance
  modes.
- Read-only Codex Primary Agent startup smoke: PASS. The new session loaded the
  required entry files and correctly reported the current mainline, next Design
  review action, T0/T1/T2 rule, protected dirty lane, and package-parity blocker.
- Runtime Design Contract package parity: FAIL. Stale files are
  `the_agent_runtime.md`, `agent_runtime_01_module_contract_and_assembly.md`,
  and `manifest.json`. The `agent_runtime_01` drift existed before this
  deployment and was produced by editing a generated package file directly.

## Open Findings / Risks

- Runtime canonical Design Intent and its packaged projection are not closed;
  do not publish a wheel until the generated-only `agent_runtime_01` change is
  either moved to canonical intent or rejected.
- The current `agent_runtime_01` through `agent_runtime_10` documents predate
  the filename-defined hierarchy. `agent_runtime_00` is T1; non-`00` documents
  are T2 even when old frontmatter says otherwise.
- Several documents mix Runtime ownership with adjacent Agency Platform or
  Software Delivery authority. An `authority` field cannot override an
  `agent_runtime_*` filename; a truly external contract must move to its owning
  product.
- Logical responsibility, physical code organization, concrete technology
  binding, current implementation status, and delivery roadmap must not appear
  as one architectural dimension.

## Next Action

Review and reformat the Runtime Design Contract system as one coherent set:

1. Start with `designDoc/the_charter.md`, `designDoc/the_agent_runtime.md`, and
   `designDoc/agent_runtime_00_execution_charter.md`.
2. Review `agent_runtime_01` through `agent_runtime_10` one by one. For each,
   decide `keep`, `rewrite`, `move`, `merge`, or `retire`; name its single
   semantic owner and map it to one or more of the six Runtime responsibilities.
3. Preserve valid technical detail. Reorganize or remove only what has the
   wrong owner, duplicates another contract, mixes architectural axes, records
   mutable implementation status, or contradicts code truth.
4. Produce `review_artifacts/agent_runtime_design_system_review.md` containing
   the target Design map, per-document disposition, confirmed contradictions,
   and the minimal rewrite order.
5. Do not change Runtime implementation code until this Design review has made
   the module boundaries and canonical contract set coherent.
6. After the canonical Design set is corrected, regenerate the packaged Design
   Contract bundle mechanically and rerun package parity.

The review target is the product result: an independently publishable Runtime
whose design directly explains Registry, Execution, Invocation, Durability,
Ledger, Inspection, their interfaces, and their replaceable bindings.

## Do Not Touch

- Do not discard, stash, stage, or rewrite the pre-existing dirty
  implementation lane without first reconciling its design owner.
- Do not edit `src/agent_runtime/design_contract/` by hand; it is generated.
- Treat `designDoc/the_*.md` as the only project-facing T0 authority. Do not
  resolve or edit deployment-source copies during this Runtime Design review.
- Do not import trading-platform business workflows, Skills, Entitlements, or
  governed data implementations into Runtime core.
- Do not add an ad hoc compatibility layer to make a failing architecture test
  green before identifying the owning design defect.

## Key Files To Read First

- `CLAUDE.md`
- `09_claude/core/PROJECT_ADAPTER.md` or `09_codex/core/PROJECT_ADAPTER.md`
- `designDoc/the_charter.md`
- `designDoc/the_agent_runtime.md`
- `designDoc/the_design_doc_management.md`
- `designDoc/agent_runtime_00_execution_charter.md`
- `README.md`
- `src/agent_runtime/registry/registry_architecture_registration.py`
- `tools/build_agent_runtime_design_contract_bundle.py`
