# 股票异动侦察 MySQL 表设计

## 原则

MySQL 是唯一业务状态源。

## 核心表

```text
stocks                    股票基础信息
stock_company_profiles    冷数据，公司画像/主营/官网

scan_runs                 每次通达信扫描
scan_movers               每次扫描 TopN 明细

windows                   5分钟聚合窗口
window_movers             窗口排名
evidence_candidates       当前窗口待补证据股票

community_posts           雪球原帖
community_evidence        社区叙事提炼
community_evidence_posts  叙事与原帖引用关系

official_evidence         公告/新闻/互动易/官网新闻
window_official_evidence  窗口与官方证据引用关系

evidence_layers           综合证据层
generated_posts           最终文案

scheduled_tasks           周期任务定义
task_queue                可执行任务队列
task_runs                 执行历史
worker_heartbeats         worker 心跳
pipeline_events           流程事件
```

## 常用查询

最新窗口榜：

```sql
SELECT rank_no, code, name, appearance_count, avg_rank_speed, max_speed, latest_pct_change
FROM v_latest_window_movers
ORDER BY rank_no;
```

最新证据层：

```sql
SELECT rank_no, code, evidence_strength, community_status, evidence_gaps
FROM v_latest_evidence_layers
ORDER BY rank_no;
```

最终文案：

```sql
SELECT gp.code, s.name, gp.content
FROM generated_posts gp
JOIN windows w ON w.id = gp.window_id
LEFT JOIN stocks s ON s.code = gp.code
WHERE w.window_id = '20260507_134934'
  AND gp.post_type = 'dav_info_gap'
ORDER BY gp.id;
```

任务状态：

```sql
SELECT task_type, task_kind, status, COUNT(*)
FROM task_queue
GROUP BY task_type, task_kind, status;
```
