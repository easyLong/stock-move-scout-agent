# stock-move-scout-agent

这是一个独立的股票异动侦查项目，目标是盘中快速发现 A 股异动，并把“为什么动、强度如何、是否带动同题材、是否有持续性”整理成可读的 AI 情报流。

## 核心链路

1. 通达信行情扫描：`scripts/windowed_stock_scout_agent.py`
2. 5 分钟窗口聚合：同一主链路内完成
3. 题材锚点与角色判断：`scripts/build_anchor_realtime_roles.py`
4. 异步证据层：`scripts/run_mysql_window_evidence_worker.py`、`scripts/summarize_async_evidence.py`
5. 异动解释与持续性判断：`scripts/build_stock_move_judgements.py`
6. Web 展示：`scripts/stock_scout_web.py`

## 目录说明

- `src/stock_move_scout/`：Python 包入口和逐步迁移后的共享模块
- `scripts/`：stock-move-scout-agent 全部执行脚本
- `database/mysql/stock_scout_schema.sql`：MySQL 表结构
- `config/stock_scout_evidence_refresh.json`：证据层刷新任务配置
- `docs/`：当前架构、数据源、证据层、MySQL、评估体系等项目文档
- `docs/images/`：股票异动侦查架构图
- `data/stock/`：运行时数据目录，默认不带历史缓存
- `runs/`：日志、pid、临时输出目录，默认不带历史运行产物

当前架构见：[`docs/current_architecture.md`](docs/current_architecture.md)
数据源层见：[`docs/data_sources.md`](docs/data_sources.md)
证据层见：[`docs/evidence_layer.md`](docs/evidence_layer.md)
评估体系见：[`docs/evaluation_system.md`](docs/evaluation_system.md)
重构进度见：[`docs/refactor_status.md`](docs/refactor_status.md)

## 快速启动

先初始化数据库：

```powershell
cd C:\Code\stock-move-scout-agent
powershell -ExecutionPolicy Bypass -Command "python -m pip install -e ."
powershell -ExecutionPolicy Bypass -File scripts\init_stock_scout_mysql.ps1 -MysqlUser root -MysqlPassword 123456
```

启动 Web：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_stock_scout_web.ps1 -MysqlPassword 123456
```

打开：

```text
http://127.0.0.1:8788/
```

启动盘中扫描主链路：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_windowed_stock_scout_agent.ps1 -MysqlPassword 123456
```

启动调度器：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_stock_scout_scheduler.ps1 -MysqlPassword 123456
```

也可以使用包入口：

```powershell
stock-move-scout web --mysql-enabled --mysql-password 123456
stock-move-scout scheduler --mysql-enabled --mysql-password 123456
stock-move-scout worker --worker-types hot,warm --mysql-enabled --mysql-password 123456
```

## 备注

- 本次拆分没有复制历史 `runs/` 和大批量 `data/stock/` 缓存。
- 没有复制 `xueqiu_cookie.txt` 这类敏感文件。
- 默认仍使用 MySQL 数据库 `stock_scout`，如需完全隔离，可通过 `--mysql-database` 改库名。
- MySQL 是唯一业务状态源；日志、缓存、临时 JSON/CSV 只用于运行辅助和旧脚本兼容。
