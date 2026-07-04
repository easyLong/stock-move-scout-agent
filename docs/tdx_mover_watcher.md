# 通达信盘中异动扫描

## 职责

通达信扫描链路负责盘中热数据，不负责拉 F10、不负责模型总结、不负责重建研究池。

主要目标：

- 发现研究池股票的实时异动。
- 保存每个扫描点的涨速、成交额、成交额增量等指标。
- 聚合开盘至今窗口强度。
- 为异动情报流、实时领涨、稳定异动提供数据。

## 扫描范围

底层扫描范围以研究池为核心，并额外保留上证指数等辅助标的，方便后续开发区间 Top 和市场共振。

研究池来自：

```text
research_pool_items
```

不是问财 Top50。

## 数据链路

```text
realtime_mover_scan
  -> scan_runs
  -> scan_movers
  -> windows
  -> window_movers
  -> event_engine
  -> stock_move_events / derived_signals / stock_move_evidence
  -> stock_move_judgements
  -> 异动情报流
```

## 主要表

| 表 | 用途 |
| --- | --- |
| `scan_runs` | 一次扫描批次 |
| `scan_movers` | 当前扫描点命中的异动股票 |
| `windows` | 开盘至今窗口 |
| `window_movers` | 窗口内累计强度 |
| `stock_move_events` | 归一化异动事件 |
| `stock_move_judgements` | 异动解释和延续性判断 |

## 异动条件

当前异动触发以涨速为核心：

- 去掉 `amount_delta_15s >= 30000000 AND speed > 0.5` 触发。
- 涨速阈值改为 `speed >= 1.5`。

成交额增量仍可作为排序和辅助解释指标，但不再作为低涨速触发入口。

## 实时领涨

实时领涨展示当前能看到的所有相关股票，并按开盘至当前累计强度排序。

设计要点：

- 不是 5 分钟窗口。
- 是从开盘到当前的累计强度。
- 统计 Top5。
- 和稳定异动榜在 UI 上明确区分。

## 稳定异动

稳定异动是全局 Top5，放在异动情报流前面。

展示内容：

- 当前排名。
- 出现次数。
- 代表强度。
- 最近更新时间。

## 调度

相关热任务只在交易日交易时间运行：

- `realtime_mover_scan`
- `event_engine`
- `stock_move_judgements`
- `anchor_realtime_roles`
- `market_width_snapshot`
- `kpl_plate_strength`

集合竞价任务：

- `auction_candidates`：09:15-09:25，最终候选只保留涨停且封单额最大的 Top3，分钟雷达采集所有涨停/跌停封单。

## 性能原则

- 盘中不重建研究池。
- 盘中不跑模型总结。
- 页面读取盘中表和根证据缓存。
- 盘中只计算会随行情变化的字段。

## 常见问题

### 为什么看起来数据不全？

优先检查：

- 研究池当天是否完整。
- `scan_runs` 是否覆盖目标时间段。
- `scan_movers` 是否有目标股票。
- `window_movers` 是否正常生成。
- 交易时间判断是否跳过了当前时段。

### 为什么页面卡？

常见原因：

- 详情页现场拼复杂 SQL。
- 题材卡片切换时重复请求重数据。
- 根证据缓存缺失，回退到实时计算。

修复方向：

- 交易日前夜刷新 `stock_root_evidence_cache`。
- 盘中只更新热数据。
- 详情页优先读缓存。
