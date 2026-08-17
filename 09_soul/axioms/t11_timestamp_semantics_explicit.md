---
id: axiom_t11_timestamp_semantics_explicit_2026
category: technical
created: 2026-04-18T22:59:00-07:00
updated: 2026-05-03T13:05:00-07:00
---

> Self-demonstration: this axiom's own `created` / `updated` fields use the
> ISO-8601-with-offset form mandated below. A bare `2026-04-18` would have
> been ambiguous (PT? ET? UTC build day?) and would itself violate T11.

# T11. 时间戳与日期字段必须显性声明语义

## 1. 核心公理

任何 `date` 或 `timestamp` 字段必须在命名层显性表达它的时区语义；任何对两个时间值的比较，必须先验证两边语义匹配。不允许出现"同名字段在不同上下文里语义不同"的情况，更不允许"用字段 A 当成字段 B 的语义喂给比较器"。

## 2. 深度推演

### 2.1 时间是跨域语义最容易被偷换的维度

价格存储是 UTC、市场会话是 ET / CT、加密货币是 24/7、宏观数据按 series 自带时区、Python wall-clock 默认带或不带 tz——同一个 `date(2026, 4, 18)` 在上下游可能各代表不同的真实事件。命名上不区分，开发者就只能靠记忆来维持语义一致，记忆失效那一刻就是 bug 出现的那一刻。

### 2.2 Bug 的根源不是"选错时区"，而是"没标语义"

现实里多市场系统通常被迫处理多个时区。把存储统一切到某个本地时区不能消灭问题，只是让原本暴露在 UTC 端的 bug 跑到本地时区端去藏。真正的解法是承认多时区客观存在，并在每一个具体字段上把时区语义标出来，让"语义不匹配"在编辑器和 review 阶段就被肉眼识别。

### 2.3 同名异义比缺字段更危险

缺字段会被静态检查或运行时报错。同名字段在不同上下文里语义不同（例如 `report_date` 在 frontmatter 里指市场会话日，在 packet 里指 UTC 构建日），下游写比较代码的人会按自己当下的语义直觉去用，bug 沉默地通过测试，每次跑都偷偷扩大破坏面。

### 2.4 命名后缀比类型系统更通用

Python 的 `date` / `datetime` 类型本身不区分语义；即使引入 `tzinfo`，对"日历日 vs 市场会话日"这种语义差也无能为力。命名后缀（`_at_utc`, `_session_date_et`, `_calendar_day_utc`）是最低成本、跨语言、跨配置文件、跨 schema 都生效的通用契约。

### 2.5 DST 是"同名异义"最阴险的子类

夏令时把"看似稳定的时区标签"变成时间函数：同一个 IANA tz `America/New_York` 在一年里 offset 会在 `-05:00` 与 `-04:00` 之间切换两次，且切换发生在凌晨而不是日界，跨日逻辑常常踩到。三类 bug 几乎专门由 DST 引发：

1. **把 fixed offset 当 timezone 复用**：`timezone(timedelta(hours=-5))` 看起来等价于 ET，但只在标准时间内成立。一旦跨 DST 切换日，所有用它构造的 UTC 锚都会错 1 小时；下游 freshness / idempotency 在那一天悄悄失效，第二天又自己"恢复"，使 bug 极难复现。
2. **`combine(date, time(16,0), tzinfo=ET)` 自以为是"市场收盘"**：这套构造既忽略了交易所早收市日（13:00 ET），也假设 DST 切换日不存在 wall-clock 的 gap / fold；即使用了 IANA tz，仍然是"先想象一个 wall clock，再倒推 UTC"，方向错了。市场收盘时刻只能由交易所 calendar 的 `session_close()` 给出，不能由开发者重写。
3. **`now_utc - timedelta(days=N)` 当作"N 天前的同一时刻"**：在 DST 切换日附近，本地 wall clock 的"N 天前"不再等于 `N × 24h`；如果下游用这个差值去推 session_date，可能整体偏 1 个 session。涉及"上一个交易日"或"N 个会话之前"的语义必须走 calendar 的 `previous_session()`，不能用 `timedelta`。

DST 的根因仍然是同名异义：`-04:00` 这个后缀字符串描述的是某个具体时刻的 offset，不是某个市场的"时区"。把前者当后者用，就是用字段 A 的语义去喂字段 B 的比较器——T11 第一性问题在 DST 这里以最隐蔽的方式重现。

### 2.6 解析与校验委托给标准库，不要自己重写

时间相关的"语义守卫"很容易写成"枚举禁用清单 + 自写形状正则"——`if name.startswith("+") or name.startswith("-")`、`re.compile(r"^\d{4}-\d{2}-\d{2}T...")`、自己列 `ALLOWED_TZS = {...}`。这种写法每一条都是把 Python 标准库已经实现过的判定规则在外层重写一遍：

- `zoneinfo.ZoneInfo(s)` 已经是 IANA tz 的 ground truth：合法名（含 `Etc/GMT+4` 这种少见但真实的 entry）放行，`-04:00` / `+00:00` / `GMT+4` / 错拼名 / 空串 / 非字符串全部 raise。
- `datetime.fromisoformat(s)` 已经是 ISO-8601 的 ground truth：能 parse 就接受，不能 parse 就 raise；带不带 tz 用 `parsed.tzinfo is None` 判断，不需要再写 offset 正则。

自己重写这层的代价是：

1. **必然不全**——你的黑名单总会漏掉真实存在但少见的合法值（`Etc/GMT+4`），以及没想到的非法形状（`"   "`、`int`、`None`）。
2. **跟库的版本演化错位**——Python 升级带来的 ISO-8601 容忍度变化（如 3.11 起接受 `Z` 后缀）会让自写正则越来越偏。
3. **掩盖原始错误信息**——库 raise 时携带"`No time zone found with key -04:00`"这种精准 message，自写检查只能给出"shape mismatch"之类的笼统错误。

正确形状是**让库自己 parse，parse 不出就把异常翻译成本地的 domain error**：守卫的职责是"把 stdlib 的 `ZoneInfoNotFoundError` / `ValueError` / `TypeError` 转成 `TimeSemanticsMismatch`,带上原 message",不是"自己重新发明 parser"。这条原则适用范围远不止时间——任何时候发现自己在为标准库已覆盖的语法写黑名单或正则，都应该回头委托给库本身。

### 2.7 日期级观测不能伪装成精确时刻

外部 source 经常只给日期，不给精确发布时间。系统仍可能需要一个可排序的 UTC anchor，但这个 anchor 的精度必须显式声明，否则 date-level 事实会被下游误读成 precise publication instant。

正确形状是把两个事实分开：

1. `observed_precision` 说明精度：`datetime` 表示精确时刻已知，`date` 表示只知道日期。
2. `observed_at_utc` 提供可排序 anchor：精确时刻已知时写真实时刻；只有日期时，写该日期在业务语义时区下的保守截止时刻。
3. `observed_note` 解释转换：date-level cutoff 是排序和 freshness 的保守锚，不是外部 source 的真实发布时间。

在市场系统里，date-level conservative cutoff 的常用规则是：给定 `observed_date=2026-04-09` 和 `market_tz="America/New_York"`，取 `2026-04-09 23:59:59 America/New_York`，再投影为 `observed_at_utc="2026-04-10T03:59:59Z"`。这个规则把"这一天已经结束前它应被视为可观察"表达清楚，同时保留 `observed_precision: "date"` 和 `observed_note`，防止任何下游把它当成精确发布时间。

## 3. 应用判定

### 何时使用

- 设计或修改任何包含日期/时间字段的 schema（packet、报告 frontmatter、sidecar、CLI 参数、配置文件）
- 写或 review 任何对时间字段的 `==` / `>=` / `<` 比较
- 实现 freshness / idempotency / 缓存命中 / data-anchor 校验逻辑
- 设计跨市场（equity + futures + crypto + fx + macro）的统一 contract

### 命名约定

| 后缀 | 语义 | 示例 |
|---|---|---|
| `*_at_utc` | wall-clock 时刻，UTC ISO 8601 | `built_at_utc`, `generated_at_utc` |
| `*_calendar_day_utc` | 纯 UTC 日历日，24/7 资产 | `crypto_calendar_day_utc` |
| `*_session_date_et` | XNYS / NYSE ET 市场会话日 | `session_date_et` |
| `*_session_date_ct` | CMES CT trading day | `session_date_ct` |
| `*_market_day` + `market_tz` | 按该资产 calendar 解析的 market day（generic） | `market_day` + `market_tz: "America/New_York"` |

### 观测精度约定

当 schema 同时需要可排序的 UTC anchor 和精度声明时，用：

- `observed_precision: "datetime"`：`observed_at_utc` 是真实精确时刻。
- `observed_precision: "date"`：`observed_at_utc` 是 date-level conservative cutoff，由业务语义时区的当日 `23:59:59` 投影到 UTC。
- `observed_note`：必须说明 cutoff 规则和原始日期来源，避免下游误读。

### 比较契约

任何比较两个 date / timestamp 的代码必须满足：

1. 两边语义后缀一致（`*_session_date_et` 只跟 `*_session_date_et` 比）
2. 跨语义比较必须显式调用 calendar-aware 转换函数（例如 `session_date_for_asset(asset_type, anchor_utc)`）
3. 比较器在语义不匹配时应 raise，不能静默降级——静默会让下一波时区 bug 重新长出来

### DST 与 wall-clock anchor 契约

DST 把"时区标签 → UTC offset"从常量变成时间函数，必须在编码层面用强约束抵消：

1. **只承认 IANA tz database**。所有"市场墙钟时区"必须以 `ZoneInfo("America/New_York")` / `ZoneInfo("America/Chicago")` 表示。禁止用 `timezone(timedelta(hours=-5))`、`+05:00` 字符串拼装、`pytz.FixedOffset(...)` 之类的 fixed-offset 构造冒充时区——后者只能描述某一具体时刻的 offset，不能跨 DST 切换日复用。
2. **市场会话开/收时刻只能查交易所 calendar**。任何"该 market_day 的开盘 / 收盘 UTC 时刻"必须由 `cal.session_open(d)` / `cal.session_close(d)` 给出。禁止 `datetime.combine(d, time(16,0), tzinfo=ZoneInfo("America/New_York"))` 这类"先想象 wall clock 再反推 UTC"的写法——它既会忽略早收市日（XNYS 13:00 ET 半日），也会在 DST 切换日的 gap / fold 区间产生不存在或重复的 wall clock。
3. **跨"业务日"距离用 calendar，不用 `timedelta`**。"上一个交易日 / N 个 session 之前"必须走 `cal.previous_session()` / `cal.sessions_in_range()`。`now_utc - timedelta(days=N)` 在跨 DST 切换日时不再对应"N 天前的同一墙钟时刻"，会让 lookback 边界整体偏 1 小时甚至漂掉一个 session。涉及"小时级 lookback"则相反：必须用 UTC 物理 `timedelta`，不要先 `astimezone` 到 ET 再 `timedelta`。
4. **存储侧永远 UTC，展示侧再投影 IANA tz**。同一物理时刻在 UTC 永远是同一字符串，是 DST 唯一不会偷换语义的方向；所有"先用 ET 字符串存，再回头比较"的设计都禁止采用。
5. **带 offset 字符串只描述瞬时**。`built_at_utc: "2026-04-19T06:00:00+00:00"` 是合法的（描述一个具体瞬时），但不能反过来从这个字符串推断该资产"使用 -04:00 时区"。任何"该资产的市场时区"信息必须来自独立的 IANA tz 字段（如 `market_tz: "America/New_York"`），不能从 offset 后缀逆向推。

### 用户输入的 D

任何来自用户的 date 输入（CLI `--date`、API request、报告标题）默认按 ET 市场日解读，graph / builder 内部再按各资产 calendar 翻译。这是因为 PM / 用户的认知默认是市场日，不是 UTC 日历日。

## 4. 相关公理

- **A14 Prompt 边界卫生**：契约层面分清 task-plane 和 control-plane，时间语义属于 task-plane 必须显性的部分。
- **A15 通路层诊断优先**：时间戳 bug 通常是通路层 mismatch（命名层把语义糊在一起），先回到通路层再修个例。
- **V02 可验证性是信任的地基**：显性语义让 freshness / idempotency 校验真正可被验证，否则只是在"看起来对"。
- **V04 时间锚定防止幻觉**：信息时效性的判断质量直接取决于时间字段语义是否清晰。
- **X05 精度在系统中级联**：单个时区失误（凌晨 1 小时差）会向下级联成完整工作流的全量重算或漏算。

## 5. 简化准则

写或读任何带时间的字段时问自己：

1. 这个字段的时区语义是什么？写在了名字里吗？
2. 跟它比较的另一个字段，语义跟它一样吗？
3. 如果不一样，转换函数显式吗，还是靠记忆？
4. 如果代码里有"市场墙钟时刻 → UTC 时刻"的转换，它走的是 IANA tz + 交易所 calendar，还是开发者自己拼的 fixed offset / 硬编 16:00 ET？后者在 DST 切换日和早收市日一定会错，必须改回前者。
5. 如果代码里出现 `timedelta(days=N)` 用来表达"N 个交易日前 / 同一墙钟时刻 N 天前"，它对 DST 切换日是否成立？如果不成立，是否应该改用交易所 calendar 的 `previous_session()`？
6. 如果代码里写了"以 + / - 开头则拒绝"、"必须含 / "、"匹配某 ISO 形状正则"这类校验，标准库（`ZoneInfo`、`datetime.fromisoformat`、`pandas.Timestamp`）是不是已经能 raise 同样的错？如果是，把校验改成"调一次库 + 翻译异常",删掉黑名单/正则。
7. 如果 source 只给日期，schema 是否显式写了 `observed_precision: "date"` 和 `observed_note`？`observed_at_utc` 是否只是 conservative cutoff，而不是伪装成精确发布时间？
