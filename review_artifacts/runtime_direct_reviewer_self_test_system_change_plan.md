# SystemChangePlan: Direct managed Reviewer self-test

## Requested result

Close `EC-001` from the registered review of commit `bbe77ca` with the smallest
Runtime-owned execution seam: one exact registered Module and Profile can run
under an explicit trusted Test/Evaluation resource binding without constructing
or calling Product Authorization. Preserve the existing external authority
path unchanged.

## Affected surfaces

The change includes the canonical Adapter request's paired test-binding fields,
test operation intent and receipt, Invocation preflight and tool dispatch, the
Module execution kernel's explicit test authority, the registered evaluation
helper's test-resource factory, focused tests, and the two review findings that
define this successor.

It excludes production entry, Product Authorization behavior, credentials,
database access, persistent Runtime schemas, Variant Policy, active pointers,
provider selection, Reviewer prompt/schema meaning, and host business code.

## Ordered steps

| Step | Prerequisite | Owner | Method | Output | Gate | Completion |
| --- | --- | --- | --- | --- | --- | --- |
| Direct self-test seam | Registered finding `EC-001`, accepted T2 08/T2 09/T2 11 intent, exact v4 Module/Profile registration | Execution and Invocation owners | approved bounded CodeDesignBasis, then implementation | exact engineering candidate | identity, boundary, provider-preflight, tool-operation, external-regression, architecture and complete environment tests | The same execution kernel runs the registered Reviewer with an exact test binding and no Product authority object; invalid, mixed or changed bindings fail before effect |
| Reviewer recheck | Passing implementation gates and exact commit | Software Delivery owner | `engineering_change_reviewer` v4 | registered Engineering verdict | output schema and semantic validation | Exact successor commit is accepted or findings return to their named owner |

## Unresolved decisions

`[]`
