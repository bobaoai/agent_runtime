# Doc Self-Review（交付前自审 doc）

## 元数据

- **类型**: BestPractice
- **适用场景**: 在交付任何 Proposal 前（Plan、Design Doc、Methodology Doc、Framing Doc、Retrospective、给用户消费的 inline 回复或多 section 长 response、带 evidence verdict 的 PM-actionable reply）执行 self-review
- **创建日期**: 2026-04-25
- **来源**: 多次 doc 交付后被用户当场指出 reader-state 结构性失败的实战经验，加上 R14 Self-Review Before Handoff 的 protocol 化需求

---

## 这个文件是干什么的

R14 规定交付 Proposal 前必须 self-review。本 skill 声明 review 时要查的四份参考，并把 "查什么" 落成 "按什么顺序查、每步产出什么、什么时候判定 review 通过"。这个 skill 把 review 从 principle 落到 procedure，是 R14 的 canonical 执行路径。

它解决的具体问题：Doc self-review 经常退化成 "再读一遍找错别字"，结构性 reader-state 失败（重复段、缺 priority、缺执行序、abstract 没传达核心收益）漏掉。这种漏掉只有在用户当场指出时才被发现，每次都是事后修补。

---

## 基础公理（详见 axioms）

- **T02**：结果确定性优于过程确定性（self-review 的结果是 doc 满足验收，不是 review 流程跑完）
- **A08**：提示质量是主要杠杆（doc 是给用户的 prompt，质量决定下游决策质量）
- **A16**：先揭示隐藏假设（review 的核心是把 doc 里的隐藏 weakness 显式化）

---

## 触发条件

下列任一情况触发：

- 即将交付 Plan、Design Doc、Methodology Doc、Framing Doc、Retrospective 给用户
- 即将给用户回一段多 section 长 response（>3 段且涉及 framing / 决策 / 推荐）
- 即将给用户 surface 一个会影响 PM 信念、证据入账、verification verdict、review verdict 或下一步执行授权的 substantive reply
- 修改了 R14 涵盖的 doc 后再次交付（即使是小改也要重跑相关 stage）
- 用户问 "review 一下" / "自己看看" / "你 review 过了吗"

不触发：

- 单轮事实问答
- 单文件 trivial 改动（错别字、单行修复）
- 纯执行类回复（"已经跑完，结果是 X"）

---

## 目标、边界、验收

**目标**：交付前先完成 artifact routing，再对 Proposal 完成结构 + 内容 + 风格三层 review，输出明确的 "改了 X 处 + 保留 Y 处及理由" 报告。

**边界**：

- 只 review 即将交付的 Proposal 本身，不顺手扩 scope 改其他 doc
- 只跑 Layer 0 artifact routing + 4 份参考定义的 review 维度，不引入临时新维度
- 跳过任何 stage 必须显式声明，禁止静默跳过

**验收**（review 通过判定）：

- Layer 0 + Stage 1-3 全部跑过，每个 layer / stage 有 explicit pass/fix 记录
- 4 份参考全部对照过（或显式声明跳过及理由）
- Reader-state 5 问全部跑过且都过
- 冷读缺陷检查全部跑过；每个“是”都进入 fix list，无法判断的项显式升级人工或 independent review
- 任何重写都完成受保护语义检查，没有改变数字、因果、置信度、场景条件、权限边界或 schema 身份
- 输出 self-review 报告包含：Layer 0 artifact classification + 改动 list（每条带 §引用 + 修法）+ 保留 list（每条带理由）+ 跳过 list（每条带理由）

---

## 强制 references

下表 4 个 reference 是 doc self-review 强制必读. agent 触发本 skill 时先 read 这 4 个, 然后再进 Layer 0.

| Reference | Path | 何时必读 | 何时可跳过 | Stage 用途 |
|---|---|---|---|---|
| **COMMUNICATION** | `09_soul/core/COMMUNICATION.md` | 任何 Proposal 都必读 (universal 风格 contract) | 不可跳过 | 3 (风格审) |
| **First Principles** | `09_soul/axioms/FP_first_principles.md` | 任何 Proposal 都必读 (FP1-FP7 是 universal first principles) | 不可跳过 | 1+2 (结构 + 内容审) |
| **Reader State and Judgment Gain** | `09_soul/skills/bestpractice_reader_state_and_judgment_gain.md` | 任何 Proposal 都必读 (Stage 1 reader-state 5 问 source) | 不可跳过 | 1 (结构审) |
| **Skill Writing** | `09_soul/skills/bestpractice_skill_writing.md` | 涉及 SKILL / contract / rule / axiom design 的 Proposal | Proposal 是纯 status update / fact answer 时可跳过 | 2 (内容审, 钉不变量 / 检测式 boundary) |

---

## Layer 0：Artifact Routing 与 Review Boundary（先于三阶段）

Stage 1-3 审的是 Proposal 的结构、内容和风格。它们不判断某条 evidence 是否应该进入信念层，也不替代 domain-specific independent reviewer。任何 substantive reply 在进入 Stage 1 前，先做 Layer 0 classification。

### Layer 0 自检问

| # | 自检问 | 如果答案是 yes |
|---|---|---|
| L0-Q1 | 这份输出是否只是 prose proposal / plan / framing，而不产生证据 verdict？ | 跑本 skill 三阶段即可 |
| L0-Q2 | 这份输出是否在设计 SKILL / contract / rule / axiom？ | Stage 2 必须使用 `bestpractice_skill_writing.md` 检查不变量与检测式 boundary |
| L0-Q3 | 这份输出是否包含 evidence-bearing claim、verification finding、source-quality judgment、事实真伪 verdict？ | 本 skill 只能审 prose；必须显式说明是否已有 `evidence_record` 与 independent `evidence-reviewer` pass |
| L0-Q4 | 这份输出是否会改变 PM belief、触发 PM ack、改变 thesis claim、或授权 portfolio / research workflow 下一步？ | 必须区分 author provisional reading、independent reviewer verdict、PM-only decision；不能用 self-review 冒充授权 |
| L0-Q5 | 这份输出是否引用外部事实、quote、transcript、date、price、filing、policy statement？ | Stage 2 内容审必须检查 source surface、time semantics、quote location；必要时转入对应 domain reviewer |

### Layer 0 输出

每次 self-review 报告顶部先写一行：

`Layer 0 classification: <proposal_only | skill_contract | evidence_bearing | pm_belief_update | mixed>; required independent reviewer: <none | evidence-reviewer | project-review | theme-report-reviewer | other>.`

如果 classification 是 `evidence_bearing` 或 `pm_belief_update`，还必须写：

- `author_status`: 这只是作者 provisional reading，还是已经有 independent reviewer verdict
- `artifact_status`: 是否已有落盘 artifact（如 `evidence_record`）和对应 review log
- `handoff_status`: 交付给 PM 的是问题、建议、还是已审 verdict

### Layer 0 边界

| 禁止式 | 检测式（无声违反时长什么样） |
|---|---|
| 不允许用 doc self-review 替代 evidence-reviewer | 回复里给出 source-quality / quote truth verdict，但没有对应 `evidence_record`、没有 `ai_review_log[]` entry，或没有声明这是 author provisional reading |
| 不允许把 author reading 写成 reviewer verdict | 句子说 "验证结论是 X"，但实际只有 verifier / drafter 自己读了工具输出，独立 reviewer 没跑 |
| 不允许把 PM-only belief update 写成 agent 已完成动作 | 回复里说 thesis 应该修改 / evidence 应该 ack，但没有明确这是待 PM 决策 |
| 不允许只做风格修正后交付 evidence verdict | self-review 报告只列破折号、否定句、语气问题，没有 L0 classification 和 evidence review boundary |

Layer 0 通过后，再进入 Stage 1。Layer 0 如果判定需要 independent reviewer，而该 reviewer 还没跑，最终回复只能交付 status / blocker / next-step request，不能交付 verified verdict。

## 三阶段执行序

按顺序跑，前一 stage 出的 fix 全部应用后再进下一 stage。否则风格审改了一处 prose，结构审又把这段删了，浪费两次工作。

### Stage 1：结构审（最重要）

**输入**：草稿 doc 全文 + Reader State and Judgment Gain skill

**操作**：跑 reader-state 5 问，每问产生 0-N 个 fix item。

| # | 自检问 | fix 信号 |
|---|---|---|
| Q1 | 读完后会更清楚什么？ | Abstract 没传达核心收益 / 主线被埋在第 N 节 |
| Q2 | 会更能区分什么？ | 概念对比模糊 / 应当用 table 但用了 prose |
| Q3 | 哪种误读现在更容易被拒绝？ | 已知误读没被显式 reject / 缺 disclaimer |
| Q4 | 下一步判断或决策会被怎样 sharpen？ | Action list 缺执行序 / 缺优先级 / 缺依赖图 |
| Q5 | 删掉某段读者判断能力是否真的下降？ | dead section / 重复段 / status table 重复 actions list |

**额外结构检查**（reader-state 之外）：

- 同级 section 对称性：同 level 标题下的内容长度 / 深度是否平衡。一节 12 行嵌表格、其他节 1 行，就是失衡（典型例子：COMMUNICATION.md 第一性原理 #7 曾经 12 行打破其他 6 条 1 行的平衡）
- Cross-ref 完整性：doc 里引的 §X / 文件路径 / 表格是否真的存在

**冷读缺陷检查**：以下问题统一采用缺陷极性。回答“是”表示发现问题，必须产生 fix item；回答“否”表示该项通过；无法判断时必须显式升级人工或 independent review。

| # | 是否存在缺陷 | fix 信号 |
|---|---|---|
| C1 | 是否在读者理解对象、现象或任务之前引入抽象定义，迫使读者暂存尚未理解的术语？ | 先给操作锚点、问题或可观察差异，再命名概念；正式 schema 定义因精确性需要先出现时，立即补一句通俗角色说明 |
| C2 | 是否有解释性段落使冷读者无法回答“在说什么、为什么成立或存在、会改变什么”？ | 补齐对象或现象、理由或机制、影响或执行变化；纯字段表、引用块和枚举不强套段落三问 |
| C3 | 是否存在翻译腔、教科书口吻、机械编号或不自然的概念堆叠，遮住真正的 contract 或判断？ | 改为直接陈述；编号只在顺序或稳定引用本身有价值时保留 |
| C4 | 是否在过短篇幅内引入过多相互独立的新概念，超过目标读者的理解负荷？ | 按依赖关系拆开，引入一个概念后先说明用途或影响，再进入下一个 |
| C5 | 是否存在推理或 contract 断层，使相邻段落成为互不相干的判断、规则或定义列表？ | 补出上一段结论如何约束或启用下一段；无法建立关系时删除、移动或重组 |

**输出**：Stage 1 fix list。每条 `[§X.Y] 问题 → 修法`。

**Stage 1 必须先全部应用完，再进 Stage 2。**

### Stage 2：内容审

**输入**：Stage 1 修过的 doc + bestpractice_skill_writing (涉及 SKILL / contract / rule / axiom design 的 Proposal)

**操作**：

- 论证链完整性：每个主张是否有 evidence / 引用 / 推理链支撑
- 例子有效性：例子是否带 confound（如想说明 authority 但例子里 freshness 也变了）
- 引用准确性：cite 的 paper / 文件 / 数据是否真实存在且数字对
- 重复段：前后是否说同一件事（即使措辞不同）
- 事实 / 推断 / 判断分层：来源事实、机制推断和作者判断是否被不同语气或明确边界区分；不能把推断写成 authority
- 受保护语义：任何删改、润色或重排是否改变数字、因果方向、不确定性、场景条件、falsifier、权限边界、canonical path、schema 字段或 artifact identity
- Skill / contract 段落：用 Skill Writing Best Practice 检查（结果导向 / 不变量 / 检测式 boundary）
- AI-facing artifact 段落：直接检查 contract 字段完整、边界明确、handoff 清楚；若问题已经进入产品设计判断，另行路由到对应 product-design review，不把它伪装成第五份强制 reference

**输出**：Stage 2 fix list。

**Stage 2 必须先全部应用完，再进 Stage 3。**

### Stage 3：风格审

**输入**：Stage 1+2 修过的 doc + COMMUNICATION.md 语言风格 section

**润色执行位（用户 2026-07-29 拍板）**：Stage 3 对文件级交付物产生的成段重写，默认交
Codex CLI（`gpt-5.6-sol`·max）润色、主 agent 逐段审定后落盘；对话内 Proposal 的
风格自检仍由主 agent 就地完成（不为一条回复起 codex 往返）。

**操作**：扫这些 violations：

- 破折号 ——/—/-- （prohibited per COMMUNICATION.md 语言风格）
- 否定句式（X 不是 Y → X 是 Z）
- 华丽辞藻、营销词（"惊喜"、"卓越"、"赋能"）
- AI 味比喻（"在虚空里相遇" / "leaves your mouth"）
- 「主句——插入——主句」结构
- 「长出来 / 长出了」用法
- 多余的 "下面"、"接下来"、"总之" filler
- 客套话、废话（"这是一个非常好的问题"）
- meta-mention 例外：如果文档自身在举例展示禁用对象（line 53 「主句——插入——主句」这种结构尤其要避免），保留破折号是合理的

**双语与 PM register（2026-06-17 新增，来自 theme report 实战，针对中英混杂 doc）**：

- **双语分层**：英文只保留三类——(a) 工具 / ticker / series code / 技术指标（SOFR、/ZQ、2s10s、DXY、CPI、HY）；(b) 固定 policy / regime / doctrine 复合术语（forced pause、look-through、bear steepening、term premium、OBBBA）；(c) 命名框架专名与具名引语。短抽象词有准确中文对应的必须翻（定价 / 偏鸽 / 偏鹰 / 警戒 / 走陡 / 信心 / 证伪 / 记者会 / 风险偏好）。无声违反：中英碎片粘连成「price 加息」「dovish 判断」「conviction 折扣」，读起来既不是干净中文也不是干净英文，两边都不顺。
- **命名框架专名不翻译**：Echo Shock 这类作者命名的专有框架词，翻成中文（回声）会丢失特指。保留英文 + 首次出现 gloss 一次，之后一致沿用；英文与汉字间留空格。
- **口语词**：避免「烫 / 砸 / 爆」这类口语，用精确表述（高于预期 / 大幅回落）。
- **黑话 / 内部分层词泄漏**：「物理层 / 政策反应层 / mechanism layer」这类从 thesis 内部借来的分层黑话，读者看不懂，翻成机制白话。
- **working-log 不开篇**：数据口径、as-of、平台最新时点、实拉方法这类工作日志放末尾附录，不放正文开头；开头是判断。无声违反：报告头两段在交代数据 provenance 而非判断。
- **历史数据趋势引用克制**：跟证据链 / 推理链无关的历史走势、逐月累计、满地穿插的观测日 as-of，是验证的副产品、是噪声。正文只留推进判断的数，provenance 归附录。
- **jargon 当既成事实**：把单一来源、未证实的判断用既成事实的词写出来（「错价」「源头已反转」断言市场错了），要改成标明「谁的判断 / 会不会落空」，不替市场下结论。

**输出**：Stage 3 fix list。

### 最终输出：Self-review 报告

格式（直接交付给用户）：

```
## Self-Review 报告

Layer 0 classification: <...>

改了 N 处：
| # | 问题 | 修法 |
|---|---|---|
| 1 | [§X.Y] ... | ... |
...

跳过的 stage / 参考（必须声明）：
- 跳过了 X 因为 Y（按 R14 跳过协议）

保留未改的（评估后保留）：
- §X.Y ... 因为 ...
```

报告里禁止说 "感觉 OK" / "整体没问题" / "review 通过" 这种 vague 表述。

---

## 不允许的捷径（禁止式 + 检测式）

每条 boundary 都配一句 "无声违反时长什么样"。这是 agent 自己回头能检查的可观察特征。

| 禁止式 | 检测式（无声违反时长什么样） |
|---|---|
| 不允许跳过 Layer 0 artifact routing | 输出含 evidence verdict / PM belief implication，但 review 报告没有说明这是 prose-only、evidence-bearing、还是 PM belief update |
| 不允许跳过 reader-state 5 问而声称 review 通过 | 交付的 doc 有结构性 reader-state 失败（重复段 / 缺 priority / 缺执行序 / abstract 没传达核心收益），但 review 报告里没提到这些维度 |
| 不允许把 Stage 3 风格审当成 self-review 全部 | review 报告通篇都是 em dash / negation 这种局部 fix，没有 reader-state 维度的 fix 或 explicit pass |
| 不允许跳过 4 份参考中任一条而不显式声明 | review 报告只引用 3 份参考的 finding，第 4 份既没出现 finding 也没出现 "skipped because Y" |
| 不允许在 review 报告里用 "感觉 OK" / "整体没问题" | 报告没具体列出 Stage 1/2/3 各自的 fix list 和 explicit pass，只有结论性陈述 |
| 不允许 review 后再加新内容不重 review | doc final 版本比 review 报告基于的版本字数更多，新加的段没被任何 stage 检查覆盖 |
| 不允许把 "已应用 fix" 和 "保留未改" 混在一个 list | 用户读完不知道哪些是改过的 / 哪些是评估后保留 / 哪些是被忽略的 |

---

## 跳过协议

R14 允许在具体 Proposal 上下文不涉及某条参考时跳过那一条。本 skill 的跳过协议：

- **可以跳过整个 stage**：如 Stage 2 内容审在纯 framing doc 上没有可论证的 claim，跳过 Stage 2 (内容审)，但必须在最终报告写 "跳过 Stage 2 因为本 doc 不含可证伪 claim"
- **可以跳过单条参考**：如 doc 不涉及 skill / contract 设计，跳过 Skill Writing Best Practice 检查
- **不可以跳过 Layer 0**：artifact routing 对所有 substantive Proposal 适用。即使最后判定为 `proposal_only`，也要写明
- **不可以跳过 Stage 1**：reader-state 5 问对所有 Proposal 适用，没有跳过场景
- **不可以跳过 Stage 3**：风格审对所有 Proposal 适用
- **不可以静默跳过**：任何跳过必须在最终报告显式声明

---

## 常见陷阱

| 陷阱 | 表现 | 应对 |
|---|---|---|
| 把 self-review 当 independent review | author 自己读完工具输出，跑一遍 doc self-review，就把 finding 当 reviewer verdict 给 PM | Layer 0 先分类。evidence-bearing 输出必须显式声明是否需要 `evidence-reviewer`，未跑时只能说 provisional |
| 把手工挑刺当 self-review 全部 | 通篇都是 em dash / negation / 重复词 这种局部 fix，没有 reader-state 维度的检查 | 强制先跑 Stage 1，Stage 1 全部应用后再进 Stage 3 |
| 加内容时破坏父结构对称性 | 在 numbered list 第 N 条下塞 12 行嵌套表格，破坏其他 N-1 条 1 行的平衡 | Stage 1 "同级 section 对称性" 检查即可 catch |
| 跳过参考不声明 | 只跑 reader-state 5 问就交付，没碰 skill writing / AI product design | 报告强制 4 份参考逐条说明 used / skipped + 理由 |
| Review 后加内容不重 review | 用户问完 follow-up，加了新段，没重新跑 Stage 1-3 | 任何 doc 改动后默认重跑 affected stage，明显不影响某 stage 才跳过 |
| 用 "感觉 OK" 替代 explicit fix list | 报告只说 "我读了一遍没问题" | 强制输出 fix list（即使是 0 条也要明示 "Stage X 跑完 0 fix"） |
| 把 fix list 和 retain list 混 | 用户读完不知道哪些改了哪些没改 | 报告分两个独立 list：改了 N 处 / 保留 M 处及理由 |
| Stage 顺序错乱 | Stage 3 改 prose，Stage 1 把这段删了，重复劳动 | 严格按 1→2→3 顺序，前 stage fix 全部应用再进下一 stage |

---

## 何时回读本文件

- 即将交付 Proposal 给用户之前
- 用户问 "review 用了什么 skill" 或 "你自己看看"
- 发现某次 review 漏掉了用户当场指出的问题，回头看是哪个 stage 没跑
- 在写新 doc 时想知道 doc 完成后该怎么 self-review
- 修改 R14 / Self-Review Protocol 时回看本 skill 是否需要同步

---

## 与项目级 doc-review skills 的关系

本 skill (`bestpractice_doc_self_review`) 是 **universal 自审 skill**, 跨项目跨 doc 类型适用. 所有 Proposal 交付前必跑.

trading_platform 项目下另有 4 个 **domain-specific review skills**, 各自审一种特定 artifact 类别. doc_self_review 跟这 4 个 skill 关系是 **upstream universal vs downstream domain-specific**, 不是替代关系:

| Skill (path) | Reviews what artifact | Trigger | 跟 doc_self_review 的边界 |
|---|---|---|---|
| `.claude/skills/project-review/SKILL.md` | engineering commit (charter-alignment phases / schema / contract / skill cluster / validator / harness extensions) | peer Claude/Cursor session 落 commit + user 说 "独立 review 下" | doc_self_review 跑在 **author 自己** 交付前; project-review 跑在 **同事 commit 后** independent review. project-review §3.5 commit scope itemization 是 doc_self_review Stage 1 reader-state Q4 (decision sharpened) 在 commit-layer 的具体化 |
| `.claude/skills/theme-report-reviewer/SKILL.md` | theme report draft (`data/research/theme_update_drafts/<theme_id>.ds.md`) | DS-written draft exist + owner decision exist | doc_self_review 跑在任何 **prose proposal** 交付前; theme-report-reviewer 跑在 **theme report draft** 这一类 prose, 加 adversarial review + 可选 Perplexity external verification + structured verdict. theme-report-reviewer 的"compliance/style gating"对应 doc_self_review Stage 3 风格审, 但加 theme-package + owner_decision contract 检查 |
| `.claude/skills/evidence-reviewer/SKILL.md` | evidence_record artifact (`data/research/evidence_ledger/<weekiso>/<id>.json`) | authoring persona (verifier / adversary / pm / scanner / sweeper) 已写 evidence_record | doc_self_review 跑在 **author surface 给 user 之前** prose-layer self-audit; evidence-reviewer 跑在 **author 写完 JSON artifact 之后** structured-data-layer independent audit. evidence-reviewer 是 charter §II + B.0.7 信念层授权 break self-attestation loop 的 structural enforcement, 不是 prose self-review |
| `.claude/skills/theme-report-debater/SKILL.md` | theme report draft (same as theme-report-reviewer 但 BEFORE compliance gating) | theme report writer produced draft + 在 theme-report-reviewer 之前 | doc_self_review 是 **author self-audit** 单方; theme-report-debater 是 **adversarial content-logic critic** independent agent. debater 攻 coupling consistency / reasoning chain integrity / mechanism proxy closure / cross-thesis weaving, 是 doc_self_review Stage 2 内容审在 theme-report 域的 adversarial 强化版 |

### 触发 sequence

不同 artifact 触发不同 skill chain:

- **Prose proposal (plan / design doc / framing / inline reply)**: 只跑 `bestpractice_doc_self_review` (本 skill) 三阶段, 不触发 4 个 project-level
- **Engineering commit**: author 跑 `bestpractice_doc_self_review` 在 commit message + 改动 doc; 同事跑 `project-review` independent. 两 skill 在不同时点 / 不同 persona run, 不冲突
- **Theme report draft**: author 跑 `bestpractice_doc_self_review` on draft prose; 然后 `theme-report-debater` adversarial pass; 然后 `theme-report-reviewer` compliance/style gating. doc_self_review 在 chain 最前
- **Evidence_record JSON artifact**: author (verifier / adversary 等) 跑 `bestpractice_doc_self_review` on 起草过程的 reasoning prose (e.g. evidence_summary 字段); 然后 `evidence-reviewer` independent audit JSON artifact. 两 skill 在不同 layer (prose vs structured data)

### 共同 invariant (跨 5 skills shared)

5 个 review skill 共享 4 条 universal invariant:

1. **Independent reviewer ≠ author**: review skill 由非 author persona run (project-review / theme-report-reviewer / evidence-reviewer / theme-report-debater 由 independent agent run; doc_self_review 由 author run on author's own work, 但 author 必须 step out of authoring mindset 走 reviewer mindset, 这是 hardest case)
2. **审完输出 explicit verdict + fix list**: 不允许 "感觉 OK" / "整体没问题" 类 vague conclusion
3. **跳过任何 stage / sub-check 必须显式 narrate skip + reason**: 不允许 silent skip
4. **检测式 boundary 配 禁止式**: 每条 invariant 配 "无声违反时长什么样" 检测式 (per `bestpractice_skill_writing.md` 原则五)

---

**最后更新**: 2026-04-28
