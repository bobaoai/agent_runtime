# ClaudeAdapter 与 Claude CLI 执行

## 1. 使用方式

ClaudeAdapter 消费 Runtime 已准备的规范化请求，按字段和资源组合 claude -p。工具、工作区、模型、effort、输出和超时各有准确来源，同一实现服务普通 Module 与 ModuleReviewer。
参数、返回、失败和效果说明从实际类及方法的 docstring 自动导出，见 [ClaudeAdapter API](agent_runtime_reviewer_api.md#claudeadapter)。本页说明操作和验证，不维护另一份接口定义。

### 1.1 宿主需要准备的环境

这里的宿主是使用 Runtime 的项目，例如 Analyst Billie（AB）。环境配置放在宿主，下面的执行和
存储代码使用 Runtime 包内实现。先准备表中的实际值，再使用 §1.2 的 sample；表中名称对应现有
Python 参数或对象，不要求创建新的配置文件格式。

| 宿主准备什么 | 交给哪个现有入口 | 必须明确的内容 |
| --- | --- | --- |
| Runtime 与 Python | 安装同一候选版本的 Runtime；运行同版本源码中的 sample | 实际 import 来源与源码一致；安装所选用例需要的 pytest、jsonschema、psycopg 等依赖。直接 Claude CLI 路径不要求 Claude Agent SDK |
| Claude CLI 与现有登录 | Adapter 的 `cli_path` | 可执行文件的准确路径；认证检查使用这个文件，不自动发起登录 |
| Python 运行环境 | 启动 Runtime 的 `sys.executable` | 使用当前解释器及其只读运行库，不另选 Python 或创建环境 |
| Git 等额外工具与依赖 | Adapter 的 `read_only_dependencies` | 真实安装目录，包含程序及必要库；Adapter 将这些目录下的 `bin` 加入 PATH，不依赖 Agent 临时寻找程序 |
| 本次运行位置 | Adapter 的 `workspace_root` | 宿主拥有且可写的专用运行根目录；Runtime 在其下创建 Attempt/work、materials 和 scratch，不把宿主整个仓库默认为可读材料 |
| 审核材料与任务 | 公共执行入口的 `input_payload` 和授权内容读取接口 | 核心内容与明确辅助材料来自调用方；辅助材料由 Runtime 读取、核验并准备，不由 Adapter 扫描仓库 |
| 固定定义与本次执行选择 | Runtime 的 prepare_local_workflow_module 返回准确定义、Profile 和 Variant | 先通过 Registration Runbook 注册固定定义，再准备本次选择；相容性由 Runtime 按通用运行要求和真实 Adapter 判断，source 不声明 transport |
| Runtime 数据库 | `PostgresRuntimeReleaseStore`、`PostgresRuntimeExecutionRecordStore`、`PostgresRuntimeExecutionQueryStore` 的 `from_dsn(..., schema=...)` | 宿主提供连接与注册/执行 schema；二者可在同一个 PG 数据库内。管理员先通过 Runtime 安装/迁移 API 准备结构，普通注册和调用不自动执行 DDL |
| 宿主授权与内容存储 | `authorize`、`context_client`、`operation_client`、`artifact_host`、`record_store`、`content_store` 等公共入口参数 | 按当前 public API 提供真实绑定；测试里的 `_Host` 是替身，不是可以复制进生产的授权服务 |

具体环境值不放入 Runtime core。AB 可以继续使用自己的 `governance_bindings/` 保存配置和一个薄调用
入口；它不负责重新实现 CLI 参数组装、进程管理、输出解析、执行日志或 PG 持久化。
数据库连接由宿主注入，不写进 prompt、公开文档或 CLI argv，也不提供给模型的 Bash 环境。

```mermaid
flowchart LR
    H["宿主提供 root、任务材料与适用资源"] --> E["Runtime 公共执行入口<br/>固定要求与独立模型选择"]
    E --> A["Runtime 准备工作区<br/>Claude Adapter 直接启动 CLI"]
    A --> R["Runtime 校验输出<br/>保存成功/失败、工具记录和用量"]
    R --> P["已授权持久存储或自测内存结果"]
    P --> Q["宿主使用 Runtime 查询 API<br/>按 execution_id 回读结果"]
```

### 1.2 使用 Runtime 准备结果

```python
from pathlib import Path
from agent_runtime import prepare_local_workflow_module
from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter

prepared, selection = prepare_local_workflow_module(
    project_root, workflow_id,
    model_id=requested_model, reasoning_profile=requested_effort,
)

adapter = ClaudeAdapter(
    release_registry=prepared.registry, artifact_host=cell_artifacts,
    workspace_root=Path(host_workspace_root), cli_path=Path(host_claude_cli),
    read_only_dependencies=tuple(Path(p) for p in host_test_dependencies),
)
adapters.register(adapter)
```

project_root、workflow_id 指向准确注册；requested_model/requested_effort 为本次选择，None 依次取执行参数文件与 Runtime 默认。cell_artifacts、adapters 和 host_* 是已有执行入口的真实资源。通过 run_registered_workflow_module 或 run_workflow_module 消费同一次准备结果，完整参数沿用公共 API。普通自测直接使用 agent-runtime-test-run，无需手工实例化 Adapter 或提供 PG。

新准备使用 claude_cli_adapter@v4，完整实际输入在 Prompt Envelope 保存前固定。旧
claude_cli_adapter@v1/v2/v3 和 claude_cli_native_tools_executor@v2 的记录仍按原 bytes/hash 读取，
已提交请求通过原记录重放；新包不执行这些旧 pair，也不将其改写成新绑定。当前宿主入口先迁移
到公共准备接口，再采用新包；需要再次执行时明确准备新的 Profile。普通调用的私有草稿以 Attempt 的
work 为 cwd（materials 与 scratch 并列），只有 `./scratch/**` 可写；显式项目以调用者的
`project_directory` 为 cwd 和写区；本地命令/callback 扩展调用保持原有 scratch cwd。

## 2. 参数与材料

| Profile 工具 ID | Claude 原生工具 |
| --- | --- |
| read | Read |
| search | Grep、Glob |
| edit | Edit、Write（`Edit(path)` 规则同时约束 Write） |
| shell | Bash |
| mcp__&lt;name&gt; | 宿主 `.runtime/config.json` 的 `mcp_servers.claude_cli.<name>` 连接 |

工具按记录的 `tool_policy` 精确映射，不另加工具；没有 `edit` 的已保存快照不获得 Write/Edit。空集合明确产生 --tools 空字符串。tool_free 与 agent+空工具都不会隐含授予工具。attempt_workspace_policy=none 不提供模型可写草稿；own_draft_read_write 仅授予私有 scratch；explicit_project_read_write 授予调用者提供的已存在目录（Runtime 从不删除、封存或清理它）。`edit` 需要可写 workspace。

model_id 原样传入 CLI，包含 Claude 支持的 `[1m]` 选择形式；不依赖
API-key-only beta。CLI 不锁定某一个版本，代码检查所需选项，并在 trace 中记录实际版本。
新 CLI 的实际行为仍需要相应测试，不能仅凭 `--help` 断言隔离或输出能力。

当前 v4 在 Attempt 的私有目录（或显式项目目录）运行模型。授权输入按安全的 logical_name 放入只读 materials，
提供路径索引；local_handle 只经过 host 查表。文件工具由 Claude 自身的 `blockReadsOutsideWorkingDirectories`
与 `blockWritesOutsideWorkingDirectories` 限定在工作目录（cwd 加 `--add-dir` 的只读目录）内；草稿布局另以
`Edit(./materials/**)` 拒绝修改材料。OS sandbox 只在 Profile 明确记录 network `denied` 且含 Shell 时配置，
由 Claude 自己报告能否执行；`provider_managed` 不加 sandbox。运行依赖显式只读提供。旧 draft 的隐含工具权限
不会继续执行，需使用显式工具配置。

所有 argv 由同一 Adapter 转换逻辑组装，实际调用使用它；输入经 stdin，settings 仅来自已验证资源，调用方不透传任意参数/JSON。命令与生效配置记录在 trace 中，`effective_configuration` 汇总实际工具、允许规则、读写目录、MCP 连接（名称与配置哈希）、命令范围和 CLI 版本。

agent 调用的 stdin 是一段由代码生成的 `## Runtime Invocation Locations`，空一行后接原样的冻结
Prompt Envelope。这段位置说明以 JSON 给出本次 Attempt 实际准备的位置：工作目录、可写目录、
Attempt 的只读 materials 文件夹（含 task_input，冻结材料在其 source 下）、本次选定的只读依赖，
以及启用 Shell 时位于 PATH 最前的 Runtime Python；临时文件只给出变量名 `TMPDIR`、`TEMP`、`TMP`。
它不含原始 material_root、Attempt 根目录、私有 `crt-` 文件夹（其中有私有 `mcp.json`）、CLI 主目录、
连接配置或凭据。tool_free 调用只发送 Envelope。trace 的 `actual_prompt` 保存实际 stdin 全文，
`effective_configuration.invocation_locations` 保存生成它的值，Inspection 的 provider log 原样返回；
Envelope 的 ref／hash 只标识冻结 Envelope，不是整个 stdin 的 hash。Claude 进程环境中的
`TMPDIR`、`TEMP`、`TMP` 与 `CLAUDE_CODE_TMPDIR` 都指向本次调用的私有临时文件夹，使 Python、
Git Bash 工具与 Windows 原生／.NET 工具使用同一位置；只改子进程环境，不改全局变量或访问控制。
Runtime 不设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD`，项目已安装的 pytest 插件照常加载（与 Codex 相同）。
Bash 不默认开放：Runtime 只为宿主提供的 `command_permits` 渲染 `Bash(<permit>)` 允许规则，从不渲染裸 `Bash`；
Claude 文档中的内置只读命令（含只读 git 形式）始终可运行，因此许可是预批准而非封闭白名单
（`command_scope=native_read_only_plus_permits`）。封闭命令集需要 §2.1 的显式 commands 扩展。
用户与项目的 Claude 设置正常加载，其允许/拒绝规则照常生效。未声明 MCP 连接、记录型命令或任务 callback 时
使用 safe-mode；声明 MCP 时以 strict MCP 只加载私有 `mcp.json` 中声明的连接，并以 settings 排除 CLAUDE.md、
自动记忆、hooks 与 plugins。原生工具会话的临时目录由 Runtime 独立创建；进程停止已确认时退出后清理，
未确认时保留并报告其位置。原始任务、工具记录、实际配置与
结果通过现有私有 trace 和 Ledger 返回；已授权存储才持久保存，无 PG 自测不承诺跨进程恢复。执行失败与 Reviewer 的 non_pass 分开解释。

当前 CLI 支持 Runtime 本地命令与明确自测 callback 的进程桥；callback 的定义和资源由可信
tool-session factory 提供，不从任务 JSON 加载服务。该能力不等于生产领域 Gateway 已接通：
当前生产 Gateway/hybrid 输入请求仍在调用前报告不支持。Runtime 不提供 Claude SDK 执行路径或
fallback，也不直接透传任务中的 mcp-config。准确边界见 ClaudeAdapter 的 docstring。

输出方式来自准确 Profile：prompt_only_json 不传 --json-schema，native_structured_output 使用现有 schema projection；返回都按完整 canonical schema 校验，失败不自动切换方式。timeout 控制进程期限；max_attempts 留在 Runtime Policy，不被翻译成 CLI turn 或费用预算。

私有 trace 中的 exit_code 保留操作系统实际返回值；无法取得时为 null。Runtime 中止读取时，
stop_reason 单独说明进程中止、输出超限、超时或清理失败；cleanup_error 保留附加清理诊断。
exit_code 为 0 不代替 Provider 整体终态、最终结果和 canonical schema 检查。
普通运行只解释这些必要结果条件；未知非结果日志不使合法最终输出失效，也不从工具错误推断
权限原因。附加流处理诊断不覆盖已确定的进程停止原因。
stdout/stderr 保留有界原始输出，process_output_complete=false 表示它们不能视为完整轨迹。
自定义 process_runner 仍可使用标准 subprocess 异常；Runtime runner 的停止和超时异常分别
兼容 CalledProcessError 和 TimeoutExpired，不能将 Runtime 停止原因解释为进程退出码。

CLI 使用 `--permission-mode dontAsk`：未被允许规则覆盖的操作被拒绝而不是询问；不使用 bypassPermissions，文件与网络窗口继续强制执行。
CLI 返回的 permission_denied、permission_denials 和普通工具错误逐次保留。Agent 可以在原有
权限范围内处理错误并继续形成有效结果；它们本身不使 Attempt 失败。启动前按 Profile 配置工具、
网络和工作区，普通运行不比较初始化工具清单或扫描工具日志来拒收输出。材料完整性、实际
资源授权、Provider 整体失败、超时、运行中取消和输出无效仍按各自合同处理。
Inspection 在明确请求详细日志时标明缺少或矛盾的事件，不从错误正文或模型描述编造 ID、权限原因或实际效果。
因此 completed 不表示每个工具都成功；业务 non_pass/blocked 也可以是正常完成的审核结果。
配置效果由明确的能力测试验证。Git/Python 使用实际运行环境，避免命中系统启动
代理；Git 不加载用户或系统配置。trace 同时保存 argv 与安全环境值，便于复现，不依赖 Agent 临时修环境。

### 2.1 明确的本地工程资源与命令记录

普通自测通过 `agent-runtime-test-run --resources FILE` 提供冻结材料树、只读依赖和任务命令，
不需要调用者实例化 Adapter。资源控制包进入原 inputs hash 闭包，控制包本身保持私有；模型只看到
任务全文、确定的相对材料位置和命令说明。实际 stdin 与保存的 Prompt Envelope 使用相同字节。
可选 `.runtime/config.json` 提供程序/依赖 locator 默认、Attempt 文件夹根（`attempt_workspace_root`）和可选
MCP 连接（`mcp_servers`），完整参数见自动 API 中的 load_runtime_config。材料与依赖在所有宿主可用；
commands 需要 macOS。普通 Shell 命令的逐次许可（`--command-permit`，渲染为 Claude 的 `Bash(<permit>)` 允许规则）
是另一项能力，在所有宿主上可用；记录型 commands 不限制普通 Bash，也不是普通文件、代码或 MCP 任务的前提。

有 commands 时，Runtime 增加一个 `sandbox_command_execute` 本地 MCP 工具，让 Agent 按
command_id 选择本次已经提供的命令；原生 Read/Grep/Bash 保留。父进程使用当前真实资源 guard，
在 macOS sandbox-exec 内执行该命令，保存实际 argv/cwd/returncode、stdout/stderr 与原始 bytes。
同步调用的工作期限还约束这些命令和受管理子调用。进程控制只作用于 Runtime 启动的进程：POSIX 以
killpg 停止其会话进程组，Windows 以 TerminateProcess 停止该进程，随后在限定时间内确认退出；未确认
时报告 cleanup_error 并保留私有目录。CLI 自己启动的进程归 CLI 管理，不被枚举；停止后仍被它们占用的
输出管道记为 `output_streams_closed=false`（日志不完整），不是停止失败。需要长期后台工作的任务应使用
明确的宿主作业入口，不把一次同步 Test Run 当作 daemon 管理器。
IPC 代理和控制目录不进入模型或命令可读范围。这是本地资源工具，不是领域 Gateway 或生产 grant。

命令与 callback 进程桥需要 `cli_tools` 可选依赖，保持原有 settings 与允许列表，使用 strict MCP
仅加载本次对应服务，以及明确的 memory/hooks/plugins 限制。safe-mode 会关闭 MCP，因此不用于
这些桥接分支或声明 MCP 连接的调用；只有原生工具或无工具时继续 safe-mode。callback 进程桥需要
Unix-domain socket，缺少该传输的宿主（Windows）在打开会话前报告不支持。首次接入或 CLI/配置变化时，在明确的
能力测试中核对初始化与允许/拒绝效果；不能仅凭 help 声称隔离成立，也不增加绕过开关。

| 能力 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| 冻结材料与只读依赖 | 支持（只读权限位与可执行位） | 支持 | 支持（只读属性；不表示可执行位，链接与 reparse point 被拒绝） |
| 原生 Read/Grep/Glob/Edit/Write、按许可的 Bash | 支持 | 支持 | 支持；Bash 需要 Claude 能找到 Git Bash：PATH 中第一个 `git` 不属于 Git for Windows 时，由宿主设置 Claude 文档中的 `CLAUDE_CODE_GIT_BASH_PATH`（Runtime 原样传递）。找不到时 Claude 不提供 Bash 工具（2.1.325 实测），Runtime 不把 `shell` 换成 PowerShell |
| 声明的 MCP 连接 | 支持 | 支持 | 支持 |
| 记录型 commands | 支持（sandbox-exec） | 不支持 | 不支持 |
| 任务 callback | 支持 | 支持 | 不支持（无 Unix-domain socket） |
| CLI 程序 | 原生程序或脚本（shebang） | 同左 | 只启动原生 `.exe`；`.cmd`/`.bat`/`.ps1` 启动器会让另一个解释器重新解析 argv，因此不启动，请在 `provider_cli_paths` 或 `cli_path` 配置它启动的原生程序 |

Adapter 在原始 trace 的 local_command_calls/local_callback_calls 保留实际资源请求与结果。
Inspection 按需生成统一日志，仅在实际返回的 local_call_id 与父进程事实唯一一致时合并为一次调用，同时保留 Provider
调用 ID、事件位置和原生观察。普通非零/超时是工具结果；资源、材料或必需记录失效另行阻止提交。
缺失、矛盾或未配对的观察明确不完整，业务 owner 决定是否完成必做命令，不从模型文字补造退出码。

通过 `read_execution_log` 查询时，默认只返回 metadata；明确 include_private_content=True 才读取
原文并解析详细工具视图。旧 trace 的 tool_log 保留原解释，新原始日志按需解析而不回写。日志视图
不改写 Attempt 的成功、失败或重试；显式自测 CLI 返回完整视图供后续评价。资源清理后的失败
交接最多保存一次诊断，诊断写入期间的晚到取消不触发二次分类；实际运行中的取消仍保留。

## 3. 重复验证

### 3.1 字段组合回归与真实工具测试

完整测试在同版本源码 checkout 中。离线组覆盖工具八个子集、合法 mode/workspace、两种输出、模型与 effort 独立变化、准确新旧 binding 和错误组合。真实 CLI 另验证无工具、只读无草稿、受限 scratch 和范围外读写/网络拒绝。Claude 2.1.267 的 -p 会静默忽略无效 settings，进程退出 0 或 --help 有参数均不能代替实际权限效果。

普通本地回归：

```sh
python -B -m pytest -q -p no:cacheprovider tests/test_agent_runtime_claude_native_tools.py
```

真实工具用例需要显式设置 `RUN_PROVIDER_INTEGRATION=1`、`AGENT_RUNTIME_TEST_AB_WORKSPACE` 与
`AGENT_RUNTIME_TEST_CLAUDE_BIN`。工作区是测试者已获授权的专用可写运行根目录，每批选新目录，不设为宿主仓库根；
sample 的 Python/Git 目录以测试源码中的 `read_only_dependencies` 为准，换机器时由宿主调整该 fixture，
不写成 Runtime 的全局默认。

```sh
python -B -m pytest -q -s -p no:cacheprovider \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_native_tools_in_ab \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_rejects_ab_sibling_read \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_resource_boundaries_in_ab
```

### 3.2 宿主的注册 Reviewer 与 PG 用例

下面的用例演示宿主（这里是 AB）准备好环境后，用 Runtime 完成注册、执行和 PG 回读；它不替宿主安装环境，
不迁移宿主的默认 Reviewer 配置，也不修改现有 active pointer。除 `RUN_PROVIDER_INTEGRATION=1` 与
`RUN_AB_REVIEWER_PG=1` 外，它使用 §3.1 的工作区与 Claude 程序、`AGENT_RUNTIME_TEST_AB_ROOT` 下的 Reviewer
source 与 `governance_bindings/ddm_runtime.json` 数据库绑定，以及 `AGENT_RUNTIME_TEST_REVIEW_INPUT` 指向的完整
审核输入；缺少开关时显示 skipped，不算已执行，具体读取内容以测试源码为准。注册与 schema 准备见
[Registration Runbook](agent_runtime_registration_runbook.md)。

```sh
python -B -m pytest -q -s -p no:cacheprovider \
  tests/test_agent_runtime_claude_native_tools.py::test_live_ab_registered_design_reviewer_and_postgres
```

### 3.3 Runtime 能力检查与业务验收

这些用例验证 Runtime 的调用与记录边界：同一 execution_id 的执行、真实工具事件、完整输出校验与 PG 新连接回读
（导出文件写入工作区），而不是只看 CLI 退出零或 Reviewer 写了 passed。其中的 `_Host` 是测试授权替身，
通过不代表宿主授权已经实现。工具与 schema 测试通过只说明对应能力成立；实际审核是否完成，由该 Reviewer 的完整
schema 和语义 validator 判断，sample 不改变审核要求。

缺少 CLI/登录/运行库/PG/schema/source/input 时，补宿主对应的环境或注册输入；Runtime 公共入口、
Adapter、记录或查询实现有缺陷时，回到 Runtime 修复并随包交付。审核内容本身的缺陷交给所属
Reviewer/文稿负责人，不通过改启动参数或替换 schema 让它通过。

### 3.4 四种常用模式的随包样例

运行命令、产物与检查依据见 [README 的可运行样例](../README.md#runnable-examples)，真实 CLI 用例的开关见
[真实 CLI 用例开关](../README.md#live-run-gates)。以下是与 Provider 相关、样例依赖的事实：

- `mcp_data` 的 fixture 是随包的无凭据 stdio MCP 服务器（标准 `mcp` 包，即 `cli_tools`），经普通 `mcp_servers`
  连接配置交给 CLI，不是 Runtime callback。样例保存的是工具返回的数据对象（有 structured content 时即它，否则是
  文本内容中的 JSON），不是 MCP 协议外层（`content`、`structuredContent`、`isError`）。
- Claude 的 `code_change` 样例以固定命令 `sh run_check.sh`（在 Git Bash 中运行项目内的检查脚本）配合同名
  `--command-permit`；Codex 不接受逐条许可，直接在 PowerShell 中以调用运算符运行检查。
- Windows 上 Codex 的 PowerShell 可能处于受限语言模式：`[pscustomobject]` 之类的构造会让整条命令失败，失败的命令
  不算读回。样例指令因此要求用单独、简单的读命令（例如 `Get-Content review.md`）完成读回，失败时重新做一次干净的
  读取；最终答案是任务的结构化输出，不需要在 shell 里拼对象。

## 4. Codex workspace 执行（Reviewer 默认环境）

agent Module 可以选择 `codex_cli` 执行：本次调用、Workflow 或 workspace 参数文件给出
`transport_kind=codex_cli`、模型与 effort，Runtime 绑定 `codex_cli_agent_workspace_executor@v5`。
无工具 Module 使用 `codex_cli_agent_executor@v5`。
这两个 v5 绑定是当前绑定（workspace v5 保留 configured 默认行为，另增加显式 host 选择）。历史 Profile
（包括较早的 workspace 候选 `codex_cli_agent_workspace_executor@v2`）与已提交结果仍可读取，准确的已提交请求
无需已退役的 Adapter 即按原记录重放；新执行需要明确准备的当前 Profile，旧绑定在绑定检查时被拒绝，已保存记录
不被改写。Module 要求的工具不会为适配任一路径而被删去。
执行器参数与错误见 [CodexCliAgentWorkspaceModuleExecutor API](agent_runtime_reviewer_api.md#codexcliagentworkspacemoduleexecutor)。
省略 `codex_execution_mode` 时使用 `configured`；下表描述这一默认模式，包括 Reviewer 的默认调用。

| 项目 | Codex workspace v5 的 configured 默认行为 |
| --- | --- |
| 支持的组合 | agent、inline、network `denied` 或 `provider_managed`。含 Shell 时需要私有草稿或显式 `project_directory`；Codex 没有独立的 Read、Grep、Edit 工具，read、search、edit 由 shell 承担，没有 shell 的 read/search/edit 组合在准备时报告 `ADAPTER_CAPABILITY_UNSUPPORTED`。只声明 `mcp__<name>` 时 shell 关闭，workspace 可为 `none`，`file_io` 记录为 `unavailable_without_shell` |
| 主文件夹 | 私有草稿：每个 Attempt 的 scratch，`TMPDIR`、`TEMP`、`TMP` 都为其中的 `.tmp`；显式项目：调用者的 `project_directory`，三者都为 Attempt 的 `work/tmp`（Codex Windows sandbox 授权写入的文件夹）。给 Agent 的说明写明可写区域，其他位置按 CLI 自身权限 |
| 实际 stdin | 与 Claude 相同：代码生成的 `## Runtime Invocation Locations`（工作目录、可写目录、Attempt materials、选定只读依赖、启用 Shell 时的 Runtime Python）后接原样的冻结 Envelope；tool-free 只发送 Envelope。trace 的 `actual_prompt` 保存实际 stdin 全文，Inspection 原样返回；Envelope 的 ref／hash 只标识 Envelope |
| 权限配置 | 每次调用生成的权限 profile 以 Codex 的 `:read-only` 为基线，把本次 Attempt 的只读材料文件夹声明为 `read`（表明预期输入，不声称由此强制），只对声明的可写文件夹授予写入；仅当 Profile 记录 `denied` 时关闭工具网络。Windows 上（codex-cli 0.166.0 实测）Codex sandbox 以自身身份运行命令，Codex 只为可写文件夹加入其 sandbox 访问项，其余读取依赖宿主在上层目录树设置的访问；因此 Attempt 的 work/materials/scratch 以继承宿主访问的普通方式创建（POSIX 仍为 0o700）。根目录位于受保护的临时目录或被应用包重定向的 AppData 时，sandbox 的 shell 无法进入工作目录。trace 的 `codex_permission_profile` 保存实际配置 |
| 登录与配置 | 使用宿主自身的 Codex 登录（CODEX_HOME 原样传递，Runtime 不读取认证文件），`config.toml` 与 execpolicy 规则正常加载；项目文档、Skills、agents、Web 搜索与 apps/plugins/hooks 等功能关闭，`approval_policy="never"` |
| MCP | 只渲染声明的连接（`-c mcp_servers.<name>`，凭据仅以 `env_vars`、`bearer_token_env_var`、`env_http_headers` 引用环境变量名）。`-c mcp_servers={}` 不会清除宿主配置里的服务器，因此每次 exec 前由同一程序执行 `mcp list --json`，对未声明的已启用服务器追加 `-c mcp_servers.<name>.enabled=false` 并再次列出核对；启用集合与声明不一致时在 exec 前拒绝（`ADAPTER_BINDING_UNAVAILABLE`）；列出进程本身超时、超限或停止未确认时保留原有失败类别与停止事实。列出内容在任何情况下都不写入 trace 或原始日志，trace 以 `cli_probe` 标明失败发生在 exec 之前。trace 只记录名称（`host_mcp_disabled`）与配置哈希 |
| 命令许可 | Codex 没有逐次的命令许可，`command_permits` 报告 `ADAPTER_CAPABILITY_UNSUPPORTED`；Shell 记为 `command_scope=provider_policy`（宿主的 execpolicy 规则生效） |
| 本地命令 | 仅 macOS：资源声明 commands 时挂载唯一的 MCP 服务 `runtime_commands`，只暴露 `sandbox_command_execute`；命令由 Runtime 在 §2.1 的命令 sandbox 中执行，记录进同一执行日志 |

<a id="codex-mcp-unattended-tools"></a>

**Codex 无人值守调用 MCP 工具。** Runtime 以 `approval_policy="never"` 运行 Codex，无法交互式确认。
因此即使连接已登录，仍需本地确认的 MCP 工具调用也会被拒绝。

宿主为这个连接写的配置在 `root/.runtime/config.json` 的 `mcp_servers.codex_cli.<name>.config`。其中两项作用不同：

- `enabled_tools`（可选）把该连接的工具限定为任务选定、且账号已获授权的那些。它只决定工具范围，不是审批开关。
- `default_tools_approval_mode: "approve"` 用于仍需本地确认的工具。

```json
{"mcp_servers": {"codex_cli": {"example_data": {
  "config": {"url": "https://mcp.example.invalid/mcp",
             "enabled_tools": ["list_scope", "get_records"],
             "default_tools_approval_mode": "approve"},
  "environment_keys": []}}}}
```

登录或 OAuth 仍在 Codex 中完成（例如 `codex mcp login <name>`）。凭据只用环境变量名引用；Runtime 不读取凭据，
也不扩大服务端权限。`approve` 只免去 Codex 本地的逐次确认，不授予账号本来没有的权限。Claude 的 MCP 工具
由 Runtime 以 `mcp__<name>` 允许规则传入，不需要这一项。

**Claude 的命令许可要覆盖需要额外批准的检查命令。** Claude 自带的只读命令无需许可即可运行。任务指令要求
运行的其他检查命令需要额外批准：调用方的 `command_permits`（`--command-permit`）必须按 Claude Bash 规则覆盖
这条命令的实际写法。例如任务要求 `python -m pytest -q` 时，用 `"python -m pytest:*"`。这类命令未被许可或用户
允许规则覆盖时，在 `dontAsk` 下被拒绝，并记入 `permission_denials`。

需要本人宿主权限执行的 `code_change` 调用，可以在本次 API 调用中显式传入
`codex_execution_mode="host"`，或使用 CLI 的 `--codex-execution-mode host`。该模式只接受
Codex agent、inline、`explicit_project_read_write`、`provider_managed` 和恰好
read/search/edit/shell；材料包、额外只读依赖、recorded commands 与 MCP 均不适用。

host 使用原生 `-s danger-full-access` 并保留 `approval_policy="never"`，不生成上述权限 profile，
trace 的 `codex_permission_profile` 为 null。实际模式记录在 `codex_execution_mode` 和
`effective_configuration.codex_execution_mode`；Shell 记录 `command_scope=host`，文件范围记录
`filesystem_scope=task_directed`。工作目录表示本次任务范围，不能据此声称操作系统会阻止访问其他位置。
这是一项本次调用的宿主选择，执行参数文件和 Module 定义不选择它；省略时仍按表中的 configured 行为执行。

configured 模式的只读依赖若是文件系统根或与 Runtime 的 Attempt 文件夹根重叠，Runtime 在启动 Provider 前以
PermissionError 拒绝；声明的命令能经命令 sandbox 读到宿主临时根或 Codex 登录位置时同样拒绝。

能力测试使用已安装的 Codex CLI。MCP 选择以真实合并结果为准（合成的 CODEX_HOME，只执行
`mcp list`，不调用模型）；所用版本 npm `codex-cli 0.166.0`，Codex 升级后重跑：

```sh
AGENT_RUNTIME_REAL_RUN=1 AGENT_RUNTIME_CODEX_BIN=/path/to/native/codex \
  python -B -m pytest -q -p no:cacheprovider tests/test_agent_runtime_cli_boundary.py -k real_codex
AGENT_RUNTIME_REAL_RUN=1 python -B -m pytest -q -p no:cacheprovider tests/test_agent_runtime_codex_workspace.py
```

<a id="5-antigravity-执行首个绑定"></a>

## 5. Antigravity 执行（首个绑定）

本次调用、Workflow 或 workspace 参数文件给出 `transport_kind=antigravity_cli`、模型与 effort 时，Runtime 绑定
`antigravity_cli_adapter@v1` 并启动原生 `agy`。参数与错误见
[AntigravityCliAdapter API](agent_runtime_reviewer_api.md#antigravitycliadapter)，合同见 Design 08 §3.10。

| 项目 | Antigravity v1 的行为 |
| --- | --- |
| 支持的组合 | 只有两种：`structured_output`（tool_free、inline、workspace `none`、无工具、network `denied`）与 `file_review`（agent、inline、`own_draft_read_write`、恰为 read/search/edit/shell、`provider_managed`），都要求 `native_structured_output` 与显式模型、effort。其他组合在 Profile 准备时报告 `ADAPTER_CAPABILITY_UNSUPPORTED`，不启动模型 |
| 启动参数 | `--input-format stream-json --output-format stream-json --json-schema FILE --agent agent-runtime-task --model M --effort E --settings FILE --disable-slash-commands`；不带 `-p`、续接会话、沙箱或跳过权限的参数。时限由 Runtime deadline 负责 |
| 自定义 agent | `<cwd>/.agents/agents/agent-runtime-task.md`。`structured_output` 只列 `finish`（交回 schema 结果的机制，不是任务工具）；`file_review` 列 `view_file`、`write_to_file`、`replace_file_content`、`run_command`、`command_status`、`finish`，不用 `grep_search`，搜索经 Shell |
| 会话设置 | `--settings` 文件只作用于本次调用，从不写入全局设置。共有 `enableTerminalSandbox=false`、`toolPermission=request-review`、`artifactReviewPolicy=always-proceed`。`structured_output` 拒绝全部文件、命令、MCP、URL 操作；`file_review` 用 `agentMode=accept-edits`，允许读取 scratch、materials 与所选依赖，拒绝写 materials 与 agent 目录 |
| 主文件夹 | `structured_output`：Attempt 的 `work/`；`file_review`：Attempt 的 `work/scratch`，materials 在 `../materials`，`TMPDIR`、`TEMP`、`TMP` 为 scratch 中的 `.tmp`，Runtime Python 位于 PATH 首位 |
| 实际 stdin | 一条 stream-json user 事件后关闭 stdin；content 与 Claude／Codex 相同（`file_review` 为 Invocation Locations 段加冻结 Envelope，`structured_output` 只有 Envelope）。trace 同时保存 `actual_prompt` 与准确的 `stdin_jsonl` |
| 命令许可 | 每条 `command_permits` 渲染为 `command(<permit>)` 允许规则，没有许可就不允许任何命令。已实测的写法是 `regex:` 加完整命令的 Python `re.escape`，可用 `antigravity_command_permit(command)` 生成；含空格、非 ASCII 字符与括号的解释器路径按此写法已实测运行。许可须非空、单行、不含 NUL。该函数只转义给定字符串，不解析 `python` 别名、不规范化路径或引号、不合并命令；任务输入中的命令与许可须来自同一字符串，并要求每条命令单独调用（见 [registration runbook](agent_runtime_registration_runbook.md#antigravity-command-permits)）。纯前缀写法未经实测。agy 对模式是整条匹配还是部分匹配由其自身决定，Runtime 不作声明 |
| 被拒的工具请求 | headless 模式下无法获批的请求被 agy 软拒绝，stderr 给出说明，调用可继续并以 0 退出。Runtime 保留该工具步骤（状态 failed）与 stderr，不因此判调用失败，也不记为成功。若结果因此没有 `structured_output` 且 `result.denied_actions` 非空，失败仍是 `antigravity_cli_structured_output_missing`（`schema`，不重试），信息先列出这些原生拒绝与失败的工具步骤；trace 另存 `native_denied_actions`，不解析 stderr 文本 |
| 结果 | 需要退出码 0、恰有一个 `init` 与一个 `result`、`status=SUCCESS`、`init.agent` 为生成的 agent、`init.model` 等于请求模型，且 `result.structured_output` 通过 canonical schema；不解析回复文本 |
| 用量 | `output_tokens` 原样记录（已含 `thinking_tokens`）；输入总量、缓存读取、缓存创建与普通输入未知，原始用量行保留在 trace 的 `native_usage` |
| 日志限制 | Inspection 按 `step_index` 配对工具步骤；`read_file` 只给出 `N lines, M bytes` 摘要，`write_file` 不含写入内容，两者记为 `provider_result_content_unavailable`／`provider_request_content_unavailable`。`init.tools` 是通用目录，不代表本次 agent 的工具集 |
| 登录 | 使用宿主已有的 agy 登录；Runtime 与 setup 都不读取认证文件、不启动登录，setup 报告 `login: not_verified` |

随包 `file_review` 样例为 Antigravity 加一条许可，只允许 `Get-Content review.md` 读回。模型若改用 `view_file` 读回，
日志只有摘要：检查仅在该读取位于最后一次写入之后时把它算作读回，审核文件的实际内容由宿主对磁盘文件的检查证明，
`check.json` 的说明会写明这一限制。

<a id="6-native-project-execution"></a>

## 6. 原生项目执行（native_project）

需要 Agent 在调用者自己的项目里按项目指令和项目 Skills 工作时，给一次调用加 `native_project=True`（CLI
`--native-project`）。仍是一次独立的 `claude -p` 或 `codex exec`：Runtime 组装输入、输出 schema、模型、时限与
选定的 MCP 连接，接收日志与最终结果，按 canonical schema 校验并保存记录；不保存、不续接会话。合同见
[Design 08 §3.4.7](../designDoc/agent_runtime_08_agent_execution_adapter_contract.md#57-native-project-execution)，
参数见 [run_local_workflow_test](agent_runtime_reviewer_api.md#run_local_workflow_test)。

**适用的 Module 与本次资源。** Module 用既有 `code_change` 或 `mcp_data` 预设注册（选择 native 不改定义）；
本次提供 `--project-directory`（调用者的项目，cwd 与可写范围），Codex 另加 `--codex-execution-mode host`。
材料包、`--resources` 中的 commands 与额外依赖、`--command-permit`、callback 和 Antigravity 在启动 Provider
前被拒绝（`ADAPTER_CAPABILITY_UNSUPPORTED`）。宿主的 `read_only_dependencies` 默认值也会被拒绝，Python API 用
`read_only_dependencies=()` 清除。

| 原生表面 | Claude（claude_cli_adapter@v4） | Codex（workspace v5，host） |
| --- | --- | --- |
| 项目指令 | CLAUDE.md 层级（用户、项目上级目录、项目） | AGENTS.md（项目文档默认发现） |
| Skills | 用户与项目 `.claude/skills` 及 Claude 自带 Skills；经 `Skill` 工具调用，不需要在任务里点名 | 用户与项目 `.agents/skills`；模型读取 SKILL.md 后用 Shell 执行 |
| 文件与命令 | Read、Grep／Glob（声明 search 时）、Edit／Write，`Bash` 与 `PowerShell`；`dontAsk` 下整项允许，写入规则为 `Edit(./**)` | 原生 Shell（Windows 为 PowerShell），`-s danger-full-access`、`approval_policy="never"` |
| MCP | 只传根配置中被 Module 声明的连接（私有 `mcp.json`、`--strict-mcp-config`）；同时提供 `ListMcpResourcesTool`、`ReadMcpResourceTool`、`ToolSearch`（延迟加载的 MCP 工具经它取得 schema） | 渲染声明连接；未声明的已启用宿主服务器本次关闭；需要无人值守的工具在连接配置中设 `default_tools_approval_mode: "approve"` |
| 本次关闭 | 自动记忆、hooks（托管策略照常） | agents、Web 搜索、memories、hooks、plugins 等既有关闭项 |
| Python | Runtime Python 位于 PATH 首位；项目要用自己的环境时，在 CLAUDE.md／AGENTS.md 或 Skill 中写明解释器的绝对路径 | 同左 |

与默认（configured／隔离）模式的区别：默认 Claude 调用使用 safe-mode 与 `--disable-slash-commands`，不加载
CLAUDE.md 与 Skills，只按 `--command-permit` 预批准 Bash，文件工具限定在工作目录；默认 Codex 关闭项目文档与
Skills，以按次生成的权限 profile 运行。原生项目模式下 Shell 以本人宿主权限运行，项目目录是任务范围而非
操作系统边界；用户、项目与托管的 deny／ask 规则和 Codex execpolicy 仍然生效，不使用 bypassPermissions 或
跳过权限的参数。

**凭据与连接身份。** 连接写在 `root/.runtime/config.json` 的 `mcp_servers.<transport>.<name>`。使用 OAuth 等原生
登录的连接，`<name>` 与 URL 要和 CLI 自己的连接一致（`agent-runtime-setup --mcp <name>` 按原名写入），CLI 才能
在本次调用中找到已有登录；名称不同的连接在 Claude init 中显示为需要认证。Runtime 不读取、复制或改写认证文件
和全局设置。记录中的连接身份是名称与配置哈希；Codex 另有 `host_mcp_overlaid`（与宿主同名连接叠加）和
`host_mcp_disabled`。

**记录与检查。** 每个 Attempt 的 `effective_configuration` 写入 `native_project: true`、`context_enabled`
（Claude `["claude_md", "skills"]`，Codex `["agents_md", "skills"]`，表示 Runtime 不再关闭这些原生发现）、
`context_suppressed`、实际工具与允许规则、`command_scope=host`、`filesystem_scope=task_directed`。实际发现与
使用看原始日志：Claude 的 init 事件列出工具、Skills、plugins 与连接状态；`Skill`、命令、文件与 MCP 调用由
`agent-runtime-inspect --view log` 返回。Shell 里发起的 HTTP 请求是命令，不计为 MCP 调用。

```powershell
# Claude：项目指令与 Skills 原生加载
agent-runtime-test-run --root $Root --selection $Selection --input $Input `
  --transport claude_cli --model claude-sonnet-5-5 --effort high `
  --project-directory $Project --native-project --record-out $Record
# Codex：同一 Module，另以 host 权限运行
agent-runtime-test-run --root $Root --selection $Selection --input $Input `
  --transport codex_cli --model gpt-6-astra --effort xhigh `
  --project-directory $Project --native-project --codex-execution-mode host --record-out $Record
```

随包样例生成一个带 CLAUDE.md／AGENTS.md、两个 Skills（各一份给两种 CLI）、一个 Python 脚本和 MCP fixture 的
项目，用普通注册与 Test Run 运行，并检查项目指令中的标记、Skill 的实际调用或读取、Claude init 的 Skills 列表、
fixture 审计、脚本产物与结构化输出：

```powershell
python -m agent_runtime.testing.conformance_pattern_examples run --pattern native_project --transport claude_cli --root $Root --model claude-sonnet-5-5 --effort high
python -m agent_runtime.testing.conformance_pattern_examples run --pattern native_project --transport codex_cli --root $Root --model gpt-6-astra --effort xhigh
```

源码 checkout 中的真实用例：`AGENT_RUNTIME_REAL_RUN=1` 加 `AGENT_RUNTIME_CLAUDE_BIN`／`AGENT_RUNTIME_CODEX_BIN`，
运行 `tests/test_agent_runtime_pattern_examples.py -k native_project`。
