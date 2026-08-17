# 09_soul：Hoveath Portable Pluggable System

`09_soul/` 是 Hoveath 的 portable digital self，组织成一个可插拔系统：drop 整个 `09_soul/` 进任何 workspace，按 [`handoff/installation_guide.md`](handoff/) 走一遍 bootstrap，就能让该 workspace 拥有：
- 跨项目稳定的判断方法（Philosophy）
- 跨项目稳定的角色 / 沟通风格 / 用户偏好（Identity）
- 跨项目稳定的执行 best-practice（Execution）
- 跨项目可复用的顶层治理底座（Governance）
- 同步 source ↔ agent 投影的工具（Bridging）
- 项目 ↔ Hoveath upstream 的交接协议（Handoff）
- 真实项目的参考实现（Examples）

Hoveath 是被这一层承载的数字自我；09_soul 是承载的代码 / 文档容器。

## 当前形态：Claude Code 优先

Hoveath 在 agent runtime 选择上保持 agnostic，但当前主投影是 Claude Code（`09_claude/` 是首个完整投影）。Cursor 投影（`.cursor/`）已退居辅助、冻结状态。新增 agent 投影（如 09_codex/）按 [`handoff/add_new_agent_projection.md`](handoff/) 协议建立。

## 目录结构与层归属

```
09_soul/
├── README.md                 ← 本文件（bootstrap 入口）
│
├── core/                     ← Identity 层 source-of-truth
│   ├── SOUL.md               (character + 工作姿态)
│   ├── COMMUNICATION.md      (沟通风格 + Self-Review pointer + Retrospective trigger)
│   ├── USER.md               (跨项目 portable user profile)
│   └── PROJECT_ADAPTER_TEMPLATE.md
│
├── axioms/                   ← Philosophy 层（最高抽象）
│   ├── INDEX.md              (50 axioms + FP 系列)
│   ├── FP_first_principles.md (最高优先级 axiom)
│   └── A01..X06.md           (47 单点 axioms)
│
├── skills/                   ← Execution 层 best-practice 模板
│   ├── INDEX.md
│   ├── bestpractice_doc_self_review.md      (R14 canonical 路径)
│   ├── bestpractice_retrospective_writing.md
│   ├── bestpractice_mirror_sync.md
│   └── ...                   (parallel subagent / staged approach / debugging / 等)
│
├── bridging/                 ← bridging-management-layer
│   ├── README.md
│   ├── mirror_sync.py        (09_soul/core → 09_<agent>/core 物理 mirror 工具)
│   └── mirror_manifest.json  (外置 sync 状态，文档零 metadata)
│
├── handoff/                  ← 项目 ↔ Hoveath 交接协议
│   ├── README.md
│   ├── installation_guide.md     (Hoveath → 项目 bootstrap SOP，Phase 1-8)
│   ├── distillation_protocol.md  (项目 → Hoveath 反向蒸馏 SOP，S0-S8)
│   └── add_new_agent_projection.md
├── governance/              ← portable governance upstream；项目 T0 系统的释放底座
│
├── examples/                 ← 真实项目的参考实现
│   ├── README.md
│   └── trading_platform/
│       └── claude_management_layer.md
│
├── tools/                    ← 业务工具（analytics / email / image gen 等，与 bridging 区分）
├── contexts/                 ← 历史上下文 scaffolding
├── docs/                     ← 历史 install / cron 参考
├── memory/                   ← 历史 observation 与 reflection
├── periodic_jobs/            ← 历史 cron 自动化骨架
└── reference/                ← 历史外部参考材料
```

## 四层语义边界

| 层 | 内容 | 跨项目 | 跨 agent | 修改入口 |
|---|---|---|---|---|
| Philosophy | axioms（含 FP） | ✅ | ✅ | `09_soul/axioms/` |
| Identity | SOUL / COMMUNICATION / USER profile | ✅ | ✅ | `09_soul/core/`（mirror 由 bridging/ 同步到各投影） |
| Governance | portable top-level governance + T0 release scaffold | ✅ | ✅ | `09_soul/governance/`；项目安装后生成本地 T0 系统 |
| Working Environment | R-rules / task-scoped rules / routing | ❌ | ❌ | 各项目 `<entry-doc>` + `09_<agent>/rules/` + `09_<agent>/routing/` |
| Execution | skills + tools | best-practice ✅ / 业务 ❌ | best-practice ✅ / 业务 ❌ | `09_soul/skills/`（best-practice）/ `.<agent>/skills/`（业务） |

详细语义见 [`examples/trading_platform/claude_management_layer.md`](examples/trading_platform/claude_management_layer.md) §10。

## Bootstrap 场景

把 09_soul/ drop 进新 workspace 后按 [`handoff/installation_guide.md`](handoff/installation_guide.md) 的 Phase 1-8 SOP 走。简版步骤：

1. 复制 09_soul/ 到目标 repo
2. **Phase 1**：建 `09_<agent>/core/`，跑 `bridging/mirror_sync.py --register` 注册 SOUL/COMMUNICATION/USER mirror，写 `09_<agent>/core/PROJECT_ADAPTER.md`，写 `<entry-doc>`（CLAUDE.md / AGENTS.md / ...）含 Session Startup + First Principles + Core Rules
3. **Governance release**：用 `governance/` 基线加项目 Charter 释放本地 T0 系统
4. **Phase 2-5**：建 routing / rules / axioms / skills 各层（细节见 installation_guide）
5. **Phase 6**（仅老项目转 Hoveath）：旧 agent 投影降级
6. **Phase 7-8**：hooks 启用 + dogfood validation

trading_platform 已完成 Phase 1，剩余动作详见 [`examples/trading_platform/claude_management_layer.md`](examples/trading_platform/claude_management_layer.md) §16。

## 维护协议

- **Identity 层 source-of-truth 在 09_soul/core/**：各 agent 投影里的 SOUL/COMMUNICATION/USER 是物理 mirror，由 [`bridging/mirror_sync.py`](bridging/mirror_sync.py) 维护一致性
- **Philosophy 层稳定**：axioms 跨项目跨 agent 通用；新 axiom 经过 ≥3 项目验证再加
- **项目 lesson 反向回吐**：项目内积累的 portable 经验通过 `handoff/distillation_protocol.md` 蒸馏回 templates/，下一项目复用
- **Examples 是验证集，不是规范**：例项目展示「填完 templates 长什么样」，不能反向定义协议

## Quick Reference

| 想做的事 | 去哪 |
|---|---|
| 改判断方法 / philosophy | `axioms/`（先看 INDEX） |
| 改沟通风格 / 用户偏好 | `core/COMMUNICATION.md` / `core/USER.md`，记得跑 `bridging/mirror_sync.py --apply` |
| 加跨项目 best-practice skill | `skills/`，更新 INDEX |
| 加项目特定 skill | `.<agent>/skills/`（在项目内，不在 09_soul/） |
| 同步 mirror | `python 09_soul/bridging/mirror_sync.py --check` / `--apply` |
| 加新 agent 投影 | 看 `handoff/add_new_agent_projection.md`，参考 `examples/trading_platform/` |
| 反向蒸馏 lesson | 看 `handoff/distillation_protocol.md` |
| 看「真实项目长啥样」 | `examples/<project>/` |

## 旧版

旧版 README 描述 09_soul 是「copy-first 引入的 portable 框架」，那时的核心承诺是「跨 Cursor 项目复用」。当前架构升级后，承诺变为「跨 agent runtime + 跨项目的可插拔系统，drop 即用」，所以 README 重写。旧 README 不保留（git history 已存）。
