# Data Sources

数据源层已经迁移到：

```text
src/stock_move_scout/sources/
  definitions.py
  commands.py
```

## 1. Layer Contract

数据源层负责回答三件事：

```text
这个源是什么
它属于 hot / warm / cold 哪一层
它由哪个脚本采集，产出哪些 MySQL 表
它是否需要按股票池分批入队
```

调度器不再直接硬编码这些数据源脚本参数，而是调用：

```python
stock_move_scout.sources.build_source_command(...)
stock_move_scout.sources.is_batched_source_task(...)
```

## 2. Current Sources

```text
tdx_market
  tier: hot
  tables: stocks, scan_runs, scan_movers, windows, window_movers
  role: 通达信行情、股票池、15秒异动扫描、5分钟窗口聚合

ths_root
  tier: cold
  tables: stock_company_profiles, stock_ths_root_items
  role: 同花顺F10根页面，公司画像、亮点、近期事件、公告、题材要点

market_news
  tier: warm
  tables: market_news_items, daily_market_themes
  role: 财联社/华尔街见闻盘前资讯，以及每日主题加工

ths_theme
  tier: cold
  tables: ths_hot_concept_events, ths_hot_concept_members,
          ths_limit_up_review_items, ths_stock_concept_explanations,
          stock_theme_reason_bank, active_market_anchors,
          active_market_anchor_members, active_market_anchor_relations,
          active_anchor_match_candidates
  role: 同花顺题材、涨停复盘、个股概念解释和题材理由库

iwencai_period_rankings
  tier: warm
  tables: stock_period_rankings
  role: 问财3/5/10日区间强度排名

lhb_seat
  tier: warm
  tables: stock_lhb_seat_evidence
  role: 龙虎榜席位结构

auction_market
  tier: hot
  tables: auction_candidates, auction_minute_analysis, auction_trend_summary
  role: 集合竞价分钟雷达和趋势总结
```

## 3. Inspect Sources

```powershell
stock-move-scout sources
stock-move-scout sources --json
```

当前需要分批入队的 source 任务：

```text
cold_company_profile
cold_company_profile_batch
ths_root_extended_items
ths_stock_concepts
```

调度器只调用 `is_batched_source_task()`，不再直接硬编码这些同花顺/公司画像任务名。

## 4. Next Boundary

后续如果新增数据源，优先改：

```text
src/stock_move_scout/sources/definitions.py
src/stock_move_scout/sources/commands.py
src/stock_move_scout/scheduler/task_definitions.py
```

不要直接把新源写进 `stock_scout_task_scheduler.py`。

MySQL 是唯一业务状态源。`runs/`、临时 JSON、CSV 只作为日志、缓存或旧脚本兼容输出，不参与业务状态流转。
