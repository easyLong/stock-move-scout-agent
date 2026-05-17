# 重构状态

## 当前主链路

本项目当前围绕“个股异动，快速定位异动原因，找真正有效证据”组织。

```text
盘后 15:25  涨停池更新
盘后 16:05  日K和市场概览收盘快照
盘后 16:20  收盘确认版领头羊快照
夜间 22:30  生成下一个交易日的研究池、有效事实、模型总结、根证据缓存
盘中        只对服务日研究池做实时扫描、窗口统计、市场概览和异动情报流展示
```

## 已完成

- 研究池落地到 `research_pool_snapshots` 和 `research_pool_items`。
- 研究池规则统一为“近5日涨停 + 近5日无涨停且5日涨幅Top30”。
- 市场概览使用 `research_pool_*` 字段，不再使用旧问财 Top50 命名。
- 领头羊新增 `leaderboard_snapshots`，收盘后生成确认版快照。
- Web API 增加 `service_trade_date`、`base_trade_date`、`leader_data_trade_date`。
- 有效事实层简化为 F10 近期重要事件近10日过滤。
- 龙虎榜只保留同花顺详情里的蓝色席位标签证据。
- 证据详情和领头羊页优先展示有效事实总结。

## 已退出主链路

- `stock_active_facts`
- `stock_announcement_effects`
- `stock_theme_reason_bank`
- `ths_limit_up_review_items`
- `iwencai_period_rankings`
- `stock_move_judgement_dirty_queue`

## 保留但非主链路

- `stock_root_evidence_cache_dirty_queue`：保留为增量缓存兜底，当前主要依赖批量 pipeline 刷新。
- 旧问财 Top50 物理字段已从当前 schema 和线上表移除。

## 下一步建议

- 把领头羊大 SQL 拆成维度分表，便于解释每个得分来源。
- 把 `stock_scout_web.py` 的 API 继续拆成独立 router。
- 给盘中热任务增加更细的耗时和失败原因统计。
