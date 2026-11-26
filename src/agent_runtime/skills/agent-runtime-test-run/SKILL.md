---
name: agent-runtime-test-run
description: 使用已安装的 agent-runtime-test-run 执行测试：注册时写出的定义选择文件所指的已注册单节点 Module Workflow（定义来自 root 或已配置的只读 PostgreSQL store），或宿主受限的随包多 Agent callback 样例。交出保存的执行记录与证据，并按对象所属标准解释结果；行为评价和故障归因交各自负责人，不写 PG、不修改默认模型配置。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_test_run
  first_authority_ref: agent_runtime/design_contract/agent_runtime_00_execution_charter.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime Test Run

## 1. Task Summary

使用 `agent-runtime-test-run` 执行调用者选定的测试，交出可追溯的执行事实和证据。普通路径用
`--selection` 执行注册（或 `agent-runtime-registry select`）写出的定义选择文件所指的准确单节点定义，定义来自 root
或宿主已配置的 PostgreSQL store；已持有准确值的程序仍可用低层的 `--workflow`（root 下的定义）或 `--workflow-ref`
加 `--workflow-sha256`（PostgreSQL 中的准确定义）。`--example` 注册并执行 Runtime 随包固定多 Agent 样例。各路径通过
同一 Runtime 内核调用模型，不写 PG，不需要生产授权服务，不修改日常模型配置。Test Run 先做轻量 setup，首次或缺项时补齐本地环境和三份随包
操作 Skill；已就绪时不重复写入。

“测试这个 Module”“测试这个 Reviewer”“在这个 root 跑一次已注册定义”“用 PG 里这个准确版本测一次”
进入普通路径；“运行 Runtime 随包多 Agent 能力样例”进入样例路径。还没决定任务该用哪种 Module 要求、
每次调用要给哪些目录和许可时，先用 `agent-runtime-usage` 选择常用模式与 preset，再回到本 Skill 执行。

本 Skill 只负责执行测试并交出证据。对象输出是否合格由对象所属 validator 或调用者给出的依据判断；
Agent 行为的专门评价使用 Runtime 的 Behavior Evaluation 能力；执行失败的根因分析交 Runtime 维护者
按 Inspection 取证。

## 2. Intended Outcomes

1. **选对路径并确认副作用。** 执行 Agent 能在选择文件、低层本地或 PG 准确定义和固定样例之间选择，事先说明
   各自的输入、注册副作用（只有样例会在 root 注册其固定定义）和所需授权，不把固定样例当作业务验收。
2. **执行事实可追溯。** 执行的正是所选定义：使用选择文件时，记录的 `definition_selection` 给出所用选择，
   `executed_matches_selection` 由代码比较执行身份得出，之后注册的新版本不会替换它。交付的结果写明实际 Module、
   Workflow 版本与 hash，本次 transport、模型、effort、时限及每项的来源层，执行状态、输出或 failure detail、用量、
   `execution_log` 和 Attempt 文件夹位置；请求要求留存时，记录保存在请求指定的新文件中，可用
   `agent-runtime-inspect --test-run-record` 读取，不需要抄写 execution ID 或内部路径。请求方据此核对本次到底运行了
   什么，维护者据此复查失败。
3. **技术结果与业务结论分开。** 交付能区分技术完成、业务 `non_pass`/`blocked`、等待、取消与失败；退出成功只表示
   取得符合注册 output schema 的输出，语义是否合格另由对象所属 validator 判断，未判断时明确标记未完成。
4. **不能继续的执行被识别。** 资源失效、等待未满足或清理未确认时，交付说明哪些证据可保留、哪些执行不能继续，
   不把保存的记录或 stdout 说成可恢复记录。

## 3. Boundaries

普通路径使用已注册的单节点 Workflow，由 Runtime 从其中的 Module 解析通用运行要求。历史定义按同版本
兼容规则读取，保持其保存的工具与网络要求，不按版本标签推断；操作者不检查或补写 `ModuleExecutionRequirements`
字段。Module 无需属于 Reviewer，也不以 `ReviewerDefaults` 快照作为进入条件。Runtime 根据解析的运行要求、
测试用途及本次 Adapter 的真实能力判断能否执行，信息不足时返回具体缺口。

当前入口支持 Claude CLI（`claude_cli_adapter@v4`：无工具或原生 Read/Grep/Glob/Edit/Write/Bash 与声明的 MCP，
workspace 为 none、私有草稿或调用者的项目目录），以及 Codex CLI（无工具 `codex_cli_agent_executor@v5`；
带工具 `codex_cli_agent_workspace_executor@v5`：read、search、edit 经 shell 提供故须含 `shell`，或只用声明的 MCP 而
关闭 shell）。Codex 不支持逐条命令许可，收到 `--command-permit` 时报告 `ADAPTER_CAPABILITY_UNSUPPORTED`。
Antigravity CLI（`antigravity_cli_adapter@v1`）首个绑定只运行 `structured_output` 与 `file_review` 两种要求形状，
需显式模型与 effort；命令只按 `--command-permit` 允许（agy 规则体，已实测写法为 `regex:` 加完整命令的
`re.escape`，由 `antigravity_command_permit` 生成，它只转义给定字符串）。任务输入里的每条命令与其许可须来自
同一字符串，并要求每条命令单独一次工具调用（见随包 registration runbook 的 `antigravity-command-permits`）。
其他形状在启动模型前报告 `ADAPTER_CAPABILITY_UNSUPPORTED`。
Runtime 不会把有工具 Module 降为无工具；完整相容性以当前安装的 CLI 和自动 API 文档为准。请求超出当前公开
参数时，报告准确缺口，交 Runtime 维护者处理，不临时拼执行器或增加参数。

只有 Module、尚无可用 Workflow 时，使用 `agent-runtime-registration` 处理已授权注册；本 Skill
不临时拼图或复制 Reviewer。样例路径仅支持 CLI 列出的固定图，并要求在指定 root 注册样例和调用
模型的授权。这两个样例使用显式选择的 callback 工具，是宿主受限的高级扩展：需要 macOS/Linux 与 `cli_tools`，
Windows 在打开会话前报告不支持，Runtime 不为此增加 Windows IPC。四种常用模式的可运行样例见
`agent-runtime-usage`。任意业务多节点图、生产操作、PG 持久执行、历史 execution 查询或跨进程恢复，使用
宿主已有的对应入口。请求不相容时保留原错误，不自动换模型、资源或放宽声明。PG 定义缺失、hash
不符或不是单节点时，Runtime 直接拒绝，不回退到 root 下的同名定义。

| 边界 | 可观察的越界 |
| --- | --- |
| 普通执行与固定样例注册分开 | 用 --workflow 重注册 prompt，或未获注册授权就执行 --example |
| 定义与材料的机器字段由代码形成 | 把注册输出中的 ID、ref、hash 抄成 --workflow-ref/--workflow-sha256，编辑选择文件，或手算材料 sha256 写进资源文件 |
| 模型选择与能力相容性分开 | 换模型时改工具权限，清空 Module 要求以通过测试，或把旧 Profile 当新调用默认 |
| 本次资源由请求限定 | 把 root 当模型可读项目全集，或从业务配置搜 PG 凭据 |
| Shell 范围由每次许可决定 | 给 Claude 开放裸 Bash，或把 Codex 不支持的许可删掉后假称已受限 |
| DSN 只交给定义读取 | 把 DSN 写进命令行、输入、证据文件，或放进 Provider 会转交的环境变量 |
| 执行参数与定义版本分开 | 在参数文件里写版本或定义目标，或手改注册文件来换模型 |
| 同步预算包含受管理子调用与实际重试 | 为每次重试重置完整时限，或让子调用超过父级剩余期限 |
| Runtime 负责执行配置 | 操作者叠加未声明 CLI 开关、拼 Provider 启动器或使用虚构的生产授权 |
| 执行与语义结论分开 | 用退出成功代替材料通过，或反复重跑直到得到 passed |
| 临时测试与持久恢复分开 | 把保存的记录叫 Ledger，或声称可用此次 ID 跨进程重放 |

## 4. Inputs and Completion

### 4.1 Inputs

- 调用者明确的 root、普通路径的定义选择文件（或下述低层定义参数）或样例路径的固定 example 名称，
  以及本次模型调用和材料使用的授权；样例还需要在该 root 注册其固定定义的授权。
  新执行使用当前编码的 Module 要求。旧 Reviewer 定义被拒绝时，按 §5.1 定位同版本 registration runbook
  的“升级已有 Reviewer”（anchor `upgrade-existing-reviewer`），再交给 registration Skill；本 Skill 只在准确
  新版本可读取后测试，不手改旧 JSON，也不为每个时间值另注册 Reviewer。
- 普通路径的定义来自 `agent-runtime-registration` 交来的选择文件（注册的 `--selection-out` 或 `select`
  写出），它属于创建它的 root。定义在 PostgreSQL 时，选择文件只记录宿主在 `root/.runtime/config.json` 的
  `release_stores` 中配置的 store 名称；该配置写 DSN 所在的环境变量名和 schema，DSN 本身只放在该环境变量里，
  不写进命令、输入、证据文件或 Skill；变量名不能与 Provider 环境白名单重名（如 `HOME`、`LOGNAME`、代理变量），
  否则 Runtime 拒绝。尚无选择文件时，先请注册负责人写出，不从注册输出抄写身份、ref 或 hash。
- 已在程序中持有准确值的调用者仍可用低层定义参数：本地 Workflow ID 加可选准确版本（未指定时加载最新已注册
  定义），或 PG 的准确 `workflow_release_ref`、`workflow_release_sha256`、DSN 环境变量名与 release schema；
  可加 `--expected-module-id`，不一致时在读取材料和调用模型前停止。
- 普通路径使用对象所属工具或 owner 按已注册 Module input schema 准备的 JSON 输入，以及适用的输出语义
  validator 或判断依据。未提供材料时先补齐，不从整个 Workspace 搜集输入。
- 按 Module 的 workspace 要求提供本次资源：材料用 `--material-root` 指定所在文件夹，再用可重复的 `--material`
  选出文件，或明确用 `--all-materials` 交出整个文件夹；相对路径、sha256 与执行位由代码按实际 bytes 计算。
  事先冻结的清单与只读依赖用 `write_local_resources` 写出的 `--resources` 文件；`explicit_project_read_write`
  用 `--project-directory`（调用者的 checkout、worktree 或输出目录）；Claude 需要运行命令时用可重复的
  `--command-permit`（如 `"python -m pytest:*"`）。Claude 自带只读命令无需许可；任务要求运行、需要额外批准的检查命令，
  许可须覆盖其实际写法。宿主选好的 MCP 连接写在 `root/.runtime/config.json`（用 `agent-runtime-setup --mcp <name>` 写入）。
  Codex 无法交互式确认，所以仍需本地确认的工具
  在该连接中设 `default_tools_approval_mode: "approve"`；`enabled_tools`（可选）只限定选定的工具，不是审批开关
  （见 `agent_runtime_claude_native_tools.md` 的 `codex-mcp-unattended-tools`）。
- 宿主已安装且可用的 Provider CLI，以及当前入口所需的登录条件。Windows 只启动原生 `claude.exe`、`codex.exe`、`agy.exe`；
  Claude 的 Bash 需要 Git for Windows。transport、model、effort 和同步运行时限逐项取本次
  参数、`root/.runtime/execution_parameters/workflows/<workflow_id>.json`、
  `root/.runtime/execution_parameters/workspace.json`，最后是 Runtime 默认。参数文件是 JSON 对象：
  `runtime_execution_parameters_v1` 保留 `transport_kind`、`model_id`、`reasoning_profile` 三字段；
  `runtime_execution_parameters_v2` 另外允许 `run_timeout_seconds`，其值为整数秒。未知键或非法值
  均拒绝，文件不选择定义版本；格式说明与示例见随包 `agent_runtime/docs/agent_runtime_registration_runbook.md`
  （anchor `test-reviewer`）。文件无效时准备失败，不跳到下一层。model 或 effort 可以来自任一层，但该层及其以下各层得到的
  transport 必须等于最终 transport，否则 Runtime 拒绝；Codex 与 Antigravity 没有默认模型和 effort，不会从
  Claude 默认补齐。不能从模型名或环境名推断 Provider。模型默认值和支持语法由 Runtime 与同版本 CLI 说明，不在
  Skill 保存另一份表。
- 仅在需要留存时提供明确的证据输出位置：一个尚不存在、所在文件夹已存在的 `--record-out` 文件。普通运行默认
  只返回结果。

样例不接受 `--version`、`--selection`、`--resources`、材料参数或 `--record-out`。省略样例输入时使用随包测试材料；提供输入时只接受
`task` 和 `required_facts`，不用普通 Module 的任意 schema 替代这两个字段。固定样例定义、状态
分支和工具能力以同版本 runbook 为准；当前带工具样例走 Claude CLI。

准确支持范围、参数和错误来自当前安装的 CLI 与随包文档，见 §5.1。root 在每种定义来源都必填，只定位
setup、注册定义和参数文件，不向模型开放整个项目，也不授权访问生产数据。Provider 登录仍由宿主维护；缺失时报告，
不自行开启登录、搜索其他账号或修改环境配置。程序位置或登录状态不明时，`agent-runtime-setup --root <root> --provider <cli>`
给出所选程序与登录状态；本人完成登录后再运行。

### 4.2 Output

保留 CLI 原始结果，报告实际 Module、Workflow 版本与 hash（使用选择文件时连同 `definition_selection` 与
`executed_matches_selection`；请求留存时连同 `--record-out` 文件位置）、本次模型和 effort 及
`execution_parameter_sources` 中每项的来源层与参数文件 hash、执行状态、输出或 failure detail，以及
实际可取得的用量（`input_tokens` 是含缓存的输入总量；普通输入、缓存读取与缓存创建可能未知，未知是 `null`，
含义见随包 README 的 token 段）。结果中的 `execution_budget` 区分请求时限、有效时限、耗时和父级限制；
子调用请求一小时不代表实际获得一小时。旧记录缺失的预算信息不补造。使用返回的 `execution_log` 查看每个 Attempt 已采集的
原始流、工具记录和完整性说明；未知或缺失的记录如实保留，不能由 Agent 补写执行日志。
这个详细视图由 Test Run 显式调用 Inspection 取得。普通模型运行负责 metadata、最终 result 与原文
归档，工具日志的解析缺口不改变其已记录终态；行为或证据是否合格按本次明确的评价要求判断。
样例从 `execution.nodes` 和 `child_executions` 读取逐次记录，结合 `stop_reason` 与 `failure` 判断
进度。准确字段和继续条件见 §5.1 的同版本 API；没有错误码时保留实际异常类型和诊断。

普通路径的 `attempt_workspaces` 列出每个 Attempt 文件夹（默认在 `root/.runtime/attempt_workspaces`），其中保存
材料副本、scratch 和 `archive/execution_record.json`；执行结束、所启动的 CLI 进程确认停止且留档保存后才封存，
否则给出 `seal_skipped_reason`。这些文件夹不会自动删除，由宿主用 `maintain_attempt_workspaces` 查询或清理已封存且
过期的文件夹；调用者的项目目录从不进入清理集合。只有 `--example` 样例路径的工作目录是临时目录，结束后删除；
`agent-runtime-usage` 的四种预设与 `native_project` 样例的 Attempt 文件夹保留在其运行文件夹的 `attempts` 下。
root 配置了中央 `run_archive` 且调用者要求留档时，在运行结束后用 `agent-runtime-archive save --root ROOT --record
RECORD --receipt-out RECEIPT` 保存本次记录与 Attempt 文件并从数据库读回核对，`cleanup --receipt RECEIPT` 只删本次
Attempt 现场，`read --receipt RECEIPT` 从数据库取回记录、日志与文件。`RECORD` 是 `--record-out` 写出的完整记录文件，
不是 inspect 摘要；留档命令所在的 Python 环境需安装 `agent-runtime-core[postgres]`。分别报告
收据的 `run_status`（运行结果）与 `archive_status`（留档结果）；留档失败或保留原因照实交回，不自行删除现场。
项目目录中的业务产出不在 Attempt 内，不进入留档。说明与可运行样例见随包 registration runbook 的 `archive-test-run`
节（样例 anchor `archive-test-run-example`）。

本命令返回 `persistence=not_requested`，指执行事实不写持久存储，不排除前置 setup 补齐环境文件，也不妨碍运行结束后
用上述 `agent-runtime-archive` 显式留档这份记录。
已注册定义仍留在 `.runtime`。execution ID 不能用于之后查询持久历史。`--record-out` 把同一 JSON（运行未返回记录时
为错误文档）存为新文件，用 `agent-runtime-inspect --test-run-record FILE --view summary|output|log|files` 查看结果、
执行日志与 Attempt 文件；查看不注册、不重跑、不连 PG，`durable_history=not_available` 表示它是测试证据，不构成
PG Ledger 或可恢复请求回执。完整执行日志可能含私有材料，只交给本次已授权的读取者。

### 4.3 Completion Contract

完成需要：所选路径实际执行；使用选择文件时 `executed_matches_selection` 为 true；交付第 4.2 节的执行事实；对象所属的
语义 validator 已运行，或明确标记该项未完成。`executed_matches_selection` 为 false 时，本次结果不作为所选定义的
证据，保留记录交 Runtime 维护者。
退出成功只表明执行完成并取得符合注册 output schema 的输出。Reviewer 返回有效 `non_pass` 是材料需要修改的
结论，不是技术重试理由。某次工具被拒绝或报错不自动等于整次调用失败；以 Runtime 的实际终态、输出校验和
相应工具记录分别说明。

失败时保留 stdout 中已有执行事实和 stderr 原错误，不用空响应制造 Reviewer verdict。若返回私有资源保留、
`process_stop_unconfirmed` 或清理失败，按 Runtime 的错误说明交给环境负责人，不自行删除恢复目录。
超时、丢失响应或中断后先报告已有事实和不确定部分，不自动重跑整条命令；输入或模型明确变化并获得新测试请求时，
再启动相应调用。固定样例只证明所列路径，不代替完整能力目录的验收；测试成功也不等于生产部署或对象的发布批准。

## 5. Method

### 5.1 定位现有接口和固定定义

先读 `agent-runtime-test-run --help`，确认当前安装支持的 transport 和参数。
在宿主明确的 Python 环境中，可用以下只读代码定位同版本说明：

```python
from importlib.metadata import version
from importlib.resources import files
import agent_runtime

print(version("agent-runtime-core"))
print(agent_runtime.__file__)
package = files("agent_runtime")
print(package.joinpath("design_contract/agent_runtime_00_execution_charter.md"))
print(package.joinpath("docs/agent_runtime_registration_runbook.md"))
print(package.joinpath("docs/agent_runtime_capability_runbook.md"))
print(package.joinpath("docs/agent_runtime_reviewer_api.md"))
```

普通路径读 registration runbook §0.3，以及自动 API reference 的 Test Run CLI、run_local_workflow_test。
默认模型、Provider 认证支持、能力限制和失败处理使用这些同版本说明。接口缺失、安装不匹配或
注册定义缺少当前准备所需运行要求时，交给 Runtime 或定义负责人；不从生产源码拼 Provider 启动器。

样例路径读 capability runbook 的“宿主受限的高级 callback 样例”与“判断结果与继续”；准确返回和等待边界
见 API reference 的 `run_agent_example`、`WorkflowSelfTestResources`。

选择文件由注册命令或 `agent-runtime-registry select` 写出，Test Run 每次按它重新读取准确定义，缺失、hash 不符
或不是单节点时直接拒绝，不回退到同名的其他版本。需要按所选定义准备输入时，让回读命令直接消费同一选择文件
（前置 setup 在首次或缺项时仍可能写入环境资源）：

```sh
agent-runtime-registry load --root /path/to/host --selection /path/to/registered.selection.json
```

结果中的 `input_schema` 与 `output_schema` 属于选择文件所指的准确版本（本地或已配置的 PG store 均可），另含该
Workflow、Module 与固定依赖。已有输入准备工具时直接用其公开入口，不从可变作者目录重新组装 Reviewer。
schema 之外的输入含义由对象所属工具或 owner 提供；依据不足时交回提供方。

### 5.2 执行一次普通测试

使用注册交来的选择文件，执行参数取参数文件或 Runtime 默认；需要留存时同时保存记录：

```sh
agent-runtime-test-run --root /path/to/host --selection /path/to/registered.selection.json \
  --input /path/to/prepared_input.json --record-out /path/to/new_record.json
agent-runtime-inspect --test-run-record /path/to/new_record.json --view output
```

调用者已明确选择 Codex 时，使用同一个入口并给出模型与 effort：

```sh
agent-runtime-test-run --root /path/to/host --selection /path/to/registered.selection.json \
  --input /path/to/prepared_input.json \
  --transport codex_cli --model selected_model --effort selected_effort
```

Antigravity 同样使用 `--transport antigravity_cli --model selected_model --effort selected_effort`，只适用于
`structured_output` 与 `file_review` 形状的定义。
`selected_model` 和 `selected_effort` 换成本次明确的真实值，不写入 source 或另存环境 Profile。
长期固定的项目或 Workflow 选择写进上述参数文件，由 Runtime 取值并在结果中记录来源。

需要临时延长一次运行时，在同一个命令增加 `--run-timeout-seconds 3600`。省略时按四层配置
解析，Runtime 默认 1200 秒；合法范围和参数类型以同版本 CLI 为准。该参数不会写回长期配置。
它约束本次同步工作及受管理的同进程子调用和实际重试；子调用不能延长父级期限。超时后的清理
可能使返回略晚于工作期限，不能据此声称整个函数精确在秒数内返回。普通 Shell 子进程、跨进程
Runtime 和跨调用 durable Workflow 寿命不自动继承这一保证。

定义保存在 PG 时，选择文件指向已配置的 store，命令只读取准确定义，root 仍用于 setup、资源配置和参数文件；
命令形式与上面相同。store 名称未配置或其 DSN 环境变量缺失时，命令在 setup 前报告该缺口，不猜其他 store。

需要指定宿主已安装的程序时增加 `--cli-path /path/to/provider_cli`；省略时由 Runtime 解析所选程序。
提供材料按其 schema 约定使用，Runtime 负责当前已支持的隔离、工具映射、私有状态和启动条件。
操作者不另拼 read/search/shell 或 Read/Grep/Bash 配置，也不按本次任务擅自改默认能力。

已有明确材料时，普通路径选出文件，hash 由代码计算：

```sh
agent-runtime-test-run --root /path/to/host --selection /path/to/registered.selection.json \
  --input /path/to/prepared_input.json --material-root "/path/to/task materials" \
  --material "notes/review notes.md" --material data.csv
```

`--material` 的路径相对 `--material-root`（或位于其中的绝对路径）；要交出整个文件夹时明确写 `--all-materials`，
文件夹中有链接或 reparse point 时被拒绝。准备之后材料变化或缺失时运行在调用模型前失败，hash 不会被刷新；
这种失败交材料提供方确认内容；确认后的再次运行是一次新的测试，由代码重新计算，不修补原清单或原资源文件。事先冻结的清单与只读依赖用 `write_local_resources` 写出的
`--resources` 文件提供（不能与 `--material-root` 同用）；其中记录型 `commands` 是另一项能力：只在 macOS
（sandbox-exec，需要 `cli_tools`）上按 ID 执行并记录，不把普通 Bash 限制成该列表。普通 Shell 命令由每次
`--command-permit` 预批准，例如编辑调用者项目时：

```sh
agent-runtime-test-run --root /path/to/host --selection /path/to/registered.selection.json \
  --input /path/to/prepared_input.json --project-directory /path/to/checkout \
  --command-permit "python -m pytest:*"
```

要让 Agent 在调用者项目里按项目指令和项目 Skills 工作时，用原生项目模式代替命令许可：加 `--native-project`
（Codex 另加 `--codex-execution-mode host`），不再给 `--command-permit`。Claude 加载 CLAUDE.md 与 `.claude/skills`，
Codex 加载 AGENTS.md 与 `.agents/skills`；Shell 以宿主权限运行，只传 Module 声明的 MCP 连接，每次是一次新的会话。
只接受 `code_change`／`mcp_data` 形状的 Module，材料包、记录型 commands、额外依赖和 Antigravity 会在调用模型前被拒绝：

```sh
agent-runtime-test-run --root /path/to/host --selection /path/to/registered.selection.json \
  --input /path/to/prepared_input.json --project-directory /path/to/project \
  --transport codex_cli --model MODEL --effort EFFORT --native-project --codex-execution-mode host
```

记录中每个 Attempt 的 `effective_configuration` 写明 `native_project`、`context_enabled`、`context_suppressed`；实际
调用了哪些 Skills、命令与 MCP 工具，用 `agent-runtime-inspect --view log` 看原始事件，不以配置或模型自述代替。

不要把模型、凭据或生产授权写进资源文件。需要留存时用 `--record-out` 写到调用者明确的新文件，并保留 stderr 和
退出码；已有文件不会被覆盖，命令在 setup 和模型调用前以用法错误退出。仅使用当前 CLI 已公开的参数，不追加未支持的
持久保存或执行配置开关。

### 5.3 执行随包多 Agent 样例

确认调用者需要固定能力验证、宿主是 macOS/Linux 且已安装 `cli_tools`，并已授权样例注册和模型调用后，
使用同一命令：

```sh
agent-runtime-test-run --root /path/to/host --example agent_capability_example
agent-runtime-test-run --root /path/to/host --example agent_evaluation_example
```

只运行本次需要的样例。前者验证多节点顺序、查询工具、并行审核和选择；后者验证被测 Agent 自己
请求任务内 Reviewer，再由独立评价 Agent 读取实际证据。需要专门验证修订或等待分支时，前者增加
`--scenario revision` 或 `--scenario wait`。这些是刻意选择的测试分支；wait 在同一进程自动收到
匹配测试事件，不提供交互式暂停或跨进程恢复。在 Windows 上，这两个样例会报告 callback 扩展不支持；
需要在 Windows 验证宿主环境时，改用 `agent-runtime-usage` 的四种常用模式样例。

### 5.4 判断结果与停止

先检查退出码和实际 `status`，再做对象所属输出语义检查。准确退出码含义见同版本自动接口说明，
不要把技术失败、用法错误、中断或业务 `non_pass` 混为一类。

多节点路径还要核对已提交的 `execution.outcomes`。WAIT 或资源失效后，最后快照都可能仍为
running；结合停止原因和错误判断能否继续，不能只看这个字段。

Runtime 内部技术重试由原有策略决定。再次启动 CLI 是新的测试，不能据此恢复上一次临时执行。
历史持久执行按宿主日志查询入口读取原记录。测试结束交出实际执行事实、语义校验结果和剩余限制。
