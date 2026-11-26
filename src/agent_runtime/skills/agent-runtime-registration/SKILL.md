---
name: agent-runtime-registration
description: 使用已安装的 Agent Runtime CLI，把已审 Reviewer 或普通 Module 的 source 文件夹、或已编译 bundle 注册到指定 root 的 .runtime，回读准确结果并写出供 Test Run 使用的定义选择文件；也用于查询已有注册或为已有版本写出选择。不编写 Reviewer、不调用模型、不选择执行配置。
metadata:
  skill_class: primary_agent_development
  primary_agent_entry_role: operator
  primary_agent_entry_subject: runtime_registration_candidate
  first_authority_ref: agent_runtime/design_contract/agent_runtime_01_module_contract_and_assembly.md
  runtime_distribution: agent-runtime-core
---

# Agent Runtime 注册

## 1. Task Summary

将已审的 Reviewer 或普通 Module 的 source 文件夹，或已编译的 Module、Workflow bundle，通过
`agent-runtime-registry` 保存到调用者指定 root 的 `.runtime`，并回读准确结果。注册命令同时写出一份
定义选择文件（selection），交给 `agent-runtime-test-run --selection` 运行刚注册的准确版本。已有定义只需
查询或运行新材料时，直接使用已有注册，不重新编译；需要运行某个已有版本时用 `select` 写出它的选择文件。
注册与模型选择分开，Reviewer 默认能力由 Runtime 解析，普通 Module 的运行要求由 source owner 选定的预设给出。

“注册 Reviewer”“注册 Module/Workflow”“更新已审定义版本”“查询已有注册”及“为已有版本准备运行选择”
进入本 Skill。现有命令在操作前共用轻量 setup：首次或缺项时准备 `.runtime` 并接入三份随包操作 Skill，
已就绪时不重复写入。无需另执行 Skill 安装命令；具体对象检查仍由注册或查询操作负责。

## 2. Intended Outcomes

1. **注册到正确位置并可回读。** 交付的定义保存在请求指定 root 的 `.runtime`，回读得到的身份、版本和 release hash
   与注册结果一致（CLI 输出 `readback=verified`）。注册结果可以直接被后续 Test Run 或宿主入口使用，无需猜测名称
   或重新编译。
2. **注册内容就是已审内容。** 保存的任务指令、schema、owner 和运行要求与 source owner 批准的版本一致；
   Skill 与 Module 身份由命令从所选 source 读取，不由操作者另行输入。已保存定义保持其保存的工具与网络要求，
   不按版本标签或当前默认重新推断，同版本不同内容或不同运行要求不会被静默覆盖。
3. **交接由代码写出的选择文件承载。** 交给后续测试的是实际 root、选择文件位置及其所指对象；选择文件由命令写出，
   记录准确的 Workflow 与 Module 身份、版本、ref 和 hash。后续调用直接使用该文件，不需要任何人从报告中抄写
   ID、ref、hash 或拼接 `.runtime` 路径；之后在同一 root 注册的新版本不会改变它所指的定义。注册事实与 PG 写入、
   模型执行或审核通过分开表述。
4. **失败可恢复。** 写入失败或响应丢失时，交付回读后的实际状态和原始错误，使请求方能决定是否重复同一请求。
5. **查询得到请求的准确定义。** 只查询已有注册时，交付请求目标的实际 release 身份、版本、hash 与固定依赖；
   请求指定版本时与该版本一致，未指定时如实报告加载到的最新版本。为已有版本写选择文件时，选择的正是请求的
   准确版本。查询与选择都不触发注册，也不改变任何已保存定义。

## 3. Boundaries

只有审核想法、尚无已审 source 时，先交 source owner 完成定义与审核。修改任务指令、owner 或
输入输出 schema 属于 source 更新，不在注册途中补写。当前注册入口消费 `runtime_module_registration_v4`
任务 source。v2/v3 或其他不相容来源交回 source owner 明确迁移；操作者不靠删字段、补 transport 或改版本使注册通过。

普通 Module 的运行要求由 source owner 选定（常用模式的预设见 `agent-runtime-usage`）；`ModuleReviewer`
使用 Runtime 固定的 Reviewer 预设。注册操作者不改这些要求。

请求运行已注册对象时，使用 `agent-runtime-test-run` 完成其支持的普通自测；需要持久执行、
PG 注册或其他宿主专用操作时，按同版本 runbook 交给已有宿主入口，不将这些配置变成普通本地注册前提。
CLI 或必要资料缺失时说明具体缺件及提供方，保留已完成事实。

| 边界 | 可观察的越界 |
| --- | --- |
| CLI 负责注册与保存 | 手写或修补 .runtime JSON 或选择文件，复制测试 builder 代替正式入口 |
| 机器字段由命令读取和写出 | 从目录名推断后手输 Skill/Module ID，自拟发布包标签，或把输出中的 ref/hash 抄进下一条命令 |
| 已审定义与模型选择分开 | 为注册设置模型或环境 Profile，因换模型而重注册未变 source |
| 本地保存与其他宿主操作分开 | 自动连接 PG、建表、切换日常配置或在注册后调用模型 |
| 注册事实与使用结果分开 | 只见文件存在就声称回读成功，或把注册成功说成审核通过 |
| source owner 保留内容决定 | 注册途中修改任务、schema 或旧版字段，或向 v4 source 塞入工具、transport、Policy 参数 |
| 已保存定义不变 | 为同一版本写入不同内容或运行要求，或按新预设改写旧定义的工具与网络要求 |

## 4. Inputs and Completion

### 4.1 Inputs

- 调用者明确的宿主 root，以及在该位置注册的授权。root 决定 `.runtime` 的位置，不决定模型权限。
- Reviewer 或普通 Module 注册需要 source owner 交付的 source 文件夹
  （`.claude/skills/<skill>/runtime_modules/<module>`，或其中的 `module_registration.json`）、批准的定义版本及
  对应 source 审核依据。普通 Module 另需 owner 选定的运行要求预设；`mcp_data` 预设还需宿主连接名称。
- source 中的 `prompt.md` 是 source owner 从 Skill 提取的一项独立任务的完整执行 prompt。写法与示例见同版本
  runbook 的“从 Skill 提取一项独立任务的执行 prompt”（anchor `skill-task-base`）。注册操作者不编写、改写或
  批准它；缺少或不完整时交回 source owner。
- 需要特定 Workflow 名称时，提供已批准的名称；未提供时 Runtime 生成与 Module 同名、同版本的单节点 Workflow。
- 其他 Module 或 Workflow 使用 source owner 提供的完整已编译 bundle JSON。发布包标签只被校验、不被保存。
  bundle 恰有一个 Workflow（不论含几个 Module），或没有 Workflow 且只有一个 Module 时可以省略，由命令从该
  Workflow 或 Module 读取；含多个 Workflow，或没有 Workflow 而有多个 Module 时，由发布负责人给出 plugin ID 与版本。
  仅有 graph 构想或零散记录不足以使用 bundle 注册命令。
- 需要交给 Test Run 时，提供一个尚不存在、所在文件夹已存在的选择文件位置。
- 查询只需目标 root、对象种类和 ID；版本可指定，也可按请求加载最新定义。为已有版本写选择需要 Workflow ID 与准确
  版本；定义在 PostgreSQL 时，另需宿主已在 `root/.runtime/config.json` 的 `release_stores` 中配置的 store 名称。

任务含义与 source 由其 owner 负责。Registry 的准确合同、默认行为及错误以当前安装的 Runtime
和随包说明为准，定位方法见 §5.1。显式 source 依赖由 loader 校验；缺失或不相容时返回原错误，
不删声明、改版本或手拼默认策略来绕过。

### 4.2 Output

返回原生 CLI 结果及实际 root、对象种类、ID、版本和 release hash。`register-reviewer`、`register-module` 的结果
含 `readback=verified`、保存路径、完整 Module 与 Workflow records，以及 `selection`（给出 `--selection-out` 时还有
`selection_path`）。`register` 返回对象引用、发布包标签、保存路径和回读结论；bundle 恰有一个单节点 Workflow 时
同样给出 `selection`，否则 `selection_unavailable` 说明原因。bundle 中有多个单节点 Workflow 时，请求方指明其中
要测试的一个，再用 `select` 写出它的选择文件；bundle 只有 Module 或只有多节点 Workflow 时，没有普通 Test Run 可用的
对象，注册结果照常交付，并说明 Test Run 交接尚不成立。交给 Test Run 的是选择文件的位置，不是从输出中摘抄的名称或 hash。

Module 保存在 `.runtime/module/<module_id>/<version>.json`，Workflow 保存在
`.runtime/workflow/<workflow_id>/<version>.json`；单节点 Workflow 也在 workflow 中。
文件路径使用 CLI 返回值，格式细节由 Runtime 处理。查询结果包含 `release` 与固定依赖 `bundle`。
这些是本地注册事实，不代表 PG 写入、模型已执行或材料通过审核。

### 4.3 Completion Contract

本 Skill 有三种请求，各自按下列条件判断完成：

- **注册请求**：注册命令成功、输出 `readback=verified`，且请求交给 Test Run 时选择文件已写出、其中的对象就是本次
  注册的 Workflow 时完成。注册成功但执行入口不相容时，保留注册完成状态并说明测试未执行，不临时换 source。
  bundle 不含单节点 Workflow 时，注册本身已完成；所需的单节点 Workflow 或其他执行入口交定义负责人或宿主入口负责人，
  不临时拼图或改写 bundle。
- **仅查询已有注册**：按请求的 root、对象种类、ID 和版本要求执行 `load`，返回的 `release` 种类与 ID 等于请求目标，
  请求指定版本时返回的版本等于该版本，并取得 release hash 与固定依赖 `bundle` 时完成；未指定版本时报告实际
  加载到的版本与 hash。目标或指定版本不存在时，报告原生错误与已检查的目标，交请求方决定是否另行注册，
  不为满足查询而注册或改版本。
- **为已有版本准备运行选择**：`select` 成功并写出选择文件，所选 Workflow 与版本等于请求时完成；版本不存在、
  store 未配置或其 DSN 环境变量缺失时报告原错误，交宿主或请求方补齐。

失败时保留退出码、stdout、stderr 和原生错误。写入失败或响应丢失不证明没有保存内容；
先向同一 root 回读准确对象，确认实际结果后再决定是否重复原请求，不随机换版本。若 Module 已保存而
Workflow 保存失败，回读后可重试同一请求补齐，不手工拼写缺失文件。选择文件位置已被占用时命令在注册前
以用法错误退出；另选新位置重试，不覆盖已有文件。

## 5. Method

### 5.1 找到当前安装的入口与说明

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

日常新注册读 runbook 的“机器字段的来源与交接”、§0.1 和 API reference 中的 CLI commands；已有 Reviewer 因新执行编码
需要升级时，转到同一安装版本 runbook 的“升级已有 Reviewer”（anchor `upgrade-existing-reviewer`）。底层兼容示例不需要
逐个执行。默认参数、退出码与错误处理查同版本 help 和自动接口说明，不另维护一套配置表。
CLI、文档或构建身份不匹配时返回安装维护者，不回落到 sibling checkout。setup 的具体效果查自动手册
中的 setup_runtime；本地 Skill 冲突按原错误交其 owner 处理，不手工覆盖。向共享目录写入新格式前，
按同版本 runbook 的兼容说明核对受影响读取者；相同 dev 版本字符串不足以证明兼容。

### 5.2 注册准确 source 或 bundle

以下均为示例路径，执行时换成请求提供的 source 文件夹、版本与文件位置：

```sh
agent-runtime-registry register-reviewer --root /path/to/host \
  --source /path/to/project/.claude/skills/approved-skill/runtime_modules/approved_reviewer \
  --version approved_version --selection-out /path/to/approved_reviewer.selection.json
```

`--source` 可以是 Module 文件夹或其中的 `module_registration.json`。命令从该声明读取 Skill 与 Module 身份，
并核对文件夹确实位于 `<project>/.claude/skills/<skill>/runtime_modules/<module>`；位置与声明不符时拒绝，
不扫描相邻项目或其他 Module。source loader 再读取同一文件夹的 prompt、schemas 及其声明依赖。这个作者目录
与当前 Agent 从哪里发现本操作 Skill 是两件事。缺少完整 source 时由提供方补齐，不为注册手工安装或迁移 Skill。
已在程序中持有准确身份的调用者仍可用低层的 `--source-root`、`--skill-id`、`--module-id`。

普通 Module 用同样的方式注册，并给出 owner 选定的预设：

```sh
agent-runtime-registry register-module --root /path/to/host \
  --source /path/to/project/.claude/skills/approved-skill/runtime_modules/approved_task \
  --preset code_change --version approved_version --selection-out /path/to/approved_task.selection.json
```

`mcp_data` 预设用可重复的 `--mcp-connection NAME` 声明宿主连接；`--max-attempts` 默认 1。

v4 source 声明任务身份、owner 路径和输入输出 schema 的引用及路径，prompt 来自同一目录。
它不携带模型、transport、工具、技术 Policy 或 Profile 参数；这些由 Runtime 的定义和执行接口
各自处理。准确字段由 loader 和同版本文档定义，注册操作者不重写。

命令编译 Module 及单节点 Workflow、保存并回读。默认图与 Module 同名、同版本；需要已批准的其他图名时增加
`--workflow-id explicit_workflow_id`。不使用旧 `_review` 后缀猜当前图名，既有图也不会因新注册被自动重命名或删除。
注册无需 Provider CLI、登录、PG 或预选模型；执行相容性由后续 Runtime 准备阶段检查。

重复同版本注册会核对任务 source（普通 Module 还核对运行要求）并复用原定义与依赖，不重新应用当前默认，
重复写出的选择与第一次相同。真实定义变化需要 source owner 批准新版本；同版本不同内容的冲突不能靠临时换版本回避。

已编译的其他 Module 或 Workflow 使用：

```sh
agent-runtime-registry register --root /path/to/host --bundle /path/to/approved_bundle.json \
  --selection-out /path/to/approved_bundle.selection.json
```

bundle 含多个 Workflow，或没有 Workflow 而有多个 Module 时（与 §4.1 的条件相同），增加发布负责人给出的
`--plugin-id` 与 `--plugin-version`。`--selection-out` 只在 bundle 恰有一个单节点 Workflow 时使用；其他 bundle 省略它。
注册后，只有请求方指明的单节点 Workflow 才用 `select` 写出选择文件；只有 Module 或多节点 Workflow 的 bundle 按 §4.3 交接。保留 bundle 的完整依赖与原记录，不在操作过程中拼图、改内容或发明发布包标签。

### 5.3 回读、选择与交接

```sh
agent-runtime-registry load --root /path/to/host --kind workflow \
  --id registered_workflow_id --version approved_version
agent-runtime-registry select --root /path/to/host --workflow registered_workflow_id \
  --version approved_version --selection-out /path/to/registered_workflow.selection.json
```

`load` 只回读；查 Module 时改用 `--kind module`。未指定版本时加载最近成功注册的新定义；重复注册同一版本
不把它重新提升为最新。要复验某次结果，显式使用其版本。这个本地入口没有 active 选项或激活步骤，查询旧版本
无需删除其他版本。已有选择文件时，`agent-runtime-registry load --root /path/to/host --selection FILE` 直接返回
该文件所指的 Workflow、Module 及 `input_schema`、`output_schema`，供输入准备使用。`select` 要求准确版本，只为已有
单节点 Workflow 写选择文件；注册命令已写出选择时不需要再执行它。
定义保存在 PostgreSQL 时，用 `--release-store NAME` 选择宿主已配置的 store；选择文件只记录该名称，DSN 留在
配置所指的环境变量里，每次运行按当前配置打开 store 并读取准确 ref/hash。

确认 `readback=verified` 后交给测试：实际 root、选择文件位置，以及本次输入准备入口。选择文件属于创建它的 root，
在另一 root 上使用会被 Test Run 拒绝；不要编辑其中内容，需要其他版本时重新注册或 `select`。不要求携带环境绑定 Profile。
