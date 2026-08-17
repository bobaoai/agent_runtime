---
direction: upstream_to_subordinate
topic: distill_batch_1
from_commit: 4e2ba18
status: acknowledged
requires_action: true
created_date: 2026-04-25
last_status_update: 2026-04-25
related_skills:
  - bestpractice_prose_without_editorial_meta
  - bestpractice_prompt_boundary
  - bestpractice_skill_writing
  - bestpractice_retrospective_writing
  - bestpractice_reader_state_and_judgment_gain
related_axioms:
  - A20
  - T11
  - FP
---

# Distill Batch 1：Tier S 蒸馏落地通告

## What

母体完成了第一轮 distillation 窗口的 Tier S 5 项蒸馏，全部落地到 `09_soul/skills/`。新增 2 个 portable skill，升级 3 个已有 skill。建议 trading_platform 在下次合适窗口拉取并 dogfood 一遍。

## Why now

trading_platform 在 2026-04-24 完成 critic_pipeline 第一次完整 dogfood + retrospective。母体把这次复盘里凡是 portable shape 的 lesson 蒸馏成跨项目 skill，目的是让下一个子体（以及 trading_platform 自身后续 dogfood）能直接消费成果，而不是每次重新发明。

## Specific items（5 项）

### 1. 新增：`bestpractice_prose_without_editorial_meta.md`

- **来源**：trading_platform `09_claude/rules/35_pm_writing_contract.md` §3
- **内容**：sentence-level 守门 — reader-facing 报告 + stable AI-facing artifact 都禁止 editorial meta / conversation attribution / workflow time-window deictics / rule invocation as endorsement
- **trading_platform 影响**：35_pm_writing_contract.md §3 可考虑改为 pointer 指向母体 skill，body 留 PM-specific 例子；保留 trading 具体反模式作为 example，不重复 portable 内容

### 2. 新增：`bestpractice_prompt_boundary.md`

- **来源**：trading_platform `09_claude/rules/31_prompt_boundary_task_vs_control_plane.md`
- **内容**：A14 的 canonical 操作路径 — task-plane 6 类保留 / control-plane 6 类删除清单 + bad/good 例子 + 与 A19 的关系
- **trading_platform 影响**：31_prompt_boundary_task_vs_control_plane.md 可改为 pointer，保留任何 trading-specific 的 prompt-边界范例（如 critic pipeline subagent prompt）作为 example

### 3. 升级：`bestpractice_skill_writing.md` 原则三

- **来源**：trading_platform `09_claude/rules/30_ai_facing_docs_detail_first.md`
- **内容**：原则三从泛泛"先讲清 contract"扩展为 6 个必答问题（layer 用途/进入条件/first authority/产出/handoff/邻近易混淆概念）+ 3 类 anti-pattern + "删一段后 agent 能否执行"检测
- **trading_platform 影响**：30_ai_facing_docs_detail_first.md 可改为 pointer，保留任何 trading-specific 的 detail-first 范例

### 4. 升级：`bestpractice_retrospective_writing.md` Item Schema

- **来源**：trading_platform `designDoc/retrospectives/INDEX.md`
- **内容**：加 Item schema baseline（`status` / `validation: deterministic|next_dogfood|cross_instance|architectural` / `follow_up_trigger`）+ Validation Kind 决策 + Follow-up Trigger 落点（必须 inline 到 skill / code 旁的 trace comment）
- **trading_platform 影响**：retrospectives/INDEX.md 可保持原状（已经是 trading 自己用的），但下次新建项目用 retrospective 时可直接复用母体 skill 而不重写 schema

### 5. 升级：`bestpractice_reader_state_and_judgment_gain.md` Multi-Agent Handoff

- **来源**：trading_platform `designDoc/retrospectives/critic_pipeline_20260424.md` §1 + §2.4
- **内容**：新增第四层 "Multi-Agent Handoff Reader-Gain 化" — vehicle-shaped vs reader-gain-shaped 对照表 + 5 类 critic-pipeline handoff 反模式（confidence/severity 解耦、root_cause_cluster_id、reader_gain_after_patch、location_grep_verified、role tagging）+ pipeline-level 5 项 checklist
- **trading_platform 影响**：critic pipeline 5 个 SKILL（debater / reviewer / maintainer / owner / writer-handoff）可参考母体 skill 第四层重新审视各自 handoff contract，看是否还有 vehicle-shaped 字段未升级

## Suggested action

1. 在子体 dogfood window 找一个合适时机（不要打断当前 critic pipeline run）
2. `git pull` 母体 (`https://github.com/bobaoai/Hoveath.git` HEAD = `4e2ba18`) → 把 09_soul/ 整个覆盖到子体 09_soul/，注意保留子体 mirror_manifest（母体的 manifest 是空 schema，不要覆盖子体的 56 条 mirror 注册）
3. 子体跑 `python 09_soul/bridging/mirror_sync.py --apply`，让 09_claude/core / 09_claude/axioms / 09_claude/skills 全部同步到新 source
4. 至少 dogfood 1 个流程命中新 skill 或新升级的 section（建议 critic pipeline 下一次 run 直接试 §2.5 的 vehicle-shaped 检查清单）
5. dogfood 完成后写反馈到子体 `09_<agent>/handoff/to_upstream/<YYYYMMDD>_distill_batch_1_feedback.md`，搬运到母体 `examples/trading_platform/inbox/` 让母体闭环

完成后把本 message 的 status 改为 `applied` 并在 body 末尾加一句完成记录。

## References

- 母体本轮蒸馏的源 commit：`4e2ba18`（写本 message 时的 HEAD）
- 5 项的母体源文件：[`09_soul/skills/bestpractice_prose_without_editorial_meta.md`](../../../skills/bestpractice_prose_without_editorial_meta.md) / [`09_soul/skills/bestpractice_prompt_boundary.md`](../../../skills/bestpractice_prompt_boundary.md) / [`09_soul/skills/bestpractice_skill_writing.md`](../../../skills/bestpractice_skill_writing.md) §原则三 / [`09_soul/skills/bestpractice_retrospective_writing.md`](../../../skills/bestpractice_retrospective_writing.md) §Step 2 / [`09_soul/skills/bestpractice_reader_state_and_judgment_gain.md`](../../../skills/bestpractice_reader_state_and_judgment_gain.md) §Multi-Agent Handoff
- 子体侧 source 文件（这次蒸馏的输入）：trading_platform `09_claude/rules/30 / 31 / 35`、`designDoc/retrospectives/INDEX.md`、`designDoc/retrospectives/critic_pipeline_20260424.md`
- 蒸馏候选完整清单（含未蒸馏的 Tier A / Tier B）：见母体本轮 chat 蒸馏 proposal section（暂未落档；下次 distillation 窗口前应蒸馏成 `handoff/` 内的 backlog 文件）
