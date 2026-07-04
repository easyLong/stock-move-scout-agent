# stock-move-scout-agent

个股异动侦查项目。核心目标是：围绕研究池股票，在盘中快速发现异动，定位仍然有效的硬证据，并把市场温度、题材强度、领头羊、板块爆发、竞价变化和帖子产物展示出来。

## 当前主链路

```text
盘后确认数据
  -> 研究池
  -> 同花顺 / 开盘啦 / 东方财富 / AkShare / 通达信
  -> 有效事实、题材归因、市场宽度、竞价摘要
  -> 领头羊快照、板块爆发榜、根证据缓存、早盘帖子
  -> Web 工作台
```

核心原则：

- MySQL 是唯一业务状态源，文件只用于日志、缓存和导出。
- 盘后生成冷数据和确认快照，盘中只做热数据扫描和展示。
- 市场概览和异动情报使用最全研究池口径。
- 领头羊和板块爆发榜支持牛市/熊市研究池口径切换。
- 早盘帖子消费事实、skill 结果和消息摘要，不在帖子脚本里沉淀长期判断标准。

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

## 常用页面

| 页面 | 地址 | 用途 |
| --- | --- | --- |
| 异动情报流 | `/` | 盘中异动、证据、题材角色；不展示竞价 |
| 市场概览 | `/market-width` | 全市场、成交额 Top50、研究池宽度、市场加速度观察 |
| 同花顺领头羊 | `/leaders` | 同花顺冻结题材下的领头羊快照 |
| 开盘啦领头羊 | `/kpl-leaders` | 开盘啦精选板块下的领头羊快照 |
| 板块爆发榜 | `/plate-breakouts` | 开盘啦精选板块爆发原因、子板块、研究池交集 Top |
| 竞价详情 | `/auction-detail` | 独立竞价研究页，顶部看涨停封单 Top3 机会和跌停封单 Top3 风险，下面看 09:15-09:25 动态博弈 |

## 核心目录

| 目录 | 职责 |
| --- | --- |
| `src/stock_move_scout/` | 可复用业务模块 |
| `scripts/` | 任务入口、Web 服务、调度器和兼容脚本 |
| `database/mysql/` | MySQL 表结构 |
| `docs/` | 架构、数据链路、任务、页面和维护说明 |
| `data/stock/` | 运行缓存，不提交历史数据 |
| `runs/` | 日志、帖子和临时运行产物，不提交历史产物 |
| `output/` | UI 审查截图和临时导出，已忽略 |

## 研究池口径

| 口径 | 参数 | 含义 | 主要使用位置 |
| --- | --- | --- | --- |
| 熊市系统 | `ma_mode=none` | 近 5 日涨停 + 近 5 日无涨停且 5 日涨幅 Top30，最全研究池 | 市场概览、异动情报、默认分析 |
| 牛市系统 | `ma_mode=ma5_10_20_30_up` | 在最全研究池基础上增加 MA5/10/20/30 各自前周期向上过滤 | 领头羊、板块爆发榜、强更强环境 |

## 文档入口

- [文档地图](docs/README.md)
- [数据链路总览](docs/data_flow_overview.md)
- [当前整体架构](docs/project_architecture.md)
- [任务调度](docs/task_schedule.md)
- [竞价详情系统](docs/auction_detail_system.md)
- [早盘帖子 workflow](docs/morning_reference_workflow.md)
- [MySQL 表职责](docs/mysql_schema.md)
