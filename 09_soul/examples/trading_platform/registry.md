---
project_name: trading_platform
git_remote_url: https://github.com/bobaoai/trading_platform.git
agent_runtime: claude_code
entry_doc_relative: CLAUDE.md
subordinate_soul_relative: 09_soul/
subordinate_agent_projection_relative: 09_claude/
subordinate_runtime_config_relative: .claude/
last_known_upstream_commit: 4e2ba18
last_known_subordinate_commit: 6fd2961
inherited_baseline_snapshot: 2026-04-25
dogfood_status: active
relationship: bidirectional
---

# Subordinate Registry — trading_platform

trading_platform 是 Hoveath 母体当前的**首个完整子体**。本 registry 是逻辑注册卡，跨机器一致。本机物理路径在 [`../.local_paths.json`](../.local_paths.json)（gitignored）。

## 关系定义

### 母体在子体内的位置

子体 repo 内有母体的完整副本：

| 母体内容 | 子体内的位置 |
|---|---|
| portable framework | `09_soul/`（trading_platform repo 根下） |
| Claude Code 投影 | `09_claude/` |
| runtime config | `.claude/` |
| entry doc | `CLAUDE.md` |
| handoff 协议 | `09_soul/handoff/`（同步自母体） |

trading_platform 的 09_soul/ 通过 mirror_sync.py 物理 mirror 到 09_claude/，这层一致性由子体自己维护。

### 子体在母体内的位置

母体（本仓库）内对 trading_platform 的引用：

| 母体内位置 | 内容 |
|---|---|
| [`examples/trading_platform/claude_management_layer.md`](claude_management_layer.md) | 精简版参考实现（结构 + 4-layer semantic boundary） |
| [`examples/trading_platform/PROJECT_ADAPTER_trading_platform.md`](PROJECT_ADAPTER_trading_platform.md) | adapter 范例 |
| [`examples/trading_platform/registry.md`](registry.md) | 本文件：注册卡 |
| [`examples/trading_platform/inbox/`](inbox/) | 子体 → 母体反馈 |
| [`examples/trading_platform/outbox/`](outbox/) | 母体 → 子体通告 |

母体不持有子体业务内容（trading 策略 / 持仓 / Fed 视角等）；那些只在子体本地。

## 继承的 baseline 摘要

trading_platform 当前继承的母体内容（截至 `last_known_upstream_commit`）：

- **Philosophy 层**：FP（7 条）+ 52 axioms（A01-A20、T01-T11、M01-M10、V01-V05、X01-X06）。子体内编号与母体一致（a17 reconciliation 已处理：母体 a17_five_walls / a18 / a19 保留，子体 a17_reader_persona_primacy 在母体重编号为 a20）
- **Identity 层**：SOUL / COMMUNICATION / USER 三个 mirror（addendum_marker = `## Project-Specific Addendum`）
- **Execution 层 baseline skill**：14 个 baseline mirror（含本轮新增的 prose_without_editorial_meta + prompt_boundary）
- **Bridging 层**：mirror_sync.py + manifest schema
- **Handoff 层**：installation_guide.md + distillation_protocol.md（add_new_agent_projection 仍待补）

子体特有（不属于母体管辖）：
- `09_claude/rules/`（10 条 task-scoped rule，部分已完成蒸馏批次 1 = 5 条）
- `09_claude/routing/task_mainlines.md` + `overlay_rules.md`
- `.claude/skills/` 下的 23 个业务 skill（trading / theme / portfolio / Fed-watcher 等）
- `designDoc/` 全部内容（架构与设计真相）

## 蒸馏 / 反吐通道

按 [`../../handoff/distillation_protocol.md`](../../handoff/distillation_protocol.md) 走完一轮蒸馏后，相关消息落到本目录：

- 母体侧通告（"刚升级了 X"）→ [`outbox/`](outbox/)
- 子体侧反馈（"X 在 dogfood 中发现 Y"）→ [`inbox/`](inbox/)（由人工搬运自子体 `09_<agent>/handoff/to_upstream/`）

message 格式见 [`../MESSAGE_FORMAT.md`](../MESSAGE_FORMAT.md)。

## 已知 drift

蒸馏过程中发现的母 vs 子 drift（未来需 reconcile）：

| Drift 项 | 状态 |
|---|---|
| `axioms/v02_verifiability.md` 母体含 §2.8 语义漂移 + 引用 A17/A18/A19；子体仍是旧版 | pending — 子体下一次 mirror_sync --apply 会自动同步 |
| `axioms/INDEX.md` 母体含 FP / Doc Authoring 群 / A20 / T11 / 53 总数；子体仍是 50 总数旧版 | pending — 子体下一次 mirror_sync --apply 会自动同步 |
| 母体 `bridging/mirror_manifest.json` 是空 schema；子体管理 56 条 mirror（自身的 09_soul→09_claude 投影） | by-design — 每个 repo 自己 manifest，不共享 |
| 母体新增的 5 个蒸馏成果（prose_without_editorial_meta / prompt_boundary / skill_writing 升级 / retrospective_writing 升级 / reader_state 升级） | pending — 子体未拉取；待 outbox/<batch_1> message 触发同步 |

## 同步建议

子体下次 sync 母体的步骤（手动）：

1. 在子体内 `git fetch && git pull` 它自己 — 这是子体 repo 自身的 commit history
2. 把母体 [`09_soul/`](../../) 的最新内容覆盖到子体的 `09_soul/`（注意保留子体特化部分；core 层用 mirror_sync addendum 机制保护，axioms / skills 直接覆盖）
3. 在子体 repo 内跑 `python 09_soul/bridging/mirror_sync.py --apply` 把新 source 重写到 09_claude/ 投影
4. 子体跑一次 dogfood（命中至少一条新 skill），验证升级正确
5. 子体把发现写到 `09_<agent>/handoff/to_upstream/<YYYYMMDD>_<topic>.md`
6. 拉到母体 [`inbox/`](inbox/) commit

## 维护协议

- `last_known_upstream_commit` / `last_known_subordinate_commit` 在每轮蒸馏 / 同步周期收口时手动更新
- `inherited_baseline_snapshot` 用日期记录最近一次完整 baseline 同步
- `dogfood_status: active` 表示子体正在产生 lesson；`paused` 表示暂时不 dogfood；`sunset` 表示项目结束（届时本 registry 移到 `examples/_archive/`）
