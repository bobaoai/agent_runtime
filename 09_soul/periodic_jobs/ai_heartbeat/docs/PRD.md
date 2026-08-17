# AI Heartbeat: 渐进式披露记忆系统产品需求文档 (PRD)

## 1. 产品概述

### 1.1 愿景
构建一个**Agentic 驱动的、全局统一但按需披露的观测记忆系统**。彻底摆脱由外部脚本“拼凑 Prompt 并喂给 AI”的低级模式，转而让 AI 引擎（OpenCode-Builder）在接收到简单的“路径与目标”后，自主探索文件系统、分配子任务并提纯观测结果。系统遵循 **Progressive Disclosure** 理念：记忆池是全局的，但 Agent 接收到的上下文始终保持稀疏（Sparse）和高密度（High Density）。

### 1.2 核心价值主张
- **Agentic 自主探索**: 脚本只负责触发任务和提供线索（文件路径），AI 负责阅读、过滤（如排除仅格式变动的 Blog）和总结。
- **渐进式披露 (Progressive Disclosure)**: 默认不加载详细记忆，仅由 Agent 根据当前任务逻辑主动检索相关的 L1/L2 观测点。
- **全局分层架构**: 
  - **L3**: 全局硬性约束（存放在 `rules/`，全局被动加载）。
  - **L1/L2**: 动态观测日志（存放在全局记忆池，Agent 主动检索）。
- **抗噪设计**: 利用 AI 的语义理解能力识别真正的“新内容”。例如，针对 300+ 篇 Blog 的格式变动，AI 应通过检查元数据（Metadata）中的创建日期来识别真正的新文章。

### 1.3 目标用户
- **OpenCode-Builder**: 作为记忆的生产者和核心消费者。
- **开发者**: 仅作为系统边界的定义者和记忆日志的最终审计者。

---

## 2. 核心设计原则 (The Agentic Way)

### 2.1 拒绝 Push 模式，拥抱 Pull 模式
传统的系统试图把所有 Context “推送”给模型。本系统要求 Agent 具备“拉取”意识。脚本告诉 Agent：“这些文件变了，去把有价值的 lessons 学回来”，Agent 应该自己决定读什么、读多少。

### 2.2 记忆稀疏性假设 (Sparse Context Assumption)
我们假设：对于任何给定任务，真正相关的记忆是极少数的。因此，全局记忆池（OBSERVATIONS.md）允许不断增长，但 Agent 必须能够通过标签（Tags）或关键字进行高效的局部加载。

### 2.3 零摩擦资产化
记忆日志采用纯文本追加模式。它不仅是 Agent 的运行状态机，也是开发者的知识资产。

---

## 3. 功能需求：三层分级体系

### 3.1 L3: 全局约束与哲学 (Global Constraints)
- **内容**: 存放于 `rules/SOUL.md` 和 `rules/USER.md`。
- **硬性约束**: 必须包含语言风格约束（不准用大词、不准用营销词、不准用引号、尽量避免 "not" 负向句式）、应对策略等。
- **加载方式**: Session 启动时被动全局加载。

### 3.2 L1: 每日观测与心跳 (Daily Observation)
- **内容**: 过去 24 小时的关键事件、技术决策、真实的错误修复经验。
- **打标格式**: `🔴 High (方法论/约束)`、`🟡 Medium (项目状态/决策)`、`🟢 Low (任务流水)`。
- **产生方式**: 脚本仅提供 `find` 命令找出的文件路径集合，交给 OpenCode-Builder。Agent 自主处理（包括调用 Sub-agent 读文件、检查 Metadata）。

### 3.3 L2: 记忆蒸馏与反思 (Weekly Reflection)
- **职责**: 垃圾回收。
- **逻辑**: 每周运行，AI 自主读取 L1 记忆池，删除过期的 🟢，合并同主题的 🟡，将共性经验晋升为 🔴。

---

## 4. 关键业务流 (User Story)

### 4.1 智能体自发的心跳任务
1. **触发**: 系统 Cron Job 触发脚本。
2. **输入**: 脚本执行 `find -mtime -1`，获得一个长路径列表（可能包含 300+ 篇变动的 Blog）。
3. **分配**: 脚本启动一个 OpenCode-Builder Session。
4. **指令**: “这是过去 24 小时变动的文件列表。你的目标是生成观测记录。注意：对于 blog/ 目录下的文章，请检查其 Metadata 中的 Date 字段，仅处理真正今天创作的内容。如果是格式重排，请忽略。”
5. **执行**: Agent 看到任务后，自主启动 sub-agents (librarian/explore) 分头读取文件，最后汇总输出。
6. **产出**: 结果 Append 到全局 `contexts/memory/OBSERVATIONS.md`。

---

## 5. 技术约束与集成

- **执行引擎**: 本地 OpenCode Server (localhost:<your-port>)。
- **核心模型**: `<your-model>`。
- **Agent Identity**: `<your-agent>`。
- **记忆存储**: Markdown 文件（支持 Git 版本控制）。
