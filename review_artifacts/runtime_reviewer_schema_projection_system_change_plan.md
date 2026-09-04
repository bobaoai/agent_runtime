# SystemChangePlan: Runtime Reviewer Schema Projection Completion

## Requested Result

基于已冻结的 Agent Execution Adapter T2 和已提交的 P1 projection compiler，完成 Claude/Codex Adapter
cutover、Adapter revision 更新、typed pre-provider projection failure、五个 registered Reviewer 的 static/live
transport evidence，以及 exact Engineering Review 和 commit。canonical Module schema 继续是 Runtime 最终校验
authority；provider projection 只改变 submitted framing copy。

## Affected Surfaces

| surface | layer | owner | required change | reason |
| --- | --- | --- | --- | --- |
| `src/agent_runtime/invocation/invocation_claude_module_invocation.py` | Code | Claude Adapter owner | 使用 `claude_native_output_schema`，projection failure 在 provider 前返回 typed schema failure，revision 升级 | 旧 `_structured_output_format` 不支持 Reviewer positional arrays |
| `src/agent_runtime/invocation/invocation_codex_module_invocation.py` | Code | Codex Adapter owner | 使用统一 Codex projection failure path，revision 升级 | P1 已改变 submitted schema bytes，旧 revision 不再唯一 |
| `src/agent_runtime/execution/execution_module_invocation.py` | Code | Agent Runtime Execution owner | 更新 exact Claude Adapter revision admission bindings | kernel 必须只准入新 revision |
| `tests/test_agent_runtime_native_structured_output.py` | Code | Agent Runtime testing owner | 证明 submitted projection、zero-provider failure、canonical post-validation 和 revision binding | static function tests不能代替 Adapter execution tests |
| `tests/test_agent_runtime_module_authoring.py` | Code | Agent Runtime testing owner | 更新 Reviewer/Profile fixture revision | authoring export必须引用当前 Adapter revision |
| `tests/test_agent_runtime_managed_design_reviewer.py` | Code | Agent Runtime testing owner | 更新 managed Reviewer Profile 和 static adapter revision | live/static path必须解析同一 revision |
| `tests/test_agent_runtime_workflow_authoring.py` | Code | Agent Runtime testing owner | 更新 Workflow Profile fixture revision | Workflow export不能保留旧 Adapter identity |
| registered Reviewer live conformance surface | Code | Agent Runtime testing owner | 对五个 Reviewer 和每个 declared Claude/Codex transport 形成 exact evidence | compatible transport claim 必须来自真实执行 |

## Frozen Upstream Prerequisites

- `designDoc/agent_runtime_08_agent_execution_adapter_contract.md` at SHA-256
  `e9eb813a77ff64d39811ab50993bfe03bfa10ba2bac054fec73fae616f336e76`；本次不修改。
- P1 Runtime commit `b0847e2`，包含唯一 Claude/Codex projection compiler 和五-schema static matrix。
- 当前 registered `engineering_change_reviewer` 仍要求 `ChangeSetManifest`；该输入与用户已确认的
  exact-commit review 不一致，本计划不调用它，也不为兼容旧 schema 生成临时 manifest。Runtime exact
  candidate commit 形成后，由独立 Portable successor 先更新 Software Delivery 与 Reviewer 输入，再对本
  commit 执行正式 Engineering Review。
- P1 full-suite baseline 的既有 blocker 只包括：T2 11 尚未进入 generated Design bundle、Slice 11A testing source
  尚未进入 Runtime architecture registration，以及 `registry_module_authoring` 对
  `contracts.execution_module_definition` 的新增 dependency debt。它们不属于 P2 changed surfaces。

## Ordered Steps

| step | prerequisite | final accountable owner | authoring or implementation method | output object type | review gate | completion condition |
| --- | --- | --- | --- | --- | --- | --- |
| 1 `adapter_cutover_code_design` | frozen P1 commit 和 frozen T2 08 | Agent Runtime Invocation owner | `engineering-code-design` | `CodeDesignBasis` | deterministic basis structure check 和 accountable owner decision | exact basis 绑定本 Plan hash，闭合两个 Adapter、revision、typed projection failure、tests、live matrix、rollback 和 deferred baseline closure |
| 2 `adapter_cutover_implementation` | approved exact step 1 basis | Agent Runtime implementation owner | implementation under approved `CodeDesignBasis` | `engineering_change_candidate` revision containing Adapter/Execution source changes | deterministic source、Adapter revision 和 import checks | 两个 Adapter 只调用统一 projection；unsupported schema 进入 typed failure；所有 source revision bindings 更新 |
| 3 `adapter_conformance_test_candidate` | frozen step 2 candidate | Agent Runtime testing owner | test implementation under approved `CodeDesignBasis` | complete `engineering_change_candidate` including exact test changes | deterministic test collection 和 changed-surface checks | 四个 test files 闭合 submitted projection、zero-provider failure、canonical validation、Profile/Workflow revision；candidate bytes 冻结 |
| 4 `adapter_static_conformance` | frozen step 3 candidate | Agent Runtime testing owner | deterministic Runtime test execution | candidate-bound static test evidence | exact schema、Invocation、Execution、Module authoring、Workflow authoring 和 full-suite commands | P2 focused suite 全过；full suite 相对 frozen P1 baseline 没有新增 failure，三个 predecessor blocker family 保持原 owner；canonical validation behavior 不变 |
| 5 `reviewer_live_conformance` | steps 3 和 4 绑定相同 bytes | Agent Runtime testing owner | provider-gated conformance execution | candidate-bound live test evidence | exact Claude/Codex commands加 canonical validation | 每个 Reviewer 对每个 declared transport 产生 `supported` 或 `unsupported` evidence；本步不修改 registration |
| 6 `runtime_projection_candidate_commit` | steps 1、3、4、5 完成 | Software Delivery | exact-path Git commit | immutable Runtime candidate commit | commit parent、changed paths、content 与 test evidence 的机械绑定 | commit hash 固定，只包含本计划声明的 Runtime source/test paths，不含 Portable T0、Skill 或 Reviewer source |
| 7 `engineering_review_successor_handoff` | step 6 exact commit | Software Delivery | handoff to Portable Software Delivery successor | exact commit ref、parent ref、approved `CodeDesignBasis` 和 commit-bound test evidence | successor 必须先删除 Reviewer 对 `ChangeSetManifest` 的依赖并注册 exact-commit input | 新版 `engineering_change_reviewer` 可直接审 step 6 commit；本计划不伪造旧 Reviewer pass |

## Explicitly Excluded Surfaces

- Portable T0、Reviewer prompt/schema/fixture/registration source migration、Governance projection、Runtime Module
  registration、Reviewer Handbook 和 Release/deployment：Runtime step 6 冻结后进入新的 successor
  `SystemChangePlan`，并从 Design 开始。
- 各 Reviewer 的 checklist、severity meaning 和 owner-specific judgment：本计划只验证 Runtime transport。
- 共享 `AuditResult`、集中 Reviewer semantic owner 和 Review Contract 通用审核入口：均不恢复。
- Runtime Module/Workflow semantics、authorization、Ledger、durability 和 attachment implementation：本计划不修改。
- T2 11 Design bundle、Slice 11A architecture registration 和 `registry_module_authoring` dependency debt：step 7
  后进入 Runtime baseline-closure successor `SystemChangePlan`；该 successor 完成后才能开始 Portable T0 计划。

## Unresolved Decisions

`[]`
