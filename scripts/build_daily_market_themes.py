#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_daily_market_theme_rows,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_string,
)


THEME_RULES = [
    {
        "theme_name": "黄金贵金属",
        "keywords": ["黄金", "金价", "现货黄金", "贵金属", "避险"],
        "related_industries": ["贵金属", "黄金"],
        "related_concepts": ["黄金概念", "贵金属", "避险资产"],
    },
    {
        "theme_name": "AI算力芯片",
        "keywords": ["AI", "人工智能", "算力", "芯片", "半导体", "英伟达", "GPU", "HBM", "光模块", "CPO"],
        "related_industries": ["半导体", "通信设备", "计算机设备"],
        "related_concepts": ["人工智能", "算力", "芯片", "半导体", "光模块", "CPO"],
    },
    {
        "theme_name": "机器人",
        "keywords": ["机器人", "人形机器人", "具身智能", "减速器", "伺服", "灵巧手"],
        "related_industries": ["自动化设备", "通用设备"],
        "related_concepts": ["机器人概念", "人形机器人", "智能制造"],
    },
    {
        "theme_name": "中东冲突油气军工",
        "keywords": ["伊朗", "以色列", "中东", "停火", "袭击", "霍尔木兹", "油轮", "原油", "军工", "导弹"],
        "related_industries": ["石油石化", "军工", "航运港口"],
        "related_concepts": ["油气", "军工", "航运", "黄金概念", "地缘冲突"],
    },
    {
        "theme_name": "人民币汇率",
        "keywords": ["人民币", "离岸人民币", "汇率", "美元指数", "升值", "贬值"],
        "related_industries": ["航空机场", "造纸", "银行", "纺织服饰"],
        "related_concepts": ["人民币升值", "汇率受益", "出口链"],
    },
    {
        "theme_name": "关税贸易",
        "keywords": ["关税", "贸易", "出口", "进口", "贸易协定", "美欧贸易", "中美"],
        "related_industries": ["跨境电商", "家电", "纺织服饰", "汽车零部件"],
        "related_concepts": ["跨境电商", "外贸", "出口链", "关税豁免"],
    },
    {
        "theme_name": "新能源汽车",
        "keywords": ["新能源车", "电动车", "汽车", "800V", "固态电池", "锂电", "充电桩", "特斯拉"],
        "related_industries": ["汽车零部件", "电池", "电力设备"],
        "related_concepts": ["新能源汽车", "固态电池", "锂电池", "充电桩"],
    },
    {
        "theme_name": "创新药医疗",
        "keywords": ["创新药", "医药", "临床", "获批", "药品", "医疗器械", "CXO"],
        "related_industries": ["化学制药", "生物制品", "医疗器械"],
        "related_concepts": ["创新药", "医药", "CXO", "医疗器械"],
    },
    {
        "theme_name": "低空经济",
        "keywords": ["低空", "eVTOL", "无人机", "飞行汽车", "通航"],
        "related_industries": ["航空装备", "军工", "通信设备"],
        "related_concepts": ["低空经济", "无人机", "飞行汽车"],
    },
    {
        "theme_name": "政策会议",
        "keywords": ["国务院", "中办", "国办", "发改委", "证监会", "央行", "商务部", "会议", "政策"],
        "related_industries": ["综合政策"],
        "related_concepts": ["政策催化", "改革", "稳增长"],
    },
    {
        "theme_name": "地产基建",
        "keywords": ["房地产", "地产", "基建", "城中村", "水利", "重大项目", "REITs"],
        "related_industries": ["房地产开发", "建筑装饰", "水泥建材"],
        "related_concepts": ["房地产", "基建", "水利", "REITs"],
    },
    {
        "theme_name": "金融证券",
        "keywords": ["券商", "证券", "银行", "保险", "金融", "降准", "降息"],
        "related_industries": ["证券", "银行", "保险"],
        "related_concepts": ["大金融", "券商", "银行"],
    },
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def time_window(args: argparse.Namespace) -> tuple[date, datetime, datetime]:
    end = datetime.now()
    if args.until:
        end = parse_time(args.until)
    trade_date = end.date()
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    if args.since:
        start = parse_time(args.since)
    else:
        start = (datetime.combine(trade_date, datetime.min.time()) - timedelta(days=1)).replace(hour=args.after_close_hour)
    return trade_date, start, end


def read_market_news(config: Any, start: datetime, end: datetime, min_importance: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      source,
      source_item_id,
      item_kind,
      DATE_FORMAT(published_at, '%Y-%m-%d %H:%i:%s') AS published_at,
      title,
      COALESCE(content, '') AS content,
      url,
      importance
    FROM market_news_items
    WHERE published_at >= {sql_string(start.strftime("%Y-%m-%d %H:%M:%S"))}
      AND published_at <= {sql_string(end.strftime("%Y-%m-%d %H:%M:%S"))}
      AND importance >= {int(min_importance)}
    ORDER BY importance DESC, published_at DESC;
    """
    keys = ["source", "source_item_id", "item_kind", "published_at", "title", "content", "url", "importance"]
    return [dict(zip(keys, row)) for row in mysql_rows(run_mysql(config, sql, batch=True)) if len(row) >= len(keys)]


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = text.lower()
    hits = []
    for keyword in keywords:
        if keyword.lower() in lowered:
            hits.append(keyword)
    return hits


def build_themes(news_rows: list[dict[str, Any]], trade_date: date, limit_titles: int) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in news_rows:
        text = f"{row.get('title', '')} {row.get('content', '')}"
        for rule in THEME_RULES:
            hits = matched_keywords(text, rule["keywords"])
            if not hits:
                continue
            bucket = buckets.setdefault(
                rule["theme_name"],
                {
                    "trade_date": trade_date.isoformat(),
                    "theme_name": rule["theme_name"],
                    "keywords": set(),
                    "related_industries": rule["related_industries"],
                    "related_concepts": rule["related_concepts"],
                    "items": [],
                    "score": 0.0,
                },
            )
            bucket["keywords"].update(hits)
            importance = int(float(row.get("importance") or 0))
            bucket["score"] += max(1, importance)
            if row.get("item_kind") == "headline":
                bucket["score"] += 1.5
            item = dict(row)
            item["_hit_count"] = len(hits)
            bucket["items"].append(item)

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        items = sorted(
            bucket["items"],
            key=lambda item: (
                0 if re.search(r"早报|早餐", str(item.get("title", ""))) else 1,
                int(item.get("_hit_count") or 0),
                int(float(item.get("importance") or 0)),
                item.get("published_at", ""),
            ),
            reverse=True,
        )
        source_ids = []
        titles = []
        seen_ids = set()
        for item in items:
            source_ref = f"{item.get('source')}:{item.get('source_item_id')}"
            if source_ref in seen_ids:
                continue
            seen_ids.add(source_ref)
            source_ids.append(source_ref)
            title = compact(item.get("title") or item.get("content"), 120)
            if title:
                titles.append(title)
        source_count = len(source_ids)
        score = float(bucket["score"]) + min(source_count, 5) * 0.3
        keywords = sorted(bucket["keywords"])
        summary_title = titles[0] if titles else ""
        rows.append(
            {
                "trade_date": bucket["trade_date"],
                "theme_name": bucket["theme_name"],
                "keywords": keywords,
                "source_count": source_count,
                "source_titles": titles[:limit_titles],
                "source_item_ids": source_ids,
                "related_industries": bucket["related_industries"],
                "related_concepts": bucket["related_concepts"],
                "importance_score": round(score, 2),
                "summary": f"{bucket['theme_name']}：{summary_title}" if summary_title else bucket["theme_name"],
                "generated_at": now_text(),
            }
        )
    rows.sort(key=lambda row: (float(row["importance_score"]), int(row["source_count"])), reverse=True)
    return rows


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build daily catalyst themes from morning market news.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--since", default="")
    parser.add_argument("--until", default="")
    parser.add_argument("--after-close-hour", type=int, default=15)
    parser.add_argument("--min-importance", type=int, default=2)
    parser.add_argument("--limit-titles", type=int, default=5)
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "daily_market_themes.json")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    trade_date, start, end = time_window(args)
    news_rows = read_market_news(config, start, end, args.min_importance)
    rows = build_themes(news_rows, trade_date, args.limit_titles)
    payload = {
        "built_at": now_text(),
        "trade_date": trade_date.isoformat(),
        "since": start.strftime("%Y-%m-%d %H:%M:%S"),
        "until": end.strftime("%Y-%m-%d %H:%M:%S"),
        "news_count": len(news_rows),
        "row_count": len(rows),
        "rows": rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    imported = import_daily_market_theme_rows(config, rows)
    print(json.dumps({"ok": True, "news_count": len(news_rows), "themes": len(rows), "imported": imported, "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
