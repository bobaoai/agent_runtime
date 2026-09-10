# Agent Runtime Registration Runbook

本 Runbook 面向把已批准 Module 或 Workflow 接入 Agent Runtime 的宿主开发者。它随
`agent-runtime-core` 分发，说明如何使用当前 package 的 public API 完成 source loading、release
compilation、persistent registration、exact resolution 和 active selection。

Runtime Design Contract 定义稳定语义，public Python API 定义当前可执行接口。本 Runbook 不创造新的
release 类型、权限规则或持久化协议；当文字与 public API 不一致时，停止 registration 并报告 package
drift。

接口定义见从源码自动生成的 [Reviewer API reference](agent_runtime_reviewer_api.md)。
先读其中的 ModuleReviewer 类说明，确定固定定义、独立执行参数和存储归属；再按本 Runbook
完成一次注册。注册完成后的每次审核交给固定执行绑定，只提交本次输入与执行 key。
当前单节点执行入口的参数、授权依赖、重放限制和失败处理也在该接口参考中。
测试用途不要求复制 Reviewer；切换 Profile 是否需要新的 Module，取决于 Module 内容是否改变及
兼容声明是否满足，具体规则以类说明和现有 Registry 合同为准。

<a id="new-reviewer"></a>

## 0. 注册新的 Reviewer 并在宿主测试 / Register a new reviewer and test it

本节是日常操作入口。你无需读取 Runtime 生产源码或测试 fixture；先按当前任务选择：

| 当前已有的东西 | 下一步 |
| --- | --- |
| 只有“我需要一个新的 Reviewer”的想法 | 由该审核对象的负责人完成 Reviewer 定义与审核；注册工具不会替你编写职责、prompt 或 schema |
| 已批准的准确 Reviewer source，尚未注册 | [准备](#prepare-reviewer)，然后[注册固定定义](#register-reviewer) |
| 已有准确 Module release，想运行一份新材料 | 直接进入[首次或再次测试](#test-reviewer)，不重新编译或注册 |
| 已有 execution ID，想看结果或排错 | 进入[查询](#inspect-reviewer)，不再次调用模型 |

参数含义以 [ModuleReviewer](agent_runtime_reviewer_api.md#modulereviewer) 及其公开方法说明为准。
下列示例只组合现有 public API，不创建新的 wrapper、数据库结构或测试 Reviewer。

<a id="prepare-reviewer"></a>

### 0.1 准备资料、固定配置和操作环境

已有完整且已审的 Reviewer source 时，优先使用正式 CLI：

```sh
agent-runtime-registry register-reviewer --help
agent-runtime-registry register-reviewer --root /path/to/host --source-root /path/to/source \
  --skill-id reviewed-skill --module-id reviewed_reviewer --version v1
agent-runtime-registry load --root /path/to/host --kind workflow --id reviewed_reviewer_review
agent-runtime-registry load --root /path/to/host --kind workflow --id reviewed_reviewer_review --version v1
```

示例中的身份与版本应替换为准确已审 source 的值。source-root 省略时使用 root。
命令直接读取 source，自动解析 Runtime 默认、编译固定单节点 Workflow 并保存、回读；不需要
先手写 Policy、Profile 或 bundle。软件安装使用宿主明确的标准安装命令，与注册分开。
该命令不会安装依赖、创建环境、登录 Provider、创建 PG schema 或调用模型。

新 source 的 runtime_module_registration_v3 允许省略三个 Policy 引用；v2 仍要求完整声明。
显式引用保持原限制，未声明 claude_cli 的 source 会返回 MODULE_EXECUTION_PROFILE_INCOMPATIBLE，
注册不会替作者补 transport。Runtime 默认值与准确错误见生成的 API，而不由本手册另定义。
重复注册保持已保存版本的能力与绑定；显式 --model-id 或 --reasoning-profile 可改变独立模型选择。
切换模型后的新调用使用新执行身份；历史执行按原 execution ID 查询 Ledger，不读取当前默认来重跑。

stdout 包含真实注册 records、保存路径和 readback=verified。失败返回非零退出码，stderr 返回
error_type、原生 error_code（若存在）和 detail；保留错误，不随机换版本或换模型继续。

**客户端兼容与升级顺序。** 新默认记录包含参与 hash 的 reviewer_defaults 或
model_defaults_version。不认识这些字段的旧 Runtime 会因 hash 不匹配而拒绝读取。
PostgreSQL 的 load_release_registry 会解码整个 catalog，所以即使调用者仍选旧 Module、
未切换 active pointer，共享 catalog 中的一条新记录也可能使旧客户端无法加载。
本地 register 同样需要恢复已有 catalog；单个旧版本文件可读，不证明整个混合目录可读。
向共享 store 注册前，先升级所有受影响读取者并核对实际构建身份；相同 dev 版本字符串不足以判断。
可以在独立测试目标验证兼容，不自动更改生产连接或迁移 schema。
旧记录内容保持不变。新记录写入后，恢复可用性应恢复支持新格式的客户端；仅降级软件并保留
混合 catalog 不能恢复读取，也不应通过删改历史记录或增加 DDL 绕过内容校验。

本地保存、版本加载和 CLI 验证：

宿主已有注册调用可传入 `root`：

```python
registration = register_runtime_module_plugin(store, plugin, root=project_root)
```

注册成功后，Module 保存到 `.runtime/module/<module_id>/<version>.json`，Workflow 保存到
`.runtime/workflow/<workflow_id>/<version>.json`。单节点 Workflow 同样在 workflow 中。
保存内容包含该对象的固定依赖，旧 Workflow 不会因其他 Module 文件更新而被重新拼装。

也可直接从已编译的完整 bundle JSON 通过安装的 CLI 注册、加载：

```sh
agent-runtime-registry register --root /path/to/host --bundle bundle.json --plugin-id review_package --plugin-version v1
agent-runtime-registry load --root /path/to/host --kind workflow --id review_workflow
agent-runtime-registry load --root /path/to/host --kind workflow --id review_workflow --version v1
```

CLI register 使用既有内存 Registry 校验并保存结果，不自行连接 PG；宿主要同时登记 PG 时使用上面的
既有 store 调用。未指定版本时选最近成功保存的新定义版本；重复保存不改变顺序，不使用文件 mtime。
这里没有 active/latest 指针文件或激活步骤。PG 既有入口不因此改义。

直接加载用 `load_runtime_registration(root, "workflow", workflow_id, version)`；单节点审核可用
`run_local_workflow_module`，由它从文件取出 Workflow、Module 和唯一绑定，再调用原 Evaluation 内核。
宿主继续提供原有实时授权、Adapter 和 Ledger 端口，无需重新编译或组装定义。多节点图完整保存/加载，
执行使用其已有图入口；这不新增生产执行用途。

这些接口的实际参数、返回和错误见[源码生成的 API 手册](agent_runtime_reviewer_api.md#load_runtime_registration)。
普通 source 注册使用上面的 CLI 和 Runtime 默认解析。下方显式 Policy/Profile 的 Python 示例
保留为底层接口和既有宿主的兼容说明；调用新 CLI 时无需手工执行这些组装步骤。

先使用当前宿主选定的 Python 确认安装来源，并打开同一安装包里的说明：

```python
from importlib.metadata import version
from importlib.resources import files
import agent_runtime

print(version("agent-runtime-core"))
print(agent_runtime.__file__)
print(files("agent_runtime").joinpath("README.md"))
print(files("agent_runtime").joinpath("docs/agent_runtime_registration_runbook.md"))
```

保存准确安装来源；相同版本字符串不证明相同源码。缺失文档或安装与目标版本不一致时，先由
Runtime 安装维护者处理。PostgreSQL 操作需要安装包的 `postgres` 可选依赖；连接由宿主注入。

以下是示例所需的输入来源表，不是新的配置文件格式。值必须由负责人提供，不能采用 fixture 中的
ID、hash、数据库或授权替身。表中的名称是后续 Python 示例使用的变量。

| 输入 | 提供方与内容 |
| --- | --- |
| `project_root`、`skill_id`、`module_id`、`module_version` | Source owner 提供准确、已审的 Skill/Module source 与批准版本；根目录为 Path，两个 ID 对应真实注册文件 |
| `behavior_binding`、`evaluation_binding`、`retry_binding` | Runtime 环境维护者提供已批准 Policy 的 `(release_ref, release_sha256)` 元组；与 source 引用一致 |
| `profile_binding` | 环境维护者提供本次固定且相容的 Profile `(release_ref, release_sha256)`，用于编译前的兼容核对 |
| `database_url`、`registry_schema` | 宿主提供本次获准使用的 Registry 连接与 schema；凭据不写入任务材料、日志或本手册 |
| `plugin_id`、`plugin_version` | 注册操作者提供本次明确的发布包身份；与 Reviewer 的 Module 版本分开 |
| 已配置的宿主执行入口 | 宿主集成维护者提供对应 Module 的准确 Workflow、Variant、Profile 和授权/存储绑定，以及命令或已绑定函数 |
| 本次审核输入、输出 validator | Source owner 按该 Reviewer 的真实输入 schema 提供；输出由该对象的 schema 与语义 validator 判断 |

先确认测试入口支持本次 Profile 和操作声明，再写 Registry，可以避免注册完成后才发现无法测试。
固定环境只需配置一次。每次新审核提交新材料和新执行 key，保持未改变的 Module release。
缺少任何必要输入时停在对应提供方，不临时选模型、修改声明或编译样例版本。

<a id="register-reviewer"></a>

### 0.2 注册一次固定定义

本段会写入明确提供的 Registry，仅在本次注册已授权且上表输入齐备后执行。它注册 Module 及其
固定依赖，不生成 Workflow，不设置 active pointer，也不调用模型。具体接口见
[加载 source](agent_runtime_reviewer_api.md#modulereviewerfrom_registration)、
[export](agent_runtime_reviewer_api.md#modulereviewerexport) 和
[origin_bundle](agent_runtime_reviewer_api.md#moduleexportorigin_bundle)。

<!-- example:register-reviewer:start -->
```python
from agent_runtime import ModuleReviewer, RuntimeModulePlugin, register_runtime_module_plugin
from agent_runtime.registry import PostgresRuntimeReleaseStore

store = PostgresRuntimeReleaseStore.from_dsn(database_url, schema=registry_schema)
if store.installed_schema_release().state != "ready":
    raise RuntimeError("Registry schema is not ready; return to the schema owner")
registry = store.load_release_registry()
reviewer = ModuleReviewer.from_registration(
    project_root, skill_id=skill_id, module_id=module_id,
)
exported = reviewer.export(
    module_version=module_version,
    behavior_policy=registry.get_behavior_policy(*behavior_binding),
    evaluation_policy=registry.get_evaluation_policy(*evaluation_binding),
    retry_policy=registry.get_retry_policy(*retry_binding),
    execution_profile=registry.get_execution_profile(*profile_binding),
)
plugin = RuntimeModulePlugin(
    plugin_id=plugin_id, plugin_version=plugin_version,
    release_bundle=exported.origin_bundle,
)
registration = register_runtime_module_plugin(store, plugin)
registration.validate()
module_ref = exported.module_release.release_ref
module_hash = exported.module_release.release_sha256

# Fresh persistent read: do not use the in-memory export as readback evidence.
reopened = PostgresRuntimeReleaseStore.from_dsn(database_url, schema=registry_schema)
resolved = reopened.load_release_registry().get_module(module_ref, module_hash)
if resolved != exported.module_release:
    raise RuntimeError("Registered Module readback differs from the compiled release")
print({"module_release_ref": module_ref, "module_release_sha256": module_hash})
```
<!-- example:register-reviewer:end -->

保存这组 ref/hash 以及本次 Runtime 安装来源、目标 Registry 和 source 审核依据。相同 bundle
重复提交使用原生幂等注册；同一 ref 的内容冲突应返回发布负责人，不覆盖已有记录或临时改个版本绕过。
数据库结构未就绪时停止，本段不会自动建表或迁移。

`origin_bundle` 刻意不含 Profile 和 Variant。上段的 Profile 是读取已批准配置做兼容核对；它没有
重新注册 Profile，也没有把 export 附带的 standalone Variant 当成 Workflow Variant。
将 Module 用于宿主执行时，由宿主集成维护者按[Workflow 组装](#5-可选-workflow-assembly)和
[注册操作](#8-registerresolve-与-active-pointer)固定真实 Workflow/Variant。缺少这个绑定，就仍未具备
首次测试的条件；不能用测试夹具构造的授权对象代替。

<a id="test-reviewer"></a>

### 0.3 首次测试与再次调用 / Test a reviewer

先进入宿主项目文档的“Reviewer 测试/运行”入口。宿主应交付已绑定的操作命令或函数及其帮助，
而不是要求每位使用者重新组装 Runtime 的环境参数。该入口必须固定目标 Module/Workflow、
Variant/Profile、调用授权和 Registry/Ledger，保持本次选定的 Runtime 软件来源。

当前 Runtime 的公共入口是
[run_registered_workflow_module](agent_runtime_reviewer_api.md#run_registered_workflow_module)，
它支持已注册的单节点 Workflow evaluation，并保留明确的宿主授权接口要求；不能从这个函数存在
推断任意宿主、任意工具或 Claude/Codex 配置已接通。已有接口参考列出了完整参数及失败限制。

**找不到宿主命令或固定执行绑定时，停止在宿主集成维护者。** 本 Runbook 不提供一个尚未实现的
通用 `reviewer test` 命令。Runtime 开发者的 live pytest sample 验证其声明的 fixture 与能力；它不能
代替你的新 Reviewer 配置，也不能为了测试而悄悄创建另一个 Module release。

调用前核对，调用后分别记录：

| 时点 | 操作者要核对的事实 |
| --- | --- |
| 调用前 | 当前输入符合该 Reviewer 的 schema；实际固定 Module/Profile/Workflow 与批准值一致；入口支持操作声明；使用明确的新执行 key |
| 调用后 | 保存返回的 execution ID、真实执行状态、Attempt、Module/Profile 身份和用量；调用异常时保留原始错误及已取得的执行 ID |
| 输出判断 | 按该 Reviewer 的输出 schema 和语义 validator 判断，区分“执行完成”与文稿 verdict；合法 non_pass 可以表示被审材料需要修改 |
| 持久性 | 使用下一节的新查询连接读取同一 execution ID；即时内存结果不能代替 PG 回读 |

再次审查新材料时保持相同固定绑定，提交新的输入与 key；不重新读取 authoring source、export 或
register。相同 key 用于同一执行重放，不能换材料。已有 started 记录但没有 committed 结果时，使用
Runtime 原恢复入口；不要换 key 重复不明效果。是否激活或正式部署另行决定。

<a id="inspect-reviewer"></a>

### 0.4 查询执行结果与失败 / Inspect a review

优先用宿主现成的 inspect 命令。直接使用公共查询 API 时，`execution_id` 来自刚才的真实返回；
`database_url` 与 `execution_schema` 来自该次执行使用的 Ledger 配置。连接和查询权限由宿主提供。
这里的 schema 是 Execution Ledger，不是上段的 Registry schema。

<!-- example:inspect-reviewer:start -->
```python
from agent_runtime.ledger import PostgresRuntimeExecutionQueryStore

query = PostgresRuntimeExecutionQueryStore.from_dsn(
    database_url, schema=execution_schema,
)
trace = query.load_trace(execution_id)
if not trace.records:
    raise RuntimeError("No committed records; verify the execution ID and Ledger binding")
metadata = query.list_content_metadata(execution_id)
print({"execution_id": execution_id, "record_count": len(trace.records),
       "content_metadata": [dict(item) for item in metadata]})
```
<!-- example:inspect-reviewer:end -->

这一步只读取已提交事实，不重跑模型。按 trace 中的 Attempt 状态、failure 和输出引用检查结果。
需要正文时，用实际记录的 `content_ref` 调用 `query.load_content(execution_id, content_ref)`；返回
None 表示该引用没有可读内容，不能当成成功空输出。输出、prompt、诊断可能包含私有材料，只向
获授权读者展示，不把凭据或原文发到共享日志。

本轮注册、模型执行、输出校验、持久回读要分别有证据。注册成功不等于实际测试成功；测试环境缺件
也不等于 Reviewer 对文稿给出 blocked。具体失败保留原生错误和所属负责人：源或 schema 问题找
source owner，Profile/Adapter/入口不相容找宿主集成维护者，Registry/Ledger 不可用找存储维护者。

## 1. 开始前确认

调用方必须已经拥有：

- 已批准的 Module 或 Workflow meaning；
- 精确的 Skill、Module registration、prompt、input/output schemas 和 owner contract；
- Module 所需的 Behavior、Evaluation、Retry Policy releases；
- 需要执行时使用的 exact Execution Profile release；
- 明确提供的 Runtime Release Store；
- PostgreSQL store 使用的 host-supplied DSN 和 schema name。

Registration 不负责创作这些输入，也不解析 credential。它不会调用 provider、执行 Reviewer、设置产品
权限或写入业务数据库。

## 2. Registration Flow

```mermaid
flowchart LR
    I["Installed agent-runtime-core"] --> D["Read packaged README<br/>Registry Design Contract · this Runbook"]
    D --> S["Module.from_registration<br/>or ModuleReviewer.from_registration"]
    S --> E["Module.export<br/>path-free immutable closure"]
    E --> W["Optional Workflow.from_graph().export()"]
    E --> P["RuntimeModulePlugin"]
    W --> P
    P --> G["installed_schema_release preflight"]
    G -->|ready| R["register_runtime_module_plugin"]
    G -->|not ready| B["Return Registry deployment/migration owner"]
    R --> X["Resolve exact ref + hash from a new store connection"]
    X --> A["Optional separate active-pointer change"]
```

## 3. 读取随包文档与 public API

从当前 Python environment 解析真实安装来源：

```python
from importlib.metadata import version
from importlib.resources import files
import agent_runtime

runtime_version = version("agent-runtime-core")
runtime_origin = agent_runtime.__file__
runtime_readme = files("agent_runtime").joinpath("README.md")
registration_runbook = files("agent_runtime.docs").joinpath(
    "agent_runtime_registration_runbook.md"
)
registry_design = files("agent_runtime.design_contract").joinpath(
    "agent_runtime_01_module_contract_and_assembly.md"
)
```

Registration 只使用公开导出：

```python
from agent_runtime import (
    Module,
    ModuleReviewer,
    ReleaseSubjectKind,
    RuntimeModulePlugin,
    RuntimeReleaseBundle,
    Workflow,
    register_runtime_module_plugin,
)
from agent_runtime.registry import PostgresRuntimeReleaseStore
```

缺少所需 public symbol、随包文档或安装来源时停止。不要导入 private module、已删除 symbol 或宿主保存的
compatibility facade。

Editable install 需要额外记录 source root、exact commit 和 Runtime package source 的工作树状态。相同
package version 不足以证明相同 code identity。

## 4. 加载并编译 Module

Reviewer 使用当前公开 loader：

```python
reviewer = ModuleReviewer.from_registration(
    project_root,
    skill_id=skill_id,
    module_id=module_id,
)
```

其他 role 使用其已实现的 `Module` subclass。当前 loader 只读取调用方明确提供的 project root 下的固定
authoring closure：

```text
.claude/skills/<skill_id>/runtime_modules/<module_id>/
├── module_registration.json
├── prompt.md
└── schemas/
    ├── input.schema.json
    └── output.schema.json
```

调用 `.export(...)` 时显式提供 Module version，以及 exact Behavior、Evaluation、Retry Policy 和可选
Execution Profile releases。返回的 `ModuleExport` 包含 path-free Module origin closure；source path 不进入
release identity，也不参与 production resolution。

Module release 与 Execution Profile 分开。Profile 或 provider 变化通过新的 Profile 与 Variant Policy
release 表达，不要求重写未变化的 Module。

## 5. 可选 Workflow assembly

Workflow graph 只引用 exact Module refs/hashes：

```python
workflow = Workflow.from_graph(
    workflow_candidate,
    module_exports=module_exports,
).export()
```

`module_exports` 必须与 graph 中的 Module closure 完全相等。缺少、额外或 hash 不一致都会在 Registry
mutation 前失败。

## 6. 形成 RuntimeModulePlugin

使用 `ModuleExport.origin_bundle` 或 `WorkflowExport.origin_bundle` 取得 target-independent closure，再把
需要的 Execution Profile 与 Execution Variant Policy releases 合并进一个 `RuntimeReleaseBundle`。
同一 ref 只能对应同一内容；不得手工改变 compiler 返回的 Prompt、Schema、Policy、Module 或 Workflow
records。

```python
plugin = RuntimeModulePlugin(
    plugin_id=plugin_id,
    plugin_version=plugin_version,
    release_bundle=release_bundle,
)
plugin.validate()
```

In-memory `RuntimeReleaseRegistry` 只用于 isolated conformance。只有目标 `PostgresRuntimeReleaseStore`
成功写入并从新连接 round-trip，才能形成 persistent registration evidence。

## 7. PostgreSQL schema preflight

调用方提供 DSN；Runtime 不读取 Infisical、环境文件或其他 credential source：

```python
store = PostgresRuntimeReleaseStore.from_dsn(
    database_url,
    schema=runtime_schema,
)
installation = store.installed_schema_release()
```

只有 `installation.state == "ready"`，且 schema release 与 structure hash 均受当前 Runtime 支持时，才进入
registration。`unknown`、`installing`、结构漂移或不受支持时停止；保留实际安装状态与 Runtime 返回的
异常信息，交给 schema 安装或迁移负责人。

首次安装干净 schema 是独立管理员操作：

```python
store.create_schema(installed_at_utc=installed_at_utc)
```

已有 schema 的升级使用已审核的 `RegistrySchemaMigrationPlan`，再调用 `migrate_schema(...)` 或
`resume_schema_migration(...)`。Registration 不能把 schema installation 或 migration 当成 fallback；未经
明确 DDL 授权时只返回 Registry deployment/migration owner。

## 8. Register、resolve 与 active pointer

Persistent registration 只调用 public sink：

```python
result = register_runtime_module_plugin(store, plugin)
```

Registration 事务性写入整个 dependency-closed bundle。任一 dependency、schema 或 ref/hash conflict
失败时不产生部分结果。相同 exact bundle replay 返回等价 registration result；相同 ref 对应不同内容时
永久冲突。

注册后，使用同一 PostgreSQL schema 的新 store connection 按 exact ref/hash 解析 Module 或 Workflow，
并与 export 比较。Production resolution 不再读取 authoring source。

设置 active pointer 是独立操作：

```python
pointer = store.set_active_release(
    ReleaseSubjectKind.MODULE,
    module_id,
    module_release_ref,
    module_release_sha256,
)
```

清除 pointer 必须携带 expected current ref/hash。Registration 不自动 activation；pointer 变化也不修改
任何 immutable release。

## 9. 完成检查

一次 persistent registration 至少验证：

1. package version、import origin、随包文档与所需 public API 均可解析；
2. Module source closure 完整，owner、prompt 和 schemas 均绑定 exact content；
3. Policy、Profile、Module 与 Workflow dependency refs/hashes 闭合；
4. PostgreSQL schema preflight 为 `ready`；
5. registration、identical replay 和 conflicting-ref refusal 均符合原生结果；
6. 新连接能够按 exact ref/hash 解析已注册 release；
7. active pointer 只有在单独请求后才改变；
8. registration 后删除或改变 authoring source 不会改变已注册 release；
9. generated inspection 与持久 store 的 exact records 一致；
10. 没有 provider invocation、业务数据库写入或 Runtime core 对宿主包的 import。

只报告实际运行过的验证。没有 PostgreSQL binding 时可以形成 `conformance_only`，不能把它表述为
persistent registration。
