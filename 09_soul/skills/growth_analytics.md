---
title: 增长数据分析工具集
category: API Guide
tags: [analytics, ga4, kit, typefully, growth, metrics]
created: 2026-03-14
updated: 2026-03-14
---

# Skill: 增长数据分析（GA4 / Kit / Typefully）

三个 CLI 工具，分别查询网站流量（GA4）、邮件订阅（Kit）、Twitter 发布与互动（Typefully）的数据。

## When to Use

用户说出以下意图时触发：
- "查一下最近的流量"、"网站数据怎么样"
- "订阅者增长了多少"、"打开率多少"
- "Twitter 互动数据"、"impression 多少"
- "做一下增长分析"、"拉一下 growth 数据"
- 任何涉及 <your-site> 网站、Newsletter、社交媒体指标的查询

## Prerequisites

- 根目录 `.env` 包含：
  - `KIT_API_KEY` — Kit (ConvertKit) API v4 key
  - `TYPEFULLY_API_KEY` — Typefully v2 API key（发布查询用）
  - `GA4_CREDENTIALS_PATH` — GA4 service account JSON 文件的绝对路径
- Typefully 浏览器级凭据（可选，仅 engagement metrics 需要）：
  - `TYPEFULLY_AUTHORIZATION`、`TYPEFULLY_ACCOUNT`、`TYPEFULLY_SESSION`
- Python venv 已激活（`source .venv/bin/activate`）

## Local Status

本 skill 已对齐到本地 `09_soul/tools/` 工具路径。

注意：
- 工具脚本已导入
- 历史项目目录 `adhoc_jobs/website_growth/` 未导入
- 如需持久化结果，当前建议直接用 `--output` 写到你自己的路径

## 工具一：Kit 订阅数据

```bash
python 09_soul/tools/kit_metrics.py account              # 账号信息
python 09_soul/tools/kit_metrics.py growth               # 最近 14 天订阅增长
python 09_soul/tools/kit_metrics.py growth --start-date 2026-02-28 --end-date 2026-03-14
python 09_soul/tools/kit_metrics.py email-stats           # 近 90 天打开率/点击率
python 09_soul/tools/kit_metrics.py subscribers --count   # 当前活跃订阅者数
python 09_soul/tools/kit_metrics.py broadcasts --limit 10 # 最近 10 期邮件
python 09_soul/tools/kit_metrics.py broadcast-stats 23288438  # 单期打开率/点击率
python 09_soul/tools/kit_metrics.py snapshot              # 全量数据快照
python 09_soul/tools/kit_metrics.py snapshot --output /tmp/kit_snapshot.json
```

### Kit 关键指标

- **growth_stats**：时间段内的新增、退订、净增、总数
- **email_stats**：90 天汇总发送量、打开数、点击数
- **broadcast stats**：单期 open_rate、click_rate、unsubscribes
- **subscribers**：活跃/非活跃/退订订阅者列表和总数

## 工具二：GA4 网站流量

```bash
python 09_soul/tools/ga4_metrics.py daily --days 7        # 每日流量趋势
python 09_soul/tools/ga4_metrics.py weekly --days 90       # 周级汇总
python 09_soul/tools/ga4_metrics.py top-pages --limit 20   # 热门页面
python 09_soul/tools/ga4_metrics.py sources                # 流量来源明细
python 09_soul/tools/ga4_metrics.py channels               # 渠道分组
python 09_soul/tools/ga4_metrics.py campaigns --days 14    # UTM campaign 归因（Twitter 效果追踪）
python 09_soul/tools/ga4_metrics.py snapshot --output /tmp/ga4_snapshot.json  # 全量
```

### GA4 关键指标

- **daily/weekly**：activeUsers, newUsers, sessions, screenPageViews, averageSessionDuration, bounceRate
- **top-pages**：pagePath, pageTitle, screenPageViews, activeUsers
- **sources**：sessionSource, sessionMedium 维度的流量分布
- **channels**：sessionDefaultChannelGroup（Direct, Organic Search, Referral, Social 等）
- **campaigns**：sessionCampaignName — 用于验证 UTM 标记的 Twitter thread 等是否真正带来了流量

### GA4 Property

- Property ID：*(由 GA4 console 获取)*
- 网站：<your-site> (Computing Life)
- Service Account JSON 位置：由 `.env` 中 `GA4_CREDENTIALS_PATH` 指定

## 工具三：Typefully Twitter 数据

### 发布数据（v2 API，API key 认证）

```bash
# 查看已发布的推文/thread
curl -s -H "Authorization: Bearer $TYPEFULLY_API_KEY" \
  "https://api.typefully.com/v2/social-sets/<your-social-set-id>/drafts?status=published&limit=50"
```

Social set ID：*(由 Typefully API 获取)*

### Engagement 数据（浏览器 session 认证）

```bash
python 09_soul/tools/typefully_metrics.py snapshot         # 全量 engagement 快照
python 09_soul/tools/typefully_metrics.py metric impressions --start-date 2026-03-01
python 09_soul/tools/typefully_metrics.py metric engagements
python 09_soul/tools/typefully_metrics.py metric followers
```

详见 `09_soul/skills/typefully_metrics.md`。

**注意**：Typefully engagement API 需要浏览器级凭据（TYPEFULLY_AUTHORIZATION / ACCOUNT / SESSION），这些需要从 Typefully 网页端抓取。v2 API key 只能查发布状态，不能查 impressions/engagement。

## 典型用法

### 快速查看增长概况

```bash
# 一条命令看 Kit 全量
python 09_soul/tools/kit_metrics.py snapshot

# 一条命令看 GA4 全量
python 09_soul/tools/ga4_metrics.py snapshot
```

### 验证 Twitter 推广效果

```bash
# 查 GA4 UTM campaign 数据，看 Twitter thread 是否带来了流量
python 09_soul/tools/ga4_metrics.py campaigns --days 14
```

### 交叉分析

同时拉取 Kit 和 GA4 数据，对比订阅增长和流量趋势：

```bash
python 09_soul/tools/kit_metrics.py growth --start-date 2026-03-01 --end-date 2026-03-14
python 09_soul/tools/ga4_metrics.py weekly --days 30
```

## 数据存储

如需持久化历史数据，用 `--output` 参数保存 JSON。原始 reference repo 中的 `adhoc_jobs/website_growth/` 未导入本地，当前请自行选择输出目录。

## 注意事项

- Kit API rate limit：120 requests / 60 seconds
- GA4 Data API 有配额限制，snapshot 命令一次跑多个 report，注意不要频繁调用
- Typefully engagement API 是私有 API，可能随时变动
- 所有工具默认输出 JSON 到 stdout，可用 `| python3 -m json.tool` 格式化
