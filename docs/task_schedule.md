# 任务调度

任务定义以 `src/stock_move_scout/scheduler/task_definitions.py` 为准。本文只保留日常排查需要看的主流程。

所有热任务都必须满足交易日和交易时间判断。盘后任务生成的结论主要服务下一个交易日。

## 盘前任务

| 时间 | 任务 | 用途 |
| --- | --- | --- |
| 07:00 | `morning_market_news` | 采集上一交易日收盘后至今的财联社、华尔街见闻等盘前消息 |
| 07:02 | `daily_market_themes` | 从盘前消息生成今日催化主题池 |
| 07:05 | `morning_reference_post` | 结合市场加速度、强分支 skill、盘前消息生成雪球早参帖子 |
| 08:00 | `scheduled_task_health_check` | 检查到点未跑、失败、超时任务 |
| 09:15 | `auction_candidates` | 09:15-09:25 持续抓竞价封单；最终涨停封单 Top3，分钟雷达同步采所有涨停/跌停封单 |
| 09:26 | `auction_trend_summary` | 压缩竞价分钟雷达，生成强一致、撤单风险、最终突入、尾盘掉榜等标签 |

## 盘中热任务

热任务必须由 `hot` worker 消费。本项目启动脚本默认 worker 类型包含 `hot,maintenance,cold,warm`。如果市场概览或异动情报没有实时数据，先检查 `stock_scout_worker_hot*.pid` 或默认 worker 是否包含 `hot`。

| 时间 | 任务 | 用途 |
| --- | --- | --- |
| 09:30-14:59 | `realtime_mover_scan` | 研究池实时扫描，5 秒一轮，分钟级任务循环 |
| 09:30-14:59 | `market_width_snapshot` | 市场概览实时快照，含全市场、成交额 Top50、研究池和开盘啦预测量能 |
| 09:30-14:59 | `kpl_plate_strength` | 刷新开盘啦精选板块强度 |
| 09:30-14:59 | `anchor_realtime_roles` | 计算题材内实时领涨和中军角色 |
| 09:35-14:59 | `event_engine` | 生成标准化盘中异动事件 |
| 09:35-14:59 | `stock_move_judgements` | 生成异动情报流判断 |

## 盘后任务

| 时间 | 任务 | 依赖 | 用途 |
| --- | --- | --- | --- |
| 15:25 | `eastmoney_limit_up_pool` | AkShare `stock_zt_pool_em` | 更新当日涨停池 |
| 15:30 | `ths_hot_concepts` | 同花顺 | 更新低频热点概念 |
| 15:55 | `ths_homepage_headline_themes` | 同花顺首页 | 采集同花顺首页头条题材 |
| 16:05 | `ths_homepage_headline_freeze` | 同花顺首页题材 | 冻结同花顺题材快照 |
| 16:05 | `market_width_daily_close` | AkShare 日 K | 生成收盘市场概览和五日结构 |
| 16:10 | `ths_market_after_close_summary` | 同花顺盘后小结 | 给早参提供前一日市场背景 |
| 16:25 | `research_pool_snapshot` | 涨停池、日 K | 生成当日研究池 |
| 16:30 | `ths_stock_concepts` | 研究池 | 增量刷新个股概念解释 |
| 20:05 | `kpl_limit_up_reasons` | 研究池 | 采集开盘啦个股涨停原因 |
| 20:15 | `kpl_replay_limit_themes` | 开盘啦复盘啦 | 采集涨停原因分组和主归因 |
| 20:20 | `kpl_stock_featured_sections` | 研究池 | 采集个股精选板块 |
| 20:25 | `kpl_plate_details` | 开盘啦精选强度 | 采集 Top5 精选板块详情、爆发原因、子板块 |
| 20:35 | `post_close_leaderboard_snapshot` | 研究池、同花顺冻结题材、涨停池 | 生成同花顺领头羊牛市/熊市双口径快照 |
| 20:45 | `kpl_leaderboard_snapshot` | 开盘啦精选强度、涨停原因、精选板块 | 生成开盘啦领头羊牛市/熊市双口径快照 |
| 22:30 | `pre_trade_night_evidence_prepare` | 研究池、F10、有效事实 | 生成下一交易日证据底稿 |

## 禁用或手动任务

| 任务 | 状态 | 说明 |
| --- | --- | --- |
| `cold_company_profile` | 禁用 | 按需补全公司画像 |
| `kpl_market_capacity` | 禁用 | 市场概览已在 `market_width_snapshot` 中同步采集预测量能 |
| `research_pool_theme_members` | 独立调度禁用 | 由 `pre_trade_night_evidence_prepare` 内部调用 |
| `headline_theme_role_evidence` | 独立调度禁用 | 由 `pre_trade_night_evidence_prepare` 内部调用 |
| `effective_facts` | 独立调度禁用 | 由 `pre_trade_night_evidence_prepare` 内部调用 |
| `async_evidence_source_sync` | 禁用 | 已退出当前证据主链路 |
| `async_evidence_summary` | 独立调度禁用 | 由证据底稿 pipeline 内部调用 |
| `root_evidence_cache_dirty` | 独立调度禁用 | 当前以批量 pipeline 刷新为主 |

## 常用手工补跑

```powershell
# 竞价详情摘要
python scripts\build_auction_trend_summary.py --trade-date YYYY-MM-DD --limit 80 --mysql-enabled --mysql-password <MYSQL_PASSWORD>

# 研究池
python scripts\build_research_pool_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --ma-mode none --force
python scripts\build_research_pool_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --ma-mode ma5_10_20_30_up --force

# 领头羊双口径快照
python scripts\build_leaderboard_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --all-pool-modes --force

# 早盘帖子
python scripts\build_morning_reference_post.py --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

## 已归档任务

- `iwencai_period_rankings`
- `ths_limit_up_review`
- `stock_theme_reason_bank`
- `stock_move_judgement_dirty`
- `daily_root_evidence_pipeline` 旧任务 ID
- `next_trade_day_evidence_prepare`
- `post_close_next_trade_day_evidence_prepare`
