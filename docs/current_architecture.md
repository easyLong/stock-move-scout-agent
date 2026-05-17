# 当前架构

当前架构以 `project_architecture.md` 为准。本文只保留快速入口，避免两份架构文档长期分叉。

## 快速链路

```text
research_pool_items
  -> scan_runs / scan_movers / windows / window_movers
  -> research_pool_theme_members / stock_headline_theme_role_evidence
  -> stock_move_events / stock_move_judgements
  -> stock_ths_root_items
  -> stock_current_effective_facts_view / stock_effective_facts
  -> async_evidence_summaries
  -> stock_root_evidence_cache
  -> Web 页面
```

## 关键页面

- 异动情报流：盘中异动、窗口强度、实时证据、有效事实总结。
- 证据详情：多题材切换、题材角色、时间序列、有效事实总结。
- 市场概览：全市场宽度、研究池宽度、成交额 Top50、指数五日结构。
- 领头羊：研究池内情绪票和趋势票的候选展示。

## 废弃主链路

- 问财 Top50 不再作为研究池。
- `stock_active_facts` 不再作为有效事实主表。
- `stock_announcement_effects` 不再作为公告影响主链路。
- `stock_theme_reason_bank` 不再作为题材理由来源。
- `ths_limit_up_review_items` 不再作为涨停池来源。

详细说明见 `project_architecture.md`、`data_sources.md`、`evidence_layer.md`。
