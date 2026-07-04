# 股票异动侦查项目文档

这组文档只描述当前有效架构。旧的问财 Top50 研究池、同花顺涨停复盘、题材理由库、公告影响评分等链路已经退出主流程，只在“已退出主链路”小节里保留说明。

## 推荐阅读顺序

1. `README.md`：项目入口、启动方式、常用页面。
2. `data_flow_overview.md`：从采集到页面/帖子的主数据链路。
3. `project_architecture.md`：整体分层和架构边界。
4. `task_schedule.md`：每日任务时间、依赖和用途。
5. `mysql_schema.md`：主要表按职责分组说明。

## 文档索引

| 文档 | 定位 |
| --- | --- |
| `data_flow_overview.md` | 当前主数据链路、页面链路、研究池口径、补数入口 |
| `project_architecture.md` | 当前整体架构、分层职责、核心数据链路 |
| `architecture_refactor.md` | 松耦合重构准则、pipeline 边界、后续拆分路线 |
| `morning_reference_workflow.md` | 早盘帖子 workflow、事实输入、产物路径、写作规则 |
| `data_sources.md` | 数据源、刷新周期、落库表、使用位置 |
| `evidence_layer.md` | 有效事实层、模型总结层、根证据缓存 |
| `mysql_schema.md` | 主要表按职责分组说明 |
| `task_schedule.md` | 当前调度任务、时间、用途 |
| `auction_detail_system.md` | 集合竞价详情页的数据链路、字段定义、排序和颜色规则 |
| `tdx_mover_watcher.md` | 盘中异动扫描、窗口强度、实时领涨 |
| `evaluation_system.md` | 验证清单和质量检查方式 |
| `refactor_status.md` | 已完成重构、保留兼容项、后续债务 |
| `current_architecture.md` | 当前架构快速入口，避免临时翻长文档 |

## 一句话架构

项目围绕“个股异动，快速定位异动原因，找真正有效证据”组织：盘后生成研究池、证据底稿、领头羊和板块快照，盘中只做热数据扫描和动态展示，帖子和页面优先消费稳定事实包。

```text
Data Facts
  -> Domain Rules
  -> Pipelines
  -> Skills / Posts
  -> Web 工作台
```

## 当前主口径

- 市场概览、异动情报引擎：使用最全研究池，等价于熊市系统 `ma_mode=none`。
- 领头羊、开盘啦领头羊、板块爆发榜：支持牛市/熊市系统切换。
- 竞价详情：最终候选保留涨停封单 Top3，分钟雷达采集所有涨停/跌停封单。
- 早盘帖子：开头使用市场加速度模型，备选个股使用 Top3 强分支 skill，消息推演来自盘后至盘前新闻和主题雷达。

## 文档维护原则

- 新链路先补 `data_flow_overview.md`，再补专题文档。
- 任务时间以 `src/stock_move_scout/scheduler/task_definitions.py` 为准。
- 表职责以 `docs/mysql_schema.md` 为准。
- 页面行为改动后同步更新根 `README.md` 的“常用页面”。
- 废弃链路不要混进主流程，只在对应文档末尾说明。
