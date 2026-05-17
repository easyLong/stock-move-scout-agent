# 任务调度

## 盘前任务

| 时间 | 任务 | 目的 |
| --- | --- | --- |
| 08:00 | `scheduled_task_health_check` | 检查到点未跑、失败、超时任务 |
| 08:30 | `morning_market_news` | 采集盘前消息 |
| 08:32 | `daily_market_themes` | 从盘前消息生成主题雷达 |
| 08:35 | `morning_reference_post` | 使用模型生成早参帖子 |
| 09:10 | `ths_homepage_headline_themes` | 开盘前刷新同花顺首页头条题材 |
| 09:15 | `auction_candidates` | 竞价涨停封单 Top3 |

## 盘中热任务

这些任务必须满足交易日和交易时间条件。

| 时间 | 任务 | 目的 |
| --- | --- | --- |
| 09:30-14:59 | `market_width_snapshot` | 市场概览实时快照 |
| 09:30-14:59 | `anchor_realtime_roles` | 题材内实时领涨和中军角色 |
| 09:35-14:59 | `event_engine` | 生成盘中事件信号 |
| 09:35-14:59 | `stock_move_judgements` | 生成异动情报流判断 |

## 盘后任务

| 时间 | 任务 | 依赖 | 目的 |
| --- | --- | --- | --- |
| 15:25 | `eastmoney_limit_up_pool` | AkShare `stock_zt_pool_em` | 更新当天涨停池 |
| 15:30 | `ths_hot_concepts` | 同花顺 | 更新低频热点概念 |
| 15:45 | `ths_market_after_close_summary` | 同花顺盘后小结 | 早参市场背景 |
| 16:05 | `market_width_daily_close` | 日K | 生成收盘市场概览 |
| 16:20 | `post_close_leaderboard_snapshot` | 涨停池、日K、收盘宽度 | 生成收盘确认版领头羊 |
| 16:30 | `ths_stock_concepts` | 研究池 | 增量刷新个股概念解释 |
| 22:30 | `pre_trade_night_evidence_prepare` | 收盘确认数据 | 为下一个交易日生成证据底稿 |

## 已归档任务

- `daily_root_evidence_pipeline`：保留为 task kind，当前任务名为 `pre_trade_night_evidence_prepare`。
- `root_evidence_cache_dirty`
- `stock_move_judgement_dirty`
- `ths_limit_up_review`
- `iwencai_period_rankings`
- `stock_theme_reason_bank`
