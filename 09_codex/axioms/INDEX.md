# Axioms 索引

> **公理（Axioms）** 是用户的核心认知滤镜——从个人经历中提炼的深层哲学原则。
>
> 不同于 Skills（执行层，"怎么做"），Axioms 是思考层（"为什么"），用于启发深度思考和提供 Alternative 视角。

---

## 如何使用

### 快速浏览（INDEX）
**何时看索引**：
- 开始一个涉及战略决策或架构设计的任务时
- 想了解用户的"认知底色"时
- 需要找到与当前任务相关的公理类别时

### 深度阅读（详细文件）
**何时看详细**：
- 任务涉及用户的核心价值观或方法论时
- 需要理解某个公理的"深度推演"和"应用判定"时
- 用户明确要求参考其个人哲学时
- 在做反思层工作（沉淀公理、深度复盘）时

### 最高优先级（First Principles）

- **FP**：[第一性原理](FP_first_principles.md) — 7 条 work-judgment 准则，优先于所有其他 axiom 与工作层契约。Philosophy 层，跨项目跨 agent 稳定。每会话每任务**先用 FP，再考虑其他 axiom 或 rule**。

### 核心 Axiom 群

这些公理形成有机整体，常被一起调用：

- **AI 管理三角**：A02(放大器) + A03(IC→Manager) + A04(可靠性) — 所有 AI 协作的基础心智
- **认知资产组**：T05(认知是资产) + A02 + A03 — 代码成本坍塌后的价值重定位
- **验证闭环**：V02(可验证性) + T07(隔离-处理-验证) + V04(时间锚定) — 构建可信任的 AI 系统

### 主动检索触发

当用户提到以下关键词时，主动查阅相关 axiom：

- 「管理 AI」「委派」「delegation」→ A03, A04
- 「框架选择」「技术选型」「lock-in」→ A06, T08
- 「验证」「信任」「可靠性」→ V 系列, A04
- 「优先级」「效率」「瓶颈」→ M03, X03
- 「上下文」「记忆」「文档」→ A05, T03
- 「减仓」「暴露」「调仓」「建仓」「胜率」「回报比」→ P01
- 「问题 framing」「隐藏假设」「真正目标」→ A16, A08
- 「可靠性」「链条失效」「同步失败」「五面墙」→ A17
- 「验证成本」「审校预算」「verification budget」→ A18
- 「概率面」「确定性面」「probabilistic surface」→ A19
- 「读者」「persona」「服务谁」「reader」→ A20
- 「Skill」「Agent」「Workflow」「Tool」「假 Agent」「能力边界」→ A21

---

## A. AI/Agentic 领域 (21条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| A01 | [问答到执行的范式转移](a01_ask_do_paradigm.md) | AI 的价值在于端到端交付最终产物 |
| A02 | [AI 是放大器而非替代品](a02_multiplier_not_replacement.md) | AI 放大的是使用者的能力 |
| A03 | [从 IC 到经理的心智转变](a03_ic_to_manager.md) | 有效使用 AI 需要管理思维 |
| A04 | [可靠性是管理问题](a04_reliability_management.md) | AI 不可靠是因为被当作工具而非团队成员 |
| A05 | [文档即长期记忆](a05_docs_long_term_memory.md) | 上下文窗口限制是记忆问题 |
| A06 | [框架选择即世界观锁定](a06_framework_worldview.md) | 选框架是对未来适应性的哲学押注 |
| A07 | [设计哲学决定能力上限](a07_design_philosophy_ceiling.md) | 规划驱动 vs 任务驱动各有最佳场景 |
| A08 | [提示质量是主要杠杆](a08_prompt_quality_lever.md) | 代码质量取决于文档质量 |
| A09 | [构建者思维是护城河](a09_builders_mindset_moat.md) | 主动构建 > 被动使用 |
| A10 | [熟悉度胜过原始智能](a10_rapport_over_raw_intelligence.md) | 竞争优势来自积累的上下文 |
| A11 | [工具组合即能力扩展](a11_tool_composition_as_capability_expansion.md) | 工具被组合时能力非线性扩展 |
| A12 | [AI 原生开发范式](a12_ai_native_development_paradigm.md) | 软件应优先提供 AI 可消费的接口 |
| A13 | [技术采用三阶段](a13_tech_adoption_stages.md) | 电动机平行：Driver→Co-pilot→Architect |
| A14 | [Prompt 边界卫生](a14_prompt_boundary_hygiene.md) | 下游 prompt 只保留 task-plane 信息 |
| A15 | [通路层诊断优先](a15_path_layer_diagnosis.md) | 诊断先回到通路层，再判断错误为何出现与是否会复发 |
| A16 | [先揭示隐藏假设，再回答更好的问题](a16_hidden_assumption_better_question.md) | 先识别限制性假设，再用更高价值的 framing 回答真实目标 |
| A17 | [五面墙：可靠性链条同步失效](a17_five_walls_of_agent_reliability.md) | Agent 可靠性由感知、记忆、推理、执行、验证五面墙共同决定，最短板定上限 |
| A18 | [验证成本守恒](a18_verification_cost_conservation.md) | 生成省下的成本会转移为验证成本；设计时给验证留预算 |
| A19 | [概率性面积最小化](a19_minimize_probabilistic_surface.md) | 能确定性判定的面不留给概率判断；概率面越小系统越可控 |
| A20 | [读者 Persona 优先](a20_reader_persona_primacy.md) | 任何 doc/skill 在动笔前显性声明读者 persona；六 persona 集合是 repo 全局固定 |
| A21 | [Skill / Agent 边界不可混淆](a21_skill_agent_boundary.md) | Tool / Skill / Workflow / Agent 四层分离；Skill 是能力单元，Agent 是带目标、状态和权限的执行主体 |

---

## T. 技术决策 (11条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| T01 | [基础设施优于组件](t01_infrastructure_over_components.md) | 瓶颈在基础系统，不在单个工具 |
| T02 | [结果确定性优于过程确定性](t02_results_certainty.md) | 验证输出，不强制流程 |
| T03 | [上下文隔离是多 Agent 价值](t03_context_isolation.md) | 隔离信息域，不模仿组织结构 |
| T04 | [数据优于观点](t04_data_over_opinion.md) | 有数据时用数据决策 |
| T05 | [认知是资产，代码是消耗品](t05_cognition_asset.md) | 代码生成成本趋零时，理解是核心 |
| T06 | [依赖拓扑优于任务数量](t06_dependency_topology.md) | 架构选择基于耦合度 |
| T07 | [隔离-处理-验证闭环](t07_isolation_processing_verification.md) | 收集 → 沙箱 → 验证 |
| T08 | [第一性原理方法论设计](t08_first_principles_methodology.md) | 先质疑框架是否解决问题 |
| T09 | [数据策略与MDP](t09_data_strategy_mdp.md) | 数据捕获本身就是第一阶段产品 |
| T10 | [Index 优先，AI 填补语义缺口](t10_index_first_ai_for_gaps.md) | 有结构化事实时直接消费，语义缺口交给 AI，不引入 heuristic 中间层 |
| T11 | [时间戳与日期字段必须显性声明语义](t11_timestamp_semantics_explicit.md) | 任何时间字段必须命名后缀带时区/语义，比较前必须验证两边语义匹配 |
| T11 | [时间戳与日期字段必须显性声明语义](t11_timestamp_semantics_explicit.md) | 任何时间字段必须命名后缀带时区/语义，比较前必须验证两边语义匹配 |

---

## M. 管理/工作哲学 (10条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| M01 | [闭环校准](m01_closed_loop_calibration.md) | 精通来自持续反馈，非完美计划 |
| M02 | [逆向调试心智](m02_reverse_debug_mindset.md) | 假设测试缩小可能性范围 |
| M03 | [量化优先级](m03_quantified_prioritization.md) | 聚焦 20% 驱动 80% 结果 |
| M04 | [主动管理优于工具心智](m04_active_management_over_tool_mentality.md) | 复杂系统需要管理，非即插即用 |
| M05 | [简单即认知效率](m05_simplicity_cognitive_efficiency.md) | 轻量系统减少认知开销 |
| M06 | [连接创造优于孤立知识](m06_connection_making.md) | 创新来自跨领域链接 |
| M07 | [多线程即可持续能量](m07_multi_threading_energy.md) | 平行兴趣创造韧性 |
| M08 | [自我系统化](m08_self_systematization.md) | 人+工具+日志+AI=混合有机体 |
| M09 | [AI 时代管理范式](m09_ai_era_management_paradigm.md) | 过程确定性→结果确定性，科学脑评估 |
| M10 | [教育流失阶梯模型](m10_education_retention_ladder.md) | 四道摩擦关卡与工程化消除 |
| M11 | [阶段门纪律](m11_phase_gate_discipline.md) | AI 注意力有限——完成标准前置、逐项处理、先审后析 |

---

## V. 信任与验证 (5条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| V01 | [责任无法被委派](v01_responsibility.md) | 执行可委派，问责不可 |
| V02 | [可验证性是信任的地基](v02_verifiability.md) | 设计可检测错误的架构 |
| V03 | [归因塑造感知](v03_attribution_shapes_perception.md) | 让隐形工作可见 |
| V04 | [时间锚定防止幻觉](v04_temporal_grounding_prevents_hallucination.md) | 验证时效性信息 |
| V05 | [信任归因双维度](v05_trust_attribution_dual_dimension.md) | 信任主体×信任域的产品设计 |

---

## X. 跨域隐喻 (6条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| X01 | [约束悖论](x01_constraint_paradox.md) | 更大系统引入更多问题 |
| X02 | [假设驱动的系统化调试](x02_systematic_debugging_hypothesis_testing.md) | 受控实验分割问题空间 |
| X03 | [效率由瓶颈决定](x03_efficiency_determined_by_bottlenecks.md) | 整体效率受限于最弱环节 |
| X04 | [为真实用例设计](x04_design_for_actual_use_case.md) | 适配约束 > 最高规格 |
| X05 | [精度在系统中级联](x05_precision_cascades_through_systems.md) | 小误差放大并级联传播 |
| X06 | [抓大放小系统分析](x06_big_picture_system_analysis.md) | 先找最大决策点，意外是最佳信号 |

---

## P. 交易/仓位哲学 (1条)

| ID | 公理 | 核心含义 |
|----|------|----------|
| P01 | [仓位决策是期望值分解决策](p01_position_decisions_expectancy.md) | 减仓防具体威胁、持有吃阶段期望值；胜率×回报比分桶差异化，禁全局一刀切 |

---

## 统计

| 领域 | 数量 |
|------|------|
| FP 第一性原理（最高优先级，独立系列） | 1 |
| AI/Agentic | 18 |
| 技术决策 | 11 |
| 管理/工作哲学 | 11 |
| 信任与验证 | 5 |
| 跨域隐喻 | 6 |
| 交易/仓位哲学 | 1 |
| **总计** | **53** |
