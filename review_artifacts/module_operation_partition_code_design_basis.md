# Module operation partition CodeDesignBasis

Status: approved for the registered `EC-002` correction under the user's
standing instruction to implement valid in-scope review findings directly.

## Intended result

`partition_module_operation_ids` has one canonical implementation beside
`ModuleRelease`, `MODEL_INVOCATION_OPERATION_IDS`, and the
`declared_operation_ids` release field it validates. Registry authoring and
Execution import that contract helper. Neither consumer redefines the
partition rule or imports the other consumer.

## Module and interfaces

- Owner: Runtime Registry release contract.
- Canonical path: `src/agent_runtime/contracts/registry_release_definition.py`.
- Consumers:
  `src/agent_runtime/registry/registry_module_authoring.py` and
  `src/agent_runtime/execution/execution_module_invocation.py`.
- Input: immutable tuple of declared operation IDs.
- Output: the sole model operation ID and exact non-model operation set.
- Failures: wrong container, invalid/duplicate ID, or a model-operation count
  other than one remains a deterministic `ValueError` before registration or
  execution.
- Dependencies: foundation validators and the release-contract vocabulary
  only.

## Verification and rollback

`tests/test_agent_runtime_module_authoring.py` binds both consumers to the one
canonical helper. Runtime architecture tests prove Registry authoring does not
import Execution. Reviewer Module compilation exercises the same authoring
path. Rollback restores the predecessor owner only together with an approved
replacement for the Registry-to-Execution dependency; it cannot duplicate the
helper.

## Acceptance criteria

- exactly one implementation owns the partition rule;
- both consumers resolve the same function object;
- operation validation behavior remains unchanged;
- focused module-authoring, Reviewer compilation and architecture gates pass;
- no provider, persistence, authorization or host behavior changes.
