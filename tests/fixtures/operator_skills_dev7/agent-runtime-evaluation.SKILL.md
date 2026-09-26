---
name: agent-runtime-evaluation
description: 使用已安装的 agent-runtime-evaluate，自测已注册的单节点 Module Workflow，或注册并运行随包多 Agent 样例。报告执行事实并按对象标准解释结果；不写 PG 或修改默认模型配置。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_evaluation
  first_authority_ref: agent_runtime/design_contract/agent_runtime_00_execution_charter.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime 普通自测

## 1. Task

使用 `agent-runtime-evaluate` 执行调用者选定的自测，报告可追溯的执行事实和业务结果。
`--workflow` 执行已有单节点定义；`--example` 注册并执行 Runtime 随包固定多 Agent 样例。
两条路径通过同一 Runtime 内核调用模型，不需要 PG 或生产授权服务，不修改日常模型配置。
工具入口先共用轻量 setup，首次或缺项时补齐本地环境和两份随包操作 Skill；已就绪时不重复写入。

## 2. Reader Gain

Agent 能选择已注册 Workflow 自测或固定样例，确认各自输入与注册副作用，用现有入口执行；能从
实际记录区分技术完成、业务结论、等待与失败，并知道哪些证据可保留、哪些执行不能继续。

## 3. Entry and Exit

“测试这个 Module”“测试这个 Reviewer”“在这个 root 跑一次已注册定义”进入本 Skill 的普通路径；
“运行 Runtime 随包多 Agent 能力样例”进入样例路径。先按请求选定路径，不能把固定样例代替业务验收。
普通路径使用已注册的单节点 Workflow，由 Runtime 从其中的 Module 解析通用运行要求。历史定义按同版本
兼容规则读取，不要求操作者检查或补写 `ModuleExecutionRequirements` 字段。Module 无需属于
Reviewer，也不以 `ReviewerDefaults` 快照作为进入条件。Runtime 根据解析的运行要求、evaluation
用途及本次 Adapter 的真实能力判断能否执行，信息不足时返回具体缺口。

当前已交付入口支持 Claude CLI，以及显式选择 Codex 的无工具、inline、workspace none、network
denied 组合。Codex 不会把有工具 Module 降为无工具；完整相容性以当前安装的 CLI 和自动 API 文档
为准。普通路径可通过 `--resources` 提供明确材料与工程命令，格式和支持条件见 §6.2。
请求超出当前公开参数时，报告准确缺口，交 Runtime 维护者处理，不临时拼执行器或增加参数。

只有 Module、尚无可用 Workflow 时，使用 `agent-runtime-registration` 处理已授权注册；本 Skill
不临时拼图或复制 Reviewer。样例路径仅支持 CLI 列出的固定图，并要求在指定 root 注册样例和调用
模型的授权。任意业务多节点图、生产操作、PG 持久执行、历史 execution 查询或跨进程恢复，使用
宿主已有的对应入口。请求不相容时保留原错误，不自动换模型、资源或放宽声明。

## 4. Execution Contract

### 4.1 Inputs and Authority

- 调用者明确的 root、普通路径的已注册单节点 Workflow ID 或样例路径的固定 example 名称，
  以及本次模型调用和材料使用的授权；样例还需要在该 root 注册其固定定义的授权。
- 普通路径可选准确版本。未指定时由 Runtime 加载最新已注册定义；复验某个已知版本时显式指定。
- 普通路径使用对象所属工具或 owner 按已注册 Module input schema 准备的 JSON 输入，以及适用的输出语义
  validator 或判断依据。未提供材料时先补齐，不从整个 Workspace 搜集输入。
- 宿主已安装且可用的 Provider CLI，以及当前入口所需的登录条件。可选本次 transport 和 CLI 路径；
  Codex 必须显式提供 model 和 effort。省略 transport 继续 Runtime 的 Claude 默认，不能从模型名
  或环境名推断 Provider。模型默认值和支持语法由 Runtime 与同版本 CLI 说明，不在 Skill 保存另一份表。
- 仅在需要留存时提供明确的证据输出位置。普通运行默认只返回结果。

样例不接受 `--version` 或 `--resources`。省略样例输入时使用随包测试材料；提供输入时只接受
`task` 和 `required_facts`，不用普通 Module 的任意 schema 替代这两个字段。固定样例定义、状态
分支和工具能力以同版本 runbook 为准；当前带工具样例走 Claude CLI。

准确支持范围、参数和错误来自当前安装的 CLI 与随包文档，见 §6.1。root 只定位注册定义，
不向模型开放整个项目，也不授权访问生产数据。Provider 登录仍由宿主维护；缺失时报告，
不自行开启登录、搜索其他账号或修改环境配置。

### 4.2 Output and Completion

保留 CLI 原始结果，报告实际 Module、Workflow 版本与 hash、本次模型和 effort、执行状态、
输出或 failure detail，以及实际可取得的用量。使用返回的 `execution_log` 查看每个 Attempt 已采集的
原始流、工具记录和完整性说明；未知或缺失的记录如实保留，不能由 Agent 补写执行日志。
这个详细视图由自测入口显式调用 Inspection 取得。普通模型运行负责 metadata、最终 result 与原文
归档，工具日志的解析缺口不改变其已记录终态；行为或证据是否合格按本次明确的评价要求判断。
样例从 `execution.nodes` 和 `child_executions` 读取逐次记录，结合 `stop_reason` 与 `failure` 判断
进度。准确字段和继续条件见 §6.1 的同版本 API；没有错误码时保留实际异常类型和诊断。

退出成功只表明执行完成并取得符合注册 output schema 的输出。随后调用对象所属的语义 validator，
或按调用者给出的判断依据解释结果；未执行所需语义校验时明确标记未完成该项。
Reviewer 返回有效 `non_pass` 是材料需要修改的结论，不是技术重试理由。

本命令返回 `persistence=not_requested`，指执行事实不写持久存储，不排除前置 setup 补齐环境文件。
已注册定义仍留在 `.runtime`，不随本次临时执行资源清理。
正常退出时清理本次临时资源；若返回私有资源保留或清理失败，按 Runtime 的错误说明交给环境负责人，
不自行删除恢复目录。execution ID 不能用于之后查询持久历史；明确保存 stdout 可以保留测试证据，
但不构成 PG Ledger 或可恢复请求回执。完整执行日志可能含私有材料，只交给本次已授权的读取者。
失败时保留 stdout 中已有执行事实和 stderr 原错误，不用空响应制造 Reviewer verdict。

## 5. Boundaries

| 边界 | 可观察的越界 |
| --- | --- |
| 普通执行与固定样例注册分开 | 用 --workflow 重注册 prompt，或未获注册授权就执行 --example |
| 模型选择与能力相容性分开 | 换模型时改工具权限，清空 Module 要求以通过测试，或把旧 Profile 当新调用默认 |
| 本次资源由请求限定 | 把 root 当模型可读项目全集，或从业务配置搜 PG 凭据 |
| Runtime 负责执行配置 | 操作者叠加未声明 CLI 开关、拼 Provider 启动器或使用虚构的生产授权 |
| 执行与语义结论分开 | 用退出成功代替材料通过，或反复重跑直到得到 passed |
| 临时测试与持久恢复分开 | 把保存的 stdout 叫 Ledger，或声称可用此次 ID 跨进程重放 |

## 6. Method

### 6.1 定位现有接口和固定定义

先读 `agent-runtime-evaluate --help`，确认当前安装支持的 transport 和参数。
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

普通路径读 registration runbook §0.3，以及自动 API reference 的 Evaluation CLI、evaluate_local_workflow_module。
默认模型、Provider 认证支持、能力限制和失败处理使用这些同版本说明。接口缺失、安装不匹配或
注册定义缺少当前准备所需运行要求时，交给 Runtime 或定义负责人；不从生产源码拼 Provider 启动器。

样例路径读 capability runbook 的“随包多 Agent 样例”与“判断结果与继续”；准确返回和等待边界
见 API reference 的 `run_agent_example`、`WorkflowSelfTestResources`。

需要确认定义时使用现有回读命令。它只读注册定义；前置 setup 在首次或缺项时仍可能写入环境资源：

```sh
agent-runtime-registry load --root /path/to/host --kind workflow \
  --id registered_workflow_id --version selected_version
```

普通路径的身份与版本换成本次目标。未要求固定版本时可省略版本；若先回读确定了准确测试对象，执行时
使用该次返回的版本，避免再次选择 latest。从返回的固定 bundle 取得节点 Module 和 schema；
已有输入准备工具时直接用其公开入口，不从可变作者目录重新组装 Reviewer。

### 6.2 执行一次普通自测

使用 Runtime 默认执行选择：

```sh
agent-runtime-evaluate --root /path/to/host --workflow registered_workflow_id \
  --version selected_version --input /path/to/prepared_input.json
```

对符合当前 Codex 无工具要求的已注册 Module，且调用者已明确选择 Codex 时，使用同一个入口：

```sh
agent-runtime-evaluate --root /path/to/host --workflow registered_workflow_id \
  --version selected_version --input /path/to/prepared_input.json \
  --transport codex_cli --model selected_model --effort selected_effort
```

`selected_model` 和 `selected_effort` 换成本次明确的真实值，不写入 source 或另存环境 Profile。
需要指定宿主已安装的程序时增加 `--cli-path /path/to/provider_cli`；省略时由 Runtime 解析所选程序。
提供材料按其 schema 约定使用，Runtime 负责当前已支持的隔离、工具映射、私有状态和启动条件。
操作者不另拼 read/search/shell 或 Read/Grep/Bash 配置，也不按本次任务擅自改默认能力。

已有明确材料或工程命令时，普通路径增加 `--resources /path/to/resources.json`。文件中的材料根、
准确文件清单、只读依赖和命令项使用同版本 `--help` 或 API 中的真实 schema；路径相对该资源文件
解析，命令工作目录相对本次 source/scratch。命令项供 Agent 按 ID 选择本次提供的命令，不把普通
Bash 限制成该列表，也不把整个 root 变成模型材料。不要把模型、凭据或生产授权写进该文件。

需要留存时，将本次 stdout 捕获到调用者明确的新证据文件，保留 stderr 和退出码；未请求时直接返回。
仅使用当前 CLI 已公开的参数，不追加未支持的持久保存或执行配置开关。

### 6.3 执行随包多 Agent 样例

确认调用者需要固定能力验证，并已授权样例注册和模型调用后，使用同一命令：

```sh
agent-runtime-evaluate --root /path/to/host --example agent_capability_example
agent-runtime-evaluate --root /path/to/host --example agent_evaluation_example
```

只运行本次需要的样例。前者验证多节点顺序、查询工具、并行审核和选择；后者验证被测 Agent 自己
请求任务内 Reviewer，再由独立评价 Agent 读取实际证据。需要专门验证修订或等待分支时，前者增加
`--scenario revision` 或 `--scenario wait`。这些是刻意选择的测试分支；wait 在同一进程自动收到
匹配测试事件，不提供交互式暂停或跨进程恢复。

### 6.4 判断结果与停止

先检查退出码和实际 `status`，再做对象所属输出语义检查。准确退出码含义见同版本自动接口说明，
不要把技术失败、用法错误、中断或业务 `non_pass` 混为一类。某次工具被拒绝或报错不自动等于整次
调用失败；以 Runtime 的实际终态、输出校验和相应工具记录分别说明。

多节点路径还要核对已提交的 `execution.outcomes`。WAIT 或资源失效后，最后快照都可能仍为
running；结合停止原因和错误判断能否继续，不能只看这个字段。

Runtime 内部技术重试由原有策略决定。再次启动 CLI 是新的测试，不能据此恢复上一次临时执行。
超时、丢失响应或中断后先报告已有事实和不确定部分，不自动重跑整条命令。输入或模型明确变化，
并获得新测试请求时，再启动相应调用；历史持久执行按宿主日志查询入口读取原记录。

测试结束交出实际执行事实、语义校验结果和剩余限制。固定样例只证明所列路径，不代替完整能力
目录的验收；自测成功也不等于生产部署或对象的发布批准。
