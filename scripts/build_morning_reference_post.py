#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
import os
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string
from stock_move_scout.calendar import previous_trade_close_window
from stock_move_scout.evidence.model_config import (
    ensure_model_config_table,
    read_openai_env_file,
    resolve_api_key,
    resolve_model_runtime_config,
)
from stock_move_scout.sources.market_news import read_market_news_window
from stock_move_scout.sources.market_themes import read_market_themes


SYSTEM_PROMPT = (
    "你是一个每天盘前写雪球短帖的A股短线交易者。"
    "你的任务是基于输入的盘前消息和主题雷达，写一篇像真人复盘笔记的早盘策略帖。"
    "只允许基于输入材料归纳，不要编造股票、消息、数据或政策细节，不给买卖建议。"
    "文字要有人味：有轻重、有迟疑、有自己的盘前观察，但不要油腻、不要煽动。"
)


USER_PROMPT = """请基于下面 JSON 生成一篇适合发布到雪球的中文早盘策略帖。

用户写法习惯：
- 先定盘面温度，再抛一个盘中要验证的问题，最后围绕“谁分歧、谁修复、资金会不会接”来写策略。
- 标题要像真实盘前判断：前半句是行情概况，后半句直接给出操作策略，不要使用疑问句。
- 策略不是简单看多/看空，而是条件推演：强修复怎么办、修复延续怎么办、分歧延续怎么办、修复很差怎么办、资金切换怎么办。
- 条件推演要结合昨晚外围消息：先说外围对今天开盘预期的影响，再落回A股自己能不能承接、资金会不会换方向。
- 如果昨晚外围消息不好，而上一交易日A股主线刚刚强修复，推演重点应是“主线分歧能不能接住”，不要轻易发散到油气、军工、防守等非主流方向。
- 推演必须从事实组合出发，不要凭空发散。先提炼 2-3 个最关键事实，再写这些事实叠加后最可能带来的盘面影响。例如“昨天科技权重缩量抢修复 + 昨晚外围科技表现不佳”，推演重点应是今天科技大概率先受压，核心看分歧能不能被资金接住。
- 事实组合是内部思考方法，正文不要显式写“有两个事实要放在一起看”“两个因素叠加”“最合理的推演”“这个事实推出来”等解释推理过程的话，直接写结论和盘中看法。
- 正文里要有一个“今日最佳策略”判断：事实组合偏机会时写“今日最佳入手机会”，事实组合偏压力时写“今日风险点”。它必须来自前文事实推演，不要凭空给操作口号。
- 如果核心涨价逻辑或主线逻辑没有被证伪，只是被昨晚外围拖累杀跌，优先把“核心涨价个股/核心主线品种被动杀跌后的承接低吸”写成今日最佳入手机会；风险点只写在承接完全失败时。
- 重视分歧里的买点，修复太顺不急着追，重点看分歧时有没有承接、承接后有没有扩散。
- 消息只服务于交易判断，不平均罗列新闻；每条主线最多保留 1-2 个关键原因，重点写盘中怎么观察。
- 语气像个人盘前复盘：短句、有判断、带一点迟疑，不装确定性，也不要写成研报或模型摘要。

写作要求：
1. 面向雪球阅读，整体控制在 700-1100 个中文字符，最多不要超过 1200 字。
2. 首行必须是帖子标题，格式为：【日期盘前：行情概况 + 直接策略】。标题先写清楚盘面状态，后半句直接给出操作策略，不要使用疑问句，不要只喊口号，也不要写成命令句。例子：【6月2日盘前：科技权重分歧延续，修复先看承接】、【6月3日盘前：情绪小票回暖，买点后置到分歧】、【6月4日盘前：权重修复不强，资金切换先看试错】。
3. 第一段必须先交代上一交易日市场温度和情绪周期：涨跌家数、跌超5%、跌停压力、量能、科技/主线强弱，以及当前更像普涨、修复、分歧、退潮、分歧延续还是情绪回暖。
4. 第二段再给今天的总判断，可以写“今天先看……”“这里不能急着下结论……”这类自然表达，但不要写“人话结论就是”。
5. 今日总判断和后面的推演必须参考昨晚外围消息，尤其是美股科技、纳指、半导体、原油、黄金、汇率、地缘风险等对A股开盘预期的影响；但不能停在外围消息本身，必须回到A股盘中承接、分歧质量和资金切换。
6. 每一段策略推演都要能回答“这个判断来自哪几个事实的组合”。如果只有孤立消息，不能强行推演成方向；如果事实之间不能形成合力，就写成观察，不写成重点。
7. 正文不要出现“有几个事实”“推演如下”“因素叠加”“最合理的推演”“可以得出”这类说明推理过程的机器味表达，直接写交易结论。
8. 正文不要写成清单感很强的模板，优先用短段落，每段 1-2 句，段与段之间留空行。
9. 雪球排版要像短帖，不要写“一、二、三、四、五、六”这种文章提纲，也不要写“第一、第二、第三”这种教案式编号。
10. 前半段自然写：市场温度+情绪周期；今日总判断；主线和后手观察。中间不要单独起“上一交易日市场温度”“今日总判断”“主线展开”这些标题。
11. 只允许两个固定栏目标题：“今日最佳入手机会”或“今日风险点”，以及“今天盯什么”。这两个标题单独成行，标题后不要加冒号，不要用【】包住；只有首行帖子标题可以用【】。
12. “今日最佳入手机会”或“今日风险点”下面用 1 个短段落说明，不要同时写两者；如果事实组合偏压力，优先写风险点，如果事实组合偏承接机会，优先写入手机会。
13. “今天盯什么”必须单独一行，下面分 3-4 行写，每行只写一个观察点，不要编号，不要解释成两行。
14. 不要每段都写“依据/看点/映射方向/盘中验证点”，这些词会显得机器味很重。
15. 每条主线只保留最关键的 1-2 个消息原因，再写你会怎么观察盘面。
16. 不要平均罗列新闻，不要把每条消息都展开；只保留对今天交易情绪最有用的信息。
17. 只写输入中明确存在的主题、概念、消息，不要臆造具体个股；除非输入明确显示已经成为当日主流，否则不要把油气、军工、黄金、防守等非主流方向写成重点推演。
18. 语气像个人盘前复盘，短句、清楚、有判断力，可以有一点“不确定性”，不要绝对化、不要喊口号。
19. 避免 Markdown 表格、代码块、粗体符号、过多小标题；输出纯正文，不要 JSON。
20. 末尾必须包含“仅为个人盘前复盘，不构成投资建议。”
21. 如果输入 JSON 里有 kpl_tomorrow_fry（开盘啦精选板块强度快照，类似“明日炒什么”），把它当作盘面指引使用：只说板块名（不要编造个股），优先挑 2-4 个最值得盯的板块，并写清楚盘中怎么观察承接/扩散，不要把数字堆成清单。

核心词汇必须按下面含义使用：
- “修复延续”：修复还在，但修复强度没有前一日强，不等于强修复或重新主升。
- “继续修复”：修复力度和昨日相当。
- “加强修复”：修复力度比昨日更强。
- “分歧延续”：分歧还在，但分歧力度比前一日弱。
- 如果表达“分歧比前一日弱了”，统一写“分歧延续”，不要写“分歧减缓”。
- 如果表达“修复和前一日差不多”，优先写“继续修复”，不要写“修复延续”。
- 如果表达“修复比前一日更强”，优先写“加强修复”，不要写“修复延续”。

二八行情按轮动理解：
- 二八行情不是简单看强弱，而是看权重和情绪票之间的轮动。
- 谁在分歧延续，就优先观察谁的承接机会。
- 情绪票分歧延续了，就看情绪票；科技权重分歧延续了，就看科技权重。
- 不要把二八行情机械写成“只看权重”或“只看情绪”，要写清楚当前是哪一边在分歧延续、哪一边在修复。

尽量避免这些机器感表达：
- “映射方向包括”
- “消息密度最高”
- “风险偏好形成压制”
- “持续性取决于”
- “总体思路”
- “核心不是单一利好”
- “盘中验证点”
- “第一条 / 第二条 / 第三条”

输入 JSON：
{payload_json}
"""


MARKET_CONTEXT_PROMPT = """
补充硬性要求：
如果输入 JSON 里有 previous_market_context，第一段必须先交代上一交易日 A 股市场温度和情绪周期。
市场温度包括：涨跌家数、跌超5%家数、跌停压力、量能、权重/题材谁强谁弱。
情绪周期要用交易者能听懂的话判断：例如情绪普涨、修复延续、继续修复、加强修复、分歧延续、分歧加大、退潮、弱修复、强分歧后的反抽等。
核心词汇按用户语境使用：“修复延续”表示修复还在但强度没有前一日强；“继续修复”表示修复力度和昨日相当；“加强修复”表示修复力度比昨日更强；“分歧延续”表示分歧还在但力度比前一日弱。不要使用“分歧减缓”。
二八行情按轮动理解：权重和情绪票会轮流分歧、轮流修复。谁在分歧延续，就优先观察谁的承接机会；情绪票分歧延续看情绪票，科技权重分歧延续看科技权重。
推演过程必须结合昨晚外围消息：外围强弱用于判断今天高开、低开、冲高或恐慌的预期，但最后要落到A股自己的承接、量能、扩散和资金切换，不能只复述外围新闻。
如果 previous_market_context 显示上一交易日下跌家数多、跌超5%家数多、跌停压力明显，
今天必须先按“修复盘/分歧承接”来写，不能因为外盘 AI 或美股科技强，就直接写成纯进攻。
所有强主题都要落回一句话：它能不能修复昨天弱盘，并吸引真实跟随。
"""


DISCLAIMER = "仅为个人盘前复盘，不构成投资建议。"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def responses_url(base_url: str) -> str:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/responses"):
        return base
    return f"{base}/responses"


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"].strip()
    for output in response.get("output", []) or []:
        for content in output.get("content", []) or []:
            if isinstance(content.get("text"), str):
                return content["text"].strip()
    return ""


def call_morning_reference_model(
    payload: dict[str, Any],
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
) -> str:
    if not api_key and (base_url or "").rstrip("/") == "https://api.openai.com/v1":
        raise RuntimeError("OPENAI_API_KEY is not set")
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    USER_PROMPT.format(payload_json=json.dumps(payload, ensure_ascii=False, indent=2))
                    + "\n\n"
                    + MARKET_CONTEXT_PROMPT
                ),
            },
        ],
    }
    request = urllib.request.Request(
        responses_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:1200]}") from exc
    text = extract_response_text(data)
    if not text:
        raise RuntimeError("model returned empty text")
    return text.strip() + "\n"


def call_model_text(
    *,
    system: str,
    user: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
) -> str:
    if not api_key and (base_url or "").rstrip("/") == "https://api.openai.com/v1":
        raise RuntimeError("OPENAI_API_KEY is not set")
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    request = urllib.request.Request(
        responses_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:1200]}") from exc
    text = extract_response_text(data)
    if not text:
        raise RuntimeError("model returned empty text")
    return text.strip()


def decode_json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [compact(item, 160) for item in value if compact(item, 160)]
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [compact(item, 160) for item in parsed if compact(item, 160)]
    except Exception:
        pass
    return [compact(part, 160) for part in re.split(r"[|;；]\s*", text) if compact(part, 160)]


def known_trade_dates(config: Any, trade_date: date) -> list[str]:
    sql = f"""
    SELECT DISTINCT DATE(scanned_at) AS trade_day
    FROM scan_runs
    WHERE accepted=1
      AND DATE(scanned_at) < {sql_string(trade_date.isoformat())}
    ORDER BY trade_day DESC
    LIMIT 30;
    """
    try:
        return [row[0] for row in mysql_rows(run_mysql(config, sql, batch=True)) if row and row[0]]
    except Exception:
        return []


def resolve_window(args: argparse.Namespace, config: Any | None) -> tuple[date, datetime, datetime]:
    end = datetime.now()
    if args.until:
        end = datetime.strptime(args.until, "%Y-%m-%d %H:%M:%S")
    trade_day = end.date()
    if args.trade_date:
        trade_day = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d %H:%M:%S")
    else:
        _, start, _ = previous_trade_close_window(
            datetime.combine(trade_day, end.time()),
            after_close_hour=args.after_close_hour,
            known_trade_dates=known_trade_dates(config, trade_day) if config else None,
        )
    return trade_day, start, end


def read_themes_mysql(config: Any, trade_day: date, limit: int) -> list[dict[str, Any]]:
    return read_market_themes(config, trade_day, limit)


def read_news_mysql(config: Any, start: datetime, end: datetime, min_importance: int, limit: int) -> list[dict[str, Any]]:
    return read_market_news_window(config, start, end, min_importance=min_importance, limit=limit)


def to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def read_previous_ths_after_close_summary(config: Any, trade_day: date) -> dict[str, Any]:
    sql = f"""
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      title,
      summary,
      LEFT(COALESCE(content, ''), 3000),
      url,
      DATE_FORMAT(published_at, '%Y-%m-%d %H:%i:%s'),
      source_status,
      DATE_FORMAT(collected_at, '%Y-%m-%d %H:%i:%s')
    FROM ths_market_after_close_summaries
    WHERE trade_date = (
      SELECT MAX(trade_date)
      FROM ths_market_after_close_summaries
      WHERE trade_date < {sql_string(trade_day.isoformat())}
        AND source_status = 'ok'
    )
      AND source_status = 'ok'
    ORDER BY published_at DESC, collected_at DESC
    LIMIT 1;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return {}
    if not rows or len(rows[0]) < 8:
        return {}
    row = rows[0]
    return {
        "source": "ths_market_after_close_summaries",
        "trade_date": row[0],
        "title": row[1],
        "summary": row[2],
        "content_excerpt": row[3],
        "url": row[4],
        "published_at": row[5],
        "source_status": row[6],
        "collected_at": row[7],
    }


def read_previous_market_context(config: Any, trade_day: date) -> dict[str, Any]:
    sql = f"""
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
      total_count, up_count, down_count, flat_count, up3_count, down3_count,
      up5_count, down5_count,
      limit_up_count, limit_down_count,
      amount_top50_count, amount_top50_up_count, amount_top50_down_count,
      research_pool_count,
      research_pool_up_count,
      research_pool_down_count
    FROM market_width_snapshots
    WHERE trade_date = (
      SELECT MAX(trade_date)
      FROM market_width_snapshots
      WHERE trade_date < {sql_string(trade_day.isoformat())}
    )
      AND ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00')
        OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00'))
    ORDER BY captured_at DESC
    LIMIT 1;

    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      COUNT(*),
      ROUND(AVG(up_count), 0),
      ROUND(AVG(down_count), 0),
      MAX(down_count),
      ROUND(AVG(down5_count), 0),
      MAX(down5_count),
      MAX(limit_down_count),
      MAX(limit_up_count)
    FROM market_width_snapshots
    WHERE trade_date = (
      SELECT MAX(trade_date)
      FROM market_width_snapshots
      WHERE trade_date < {sql_string(trade_day.isoformat())}
    )
      AND ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00')
        OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00'))
    GROUP BY trade_date;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    latest = rows[0] if rows else []
    stats = rows[1] if len(rows) > 1 else []
    if len(latest) < 18:
        ths_summary = read_previous_ths_after_close_summary(config, trade_day)
        return {"source": "ths_market_after_close_summaries", "ths_after_close_summary": ths_summary} if ths_summary else {}
    total = max(1, to_int(latest[2]))
    down_count = to_int(latest[4])
    down5_count = to_int(latest[9])
    limit_down_count = to_int(latest[11])
    up_count = to_int(latest[3])
    down_ratio = down_count / total
    down5_ratio = down5_count / total
    if up_count > down_count * 1.5:
        if down5_ratio >= 0.03 or limit_down_count >= 10:
            temperature = "上一交易日市场明显修复，但跌超5%和跌停压力没有完全清掉"
        else:
            temperature = "上一交易日市场偏强，今天关注修复延续和分化"
    elif down_ratio >= 0.72 or down5_ratio >= 0.08 or limit_down_count >= 10:
        temperature = "上一交易日市场明显偏弱/大跌，今天先按修复盘和分歧承接处理"
    elif down_ratio >= 0.6 or down5_ratio >= 0.03:
        temperature = "上一交易日市场偏弱，今天需要先看修复力度"
    elif up_count > down_count:
        temperature = "上一交易日市场偏强，今天关注延续性"
    else:
        temperature = "上一交易日市场中性偏分歧"
    context = {
        "source": "market_width_snapshots",
        "trade_date": latest[0],
        "latest_snapshot_at": latest[1],
        "temperature": temperature,
        "latest": {
            "total_count": total,
            "up_count": to_int(latest[3]),
            "down_count": down_count,
            "flat_count": to_int(latest[5]),
            "up5_count": to_int(latest[8]),
            "down5_count": down5_count,
            "limit_up_count": to_int(latest[10]),
            "limit_down_count": limit_down_count,
            "amount_top50_count": to_int(latest[12]),
            "amount_top50_up_count": to_int(latest[13]),
            "amount_top50_down_count": to_int(latest[14]),
            "research_pool_count": to_int(latest[15]),
            "research_pool_up_count": to_int(latest[16]),
            "research_pool_down_count": to_int(latest[17]),
        },
        "session_stats": {
            "snapshot_count": to_int(stats[1]) if len(stats) > 1 else 0,
            "avg_up_count": to_int(stats[2]) if len(stats) > 2 else 0,
            "avg_down_count": to_int(stats[3]) if len(stats) > 3 else 0,
            "max_down_count": to_int(stats[4]) if len(stats) > 4 else 0,
            "avg_down5_count": to_int(stats[5]) if len(stats) > 5 else 0,
            "max_down5_count": to_int(stats[6]) if len(stats) > 6 else 0,
            "max_limit_down_count": to_int(stats[7]) if len(stats) > 7 else 0,
            "max_limit_up_count": to_int(stats[8]) if len(stats) > 8 else 0,
        },
    }
    ths_summary = read_previous_ths_after_close_summary(config, trade_day)
    if ths_summary:
        context["source"] = "market_width_snapshots+ths_market_after_close_summaries"
        context["ths_after_close_summary"] = ths_summary
    return context


def read_previous_kpl_tomorrow_fry(config: Any, trade_day: date, limit: int = 8) -> dict[str, Any]:
    """Read KPL featured-plate strength snapshot as a 'tomorrow watchlist' hint.

    KPL plate strength is post-close friendly and maps well to "明日炒什么" style browsing.
    """
    if config is None:
        return {}
    day = trade_day.isoformat()
    sql = f"""
    WITH prev_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM kpl_plate_featured_strengths
      WHERE trade_date < {sql_string(day)}
    ),
    picked AS (
      SELECT captured_at
      FROM (
        SELECT
          p.captured_at,
          COUNT(*) AS row_count,
          SUM(
            IF(
              COALESCE(p.plate_name, '') <> ''
              AND COALESCE(p.plate_name, '') NOT LIKE '%ST%'
              AND COALESCE(p.plate_name, '') NOT LIKE '%退市%',
              1,
              0
            )
          ) AS non_st_count
        FROM kpl_plate_featured_strengths p
        JOIN prev_day d ON d.trade_date=p.trade_date
        GROUP BY p.captured_at
      ) s
      ORDER BY IF(non_st_count >= 8, 0, 1) ASC, captured_at DESC
      LIMIT 1
    )
    SELECT
      DATE_FORMAT(p.trade_date, '%Y-%m-%d') AS trade_date,
      DATE_FORMAT(p.captured_at, '%Y-%m-%d %H:%i:%s') AS captured_at,
      p.row_rank,
      p.plate_code,
      p.plate_name,
      p.strength,
      p.change_pct,
      p.speed,
      p.amount
    FROM kpl_plate_featured_strengths p
    JOIN prev_day d ON d.trade_date=p.trade_date
    JOIN picked s ON s.captured_at=p.captured_at
    WHERE COALESCE(p.plate_name, '') <> ''
      AND COALESCE(p.plate_name, '') NOT LIKE '%ST%'
      AND COALESCE(p.plate_name, '') NOT LIKE '%退市%'
    ORDER BY p.row_rank ASC
    LIMIT {max(1, int(limit))};
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return {}
    if not rows:
        return {}
    plates: list[dict[str, Any]] = []
    trade_date = str(rows[0][0] or "").strip()
    captured_at = str(rows[0][1] or "").strip()
    for row in rows:
        if len(row) < 9:
            continue
        plates.append(
            {
                "rank": to_int(row[2]),
                "plate_code": str(row[3] or "").strip(),
                "plate_name": str(row[4] or "").strip(),
                "strength": row[5],
                "change_pct": row[6],
                "speed": row[7],
                "amount": row[8],
            }
        )
    plates = [p for p in plates if p.get("plate_code") and p.get("plate_name")]
    if not plates:
        return {}
    return {
        "source": "kpl_plate_featured_strengths",
        "trade_date": trade_date,
        "captured_at": captured_at,
        "plates": plates,
    }


def read_json_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def read_fallback(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    theme_payload = read_json_payload(root / "runs" / "data_tasks" / "daily_market_themes.json")
    news_payload = read_json_payload(root / "runs" / "data_tasks" / "morning_market_news.json")
    themes = theme_payload.get("rows") if isinstance(theme_payload.get("rows"), list) else []
    news = news_payload.get("rows") if isinstance(news_payload.get("rows"), list) else []
    return themes, news


def format_day(trade_day: date) -> str:
    return f"{trade_day.month}月{trade_day.day}日"


def theme_name(theme: dict[str, Any]) -> str:
    return compact(theme.get("theme_name"), 40)


def theme_titles(theme: dict[str, Any], limit: int = 2) -> list[str]:
    titles = decode_json_list(theme.get("source_titles"))
    if not titles and theme.get("summary"):
        titles = [compact(theme.get("summary"), 140)]
    return titles[:limit]


def xueqiu_title(trade_day: date, themes: list[dict[str, Any]]) -> str:
    first = theme_name(themes[0]) if themes else ""
    if first:
        return f"【{format_day(trade_day)}盘前：{first}还在修复，买点后置看承接】"
    return f"【{format_day(trade_day)}盘前：盘面还在选方向，先看分歧承接】"


def split_watch_line(line: str) -> list[str]:
    match = re.match(r"^(今天(?:主要)?盯什么[:：])\s*(.+)$", line)
    if not match:
        return [line]
    head = match.group(1)
    rest = match.group(2).strip()
    parts = [part.strip(" ；;") for part in re.split(r"[；;]\s*(?=[一二三四五]看)", rest) if part.strip(" ；;")]
    if len(parts) <= 1:
        return [line]
    return [head, *parts]


def split_long_line(line: str, limit: int = 170) -> list[str]:
    if len(line) <= limit or line.startswith("【"):
        return [line]
    sentences = [part.strip() for part in re.split(r"(?<=[。！？])", line) if part.strip()]
    if len(sentences) <= 1:
        return [line]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > limit:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


XUEQIU_KEEP_HEADINGS = {"今日最佳入手机会", "今日风险点", "今天盯什么"}
XUEQIU_DROP_HEADINGS = {
    "上一交易日市场温度+情绪周期",
    "上一交易日市场温度",
    "市场温度+情绪周期",
    "市场温度",
    "情绪周期",
    "今日总判断",
    "总判断",
    "主线展开",
    "最强主线",
    "次强主线",
    "后手观察",
    "免责声明",
}


def normalize_xueqiu_line(line: str) -> str | None:
    clean = line.strip()
    clean = re.sub(r"^[一二三四五六七八九十]+[、.．]\s*", "", clean)
    clean = re.sub(r"^\d+[、.．)]\s*", "", clean)
    clean = clean.strip()
    if re.fullmatch(r"【(?:风险点|结论|条件)】", clean):
        return None
    bracket_heading = re.fullmatch(r"【(今日最佳入手机会|今日风险点|今天盯什么)】", clean)
    if bracket_heading:
        return bracket_heading.group(1)
    heading = clean.rstrip(":：").strip()
    if heading in XUEQIU_KEEP_HEADINGS:
        return heading
    if heading in XUEQIU_DROP_HEADINGS:
        return None
    if "不构成" in clean and "投资建议" in clean and clean != DISCLAIMER:
        return None
    clean = re.sub(r"^(?:结论先放前面|条件也很清楚|结论|条件)[:：]\s*", "", clean)
    clean = re.sub(r"^第[一二三四五六七八九十]+[，、]\s*", "", clean)
    return clean


def clean_watch_item(line: str) -> str:
    clean = re.sub(r"^\d+[、.．)]\s*", "", line.strip())
    clean = re.sub(r"^第[一二三四五六七八九十]+[，、]\s*", "", clean)
    if clean.startswith("盯"):
        clean = "看" + clean[1:]
    return clean


def split_inline_watch_items(line: str) -> list[str]:
    parts = [part.strip(" ，,；;") for part in re.split(r"[；;]\s*[,，]?\s*", line) if part.strip(" ，,；;")]
    if len(parts) <= 1 and len(line) > 40 and "，" in line:
        parts = [
            part.strip(" ，,")
            for part in re.split(r"，(?=(?:有色金属|并购重组|商业航天|通信|芯片|元器件|机器人|算力|科技权重|跌超5%|跌停|量能|证券))", line)
            if part.strip(" ，,")
        ]
    if len(parts) <= 1:
        return [line]
    return [clean_watch_item(part) for part in parts]


def limit_watch_items(items: list[str]) -> list[str]:
    cleaned = [item for item in items if item]
    if len(cleaned) <= 4:
        return cleaned
    tail = "，".join(item.rstrip("。") for item in cleaned[3:])
    return [*cleaned[:3], tail + "。"]


def strategy_watch_items(strategy: dict[str, Any]) -> list[str]:
    raw_items = strategy.get("watch_points") if isinstance(strategy.get("watch_points"), list) else []
    items: list[str] = []
    for raw in raw_items:
        item = clean_watch_item(str(raw)).strip(" ，,；;。")
        if item and item not in items:
            items.append(item)
    return limit_watch_items(items)[:4]


def enforce_strategy_watch_block(content: str, strategy: dict[str, Any]) -> str:
    items = strategy_watch_items(strategy)
    if len(items) < 3:
        return content

    body = (content or "").strip()
    replacement = "今天盯什么\n" + "\n".join(items)
    disclaimer = DISCLAIMER if DISCLAIMER in body else ""
    body_without_disclaimer = body.replace(DISCLAIMER, "").rstrip()

    if "今天盯什么" in body_without_disclaimer:
        before = body_without_disclaimer.split("今天盯什么", 1)[0].rstrip()
        body = f"{before}\n\n{replacement}".strip()
    else:
        body = f"{body_without_disclaimer}\n\n{replacement}".strip()

    if disclaimer:
        body = f"{body}\n\n{disclaimer}"
    return body.strip() + "\n"


def compact_blanks(lines: list[str]) -> list[str]:
    result: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if result and not previous_blank:
                result.append("")
            previous_blank = True
            continue
        result.append(line)
        previous_blank = False
    while result and not result[-1]:
        result.pop()
    return result


def normalize_strategy_title(line: str) -> str:
    if not line.startswith("【") or not line.endswith("】"):
        return line
    title = line
    replacements = {
        "分歧能不能接住？": "买点后置看承接",
        "能不能接住？": "先看分歧承接",
        "修复要不要追？": "修复太顺不追",
        "资金会往哪边试？": "资金切换先看试错",
        "敢不敢接？": "买点后置看承接",
        "要不要追？": "修复太顺不追",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    title = title.replace("？", "").replace("?", "")
    return title


def reshape_watch_block(lines: list[str]) -> list[str]:
    result: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line != "今天盯什么":
            result.append(line)
            idx += 1
            continue

        result.append(line)
        idx += 1
        while idx < len(lines) and not lines[idx]:
            idx += 1

        items: list[str] = []
        current = ""
        while idx < len(lines):
            item_line = lines[idx].strip()
            if not item_line:
                idx += 1
                continue
            if item_line in XUEQIU_KEEP_HEADINGS or item_line == DISCLAIMER:
                break
            inline_items = split_inline_watch_items(item_line)
            if len(inline_items) > 1:
                if current:
                    items.append(current)
                    current = ""
                items.extend(inline_items)
                idx += 1
                continue
            looks_new = bool(re.match(r"^(?:\d+[、.．)]\s*)?(?:盯|看)", item_line)) or not current
            item_line = clean_watch_item(item_line)
            if looks_new:
                if current:
                    items.append(current)
                current = item_line
            elif current:
                current = current.rstrip("。") + "，" + item_line
            else:
                current = item_line
            idx += 1
        if current:
            items.append(current)

        result.extend(limit_watch_items(items))
        if idx < len(lines) and lines[idx] != DISCLAIMER:
            result.append("")
    return compact_blanks(result)


def normalize_xueqiu_post(content: str, trade_day: date, themes: list[dict[str, Any]]) -> str:
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"```(?:\w+)?\n?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)

    lines: list[str] = []
    previous_blank = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        line = re.sub(r"^[\-*]\s+", "", line)
        line = normalize_xueqiu_line(line) if line else line
        if line is None:
            continue
        if not line:
            if lines and not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        inline_heading = re.match(r"^(今日最佳入手机会|今日风险点|今天盯什么)[:：]\s*(.+)$", line)
        if inline_heading:
            lines.append(inline_heading.group(1))
            lines.append(inline_heading.group(2).strip())
            previous_blank = False
            continue
        lines.append(line)
        previous_blank = False

    while lines and not lines[-1]:
        lines.pop()

    if not lines or not lines[0].startswith("【"):
        lines.insert(0, xueqiu_title(trade_day, themes))
        lines.insert(1, "")
    if lines:
        lines[0] = normalize_strategy_title(lines[0])

    laid_out: list[str] = []
    for line in lines:
        if not line:
            if laid_out and laid_out[-1]:
                laid_out.append("")
            continue
        for watch_part in split_watch_line(line):
            chunks = split_long_line(watch_part)
            for idx, chunk in enumerate(chunks):
                if idx > 0 and laid_out and laid_out[-1]:
                    laid_out.append("")
                laid_out.append(chunk)
    lines = laid_out
    lines = reshape_watch_block(lines)

    body = "\n".join(lines).strip()
    if DISCLAIMER not in body:
        body = f"{body}\n\n{DISCLAIMER}"
    return body.strip() + "\n"


def build_post(trade_day: date, start: datetime, end: datetime, themes: list[dict[str, Any]], news: list[dict[str, Any]]) -> str:
    primary = theme_name(themes[0]) if themes else "盘面"

    lines: list[str] = [
        xueqiu_title(trade_day, themes),
        "",
        f"今天我先看{primary}能不能扛住外盘分歧。",
        f"窗口还是 {start.strftime('%m-%d %H:%M')} 到 {end.strftime('%m-%d %H:%M')}，盘前先别急着拍板，先看资金愿不愿意接。",
        "",
    ]

    if themes:
        for idx, theme in enumerate(themes[:3]):
            name = theme_name(theme)
            titles = theme_titles(theme)
            concepts = decode_json_list(theme.get("related_concepts"))[:4]
            if idx == 0:
                lines.append(f"{name}我放第一位。")
            elif idx == 1:
                lines.append(f"{name}可以当第二层去看。")
            else:
                lines.append(f"{name}先当后手观察。")
            if titles:
                lines.append("盘前能看到的理由：" + "；".join(titles[:2]) + "。")
            if concepts:
                lines.append("我会顺着看 " + "、".join(concepts) + "，重点还是开盘后的承接和扩散。")
            lines.append("")

        rest = [theme_name(item) for item in themes[3:6] if theme_name(item)]
        if rest:
            lines.append("另外还能留意：" + "、".join(rest) + "。")
            lines.append("")
    elif news:
        lines.append("今天盘前重要消息不算少，但先不展开，等开盘确认方向。")
        for item in news[:5]:
            title = compact(item.get("title") or item.get("content"), 100)
            if title:
                lines.append(title)
        lines.append("")
    else:
        lines.extend(["今天盘前有效信息不多，先按竞价和盘中反馈看。", ""])

    lines.extend(
        [
            "我今天主要盯三件事。",
            "先看竞价先把哪条线带出来。",
            "再看开盘十几分钟有没有同方向扩散。",
            "最后看冲高后有没有资金愿意接，只有脉冲没有承接，我会先降预期。",
            "",
            DISCLAIMER,
        ]
    )
    return normalize_xueqiu_post("\n".join(lines), trade_day, themes)


def day_label(trade_day: date) -> str:
    return f"{trade_day.month}月{trade_day.day}日"


def money_100m(value: Any) -> int:
    try:
        return int(float(str(value or "0")) / 100000000)
    except Exception:
        return 0


def pick_plate_names(kpl_tomorrow_fry: dict[str, Any], limit: int = 6) -> list[str]:
    plates = kpl_tomorrow_fry.get("plates") if isinstance(kpl_tomorrow_fry, dict) else []
    if not isinstance(plates, list):
        return []
    names: list[str] = []
    for item in plates:
        if not isinstance(item, dict):
            continue
        name = compact(item.get("plate_name"), 40)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def fact_collector_agent(payload: dict[str, Any]) -> dict[str, Any]:
    market = payload.get("previous_market_context") if isinstance(payload.get("previous_market_context"), dict) else {}
    latest = market.get("latest") if isinstance(market.get("latest"), dict) else {}
    ths = market.get("ths_after_close_summary") if isinstance(market.get("ths_after_close_summary"), dict) else {}
    kpl = payload.get("kpl_tomorrow_fry") if isinstance(payload.get("kpl_tomorrow_fry"), dict) else {}
    themes = payload.get("themes") if isinstance(payload.get("themes"), list) else []
    news = payload.get("news") if isinstance(payload.get("news"), list) else []
    theme_names = [theme_name(item) for item in themes if isinstance(item, dict) and theme_name(item)]
    plate_names = pick_plate_names(kpl)
    previous_summary = compact(ths.get("summary") or ths.get("content_excerpt"), 600) if ths else ""
    external_news: list[str] = []
    for item in news[:12]:
        if not isinstance(item, dict):
            continue
        title = compact(item.get("title") or item.get("content"), 120)
        if title:
            external_news.append(title)
    return {
        "agent": "fact_collector",
        "trade_date": payload.get("trade_date"),
        "window": payload.get("window"),
        "market_temperature": {
            "source": market.get("source"),
            "previous_trade_date": market.get("trade_date"),
            "temperature": market.get("temperature"),
            "total_count": to_int(latest.get("total_count")),
            "up_count": to_int(latest.get("up_count")),
            "down_count": to_int(latest.get("down_count")),
            "down5_count": to_int(latest.get("down5_count")),
            "limit_up_count": to_int(latest.get("limit_up_count")),
            "limit_down_count": to_int(latest.get("limit_down_count")),
            "amount_top50_up_count": to_int(latest.get("amount_top50_up_count")),
            "amount_top50_down_count": to_int(latest.get("amount_top50_down_count")),
            "research_pool_up_count": to_int(latest.get("research_pool_up_count")),
            "research_pool_down_count": to_int(latest.get("research_pool_down_count")),
        },
        "after_close_summary": {
            "title": compact(ths.get("title"), 160) if ths else "",
            "summary": previous_summary,
        },
        "theme_names": theme_names[:8],
        "kpl_plate_names": plate_names,
        "external_news": external_news,
        "raw_counts": {
            "themes": len(themes),
            "news": len(news),
        },
    }


def unique_keep_order(items: list[Any], limit: int = 8) -> list[str]:
    result: list[str] = []
    for item in items:
        text = compact(item, 40)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def external_market_bias(external_news: list[Any]) -> dict[str, Any]:
    positive_words = ("暴涨", "大涨", "走强", "新高", "飙升", "反弹", "收涨", "上行")
    negative_words = ("暴跌", "大跌", "走弱", "承压", "下挫", "收跌", "跳水", "回落")
    tech_words = ("美股", "纳指", "半导体", "科技股", "AI", "英伟达", "费半", "芯片")
    risk_words = ("原油", "黄金", "汇率", "中东", "霍尔木兹", "关税", "制裁")
    positive_hits: list[str] = []
    negative_hits: list[str] = []
    tech_hits: list[str] = []
    risk_hits: list[str] = []
    for item in external_news:
        text = str(item)
        if any(word in text for word in tech_words):
            tech_hits.append(compact(text, 90))
        if any(word in text for word in risk_words):
            risk_hits.append(compact(text, 90))
        if any(word in text for word in positive_words):
            positive_hits.append(compact(text, 90))
        if any(word in text for word in negative_words):
            negative_hits.append(compact(text, 90))
    score = len(positive_hits) - len(negative_hits)
    if score >= 2:
        bias = "外围偏强"
    elif score <= -2:
        bias = "外围偏弱"
    elif positive_hits and not negative_hits:
        bias = "外围偏强"
    elif negative_hits and not positive_hits:
        bias = "外围偏弱"
    else:
        bias = "外围中性"
    return {
        "bias": bias,
        "score": score,
        "tech_related": bool(tech_hits),
        "risk_related": bool(risk_hits),
        "positive_hits": positive_hits[:3],
        "negative_hits": negative_hits[:3],
        "summary": "；".join((positive_hits or negative_hits or tech_hits or risk_hits)[:2]),
    }


def safe_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator > 0 else 0.0


def market_judge_agent(facts: dict[str, Any]) -> dict[str, Any]:
    temp = facts.get("market_temperature") if isinstance(facts.get("market_temperature"), dict) else {}
    up_count = to_int(temp.get("up_count"))
    down_count = to_int(temp.get("down_count"))
    down5_count = to_int(temp.get("down5_count"))
    limit_down_count = to_int(temp.get("limit_down_count"))
    limit_up_count = to_int(temp.get("limit_up_count"))
    top50_up = to_int(temp.get("amount_top50_up_count"))
    top50_down = to_int(temp.get("amount_top50_down_count"))
    pool_up = to_int(temp.get("research_pool_up_count"))
    pool_down = to_int(temp.get("research_pool_down_count"))
    theme_names = facts.get("theme_names") if isinstance(facts.get("theme_names"), list) else []
    plate_names = facts.get("kpl_plate_names") if isinstance(facts.get("kpl_plate_names"), list) else []
    external_news = facts.get("external_news") if isinstance(facts.get("external_news"), list) else []
    external = external_market_bias(external_news)
    strong_lines = unique_keep_order([*plate_names, *theme_names], limit=5)
    market_total = up_count + down_count
    top50_total = top50_up + top50_down
    pool_total = pool_up + pool_down
    market_up_ratio = safe_ratio(up_count, market_total)
    top50_up_ratio = safe_ratio(top50_up, top50_total)
    pool_up_ratio = safe_ratio(pool_up, pool_total)
    weight_market_gap = round(top50_up_ratio - market_up_ratio, 4)
    pool_market_gap = round(pool_up_ratio - market_up_ratio, 4)
    pool_weight_gap = round(pool_up_ratio - top50_up_ratio, 4)
    weak_signals: list[str] = []
    if top50_down > top50_up and top50_down > 0:
        weak_signals.append("成交额Top50下跌数量多于上涨数量，权重核心情绪不强")
    if down5_count >= 150 or limit_down_count >= 10:
        weak_signals.append("跌超5%和跌停压力还没有完全清掉")

    if top50_up_ratio >= 0.7:
        weight_state = "权重核心情绪强"
    elif top50_up_ratio <= 0.45 and top50_total > 0:
        weight_state = "权重核心情绪分歧"
    else:
        weight_state = "权重核心情绪中性"

    if market_up_ratio >= 0.65 and limit_down_count <= 8 and down5_count < 100:
        emotion_state = "全市场情绪强"
    elif market_up_ratio <= 0.45 or down5_count >= 150 or limit_down_count >= 10:
        emotion_state = "全市场情绪分歧"
    elif market_up_ratio > 0.5:
        emotion_state = "全市场情绪有修复"
    else:
        emotion_state = "全市场情绪中性"

    if pool_up_ratio >= 0.65:
        strong_stock_state = "近期强势股情绪强"
    elif pool_up_ratio <= 0.45 and pool_total > 0:
        strong_stock_state = "近期强势股情绪分歧"
    elif pool_up_ratio > 0.5:
        strong_stock_state = "近期强势股情绪有修复"
    else:
        strong_stock_state = "近期强势股情绪中性"

    if pool_market_gap >= 0.15:
        strong_stock_vs_market = "近期强势股情绪强于全市场情绪"
    elif pool_market_gap <= -0.15:
        strong_stock_vs_market = "近期强势股情绪弱于全市场情绪"
    else:
        strong_stock_vs_market = "近期强势股情绪接近全市场情绪"

    if pool_weight_gap >= 0.15:
        strong_stock_vs_weight = "近期强势股情绪强于权重核心"
    elif pool_weight_gap <= -0.15:
        strong_stock_vs_weight = "近期强势股情绪弱于权重核心"
    else:
        strong_stock_vs_weight = "近期强势股情绪接近权重核心"

    if weight_market_gap >= 0.18:
        weight_vs_emotion = "权重核心强于全市场情绪"
    elif weight_market_gap <= -0.18:
        weight_vs_emotion = "全市场情绪强于权重核心"
    elif top50_up_ratio >= 0.6 and market_up_ratio >= 0.6:
        weight_vs_emotion = "权重核心和全市场情绪共振"
    elif top50_up_ratio <= 0.45 and market_up_ratio <= 0.45:
        weight_vs_emotion = "权重核心和全市场情绪共振分歧"
    else:
        weight_vs_emotion = "权重核心和全市场情绪接近"

    if up_count > down_count * 1.8 and up_count > 2500:
        if external["bias"] == "外围偏强" and not weak_signals:
            cycle = "继续修复"
            core_view = "上一交易日已经明显修复，外围又给正反馈，今天先看修复能不能保持昨日力度"
        elif external["bias"] == "外围偏弱" or weak_signals:
            cycle = "修复延续"
            core_view = "上一交易日修复很强，但今天更容易从全面回暖转成内部分化"
        else:
            cycle = "修复延续"
            core_view = "修复还在，但强度大概率弱于前一日，重点看主线承接"
    elif down_count > up_count * 1.5:
        if external["bias"] == "外围偏强":
            cycle = "分歧延续"
            core_view = "上一交易日分歧偏大，但外围给修复预期，今天先看核心方向能不能被接住"
        else:
            cycle = "分歧加大"
            core_view = "分歧仍重，先看有没有一次性杀到位后的修复，不急着下结论"
    elif up_count > down_count:
        if external["bias"] == "外围偏强":
            cycle = "继续修复"
            core_view = "修复基础还在，外围偏强会抬高开盘预期，重点看高开后承接"
        else:
            cycle = "修复延续"
            core_view = "修复还在，但要看量能和主线承接"
    else:
        cycle = "分歧延续"
        core_view = "分歧还在，重点看核心方向能不能接住"

    if weight_vs_emotion == "权重核心强于全市场情绪":
        rotation_view = "成交额Top50代表的权重核心情绪强于全市场情绪，短线先看权重接力，情绪票更多看跟随"
        preferred_side = "科技权重"
    elif weight_vs_emotion == "全市场情绪强于权重核心":
        rotation_view = "全市场情绪强于权重核心，优先看情绪个股分歧延续后的承接"
        preferred_side = "情绪个股"
    elif weight_vs_emotion == "权重核心和全市场情绪共振":
        rotation_view = "权重核心情绪和全市场情绪共振，修复质量更好，重点看主线能否继续扩散"
        preferred_side = "共振修复"
    elif weight_vs_emotion == "权重核心和全市场情绪共振分歧":
        rotation_view = "权重核心情绪和全市场情绪都偏分歧，先看是否一次性杀到位"
        preferred_side = "风险控制"
    else:
        rotation_view = "权重核心情绪和全市场情绪接近，按二八轮动看，谁分歧延续就看谁的承接"
        preferred_side = "轮动承接"
    if strong_stock_vs_weight == "近期强势股情绪弱于权重核心":
        rotation_view += "；研究池代表近期强势股情绪，弱于权重核心时，强势股买点要后置"
        if preferred_side == "轮动承接":
            preferred_side = "权重优先"
    elif strong_stock_vs_weight == "近期强势股情绪强于权重核心":
        rotation_view += "；研究池代表近期强势股情绪，强于权重核心时，情绪票接力优先级更高"
        preferred_side = "近期强势股"
    market_sentiment = {
        "market_up_ratio": market_up_ratio,
        "top50_up_ratio": top50_up_ratio,
        "research_pool_up_ratio": pool_up_ratio,
        "weight_market_gap": weight_market_gap,
        "strong_stock_market_gap": pool_market_gap,
        "strong_stock_weight_gap": pool_weight_gap,
        "market_emotion_proxy": "全市场涨跌占比",
        "weight_core_proxy": "成交额Top50涨跌占比",
        "strong_stock_proxy": "研究池涨跌占比",
        "weight_vs_emotion": weight_vs_emotion,
        "strong_stock_vs_market": strong_stock_vs_market,
        "strong_stock_vs_weight": strong_stock_vs_weight,
    }
    return {
        "agent": "market_judge",
        "cycle": cycle,
        "core_view": core_view,
        "strong_lines": strong_lines,
        "weak_signals": weak_signals,
        "market_sentiment": market_sentiment,
        "external_bias": external,
        "weight_state": weight_state,
        "emotion_state": emotion_state,
        "strong_stock_state": strong_stock_state,
        "style": rotation_view,
        "preferred_side": preferred_side,
    }


def strategy_agent(facts: dict[str, Any], judgement: dict[str, Any]) -> dict[str, Any]:
    strong_lines = judgement.get("strong_lines") if isinstance(judgement.get("strong_lines"), list) else []
    primary = [name for name in strong_lines if name][:3]
    theme_text = "、".join(primary) if primary else "核心方向"
    external_bias = judgement.get("external_bias") if isinstance(judgement.get("external_bias"), dict) else {}
    market_sentiment = judgement.get("market_sentiment") if isinstance(judgement.get("market_sentiment"), dict) else {}
    weight_vs_emotion = str(market_sentiment.get("weight_vs_emotion") or "")
    strong_stock_vs_weight = str(market_sentiment.get("strong_stock_vs_weight") or "")
    strong_stock_vs_market = str(market_sentiment.get("strong_stock_vs_market") or "")
    weak_signals = judgement.get("weak_signals") if isinstance(judgement.get("weak_signals"), list) else []
    cycle = str(judgement.get("cycle") or "")
    preferred_side = str(judgement.get("preferred_side") or "")
    tech_keywords = ("科技", "通信", "芯片", "半导体", "机器人", "算力", "元器件", "AI", "物理AI")
    is_tech_weight_core = (
        preferred_side == "科技权重"
        or weight_vs_emotion == "权重核心强于全市场情绪"
        and any(any(keyword in name for keyword in tech_keywords) for name in primary)
    )
    external_adjustment = external_bias.get("bias") == "外围偏弱" or bool(external_bias.get("negative_hits"))
    external_hint = "外围消息不作为单独方向，只用来判断开盘情绪和承接质量"
    if external_bias.get("tech_related"):
        external_hint = "外围科技强弱会影响开盘预期，但最后仍要看A股自己的承接"
    if external_bias.get("bias") == "外围偏强":
        external_hint = "外围偏强会抬高开盘预期，但不能把高开直接当成承接"
    elif external_bias.get("bias") == "外围偏弱":
        external_hint = "外围调整主要影响开盘，重点看核心方向被动杀跌后有没有资金接"

    if cycle in {"继续修复", "加强修复"}:
        opportunity = f"{theme_text}高开后的承接"
        best_opportunity = f"优先看{opportunity}。如果开盘被外围带高，不急着追，盘中能承接住才说明修复有质量"
        risk_condition = "如果核心方向只高开不承接，量能又快速缩回去，就从机会切到风险"
    elif cycle == "分歧加大" or len(weak_signals) >= 2:
        opportunity = f"{theme_text}恐慌后的承接"
        best_opportunity = f"优先等{opportunity}。如果承接迟迟不出来，就不要硬做修复"
        risk_condition = "如果跌超5%和跌停压力继续扩大，今日重点写风险点"
    else:
        opportunity = f"{theme_text}第一次分歧承接"
        best_opportunity = f"优先看{opportunity}，修复太顺不追，分歧延续后还能回流才是更舒服的点"
        risk_condition = "如果核心方向只高开不承接，量能又快速缩回去，帖子要转向风险点"

    decisive_view = ""
    trade_bias = opportunity
    if is_tech_weight_core and external_adjustment:
        decisive_view = "外围调整只影响开盘，权重科技核心依旧有低吸机会"
        trade_bias = f"{theme_text}被外围带低后的分歧低吸"
        opportunity = trade_bias
        external_hint = "外围调整主要影响开盘，不直接否定A股科技权重主线"
        best_opportunity = (
            f"观点明确：外围调整只影响开盘，权重科技核心地位还在。"
            f"{theme_text}如果被外围带低，重点看分歧低吸机会；高开太顺不追"
        )
        risk_condition = "只有科技权重开盘后承接失败、成交额Top50转弱，才从低吸机会切到风险"

    if weight_vs_emotion == "权重核心强于全市场情绪":
        best_opportunity += "；但这是权重核心情绪更强，情绪票买点要后置，等分歧承接出来"
    elif weight_vs_emotion == "全市场情绪强于权重核心":
        best_opportunity += "；如果权重不拖后腿，情绪个股的分歧承接优先级更高"
    elif weight_vs_emotion == "权重核心和全市场情绪共振":
        best_opportunity += "；权重核心和全市场情绪共振时，修复延续的质量会更好"
    elif weight_vs_emotion == "权重核心和全市场情绪共振分歧":
        best_opportunity = "先看风险。权重核心和全市场情绪都偏分歧，只有杀跌后出现主动承接，才考虑修复机会"
    if strong_stock_vs_weight == "近期强势股情绪弱于权重核心":
        best_opportunity += "；研究池代表近期强势股情绪，弱于权重核心时，情绪票不要前置"
    elif strong_stock_vs_weight == "近期强势股情绪强于权重核心":
        best_opportunity += "；研究池代表近期强势股情绪，强于权重核心时，情绪票分歧承接可以更前置"

    if cycle == "分歧加大" and external_bias.get("bias") != "外围偏强" and not decisive_view:
        best_section = "今日风险点"
        best_opportunity = "风险点在核心方向继续杀跌却没有承接。只要量能缩、跌停和跌超5%扩散，就先少做修复预期"
    else:
        best_section = "今日最佳入手机会"

    first_watch = (
        f"{primary[0]}高开后有没有主动承接"
        if cycle in {"继续修复", "加强修复"} and len(primary) >= 1
        else (f"{primary[0]}分歧下来有没有主动承接" if len(primary) >= 1 else "核心方向分歧下来有没有主动承接")
    )
    if decisive_view:
        first_watch = "科技权重被外围带低后有没有主动承接"
        second_watch = (
            f"{theme_text}低开或分歧时有没有资金接回"
            if primary
            else "核心科技权重低开或分歧时有没有资金接回"
        )
    else:
        second_watch = f"{primary[1]}能不能从个股强扩散成板块强" if len(primary) >= 2 else "次强方向能不能扩散"
    return {
        "agent": "strategy_builder",
        "external_hint": external_hint,
        "decisive_view": decisive_view,
        "trade_bias": trade_bias,
        "primary_strategy": opportunity,
        "best_section": best_section,
        "best_opportunity": best_opportunity,
        "risk_condition": risk_condition,
        "weight_vs_emotion": weight_vs_emotion,
        "strong_stock_vs_market": strong_stock_vs_market,
        "strong_stock_vs_weight": strong_stock_vs_weight,
        "watch_points": [
            first_watch,
            second_watch,
            "成交额Top50能不能继续强于全市场情绪" if decisive_view else (weight_vs_emotion or "权重核心情绪和全市场情绪谁更强"),
            "研究池弱于权重核心时，情绪票买点继续后置" if decisive_view else (strong_stock_vs_weight or "研究池近期强势股情绪能不能接上"),
        ],
    }


def workflow_fallback_post(
    trade_day: date,
    facts: dict[str, Any],
    judgement: dict[str, Any],
    strategy: dict[str, Any],
) -> str:
    temp = facts.get("market_temperature") if isinstance(facts.get("market_temperature"), dict) else {}
    summary = facts.get("after_close_summary") if isinstance(facts.get("after_close_summary"), dict) else {}
    strong_lines = judgement.get("strong_lines") if isinstance(judgement.get("strong_lines"), list) else []
    primary = [str(item) for item in strong_lines if str(item).strip()]
    first = primary[0] if primary else "核心方向"
    second = primary[1] if len(primary) > 1 else "次强方向"
    decisive_view = str(strategy.get("decisive_view") or "")
    trade_bias = str(strategy.get("trade_bias") or strategy.get("primary_strategy") or "买点后置看承接")
    title_action = "科技权重低吸机会还在" if "权重科技" in decisive_view else "买点后置看承接"
    title = f"【{day_label(trade_day)}盘前：{judgement.get('cycle', '盘面修复')}，{title_action}】"
    market_line = (
        f"上一交易日A股温度先看修复，收盘{to_int(temp.get('up_count'))}家上涨、"
        f"{to_int(temp.get('down_count'))}家下跌，跌超5%的{to_int(temp.get('down5_count'))}家，"
        f"跌停{to_int(temp.get('limit_down_count'))}家，涨停{to_int(temp.get('limit_up_count'))}家。"
    )
    if to_int(temp.get("amount_top50_down_count")) > to_int(temp.get("amount_top50_up_count")):
        market_line += " 情绪是回暖了，但权重内部没那么强，成交额前50里下跌数量还更多。"
    if primary:
        market_line += " 核心方向主要看" + "、".join(primary[:4]) + "。"
    watch_points = strategy.get("watch_points") if isinstance(strategy.get("watch_points"), list) else []
    watch_points = [compact(item, 80) for item in watch_points if compact(item, 80)][:4]
    while len(watch_points) < 4:
        watch_points.append("量能和跌停压力能不能继续改善。")
    lines = [
        title,
        "",
        market_line,
        "",
        (
            f"今天先按{judgement.get('cycle', '修复延续')}看。{decisive_view}，具体买点看{trade_bias}。"
            if decisive_view
            else f"今天先按{judgement.get('cycle', '修复延续')}看。{judgement.get('core_view', '重点看分歧承接')}，不适合开盘只看谁冲得快。"
        ),
        "",
        f"外围这里先当情绪背景处理。{strategy.get('external_hint', '最后还是看A股自己的承接')}。",
        "",
        f"最强先看{first}。如果开盘直接一致，不急；更好的点在第一次分歧延续，核心品种回落后还能被接住，才说明上一交易日的强不是一日游。",
        "",
        f"{second}也放进观察。它要从个股强变成板块强，不能只靠前排硬顶，后排没有扩散就还是脉冲。",
        "",
        "科技明天位置后置。只要科技权重没有重新打出强度，就先看半导体、通信这些方向能不能修复弱势，别急着把高开当反包。",
        "",
        strategy.get("best_section", "今日最佳入手机会"),
        "",
        strategy.get("best_opportunity", "我更看强主线第一次分歧承接。修复太顺不追，分歧延续后还能回流，才是更舒服的点。"),
        "",
        "今天盯什么",
        "",
        *watch_points,
        "",
        DISCLAIMER,
    ]
    return enforce_strategy_watch_block(normalize_xueqiu_post("\n".join(lines), trade_day, []), strategy)


def draft_writer_agent(
    payload: dict[str, Any],
    facts: dict[str, Any],
    judgement: dict[str, Any],
    strategy: dict[str, Any],
    *,
    trade_day: date,
    themes: list[dict[str, Any]],
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
) -> str:
    system = "你是A股短线交易者，只负责把已固定的事实、判断和策略写成像真人的中文盘前帖子。不要新增事实。"
    user = "\n".join(
        [
            "请按下面已固定的 workflow 产物写一篇雪球早盘帖。",
            "硬性结构：标题；自然短段写市场温度+情绪周期、今日总判断、主线和后手观察；单独写今日最佳入手机会或今日风险点；单独写今天盯什么；免责声明。",
            "雪球排版：不要写一、二、三、四、五、六；不要写第一、第二、第三；不要写公众号式小标题。只允许保留“今日最佳入手机会/今日风险点”和“今天盯什么”两个栏目标题，标题后不要加冒号，不要用【】包住栏目标题。",
            "今天盯什么下面只写3-4行，每行一个观察点，不编号，不拆成解释性两行。",
            "语气短句、有判断、不要研报味。不要出现：分歧减缓、有几个事实、推演如下、因素叠加、最合理的推演、可以得出、映射方向包括、盘中验证点、结论先放前面、条件也很清楚。",
            "策略部分必须观点明确，先写结论，再写条件，不要只写“看承接”。如果 STRATEGY.decisive_view 不为空，今日总判断必须直接使用这个观点。",
            "只能使用事实、判断和策略里的内容，不要编造个股。",
            "用户词汇：修复延续=修复还在但弱于前一日；继续修复=力度相当；加强修复=更强；分歧延续=分歧还在但弱于前一日。",
            "FACTS:",
            json.dumps(facts, ensure_ascii=False, indent=2),
            "JUDGEMENT:",
            json.dumps(judgement, ensure_ascii=False, indent=2),
            "STRATEGY:",
            json.dumps(strategy, ensure_ascii=False, indent=2),
            "RAW_PAYLOAD_FOR_REFERENCE:",
            json.dumps(payload, ensure_ascii=False, indent=2)[:6000],
        ]
    )
    text = call_model_text(system=system, user=user, model=model, base_url=base_url, api_key=api_key, timeout=timeout)
    return enforce_strategy_watch_block(normalize_xueqiu_post(text, trade_day, themes), strategy)


FORBIDDEN_POST_PHRASES = [
    "分歧减缓",
    "映射方向包括",
    "消息密度最高",
    "风险偏好形成压制",
    "持续性取决于",
    "总体思路",
    "核心不是单一利好",
    "盘中验证点",
    "第一条",
    "第二条",
    "第三条",
    "有几个事实",
    "推演如下",
    "因素叠加",
    "最合理的推演",
    "可以得出",
    "强线",
    "弱线",
]


ALLOWED_POST_HEADINGS = {
    "今日最佳入手机会",
    "今日风险点",
    "今天盯什么",
    "总体就是一句话",
    "今日总判断：",
    "最强主线：",
    "次强主线：",
    "后手观察：",
    "今日最佳入手机会：",
    "今日风险点：",
    "今天盯什么：",
    "总体就是一句话：",
}


def review_goal_agent(content: str) -> dict[str, Any]:
    stripped = (content or "").strip()
    issues: list[str] = []
    if not stripped.startswith("【"):
        issues.append("标题必须是首行，且用【】包住")
    first_line = stripped.splitlines()[0].strip() if stripped.splitlines() else ""
    if first_line.endswith(("？", "?")) or "？" in first_line or "?" in first_line:
        issues.append("标题不要使用疑问句，要直接给出策略")
    if "今日最佳入手机会" not in stripped and "今日风险点" not in stripped:
        issues.append("缺少今日最佳入手机会或今日风险点")
    if "今天盯什么" not in stripped:
        issues.append("缺少今天盯什么")
    if DISCLAIMER not in stripped:
        issues.append("缺少免责声明")
    if "跌超5%" not in stripped and "跌超 5%" not in stripped:
        issues.append("第一段缺少跌超5%压力")
    if "跌停" not in stripped:
        issues.append("第一段缺少跌停压力")
    if "外围" not in stripped and "美股" not in stripped and "纳指" not in stripped and "半导体" not in stripped:
        issues.append("缺少外围消息对开盘预期的影响")
    for phrase in FORBIDDEN_POST_PHRASES:
        if phrase in stripped:
            issues.append(f"出现机器味或禁用表达：{phrase}")
    if len(stripped) < 500:
        issues.append("正文过短")
    if len(stripped) > 1500:
        issues.append("正文过长")
    if "今日最佳入手机会" in stripped and "今日风险点" in stripped:
        issues.append("今日最佳入手机会和今日风险点不能同时写")
    for line in stripped.splitlines():
        clean = line.strip()
        if clean in ALLOWED_POST_HEADINGS:
            continue
        if re.match(r"^[一二三四五六七八九十]+[、.．]\s*", clean):
            issues.append(f"雪球排版不应使用文章提纲编号：{clean[:40]}")
            break
        if re.match(r"^第[一二三四五六七八九十]+[，、]\s*", clean):
            issues.append(f"雪球排版不应使用教案式编号：{clean[:40]}")
            break
        if clean.endswith(("，", "、", "：")) or clean in {"板块题材上", "板块题材上，"}:
            issues.append(f"存在半截句：{clean[:40]}")
            break
    return {
        "agent": "review_goal",
        "pass": not issues,
        "issues": issues,
        "checks": {
            "has_title": stripped.startswith("【"),
            "has_best_or_risk": "今日最佳入手机会" in stripped or "今日风险点" in stripped,
            "has_watch": "今天盯什么" in stripped,
            "has_disclaimer": DISCLAIMER in stripped,
            "length": len(stripped),
        },
    }


def rewrite_agent(
    draft: str,
    review: dict[str, Any],
    facts: dict[str, Any],
    judgement: dict[str, Any],
    strategy: dict[str, Any],
    *,
    trade_day: date,
    themes: list[dict[str, Any]],
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
) -> str:
    system = "你是严格的A股盘前帖子编辑，只根据审稿问题重写，不新增事实。"
    user = "\n".join(
        [
            "下面这篇早盘帖没有通过审稿，请重写成最终版。",
            "必须修复 review issues，仍保持真人短线复盘语气。",
            "策略部分必须观点明确，先写结论，再写条件，不要只写“看承接”。如果 STRATEGY.decisive_view 不为空，今日总判断必须直接使用这个观点。",
            "不要出现“结论先放前面”“条件也很清楚”这类写作提示词口吻。",
            "DRAFT:",
            draft,
            "REVIEW:",
            json.dumps(review, ensure_ascii=False, indent=2),
            "FACTS:",
            json.dumps(facts, ensure_ascii=False, indent=2),
            "JUDGEMENT:",
            json.dumps(judgement, ensure_ascii=False, indent=2),
            "STRATEGY:",
            json.dumps(strategy, ensure_ascii=False, indent=2),
        ]
    )
    text = call_model_text(system=system, user=user, model=model, base_url=base_url, api_key=api_key, timeout=timeout)
    return enforce_strategy_watch_block(normalize_xueqiu_post(text, trade_day, themes), strategy)


def run_morning_reference_workflow(
    payload: dict[str, Any],
    *,
    trade_day: date,
    themes: list[dict[str, Any]],
    news: list[dict[str, Any]],
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    fallback_without_model: bool,
    review_max_rewrites: int,
) -> dict[str, Any]:
    facts = fact_collector_agent(payload)
    judgement = market_judge_agent(facts)
    strategy = strategy_agent(facts, judgement)
    stages: list[dict[str, Any]] = [
        {"name": "fact_collector", "ok": True},
        {"name": "market_judge", "ok": True},
        {"name": "strategy_builder", "ok": True},
    ]
    model_ok = False
    model_error = ""
    drafts: list[str] = []
    try:
        content = draft_writer_agent(
            payload,
            facts,
            judgement,
            strategy,
            trade_day=trade_day,
            themes=themes,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
        )
        model_ok = True
        drafts.append(content)
        stages.append({"name": "draft_writer", "ok": True, "model": model})
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
        stages.append({"name": "draft_writer", "ok": False, "error": model_error[:1000]})
        if not fallback_without_model:
            raise
        content = workflow_fallback_post(trade_day, facts, judgement, strategy)
        drafts.append(content)
        stages.append({"name": "fallback_writer", "ok": True})

    reviews: list[dict[str, Any]] = []
    for attempt in range(max(0, int(review_max_rewrites)) + 1):
        review = review_goal_agent(content)
        review["attempt"] = attempt
        reviews.append(review)
        stages.append({"name": "review_goal", "ok": bool(review.get("pass")), "attempt": attempt})
        if review.get("pass"):
            break
        if attempt >= max(0, int(review_max_rewrites)):
            break
        try:
            content = rewrite_agent(
                content,
                review,
                facts,
                judgement,
                strategy,
                trade_day=trade_day,
                themes=themes,
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
            )
            model_ok = True
            drafts.append(content)
            stages.append({"name": "rewriter", "ok": True, "model": model, "attempt": attempt + 1})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            stages.append({"name": "rewriter", "ok": False, "error": error[:1000], "attempt": attempt + 1})
            if not fallback_without_model:
                raise
            break
    final_review = reviews[-1] if reviews else review_goal_agent(content)
    return {
        "content": content,
        "facts": facts,
        "judgement": judgement,
        "strategy": strategy,
        "drafts": drafts,
        "reviews": reviews,
        "final_review": final_review,
        "stages": stages,
        "model_ok": model_ok,
        "model_error": model_error,
        "model": model if model_ok else "fallback_without_model",
    }


def write_workflow_artifacts(
    *,
    output_dir: Path,
    trade_day: date,
    payload: dict[str, Any],
    workflow: dict[str, Any],
) -> dict[str, str]:
    workflow_dir = output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.workflow"
    workflow_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "workflow_dir": workflow_dir,
        "facts_path": workflow_dir / "facts.json",
        "market_judgement_path": workflow_dir / "market_judgement.json",
        "strategy_view_path": workflow_dir / "strategy_view.json",
        "review_path": workflow_dir / "review.json",
        "final_path": workflow_dir / "final.md",
        "payload_path": workflow_dir / "payload.json",
        "workflow_path": output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.workflow.json",
    }
    artifacts["facts_path"].write_text(json.dumps(workflow["facts"], ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["market_judgement_path"].write_text(json.dumps(workflow["judgement"], ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["strategy_view_path"].write_text(json.dumps(workflow["strategy"], ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["review_path"].write_text(json.dumps(workflow["reviews"], ensure_ascii=False, indent=2), encoding="utf-8")
    draft_paths: list[str] = []
    for idx, draft in enumerate(workflow.get("drafts") or [], start=1):
        draft_path = workflow_dir / f"draft_v{idx}.md"
        draft_path.write_text(str(draft), encoding="utf-8")
        draft_paths.append(str(draft_path))
    artifacts["final_path"].write_text(str(workflow["content"]), encoding="utf-8")
    artifacts["payload_path"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    workflow_summary = {
        "ok": bool(workflow.get("final_review", {}).get("pass")),
        "workflow": "morning_reference_post",
        "generated_at": now_text(),
        "facts": workflow["facts"],
        "judgement": workflow["judgement"],
        "strategy": workflow["strategy"],
        "reviews": workflow["reviews"],
        "stages": workflow["stages"],
        "drafts": draft_paths,
        "artifacts": {key: str(value) for key, value in artifacts.items()},
    }
    artifacts["workflow_path"].write_text(json.dumps(workflow_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: str(value) for key, value in artifacts.items()}


def should_wait_for_inputs(themes: list[dict[str, Any]], news: list[dict[str, Any]], min_themes: int) -> bool:
    if min_themes > 0 and len(themes) < min_themes:
        return True
    return not themes and not news


def loop_deadline_today(loop_until: str) -> datetime | None:
    text = (loop_until or "").strip()
    if not text:
        return None
    try:
        hour, minute = [int(part) for part in text.split(":", 1)]
        return datetime.combine(datetime.now().date(), datetime.now().time().replace(hour=hour, minute=minute, second=0, microsecond=0))
    except Exception:
        return None


def read_inputs(
    *,
    config: Any | None,
    root: Path,
    trade_day: date,
    start: datetime,
    end: datetime,
    theme_limit: int,
    news_limit: int,
    min_importance: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    if config is not None:
        themes = read_themes_mysql(config, trade_day, theme_limit)
        news = read_news_mysql(config, start, end, min_importance, news_limit)
        market_context = read_previous_market_context(config, trade_day)
        kpl_tomorrow_fry = read_previous_kpl_tomorrow_fry(config, trade_day, limit=8)
        return themes, news, market_context, kpl_tomorrow_fry
    themes, news = read_fallback(root)
    return themes, news, {}, {}


def model_payload(
    trade_day: date,
    start: datetime,
    end: datetime,
    themes: list[dict[str, Any]],
    news: list[dict[str, Any]],
    market_context: dict[str, Any] | None = None,
    kpl_tomorrow_fry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "task": "morning_reference_post",
        "trade_date": trade_day.isoformat(),
        "window": {
            "label": "previous_trade_close_to_now",
            "since": start.strftime("%Y-%m-%d %H:%M:%S"),
            "until": end.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "previous_market_context": market_context or {},
        "kpl_tomorrow_fry": kpl_tomorrow_fry or {},
        "themes": themes[:8],
        "news": news[:30],
        "output_constraints": {
            "platform": "xueqiu",
            "audience": "A-share short-term trader morning reference",
            "length": "700-1100 Chinese characters, hard max 1200",
            "tone": "human, experienced, conversational, not report-like",
            "must_include": ["xueqiu-style title", "strongest themes", "secondary themes", "intraday validation points", "risk disclaimer"],
            "must_not": ["invent stocks", "invent data", "give buy/sell advice", "markdown table", "code block", "rigid template language"],
        },
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build morning reference post from market news and daily themes.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--after-close-hour", type=int, default=15)
    parser.add_argument("--theme-limit", type=int, default=8)
    parser.add_argument("--news-limit", type=int, default=30)
    parser.add_argument("--min-importance", type=int, default=2)
    parser.add_argument("--output-dir", type=Path, default=root / "runs" / "posts")
    parser.add_argument("--model-config", default=os.environ.get("MODEL_CONFIG_NAME", "default"))
    parser.add_argument("--no-model-config", action="store_true")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1")
    parser.add_argument("--api-key-file", default=os.environ.get("OPENAI_API_KEY_FILE", ""))
    parser.add_argument("--model-timeout", type=int, default=int(os.environ.get("OPENAI_TIMEOUT", "60")))
    parser.add_argument("--fallback-without-model", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--workflow", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--review-max-rewrites", type=int, default=1)
    parser.add_argument("--mirror-output-dir", type=Path, default=root / "output" / "morning_reference")
    parser.add_argument("--loop-until", default="")
    parser.add_argument("--loop-interval-seconds", type=int, default=60)
    parser.add_argument("--min-themes", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    root = project_root()
    config = mysql_config_from_args(args) if args.mysql_enabled else None
    trade_day, start, end = resolve_window(args, config)
    deadline = loop_deadline_today(args.loop_until)
    while True:
        themes, news, market_context, kpl_tomorrow_fry = read_inputs(
            config=config,
            root=root,
            trade_day=trade_day,
            start=start,
            end=end,
            theme_limit=args.theme_limit,
            news_limit=args.news_limit,
            min_importance=args.min_importance,
        )
        if not should_wait_for_inputs(themes, news, args.min_themes):
            break
        if deadline is None or datetime.now() >= deadline:
            break
        time.sleep(max(5, int(args.loop_interval_seconds)))
    payload = model_payload(trade_day, start, end, themes, news, market_context, kpl_tomorrow_fry)
    model_ok = False
    model_error = ""
    model_name = "fallback_without_model"
    workflow_result: dict[str, Any] | None = None
    workflow_artifacts: dict[str, str] = {}
    try:
        if config is not None:
            ensure_model_config_table(config)
            runtime = resolve_model_runtime_config(
                config=config,
                config_name=args.model_config,
                no_model_config=args.no_model_config,
                api_key_file=args.api_key_file,
                base_url=args.base_url,
                model=args.model,
                timeout=args.model_timeout,
            )
            if args.workflow:
                workflow_result = run_morning_reference_workflow(
                    payload,
                    trade_day=trade_day,
                    themes=themes,
                    news=news,
                    model=runtime.model,
                    base_url=runtime.base_url,
                    api_key=runtime.api_key,
                    timeout=runtime.timeout,
                    fallback_without_model=args.fallback_without_model,
                    review_max_rewrites=args.review_max_rewrites,
                )
                content = str(workflow_result["content"])
                model_ok = bool(workflow_result.get("model_ok"))
                model_error = str(workflow_result.get("model_error") or "")
            else:
                content = call_morning_reference_model(
                    payload,
                    model=runtime.model,
                    base_url=runtime.base_url,
                    api_key=runtime.api_key,
                    timeout=runtime.timeout,
                )
                content = normalize_xueqiu_post(content, trade_day, themes)
                model_ok = True
            model_name = runtime.model
        else:
            file_env = read_openai_env_file(args.api_key_file)
            base_url = args.base_url
            model = args.model
            if base_url == "https://api.openai.com/v1" and file_env.get("OPENAI_BASE_URL"):
                base_url = file_env["OPENAI_BASE_URL"]
            if model == "gpt-4o-mini" and file_env.get("OPENAI_MODEL"):
                model = file_env["OPENAI_MODEL"]
            if args.workflow:
                workflow_result = run_morning_reference_workflow(
                    payload,
                    trade_day=trade_day,
                    themes=themes,
                    news=news,
                    model=model,
                    base_url=base_url,
                    api_key=resolve_api_key(args.api_key_file),
                    timeout=args.model_timeout,
                    fallback_without_model=args.fallback_without_model,
                    review_max_rewrites=args.review_max_rewrites,
                )
                content = str(workflow_result["content"])
                model_ok = bool(workflow_result.get("model_ok"))
                model_error = str(workflow_result.get("model_error") or "")
            else:
                content = call_morning_reference_model(
                    payload,
                    model=model,
                    base_url=base_url,
                    api_key=resolve_api_key(args.api_key_file),
                    timeout=args.model_timeout,
                )
                content = normalize_xueqiu_post(content, trade_day, themes)
                model_ok = True
            model_name = model
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
        if not args.fallback_without_model:
            raise
        if args.workflow:
            fallback_payload = model_payload(trade_day, start, end, themes, news, market_context, kpl_tomorrow_fry)
            facts = fact_collector_agent(fallback_payload)
            judgement = market_judge_agent(facts)
            strategy = strategy_agent(facts, judgement)
            content = workflow_fallback_post(trade_day, facts, judgement, strategy)
            review = review_goal_agent(content)
            workflow_result = {
                "content": content,
                "facts": facts,
                "judgement": judgement,
                "strategy": strategy,
                "drafts": [content],
                "reviews": [review],
                "final_review": review,
                "stages": [
                    {"name": "workflow_exception", "ok": False, "error": model_error[:1000]},
                    {"name": "fallback_writer", "ok": True},
                    {"name": "review_goal", "ok": bool(review.get("pass")), "attempt": 0},
                ],
                "model_ok": False,
                "model_error": model_error,
                "model": "fallback_without_model",
            }
        else:
            content = build_post(trade_day, start, end, themes, news)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.txt"
    latest_path = args.output_dir / "morning_reference_latest.txt"
    meta_path = args.output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.json"
    output_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
    if args.mirror_output_dir:
        args.mirror_output_dir.mkdir(parents=True, exist_ok=True)
        mirror_output_path = args.mirror_output_dir / output_path.name
        mirror_latest_path = args.mirror_output_dir / latest_path.name
        mirror_output_path.write_text(content, encoding="utf-8")
        mirror_latest_path.write_text(content, encoding="utf-8")
    else:
        mirror_output_path = None
        mirror_latest_path = None
    if workflow_result is not None:
        workflow_artifacts = write_workflow_artifacts(
            output_dir=args.output_dir,
            trade_day=trade_day,
            payload=payload,
            workflow=workflow_result,
        )
    meta = {
        "ok": True,
        "trade_date": trade_day.isoformat(),
        "since": start.strftime("%Y-%m-%d %H:%M:%S"),
        "until": end.strftime("%Y-%m-%d %H:%M:%S"),
        "themes": len(themes),
        "news": len(news),
        "model_ok": model_ok,
        "model": model_name,
        "model_error": model_error[:1000],
        "workflow_enabled": bool(args.workflow),
        "review_pass": bool(workflow_result.get("final_review", {}).get("pass")) if workflow_result else None,
        "workflow_artifacts": workflow_artifacts,
        "output_path": str(output_path),
        "latest_path": str(latest_path),
        "mirror_output_path": str(mirror_output_path) if mirror_output_path else "",
        "mirror_latest_path": str(mirror_latest_path) if mirror_latest_path else "",
        "meta_path": str(meta_path),
        "generated_at": now_text(),
    }
    meta_path.write_text(json.dumps({**meta, "payload": payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
