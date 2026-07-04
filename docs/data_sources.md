# 数据源

## 实时行情

| 数据源 | 采集方式 | 落库/产物 | 用途 |
| --- | --- | --- | --- |
| 通达信快照 | `get_security_quotes` | `scan_runs`、`scan_movers`、`market_width_snapshots` | 异动情报流、窗口强度、市场概览 |
| 上证指数快照 | 通达信指数符号 | `market_width_snapshots` | 市场概览指数展示 |
| 开盘啦精选强度 | `ZhiShuRanking.RealRankingInfo` | `kpl_plate_featured_strengths` | 开盘啦领头羊板块排序 |
| 开盘啦精选详情 | `ZhiShuRanking.SonPlate_Info` | `kpl_plate_featured_details` | 精选行业点击详情、板块爆发原因、最强两个子板块下的研究池 Top5 |
| 开盘啦预测量能 | `HomeDingPan.MarketCapacity` | `kpl_market_capacity_snapshots`、`kpl_market_capacity_trends` | 市场概览预测成交额 |
| 集合竞价快照 | 通达信竞价行情 | `auction_minute_analysis`、`auction_candidates`、`auction_trend_summary` | 竞价详情页、封单稳定性、撤单风险、最终突入 |

说明：

- 通达信和开盘啦实时数据只服务当日交易时段。
- 开盘啦精选强度历史日使用 `apphis.longhuvip.com` 加 `Date=YYYY-MM-DD`。
- 开盘啦历史精选强度请求量限制为 70，避免历史接口 `st=80/100` 只返回 10 条的问题。
- 集合竞价历史只能补摘要，不能倒采当时没有保存的分钟明细。

## 盘后确认数据

| 数据源 | 表 | 用途 |
| --- | --- | --- |
| 东方财富涨停池 | `limit_up_pool_items` | 研究池、封板时间、连板辨识度、情绪票得分 |
| AkShare 日 K | `stock_daily_bars` | 5 日涨幅、趋势票、收盘市场概览 |
| 同花顺盘后小结 | `ths_market_after_close_summaries` | 早参市场背景 |
| 同花顺首页头条题材 | `ths_homepage_headline_themes` | 同花顺领头羊题材作用域，盘后冻结 |
| 开盘啦复盘啦涨停原因 | `kpl_replay_limit_theme_groups`、`kpl_replay_limit_theme_stocks` | 涨停票主归因和情绪票说明 |
| 开盘啦个股精选板块 | `kpl_stock_featured_sections` | 非涨停票主板块归属 |
| 开盘啦个股涨停原因 | `kpl_stock_limit_up_reasons` | 情绪票卡片和证据详情 |

## 研究池口径

| 口径 | 参数 | 用途 |
| --- | --- | --- |
| 最全研究池 / 熊市系统 | `ma_mode=none` | 市场概览、异动情报、默认分析 |
| 精细研究池 / 牛市系统 | `ma_mode=ma5_10_20_30_up` | 领头羊、开盘啦领头羊、板块爆发榜的强更强口径 |

市场概览和异动情报引擎固定使用最全研究池口径；领头羊和板块爆发榜保存并展示不同研究池口径。

## 同花顺 F10

| 数据 | 表 | 用途 |
| --- | --- | --- |
| 近期重要事件 | `stock_ths_root_items` | 有效事实候选 |
| 个股概念解释 | `ths_stock_concept_explanations` | 趋势票解释、同花顺题材关系 |
| 公司画像 | `stock_company_profiles` | 公司亮点、主营业务、概念标签 |

当前 F10 重要事件只保留有效事实需要的信息，不再把热点新闻、普通公告和题材要点作为证据主链路。

## 新闻和帖子

| 数据源 | 产物 | 用途 |
| --- | --- | --- |
| 财联社 | `market_news_items`、`runs/data_tasks/morning_market_news.json` | 早盘消息 |
| 华尔街见闻 | `market_news_items`、`runs/data_tasks/morning_market_news.json` | 早盘消息 |
| 同花顺盘后小结 | `ths_market_after_close_summaries` | 前一交易日市场背景 |
| 模型总结 | `runs/data_tasks/morning_reference_post.json` | 雪球早参帖子 |

## 模型输入

模型只处理已经进入有效事实层的候选事实。

```text
原始事实
  -> 近 10 日有效事实候选
  -> 有效事实落库
  -> 模型总结
  -> 根证据缓存 / 页面展示
```

没有有效事实时不调用模型。

## 已不作为主数据源

- 问财 Top50 排名。
- 同花顺涨停复盘表。
- 题材理由银行 `stock_theme_reason_bank`。
- 独立公告影响评分表。
