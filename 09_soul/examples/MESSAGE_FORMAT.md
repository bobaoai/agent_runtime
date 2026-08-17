# Message Format — 母体 ↔ 子体双向通告

## 这层是干什么的

`examples/<project>/inbox/` 与 `examples/<project>/outbox/` 是母体（本仓库）跟某个具体子体之间的双向通告通道。

- **outbox/**：母体写给子体的通告（"这次升级了 X，影响你那边的 Y，建议 dogfood 时关注 Z"）
- **inbox/**：子体写给母体的反馈（"我在 Y 上 dogfood 了 N 周，发现 X，建议下一轮蒸馏考虑"）

通道的设计目的：
- 让母体跟子体在 git 层面解耦（每个 repo 各自 commit 自己的 inbox/outbox 视图，不走 cross-repo merge）
- 让蒸馏 / 升级 / lesson 回流的**消息载体**有 canonical 形态，避免散落在 chat / commit message / 临时 doc 里
- 让"母体在每台机器上是一致的"这个承诺成立 — 通道是逻辑层，不依赖本机路径

## 跟 handoff/ 的边界

- [`handoff/`](../handoff/) 定义**协议本身**：installation_guide（入站 SOP）、distillation_protocol（出站 SOP）、add_new_agent_projection（待补）。这些是动作脚本。
- 本目录（`examples/<project>/inbox + outbox`）是**消息载体**。distillation_protocol 走完一步会产出一条 message，落到这里。

handoff/ 答"怎么做"，examples/<project>/ 答"做了什么、说了什么"。

## 文件命名

```
<YYYYMMDD>_<topic_slug>.md
```

- 日期是 message 写下的日期，不是事件发生日
- topic_slug 用 snake_case，要 specific 到能 disambiguate（`distill_batch_1` 而不是 `update`）
- 同一天内多条同 topic 用 `<YYYYMMDD>_<topic>_<seq>.md` 加序号

## Frontmatter Schema

每条 message 顶部带 YAML frontmatter，字段固定：

```yaml
---
direction: upstream_to_subordinate | subordinate_to_upstream
topic: <短语，跟文件名 slug 对齐>
from_commit: <写 message 时本仓库的 git HEAD short hash>
to_commit: <可选；如果 message 指向某个具体 target commit / version>
status: pending | acknowledged | applied | rejected | superseded
requires_action: true | false
created_date: YYYY-MM-DD
last_status_update: YYYY-MM-DD
related_skills: [<skill 文件名 slug 列表>]   # 可选
related_axioms: [<axiom id 列表>]            # 可选
related_messages: [<file basename 列表>]     # 可选；引用其他 message
---
```

字段语义：

| 字段 | 含义 |
|---|---|
| `direction` | 必填。母→子 vs 子→母 |
| `topic` | 必填。一句话 slug |
| `from_commit` | 必填。让对方知道这条 message 来自哪个版本，避免 message 跟实际 repo 状态错位 |
| `to_commit` | 可选。若 message 是"建议升级到 X commit"或"针对 X commit 的反馈"，填 X |
| `status` | 必填。生命周期状态机：`pending`（刚发出，未被对方处理） → `acknowledged`（对方已读但尚未行动） → `applied`（已落实）/ `rejected`（决定不做）/ `superseded`（被后续 message 替代） |
| `requires_action` | 必填。`false` 时是纯通告，`true` 时对方需在合理时间内 status 升级 |
| `created_date` / `last_status_update` | 必填。后者随 status 变化更新 |
| `related_*` | 可选。让 message 跟具体 skill / axiom / 其他 message 形成 reference 网 |

## Body 结构（推荐）

frontmatter 之下的 body 没有强约束，但建议至少回答：

1. **What** — 一两句话讲清这条 message 是什么
2. **Why now** — 为什么现在发；触发事件 / 上游 commit / dogfood 周期收口
3. **Specific items**（如果是建议清单）— 用编号列表，每条 ≤3 行
4. **Suggested action**（若 `requires_action: true`）— 具体到"读哪个文件 / 跑哪条命令 / dogfood 哪个流程"
5. **References** — 链接到 commit / 具体 skill / 具体 axiom

不要在 body 里写 editorial meta（"本次 message"、"上一轮我们提到"），按 [`bestpractice_prose_without_editorial_meta.md`](../skills/bestpractice_prose_without_editorial_meta.md) 处理。

## 生命周期

### 母体侧（本仓库）

- **outbox/**：母体写完后 commit 到母体 repo。子体下次拉母体（或人工搬运）后会看到
- **inbox/**：子体写好的 message **由人工搬运**进母体 repo（详见下文"搬运纪律"），然后母体侧 commit + 处理

### 子体侧（host repo）

子体 repo 在 `09_<agent>/handoff/` 下维护对称结构：

```
09_<agent>/handoff/
├── from_upstream/<YYYYMMDD>_<topic>.md   ← 拉自母体 outbox 的副本
└── to_upstream/<YYYYMMDD>_<topic>.md     ← 即将搬运到母体 inbox 的草稿
```

子体读完 from_upstream/ 里的 message，更新自己 frontmatter 的 status；写新的 to_upstream/ 草稿，搬运到母体 inbox 后 commit。

### 搬运纪律

不实时同步。message 是**人工或脚本搬运**的：

- 母体 outbox → 子体 from_upstream：子体 dogfood session 中由 agent 主动 pull 母体 repo 后 cp（或运行未来的搬运脚本）
- 子体 to_upstream → 母体 inbox：子体收口某个反馈后 commit 进自己 repo，再由 agent 主动复制到母体 inbox/

不走 git submodule / cross-repo automation，避免 git 冲突 + 保持每个 repo 独立可 commit。

## Status 状态机

```
pending ──(对方读到)──→ acknowledged ──(执行落实)──→ applied
   │                          │
   │                          └─(决定不做)──→ rejected
   │
   └─(被后续 message 替换)──→ superseded
```

- `pending` 与 `acknowledged` 之间转换：对方读到后**自己改自己 inbox/from_upstream 那份**的 status
- `applied` / `rejected` 转换：完成动作后改 status，并在 body 末尾加一段简短记录（"applied at commit X" / "rejected because Y"）
- `superseded`：新 message 在 frontmatter 加 `related_messages: [old_topic.md]` + `supersedes: old_topic.md`

## 维护建议

- 单条 message 控制在 ≤200 行 body。更长的内容应该是 commit / skill / doc 本身，message 只做指针
- inbox/ 和 outbox/ 不删旧 message。`status` 字段就是它们的归档机制；按时间堆积可以
- 半年累积一次后看是否需要把高密度 topic 提炼成 retrospective entry（距离已远到值得复盘时）

## 跟 retrospective / progress tracking 的关系

- **Message** = 即时通告，可以是单点 fact / single decision
- **Retrospective entry** = 周期性结构化复盘，dogfood 周期收口才写
- **Progress tracking**（host 项目内的 `designDoc/progress/` 等）= 状态聚合层

三者各自 SoT，不复制内容；引用对方时用 path + id，不 inline。message 提到某条 retrospective Item 时只 link，不 copy。
