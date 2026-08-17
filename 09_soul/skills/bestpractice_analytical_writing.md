---
name: analytical-writing
type: prompt-module
description: 分析写作质量标准（因果链、alternative排除、judgment transfer、so-what、calibration、narrative arc、冷读概念顺序、分析语义保护）
modes:
  - thesis-writer
  - thesis-adversary
  - theme-report-writer
---

# 分析写作质量标准

## 元数据

- **类型**: BestPractice / Prompt Module
- **适用场景**: 写 thesis section prose、theme report 正文、evidence synthesis、任何需要从事实推导到判断的分析性文字
- **创建日期**: 2026-06-20
- **来源**: thesis / report pipeline 多轮实战。pipeline 产出结构正确、风格合规，但分析浅、洞见弱。根因是现有 bestpractice 体系只防坏（style / structure / editorial meta），没有定义好的分析内容长什么样

---

## 核心原则

### 原则一：因果链必须完整

每段分析 prose 背后都有一条因果链。完整的因果链有四个环节：

```
观测 → 机制 → 效果 → 含义
```

- **观测**：发生了什么（数据、事件、政策行为）
- **机制**：为什么会产生影响（传导路径、约束条件、反馈回路）
- **效果**：影响了什么（价格、概率、市场结构、政策空间）
- **含义**：对判断意味着什么（thesis 方向变化、scenario 距离调整、what to watch）

缺任何一环就是质量缺陷：

| 缺环 | 表现 | 例子 |
|---|---|---|
| 缺机制 | 观测直接跳到效果，读者不知道 why | "CPI 超预期，因此加息概率上升"（传导路径是什么？哪个分项驱动？persistent 还是 transient？） |
| 缺效果 | 有机制但没说影响了什么 | "Fed 的 look-through 框架允许忽略一次性冲击"（所以呢？对 rate path 的具体影响是什么？） |
| 缺含义 | 有效果但没到 actionable 判断 | "长端利率上升 30bp"（对 thesis 的判断方向变了吗？是 promote 还是 neutral？为什么？） |
| 缺观测 | 纯推理没有事实锚点 | "如果 Fed 转向 easing，风险资产将反弹"（什么证据暗示 Fed 会转向？） |

### 原则二：必须排除 alternative explanation

单因解释几乎总是不够的。一个 observation 通常可以被多条机制解释。好的分析 prose 不仅说"我认为是 A"，还要说"为什么不是 B 或 C"。

操作方式：

1. 陈述主判断（"这条 evidence promote 了 thesis，因为 mechanism A"）
2. 点出至少一个 alternative（"表面上也可以用 mechanism B 解释"）
3. 说明为什么 A 优于 B（"但 B 需要条件 X 成立，而 X 目前不成立，因为..."）

不需要穷举所有 alternative。需要覆盖的是：读者最可能产生的 alternative reading。如果一个 alternative 足够强到你自己也犹豫，写出来，标明 uncertainty。

检测式 boundary：如果一段 prose 只有"A 导致了 B"而没有"为什么不是 C 导致了 B"，读者无法判断作者是否考虑过 alternative，判断可信度打折。

### 原则三：转移判断能力，不要列出结论

fact listing："Fed 维持 forced pause，概率 65%。"

judgment transfer："Fed 维持 forced pause 的三个条件：(1) 通胀 sticky 但不加速，(2) 就业降温但不坍塌，(3) 没有外部冲击打破观望空间。目前三个条件都成立。65% 的 base case 建立在条件 (3) 最脆弱的判断上：如果贸易冲击再升级，forced pause 的政治可行性会被压缩，概率降到 40%。"

区别在于：读者读完第一种，只知道结论。读者读完第二种，知道结论成立的条件、哪个条件最脆弱、什么情况下结论会变。读者获得了 reproduce 作者推理的能力。

judgment transfer 的三个要素：

1. **条件显性化**：判断成立的前提是什么
2. **脆弱点标注**：哪个条件最容易被打破
3. **翻转条件**：什么情况下判断方向会变

### 原则四：每段分析必须到"so what"

观测和机制是中间产品。分析的终点是 actionable 判断。

"So what" 有三种合法终点：

1. **方向判断**：这条因果链让某个 thesis 更可信还是更不可信（promote / downgrade / neutral + 理由）
2. **监测指标**：接下来要盯什么来判断这条链的走向（what to watch）
3. **证伪条件**：什么情况下这条链会断（falsifier）

三种都可以作为终点，但至少要到一种。停在"observation + mechanism"层面而不到任何一种终点，分析就没有完成。

检测式 boundary：如果删掉一段 prose 后，reader 的行动选项没有改变（不知道该 watch 什么、不知道什么情况下判断会变），这段 prose 只是背景，不是分析。

### 原则五：不确定性必须 calibrated

两种不确定性标注方式：

**弱标注**：用 hedge 词（"可能"、"也许"、"存在风险"）。这些词告诉读者"作者不确定"，但不告诉读者"不确定到什么程度"、"不确定性来自哪里"、"什么条件能消除不确定性"。

**calibrated 标注**：标明置信来源和翻转条件。

```
弱：  "Fed 可能在 Q4 降息。"
强：  "Fed Q4 降息的 base case 概率 35%，低于市场定价（55%）。
      差距来自我们对 shelter 通胀的判断比市场更 sticky。
      如果 7-8 月 CPI shelter 连续两月环比 < 0.3%，概率升到 50%。"
```

强标注有三个成分：(1) 概率 + 锚定比较（vs 市场、vs 上次判断），(2) 分歧来源（什么分析前提让我跟 consensus 不一样），(3) 更新条件（什么数据出来后我会修正）。

不是每个判断都需要完整的三成分。但如果一段 prose 里全是 hedge 词而没有任何 calibrated 标注，整段的信息量接近零。

### 原则六：report 需要 narrative arc

Theme report 不是 thesis 摘要的拼接。读者读一篇 report，期待的是一个完整的分析叙事：

1. **定位**（开篇）：现在这个 theme 里最重要的问题是什么。不是"theme 概述"，是"为什么你现在要读这篇"
2. **展开**（主体）：各 thesis 怎么相互作用。thesis 之间的张力、互补、共同依赖是叙事的核心，不是逐条 thesis 总结
3. **收敛**（结尾）：综合判断 + what to watch。读者读完后应该比读前更能判断接下来要关注什么

拼接式 report 的检测信号：每个 section 可以独立成段，删掉任何一段不影响其他段的理解。如果是这样，这篇 report 缺少 narrative 线索（thesis 之间的联系没被写出来）。

### 原则七：按读者的理解依赖引入概念

分析深度不能靠术语密度制造。正文应先建立可观察事实、正在解释的问题或与基准的差异，再引入机制名称、框架标签和抽象分类。需要长期复用的术语应在读者已经知道它解释什么、改变什么之后命名。

正式定义可以因精确性需要先出现，但必须在首次出现时同时给出通俗角色或分析用途。读者不应被迫暂存多个尚未用于推理的名称。

每个解释性段落都应让冷读者回答：发生了什么，为什么，意味着什么。原则一检查因果链有没有缺环；本原则检查这些环节是否按读者能够吸收的顺序出现，以及相邻段落是否形成连续推理。

### 原则八：润色不能改变分析状态

分析性 prose 的数字、事实归属、因果方向、不确定性、场景条件、证伪条件和置信边界是受保护语义。Writer 可以改善清晰度，Reviewer 可以指出问题，但单纯润色不能把：

- 可能性改成确定性
- 相关性改成因果关系
- 条件场景改成 base case
- 监测项改成证伪条件
- source claim 改成作者已经确认的判断

如果改善 prose 必须改变其中任一项，任务已经从润色变成分析修订，应返回分析作者或 owning workflow，而不是由 Prose Reviewer 静默决定。

---

## 两类产物的侧重

### Thesis section prose

侧重原则 1-5。每个 section 是一条独立的因果链系统。

质量检查：

- 因果链四环节是否完整（观测 → 机制 → 效果 → 含义）
- 是否排除了读者最可能的 alternative reading
- promote / downgrade / neutral 判断是否附带了推理链，不只是标签
- 不确定性是否 calibrated（至少标明条件 + 翻转）
- evidence 引用是否支撑因果链中的关键环节，而不只是末尾堆 refs
- 概念是否按事实或问题、差异、影响、名称的理解依赖出现
- 润色是否保持数字、因果、不确定性和证伪条件不变

### Theme report

侧重原则 3、4、6。report 是 thesis 层之上的 synthesis。

质量检查：

- 开篇是否直接进入"现在最重要的问题是什么"（不是 theme 介绍、不是 pipeline 日志）
- thesis 之间的关系是否被显式写出（互补、张力、共同依赖）
- 综合判断是否超越了各 thesis 判断的简单加权（"因为 thesis A 和 thesis B 有共同依赖 Z，Z 的走向同时决定两条链的方向"）
- 结尾是否给了 what to watch（具体到指标、事件、时间窗口）
- 全篇是否有 narrative arc（读者读完后能复述"这篇 report 讲了什么"，而不只是"它提到了哪些 thesis"）
- 新术语是否在读者知道其分析用途之后出现，或在首次定义时获得通俗角色说明
- Reviewer 建议是否保持场景、置信边界和因果方向不变

---

## 常见陷阱

| 陷阱 | 表现 | 应对 |
|---|---|---|
| 事实堆砌当分析 | 列了 10 个数据点，没有一条因果链 | 每段必须有 observation → mechanism → effect → implication |
| 单因简化 | "CPI 高所以不降息"，没考虑 labor market / financial conditions / 政治周期 | 至少点出一个 alternative + 说明为什么主判断优于 alternative |
| 结论无条件 | "概率 65%"，没说建立在什么条件上 | 标明条件 + 脆弱点 + 翻转条件 |
| Thesis 拼接当 report | report 每段独立，读完不知道 thesis 之间什么关系 | 写 thesis 间的张力、互补、共同依赖 |
| Hedge 词替代 calibration | 通篇"可能""或许""存在风险"，没有任何概率锚定或更新条件 | 至少一处 calibrated 标注（概率 + 锚定 + 更新条件） |
| Mechanism 手一挥 | "传导路径清晰"但没写路径是什么 | 写出 A → B → C 的具体环节 |
| So-what 断在中途 | 写了 observation + mechanism 就停了，没到判断 | 每段到至少一个终点：方向判断 / 监测指标 / 证伪条件 |
| 背景太重判断太轻 | 前 70% 是历史回顾和定义解释，后 30% 才开始分析 | 背景只保留推进当前判断所需的部分，其余删或挪附录 |
| 术语密度冒充分析深度 | 一段连续引入多个框架名，读者还不知道它们解释什么 | 先建立事实、问题和影响，再命名；必要的正式定义立即补分析用途 |
| 润色改变分析状态 | 句子更顺，但可能性、因果、场景或证伪条件变了 | 把这些元素视为受保护语义；需要改动时返回分析作者 |

---

## 自检问题

写完分析 prose 后，至少检查这八件事：

1. 因果链四环节（观测 → 机制 → 效果 → 含义）是否都有？缺哪一环？
2. 读者最可能的 alternative reading 是否被排除或被标注为 open question？
3. 读者读完后能否 reproduce 我的推理（条件 + 脆弱点 + 翻转条件），还是只知道结论？
4. 每段 prose 到了 so-what 终点（方向判断 / 监测指标 / 证伪条件）了吗？
5. 不确定性是 hedge 词还是 calibrated 标注？
6.（report only）thesis 之间的关系是否被显式写出？全篇有 narrative arc 吗？
7. 新概念是否按读者的理解依赖出现，而不是先堆名称后解释用途？
8. 润色或审核建议是否保持数字、因果方向、不确定性、场景和证伪条件不变？

---

**最后更新**: 2026-08-08
