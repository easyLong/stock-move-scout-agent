# 证据层设计

## 目标

证据层只服务一个问题：当前个股异动时，哪些事实仍然值得被人看到。

因此证据层不追求展示所有信息，而是过滤掉：

- 太老的消息。
- 普通公告噪声。
- 普通龙虎榜噪声。
- 与当前异动关系弱的题材背景。
- 旧问财排名、旧复盘文本等历史链路。

## 当前主链路

```text
F10 近期重要事件
  -> 当前有效事实视图
  -> 有效事实落库
  -> 模型/兜底总结
  -> 根证据缓存
  -> 页面展示
```

对应对象：

- `stock_ths_root_items`
- `stock_current_effective_facts_view`
- `stock_effective_facts`
- `async_evidence_summaries`
- `stock_root_evidence_cache`

## 原始事实层

当前有效事实只从 `stock_ths_root_items` 进入，且主要取：

```text
item_kind = important_event
```

`stock_ths_root_items` 保存：

- 股票代码和名称。
- 事件标题。
- 事件正文。
- 事件详情。
- 事件日期。
- 原始链接。
- 标签和原始 JSON。

## 当前有效事实视图

`stock_current_effective_facts_view` 负责把原始事实粗筛成候选有效事实。

当前规则：

- 事件日期存在。
- 事件距离服务交易日不超过 10 天。
- 只取 F10 近期重要事件。
- 过滤融资融券、股东人数变化、股东大会、普通分红、大宗交易、普通异动提醒等低价值项目。
- 普通龙虎榜默认排除。
- 龙虎榜只有 F10 详情里带蓝色席位标签时才保留。

## 有效事实落库

`stock_effective_facts` 是展示和模型总结的有效事实表。

字段约定：

| 字段 | 当前约定 |
| --- | --- |
| `source_table` | `stock_ths_root_items` |
| `source_key` | `stock_ths_root_items:{id}` |
| `fact_type` | `important_event` |
| `evidence_group` | `current_effective` |
| `evidence_role` | `hard_catalyst` |
| `valid_status` | `active` |
| `display_level` | 3 日内为 `primary`，更早为 `secondary` |

## 模型总结层

`async_evidence_summaries` 保存每只股票在某个交易日的有效事实总结。

调用原则：

- 没有有效事实，不调用模型。
- 有效事实 hash 不变，复用已有总结。
- 事实减少但剩余内容没有新增时，优先裁剪缓存，不强制重新总结。
- 模型总结必须保留关键数字、日期、金额、比例和交易方向。
- 模型不可用时允许 fallback 总结，保证页面可读。

## 根证据缓存

`stock_root_evidence_cache` 是 Web 的主要读取入口。

实际刷新由 `pre_trade_night_evidence_prepare` 批量完成。独立的 `effective_facts`、`async_evidence_summary`、`root_evidence_cache_dirty` 不再作为常规调度任务，保留为手动排障或兼容入口。

缓存包含：

- 有效事实列表。
- 有效事实总结。
- 公司亮点。
- 研究池身份。
- 多题材角色。
- 盘中异动判断摘要。

页面不应该每次打开都现场拼完整证据 SQL。

## 展示原则

证据详情优先顺序：

1. 有效事实总结。
2. 近期有效事实明细。
3. 关联题材卡片。
4. 题材内角色、带动性、时间序列。
5. 公司亮点。

不再展示：

- 问财排名。
- 普通题材背景长文本。
- 过期公告。
- 无有效事实的模型空总结。
- 独立龙虎榜噪声。

## 风险

- 10 日窗口适合快速可用，但对超长期订单、股权、并购事件可能偏保守。
- 龙虎榜标签依赖 F10 详情解析，后续可以继续把规则表化，补充席位白名单和金额阈值。
