# 数据源

## 实时行情

| 数据源 | 采集方式 | 用途 |
| --- | --- | --- |
| 通达信快照 | `get_security_quotes` | 异动情报流、窗口强度、市场概览 |
| 上证指数快照 | 通达信指数符号 | 市场概览指数展示 |

盘中实时任务只服务当天交易日。

## 盘后确认

| 数据源 | 表 | 用途 |
| --- | --- | --- |
| 东方财富涨停池 | `limit_up_pool_items` | 研究池、领头羊封板维度 |
| AkShare 日K | `stock_daily_bars` | 5日涨幅、收盘市场宽度 |
| 同花顺盘后小结 | `ths_market_after_close_summaries` | 早参市场背景 |

领头羊快照必须等涨停池、日K、收盘市场宽度齐备后生成。

## 同花顺 F10

| 数据 | 表 | 用途 |
| --- | --- | --- |
| 近期重要事件 | `stock_ths_root_items` | 有效事实候选 |
| 个股概念解释 | `ths_stock_concept_explanations` | 题材解释、领头羊展示 |
| 首页头条题材 | `ths_homepage_headline_themes` | 多题材关联 |
| 头条题材成分 | `ths_homepage_headline_theme_members` | 题材内领涨和成员关系 |

## 模型输入

模型只处理已经进入有效事实层的候选事实。没有有效事实时不调用模型。

```text
原始事实 -> 近10日有效候选 -> 有效事实总结 -> 根证据缓存 / 页面展示
```

## 已不作为主数据源

- 问财 Top50 排名
- 同花顺涨停复盘表
- 题材理由银行 `stock_theme_reason_bank`
- 独立公告影响评分表
