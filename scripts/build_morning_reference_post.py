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


SYSTEM_PROMPT = (
    "你是A股盘前早参作者。你的任务是基于输入的盘前消息和主题雷达，"
    "写出一篇给短线交易者阅读的早参帖子。只允许基于输入材料归纳，"
    "不要编造股票、消息、数据或政策细节，不给买卖建议。"
)


USER_PROMPT = """请基于下面 JSON 生成一篇中文早参帖子。

写作要求：
1. 不要使用死板模板，但必须让读者一眼看到：最强主线、辅助线、盘中验证点。
2. 时间范围必须体现为“上一个交易日收盘后至今”，可以写具体时间窗口。
3. 先判断消息强弱，不要平均罗列；把高密度、高相关、可能影响今日风险偏好的方向放前面。
4. 每条主线要说明：消息依据、可能映射方向、盘中如何验证。
5. 不要臆造具体个股。只有输入里明确给出的主题、概念、消息才可以写。
6. 语气像个人盘前复盘，清晰、有判断力、不过度煽动。
7. 末尾必须包含“仅为个人盘前复盘，不构成投资建议。”
8. 输出纯正文，不要 Markdown 代码块，不要 JSON。

输入 JSON：
{payload_json}
"""


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
            {"role": "user", "content": USER_PROMPT.format(payload_json=json.dumps(payload, ensure_ascii=False, indent=2))},
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
    sql = f"""
    SELECT
      theme_name,
      importance_score,
      source_count,
      COALESCE(summary, ''),
      COALESCE(CAST(source_titles AS CHAR), '[]'),
      COALESCE(CAST(related_concepts AS CHAR), '[]')
    FROM daily_market_themes
    WHERE trade_date = {sql_string(trade_day.isoformat())}
    ORDER BY importance_score DESC, source_count DESC, generated_at DESC
    LIMIT {int(limit)};
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 6:
            continue
        rows.append(
            {
                "theme_name": row[0],
                "importance_score": row[1],
                "source_count": row[2],
                "summary": row[3],
                "source_titles": decode_json_list(row[4]),
                "related_concepts": decode_json_list(row[5]),
            }
        )
    return rows


def read_news_mysql(config: Any, start: datetime, end: datetime, min_importance: int, limit: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      source,
      item_kind,
      DATE_FORMAT(published_at, '%Y-%m-%d %H:%i:%s'),
      REPLACE(REPLACE(title, CHAR(9), ' '), CHAR(10), ' '),
      REPLACE(REPLACE(COALESCE(content, ''), CHAR(9), ' '), CHAR(10), ' '),
      url,
      importance
    FROM market_news_items
    WHERE published_at >= {sql_string(start.strftime("%Y-%m-%d %H:%M:%S"))}
      AND published_at <= {sql_string(end.strftime("%Y-%m-%d %H:%M:%S"))}
      AND importance >= {int(min_importance)}
    ORDER BY importance DESC, published_at DESC
    LIMIT {int(limit)};
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 7:
            continue
        rows.append(
            {
                "source": row[0],
                "item_kind": row[1],
                "published_at": row[2],
                "title": row[3],
                "content": row[4],
                "url": row[5],
                "importance": row[6],
            }
        )
    return rows


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


def build_post(trade_day: date, start: datetime, end: datetime, themes: list[dict[str, Any]], news: list[dict[str, Any]]) -> str:
    top_names = [theme_name(item) for item in themes[:2] if theme_name(item)]
    headline = "、".join(top_names) if top_names else "盘前消息"
    lines: list[str] = [
        f"【{format_day(trade_day)}早参：{headline}为盘前主线】",
        "",
        f"消息窗口：{start.strftime('%Y-%m-%d %H:%M')} 至 {end.strftime('%Y-%m-%d %H:%M')}。",
        "",
    ]

    if themes:
        lines.append("一、盘前主线")
        for idx, theme in enumerate(themes[:3], start=1):
            name = theme_name(theme)
            score = compact(theme.get("importance_score"), 20)
            source_count = compact(theme.get("source_count"), 20)
            lines.append(f"{idx}、{name}")
            meta = []
            if score:
                meta.append(f"强度 {score}")
            if source_count:
                meta.append(f"{source_count} 条消息")
            if meta:
                lines.append("信号：" + "，".join(meta))
            titles = theme_titles(theme)
            if titles:
                lines.append("依据：")
                for title in titles:
                    lines.append(f"- {title}")
            concepts = decode_json_list(theme.get("related_concepts"))[:4]
            if concepts:
                lines.append("观察：" + "、".join(concepts))
            lines.append("")

        rest = [theme_name(item) for item in themes[3:7] if theme_name(item)]
        if rest:
            lines.append("二、辅助观察线")
            for name in rest:
                lines.append(f"- {name}")
            lines.append("")
    elif news:
        lines.append("一、盘前重要消息")
        for item in news[:8]:
            title = compact(item.get("title") or item.get("content"), 120)
            if title:
                lines.append(f"- {title}")
        lines.append("")
    else:
        lines.extend(["一、盘前消息", "暂未采集到有效盘前消息，先按昨日盘面和竞价反馈观察。", ""])

    priority = [theme_name(item) for item in themes[:6] if theme_name(item)]
    if priority:
        lines.append("三、今日主题优先级")
        lines.append(" > ".join(priority))
        lines.append("")

    lines.extend(
        [
            "四、盘中验证标准",
            "- 高开后是否承接住；",
            "- 是否有同题材扩散；",
            "- 是否出现领涨票和中军票；",
            "- 成交额是否放大；",
            "- 是否出现回封、抗跌或持续换手结构。",
            "",
            "仅为个人盘前复盘，不构成投资建议。",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def model_payload(trade_day: date, start: datetime, end: datetime, themes: list[dict[str, Any]], news: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": "morning_reference_post",
        "trade_date": trade_day.isoformat(),
        "window": {
            "label": "previous_trade_close_to_now",
            "since": start.strftime("%Y-%m-%d %H:%M:%S"),
            "until": end.strftime("%Y-%m-%d %H:%M:%S"),
        },
        "themes": themes[:8],
        "news": news[:30],
        "output_constraints": {
            "audience": "A-share short-term trader morning reference",
            "must_include": ["strongest themes", "secondary themes", "intraday validation points", "risk disclaimer"],
            "must_not": ["invent stocks", "invent data", "give buy/sell advice"],
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
    else:
        themes, news = read_fallback(root)
    payload = model_payload(trade_day, start, end, themes, news)
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
