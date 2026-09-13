---
name: agent-runtime-registration
description: 使用已安装的 Agent Runtime CLI，把已审 Reviewer source 或已编译 Module、Workflow bundle 注册到指定 root 的 .runtime，并查询准确注册结果。用于新增注册、更新定义版本或回读；不编写 Reviewer、不调用模型、不选择执行配置。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_registration_candidate
  first_authority_ref: agent_runtime/design_contract/agent_runtime_01_module_contract_and_assembly.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime 注册

## 1. Task

将已审的 Reviewer source 或已编译的 Module、Workflow bundle，通过 `agent-runtime-registry`
保存到调用者指定 root 的 `.runtime`，并回读准确结果。已有定义只需查询或运行新材料时，直接使用
已有注册，不重新编译。注册与模型选择分开，Reviewer 默认能力由 Runtime 解析。

现有命令在操作前共用轻量 setup：首次或缺项时准备 `.runtime` 并接入两份随包操作 Skill，已就绪时
不重复写入。无需另执行 Skill 安装命令；具体对象检查仍由注册或查询操作负责。

## 2. Reader Gain

Agent 能找到同一安装版本的 CLI 和操作说明，区分宿主 root 与 source root，完成注册和准确回读，
并把返回的 Workflow 身份交给后续 evaluation。无需搜索生产源码、手写 Policy/Profile 或猜测 Workflow 名称。

## 3. Entry and Exit

“注册 Reviewer”“注册 Module/Workflow”“更新已审定义版本”及“查询已有注册”进入本 Skill。
只有审核想法、尚无已审 source 时，先交 source owner 完成定义与审核。修改任务指令、owner 或
输入输出 schema 属于 source 更新，不在注册途中补写。

当前 Reviewer 注册入口消费 `runtime_module_registration_v4` 任务 source。v2/v3 或其他不相容来源
交回 source owner 明确迁移；操作者不靠删字段、补 transport 或改版本使注册通过。

请求运行已注册对象时，使用 `agent-runtime-evaluation` 完成其支持的普通自测；需要持久执行、
PG 注册或其他宿主专用操作时，按同版本 runbook 交给已有宿主入口，不将这些配置变成普通本地注册前提。
CLI 或必要资料缺失时说明具体缺件及提供方，保留已完成事实。

## 4. Execution Contract

### 4.1 Inputs and Authority

- 调用者明确的宿主 root，以及在该位置注册的授权。root 决定 `.runtime` 的位置，不决定模型权限。
- Reviewer 注册需要 source root、准确 skill ID、module ID、批准的定义版本及对应 source 审核依据。
  source root 省略时使用宿主 root；两者不同时显式传入。
- 需要特定 Workflow 名称时，提供已批准的名称；未提供时 Runtime 生成与 Module 同名、同版本的单节点 Workflow。
- 其他 Module 或 Workflow 使用 source owner 提供的完整已编译 bundle JSON，以及其 plugin ID 和版本。
  仅有 graph 构想或零散记录不足以使用 bundle 注册命令。
- 查询只需目标 root、对象种类和 ID；版本可指定，也可按请求加载最新定义。

任务含义与 source 由其 owner 负责。Registry 的准确合同、默认行为及错误以当前安装的 Runtime
和随包说明为准，定位方法见 §6.1。显式 source 依赖由 loader 校验；缺失或不相容时返回原错误，
不删声明、改版本或手拼默认策略来绕过。

### 4.2 Output and Completion

返回原生 CLI 结果及实际 root、对象种类、ID、版本和 release hash。Reviewer 注册结果应含
`readback=verified`、保存路径及完整 Module、Workflow records；消费返回的 Workflow 身份，不自行拼接名称。
bundle 注册返回对象引用后，用 `load` 分别回读所需对象和准确版本，再报告完成。

Module 保存在 `.runtime/module/<module_id>/<version>.json`，Workflow 保存在
`.runtime/workflow/<workflow_id>/<version>.json`；单节点 Workflow 也在 workflow 中。
文件路径使用 CLI 返回值，格式细节由 Runtime 处理。查询结果包含 `release` 与固定依赖 `bundle`。
这些是本地注册事实，不代表 PG 写入、模型已执行或材料通过审核。

失败时保留退出码、stdout、stderr 和原生错误。写入失败或响应丢失不证明没有保存内容；
先向同一 root 回读准确对象，确认实际结果后再决定是否重复原请求，不随机换版本。

## 5. Boundaries

| 边界 | 可观察的越界 |
| --- | --- |
| CLI 负责注册与保存 | 手写或修补 .runtime JSON，复制测试 builder 代替正式入口 |
| 已审定义与模型选择分开 | 为注册设置模型或环境 Profile，因换模型而重注册未变 source |
| 本地保存与其他宿主操作分开 | 自动连接 PG、建表、切换日常配置或在注册后调用模型 |
| 注册事实与使用结果分开 | 只见文件存在就声称回读成功，或把注册成功说成审核通过 |
| source owner 保留内容决定 | 注册途中修改任务、schema 或旧版字段，或向 v4 source 塞入工具、transport、Policy 参数 |

## 6. Method

### 6.1 找到当前安装的入口与说明

使用宿主选定 Python 环境中的 CLI。先看 `agent-runtime-registry --help`，再读对应子命令帮助。
同环境中可用以下只读代码定位安装版本、来源与随包文档；`python` 指宿主明确提供的解释器。

```python
from importlib.metadata import version
from importlib.resources import files
import agent_runtime

print(version("agent-runtime-core"))
print(agent_runtime.__file__)
package = files("agent_runtime")
print(package.joinpath("design_contract/agent_runtime_01_module_contract_and_assembly.md"))
print(package.joinpath("docs/agent_runtime_registration_runbook.md"))
print(package.joinpath("docs/agent_runtime_reviewer_api.md"))
```

日常操作读 runbook §0.1 和 API reference 中的 CLI commands；底层兼容示例不需要逐个执行。
默认参数、退出码与错误处理查同版本 help 和自动接口说明，不另维护一套配置表。
CLI、文档或构建身份不匹配时返回安装维护者，不回落到 sibling checkout。setup 的具体效果查自动手册
中的 setup_runtime；本地 Skill 冲突按原错误交其 owner 处理，不手工覆盖。向共享目录写入新格式前，
按同版本 runbook 的兼容说明核对受影响读取者；相同 dev 版本字符串不足以证明兼容。

### 6.2 注册准确 source 或 bundle

以下均为示例路径和身份，执行时换成请求提供的值：

```sh
agent-runtime-registry register-reviewer --root /path/to/host --source-root /path/to/source \
  --skill-id approved-skill --module-id approved_reviewer --version approved_version
```

当前 source loader 读取 source root 下
`.claude/skills/<skill_id>/runtime_modules/<module_id>/` 的 `module_registration.json`、
prompt、schemas 及其声明依赖。这个作者目录与当前 Agent 从哪里发现本操作 Skill 是两件事。
缺少完整 source 时由提供方补齐，不为注册手工安装或迁移 Skill。

v4 source 声明任务身份、owner 路径和输入输出 schema 的引用及路径，prompt 来自同一目录。
它不携带模型、transport、工具、技术 Policy 或 Profile 参数；这些由 Runtime 的定义和执行接口
各自处理。准确字段由 loader 和同版本文档定义，注册操作者不重写。

命令解析 Runtime 默认、编译 Reviewer 及单节点 Workflow、保存并回读。默认图与 Module 同名、
同版本；需要已批准的其他图名时增加 `--workflow-id explicit_workflow_id`。不使用旧 `_review`
后缀猜当前图名，既有图也不会因新注册被自动重命名或删除。
注册无需 Provider CLI、登录、PG 或预选模型；执行相容性由后续 Runtime 准备阶段检查。

重复同版本注册会核对任务 source 并复用原定义与依赖，不重新应用当前默认。真实定义变化需要
source owner 批准新版本；同版本不同内容的冲突不能靠临时换版本回避。若 Module 已保存而
Workflow 保存失败，回读后可重试同一请求补齐，不手工拼写缺失文件。

已编译的其他 Module 或 Workflow 使用：

```sh
agent-runtime-registry register --root /path/to/host --bundle /path/to/approved_bundle.json \
  --plugin-id approved_package --plugin-version approved_version
```

保留 bundle 的完整依赖与原记录，不在操作过程中拼图、改内容或发明 plugin 身份。

### 6.3 回读与交接

```sh
agent-runtime-registry load --root /path/to/host --kind workflow \
  --id returned_workflow_id --version approved_version
```

`returned_workflow_id` 来自注册输出。查 Module 时改用 `--kind module` 和实际 Module ID。
未指定版本时加载最近成功注册的新定义；重复注册同一版本不把它重新提升为最新。要复验某次结果，
显式使用其版本。这个本地入口没有 active 选项或激活步骤，查询旧版本无需删除其他版本。

确认返回的身份、版本和 hash 与注册结果一致。注册成功但执行入口不相容时，保留注册完成状态并
说明测试未执行，不临时换 source。交给 evaluation 时提供实际 root、返回的 Workflow ID、需要固定的
版本以及本次输入准备入口；不要求携带环境绑定 Profile。
