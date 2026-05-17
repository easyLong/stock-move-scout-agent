# MySQL 表说明

## 研究池

| 表 | 用途 |
| --- | --- |
| `research_pool_snapshots` | 每个服务交易日的研究池快照 |
| `research_pool_items` | 研究池成分，区分情绪票和趋势票来源 |
| `research_pool_theme_members` | 研究池股票和同花顺题材/概念解释的关系 |

研究池服务日必须是交易日。周末服务日数据应视为脏数据并清理。

## 盘中行情和市场概览

| 表 | 用途 |
| --- | --- |
| `scan_runs` / `scan_movers` | 盘中实时扫描 |
| `windows` / `window_movers` | 开盘至今窗口强度 |
| `market_width_snapshots` | 全市场、成交额Top50、研究池宽度快照 |
| `market_width_amount_top50` | 每个市场概览快照的成交额Top50 |

新逻辑只使用 `research_pool_*` 字段，旧问财 Top50 物理字段已移除。

## 涨停池和日K

| 表 | 用途 |
| --- | --- |
| `limit_up_pool_items` | 东方财富涨停池，来自 AkShare `stock_zt_pool_em` |
| `stock_daily_bars` | 日K，主要来自 AkShare `stock_zh_a_hist` |

`post_close_leaderboard_snapshot` 必须在这两类数据和收盘市场宽度完整后运行。

## 有效事实和证据缓存

| 表/视图 | 用途 |
| --- | --- |
| `stock_ths_root_items` | F10 近期重要事件原始事实 |
| `stock_current_effective_facts_view` | 近10日有效事实候选视图 |
| `stock_effective_facts` | 有效事实落库 |
| `async_evidence_summaries` | 有效事实总结 |
| `stock_root_evidence_cache` | Web 根证据缓存 |

## 领头羊

| 表 | 用途 |
| --- | --- |
| `leaderboard_snapshots` | 收盘确认版领头羊整页快照 |

盘中 `leaders` 页面读最近收盘确认快照；盘后 16:20 后读当天确认快照。

## 已退出主链路

| 对象 | 状态 |
| --- | --- |
| `stock_active_facts` | 已退出 |
| `stock_announcement_effects` | 已退出 |
| `stock_theme_reason_bank` | 已退出 |
| `ths_limit_up_review_items` | 已退出 |
| `stock_move_judgement_dirty_queue` | 已归档 |
