# Agent Runtime 测试与样例 runbook

从 [README](../README.md) 或 [能力索引](agent_runtime_capabilities.md) 进入。本文由同一份 AgentCapabilityTestCase 生成，帮助使用者找到实际样例并重复运行测试。

## 1. 阅读和运行环境

wheel 随附本文档；完整测试和 fixtures 位于同版本 Runtime 源码 checkout。以下路径均相对源码根目录，命令中的 python 指安装了 Runtime 与测试依赖的解释器。测试依赖、可选 Provider/PG/Temporal 依赖以 pyproject.toml 为准。

每组的样例 selector 指向真实测试函数。先读该函数及它直接使用的 fixture，再运行对应命令；fixture 中的 Provider 替身和测试授权对象用于验证，不是生产宿主配置。这些是已有能力的具体样例，尚未实现的完整 agent_capability_example Workflow 不在其中。

### 1.1 Runtime 与宿主环境各准备什么

宿主是使用 Runtime 的项目，例如 Analyst Billie。宿主决定执行什么、使用哪个环境；Runtime 提供执行、隔离、记录与读取能力。一次实际调用并不需要宿主另写一套执行器。

| 内容 | Runtime 提供 | 宿主提供 |
| --- | --- | --- |
| 模型与工具执行 | 公共调用入口、Provider Adapter、参数组装、进程与结果处理 | Module/Profile 的选择、CLI 安装与登录、实际程序路径 |
| 文件与运行依赖 | 按授权范围准备 Attempt 工作区、落实读写和网络限制、清理本次临时资源 | 任务材料、可用工作区根、Python/Git 等只读依赖 |
| 注册与执行记录 | Registry/Ledger 的表结构、安装与迁移 API、写入与查询实现 | PG 连接、独立 schema、数据访问授权；管理员按需调用安装/迁移 API |
| 审核内容 | 执行已注册 Module、校验输出结构、保存真实结果 | 所属 Skill Package 的 prompt/schema、审核任务及所属语义 validator |
| 测试与验收 | 固定 sample、测试入口与执行证据 | 本次实验目标、所选用例、环境配置与结果判断标准 |

宿主可以保存配置并组合这些公开 API，不应复制 Claude 启动、超时处理、事件解析或 PG 记录代码。Runtime 的 PG 表结构与实现属于 Runtime；连接哪一个数据库、选择哪些 schema 属于宿主。注册接口不自动建库或变更管理员权限。

环境缺件与 Runtime 缺陷分开处理：缺 CLI、登录、依赖目录或 PG 配置，补宿主环境；公共入口不能执行相容 Profile、失败日志没有保存或已保存记录无法回读，修 Runtime。测试授权替身只留在测试里，不能直接当作宿主正式授权实现。

Claude 工具与 AB 持久审核的具体准备表见 [Claude sample 环境说明](agent_runtime_claude_native_tools.md#11-宿主需要准备的环境)。Module/Workflow 注册步骤见 [Registration Runbook](agent_runtime_registration_runbook.md)。

### 1.2 自定义 ModuleExecutionLedger

公开注册执行入口使用 Runtime 内置 Ledger。直接调用 run_module 或 run_workflow_module 并提供自定义 ModuleExecutionLedger 时，还需实现 record_attempt_start(started: ModuleAttemptStartedRecord)。Kernel 先用 begin 登记 Run/Variant（attempt_starts 为空），再于各 Attempt 实际开始时记录既有 StartRecord。缺少该接口会在新增执行记录前拒绝。相同 ID 的开始时间不可被刷新；Workflow 路径沿用 PG 中原已保存的 Runtime 时间。

## 2. 单例、分组与全仓批量运行

单例和分组命令见第 4 节。全仓使用 pytest 自动收集，不需要手工维护另一个测试列表。可以先列出所有实际测试，再执行：

```sh
python -B -m pytest --collect-only -q
python -B -m pytest -q -rs -p no:cacheprovider
```

默认不开启真实 Provider 和 Temporal。为避免继承了已开启的环境变量，普通批次可显式设置：

```sh
RUN_PROVIDER_INTEGRATION=0 RUN_TEMPORAL_INTEGRATION=0 python -B -m pytest -q -rs -p no:cacheprovider
```

提供 AGENT_RUNTIME_TEST_DATABASE_URL 后，同一批次也会运行 PG 测试；不提供时真实 PG 用例跳过。只使用已授权的测试数据库，fixtures 负责临时 schema 的创建和清理。SQL_ASCII 测试数据库可在连接环境设置 PGCLIENTENCODING=UTF8，不改变数据库编码。

需要真实环境时，按第 4 节准备依赖，再显式开启 RUN_TEMPORAL_INTEGRATION=1 或 RUN_PROVIDER_INTEGRATION=1。Provider 调用可能计费；后者会开启现有 Codex 和 Claude 用例，不是选择某一个模型。模型、工具、超时和登录要求以所选测试的 Profile/fixture 为准。公共文档不填写 DSN、token 或密码。

向同一 pytest 命令添加 --junitxml=PATH 可导出机器可读结果。PATH 由调用者选择新的报告路径，避免覆盖旧报告；pytest 默认终端输出已含真实计数与 -rs 的跳过原因。

## 3. 怎样判断这一批是否完成

pytest 退出零表示实际执行的断言未失败，不表示所有用例都执行了。先核对本次选择、通过数、失败数和 skip 原因。必需用例仍未运行时，这一批的验证尚未完成。JUnit 中的 skipped 也不能算通过；CI 消费报告时要保留这一差异。

分组中的测试文件可能含有其他回归或集成测试，同一文件也可能服务多个能力分组。若要一次运行全仓，直接使用全仓命令，避免把所有分组命令串起来重复执行。正确拒绝负例可以通过；模型返回合法的 non_pass 或 blocked 也可能满足 transport 测试。

当前批次结果不能替代完整 T2 11 的 AgentCapabilityVerificationResult。完整 Example、所需真实环境或完整能力证据未齐时，保留未完成状态。

## 4. 用例索引

- [编译 Module 与检查 Reviewer 格式](#module_release_assembly_case)
- [模型输入输出与工具边界](#agent_invocation_case)
- [调用已注册 Module 与重放](#module_execution_case)
- [Workflow 注册、并行与恢复](#workflow_graph_case)
- [查看执行与注册记录](#ledger_inspection_case)
- [真实 PostgreSQL 写入与重新读取](#persistent_runtime_case)
- [真实 Temporal 恢复与取消](#durable_backend_case)
- [真实 Provider 与 Reviewer 调用](#live_module_transport_case)
- [独立安装与 public API](#public_package_case)

<a id="module_release_assembly_case"></a>

### 4.1 编译 Module 与检查 Reviewer 格式

Case：`module_release_assembly_case`；证据来源：`executable_owner_case`。

验证能力：`explicit_module_loading`, `path_free_release_identity`, `schema_closure`, `prompt_closure`, `policy_closure`, `profile_independence`, `generic_profile_compatibility`, `registered_module_transport`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 测试 fixture 提供完整 Skill/Module source、schemas、policies 和两个不同模型的 Profile。

前置环境：

- 安装测试依赖的 Runtime 源码 checkout；无需真实 Provider。

样例代码位置及单例命令：

`tests/test_agent_runtime_module_authoring.py::test_module_reviewer_exports_release_and_profile_independently`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_module_authoring.py::test_module_reviewer_exports_release_and_profile_independently
```

`tests/test_agent_runtime_reviewer_output_format.py::test_different_module_specific_schemas_share_the_format`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_reviewer_output_format.py::test_different_module_specific_schemas_share_the_format
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_module_authoring.py tests/test_agent_runtime_registry_candidate_compilation.py tests/test_agent_runtime_reviewer_output_format.py
```

预期结果：

- ModuleReviewer.from_registration().export() 在只改 Profile 时保留相同 Module release hash，Variant hash 改变。
- 不同 Reviewer 的 schema 使用共同输出格式；格式拒绝和兼容负例见同组测试。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="agent_invocation_case"></a>

### 4.2 模型输入输出与工具边界

Case：`agent_invocation_case`；证据来源：`executable_owner_case`。

验证能力：`inline_semantic_input`, `structured_output`, `tool_free_execution`, `runtime_hosted_self_test`, `authorized_gateway_read`, `attempt_workspace`, `context_isolation`, `network_enforcement`, `provider_failure_normalization`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- fixture 提供无工具 Profile 或声明 Gateway 的 Profile，以及受控输入和工具响应。

前置环境：

- 普通用例使用 Provider 测试替身；真实调用的开启方式见批量运行说明。

样例代码位置及单例命令：

`tests/test_agent_runtime_native_structured_output.py::test_tool_free_profile_rejects_undeclared_gateway_surface_before_provider`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_native_structured_output.py::test_tool_free_profile_rejects_undeclared_gateway_surface_before_provider
```

`tests/test_agent_runtime_native_structured_output.py::test_gateway_read_authorizes_each_resource_call_and_records_lineage`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_native_structured_output.py::test_gateway_read_authorizes_each_resource_call_and_records_lineage
```

`tests/test_agent_runtime_claude_native_tools.py::test_profile_drives_one_cli_command_builder`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_claude_native_tools.py::test_profile_drives_one_cli_command_builder
```

`tests/test_agent_runtime_claude_native_tools.py::test_live_claude_native_tools_in_ab`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_claude_native_tools.py::test_live_claude_native_tools_in_ab
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_native_structured_output.py tests/test_agent_runtime_claude_native_tools.py tests/test_agent_runtime_public_adapter_contracts.py tests/test_agent_runtime_attempt_workspace.py
```

预期结果：

- 无工具 Profile 拒绝未声明 Gateway，Provider 不进入；允许的 Gateway 调用经过逐次授权并记录实际调用。
- structured output、workspace、隔离和错误路径由同组测试断言，真实模型结果只由 live 用例证明。
- Claude 原生工具配置与实际参数见 agent_runtime_claude_native_tools.md；AB 正向、越界及注册 Reviewer/PG 用例在同一测试文件。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="module_execution_case"></a>

### 4.3 调用已注册 Module 与重放

Case：`module_execution_case`；证据来源：`executable_owner_case`。

验证能力：`module_run`, `multiple_variants`, `evaluation_and_selection`, `retry_budget`, `idempotent_replay`, `operation_boundary_enforcement`, `cancellation`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- fixture 注册一节点 Workflow；run_registered_workflow_module 接收 {"value": "example"} 和同一个 key。隔离 Module 的计时样例使用两个 Variant，每次预算 10 秒，耗时为 (6, 6) 或 (6, 11) 秒。

前置环境：

- 普通用例使用内存 stores 和 Provider 测试替身。
- 本组的 PG 用例需要 AGENT_RUNTIME_TEST_DATABASE_URL；未提供时这些用例跳过。

样例代码位置及单例命令：

`tests/test_agent_runtime_registered_module_execution.py::test_candidate_result_replay_preserves_policy_and_recorded_bytes`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_registered_module_execution.py::test_candidate_result_replay_preserves_policy_and_recorded_bytes
```

`tests/test_agent_runtime_registered_module_execution.py::test_invalid_input_stops_before_authorization_or_provider`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_registered_module_execution.py::test_invalid_input_stops_before_authorization_or_provider
```

`tests/test_agent_runtime_terminal_evidence.py::test_sequential_variants_use_their_own_attempt_start`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_terminal_evidence.py::test_sequential_variants_use_their_own_attempt_start
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_execution_records.py tests/test_agent_runtime_execution_authorization.py tests/test_agent_runtime_module_evaluation.py tests/test_agent_runtime_registered_module_execution.py tests/test_agent_runtime_terminal_evidence.py
```

预期结果：

- 首次执行输出 {"value": "done"}；同 key 重放返回相同 execution 和输出，Provider 替身只调用一次。
- evaluated_single 保持未决 candidate，没有伪造 Resolution；非法输入在授权或 Provider 调用前拒绝。
- 隔离入口的每个 Variant 独立计时：预算各 10 秒、依次各用 6 秒时都完成；真实超时仍失败。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="workflow_graph_case"></a>

### 4.4 Workflow 注册、并行与恢复

Case：`workflow_graph_case`；证据来源：`executable_owner_case`。

验证能力：`graph_authoring`, `sequential_and_branch_routing`, `parallel_fan_out_and_join`, `revision_loop`, `wait_and_external_event`, `crash_recovery`, `portable_workflow_registration`, `per_registry_execution_binding`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 同一 Workflow export 注册到两个独立 Registry；并行 fixture 声明两个 branch 和一个 join。

前置环境：

- 本组使用内存 Registry 与受控执行替身，不启动真实 Temporal。

样例代码位置及单例命令：

`tests/test_agent_runtime_workflow_authoring.py::test_same_workflow_export_registers_into_two_independent_registries`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_workflow_authoring.py::test_same_workflow_export_registers_into_two_independent_registries
```

`tests/test_agent_runtime_parallel_workflow.py::test_parallel_group_dispatches_branches_concurrently_and_joins_once`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_parallel_workflow.py::test_parallel_group_dispatches_branches_concurrently_and_joins_once
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_workflow_authoring.py tests/test_agent_runtime_parallel_workflow.py tests/test_agent_runtime_product_host_execution_api.py
```

预期结果：

- 同一 origin 可以复用，两个 Registry 的执行配置独立。
- 两个 branch 并发执行，join 执行一次；恢复与失败路径见同组回归测试。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="ledger_inspection_case"></a>

### 4.5 查看执行与注册记录

Case：`ledger_inspection_case`；证据来源：`executable_owner_case`。

验证能力：`attempt_and_workflow_ledger`, `usage_truth`, `release_inspection`, `execution_inspection`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 输入为 fixture 中已记录的 Workflow、Attempt、usage、输入输出引用及 Registry facts。

前置环境：

- 使用固定的 execution trace 与 release fixtures，无需数据库或模型。

样例代码位置及单例命令：

`tests/test_agent_runtime_execution_inspection.py::test_execution_trace_projects_directly_to_portable_inspector_view`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_execution_inspection.py::test_execution_trace_projects_directly_to_portable_inspector_view
```

`tests/test_agent_runtime_release_inspection.py::test_release_inventory_projects_prompt_components_under_schema_v4`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_release_inspection.py::test_release_inventory_projects_prompt_components_under_schema_v4
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_execution_inspection.py tests/test_agent_runtime_release_inspection.py tests/test_workflow_execution_ledger_recording.py
```

预期结果：

- Inspector 从记录生成只读视图；样例的 Module 已完成，但 Workflow 仍为 running，不能推断整体结束。
- usage 和 release 内容与原始记录一致，Inspection 不成为第二份 Ledger。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="persistent_runtime_case"></a>

### 4.6 真实 PostgreSQL 写入与重新读取

Case：`persistent_runtime_case`；证据来源：`environment_gate`。

验证能力：`persistent_registry`, `persistent_ledger`, `persistent_inspection`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 在临时 PG stores 执行一节点 Workflow，分别使用正常输出、Provider 失败和 schema 失败 fixture。

前置环境：

- 调用者显式提供 AGENT_RUNTIME_TEST_DATABASE_URL；现有 fixtures 创建和清理独立临时 schema。
- SQL_ASCII 数据库可配置 PGCLIENTENCODING=UTF8；不修改服务器编码。

样例代码位置及单例命令：

`tests/test_agent_runtime_registered_module_execution.py::test_postgres_fresh_read_and_replay_preserve_candidate_policy`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_registered_module_execution.py::test_postgres_fresh_read_and_replay_preserve_candidate_policy
```

`tests/test_agent_runtime_registered_module_execution.py::test_postgres_concurrent_first_call_has_one_provider_entry`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_registered_module_execution.py::test_postgres_concurrent_first_call_has_one_provider_entry
```

`tests/test_agent_runtime_terminal_evidence.py::test_postgres_attempt_clock_excludes_run_preparation`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_terminal_evidence.py::test_postgres_attempt_clock_excludes_run_preparation
```

`tests/test_agent_runtime_terminal_evidence.py::test_postgres_existing_attempt_start_never_refreshes_budget`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_terminal_evidence.py::test_postgres_existing_attempt_start_never_refreshes_budget
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_postgres_release_store.py tests/test_agent_runtime_postgres_execution_ledger.py tests/test_agent_runtime_registered_module_execution.py tests/test_agent_runtime_terminal_evidence.py
```

预期结果：

- 新建查询连接能读取相同执行记录和内容 hash；重放不重复调用 Provider，不伪造 candidate Resolution。
- 同 key 并发首次调用只有一个 Provider 入口；PG 是真实连接，Provider 仍是测试替身。
- Attempt 起点不包含 Run 准备时间；已有开始记录不刷新预算，已完成结果重放不再次调用。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="durable_backend_case"></a>

### 4.7 真实 Temporal 恢复与取消

Case：`durable_backend_case`；证据来源：`environment_gate`。

验证能力：`durable_backend`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- fixtures 提供两个 Cell、独立 worker、事件及取消请求，并在本地测试 server 上运行。

前置环境：

- 设置 RUN_TEMPORAL_INTEGRATION=1，并安装 temporal 测试依赖；fixtures 启动本地 Temporal dev server。
- SDK 可能需要取得 server binary；先准备运行环境。未开启时真实集成用例跳过。

样例代码位置及单例命令：

`tests/test_agent_runtime_temporal_integration.py::test_real_temporal_two_cell_durable_execution`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_temporal_integration.py::test_real_temporal_two_cell_durable_execution
```

`tests/test_agent_runtime_temporal_target_adapter.py::test_real_target_temporal_cancellation_is_replay_safe`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_temporal_target_adapter.py::test_real_target_temporal_cancellation_is_replay_safe
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_temporal_integration.py tests/test_agent_runtime_temporal_target_adapter.py
```

预期结果：

- 真实 Temporal 验证隔离、事件恢复、重放与 worker 恢复；取消重放不重复产生影响。
- 测试结束按既有 fixture 关闭 worker 和 server；纯内存测试不能替代这些真实集成结果。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="live_module_transport_case"></a>

### 4.8 真实 Provider 与 Reviewer 调用

Case：`live_module_transport_case`；证据来源：`environment_gate`。

验证能力：`live_provider_adapter`, `registered_module_transport`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 已注册测试 Module、固定 prompt/input/schema 与 transport Profile；审核 smoke 消费受控 Design fixture。

前置环境：

- 设置 RUN_PROVIDER_INTEGRATION=1，准备既有测试所用 Codex/Claude executable、SDK 和有效登录。
- 真实调用可能计费并消耗额度；先检查测试中的模型、超时、工具和环境设置，不自动登录。

样例代码位置及单例命令：

`tests/test_agent_runtime_native_structured_output.py::test_live_codex_evaluation_runs_through_run_module`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_native_structured_output.py::test_live_codex_evaluation_runs_through_run_module
```

`tests/test_agent_runtime_managed_design_reviewer.py::test_registered_design_reviewer_runs_live_opus_5_through_runtime`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_managed_design_reviewer.py::test_registered_design_reviewer_runs_live_opus_5_through_runtime
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_native_structured_output.py tests/test_agent_runtime_managed_design_reviewer.py
```

预期结果：

- 真实调用通过 Runtime 交付输入、验证输出并记录执行；Codex/Claude 的不同用例各自提供证据。
- Reviewer 返回合法 non_pass 或 blocked 不代表 transport 失败；Provider 退出零也不代表输出校验通过。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

<a id="public_package_case"></a>

### 4.9 独立安装与 public API

Case：`public_package_case`；证据来源：`referenced_peer_result`。

验证能力：`public_package`

输入与 fixtures：

- 本次 Runtime 候选的准确引用与 hash。
- 本次测试代码的准确引用与 hash。
- 本次依赖和配置的准确引用与 hash。
- 从 Runtime 源码构建并安装临时 wheel，在独立解释器里使用 public API。

前置环境：

- 准备已有 build/test 依赖；测试在临时源码和安装目录中构建 wheel，不联网安装依赖。

样例代码位置及单例命令：

`tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_import_uses_public_namespace_without_domain_packages`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_import_uses_public_namespace_without_domain_packages
```

`tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_executes_target_release_registry_module_slice`

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_packaging_boundary.py::test_clean_wheel_executes_target_release_registry_module_slice
```

整组命令：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_packaging_boundary.py tests/test_agent_runtime_conformance_package.py
```

预期结果：

- wheel 的 public import 与注册执行样例不依赖宿主 domain package。
- 随包 docs 与源码 docs 一致；这些测试结果不替代完整验证所需的独立 package conformance 结果。

结果与证据：

- pytest 显示真实 passed、failed、skipped 及 skip 原因；可用 --junitxml 导出报告。
- 样例中的断言核对实际输出与记录；测试通过不代表完整 T2 11 或被审对象通过。

清理：

- 只清理本次用例创建的临时资源，保留需要交付的结果。

失败处理：

- 测试装置故障由 Agent Capability Verification 负责人处理。
- Runtime 能力缺陷按其所属 Design 与错误含义处理。

需要重跑的变化：

- Runtime 候选或测试代码变化后重跑。
- 依赖或环境配置变化后重跑，不把旧报告作为新候选的通过结果。

## 5. 更新用例与重新生成文档

能力分组和样例导航在 conformance_agent_capability_verification.py 中维护。example_test_refs 指向真实测试函数；新增普通回归放入 tests 后由 pytest 自动收集，涉及能力分组时再更新对应 case。不要手工同时编辑 docs 与随包投影。Claude 环境说明维护在 src/agent_runtime/docs/agent_runtime_claude_native_tools.md，docs 下的同名页由下面的命令复制。

从源码根目录调用现有 renderer 更新两份文档：

```python
from pathlib import Path
from agent_runtime.testing.conformance_agent_capability_verification import (
    required_agent_capability_cases, required_agent_capability_inventory,
    render_agent_capability_catalog_markdown, render_agent_capability_runbook_markdown,
)

cases = required_agent_capability_cases()
documents = {
    "agent_runtime_capabilities.md": render_agent_capability_catalog_markdown(
        required_agent_capability_inventory(), cases),
    "agent_runtime_capability_runbook.md": render_agent_capability_runbook_markdown(cases),
}
documents["agent_runtime_claude_native_tools.md"] = Path(
    "src/agent_runtime/docs/agent_runtime_claude_native_tools.md"
).read_text(encoding="utf-8")
for directory in (Path("docs"), Path("src/agent_runtime/docs")):
    for name, body in documents.items():
        (directory / name).write_text(body, encoding="utf-8")
```

生成后运行以下检查，再按第 2 节跑本次需要的全仓批次：

```sh
python -B -m pytest -q -rs -p no:cacheprovider tests/test_agent_runtime_capability_verification.py
```
