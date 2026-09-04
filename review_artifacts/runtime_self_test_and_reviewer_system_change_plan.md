# SystemChangePlan: Runtime Self-Test Independence and Reviewer Verification

## Requested Result

Run ordinary Runtime-owned tests and evaluations without a host product's
production authorization service, entitlement, execution grant or fabricated
allow-all authorization evidence. Preserve exact release and input binding,
declared operations, Profile compatibility, resource isolation, output
validation, execution lineage and failure handling.

Use this independent self-test path to exercise registered Reviewer Modules,
then continue capability inventory and verification-runner work under T2 11.
Archive the obsolete Agency Platform host-binding document currently occupying
`designDoc/agent_runtime_10_workflow_execution_binding_and_admission_contract.md`;
retain its historical bytes while removing it from the current Runtime package.
Tests use Runtime-hosted temporary resources. External Ledger writes require
an explicitly supplied external binding; absent such binding, there is no
external Ledger write. Credential resolution remains outside Runtime core.

## Owner Intent and Prerequisites

The user directly authorized entry into SystemChangePlan and clarified:
“正常 runtime 自己测试，用不到 production authorization。” This plan records
that requested boundary; it does not treat the statement as implementation or
review approval. The earlier Runtime plan is prior evidence, not an approved
predecessor whose verdict can transfer to this candidate.
The user separately directed archival of the legacy `agent_runtime_10` host
binding document. Archival does not adopt the separate Execution Contract draft
under `review_artifacts` or create a replacement Runtime contract implicitly.
The direction applies only to the obsolete copy held by this Runtime repository.
It authorizes the local archive move and package removal, not retirement of an
Agency Platform-owned contract or modification of that product's Design bindings.

The unchanged governing inputs are:

- `designDoc/the_charter.md`: the product constitution, distinct from the T1
  `designDoc/agent_runtime_00_execution_charter.md` revised by step 1;
- `designDoc/the_agent_runtime.md`: installed Runtime T0;
- `designDoc/the_system_change_governance.md`: plan and review requirements;
- `designDoc/the_design_doc_management.md`: Design authoring and review;
- `designDoc/the_software_delivery.md`: engineering and delivery requirements;
- `designDoc/the_review_contract.md`: common review rules; and
- `designDoc/the_product_authorization.md`: unchanged database-permission meaning.

Host preparation binds the exact content and hashes of those inputs. Installed
Reviewer prompts and schemas retain their subject-specific meaning.

Code evidence: `run_module()` in
`src/agent_runtime/execution/execution_module_invocation.py` restricts public
calls to Test/Evaluation, but its model-operation branch still requires
`ModuleExecutionAuthority`; the inline evaluation helper also requires an
`authority_factory`. These are the seams to re-design, not proof that a new
authorization service should be built.

## Affected Surfaces

| Surface | Layer | Accountable owner | Required result |
| --- | --- | --- | --- |
| `designDoc/agent_runtime_00_execution_charter.md` | Design | Runtime domain owner | Distinguish Runtime-hosted self-test entry from external production request acceptance without changing the peer responsibility map |
| `designDoc/agent_runtime_09_authorization_integration_contract.md` | Design | Runtime external-authority integration owner | Remove production authorization as a prerequisite for ordinary self-tests; align the candidate's layer and parent to the T2 delegation accepted in step 1; keep external production boundaries separate and avoid assigning non-database permissions to Portable PA |
| `designDoc/agent_runtime_08_agent_execution_adapter_contract.md` | Design | Invocation owner | Define how a self-test invokes an exact compatible Adapter and enforces registered capabilities and resource limits without a production PA decision |
| `designDoc/agent_runtime_11_agent_capability_verification.md` | Design | Agent Capability Verification owner | Define self-test and Reviewer verification evidence, temporary-resource boundaries, optional external Ledger binding, and truthful unavailable/unsupported outcomes |
| Execution kernel and inline evaluation helper | Code | Execution owner | Implement the accepted self-test entry through existing Runtime execution; eliminate required production-authority construction on this path |
| Provider adapters and context preparation, only where required by the accepted self-test design | Code | Invocation owner | Consume the accepted self-test context while retaining operation, workspace, network, schema and output controls |
| Existing temporary record-store and artifact-host composition | Code | Agent Capability Verification owner | Bind test resources explicitly and clean them up according to test policy; no automatic external Ledger destination and no credential custody in core |
| `src/agent_runtime/testing/agent_capability_verification.py` and owning tests | Code | Agent Capability Verification owner | Add inventory and runner slices that verify self-test independence and registered Reviewer behavior from exact inputs |
| Code-owned Design registration/binding metadata, canonical bundle source inventory, projector and package tests | Code | Standalone Release conformance owner | Close T2 09 layer/parent binding against the accepted T1 decision, register T2 11, remove the archived host-binding document from active package inventories, and mechanically project accepted Design bytes; do not create a shadow Design registry |
| Current Design naming inventory in `tests/test_agent_runtime_naming_contract.py` | Code | Source Architecture owner | Remove the legacy host-binding copy from the current Runtime Design inventory; retain existing naming semantics and do not classify archived content as current Runtime authority |
| `designDoc/agent_runtime_10_workflow_execution_binding_and_admission_contract.md` to `designDoc/archive/agent_runtime_10_workflow_execution_binding_and_admission_contract.md` | Release | Standalone Release conformance owner | Perform only the user-directed byte-preserving move of this Runtime-local copy; the archive path and removal from current inventories establish its local non-current status; no note is added inside the file |
| T2 08 references to the archived host-binding document | Design | Invocation owner | Remove its role as a required current contract; any retained historical citation is explicitly non-normative and cannot create an active package dependency |
| Reviewer test Profile and Variant releases, if the accepted design requires new bindings | Runtime | Registry operator | Register exact tested successor bindings without changing Reviewer meaning or mutating existing releases |

Exact files and symbols within the named code-owned seams are assigned by the
approved CodeDesignBasis before implementation. An unexpected ownership or
surface change returns to plan review; it is not permission for an adjacent fix.

## Excluded Surfaces

| Surface | Reason |
| --- | --- |
| Portable T0, Skills and Reviewer source changes | The defect is Runtime's self-test coupling, not a requirement to redesign PA or rewrite review semantics |
| TP source, business data and authorization services | Only the already authorized development environment may be shared; no TP implementation is imported or migrated |
| Agency Platform's canonical Design meaning and external parent/child bindings | The archival instruction concerns the Runtime-local legacy copy only. It neither retires the external contract nor modifies `designDoc/the_agency_platform.md`, TP documents or external Design registries; references in preserved historical evidence remain historical |
| Blanket production authorization removal | Self-test independence does not make production purposes executable or authorize production resources |
| Execution T2 scheduling, target resolution, evaluation/selection and Workflow transitions | These behaviors remain unchanged. The self-test authority precondition is handled by the T1 boundary and T2 09/08 revisions, not a new execution algorithm. The obsolete host-binding document is archived without changing its meaning; the separate `review_artifacts/agent_runtime_t2_candidate_execution/agent_runtime_10_execution_contract.md` is not silently promoted to current Design |
| Provider authentication redesign | Existing provider credential handling remains an Adapter/environment concern, distinct from product production authorization |
| External production Ledger integration | This plan establishes explicit opt-in boundaries only; it does not configure a production sink or write production records |
| Existing persistent Release Registry retention | Previously registered Module/Profile/Prompt records are not reclassified as disposable or deleted |
| SystemChangePlan prepare implementation and Task Routing Registry construction | Those are repository-governance concerns; Runtime self-test changes do not provide their semantic validation or claim review prerequisites have passed |
| Canonical Example Workflow from the later T2 11 slice | Continue through its own reviewed successor after inventory and runner evidence exist |
| Production deployment, publication and active-pointer promotion | This plan targets verified development capability and exact test bindings only |

## Ordered Steps

System Change Review precedes execution of all steps. Design review and owner
acceptance precede Code Design approval, which precedes implementation.

Steps 8, 8a, 9, 9a and 10 freeze implementation candidates and deterministic evidence;
they do not require the final Engineering Review before test bindings exist.
Step 11 registers or resolves only the test bindings determined by the accepted
Design and CodeDesignBasis, using pre-provider/static conformance evidence.
Live registered Reviewer verification follows in step 12, together with the
mandatory exact-commit Engineering Review. Test registration does not grant
production admission. Existing adequate bindings are reused by exact ref/hash.

| Step | Prerequisite | Final accountable owner | Method | Output kind | Gate | Completion |
| --- | --- | --- | --- | --- | --- | --- |
| 1. Self-test domain boundary | Reviewed plan and frozen Charter/T0 inputs | Runtime domain owner | `the-design-authoring` | one T1 candidate | deterministic Design validation, `design_contract_reviewer`, owner decision | Runtime self-tests and external production entry have explicit, separate prerequisites |
| 2. External-authority boundary | Accepted step 1, including its T2 09 delegation | Runtime external-authority integration owner | `the-design-authoring` | one T2 09 candidate | deterministic Design validation, `design_contract_reviewer`, owner decision | Self-test execution has no required production PA service, grant or synthetic allow-all decision; capsule layer and parent match the accepted T1 delegation; production behavior is not weakened |
| 3. Adapter self-test contract | Accepted steps 1 and 2 and the user-directed archive scope | Invocation owner | `the-design-authoring` | one T2 08 candidate | deterministic Design validation, `design_contract_reviewer`, owner decision | Exact Adapter selection, resource controls, operation admission, output validation and failure handling remain enforceable without production authorization; the obsolete host-binding document is no longer a required current dependency |
| 4. Verification contract | Accepted steps 1 through 3 | Agent Capability Verification owner | `the-design-authoring` | one T2 11 candidate | deterministic Design validation, `design_contract_reviewer`, owner decision | Test-local records, optional external sink, Reviewer checks, inventory, runner and completion evidence are explicit |
| 5. Execution Code Design | Accepted steps 1 through 4 | Execution owner | `engineering-code-design` | execution `CodeDesignBasis` | Software Delivery basis validation and owner approval | Existing kernel/helper seams, the handoff to Invocation-owned Adapters, compatibility, failure paths, isolation tests and rollback are assigned to bounded slices |
| 5a. Invocation Code Design | Accepted steps 1 through 4 and approved step 5 interface boundary | Invocation owner | `engineering-code-design` | invocation `CodeDesignBasis` | Software Delivery basis validation and owner approval | Any required provider Adapter/context-preparation changes have Invocation-owned symbols, exact handoffs, tests and rollback; an unchanged Adapter is explicitly retained |
| 6. Verification Code Design | Accepted step 4 and approved step 5 and 5a bases | Agent Capability Verification owner | `engineering-code-design` | verification `CodeDesignBasis` | Software Delivery basis validation and owner approval | Inventory and runner changes, temporary resource composition, real-provider checks and deferred Example work are bounded |
| 7. Package projection and archive Code Design | Accepted exact Design candidates from steps 1 through 4 and the user-directed local archive scope | Standalone Release conformance owner | `engineering-code-design` | projection `CodeDesignBasis` | Software Delivery basis validation and owner approval | Existing code-owned Design bindings cover T2 09 layer/parent; T2 11 registration, local archive, inbound-reference closure, package removal and rollback have one owner and exact tests; unavailable registration is reported rather than invented |
| 7a. Source Architecture inventory Code Design | Accepted step 1 and the user-directed local archive scope | Source Architecture owner | `engineering-code-design` | naming inventory `CodeDesignBasis` | Software Delivery basis validation and owner approval | The exact naming-test inventory adjustment and archive exclusion tests are bounded; naming law and unrelated source topology remain unchanged |
| 8. Execution implementation | Approved step 5 basis | Execution owner | implementation under approved basis | exact engineering candidate | kernel/helper unit tests and negative production/resource tests; freeze exact candidate for joint step 8a testing and step 12 review | Execution-side self-test prerequisites are implemented without production authority; joint provider evidence waits for the compatible Adapter candidate; no final engineering acceptance is claimed |
| 8a. Invocation implementation | Approved step 5a basis and frozen step 8 candidate for joint verification | Invocation owner | implementation under approved basis | exact invocation engineering candidate | Adapter/context positive and negative tests and real model execution seam tests; freeze for step 12 | Joint self-test reaches an exact compatible Adapter without production authority; incompatible operations still fail before provider entry |
| 9. Verification implementation | Approved step 6 basis and deterministic evidence from steps 8 and 8a | Agent Capability Verification owner | implementation under approved basis | exact engineering candidate | inventory, runner, temporary-store and static Reviewer binding tests; freeze exact candidate for step 12 | Runner results bind exact subjects and preserve failed/not-run facts; live Reviewer evidence that needs step 11 bindings is deferred to step 12 |
| 9a. Source Architecture inventory maintenance | Approved step 7a basis | Source Architecture owner | implementation under approved basis | exact naming inventory candidate | naming inventory unit tests and declared archive exclusion fixtures; freeze for step 12 repository-wide verification | Current naming inventory excludes the old host-binding copy without requiring the real archive move to test its rules |
| 10. Design binding, archive and package projection | Approved step 7 basis and frozen step 9a inventory candidate | Standalone Release conformance owner | implementation and existing projector | exact package projection candidate | T2 09 binding checks, archive byte equality, active-reference closure and source/package parity; freeze for step 12 | Accepted Design bytes include T2 11; the Runtime-local host-binding copy is recoverable in archive and absent from current package inventories; no external contract retirement, allowlist bypass or final engineering acceptance is claimed |
| 11. Test binding registration | Frozen candidates and deterministic evidence from steps 8, 8a, 9, 9a and 10; exact test bindings specified by the accepted Design and bases | Registry operator | existing Runtime registration operator | exact test Profile/Variant bundle when needed, otherwise existing exact bindings | dependency/hash checks, pre-provider/static compatibility and store round-trip | Required test bindings resolve without depending on later live Reviewer evidence; existing immutable releases remain unchanged |
| 12. Verification and delivery | Frozen candidates from steps 8, 8a, 9, 9a and 10 and resolved exact test bindings from step 11 | Software Delivery owner | declared verification and exact-commit review | bounded engineering delivery candidate | live registered Reviewer verification, focused/package/naming gates and mandatory `engineering_change_reviewer` review of every exact implementation commit | Scope, approved bases and tests bind the same exact commits as valid Reviewer outputs with `engineering_layer_disposition=passed` and `software_delivery_readiness=accepted`; no production admission is inferred |

## Required Verification

- Ordinary Test/Evaluation model execution needs neither a production PA
  client nor a test double that manufactures production approval.
- Missing/invalid release, input, Profile, operation or workspace binding still
  fails before a prohibited effect. Canonical output validation remains active.
- A caller cannot access production resources merely by selecting a test
  purpose. Existing production entry gates retain explicit negative tests.
- Default self-test records use declared Runtime-hosted temporary resources;
  no external sink is contacted when none is supplied. Credentials are not
  parsed or persisted by Runtime core.
- A provider's login failure is reported as an Adapter/environment failure,
  not as a missing production authorization decision.
- The archived host-binding document preserves its exact pre-move content.
  Current imports, required Design references, package inventories and naming
  inventories do not resolve it as an active Runtime contract. Historical
  review evidence remains intact and no replacement Design is admitted by a move.
- Registered Reviewer evaluations preserve exact input/output, Profile,
  Attempt, usage and failure evidence. They do not manufacture plan preparation
  or subject-specific review acceptance.
- Tests run outside the execution sandbox as authorized. Reviewer isolation
  and test resource restrictions remain explicit controls, not evidence of a
  product production grant.

## Unresolved Decisions

None for this bounded self-test objective. Formal plan preparation, independent
review and later owner approvals remain prerequisites, not claimed results.
