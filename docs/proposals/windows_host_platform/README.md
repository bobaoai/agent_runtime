# Windows Runtime 文档入口

## 当前职责边界

Runtime 按本次参数配置并启动 `claude -p`、`codex exec`，处理自己发起的外层调用的时限与取消，接收日志、退出状态和最终结果，并保存执行事实与任务留档。

本次资源范围包括可读文件、可写目录、可用 MCP，以及网关／数据库绑定。CLI 内部如何运行由 CLI 负责；MCP、网关和数据库内部授权由对应服务负责。Runtime 不另建权限判定器、默认回调桥、平台沙箱或内部进程监督方案。

| 设计层级 | 当前权威文档 |
| --- | --- |
| T0 总章 | [Agent Runtime §5.1](../../../designDoc/the_agent_runtime.md#51-external-executor-boundary) |
| T1 执行总约与四种常用场景 | [Execution Charter §5.1–5.2](../../../designDoc/agent_runtime_00_execution_charter.md#52-常用调用场景) |
| T2 场景配置、调用与留档 | [Invocation §4.3](../../../designDoc/agent_runtime_08_agent_execution_adapter_contract.md#43-常用调用场景) 及 §6.6 |
| T2 默认定义与验证 | [Registry §6.1](../../../designDoc/agent_runtime_01_module_contract_and_assembly.md#61-module-共用行为与-reviewer-默认环境)、[Capability Verification §4](../../../designDoc/agent_runtime_11_agent_capability_verification.md#4-capability-and-operation) |
| T2 授权集成边界 | [External Authority Integration §4](../../../designDoc/agent_runtime_09_authorization_integration_contract.md#4-capability-and-operation) |

## 当前状态

`agent-runtime-core` 0.2.0.dev11 已通过独立代码审核与整体验收，并已安装到维护者本机的共享 Python 环境。验收在 Windows 11 上运行同版本源码的本地测试套件，以及显式开启的真实 Claude CLI、Codex CLI 用例；需要 macOS/Linux、PostgreSQL、Temporal 或其他真实服务的用例被跳过，跳过不算通过。没有启用定期清理任务；Attempt 文件夹由宿主按自己的保留策略清理。

旧 IPC、沙箱、凭据复制和深入进程管理方案及其后续批次计划已从当前文档中删除，不再是后续任务。保留的 Windows 实现是按宿主平台选择的工作区租约、私有目录处理和外层进程停止。

四个常用场景为纯文本／结构化输出、文件审核、代码开发并运行命令、已有 MCP／网关查询。普通 agent／Reviewer 默认采用 `provider_managed`，沿用所选 CLI／宿主配置；不强制断网或操作系统沙箱。已明确选择的严格要求与历史定义保持原义。

MCP 数据研究包含基本的本地保存和读回处理；最小 MCP-only 用例不代表整个场景。Reviewer 允许范围按真实历史用量及本次审核需要配置，不默认开放全量 Shell；见 [Invocation §8.2](../../../designDoc/agent_runtime_08_agent_execution_adapter_contract.md#82-必做命令限制命令与领域许可)。

当前总章明确三阶段外层合同：启动前任务组装、运行中外层控制和日志接收、结束结果／记录／留档。普通 Agent 要提供实际原生文件读写；CLI 内部执行由 CLI 自己负责。

## 使用与实现文档

| 内容 | 文档 |
| --- | --- |
| 当前状态、安装、四种常用模式、原生文件读写、留档与清理、token 含义、平台限制 | [Runtime README](../../../README.md#current-status) |
| Windows／macOS／Linux 能力矩阵与 Claude、Codex 宿主环境 | [Claude CLI 工具与宿主环境](../../agent_runtime_claude_native_tools.md) |
| 准确参数、默认值、返回值与错误 | [Module 与执行 API reference](../../agent_runtime_reviewer_api.md)，留档清理见 [maintain_attempt_workspaces](../../agent_runtime_reviewer_api.md#maintain_attempt_workspaces) |
| 外层 CLI 边界的实现设计 | [cli_boundary_implementation/CODE_DESIGN.md](cli_boundary_implementation/CODE_DESIGN.md) |
| 宿主平台选择的实现设计与审核（历史记录） | [CODE_DESIGN_BATCH1.md](CODE_DESIGN_BATCH1.md)、[审核 R1](CODE_DESIGN_BATCH1_REVIEW_R1.md)、[审核 R2](CODE_DESIGN_BATCH1_REVIEW_R2.md) |
| Windows 工作区租约设计 | [windows_attempt_workspace_lease/CODE_DESIGN.md](../windows_attempt_workspace_lease/CODE_DESIGN.md) |
| 历史 Windows 失败用例清单（基线对比用） | [8704f37](baselines/windows_8704f37_failed.txt)、[main 2f39158](baselines/windows_main_2f39158_failed.txt) |
