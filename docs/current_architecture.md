# 当前架构快速入口

详细说明以 `project_architecture.md` 和 `data_flow_overview.md` 为准。本文只保留快速入口，避免临时排查时翻长文档。

## 快速链路

```text
research_pool_items
  -> market_width_snapshots / market_width_amount_top50
  -> limit_up_pool_items / stock_daily_bars
  -> ths_homepage_headline_themes / ths_stock_concept_explanations
  -> kpl_plate_featured_strengths / kpl_plate_featured_details / kpl_stock_featured_sections
  -> stock_current_effective_facts_view / stock_effective_facts / stock_root_evidence_cache
  -> scan_runs / scan_movers / windows / window_movers
  -> stock_move_events / stock_move_judgements / anchor_realtime_roles
  -> leaderboard_snapshots / auction_trend_summary
  -> Web 页面 / 早盘帖子 / skills
```

## 关键页面

| 页面 | 地址 | 数据策略 |
| --- | --- | --- |
| 异动情报流 | `/` | 服务日最全研究池 + 当日实时信号 + 根证据缓存；不混入竞价 |
| 证据详情 | `/api/feed/detail` | 有效事实总结、近期有效事实、多题材角色、时间序列 |
| 市场概览 | `/market-width` | 全市场、成交额 Top50、最全研究池、指数五日结构、开盘啦预测量能 |
| 同花顺领头羊 | `/leaders` | 同花顺首页题材冻结快照 + 研究池候选，支持牛市/熊市口径 |
| 开盘啦领头羊 | `/kpl-leaders` | 开盘啦精选板块 Top8 + 研究池候选，支持牛市/熊市口径 |
| 板块爆发榜 | `/plate-breakouts` | 开盘啦精选强度 Top5、爆发原因、子板块、研究池交集 Top |
| 竞价详情 | `/auction-detail` | 独立竞价研究系统，观察 09:15-09:25 封单稳定性、撤单、最终突入、尾盘掉榜 |

## 研究池口径

- 熊市系统：`ma_mode=none`，最全研究池，适合调整期和低位试错。
- 牛市系统：`ma_mode=ma5_10_20_30_up`，在最全研究池上增加 MA5/10/20/30 向上过滤，适合强更强环境。
- 市场概览和异动情报固定使用最全研究池。
- 领头羊和板块爆发榜可以切换牛市/熊市系统。

## 已退出主链路

- 问财 Top50 不再作为研究池。
- `stock_active_facts` 不再作为有效事实主表。
- `stock_announcement_effects` 不再作为公告影响主链路。
- `stock_theme_reason_bank` 不再作为题材理由来源。
- `ths_limit_up_review_items` 不再作为涨停池来源。
