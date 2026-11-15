---
name: agent-runtime-usage
description: 为准备交给 Agent Runtime 运行的任务查找或选择四种常用模式之一（结构化输出、文件审核、代码修改并运行命令、MCP 取数落盘回读），给出该模式在代码中的 Module 要求预设、每次调用需要的组装、随包样例与学习入口；只有请求要求时才运行随包样例确认宿主环境，或通过普通注册与 Test Run 接口运行自己的任务。不编写业务 prompt、不选择默认模型、不处理 MCP 后端授权。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_usage_pattern
  first_authority_ref: agent_runtime/design_contract/agent_runtime_01_module_contract_and_assembly.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime 使用模式

## 1. Task Summary

其他项目想了解 Runtime 的常用任务模式与样例，或准备用 Agent Runtime 运行一项 Agent 任务、但还不确定该给 Module
定哪些运行要求、每次调用要提供什么时，使用本 Skill。Runtime 把常见任务分成四类，每类有一个同名的要求预设（preset）和一个可直接运行的随包样例：

| 类别与 preset ID | 任务形态 |
| --- | --- |
| `structured_output` | 只根据输入文本给出符合 schema 的结构化结果，不读写文件、不运行命令 |
| `file_review` | 读取调用者提供的任务材料，在私有草稿区写出审核文件并读回 |
| `code_change` | 在调用者的项目目录里修改代码，并运行一条有界的检查或测试命令 |
| `mcp_data` | 通过宿主已配置的 MCP 连接取数，把数据存成本地文件、读回，再写出派生结果 |

请求分三种，只做被请求的部分：

- **查询与配置**：想知道去哪里学样例、某个任务该用哪个 preset、要准备哪些参数。交付类别与 preset、该 preset 的
  实际要求值、源码、API、文档与样例入口、已能确定的调用组装，以及运行前还缺的输入。不调用模型、不注册、不运行样例。
- **验证随包样例**：在宿主上运行所请求模式的样例，交付其检查结论与运行证据。
- **运行自己的任务**：按所选 preset 注册并回读定义，再用 Test Run 运行，交付与回读身份一致的执行证据。

新宿主或新 Provider 上建议先验证对应样例，但这只是建议；请求没有要求时，不把样例验证或任务运行变成必做工作。
注册和执行仍使用已有的 `agent-runtime-registration`、`agent-runtime-test-run` 和同版本 API；本 Skill 帮助查找、
选择和组装，不提供另一条执行路径。

## 2. Intended Outcomes

1. **任务归入正确类别。** 请求方或执行 Agent 能说明任务属于哪一类及理由；任务同时需要多种能力时，选择覆盖全部
   实际需要的类别（例如既取 MCP 数据又写文件选 `mcp_data`），而不是选能力更少的类别后再临时放宽。
   不属于四类的任务（多节点业务图、生产 Gateway、callback 工具、PG 持久执行）被明确识别并转到对应入口。
2. **Module 要求来自代码中的 preset。** 交付的要求值由 `module_requirements_preset(<preset_id>, ...)` 实际产生，
   与注册后保存的定义一致；接收者据此注册新定义，不另抄一份字典。已注册定义保持其保存的工具与网络要求，
   不按版本名或当前 preset 重新推断。
3. **每次调用的组装完整且与 Provider 相符。** 交付说明本次使用的 transport、模型与 effort 的来源层，以及任务材料、
   项目目录、Claude 命令许可、MCP 连接名称中实际需要的项；尚未确定的项明确列为运行前还缺的输入，不填写猜测值。
   Codex 的差异（必须显式模型与 effort、文件读写经 shell、不接受逐条命令许可）被如实处理，没有为了通过而换
   Provider、删除工具或开放整个 Shell。
4. **查询与配置请求得到可直接使用的答案。** 只问去哪里学、用哪个 preset 或怎样配置时，请求方得到类别、preset 与
   其实际值、可打开的源码、API、文档与样例命令入口、已确定的组装和还缺的输入，可以据此自行决定下一步；
   回答本身不调用模型、不注册、不运行样例或业务任务，也不修改任何宿主文件。
5. **请求验证时由样例给出结论。** 对应类别的随包样例给出 `check.json` 结论：通过表示执行身份、文件效果、
   执行日志（以及 MCP 样例的 fixture 审计）实际成立；未通过时保留真实失败与运行文件夹，作为环境缺口交给负责人。
6. **请求运行自己的任务时得到可核对的结果。** Test Run 执行的正是注册并回读的那个定义：记录中的 Workflow 与 Module
   身份和回读结果一致，因此结果能证明所选 preset 与任务定义得到验证。交付执行状态、输出、Attempt 文件夹、实际文件
   效果和所需的语义核对；模型的自我陈述不被当作文件已写、命令已运行或 MCP 已调用的证据。

## 3. Boundaries

| 边界 | 由谁决定 | 可观察的越界 |
| --- | --- | --- |
| 本次做到哪一步（查询与配置、验证样例、运行自己的任务） | 请求方 | 只问查询或配置，却调用模型、注册定义、运行样例或改动宿主文件 |
| 任务含义、prompt、输入输出 schema | 任务 owner | 为了套用某个 preset 改写任务指令或 schema |
| Module 运行要求 | 任务 owner 在注册前选定 preset；保存后不可变 | 运行时改工具、改网络策略，或手改 `.runtime` JSON |
| 模型、effort、transport、时限 | 每次调用或执行参数文件 | 把模型写进 preset 或 source；Codex 未给模型时自动改用 Claude |
| Shell 命令范围 | 宿主每次给 Claude 的 `--command-permit` | 给 Claude 开放裸 Bash；给 Codex 传逐条许可 |
| MCP 连接与后端授权 | 宿主（连接配置）与服务 owner（授权） | 读取、复制或改写服务凭据；用 Runtime callback 代替 MCP |
| 宿主配置与项目文件 | 宿主 | 样例或任务覆盖已有 `config.json`、项目文件或用户设置 |

Runtime 只组装任务、模型、输入与 schema、目录、选定 MCP 和原生 CLI 允许规则，启动 `claude -p` 或 `codex exec`，
管理外层时限、取消、日志、结果校验与留档。CLI 自己规划、读写文件、运行命令、调用 MCP 和重试。
MCP、网关和后端服务按各自授权批准或拒绝。本 Skill 不新增权限引擎、强制沙箱、凭据复制、Windows callback IPC、
后代进程监管或生产数据库写入。

不适用的请求按下列方式转交：

- 注册已审 Reviewer source、bundle 或回读：`agent-runtime-registration`。
- 只需执行已有注册定义或随包多 Agent 样例：`agent-runtime-test-run`。
- 显式 callback 工具（`tool_session_factory`）、记录型 `resources.commands`、`--example` 多 Agent 图：这些是宿主受限的
  高级扩展。callback 需要 macOS/Linux 与 `cli_tools`，Windows 在打开会话前报告不支持；`commands` 只在 macOS 可用。
  它们不是四种常用模式的前提。
- 生产领域 Gateway、`gateway_read`/`hybrid` 输入、PG 持久执行或跨进程恢复：宿主已有入口或 Runtime 维护者。

## 4. Inputs and Completion

### 4.1 Inputs

查询与配置只需要请求方对任务的说明，以及已安装的 `agent-runtime-core`（或同版本公开源码）用来读取 preset、
文档和样例入口；缺少任务说明时仍可回答四类的区别与入口。以下执行条件只在请求运行样例或自己的任务时需要；
查询时把其中尚缺的项作为“运行前还缺的输入”交付，不为补齐它们而登录、安装或运行。

- 任务 owner 给出的任务说明，或已有的 v4 任务 source（`module_registration.json`、prompt、两份 schema）。
  运行自己的任务时缺少的话，先由 owner 补齐；本 Skill 不代写业务内容。
- 宿主 root、Python 环境与已安装的 `agent-runtime-core`。用下面的命令确认版本与随包文档位置：

  ```powershell
  python -c "from importlib.metadata import version; print(version('agent-runtime-core'))"
  python -c "from importlib.resources import files; print(files('agent_runtime') / 'README.md')"
  ```

- 所选 CLI（原生 `claude.exe` 或 `codex.exe`）已安装并由宿主登录。Windows 上 Claude 的 Bash 需要 Git for Windows；
  必要时宿主在本进程设置 `CLAUDE_CODE_GIT_BASH_PATH`。Codex 需要调用者明确的模型与 effort。
- 按类别需要的本次资源：`file_review` 的材料文件及其 sha256；`code_change` 的项目目录和要允许的检查命令；
  `mcp_data` 的输出目录，以及宿主已有 MCP 服务器在所选 CLI 中的原生连接配置（命令或 URL、凭据所在环境变量名）。
- 运行 `mcp_data` 样例需要该 Python 中的 `mcp` 包（安装 `agent-runtime-core[cli_tools]`）。

准确参数、默认值和错误以同版本 `agent-runtime-test-run --help`、随包 README 与
`agent_runtime/docs/agent_runtime_reviewer_api.md` 为准。缺少登录、CLI、材料或 MCP 配置时，说明缺什么、由宿主或
服务 owner 提供，不自行登录、搜索凭据或改用其他账号。

### 4.2 Output

交给请求方或任务 owner，按请求的路径取用：

- 类别、preset ID 和选择理由；`module_requirements_preset(...)` 的实际返回值（字段与值）。
- 查询与配置时：源码、API、文档与样例入口（见第 5.2、5.3 节的命令与位置）；已确定的组装与其来源；运行前还缺的
  输入（例如 Codex 的模型与 effort、项目目录、命令许可、MCP 连接配置、材料清单）；需要时附上建议的下一步。
- 运行样例或自己任务时的调用组装：transport、模型、effort 与时限的取值及来源层；`--resources`、`--project-directory`、`--command-permit`
  和 `root/.runtime/config.json` 中 `mcp_servers.<transport>.<name>` 的实际值（凭据只写环境变量名）。
- 样例运行时：运行文件夹路径、`check.json` 的 `passed` 与未通过项、`record.json` 中的状态。
- 自己任务运行时：注册回读的 Workflow ID、版本与 release hash；Test Run 记录中的 `workflow_release_ref`、
  `workflow_release_sha256`、`module_release_ref`、`module_release_sha256` 及二者是否一致；Test Run 的状态、输出、
  `attempt_workspaces` 和语义核对结果。

### 4.3 Completion Contract

按本次请求实际走的路径判断完成；未请求的路径不是完成前提：

- **只查询或配置**：就请求所问给出相应答案时完成：问用哪个 preset 时，给出类别与 preset 的依据和来自代码的实际
  要求值；问去哪里学时，给出源码、API、文档与样例入口；问怎样配置时，给出已确定的组装并列出运行前还缺的输入。
  这一路径不调用模型、不注册、不运行样例或自己的任务；对新宿主可以建议先验证样例，建议不构成完成前提。
- **只验证随包样例**（例如在新宿主或新 Provider 上检查环境）：请求的每个模式与 CLI 组合各有一次样例运行，
  其 `check.json` 为 `passed: true`，并交付第 4.2 节的样例项（运行文件夹、`check.json` 的检查结果、`record.json`
  的状态）时完成。样例自己按代码中的 preset 注册、固定版本运行，并核对执行身份、文件效果与日志；这一路径不需要
  注册或运行调用者自己的任务。
- **为自己的任务选择并运行**：类别与 preset 有依据；要求值来自代码；调用组装与所选 Provider 的实际能力一致；
  Test Run 记录中的 Workflow 与 Module 身份及 hash 等于注册回读的结果，且结果连同文件效果与语义核对一起报告。
  身份不一致时，本次结果不作为所选定义的证据：报告两组身份，按回读的版本重新执行，或由调用者明确选择另一
  已注册对象并记录该选择及其回读。
- **请求了多个路径**：同时满足每个已请求路径的条件。

部分完成时如实交付：例如样例因 CLI 未登录、Git Bash 缺失、Codex 无法进入受保护目录而失败，保留运行文件夹、
`record.json` 的失败信息和 `check.json`，交宿主环境负责人；任务属于不支持的组合时，保留 Runtime 返回的
`ADAPTER_CAPABILITY_UNSUPPORTED` 等原错误交 Runtime 维护者。不以退出码 0、模型的成功陈述或文件存在单独宣布完成。

## 5. Method

### 5.1 判断请求与类别

先确认请求要的是查询与配置、验证样例，还是运行自己的任务，只做被请求的部分。只是查询与配置时，使用第 5.2 节和
第 5.3 节开头的只读查看方式；第 5.3、5.4 节的运行与注册命令只作为入口给出，不执行。

再从任务实际要对外部世界产生的效果出发判断类别，不从任务名称判断：

- 只需要读输入、给结构化答复：`structured_output`。
- 需要读取调用者提供的文件，并把审核意见写成文件：`file_review`。材料以只读副本进入 Attempt，模型只写私有草稿区。
- 需要修改调用者已有的代码或文档，并运行检查命令：`code_change`。改动留在调用者目录，Runtime 不删除、不提交。
- 需要从已有服务取数再落盘处理：`mcp_data`。数据来自宿主声明的 MCP 连接，结果写入调用者目录。

只读文件、不写文件的审核，仍可用 `file_review`（工具集合包含 edit 但模型可以不写）；需要写调用者已有目录时用
`code_change` 或 `mcp_data` 的项目目录形态。确实只需要更少工具时，可以由任务 owner 直接构造
`ModuleExecutionRequirements`；Codex 的 read、search、edit 必须同时带 `shell`。

### 5.2 查看实际 preset

preset 是代码中的值，不是另一份配置。查看时直接调用：

```powershell
python -c "from agent_runtime import module_requirements_preset as p; print(p('code_change'))"
python -c "from agent_runtime import module_requirements_preset as p; print(p('mcp_data', mcp_connections=('example_data',)))"
```

当前各 preset 的固定要求（均为 `workflow_execution_isolated`、`inline` 输入、`native_structured_output`、
默认 `max_attempts=1`，调用者可传其他合法值）：

| preset | execution_mode | workspace | tools | network |
| --- | --- | --- | --- | --- |
| `structured_output` | `tool_free` | `none` | 无 | `denied` |
| `file_review` | `agent` | `own_draft_read_write` | `edit, read, search, shell` | `provider_managed` |
| `code_change` | `agent` | `explicit_project_read_write` | `edit, read, search, shell` | `provider_managed` |
| `mcp_data` | `agent` | `explicit_project_read_write` | `edit, read, shell, mcp__<name>…` | `provider_managed` |

`file_review` 与 `ModuleReviewer` 的固定能力相同；`ModuleReviewer` 仍固定 `max_attempts=3`，已注册的 Reviewer
不受 preset 影响。`provider_managed` 表示 Runtime 不另加工具联网限制，由 CLI 与宿主配置决定；已保存为
`denied` 的旧定义保持原意，在没有 Claude sandbox 的宿主（如 Windows）上含 Shell 时会得到 Claude 自己的拒绝。

### 5.3 随包样例：学习入口与运行

不运行也可以查看样例：`python -m agent_runtime.testing.conformance_pattern_examples --help` 列出子命令；
`python -c "import agent_runtime.testing.conformance_pattern_examples as m; print(m.__file__)"` 给出样例源码位置，
其中 `build_pattern_workflow(<preset_id>)` 是每个样例的 Module 定义，`PATTERN_DEFINITIONS` 是任务指令与 schema；
随包 README 的“可运行样例”说明产物与检查依据。

请求验证样例时才运行。每次运行在 `--root` 下新建一个运行文件夹（自带 host root、Attempt 文件夹、输入、材料或项目），不覆盖已有内容，
重复运行不会与已注册版本冲突。根目录用普通用户文件夹，路径可以含空格；Windows 路径默认限 260 字符，根目录宜短。

```powershell
$Root = "$HOME\Documents\Agent Runtime Examples"
python -m agent_runtime.testing.conformance_pattern_examples run --pattern structured_output --transport claude_cli --root $Root
python -m agent_runtime.testing.conformance_pattern_examples run --pattern file_review --transport codex_cli --root $Root --model <codex-model> --effort <effort>
```

`--pattern` 取四个 preset ID 之一；需要指定原生程序时加 `--cli-path`。输出 JSON 的 `passed` 汇总 `checks`；
`run_directory` 下的 `check.json` 与 `record.json` 留作证据。`prepare` 只准备文件夹并打印等价的
`agent-runtime-test-run` 命令，`check --run-dir DIR` 对保存的记录重新核对。检查依据是磁盘上的文件、
返回记录中 Inspection 的工具调用日志、代码样例的宿主独立复跑，以及 MCP 样例 fixture 自己的调用审计。

### 5.4 用于自己的任务

1. 由任务 owner 的 v4 source 构造 Module，并传入 preset：
   `Module.from_registration(source_root, skill_id=..., module_id=..., execution_requirements=module_requirements_preset("code_change"))`，
   再用 `Module.to_workflow(module.export(module_version="v1")).export()` 得到定义闭包（`origin_bundle`）。
   随包样例 `build_pattern_workflow` 是同一组合的可读实例。注册与准确回读交给 `agent-runtime-registration`
   （CLI 的 bundle 注册，或 Python 宿主的 `register_runtime_module_plugin(..., root=host_root)`）；内容变化时
   注册新版本，不覆盖旧版本。记下回读返回的 Workflow ID、版本和 release hash，以及其中 Module 的 ID 与 hash。
2. 每次调用都用回读得到的 Workflow ID 和版本，并用 `--expected-module-id` 指定其中的 Module；不省略 `--version`，
   因为省略时加载的是该 root 最近注册的定义，可能不是本次回读的那个：

   ```powershell
   agent-runtime-test-run --root $HostRoot --workflow my_module --version v1 --expected-module-id my_module `
     --input input.json --transport claude_cli --project-directory "D:\work\my project" --command-permit "python -m pytest:*"
   ```

   执行后核对记录中的 `workflow_release_ref`、`workflow_release_sha256`、`module_release_ref` 和
   `module_release_sha256` 与回读结果一致；不一致时按 §4.3 处理。随包样例的 `run` 也固定版本 `v1` 并做同样的核对。
   `file_review` 用 `--resources resources.json` 提供材料；`code_change` 与 `mcp_data` 用 `--project-directory`；
   Claude 需要运行命令时用可重复的 `--command-permit`，Codex 省略；Codex 增加 `--model` 与 `--effort`。
   长期选择写入 `root/.runtime/execution_parameters/workspace.json` 或 `workflows/<workflow_id>.json`。
3. `mcp_data` 使用宿主已有服务（例如团队已在用的数据 MCP）时，把该服务在所选 CLI 中的原生连接写入
   `root/.runtime/config.json` 的 `mcp_servers.<transport>.<name>`，凭据只用环境变量名引用（Claude 用
   `env`/`headers` 中的 `${VAR}` 并列入 `environment_keys`；Codex 用 `env_vars`、`bearer_token_env_var` 或
   `env_http_headers`），Module 使用 `module_requirements_preset("mcp_data", mcp_connections=("<name>",))`。Runtime
   只传递声明的连接，不读取凭据值、不代管登录；在 CLI 账号层面托管、无法写成连接配置的连接器不经此路径传递。
4. 读取结果：先确认执行身份与回读一致，再看 `status`（技术完成）、`output`（任务结论）和 `attempt_workspaces`
   （留档文件夹），文件效果到磁盘核对。

### 5.5 Provider 差异

| 项 | Claude CLI | Codex CLI |
| --- | --- | --- |
| 模型与 effort | 可省略，使用 Runtime 默认 | 必须显式给出 |
| 文件读写 | Read、Grep/Glob、Edit/Write 原生工具 | 经 shell；read/search/edit 都需要 `shell` |
| 命令 | Bash 只运行自带只读命令与 `--command-permit` 允许的命令 | 不接受逐条许可；范围由 Codex sandbox 与宿主 execpolicy 决定 |
| Windows Shell | Git Bash（`sh`/`bash` 语法） | PowerShell |
| 可写位置 | 私有草稿的 `scratch/` 或项目目录 | 私有草稿 scratch 或项目目录；根目录不能在受保护的 Temp 或被重定向的 AppData 下 |
| MCP | 私有 `mcp.json` 加 `--strict-mcp-config` | `-c mcp_servers.<name>`，未声明的宿主服务器本次关闭 |

### 5.6 已知陷阱

- Codex 在 Windows 上用自己的 sandbox 身份运行命令；运行根在 `%TEMP%` 或被应用包重定向的 AppData 下时，
  shell 进不了工作目录，失败按 CLI 原样记录。改用 Documents 下的根目录。
- 路径过长会让 Runtime 或 CLI 在 Attempt 文件夹内建文件失败（`WinError 206`）。根目录保持短路径。
- `.cmd`、`.bat`、`.ps1` 启动器不会被启动；PATH 里只有这些时用 `--cli-path` 指向原生程序。
- `prepare` 打印的 `agent-runtime-test-run` 命令在 Windows PowerShell 5.1 中用 `Out-File -Encoding utf8` 保存；
  含非 ASCII 输出时先设 `[Console]::OutputEncoding = [Text.Encoding]::UTF8` 与 `$env:PYTHONIOENCODING = 'utf-8'`。
- Attempt 文件夹不会自动删除。宿主用 `maintain_attempt_workspaces` 的 `intent="query"`/`"cleanup"` 管理已封存且过期的
  文件夹；调用者的项目目录从不进入清理集合。旧 `--example` 多 Agent 样例则使用临时目录，结束后删除。
