# External Agent Builder（Hoveath 子体调用）

## 元数据

- **类型**: BestPractice
- **适用场景**: 需要把 Claude Code CLI、DeepSeek、OpenAI、Cursor 子体或其他外部 AI 执行面当作独立 Hoveath 子体运行，用于 verifier、evidence reviewer、批量 triage、视觉/文本判读、独立审查、外部 runner 自动化
- **创建日期**: 2026-05-03
- **来源**: 多轮外部 AI runner、Claude Code CLI、DeepSeek / API worker、image review、quota 控制、prompt 边界和审计日志经验沉淀

---

## 这个 skill 解决什么

External Agent Builder 的核心目标不是“调用另一个模型”，而是把另一个 AI runtime 变成可控、可审计、可中断、可复盘的 Hoveath 子体。

执行面可以是 Claude Code CLI、DeepSeek API、OpenAI API、Cursor subagent，或其他本地 / 云端 AI worker。执行面不同，runner 不变量相同：prompt 模块化、输入可审计、输出可验证、硬阻塞会停机、结果能回到本地 artifact state。

成功的 runner 应该满足：

- prompt 只包含 worker 完成任务需要的 task-plane 信息
- 每次运行有可追踪的 `run_id`、输入选择、模型、effort、timeout、日志和结果状态
- 长批量 / 高成本调用不会重复提交同一对象
- quota / auth / rate limit 这类硬阻塞会立即停机，而不是继续消耗失败请求
- 子体结果写回前会重新检查当前状态，避免旧 pending snapshot 覆盖新 ledger
- 输出能被本地代码解析、验证，并和本地 evidence / artifact state 对齐

---

## 何时使用

使用本 skill，当任务满足任一条件：

- 需要独立 AI verdict，而主 agent 的判断不应直接成为最终裁决
- 需要在本地文件 / artifact 上批量运行模型判读
- 需要可审计的 external model call log，而不是聊天内临时结论
- 任务会消耗明显 quota，需要 preflight、批次控制和 hard-stop 机制
- worker 输出必须以 JSON / JSONL / sidecar 写回本地 artifact

不要使用本 skill：

- 一次性简单问答，主 agent 可以直接完成
- 不需要独立执行面的普通代码搜索或解释
- 没有明确输入对象、输出 schema 或写回位置的探索性聊天

---

## 关键不变量

### Runner identity

每个 runner 必须显式记录：

- `run_id`
- `model`
- `effort`
- `timeout_sec`
- `max_workers`
- `selected_count`
- `input_selection_rule`
- `prompt_template_version`
- `static_prompt_hash`
- `general_module_hash`
- `customize_module_hash`
- `data_dependent_module_hash`
- `input_payload_hash`
- `log_path`

无声违反信号：只能从终端历史猜测“跑过哪些对象”，没有结构化 JSONL run log。

### Prompt boundary

Prompt 只放会改变 worker 输出质量的信息：

- task goal
- factual boundary
- allowed evidence
- output schema
- required judgments
- uncertainty rules
- forbidden prose / JSON modes

不要把 caller 的 orchestration、内部 routing、persona 来源、准备过程、上游如何筛选输入写进 worker prompt，除非这些信息会改变 worker 对当前对象的判断。

不要在 worker prompt 里说明“这是给 Claude Code CLI / DeepSeek / Codex CLI / OpenCode 的 prompt”。执行面名称是 caller 的 control-plane，不是 worker 的 task-plane。Worker 只需要知道自己要完成什么任务、可用证据是什么、输出 schema 是什么；让它意识到自己在某个 runner 里，通常只会污染输出，诱发流程说明、工具身份表演或不必要的 hedging。

推荐形态：

```text
Read one local artifact as [role].
Return JSON only.
Required keys: ...
Decision labels allowed: ...
If evidence is insufficient, say so in ...
---
object_id: <id>
input_path: <path>
```

### Prompt cache shape

省 token 的核心方法是让外部模型的 prompt / context cache 尽可能命中：**前面全部固定，只在后面追加变化内容**。

Runner prompt 应拆成两层：

- **Stable prefix**：任务定义、输出 schema、allowed labels、判断标准、失败处理、示例、禁止事项。这部分在同一批 runner 中完全不变。
- **Dynamic suffix**：当前 `object_id`、`input_path`、短 metadata、待审文本片段、row hash、run-specific context。这部分只放在 prompt 最后。

推荐形态：

```text
[STABLE PREFIX - identical for every object in this runner]
You are reviewing one local artifact.
Return JSON only.
Required keys: ...
Allowed decisions: ...
Evidence rules: ...
Uncertainty rules: ...

---

[DYNAMIC SUFFIX - object-specific]
object_id: <id>
input_path: <path>
metadata: <small object-specific fields>
```

不要把以下内容放进 stable prefix：

- execution surface 名称，例如 `Claude Code CLI`、`DeepSeek`、`Codex CLI`、`OpenCode`
- `run_id`
- `object_id`
- `input_path`
- 当前时间
- batch 序号
- per-object source snippet
- caller 对这个对象的临时判断

这些字段一旦进入前缀，会让每个对象都变成不同 prompt，降低 cache 命中。Runner 应记录 `static_prompt_hash`，并在同一批次内验证它不变。

### Three prompt modules instead of one universal charter

不要把 `FP_first_principles.md`、`COMMUNICATION.md`、`bestpractice_reader_state_and_judgment_gain.md`、`bestpractice_skill_writing.md` 等文件原样全文塞进 worker prompt。它们之间有重叠：先定义结果、少废话、reader gain、自审、结果确定性、contract boundary 会反复出现。

Caller 应先把 prompt 拆成三类模块。`WORKER_CHARTER` 只覆盖非常 general 的底座，不承担所有任务意图。真正的任务意图、schema、数据源语义必须由后两层补上。

1. **General module**：跨任务通用的工作方式，例如 evidence boundary、reasoning fidelity、少废话、reader gain、自查。通常来自 communication / first principles / prompt boundary / skill-writing 的去重蒸馏。
2. **Customize module**：针对当前任务的专用 prompt，例如 source card writer、image triage、evidence reviewer、thesis verifier、asset technical writer。这里定义任务目标、输出 schema、allowed labels、升级规则、成功标准。
3. **Data-dependent module**：针对数据源或输入族的说明，例如 AgentMail message、PDF page crop、Substack newsletter、broker statement、market data packet。这里定义字段语义、时间规则、source-specific caveat、可用证据形态。

三层都可以进入 stable prefix，但前提是它们在同一个 runner template / source family 内固定不变。单个对象变化的内容，例如 `object_id`、路径、原文、row hash、source timestamp、caller notes，仍然放 dynamic suffix。

默认的通用 worker charter 已拆到 `bestpractice_external_worker_general_module.md`。Runner 只能抽取其中的 `## WORKER_CHARTER / GENERAL_MODULE` 进入 stable prefix；不要把整份 skill 文件或这些来源文档原样全文塞进 worker prompt。

完整 prompt template 应接上 task-specific 和 data-dependent 模块：

```text
CUSTOMIZE_MODULE:
- <task-specific goal>
- <output schema>
- <allowed labels>
- <success and failure criteria>

DATA_DEPENDENT_MODULE:
- <source-family field semantics>
- <timestamp and freshness rules>
- <source-specific caveats>
- <allowed evidence blocks>
```

不要让 general module 假装能 serve 所有 intention。缺少 customize module 时，worker 只知道“工作风格”，不知道“要产出什么”。缺少 data-dependent module 时，worker 只知道“任务”，不知道“这类材料应该怎么读”。

Caller-side merge 规则已拆到 `external_writer_merge_rule.md`。它用于 main agent 合并外部 writer draft，不属于普通 worker stable prefix。

来源路径和版本可以写进 runner design、audit metadata 或 prompt builder 注释；worker prompt 里只保留可执行的模块内容。不要把方法论来源说明混进 worker prompt。

### Prompt module projection

Domain runner 可以把通用 runner 原则投影成独立的 worker prompt module 文件，例如：

```text
09_soul/skills/bestpractice_<domain>_worker_general_module.md
```

这种文件不是替代本 skill，而是某个子体场景的 `GENERAL_MODULE` 投影。它应该满足：

- 文件可以有元数据、来源、使用边界，供 caller / design / audit 使用。
- Worker prompt 只能抽取可执行 section，例如 `## WORKER_CHARTER / GENERAL_MODULE`，不能把整份 skill 文件塞进 prompt。
- 抽取后的 `GENERAL_MODULE` 必须继续和 `CUSTOMIZE_MODULE`、`DATA_DEPENDENT_MODULE` 拼接；单独的通用 charter 不能 serve 完整 intention。
- 如果 data source 需要更窄边界，`DATA_DEPENDENT_MODULE` 必须压住通用 charter。例如通用 charter 允许“明确列出的本地输入文件”，但某个 Source Card runner 可以规定路径只作 audit metadata，事实只能来自 embedded packet。

审计上不要只记录一个 `static_prompt_hash`。更好的记录是：

```yaml
prompt_template_version: <template version>
static_prompt_hash: <sha256 of full stable prefix>
general_module_hash: <sha256 of extracted GENERAL_MODULE>
customize_module_hash: <sha256 of CUSTOMIZE_MODULE>
data_dependent_module_hash: <sha256 of DATA_DEPENDENT_MODULE>
input_payload_hash: <sha256 of dynamic suffix / embedded packet>
```

这样后续内容质量变化可以定位到具体模块漂移，而不是只知道“stable prefix 变了”。

### External agent builder projection

External agent builder 是本 skill 在具体项目里的执行投影。它负责把任意外部 AI worker 变成 runner artifact，而不是让每个场景临时手写 prompt。External review、DeepSeek 批量阅读、OpenAI evidence extraction、Cursor subagent packet review、外部视觉模型判读都应落在同一组不变量上：

- 稳定 prefix 由本地 builder 或 template 持有，不能为每个 case 手改一份完整 prompt。
- 任务专用判断标准进入 `CUSTOMIZE_MODULE`。
- 输入族、允许证据、source caveat、local-reference 语义进入 `DATA_DEPENDENT_MODULE`。
- 本地 references、source packet、target artifact 或 object payload 必须由 builder / runner 嵌入；只列路径等于把理解任务丢给外部 runtime。
- 运行前必须检查是否已有同一 target / prompt / object selection / output path 的旧 runner 正在跑，并停掉过时 run。
- 如果旧 prompt 或 payload 产出了 output，而输入后来被判定无效，该 output 必须删除、重命名或在 handoff 中标为 stale，不能让下游误认为最终结果。

在 `trading_platform` 中，这条原则的项目级投影是 `.cursor/skills/support-external-agent-builder/SKILL.md`。Doc / skill review 的子流程是 `.cursor/skills/support-external-agent-builder/external_review_builder.md`，底层拼接工具是 `src/tools/build_doc_review_prompt.py`；DeepSeek 或其他 API worker 应作为同级 subskill 使用同样的 stable prefix + modules + dynamic suffix 形态。

无声违反信号：

- `.scratch/` 里出现一份完整手写 worker prompt，但没有对应 module 文件和 builder command。
- prompt 让 worker “读取这些本地路径”，却没有嵌入文件正文。
- task-specific 目标被直接塞进 stable prefix，使同类 runner 无法复用 prompt cache。
- code fence 或 serialization boundary 因嵌入内容本身包含边界符而提前闭合，导致后续 target body / source packet / module 变成 prompt 指令或普通散文。

### Preflight before quota work

启动长批量、并发模型调用、image review、verifier swarm 前，先检查：

- 是否已有同类 runner 正在运行
- 是否已有旧终端 / 后台进程还在处理同一对象集合
- 是否已有本地 log 显示同一 `run_id` 或同一 selection 正在进行
- 当前 ledger 里哪些对象仍是真正需要处理的 pending state

如果 active runner 状态不清楚，先停下来读日志或等待，不要另起一份。

无声违反信号：同一 `object_id` 在 usage log 中短时间内出现多次模型调用，且调用来自不同 runner。

### Claim / status gate

批量 runner 必须在两个点检查状态：

1. **提交模型前**：对象仍处于待处理状态。
2. **写回结果前**：对象仍处于同一待处理状态。

如果状态已经变成 terminal state，跳过写回并记录 `skipped_already_reviewed` 或同等状态。

理想状态是 code-level claim / lease；没有 claim 时，至少要做提交前和写回前的 current-state check。

无声违反信号：一个旧 worker 返回后覆盖了另一个新 worker 已写好的 reviewed / verified / dismissed row。

### Hard blockers stop the runner

以下状态是 hard stop：

- quota / monthly usage limit
- `429`
- auth failure
- unsupported token / invalid token
- runner dependency missing
- systemic output schema failure，例如 parser / template 在初始 smoke batch 中连续失败

Hard stop 发生时：

- 取消未开始的 futures
- 停止后续 batch
- 写入 `run_stopped_quota` / `run_stopped_auth` / `run_stopped_schema` 等结构化事件
- 返回给 caller 一个可恢复的 remaining target count

不要把 hard blocker 当成普通 failed row 继续跑完整批。

单个对象输出坏 JSON 或 schema 不合格时，优先记为 per-row failure；只有失败显示为模板、parser、schema contract 或 runner 依赖层的系统性问题时，才停止整批。

### Audit log as source of truth

每次 runner 至少写 JSONL log：

- `run_start`
- `batch_start`
- `image_result` / `object_result` / `worker_result`
- `image_saved` / `object_saved`
- `batch_complete`
- `run_complete` 或 `run_stopped_*`

每条 result 应包含：

- `run_id`
- `recorded_at_utc`
- `object_id`
- `status`
- `elapsed_sec`
- `input_path`
- `input_payload_hash`
- `error`（失败时）

无声违反信号：只能在 terminal stdout 中看到自然语言进度，没有可机器读取的 run log。

---

## Prompt 编写规则

### 输出 schema 先行

如果下游代码要解析结果，prompt 必须明确：

- JSON only
- required keys
- allowed enum values
- bool / number / list 字段语义
- insufficient evidence 时如何表达

不要只说“结构化输出”。要给出字段名和允许值。

### Evidence first, interpretation second

对于 review / evidence / image / document read：

- 先要求 worker 写 visible facts / observed facts
- 再写 inference / judgment
- 最后写 PM relevance / reader gain

这可以减少 worker 先编故事再找证据的倾向。

### Keep dynamic path visible

Prompt 中必须明确当前对象路径或 ID，例如：

```text
object_id: image_abc123
input_path: /repo/data/.../crop.png
```

如果 worker 需要读本地文件，路径必须是 worker runtime 可访问的路径。

### Do not hide uncertainty

要求 worker 在看不清、证据不足、OCR 不完整、图表坐标不精确时明确写 uncertainty。不要诱导 worker 给出精确数字。

---

## Runner 设计建议

### Model tiering

用低成本模型做 dismissal / triage，用高能力模型做最终 evidence extraction 或 verdict。

典型分层：

- deterministic prefilter：logo、CTA、tracking pixel、明显无证据对象
- Sonnet / lightweight model：是否值得进入高成本 review
- Opus / strongest model：最终视觉证据、复杂推理、独立 verdict

不要把所有对象直接送最高成本模型。也不要让低成本 triage 直接写入高风险解释结论。

### Compression policy

先减少无关输入，再考虑降分辨率或压缩。

对于图像：

- 优先 crop 到证据区域
- 真正 data figure 保留原始可读分辨率
- 只有 quota / payload 硬约束时才降分辨率
- 不用文件大小单独判断是否有证据；稀疏线图可能很小但信息密度高

### Batch sizing

并发数和 batch size 要保守设置。高成本 / 高失败代价任务优先小批次，确认稳定后再扩大。

无声违反信号：一次启动大批量，第一批已经出现 schema / quota / stale-state 问题，runner 仍继续向后提交。

---

## 结果验收标准

一个 External Agent Builder 产出可以交付，至少满足：

- 有结构化 run log，能复盘输入、模型、状态和失败原因
- 有 `general_module_hash`、`customize_module_hash`、`data_dependent_module_hash`，能定位 stable prefix 内部漂移
- 每个结果记录 `input_payload_hash`，能证明 worker 实际消费的动态输入
- 所有成功结果已写回 canonical artifact 或 sidecar
- 所有失败结果保留错误上下文
- hard blocker 没有被吞掉
- 运行结束后 remaining targets 可计算
- 写回前状态检查防止 stale overwrite
- 下游 index / read surface / sidecar 已同步，或明确说明还未同步

---

## 与其他 skill 的关系

- `ai_agent_cli_guide.md`：提供 CLI Agent 工具总览和文件响应模式。
- `bestpractice_prompt_boundary.md`：约束 worker prompt 只保留 task-plane 信息。
- `workflow_parallel_subagents.md`：适用于 Cursor / agent subagent 并行；本 skill 适用于外部 AI runner。
- `bestpractice_staged_approach.md`：提供隔离-处理-验证闭环，本 skill 是外部 AI 调用场景的具体化。

---

## 常见失败模式

| 失败模式 | 表现 | 修正 |
|---|---|---|
| 重复 runner | 同一对象短时间多次模型调用 | 启动前查 active process / terminal / run log，写入 claim 或做 current-state gate |
| Prompt 塞入 control-plane | worker 输出在描述流程、过度 hedging | 用 `bestpractice_prompt_boundary.md` 重写 prompt |
| Prompt 暴露执行面 | worker 写“作为 Claude Code / DeepSeek / Codex CLI 我会...”或解释工具身份 | 删除执行面名称，只保留任务、证据边界和输出 schema |
| Raw excerpts 堆叠 | stable prefix 里多份 skill 反复强调同一原则，worker 输出变成方法论总结 | 去重成 `GENERAL_MODULE`，来源和版本留在 caller audit metadata |
| General module 过载 | 通用 charter 试图 serve 所有任务，prompt 缺少任务目标或数据源语义 | 增加 `CUSTOMIZE_MODULE` 和 `DATA_DEPENDENT_MODULE`，把 intention 和 source-family contract 分开 |
| 整份 skill 塞进 prompt | worker prompt 带入元数据、来源、使用边界，输出开始解释流程 | 只抽取可执行 section，例如 `WORKER_CHARTER / GENERAL_MODULE` |
| 手写 external worker prompt | prompt 看似更准确，但绕过 stable prefix / module / builder 拼接，外部 worker 看不到本地正文或 prompt cache 被破坏 | 写 task-specific module，用本地 builder 嵌入 references 和 target body，再运行 external worker |
| 只列本地路径 | external worker 不知道路径内容，输出变成泛化猜测或要求人工补上下文 | builder 必须把 local reference 正文拼进 prompt |
| 嵌入 fence 断裂 | 被嵌入文件含有 ```，导致 target body 或 module 跑出 code fence | prompt builder 按 body 中最长反引号动态选择更长 fence |
| Quota failure 被当普通失败 | 429 后继续提交后续对象 | hard stop，取消 futures，记录 remaining targets |
| 动态输入不可审计 | 只能证明 prompt 模板版本，不能证明 worker 看了哪个对象内容 | 每条 result 记录 `input_payload_hash` |
| 模块漂移不可定位 | 只有 `static_prompt_hash`，不知道是 general、customize 还是 data module 变了 | 分别记录三段 module hash |
| Stale overwrite | 旧结果覆盖新 ledger | 写回前重读状态，不是 pending 就 skip |
| 无审计日志 | 只能从聊天或终端回忆运行过程 | 每次 run 写 JSONL run log |
| 低成本模型越权 | triage 模型直接写最终判断 | triage 只决定是否升级，高风险解释留给最终 worker / reviewer |

---

**最后更新**: 2026-05-04
