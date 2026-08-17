# Handoff Templates

## 这层是干什么的

「项目 ↔ Hoveath upstream」之间的交接协议与模板。

Hoveath 的初心是 portable digital self：drop `09_soul/` 进任何 workspace，它就能 bootstrap。这一承诺要兑现，需要两类协议固化下来：

1. **入站（Hoveath → 项目）**：把 09_soul/ 装进新项目，建立各 agent 投影（09_claude/、未来 09_codex/ 等）
2. **出站（项目 → Hoveath）**：项目在实战中磨合出来的可移植 lesson，反向蒸馏回 Hoveath upstream，让下个项目直接复用

这俩协议属于 Hoveath 框架本身的契约（跨项目通用），所以住在 `09_soul/handoff/`，跟具体项目内部事分开。

## 当前模板

| 模板 | 方向 | 作用 |
|---|---|---|
| [`installation_guide.md`](installation_guide.md) | Hoveath → 项目 | 把 09_soul/ drop 进新 workspace 后的 8 阶段 SOP（Phase 1-8 机制 / 验收 / 项目侧填空点）+ 跨 agent runtime 适配点。任何项目装 Hoveath 都参照这份走 |
| [`distillation_protocol.md`](distillation_protocol.md) | 项目 → Hoveath | 项目内的 09_<agent>/ + entry doc + skills 反向蒸馏为 Hoveath upstream 的 portable templates。8 阶段 SOP（S0-S8）+ placeholder 协议 + round-trip 验证 |
| [`add_new_agent_projection.md`](add_new_agent_projection.md) | 跨 agent 扩展 | 加新 agent runtime（如 09_codex/）时建立投影的协议 |

## 跟其他层的关系

- handoff 协议**消费** [`../core/`](../core/)（Identity 层）、[`../axioms/`](../axioms/)（Philosophy 层）、[`../skills/`](../skills/)（Execution 层）的内容
- handoff 协议**产出**模板（写入 templates/）和迁移过程指南（写入 docs/）
- handoff 协议**使用** [`../bridging/`](../bridging/) 的 mirror_sync 等工具来维护一致性
- handoff 协议**参考** [`../examples/`](../examples/) 的真实项目实现来检验自身可行性

## 设计原则

- **协议在这层，实现不在这层**：handoff/ 只放 SOP / 模板 / 协议，不放具体某项目的产物
- **placeholder-first**：所有跨项目可复用的内容用 `<placeholder>` 表达，避免被某个项目的术语污染
- **examples 是验证集**：每个 handoff 协议都应在 `../examples/` 下有至少一个真实项目的实例佐证它跑得通
