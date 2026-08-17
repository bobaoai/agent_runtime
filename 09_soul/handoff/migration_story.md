# Migration Story：从 09_soul 文件夹到 Claude Code 工作区

## 这份 doc 是干什么的

这是一个**实战记叙 + 设计案例**：Hoveath 是怎么从一个朴素的 `09_soul/` 文件夹，演化为一个能装进 Claude Code、能管理子体、能反向蒸馏 lesson 的可插拔系统的。

读完应能判断：

- 一个朴素的 portable doc 文件夹，要成长为完整 agent runtime 主投影，需要解决哪几个具体问题
- 哪些选择对、哪些回头看可以更早做
- 自己的项目要套用同一套路径时，前置条件是什么

读者：想把 Hoveath 装进自己 host 项目的人；想用类似思路做自家 portable layer 的人。

如果你是这两类读者之一：先读 `[installation_guide.md](installation_guide.md)` 拿动作清单，再读本文知道每一步**为什么**长成现在的样子。

---

## 起点：一个 09_soul 文件夹

[grapeot 的 context-infrastructure](https://github.com/grapeot/context-infrastructure) 是源头。它的承诺很朴素：

- `rules/SOUL.md` + `USER.md` + `COMMUNICATION.md`：人格 / 用户 / 沟通风格
- `rules/axioms/`：47 条决策原则
- `rules/skills/`：20+ 个可复用工作流t
- `tools/`：邮件 / 语义搜索 / 报告分享等业务工具
- `contexts/` + `periodic_jobs/`：记忆系统骨架

这一层的设计目标是「跨项目 portable」。想法：把同一份 `09_soul/` drop 进任何 workspace，让 Cursor 读它，AI 就能保持稳定 voice、判断方法和工作风格。

### 起点的局限

朴素文件夹形态遇到三个问题：

1. **没有 agent runtime 主投影**。Cursor 通过 `.cursor/rules/` 加载 `09_soul/`，但当用户切到 Claude Code / OpenCode / Codex 时，每个 runtime 要重新写一遍触发协议。语义层（"你是谁、用户是谁、风格是什么"）和 runtime 层（"在 Claude Code 怎么加载它"）混在一起。
2. **没有项目特化的对接面**。`09_soul/` 是 portable 的，但具体项目（trading 平台 / 课程 repo / consulting workspace）有自己的工作面、术语、tripwire。两者怎么对接没有协议；用户每装一个新项目都重新发明衔接方式。
3. **没有 lesson 回流通道**。在某个项目里 dogfood 出来的 portable lesson（比如"不要在 prompt 里塞 control-plane 信息"）想升级回 `09_soul/`，没有 SOP。lesson 死在项目里。

很多人觉得「把这种 portable 框架装进我自己的项目会很难」，难在哪里？难在第 1、2、3 点全都开放，每个用户重新发明 — 这才是真正的成本。

---

## 我们的路径

整个迁移分四步走，每一步都先解决一个上面的限制。

### 第一步：母体完整化

把朴素 `09_soul/` 提升为「框架仓库」(母仓库 = `Hoveath/`)，让它有自己的稳定身份：

- **加 Philosophy 层** — FP first principles 升为最高优先级 axiom，跨项目跨 runtime 稳定
- **加 4 层语义边界** — Philosophy（axioms）/ Identity（SOUL/USER/COMMUNICATION）/ Working Environment（rules + routing）/ Execution（skills + tools）。每层都有清楚的 portability 标签：跨项目 vs 项目特化
- **加 reconciliation 协议** — 多个 fork 演化出来的 axiom 编号会冲突；母体规定「先到先得，后到者重编号 + frontmatter 加 conflict note」（参见 a20_reader_persona_primacy 的 frontmatter 范例）

**前置条件**：至少要有一个项目（trading_platform）已经在朴素 `09_soul/` 上 dogfood 过 ≥1 周，产生过真实的 fork drift 与 lesson 才有素材完整化母体。

**回头看的判断**：母体完整化不能比第一个真实子体早。先有 dogfood，才有母体；不是反过来。

### 第二步：Claude Code 主投影（Phase 1-5 + Phase 6 sunset）

母体自己装一份 Claude Code runtime，证明「装 Hoveath」这件事是可重复的。

按 `[installation_guide.md](installation_guide.md)` 的 Phase 1-7 SOP：


| Phase              | 落地物                                                                                                     | 验收                                           |
| ------------------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| 1 — Identity core  | `CLAUDE.md`（Session Startup + FP + 13 R-rules）+ `09_claude/core/`（4 文件 mirror）+ `.claude/settings.json` | mirror_sync `--check` 退出码 0                  |
| 2 — Routing        | `09_claude/routing/task_mainlines.md`（12 主线 + overlay + package-review）                                 | 信号词命中正确 mainline                             |
| 3 — Rules          | `09_claude/rules/INDEX.md`（13 R-rules 分组、portability 标签、加载时机）                                           | task 命中按 INDEX 加载对应 rule                     |
| 4 — Axiom mirror   | 54 个 axiom 进 manifest，物理 mirror 到 `09_claude/axioms/`                                                   | manifest 全 in-sync                           |
| 5 — Skill baseline | 12 个 best-practice / workflow skill mirror 到 `09_claude/skills/`                                        | routing 表里每个 mainline 的 downstream skill 都到位 |
| 6 — 旧投影 sunset     | `.cursor/rules/*.mdc` 改 `alwaysApply: false` + body 改 sunset 指针；`AGENTS.md` 重写为 runtime 状态表             | 旧目录零新增 rule，`grep alwaysApply: true` 无匹配     |


第 6 步特别值得说一句：**不要直接删旧投影目录**。任何用 Cursor 打开本仓的人都需要看到「主投影迁了，但仍可读 portable 层」的指针。删除 = 用户看不到迁移；改 sunset = 用户被引导。

### 第三步：反向蒸馏（distillation_protocol）

`trading_platform` 项目跑了一次 critic_pipeline 完整 dogfood，产出一份 retrospective + 升级了若干本地 rule。这些本地 lesson 要不要升 `09_soul/`？

`[distillation_protocol.md](distillation_protocol.md)` 把这件事变成有 SOP 的动作：

1. **S0 前置门**：项目主投影 dogfood ≥1 周（避免把还在迭代的中间态固化）
2. **S1 reconciliation**：母体 ↔ 项目双向同步，先解决 axiom 编号冲突等基础 drift
3. **S2-S6 蒸馏**：按 Layer 顺序（Philosophy → Identity → Rules/Routing → Skills → entry doc → settings）抽取 portable 形态
4. **Tier 分级**：
  - **Tier S**：portable shape 已经在项目 dogfood 验证 ≥1 周，可直接蒸馏
  - **Tier A**：portable 但需要抽象工作（剥离项目特定术语）
  - **Tier B**：留 examples/ 即可，不进 portable 层

我们对 trading_platform 的第一轮蒸馏摘了 5 个 Tier S：


| 来源                                                     | 蒸馏成                                                                      |
| ------------------------------------------------------ | ------------------------------------------------------------------------ |
| `09_claude/rules/35_pm_writing_contract.md` §3         | `bestpractice_prose_without_editorial_meta.md`（新）                        |
| `09_claude/rules/31_prompt_boundary_*.md`              | `bestpractice_prompt_boundary.md`（新，A14 canonical 操作路径）                  |
| `09_claude/rules/30_ai_facing_docs_detail_first.md`    | `bestpractice_skill_writing.md` 原则三（升级）                                  |
| `designDoc/retrospectives/INDEX.md`                    | `bestpractice_retrospective_writing.md` Item schema（升级）                  |
| `designDoc/retrospectives/critic_pipeline_20260424.md` | `bestpractice_reader_state_and_judgment_gain.md` Multi-Agent Handoff（升级） |


第二轮蒸馏（这份 doc 落地的同一会话）从 `[grapeot/context-infrastructure](https://github.com/grapeot/context-infrastructure)` 上游摘了一个 `bestpractice_chinese_writing_voice.md`（7 层中文 voice 守则，源是 grapeot 长期高产中文写作的反模式提炼）。

**这一步证明**：portable lesson 的回流不是手工拷贝，是有协议的；下一个项目 dogfood 出来的 lesson 走同一条路径就能升 `09_soul/`。

### 第四步：子体管理层

到这里母体能装、能蒸馏，但还差最后一件事：**母体怎么追踪它管的多个子体**。

trading_platform 是首个子体。下一个项目（assume：某个写作 / consulting / 课程 repo）会是第二个。母体得知道：

- 哪些子体在被它管
- 每个子体当前继承到哪个 baseline
- 子体反馈怎么回到母体
- 母体升级怎么通告给子体
- 跨机器（laptop / desktop / cloud）怎么保持母体一致

我们的设计：

- **handoff/** 答"怎么做"（installation / distillation / add_new_agent_projection 协议）
- **examples/** 答"哪些子体在被管、它们说了什么、母体回了什么"
- **examples/****/registry.md** = 注册卡（git_remote_url / agent_runtime / inherited_baseline / 已知 drift / 同步建议）
- **examples/****/inbox + outbox** = 双向 message 通道
- **examples/.local_paths.json**（gitignored）= 本机特定路径，让母体跨机器一致 / 子体物理路径每机器各异
- **examples/MESSAGE_FORMAT.md** = message frontmatter schema + 状态机（pending → acknowledged → applied|rejected|superseded）

关键约束：**不实时同步、不走 git submodule**。每个 repo 各自 commit 自己的视图，message 是人工或脚本搬运的。这样跨 repo 的 git 不会冲突，子体作者保留对自己 repo 的完全控制权。

第一条真实 message 已经发出（[examples/trading_platform/outbox/20260425_distill_batch_1.md](../examples/trading_platform/outbox/20260425_distill_batch_1.md)），通告 trading_platform：母体刚摘了 5 项 Tier S 蒸馏，建议下次 dogfood 时拉取并 dogfood 一遍。子体侧已读到（status 升到 `acknowledged`），等下一轮 dogfood 周期闭环。

---

## 关键设计决策（事后总结）

四步路径里几个值得单独抽出来的判断：

### 1. 4 层语义边界先于一切

Philosophy / Identity / Working Environment / Execution。每个 artifact 进 09_soul 前先回答：

- 它是跨项目稳定的吗（跨 → Philosophy / Identity）
- 它是跨 agent runtime 稳定的吗（跨 → Philosophy）
- 它绑定具体工作面吗（绑 → Working Environment，留项目，不进 09_soul）
- 它是 best-practice 还是业务工具（best-practice → Execution 跨项目；业务 → 留项目）

这一层判断如果不做，09_soul 会膨胀进项目特定术语，下个项目继承时被迫吞污染。

### 2. mirror_sync 的零 metadata 物理 mirror

`bridging/mirror_sync.py` 把 `09_soul/core/*` 同步到 `09_<agent>/core/*`。两个原则：

- **零 metadata 进文档**：mirror 文件就是 source 内容 + 可选 project-local addendum。每会话首条 message 都要读 SOUL/USER/COMMUNICATION，对 token 敏感
- **外置 manifest**：所有 sync 状态进 `bridging/mirror_manifest.json`，文档保持纯净。drift 检测、source_hash、last_synced 都在 manifest，不污染 mirror 文件

这个选择的意义在于：用户读 09_claude/core/SOUL.md 时看到的就是 09_soul/core/SOUL.md 的内容，没有「mirror 自 X commit」「last synced YYYY-MM-DD」之类的元数据骚扰。一致性由 `mirror_sync --check` 保证，不靠人工记忆。

### 3. handoff vs examples 的边界

最初想把"子体管理"塞进 handoff/ 作为新 layer。被拒了。

理由：handoff/ 是**操作协议**（installation / distillation / add_new_agent — 都是动作脚本），examples/ 是**实例 + 通道**（哪些子体、说了什么）。两个是不同语义层；混进一个目录会让用户分不清"看协议"和"看子体状态"。

最终落点：handoff/ 加一段指针指向 examples//inbox + outbox，body 不重复；examples/ 升级为双职责（参考实现 + 子体管理）。

### 4. 母体跨机器一致 / 子体路径本地化

约束："以后母体在每一台机器上要求是一样的，但母体管的子体会通过母体跟 git 连接"。

实现：

- registry.md frontmatter 用 `git_remote_url` 标识子体（机器无关）
- `.local_paths.json` 装机器特定的物理路径（gitignored）
- `.local_paths.json.template`（committed）让新机器知道格式

效果：母体 clone 到任何机器都一样；子体的物理路径在各自机器上由 `.local_paths.json` 解析。

---

## 验收（数字快照，2026-04-25）


| 维度                                       | 状态                                                                                  |
| ---------------------------------------- | ----------------------------------------------------------------------------------- |
| Axioms 总数                                | 53（FP + 20A + 11T + 10M + 5V + 6X）                                                  |
| Skills 总数（09_soul/skills/）               | 31（含本轮新增的 chinese_writing_voice）                                                    |
| Skill baseline mirror（09_claude/skills/） | 15                                                                                  |
| 总 mirror 数（manifest in-sync）             | 72 / 0 drift / 0 missing-source                                                     |
| Claude Code 主投影                          | Phase 1-6 完成                                                                        |
| 已注册子体                                    | 1（trading_platform，dogfood active）                                                  |
| 已发出 outbox message                       | 1（distill_batch_1，status=acknowledged）                                              |
| 已收到 inbox 反馈                             | 0（等子体下一轮 dogfood 闭环）                                                                |
| 母体 commit 数                              | 4（initial + basics update + distill+management + claude projection + cursor sunset） |


---

## 经验教训

### 这次走对的几件事

- **先有 dogfood，再升 portable**：所有进 `09_soul/` 的内容都来自至少一个项目的 ≥1 周 dogfood。没 dogfood 不抽象
- **portability 标签先行**：每条 R-rule、每个 skill 在写出来时就带 `<portable>` / `<portable-shape>` / `<project>`，蒸馏时按标签直接分类
- **零 metadata 物理 mirror**：让 token-sensitive 的 identity 文件保持纯净
- **不实时同步双向 message**：每个 repo 各自 commit，git 不冲突，子体作者保留控制权
- **跨机器一致约束放最后**：先把母体语义做对，再处理「换台机器跑」的工程问题；如果把"跨机器"放第一位，会过早抽象出 abstraction layer

### 回头看可以更早做的

- **handoff/distillation_protocol** 写得太晚。第一次蒸馏（trading_platform → 母体）几乎是手工跑的，事后才补 SOP。下次第二个子体接入前应当先有协议
- **examples/ 双职责**升级也是事后补的。最初 examples/ 只是"参考实现"，子体管理需求出现后才扩职责。如果一开始就规划，可以省掉一次 README 重写
- `**templates/`** 仍是空。distillation_protocol 提到的 templates v0.1 至今没产出；下一个子体接入时会感觉到这个 gap（要重新发明 entry-doc 骨架）

### 还没做的

- `handoff/add_new_agent_projection.md`（OpenCode / Codex 等非 Claude runtime 的接入协议）— 待补
- `templates/` v0.1 — 待蒸馏到 round-trip validation 通过
- 第二个子体的注册 — 验证 registry 是否真的 generic（trading_platform 是首例，不是回归测试）

---

## 给想用同一套路径的人

如果你想把 Hoveath（或类似的 portable layer）装进自己的项目：

1. **不要从空白开始**。Hoveath 母仓库是公开的，先 clone 它，再按 `[installation_guide.md](installation_guide.md)` 装到你自己的 host 项目。先抄成熟的，再本地化
2. **本地化只动 4 件**：`USER.md` / `PROJECT_ADAPTER.md` / `<entry-doc>` / `<runtime-config>`。其他不动，让 mirror_sync 维护一致性
3. **dogfood ≥1 周再考虑蒸馏**。第一周写出来的 lesson 大多还是项目特定，过早升 09_soul/ 等于污染
4. **每条 lesson 进 09_soul 前问 4 个问题**（来自 `core/DEVELOPMENT_INTEGRATION.md`）：
  - 它在多个任务里重复出现过吗
  - 它能脱离这个 repo 的路径布局活下来吗
  - 它是关于判断 / 沟通 / 计划 / 操作方法吗
  - 另一个 host 项目继承它时会不需要重写吗
   四个 yes 才升。任何一个 weak，留项目本地。
5. **建立母体 ↔ 子体 message 通道**：从一开始就用 `examples/<your-project>/inbox + outbox` 记录双向通告。lesson 会自然累积成可复用资产

如果你做完这些，你会发现「装 Hoveath 进项目」不难。难的是**第一次没有协议时的盲走**。本 doc + handoff/ + examples/ 的存在就是把第一次盲走的成本前置消化掉了。

---

## 下一个版本的形态（v1 → v2）

这一版（v1）解决了"装得进 / 能蒸馏 / 能管子体"。v2 想解决的是：

- **Templates v0.1**：让新项目 install 不依赖手动看 examples 抄，直接从 templates 填 placeholder
- **第二个子体（非 trading 领域）**：验证 portability 是否真泛化
- **跨 agent runtime**（OpenCode / Codex）：让 Hoveath 不绑死在 Claude Code
- **Auto-distillation tooling**：把"扫 retrospective + 抽 portable lesson"半自动化（当前是人工）

每一步都还需要至少一个真实 dogfood 周期才能稳定。继续按 distillation_protocol 跑。

---

## Provenance

- 起源代码：[grapeot/context-infrastructure](https://github.com/grapeot/context-infrastructure)（朴素 portable doc 文件夹形态）
- 中间节点：trading_platform fork 的 09_soul/，2026-03 → 2026-04 dogfood 数周后产生 critic_pipeline retrospective + 多条本地 R-rule
- 当前节点：母仓库 [bobaoai/Hoveath](https://github.com/bobaoai/Hoveath)，截至 2026-04-25 完成本 doc 描述的四步路径
- 相关协议：`[installation_guide.md](installation_guide.md)` / `[distillation_protocol.md](distillation_protocol.md)` / `[../examples/MESSAGE_FORMAT.md](../examples/MESSAGE_FORMAT.md)`
- 第一条蒸馏 message：`[../examples/trading_platform/outbox/20260425_distill_batch_1.md](../examples/trading_platform/outbox/20260425_distill_batch_1.md)`

---

**最后更新**：2026-04-25