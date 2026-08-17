# Prompt Boundary（Task-Plane vs Control-Plane）

## 元数据

- **类型**: BestPractice
- **适用场景**: 写下游 prompt（subagent 任务、worker LLM 调用、写手 brief、reviewer instruction）；evaluating "这条 prompt 是否还能瘦身"
- **创建日期**: 2026-04-25
- **来源**: 从 trading_platform `09_claude/rules/31_prompt_boundary_task_vs_control_plane.md` 蒸馏；axiom 锚点是 [`A14 Prompt 边界卫生`](../axioms/a14_prompt_boundary_hygiene.md)。本 skill 是 A14 的 canonical 操作路径

---

## 核心原则

**下游 prompt 只放真正能改变 worker 输出质量的信息（task-plane）；其余（control-plane）一律留在 caller 上游。**

- **Task-plane**：worker 完成任务必须知道的事 — writing goal / 事实边界 / 允许的证据 / 必须给出的判断 / 输出格式 / 禁止的 prose 模式
- **Control-plane**：caller 内部的事 — 编排细节 / 包装保证 / persona 来源标签 / 内部 routing rationale / 组装备注 / 「这次 package 怎么准备的」

把 control-plane 塞进 prompt 不仅浪费 token，还污染 worker 的注意力 — worker 会把"我们怎么准备 worker 的"误读为"task constraint"，做出不必要的 hedging / over-cautious 输出。

---

## 1. 何时触发

- 写新 subagent prompt / worker LLM brief 时
- 评审现有 prompt 是否能瘦身
- 看到 worker 输出在 hedge 一些 caller 已经保证的事（"我会确保不混 account…"，但 caller 已经只传了 1 个 account）— 这是 prompt 边界违规的副产品信号
- 调试 worker 输出"格调不对" / "在描述自己怎么工作" — 常常是 control-plane 漏进了 prompt

---

## 2. 划分 task-plane / control-plane

### Task-plane 的 6 类内容（保留）

| 类 | 例子 |
|---|---|
| Writing goal | 「写一份让 PM 看完能下 next-week 决策的 single-stock note」 |
| Factual boundary | 「只用 package 内事实，不引入外部数据」 |
| Allowed evidence | 「引用 thesis A / thesis B / live anchor，不引用其他」 |
| Required judgments | 「core judgment / biggest mistake / largest open risk / next-week watchpoint 四块必须 inline」 |
| Output format | 「Markdown，二级标题 4 个，每段不超过 3 句」 |
| Forbidden prose modes | 「不写 editorial meta，不用『本次 package』『相比上一版』」 |

### Control-plane 的常见反模式（删除）

| 反模式 | 例子 | 为什么删 |
|---|---|---|
| Persona-source label 当 prompt | 「你是 Hoveath-compatible 的写手」 | worker 不需要知道它"属于哪条 persona 链"；只需要知道写什么、怎么写 |
| 已被 caller / input 保证的 guardrail | 「不要把多个账户的持仓混在一起」（caller 已只传 1 个账户） | 重复 + 把"组装契约"误传给 worker |
| 编排细节 | 「这是 critic pipeline 的 stage 2，上一步是 Debater」 | worker 不需要知道编排，只需要知道这一轮要做什么 |
| 内部 routing rationale | 「我们之所以选你而不是另一个 skill 是因为…」 | worker 完全不需要知道 |
| 组装备注 | 「package 由 build_theme_writer_package.py 生成」 | worker 不消费这个事实 |
| 组织内部术语裸传 | 「按我们 KB/AP 边界来…」（worker 不在系统内部） | 翻译成 task-plane 语言：「研究素材在 input 里，输出形态是 PM 报告」 |

---

## 3. 自查问句（最小化 prompt 的杠杆）

写完 prompt 后扫每一句，问：

> **「这句话改变 worker 的输出质量，还是只描述我们怎么准备 worker？」**

- 改变输出质量 → 留 task-plane
- 只描述准备过程 → 删

进阶检测：
- 这条 guardrail 是否已经被 caller 或 input artifact 在结构上保证了？是 → 删（structural 约束 > prompt 约束，A19）
- 删掉这句话，worker 的输出会变差吗？不会 → 删
- worker 真的需要这个上下文才能做判断，还是我在"以防万一"加 padding？后者 → 删

---

## 4. 例子

### Bad（control-plane 泄漏）

```
You are a Hoveath-compatible writer. This is stage 2 of the critic pipeline.
The package was assembled by build_theme_writer_package.py. Make sure not to
merge multiple accounts. Be careful about KB/AP boundary. Use restrained
Chinese. ...
```

问题：
- "Hoveath-compatible writer" — persona-source label，worker 不消费
- "stage 2 of the critic pipeline" — 编排细节
- "package assembled by build_theme_writer_package.py" — 组装备注
- "not to merge multiple accounts" — caller 只传了 1 个账户，已结构性保证
- "KB/AP boundary" — 内部术语裸传

### Good（纯 task-plane）

```
Write a PM-facing single-stock note based strictly on the facts in the package.
Use restrained Chinese; no editorial meta ("本次 package" / "相比上一版"). State
uncertainty narrowly when present. Include exactly four sections: core judgment,
biggest mistake possible in the thesis, largest open risk, next-week watchpoint.
```

每一句都改变输出质量，没有一句在描述准备过程。

---

## 5. 与其他原则的关系

- **A14 Prompt 边界卫生**：本 skill 是 A14 的操作清单
- **A19 概率性面积最小化**：A19 说"把约束从 prompt 搬到结构上"。本 skill 的「已被 caller / input 保证的 guardrail 一律删」与 A19 同源 — 结构性约束 > prompt 性约束
- **`bestpractice_prose_without_editorial_meta.md`**：本 skill 删的 control-plane 大多在 worker 输出里会变成 editorial meta；上游删干净，下游就不容易污染
- **`bestpractice_reader_state_and_judgment_gain.md`**：写 prompt 时先定义"这一层 reader（worker）读完后新解锁什么"，再决定加什么 task-plane 内容

---

## 6. 自查 checklist（交付 prompt 前）

- [ ] 每一句都能回答「改变 worker 输出质量？」 — 答否的全删
- [ ] 没有 persona-source label / 编排细节 / 组装备注
- [ ] 没有 caller 或 input 已经保证的重复 guardrail
- [ ] worker 不被要求知道它在 pipeline 哪一步
- [ ] 内部术语已翻译成 task-plane 语言
- [ ] worker 输出对应的 reader-gain 是清楚的（这条 prompt 帮 worker 让谁带走什么判断）

任何 [ ] 没勾，回去删 / 改。
