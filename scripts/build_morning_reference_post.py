#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
import os
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Any

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

写作要求：
1. 面向雪球阅读，整体控制在 700-1100 个中文字符，最多不要超过 1200 字。
2. 首行必须是帖子标题，格式类似：【5月13日盘前：先看AI能不能扛住分歧】。
3. 第二段先说人话结论，可以用“今天我会先看……”“这里不急着下结论……”这类自然表达。
4. 正文不要写成清单感很强的模板，优先用短段落，每段 1-2 句，段与段之间留空行。
5. 排版建议是：先一句总判断，再分三段讲最强主线、次强主线、后手观察，最后单独收一个“今天盯什么”。
6. “今天盯什么”必须单独一行，下面分 3-4 行写，每行只写一个观察点，不要挤在一个长段落里。
7. 不要每段都写“依据/看点/映射方向/盘中验证点”，这些词会显得机器味很重。
8. 每条主线只保留最关键的 1-2 个消息原因，再写你会怎么观察盘面。
9. 不要平均罗列新闻，不要把每条消息都展开；只保留对今天交易情绪最有用的信息。
10. 只写输入中明确存在的主题、概念、消息，不要臆造具体个股。
11. 语气像个人盘前复盘，短句、清楚、有判断力，可以有一点“不确定性”，不要绝对化、不要喊口号。
12. 避免 Markdown 表格、代码块、粗体符号、过多小标题；输出纯正文，不要 JSON。
13. 末尾必须包含“仅为个人盘前复盘，不构成投资建议。”

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
如果输入 JSON 里有 previous_market_context，开头 1-2 段必须先交代上一交易日 A 股市场温度。
如果 previous_market_context 显示上一交易日下跌家数多、跌超3%家数多、跌停压力明显，
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
      ROUND(AVG(down3_count), 0),
      MAX(down3_count),
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
    if len(latest) < 16:
        ths_summary = read_previous_ths_after_close_summary(config, trade_day)
        return {"source": "ths_market_after_close_summaries", "ths_after_close_summary": ths_summary} if ths_summary else {}
    total = max(1, to_int(latest[2]))
    down_count = to_int(latest[4])
    down3_count = to_int(latest[7])
    limit_down_count = to_int(latest[9])
    down_ratio = down_count / total
    down3_ratio = down3_count / total
    if down_ratio >= 0.72 or down3_ratio >= 0.12 or limit_down_count >= 10:
        temperature = "上一交易日市场明显偏弱/大跌，今天先按修复盘和分歧承接处理"
    elif down_ratio >= 0.6 or down3_ratio >= 0.06:
        temperature = "上一交易日市场偏弱，今天需要先看修复力度"
    elif to_int(latest[3]) > down_count:
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
            "up3_count": to_int(latest[6]),
            "down3_count": down3_count,
            "limit_up_count": to_int(latest[8]),
            "limit_down_count": limit_down_count,
            "amount_top50_count": to_int(latest[10]),
            "amount_top50_up_count": to_int(latest[11]),
            "amount_top50_down_count": to_int(latest[12]),
            "research_pool_count": to_int(latest[13]),
            "research_pool_up_count": to_int(latest[14]),
            "research_pool_down_count": to_int(latest[15]),
        },
        "session_stats": {
            "snapshot_count": to_int(stats[1]) if len(stats) > 1 else 0,
            "avg_up_count": to_int(stats[2]) if len(stats) > 2 else 0,
            "avg_down_count": to_int(stats[3]) if len(stats) > 3 else 0,
            "max_down_count": to_int(stats[4]) if len(stats) > 4 else 0,
            "avg_down3_count": to_int(stats[5]) if len(stats) > 5 else 0,
            "max_down3_count": to_int(stats[6]) if len(stats) > 6 else 0,
            "max_limit_down_count": to_int(stats[7]) if len(stats) > 7 else 0,
            "max_limit_up_count": to_int(stats[8]) if len(stats) > 8 else 0,
        },
    }
    ths_summary = read_previous_ths_after_close_summary(config, trade_day)
    if ths_summary:
        context["source"] = "market_width_snapshots+ths_market_after_close_summaries"
        context["ths_after_close_summary"] = ths_summary
    return context


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
        return f"【{format_day(trade_day)}盘前：先看{first}能不能走出来】"
    return f"【{format_day(trade_day)}盘前：先看开盘怎么选方向】"


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
        if not line:
            if lines and not previous_blank:
                lines.append("")
            previous_blank = True
            continue
        lines.append(line)
        previous_blank = False

    while lines and not lines[-1]:
        lines.pop()

    if not lines or not lines[0].startswith("【"):
        lines.insert(0, xueqiu_title(trade_day, themes))
        lines.insert(1, "")

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


def model_payload(
    trade_day: date,
    start: datetime,
    end: datetime,
    themes: list[dict[str, Any]],
    news: list[dict[str, Any]],
    market_context: dict[str, Any] | None = None,
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
    if config is not None:
        themes = read_themes_mysql(config, trade_day, args.theme_limit)
        news = read_news_mysql(config, start, end, args.min_importance, args.news_limit)
        market_context = read_previous_market_context(config, trade_day)
    else:
        themes, news = read_fallback(root)
        market_context = {}
    payload = model_payload(trade_day, start, end, themes, news, market_context)
    model_ok = False
    model_error = ""
    model_name = "fallback_without_model"
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
            content = call_morning_reference_model(
                payload,
                model=runtime.model,
                base_url=runtime.base_url,
                api_key=runtime.api_key,
                timeout=runtime.timeout,
            )
            content = normalize_xueqiu_post(content, trade_day, themes)
            model_name = runtime.model
        else:
            file_env = read_openai_env_file(args.api_key_file)
            base_url = args.base_url
            model = args.model
            if base_url == "https://api.openai.com/v1" and file_env.get("OPENAI_BASE_URL"):
                base_url = file_env["OPENAI_BASE_URL"]
            if model == "gpt-4o-mini" and file_env.get("OPENAI_MODEL"):
                model = file_env["OPENAI_MODEL"]
            content = call_morning_reference_model(
                payload,
                model=model,
                base_url=base_url,
                api_key=resolve_api_key(args.api_key_file),
                timeout=args.model_timeout,
            )
            content = normalize_xueqiu_post(content, trade_day, themes)
            model_name = model
        model_ok = True
    except Exception as exc:
        model_error = f"{type(exc).__name__}: {exc}"
        if not args.fallback_without_model:
            raise
        content = build_post(trade_day, start, end, themes, news)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.txt"
    latest_path = args.output_dir / "morning_reference_latest.txt"
    meta_path = args.output_dir / f"morning_reference_{trade_day.strftime('%Y-%m-%d')}.json"
    output_path.write_text(content, encoding="utf-8")
    latest_path.write_text(content, encoding="utf-8")
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
        "output_path": str(output_path),
        "latest_path": str(latest_path),
        "meta_path": str(meta_path),
        "generated_at": now_text(),
    }
    meta_path.write_text(json.dumps({**meta, "payload": payload}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
