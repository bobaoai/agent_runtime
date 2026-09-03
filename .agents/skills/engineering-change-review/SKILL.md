---
name: engineering-change-review
description: "依据已批准的设计决定、exact scope、可复现 gate 与 repository contract，独立审查一个有唯一 parent 的 exact commit。本 Skill 绝不编辑被审 subject，也不审 live working tree、staged diff 或其他未提交内容。"
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: review
  primary_agent_entry_subject: engineering_change_candidate
  first_authority_ref: designDoc/the_software_delivery.md
  design_layer_guard_source_ref: t0:the_design_doc_management
  design_layer_guard_source_form: portable_design_layer_semantics
  design_layer_guard_source_sha256: 989398d0a743f4dce51c440ce4fe04f79bc4e3f39732484f624456b5e249acf8
  design_layer_guard_version: design_layer_semantics_v3
---

# Engineering Change Review

## 1. Task

- class: `primary_agent_development`
- Primary Agent role: `review`
- workflow owner: Software Delivery 下 project-bound 的 engineering-change specialization
- 复用的 assurance rules 来自：`designDoc/the_review_contract.md`
- task: 独立判断一个 frozen engineering change
- output: registered Engineering verdict 与 evidence-bound findings

本 Skill 是 project-bound Engineering Change Governance workflow 的 portable review 方法。
Software Delivery 绑定的 engineering specialization 管 implementation-review task meaning、
Reviewer Module semantics、finding severity、`engineering_layer_disposition` 与 readiness meaning。
Review Contract 只提供 universal reviewer sequence、independence、evidence discipline 与 prose meaning
preservation rules。两者都不拥有对方的 decision。Design Doc Management 不管 as-built verdict。本 Skill
不是 authoring guide，也不是 release gate。不能把它理解为创建 candidate 的方法。

本 Skill 审查 implementation。每个 material code candidate 都必须已有 approved、machine-readable
`CodeDesignBasis`，其中写明 owning logical module、intended result、resource 与 interface boundary、
allowed dependency、implementation path、required test、rollback 与 acceptance criteria。只要 as-built
change 没有超出其中声明的 path、behavior、interface、effect 与 error，既有 approved
`CodeDesignBasis` 就可复用；Design Contract 只提供 owning intent，绝不是 basis。任何 material-change
classification 都返回 Design Doc Management 的 `design_change_classify` interface。Reviewer 对照该
basis 检查 implementation，绝不根据 diff 反向重建或重设产品。

## 2. Reader Gain

冷启动 Reviewer 只读本 Skill 和 closed review package，就能判断是否应进入 Engineering review、应信任
哪一份 `SystemChangePlan` step 与 `CodeDesignBasis`、如何复现 exact frozen subject 与 claimed gates、实现是否
忠实落地 selected Slice，以及应返回哪一个 Software Delivery disposition。读完后，Reviewer 也能识别
需要退回 Design owner 的缺口，而不会在 Engineering review 中补设计、编辑 candidate、扩大 review scope，
或把 review verdict 误当成 release admission。

## 3. Entry and Exit

### 3.1 Design Layer Route-back Guard

Design Doc Management 管下述 Charter/T0/T1/T2 semantics。这是简洁的 route-back guard，不是第二套
Design review 方法；如有差异，以 owning contract 为准，并把不一致返回上游。stored hash 绑定已声明的
`portable_design_layer_semantics` source form，Governance Skill release test 会在 projection 前解析并
比较该 source。

本 Reviewer 携带足够的 Design-layer semantics，以便为无效 basis 选路，但不接管 Design review：

- Charter 管 constitutional human authority；它可以引用但不能复制 code-owned 的当前 T0-topology
  projection；它带有字面标题 User Intent 与 Reader Gain；
- T0 管一个 system-wide object、六个受保护标题——User Intent、Reader Gain、Owned System Object、
  Authority、System-wide Invariants 与 Peer Boundaries——以及 responsibility/authority/peer/T1-delegation Flowmap；
- T1 带有字面标题 User Intent 与 Reader Gain，管一个 domain root 及其 domain architecture/lifecycle/
  T2-partition Flowmap；
- T2 带有字面标题 User Intent 与 Reader Gain，管一个 bounded capability 及其 operational Flowmap、
  I/O、failures、completion、recovery、dependencies 与 verification requirements。

如果 approved `CodeDesignBasis` 要求 implementation 自行发明缺失的 T1/T2 intent、把 child operation
搬进 T0、改变 parent 或 peer authority，或复制 peer contract，应在解释 diff 前停止。通过现有
blocked/not-reproducible subject-closure path 将冲突返回 Design owner；不能另造 disposition、修 basis，
也不能把通过的测试当作替代品。本 guard 只决定 route-back；它不会再次进行一轮对有效 Design candidate
的 semantic review。

这是 subject-closure result，不是 applicability rejection：`not_reproducible` 表示 approved basis
因要求 undeclared Design meaning，无法作为可复现的 implementation authority。

### 3.2 Review Subject 与 Disposition

implementation 分阶段时，review subject 从 approved `CodeDesignBasis` 的 `implementation_slices`
list 中指定一个 exact `slice_id`。Reviewer 独立判断两个问题：commit 是否完整证明该 Slice 包含的
result 与 seam；它是否引入 boundary 外的 behavior、dependency 或 test。Reviewer 不能要求 approved
Slice 明确排除的 database、network、provider 或 host-repository integration。它必须确认所有 deferred
integration 已分给 later Slice 与 gate，而不是被悄悄遗漏。

每次 review 的 subject 都是一个 exact commit：

| Subject | Normal decision |
| --- | --- |
| 有唯一 parent 的 exact `commit_ref` | `accepted`、`changes_required` 或 `not_reproducible` |

Input 与 readiness vocabulary 由 Software Delivery 拥有，并由已准入的 successor input/output schema
注册；本 Skill 不声明尚未准入的 schema identity，也不发明或扩展其值。

每次 review 还返回 Software Delivery 自己定义的 `engineering_layer_disposition`：`passed`、`non_pass` 或
`blocked`。finding 使用 `block`、`fix` 或 `note`。`software_delivery_readiness` 是同一 Software Delivery
result 的解释：`not_reproducible` 必须对应 `blocked`，`changes_required` 必须对应
`non_pass`，`accepted` 必须对应 `passed`。`accepted` 只说明 exact `commit_ref` 已通过 Engineering
Review，不构成 release 或 deployment admission。

branch 不是 review 要求。isolation 来自冻结 subject，而不是把同一份 diff 放到另一 branch。

### 3.3 执行环境

本 Skill 是导出 `engineering_change_reviewer` Runtime Module source 的 Primary Agent entry 与 bootstrap
path。target managed surface 使用由 Agent Runtime 解析并准入的 repository-review Execution Profile。
该 Profile 声明 frozen read-only repository subject、registered command-only execution、deny-write
enforcement、pre/post subject-hash verification、exact cwd/environment/timeout/network boundary 与
writable temporary/output roots。Agent Runtime 管 Context delivery、concrete capability、Adapter、sandbox、
recording、retry 与 lineage contract；本 Skill 消费其 admitted result，不重新定义这些 contract。

Module 要求 complete review instruction、exact commit/parent、reviewed `SystemChangePlan` step、
`CodeDesignBasis`、commit-bound deterministic evidence、acceptance criteria、prior findings 与 output contract，按其 declared
`behavior_policy_ref` 绑定已注册的 behavior policy。repository read 与 command 用于对照 as-built code 和 test
result 验证 brief；它们不是让 Reviewer 通过搜索 tree 反向猜测 assignment 的机制。

进入 engineering review 后必须使用已注册且可执行的 `engineering_change_reviewer` Runtime Module。
Module 或 repository-review Profile 不可用时，返回 Runtime registration 或 execution 的真实 owner 并停止；
Primary Agent 不直审、不改用 tool-free Reviewer，也不使用 ambient Bash 绕过该 gate。

non-managed isolated SDK/CLI run 只可作为 bootstrap 与 comparison path。它使用同一 frozen package
与 declared capability policy，绝不能冒充 managed Runtime execution。current admission 与 migration
state 来自 code-owned binding，不来自本 portable Skill。

tool-free semantic reviewer 可以审 Design meaning、boundary 或 state semantics。它无法满足本 Skill
的 implementation-review gate，因为它不能独立复现 committed diff、dependency closure 或 declared test。

### 3.4 适用性 Gate

在语义上读取 diff 或 repository snapshot 前，Primary Agent Skill 先核实 exact reviewed
SystemChangePlan step 是否产出 `engineering_change_candidate`、是否把
`engineering_change_reviewer` 指定为 registered review gate，以及是否请求 as-built engineering
verdict。commit、branch、directory、test failure、Skill name 或 Reviewer name 都只是 locating evidence。

任一检查失败时，在 review candidate 前停止。使用该 authority 的 registered disposition vocabulary，
把 observed subject kind、governed layer、likely accountable owner 与 mismatch evidence 返回 successor
SystemChangePlan。若没有注册可用 disposition，把 missing registration 作为 evidence 返回，不能发明
token。这个结果是 applicability rejection；它不是 `not_reproducible`、`blocked`，也不是 candidate
verdict。此路径不调用 Runtime Module，因此其 output schema 不适用于 applicability rejection。

### 3.5 何时使用

当 peer Agent 或 human 已形成有唯一 parent 的 exact commit，并需要 independent Engineering verdict 时，
使用本 Skill。尚未提交的 candidate 先由 implementation owner 冻结成 commit，再进入本 Skill。

典型 subject 包括：

- package、architecture、source-layout、schema 或 contract change；
- Skill cluster、registry、validator、migration 与 recovery work；
- workflow、Runtime、data-access、persistence 或 release-boundary change；
- 声称已关闭 earlier finding 的 fix-up candidate。

报告、Thesis、Evidence 或其他 analytical prose 使用 domain content Reviewer。security audit 使用
security-review workflow。Review Contract 只提供统一审核规则；Software Delivery 直接解释本 Reviewer
返回的 exact subject-specific result，不再包装第二个 review result，也不对同一个 engineering candidate
重复运行第二个 semantic reviewer。engineering diff 中的 schema 或 registry 仍属于本 Engineering
Change Review subject。

## 4. Execution Contract

### 4.1 Inputs and Authority

#### 4.1.1 必需 Review Package

每次 review 都从 closed package 开始，其中包含：

1. exact current SystemChangePlan engineering-review step ref/hash，包括 accountable owner、included
   engineering surfaces、produced candidate kind、review gate、completion condition 与 predecessor steps；
2. exact `commit_ref` 与唯一 `parent_ref`；
3. 由 host 从 commit 与 parent 机械生成的 exact path set、registered path state、content hash、committed
   diff 与 subject hash；每条 path 只使用已准入 successor input schema 的一种 state；
4. approved machine-readable `CodeDesignBasis`、其 `design_basis_sha256`、approval reference，以及
   owning contract 或 decision references 对应的 exact frozen Design 与 Code Projection hashes；
5. delivery 分阶段时，从该 basis 中选择的 exact `slice_id`、intended result、included surface、
   excluded surface、带 later owning `slice_id` 与 gate 的 deferred integration、required test 与
   completion gate；这些信息直接来自 approved `CodeDesignBasis`；
6. migration work 所需的 exact Migration Guidance refs，以及记录 actual guidance、Design Doc、registry、
   Code Design、test 与 review delta 的 append-only Migration Log entry；
7. 每个 claimed validation command 及其 working directory、material environment switch、expected
    result 与 known baseline failure；
8. frozen `SandboxCommandPlan` ref/hash，其中包含每个 admitted command、exact interpreter/argv、cwd、
    timeout 与 network policy；Execution Profile 管 environment 与 writable temporary/output roots；
9. current working-tree inventory，且与 exact commit subject 分开记录；
10. 变更声称已关闭的 prior findings；
11. commit 的 change-registration audit：每个 changed production symbol、public export、schema、
    migration 与 test 映射到一个 owning Slice；每个 generated/public projection 映射到一个 code-owned
    projector 或 conformance owner 与 deterministic regeneration gate；
12. 当 basis 或 commit 改变 durable structure 或 release-unit membership 时，由 Plan step 携带的
    accountable parent authority exact recorded structure decision。

`CodeDesignBasis` 直接或通过 code-owned module record 绑定唯一 repository subject，并包含：

- `package_id` 与 `primary_module_id`；
- exact module path 与 declared compatibility slice；
- changed resource 与 interface；
- allowed package-internal module edge 与 external dependency root；
- generated/public projection path、其唯一 projector 或 conformance owner 与 deterministic regeneration gate；
- staged implementation Slice，包括 exact included/excluded surface id、带 later owning `slice_id` 与 gate
  的 deferred integration，以及 smallest sufficient test；
- focused test path、rollback boundary 与 acceptance gate；
- migration work 所需的 exact `migration_guidance_refs`、`intended_result`、`changed_resource_ids`、
  `future_capability_impact` 与 `migration_log_ref`。

ordered cross-repository work 中，每个 repository 都有自己的 exact commit、`CodeDesignBasis`、
subject hash、gate 与 rollback。commit、branch、stash、target directory 或 current implementation 都是
evidence；都不是 Code Design Basis。缺失或未批准的 `CodeDesignBasis` 使 subject 成为
`not_reproducible`。应在语义上读取 diff 前停止；Reviewer 不能替作者补写缺失设计。

如果 claimed gate 没有 exact command、working directory 与 scope，它就不可复现。记录为
`not_reproducible`，不要推断作者的 filter。

Host 在 Review 前与 verdict 后重新解析 exact commit identity，并把结果写入 subject closure。live dirty
working tree 只是 context，绝不是 review subject。Host 报告 commit ref 或其可解析内容变化时，Reviewer
停止并返回 `not_reproducible`。

recovery 或 migration work 的 package 还包含：

- exact recovery source，例如 stash commit、worktree snapshot 或 old package release；
- 每条 discovered path 的 disposition：`reuse`、`rewrite`、`retire` 或 `keep_at_parent`；
- 三方比较：`parent`、`recovery source` 与 exact commit。

当 migration 由 code-owned assessment registry 治理时，package 还包含 exact assessment snapshot、selected
logical module 与 target package record、registry validator command 及其 reproduced result。声称某个
logical module 可进入 implementation 时，必须包含 module-scoped migration-readiness result。声称 package
可 cut over 时，必须包含更严格的 package-scoped result。缺失或 stale registry closure 会使 review subject
成为 `not_reproducible`；prose disposition 不能代替 registered record。

Migration Log 是 decision 与 evidence lineage，不是 current-state authority。对于 migration work，核实其
entry 是否指明 actual Guidance、Design Doc、registry、Code Design、test 与 review delta。若 owning design
semantics 未改变，entry 必须写 `design_change: none`；沉默不构成决定。log entry 不能修复 stale registry、
missing Code Design Basis 或 unapproved design change。

任何 claimed registry 或 conformance command 都会把 executable import 与 test-input closure 纳入 frozen
package。如果复制的 registry 缺少 validator code、referenced schema、source record 或 command 所需的
sibling manifest，就不是 reproducible evidence。

commit status 不决定 semantic authority。uncommitted 与 stash-only work 可以是有效 authoring material，
但必须先形成 exact commit 才能进入本 Review；committed code 也可能过时。approved disposition 与 current
contract 决定哪些内容可被接受。

### 4.2 Output and Completion

Applicability Gate 拒绝进入时，只产出 observed subject kind、governed layer、likely accountable owner、
mismatch evidence、return target 与 owning authority registered applicability disposition。若没有注册
disposition，报告 missing registration，不能发明 token。不要产出下方 registered Module fields，因为
没有发生 engineering review。这是 Primary Agent Skill return，不是 Runtime Module output。

进入 Engineering review 后，严格返回以下 registered fields：

1. `subject_closure`：commit ref、parent ref、subject hash、path count、dirty-tree separation、
   `SandboxCommandPlan` disposition；若为 staged delivery，还包括 selected Slice conformance，含其 boundary、
   test coverage 与 later gate；
2. `gate_results`：每个 exact command、expected result、reproduced result 与 mismatch attribution；
3. `findings`：prior-finding follow-up、vertical defect，以及仅 material 的 longitudinal observation；每项
   都带 severity、evidence、accountable owner 与 `required_change`；
4. `engineering_layer_disposition`：`passed`、`non_pass` 或 `blocked`；
5. `software_delivery_readiness`：一个与 Engineering disposition 一致的值；
6. `prose_and_meaning_preservation`：同一 frozen bytes 上、不改变或豁免 semantic finding 的 candidate-prose result；
7. `safe_next_step`：与 Engineering disposition 和 Software Delivery readiness 一致的最小 authorized next action。

每项 finding 说明哪里错、为何重要、evidence 与必须恢复清楚的结果。Reviewer 报告 defect，绝不重写 candidate
或替作者选择实现方案。

applicability rejection 在以下条件下完成：把 mismatch evidence 与 registered applicability disposition
返回 successor SystemChangePlan，没有语义读取 diff，也没有发出 review 或 readiness disposition。缺失的
disposition registration 作为 evidence 返回，不能由 Reviewer 发明。

Engineering review 只有在 subject 始终 hash-stable、每个 claimed gate 都有 reproducibility disposition、
staged delivery 时 selected Slice 有明确 boundary 与 sufficient-test disposition、每项 finding 都引用 actual
evidence，且已产出一个 Engineering layer disposition、一个一致的 Software Delivery readiness statement、
一个 `prose_and_meaning_preservation` result 与一个 `safe_next_step` 时才完成。任何后续 edit 都产生新
candidate，必须重新 review。最终 human-readable report 还必须通过已安装的 Soul `COMMUNICATION` 规则，
且不能弱化 finding、evidence 或 readiness。

## 5. Boundaries

| Boundary | Detectable violation |
| --- | --- |
| 只 review；绝不编辑 subject | review turn 写入、stage、commit、amend 或 push 被审 path。 |
| 只 review 一个 frozen subject | file hash 或 path set 在 review start 与 verdict 之间变化。 |
| 不发明 gate | report 包含 review package 中不存在的 command，却把它当成作者声明。 |
| Design authority 留在上游 | Reviewer 替换 approved design decision，而不是核实 conformance 或返回 `not_reproducible` subject-closure result。 |
| Entry applicability | Plan step 的 registered review gate 或 produced `engineering_change_candidate` subject kind 不匹配后，Reviewer 仍语义读取 diff 或发出 verdict。 |
| 保持 boundary coherence | candidate 混淆 semantic owner、author、operator、reviewer、persistence owner、implementation technology 或 admission authority。 |
| 分离 dirty-tree effect | 未在 parent 或 exact commit 上复现，就把 failure 归因给 subject。 |
| 保留 recovery provenance | recovered file 没有 recovery source 与 explicit disposition 就进入 candidate。 |
| 保持 migration-registry closure | registry-managed migration 只凭 prose 或 Git state 被接受，没有 current assessment snapshot 与适用的 reproduced module-entry 或 package-cutover readiness result。 |
| 保持 migration-supervision closure | Migration Guidance、owning Design Docs、Code Design Basis、registry snapshot 或 Migration Log 描述不同 scope，或 log 被当作 current-state authority。 |
| code review 前必须有 design | material code candidate 没有 approved `CodeDesignBasis` 分配 responsibility、boundary、dependency、path、test 与 rollback，就被 semantic review。 |
| 先 review logical-module slice，再审 package closure | multi-module candidate 只凭 aggregate test 被接受，但某个 logical-module slice、generated projection gate、resource owner 或 dependency edge 仍未声明。 |
| 保持 Slice boundary | Reviewer 要求 explicitly excluded integration、漏掉 in-scope behavior 或 seam、接受 undeclared scope expansion，或允许 deferred integration 没有 later Slice 与 gate。 |
| 保持 change registration | changed symbol、export、schema、migration 或 test 没有 Slice owner；分给 `included_surfaces` 不包含其所用 surface 的 Slice；generated/public projection 没有唯一 projector owner 与 deterministic gate；或 already-present later-Slice code 被算作 selected-Slice evidence。 |
| 保持 test ownership | test 被分给 setup dependency 而非其断言的 contract 或 seam；independent owner assertion 仍混在一个文件；或 test 被当作 projection。 |
| 拒绝 false-green closure | load-bearing ref/hash edge 只检查 shape 或 key presence；absence claim 依赖 denylist 而非完整 structural fence；或 behavioral guard 没有 live rejection control。 |
| 保持 canonical final state 与 architectural integration | 被拒 authoring history 留在 implementation 中，却没有 declared migration、audit 或 external compatibility obligation；added behavior 绕过 Code Design Basis 指定的 owner 与 canonical path；独立 abstraction 没有 declared responsibility boundary；或 superseded path 没有 approved removal deferral 仍保持 active。 |
| 保持 flow、interface 与 error closure | as-built path 没有 approved primary-flow edge；public interface 改变 input 或 output 却没有 matching basis revision；或 observable failure 缺少 declared stable error code 与 caller action。 |

## 6. Method

### 6.1 通用 Reviewer 规则

以下 block 是所有 Reviewer Skill 与 Module prompt 共用的 byte-exact Review Contract source。

<!-- embedded-resource:t0:review_contract_universal_review_style:start -->
- 先做 subject-specific semantic review；只有 semantic review 通过后，才对同一份 exact candidate bytes 做 prose and communication review；最后只按 owner-specific registered output schema 返回结果。任何阶段缺失、倒序或换用另一份 bytes 都不能形成成功 verdict。
- 只审 exact frozen subject 和 declared closure。背景材料只用于理解 subject，不能扩大被审候选。
- 判断候选按当前写法能否实现其声明的 `Reader Gain` 和 intended result；如果作者、实施者或使用者仍需自行补一个未声明的决定，结果就没有闭包。
- 每个 actionable finding 必须引用 exact subject evidence，并指出哪一个已声明结果仍无法确定；不能先判断某个概念“该不该存在”。
- `required_change` 只说明必须恢复清楚的结果，不替作者选择删除、补全、替换，也不新增 governing contract 未要求的 object、field、workflow、state、lifecycle、policy 或 mechanism。
- parent、peer、child 或 implementation 的问题返回真实 owner；不能借当前 review 审查或重写其他 subject。
- severity 只使用 owner-specific registered meaning；不能为凑 finding 数量、提高措辞强度或强行通过而改变 severity。
- prose check 只能提高可读性，不能改变事实、数字、归因、authority、不确定性、因果、兼容性、停止条件或 governing meaning。
- prior finding 只作为 evidence；必须在当前 hash 上重新判断，不能继承 verdict。
- 只返回 owner-specific registered output object。
<!-- embedded-resource:t0:review_contract_universal_review_style:end -->

### 6.2 Review 工作流

#### 6.2.1 核实 Subject Closure

- 先消费 Host 已通过 schema、hash、Git parsing 与 registration gate 的 exact subject closure；Reviewer 不
  重算这些机械结果。随后阅读 approved `CodeDesignBasis` 正文，判断其 package、primary module、
  compatibility slice、resource、path、dependency、test 与 rollback 是否在语义上覆盖此 repository subject。
- subject 是一个 implementation Slice 时，先核实其 exact result、included surface、excluded surface、
  smallest sufficient test 与 completion gate；之后才能判断 absent external integration 是否为缺陷。
- migration work 中，核实 exact Migration Guidance refs、翻修后的 owning Design Docs、Code Design Basis、
  registry snapshot 与 Migration Log entry 是否描述相同 scope。若声称没有 semantic design delta，必须明确
  写 `design_change: none`。
- 使用 Host 已准入的 path set、path state、content hash 与 committed diff；若 closure 报告缺失、变化或
  schema/hash mismatch，则以 `not_reproducible` 停止，而不是自然语言重做检查。
- 只执行 frozen `SandboxCommandPlan` 中已经机械准入的 command，并核实 reproduced result 是否支持作者
  声称；不重新计算 plan ref/hash 或 command membership。
- 将 actual changed symbol 与 public export 和 selected Slice registered surface 比较。already-present
  later-Slice code 与 frozen subject 分开；它出现在 worktree、branch 或 stash 中不构成 admission evidence。
- 把每个 test 分给其断言结果对应的 contract 或 integration seam；setup fixture 与 lower-layer dependency
  不产生 joint ownership。独立的 multi-owner test file 在拆分前应拒绝；任何被当作 projection 的 test 都应拒绝。
- 拒绝 scope addition、missing path，以及未声明的 generated 或 mirror file。
- recovery work 中，核实每条 source path 是否有 explicit disposition，并确保旧 implementation 没有
  悄悄替换新 contract。
- registry-managed migration 中，核实 assessment snapshot 是否 current，且 selected logical-module、package、
  source 与 dependency record 是否完整覆盖 exact commit。单独核实 review package 的 test 与 rollback
  evidence。声称 implementation entry 时复现 module readiness；声称 cutover eligibility 时复现 package readiness。
- 拒绝没有 declared module owner 的 path、有两个 active owner 的 resource，以及存在 multiple owner、没有
  code-owned projector 或没有 deterministic regeneration gate 的 generated/public projection。
- durable structure 或 release-unit membership 变化时，核实 basis 与 Plan step 是否消费同一个 accountable-
  parent decision；review 绝不创建或重新裁决该 decision。

subject closure 失败时，以 `not_reproducible` 停止。针对 moving 或 incomplete subject 的 finding 不可靠。

#### 6.2.2 复现 Claimed Gates

使用 frozen `SandboxCommandPlan` 中的 exact interpreter、argv、working directory 与 timeout 运行每个
claimed command。Agent Runtime 从 exact Execution Profile 提供 environment 与 writable temporary/output
roots。并排记录 author expected result 与 reproduced result。

对 exact commit subject 运行同一个 focused gate。只有作者声称或 owning contract 要求时
才运行 broader repository gate。把 failure 分为：

- subject-induced；
- dirty-working-tree-induced；
- pre-existing baseline；
- infrastructure 或 unavailable dependency。

只有 base 与 candidate 产生相同 failure，且 exact evidence 已记录时，才可以排除 unrelated known failure。

对于分阶段 delivery 的 exact commit，复现 selected Slice 声明的 smallest sufficient test set。确认它覆盖每个 in-scope
positive behavior、required negative behavior、failure path 与 owned seam。不能为了让测试像最终 deployed
system 就添加 external dependency。database、network、provider 或 host-repository integration 只有在
selected Slice 拥有、改变或明确验证该 seam 时，才属于本次 review。approved basis 已为 excluded seam
指定 later Slice 与 concrete gate 时，它不是当前 finding；approved overall result 所需的 seam 若被全部
Slice 遗漏，则是 Code Design defect，必须返回上游。

#### 6.2.3 阅读实际变更

阅读 full diff 与 structural file 的 post-change form。将它们与 approved decision table 和 author scope
statement 比较。

先独立 review 每个 declared logical-module slice，再 review cross-module 与 package closure。delivery
分阶段时，先按 selected implementation Slice 自身声明的 boundary 判断。先解析 approved primary flow：
每条 handoff edge 必须绑定一个 declared interface，每条 failure edge 必须绑定一个 declared error code。
as-built interface 必须保留 basis 定义的 input、successful output、effects 与 error codes。每个 declared
interface 与 error identity 仍只属于其 semantic owner。referenced peer identity 不能在 candidate 中
被重声明，或改变 owner、payload、condition、meaning 或 caller action。

对每个 changed requirement，识别：

- logical owner；
- 它改变的 executable 或 persisted surface；
- 执行它的 validator 或 test；
- 消费它的 downstream interface；
- failure 与 rollback behavior。

对每条 externally observable failure path，按 approved basis 核实稳定 `error_code`、producing owner、
trigger condition 与 caller action。log message、exception class、prose phrase 或 provider response 可作
diagnostic evidence，但不能成为 public failure contract。任何 undeclared input、output、effect、fallback
或 error code 都应作为 design drift 拒绝。

对每项 load-bearing contract claim，尝试一个具体 false-green mutation：描述一项会使 declared result
失真的 production change，再找出必须失败的 exact test。如果没有现有测试会失败，Slice evidence 就不足。
尤其应对 ref/hash binding、policy 或 schema authority、capability absence 与 recovery fence 做此检查。
ref/hash edge 只有在测试解析 referenced artifact，并将 stored hash 与其 canonical hash value 比较时才闭包。
只有 key-set assertion 而没有 value comparison，不能证明 hash domain。

当某个 field、dependency、provider choice、path 或 capability 的不存在是 declared result 时，在可行处核实
exact input/result/record field set；不要接受 familiar bad name denylist 作为完整证明。当 guard 或 sandbox
predicate 本身是 evidence 时，既要求 allowed path，也要求 live positive control 展示一个被拒 input。

按 directory convenience、current technology、legacy location 或 copy source，而非 approved responsibility
owner 放置的 code，即使 local test 通过也是 boundary defect。反之，如果 approved `CodeDesignBasis`
把 source 分类为 `rewrite`，不要要求 legacy file parity。

把 exact commit 当作 cold-start final state 阅读。如果 name、comment、document、test、compatibility
path 或 release note 的唯一作用是解释或保留被 `CodeDesignBasis` 拒绝的 alternative，则属于 architectural
finding；除非 Plan step 或 basis 声明必须保留它的 migration、audit 或 external compatibility obligation。

将每项 added behavior 追溯到 `CodeDesignBasis` 指定的 accountable owner 与 canonical path。new parallel
path、wrapper、adapter、registry、state store、schema 或 orchestration layer 必须由 basis 写明阻止其并入
existing owner 的 responsibility boundary。Reviewer 只核实该声明及其 as-built conformance；绝不补写
缺失 justification。superseded path 要么在 selected Slice 中移除，要么由该获批 Slice 延期给 named later
owner 与 gate。

按系统必须维护的 concept、owner、dependency、state 与 execution path 判断该边界，而不是按 local diff size。
correct behavior 与 passing test 不能免除 architectural integration defect。将这些 defect 记录为 `block`
或 `fix`，绝不能是 `note`。

当某项条件可以机械测试时，仅在 instruction 中承诺，不算已经实施 deterministic code-level enforcement。

#### 6.2.4 应用相关 Cross-Cutting Checks

只运行被 subject 触发的检查：

##### 6.2.4.1 Scope 与 Dependency Closure

- package import 留在 declared dependency boundary 内；
- normal execution 不依赖 hidden filesystem、ambient session 或 sibling repository；
- registry、schema、code binding、test 与 generated projection 一致；
- deleted 或 retired entry 已从 active discovery path 移除。

##### 6.2.4.2 Data 与 Time Contract

- schema enum 解析到真实 store 或 registered object；
- timestamp field 遵守 `designDoc/the_timestamp_semantic.md` 及其 code-owned matrix；
- data read/write 使用 owning gateway 与 declared authorization boundary；
- fixture 与 integration test 覆盖 happy、denial 与 drift path。

##### 6.2.4.3 Skill 与 Host Projection Closure

Skill Management 拥有 canonical Skill method；Agency Platform 拥有 host projection relationship。
code-owned Governance Skill manifest 与 generated inspection 提供本次 review 使用的 exact projection set，
但不取得这两项语义 authority。`SKILL.md` 投影到 manifest 声明的 supported Primary Agent hosts；Runtime
Module asset 只投影到 manifest 声明的 canonical Skill Package surface。Runtime transport compatibility
（包括 Codex CLI execution）是独立 Runtime concern，不要求这些 asset 的 Codex host projection。

missing/undeclared target、与 manifest 不同的 projection set、byte drift、undeclared package member 或
stale release hash 都是 finding，并返回 Agency Platform 或其 code-owned projection implementation owner。
未来 host-specific semantic delta 必须先明确修改 governing contract、Registry、manifest、projector 与
test，review 才能接受。

##### 6.2.4.4 Architecture Classification Integrity

subject 改变 architecture 时，分别检查三个 view：

1. logical responsibility；
2. physical source organization；
3. implementation binding。

一个 peer list 或 diagram 只能使用一种 view。每个 arrow 声明一种 meaning，例如 runtime call、data flow
或 source import。directory 或 current technology 不能悄悄成为 peer logical responsibility。

还要核实整个 change 的 boundary coherence：

- semantic owner；
- authoring authority；
- operator 或 execution surface；
- independent review method；
- persistence 或 data owner；
- implementation binding；
- approval 或 admission authority。

这些维度可以交互，但不能被呈现为可互换的 peer module、role 或 decision。若 ambiguity 会改变谁决定、
写入、执行、审查、持久化或准入 subject，它就是 material。把冲突报告给 accountable owner；不能在 review
turn 内修 design。

对于 modular subject，还要核实：

- 每个 module 管一个 coherent capability 与一个 stable resource set；
- peer module 使用同一 classification axis；
- source path、fixture、test 与 interface 有唯一 owner 或 declared consumer closure；
- actual import 是 approved acyclic module graph 的子集；
- 每个 module 都能独立 test、review、rollback，无需读取 sibling module private implementation；
- compatibility slice 有 explicit retirement gate。

##### 6.2.4.5 Recovery Integrity

- useful behavior 按 approved disposition 选择，不按 commit state 选择；
- copied code 保留 required test 与 dependency；
- obsolete WIP 不覆盖 newer schema、registration 或 hash；
- rewritten code 按 new package contract 而非 legacy implementation parity 判断；
- retired material 从 active discovery 移除，并通过 governing retirement mechanism 记录。

#### 6.2.5 在不改变含义的前提下审查 Candidate Prose

Engineering semantic checks 完成后，只检查 candidate 中 human-facing 的 Design Doc、README text、comment、
error message、migration note 与 release note 是否清楚且保留原意。本阶段不把 source-code style 当 prose，
不改变 approved `CodeDesignBasis`，也不豁免 semantic finding。对同一 exact commit bytes 在
`prose_and_meaning_preservation` 中记录结果。

### 6.3 Engineering Finding 的适用方式

上方 universal block 管 finding vocabulary。在 Engineering evidence 上按以下含义应用：

- `block`：invalid subject、broken approved boundary、failed deterministic gate、unsafe canonical effect
  或 unrecoverable admission conflict 阻止 Engineering layer 通过；
- `fix`：material implementation、compatibility、migration、recovery、security 或 enforcement defect
  必须在 pass 前修复；
- `note`：non-blocking observation 或 explicitly accepted bounded debt。

不能把 cross-project 或 historical process weakness 提升为针对当前作者的 blocking finding。longitudinal
observation 放在独立 section，并写明 actual owner。
