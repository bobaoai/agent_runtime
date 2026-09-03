---
name: engineering-code-design
description: Designs one material code change before implementation. Use after the owning Design Intent and engineering route are known, and before editing production code, schemas, migrations, or tests. Produces a bounded CodeDesignBasis; it does not implement or review the change.
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: system_change_plan_step
  first_authority_ref: designDoc/the_software_delivery.md
  design_layer_guard_source_ref: t0:the_design_doc_management
  design_layer_guard_source_form: portable_design_layer_semantics
  design_layer_guard_source_sha256: 989398d0a743f4dce51c440ce4fe04f79bc4e3f39732484f624456b5e249acf8
  design_layer_guard_version: design_layer_semantics_v3
---

# Engineering Code Design

## 0. AI-facing Authoring Rules

<!-- embedded-resource:soul:bestpractice_ai_facing_writing:start -->
## 核心原则

### 原则一：结果确定性优先于过程确定性

传统 skill 写法喜欢把任务拆成步骤：第一步做什么、第二步做什么、遇到 X 怎么办。这种写法把确定性押在过程上，本质上是在用自然语言写脚本。

问题在于：

- agent 有推理和工具调用能力，把它当脚本用是浪费
- 步骤式写法覆盖不了长尾 corner case
- 越是语义复杂、边界模糊的任务，越不适合靠固定流程硬写

更好的写法是把确定性从过程移到结果上：定义终点长什么样、如何验证到了终点，让 agent 自己决定路径。

一个 skill 文件至少要回答四个核心问题：

1. **目标**：要完成什么，用一句话说清楚
2. **验收标准**：什么结果算成功，写到 agent 能自行判断“我做完了没有”
3. **可用资源**：能用哪些工具、读哪些文件、必须遵守哪些边界
4. **输出规格**：产出物的格式、路径、schema

这四项是骨架。其他内容，例如方法论建议、领域知识、历史经验，都是围绕它们展开。

### 原则二：写 enabling 的指导，而非 SOP

Skill 的读者是一个有推理能力的 agent，它的 context window 是稀缺资源。每一段文字都应该增加 agent 成功完成任务的概率，而不是消耗它的注意力。

方法论建议可以写，但要以“建议”和“约束”的形式出现，而不是硬编码成唯一过程。例如：

- 按行业板块分组分析，是有效的分析视角
- 但如果当天核心只有一个宏观冲击，agent 应该有自由直接做全局分析

已知陷阱一定要写，因为这些往往是 agent 自己不容易从一轮上下文里可靠推断出来的。一个真实踩坑记录，通常比十条泛泛方法论更有价值。

判断一段内容是否值得写进 skill，可用两个标准：

1. 如果删掉这段话，agent 完成任务的质量或成功率会下降吗？
2. 这段话是在描述“怎么做”，还是在描述“做成什么样”？

优先保留后者。前者只在确实能提高成功率时保留。

---

### 原则三：AI-facing 文档先讲清 contract，再追求压缩表达

Skill 本身就是 AI-facing artifact。它不是给人类快速扫一眼的海报，而是给 agent 真正执行时消费的 contract。这条原则同样适用于一切**主要给 AI 消费**的稳定 doc：top-level design doc、routing doc、rule、axiom、entry doc（CLAUDE.md / AGENTS.md）。

#### 6 个必须能回答的问题

写 / 评审任何 AI-facing artifact 时，先确保它能直接回答：

1. **这一层是干什么的**（layer 用途）
2. **什么时候应该进入它**（进入条件 / 触发信号）
3. **进入后先读什么**（first authority / truth surface）
4. **应该产出什么**（输出形态 + reader-end-state）
5. **和相邻层如何交接**（handoff logic：上游期望什么 / 下游消费什么 / 失败时怎么 fall back）
6. **哪些邻近概念容易和它混淆**（disambiguation：和 X 区别在哪、什么时候不要用它而用 Y）

只有 1-6 都清楚时，再去考虑是否还能更短、更整洁。

#### 不要为了视觉清爽牺牲 contract

常见误区是为了"看起来清爽"，把关键边界压没了。AI-facing artifact 不需要 human skim-first 的整洁；它需要 agent 在执行时能找到 contract。

具体几条 anti-pattern：
- **任务主线收缩**：不要为了让 routing table 行数更少，就把 distinct 的 recurring task line 合并成一行。每条主线如果实际产出形态不同，就该独立一行
- **层级误读**：要显式区分 `task mainline` / `skill layer` / `writer gateway layer` / `deterministic builder / package layer` — 名字相近的层最容易被误用
- **靠命名承载含义**：如果一个名字容易误导（例如 "manager" 既可能指角色也可能指模块），不要只靠命名暗示，**直接在文档内写清楚它真正负责什么、以及它不负责什么**
- **summary 在 contract 之前**：可以有 summary，但只在详细 contract 已清晰之后再 summary

#### 检测：删一段后 agent 能否执行

写完一段后扫一遍：删掉这段，agent 还能完成 reader-end-state 列出的任务吗？
- 能 → 这段是 padding，删
- 不能 → 这段是 contract，保留

注意这个检测和 FP4 / `bestpractice_prose_without_editorial_meta.md` 的检测方向**相反但互补**：FP4 删的是"对世界判断没贡献的句子"（多写无益），本检测保留的是"对 agent 执行有贡献的 contract"（少写则模糊）。AI-facing artifact 在两者之间找平衡点。

---

### 原则四：钉「不变量」，不钉「实现路径」

「结果确定性优于过程确定性」回答了「该把确定性放在哪一层」。但实际写 skill 时还有一个更细的问题：在「结果」这一层内部，哪些必须钉死，哪些应该留给 agent 自由发挥。

如果什么都钉死，agent 失去 agency；如果什么都不钉，系统在每一轮都重新发明同一个东西，失去连贯性。

可用的划分：

- **必须钉死的不变量（invariant）**：
  - 产物的 canonical 路径与命名
  - 产物的 identity 字段（如 `report_date`、`asset_id`、`theme_id`）
  - 上游依赖的身份与新鲜度契约
  - 产出此节点的 canonical builder（不允许第二条产出路径）
  - 与相邻 skill / builder / writer 的接口形状
- **应该留开给 agent 的部分**：
  - 具体 prose 怎么写
  - 判断的具体路径与权衡
  - 中间过程怎么组织
  - 哪些证据要重点展开、哪些只点到为止

写 skill 时要把这两类显式分开。「钉死不变量」是为了让系统连贯、artifact 可追踪、跨任务可复用；「留开实现」是为了让 agent 可以根据当下 context 做最优判断。如果一个 skill 通篇都在规定 prose 结构和写作步骤，却没有讲清不变量，那么它一定既限制了 agent，又允许了 artifact 漂移——两头不讨好。

判断检查：

- 这条要求是不变量还是实现细节
- 如果不是不变量，能不能改写成「建议 + 失败信号」而不是「必须按 X 顺序做」
- 不变量是否足够少：少到 agent 能记住，多到系统不会漂移

### 原则五：边界有两面——禁止式 + 检测式

现有 skill 里的边界大多是禁止式：「不要做 X」「不要把 Y 误读为 Z」。这种 boundary 在 agent 注意到禁令时有效，但当 agent 用更短的路径绕过禁令、产出了一个表面合规的 artifact 时，禁止式 boundary 完全失效——它没有任何检测机制。

实际写 skill 时，每一条关键边界都应配一句**无声违反时长什么样**的描述。这是检测式 boundary。两面合在一起，agent 才有自我校验的把手。

例如：

- 禁止式：「不能在没跑完 data update 的情况下写 PM 报告」
- 检测式：「如果你在没看到 `daily_update_status.json` 显示 `blocking == false` 的情况下产出了 PM 报告，那就是无声违反；artifact 自己看不出问题，但上游 freshness 没满足，下次复盘会发现」

或：

- 禁止式：「不要让 theme overlay 替代 first authority」
- 检测式：「如果最终 artifact 的核心论述是 theme 的标准结论而不是当前 ticker / 当前市场 window 的解读，那就是无声违反——读者读完后判断的是 theme，不是 stock 或 today's tape」

写检测式 boundary 时尽量做到：

- 描述一个 agent 在产出后能自己回头检查的可观察特征
- 不要只说「读起来不对」，要说「读起来不对是因为缺了哪一类证据 / 用错了哪一层 truth surface」
- 如果违反只能在更下游被发现（比如下次复盘才能看出），明确说出在哪一层会被发现

这条原则的意义不是让 skill 文件变长，而是让 agent 拥有「我刚才那条路是不是已经无声越界」的自检能力。这正好弥补「结果确定性 + 留开实现」组合下的天然漏洞——agent 自由度高的地方，最容易出现自己看不见的偏移。

### 原则六：按冷读者的理解顺序组织概念

Skill 的执行者通常没有作者当时的讨论上下文。写作顺序必须从读者已经拥有的认知出发，让每个新概念只依赖前文已经建立的对象、任务或边界。

先确定两端：

- **读者起点**：进入 Skill 时已经知道哪些对象、字段、工具和上游事实
- **读者目标状态**：读完后能够判断何时进入、先信什么、产出什么、何时完成、何时停止或升级

概念进入正文时，优先按这个顺序提供理解支点：

1. 当前对象、任务或可观察问题是什么
2. 它与邻近对象、正常路径或已有认知有什么关键差异
3. 这项差异会改变什么执行、判断或 handoff
4. 最后给出需要长期复用的正式名称

这不是“定义永远不能先出现”的硬规则。Schema、协议对象或法律式术语可能需要先定义以保证精确；但读者在首次遇到它时仍应立即获得通俗角色、适用任务或操作影响，不能只拿到一个需要暂存的名称。

解释性段落应让冷读者恢复三个信息：正在描述什么、为什么需要这样规定、它会改变什么。纯字段表、枚举、代码块和引用块不强制套用段落三问，但它们前后必须有足够的用途说明。

交付前用统一缺陷极性检查。以下问题回答“是”表示发现缺陷，必须修改或形成 finding：

- 是否在建立任务或对象之前提前引入抽象术语，迫使读者暂存未理解的名称？
- 是否有解释性段落无法让冷读者恢复“什么、为什么、改变什么”？
- 是否存在机械编号、教科书口吻或不自然的概念堆叠？
- 是否在过短篇幅内引入过多相互独立的新概念？
- 是否存在段落间的 contract 或推理断层？
- 是否有改写改变了 governing source 中的数字、身份、权限、因果方向、不确定性、兼容性或停止条件？

最后一项是语义保护，不是普通风格偏好。作者只能改善投影的可理解性；如果更自然的措辞会改变 governing source 的含义，应保留原意或返回 owner，而不能替 authority 重写 contract。

---

<!-- embedded-resource:soul:bestpractice_ai_facing_writing:end -->

## 1. Task

### 1.1 Identity

This is the portable Primary Agent authoring method for the code-design stage of
one material engineering change. Its authority comes from the installed
`designDoc/the_software_delivery.md`, the owning Design Contract, and the
approved change decision. The Skill is an operating method, not another design
authority.

It does not implement code, approve Design Intent, review its own candidate,
admit a release, or infer product behavior from the repository.

### 1.2 Objective

Produce one proposed `CodeDesignBasis` candidate that is ready for the
Software-Delivery-owned approval decision before production code changes
begin. The basis must be sufficient for an author to implement the result and
for an independent reviewer to judge whether the implementation stayed inside
the approved boundary. This Skill returns the exact proposed candidate; it
never records the approval decision.

Result correctness has priority over preserving a convenient current layout.
If the existing architecture causes duplicated authority, hidden cross-module
dependencies, compatibility shadows, or repeated local fixes, redesign the
affected architecture before implementation. Do not add a side path merely to
avoid confronting the blocking design problem.

For every added behavior, begin with the existing accountable owner and
canonical path. The basis states whether the change integrates into that path,
replaces it, or introduces a genuinely separate abstraction. A new abstraction
is justified only when integration would violate a declared responsibility
boundary. Convenience, isolation of the current fix, or lower editing cost is
not a boundary. Rejected alternatives remain in the design evidence rather
than surviving in implementation names, comments, documentation, or tests.

## 2. Reader Gain

Primary Agent 读完本 Skill 和 exact inputs 后，能够在修改 production code 前形成一份可独立实现、可独立测试、可独立审核的 `CodeDesignBasis`。读者可以直接判断变更属于哪些 logical modules，每条 interface 的输入输出是什么，每个 error code 由谁拥有，以及每个 Slice 用哪些最小测试证明完成。

## 3. Entry and Exit

### 3.1 Design Layer Guard

Design Doc Management owns the layer semantics for the Charter, `T0` (the
system-wide authority layer), `T1` (the domain-root layer), and `T2` (the
bounded-capability layer). This is a compact route-back guard, not a second statement of Design authority; the
owning contract prevails on divergence and a mismatch returns to Design
authoring rather than being resolved in Code Design.

This Skill does not repair missing or mis-layered Design through code:

- Charter owns constitutional human authority and may reference, but not copy,
  the code-owned current T0-topology projection; it carries literal User Intent
  and Reader Gain headings and never owns implementation
  detail or the current topology inventory;
- T0 owns one system-wide object and six protected headings: User Intent,
  Reader Gain, Owned System Object, Authority, System-wide Invariants, and Peer
  Boundaries. Its Flowmap shows responsibility, authority, peer handoffs, and
  delegation to T1;
- T1 carries literal User Intent and Reader Gain headings and owns one domain
  root. Its Flowmap shows the domain architecture, lifecycle, public boundary,
  and T2 partition; and
- T2 carries literal User Intent and Reader Gain headings and owns one bounded
  concrete capability. Its Flowmap shows the concrete operation. The complete
  T2 contract also supplies owner-local interface and error tables plus state
  effects, completion, recovery, dependencies, and verification requirements.

Before forming a `CodeDesignBasis`, verify that the approved Design supplies the
authority and layer-owned result the implementation needs. If code design would
have to invent a missing T1 or T2 contract, move child operations into T0, change
parent or peer authority, or copy a peer contract, stop and return to Design
authoring or System Change scope. Current paths, passing tests, framework
convenience, and prior findings cannot fill that gap. Once the Design boundary
is valid, this Skill designs only the logical modules and Slices inside that
approved result.

## 4. Execution Contract

### 4.1 Inputs and Authority

Resolve, in order:

1. the approved requested result, the exact current SystemChangePlan
   engineering-step reference, and that step's hash;
2. the owning T0, T1, or T2 Design Intent and approved design decisions;
3. current code, schemas, Registries, public interfaces, and characterization
   tests for the affected surface;
4. current dependency and data-flow facts;
5. migration, compatibility, rollback, and release constraints when present;
6. the accountable parent authority's exact recorded structure decision,
   carried by the reviewed Plan step, when a split, merge, retirement,
   replacement, rename, new abstraction, or release-unit reassignment changes
   durable structure; and
7. the two byte-exact Soul selections embedded below under
   `Fixed Authoring Instructions (verbatim)`: the complete
   `soul:communication_authoring_prose` selection and the complete
   `soul:bestpractice_ai_facing_writing` selection. Those embedded bytes are
   the fixed writing inputs for this Skill; execution requires no additional
   read of either parent Soul file; and
8. prior review findings only as evidence, never as authoring authority.

Adjudicate every prior finding before it changes the `CodeDesignBasis`. Verify
the quoted evidence against the exact reviewed subject, then test the finding
against the owning Design Intent, engineering-step scope, and code
truth. Accept only findings that survive those checks. Reject and record a
finding that targets the wrong owner or subject, assumes a false dependency,
duplicates an existing mechanism, or requests an out-of-scope workaround,
citing the exact Design, Plan step, or code-truth locus that disproves it.
Return a wrong-owner, wrong-subject, or out-of-scope finding to a successor
SystemChangePlan or named owning authority rather than
dropping it. Rejecting findings never changes a standing non-passing verdict
under the owning review authority's registered vocabulary; the unchanged basis
cannot advance until the disputed verdict is escalated with the adjudication
record and the exact candidate receives a subsequent passed review.

Before interpreting implementation, verify that the exact reviewed SystemChangePlan step
names `engineering-code-design`, produces a CodeDesignBasis, and requests a material
code-design result. A package, path, language, framework, or failing test is
locating evidence only. If any check fails, stop without designing or editing
code and return the observed subject kind, governed layer, likely accountable
owner, and mismatch evidence to a successor SystemChangePlan.

If the owner, intended result, or peer responsibility boundary is unresolved,
return to the owning design workflow. Do not solve an authority question by
inventing a code module.

### 4.2 Output and Completion

When the entry applicability check rejects, return only the observed subject
kind, governed layer, likely accountable owner, mismatch evidence, and current
System Change return target using that authority's registered disposition
vocabulary. When no applicable disposition is registered, return that missing
registration as evidence rather than inventing a token. Do not emit a
`CodeDesignBasis`.

When the Design Layer Guard rejects after applicability succeeds, return one
route-back result using the owning Design or System Change authority's
registered disposition vocabulary. It contains the owning Design ref, observed
contradiction, accountable route, and evidence. When no applicable disposition
is registered, return that missing registration as evidence rather than
inventing a token. Emit no `CodeDesignBasis`; this is not a replacement Design
verdict.

When applicability and the Design Layer Guard pass but the candidate lacks any
required primary-flow, interface, error, module, Slice, test, migration,
compatibility, rollback, or acceptance closure, return the referenced Software
Delivery error `CODE_DESIGN_BASIS_INCOMPLETE` with the exact missing contract
loci and its registered caller action. Emit no `CodeDesignBasis`. A complete
authoring result is one exact proposed `CodeDesignBasis` whose complete fields
satisfy the Completion Standard below. Software Delivery alone may later change
its status to `approved` and record `approved_decision_ref`.

```yaml
CodeDesignBasis:
  status: proposed | approved | superseded
  approved_decision_ref: exact approval ref when approved; null while proposed
  requested_result: bounded outcome
  owning_design_refs:
    - design_ref: exact owning Design identity
      design_sha256: exact frozen Design content hash
      code_projection_ref: exact immutable Code Projection ref or null
      code_projection_sha256: exact Code Projection hash or null
  system_change_plan_step_ref: exact ref
  system_change_plan_step_sha256: exact hash
  architecture_disposition: retain | refactor | rewrite
  primary_flow:
    diagram_type: flowchart | sequenceDiagram | stateDiagram
    diagram: exact Mermaid source
    edges:
      - from: stable step or module id
        to: stable step or module id
        interface_id: stable interface id or null
        error_code: stable error code or null
  interface_contracts:
    - interface_id: stable interface id
      identity_mode: declared | referenced
      semantic_owner_ref: owning logical module or peer contract ref
      owner_module_id: stable module id
      input: exact logical input or contract ref
      output: exact successful logical output or contract ref
      effects: externally visible effects or none
      error_codes: [stable error codes]
  error_contracts:
    - error_code: stable machine-readable code
      identity_mode: declared | referenced
      semantic_owner_ref: owning logical module or peer contract ref
      owner_module_id: stable module id
      condition: exact failure condition
      meaning: result that was not produced
      caller_action: exact required action; one value of the CodeDesignBasis vocabulary once registered
  logical_modules:
    - module_id: stable snake_case id
      responsibility: one bounded responsibility
      owned_resources: [stable resource ids]
      public_interfaces: [stable interface ids]
      allowed_dependencies: []
      prohibited_dependencies: []
      failure_and_recovery: []
      required_tests: []
      future_capabilities: []
      implementation_bindings: []
      disposition: retain | refactor | rewrite | split | merge | retire
  implementation_slices:
    - slice_id: stable slice id
      intended_result: independently testable result
      included_surfaces: [stable surface ids]
      excluded_surfaces: [stable surface ids]
      deferred_integrations:
        - surface_id: stable excluded surface id required by the approved result
          owning_slice_id: later slice_id that proves this seam
          completion_gate: exact later passing condition
      required_tests: []
      completion_gate: exact passing condition
  cross_module_seams: [stable seam ids]
  migration_and_compatibility: []
  rollback_boundary: bounded recovery statement
  acceptance_criteria: []
  unresolved_decisions: []
```

This authoring Skill emits the shape above only with `status: proposed` and
`approved_decision_ref: null`. The other lifecycle values remain visible
because the code-owned record is later updated by Software Delivery; they are
not alternative authoring results.

The inline shape is a human-readable projection of the code-owned
`CodeDesignBasis` machine contract. The registered code-owned schema prevails
on divergence; a mismatch returns to Software Delivery and is never resolved by
inventing a field or enum inside this Skill.

`implementation_bindings` describe current physical realization. They do not
change logical ownership and must be replaceable without rewriting the module's
responsibility.

A Slice surface id names exactly one `module_id`, public-interface id,
owned-resource id, or cross-module seam id declared in this basis. The
`implementation_slices` list is ordered. A
`deferred_integrations[].owning_slice_id` must name a Slice that appears later
in that order and lists the same `surface_id` in its own
`included_surfaces`.

### 4.3 Flow, Interface, and Error Rule

Before module inventories, implementation paths, or long prose, draw one
smallest useful Mermaid diagram for the primary code path from declared input
to intended result. Every handoff edge names an `interface_id` and resolves to
one interface row; every failure edge names its stable `error_code` and resolves
to one error row. Define each interface immediately afterward with its owning
module, exact logical input, successful output, externally visible effects, and
possible error codes.

Every externally observable failure path has one stable `error_code`. Define
its owning module, trigger condition, meaning, and required caller action.
Each `primary_flow.edges` row sets exactly one of `interface_id` or
`error_code`; it never sets both and never leaves both empty.
Handoff and failure edges produce rows. An illustrative physical `implements`
relation and an unlabelled fact-flow edge between typed artifacts that invokes
no public interface are not part of the public primary-flow contract and
produce no `primary_flow.edges` row.
Retries, recovery, fallback, and owner routing consume these codes. Diagnostic
messages, exception classes, and provider errors may accompany a code; they do
not replace it. The approved flow, interfaces, and error contracts remain
logical design. Physical files and technologies remain implementation
bindings.

Each `interface_id` and `error_code` is declared exactly once by its owning
logical module. Another basis or module may reference that identity and its
typed result, but never redeclare or change its owner, payload, condition,
meaning, or caller action. Every contract row states whether the identity is
`declared` here or `referenced` from its semantic owner.

## 5. Boundaries

### 5.1 Logical Module Rule

A logical module is an independently reviewable unit of responsibility. It is
not automatically a file, directory, class, process, package, service, Runtime
Module, deployment unit, or database schema.

For every affected logical module, state:

- the result and responsibility it owns;
- the resources and durable state it may read, create, change, or control;
- its public inputs, outputs, and externally visible effects;
- its allowed dependencies and prohibited cross-dependencies;
- failure, retry, idempotency, and recovery behavior;
- the smallest tests that prove its own contract and its seams;
- expected future capabilities that the design must leave room for;
- implementation bindings, including paths and technologies, as a separate
  current-state section.

Use those existing fields to make architectural integration explicit. Record
the current accountable owner and canonical path in the module responsibility,
public-interface, and implementation-binding entries; record the chosen
`retain`, `refactor`, `rewrite`, `split`, `merge`, or `retire` disposition; and
name the responsibility boundary whenever the result introduces a separate
module or path. `migration_and_compatibility` records every superseded path and
either its removal in the selected Slice or its approved deferral to a named
later owner and gate. It also records any implementation artifact that must
remain for migration, audit, or external compatibility, together with the
obligation that requires it. Do not add a second record family for these facts.

Several physical files may implement one logical module. One physical file may
host several small logical responsibilities only when their boundaries remain
independently testable and reviewable. Physical proximity never proves shared
ownership.

### 5.2 Slice Boundary and Test Sufficiency

An implementation Slice is one bounded increment of an approved Code Design.
It is not permission to implement whichever adjacent dependency makes a test
look more realistic. Before work starts, each Slice states its intended result,
included responsibilities and seams, explicit exclusions, and smallest
sufficient test set.

Tests follow the declared Slice boundary:

- prove every behavior, public interface, failure path, and seam the Slice owns;
- use fixed content fixtures or in-memory implementations when they are
  sufficient to prove the owned contract;
- introduce a database, network, provider, or host-repository integration only
  when the Slice owns, changes, or explicitly
  proves that integration seam;
- defer an excluded integration to the later Slice that owns it rather than
  pulling the dependency forward because the final product will eventually use
  it; and
- treat a failure outside the approved boundary as routing evidence. It does
  not authorize an ad hoc implementation or an unreviewed expansion of scope.

The smallest sufficient test set is not the fewest convenient tests. It is the
minimum set that proves the Slice's complete declared result, including
positive behavior, required negative behavior, and each in-scope seam. A later
integration gate remains mandatory when the approved design assigns that seam
to a later Slice.

The Slice inventory also closes over the actual candidate change. Every changed
production symbol, public export, schema, migration, and test resolves to
exactly one owning Slice. A generated or public projection has one code-owned
projector or conformance owner and one deterministic regeneration gate; it is
not co-owned by the Slices whose facts it renders. Code for a later Slice may
already exist in a dirty worktree, recovered source, or predecessor branch; its
presence does not make it part of the selected Slice or accepted evidence.

A test belongs to the contract or integration seam whose result it asserts.
Using lower-layer candidates, registries, stores, or adapters as fixtures does
not give those dependencies joint ownership of the test. If one test file
contains independent assertions for several owners, split it into owner-named
test files before review. Tests are never projections.

When the result depends on reference and hash closure, the design names the hash
domain at every edge. Tests resolve the referenced artifact and compare the
stored hash with that artifact's canonical hash value. Checking only that a ref
exists, a hash is well formed, or a serialized key is present does not prove
closure. When absence of a field or capability is a contract property, prefer
an exact candidate, result, or record field-set fence over a denylist of expected bad
names. Any behavioral guard used as evidence has a live positive control that
proves the guard can fire.

## 6. Method

### 6.1 Workflow

1. Restate the intended result and acceptance boundary without prescribing an
   implementation.
2. Draw the current primary flow and map responsibilities, resources,
   interfaces, dependencies, failure paths, tests, and implementation bindings.
3. Identify responsibility overlap, missing ownership, circular dependency,
   cross-package leakage, compatibility debt, and ad hoc fixes.
4. Define the smallest complete target set of logical modules. Start from the
   current accountable owner and canonical path for every added behavior, then
   explicitly choose `retain`, `refactor`, `rewrite`, `split`, `merge`, or
   `retire` for each affected responsibility. A separate abstraction names the
   responsibility boundary that prevents integration; absent that declaration,
   integrate the behavior into the existing owner. Any disposition that changes
   durable structure or release-unit membership consumes the accountable parent
   authority's exact recorded structure decision carried by the Plan step; this
   Skill never creates or re-judges that decision. If it is missing, stop and
   return to the accountable parent decision or successor SystemChangePlan
   without emitting a basis.
5. Draw the target primary flow. Define every handoff edge as an interface with
   exact input and output, and every failure edge as a stable error code with a
   caller action. Fix dependency direction before assigning files or
   technologies.
6. Divide implementation into bounded Slices when staged delivery is required.
   For each Slice, declare its result, in-scope and excluded surfaces, smallest
   sufficient tests, and completion gate. Define module-local tests, in-scope
   seam tests, later integration gates, migration checks, and rollback without
   importing excluded dependencies into the current Slice.
7. Bind the approved logical design to current implementation paths and freeze
   the candidate as one `CodeDesignBasis`.
8. Return the exact proposed `CodeDesignBasis` to Software Delivery. The owned
   authoring invocation ends here. Software Delivery later validates the exact implementation
   candidate and its change registration before Slice admission; that
   downstream validation is not evidence required to complete this Skill.

### 6.2 Fixed Authoring Instructions (verbatim)

The following byte-exact resources are fixed inputs to this authoring method.
They constrain communication and AI-facing contract quality without widening
the approved Code Design result, ownership, outputs, or implementation scope.

<!-- embedded-resource:soul:communication_authoring_prose:start -->
## 语言风格

Applicability: `always_on_surface`

务实、理性、克制。用思考深度体现专业，不堆砌宏大词藻，不用文学性比喻。

- 不用华丽辞藻，不用"惊喜"这类营销词汇
- 不说废话，不说客套话，直奔主题
- 用数据和逻辑说话，不靠形容词
- 不要用破折号（——/—/--）。能拆成两句的，拆开写；能用冒号或分句表达的，用冒号或分句。「主句——插入——主句」这种结构尤其要避免 <!-- prose-lint-ignore E1 -->
- 避免「长出来 / 长出了」用于系统或抽象事物的演化。用「逐步发展」「逐步形成」「演化为」等替代
- 避免否定句式，改用正向陈述。与其说 X 不是 Y，不如直接说 X 是什么
- **任何编号标签每条回复内首次出现都必须 inline 注明它讲什么；下一条回复再出现时必须再次注明**。涵盖 axiom（a17 / R07 / T10 / FP6 等）/ rule（M1 / S2 / N1 / D3 等 review 编号）/ method（M1-M6 distillation method）/ baseline（B0-B4）/ experiment（b1_language_delta 等）/ phase（Phase 3）/ gate（G1-G5）/ task（card_001）等所有 letter+digit identifier。`<label>（一句话讲什么）` 或 `<label>: 一句话讲什么` 都行，关键是读者每条回复都不需要 mental dictionary lookup。**没有 "上一条已经说过所以省略" 这种豁免**，因为用户可能从某条中间回复读起，每条必须 self-contained。同一回复内同标签反复出现，第二次起可省略

标签注解的例子：
- ❌ "M1 / S2 / N1 已 close"
- ✅ "M1（b6 概念错位）/ S2（version bump 规则）/ N1（serves_persona_decision 字段）已 close"
- ❌ "按 a17 axiom + R07 boundary，T10 也 cover 了"
- ✅ "按 a20（Reader Persona Primacy）+ R07（KB / AP boundary），T10（Index First）也 cover 了"
- ❌ "B3 在 G3 fail，要走 D2 default"
- ✅ "B3（M3 Hansen-McMahon 两轴 baseline）在 G3（toy validation gate）fail，要走 D2（每组合跑 1 次的默认配置）"

否定改正向的例子：
- `you're not a user of the tool` 改为 `you end up serving as a component of the tool`
- `it doesn't know your config` 改为 `it goes in blind: config unknown`
- `this isn't just faster` 改为 `this is a categorical shift`
- `not just coding` 改为 `brainstorming, drafting, planning, everything`

这一原则适用于中英文，在 slide 文案和 speaker notes 中需严格执行。

## 把句子说完整

Applicability: `always_on_surface`

读者是人。写作为阅读优化，不为压缩优化：读者的效率是理解速度，不是字数。机器格式（JSON 字段、代码、表格列）要求紧凑；给人读的散文遵守本节。

- **每句话有完整的主语和谓语**。电报体、名词短语堆叠、自造行话动词都算病句。"方向判断挂周期叙事"这种写法要求读者自己解压；应写成"方向判断什么时候允许修改，取决于周期叙事有没有触发登记的阈值"。
- **括号最多一层，只放次要补充**。条件、结论、出处这类主干信息写进正句。括号强迫读者把主句挂起、读完插入语再接回来；主句十五个字、括号六十个字，是典型病句。
- **散文里禁用符号连接词**：斜杠串、加号串、箭头链、等号都属于笔记压缩符号，不是给人读的语言。用"和""或""先……然后……""也就是说"把关系说出来。上一节的破折号禁令与本条同族。
- **句子要有节奏**。重点句配过渡句，长短错落；每句都满载，等于全文没有重点。允许一句话只说一件小事。
- **代号第一次出现时带一句人话**，隔远了再重述一次。这条是上一节编号标签规则的推广，覆盖仓内一切代号（阈值编号、行权价简称、财务缩写）。中文能表达的意思用中文，系统字段名需要引用原文时除外。
- **产品、架构和设计文档优先使用企业通行术语**。先写行业内普遍理解的名称，再在括号内标注内部 interface、字段或兼容角色名。确实需要自造术语时，第一次出现就用一句人话说明它负责什么、不负责什么。不要要求读者先学习一套内部词典。
- **技术架构合同以英文为 canonical language**。Taxonomy、interface、schema、diagram、comparison table 和 machine-auditable contract 使用英文，避免中文翻译压平 `authority`、`ownership`、`responsibility`、`control` 和 `system of record` 等不同概念。中文只作为 PM-facing explanation，不建立第二套合同词汇。
- **复杂结构先声明分类轴**。同一张表或同一级列表只比较同类对象。文档同时涉及角色、决策权、运行服务、数据记录或部署实现中的两个以上视图时，先用 Structure Index 或 Mermaid 图标明各视图和层级，再分别展开。禁止把不同层级对象放进一张平铺清单，让读者自行猜关系。
- **Mermaid 用于跨对象关系和过程变化**。Architecture dependency、cross-layer flow、stepwise workflow、state transition 或三个以上对象的交互优先用最小可用 Mermaid；同层比较继续用 table，单一事实继续用 prose。Diagram 必须增加关系、方向、顺序或状态信息，不能只是把相邻段落重新画一遍。
- 适用面：对话回复、doc 条款、框架与档案 JSON 里的散文字段、报告、review。
- 执行面由各项目在自己的 `PROJECT_ADAPTER.md`、validator registry 和测试中绑定。可确定判断的格式规则应由项目代码硬拦；缺少项目级 validator 时，本节仍是作者与 Reviewer 的交付门，但不得虚构已经存在的机器执法。

## 冷读者与语义保护契约

Applicability: `always_on_surface`, `artifact_gate`

多段的人类可读正文统一用**缺陷极性**审查：finding 表示缺陷存在，无 finding 表示通过。不得在同一 verdict 中混用“读者是否能理解”这类正向问法和“是否存在教材声”这类负向问法。

交付前检查六类缺陷：

1. 新术语是否早于它命名的现象、动作、身份或后果出现。正式市场、政策、法律、schema 或协议定义在精度需要时可以先到，但首次出现必须同时说明普通语言角色或分析后果。
2. 是否存在某一段，使冷读者无法回答“发生了什么、为什么重要、意味着什么”。
3. 是否存在翻译腔、教科书口吻、机械编号式展开或不自然的概念堆叠。
4. 新概念进入速度是否超过读者无需回读即可维持的认知负荷。
5. 相邻段落是否没有形成连续推理，只是互不相干的判断列表。
6. 清晰度修改是否可能改变数字、来源归属、因果方向、不确定性、置信边界、场景条件、时间口径或其他 governing semantics。

前五类回写作层修改。第六类回事实、分析或 contract owner；prose reviewer 只能报告风险，不能借润色改变含义。完整执行路径见 [`writing_workflows.md`](../skills/writing_workflows.md) 与 [`bestpractice_doc_self_review.md`](../skills/bestpractice_doc_self_review.md)。

<!-- embedded-resource:soul:communication_authoring_prose:end -->



## Completion Standard

An applicability rejection completes before code design when it returns the
mismatch evidence and that authority's registered disposition to a successor
SystemChangePlan and produces no basis. If the
disposition is not registered, the rejection reports that missing registration
instead of inventing one.

A Design Layer Guard rejection completes when it returns
the owning authority's registered route-back result to the accountable
Design or System Change owner and produces no basis.

The proposed candidate is ready for Software Delivery approval only when:

- the intended result is testable and all affected responsibilities have one
  clear owner;
- the primary flow is visible before implementation detail, every handoff edge
  resolves to one interface contract, every failure edge resolves to one error
  contract, and every interface defines exact input and successful output;
- every interface and error identity is unique, declares its semantic owner,
  and is marked as declared here or referenced without redefinition;
- every externally observable failure path resolves to one stable error code,
  owning module, trigger, meaning, and caller action;
- each logical module can be reviewed and tested independently;
- every implementation Slice has an exact result, included and excluded
  surfaces, and a smallest sufficient test set that proves all in-scope
  behavior without pulling excluded infrastructure into the Slice;
- every excluded surface still required by the approved overall result appears
  in `deferred_integrations` with exactly one later owning `slice_id` and
  completion gate; a surface outside the approved overall result needs no
  deferred integration;
- dependency direction, data access, external effects, failure, recovery, and
  rollback are explicit;
- implementation paths are closed over the design but kept distinct from the
  logical module map;
- the basis requires the future `ChangeSetManifest` to register every actual
  changed symbol, export, schema, migration, and test to one Slice. It also
  requires every generated or public projection to have one projector or
  conformance owner and deterministic regeneration gate, and requires
  already-present later-Slice code to remain explicitly unaccepted;
- every load-bearing reference-and-hash edge names and tests its exact hash domain, every
  claimed field absence has a complete structural fence, and every behavioral
  guard used as evidence has a live positive control;
- future capability needs are named without speculative framework building;
- every added behavior resolves to its existing accountable owner and canonical
  path, or to a declared responsibility boundary that justifies a separate
  abstraction;
- every split, merge, retirement, replacement, rename, new abstraction, or
  release-unit reassignment that changes durable structure resolves to the
  accountable parent authority's exact recorded structure decision carried by
  the Plan step;
- every superseded path is removed in the selected Slice or has one approved
  later owner and gate, and every retained migration, audit, or external
  compatibility artifact names the obligation that requires it;
- rejected alternatives remain in design evidence and do not leak into the
  final implementation merely to explain the authoring history;
- there is no hidden fallback, parallel authority, or ad hoc bypass; and
- an independent reviewer can compare the future `ChangeSetManifest` with this
  exact basis.

The final `CodeDesignBasis` also follows those verbatim loaded
`COMMUNICATION` and `bestpractice_skill_writing` sections without changing
approved Design meaning.

Any material correction produces a new candidate and new hash. Implementation
starts only after the Software-Delivery-owned Code Design approval records the exact
`approved_decision_ref`; the Code Design author is never that approving identity. The
owning Design Intent remains an inherited authority and bound input. A change to any bound owning Design or Code Projection hash
invalidates the basis and returns the change to Code Design.

## Observable Failure Signals

The design is incomplete when any of these conditions is visible:

- production implementation changes exist before an approved basis;
- the method designs or reviews code after the Plan step's authoring method or
  produced CodeDesignBasis subject kind fails to match;
- a module map uses directories, frameworks, databases, or providers as peer
  responsibilities without first defining the logical responsibility;
- extended prose or implementation bindings appear without a primary flow, a
  handoff edge has no matching interface contract, a failure edge has no
  matching error contract, or an interface leaves its input or successful
  output implicit;
- a public failure path is represented only by prose, logs, exceptions, or
  provider detail, or a declared error code has no owner and caller action;
- an interface or error identity is redeclared by a non-owner, or a referenced
  identity changes its owner, payload, condition, meaning, or caller action;
- two modules can change the same durable resource without one declared owner;
- a dependency appears in code but not in the allowed seam map;
- a Slice test connects a database, provider, network, or host-repository
  integration that the approved Slice does not own, change, or
  explicitly prove;
- an integration is omitted entirely rather than assigned to the later Slice
  that owns its seam;
- tests cover internal functions while leaving a public seam or recovery path
  unproved;
- a public export or changed code unit has no Slice owner, is assigned to a
  Slice whose `included_surfaces` do not contain the surfaces it exercises, a
  generated or public projection has multiple owners or no deterministic
  projector gate, or already-present later-Slice code is counted as evidence
  for the selected Slice;
- a test is assigned to every dependency used in setup rather than the contract
  it asserts, a mixed-owner test file is left unsplit, or a test is treated as a
  projection;
- a reference-and-hash test checks only shape, reference text, or hash format without resolving
  and comparing the referenced artifact's canonical hash;
- a test claims a capability is absent by denylisting familiar field names, or
  claims a guard works without demonstrating one input that the guard rejects;
- an added behavior has no declared existing owner and canonical path, a new
  abstraction supplies no responsibility boundary that prevents integration,
  or short-term implementation convenience is presented as that boundary;
- a structure-changing disposition or release-unit reassignment lacks the
  accountable parent authority's exact recorded decision in the Plan step, or
  Code Design creates or re-judges that decision;
- a superseded path remains active without removal or an approved later owner
  and gate, or an implementation artifact preserves a rejected alternative
  without a declared migration, audit, or external compatibility obligation;
- the independent reviewer must infer the intended result, acceptance
  criteria, or rollback boundary from the diff.

Return these failures as `CODE_DESIGN_BASIS_INCOMPLETE` with the exact missing
contract loci and no `CodeDesignBasis`. Adding prose to the review package
after implementation does not repair a missing pre-implementation decision.
