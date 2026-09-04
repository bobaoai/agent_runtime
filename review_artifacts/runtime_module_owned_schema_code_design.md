# Runtime Module-owned Schema Code Design Basis

Status: approved upstream design dependency for implementation.

## Requested result

Allow one Runtime Module authoring package to carry its exact input and output
schema files beside `prompt.md` and `module_registration.json`, while rejecting
undeclared files and preserving support for existing external schema paths.

## Architecture disposition

`refactor`

The current validator prose says the Module directory owns schemas, but its
root-entry allowlist rejects `schemas/`. The implementation must align with the
declared closed-package model instead of forcing a project-local duplicate.

## Logical module

`registry_module_exporting`

- responsibility: load and validate one closed Git authoring source for a
  Runtime Module export;
- owned resources: no durable state; read-only access to the declared Skill,
  Module directory, owner contracts, prompt, and schema authoring files;
- public interface: `load_runtime_module_export` and project/Skill discovery;
- allowed dependencies: Runtime contract validators and Python standard
  library;
- prohibited dependencies: provider adapters, project domains, PostgreSQL,
  execution state, or ambient repository discovery;
- failure and recovery: reject undeclared root entries, unresolved schema
  paths, extra in-package schemas, and path/identity mismatch before release
  compilation; correction creates a new authoring candidate;
- required tests: owned schema package accepted, undeclared schema rejected,
  external legacy schema accepted, and unrelated root entry rejected;
- future capability: additional declared Module assets require an explicit
  registration-contract revision rather than expanding the directory by
  convention.

## Compatibility and rollback

This is additive for existing Module registrations: external input/output
schema paths remain readable. The new `schemas/` root is accepted only as an
exact closure over the two registered schema paths. Rollback restores the
previous allowlist without changing any persisted Runtime release.

## Acceptance criteria

- a closed Module with two registered in-package schemas loads successfully;
- an extra schema under the Module directory fails preflight;
- the complete Runtime suite remains green;
- the standalone Runtime design bundle is regenerated from the canonical
  Design Doc.
