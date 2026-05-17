# stock-move-scout-agent

个股异动侦查项目。核心目标是：在研究池范围内快速发现异动，定位还有效的硬事实，并把实时异动、题材角色、有效事实时间线和市场概览展示出来。

## 当前主链路

```text
研究池
  -> 实时行情扫描
  -> 异动情报流 / 稳定异动 / 实时领涨
  -> 头条题材角色与带动性
  -> F10 重要事件有效事实
  -> 事实时间线摘要
  -> 证据详情 / 领头羊 / 市场概览 / 早参帖子
```

## 目录

- `src/stock_move_scout/`：可复用业务模块。
- `scripts/`：任务入口和兼容脚本。
- `database/mysql/stock_scout_schema.sql`：MySQL 表结构。
- `config/stock_scout_evidence_refresh.json`：F10 增量刷新配置。
- `docs/`：当前架构、数据源、证据层和维护说明。
- `data/stock/`：运行缓存目录，不提交历史数据。
- `runs/`：日志、帖子、临时运行产物，不提交历史产物。
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

手动重跑核心证据链：

```powershell
$env:PYTHONPATH='src;scripts'
python scripts\build_effective_facts.py --mysql-enabled --mysql-password <MYSQL_PASSWORD> --trade-date 2026-05-15 --research-pool-only
python scripts\summarize_async_evidence.py --mysql-enabled --mysql-password <MYSQL_PASSWORD> --trade-date 2026-05-15 --research-pool-only --force --fallback-only --limit 500
python scripts\refresh_root_evidence_cache.py --mysql-enabled --mysql-password <MYSQL_PASSWORD> --trade-date 2026-05-15 --force --research-pool-only
```

## 关键约束

- MySQL 是唯一业务状态源，文件只用于日志、缓存和导出。
- 研究范围统一走 `research_pool_items`。
- 有效事实只从 `stock_ths_root_items` 的 F10 重要事件生成。
- 独立龙虎榜证据链已退役；如果 F10 重要事件里包含龙虎榜，则作为 F10 事实展示。
- `ths_stock_concept_explanations` 只用于题材成员、头条题材角色和领头羊关系，不再作为证据详情背景证据。

## 文档

- [当前架构](docs/project_architecture.md)
- [数据源](docs/data_sources.md)
- [证据层](docs/evidence_layer.md)
- [重构状态](docs/refactor_status.md)
