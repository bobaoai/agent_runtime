---
id: axiom_t10_index_first_ai_for_gaps_2026
category: technical_decision
created: 2026-03-31
updated: 2026-03-31
---

# T10. Index 优先，AI 填补语义缺口

## 1. 核心公理

系统设计只保留两类有效模块：`AI judgment` 与 `index-driven deterministic execution`。已有标签、index、registry、manifest 或 schema 时，直接消费结构化事实；语义缺口、解释权衡与边界判断交给 AI。不要在中间再插入关键词匹配、手搓打分或伪 NLP heuristic 层。

## 2. 深度推演

### 2.1 结构化事实不应被重复猜测

当系统已经拥有标签、索引和注册表时，最可靠的做法是直接消费这些结构化事实。再次做关键词匹配或相似度猜测，只会把确定信息重新降级成不稳定判断，并引入额外维护成本。

### 2.2 AI 负责语义判断，不负责伪装成规则引擎

AI 最有价值的地方是解释、权衡、归纳、证据排序与写作判断。把语义缺口明确交给 AI，边界更清楚，系统也更容易复盘。AI 不该被降格成模糊规则引擎；deterministic 模块也不该伪装成轻量 NLP。

### 2.3 heuristic 中间层会污染架构

关键词规则、手写打分和临时 fallback 看似便宜，实际上最容易形成第三层脏边界。它既比不上真正的 index，也替代不了 AI judgment，还会拖延把稳定判断沉淀成 schema、tag 或 index 的工作。

## 3. 应用判定

### 何时使用

- 设计 selection、routing、assembly 或 package contract 时
- 判断一个模块应由 AI 还是 deterministic 代码负责时
- 发现系统想用关键词、正则或打分逻辑去近似语义判断时

### 如何实践

1. 先问：这里是否已经有可直接消费的标签、index、registry 或 manifest。
2. 如果有，走 deterministic path，不重复做语义猜测。
3. 如果没有，显式交给 AI judgment。
4. 若某个 AI 判断反复稳定出现，把它沉淀成 schema、tag 或 index，而不是加入 heuristic 中间层。

## 4. 相关公理

- **A03 从 IC 到管理者的心智转变**：管理者要决定哪层该知道什么、负责什么。
- **A12 AI 原生开发范式**：系统边界应为 AI 与 deterministic 模块分别提供清晰接口。
- **T04 数据优于观点**：已有结构化事实时，优先消费事实而不是重做推测。

## 5. 简化准则

设计一个判断链路前，先回答两件事：

1. 这里有没有现成的结构化事实可直接消费。
2. 如果没有，这是否应明确交给 AI，而不是加一层 heuristic。
