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

project_root、workflow_id 指向准确注册；requested_model/requested_effort 为本次选择，None 使用 Runtime 默认。cell_artifacts、adapters 和 host_* 是已有执行入口的真实资源。通过 run_registered_workflow_module 或 run_workflow_module 消费同一次准备结果，完整参数沿用公共 API。普通自测直接使用 agent-runtime-evaluate，无需手工实例化 Adapter 或提供 PG。

新准备使用 claude_cli_adapter@v3，完整实际输入在 Prompt Envelope 保存前固定。旧
claude_cli_adapter@v1/v2 和 claude_cli_native_tools_executor@v2 的记录仍按原 bytes/hash 读取，
已提交请求通过原记录重放；新包不执行这些旧 pair，也不将其改写成新绑定。当前宿主入口先迁移
到公共准备接口，再采用新包；需要再次执行时明确准备新的 Profile。新调用的草稿 cwd 和写区均为 scratch。

## 2. 参数与材料

| Profile 工具 ID | Claude 原生工具 |
| --- | --- |
| read | Read |
| search | Grep |
| shell | Bash |

可以选择全部子集，空集合明确产生 --tools 空字符串并关闭 MCP。tool_free 与 agent+空工具都不会隐含授予工具。attempt_workspace_policy=none 不提供模型可写草稿；own_draft_read_write 仅授予明确私有写区，工具和文件权限共同生效。

model_id 原样传入 CLI，包含 Claude 支持的 `[1m]` 选择形式；不依赖
API-key-only beta。CLI 不锁定某一个版本，代码检查所需选项，并在 trace 中记录实际版本。
新 CLI 的实际行为仍需要相应测试，不能仅凭 `--help` 断言隔离或输出能力。

当前 v3 在 Attempt 的私有目录运行模型；有草稿时 cwd 是 scratch，无草稿时不提供可写 scratch。授权输入按安全的 logical_name 放入只读 materials，
提供路径索引。只有要求允许草稿时才提供可写 scratch；local_handle 只经过 host 查表。CLI 的 restricted 模式约束 Read/Grep 的
实际路径，Bash 使用原生 sandbox；运行依赖显式只读提供，网络默认关闭。旧 draft 的隐含工具权限
不会继续执行，需使用显式工具配置。

所有 argv 由同一 Adapter 转换逻辑组装，实际调用使用它；输入经 stdin，settings 仅来自已验证资源，调用方不透传任意参数/JSON。命令与生效配置记录在 trace 中。
原生 Bash 中可使用 head、git diff、ls、grep 等普通命令，不设命令白名单。未提供记录型命令或任务 callback 时，safe-mode 关闭自动加载的
CLAUDE.md、Skills、Plugins 和 hooks，MCP 与会话持久化也关闭；提供 commands 或 callback 时使用显式 MCP 配置。原生工具会话的临时目录由 Runtime 独立创建，退出后清理。原始任务、工具记录、实际配置与
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

CLI 使用 auto 模式处理其内置的确认判断，不使用 bypassPermissions；文件与网络窗口继续强制执行。
CLI 返回的 permission_denied、permission_denials 和普通工具错误逐次保留。Agent 可以在原有
权限范围内处理错误并继续形成有效结果；它们本身不使 Attempt 失败。启动前按 Profile 配置工具、
网络和工作区，普通运行不比较初始化工具清单或扫描工具日志来拒收输出。材料完整性、实际
资源授权、Provider 整体失败、超时、运行中取消和输出无效仍按各自合同处理。
Inspection 在明确请求详细日志时标明缺少或矛盾的事件，不从错误正文或模型描述编造 ID、权限原因或实际效果。
因此 completed 不表示每个工具都成功；业务 non_pass/blocked 也可以是正常完成的审核结果。
配置效果由明确的能力测试验证。Git/Python 使用实际运行环境，避免命中系统启动
代理；Git 不加载用户或系统配置。trace 同时保存 argv 与安全环境值，便于复现，不依赖 Agent 临时修环境。

### 2.1 明确的本地工程资源与命令记录

普通自测通过现有 `agent-runtime-evaluate --resources FILE` 提供冻结材料树、只读依赖和任务命令，
不需要调用者实例化 Adapter。资源控制包进入原 inputs hash 闭包，控制包本身保持私有；模型只看到
任务全文、确定的相对材料位置和命令说明。实际 stdin 与保存的 Prompt Envelope 使用相同字节。
可选 `.runtime/config.json` 只提供程序/依赖 locator 默认，完整参数见自动 API 中的 load_runtime_config。

有 commands 时，Runtime 增加一个 `sandbox_command_execute` 本地 MCP 工具，让 Agent 按
command_id 选择本次已经提供的命令；原生 Read/Grep/Bash 保留。父进程使用当前真实资源 guard，
在 macOS sandbox-exec 内执行该命令，保存实际 argv/cwd/returncode、stdout/stderr 与原始 bytes。
IPC 代理和控制目录不进入模型或命令可读范围。这是本地资源工具，不是领域 Gateway 或生产 grant。

命令与 callback 进程桥需要 `cli_tools` 可选依赖，使用 restricted、空 setting-sources、strict MCP，
仅加载本次对应服务，以及明确的 memory/hooks/plugins 限制。safe-mode 会关闭 MCP，因此不用于
这些桥接分支；只有原生工具或无工具时继续 safe-mode。首次接入或 CLI/配置变化时，在明确的
能力测试中核对初始化与允许/拒绝效果；不能仅凭 help 声称隔离成立，也不增加绕过开关。

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

真实 AB 工具用例需要显式设置 `RUN_PROVIDER_INTEGRATION=1`、
`AGENT_RUNTIME_TEST_AB_WORKSPACE` 与 `AGENT_RUNTIME_TEST_CLAUDE_BIN`。工作区必须属于测试者授权的
AB 目录。`AGENT_RUNTIME_TEST_AB_WORKSPACE` 指向已存在的专用可写运行根目录，每批选新目录以免覆盖
导出的报告；不设为 AB 仓库根。当前 sample 的 Python/Git 目录以测试源码中的
`read_only_dependencies` 为准，环境需要安装在这些位置；换机器时由宿主调整测试 fixture 的依赖绑定，
不把这两处本机路径写成 Runtime 的全局默认。

```sh
python -B -m pytest -q -s -p no:cacheprovider \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_native_tools_in_ab \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_rejects_ab_sibling_read \
  tests/test_agent_runtime_claude_native_tools.py::test_live_claude_resource_boundaries_in_ab
```

### 3.2 AB 的注册 Reviewer 与 PG sample

这个用例演示宿主搭好环境之后，如何使用 Runtime 完成注册、执行和回读；它不是替所有宿主自动
安装环境的工具，也不会迁移 AB 的默认 Reviewer 配置。

| 已有配置/环境变量 | 该用例实际读取或要求的内容 |
| --- | --- |
| `RUN_PROVIDER_INTEGRATION=1`、`RUN_AB_REVIEWER_PG=1` | 明确开启真实模型和 AB 持久测试；缺少任一个时用例 skipped，不算已执行 |
| `AGENT_RUNTIME_TEST_AB_ROOT` | AB 项目根，含 `.claude/skills/the-design-authoring/runtime_modules/design_contract_reviewer/` 的完整 source，以及所属 Portable validator |
| `AGENT_RUNTIME_TEST_AB_WORKSPACE` | §3.1 的专用运行根目录；Runtime 工作区和该用例导出的报告位于这里 |
| `AGENT_RUNTIME_TEST_CLAUDE_BIN` | §1.1 已检查登录和版本的实际 Claude executable |
| `AGENT_RUNTIME_TEST_REVIEW_INPUT` | 已由所属 DDM builder 生成的完整输入 JSON；可直接是 payload，也可由已有记录的 `semantic_input` 字段提供，module_id 必须是 design_contract_reviewer |
| `governance_bindings/ddm_runtime.json` | 用例读取 `database_url_env`、`registry_schema`、`execution_schema`、`module_release_ref`、`module_release_sha256`；原 Module 和它的 Policy 依赖必须已能从目标 Registry 解析 |
| `database_url_env` 指名的环境变量 | 宿主在调用进程中注入连接；变量名和连接值均由宿主选择，不由 Runtime 猜测 |

AB 的注册和执行可以共用一个 PG 接口，同时分别使用 AB 自己的 Runtime control 与 execution schema。
其他项目选择自己的 schema；schema 内的表名和安装结构仍由 Runtime 管理，宿主不手写另一套表结构。
环境准备和注册方法见 [Registration Runbook](agent_runtime_registration_runbook.md) §3 至 §8。

已注册 Reviewer 与 PostgreSQL 回读用例另外要求 `RUN_AB_REVIEWER_PG=1`、
`AGENT_RUNTIME_TEST_AB_ROOT`、`AGENT_RUNTIME_TEST_REVIEW_INPUT` 及 AB 配置所指的数据库环境变量。
它复用已注册 Design Reviewer 的指令与 schema，按当前定义与执行准备接口形成测试 Module/Workflow 和独立 CLI 选择，
消费真实原始审核输入，使用测试授权 ports，写入样例 Profile/Variant 及执行记录，不修改现有 active pointer。该用例不是生产宿主授权实现。

```sh
python -B -m pytest -q -s -p no:cacheprovider \
  tests/test_agent_runtime_claude_native_tools.py::test_live_ab_registered_design_reviewer_and_postgres
```

工具与 schema 测试通过只说明对应能力成立。是否完成某份实际审核，仍由该 Reviewer 的完整 schema
和语义 validator 判断；sample 不改变审核要求。

### 3.3 结果与缺口归属

用例会导出 `registered_review_execution.json`、`registered_review_trace.json` 和
`registered_review_result.json`。核对同一 execution_id 的 Module/Profile、执行状态、真实工具事件、
完整输出校验，以及 PG 新连接回读，而不是只看 CLI 退出零或 Reviewer 写了 passed。
这里的 `_Host` 来自 `tests/test_agent_runtime_registered_module_execution.py`，只用于验证 Runtime 的
调用与记录边界。真实宿主授权仍需项目自己实现并验证；本 sample 通过不代表它已经完成。

缺少 CLI/登录/运行库/PG/schema/source/input 时，补宿主对应的环境或注册输入；Runtime 公共入口、
Adapter、记录或查询实现有缺陷时，回到 Runtime 修复并随包交付。审核内容本身的缺陷交给所属
Reviewer/文稿负责人，不通过改启动参数或替换 schema 让它通过。
