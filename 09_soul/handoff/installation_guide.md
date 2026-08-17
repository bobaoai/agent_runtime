# Installation Guide：把 09_soul/ 装进新 workspace 的 SOP

## 0. 这份 doc 的定位

[`distillation_protocol.md`](distillation_protocol.md) 描述项目 → Hoveath upstream 的反向蒸馏（出站方向）。本 doc 是对偶的**入站方向**：把 Hoveath 的 `09_soul/` drop 到一个新 workspace 后，怎么搭出可运行的主投影。

读完应能判断：
- 装 Hoveath 的 8 阶段路径（Phase 1-8）
- 每阶段产物 / 依赖 / 验收
- 跨项目通用的机制部分 vs 项目特定的填空部分如何分工
- 多 agent runtime（Claude Code / Codex / OpenCode / ...）适配点在哪

读完后**不**能直接拿到 templates 文件（templates/ 当前还在蒸馏 backlog 上）。当前实操路径：本 doc 给机制 SOP，[`../examples/`](../examples/) 给「填完什么样」的对照。Templates 落地后本 doc 会改为「按 templates 走 + 这份 SOP 解释为什么」。

---

## 1. 入口判断

启动 install 前先回答：

1. **目标 agent runtime 是？** Claude Code / OpenCode / Codex / 其他。本 SOP 默认 Claude Code（`<entry-doc>` = `CLAUDE.md`、`<agent>` = `claude`）；其他 agent 看 [`add_new_agent_projection.md`](add_new_agent_projection.md)
2. **同用户跨项目还是新用户？** 同用户：`09_<agent>/core/USER.md` 的 source 段直接复用；新用户：先在 `09_soul/core/USER.md` 改 portable profile
3. **新项目还是已有项目转 Hoveath？** 新项目：8 阶段顺序走；已有项目：Phase 0 加「现状盘点」步，识别哪些工作面已存在、哪些可继承

---

## 2. 8 阶段路径（Phase 1-8，跨项目通用）

每阶段三栏：动作 / 验收 / 项目侧填空点。

### Phase 1：身份与交互核心（基础）

**动作**：
- 拷 `09_soul/` 整个到目标 repo 根
- 建 `09_<agent>/core/` 目录
- 跑 `python 09_soul/bridging/mirror_sync.py --register` 三次注册 SOUL / COMMUNICATION / USER 三个 mirror（COMMUNICATION 与 USER 用 `--addendum-marker "## Project-Specific Addendum"`）
- 写 `09_<agent>/core/PROJECT_ADAPTER.md`（按 `09_soul/core/PROJECT_ADAPTER_TEMPLATE.md` 结构填项目特定字段）
- 建 `<entry-doc>`（CLAUDE.md / AGENTS.md / ...），填：
  - 身份段（项目名 + Hoveath 在该项目的角色）
  - Session Startup Protocol（4 文件清单：SOUL / USER / COMMUNICATION / PROJECT_ADAPTER）
  - First Principles 段（7 条 inline + pointer 到 `09_soul/axioms/FP_first_principles.md`）
  - Core Rules 段（先填项目特定 R-rules；通用 R-rule baseline 等 Phase 3）
  - 暂留 Routing Table stub（等 Phase 2）
- 建 memory 目录约定（agent harness 自带的就用；否则按 [`bestpractice_skill_writing`](../skills/) 等 skill 的 memory 范式）

**验收**：
- 在该 repo 起一个新会话，agent 能按 Session Startup 读到 SOUL/USER/COMMUNICATION/PROJECT_ADAPTER 4 文件
- agent 沟通风格符合 09_soul/core/COMMUNICATION.md（mirror 已生效）
- `python 09_soul/bridging/mirror_sync.py --check` 退出码 0

**项目侧填空点**：项目名、运行时锚点文件、truth surface 目录路径、用户偏好补充段（USER addendum）、PROJECT_ADAPTER 全部内容

**这一阶段必须先稳定再进入后续。** SOUL / USER / COMMUNICATION 影响每会话，不只特定任务类型。

### Phase 2：Routing 层

Phase 2 开始前先完成 **Governance Release**：从
`09_soul/governance/` 同时安装完整 T0 baseline 与 required Governance Skills，并生成
该项目自己的 `the_charter.md`。T0 law 与 governance operating methods 分属两个
manifest，但属于同一次 Governance Release；前者投影到 `designDoc/the_*.md`，后者
把 portable method 与可选的 hash-bound project binding 机械组合后，精确投影到已注册
的 Primary Agent host Skill 目录。项目 Registry、Code Projection、Runtime Module
binding、T1 specialization 和 deterministic enforcement 在项目侧落地；本地 Module
或 Workflow identity 进入 `governance_bindings/`，不反向写进 portable Skill source。
这个安装动作不重新做 aggregate governance-bundle review 或 announcement；只做确定性
兼容、依赖闭包和投影检查。

```bash
python 09_soul/governance/governance_t0_release.py --apply --project-root .
python 09_soul/governance/governance_skill_release.py --apply --project-root .
```

验收或 CI 使用同样两个入口的 `--check`。任一失败，Governance Release 都不 clean。

**验收**：项目 Charter 已填写项目身份与决策权；T0 Registry 与 canonical Design
Doc 闭合；portable baseline 没有被项目侧静默改写；required Governance Skills 的
role、subject、T0 dependency 与 host projection 全部闭合；本地 Code Projection 与
测试入口可解析。

**动作**：
- 建 `09_<agent>/routing/task_mainlines.md`，定义任务主线 → first authority → truth surface → downstream skill 四列表，含 overlay rule 与 package-review rule
- 建 `09_<agent>/routing/overlay_rules.md`（如果项目有 ticker / theme / 文件级 overlay 需要约定）
- `<entry-doc>` 内的 Routing Table 缩为摘要 + 指针到 routing/

**验收**：
- 给 agent 一个明确命中某 mainline 信号词的请求，agent 应路由到对应 downstream skill（如果 skill 还没建，应至少 stub 出 skill 名）
- overlay 信号（如 ticker 嵌在 portfolio 请求里）不应重定向主线

**项目侧填空点**：N 行 mainline 的具体信号词、first authority 命名、truth surface 路径、downstream skill 名

### Phase 3：Rules 层

**动作**：
- 建 `09_<agent>/rules/INDEX.md`（rule 优先级 + 加载时机说明）
- 落具体 rule 文件，按类别分组：
  - 系统结构（10 系列）
  - 写作卫生（30 系列）
  - 治理（40 系列）
  - tripwire（50 系列）
- 把 `<entry-doc>` 内嵌的 R-rule 中标 `<portable-shape>` / `<project>` 的部分，根据触发频率决定保留 inline（高频）还是迁到 rules/（task-scoped）
- 给每条 R-rule 加 portability 标签：`<portable>` / `<portable-shape>` / `<project>`

**验收**：
- agent 进特定任务模式时按 INDEX 加载对应 rules
- tripwires 在条件触发时正确激活（如外部 auth 失败 / 输出格式跨边界）
- portability 标签覆盖 100% R-rules

**项目侧填空点**：项目特定 tripwire（venv 路径 / broker auth / 输出格式约束 / ...）、写作卫生 rule 中的项目术语

### Phase 4：Axiom 层投影

**动作**：
- 把 50 个 axiom（含 FP）从 `09_soul/axioms/` mirror 到 `09_<agent>/axioms/`
- 实操：扩展 `09_soul/bridging/mirror_manifest.json`，把 axioms 加进去（建议 batch register：每个 axiom 文件一条）
- 跑 `python 09_soul/bridging/mirror_sync.py --apply`
- 写 `09_<agent>/axioms/INDEX.md`（带 trigger tag 让 lazy-load 工作）

**验收**：
- agent 在做需要 framing 的判断时能 lazy-load 具体 axiom 文件
- INDEX 中 trigger tag 覆盖核心使用场景（不能太宽 / 太窄）
- mirror_sync --check 退出码 0

**项目侧填空点**：trigger tag 的项目特定补充（如 trading 项目可加「持仓 / hedge / market」触发词到相关 axiom）

### Phase 5：Skills 层

**动作**：
- 拷 `09_soul/skills/` 中跨项目通用的 best-practice skill 到 `.<agent>/skills/`（如 doc-self-review / parallel-subagents / staged-approach 等）
- 不在本阶段重新创建或覆盖 Phase 2 已由 Governance Release 安装的 required Governance Skills
- 建项目业务 skill（项目特有，不入 09_soul/）
- 每个 skill 在 frontmatter 声明 `signals: [...]` / `mainline: <name>` / `first_authority: <area>`，跟 Phase 2 routing table 对齐
- 工具引用对齐 agent runtime 的 native 命名（如 Cursor `background_output` → Claude `run_in_background`）

**验收**：
- routing 表里每个 mainline 的 downstream skill 都已存在并能调用
- skill 内部工具引用全部用目标 agent 的 native 命名
- best-practice skill 能在 cross-project 场景被复用（不依赖项目特定路径）

**项目侧填空点**：业务 skill 全集（routing → skill 的实现）、skill 内部 tool 引用绑定

### Phase 6：旧 agent 投影降级（仅适用于已有项目转 Hoveath）

**动作**（如果项目之前用过 Cursor / OpenCode / 其他 agent）：
- 把旧投影（如 `.cursor/rules/`）的 always-on 文件改为指针文件，仅保留「主投影已迁移到 `<new-entry-doc>` / `09_<new-agent>/`」一行声明
- 旧投影的 skills 目录（如 `.cursor/skills/`）加冻结声明：新 skill 一律只去 `.<new-agent>/skills/`
- 单独 commit 锁定 sunset 起点
- 按 examples/ 中提供的 sunset 触发条件监测旧投影使用率

**验收**：
- 用户在旧 agent runtime 打开 repo 时仍能看到指针，知道主投影迁了
- 新 agent runtime 完整接管 reasoning 主线
- 旧投影 freeze 后零新增 skill

**项目侧填空点**：旧投影目录路径、降级时间表、sunset 触发条件

新项目跳过 Phase 6。

### Phase 7：Hooks 启用（可选，后置）

**动作**：
- 在 `.<agent>/settings.json` 或等价配置启用 hooks：
  - **pre-Bash 破坏性拦截**：拦 `rm -rf` / `git push --force` / `git reset --hard` 等命令（推荐第一波启用）
  - **post-Edit 09_soul drift 提醒**：编辑 09_soul/core/* 后提示是否需要 promote 到 Hoveath upstream
  - **post-Edit truth-surface drift 提醒**：编辑 designDoc/ 或等价 truth surface 后提示是否需要更新 live state pointer
  - **stop hook**：会话结束时触发 memory 写回 + project_state 同步
- 每条 hook 先 dry-run 一周再启用
- 失败 hook 不应 block 主对话流；只产生 warning

**验收**：
- 启用后实际产生几次破坏性命令拦截（验证拦截逻辑工作）
- post-Edit 提醒在适当频率出现（不烦不漏）
- 主对话整体延迟可接受（hook 不应让每个 tool call 慢 >100ms）

**项目侧填空点**：hook 脚本（hook 内的具体命令拦截规则可项目特化），dry-run 周期

### Phase 8：Validation

**动作**：
- 跑通项目所有 task mainline 各 ≥1 次实战
- 验证清单（每条做对照测试）：
  - Phase 1 Session Startup 4 文件加载在会话开始正确触发
  - Phase 2 任务路由产出正确的 downstream skill
  - Phase 3 task-scoped rules 在 task 命中时按 INDEX 加载
  - Phase 4 Axiom lazy-load 在示例决策点能工作
  - Phase 5 各 skill 能完成 dogfood 输出
  - Phase 1 沟通风格匹配 COMMUNICATION.md
  - 跨会话 memory 写回符合 auto-memory 协议
  - Doc self-review 在 Proposal 类输出前确实触发（R13）
  - Phase 7 hooks（如已启用）按预期触发

**验收**：所有清单项 pass；不 pass 的回对应 Phase 修

---

## 3. 阶段依赖

```
Phase 1（身份核心）  ← 阻塞门，必须先稳定
   ↓
Governance Release（Hoveath baseline + project Charter → local T0 system）
   ├── Phase 2（required governance Skills + routing） 可与 3/4 并行
   ├── Phase 3（rules）     可与 2/4 并行
   ├── Phase 4（axiom 投影） 可与 2/3 并行
   └── Phase 5（skills）    依赖 Phase 2 routing 完成（skill 名 vs routing 表对齐）
       ↓
   Phase 6（旧 agent 降级）  独立，可在 Phase 1 后任意时间启动；新项目跳过
       ↓
   Phase 7（hooks）         在 Phase 2-5 完成后；先 dry-run 再启用
       ↓
   Phase 8（validation）    在前面所有阶段完成后；产 validation 报告
```

---

## 4. 跨 agent runtime 适配点

不同 agent runtime 在以下点需要替换：

| 概念 | Claude Code | OpenCode | Codex | 通用 |
|---|---|---|---|---|
| entry doc | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `<entry-doc>` |
| skill dir | `.claude/skills/` | `.opencode/skills/`（待确认） | TBD | `.<agent>/skills/` |
| settings | `.claude/settings.json` | `opencode.json` | TBD | `.<agent>/<config>` |
| subagent 调用 | Agent tool + subagent_type | （待确认） | TBD | 各自 native |
| hooks | `.claude/settings.json` hooks 段 | （待确认） | TBD | 各自 native |
| memory | `~/.claude/projects/<path>/memory/` | （待确认） | TBD | 各自 native |
| persistent state | auto-memory 协议 | （待确认） | TBD | 各自 native |

第一行的 `<entry-doc>` 等占位贯穿本 SOP；其他每行的 native 等价见 [`add_new_agent_projection.md`](add_new_agent_projection.md)。

---

## 5. 项目侧填空点清单（Phase 1-8 汇总）

按字段聚合，方便项目侧一次性准备：

```
项目身份：
  <project_name>             项目名
  <project_short_desc>       一句话项目描述
  <agent>                    目标 agent runtime
  <entry-doc>                agent native entry 文件名
  <project_charter>          目标项目的产品身份、scope 与人类决策权

工作面：
  <truth_surface_dir>        架构真相目录（如 designDoc/）
  <runtime_anchor_files>     运行时锚点文件清单
  <live_state_pointer>       当前状态文档路径

Routing：
  <task_mainline_N_signal>   每条主线的信号词（中英文）
  <task_mainline_N_authority> 每条主线的 first authority
  <task_mainline_N_truth>    每条主线的 truth surface
  <task_mainline_N_skill>    每条主线的 downstream skill 名

R-rules（项目特定 tripwire）：
  <env_runtime_path>         语言运行时路径（如有）
  <external_auth_targets>    外部集成认证目标（如有）
  <output_format_targets>    需要 markdown-first 的输出格式（如有）

User addendum：
  <user_project_addendum>    USER.md addendum 段（项目特定的当前工作形态）

Identity 项目绑定：
  <project_adapter_full>     PROJECT_ADAPTER.md 全部 section

旧 agent 降级（如适用）：
  <old_agent_dir>            旧投影目录
  <sunset_triggers>          sunset 触发条件
```

---

## 6. 验收门

- Phase 1 完成 → mirror_sync --check 退出码 0、agent 沟通风格符合 COMMUNICATION
- Governance Release 完成 → project Charter + local T0 Registry + Code Projection deterministic checks 全部闭合
- Phase 2-5 完成 → 一次完整 dogfood 周期跑得通
- Phase 6 完成 → 旧投影 sunset commit 落地、零新增 skill 流入旧目录
- Phase 7 完成 → 所有 hooks dry-run 通过、误触发率 ≤5%
- Phase 8 完成 → validation 清单全 pass

每个验收门 fail 时回对应 Phase 修，不要往下推。

---

## 7. 不允许的捷径

| 禁止式 | 检测式 |
|---|---|
| 不允许跳 Phase 1 直接进 Phase 2-5 | session 起会发现 SOUL/USER/COMMUNICATION 没加载 → agent 沟通风格漂、用户偏好缺失 |
| 不允许把一个项目的 Charter 当 portable T0 baseline 复制 | 新项目会继承错误的产品身份、scope 和决策权 |
| 不允许为安装 Hoveath 再制造 aggregate governance-bundle review | upstream 已审语义被重复审；安装应只做确定性兼容与投影检查 |
| 不允许手抄 09_soul/core/* 到 09_<agent>/core/* 跳过 mirror_sync | 后续 09_soul 改了，09_<agent> 不动；下次 --check 报 DRIFT |
| 不允许 Phase 5 之前先 Phase 8 validation | skill 还没建，validation 必失败 |
| 不允许 Phase 7 hooks 跳过 dry-run 直接启用 | 误触发率高、用户体验差、可能阻断主对话流 |
| 不允许把项目业务 skill 写到 09_soul/skills/ | 污染 portable 层；下个项目继承时会带不需要的业务依赖 |

---

**最后更新**：2026-04-25
