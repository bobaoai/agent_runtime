# Examples — 子体管理与参考实现

## 这层是干什么的

`examples/` 承担两件事：

1. **真实项目的精简版参考实现** — templates 填完 placeholder 后实际落出来的样子，给新项目看「装完大概是这样」
2. **子体注册表 + 双向 message 通道** — 母体（本仓库）追踪它管的所有子体的位置、关系、同步状态、双向通告

母体跨机器一致；子体在每台机器的物理路径在 [`.local_paths.json`](.local_paths.json)（gitignored，从 `.local_paths.json.template` 衍生）。

## 跟 handoff / templates 的边界

- [`handoff/`](../handoff/) 答**怎么做**：installation_guide / distillation_protocol / add_new_agent_projection（待补）。这是协议与 SOP
- [`templates/`](../templates/)（待建）答**填什么模板**：placeholder 化的骨架
- `examples/`（本目录）答**有哪些子体、它们说了什么、母体回了什么** — 真实状态 + 双向通告

handoff/ 是动作，templates/ 是骨架，examples/ 是实例 + 通道。

## 目录结构

```
examples/
├── README.md                          ← 本文件
├── MESSAGE_FORMAT.md                  ← 双向 message 的 frontmatter schema + 状态机 + 命名约定
├── .local_paths.json.template         ← 本机路径骨架（committed）
├── .local_paths.json                  ← 本机实际路径（gitignored）
└── <project>/                         ← 每个子体一个子目录
    ├── registry.md                    ← 子体注册卡（关系定义 / 继承 baseline / 已知 drift）
    ├── claude_management_layer.md     ← 精简版参考实现（结构 + portability 决策）
    ├── PROJECT_ADAPTER_<project>.md   ← adapter 范例
    ├── inbox/                         ← 子体 → 母体反馈
    │   └── <YYYYMMDD>_<topic>.md
    └── outbox/                        ← 母体 → 子体通告
        └── <YYYYMMDD>_<topic>.md
```

## 当前注册的子体

| 项目 | Agent | Dogfood 状态 | Registry |
|---|---|---|---|
| trading_platform | Claude Code | active | [trading_platform/registry.md](trading_platform/registry.md) |

新增子体的步骤：

1. 在 host repo 完成 [`installation_guide.md`](../handoff/installation_guide.md) Phase 1-5（至少）
2. 在母体本目录建 `<project>/` 子目录 + `registry.md`（参照 trading_platform 的 frontmatter schema）
3. 在 `.local_paths.json` 加该子体的本机路径
4. 建空的 `inbox/` + `outbox/` 子目录 + `.gitkeep`

## 内容边界

每个 example 子目录**应**包含：

- **结构性产物**：management layer doc / routing 设计 / R-rule 分层
- **portability 决策的说明**：哪些是 portable / 哪些 project-specific / 为什么
- **registry**：关系定义、继承的 baseline、已知 drift、同步建议
- **inbox / outbox**：双向 message 历史

每个 example 子目录**不应**包含：

- 项目业务细节（交易策略 / 客户名单 / 内部数据）
- 完整 entry doc / skill 集合（这些是项目自己的 runtime，不是参考材料）
- 商业敏感信息

如果某个 example 不可避免要 reference 业务上下文（如「某 skill 处理市场数据」），用脱敏描述即可，不放原文。

## 双向 Message 通道

详细规格见 [`MESSAGE_FORMAT.md`](MESSAGE_FORMAT.md)。要点：

- **outbox/**（母→子）：母体写完 commit 进母体 repo；子体下次 pull 母体后人工搬运到子体内 `09_<agent>/handoff/from_upstream/`
- **inbox/**（子→母）：子体写在自己 repo 的 `09_<agent>/handoff/to_upstream/`，commit；之后人工搬运进母体本目录 inbox/，母体侧 commit + 处理
- 每条 message 带 frontmatter（direction / topic / from_commit / status / requires_action / ...）
- Status 状态机：`pending → acknowledged → applied | rejected | superseded`

不实时同步、不走 git submodule。每个 repo 各自 commit 自己的视图，git 不冲突。

## 维护承诺

- **母体跨机器一致**：本目录所有 committed 内容在每台机器都一样。机器特定路径只在 `.local_paths.json`
- **子体通过母体跟 git 连接**：母体记录子体的 git remote URL；具体物理路径由本机 `.local_paths.json` 解析
- **registry 是 SoT**：子体当前的 dogfood 状态、继承的 baseline、已知 drift 都在 `<project>/registry.md` 里维护，不在 chat / commit message 里散落
- **inbox/outbox 不删旧 message**：用 `status` 字段做生命周期管理，按时间堆积

## 跟 templates / handoff 的关系（升级版）

- **templates**（待建）：抽象骨架，placeholder 化
- **examples**：真实填完的样子 + 子体注册 + 双向通告
- **handoff/distillation_protocol**：从 examples 反向蒸馏出 templates 的 SOP；蒸馏产生的"建议子体同步"消息落到对应 `examples/<project>/outbox/`

新项目安装路径：先读 handoff/installation_guide → 用 templates → 出现疑问时翻 examples → 看相邻项目实际怎么做的 → 装完后在 examples/ 加 registry。
