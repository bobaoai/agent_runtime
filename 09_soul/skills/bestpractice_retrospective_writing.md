# Retrospective Writing（dogfood 周期复盘的 canonical 写作路径）

## 元数据

- **类型**：BestPractice
- **适用场景**：多阶段 harness pipeline（critic pipeline / polish loop / report 端到端等）走完一次完整 dogfood 周期，且用户问「下次怎么改进」/「lesson」/「retrospective」时
- **创建日期**：2026-04-25
- **来源**：从 `09_claude/core/COMMUNICATION.md §Retrospective 触发` 抽出，泛化为跨项目 portable skill

---

## 这个文件是干什么的

定义 retrospective 的写作触发与落档协议，避免：
- 在 `designDoc/temp/` 或会话中凭空开写，绕过 retrospective 体系
- follow-up 死在旧 retrospective doc 里，下次执行相关 skill / 代码时没人回头读
- 把日常任务完成、单 bug 修、单 skill 小改误升级为 retrospective

层归属：Execution 层 best-practice template（跨项目 portable）。具体路径与 INDEX 位置由项目 PROJECT_ADAPTER 提供。

---

## 触发条件

下列**全部满足**才触发：

1. 多阶段 harness pipeline 走完**一次完整 dogfood 周期**（不是单步、不是 partial）
2. 用户**显式**问「下次怎么改进」/「lesson」/「retrospective」/「复盘」
3. 周期内确实出现了 ≥1 个值得记录的判断点 / 失败点 / 升级机会

不触发：
- 单次 bug 修
- 单 skill 小改
- 日常任务完成（即使用户随口说「下次注意」）
- 用户没显式 ask retrospective 时主动写

---

## 协议

### Step 1：先读 INDEX

进入 `<retrospective_dir>/INDEX.md`（路径由项目 PROJECT_ADAPTER 提供）。INDEX 顶部维护当前 retrospective 的 convention：
- schema（必填字段、format）
- validation kind（哪些 retrospective entry 可被 dogfood 验收）
- follow_up_trigger 机制（每条 follow-up 怎么挂回执行点）

INDEX 底部是按时间倒序的 entries 列表。

### Step 2：按 convention 写新 entry

文件名约定：`<retrospective_dir>/<YYYYMMDD>_<topic>.md`（日期是写 retrospective 的日期，不是 dogfood 第一天的日期）。topic 要 specific 到能 disambiguate — `critic_pipeline` / `polish_loop` / `theme_report_e2e`，不是 `general` / `improvements`。

按 INDEX 顶部声明的 schema 填字段。**不**自创格式。如果 schema 不够用，先改 INDEX schema 再写 entry，不要在 entry 里偷加字段。

#### Item Schema（推荐 baseline）

每个 Item 至少带这三个字段。这套 schema 的作用是逼自己诚实区分"我以为做完了"和"还在等什么证据才算真做完"，避免下一轮 retrospective 误以为已经验证过：

| 字段 | 取值 | 作用 |
|---|---|---|
| `status` | `done` / `deferred` / `backlog` | 这一轮是执行了还是推后了 |
| `validation` | `deterministic` / `next_dogfood` / `cross_instance` / `architectural` | 下一个 retrospective 作者（往往是未来的自己）用什么证据判定这条 Item 真的"做完了" |
| `follow_up_trigger` | `kind: <event>, value: <具体事件>` | 关闭 validation 闭环的具体事件，**不是**"下次 session"，而是命名事件（下次 critic 跑 / 下次某个 skill 被调用 / 仓库 grep 某条件 / schema 改动） |

#### Validation Kind 决策

- **`deterministic`** — 现在就能对仓库 / DB / 文件状态验证完。不需要新输入。例：新增的 reviewer 检查项，是对 JSON schema 的 read，立即可对已知 fail / pass 的样本跑一遍
- **`next_dogfood`** — 需要一个新的真实实例才能看出升级是否真的提升了 reader gain。例：新增的 reader-end-state section，只有下一次完整周期才能看出 downstream agent 是否真的停在 declared end-state
- **`cross_instance`** — 需要 N 次观察才能看出升级是否泛化。例：attack log 上新加的 severity 字段，得在多次 critic 跑后才能判断 calibration 是否合理
- **`architectural`** — 结构性决策，工程意义上不存在 hypothesis 验证。例：schema 拆分本身是设计选择，不是假设

#### Follow-up Trigger 落点（关键）

Trigger 写在 retrospective doc 里会自死。**真正的 reminder 必须 inline 到下次执行时会自然读到的位置** — 被改的 skill 顶部、被改的代码段旁、被改的 contract section 内。retrospective Item 只 point 到 trace 的位置，不承担 reminder 的传播。

trace 的标准形态：

```
_Added <YYYY-MM-DD> during <retrospective_topic> retrospective. Validation
pending: first time this <check / section / field> fires in a real run, update
<retrospective_path> Item <N> status to close the loop._
```

这是让 convention 跨 session follow-through 的机制 — 不依赖记忆，依赖被改 artifact 自身把 reminder 带进下一次执行。

### Step 3：回 INDEX 加索引行

新 entry 写完后回 INDEX 底部加一行，按时间倒序插入。索引行通常包含：
- 日期
- topic 简述
- entry 文件路径

### Step 4：follow-up inline 到执行点

retrospective 里每个 Item 若涉及某个 skill / 某段代码 / 某个 doc 的升级，**follow-up 提醒必须 inline 到那个 skill / 代码 / doc 的位置**，不能只挂在 retrospective doc 里。

否则下次执行那段 skill / 代码时没人会回头读 retrospective，follow-up 死在旧文件里。

inline 形态：在被改的 skill / 代码 / doc 顶部或对应段落加一行 `<!-- follow-up from <retrospective_path>: <一句话提醒> -->`，或者用项目约定的 follow-up tag（具体语法由 INDEX schema 规定）。

### Step 5：按共享写作契约验收正文

Retrospective 是给未来执行者和 reviewer 读的稳定 artifact。交付前调用 `workflow_internal_writing.md` 与 `bestpractice_doc_self_review.md`，并用缺陷极性检查术语顺序、段落三问、翻译腔或教材声、概念负荷、跨段连续推理。

Retrospective 的 protected meaning 还包括：Item 的 `status`、`validation` 类型、`follow_up_trigger`、日期、已验证与待验证的边界，以及失败到 lesson 的因果方向。为求顺畅而改动这些字段或把“待 dogfood”写成“已验证”，必须退回 retrospective owner，不属于 prose 修订。

---

## 验收

retrospective 算写完，必须满足：

- 文件按 `<YYYYMMDD>_<topic>.md` 命名落到 `<retrospective_dir>/`
- INDEX 索引行已加，时间倒序正确
- 至少 1 条 follow-up inline 到对应执行点（如果该轮真有升级机会；零升级机会的 retrospective 通常不该写）
- entry 内容遵守 INDEX 顶部声明的 schema
- 正文已通过共享写作契约，且 prose 修改没有改变状态、验证类型、触发条件、日期或因果 lesson

---

## 不允许的捷径

| 禁止式 | 检测式（无声违反时长什么样） |
|---|---|
| 不允许跳过 INDEX 直接开写 | retrospective 文件存在但 INDEX 没索引行 |
| 不允许在 `designDoc/temp/` 或会话内 inline 写 retrospective 内容 | dogfood 周期走完用户问 lesson，得到一段 inline 长文回复但没文件落档 |
| 不允许 follow-up 只挂 retrospective doc | retrospective doc 写了 N 条 Item，但被引用的 skill / 代码 / doc 完全没改也没 follow-up tag |
| 不允许自创 schema | entry 字段跟 INDEX 顶部声明不匹配 |
| 不允许把日常任务完成升级为 retrospective | INDEX 中累积大量 trivial entry（单 bug 修、单 skill 小改） |

---

## 项目侧需提供

各项目 PROJECT_ADAPTER 必须声明：
- `<retrospective_dir>` 实际路径（如 `designDoc/retrospectives/`）
- INDEX 文件路径
- 项目内 follow-up tag 语法（如果跟通用 `<!-- follow-up ... -->` 不同）
- 是否有平行的 progress tracking 系统（如 `designDoc/progress/`）；如果有，retrospective Items 是各自 SoT，progress 系统**只引用 id + path，不复制内容**
- 是否有 Learning Library（外部阅读综合）会贡献 Item；如果有，那些 Item 带 `provenance:` 字段指回 LL 文件，LL 文件回带 `retrospective_cross_ref:`

---

## 何时回读本文件

- 多阶段 harness pipeline 周期收口、用户显式问 retrospective 时
- 写新 retrospective 前确认协议
- 设计新项目的 retrospective 体系时（参考触发条件 / 协议 / 验收）
- 修改 retrospective 体系自身时回看本 skill 是否需要同步

---

**最后更新**：2026-04-25
