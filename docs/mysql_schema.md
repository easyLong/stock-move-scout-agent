# MySQL 表职责

## 研究池

| 表 | 用途 |
| --- | --- |
| `research_pool_snapshots` | 每个交易日研究池快照 |
| `research_pool_items` | 研究池成分，区分情绪票和趋势票 |
| `research_pool_theme_members` | 研究池股票与同花顺概念解释的关系，当前非主实时任务 |

研究池服务日必须是交易日。周末服务日数据视为脏数据。

## 盘中行情和市场概览

| 表 | 用途 |
| --- | --- |
| `scan_runs` / `scan_movers` | 盘中实时扫描 |
| `windows` / `window_movers` | 开盘至今窗口强度 |
| `market_width_snapshots` | 全市场、成交额 Top50、研究池宽度快照 |
| `market_width_amount_top50` | 每个市场概览快照的成交额 Top50 |
| `kpl_market_capacity_snapshots` | 开盘啦预测量能快照 |
| `kpl_market_capacity_trends` | 开盘啦预测量能分时趋势 |

新逻辑只使用研究池口径字段，不再使用旧问财 Top50 口径。

## 涨停池和日 K

| 表 | 用途 |
| --- | --- |
| `limit_up_pool_items` | 东方财富涨停池，来自 AkShare `stock_zt_pool_em` |
| `stock_daily_bars` | 日 K，主要来自 AkShare `stock_zh_a_hist` |

研究池和领头羊必须在这两类数据完整后运行。

## 同花顺数据

| 表 | 用途 |
| --- | --- |
| `stock_ths_root_items` | F10 近期重要事件原始事实 |
| `ths_stock_concept_explanations` | 个股概念解释 |
| `ths_homepage_headline_themes` | 首页头条题材和冻结快照 |
| `ths_market_after_close_summaries` | 盘后市场小结 |

## 开盘啦数据

| 表 | 用途 |
| --- | --- |
| `kpl_plate_featured_strengths` | 精选板块强度，支持实时和历史日期 |
| `kpl_stock_featured_sections` | 研究池股票所属精选板块 |
| `kpl_replay_limit_theme_groups` | 复盘啦涨停原因分组 |
| `kpl_replay_limit_theme_stocks` | 分组下的涨停股票和说明 |
| `kpl_stock_limit_up_reasons` | 个股涨停原因 |

## 有效事实和证据缓存

| 表/视图 | 用途 |
| --- | --- |
| `stock_current_effective_facts_view` | 近 10 日有效事实候选视图 |
| `stock_effective_facts` | 有效事实落库 |
| `async_evidence_summaries` | 有效事实总结 |
| `stock_root_evidence_cache` | Web 根证据缓存 |

## 领头羊

| 表 | 用途 |
| --- | --- |
| `leaderboard_snapshots` | 收盘确认版领头羊整页快照 |

快照来源：

- `ths_homepage_headline`：同花顺领头羊。
- `kpl_primary_theme`：开盘啦精选领头羊。

## 已退出主链路

| 对象 | 状态 |
| --- | --- |
| `stock_active_facts` | 已退出 |
| `stock_announcement_effects` | 已退出 |
| `stock_theme_reason_bank` | 已退出 |
| `ths_limit_up_review_items` | 已退出 |
| `stock_move_judgement_dirty_queue` | 已归档 |
