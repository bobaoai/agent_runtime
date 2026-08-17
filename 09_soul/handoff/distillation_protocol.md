# Distillation Protocol：项目 09_<agent>/ 反向蒸馏到 Hoveath upstream templates

## 0. 这份 doc 的定位

[`installation_guide.md`](installation_guide.md)（待补）描述「Hoveath template 落到项目主投影」的入站方向（Hoveath → project）。本 doc 描述对偶的**出站方向**：项目在 agent runtime 上磨合出来的 `09_<agent>/` + entry doc + skills 实际工件，反向蒸馏为 Hoveath upstream 的 portable templates，让下一个项目（或下一个 agent runtime）能直接复制。

读完应能判断：
- 蒸馏的输入（项目 工件）与输出（Hoveath templates）映射
- 项目特定内容如何被剥离为 placeholder
- 蒸馏完成后如何验证（round-trip：把 templates 重 instantiate 回项目，看一致性）
- 项目侧需提供什么、Hoveath 仓库侧承担什么

蒸馏的归属：Hoveath 仓库（`09_soul/` 升 upstream），不在被蒸馏的项目内执行落盘。本 doc 写在 09_soul/handoff/ 是因为协议本身是 Hoveath 框架的一部分。

### 子体清单与双向 message 通道

母体维护的子体清单与双向 message 载体在 [`../examples/`](../examples/) 下，每个子体一份子目录：

- **子体注册卡** `examples/<project>/registry.md` — 关系定义、继承的 baseline、已知 drift、同步建议
- **outbox/** `examples/<project>/outbox/` — 母体发给子体的通告（"刚升级了 X，建议 dogfood Y"）
- **inbox/** `examples/<project>/inbox/` — 子体回母体的反馈（人工搬运自子体 `09_<agent>/handoff/to_upstream/`）
- **message 格式** [`../examples/MESSAGE_FORMAT.md`](../examples/MESSAGE_FORMAT.md) — frontmatter schema + 命名约定 + 状态机
- **本机路径** `examples/.local_paths.json`（gitignored，从 `.local_paths.json.template` 衍生）— 母体跨机器一致，子体物理路径在每台机器各异

handoff/ 答"怎么做蒸馏"，examples/<project>/ 答"哪些子体在被管、它们说了什么、母体回了什么"。本 doc 的 S5/S6 蒸馏动作产出的"建议子体同步"消息，应落到对应 outbox/。

---

## 1. 为什么需要这一层

Hoveath 的可插拔承诺有三层：

- **Layer A（`09_soul/`）**：Hoveath upstream，agent-agnostic、project-agnostic
- **Layer B template（`templates/09_<agent>/` skeleton）**：agent-specific、project-agnostic
- **Layer C template（`templates/<entry-doc>` + `templates/.<agent>/` skeleton）**：agent-specific、project-agnostic 模板，等项目填 placeholder

Layer A 已经存在。Layer B/C templates 缺失时，可插拔承诺只在 Layer A 兑现，下个项目仍要从零搭建主投影。

蒸馏必须发生在被蒸馏项目的**主投影稳定之后**（至少 dogfood ≥1 周）。过早蒸馏等于把还在迭代的中间态固化，下一个项目反而被锁死。

蒸馏本身是一次结构性测试：哪些内容能干净地剥成 placeholder，哪些剥不掉，剥不掉的就是「自以为是 portable 实际是 project-specific」的部分。这一层的副产品是把 Layer B/C 的边界从理论变成事实。

---

## 2. 输入与输出

**输入（项目内的实战工件）**：

```
<project>/
├── <entry-doc>                    # CLAUDE.md / AGENTS.md / other agent-native entry
├── .<agent>/
│   ├── settings.json              # permissions / hooks
│   ├── skills/<...>               # 项目业务 skill + best-practice 实例
│   ├── agents/<...>               # custom subagent 定义（如有）
│   ├── commands/<...>             # 自定义 slash command（如有）
│   └── hooks/<...>                # 钩子脚本（如有）
└── 09_<agent>/
    ├── README.md
    ├── core/
    │   ├── SOUL.md                # 99% 是 09_soul/core/SOUL.md 的 mirror
    │   ├── COMMUNICATION.md       # 99% 是 09_soul/core/COMMUNICATION.md 的 mirror + 项目 addendum
    │   ├── USER.md                # 99% 是 09_soul/core/USER.md 的 mirror + 项目 addendum
    │   └── PROJECT_ADAPTER.md     # 完全项目特定
    ├── axioms/                    # 多数情况是 09_soul/axioms/ 的 mirror
    ├── rules/                     # 项目+agent 特定（无 09_soul/ 等价）
    └── routing/                   # 项目+agent 特定（无 09_soul/ 等价）
```

**输出（Hoveath upstream 内的可复用 templates）**：

```
09_soul/
├── templates/
│   ├── <entry-doc>.template       # First Principles + Session Startup + R-rules + Routing Table 占位
│   ├── 09_<agent>/                # agent 投影 skeleton
│   │   ├── README.md.template
│   │   ├── core/
│   │   │   ├── SOUL.md            # 直接从 09_soul/core/ 拷
│   │   │   ├── COMMUNICATION.md   # 直接从 09_soul/core/ 拷
│   │   │   ├── USER.md            # source-of-truth 在 09_soul/core/，模板就是它
│   │   │   └── PROJECT_ADAPTER.md.template
│   │   ├── axioms/INDEX.md        # 从 09_soul/axioms/INDEX.md 派生
│   │   ├── rules/INDEX.md.template
│   │   └── routing/<...>.template
│   └── .<agent>/
│       ├── settings.json.template
│       └── skills/                # best-practice baseline 子集
├── handoff/
│   ├── distillation_protocol.md   # 本 doc
│   ├── installation_guide.md      # 入站方向 SOP
│   └── add_new_agent_projection.md
└── examples/
    └── <project>/                 # 真实 instantiation 作为参考
```

---

## 3. 蒸馏对象映射（项目工件 → Hoveath template）

按文件分类。每条三栏：源 / 目标 / 蒸馏动作。

### 3.1 entry-doc（CLAUDE.md / AGENTS.md / ...）

| 源段 | 目标段 | 蒸馏动作 |
|---|---|---|
| 身份段（Hoveath operating inside `<project>`） | 身份段 | `<project>` placeholder |
| Session Startup Protocol（4 文件清单） | 同 | verbatim 保留（结构跨项目不变） |
| First Principles | 同 | verbatim 保留（升 Philosophy 层 axiom 后跨项目通用） |
| 跨项目 R-rules（标 `<portable>`） | 同 | verbatim 保留 |
| 跨项目 R-rules（标 `<portable-shape>`） | 抽象段 | 保留结构，把项目术语改 placeholder |
| 项目特定 R-rules（标 `<project>`） | tripwire 范例段 | 改为 placeholder + 一段说明（"如果项目用 X，按此模板填"） |
| Routing Table 摘要 | Routing Table Stub | 保留三列表头与 overlay/package-review 规则，N 行 mainline 内容 placeholder |
| Deeper Context Pointers 表 | 同 | 保留前两列结构，第二列大部分 placeholder |

蒸馏后 `<entry-doc>.template` 的「跨项目可复用区」与「项目占位区」必须在 doc 内显式标注（用 `<!-- portable -->` / `<!-- project-fill -->` 注释，或采用项目已有 portability 标签如 `<portable>` / `<project>`）。

### 3.2 09_<agent>/core/* → templates/09_<agent>/core/*

| 源 | 目标 | 蒸馏动作 |
|---|---|---|
| `09_<agent>/core/SOUL.md` | `templates/09_<agent>/core/SOUL.md` | 直接拷贝（应与 `09_soul/core/SOUL.md` 一致；mirror 关系由 bridging/mirror_sync 维护）。蒸馏阶段做一次反向校验，drift 修正后再发 |
| `09_<agent>/core/COMMUNICATION.md` | `templates/09_<agent>/core/COMMUNICATION.md` | 同上；项目 addendum 段在 template 中变占位 |
| `09_<agent>/core/USER.md` | `templates/09_<agent>/core/USER.md` | 直接拷贝 source 段（用户跨项目稳定）；addendum 段变占位 |
| `09_<agent>/core/PROJECT_ADAPTER.md` | `templates/09_<agent>/core/PROJECT_ADAPTER.md.template` | 全段 placeholder（section 结构来自 `09_soul/core/PROJECT_ADAPTER_TEMPLATE.md`） |

### 3.3 09_<agent>/axioms/ → templates/09_<agent>/axioms/

axioms 内容来自 09_soul/，不需要项目特定化。蒸馏动作：把项目内的 fork 跟 09_soul/ upstream reconcile 后回吐。

| 源 | 目标 | 蒸馏动作 |
|---|---|---|
| `09_<agent>/axioms/INDEX.md` | `templates/09_<agent>/axioms/INDEX.md` | 用 reconcile 后的 axiom 编号体系重写 INDEX |
| 单个 axiom 文件 | `templates/09_<agent>/axioms/<id>_<name>.md` | 文件级 1:1 拷贝；编号冲突在 reconcile 阶段先解决 |

### 3.4 09_<agent>/rules/ → templates/09_<agent>/rules/

| 源 | 目标 | 蒸馏动作 |
|---|---|---|
| `09_<agent>/rules/INDEX.md` | `templates/09_<agent>/rules/INDEX.md.template` | 保留优先级骨架与加载时机说明，task-scope 列里的具体 rule 文件名 placeholder |
| 项目特定 rule 文件（如某个 broker 的 token 重发协议） | 留 examples/，作为「project-specific tripwire 范例」 | 不直接进 templates |
| 通用 rule 文件（如 axiom 运行时投影类） | `templates/09_<agent>/rules/<id>_<name>.md` | verbatim 拷贝 |

### 3.5 09_<agent>/routing/ → templates/09_<agent>/routing/

| 源 | 目标 | 蒸馏动作 |
|---|---|---|
| `09_<agent>/routing/task_mainlines.md` | `templates/09_<agent>/routing/task_mainlines.md.template` | 保留四列表头（Signal / Mainline / First Authority / Downstream Skill）+ overlay rule + package-review rule，N 行 mainline 信号词全部 placeholder |
| `09_<agent>/routing/overlay_rules.md` | `templates/09_<agent>/routing/overlay_rules.md` | verbatim（overlay rule 设计原则跨项目通用） |

### 3.6 .<agent>/ → templates/.<agent>/

| 源 | 目标 | 蒸馏动作 |
|---|---|---|
| `.<agent>/settings.json` | `templates/.<agent>/settings.json.template` | permissions baseline（read-only 命令 / 常用 MCP）保留，permission rules 中的项目路径 placeholder，hooks 段保留 schema |
| `.<agent>/skills/`（best-practice 子集：领域无关者） | `templates/.<agent>/skills/` baseline | verbatim 拷贝；每个 SKILL.md 检查是否有项目特定引用，有就 placeholder |
| `.<agent>/skills/`（项目业务 skill） | **不蒸馏**，留 examples/<project>/ | 仅作参考保留 |
| `.<agent>/agents/` `commands/` `hooks/` | `templates/.<agent>/<...>` 或 留 examples/ | 看是否有 portable 设计；否则留本地 |

---

## 4. 蒸馏顺序（S0-S8）

```
S0  前置：项目主投影完成且 dogfood ≥1 周
    （09_<agent>/core/* 稳定 / entry-doc 稳定 / .<agent>/skills/ 至少跑过一遍主流任务）

S1  09_soul reconciliation
    - 项目内 09_soul/ ↔ Hoveath upstream 09_soul/ 双向同步
    - 编号冲突、缺失 axiom、新增 axiom 全部 reconcile
    - 无此步骤，axioms 蒸馏会带分叉污染下游

S2  axiom / soul 层蒸馏（无项目占位）
    - templates/09_<agent>/core/SOUL.md ← 09_soul/core/SOUL.md
    - templates/09_<agent>/core/COMMUNICATION.md ← 09_soul/core/COMMUNICATION.md
    - templates/09_<agent>/axioms/ ← reconcile 后的 axiom 全集

S3  rules / routing 骨架蒸馏
    - templates/09_<agent>/rules/INDEX.md.template
    - templates/09_<agent>/routing/task_mainlines.md.template
    - 通用 rule 文件 verbatim 拷贝
    - 项目特定 tripwire 改为 examples/

S4  entry-doc.template 蒸馏（最难，最多 placeholder 决策）
    - 标注 portable / project-fill 两类区
    - First Principles + 跨项目 R-rules verbatim
    - 任务 mainline 信号词全 placeholder
    - tripwire 段提供 0..N 个范例

S5  USER.md.template + PROJECT_ADAPTER.md.template
    - USER.md：源段是 09_soul/core/USER.md 的拷贝；addendum 段变 placeholder 模板
    - PROJECT_ADAPTER.md：复用 09_soul/core/PROJECT_ADAPTER_TEMPLATE.md 已有结构，加 §<Agent>-Specific Notes 段

S6  .<agent>/ 工件蒸馏
    - settings.json.template
    - .<agent>/skills/ baseline（领域无关 skill 子集）
    - examples/<project>/ 保留项目特定 skill 作参考

S7  Playbook docs 写作（如尚未存在）
    - 09_soul/handoff/installation_guide.md（入站方向 SOP）
    - 09_soul/handoff/add_new_agent_projection.md（情境 B 模板）

S8  Round-trip validation
    - 在 sandbox repo 用 templates instantiate 一次
    - diff 跟原项目当前真实结构（去掉项目业务部分后）
    - drift > 0 的位置 = 蒸馏漏洞，回 S2-S6 修
    - drift = 0 才算 templates v0.1 ready
```

S0 是硬阻塞；S1 阻塞 S2；S2-S6 完成后才能进 S7；S7 完成后必须跑 S8。S8 是 templates 的验收门。

---

## 5. Placeholder 协议

```
<project>                  项目名（短写法，文件路径用）
<project_name>             项目名（长写法，doc 内文用）
<project_short_desc>       一句话项目描述
<agent>                    agent runtime 名（claude / codex / opencode / ...）
<entry-doc>                agent native entry 文件名（CLAUDE.md / AGENTS.md / ...）
<domain_layer_1>           主语义层 1（如某个域的 KnowledgeBase 等价）
<domain_layer_2>           主语义层 2
<runtime_anchor_files>     运行时锚点文件清单
<truth_surface_dir>        架构真相目录（如 designDoc/ 或等价）
<live_state_pointer>       当前状态文档路径
<task_mainline_N_signal>   第 N 主线的信号词
<task_mainline_N_skill>    第 N 主线的下游 skill 名
<env_runtime_path>         语言运行时路径（如 ./.venv/bin/python）
<external_auth_target>     外部集成认证目标
<output_format_targets>    需要 markdown-first 的输出格式
<user_name>                用户称呼
<user_role>                用户身份描述
<user_preferences_block>   偏好工作方式段
<user_dislikes_block>      会让你烦的段
<user_system_block>        系统偏好段
```

每个 placeholder 在 `installation_guide.md` 中必须有：含义解释（一行）+ 范例（取自某个 example project）+ 是否必填（required / optional）。未注册的 placeholder 不允许出现在 templates 中。

---

## 6. 反向同步与 fork drift 治理

蒸馏不是单次工作，是持续过程。项目在迭代，Hoveath templates 跟着迭代。

1. **项目改 09_<agent>/ 后**：判断改动是 project-specific 还是 portable。前者留项目；后者进入 promotion log，下一轮蒸馏窗口反吐 Hoveath
2. **Hoveath 改 templates 后**：项目视情况把 templates 更新拉回（结构性升级）或忽略（仅影响新项目）
3. **promotion log 位置**：每个项目维护 `09_<agent>/promotion_log.md`，记录「项目内本周新增的 portable lesson 候选」。Hoveath upstream 维护者定期巡检
4. **Drift 是信号，不是错误**：drift 出现的位置 = 当前 templates 的覆盖盲点。把 drift 当下一轮蒸馏的输入，不是要立刻消除的污染

---

## 7. 验证（Round-trip Test）

S8 的具体跑法：

```bash
# 1. sandbox 起空 repo
mkdir /tmp/<project>_reinstall && cd /tmp/<project>_reinstall
git init

# 2. 按 installation_guide.md 跑安装步骤
cp -r <hoveath_repo>/09_soul .
cp -r <hoveath_repo>/09_soul/templates/09_<agent> .
cp <hoveath_repo>/09_soul/templates/<entry-doc>.template ./<entry-doc>
cp -r <hoveath_repo>/09_soul/templates/.<agent> .

# 3. 填 placeholder（用项目实际值）
# 编辑 <entry-doc> / 09_<agent>/core/PROJECT_ADAPTER.md 等

# 4. diff 对照真实项目
diff -r <project_repo>/09_<agent> /tmp/<project>_reinstall/09_<agent>
diff <project_repo>/<entry-doc> /tmp/<project>_reinstall/<entry-doc>
```

**期望**：
- 项目-specific 内容（如 broker tripwire / 路径绑定 / 业务术语）会 diff 出（OK，那是 placeholder 填进去的）
- 业务 mainline 信号词会 diff 出（OK）
- SOUL.md / COMMUNICATION.md / axioms / 通用 rule 应**完全一致**（不一致 = fork drift，回 S1 修）
- routing 表骨架、entry-doc 的 portable R-rules 应**完全一致**（不一致 = 蒸馏 S4 漏了占位）

drift = 0 才算 templates v0.1 ready；之后每次 Hoveath templates 升级或项目 09_<agent>/ 升级，重跑一次。

---

## 8. 风险与遗留

1. **过早蒸馏**：项目主投影还在迭代时蒸馏，下个项目用到的 template 是中间态。处理：S0 强制 dogfood ≥1 周
2. **过细 placeholder**：每行都有 placeholder，模板复杂度爆炸。处理：placeholder 总数控制 ≤30；超过的合并或承认是 portable
3. **反向污染**：把项目 lesson 误升级到 portable。处理：每次写新 placeholder 前问「这个真在 3 个不同项目都会出现吗？」否就保留为 example
4. **API 稳定性破坏**：早期版本快速迭代 breaking。处理：templates v0.1 发布前都视为 alpha；之后开始 SemVer
5. **多 agent 扩展时模板不够抽象**：加 09_codex/ 时可能发现 templates 设计假设了某个 agent 特性。处理：S7 写 add_new_agent_projection.md 时把已知假设显式列出
6. **promotion log 没人巡**：项目的 promotion log 写了，Hoveath upstream 没人看。处理：规定季度巡检节奏，落到日历事件

---

## 9. 项目侧 / Hoveath 侧的责任分配

| 工作 | 谁做 |
|---|---|
| 主投影构建 + dogfood | 项目侧 |
| promotion log 维护 | 项目侧 |
| 09_soul/ 与项目 fork 的 reconcile | Hoveath 维护者 + 项目侧协作 |
| templates/ 写作 | Hoveath 维护者（拿 reconcile 完的项目工件做输入） |
| Round-trip validation | Hoveath 维护者，sandbox 跑 |
| examples/<project>/ 维护 | 项目侧 + Hoveath 维护者协作（项目侧提供素材，Hoveath 侧整理） |
| installation_guide / handoff doc 演进 | Hoveath 维护者 |

---

## 10. examples/ 是验证集

每个完成蒸馏的项目都应在 [`../examples/<project>/`](../examples/) 留一份**精简版**的实例（`<project>_management_layer.md` 或类似），用来：
- 给后来项目看「这个 template 实际填出来长什么样」
- 给 round-trip 测试做对照基线
- 给 Hoveath 维护者发现 templates 抽象失败之处

精简版不需要包含项目业务细节（不要把交易策略 / 用户偏好 / 内部信息塞进 examples/），只保留结构 + portability 决策。

---

**最后更新**：2026-04-25
