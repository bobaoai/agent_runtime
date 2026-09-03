---
name: the-review-authoring
description: 依据一条精确、已审查的 SystemChangePlan step，为一个明确的 Reviewer 编写或修订完整 prompt source。只编写 task-specific sections 1 至 3，并与机械注入的 Review Contract 通用规则和目标 Design checklist 组成候选；不执行审核、注册 Module 或发布 Runtime release。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: authoring
  primary_agent_entry_subject: system_change_plan_step
  first_authority_ref: designDoc/the_review_contract.md
---

# Reviewer Prompt 编写

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

本 Skill 接收一条 exact reviewed `SystemChangePlan` Reviewer-source step，为一个由目标 Design
authority 定义判断标准的 Reviewer 编写或修订 prompt source。

完整 Reviewer prompt 使用五个固定章节：

1. `0. review_contract_universal`；
2. `1. Review Task`；
3. `2. Inputs, Decision, and Output`；
4. `3. Boundaries and Failure Routing`；
5. `4. Subject Review Checklist`。

本 Skill 只编写第 1 至 3 节。第 0 节由 Review Contract 拥有，第 4 节由目标 Design authority
拥有；两者由 code 根据已登记的 canonical source ref 和 hash 机械注入。成功结果是一份完整、冻结、
返回 Skill Management 的 Reviewer prompt candidate。本 Skill 不执行 `reviewer_reviewer`，不审核
自己的候选，不生成 Skill package closure，不注册 Module，也不选择 provider、model 或 Runtime
profile。

Exact prompt candidate 必须使用已注册的 `reviewer_reviewer` Runtime Module 完成独立审核。Module
route 未注册、未准入或不可执行时，停止交付并返回 Runtime registration 或 execution 的真实
owner；Prompt author 不自审，Primary Agent 不直接代审。

## 2. Reader Gain

冷启动 Primary Agent 只读本 Skill、exact reviewed Plan step、Review Contract 和目标 Design
authority 后，可以：

1. 判断请求是否属于 Reviewer prompt source authoring；
2. 写清 Reviewer 要审什么、使用哪些输入、作出什么判断、返回什么结果；
3. 把 target-specific instruction 与 universal instruction、Design checklist 和 invocation data 分开；
4. 形成无需聊天历史或 ambient repository search 也能执行的完整 Reviewer prompt candidate；
5. 在 owner、subject、checklist、schema 或 failure route 不完整时停止，并返回真实 owner。

## 3. Entry and Exit

### 3.1 Entry

只有以下条件全部成立时才进入：

1. 存在一条 exact reviewed `SystemChangePlan` step，且 authoring method 指向
   `the-review-authoring`；
2. step 指定唯一 Reviewer identity、目标 Design authority、被审 subject kind 和 intended result；
3. Review Contract 的 universal source 与目标 Design authority 的 checklist source 均可解析；
4. Reviewer 的 input、output、verdict meaning 和 failure owner 已由对应 authority 定义；
5. 新建任务已比较现有 Reviewer source peer set；更新任务已取得当前 accepted prompt source。

Reviewer 名字、附近 prompt、旧 review output、Runtime Module 或 provider session 不能替代上述
entry 条件。

### 3.2 Exit

- Plan step、Reviewer identity、target Design authority 或 subject kind 不一致时，停止并返回 System
  Change Governance。
- Checklist、input/output meaning、verdict meaning 或 failure owner 未由目标 Design authority
  定义时，停止并返回该 authority。
- Universal source、checklist source、current prompt 或 exact bytes 无法重现时，返回
  `blocked_reproducibility`，不创建候选。
- 请求实际改变 Review Contract、目标 Design、Skill lifecycle、Runtime registration 或 release 时，
  退出到对应 owner，不把该变更写进 Reviewer prompt。
- Authoring 完成后把 exact candidate 与 deterministic composition result 返回 Skill Management；
  containing Skill candidate 先完成 Skill review，再把 exact prompt 交给独立 `reviewer_reviewer`。

## 4. Execution Contract

### 4.1 Inputs and Authority

本 Skill 只使用以下冻结输入：

1. exact reviewed `SystemChangePlan` 与目标 step；
2. `designDoc/the_review_contract.md`；
3. 目标 Design authority 及其 exact Reviewer checklist source；
4. Reviewer identity、subject kind、review purpose 和 intended result；
5. allowed core context、optional supporting context 和禁止读取的 context；
6. input schema、output schema、semantic validator 和 verdict meaning；
7. Review Contract universal source 的 exact ref；
8. 更新任务的 current accepted prompt source、predecessor 和必须保留的 meaning。

Plan 决定本次 scope。目标 Design authority 决定 Reviewer 要判断的 subject meaning、checklist、
output 和 verdict。Review Contract 决定共同审核纪律与固定 layout。Skill Management 决定 prompt
source 作为 Skill artifact 的完整性。Runtime 只在下游执行已注册 Module。

Prior finding 只作为 evidence。Primary Agent 必须先核对 quoted evidence、owner、scope 和 intended
result，再决定它是否属于本次 candidate；不能把外审建议直接改写成新 authority。

### 4.2 Output and Completion

成功时返回：

1. Reviewer identity、目标 Design authority、subject kind 和 intended result；
2. 完整的第 1 至 3 节 source；
3. 由 code 机械加入第 0 节和第 4 节后的完整五章节 prompt candidate；
4. universal source、Design checklist source、input/output schema 和 semantic validator 的 exact refs；
5. changed surfaces、preserved meaning、明确排除项和 predecessor；
6. deterministic composition result，以及后续可交给 `reviewer_reviewer` 的 exact subject。

Candidate 只有在以下结果全部成立时才完成：

- 冷启动 Reviewer 能判断 exact subject、允许证据、判断顺序、输出对象、verdict 和 failure route；
- 第 1 至 3 节没有复制或改写第 0 节和第 4 节；
- invocation data 没有进入 fixed prompt source；
- deterministic code 可以验证五个章节各出现一次、顺序固定、两个机械注入章节逐字同源；
- prompt 没有新增目标 Design 未要求的 object、field、workflow、state、policy 或 mechanism；
- Reviewer author、独立 Reviewer、subject owner、Runtime execution identity 和 admission owner 保持分离。

本 Skill 不计算或手改 hash，不写 manifest/schema/fixture，不生成 host projection，不返回 review verdict，
不注册、执行、发布或部署 Reviewer Module。

## 5. Boundaries

| Boundary | 本 Skill 的职责 | 无声违反时的可观察结果 |
| --- | --- | --- |
| System Change 与 authoring | 只执行一个已审 Plan step | Prompt 扩大到 Plan 未列出的 Reviewer、Design、Skill、Code 或 Runtime surface |
| Review Contract 与 target Design | 保留 universal 规则和 target checklist 的独立权责 | 第 1 至 3 节重复、概括或改写第 0 节或第 4 节 |
| Authoring 与 review | 形成 candidate 后返回 Skill Management；Skill review 通过后再交给独立 `reviewer_reviewer` | Authoring invocation 自己返回 `passed`，或跳过 containing Skill review 直接声明 prompt 完成 |
| Prompt source 与 invocation data | 固定 instruction 只描述稳定任务 | Candidate 包含本次 subject bytes、临时路径、prior output、credential 或 execution record |
| Skill 与 Runtime | 交付完整 prompt source 和 handoff | Candidate 声称 Module 已 registered、provider 已绑定或 release 已 admitted |
| Deterministic 与 semantic | Code 检查 layout、source、hash 和 schema；Reviewer 判断意义闭包 | 模型被要求核对 byte equality，或 code 被要求推断 checklist 是否充分 |
| Subject 与 context | Core context 只帮助判断 exact subject | Reviewer 获准把辅助材料变成新的 review subject 或修改 peer contract |

## 6. Method

### 6.1 冻结 Reviewer 边界

先写出 Reviewer identity、target Design authority、subject kind、intended result、allowed context、
output、verdict meaning 和 failure owner。任何一项缺失时停止 authoring，不用通用措辞掩盖空缺。

### 6.2 编写第 1 节 Review Task

第 1 节说明 Reviewer 的目的、Reader Gain、exact frozen subject 和 intended result。它必须让执行者
知道自己的判断会帮助哪个 owner 作出什么决定，同时不把辅助 context 或未来 implementation 当成
被审对象。

### 6.3 编写第 2 节 Inputs, Decision, and Output

第 2 节逐项列出 required core input、可按需读取的 supporting context、禁止读取的 context、判断顺序、
output schema、semantic validator 和 verdict meaning。Required input 必须足以完成判断；supporting
context 只能解释 subject，不能扩大 subject。

### 6.4 编写第 3 节 Boundaries and Failure Routing

第 3 节明确 Reviewer 不得编辑、批准、注册、执行或发布 subject，不得把 peer、parent、child 或
implementation 的问题写成当前 subject finding。证据不足时返回 `blocked` 和缺失输入；可由当前
candidate owner 修复的缺陷返回 `non_pass`；全部要求满足时才返回 `passed`。

### 6.5 机械组装与交接

由 code 把 Review Contract 的第 0 节和目标 Design authority 的第 4 节注入候选，并检查章节 identity、
唯一性、顺序、source bytes、hash、schema 和 projection closure。Primary Agent 只消费检查结果，不手改
机械章节或生成 hash。

冻结后的 exact prompt candidate 返回 Skill Management。Containing Skill candidate 先完成
`skill_candidate_reviewer`；通过后，exact prompt 再进入独立 `reviewer_reviewer`。Finding 只作为
evidence 返回 author；任何修订形成新的 exact candidate，并重新经过同一组 deterministic checks。

### 6.6 Author Self-Check

<!-- embedded-resource:t0:skill_author_self_check:start -->
调用外部 Reviewer 前，author 必须直接消费本 Skill 声明的、由 subject authority 拥有的 canonical checklist，不能手抄第二份 checklist。每次调用只形成临时结果表：

| `check_id` | `exact evidence` | `local result` | `unresolved finding` |
| --- | --- | --- | --- |

每个 required `check_id` 恰好出现一次，并绑定当前 exact candidate 的证据。历史 `prior_findings` 只作为需要在当前 candidate 上重新判断的 evidence，不能继承旧 verdict。任何 unresolved finding 都使 author 停止并留在本 Skill 内修订；只有本地 finding 全部关闭后，才冻结 exact candidate 并调用独立 Reviewer。

Author Self-Check 只防止作者把自己已经看见的缺口交给 Reviewer。它不产生 `passed`、approval、admission 或任何可替代独立审核的结果，也不创建持久化 record、Registry 或跨系统 schema。
<!-- embedded-resource:t0:skill_author_self_check:end -->

Canonical subject checklist：

<!-- embedded-resource:t0:reviewer_prompt_review_checklist:start -->
1. 完整说明 purpose、读者所需判断、subject、allowed context、output、verdict、boundary 和 failure routing；
2. 准确承载目标 Design authority，没有重复或改写 sections `0` 和 `4`；
3. 不要求模型替代 schema、hash、projection、registration 或其他确定性检查；
4. 不新增 governing Design 没有要求的 object、field、workflow、state、policy 或 mechanism；
5. 让冷启动 Reviewer 只读完整 prompt 和 invocation input 就能返回注册结果，无需从文件名、
   历史对话或 ambient repository 补一个未声明决定。
6. 前五项全部通过后，同一份 exact prompt candidate 的 prose and communication 不会改变事实、
   authority、boundary、failure、verdict 或 governing meaning。
<!-- embedded-resource:t0:reviewer_prompt_review_checklist:end -->
