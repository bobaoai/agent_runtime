# Mirror Sync（09_soul → 09_<agent> 投影同步）

## 元数据

- **类型**：BestPractice
- **适用场景**：编辑 09_soul/core/* 或其他 portable source 文件后，把 09_<agent>/ 投影内的物理副本同步到最新；或加新 agent 投影时建立 mirror 关系
- **创建日期**：2026-04-25
- **来源**：把 09_claude/ 投影从 pointer-only 改为 physical mirror 后的同步契约。用户高频读取这些 mirror（每会话首条 message 都要读），故 mirror 文档**零元数据进文档**，所有同步状态外置到 manifest

---

## 这个文件是干什么的

09_soul/core/SOUL.md / COMMUNICATION.md / USER.md 等 Identity 层文件 source-of-truth 在 09_soul/。每个 agent 投影（09_claude/、未来 09_codex/ 等）保留它们的物理副本，让 agent 在会话内**直接读 09_<agent>/core/**，不需要跨目录跳到 09_soul/，减少跨文件阅读时间，避免漏读。

代价是这些副本会 drift。本 skill 锁定 mirror 设计原则 + 同步协议 + 漂移检测路径。

层归属：Execution 层 best-practice，配合 [`09_soul/bridging/mirror_sync.py`](../tools/mirror_sync.py) 工具与 [`09_soul/bridging/mirror_manifest.json`](../tools/mirror_manifest.json) 外置 manifest。

---

## 设计原则：零元数据进文档

USER / COMMUNICATION / SOUL 这种文档每会话首条 message 都被读，token 成本敏感。所以：

- **mirror 文件本身**：source 内容的 verbatim 副本 + 可选 project-local addendum。**零元数据**（无 mirror-of header、无 hash 注释、无 last-synced 字段）
- **同步状态**：全部外置到 [`09_soul/bridging/mirror_manifest.json`](../tools/mirror_manifest.json)
  - `source` / `target` 路径
  - `source_hash`（sha256，用于漂移检测）
  - `last_synced` 日期
  - `addendum_marker`（区分 source 段与 project-local 段的 markdown heading）
- **更新协议**：手动改 source → 跑工具 → 工具读 manifest，rewrite mirror 同时保留 addendum

文档读 token 时只读 source 内容 + project-local 内容，不读 metadata。

---

## addendum_marker 协议

Mirror 内可选有 project-local 段（项目特化补充）。区分方式：约定一行 markdown heading 作为分割线，例如：

```markdown
## Project-Specific Addendum
```

- `addendum_marker` 在 manifest 中声明（每个 mirror 可不同；默认 `## Project-Specific Addendum`）
- mirror 文件结构：`<source 内容> \n\n <addendum_marker 行> \n\n <project-local 内容>`
- 同步时：tool 读 mirror 找 marker，从 marker 行（含）到文件末尾视为 addendum，保留；marker 之上的内容用 source 当前文本覆写
- 约束：**source 文档不能包含 addendum_marker 这一行**（否则同步时冲突）；tool 会在 register / apply 时检查并 fail
- 如果 mirror 没有 addendum，manifest 中 addendum_marker 设为 null，tool 不做 split

---

## 触发条件

下列任一情况触发：

- 编辑了 09_soul/core/SOUL.md / COMMUNICATION.md / USER.md
- 编辑了 09_soul/ 内任何 manifest 中 source 引用的文件
- 加新 agent 投影（09_codex/ 等）需要新建 mirror
- 怀疑 mirror drift（agent 表现跟 09_soul/ 实际内容不一致）
- pull 别人改动后，先 `--check` 一遍

不触发：
- 仅编辑 mirror 文件中 addendum 段
- 仅编辑 09_<agent>/core/PROJECT_ADAPTER.md（不是 mirror 文件）
- 编辑 manifest 之外的任何 09_<agent>/ 文件

---

## 协议

### Step 1：编辑 source

只在 09_soul/<source-path> 内改。

如果你打开了 09_<agent>/core/<file>.md 想改，**停下**：先确认要改的内容属于 source 段还是 addendum 段。
- 改 source 段 → 回 09_soul/ 改 source（mirror 是 read-only mirror）
- 改 addendum 段 → 直接改 mirror 文件 addendum_marker 之下的内容，不影响 sync

### Step 2：检查 drift

```
python 09_soul/bridging/mirror_sync.py --check
```

输出会列出每个 mirror 状态：
- `in-sync`：manifest 里的 source_hash 跟 source 实际 hash 匹配
- `DRIFT`：source 已改，mirror 待同步
- `MISSING SOURCE`：manifest 中声明的 source 路径不存在
- `MISSING TARGET`：mirror 文件还没生成（`--apply` 会创建）

### Step 3：apply

```
python 09_soul/bridging/mirror_sync.py --apply
```

会把所有 DRIFT 的 mirror 重写：
- 读 source → 计算 hash
- 读 target → 用 addendum_marker split 出 addendum 段
- 写 target = source 当前文本 + addendum 段
- 更新 manifest 的 source_hash 与 last_synced

单文件同步：

```
python 09_soul/bridging/mirror_sync.py --apply --path 09_claude/core/USER.md
```

### Step 4：commit

source + 受影响的 mirror 文件 + manifest 一起 commit。**禁止只 commit source 不 commit mirror / manifest**。

### 加新 mirror

```
python 09_soul/bridging/mirror_sync.py --register \
    --source 09_soul/core/SOUL.md \
    --target 09_codex/core/SOUL.md \
    --addendum-marker "## Project-Specific Addendum"
```

不带 `--addendum-marker` 表示这个 mirror 没 project-local 段（纯 verbatim 复制）。

### 列出已注册 mirror

```
python 09_soul/bridging/mirror_sync.py --list
```

---

## 验收

mirror sync 算完成，必须满足：

- `python 09_soul/bridging/mirror_sync.py --check` 退出码 0
- 所有受影响 mirror 的 manifest `last_synced` 是今天
- manifest `source_hash` 跟 source 当前 hash 一致
- mirror 文件 addendum 段（如果有）完好
- source / mirror 文件 / manifest 在同一 commit 内

---

## 不允许的捷径

| 禁止式 | 检测式（无声违反时长什么样） |
|---|---|
| 不允许直接改 mirror 文件中 source 段（addendum_marker 之上） | 下次 `--check` 报 DRIFT；下次 `--apply` 该改动被覆盖丢失 |
| 不允许只 commit source 不 commit mirror + manifest | 别人 pull 后 `--check` 报 DRIFT；mirror 进入历史漂移 |
| 不允许跳过 `--check` 直接 commit | mirror / manifest / source 不一致进入历史 |
| 不允许在 source 文档中加跟 addendum_marker 同名的 heading | `--apply` 会 fail（marker 冲突）；强制重命名 marker 或 source heading |
| 不允许手动编辑 mirror_manifest.json 的 source_hash 字段 | 工具失去漂移检测能力 |
| 不允许把 project-local 内容写到 source 段 | 它会污染 09_soul/ 跨项目内容（应留 addendum 段） |

---

## 跟 Identity 层 source-of-truth 的关系

09_soul/core/* 是 Identity 层 source-of-truth。mirror 是为了 reduce cross-file reading cost 而做的物理副本，**source-of-truth 仍在 09_soul/**。

任何关于 SOUL / COMMUNICATION / USER 的语义讨论、改动、reconcile 都在 09_soul/ 进行；mirror 只是 read-time 优化。

---

## 何时回读本文件

- 编辑 09_soul/core/* 后准备 sync mirror 时
- 加新 agent 投影 (09_codex/ 等) 需要建 mirror 时
- 看到 09_<agent>/core/<file>.md 跟 09_soul/ 同名文件想动它时
- 设计新的 mirror 关系（不只是 core 文件，比如 axioms 也想 mirror）时
- mirror_sync.py 输出意外结果时
- 修改 mirror_manifest.json schema 时回看本 skill 是否需要同步

---

**最后更新**：2026-04-25
