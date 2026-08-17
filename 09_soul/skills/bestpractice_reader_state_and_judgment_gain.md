---
name: reader-state-and-judgment-gain
type: system-module
description: 先定义读者读完后获得什么判断能力，再决定 section / prompt / contract / detail
---

# 读者状态与判断增益优先

## 元数据

- **类型**: BestPractice
- **适用场景**: 设计报告、prompt、instruction、contract、section 结构或 AI-facing 文档时
- **创建日期**: 2026-04-06
- **来源**: 多轮报告、instruction refinement 与 downstream handoff 经验的抽象

---

## 这个文件是干什么的

很多 artifact 越写越长，不是因为信息不够，而是因为没有先定义读者读完之后应该获得什么。这个 bestpractice 用来约束一个更基础的顺序：

先定义 `reader state` 和 `judgment gain`，再决定 section、prompt、标签、contract、输入字段和强调顺序。

它适用于报告、说明文档、writer prompt、技能说明、流程 contract，甚至评审意见。只要某一层是写给另一个人或另一个 agent 看的，这条原则都成立。

---

## 基础公理（详见 axioms）

- **T02**：结果确定性优于过程确定性
- **A08**：提示质量是主要杠杆
- **A16**：先揭示隐藏假设，再回答更好的问题

---

## 核心原则

### 原则一：先定义读者读完后能做什么

不要先问：

- 要分几节
- 要不要加字段
- 要不要再补一些背景
- 要不要把语气写得更强

先问：

- 读者读完后应该更清楚什么
- 读者应该更能区分什么
- 哪种误读现在应该更容易被排除
- 读者接下来应该更容易做什么判断或决定什么下一步

如果这些问题没有答案，继续加内容通常只会增加体积，不会增加价值。

### 原则二：更多内容不等于更多判断能力

artifact 常见的假进步有四种：

- 多了一些背景
- 多了一些标签
- 多了一些强调
- 多了一些流程说明

这些都可能有用，但都不自动等于 judgment gain。判断一个新增内容是否值得保留，要看它是否真的提升了读者识别边界、理解阶段、判断优先级或辨别不确定性的能力。

### 原则三：先定义 end-state，再设计结构

当你已经知道读者读完后应该获得什么，再去决定：

- section 顺序
- prompt wording
- contract 字段
- input admission 逻辑
- handoff 检查

这样设计出来的结构更稳定，因为它们服务的是一个明确结果，而不是为了“看起来完整”。

### 原则四：instruction 也要有 reader gain

这条原则不仅适用于最终报告，也适用于给下游 agent 或 writer 的 instruction。

当你在修改 prompt 或 contract 时，先问：

- 这层新增文字，是否让下游更容易判断什么
- 它是否让下游更容易分清边界
- 它是否让下游更不容易 overclaim
- 它是否让下一步更容易被正确触发

如果答案不明确，这段 instruction 很可能只是 workflow bulk。

### 原则五：reader start-state 决定概念出场顺序

定义 reader end-state 之后，还要检查读者从起点走到终点的认知路径。一个新概念只能依赖读者已经知道，或正文已经建立的对象、现象、任务和边界。

默认顺序是：

1. 先给读者可识别的对象、现象或任务
2. 说明它与已有认知的关键差异
3. 说明这项差异会改变什么判断、行动或 handoff
4. 再给需要长期复用的正式名称

这不是禁止定义先行。Schema、协议或法律式术语可能需要先定义以保证精确，但首次出现时必须同时给出通俗角色或操作影响，不能让读者先记住一个到后文才有用途的名字。

解释性段落完成后，冷读者应能恢复三个信息：正在说什么、为什么重要或成立、它意味着什么。纯字段表、枚举和代码块不强制套用段落结构，但其用途与下游影响必须由邻近文字建立。

---

## 适用方法

### 报告或 memo

先写一行：

`读完后，读者应该能够……`

然后再设计标题、顺序和重点。

### Prompt 或 contract

先写一行：

`看完这层后，下游应该更容易判断……`

然后再决定保留哪些字段和约束。

### AI-facing 文档

如果一份文档主要是给 agent 消费，而不是给人快速扫读，先确保它能回答这些问题：

- 这个层是干什么的
- 什么时候进入它
- 应先读什么
- 应产出什么
- 和相邻层如何交接

只有这些 contract 已经清楚时，才去考虑压缩表达。

---

## 常见陷阱

| 陷阱 | 表现 | 应对 |
|------|------|------|
| 先想结构，再想效果 | 一开始就在争论要几节、哪些标题更顺 | 先写出目标 reader state |
| 用细节代替判断增益 | 内容变多了，但读者仍说不清主线 | 检查每个新增块是否提升判断能力 |
| 用流程字段制造完成感 | contract 越来越长，但 handoff 质量没变好 | 只保留能改变下游结果的字段 |
| 人类简洁偏好压垮 agent contract | 文档看起来更短了，但边界和 handoff 消失了 | 对 AI-facing 文档先保证 contract 完整，再谈压缩 |
| 把“更强语气”误当成“更清楚判断” | 文案更重，但不确定性边界更模糊 | 明确 what is known / unknown / next question |

---

## 自检问题

在交付前，至少检查这七件事：

1. 读者读完后会更清楚什么。
2. 读者会更能区分什么。
3. 哪种误读现在更容易被拒绝。
4. 下一步判断或决策会被怎样 sharpen。
5. 如果删掉某一段，读者的判断能力是否真的下降。
6. 新概念是否只依赖读者已经拥有或前文已经建立的认知。
7. 解释性段落是否让冷读者恢复“什么、为什么、意味着什么”。

---

## Multi-Agent Handoff：每个交接节点都要 Reader-Gain 化

> **来源**：trading_platform critic pipeline 复盘（Writer → Debater → Writer rebuttal → Reviewer → Stage 3 Writer）发现：管线产出可用，但**最大摩擦是「每个 SKILL 告诉 agent 该做什么，而不是告诉它下一个读者读完后能新做什么」**。一旦每条 handoff artifact 显式声明 reader-gain，format 类争议消失、silent drift 变可检测、prompt 增量从主观判断变成"能否补上已知 reader-gain gap"。

在多 agent / 多 stage pipeline 里，前面四层（artifact / instruction / paragraph / sentence）都不够，还要加第五层：**每个 handoff 都是一次 reader-state 转移，每一步都要显式 declare 下一个读者新解锁什么判断能力**。

### 把 handoff 当 contract，不当传送带

每个 handoff artifact（前一个 agent 的输出 = 下一个 agent 的输入）必须能直接回答：

- 读这份产物的下一个 agent 是谁
- 读完后它新能做什么（具体到一个动词 + 一个区分）
- 它读完后**不应该**还需要去重读上游素材才能完成自己的任务（如果还要回头读，本层 reader-gain 就没真正交付）

工程化形态：每条 handoff artifact 在自身顶部 frontmatter 或第一段写 `required_reader_gain: <一句话>`。模糊版（"提供 context 给下游"）一律不算。

### Vehicle-shaped vs Reader-Gain-shaped

handoff 失败的典型不是 format 错，而是**正确格式 + 正确 section + 正确字段，但下游读者读完仍不能直接做 next-stage 任务**。

| Vehicle-shaped（够形似） | Reader-Gain-shaped（够能用） |
|---|---|
| "produce a report with sections A/B/C" | "after reading, downstream X can rank items by severity without re-reading source" |
| 文件落对路径、JSON schema 通过校验 | 下游 agent 不再需要回头 grep 上游原文 |
| 引用了上游所有 evidence | 下游能用 citation 直接 verify，不需要重新组装证据 |

### 多 Agent Pipeline 里的常见 handoff 反模式

| 反模式 | 症状 | 修法 |
|---|---|---|
| 每条 attack / patch 只有 severity 没有 confidence | reviewer 自己 mentally calibrate 哪些 attack 真该改、哪些只是吐槽 | 把 confidence 跟 severity orthogonal 分开，让下游不用猜 |
| Attack list 平铺，没有 root_cause_cluster_id | 下游看到 N 条独立 attack，要手工聚类才能优先级排序 | 加 cluster id，让共享根因的 attack 在产出时就关联 |
| Patch 没有 `reader_gain_after_patch` 字段 | rebuttal / arbitrate 的 agent 不知道 patch 落地后下游 PM 新得到什么 | 每条 patch 必须 inline 描述「打完这个 patch，PM 新能怎么判断」 |
| Location pointer 是从上游 transcribe 的（没 grep verify） | 下游照着 location 找不到对应内容 | 加 `location_grep_verified: true` flag，强制现场 verify |
| Critic 输出过度分散在多个文件 | 下游不知道哪个是 canonical operational input，哪些是 audit trail | 在 frontmatter 标 `role: operational_input` / `role: audit_trail`；加 `<pipeline>_complete` marker pointing to canonical input |

### Pipeline-level 自查问句

设计或评审一个 multi-agent pipeline 时，对每条 handoff 都问一遍：

- [ ] 这份 artifact 的 `required_reader_gain` 是否一句话能讲清
- [ ] 下游 agent 读完是否能直接做下一步，不用回头读上游素材
- [ ] 字段设计是 vehicle-shaped 还是 reader-gain-shaped
- [ ] 有没有 confidence / severity / location-verified / cluster-id / reader_gain_after_patch 这类**下游做选择必须的**字段缺失
- [ ] 有没有过度分散到多文件 — 如果 4 份文件的 90% 内容下游都不直接消费，merge 它们或显式标 audit trail

任何 [ ] 没勾，handoff contract 还没设计完。

---

## 何时优先回读本文件

- 你想重写一个 report、memo、prompt 或 contract
- 你发现 artifact 越写越长但仍不够清楚
- 你怀疑自己在堆 section，而不是提升 judgment
- 你在争论应该加什么内容，却说不清读者到底会因此得到什么
- 你在设计或调整一个 multi-agent / 多 stage pipeline，发现 handoff 一直在反复磨合却定不下来

---

**最后更新**: 2026-08-08
