# 任务调度

所有热任务都必须满足交易日和交易时间判断。盘后任务生成的结论主要服务下一个交易日。

## 盘前任务

| 时间 | 任务 | 目的 |
| --- | --- | --- |
| 08:00 | `scheduled_task_health_check` | 检查到点未跑、失败、超时任务 |
| 08:30 | `morning_market_news` | 采集盘前消息，时间范围为上一交易日收盘后至今 |
| 08:32 | `daily_market_themes` | 从盘前消息生成主题雷达 |
| 08:35 | `morning_reference_post` | 使用模型生成适配雪球的早参帖子 |
| 09:15 | `auction_candidates` | 09:15-09:25 竞价涨停封单额 Top3 |

## 盘中热任务

| 时间 | 任务 | 目的 |
| --- | --- | --- |
| 09:30-14:59 | `realtime_mover_scan` | 研究池实时扫描，5 秒一轮，分钟级任务循环 |
| 09:30-14:59 | `market_width_snapshot` | 市场概览实时快照，含开盘啦预测量能 |
| 09:30-14:59 | `kpl_plate_strength` | 开盘啦精选板块强度刷新 |
| 09:31-14:59 | `kpl_plate_details` | 开盘啦精选强度 Top5 点击详情，5 分钟刷新 |
| 09:30-14:59 | `anchor_realtime_roles` | 题材内实时领涨和中军角色 |
| 09:35-14:59 | `event_engine` | 生成标准化盘中异动事件 |
| 09:35-14:59 | `stock_move_judgements` | 生成异动情报流判断 |

## 盘后任务

| 时间 | 任务 | 依赖 | 目的 |
| --- | --- | --- | --- |
| 15:25 | `eastmoney_limit_up_pool` | AkShare `stock_zt_pool_em` | 更新当天涨停池 |
| 15:30 | `ths_hot_concepts` | 同花顺 | 更新低频热点概念 |
| 15:55 | `ths_homepage_headline_themes` | 同花顺首页 | 采集同花顺首页头条题材 |
| 16:05 | `ths_homepage_headline_freeze` | 同花顺首页题材 | 冻结同花顺题材快照 |
| 16:05 | `market_width_daily_close` | AkShare 日 K | 生成收盘市场概览 |
| 16:10 | `ths_market_after_close_summary` | 同花顺盘后小结 | 早参市场背景 |
| 16:25 | `research_pool_snapshot` | 涨停池、日 K、MA5/10/20/30 | 生成当日精细研究池 |
| 16:30 | `ths_stock_concepts` | 研究池 | 增量刷新个股概念解释 |
| 20:05 | `kpl_limit_up_reasons` | 研究池 | 采集开盘啦个股涨停原因 |
| 20:15 | `kpl_replay_limit_themes` | 开盘啦复盘啦 | 采集涨停原因分组和主归因 |
| 20:20 | `kpl_stock_featured_sections` | 研究池 | 采集个股精选板块 |
| 20:35 | `post_close_leaderboard_snapshot` | 研究池、同花顺冻结题材、涨停池 | 生成同花顺领头羊快照 |
| 20:45 | `kpl_leaderboard_snapshot` | 开盘啦精选强度、涨停原因、精选板块 | 生成开盘啦领头羊快照 |
| 22:30 | `pre_trade_night_evidence_prepare` | 研究池、F10、有效事实 | 生成下一交易日证据底稿 |

## 手动或禁用任务

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| `cold_company_profile` | 禁用 | 按需补全公司画像 |
| `kpl_market_capacity` | 禁用 | 市场概览已在 `market_width_snapshot` 中同步采集预测量能 |
| `research_pool_theme_members` | 独立调度禁用 | 仍由 `pre_trade_night_evidence_prepare` 内部调用 |
| `headline_theme_role_evidence` | 独立调度禁用 | 仍由 `pre_trade_night_evidence_prepare` 内部调用 |
| `effective_facts` | 独立调度禁用 | 仍由 `pre_trade_night_evidence_prepare` 内部调用 |
| `async_evidence_source_sync` | 禁用 | 已退出当前证据主链路 |
| `async_evidence_summary` | 独立调度禁用 | 仍由 `pre_trade_night_evidence_prepare` 内部调用 |
| `root_evidence_cache_dirty` | 独立调度禁用 | 当前以批量 pipeline 刷新为主 |

## 已归档任务

- `iwencai_period_rankings`
- `ths_limit_up_review`
- `stock_theme_reason_bank`
- `stock_move_judgement_dirty`
- `auction_trend_summary`
- `daily_root_evidence_pipeline` 旧任务 ID
- `next_trade_day_evidence_prepare`
- `post_close_next_trade_day_evidence_prepare`
