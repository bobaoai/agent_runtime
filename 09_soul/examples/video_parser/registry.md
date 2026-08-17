---
project_name: video_parser
git_remote_url: git@github.com:bobaoai/video_management_layer.git
agent_runtime: claude_code
entry_doc_relative: CLAUDE.md
subordinate_soul_relative: 09_soul/
subordinate_agent_projection_relative: 09_claude/
subordinate_runtime_config_relative: .claude/
last_known_upstream_commit: 86f486c
last_known_subordinate_commit: 085e9fb
inherited_baseline_snapshot: pending
dogfood_status: pending_install
relationship: bidirectional
---

# Subordinate Registry — video_parser

video_parser 是 Hoveath 母体的第二个注册子体。本 registry 是逻辑注册卡，跨机器一致。本机物理路径在 [`../.local_paths.json`](../.local_paths.json)（gitignored）。

母体侧注册已完成；子体内的 Hoveath 主投影（Phase 0-1 至少）尚未安装。当前 `dogfood_status: pending_install`，安装完成后升到 `active`，并把 `inherited_baseline_snapshot` 填上当时的快照日期。

## 项目读码

video_parser 是一个视频内容抓取与解析 workspace。当前内容（截至 `last_known_subordinate_commit`）：

- `bilibili_parser/` — bilibili 收藏夹抓取与下载工具，含 `fetch_collection.py`（抓取 / 下载 collection）/ `main.py`（CLI 入口）/ `design/`（设计 doc）/ `tests/`（测试）/ `outputs/`（生成产物，应被 gitignore）/ `requirements.txt` / `env.example`
- `cookies.txt` — bilibili 登录态（应被 gitignore，作为敏感凭据保护）
- `.venv/` — Python 虚拟环境（已 gitignore）

## 关系定义（计划态）

子体 Hoveath 主投影安装完成后，预期布局：

- portable framework：`09_soul/`（video_parser repo 根下，与母体命名一致）
- Claude Code 投影：`09_claude/`
- runtime config：`.claude/`
- entry doc：`CLAUDE.md`
- handoff 协议：`09_soul/handoff/`（同步自母体）

母体侧对 video_parser 的引用：

- [`examples/video_parser/registry.md`](registry.md) — 本文件：注册卡
- [`examples/video_parser/inbox/`](inbox/) — 子体 → 母体反馈（人工搬运自子体 `09_<agent>/handoff/to_upstream/`）
- [`examples/video_parser/outbox/`](outbox/) — 母体 → 子体通告

母体不持有子体业务内容（具体抓取目标、cookie、下载产物）；那些只在子体本地。

## 安装路径建议

video_parser 推荐走 [`../../handoff/installation_guide.md`](../../handoff/installation_guide.md) Phase 0-1 最小可用先 dogfood，再按需逐步铺 Phase 2-5。

简版步骤：

1. 在 video_parser repo 根 `cp -R` 母体 `09_soul/` 整体到子体
2. 在 video_parser 内建 `09_claude/core/` 目录骨架
3. 跑 `python 09_soul/bridging/mirror_sync.py --register` 三次注册 SOUL / COMMUNICATION / USER mirror（COMMUNICATION 与 USER 用 `--addendum-marker "## Project-Specific Addendum"`）
4. 写 `09_claude/core/PROJECT_ADAPTER.md`（针对 video_parser 业务面 — 视频抓取 / cookies 安全 / 输出产物路径 / 跟下游消费者的关系）
5. 写 `CLAUDE.md`（entry doc：Session Startup Protocol + First Principles + Core Rules + Routing stub + Deeper Context Pointers）
6. 写 `.claude/settings.json`（permissions allow / deny + cookies.txt deny 写入）
7. 跑 `python 09_soul/bridging/mirror_sync.py --check` 退出码 0 = Phase 1 收口

完成后子体内：

- 起新 session 时 agent 自动加载 4 份身份文件
- 子体写下的 portable lesson 通过 `to_upstream/` → 母体 `inbox/` 闭环
- 母体升级时通过 `outbox/` 通告子体

## 继承的 baseline 摘要（计划继承）

子体安装时将继承母体的当前 portable 层（截至 `last_known_upstream_commit`）：

- **Philosophy 层**：FP（7 条）+ 52 axioms（A01-A20、T01-T11、M01-M10、V01-V05、X01-X06）
- **Identity 层**：SOUL / COMMUNICATION / USER 三个 mirror
- **Execution 层 baseline skill**：15 个跨项目 baseline（含本轮新增的 chinese_writing_voice / prose_without_editorial_meta / prompt_boundary）
- **Bridging 层**：mirror_sync.py + manifest schema
- **Handoff 层**：installation_guide.md + distillation_protocol.md + migration_story.md

子体特有（不属于母体管辖）：

- 视频抓取 / cookies 处理 / B 站特定 API 调用相关的业务 skill
- bilibili_parser 包内部架构与设计 doc
- 输出产物路径与 schema

## 项目特定 tripwire（建议安装时加）

video_parser 比 trading workspace 多一类风险：cookies.txt 是 bilibili 完整登录态，泄露等于账号被接管。建议在 PROJECT_ADAPTER 或 CLAUDE.md R-rule 段加专门 tripwire：

- **cookies tripwire**：任何提交前自动检查暂存区无 `cookies.txt` / `*.cookie` / `bilibili_cookie*`；`.gitignore` 含明确 cookies 模式
- **outputs 体积 tripwire**：下载产物（视频文件）默认进 `outputs/` 且全部 gitignore
- **rate limit 纪律**：抓取 collection 时的 sleep 间隔不能在 prompt 里被 worker 自由调整（见 `bestpractice_prompt_boundary` task-plane vs control-plane）

## 已知 drift

母体侧 commit 时点（`86f486c`）vs 子体侧 commit 时点（`085e9fb`）的 drift：

- 子体当前是 `Initial commit: bilibili_parser with collection fetcher`，完全没装 Hoveath；母体已是装完 Phase 1-6 + 完成第一轮 distill batch + 注册第二个子体（本文件）
- 子体安装时会一次性吞掉所有母体当前 portable 层内容；不存在"老 fork drift"问题（这是个全新项目）

## 同步建议

子体首次同步母体的步骤（手动）：

1. 在子体 repo 内确保 `git status` clean（Initial commit 已 push）
2. 把母体 [`09_soul/`](../../) 的最新内容整体 cp 到子体的 `09_soul/`（首次安装无需保留任何子体特化部分）
3. 按本 registry §安装路径建议执行 Phase 0-1
4. 子体跑 `python 09_soul/bridging/mirror_sync.py --apply` 把 source 重写到 09_claude/ 投影
5. 子体跑一次 dogfood（任意命中 Session Startup 的请求都行），验证 4 份身份文件加载正确
6. 子体把第一轮 dogfood 发现写到 `09_<agent>/handoff/to_upstream/<YYYYMMDD>_install_validation.md`
7. 拉到母体 [`inbox/`](inbox/) commit，把本 registry 的 `dogfood_status` 升到 `active` 并填 `inherited_baseline_snapshot` 日期

## 维护协议

- `last_known_upstream_commit` / `last_known_subordinate_commit` 在每轮蒸馏 / 同步周期收口时手动更新
- `inherited_baseline_snapshot` 用日期记录最近一次完整 baseline 同步（pending_install 状态时为 `pending`）
- `dogfood_status: pending_install` 表示子体已注册但 Hoveath 尚未安装；`active` 表示子体正在产生 lesson；`paused` 表示暂时不 dogfood；`sunset` 表示项目结束（届时本 registry 移到 `examples/_archive/`）
