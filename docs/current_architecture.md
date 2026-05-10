# 股票异动侦察 Agent 当前架构

## 1. 系统定位

`stock-move-scout-agent` 是一个盘中 A 股异动侦察系统。

它的目标不是单纯做涨幅榜，而是快速回答四个问题：

```text
为什么动？
强不强？
有没有带动同题材？
能不能持续？
```

当前架构以 MySQL 为唯一业务状态源。文件只允许作为日志、缓存、兼容输出或文档资产，不再作为流程状态源。

## 2. 总体链路

```text
通达信实时行情
  -> 15 秒扫描
  -> 实时异动筛选
  -> scan_runs / scan_movers
  -> 题材锚点归因
  -> scan_stock_roles
  -> 5 分钟窗口聚合
  -> windows / window_movers / window_stock_roles
  -> 异步证据采集与摘要
  -> async_evidence_summaries
  -> 异动判断
  -> stock_move_judgements
  -> Web AI 情报流
```

主链路要求快，证据链允许异步补全。

盘中最重要的实时结果来自两类数据：

```text
实时领涨：
  最新 scan Top1-5，用于观察当前最强异动。

稳定异动：
  每个 5 分钟窗口 Top5，用于观察持续出现的异动。
```

## 3. 分层架构

```text
sources      数据源定义和采集命令
scheduler    任务调度、入队、worker 抢锁
mysql        业务状态存储
analysis     实时筛选、同锚活动、主动性、带动性
evidence     证据 payload、模型摘要、证据存储
judgement    异动解释、持续性、风险、展示契约
feed/web     Web 数据查询和前端展示
```

### sources

目录：

```text
src/stock_move_scout/sources/
  definitions.py
  commands.py
```

职责：

```text
定义数据源属于 hot / warm / cold 哪一层
定义数据源对应的采集脚本
定义采集后写入哪些 MySQL 表
判断哪些任务需要按股票池分批入队
```

调度器不再直接硬编码数据源脚本参数。

### scheduler

目录：

```text
src/stock_move_scout/scheduler/
  task_definitions.py
```

脚本：

```text
scripts/stock_scout_task_scheduler.py
```

职责：

```text
每 3 秒扫描 scheduled_tasks
判断任务是否到期
写入 task_queue
worker 抢锁执行
记录 task_runs
维护 worker_heartbeats
```

调度器只负责“什么时候跑、谁来跑”，不负责业务判断。

### analysis

目录：

```text
src/stock_move_scout/analysis/
  commands.py
  realtime_filter.py
  activity.py
  influence.py
```

已拆出的职责：

```text
realtime_filter.py
  实时异动筛选：
  非 ST / 非退市
  涨速 >= 1%
  或 涨速 > 0.5% 且 15 秒成交增量 >= 3000 万

activity.py
  同锚活动索引：
  首发股票
  波次
  同锚第几个触发
  3/5/10 分钟扩散数量
  时间序列 Top3 + 本股
  活跃序列 Top3 + 本股

influence.py
  主动性评分
  带动性评分
  短线行为评分
```

仍待拆出的职责：

```text
锚点归因
区间强度
盘面确认
```

### evidence

目录：

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

职责：

```text
从数据库取证据候选股
构建模型 payload
读取 model_api_configs
调用 OpenAI-compatible 模型
做 fallback 事实卡
计算 evidence_hash
写入 async_evidence_summaries
维护 evidence_source_fingerprints / evidence_analysis_dirty_queue
```

证据层的目标是把原始材料压缩成短线真正有用的事实：

```text
硬催化：订单、合同、业绩、重组、增减持、产业政策
题材正宗性：公司到底靠什么业务参与题材
时效性：今天、昨日盘后、近期、陈旧
持续性依据：区间强度、板块地位、是否带动扩散
最大瑕疵：证据缺口、锚点不一致、核心票走弱
```

### judgement

目录：

```text
src/stock_move_scout/judgement/
  commands.py
  display_contract.py
```

脚本：

```text
scripts/build_stock_move_judgements.py
```

职责：

```text
读取实时/窗口事件
读取锚点上下文
读取异步证据摘要
调用 analysis 的主动性和带动性逻辑
综合评分
生成 stock_move_judgements
生成 Web 展示契约 display_contract
```

当前判断维度：

```text
异动原因
硬催化强度
题材锚点一致性
区间强度
主动性
带动性
盘面确认
持续性
风险扣分
```

### feed / web

目录：

```text
src/stock_move_scout/feed/
  queries.py
```

脚本：

```text
scripts/stock_scout_web.py
```

职责：

```text
读取最新交易日
读取实时领涨
读取稳定异动
读取 stock_move_judgements
读取 display_contract
输出 Web AI 情报流
```

Web 展示原则：

```text
左侧：一眼扫完整批情报
右侧：只展示当前股票的关键证据详情
长证据折叠
推论要有事实原因
不要把公告原文大段堆到页面上
```

## 4. 数据源分层

### 热数据

盘中高频使用，必须快。

```text
通达信：
  涨幅
  涨速
  成交额
  15 秒成交增量
  scan_runs / scan_movers

题材实时角色：
  active_market_anchors
  active_market_anchor_relations
  scan_stock_roles
  anchor_realtime_role_snapshots
  anchor_realtime_role_members
```

### 温数据

盘中或每日低频更新，用于增强判断。

```text
财联社 / 华尔街见闻：
  market_news_items
  daily_market_themes

问财区间强势榜：
  stock_period_rankings

东方财富龙虎榜：
  stock_lhb_seat_evidence

异步证据摘要：
  async_evidence_summaries
```

### 冷数据

初始化一次，后续按需或每日低频更新。

```text
同花顺个股根页面：
  stock_company_profiles
  stock_ths_root_items

同花顺题材解释：
  ths_stock_concept_explanations
  stock_theme_reason_bank

同花顺热点概念 / 今日炒什么：
  ths_hot_concept_events
  ths_hot_concept_members

历史活跃锚点：
  active_market_anchors
  active_market_anchor_members
  active_market_anchor_relations
```

## 5. 核心数据表

### 行情扫描

```text
scan_runs
  每次 15 秒扫描的一次运行记录。

scan_movers
  单次扫描中命中的异动股票。

scan_stock_roles
  每次扫描中股票对应的锚点、角色、领涨/中军等信息。

scan_anchor_stats
  扫描级锚点统计。
```

### 窗口聚合

```text
windows
  5 分钟窗口。

window_scans
  窗口包含哪些 scan_runs。

window_movers
  窗口内稳定异动股票。

window_stock_roles
  窗口级股票角色。

window_sector_stats
  窗口级板块统计。
```

### 题材锚点

```text
stock_theme_reason_bank
  个股题材理由库。用于回答“这只票为什么属于这个题材”。

active_market_anchors
  近期活跃题材锚点。

active_market_anchor_members
  活跃锚点下的成分股。

active_market_anchor_relations
  锚点扩展关系、同义关系、行业/概念关联。

active_anchor_match_candidates
  个股匹配锚点的候选结果。
```

### 公司画像与冷数据

```text
stock_company_profiles
  code、stock_name、公司亮点、AI 提取的经营计划等公司画像核心字段。

stock_ths_root_items
  同花顺根页面扩展条目，如近期事件、新闻公告、题材要点。
```

### 异步证据

```text
community_posts
  社区帖子原始内容。

community_evidence
  社区证据结果。

official_evidence
  官网、公告、披露类证据。

market_news_items
  财联社、华尔街见闻等资讯。

stock_lhb_seat_evidence
  龙虎榜游资、机构、股通证据。

async_evidence_summaries
  模型或 fallback 生成的证据事实卡。
```

### 判断输出

```text
stock_move_judgements
  最终异动判断。
  Web 主要读取这里的 display_contract。
```

### 调度运行

```text
scheduled_tasks
  任务定义和周期。

task_queue
  到期任务队列。

task_runs
  任务执行记录。

worker_heartbeats
  worker 心跳。

pipeline_events
  流程事件日志。
```

### 模型配置

```text
model_api_configs
  API Key、base_url、model、temperature、max_output_tokens 等模型参数。
```

## 6. 锚点体系数据走向

```text
同花顺热点概念 / 今日炒什么 / 个股概念解释
  -> stock_theme_reason_bank
  -> active_market_anchors
  -> active_market_anchor_members
  -> active_market_anchor_relations
  -> 扫描时为个股选择 primary_anchor
  -> scan_stock_roles / window_stock_roles
  -> judgement 读取 primary_anchor 做解释和带动性分析
```

锚点选择原则：

```text
优先强命中
今天出现优先于近两周历史出现
优先具体题材，避免大概念乱抢锚点
优先有个股理由的题材
可以利用行业/细分行业作为辅助，但不应压过明确题材理由
```

## 7. 带动性设计

带动性不再只看本股是否涨得高，而是看它在同锚点里的时间位置和扩散效果。

输入：

```text
scan_movers
scan_stock_roles.primary_anchor_name
stock_period_rankings
```

核心指标：

```text
同锚第几个触发
是否同锚首发
晚于同锚首发多久
首次触发时涨速排名
首次触发时 15 秒成交增量
启动前 10 分钟同锚是否已经很热
启动后 3/5/10 分钟同锚新增异动数量
时间序列 Top3 + 本股
活跃序列 Top3 + 本股
首发股票是否在强势榜
```

输出：

```text
主动性：强 / 中 / 弱
带动性：疑似带动强 / 疑似带动中 / 同锚扩散 / 弱
短线行为分：用于最终 score
```

## 8. 异步证据链

```text
窗口候选股 / 实时候选股
  -> evidence_candidates 或 judgement 候选
  -> 证据采集任务
  -> 原始证据表
  -> evidence_source_fingerprints 判断是否有新证据
  -> evidence_analysis_dirty_queue
  -> summarize_async_evidence.py
  -> async_evidence_summaries
  -> build_stock_move_judgements.py
  -> stock_move_judgements.display_contract
  -> Web 右侧证据详情
```

模型摘要要先过滤无效信息，再提取核心事实。

有效短线证据优先级：

```text
新鲜硬信息：合同、订单、业绩、重组、增减持、监管许可、产业政策
题材正宗性：主营、产品、客户、项目是否直接对应题材
资金确认：知名游资、机构、股通
区间强度：近 3/5/10 日在同锚内排名靠前
带动性：启动后同锚是否扩散
风险：锚点不一致、硬证据不足、同锚核心票走弱
```

## 9. 调度任务

当前任务分层：

```text
hot
  anchor_realtime_roles
  stock_move_judgements

warm
  morning_market_news
  daily_market_themes
  iwencai_period_rankings
  lhb_seat_evidence
  async_evidence_source_sync
  async_evidence_summary

cold
  cold_company_profile
  ths_root_extended_items
  ths_hot_concepts
  ths_stock_concepts
  stock_theme_reason_bank
```

调度原则：

```text
热任务不能被冷任务阻塞
冷任务需要分批
模型摘要可以异步慢慢补
主扫描链路优先保证快速出结果
任务参数归属到对应业务层 commands.py
```

## 10. 入口命令

包入口：

```powershell
stock-move-scout <command> [args...]
```

常用命令：

```powershell
stock-move-scout web --mysql-enabled --mysql-password <MYSQL_PASSWORD>
stock-move-scout scheduler --mysql-enabled --mysql-password <MYSQL_PASSWORD>
stock-move-scout worker --worker-types hot,warm --mysql-enabled --mysql-password <MYSQL_PASSWORD>
stock-move-scout scan-window --once --mysql-primary --mysql-enabled --mysql-password <MYSQL_PASSWORD> --no-file-output
stock-move-scout judgements --trade-date 2026-05-08 --latest-only --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

兼容脚本入口仍保留在 `scripts/`。

## 11. 当前代码边界

已经迁入 package 的模块：

```text
src/stock_move_scout/sources/
  definitions.py
  commands.py

src/stock_move_scout/scheduler/
  task_definitions.py

src/stock_move_scout/analysis/
  commands.py
  realtime_filter.py
  activity.py
  influence.py

src/stock_move_scout/evidence/
  commands.py
  model_config.py
  model_client.py
  schema.py
  storage.py
  summary.py
  payload.py

src/stock_move_scout/judgement/
  commands.py
  display_contract.py

src/stock_move_scout/feed/
  queries.py

src/stock_move_scout/db/
  sql.py
```

仍较重的脚本：

```text
scripts/windowed_stock_scout_agent.py
  扫描、窗口聚合、任务投递仍集中在一起。

scripts/build_stock_move_judgements.py
  SQL 取数、评分、风险、写库仍有较多逻辑。

scripts/stock_scout_web.py
  Web 服务和部分展示整形仍在同一文件。

scripts/stock_scout_mysql.py
  MySQL 工具和部分领域函数仍混在一起。
```

## 12. 后续重构原则

```text
不要继续把业务逻辑加到 scripts 大文件里。
新增采集源先登记到 sources。
新增调度任务先登记到 scheduler/task_definitions.py。
新增分析算法放 analysis。
新增证据摘要和模型 payload 放 evidence。
新增最终判断字段放 judgement。
Web 优先读取 display_contract，不要重新推理业务结论。
数据库是业务状态源，文件不是。
```
