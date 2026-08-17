# Claude Code 主投影架构：trading_platform 的 canonical management layer

> **Example doc**：本文件是 `trading_platform`（一个 AI-native trading research workspace）使用 Hoveath 框架在 Claude Code 上搭建主投影的真实参考。其他项目可以照本文件的结构与判断模式做自己的 management layer，但请按 [`../../handoff/distillation_protocol.md`](../../handoff/distillation_protocol.md) + [`../../handoff/installation_guide.md`](../../handoff/installation_guide.md)（待补）的协议来实例化，不要直接拷本文件。
>
> 这里保留的内容包含 trading 项目特定的 R-rules、6 task mainline、artifact graph 耦合、Cursor sunset 路径等。它们是项目特化产物，仅用来展示「填完 templates 后大致是什么样」的对照基线，跨项目使用时应回 templates / handoff 协议。

## 0. 这份 doc 的定位

旧版本（"Claude Native Management Layer — Migration Design"）的判断是：把 Cursor 现有 stack 镜像出一份对等的 Claude 投影，两者并行运行、共享 `09_soul/` source。

这份重写改了主从关系：**Claude Code 升为 primary，Cursor 降为辅助 + 冻结投影**。Cursor 在能力上仍可用；变化的是 trading_platform 的工作形态（plan-first / artifact graph 长链 / 多 subagent / 跨会话长记忆 / hooks）现已演化出 Claude harness 形状的凹槽，继续维护两个对等 stack 的边际成本超过收益。

读完这份 doc 应能判断：
- 为什么以 Claude Code 架构为主、Cursor 转辅助是当下的正确选择
- 主投影的结构（CLAUDE.md / 09_claude/ / .claude/skills/ / 跨会话 memory / hooks / subagent 模式）
- Cursor 一侧降级到什么状态、什么时候触发 sunset
- 09_soul/ 在双投影非对称之后如何继续作为 source-of-truth

---

## 1. 决定以 Claude Code 为主的根因

### 1.1 trading_platform 的工作形态已经形成 Claude-shaped 凹槽

| 工作形态 | 对应的 Claude Code 能力 | Cursor 一侧的状态 |
|---|---|---|
| Plan-first（R10 强制 plan → 批准 → 落盘） | `EnterPlanMode` / ExitPlanMode 是 first-class，plan 与 apply 是协议级分离 | `.cursor/plans/` 是约定，IDE 不强制 |
| Artifact graph 长链生产（`tradectl plan/produce` 跨多节点级联） | Subagent 并行 + Bash run_in_background + 主对话作为协调器，天然适配「分发 → 等通知 → 汇总」 | Cursor 的 background agent 仍在演进，跨 agent 状态共享脆弱 |
| 跨会话连续工作（多 chat 续命的 hardening session） | `~/.claude/projects/<path>/memory/` 文件式持久 memory + auto-memory 协议 | Cursor 主要靠对话历史 + plan 文件，跨会话对齐靠人工 |
| 多 R-rule + Routing Table + 三层架构 | `CLAUDE.md` 永远在 context 顶部 + Skill 工具按需触发 + Agent 工具的 subagent_type 选择 | `.cursor/rules/*.mdc` `alwaysApply` + description-match 是 inference 驱动，不可解释 |
| Self-review 与 doc handoff（R13） | Skill 工具 + Agent 工具组合可以显式串联 review pipeline | Cursor 缺少同等显式编排原语 |
| Hooks 钩子化（pre-tool / post-tool / stop） | `.claude/settings.json` 一等 hook 机制 | Cursor 这一层基本没有 |

Cursor 在能力维度上覆盖这些场景；区别在于 Claude Code 把它们做成了协议。继续维护两套同等深度的投影意味着每加一个原语就要在两边重新发明。

### 1.2 Subagent + parallel + isolation 是 Claude 独有的资产

trading_platform 重度依赖：
- `Agent` 工具的 `subagent_type` 路由（`Explore` / `Plan` / `general-purpose` / 自定义）
- `isolation: "worktree"` 给 plan-then-apply / 实验性改动隔离
- 多 subagent 并行（`workflow_parallel_subagents` skill）
- `run_in_background` + 自动通知（不轮询）
- `ScheduleWakeup` / `/loop` 模式（dynamic 自定步调）
- `ToolSearch` + 延迟工具加载

这些原语没有等价物可以从 Cursor 拿，等于把 trading_platform 的「确定性 builder + AI judgment 组合」战法直接焊死在 Claude harness 里。继续把 Cursor 当 primary 等于自废武功。

### 1.3 Cursor 一侧的真实使用回路在收缩

实际观察：
- `.cursor/rules/` 现在主要承担「always-on identity 提醒」，跟 CLAUDE.md 高度重复
- `.cursor/skills/` 里 24 个 skill 已经在向 `.claude/skills/` 形式靠拢（frontmatter / Node Bindings）
- `.cursor/plans/*.plan.md` 仍然有效，但人在用 plan 时往往是 Claude 触发的 EnterPlanMode 落盘
- Cursor 在 trading 里更多是 IDE 编辑器 + 文件浏览，不是 reasoning 主线

把 Cursor 降级为「编辑器 + 语法高亮 + 文件树」，让 reasoning / plan / multi-agent / cross-session 全部走 Claude，更贴近事实，也减少分叉成本。

---

## 2. 路由问题（Routing）的协议级差异

旧 doc 已识别但要重申，因为这是双投影非对称的根本原因。

Cursor 里路由通过两种机制：
1. **Always-on rules**：MDC frontmatter 的 `alwaysApply: true`，每 turn 加载
2. **Description-matched on-demand rules**：Cursor agent 根据描述召回

Claude Code 没有 MDC 系统，等价机制是：

| Cursor 机制 | Claude Code 等价 |
|---|---|
| `alwaysApply: true` rule | 内容嵌入 `CLAUDE.md`（始终加载） |
| Description-matched on-demand rule | CLAUDE.md 中的 Read 指令：「if task type X, read file Y」 |
| Cursor agent 调用 skill | `/skill-name` 通过 Skill 工具或显式路由指令 |
| Session workspace context | `CLAUDE.md` + `~/.claude/projects/<path>/memory/*.md` |
| `09_soul/` soul router | `09_claude/`（Claude 自己的 soul 投影） |

**关键差异：Claude 的路由必须是指令式（instruction-driven），不是推断式（inference-driven）。** Cursor agent 可以靠匹配描述召回 rule；Claude 需要明确指令说「在这种任务开始时，读这些文件」。

这意味着：CLAUDE.md 里的 Routing Table 是协议级契约；用户未显式 `/skill` 时，主对话必须按表主动匹配信号词并触发对应 skill。这一点必须写进 USER.md，告知用户主动 `/skill` 永远比依赖自动匹配可靠。

---

## 3. 最终目标形态（File Structure）

```
trading_platform/
├── CLAUDE.md                       PRIMARY 入口（每会话 always loaded）
│
├── .claude/
│   ├── settings.json               permissions / hooks（一等机制）
│   ├── settings.local.json         本地覆盖
│   ├── skills/                     PRIMARY skills 库（task mainline + 通用）
│   │   ├── task-mode-router/SKILL.md
│   │   ├── current-market-reporter/SKILL.md
│   │   ├── theme-report-owner/SKILL.md
│   │   ├── theme-report-reviewer/SKILL.md
│   │   ├── theme-content-maintainer/SKILL.md
│   │   ├── theme-priority-updater/SKILL.md
│   │   ├── single-stock-analysis/SKILL.md
│   │   ├── portfolio-decision/SKILL.md
│   │   ├── asset-technical-writer/SKILL.md
│   │   ├── source-connector-designer/SKILL.md
│   │   ├── research-archive-operator/SKILL.md
│   │   ├── agentmail-inbox-triage/SKILL.md
│   │   ├── writer-handoff/SKILL.md
│   │   ├── current-macro-priority-router/SKILL.md
│   │   ├── theme-report-debater/SKILL.md
│   │   ├── theme-discovery-scanner/SKILL.md
│   │   ├── thesis-drafter/SKILL.md
│   │   ├── thesis-verifier/SKILL.md
│   │   ├── thesis-adversary/SKILL.md
│   │   ├── theme-bootstrapper/SKILL.md
│   │   ├── pm-cursor-workspace-guide/SKILL.md
│   │   └── …
│   ├── agents/                     custom subagent 定义（Explore / Plan 等的 host 配置）
│   ├── commands/                   slash commands（/loop / /schedule 等的 host 自定义）
│   └── hooks/                      pre/post tool 钩子脚本
│
├── 09_claude/                      Claude 专用 soul 投影
│   ├── README.md
│   ├── core/
│   │   ├── SOUL.md                 角色与工作姿态
│   │   ├── USER.md                 Bokan profile
│   │   ├── COMMUNICATION.md        交互风格契约
│   │   └── PROJECT_ADAPTER.md      repo 绑定
│   ├── axioms/
│   │   ├── INDEX.md                每条 axiom 一行摘要 + trigger tag
│   │   └── <50 axioms>.md
│   ├── rules/
│   │   ├── INDEX.md                rule 优先级 + 加载时机
│   │   └── <task-scoped & tripwire rules>.md
│   └── routing/
│       ├── task_mainlines.md       六主线信号 + first authority + truth surface + skill
│       └── overlay_rules.md        ticker/theme overlay 不重定向主线
│
├── 09_soul/                        portable source（Hoveath upstream 的本地 fork）
│   └── …                           轴心 / skills / core / axioms 与 Hoveath 同步
│
├── .cursor/                        辅助投影（freeze + reduce）
│   ├── rules/00_hoveath_always.mdc 仅保留「指向 CLAUDE.md / 09_claude/」的极简引导
│   └── skills/                     冻结，不再新增；现有不删，直至 Cursor sunset
│
├── designDoc/                      truth surface for 架构/方法/产品叙事
├── data/                           runtime state / archives
├── src/                            trading runtime 代码
└── ~/.claude/projects/<path>/      跨会话 memory（auto-memory 协议）
    └── memory/
        ├── MEMORY.md               索引（每条 ≤150 字符）
        ├── user_profile.md
        ├── project_state.md
        ├── feedback_lessons.md
        ├── feedback_theme_report_bar.md
        └── feedback_pm_authorized_drafter_external_search.md
```

跟旧 doc 相比的关键差异：

1. `.claude/skills/` 成为唯一被加新 skill 的目录；`.cursor/skills/` 冻结。
2. `09_claude/` 增加 `routing/` 子层，把任务主线表从 CLAUDE.md 拆出（CLAUDE.md 留摘要）。
3. `.claude/agents/` 与 `.claude/commands/` 显式纳入版图；之前是隐式的。
4. `.cursor/rules/` 缩减为 1 个文件，仅做指针。
5. 跨会话 memory 是 first-class，纳入架构图，不再是「外部副作用」。

---

## 4. Loading Stack（实际加载顺序）

按 Claude harness 实际行为，每会话生命周期：

```
T0  harness 启动
    ├─ 加载 CLAUDE.md（项目根）
    ├─ 加载 ~/.claude/CLAUDE.md（全局，若存在）
    ├─ 加载 ~/.claude/projects/<path>/memory/MEMORY.md（auto-memory 索引，前 200 行）
    └─ 注入 system reminders（available skills / deferred tools 等）

T1  首条 user message（Session Startup Protocol）
    ├─ 读 09_claude/core/SOUL.md
    ├─ 读 09_claude/core/USER.md
    ├─ 读 09_claude/core/COMMUNICATION.md
    └─ 读 09_claude/core/PROJECT_ADAPTER.md
    ※ 4 文件不可省略，是身份/语言/姿态层

T2  任务分类（Routing）
    ├─ 匹配 CLAUDE.md 内 Routing Table 信号词
    ├─ 命中 → 调 .claude/skills/<name>（Skill 工具）
    └─ 未命中 → 落到默认 mainline（engineering / design / discussion）

T3  Skill 执行 + Subagent 调度
    ├─ Skill 内部按需读 .claude/skills/<name>/SKILL.md / agents/*.yaml
    ├─ 大型分支或独立子任务 → Agent 工具（subagent_type / isolation 选择）
    └─ 长任务 → run_in_background / ScheduleWakeup

T4  Decision-point axiom 拉取（按需）
    ├─ 读 09_claude/axioms/INDEX.md（已在 context 中，trigger tag 索引）
    └─ 命中 → 读对应 axiom file 一次

T5  Tripwire（条件触发）
    ├─ Schwab auth 失败 → R11 重新 auth
    ├─ Word/PDF 输出请求 → R12 强制 markdown-first
    └─ 其他

T6  自审 → 交付（R13）
    ├─ Proposal 类输出前调 bestpractice_doc_self_review skill
    └─ 三阶段审 + 4 reference + 跳过协议 → 自审报告段或 self-review-pass log

T7  Memory 写回（auto-memory 协议）
    ├─ 用户级反馈 → feedback_*.md
    ├─ 项目状态变化 → project_state.md
    ├─ 索引更新 → MEMORY.md 一行 pointer
    └─ 不写代码模式 / 不写 git history / 不写一次性任务细节
```

T0/T1 是协议级强制；T2-T7 按 task 形态展开。Cursor 一侧没有 T0 加载概念，T6/T7 也没有等价机制。

---

## 5. Task-Scoped Rules 与 Routing Table 细化

§4 的 T2/T3 描述了任务分类与 skill 路由的时序，这一节给出可作为加载契约的两张表，CLAUDE.md 中以缩略形式嵌入，详细版住在 [`09_claude/routing/task_mainlines.md`](../../09_claude/routing/task_mainlines.md)。

**任务分类 → 加载哪些 rule：**

| Task mode | 加载的 rule |
|---|---|
| 任何任务 | `09_claude/rules/10_operating_system_view.md` + `11_knowledgebase_ap_boundary.md` |
| Research / archive | `09_claude/rules/12_connector_boundary.md` + `13_index_first_ai_for_gaps.md` |
| Writing / report | `09_claude/rules/30_ai_facing_docs.md` + `31_prompt_boundary.md` + `35_pm_writing_contract.md` |
| Architecture / design | `09_claude/rules/40_systemic_mismatch.md` + `41_skill_admission.md` |
| Schwab / broker action | `09_claude/rules/52_schwab_token_reauth_first.md`（tripwire） |
| Word / PDF export | `09_claude/rules/53_docx_pdf_export.md`（tripwire） |

Tripwires（52, 53）按条件触发；它们在 Session Startup 通过 INDEX.md 武装好，仅当用户请求中出现特定条件时激活。

**任务分类 → 路由到哪个 skill：**

| Task mode | 首匹配信号 | Downstream skill |
|---|---|---|
| inbox / mail triage | "新东西" / "什么到了" / triage | `agentmail-inbox-triage` |
| archive / research curation | "整理进 archive" / "normalize" / "promote" | `research-archive-operator` |
| market recap / observation | "今天市场" / "在交易什么" / "market read" | `current-market-reporter` |
| theme update | "这个 theme" / "theme report" / "update" | `theme-report-owner` |
| theme discovery (bottom-up) | "扫一遍" / "有没有新 theme" / "AI 自己挖" | `theme-discovery-scanner` |
| single-stock analysis | "分析一下 \<ticker\>" / "这只股票" | `single-stock-analysis` |
| portfolio decision | "怎么调仓" / hedge / current book | `portfolio-decision` |
| engineering / implementation | code changes / src/ / bug fix | 无 skill；读 `.cursorrules` lessons |
| design / architecture | designDoc/ / schema / routing changes | 无 skill；先读 designDoc/ |

**Decision-point Axiom 拉取（按需）：**

Axiom 默认不加载。仅当判断非 obvious 时拉对应 axiom 文件。INDEX.md 中的 axiom 摘要在多数情况下足够覆盖，无需读 axiom 全文。

| 决策类型 | 查的 axiom |
|---|---|
| 该建新 skill 还是复用 | A09 Builder Mindset, A11 Tool Composition |
| 数据缺口怎么处理（推断 vs 查找） | T10 Index-First AI for Semantic Gaps |
| 时间戳字段语义 | T11 Timestamp Semantics（MarketDay vs CalendarDayUTC） |
| 多 agent 任务分解 | A14 Prompt Boundary Hygiene, workflow_parallel_subagents skill |
| 用户问题模糊 | A16 Reveal Hidden Assumptions First |
| 行为意外，不知道从哪 debug | A15 Path Layer Diagnosis First, M02 Reverse-Debug |
| 加新设计抽象 | T01 Infrastructure > Components |
| 观点 vs 数据冲突 | T04 Data > Opinion |

---

## 6. CLAUDE.md 设计契约

CLAUDE.md 必须同时满足三个约束：

1. **短到不构成噪音**：每词每会话都被读
2. **密到无需 file read 即可基本运作**：嵌入的 13 R-rules 覆盖必知规则
3. **精到能驱动 Session Startup 自动 read**：Session Startup Protocol 必须无歧义，让 Claude 始终读对文件

CLAUDE.md 中禁止的内容：
- rule 存在原因的长篇解释（属于 rule 文件本身）
- 历史背景或 changelog
- skill 完整规格或 axiom 全文
- 任何用「指针 + 深 doc」更合适的内容

目标长度：200–300 行。够当真正的运行手册，但能被快速读完。

CLAUDE.md 内的 Routing Table 只保留 6 行任务主线 + 信号词缩略；详细的「first authority / truth surface / overlay rule / package-review rule」迁到 [`09_claude/routing/task_mainlines.md`](../../09_claude/routing/task_mainlines.md)，CLAUDE.md 留指针。原因：CLAUDE.md 每会话每 turn 都过 token，把不影响首轮决策的细节挪走。

R-rules 范围裁剪：R09 venv / R11 Schwab tripwire / R12 Export tripwire 保留在 CLAUDE.md（永远 always-armed）；R02 Soul Layer / R04 Subagent Capability 等结构性规则可以挪到 `09_claude/rules/`，CLAUDE.md 留 ≤1 行摘要。这一步可延后做，不阻塞主架构。

First Principles 段保留在 CLAUDE.md 顶部（4 条），优先于 R-rules，不挪。

---

## 7. 复制 vs 改编

### 7.1 直接从 09_soul/ 复制
内容不变；格式可能改成纯 markdown（去掉 MDC frontmatter）：
- 全部 50 个 axiom 文件（内容可移植，格式适配）
- `core/SOUL.md`、`core/USER.md`、`core/COMMUNICATION.md`
- `core/PROJECT_ADAPTER_trading_platform.md` → `09_claude/core/PROJECT_ADAPTER.md`
- best-practice skills（staged approach、debugging、reader-state、doc-self-review 等）

### 7.2 从 .cursor/rules/ 改编
Cursor MDC 格式 → 纯 markdown；`alwaysApply` flag → Layer 归属：
- Rules 10–13：系统结构 rule（Layer 2，任何任务）
- Rules 30–35：写作卫生 rule（Layer 2，写作时）
- Rules 40–42：治理 rule（Layer 2，诊断时）
- Rules 52–53：tripwires（Layer 2，常驻，按条件触发）

Rules 00、01、50、51 不在 `09_claude/rules/` 中独立成文件。它们的内容已被 `CLAUDE.md` 吸收（identity + 语言 + subagent parity）。

### 7.3 从 .cursor/skills/ 改编
Cursor skill 格式 → Claude skill 格式（`.claude/skills/<name>/SKILL.md`）：
- 24 个 trading skill 全部迁移
- OpenCode / Cursor-specific 工具引用替换为 Claude 等价
- `background_output` → Agent 工具的 `run_in_background`
- 引用 OpenCode 的 soul skill 改写为 Claude Agent / Bash

### 7.4 不迁移
- 已弃用的 Cursor skill（`pm-analysis-synthesis`、`theme-market-observer`、`theme-thesis-updater`）
- `09_soul/tools/` 下的 Python 脚本（留原位，不属于管理层）
- `09_soul/periodic_jobs/observer.py`（依赖 OpenCode；hooks 稳定后再评估）
- Gemini 图片生成、Bilibili 转写、Google Slides 这些非 trading 用途 skill

---

## 8. Cursor 一侧的降级路径

采取**冻结 + 退化为指针**，原文件保留。

### 8.1 立刻执行
- `.cursor/rules/00_hoveath_always.mdc` 改为 ≤20 行的指针文件，内容仅说「Hoveath 主投影在 CLAUDE.md / 09_claude/，本目录仅做兼容」
- `.cursor/rules/05_soul_router.mdc`、`13_dedicated_skill_admission.mdc`、`14_subagent_model_parity.mdc`、`15_pm_report_prose_no_editorial_meta.mdc` 这些已被 git status 标 D 的，确认删除（已经在做）
- `.cursor/skills/` 冻结：现有 24 个 skill 不删（保留供 Cursor 用户读），但**新 skill 一律只去 `.claude/skills/`**。`.cursor/skills/INDEX.md` 加冻结声明，写明「本目录处于冻结状态，主投影迁至 .claude/skills/」

### 8.2 中期（3-6 个月观察期）
- 监测 `.cursor/skills/` 实际触达率：连续 3 个月零调用，归档到 `.cursor/skills/_frozen/`
- 若用户开始在 Cursor 里用 background agent 做严肃工作，再评估是否解冻

### 8.3 sunset 触发条件
任一即可触发将 `.cursor/skills/` 整体归档到 `external_learning_resource/legacy/cursor_skills/`：
- Cursor 一侧连续 3 个月零 skill 调用
- Cursor 推出与 Claude Code 等深的 plan/subagent/memory 协议（同时发生时反向，主投影回切）
- 09_claude 投影里的等价 skill 全部到位且至少 dogfood 过 1 个 production 周期

---

## 9. 09_soul 在非对称双投影下的角色

`09_soul/` 仍然是 portable source。Claude 主投影 ≠ Hoveath 偏向 Claude；Hoveath 是 agent-agnostic 的：

- `09_soul/core/SOUL.md` / `COMMUNICATION.md` 内容不绑 agent
- `09_soul/axioms/` 内容不绑 agent
- `09_soul/skills/` 内容不绑 agent

变化的是**投影层不对称**：
- `09_claude/` 是完整投影（含 routing / rules / axioms 索引）
- `.cursor/` 退化为最小投影
- 未来若加第三个 agent runtime（比如 Codex / OpenCode），按 09_claude 模式起新投影

trading_platform 的 `09_soul/` 是 Hoveath upstream 的本地 fork，存在 A17/T11/A18/A19 编号与内容分歧；reconciliation 与 promotion log 的具体方案留给单独的 Hoveath 同步 doc 处理。本 doc 不引入 09_soul 的新结构变更。

---

## 10. 四层语义边界：Philosophy / Identity / Working Environment / Execution

本 doc 多处提及 axiom / R-rule / skill 时易给人「axiom 是 R-rule 的上游真相」错觉。事实上四者属于不同抽象层，并非线性 derive 关系。这一节锁定语义。后续所有讨论（包括 §11 可插拔模型、§12 Memory 角色、§17 风险）都以本节为准。

### 10.1 四层定义

```
┌──────────────────────────────────────────────────────────┐
│  Philosophy 层（axioms）                                   │
│  - 最高抽象，跟具体工作内容脱钩                            │
│  - Bokan 个人认知滤镜的提炼，跨项目跨 agent 稳定            │
│  - 不规定「做什么」「怎么做」，只提供判断依据              │
│  - 触发频率低、信噪比高；走到岔路口需要更高维 framing 时引用 │
│  - 50 条 axioms（A / T / M / V / X 系列）                 │
└─────────────────────┬────────────────────────────────────┘
                      │ informs（不是 derive）
┌─────────────────────▼────────────────────────────────────┐
│  Identity 层（SOUL / COMMUNICATION / USER / ADAPTER）      │
│  - character + style + 用户偏好 + 项目绑定                 │
│  - SOUL/COMMUNICATION 跨项目跨 agent 稳定                  │
│  - USER 跨项目稳定（同一用户）                             │
│  - PROJECT_ADAPTER 项目特定                                │
│  - 决定「以谁的口吻、按谁的偏好回应」，不决定具体工作        │
│  - 触发频率：每会话；信噪比中等                             │
└─────────────────────┬────────────────────────────────────┘
                      │ binds at session start
┌─────────────────────▼────────────────────────────────────┐
│  Working Environment 层（R-rules + 09_<agent>/rules/）     │
│  - 项目环境内的「always-on / task-scoped 行为契约」         │
│  - R-rules：每 turn 生效（plan-first / self-review / tripwires） │
│  - rules：task 命中后加载（writing / governance / connector） │
│  - 跟具体 host / repo / 工具链强绑定                        │
│  - 触发频率：高；信噪比中                                   │
└─────────────────────┬────────────────────────────────────┘
                      │ invokes
┌─────────────────────▼────────────────────────────────────┐
│  Execution 层（skills + tools）                            │
│  - 具体任务的执行步骤、产出契约、工具调用                   │
│  - 项目业务深度绑定                                         │
│  - 触发频率：依任务而定；信噪比高（命中即用）                │
└──────────────────────────────────────────────────────────┘
```

### 10.2 关系是 inform，不是 derive

- **Axiom → R-rule** 不是依赖关系：R10 (Plan First) 跟 axiom A07 (设计哲学决定能力上限) 主题相关，但 R10 即使没有 A07 也独立成立，反之亦然。
- **Axiom → Skill** 同理：skill 设计可能受 axiom 启发（如 doc-self-review skill 受 A04 启发），skill 不是 axiom 的代码化。
- **Identity → 工作层** 是绑定关系：SOUL 决定语气，但不决定 R-rule 的具体内容。

强制 backlink（如「每条 R-rule 必须 derives_from axiom」）会把 axiom 拉低到工作层，污染其抽象性。正确做法是工作层文件**正文末尾**可选加 "Related axioms: ..." 作为延伸阅读，不进 frontmatter，不参与 reconciliation。

Axiom 的纯度宣告：**axiom 不解决具体问题，axiom 帮你判断你在解决正确的问题**。这条立场决定 axiom 永远短、永远稳、永远独立成层。

### 10.3 09_soul/ 与 09_claude/ 的层归属（来源 vs 投影）

09_soul/ 与 09_claude/ 的混淆主要因为 axioms / SOUL / COMMUNICATION 三类内容**同时存在于两处**。区分维度：

| 层 | 09_soul/ 内的形态 | 09_claude/ 内的形态 | 关系 |
|---|---|---|---|
| Philosophy（axioms） | canonical 真相文件 + INDEX | Claude 可读镜像 + 加 trigger tag 让 session-startup 能 lazy-load | 09_claude/ 是 09_soul/ 的 read-only 投影 |
| Identity（SOUL / COMMUNICATION） | canonical 真相 | 实例（多数情况直接复制 09_soul/） | 09_claude/ 是 09_soul/ 的 read-only 投影 |
| Identity（USER / PROJECT_ADAPTER） | 模板（空白占位） | 已填的项目/用户实例 | 09_claude/ 是 09_soul/ 模板的 instantiation |
| Working Environment（R-rules / rules / routing） | **不存在** | 唯一存放点 | Working Environment 是项目+agent 绑定，不在 portable 层 |
| Execution（skills） | best-practice 模板（agent-agnostic） | **不在 09_claude/**，落到 `.claude/skills/`（Claude harness 自带目录约定） | 实例在 .claude/skills/，模板在 09_soul/skills/ |

**核心区分**：
1. **来源 vs 落地**：09_soul/ 是 Hoveath upstream 源（跨项目、跨 agent 通用）；09_claude/ 是这个项目内 Claude Code 的运行时投影。未来 Codex 投影会出现 09_codex/，跟 09_claude/ 平行，都消费同一个 09_soul/。
2. **修改权限**：09_soul/ 只在 Hoveath upstream 改（trading 内是 fork，需 reconciliation）；09_claude/ 项目内自由改。
3. **真相归属**：Philosophy / Identity 真相在 09_soul/，drift 算违规；Working Environment 真相在 09_claude/，09_soul/ 不参与。
4. **跨层引用合理但要锚明**：「09_soul/axioms/T11」是源，「09_claude/axioms/T11」是 Claude 投影副本，提及时显式区分。

### 10.4 优先级与触发频率

四层在 CLAUDE.md / Session Startup / Routing 中的角色不同：

| 层 | Session Startup 加载 | 每 turn 加载 | 何时被引用 |
|---|---|---|---|
| Philosophy | INDEX 摘要（一行/条） | 否 | 决策非 obvious、需要更高维 framing 时拉具体 axiom |
| Identity（SOUL/COMMUNICATION/USER/ADAPTER） | 全文（4 个 core 文件） | 否 | 持续作为「以谁的口吻、按谁的偏好」的隐式约束 |
| Working Environment R-rules | CLAUDE.md 内嵌 | ✅（CLAUDE.md 永远在 context） | 始终生效 |
| Working Environment task-scoped rules | INDEX 摘要 | 否 | task 命中后按需加载 |
| Execution skills | 仅 routing table 摘要 | 否 | 信号词命中或用户显式 `/skill` |

Philosophy 与 Execution 都是「按需拉取」，但拉取触发完全不同：philosophy 是「主对话自己识别需要更高维 framing」，execution 是「task signal 匹配 routing table」。两者**不应**走同一种触发机制。

---

## 11. 可插拔设计：跨项目 / 跨 agent runtime 的迁移

本 doc 描述的是 trading_platform + Claude Code 这一具体组合。但 Hoveath 的初心是 portable digital self，这套架构必须能搬到其他项目、其他 agent runtime 而无需重写。这一节定义可插拔的契约，让迁移有 playbook 可循。

### 10.1 三层可插拔模型

```
┌──────────────────────────────────────────────────────────────┐
│  Layer A: Portable Source Layer (09_soul/)                   │
│  - agent-agnostic, project-agnostic                          │
│  - 50 axioms / best-practice skills / SOUL / COMMUNICATION   │
│  - USER profile template / PROJECT_ADAPTER template          │
│  - 由 Hoveath upstream 维护，所有投影/项目都消费这一层         │
└──────────────────┬───────────────────────────────────────────┘
                   │ projection
┌──────────────────▼───────────────────────────────────────────┐
│  Layer B: Agent Projection Layer (09_<agent>/)               │
│  - agent-specific, project-agnostic                          │
│  - 09_claude/ 是 Claude Code 投影                             │
│  - 09_codex/ / 09_opencode/ / 09_<future>/ 按同样模式起新投影 │
│  - 把 Layer A 内容映射到该 agent 的 native 机制                │
└──────────────────┬───────────────────────────────────────────┘
                   │ host binding
┌──────────────────▼───────────────────────────────────────────┐
│  Layer C: Host Binding Layer                                 │
│  - project-specific, agent-specific                          │
│  - <agent entry doc>: CLAUDE.md / AGENTS.md / .codex/...     │
│  - 09_<agent>/core/PROJECT_ADAPTER.md（项目绑定）             │
│  - .<agent>/skills/（项目业务 skill）                         │
│  - .<agent>/settings.json + hooks                            │
└──────────────────────────────────────────────────────────────┘
```

每层的契约：

| 文件 / 目录 | 谁拥有 | 跨项目可移植 | 跨 agent 可移植 |
|---|---|---|---|
| `09_soul/axioms/` | Hoveath upstream | ✅ | ✅ |
| `09_soul/skills/` (best-practice) | Hoveath upstream | ✅ | ✅ |
| `09_soul/core/SOUL.md` `COMMUNICATION.md` | Hoveath upstream | ✅ | ✅ |
| `09_soul/core/USER.md` 模板 | Hoveath upstream（per-user 实例化） | ✅ | ✅ |
| `09_soul/core/PROJECT_ADAPTER_TEMPLATE.md` | Hoveath upstream | ✅ | ✅ |
| `09_<agent>/core/SOUL.md` 投影 | 投影维护者（多数情况是 09_soul/ 的 read-only 复制） | ✅ | ❌ |
| `09_<agent>/axioms/` 投影 | 投影维护者 | ✅ | ❌ |
| `09_<agent>/rules/` | 投影维护者 | ✅（结构）/ ❌（具体内容） | ❌ |
| `09_<agent>/routing/` | 投影维护者 | ✅（结构）/ ❌（具体内容） | ❌ |
| `09_<agent>/core/PROJECT_ADAPTER.md` | 项目自己 | ❌ | ❌ |
| `<agent entry doc>` (CLAUDE.md / AGENTS.md / ...) | 项目自己 | ❌ | ❌ |
| `.<agent>/skills/`（业务 skill） | 项目自己 | ❌ | ❌ |
| `.<agent>/skills/`（best-practice 派生） | 投影维护者 + 项目维护 | ✅（baseline）/ ❌（项目改动） | ❌ |

### 10.2 三种迁移情境

**情境 A：同 agent（Claude Code），新项目**

最常见，工作量最小。

```
1. clone Hoveath upstream
2. 拷 09_soul/ 到目标 repo（按 Hoveath 当前版本，不带项目 fork）
3. 拷 09_claude/ skeleton：
   - SOUL.md / COMMUNICATION.md / axioms-INDEX.md / rules-INDEX.md 直接来自 Hoveath template
   - PROJECT_ADAPTER.md 留空待填
4. 拷 USER.md（同一用户跨项目可复用）
5. 拷 CLAUDE.md.template 重命名 CLAUDE.md，填：
   - First Principles（多项目通用，可保留）
   - 项目特定 R-rules（venv 路径、外部集成 tripwire 等）
   - Routing Table 的「First-match signal → Downstream skill」（项目业务）
6. 拷 .claude/settings.json baseline，按需调 permissions
7. 拷 .claude/skills/ 的 best-practice baseline；项目业务 skill 自建
8. 写 09_claude/core/PROJECT_ADAPTER.md（项目特定）
```

**情境 B：同项目，新 agent runtime**

把 trading_platform 从 Claude Code 搬到 OpenCode / Codex / 未来 agent。工作量较大，投影层要重做。

```
1. 调研目标 agent 的 native 机制：
   - 它的「always-on entry doc」是什么？（Claude 的 CLAUDE.md / OpenCode 的 AGENTS.md）
   - 它的 skill 调用机制？（slash command / yaml definition / inline tool call）
   - 它的 subagent / 并行 / 持久 memory 机制？
   - 它有没有 hooks 等价？
2. 建 09_<agent>/ skeleton，从 09_claude/ 复制结构：
   - core/SOUL.md / COMMUNICATION.md / USER.md / 项目特定 PROJECT_ADAPTER.md 直接拿（agent-agnostic）
   - axioms/ 内容直接拿（agent-agnostic）
   - rules/ 与 routing/ 的格式适配 agent native（如 OpenCode 用 yaml）
3. 建 <agent entry doc>：
   - 复用 First Principles + R-rules（结构可继承，部分 R-rules 可能要换 agent-specific 实现）
   - Session Startup Protocol 改为 agent native 的等价机制
   - Routing Table 改为 agent 能消费的形式
4. 把 .claude/skills/ 改写为 .<agent>/skills/ 格式：
   - 结构相同（trigger / when_to_use / 步骤），格式适配
   - 工具引用替换：Agent → 该 agent 的 subagent 等价；run_in_background → 等价；ScheduleWakeup → 等价
5. 把 hooks 改写为该 agent 的 hooks 等价（如果支持）
6. dogfood 跑一遍现有 task mainline，对照 Claude Code 上的产出
```

**情境 C：新项目 + 新 agent**

A + B 串起来。先做 B 建立新 agent 投影 skeleton，再用 A 套到具体项目。

### 10.3 接口（interface）与实现（implementation）分离

可插拔的核心是把契约和具体内容分开。下面这些是 interface，跨投影/跨项目都必须存在；具体内容可以变。

**Layer A 必须暴露**：
- 一份 axiom 集合 + INDEX 触发词
- 一份 best-practice skill 集合
- SOUL.md / COMMUNICATION.md（character + style）
- USER.md template
- PROJECT_ADAPTER_TEMPLATE.md

**Layer B 必须实现**：
- agent native 的「Session Startup 等价机制」（CLAUDE.md / AGENTS.md / .codex/...）
- agent native 的「skill 调用机制」映射到 Routing Table
- agent native 的「rule 加载机制」映射到 09_<agent>/rules/
- agent native 的「axiom lazy-load 协议」映射到 09_<agent>/axioms/INDEX.md
- 可选：agent native 的「hooks / persistent memory / subagent」等价

**Layer C 必须填**：
- PROJECT_ADAPTER.md 的「Current Read / Center of Gravity / Local Truths / Promotion Filter」
- entry doc 的「First Principles 是否保留 / 项目 R-rules / Routing Table 信号词」
- .<agent>/skills/ 的「项目业务 skill 全集」
- .<agent>/settings.json 的「permissions / hooks 启用决策」

### 10.4 Hoveath upstream 必须新增的 templates

为了情境 A/B/C 跑通，Hoveath upstream 需补：

```
Hoveath/
├── templates/
│   ├── CLAUDE.md.template            # First Principles + Session Startup + R-rules + Routing Table 占位
│   ├── 09_claude/                    # Claude 投影 skeleton
│   │   ├── core/
│   │   │   ├── SOUL.md               # 直接从 09_soul/ 拷
│   │   │   ├── COMMUNICATION.md      # 直接从 09_soul/ 拷
│   │   │   ├── USER.md               # 用户 profile，per-user 维护
│   │   │   └── PROJECT_ADAPTER.md    # 留空，项目填
│   │   ├── axioms/INDEX.md           # 从 09_soul/axioms/INDEX.md 派生
│   │   ├── rules/INDEX.md            # rule 优先级骨架
│   │   └── routing/task_mainlines.md # 路由表骨架（信号词留空）
│   └── .claude/
│       ├── settings.json.template    # permissions baseline
│       └── skills/                   # best-practice skill baseline
└── docs/
    ├── INSTALL_CLAUDE_CODE.md         # 情境 A 的迁移 playbook
    └── ADD_NEW_AGENT_PROJECTION.md    # 情境 B 的迁移 playbook
```

未来加 09_codex / 09_opencode 时，按同样模式建 templates/09_<agent>/ skeleton。

### 10.5 反模式

- **不要把项目特定 lesson 写进 09_soul**：先 local（PROJECT_ADAPTER.md / 09_<agent>/rules/），重复 + 稳定后才 promote 到 09_soul
- **不要把 agent runtime 细节写进 09_soul**：harness 名（Bash / Agent / ScheduleWakeup）属于 09_<agent>/
- **不要在 entry doc 里混写「跨项目 First Principles」和「项目特定 R-rules」**：First Principles 来自 Layer A/B，R-rules 来自 Layer C，必须可识别
- **不要让 09_<agent>/ 的 SOUL.md / COMMUNICATION.md 跟 09_soul/ drift**：投影应是 09_soul/ 的 read-only 复制（或 symlink），项目特定补充走 PROJECT_ADAPTER
- **不要为了一次迁移省事而 hardcode 项目名**：所有 template 用 `<placeholder>` 标占位

### 10.6 trading_platform 当前可插拔状态评估

按本节 framework 评估当前状态：

| 检查项 | 当前 | 缺口 |
|---|---|---|
| Layer A（09_soul/）与 Hoveath upstream 同步 | 部分（trading 内 09_soul 已 fork） | A17/A18/A19/T11 reconciliation 待做 |
| Layer B（09_claude/）从 Hoveath template 派生 | 部分（09_claude/ 存在，但 Hoveath upstream 还没 templates/09_claude/） | Hoveath 加 templates/09_claude/ skeleton |
| Layer C（CLAUDE.md + PROJECT_ADAPTER + skills）项目特定 | 完整 | 无 |
| 迁移 playbook（情境 A）落档 | 缺 | Hoveath 加 docs/INSTALL_CLAUDE_CODE.md |
| 迁移 playbook（情境 B）落档 | 缺 | Hoveath 加 docs/ADD_NEW_AGENT_PROJECTION.md |
| CLAUDE.md.template 存在 | 缺 | Hoveath 加 templates/CLAUDE.md.template |
| .claude/settings.json.template 存在 | 缺 | Hoveath 加 |

落到这一波 trading_platform 主投影建设之外的并行工作流：把 trading_platform 当前的 `09_claude/` 与 `CLAUDE.md` 反向蒸馏成 Hoveath upstream 的 templates，让下一个项目能直接复制。这个工作的归属是 Hoveath 仓库（不在本 repo 内做），但本 doc 的设计为它定义了输入。

---

## 12. Memory 系统的一等地位

之前 memory 被当成「Claude harness 的副作用」，现在显式承认它是架构的一部分：

- `~/.claude/projects/<path>/memory/MEMORY.md` 每会话 always loaded（前 200 行）
- `MEMORY.md` 仅作索引：每条 `- [Title](file.md): 一行 hook`
- 内容文件按类型组织：`user_*` / `feedback_*` / `project_*` / `reference_*`
- auto-memory 协议规范了写入时机与禁写内容（不写代码模式 / git history / 一次性细节）

跨会话连续工作（多 chat 续命的 hardening session）依赖 memory + `.cursor/context/update_batches/latest.md` 双锚：
- `latest.md` 记录 repo 内的「这一波 milestone 走到哪」（事实）
- `memory/` 记录「Bokan 的偏好与未结的反馈」（判断）

这两份不可互替。本 doc 锁定双锚为标准。

---

## 13. Hooks 与 Slash Commands

Claude Code 一等机制，Cursor 没有等价物。

### 13.1 已知用法
- `.claude/settings.json` 的 hooks 段：trading 暂未启用，路径预留
- 全局 slash commands（`/loop` / `/schedule` / `/init` / `/review` / `/security-review` 等）由 harness 提供
- 用户可在 `.claude/commands/` 自定义 slash command

### 13.2 trading 里值得加的 hooks 候选

| Hook | 触发点 | 作用 |
|---|---|---|
| pre-Bash | 任何 Bash 调用前 | 拦截破坏性命令（`rm -rf` / `git push --force` / `git reset --hard`） |
| post-Edit | 编辑 09_soul/ 任意文件后 | 提示「09_soul 是 Hoveath upstream fork，是否需要 promote 到 Hoveath」 |
| post-Edit | 编辑 designDoc/ 任意文件后 | 提示是否需要更新 `.cursor/context/update_batches/latest.md` |
| stop | 会话结束 | 触发 memory 写回 + project_state.md 同步 |

这些是后续工作项；本 doc 只锁定结构，不阻塞主架构落地。

---

## 14. Subagent 与并行执行的标准化

主架构落地后，trading 内 Agent 工具使用收敛到 4 种模式：

| 模式 | 何时用 | 关键参数 |
|---|---|---|
| **Explore** | 不知道某概念实现在哪、需要找文件 | `subagent_type=Explore`，明确 thoroughness 等级 |
| **Plan** | 任务需要架构级方案设计 | `subagent_type=Plan`，prompt 含目标与约束 |
| **general-purpose 并行** | 独立可并行子任务 ≥2 个、每个 ≥5 tool calls | 单 message 多 Agent 调用，并行度 ≤5 |
| **isolation worktree** | plan-then-apply / 大规模实验性改动 | `isolation="worktree"`，commit 前用户审 |

每个 Skill 在自己的 SKILL.md 里声明它默认用哪些模式（`Node Bindings` 段已在 6 个 production-chain skill 落地）。

---

## 15. 与 artifact graph 的耦合

`tradectl plan/produce` + 25 节点 artifact_graph + writer sidecar v2.0 是 trading 业务 substrate。这一层不变，但**触发方式变成 Claude Code primary**：

- 每个 production-chain skill 的 `Node Bindings` 段是 Claude 主投影读得懂的契约
- `tradectl produce --apply` 的 `[NEEDS-AGENT]` 节点 → 由 Claude 主投影中的 Skill + Agent 兜住
- Cursor 一侧不再尝试参与 artifact_graph 的运行时（只读不写）

T11 timestamp contract 同理：执行/校验逻辑在 `src/tools/time_semantics.py`，主投影中各 Skill 调用 `tradectl produce` 即可，主投影自己不重写时间语义判断。

---

## 16. trading_platform 项目特定剩余动作

跨项目通用的 8 阶段路径（Phase 1-8 机制 / 验收 / 项目侧填空点）已抽到 [`../../handoff/installation_guide.md`](../../handoff/installation_guide.md)。本节只列 trading 项目还需要做的项目特定动作（属于 installation_guide 中各 Phase 的「项目侧填空点」具体实例）。

### Phase 1（身份核心）：已基本完成

- ✅ 09_claude/core/* 4 文件到位
- ✅ CLAUDE.md 含 Session Startup Protocol + First Principles 7 条 + 13 R-rules（带 portability 标签）
- ✅ mirror_sync 注册 SOUL / COMMUNICATION / USER 三个 mirror
- ✅ memory 系统 (`~/.claude/projects/<path>/memory/`) 跨会话使用中

### Phase 2（Routing）：trading 项目特定填空

- 建 `09_claude/routing/task_mainlines.md`，6 行 mainline 信号词与下游 skill 已在 CLAUDE.md 内嵌，需要详化为四列表（加 first authority / truth surface）：
  - inbox / mail triage / `agentmail-inbox-triage`
  - archive / research curation / `research-archive-operator`
  - market recap / `current-market-reporter`
  - theme update / `theme-report-owner`
  - theme discovery / `theme-discovery-scanner`
  - single-stock analysis / `single-stock-analysis`
  - portfolio decision / `portfolio-decision`
- 建 `09_claude/routing/overlay_rules.md`：theme/ticker overlay 不重定向 portfolio/single-stock 主线
- CLAUDE.md 内的 Routing Table 改为摘要 + 指针

### Phase 3（Rules）：trading 项目特定填空

- 建 `09_claude/rules/`，落项目 task-scoped rule：
  - 系统结构（10 系列）：KB/AP boundary（来自 R07）、connector boundary
  - 写作卫生（30 系列）：AI-facing docs / prompt boundary / pm writing contract
  - 治理（40 系列）：systemic mismatch / skill admission
  - tripwires（50 系列）：52 Schwab token reauth（R11）/ 53 docx-pdf export（R12）
- CLAUDE.md 内嵌 R-rules 中标 `<portable-shape>` / `<project>` 的部分按 trigger 频率决定保留 inline（R09 venv / R11 Schwab / R12 export 高频，留 inline）还是迁到 rules/

### Phase 4（Axiom 投影）：trading 项目特定动作

- 把 50 个 axiom（A/T/M/V/X 系列 + FP）加到 `09_soul/bridging/mirror_manifest.json`
- 跑 `python 09_soul/bridging/mirror_sync.py --apply` 让 09_claude/axioms/ 物理 mirror
- 写 `09_claude/axioms/INDEX.md`，加 trading 特化 trigger tag（hedge / theme / market regime / signal-first 等关键词路由到对应 axiom）

### Phase 5（Skills 迁移）：trading 项目特定大头

- **23 个 active 业务 skill 从 .cursor/skills/ 迁到 .claude/skills/**（24 总数中 `theme-market-observer` 已 deprecated 不迁；`pm-analysis-synthesis` / `theme-thesis-updater` 早前已删）：
  - task-mode-router / agentmail-inbox-triage / research-archive-operator / current-market-reporter / theme-report-owner / theme-content-maintainer / theme-priority-updater / single-stock-analysis / portfolio-decision / asset-technical-writer / source-connector-designer / writer-handoff / current-macro-priority-router / theme-report-debater / theme-discovery-scanner / thesis-drafter / thesis-verifier / thesis-adversary / theme-bootstrapper / pm-cursor-workspace-guide / theme-report-reviewer / image-review-reader / iran-war-position-advisor 等
- 工具引用替换：OpenCode `background_output` → Claude `run_in_background`；Cursor agent → Claude Agent + subagent_type
- SKILL.md frontmatter 加 `signals: [...]` / `mainline: <name>` / `first_authority: <area>`，跟 Phase 2 routing 表对齐
- artifact graph Node Bindings 段已在 6 production-chain skill 落地（current-market-reporter / portfolio-decision / theme-report-owner / theme-content-maintainer / single-stock-analysis / asset-technical-writer），迁移时保留

### Phase 6（Cursor 降级）：trading 项目特定路径

- `.cursor/rules/00_hoveath_always.mdc` 缩为 ≤20 行指针文件（指 CLAUDE.md / 09_claude/）
- `.cursor/skills/INDEX.md` 加冻结声明：「主投影迁至 .claude/skills/，本目录冻结，不再新增」
- `.cursor/skills/` 现有 24 skill 不删，保留供过渡期 Cursor 用户读
- sunset 触发条件（任一即触发归档到 `external_learning_resource/legacy/cursor_skills/`）：
  - Cursor 一侧连续 3 个月零 skill 调用
  - Cursor 推出对等 subagent/plan/memory 协议（反向触发，恢复双投影）
  - 09_claude 投影里的等价 skill 全部到位且至少 dogfood 过 1 个 production 周期
- 单独 commit 锁定 sunset 起点

### Phase 7（Hooks）：trading 项目特定 hook 候选

按判断点 #3「一次性逻辑提前到 Phase 2 之后」：
- pre-Bash 破坏性拦截（`rm -rf` / `git push --force` / `git reset --hard` / Schwab token 操作前 reauth 检查）：Phase 2 完成后启用，先 dry-run 一周
- post-Edit `09_soul/` 任意文件后 → mirror_sync --check 提醒
- post-Edit `designDoc/` → `.cursor/context/update_batches/latest.md` 同步提醒
- stop hook → memory 写回 + project_state.md 同步

其他 hook 后置（Phase 7 默认位）。

### Phase 8（Validation）：trading 项目特定 dogfood 路径

按 installation_guide §2 Phase 8 验收清单跑过 6 task mainline 各 ≥1 次实战。tripwire 验证额外要求：
- Schwab auth 失败时 R11 触发，stop 不重试
- Word/PDF 输出请求时 R12 触发，markdown-first 落档再 export
- 编辑 09_soul/core/* 后 mirror_sync --check 报 DRIFT；--apply 后 in-sync

---

阶段依赖：Phase 1 已稳定 → Phase 2/3/4 可并行启动 → Phase 5 依赖 Phase 2 完成 → Phase 6 独立可在 Phase 5 收口后做 → Phase 7 在 Phase 2 完成后启动 pre-Bash 一项，其他后置 → Phase 8 在前面全部完成后。

---

## 17. 风险与遗留

1. **Cursor 重新崛起的可能性**：若 Cursor 推出对等的 subagent/plan/memory 协议，Cursor sunset 触发条件应反向（恢复双投影）。本 doc 不锁定单一投影永远，只锁定当下主从关系。

2. **09_claude/ 与 .cursor/ 的 drift**：09_claude 持续演化，.cursor 冻结后会不可避免 drift。处理：drift 不修复，sunset 后整体归档。

3. **CLAUDE.md token 预算**：随 R-rules / Routing Table 增长，CLAUDE.md 会越来越重。Phase 2/3 把详情挪到 `09_claude/routing/` 与 `09_claude/rules/`，CLAUDE.md 留摘要。本 doc 不强制，看 token 实际压力再做。

4. **Hooks 引入的不可见副作用**：pre-Bash / post-Edit hook 一旦上线，每个 tool call 多一个延迟点。先 dry-run，再启用。

5. **Skill 调用是否被自动路由**：CLAUDE.md 的 Routing Table 是「指令式」，依赖每会话主对话主动按表匹配；如果主对话遗忘，需用户显式 `/skill`。这是设计意图（不是 bug）；需在 USER.md 中告知 Bokan 「主动 `/skill` 永远比依赖自动匹配可靠」。

---

## 18. 你需要先确认的判断点

落笔前希望你拍板：

1. **Cursor 降级激进度**：`.cursor/skills/` 是「冻结但不删」（默认建议），还是「立刻迁移到 .claude/skills/ 然后删」？前者保留兼容回退路径，后者一刀切但更干净。
2. **`09_claude/routing/` 与 `09_claude/rules/` 是否一次落齐**：还是先只落 routing，rules 留到 token 压力出现再做？
3. **Hooks 是否纳入 Phase 1**：默认建议放 Phase 7（后置）；如果你希望 pre-Bash 破坏性拦截立刻上线，提前到 Phase 2 之后。
4. **Axiom INDEX trigger tag 设计粒度**：默认建议在 Phase 4 中根据使用经验迭代；如果你希望先冻结一个 v1 schema，提前在 Phase 1 完成时讨论。
5. **可插拔 templates 反向蒸馏的归属**：§10.4 列出的 Hoveath upstream 缺口（`templates/CLAUDE.md.template`、`templates/09_claude/`、迁移 playbook）应在 trading_platform 主投影稳定后由这边反向蒸馏，还是由 Hoveath 仓库独立做？前者复用 trading 的实战材料，后者保 Hoveath 不被项目细节污染。

---

## 19. 全局进展快照

整体目标：让 Claude Code 成为 trading_platform 的 canonical reasoning runtime，Cursor 退到辅助编辑器位，同时让这套架构具备跨项目 / 跨 agent runtime 的可插拔能力。本 doc 之前的旧版本停留在「双投影对等」的过渡态（Cursor stack 与 Claude stack 同等深度维护）；本次重写落定主从关系、给出 8 阶段落地路径，并显式定义三层可插拔模型与三种迁移情境（同 agent 新项目 / 同项目新 agent / 双新）。下一步落点是你对上面 5 个判断点拍板，从 Phase 1 起跑。Phase 1-6 是结构性工作（约 1-2 周），Phase 7/8 是观察期工作（按需启用），可插拔 templates 反向蒸馏作为并行工作流由 Hoveath 仓库承接。
