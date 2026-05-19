#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.calendar import previous_trade_close_window
from stock_move_scout.db import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql
from stock_move_scout.sources.market_news_storage import import_market_news_rows


HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}

IMPORTANT_KEYWORDS = [
    "突发",
    "重磅",
    "国务院",
    "央行",
    "证监会",
    "发改委",
    "商务部",
    "关税",
    "制裁",
    "降息",
    "加息",
    "非农",
    "CPI",
    "PPI",
    "美联储",
    "英伟达",
    "AI",
    "半导体",
    "芯片",
    "黄金",
    "原油",
    "人民币",
    "停火",
    "战争",
    "地震",
    "爆炸",
    "大涨",
    "大跌",
    "涨停",
    "跌停",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 2000) -> str:
    text = html.unescape("" if value is None else str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def source_id(source: str, value: Any, title: str, published_at: str) -> str:
    if value not in (None, ""):
        return str(value)[:128]
    digest = hashlib.sha1(f"{source}|{published_at}|{title}".encode("utf-8", errors="ignore")).hexdigest()
    return digest[:40]


def timestamp_to_text(value: Any) -> str:
    try:
        number = int(value)
        if number > 10_000_000_000:
            number = number // 1000
        return datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def known_trade_dates(config: Any, until: datetime) -> list[str]:
    sql = f"""
    SELECT DISTINCT DATE(scanned_at) AS trade_day
    FROM scan_runs
    WHERE accepted=1
      AND scanned_at < '{until.strftime("%Y-%m-%d 00:00:00")}'
    ORDER BY trade_day DESC
    LIMIT 30;
    """
    try:
        return [row[0] for row in mysql_rows(run_mysql(config, sql, batch=True)) if row and row[0]]
    except Exception:
        return []


def parse_time_range(args: argparse.Namespace, config: Any | None = None) -> tuple[datetime, datetime]:
    end = datetime.now()
    if args.until:
        end = datetime.strptime(args.until, "%Y-%m-%d %H:%M:%S")
    if args.since:
        start = datetime.strptime(args.since, "%Y-%m-%d %H:%M:%S")
    else:
        _, start, _ = previous_trade_close_window(
            end,
            after_close_hour=args.after_close_hour,
            known_trade_dates=known_trade_dates(config, end) if config else None,
        )
    return start, end


def in_range(published_at: str, start: datetime, end: datetime) -> bool:
    if not published_at:
        return True
    try:
        value = datetime.strptime(published_at, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return True
    return start <= value <= end


def fetch_text(url: str, timeout: int) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.content.decode("utf-8", errors="replace")


def next_data(html_text: str) -> dict[str, Any]:
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html_text, re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def ssr_data(html_text: str) -> dict[str, Any]:
    marker = "__SSR__ = "
    start = html_text.find(marker)
    if start < 0:
        return {}
    start += len(marker)
    end = html_text.find("</script>", start)
    if end < 0:
        return {}
    text = html_text[start:end].strip()
    if text.endswith(";"):
        text = text[:-1]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def important_score(title: str, content: str, base: int = 1) -> int:
    text = f"{title} {content}"
    score = base
    if any(keyword in text for keyword in IMPORTANT_KEYWORDS):
        score = max(score, 2)
    if "早报" in title or "早餐" in title or "头条" in title:
        score = max(score, 3)
    return score


def cls_home_items(timeout: int) -> list[dict[str, Any]]:
    data = next_data(fetch_text("https://www.cls.cn/", timeout))
    index = (((data.get("props") or {}).get("initialState") or {}).get("indexPage") or {})
    assemble = index.get("assembleData") or {}
    result: list[dict[str, Any]] = []
    for item in assemble.get("top_article") or []:
        if not isinstance(item, dict):
            continue
        title = compact(item.get("title"), 512)
        content = compact(item.get("brief"), 2000)
        published_at = timestamp_to_text(item.get("ctime"))
        result.append(
            {
                "source": "cls",
                "source_item_id": source_id("cls", item.get("id"), title, published_at),
                "item_kind": "headline",
                "published_at": published_at,
                "title": title,
                "content": content,
                "url": item.get("external_link") or item.get("schema") or f"https://www.cls.cn/detail/{item.get('id')}",
                "tags": item.get("tags") or [],
                "importance": 3,
                "source_status": "cls_home_top_article",
                "raw_json": item,
            }
        )
    return result


def cls_telegraph_items(timeout: int) -> list[dict[str, Any]]:
    data = next_data(fetch_text("https://www.cls.cn/telegraph", timeout))
    items = ((((data.get("props") or {}).get("initialState") or {}).get("telegraph") or {}).get("telegraphList") or [])
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        content = compact(item.get("brief") or item.get("content"), 2000)
        title = compact(item.get("title") or content, 512)
        published_at = timestamp_to_text(item.get("ctime"))
        base = 2 if item.get("level") not in ("", "C", None) or item.get("recommend") or item.get("bold") or item.get("is_top") else 1
        importance = important_score(title, content, base)
        kind = "important" if importance >= 2 else "live"
        result.append(
            {
                "source": "cls",
                "source_item_id": source_id("cls", item.get("id"), title, published_at),
                "item_kind": kind,
                "published_at": published_at,
                "title": title,
                "content": content,
                "url": item.get("shareurl") or f"https://www.cls.cn/telegraph/{item.get('id')}",
                "tags": item.get("tags") or [],
                "importance": importance,
                "source_status": "cls_telegraph",
                "raw_json": item,
            }
        )
    return result


def wscn_home_items(timeout: int) -> list[dict[str, Any]]:
    data = ssr_data(fetch_text("https://wallstreetcn.com/", timeout))
    page = (((data.get("state") or {}).get("default") or {}).get("children") or {}).get("default") or {}
    payload = ((page.get("children") or {}).get("default") or {}).get("data") or page.get("data") or {}
    result: list[dict[str, Any]] = []
    for item in payload.get("slides") or []:
        if not isinstance(item, dict):
            continue
        title = compact(item.get("title"), 512)
        content = compact(item.get("content_short"), 2000)
        published_at = timestamp_to_text(item.get("display_time"))
        result.append(
            {
                "source": "wallstreetcn",
                "source_item_id": source_id("wallstreetcn", item.get("id"), title, published_at),
                "item_kind": "headline",
                "published_at": published_at,
                "title": title,
                "content": content,
                "url": item.get("uri") or "",
                "tags": [],
                "importance": 3,
                "source_status": "wscn_home_slide",
                "raw_json": item,
            }
        )
    return result


def wscn_live_items(timeout: int) -> list[dict[str, Any]]:
    data = ssr_data(fetch_text("https://wallstreetcn.com/live", timeout))
    page = (((data.get("state") or {}).get("default") or {}).get("children") or {}).get("default") or {}
    payload = ((page.get("children") or {}).get("default") or {}).get("data") or page.get("data") or {}
    result: list[dict[str, Any]] = []
    for item in payload.get("lives") or []:
        if not isinstance(item, dict):
            continue
        title = compact(item.get("title"), 512)
        content = compact(item.get("content_text") or item.get("content"), 2000)
        published_at = timestamp_to_text(item.get("display_time"))
        base = 2 if int(item.get("score") or 0) >= 2 else 1
        importance = important_score(title, content, base)
        kind = "headline" if "早餐" in title else ("important" if importance >= 2 else "live")
        result.append(
            {
                "source": "wallstreetcn",
                "source_item_id": source_id("wallstreetcn", item.get("id"), title or content, published_at),
                "item_kind": kind,
                "published_at": published_at,
                "title": title or compact(content, 80),
                "content": content,
                "url": item.get("uri") or "",
                "tags": item.get("tags") or [],
                "importance": importance,
                "source_status": "wscn_live",
                "raw_json": item,
            }
        )
    return result


def collect(args: argparse.Namespace, config: Any | None = None) -> dict[str, Any]:
    start, end = parse_time_range(args, config)
    rows: list[dict[str, Any]] = []
    statuses: list[str] = []
    collectors = [
        ("cls_home", cls_home_items),
        ("cls_telegraph", cls_telegraph_items),
        ("wscn_home", wscn_home_items),
        ("wscn_live", wscn_live_items),
    ]
    for name, func in collectors:
        try:
            part = func(args.timeout)
            statuses.append(f"{name}:ok:{len(part)}")
            rows.extend(part[: args.limit_per_source])
        except Exception as exc:
            statuses.append(f"{name}:error:{type(exc).__name__}:{exc}")
    filtered: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if not in_range(row.get("published_at", ""), start, end):
            continue
        if args.important_only and int(row.get("importance") or 0) < 2:
            continue
        key = (str(row.get("source")), str(row.get("source_item_id")))
        if key in seen:
            continue
        seen.add(key)
        row["collected_at"] = now_text()
        filtered.append(row)
    filtered.sort(key=lambda item: (item.get("published_at") or "", int(item.get("importance") or 0)), reverse=True)
    return {
        "built_at": now_text(),
        "since": start.strftime("%Y-%m-%d %H:%M:%S"),
        "until": end.strftime("%Y-%m-%d %H:%M:%S"),
        "important_only": args.important_only,
        "source_status": ";".join(statuses),
        "row_count": len(filtered),
        "rows": filtered[: args.limit],
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect daily morning market headlines from CLS and Wallstreetcn.")
    add_mysql_args(parser)
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "morning_market_news.json")
    parser.add_argument("--since", default="", help="Inclusive start time, format YYYY-MM-DD HH:MM:SS. Defaults to yesterday 15:00.")
    parser.add_argument("--until", default="", help="Inclusive end time, format YYYY-MM-DD HH:MM:SS. Defaults to now.")
    parser.add_argument("--after-close-hour", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--limit-per-source", type=int, default=50)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--important-only", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args) if args.mysql_enabled else None
    payload = collect(args, config)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    imported = 0
    if args.mysql_enabled and config is not None:
        imported = import_market_news_rows(config, payload["rows"])
    print(json.dumps({"ok": True, "rows": payload["row_count"], "imported": imported, "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
