# 数据链路总览

本文记录当前项目最常用的数据链路，方便排查“今天数据有没有”“页面为什么没展示”“补历史应该跑什么”。

## 总体链路

```text
外部数据源
  -> 原始事实表
  -> 研究池 / 题材 / 市场宽度 / 竞价 / 证据缓存
  -> 快照表或 JSON 产物
  -> Web 页面 / 早盘帖子 / skill 分析
```

## 研究池链路

```text
limit_up_pool_items + stock_daily_bars
  -> research_pool_snapshots
  -> research_pool_items
  -> 盘中扫描、领头羊、板块爆发榜、帖子备选池
```

研究池有两套口径：

| 口径 | `ma_mode` | 规则 | 使用位置 |
| --- | --- | --- | --- |
| 熊市系统 | `none` | 近 5 日涨停 + 近 5 日无涨停且 5 日涨幅 Top30 | 市场概览、异动情报、默认研究池 |
| 牛市系统 | `ma5_10_20_30_up` | 熊市系统基础上增加 MA5/10/20/30 各自前周期向上 | 领头羊、板块爆发榜、强更强环境 |

手工重建：

```powershell
python scripts\build_research_pool_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --ma-mode none --force
python scripts\build_research_pool_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --ma-mode ma5_10_20_30_up --force
```

## 市场概览链路

```text
通达信全市场行情 + 成交额 Top50 + 研究池
  -> market_width_snapshots
  -> market_width_amount_top50
  -> /market-width
  -> stock-market-acceleration-model
```

约定：

- 页面观察对象包括全市场、成交额 Top50、研究池。
- 全市场代表整体情绪。
- 成交额 Top50 代表权重核心。
- 研究池代表近期强势股情绪。
- 市场概览固定使用最全研究池口径，不作为牛熊切换页面。

手工补收盘宽度：

```powershell
python scripts\collect_market_width_daily_close.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

## 领头羊链路

```text
research_pool_items
  + limit_up_pool_items
  + stock_daily_bars
  + ths_homepage_headline_themes
  + kpl_replay_limit_theme_stocks
  + kpl_stock_featured_sections
  -> leaderboard_snapshots
  -> /leaders
  -> /kpl-leaders
```

约定：

- 盘中页面读取最近一次收盘确认快照。
- 盘后生成 T 日快照，主要服务 T+1。
- 同花顺领头羊使用 `source='ths_homepage_headline'`。
- 开盘啦领头羊使用 `source='kpl_primary_theme'`。
- 快照同时保存牛市/熊市系统，页面切换时读对应口径。

手工重建：

```powershell
python scripts\build_leaderboard_snapshot.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --all-pool-modes --force
```

## 板块爆发榜链路

```text
kpl_plate_featured_strengths
  -> kpl_plate_featured_details
  -> kpl_stock_featured_sections
  -> research_pool_items
  -> /plate-breakouts
  -> stock-top3-concept-new-high
```

约定：

- 板块顺序按开盘啦精选板块强度。
- 每个爆发板块展示最强子板块和研究池交集股票。
- 爆发原因只展示，不作为是否进入 Top 子板块的硬过滤。
- 页面支持牛市/熊市研究池切换。

手工补数：

```powershell
python scripts\collect_kpl_plate_details.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --limit 5 --pool-mode bear
python scripts\collect_kpl_plate_details.py --trade-date YYYY-MM-DD --mysql-enabled --mysql-password <MYSQL_PASSWORD> --limit 5 --pool-mode bull
```

## 竞价详情链路

```text
09:15-09:25 集合竞价封单雷达
  -> auction_minute_analysis
  -> auction_candidates
  -> auction_trend_summary
  -> /auction-detail
```

当前口径：

- 竞价不进入异动情报流，只在 `/auction-detail` 独立展示。
- 竞价详情的完整字段和页面规则见 `docs/auction_detail_system.md`。
- 页面主目标：
  - 涨停封单最强 Top3：识别竞价机会在哪。
  - 跌停封单最强 Top3：识别竞价风险在哪。
  - 过程数据：观察 09:15-09:25 涨跌停封单额的动态博弈。
- 最终候选表 `auction_candidates` 只保留涨停封单 Top3。
- 分钟雷达 `auction_minute_analysis` 同时采集所有涨停封单和跌停封单；这样最终 Top3 的全时刻封单过程天然完整。
- 页面按实时盯盘口径展示：当前快照、当前涨停封单榜、当前跌停封单榜、重点追踪和个股时间线。
- 重点追踪表看三个关键点：`09:19封单`、`09:20封单`、`当前封单`，以及 `19-20变化`、`20-最新变化`。
- `当前成交额` 只作为集合竞价成交的辅助确认字段；过程判断不使用竞价额变化。
- `封单额` 使用涨停侧或跌停侧一档封单金额；涨停最终候选优先从 `auction_candidates.raw_json.seal_amount` 兜底，避免 09:25 候选有封单但分钟摘要显示 0。
- `19-20变化` 用来观察最后可撤单窗口是否撤单或继续加强。
- `20-最新变化` 用来观察不可撤单后的封单继续加强还是被卖压消耗；09:25 后自然等同于 `20-25变化`。
- 历史数据只能基于当时已经采到的分钟深度补摘要，不能倒采当时没有保存的竞价明细。

手工补摘要：

```powershell
python scripts\build_auction_trend_summary.py --trade-date YYYY-MM-DD --limit 80 --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

正常交易日会在 09:26 自动运行 `auction_trend_summary`；手工命令主要用于历史补数或排查。

## 早盘帖子链路

```text
morning_market_news
  + daily_market_themes
  + market_width_snapshots
  + kpl_plate_featured_details
  + skill 结果
  -> morning_reference_post workflow
  -> runs/posts
```

当前帖子结构：

```text
市场温度和风格强弱
结合昨日收盘后至盘前消息做推演
给出今日机会或风险点
列出强支撑备选和核心逻辑
今天盯什么
```

手工生成：

```powershell
python scripts\build_morning_reference_post.py --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

主要产物：

```text
runs/posts/morning_reference_YYYY-MM-DD.txt
runs/posts/morning_reference_latest.txt
runs/posts/morning_reference_YYYY-MM-DD.workflow/
```

## 排查顺序

数据没展示时，按这个顺序查：

1. 查任务是否跑完：`scheduled_tasks`、`scheduled_task_runs`。
2. 查基础数据表是否有当日数据。
3. 查研究池口径是否选错，尤其是牛市/熊市切换。
4. 查页面接口是否返回空 JSON。
5. 查补数是否只能补摘要，不能倒采实时原始数据。
