# 重构状态

## 2026-05-10 本轮整理

已完成：

```text
1. DB/SQL 基础工具下沉到 src/stock_move_scout/db/
   - mysql.py: MySqlConfig、run_mysql、mysql_rows、MySQL CLI 参数
   - sql.py: sql_string/sql_json/sql_int/sql_number/sql_bool
   - scripts/stock_scout_mysql.py 继续作为旧脚本兼容出口

2. 调度命令门面下沉到 src/stock_move_scout/scheduler/commands.py
   - stock_scout_task_scheduler.py 只保留薄 wrapper
   - 业务命令仍由 sources/evidence/analysis/judgement 分层构造

3. Web 运行时辅助下沉到 src/stock_move_scout/web/
   - json_query / latest_data_date / resolve_trade_date
   - stock_scout_web.py 继续保留 FastAPI 入口和前端 HTML
```

验证：

```text
python -m compileall -q src scripts
Web /api/trade_dates 200 OK
Web、windowed agent、scheduler、hot worker、maintenance/cold/warm worker 均已按新代码重启
```

## 1. 目标分层

当前目标架构：

```text
sources -> scheduler -> mysql -> analysis -> evidence -> judgement -> feed/web
```

核心原则：

```text
MySQL 是唯一业务状态源。
scripts 保留为兼容入口和编排入口。
src/stock_move_scout 承接可复用业务模块。
调度器不硬编码业务脚本参数。
Web 不重新推理业务结论。
```

## 2. 已完成拆分

### sources

```text
src/stock_move_scout/sources/
  definitions.py
  commands.py
```

已承接：

```text
数据源定义
hot / warm / cold 分层
采集命令构造
分批任务判断
```

### scheduler

```text
src/stock_move_scout/scheduler/
  task_definitions.py
```

已承接：

```text
scheduled_tasks 定义
next_run 规则
任务参数归属判断
```

### analysis

```text
src/stock_move_scout/analysis/
  commands.py
  realtime_filter.py
  activity.py
  influence.py
```

已承接：

```text
实时异动筛选规则
同锚活动索引
同锚首发与波次
3/5/10 分钟扩散统计
时间序列 Top3 + 本股
活跃序列 Top3 + 本股
主动性评分
带动性评分
短线行为评分
analysis 任务命令路由
```

### evidence

```text
src/stock_move_scout/evidence/
  commands.py
  model_config.py
  model_client.py
  schema.py
  storage.py
  summary.py
  payload.py
```

已承接：

```text
证据层任务命令路由
模型配置读取
OpenAI-compatible 模型客户端
证据摘要 schema
evidence_hash
证据指纹
dirty queue
异步证据 payload 构建
fallback 事实卡
summary 落库
```

### judgement

```text
src/stock_move_scout/judgement/
  commands.py
  display_contract.py
```

已承接：

```text
judgement 任务命令路由
Web 展示契约 display_contract
```

### feed

```text
src/stock_move_scout/feed/
  queries.py
```

已承接：

```text
Web feed SQL 查询
交易日期查询
实时领涨 / 稳定异动读取
```

### db

```text
src/stock_move_scout/db/
  sql.py
```

已承接：

```text
部分 SQL 辅助函数
```

## 3. 当前仍较重的脚本

```text
scripts/windowed_stock_scout_agent.py
  仍包含通达信扫描、窗口聚合、任务投递。

scripts/build_stock_move_judgements.py
  已调用 analysis / judgement 模块，但仍包含较多 SQL、评分、风险和写库逻辑。

scripts/stock_scout_web.py
  Web 服务、接口、前端 HTML/CSS/JS 仍集中在一个文件。

scripts/stock_scout_mysql.py
  MySQL 工具、表初始化、导入逻辑、部分领域函数仍集中。
```

## 4. 本阶段不继续拆的内容

按当前决策，先暂停继续拆代码，优先补齐文档和稳定边界。

暂不继续拆：

```text
analysis 锚点归因
analysis 区间强度
judgement 风险模型
judgement 持续性标签
web 展示组件
stock_scout_mysql.py
```

## 5. 下一阶段建议顺序

建议后续按这个顺序继续：

```text
1. 补单元测试或样例数据回放，锁住实时筛选、带动性输出。
2. 拆 judgement 的评分输入、风险模型、持续性标签。
3. 拆 analysis 的锚点归因和区间强度。
4. 拆 feed/web 的展示数据整形。
5. 拆 db 层，把 stock_scout_mysql.py 逐步瘦身。
```

## 6. 开发约束

后续新增功能时遵守：

```text
新增数据源：先放 sources。
新增调度任务：先放 scheduler/task_definitions.py。
新增实时分析：放 analysis。
新增证据摘要：放 evidence。
新增最终判断：放 judgement。
新增 Web 数据读取：放 feed。
新增 UI 展示：尽量读取 display_contract。
不要让文件重新成为流程状态源。
```
