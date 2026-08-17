---
id: axiom_a21_skill_agent_boundary_2026
category: ai_agentic
created: 2026-05-08
updated: 2026-05-08
note: 在 trading_platform 项目内首发时编号为 a18；回流 Hoveath 母仓库时与 a18_verification_cost_conservation 冲突，按 a20 同一时间序原则重编号为 a21。
---

# A21. Skill / Agent 边界不可混淆

## 1. 核心公理

Skill 是一种可复用、可测试的能力单元。Agent 是一个带目标、状态和权限的执行主体。

更短地说：

```text
Skill 回答：这类事情应该怎么做。
Agent 回答：为了达成某个目标，现在该做什么。
```

架构上必须保持四层分离：

```text
Tool < Skill < Workflow < Agent
```

Tool 是手脚。Skill 是手艺。Workflow 是流水线。Agent 是有判断权的执行主体。

## 2. 深度推演

### 2.1 Skill 是能力结构，不是主体结构

Skill 封装的是一种稳定方法：明确输入、明确输出、稳定步骤、提示词、工具调用、检查规则和失败边界。它的价值来自可复用、可测试、可迁移。

Skill 不应该自己决定今天要研究什么，也不应该承担最终业务目标。它被调用后完成一类任务。它可以很复杂，但复杂不等于 agentic。

例子：

- Fed Speech Diff Skill：比较两次 Fed 讲话，输出语气变化、关键词变化、forward guidance shift。
- PDF Ingestion Skill：把 PDF 转成可读文本、结构化提取和阻塞原因。
- Thesis Falsification Skill：给定 thesis，生成可验证的 falsifier 和观察路径。
- Portfolio Exposure Attribution Skill：给定持仓和市场因子，解释 exposure 来源。

这些都是能力模块。它们回答“这件事怎么做”，不回答“现在该不该做这件事”。

### 2.2 Agent 是责任结构，不是 prompt 包装

Agent 拥有目标、状态、策略、可调用 Skill / Tool、权限边界和停止条件。它在不完全信息下做选择：下一步看什么、调用哪个 Skill、是否继续、是否升级给人类、结果是否够可靠。

Agent 的核心不是会调用工具，而是对一个目标负责。

例子：

- Macro Thesis Agent：维护一个宏观 thesis cluster 的状态，决定今天该看 Fed speech、CPI surprise、yield curve attribution，还是触发 regime check。
- Portfolio Risk Agent：持续跟踪 book 脆弱性，选择 exposure attribution、scenario stress、hedge gap review 等能力。
- Event Monitor Agent：监控事件流，判断噪音、重要变化和升级条件。

这些是执行主体。它们回答“为了目标，现在该做什么”。

### 2.3 有没有自主决策权，是最关键边界

判断标准：

| 问题 | 如果答案是 Yes |
|---|---|
| 它会自己决定下一步吗 | 更像 Agent |
| 它维护长期状态吗 | 更像 Agent |
| 它对一个业务目标负责吗 | 更像 Agent |
| 它只是把输入变成输出吗 | 更像 Skill |

能被 Agent 调用的，通常是 Skill。能决定调用哪些 Skill 的，才是 Agent。

### 2.4 Workflow 可以复杂，但仍然不等于 Agent

Workflow 是固定步骤的流程。它可以每天自动跑，可以有多个分支，可以很长，但只要路径提前写死，它仍然是流水线，不是 Agent。

例如：

```text
每天 8 点：
1. 拉取 Fed speeches
2. 做 diff
3. 更新 dashboard
4. 生成 summary
```

这不是 Agent。它不在目标和反馈下选择下一步。它只是在执行预设路径。

### 2.5 假 Agent 是架构污染

很多系统把任何小功能人格化：

```text
PDF Agent
SQL Agent
Search Agent
Chart Agent
Email Agent
Summary Agent
```

这些名称看起来 agentic，本质上常常只是工具函数加 prompt。污染后果是：责任边界脏、审计困难、Skill 复用失败、Workflow 顺序和 Agent 决策混在一起。

更好的结构是：

```text
Research Agent
    ├── Search Skill
    ├── PDF Reading Skill
    ├── Evidence Ranking Skill
    ├── Thesis Extraction Skill
    └── Report Writing Skill
```

Agent 按责任域划分。Skill 按能力划分。Tool 按执行接口划分。Workflow 按流程划分。

## 3. 四层定义

### 3.1 Tool

Tool 是最原子的执行 handle。

例子：

- `web_search`
- `read_pdf`
- `query_database`
- `send_email`
- `run_python`
- external writer service
- deterministic validator function

Tool 本身没有判断力。它只是手脚。

### 3.2 Skill

Skill 是可复用、可测试的能力包，用于在明确输入和明确约束下，按照稳定方法生成明确输出。

英文定义：

```text
A skill is a reusable, testable capability package that transforms a defined input into a defined output under a known procedure.
```

Skill owns:

- input contract refs;
- output contract refs;
- method;
- prompt / procedure;
- allowed Tool refs;
- checks and failure boundary.

Skill does not own:

- autonomous objective;
- long-term state;
- next-step choice;
- final business responsibility;
- permission boundary beyond its own invocation.

### 3.3 Workflow

Workflow 是固定步骤的流程。

Workflow owns:

- step order;
- step input refs;
- step output refs;
- Tool / Skill / Agent invocation order;
- pass / fail / blocked handoff shape.

Workflow does not own:

- adaptive policy;
- PM belief;
- material meaning;
- autonomous objective.

### 3.4 Agent

Agent 是面向目标运行的执行主体，能够维护状态、选择技能或工具、执行动作、评估反馈，并决定继续、停止或升级给人类。

英文定义：

```text
An agent is a goal-directed runtime actor that maintains state, selects skills/tools, executes actions, evaluates feedback, and decides when to continue, stop, or escalate.
```

Agent owns:

- objective;
- state;
- policy;
- Skill refs;
- Tool refs;
- permissions;
- stop / escalation condition;
- responsibility for the target outcome.

Agent does not own:

- Material schema unless it is also the material owner;
- raw data truth;
- Tool implementation;
- fixed workflow topology unless the Agent explicitly owns orchestration.

## 4. 应用判定

### 何时使用

- 命名任何 AI module、Skill、Agent、Workflow、Tool。
- 设计 contract inheritance、prompt source、runtime runner、external agent、validator gate。
- 发现某个 Skill 开始写 objective / state / permission。
- 发现某个 Agent 只是包装一个 prompt 或函数。
- 评审系统里是否出现假 Agent。

### 如何实践

1. 先问它是否只是把输入变成输出。是则优先建 Skill。
2. 再问它是否只是调用代码或外部服务。是则优先建 Tool。
3. 再问它是否只是固定步骤。是则优先建 Workflow。
4. 最后问它是否拥有目标、状态、策略、权限和 stop condition。只有这些成立，才建 Agent。
5. 如果一个名字里出现 Agent，但缺少 objective / state / policy / permissions / stop condition，降级为 Skill、Workflow 或 Tool。

## 5. 架构反模式

### 5.1 把 prompt wrapper 叫 Agent

如果一个“Agent”只接收输入、执行 prompt、吐出输出，它是 Skill 或 Tool wrapper。命名成 Agent 会制造虚假的自主性。

### 5.2 把 Skill 写成小型 Workflow

如果 Skill 开始决定多步调度、运行顺序、上下游 gate，它在侵占 Workflow。

### 5.3 把 Workflow 写成 Agent

如果流程只是固定步骤，却被命名成 Agent，下游会误以为它能根据目标和反馈自我调整。

### 5.4 把 Tool 写成 Skill

如果一个对象只是 `query_database`、`run_python`、external writer service 或 validator function，它是 Tool。只有当它封装稳定方法、输出契约和检查规则时，才是 Skill。

## 6. 相关公理

- **A03 从 IC 到经理的心智转变**：Agent 是管理对象，Skill 是能力单元；管理 AI 的核心是正确分配责任。
- **A04 可靠性是管理问题**：可靠性来自清楚的角色、权限、状态、检查和升级边界。
- **A11 工具组合即能力扩展**：Tool 组合成 Skill，Skill 再被 Workflow 和 Agent 使用。
- **A14 Prompt 边界卫生**：Skill / Agent 边界决定 prompt 中哪些信息属于 task-plane，哪些属于 control-plane。
- **A16 先揭示隐藏假设，再回答更好的问题**：命名一个对象前，先揭示它是否真的拥有自主决策权。
- **T03 上下文隔离是多 Agent 价值**：Agent 的价值来自隔离目标、状态和决策上下文，不是把工具人格化。
- **V02 可验证性是信任的地基**：Skill / Agent / Workflow / Tool 边界可审计，系统才可信。

## 7. 简化准则

命名前问四句：

1. 它只是执行原子动作吗？是 Tool。
2. 它把定义好的输入变成定义好的输出吗？是 Skill。
3. 它按固定步骤推进吗？是 Workflow。
4. 它根据目标、状态和反馈决定下一步吗？是 Agent。

如果第四句不成立，不要叫 Agent。

## 8. Provenance

本 axiom 来自 Bokan 在 2026-05-08 关于 AI framework 中 Skill / Agent 边界的定义：Skill 是可复用能力单元；Agent 是带目标、状态和权限的执行主体；Skill 回答“这类事情应该怎么做”，Agent 回答“为了达成某个目标，现在该做什么”。该定义在 Research Technical smoke 重构中被用于分离 Tool、Skill、Workflow、Agent 的 contract boundary。
