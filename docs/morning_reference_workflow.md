# 早盘帖子 Workflow

## 定位

早盘帖子负责把盘前事实整理成适合雪球发布的短帖。它不是数据采集任务，也不是长期判断标准沉淀处；它只消费已经存在的事实、skill 结果和消息摘要，然后完成成稿、review、落盘。

当前入口仍保持兼容：

```powershell
python scripts\build_morning_reference_post.py --mysql-enabled --mysql-password <MYSQL_PASSWORD>
```

## 当前代码结构

```text
scripts/build_morning_reference_post.py
src/stock_move_scout/posts/morning/facts.py
src/stock_move_scout/posts/morning/artifacts.py
src/stock_move_scout/posts/morning/style_guide.py
```

职责边界：

```text
facts.py
  读取和整理事实，不写帖子语言，不调用 LLM，不写文件。

artifacts.py
  统一产物路径和写入规则，保证主帖、latest、镜像目录、workflow JSON 命名稳定。

style_guide.py
  沉淀用户词汇、雪球排版规则、机器味黑名单。

build_morning_reference_post.py
  当前仍是兼容入口，负责把 facts、prompt、draft、review 和 artifacts 串起来。
```

## 事实输入

早盘帖子主要消费四类事实：

```text
market_width_snapshots
  -> stock-market-acceleration-model
  -> 市场温度、全市场/Top50/研究池上涨比例、风格强弱、近5日龙头、加速度状态

kpl_plate_featured_details
  -> stock-top3-concept-new-high
  -> Top3 强分支、板块爆发原因、研究池内强支撑个股、核心逻辑

daily_market_themes
  -> 日内题材热度和消息锚点

morning_market_news
  -> 收盘至盘前外围和重要头条
```

没有 MySQL 时，脚本会 fallback 到：

```text
runs/data_tasks/daily_market_themes.json
runs/data_tasks/morning_market_news.json
```

## 输出产物

默认输出目录：

```text
runs/posts
output/morning_reference
```

主要文件：

```text
runs/posts/morning_reference_YYYY-MM-DD.txt
runs/posts/morning_reference_latest.txt
runs/posts/morning_reference_YYYY-MM-DD.json
runs/posts/morning_reference_YYYY-MM-DD.workflow/
runs/posts/morning_reference_YYYY-MM-DD.workflow.json
output/morning_reference/morning_reference_YYYY-MM-DD.txt
output/morning_reference/morning_reference_latest.txt
```

workflow 目录里会拆开保存：

```text
facts.json
market_judgement.json
strategy_view.json
market_acceleration.json
top3_concept.json
post_plan.json
review.json
draft_v*.md
final.md
payload.json
```

## 写作规则

早盘帖子当前遵循：

```text
开头：市场加速度模型给出市场温度和风格强弱
中段：结合 morning_market_news、daily_market_themes 和昨日收盘后摘要推演
机会/风险：只保留一个主判断
备选：使用 stock-top3-concept-new-high 给出强支撑个股和核心原因
结尾：今天盯什么 + 免责声明
```

用户核心词汇统一使用：

```text
修复延续：修复还在，但修复强度没有前一日强
继续修复：修复力度和昨日相当
加强修复：修复力度比昨日更强
分歧延续：分歧还在，但分歧力度比前一日弱
```

## 后续拆分

下一步继续从兼容脚本里拆出：

```text
judgement.py
  facts -> skill judgement

plan.py
  skill judgement -> post plan

draft.py
  post plan -> draft/fallback draft

review.py
  final review and rewrite rules
```

目标是让 `scripts/build_morning_reference_post.py` 最终只保留 CLI 参数解析和 workflow 调用。
