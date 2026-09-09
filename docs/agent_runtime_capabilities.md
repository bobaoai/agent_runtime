# Agent Runtime 能力与样例索引

Runtime 负责注册 Module 和 Workflow、执行模型与工具调用、协调恢复并记录执行事实。业务角色、prompt、授权规则和业务数据由宿主提供。

代码定义了 42 项验证能力，分为 9 组。点击使用任务可直接找到样例的测试位置、输入、预期结果和命令。能力列是验证清单，不表示这些能力已全部完成或通过。

## 1. 按任务找到样例

| 使用任务 | Case | 验证能力 |
| --- | --- | --- |
| [编译 Module 与检查 Reviewer 格式](agent_runtime_capability_runbook.md#module_release_assembly_case) | `module_release_assembly_case` | `explicit_module_loading`, `path_free_release_identity`, `schema_closure`, `prompt_closure`, `policy_closure`, `profile_independence`, `generic_profile_compatibility`, `registered_module_transport` |
| [模型输入输出与工具边界](agent_runtime_capability_runbook.md#agent_invocation_case) | `agent_invocation_case` | `inline_semantic_input`, `structured_output`, `tool_free_execution`, `runtime_hosted_self_test`, `authorized_gateway_read`, `attempt_workspace`, `context_isolation`, `network_enforcement`, `provider_failure_normalization` |
| [调用已注册 Module 与重放](agent_runtime_capability_runbook.md#module_execution_case) | `module_execution_case` | `module_run`, `multiple_variants`, `evaluation_and_selection`, `retry_budget`, `idempotent_replay`, `operation_boundary_enforcement`, `cancellation` |
| [Workflow 注册、并行与恢复](agent_runtime_capability_runbook.md#workflow_graph_case) | `workflow_graph_case` | `graph_authoring`, `sequential_and_branch_routing`, `parallel_fan_out_and_join`, `revision_loop`, `wait_and_external_event`, `crash_recovery`, `portable_workflow_registration`, `per_registry_execution_binding` |
| [查看执行与注册记录](agent_runtime_capability_runbook.md#ledger_inspection_case) | `ledger_inspection_case` | `attempt_and_workflow_ledger`, `usage_truth`, `release_inspection`, `execution_inspection` |
| [真实 PostgreSQL 写入与重新读取](agent_runtime_capability_runbook.md#persistent_runtime_case) | `persistent_runtime_case` | `persistent_registry`, `persistent_ledger`, `persistent_inspection` |
| [真实 Temporal 恢复与取消](agent_runtime_capability_runbook.md#durable_backend_case) | `durable_backend_case` | `durable_backend` |
| [真实 Provider 与 Reviewer 调用](agent_runtime_capability_runbook.md#live_module_transport_case) | `live_module_transport_case` | `live_provider_adapter`, `registered_module_transport` |
| [独立安装与 public API](agent_runtime_capability_runbook.md#public_package_case) | `public_package_case` | `public_package` |

## 2. 运行与解释结果

[Runbook](agent_runtime_capability_runbook.md) 提供单例、分组、全仓批量命令及环境要求。完整测试目录由 pytest 自动收集；上表只是面向使用者的能力分组，不能代替全仓回归。

- focused 结果只证明所选用例；正确拒绝预先声明的负例可以算该用例通过。
- 环境缺失或未开启的真实集成测试是未执行，不能计为 passed。
- Provider 调用成功、输出有效与被审对象获准是不同结果。
- 完整 T2 11 还要求 canonical Example、完整能力和所需环境证据；现有 pytest 结果不自动生成这一完成结论。

当前通过数、失败和环境观察以本次实际测试报告为准，不在本索引手工填写。
