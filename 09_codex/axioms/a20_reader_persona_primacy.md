---
id: axiom_a20_reader_persona_primacy_2026
category: ai_agentic
created: 2026-04-25
updated: 2026-04-25
note: 在 trading_platform 项目内首发时编号为 a17；回流 Hoveath 母仓库时与已存在的 a17_five_walls / a18 / a19（创建于 2026-04-06）发生编号冲突，按时间序重编号为 a20。
---

# A20. 读者 Persona 优先（Reader Persona Primacy）

## 1. 核心公理

任何 KB / AP / report / skill 在动笔前，必须显性声明它服务哪一个（或哪几个）固定 persona，并按该 persona 的 decision 形态决定结构、压缩比、引用密度与失败模式。Persona 集合是 repo 全局固定的小集合，不在单个 doc/initiative 里临时发明。读者不显性，下游就一定误用。

## 2. 深度推演

### 2.1 没有读者，就没有"够好"的判定标准

一份 doc 信息密度高低，只有放在"谁会读、读完会做什么 decision"的语境里才能评分。读者缺位时，作者会被迫向"通用"漂移：什么都讲一点，但任何一个真实使用者都觉得不到位。Fed-watcher 写完一份 chair profile，是给 Thesis Researcher 验证 single claim 用，还是给 Regime Analyst 拼 path tree 用，决定了它该是 dossier 形态还是两轴 timeline 形态。

### 2.2 Persona 是 repo 全局资产，不是 per-doc 即兴

每一个 doc/initiative 自定义一套读者，会得到一个相互不兼容的 persona 集合：A doc 的 "PM" 包含 strategy，B doc 的 "PM" 不包含。下游 routing、handoff、verification 全部失效。Persona 定义必须在 repo 级别一次落定，所有 KB/AP/report/skill 引用同一份 source of truth。本 axiom 即承担该 source-of-truth 责任。

### 2.3 Buy-side 是隐含的全局约束

所有 persona 都是 buy-side 内部消费者：没有 sell-side analyst 这个角色（不需要服务外部客户），没有合规 / 监管沟通 persona，也没有面向 LP 的销售层。这一约束消去了大量"是否要 hedge 措辞""是否要附 disclaimer""是否要外部可发布版本"的讨论。

### 2.4 Persona 不是组织职位，而是 decision shape

不应按 title 切（"PM / Trader / Risk"），而是按"做什么类型的 decision"切。同一个真人可以同时戴多顶 persona 帽子，但每一份 artifact 只能为其中一顶服务。Strategist 的 "regime + path + coupling 推演"，PM 的 "instrument / level / size / hedge catalyst 落位"，Risk 的 "book 脆弱性 + hedge gap"，是三种 decision shape，不是同一人不同 mood。

### 2.5 Persona ≠ skill ≠ agent

Persona 是读者；skill 是流程；agent 是执行体。一个 skill 可以为一个 persona 写、也可以为多个 persona 写不同 section；一个 agent 可以同时被多个 persona 当作工具。但 axiom 的强制是：在 doc 顶层 metadata 必须看到 persona 字段。

## 3. 六个 Persona（canonical）

| Persona | 一句话 Decision | 不是什么 | Artifact 形态 |
|---|---|---|---|
| **System Builder** | 建 harness / skill / schema / pipeline | 不用 harness 做投资决策 | designDoc / .cursor/skills / src/ |
| **Regime Analyst**（≈ Strategist） | 判断当前 regime + 推演 path tree + cross-theme coupling | 不调仓 / 不挂单 | regime brief / scenario tree / coupling map |
| **Allocation Decision-Maker**（≈ PM） | regime → 具体 instrument / level / size / hedge catalyst | 不做 regime 推演 / 不做 exposure audit | allocation note / sizing memo |
| **Exposure Auditor**（≈ Risk Manager） | book 脆弱性 + hedge gap | 不做 allocation 决策 | exposure report / hedge gap memo |
| **Execution Trader** | 当日挂单 / 出入场 / stop / liquidity | 不做 allocation 决策 | order plan / execution log |
| **Thesis Researcher**（pending: 是否改名 `Thesis Theme Researcher`） | 写 / 验证 / 攻击 single thesis | 不组合 theme-level regime | thesis note / verifier / adversary |

### 3.1 消费流（canonical wiring）

```
Thesis Researcher → Regime Analyst → Allocation Decision-Maker → Execution Trader
                                  ↘ Exposure Auditor
                    (System Builder 横向支撑所有人)
```

- Thesis 是 atomic 单位（single claim），向上 feed Regime Analyst。
- Regime Analyst 是综合层，向下 feed Allocation 与 Exposure 两条腿。
- Execution Trader 接 Allocation 落地。
- Exposure Auditor 与 Allocation 是平行 check，不是上下游。
- System Builder 横向：harness / skill / pipeline 服务前五者，不直接产生 decision。

### 3.2 Buy-side 全局约束

- 没有 sell-side analyst persona，不写 sell-side 推荐报告。
- 没有 compliance / disclosure persona，不为外部发布裁剪。
- 没有 LP-facing persona，不需要"销售友好"语气。
- 所有读者都是内部决策者，可以承受高密度、术语、未润色 voice。

### 3.3 WHY 锚定 canonical snippet（下游 §0 机械 paste 用）

下游 doc（fed_method_validation / context_infra_evaluation / harness/EXPERIMENTS.md 等）的 §0 WHY 锚定段落必须从下面 canonical block 机械 paste，不重写 prose。如需修订，**只在本 §3.3 修，再 propagate 到下游**。

下游 paste 时：
- 包裹 `<!-- canonical from a17 §3.3, do not edit here, propagate via a17 -->` HTML 注释（do-not-edit-here marker）
- domain 前缀（"Fed Domain" / "Earnings Domain" 等）由下游自行加在第 2 项前
- 第 3 项 "本 doc/experiment 直接 enable 什么" 由下游自填（这是 doc-specific WHY，不是 canonical）

#### Canonical block（v1，2026-04-26）

```
- **终极目的**：trading_platform = **buy-side 内部 cognitive system**，目的是让 **PM 做更好的 portfolio decision**。不是学术 insight / sell-side note / publish research
- **直接服务对象**（per a17 axiom §3）：[Fed Domain / Earnings Domain / ...] 直接服务 2 个 persona — **Thesis Researcher**（写/验证/攻击 single thesis claim，atomic 单位）+ **Regime Analyst**（判断 regime + 推演 path tree + cross-theme coupling，综合层）
- **间接服务**（via Regime Analyst 桥接）：Allocation Decision-Maker / Exposure Auditor
- **不服务**：Execution Trader（scope-bound）/ sell-side 外部客户 / journalist / 学术读者 / 任何外部发布
- **本 doc/experiment 直接 enable 什么**：[下游自填一行]

**硬约束**：本 doc/experiment 内任何设计决策都必须 trace 回上面任一条。trace 不到 = 噪音，砍。
```

### 3.5 Pending 命名项（不在本 axiom 内决议）

1. `Thesis Researcher` 是否改名 `Thesis Theme Researcher`，以反映其常常工作在 theme 级 thesis 而非 single-asset thesis 的现状。
2. `Regime Analyst` 是否需要 qualifier（`Market Regime Analyst` / `Macro Regime Analyst`）。

两项均挂起，由后续单独 patch 决定，不在本文件内默改。

## 4. 应用判定

### 何时使用

- 设计新的 KB layer / AP module / skill / report template
- 评审一份 doc 时不知道"它该多详细 / 多压缩"
- handoff 时下游说"这份我用不了"
- 同一份 doc 出现服务多 persona 的张力（length / 术语 / 引用密度无法收敛）

### 如何实践

1. 在 doc 顶层 metadata 写 `reader_persona: <persona-name>`（或多个，按主次列）。
2. 用本表的 "Decision" 列检查：这份 doc 能否直接支持该 decision。能则保留；不能则要么补内容要么改读者。
3. 用 "不是什么" 列检查：是否在偷偷服务别的 persona。是则砍。
4. 跨 persona 的内容不要塞同一份 doc——拆成两份，分别声明 reader。
5. 引用本 axiom 时直接 link 这里，不要在 doc 里复述六 persona 表。

## 5. 相关公理

- **A14 Prompt 边界卫生**：persona 是 prompt 边界的上层定义，决定 prompt 该传什么。
- **A16 先揭示隐藏假设，再回答更好的问题**：本 axiom 是 A16 在 doc 设计层的具体化——"读者是谁"是最常被默认掉的隐藏假设。
- **X04 为真实用例设计**：persona = "真实用例" 在人维度的投影。
- **T05 认知是资产**：persona 集合本身就是 repo 级认知资产，必须固化。

## 6. 简化准则

写任何 doc 之前问一句：

1. 这份给谁看？（必须能从六 persona 里指名）
2. 他读完做什么 decision？（必须能用一句话说出）
3. 有没有第二个 persona 在偷偷被我服务？（有则拆）

## 7. Provenance

- 6-persona canonical 定义来自 chat session `66396402-a779-44e8-98d2-7b7db9118b6b` @ 2026-04-24 18:00–18:10。
- Buy-side 全局约束确认于同一 chat 18:00:34。
- 与现存设计文档的对应关系：
  - `external_learning_resource/learning_library/topics/our_position_vs_top10.md` LL-N1 / LL-N2 / LL-N3 分别为 Exposure Auditor / Execution Trader / Regime Analyst 的设计 driver。
  - `designDoc/retrospectives/critic_pipeline_20260424.md` Items 6 / 7 / 8 中提到的 backlog 与本表 persona 对齐。
  - `designDoc/progress/thesis_to_theme_restructure_initiative.md` §7 将本 axiom 列为 out-of-scope 的前置依赖。
