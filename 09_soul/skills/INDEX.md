# Skills Index

本索引指向可复用的 Skills（技能）—— AI 可以调用的工具、流程和最佳实践。

- **想使用某个能力** → 浏览下方分类，找到对应的 skill 文件
- **想添加新 skill** → 参考现有文件格式，添加到对应分类

---

## 本地化状态

这批 skills 采用 copy-first 导入策略，当前分为三类：
- **已本地化**：路径和主要依赖已对齐到 `09_soul/`
- **已弃用（保留脚印）**：原始材料保留，但本地缺少对应项目或脚本，暂不作为可直接执行的能力
- **保留脚印（本仓无适用面）**：从上游 [grapeot/context-infrastructure](https://github.com/grapeot/context-infrastructure) 同步进来，内容完整可读，但本仓没有对应的工作面（iOS、App Store、学术论文、家居硬件等）。留在母体是为了让后续与上游对账时 diff 干净，不投影到 `09_claude/` 或 `.claude/skills/`

本地路径约定：
- 工具：`09_soul/tools/`
- 语境与产出：`09_soul/contexts/`
- 公理：`09_soul/axioms/`
- 记忆任务：`09_soul/periodic_jobs/`

## 组件状态

### Tier 1: 核心（本地已可用或基本可用）
- ✅ Core 框架（`09_soul/core/`）— 已导入并可继续本地化
- ✅ Skills 框架（本目录）— 已导入
- ⚙️ 三层记忆系统 — 代码已导入到 `09_soul/periodic_jobs/`，仍需配置 OpenCode + cron

### Tier 2: 扩展（需要额外配置）
- ⚙️ Semantic Search — 需要 LLM Studio 或 OpenAI API
- ⚙️ Share Report — 需要 SSH 服务器或 GitHub Pages
- ⚙️ Google Docs — 需要 Google OAuth
- ⚙️ Send Email — 需要 Gmail App Password
- ⚙️ Delayed Execution — 适配你自己的工具路径

### 说明
✅ = 最多 15 分钟即可使用
⚙️ = 需要额外配置，不配不影响核心功能

---

## 分类索引

### API Guide（API 指南）

调用外部系统或工具的操作手册。

- [AI CLI Agent 实用指南](./ai_agent_cli_guide.md) — CLI Agent 设计原则、工具对比（Claude Code / Codex / OpenCode）、文件响应模式、AI 调用 AI
- [给自己发邮件技能](./send_email.md) ⚙️ — 通过 Gmail 发送邮件通知，需配置 App Password
- [分享报告到 Web](./share_report.md) 已弃用（保留脚印）— 依赖未导入的发布项目骨架
- [Google Docs 操作](./google_docs.md) 已弃用（保留脚印）— 依赖未导入的 `gdocs_skill` 项目
- [Gemini 图片生成与放大](./gemini_image_generation.md) — CLI 工具：文生图、图片编辑、分辨率放大
- [增长数据分析](./growth_analytics.md) ⚙️ — 三个 CLI 查询网站流量（GA4）、邮件订阅（Kit）、Twitter 互动（Typefully）
- [Typefully Metrics CLI](./typefully_metrics.md) ⚙️ — 通过浏览器 session 凭据查询 Twitter impression、engagement、followers 数据
- [Claude Code CLI Adapter Pointer](./bestpractice_claude_code_cli_runner.md) ✅ — 指针文件：Claude Code CLI 是 provider adapter 而非 Agent 定义，canonical 契约在 `bestpractice_agent_runtime_module_builder.md`

### Workflow（工作流）

特定任务的完整工作流程。

- [并行 Subagent 工作流](./workflow_parallel_subagents.md) ✅ — 调用后台 agent、并行执行多个 subagent
  - **必读**：初次使用并行 subagent 前，必须先读此 skill
  - **禁止轮询**：agent 运行期间不要反复调用 `background_output`，系统会自动通知
  - 判断标准：任务可拆分为 ≥2 个子任务，每个 ≥5 tool calls
  - 核心参数：并行度 ≤5，调研 overlap 30-50%，代码 overlap 0-20%
- [深度调研工作流](./workflow_deep_research_survey.md) ✅ — 多 Agent 并行 + 交叉验证
- [认知画像提取工作流](./workflow_cognitive_profile_extraction.md) — 从非结构化对话数据提取可预测的认知公理
  - 适用：群聊/Slack/Discord/邮件/播客转录等任意对话数据
  - 流程：广泛扫描 → 深度验证 → 压力测试 → 定稿（≥3 轮动态滚动）
  - **要求 Opus 模型**：写作由 Opus 亲自完成，调研全部 delegate + 并行
- [AI 生成 Slide Deck 工作流](./workflow_presentation_slides.md) 已弃用（保留脚印）— 旧代；现役能力由 sibling 仓 `presentation_skill` 提供
- [Presentation Deck（sibling 仓）](https://github.com/grapeot/presentation_skill) ✅ — 本地 clone `/Users/bokanbao/Documents/GitHub/presentation_skill`，editable install 进 venv；投影在 `.claude/skills/writer-presentation/`；deck 规划、Reveal 模式、生成资产纪律、speaker notes QA、PDF 导出
- [语义搜索技能](./semantic_search.md) ⚙️ — 利用向量相似度检索深层背景与观点演变
- [知识飞轮设计模式](./workflow_knowledge_flywheel.md) — 笨数据+笨方法+笨模型=精知识
- [视频下载与语音识别工作流](./workflow_bilibili_whisper_transcription.md) 已弃用（保留脚印）— 原始项目已脱离当前工作区
- [延时执行技能](./delayed_execution.md) ⚙️ — 定时任务：sleep + 后台执行，或 OpenCode API 智能任务
- [写作工作流路由](./writing_workflows.md) ✅ — root skill：分流内部与外部写作，并统一缺陷极性、冷读段落、概念负荷、连续推理和 protected meaning 契约
- [外部写作与成文工作流](./workflow_external_writing.md) ✅ — 对外文章：独立视角、多阶段重写、终端冷读、缺陷极性和 source/分析语义保护
- [内部写作工作流](./workflow_internal_writing.md) ✅ — 内部 memo 与 RFC：结论先行、定义先行例外、段落三问、概念负荷和连续推理
- [公开一致预期净利润审计](./workflow_public_consensus_net_income_audit.md) ✅ — 混用 MarketScreener / Yahoo / MarketWatch 时把 consensus net income 整理成可审计表格，显式区分 direct value 与 derived value

### BestPractice（最佳实践）

通用的最佳实践和经验教训。

- [AI 编程核心方法论](./bestpractice_ai_programming_mindset.md) ✅ — 70%问题、成功标准、可验证性
- [中文写作守则](./bestpractice_chinese_writing_voice.md) ✅ — 中文行文的语态、节奏与禁忌词面
- [External Worker General Module](./bestpractice_external_worker_general_module.md) ✅ — 外部 worker 的通用工作方式模块；与任务专属 CUSTOMIZE_MODULE 分层
- [API Key 管理与调用](./bestpractice_api_key_management_1password_cli.md) ✅ — 使用 1Password CLI 安全管理密钥
- [面试评估框架](./bestpractice_interview_evaluation.md) ✅ — Trait > Skill、AI 作弊识别、技术深度探测
- [Markdown 转 HTML 最佳实践](./bestpractice_markdown_html_conversion.md) ✅
- [时间敏感信息验证](./bestpractice_temporal_info_verification.md) ✅ — 验证可能超出 knowledge cutoff 的信息
- [分阶段工作法](./bestpractice_staged_approach.md) ✅ — 隔离-处理-验证闭环，破坏性操作前 Dry Run
- [多 Agent 并行 analysis](./bestpractice_multi_agent_analysis.md) ✅ — Topic 分割 50% 重叠、交叉验证
- [AI 辅助调试诊断](./bestpractice_ai_debugging_diagnosis.md) ✅ — "代码改不好"的根因诊断决策树
- [AI 产品设计原则](./bestpractice_ai_product_design.md) ✅ — 线性聊天 vs 知识工作、感知规则解耦
- [Skill 写作指南（Meta-Skill）](./bestpractice_skill_writing.md) ✅ — 写 skill 时优先定义目标、边界、验收标准与输出规格，避免把 skill 写成 SOP；含 AI-facing detail-first 6 问、不变量/检测式边界、冷读概念顺序与受保护语义
- [读者状态与判断增益优先](./bestpractice_reader_state_and_judgment_gain.md) ✅ — 先定义读者读完后获得什么判断能力，再决定 section、prompt、contract 与 detail；含 reader start-state 概念顺序与 multi-agent handoff reader-gain 化
- [Doc Self-Review（交付前自审 doc）](./bestpractice_doc_self_review.md) ✅ — R14 的 canonical 执行路径：三阶段（结构审 → 内容审 → 风格审）+ 冷读缺陷检查 + 受保护语义 + 4 份参考 + 跳过协议 + 自审报告格式
- [Retrospective Writing（dogfood 周期复盘）](./bestpractice_retrospective_writing.md) ✅ — 触发条件 / INDEX 先行 / schema / follow-up inline / prose 验收与状态、验证类型、因果 lesson 保护
- [Mirror Sync（09_soul → 09_<agent> 投影同步）](./bestpractice_mirror_sync.md) ✅ — zero-metadata mirror + 外置 manifest；配合 `09_soul/bridging/mirror_sync.py` 做 drift 检测与 sync
- [分析写作质量标准](./bestpractice_analytical_writing.md) ✅ — 分析 prose 的正面质量标准：因果链、alternative、judgment transfer、so-what、calibration、narrative arc、冷读概念顺序与分析语义保护
- [Prose Without Editorial Meta（写关于世界，不写关于稿件）](./bestpractice_prose_without_editorial_meta.md) ✅ — sentence-level 守门：reader-facing 报告与 stable AI-facing artifact 都禁止 editorial meta / conversation attribution / workflow time-window deictics / rule invocation as endorsement
- [Prompt Boundary（task-plane vs control-plane）](./bestpractice_prompt_boundary.md) ✅ — A14 的 canonical 操作路径：下游 prompt 只放改变 worker 输出质量的信息，编排 / persona-source label / 已被结构性保证的 guardrail 一律删
- [Agent Runtime Module Builder](./bestpractice_agent_runtime_module_builder.md) ✅ — 把 Skill 声明的独立 Module source 与 domain-owned Workflow 注册为 provider-neutral、可评估、可审计、可版本演进的 Runtime release；SDK、API、CLI 与 durable backend 均为 adapter
- [Agent Module General Module](./bestpractice_agent_module_general_module.md) ✅ — worker stable prefix 的去重 `WORKER_CHARTER / GENERAL_MODULE`；只能提供通用工作方式，不能替代任务专属 `CUSTOMIZE_MODULE` 或 source-family `DATA_DEPENDENT_MODULE`
- [External Writer Merge Rule](./external_writer_merge_rule.md) ✅ — caller-side 合并规则；external draft 只作 advisory，merge 后重建连续推理并保护事实、因果与不确定性
- [外部中文 Prose 诊断词汇表](./bestpractice_external_prose.md) ✅ — 对外中文行文的教材声、认知负荷、段落连续性、定义例外和语义保护诊断

### Review Module（评审模块）

供 Agent Runtime 装配的评审模块，不是给人直接读的操作手册。

- [Skill Review Module](./review_module_skill_review.md) ✅ — 审 SKILL.md 的完整性、清晰度与契约质量，输出 typed findings

### Reference / Deployment（参考与部署）

上游同步进来的参考资料与部署手册。

- [外部文章启发性分析视角](./reference_writing_thesis_catalog.md) ✅ — thesis catalog：写对外文章时可套用的分析视角清单
- [External Prose Lint CLI](./external_prose_lint.md) ⚙️ — 确定性行文校验；明确不判断定义顺序、段落三问、认知负荷、连续推理或语义漂移

---

## 如何添加你自己的 Skill

1. 参考现有 skill 文件的格式（元数据、核心说明、使用步骤、示例）
2. 以 `<category>_<name>.md` 命名（例如 `workflow_my_process.md`、`bestpractice_my_insight.md`）
3. 在 INDEX.md 对应分类下添加一行

Skill 格式参考（最简版）：
```markdown
# Skill: 名称

## When to Use
什么情况下触发这个 skill

## Prerequisites
需要什么工具/配置

## 步骤
1. 步骤一
2. 步骤二
```

## Progressive Disclosure

Skills 采用渐进式披露原则：
- **INDEX.md** 提供概览，快速定位
- **具体 skill 文件** 包含完整的操作步骤和示例
