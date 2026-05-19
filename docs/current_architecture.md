# 当前架构快速入口

详细说明以 `project_architecture.md` 为准。本文只保留快速入口，避免多份架构文档长期分叉。

## 快速链路

```text
research_pool_items
  -> stock_daily_bars / limit_up_pool_items / market_width_snapshots
  -> stock_ths_root_items / stock_current_effective_facts_view / stock_effective_facts
  -> async_evidence_summaries / stock_root_evidence_cache
  -> scan_runs / scan_movers / windows / window_movers
  -> stock_move_events / stock_move_judgements / anchor_realtime_roles
  -> leaderboard_snapshots
  -> Web 页面
```

## 关键页面

- 异动情报流：盘中异动、实时领涨、稳定异动、窗口强度和根证据。
- 证据详情：有效事实总结、近期有效事实、多题材角色、时间序列。
- 市场概览：全市场宽度、研究池宽度、成交额 Top50、指数五日结构、开盘啦预测量能。
- 领头羊：同花顺题材领头羊，按收盘确认快照展示。
- 开盘啦领头羊：开盘啦精选板块领头羊，按收盘确认快照展示。

## 已退出主链路

- 问财 Top50 不再作为研究池。
- `stock_active_facts` 不再作为有效事实主表。
- `stock_announcement_effects` 不再作为公告影响主链路。
- `stock_theme_reason_bank` 不再作为题材理由来源。
- `ths_limit_up_review_items` 不再作为涨停池来源。
