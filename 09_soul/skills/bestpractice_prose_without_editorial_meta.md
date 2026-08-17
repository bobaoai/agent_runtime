---
name: prose-without-editorial-meta
type: system-module
description: 句子级守门（写关于世界，不写关于稿件；禁 editorial meta / conversation attribution / workflow deictics）
---

# Prose Without Editorial Meta（写关于世界，不写关于稿件）

## 元数据

- **类型**: BestPractice
- **适用场景**: 写任何"读者读完后会带着判断走"的产物 — PM/客户面向的报告、stable AI-facing artifact（design doc / skill / rule / axiom / AGENTS.md / CLAUDE.md）、对外沟通文案
- **创建日期**: 2026-04-25
- **来源**: 从 trading_platform 项目 `09_claude/rules/35_pm_writing_contract.md` §4 蒸馏。trading_platform 在 PM-facing report 里反复发现 editorial meta 把"分析师在描述市场"退化成"编辑在描述这份草稿"，并把同样的 guardrail 扩展到所有 stable AI-facing artifact

---

## 写作内容守门原则

**写关于世界，不写关于稿件。**

读者要带走的是对世界（市场 / 产品 / 系统 / 决策对象）的判断能力，不是对稿件本身（这次写了什么、跟上一版差什么）的认识。任何描述"稿件本身"的句子都在挤掉本可以用来描述世界的字数。

---

## 1. 何时触发

写以下任何一类 artifact 时，本 skill 全程在场：

- **Reader-facing report**：给 PM / 客户 / 同事看的报告、备忘、recap、review
- **Stable AI-facing artifact**：design doc / skill / rule / axiom / AGENTS.md / CLAUDE.md / project adapter — 这些会被未来 agent 与人类**在没有当前对话上下文**的情况下读到
- **对外沟通文案**：邮件、提案、分享文档、slide deck speaker notes

不触发：
- 临时 changelog / handoff note / commit message / `designDoc/temp/` scratch — 这些就是要描述"这一轮 / 这次改动"的，editorial meta 是它们的本职

---

## 2. 反模式清单

### 2.1 Reader-facing report 里的 editorial meta

```
不写：
- 「这次 package 补充了…」
- 「相比现有报告，新增…」
- 「本次更新…」
- 「新增证据…」
- 「这版…」 / 「原文…」
- 「我们把 X 补进正文」
- 「以下分析结合了上一轮的 feedback」

写法替换：
- 直接陈述世界：「Q3 营收同比 +12%，主要来自数据中心业务」
- 直接陈述判断：「持仓应在 hedge 触发线之下」
- 直接陈述不确定性：「2025-Q4 capex 路径仍未确认；watch 的是…」
```

### 2.2 Stable AI-facing artifact 里的额外反模式

这些只在"会被未来 agent / 人类在没有当前对话上下文的情况下读到"的 artifact 里才致命。process artifact 里写无所谓。

| 反模式 | 例子 | 替换 |
|---|---|---|
| Conversation attribution | 「PM 在 ... 提出」「上一轮讨论」「我们刚才决定」「按用户最新反馈」 | 直接陈述结论；如果出处必要，在 frontmatter `provenance:` 字段 |
| Workflow time-window deictics | 「本轮」「这次 dogfood」「当前 phase」「下一步」（在 stable doc 内） | 「当系统进入 X 阶段时」/ 直接说 stage 名 / 或挪到 changelog |
| Rule / axiom invocation as endorsement | 「按 Rule 36 / 按 a16」用作背书或正当化语气，而非 routing 入口 | 引用作 reading reference（"see also: A16"），或干脆直接复述该 rule 的实质判断 |
| Self-referential meta | "this section explains..."、"the following will discuss..." | 直接进内容；section heading 已经做了这件事 |

### 2.3 检测式 boundary（自查）

写完一段后扫一遍。如果某个句子去掉以后**读者对世界的判断能力毫无下降**，那它就是 editorial meta，删掉。

更精确的检测：
- 这句话讲的是「世界长什么样」还是「这份稿件长什么样」？后者删
- 删掉后，读者还能完成 reader-end-state 列出的事情吗？能则删
- 如果保留必要，搬到 changelog / handoff note / git commit / `designDoc/temp/` 里去

---

## 3. 与其他原则的关系

- **A20 读者 Persona 优先**：先锁定读者，本 skill 把"读者要带走世界判断"具体化成 prose-level guardrail
- **FP4 输出说重点**：editorial meta 是 FP4 最常见的违反方式之一
- **`bestpractice_reader_state_and_judgment_gain.md`**：reader-state-first 是 artifact-level（产物形态契约），本 skill 是 sentence-level（句子级守门）。两者 stack 用
- **`bestpractice_doc_self_review.md`** R14/FP7：self-review 风格审阶段调本 skill 做最后扫一遍

本 skill 不判断概念引入顺序、段落三问、认知负荷、推理连续性或改写后的语义保持。这些是综合冷读与内容审职责，分别落在 `bestpractice_doc_self_review.md` 的 Stage 1 和 Stage 2。把它们塞进本 skill 会混淆“句子是否在谈稿件”和“正文是否容易理解”两类 finding。

---

## 4. 四层契约的位置

PM Writing 完整契约是四层 stack（来自 trading_platform 35_pm_writing_contract.md）：

| 层 | 内容 | canonical skill |
|---|---|---|
| Artifact 层 | reader end-state：读完能 rank/compare/decide/带走什么问题 | `bestpractice_reader_state_and_judgment_gain.md` §reader-state-first |
| Instruction 层 | reader gain：编辑 prompt / section spec 时先定义"这一层新解锁什么判断能力" | `bestpractice_reader_state_and_judgment_gain.md` §reader-gain-before-instruction-detail |
| Paragraph 层 | 概念按 reader start-state 的依赖顺序出现；解释段落可恢复什么/为什么/意味着什么；改写保护分析状态 | `bestpractice_reader_state_and_judgment_gain.md` 原则五 + `bestpractice_analytical_writing.md` 原则七至八 |
| Sentence 层 | 写关于世界、不写关于稿件 | **本 skill** |

如果 artifact 通过 1 但败在 Paragraph 层，读者知道目的地，却无法顺着概念和推理到达。
如果前三层通过但败在 Sentence 层，读者拿到的是 meta-essay 而不是分析。
如果 Artifact、Paragraph、Sentence 层通过，但 Instruction 层在迭代时漏，prompt 越改越重却不带来更锐利的 reader judgment。

---

## 5. 自查 checklist（交付前 30 秒扫一遍）

写完任何上面 §1 范围内的 artifact，交付前问自己：

- [ ] 有没有句子在描述「这次稿件本身」（package / 本次 / 新增 / 这版 / 相比上一版）— 删
- [ ] 有没有 conversation attribution（PM 在 X 提出 / 上一轮 / 我们刚才）— 删或挪到 frontmatter provenance
- [ ] 有没有 workflow time-window deictics（本轮 / 这次 dogfood / 当前 phase）出现在 stable artifact 内 — 改成绝对描述或挪到 changelog
- [ ] 有没有用 rule/axiom 引用做背书（按 R13 / 按 A16）而不是 routing 入口 — 改成实质陈述
- [ ] 删掉所有 editorial meta 后，读者完成 reader-end-state 任务的能力是否下降 — 不下降则删除是正确的

任何 [ ] 没勾，回去改。
