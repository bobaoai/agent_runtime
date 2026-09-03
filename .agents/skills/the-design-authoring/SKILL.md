---
name: the-design-authoring
description: 依据一条精确、已审查的 SystemChangePlan step，创建、修订、取代或退役一份 Charter、T0、T1 或 T2 Design Intent candidate。只负责形成冻结候选，不负责规划、独立审查、批准、实现、注册或发布。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: system_change_plan_step
  first_authority_ref: designDoc/the_design_doc_management.md
  design_layer_semantics_source_ref: designDoc/the_design_doc_management.md
  design_layer_semantics_version: design_layer_semantics_v3
---

# Design Intent 创作

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

把一条已经完成规划和路由的 Design change，写成一份由正确 Charter、T0、T1 或 T2 owner
拥有的完整 Design Intent candidate。一次调用只处理 SystemChangePlan step 指定的一个
Design subject，允许创建、更新、取代或退役 candidate。

Candidate 必须在实现开始前说明稳定结果、authority、boundary、Flowmap、公开交接、可观察
失败、生命周期义务和代码交接，同时不得把当前代码、Reviewer control plane 或其他 owner
的 contract 写成自己的 Design authority。

本 Skill 只完成 authoring。它不创建或修改 SystemChangePlan，不执行独立审查，不接受或
拒绝 candidate，不作 implementation 或 release decision，也不调用 Runtime Module。

结构新增、提升、拆分、合并、替换、重命名或退役仍写进普通的 layer-owned Design
candidate。不得另建 `StructureChangeProposal`、第二份 Design subject 或第二个 Reviewer
路径。

本 Skill Package 同时携带独立 `design_contract_reviewer` 的 Prompt、Schema、Fixture 和
Module registration source。这些文件用于下游 Runtime registration，不进入 authoring
invocation；Design author 不运行、修改或准入该 Reviewer。Material Design candidate 必须使用已注册的
`design_contract_reviewer` Runtime Module 完成独立审核；Module route 不可用时返回 Runtime owner，
Primary Agent 不直审也不换用相似 Reviewer。

## 2. Reader Gain

冷启动 Primary Agent 只读本 Skill、精确的 reviewed SystemChangePlan step 和第 4.1 节列出的
authority inputs 后，可以：

1. 判断目标是 Charter、T0、T1 还是 T2，以及该层必须表达和必须下沉的内容；
2. 判断请求是否进入 `the-design-authoring`，还是必须退出到 System Change scope；
3. 在不丢失既有细节的前提下，形成主次清楚、Flow/I/O/error 闭合的 Design candidate；
4. 区分 Design Intent、code-owned truth、当前 inspection 和 review evidence；
5. 判断 candidate 是否完成、应返回哪个 owner，以及下一步只能交给哪个既定 gate。

## 3. Entry and Exit

开始编辑前，逐项确认：

1. 输入包含一条精确、已审查的 SystemChangePlan step；
2. 该 step 指定 `the-design-authoring`，并只产出一个 Charter、T0、T1 或 T2 candidate；
3. step 指定 accountable Design owner、目标 layer、owned object、required result 和 subject；
4. portable source 与 project-facing projection 的目标没有混淆；
5. 当前任务不是 Skill、Code、Runtime registration、deployment 或独立 review change。

任何一项不成立，停止 authoring，返回 `DESIGN_ENTRY_MISMATCH`、observed subject、target
layer、likely owner、exact evidence 和 successor SystemChangePlan return target。标题、路径、
附近 Skill、当前代码和对话历史都不能替代该 Entry Gate。

Entry 通过后仍存在以下退出：

- layer、owner、parent、peer scope 或 structure 未决：返回 System Change scope，不生成
  candidate；
- Plan scope 有效但 candidate-owned semantic decision 未决：返回 accountable Design owner，
  不生成 candidate；
- DDM 与本 Skill 的 layer summary 不一致：返回两份 exact evidence 和 affected meaning，
  不生成 candidate；
- authoring 成功：冻结第 4.2 节定义的结果，只进入 materiality 选择的下一 gate。

Material candidate 只进入独立 Design review。Non-material candidate 只进入 DDM 定义的
non-material gate。Authoring 完成不等于 review、approval、Current admission、implementation
authorization 或 release。

## 4. Execution Contract

### 4.1 Inputs and Authority

写作前必须取得：

1. exact reviewed SystemChangePlan 与目标 step ref；hash 由 host 计算并绑定；
2. accountable owner intent、目标 layer、owned object、required result 和 materiality；
3. Charter、适用 parent、完整 same-level peer set、dependencies 和 inherited T0 constraints；
4. 对修订任务，current Design release、predecessor 和必须保留的 admitted meaning；
5. 对结构变更，Plan 已确定的 parent、完整 peer set、predecessor/successor coverage 和 handoff
   boundary；
6. 对 supersession 或 retirement，predecessor、successor（如有）、当前 inbound-reference
   inventory 和待决 disposition；
7. current code、Registry、schema、persistent state 和 generated inspection，仅作为 as-built
   evidence；
8. prior findings（如有），仅作为需要重新核对的 evidence。

以上 required input 必须以 exact stable ref 和冻结内容或冻结 inspection 进入同一次 authoring
invocation；由 host 计算并绑定 hash。需要表达 current 状态的 input 必须声明其观察时点。任何 required
input 缺失、ref 无法解析、current evidence 已过时，或 Plan、owner intent、current release、parent、
peer、dependency 与 inbound-reference inventory 相互冲突时，停止 authoring，返回冲突 evidence、真实
source owner、System Change return target 和 `candidate_not_created`，不得自行选择一版继续写作。

Design Doc Management 定义 Design artifact、layer、lifecycle、materiality 和 review condition。
目标 Charter、T0、T1 或 T2 owner 定义 candidate 的具体 meaning。Principal Manager 或明确
获委派 owner 作出 material product 和 architecture decision。Skill Management 只拥有本
Skill artifact。

本 Skill 可以整理、表达和冻结已获授权的 Design meaning，不能替任何 authority 补一个
未声明的产品、结构或边界决定。DDM 与本 Skill 的 layer summary 不一致时，以 DDM 为准，
停止本次 authoring，并返回 System Change scope；不得静默选择一版。

Prior finding 不能直接成为 authoring instruction。先核对其 quoted evidence、owning
requirement、correction owner 和 Plan scope；只有 accountable owner 接受后，才可据此修改
candidate。Wrong-owner、wrong-subject、事实错误或越界 finding 返回真实 owner。现有
`non_pass` candidate 未改变并重新通过审查前不能前进。

### 4.2 Output and Completion

成功时返回一份冻结 authoring result，包含：

1. exact SystemChangePlan 与 step ref；hash 由 host 计算；
2. candidate path、kind、layer、stable identity、owner、applicable parent 和 owned object；
3. complete candidate body；content hash 由 host 计算；
4. materiality 与适用的下一 gate；
5. changed surfaces、preserved meaning 和明确排除项；
6. layer-appropriate Flowmap，以及 owner-local interface/error closure；
7. affected parent、peer 和 dependency refs；
8. machine-contract 与 implementation handoff；
9. 不阻止 candidate identity 或 scope 的 open decisions；
10. authoring self-check observations。

结构变更还返回 Plan 已确定的 parent 和 complete peer context refs，供同一 Design candidate 的
后续独立审查使用；这些 refs 不进入 candidate body，也不创建第二个 subject。

本 Skill 不计算 hash，不创建 approval、独立审查结果、lifecycle transition、registration、
release 或 deterministic validator evidence。

成功 candidate 必须满足：layer scaffold 和 owner boundary 明确；既有 meaning 保真；按第 6.3 节
适用范围要求的 Flowmap、I/O、error、completion 和 handoff 闭合；没有为“完整”而添加
lower-layer 或 control-plane detail；下一层无需 redesign。

阻塞退出也必须完整返回阻塞维度、exact evidence、真实 owner 和 return target，并明确
`candidate_not_created`。不得返回半份 candidate，让下游猜测哪些部分仍有效。

## 5. Boundaries

| Boundary | Observable violation |
| --- | --- |
| 只做 authoring | 本调用规划、审查、批准、实现、注册、发布或执行自己的 candidate |
| Exact Plan step | 根据文件名、附近代码或对话记忆选择另一个 subject 或 method |
| Owning intent controls meaning | 因当前代码方便而发明 product behavior、authority 或 structure |
| Layer integrity | Charter/T0/T1 吸收 child operation，或 T2 扩大 parent/domain authority |
| Design/code separation | Mutable implementation path、provider、schema、test count、binding 或 release state 成为手写 Design authority；Design candidate 自身的 stable identity、canonical filename 和 source location 不在此列 |
| Candidate/control-plane separation | Review routing、Reviewer prompt、finding、Runtime execution、usage 或 approval evidence 进入 candidate body |
| Portable/project separation | 同时手写 portable source 和 project projection，或未按 Plan 选择 source |
| 保真修订 | 把未获授权删除的既有细节缩略、概括或静默丢失 |
| Review independence | Self-check 被当作 independent verdict，或 author 在 Reviewer role 修改 candidate |

下一步只按 SystemChangePlan 和 DDM 的 gate 交接冻结 candidate。本 Skill 不选择 provider、
Reviewer identity、Runtime Profile、deployment path 或 release timing。

## 6. Method

### 6.1 冻结本次边界

先写清本次 changed surfaces、必须保留的 meaning、明确排除项和 downstream handoff。对已有
Design Doc 的修订不是摘要任务：凡 Plan 未授权删除或替换的 admitted detail，都必须逐项
保留。只有新 Design 明确改变某项 meaning 时，才能增、删或替换对应内容。

一次只编辑 Plan 指定的 source。Portable baseline 和 project-facing projection 不同时手写；
后续 deterministic deployment 负责机械投影。

### 6.2 按 layer 创作

<!-- embedded-resource:t0:design_layer_semantics:start -->
Charter、T0、T1 和 T2 都必须明确 `User Intent` 与 `Reader Gain`。`User Intent` 说明
contract 为什么存在以及它服务的 product outcome。`Reader Gain` 同时写明读者，以及
读完后新增的区分、决策或执行能力。

所有 Design Doc 的解释性正文以中文为主体；受保护 heading、registered identifier、
`interface_id`、`error_code`、schema field、code symbol、path 和需要精确匹配的引用标识符
保持原样，不另造翻译名。标题以下的 section 使用连续数字编号；编号只服务
阅读导航，不表达 authority、priority、lifecycle 或执行顺序。

| Layer | 拥有的设计问题 | 必须表达的结果 | 必须下沉的内容 |
| --- | --- | --- | --- |
| Charter | 项目的 constitutional identity 与 human authority | product identity、scope、Principal Manager authority、constitutional invariants、Design/code boundary、amendment authority，以及对当前 T0 topology projection 的类型化引用 | Operational flow、peer interface/error、Runtime binding、Reviewer execution 和当前 T0 inventory |
| T0 | 多个独立 T1 必须一致回答的一个 system-wide question | `User Intent`、`Reader Gain`、`Owned System Object`、`Authority`、`System-wide Invariants`、`Peer Boundaries`，以及 T1 delegation、machine-enforcement result 和 review requirement | T1/T2 workflow、code field、provider、directory、database、Reviewer Module execution 和 peer internal contract |
| T1 | 一个 project domain 或 independently governed product boundary | Domain outcome、objects/states、architecture/lifecycle、public boundary、quality rules、loops、human decision gates、inherited T0 constraints、dependencies、T2 partition、completion 和 failure | T2 operation detail，以及其他 T0 拥有的 authorization、data、time、Runtime、audit 和 delivery law |
| T2 | 一个 T1 domain 内的有界 capability，以及它与 code truth 的稳定交界 | Capability intent、boundary，以及实现该 capability 所需的最小适用 machine-contract/code-truth 集合；按实际需要表达 operation、I/O、effect、failure、interface/Protocol、state、transaction、replay/recovery、Schema/Registry/Validator identity、Code Projection/Current Inspection binding 和 verification | 第二个 domain root、parent authority 扩张、cross-domain ownership，以及与本 capability 无关的 machine surface；exact path、当前 class/module layout、物理 schema/table、current version/hash/binding、测试数量和 deployment state |

T2 的 machine-contract/code-truth 项不是统一必填清单。Pure transformation 不必发明 state、transaction、
idempotency 或 rollback；没有 public operation 的 internal capability 不必发明 public interface；不使用
Schema、Registry 或 Validator 的 capability 不得为形式闭包创建它们。T2 只冻结下一层无需重新设计所必需的
最小适用集合，并对不适用的 required section 明确写 `none` 或 `not applicable` 及原因。

新 capability 尚无 implementation 时，T2 固定 required code-truth result 与 implementation handoff；已有
implementation 时，T2 绑定适用的 immutable `Code Projection`，并把 mutable current version、binding、
inventory 和 gate result 留给 `Current Inspection`。两种情况都不能从 current code 反向发明 capability intent。
<!-- embedded-resource:t0:design_layer_semantics:end -->

标题以下的 section 使用连续的 hierarchical numeric index。受保护 heading name、registered
identifier、`interface_id` 和 `error_code` 保留精确英文；解释性正文以中文为主。数字只服务
阅读导航，不表达 authority、priority、lifecycle 或执行顺序。

Charter、T0、T1 和 T2 都使用 `## 0. Intent Capsule`。四层共同包含 `layer`、`status`、
`canonical_owner`、`owned_system_object`、`scope`、`non_goals`、`inputs`、`outputs`、
`truth_surfaces`、`runtime_triggers`、`downstream_consumers`、`open_decisions`、`review_gate`、
`runtime_surface_ledger` 和 `verification_hooks`。T0 另外必须包含唯一 `t0_layer_id`；T1 和 T2
另外必须包含唯一 `parent`；Charter 不包含 `t0_layer_id` 或 domain parent。Capsule `inputs` 只放本 authority 接受的 work-plane input 或 exact reviewed
SystemChangePlan step；parent、peer、dependency 和 mutable evidence 留在对应正文或控制面。

T1 canonical filename 是 `<domain>_00_<subject>.md`。每个 active domain 恰好有一个 active
`00` root。T2 filename 是 `<domain>_<NN>_<subject>.md`，其中 `NN` 不是 `00`；domain prefix
把它绑定到唯一 T1 root。Cross-domain reference 是 dependency，不是第二个 parent。

### 6.3 先画 Flowmap，再闭合 I/O 与 error

Charter 可以使用 constitutional authority map，但不要求 operational flow。T0、T1 和 T2
在 extended prose 之前提供 `## 1. Primary System Flow`：

- T0 展示 system responsibility、authority、peer handoff 和 T1 delegation；
- T1 展示 domain objects/states、architecture/lifecycle、public boundary、quality rules、loops、
  human gates、dependencies 和 T2 partition；
- T2 展示 concrete operation、effects、completion 和 recovery。

以上 Flowmap、interface 和 error 表达规则适用于新 candidate，以及 material revision 实际改变的
完整 surface。对尚未迁移到该表达方式的已准入旧文档，non-material revision 只修改 Plan 明确授权的
surface，并保留其既有 scaffold；不得顺手引入 syntax-only 迁移。该旧文档在下一次 material revision
时再迁移，除非 reviewed Plan 已把迁移本身列为 material changed surface。

Flowmap 只画本 layer 拥有的 logical responsibility、state 和 governed resource。每个
owner-local public operation 使用一个 `interface_id`，并解析到唯一 interface row；row 写明
owner、input、successful output、effects 和 error codes。每个 caller-visible failure 使用一个
`error_code`，并解析到唯一 error row；row 写明 owner、condition、meaning 和 caller action。

图、表和正文必须一致。如果本 layer 没有 owner-local public operation 或 caller-visible
failure，对应表格明确写 `none`，不能复制 child 或 peer 的 operation/error 充数。Peer 只作为
owner-qualified input、output、decision 或 result 出现。

### 6.4 分离 Design、code 与当前事实

Candidate 保存稳定 Design Intent：outcome、authority、invariant、boundary、target behavior、public handoff、
observable failure 和 material open decision。T2 还冻结本 capability 所需的最小适用 machine contract 与
code-truth requirement；可以固定 stable interface/Protocol 或 Schema/Registry/Validator identity，但不能把
这些可能项变成所有 T2 的必填项。

Physical path、具体 class/field/module layout、validator implementation、storage、test count、current binding、
implementation status 和 release state 属于 code-owned 或 generated surfaces。新 capability 尚无 code 时，
candidate 写 required code-truth result 和 handoff；已有 code 时，适用的 immutable `Code Projection` 与
Design Intent 共同构成 review subject。Mutable `Current Inspection` 只提供 current evidence，不产生
Design authority。没有 applicable machine surface 时，不创建空 Registry、Schema、Validator 或 projection。

### 6.5 表达 lifecycle、completion 与 handoff

需要时明确 `Candidate`、`UnderReview`、`Current`、`Superseded` 和 `Retired` 的语义，以及进入、
退出和停止条件。Design lifecycle admission、implementation handoff 和 Software Delivery
admission 是不同决定；不得让一个结果静默推出另一个结果。

Supersession 不会在 successor 审查期间改变 predecessor authority。Retirement 必须保留
identity traceability，并消费 DDM-owned Design reference validator 提供的 inbound-reference closure result。具体
implementation、release、deployment 和 rollback admission 留给 Software Delivery。

### 6.6 Author Self-Check

<!-- embedded-resource:t0:skill_author_self_check:start -->
调用外部 Reviewer 前，author 必须直接消费本 Skill 声明的、由 subject authority 拥有的 canonical checklist，不能手抄第二份 checklist。每次调用只形成临时结果表：

| `check_id` | `exact evidence` | `local result` | `unresolved finding` |
| --- | --- | --- | --- |

每个 required `check_id` 恰好出现一次，并绑定当前 exact candidate 的证据。历史 `prior_findings` 只作为需要在当前 candidate 上重新判断的 evidence，不能继承旧 verdict。任何 unresolved finding 都使 author 停止并留在本 Skill 内修订；只有本地 finding 全部关闭后，才冻结 exact candidate 并调用独立 Reviewer。

Author Self-Check 只防止作者把自己已经看见的缺口交给 Reviewer。它不产生 `passed`、approval、admission 或任何可替代独立审核的结果，也不创建持久化 record、Registry 或跨系统 schema。
<!-- embedded-resource:t0:skill_author_self_check:end -->

Canonical subject checklist：

<!-- embedded-resource:t0:design_contract_review_checklist:start -->
前十项是 semantic stage；第十一项只在 semantic stage 全部通过后执行。输入
`required_check_ids` 必须与下表顺序和集合完全一致。

| 顺序 | `check_id` | 必须确定的结果 | `finding_class` |
| --- | --- | --- | --- |
| 1 | `intent_and_reader_result` | `intended_result`、`User Intent`、`Reader Gain` 和 material Design choice 清楚一致；Reader Gain 说明 reader 与新增能力 | `intent_gap` |
| 2 | `layer_owner_and_parent` | layer、identity、owner、parent、same-level peer、dependency 和 structural disposition 一致；完整 peer set 不重复、不遗漏、不侵入 authority | `layer_or_owner_defect` |
| 3 | `layer_content_fit` | owned object 唯一；authority、inheritance、delegation 和 layer scaffold 正确；只包含本层相关内容；T2 只闭合 capability 所需的最小适用 machine-contract/code-truth 集合，不为形式完整发明不适用的 surface | `layer_content_misfit` |
| 4 | `peer_authority_and_inheritance` | inherited constraints、peer handoff、dependency direction 和结构变更 coverage 闭合；不复制 peer internal contract | `peer_or_inheritance_conflict` |
| 5 | `boundary_coherence` | semantic owner、author、operator、Reviewer、persistence owner、implementation binding 与 approval authority 可区分 | `boundary_ambiguity` |
| 6 | `design_and_code_truth_separation` | Design Intent、required code-truth result、immutable Code Projection 与 mutable Current Inspection 分离；T2 的 applicable binding 不遗漏，也不把所有可能 machine surface 变成必填项 | `code_truth_leakage` |
| 7 | `flow_interface_and_error_closure` | 适用的 layer Flowmap、owner-local interface/error、图、表和正文互相解析；parent 不复制 child row | `flow_or_interface_closure_gap` |
| 8 | `failure_completion_and_rollback` | invariant、public handoff、failure、completion、recovery、rollback、materiality 和 lifecycle obligation 属于正确 layer | `failure_or_completion_gap` |
| 9 | `implementability_without_redesign` | 下一层无需补一个未声明的 authority、product behavior、boundary、payload、failure、peer decision 或 applicable stable machine-contract identity；不适用的 machine surface 无需占位实现 | `implementability_gap` |
| 10 | `review_approval_and_admission` | independent review、owner decision、Design admission、implementation completion 与 release 是不同决定 | `review_or_admission_conflict` |
| 11 | `prose_and_meaning_preservation` | Semantic 全过后，同一 bytes 可被冷读并准确复述，不改变 governing meaning | `prose_or_communication_defect` |

对结构变更，第 2 项确定 identity、owner、parent 与 complete peer-set disposition；第 4 项确定
inherited law、peer handoff 和 predecessor/successor coverage。同一根因影响同一 finding_class 时不重复 finding；跨不同 finding_class 时分别生成 finding，并指向同一个待恢复结果。
只有 `block` 或 `fix` finding ID 可以进入 disposition=`finding` 的 check；`passed` 与 `not_run` check 的 `finding_ids` 必须为空，`note` 只保留在顶层 findings。
<!-- embedded-resource:t0:design_contract_review_checklist:end -->

发现 semantic owner 未决，或清晰度修改将改变 governing meaning 时，停止并返回 accountable owner。
