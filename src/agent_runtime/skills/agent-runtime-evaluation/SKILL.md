---
name: agent-runtime-evaluation
description: 使用已安装的 agent-runtime-evaluate，对指定 root 中已固定 Reviewer 默认能力的已注册单节点 Workflow 做普通自测，检查执行事实并交给对象所属语义校验。不注册对象、不保存 PG、不修改默认模型配置。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_evaluation
  first_authority_ref: agent_runtime/design_contract/agent_runtime_00_execution_charter.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime 普通自测

## 1. Task

使用 `agent-runtime-evaluate` 对已有注册定义和本次输入运行一次普通自测，报告实际执行结果。
当前 CLI 支持范围见 §3，通过现有 Runtime 内核调用模型，不需要 PG 或生产授权服务。
它不会注册 source，也不会改写 `.runtime` 中已有的注册定义或日常模型配置。工具入口先共用轻量
setup，首次或缺项时补齐本地环境和随包操作 Skill；已就绪时不重复写入，无需单独安装 Skill。

## 2. Reader Gain

Agent 能从已注册 Workflow 找到固定 Module 与输入 schema，使用本次独立模型参数或 Runtime 默认
执行测试，分清技术执行完成、输出语义有效和材料审核结论，并如实说明证据是否被保留。

## 3. Entry and Exit

“测试这个 Reviewer”“在这个 root 跑一次已注册定义”进入本 Skill。当前入口支持注册时已固定
Reviewer 默认能力的单节点 Workflow，其 Module 带有 `ReviewerDefaults` 能力快照；执行前由
Runtime 检查该定义是否允许 evaluation，以及能力与本次执行配置的相容性。
通过 `register-reviewer` 注册时，这些默认能力由 Runtime 自动提供，调用者无需额外选择或组装。
只有 Module、尚无可用 Workflow 时先使用 `agent-runtime-registration` 处理已授权注册，
本 Skill 不临时拼装图、复制 Reviewer 或手补能力快照。

多节点图、生产操作、PG 持久执行、历史 execution 查询或跨进程恢复，使用宿主已有的对应入口。
不要把它们降格为普通自测后宣称完成。模型、工具或输入所需能力超出当前 CLI 支持范围时，保留
不相容结果并交给对应负责人，不自动换模型或放宽声明。

## 4. Execution Contract

### 4.1 Inputs and Authority

- 调用者明确的 root、已注册单节点 Workflow ID，以及本次模型调用和材料使用的授权。
- 可选准确版本。未指定时由 Runtime 加载最新已注册定义；复验某个已知版本时显式指定。
- 对象所属工具或 owner 按已注册 Module input schema 准备的 JSON 输入，以及适用的输出语义
  validator 或判断依据。未提供材料时先补齐，不从整个 Workspace 搜集输入。
- 宿主已安装且可用的 Provider CLI。可选本次 transport、model、effort 和 CLI 路径；
  省略执行参数时使用 Runtime 默认，不从环境名称推导模型或重新绑定 Profile。
- 仅在需要留存时提供明确的证据输出位置。普通运行默认只返回结果。

准确支持范围、参数和错误来自当前安装的 CLI 与随包文档，见 §6.1。root 只定位注册定义，
不向模型开放整个项目，也不授权访问生产数据。Provider 登录仍由宿主维护；缺失时报告，
不自行开启登录或修改环境配置。

### 4.2 Output and Completion

保留 CLI 原始结果，报告实际 Module、Workflow 版本与 hash、本次模型和 effort、执行状态、
输出或 failure detail，以及实际可取得的用量。字段来自 Runtime 返回，不能手写一份执行日志代替。

退出成功只表明执行完成并取得符合注册 output schema 的输出。随后调用对象所属的语义 validator，
或按调用者给出的判断依据解释结果；未执行所需语义校验时明确标记未完成该项。
Reviewer 返回有效 `non_pass` 是材料需要修改的结论，不是技术重试理由。

本命令返回 `persistence=not_requested`，指执行事实不写持久存储，不排除前置 setup 补齐环境文件。
临时资源退出时清理。execution ID 不能用于之后查询
持久历史；明确保存 stdout 可以保留测试证据，但不构成 PG Ledger 或可恢复请求回执。
失败时保留 stdout 中已有执行事实和 stderr 原错误，不用空响应制造 Reviewer verdict。

## 5. Boundaries

| 边界 | 可观察的越界 |
| --- | --- |
| 已注册定义与新材料分开 | 每次自测重注册 prompt，或修改 .runtime 定义 |
| 模型选择与能力相容性分开 | 换模型时改工具权限，或为通过测试删 source 声明 |
| 本次资源由请求限定 | 把 root 当模型可读项目全集，或从业务配置搜 PG 凭据 |
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
print(package.joinpath("docs/agent_runtime_reviewer_api.md"))
```

读 runbook §0.3 与自动 API reference 的 Evaluation CLI、evaluate_local_workflow_module。
当前支持 `claude_cli`；具体默认模型和重试规则由 Runtime 提供，不复制到本 Skill。
接口缺失或安装不匹配时交安装维护者，不从生产源码拼 Provider 启动器。

需要确认定义时使用现有回读命令。它只读注册定义；前置 setup 在首次或缺项时仍可能写入环境资源：

```sh
agent-runtime-registry load --root /path/to/host --kind workflow \
  --id registered_workflow_id --version selected_version
```

身份与版本换成本次目标。未要求固定版本时可省略版本；若先回读确定了准确测试对象，执行时
使用该次返回的版本，避免再次选择 latest。从返回的固定 bundle 取得节点 Module 和 schema；
已有输入准备工具时直接用其公开入口，不从可变作者目录重新组装 Reviewer。

### 6.2 执行一次普通自测

```sh
agent-runtime-evaluate --root /path/to/host --workflow registered_workflow_id \
  --version selected_version --input /path/to/prepared_input.json
```

只有本次请求需要时才加 `--transport`、`--model`、`--effort` 或 `--cli-path`。
参数与允许值查实际 help；注册记录中旧的模型绑定不成为这个入口的默认。提供材料按其 schema
约定使用，Runtime 负责原有隔离和工具映射，操作者不另拼 read/search/shell 或 Read/Grep/Bash 配置。

需要留存时，将本次 stdout 捕获到调用者明确的新证据文件，保留 stderr 和退出码；未请求时直接返回。
不添加当前 CLI 不支持的持久保存参数。

### 6.3 判断结果与停止

先检查退出码和实际 `status`，再做对象所属输出语义检查。准确退出码含义见同版本自动接口说明，
不要把技术失败、用法错误、中断或业务 `non_pass` 混为一类。

Runtime 内部技术重试由原有策略决定。再次启动 CLI 是新的测试，不能据此恢复上一次临时执行。
超时、丢失响应或中断后先报告已有事实和不确定部分，不自动重跑整条命令。输入或模型明确变化，
并获得新测试请求时，再启动相应调用；历史持久执行按宿主日志查询入口读取原记录。

测试结束交出实际执行事实、语义校验结果和剩余限制。自测成功不等于生产部署或对象的发布批准。
