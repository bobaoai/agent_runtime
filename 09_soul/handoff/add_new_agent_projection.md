# Add New Agent Projection：给现有 Hoveath workspace 增加一个 agent runtime 投影

## 0. 定位

本协议用于一个 repo 已经有 `09_soul/`，并且要新增一个并行 agent runtime 投影时使用，例如在已有 `09_claude/` 与 `.cursor/` 之外增加 `09_codex/`。

它不是完整 installation guide 的替代品。`installation_guide.md` 解决「把 Hoveath 装进一个新 workspace」；本文件解决「同一个 workspace 里多接一个 agent runtime」。

新增投影的目标是让 agent 能稳定读到 Hoveath portable 层，并按自身 runtime 的优势工作。它不自动替代当前主投影，也不要求复制已有投影的全部规则、skills、hooks 或 settings。

## 1. 输入判断

开工前先确认五个字段：

| 字段 | 说明 |
|---|---|
| `<agent>` | runtime 名称，如 `codex` / `opencode` |
| `<entry-doc>` | 该 runtime 天然会读取的入口文件 |
| `<projection-dir>` | repo 内投影目录，默认 `09_<agent>/` |
| `<status>` | `draft / active experimental`、`active primary`、`auxiliary`、`deprecated` 等 |
| `<runtime-strength>` | 该 agent 在此 repo 中最适合承担的工作面 |

如果 runtime 天然读取已有入口文件，优先复用它。不要为了对称而发明一个该 runtime 不会自动读取的新入口。

Codex 在本 repo 的默认值：

| 字段 | 值 |
|---|---|
| `<agent>` | `codex` |
| `<entry-doc>` | `AGENTS.md` |
| `<projection-dir>` | `09_codex/` |
| `<status>` | `active workbench projection` in `trading_platform`; `draft / active experimental` is still fine for a first landing in a new repo |
| `<runtime-strength>` | implementation / verification operator |

## 2. First Principles

1. **入口 native-first**：用 agent 实际会读的入口。Codex 优先 `AGENTS.md`；不要先建 `CODEX.md` 或 `.codex/<entry>`。
2. **投影先薄后厚**：v0 只建能启动、能同步、能路由的最小层。等 dogfood 暴露真实缺口后再扩展。
3. **Identity 走 mirror**：`SOUL.md` / `COMMUNICATION.md` / `USER.md` 从 `09_soul/core/` mirror 到 `09_<agent>/core/`。不要手抄。
4. **Adapter 本地化**：项目工作习惯、runtime 优势、truth surface、验证纪律写入 `09_<agent>/core/PROJECT_ADAPTER.md`。
5. **不机械翻译旧投影**：不要把 `.cursor/rules/` 或 `CLAUDE.md` 逐条改名成新 runtime 规则。只迁移新 runtime 启动时必须知道的约束。
6. **skills 与 settings 慢建**：除非 runtime 已确认支持并且当前 task 需要，否则 v0 不建 `.<agent>/skills/`、hooks、settings。
7. **dirty worktree scoped diff**：已有未提交改动时，只碰本投影落地所需文件；不整理、不回滚其他工作面。

## 3. Preflight

新增投影前读取：

1. `<entry-doc>` 当前内容
2. `09_soul/README.md`
3. `09_soul/handoff/README.md`
4. `09_soul/bridging/README.md`
5. `09_soul/bridging/mirror_manifest.json`
6. `09_soul/bridging/mirror_sync.py`
7. 已有主投影入口与 adapter（如 `CLAUDE.md`、`09_claude/core/PROJECT_ADAPTER.md`），只作参考，不作复制源
8. 当前 repo 状态：`git status --short`

如果 `<entry-doc>` 同时服务多个 runtime，在文件内显式声明 runtime boundary：

- 哪个段落属于新 runtime
- 哪些已有投影仍由自己的 native entry 管理
- 新 runtime 不执行其他投影的 startup protocol

## 4. v0 文件集

最小投影建议：

```text
09_<agent>/
├── core/
│   ├── SOUL.md
│   ├── USER.md
│   ├── COMMUNICATION.md
│   └── PROJECT_ADAPTER.md
├── routing/
│   └── task_mainlines.md
└── rules/
    └── INDEX.md
```

不要在 v0 默认创建：

- `.<agent>/skills/`
- `.<agent>/settings.json`
- `.<agent>/hooks/`
- `09_<agent>/axioms/` 全量 mirror
- 从其他投影复制来的细规则目录

这些都应由后续 dogfood 缺口触发，而不是由目录对称性触发。

当某个 runtime 会成为高频工作面时，可以在不创建未确认 native config 的前提下扩展 `09_<agent>/operators/` 或等价 playbook 层。它记录该 runtime 最顺手的执行方式，不要求和 Claude / Cursor 的 skill 目录同构。

`trading_platform` 的 Codex 投影已经采用这条高频工作面路径：`09_codex/skills/` 是 Codex 本地 skill-contract read surface，`09_codex/operators/` 是 Codex-native 执行层，`.claude/skills/` 作为 upstream sync source。

## 5. 建 core mirror

使用 `mirror_sync.py --register` 注册三份 Identity mirror：

```bash
./.venv/bin/python 09_soul/bridging/mirror_sync.py --register \
  --source 09_soul/core/SOUL.md \
  --target 09_<agent>/core/SOUL.md

./.venv/bin/python 09_soul/bridging/mirror_sync.py --register \
  --source 09_soul/core/COMMUNICATION.md \
  --target 09_<agent>/core/COMMUNICATION.md \
  --addendum-marker "## Project-Specific Addendum"

./.venv/bin/python 09_soul/bridging/mirror_sync.py --register \
  --source 09_soul/core/USER.md \
  --target 09_<agent>/core/USER.md \
  --addendum-marker "## Project-Specific Addendum"
```

`PROJECT_ADAPTER.md` 不是 mirror。它是 agent + repo 特定的工作绑定。

## 6. 写 PROJECT_ADAPTER

`09_<agent>/core/PROJECT_ADAPTER.md` 至少覆盖：

- repo 当前真实用途
- truth surfaces
- 该 runtime 在本 repo 的优势与职责
- 该 runtime 不应该接管的边界
- dirty worktree / verification / local tool discipline
- 何时 consult `09_soul/`、何时 consult 其他投影作 drift reference

Codex 类 runtime 的推荐定位是 implementation / verification operator：读仓、改文件、跑验证、做局部迁移、发现一致性风险。少做人格层再解释，多把 Hoveath portable 层落到可验证状态。

## 7. 写 routing 与 rules 索引

`routing/task_mainlines.md` v0 是薄索引，不是业务 overlay 全表。它应告诉 agent：

- 先按输出形态识别主线
- 每条主线的 first authority / truth surface 在哪
- 没有 dedicated skill 时怎么走本地证据与 deterministic gates
- 需要更细业务路由时去读哪个设计 doc 或现有投影参考

`rules/INDEX.md` v0 是加载地图，不是规则大全。它应列出：

- session startup 需要读哪些 core 文件
- always-on 的少量 runtime 纪律
- task-scoped 深读入口
- v0 明确没有加载的东西

## 8. 更新 entry doc

在 `<entry-doc>` 中加入：

- 新 runtime 的 status
- `<projection-dir>` 指针
- startup protocol
- runtime boundary
- host truth surfaces
- 不执行其他投影 startup 的声明

Codex 特例：`AGENTS.md` 是 canonical entry。它可以继续为其他工具保留兼容指针，但 Codex 段必须能独立指向 `09_codex/`。

## 9. 验收

完成后跑：

```bash
./.venv/bin/python 09_soul/bridging/mirror_sync.py --check
```

并做一次 cold-start bootstrap simulation：

1. 从 `<entry-doc>` 开始读
2. 按 startup protocol 读取所有 v0 必读文件
3. 标记哪些是 lazy-load 而没有读
4. 标记哪些能力暂未建目录或 settings
5. 用一个小任务验证路由能落到正确 truth surface

验收输出要明确区分：

- **missing by bug**：入口应读但没有路径或文件
- **missing by v0 design**：v0 刻意不建，如 skills、hooks、full axiom mirror
- **lazy by design**：只有任务触发才读，如具体 axiom、task-scoped detailed rules

## 10. 升级门

满足以下任一条件，再把 v0 往后扩：

- 连续三次 dogfood 都需要同一条 task-scoped 规则
- routing 索引无法把请求稳定落到 truth surface
- agent runtime 已确认支持 native skills/settings/hooks，且当前 repo 有真实任务需要
- mirror check 或 bootstrap simulation 暴露了重复漏读

升级时仍遵守 scoped diff：只扩真实缺口，不追求目录形状对称。

**最后更新**：2026-05-06
