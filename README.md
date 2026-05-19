# stock-move-scout-agent

个股异动侦查项目。核心目标是：围绕研究池股票，在盘中快速发现异动，定位仍然有效的硬证据，并把实时异动、题材角色、有效事实、市场环境和领头羊结论展示出来。

## 当前主链路

```text
盘后确认数据
  -> 研究池
  -> F10 重要事件 / 开盘啦 / 同花顺 / 行情宽度
  -> 有效事实与模型总结
  -> 根证据缓存和领头羊快照
  -> 盘中异动情报流 / 市场概览 / 领头羊 / 证据详情
```

## 核心目录

- `src/stock_move_scout/`：可复用业务模块。
- `scripts/`：任务入口、Web 服务、调度器和兼容脚本。
- `database/mysql/stock_scout_schema.sql`：MySQL 表结构。
- `docs/`：当前架构、数据源、证据层、任务和维护说明。
- `data/stock/`：运行缓存目录，不提交历史数据。
- `runs/`：日志、帖子和临时运行产物，不提交历史产物。
- `output/`：UI 审查截图和临时导出，已忽略。

## 快速启动

```powershell
cd C:\Code\stock-move-scout-agent
python -m pip install -e .
powershell -ExecutionPolicy Bypass -File scripts\start_stock_scout_web.ps1 -MysqlPassword <MYSQL_PASSWORD>
```

打开：

```text
http://127.0.0.1:8788/
```

启动调度器：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_stock_scout_scheduler.ps1 -MysqlPassword <MYSQL_PASSWORD>
```

## 关键约束

- MySQL 是唯一业务状态源，文件只用于日志、缓存和导出。
- 研究范围统一来自 `research_pool_items`。
- 研究池分为情绪票和趋势票：情绪票来自近 5 日有涨停，趋势票来自近 5 日无涨停且 5 日涨幅 Top30。
- 有效事实主链路来自 `stock_ths_root_items` 的 F10 近期重要事件。
- 领头羊盘中读最近收盘确认快照，盘后生成当日确认快照服务下一个交易日。
- 盘中热任务必须满足交易日和交易时间判断。

## 文档

- [当前整体架构](docs/project_architecture.md)
- [数据源](docs/data_sources.md)
- [证据层](docs/evidence_layer.md)
- [任务调度](docs/task_schedule.md)
- [MySQL 表职责](docs/mysql_schema.md)
- [重构状态](docs/refactor_status.md)
