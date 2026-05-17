# 证据层设计

## 证据层目标

证据层只服务一个问题：当前个股异动时，哪些事实仍然值得被人看到。

因此证据层不追求把所有信息都展示出来，而是过滤掉：

- 太老的消息。
- 普通公告噪音。
- 普通龙虎榜噪音。
- 与当前异动关系很弱的题材背景。
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

对应表：

- `stock_ths_root_items`
- `stock_current_effective_facts_view`
- `stock_effective_facts`
- `async_evidence_summaries`
- `stock_root_evidence_cache`

## 原始事实层

当前有效事实只从 `stock_ths_root_items` 进入，且只取：

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
- 标签。

## 当前有效事实视图

`stock_current_effective_facts_view` 负责把原始事实粗筛成候选有效事实。

当前规则：

- 事件日期存在。
- 事件距离服务交易日不超过 10 天。
- 只取 F10 近期重要事件。
- 排除融资融券、股东人数变化、股东大会、普通分红、大宗交易、普通异动提醒等低价值项目。
- 普通龙虎榜默认排除。
- 龙虎榜只有命中知名游资、机构席位、明显净买入等强信息时才保留。

## 有效事实落库

`stock_effective_facts` 是展示和模型总结的有效事实表。

当前主链路字段约定：

| 字段 | 当前约定 |
| --- | --- |
| `source_table` | `stock_ths_root_items` |
| `source_key` | `stock_ths_root_items:{id}` |
| `fact_type` | `important_event` |
| `evidence_group` | `current_effective` |
| `evidence_role` | `hard_catalyst` |
| `valid_status` | `active` |
| `display_level` | 3 日内 `primary`，更早为 `secondary` |

## 模型总结层

`async_evidence_summaries` 保存每只股票在某个交易日的有效事实总结。

调用原则：

- 没有有效事实，不调用模型。
- 有效事实 hash 不变，复用已有总结。
- 事实减少但剩余内容没新增时，优先做缓存裁剪，不强制重新总结。
- 模型总结必须保留关键数字和日期，不能把硬信息总结没。

如果模型不可用，允许 fallback 总结，保证页面可读。

## 根证据缓存

`stock_root_evidence_cache` 是 Web 的主要读取入口。

缓存包含：

- 有效事实列表。
- 有效事实总结。
- 公司亮点。
- 研究池身份。
- 头条题材角色。
- 盘中异动判断摘要。

页面不应该每次打开都现场拼完整证据 SQL。

## 证据详情展示原则

优先顺序：

1. 有效事实总结。
2. 近期有效事实明细。
3. 关联头条题材卡片。
4. 题材内角色、带动性、时间序列。
5. 公司亮点。

不再展示：

- 问财排名。
- 普通题材背景长文本。
- 过期公告。
- 无有效事实的模型空总结。
- 独立龙虎榜噪音。

## 还需要注意的风险

- 龙虎榜识别已经拆到 `stock_effective_fact_rules`，但默认仍是关键词规则，后续可以继续补充买卖方向、金额阈值和席位白名单。
- 有效事实目前按 10 天窗口粗筛，适合快速可用，但对超长期订单/股权/并购事件可能偏保守。
