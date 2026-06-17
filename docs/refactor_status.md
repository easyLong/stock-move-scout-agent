# 重构状态

## 当前主链路

项目当前围绕“个股异动，快速定位异动原因，找真正有效证据”组织。

```text
15:25  更新涨停池
16:05  生成收盘市场概览和日 K
16:25  生成研究池
20:35  生成同花顺领头羊快照
20:45  生成开盘啦领头羊快照
22:30  生成下一交易日证据底稿
盘中   只对服务日研究池做实时扫描、窗口统计、市场概览和异动情报流展示
```

## 已完成

- 研究池落地到 `research_pool_snapshots` 和 `research_pool_items`。
- 研究池规则统一为“近 5 日涨停 + 近 5 日无涨停且 5 日涨幅 Top30”。
- 情绪票和趋势票在研究池、领头羊和 UI 中分开展示。
- 市场概览使用研究池口径，不再使用旧问财 Top50 命名。
- 领头羊新增 `leaderboard_snapshots`，收盘后生成确认版快照。
- 同花顺首页题材改为盘后冻结快照。
- 新增开盘啦精选领头羊链路。
- 开盘啦精选强度支持历史日期采集。
- 开盘啦预测量能接入市场概览。
- 有效事实层简化为 F10 近期重要事件近 10 日过滤。
- 龙虎榜只保留 F10 详情中有席位标签的证据。
- 证据详情和领头羊页优先展示有效事实总结。
- 盘中热任务统一增加交易日和交易时段判断。

## 已退出主链路

- `stock_active_facts`
- `stock_announcement_effects`
- `stock_theme_reason_bank`
- `ths_limit_up_review_items`
- `iwencai_period_rankings`
- `stock_move_judgement_dirty_queue`

## 保留但非主链路

- `root_evidence_cache_dirty`：保留为增量缓存兜底，当前主要依赖批量 pipeline 刷新。
- `research_pool_theme_members`：保留表结构，当前不作为盘中主任务。
- `kpl_market_capacity`：保留手动任务，市场概览已在 `market_width_snapshot` 中同步采集。

## 下一步建议

- 把领头羊大 SQL 拆成维度分表，方便解释每个得分来源。
- 把 `stock_scout_web.py` 的 API 拆成独立 router。
- 给盘中热任务增加更细的耗时和失败原因统计。
- 继续清理源码里的历史中文乱码。
