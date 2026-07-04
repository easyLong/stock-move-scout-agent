# Stock Move Scout 架构重构准则

## 目标

这个项目后续要同时承载数据采集、研究池、市场模型、选股 skill、帖子生成、页面展示和任务编排。重构的核心不是换目录名，而是让每一层只做自己的事：

```text
Data Facts -> Domain Rules -> Pipelines -> Skills/Agents -> Orchestration -> UI/Posts
```

## 分层边界

### 1. Data Facts

职责：采集、清洗、入库、读取事实。

目录：

```text
src/stock_move_scout/sources
src/stock_move_scout/db
src/stock_move_scout/calendar
```

原则：

- 不写策略判断。
- 不写帖子语言。
- 不调用 LLM。
- 输出应尽量是稳定表、稳定 JSON 或稳定 dataclass。

### 2. Domain Rules

职责：表达可复用的业务规则。

现有位置：

```text
src/stock_move_scout/research_pool.py
src/stock_move_scout/market_width.py
src/stock_move_scout/analysis
src/stock_move_scout/evidence
src/stock_move_scout/feed/leaderboard_snapshot.py
```

原则：

- 研究池、领头羊、市场宽度、有效事实、支撑度判断都属于 domain。
- domain 可以读写数据库，但不负责调度一整天流程。
- domain 函数应能被 CLI、scheduler、web、skill 复用。

### 3. Pipelines

职责：编排多个 domain/source 步骤，形成可复用流程。

目录：

```text
src/stock_move_scout/pipelines
```

原则：

- pipeline 只编排，不写采集细节。
- pipeline 输出 `PipelineResult / StepResult`。
- scheduler、CLI、临时回补都应调用 pipeline，而不是复制一串脚本命令。
- 一个 pipeline 应该能被手动执行，也能被定时任务执行。

已新增：

```text
src/stock_move_scout/pipelines/runner.py
src/stock_move_scout/pipelines/history_rebuild.py
scripts/rebuild_history_week.py
```

### 4. Skills / Agents

职责：

- Skill：沉淀判断标准和可复用分析方法。
- Agent：按目标执行流程，可以调用多个 skill 和 pipeline。

原则：

- skill 不直接关心页面怎么展示。
- skill 输入应是事实包或稳定查询结果。
- agent 不沉淀长期判断标准，判断标准回写到 skill。

### 5. Orchestration

职责：调度、依赖、状态、重试、产物位置。

现有位置：

```text
src/stock_move_scout/scheduler
scripts/stock_scout_task_scheduler.py
```

目标：

- scheduler 只决定“何时运行什么 pipeline”。
- 任务定义里不要拼太长命令。
- 重要任务要有健康检查和产物校验。

### 6. UI / Posts

职责：展示和表达。

现有位置：

```text
scripts/stock_scout_web.py
scripts/stock_scout_web_templates.py
scripts/build_morning_reference_post.py
```

目标：

- Web 拆成 router + query service + template/assets。
- 帖子生成拆成 facts -> judgement -> outline -> draft -> review -> final。
- 页面和帖子都只消费稳定 facts/skill 结果，不直接拼业务链路。

## 数据链路原则

每条数据链路都要能回答五个问题：

```text
1. 输入事实来自哪里？
2. 中间规则是什么？
3. 输出到哪张表/哪个文件？
4. 哪些页面/帖子/skill 消费它？
5. 如何重跑、如何校验？
```

建议每个重要 pipeline 都提供：

```text
run()
validate()
summary()
```

## 当前第一阶段落地

已把“过去一周重建”抽成 reusable pipeline：

```powershell
python scripts\rebuild_history_week.py --mysql-enabled --mysql-password <MYSQL_PASSWORD> --days 5
```

或通过统一 CLI：

```powershell
stock-move-scout rebuild-history --mysql-enabled --mysql-password <MYSQL_PASSWORD> --days 5
```

这个 pipeline 会顺序重建：

```text
research_pool
kpl_featured_sections
kpl_plate_strength
kpl_plate_details
research_pool_theme_members
leaderboard_snapshot
kpl_leaderboard_snapshot
```

## 当前第二阶段落地

已开始拆“早盘帖子”链路中最稳定的边界：

```text
src/stock_move_scout/posts
src/stock_move_scout/posts/morning
src/stock_move_scout/posts/morning/facts.py
src/stock_move_scout/posts/morning/artifacts.py
src/stock_move_scout/posts/morning/style_guide.py
```

当前边界：

```text
facts.py       -> 市场加速度、Top3 强分支、fallback JSON 读取
artifacts.py   -> 主帖、latest、镜像目录、workflow JSON 的路径和写入
style_guide.py -> 用户词汇、雪球排版规则、机器味黑名单
```

`scripts/build_morning_reference_post.py` 仍是兼容入口，但事实读取、产物写入和写作规范已经开始移出。详细说明见 `morning_reference_workflow.md`。

## 后续重构顺序

1. 拆 `build_morning_reference_post.py`

目标结构：

```text
src/stock_move_scout/posts/morning/facts.py
src/stock_move_scout/posts/morning/judgement.py
src/stock_move_scout/posts/morning/draft.py
src/stock_move_scout/posts/morning/review.py
```

2. 拆 Web

目标结构：

```text
src/stock_move_scout/web/app.py
src/stock_move_scout/web/routes/feed.py
src/stock_move_scout/web/routes/leaders.py
src/stock_move_scout/web/routes/market_width.py
src/stock_move_scout/web/routes/plate_breakouts.py
src/stock_move_scout/web/templates
```

3. 给双口径研究池补表维度

第一阶段已落地到页面依赖的结果表。口径边界如下：

- 市场概览、异动情报流：固定使用最全研究池，也就是 `bear/none`。
- 领头羊、爆发板：允许按牛市/熊市系统切换。

允许切换的结果表按同一组口径字段过滤：

```text
pool_mode
research_pool_ma_mode
```

已完成：

```text
kpl_plate_featured_details
kpl_stock_featured_sections
leaderboard_snapshots
```

仍待拆分：

```text
research_pool_snapshots
research_pool_items
```

当前策略是先保证页面结果表双口径并存；研究池主表仍保持可重建口径，生成 bull 快照后再恢复 bear 默认口径，避免一次性改动主链路主键。

4. 统一任务定义

目标是任务定义从“脚本命令”升级成：

```text
task_id
pipeline
params
dependencies
outputs
validator
```

这样调度层只管调度，pipeline 层管流程，domain 层管规则。
