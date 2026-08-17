# Bridging Management Layer

## 这层是干什么的

`09_soul/` 是 portable source（Philosophy / Identity / Execution-template），不直接被 agent runtime 消费。各 agent 投影（`09_claude/`、未来的 `09_codex/` 等）需要把 09_soul/ 的内容**搬到** agent native runtime 能直接读的位置。这层就是「搬运 + 一致性维护」的工具与契约。

之所以叫 bridging-management-layer：它不是 portable source（Layer A），也不是 agent 投影（Layer B），是连接两者的工具层。

## 当前内容

| 文件 | 作用 |
|---|---|
| [`mirror_sync.py`](mirror_sync.py) | 把 09_soul/core/* 物理 mirror 到 09_<agent>/core/* 的工具。stdlib only，外置 manifest，零 metadata 进文档。命令：`--check` / `--apply` / `--register` / `--list` |
| [`mirror_manifest.json`](mirror_manifest.json) | 外置 manifest：source/target 路径、source_hash、last_synced、addendum_marker。所有 sync 状态都在这，文档本身保持纯净 |
| 配套 skill：[`../skills/bestpractice_mirror_sync.md`](../skills/bestpractice_mirror_sync.md) | 何时触发、协议、addendum 机制、不允许的捷径 |

## 设计原则

1. **零 metadata 进文档**：mirror 文件就是 source 内容 + 可选 project-local addendum。读 token 时不读任何 sync 元数据。USER / COMMUNICATION 这种每会话首条 message 都被读的文档对 token 敏感，metadata 全部外置
2. **外置 manifest**：所有 sync 状态进 `mirror_manifest.json`，文档保持纯净
3. **addendum 段保护**：用 markdown heading 分割 source 与 project-local，工具同步时保留 marker 之下内容
4. **stdlib only**：bridging 工具不能引入额外 Python 依赖；任何 Hoveath 安装的项目（无论用什么 venv 管理）都能直接跑

## 未来在这层加什么

- 其他 portable source ↔ agent 投影的 sync 工具（如果将来 axioms / skills 也要 mirror）
- agent runtime 探针（检测 09_<agent>/ 是否完整、是否跟 09_soul/ 一致）
- 安装 / 卸载脚本（drop 09_soul/ 进新 workspace 的 bootstrap 命令）
- 跨投影一致性检查（如果同时维护 09_claude/ 和 09_codex/，确保两份 mirror 的 source 来自同一 09_soul/ commit）

## 不要在这层放

- 业务工具（analytics / email / image gen 等）→ 留 [`../tools/`](../tools/)
- 单 agent 投影本身（如 09_claude/）→ 留各自的 09_<agent>/
- 文档读者直接消费的 skill（如 retrospective writing）→ 留 [`../skills/`](../skills/)

bridging 这层是「桥」，不是终点。终点是各 agent 投影；桥的任务是确保它们跟 09_soul/ 不漂移。
