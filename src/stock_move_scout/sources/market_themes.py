from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string
from stock_move_scout.sources.quote_rows import safe_to_float as to_float


@dataclass(frozen=True)
class MarketTheme:
    theme_name: str
    keywords: list[str]
    related_industries: list[str]
    related_concepts: list[str]
    importance_score: float
    summary: str = ""
    source_count: int = 0
    source_titles: list[str] | None = None
    source_item_ids: list[str] | None = None
    generated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "theme_name": self.theme_name,
            "keywords": self.keywords,
            "related_industries": self.related_industries,
            "related_concepts": self.related_concepts,
            "importance_score": self.importance_score,
            "summary": self.summary,
            "source_count": self.source_count,
            "source_titles": self.source_titles or [],
            "source_item_ids": self.source_item_ids or [],
            "generated_at": self.generated_at,
        }


def parse_json_text(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def split_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[,，、;\s]+", str(value or "")) if part.strip()]


def safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def read_market_themes(config: MySqlConfig, trade_date: date, limit: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      theme_name,
      COALESCE(CAST(keywords AS CHAR), '[]'),
      COALESCE(CAST(related_industries AS CHAR), '[]'),
      COALESCE(CAST(related_concepts AS CHAR), '[]'),
      importance_score,
      COALESCE(summary, ''),
      source_count,
      COALESCE(CAST(source_titles AS CHAR), '[]'),
      COALESCE(CAST(source_item_ids AS CHAR), '[]'),
      DATE_FORMAT(generated_at, '%Y-%m-%d %H:%i:%s')
    FROM daily_market_themes
    WHERE trade_date = {sql_string(trade_date.isoformat())}
    ORDER BY importance_score DESC, source_count DESC, generated_at DESC
    LIMIT {int(limit)};
    """
    themes: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 10:
            continue
        themes.append(
            MarketTheme(
                theme_name=str(row[0] or ""),
                keywords=split_text_list(parse_json_text(row[1]) or []),
                related_industries=split_text_list(parse_json_text(row[2]) or []),
                related_concepts=split_text_list(parse_json_text(row[3]) or []),
                importance_score=to_float(row[4]),
                summary=str(row[5] or ""),
                source_count=safe_int(row[6]),
                source_titles=split_text_list(parse_json_text(row[7]) or []),
                source_item_ids=split_text_list(parse_json_text(row[8]) or []),
                generated_at=str(row[9] or ""),
            ).as_dict()
        )
    return themes


def match_market_themes(row: dict[str, Any], themes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    industry = str(row.get("industry") or "")
    sub_industry = str(row.get("sub_industry") or "")
    concepts = split_text_list(row.get("concepts") or [])
    haystack = " ".join([row.get("name", ""), industry, sub_industry, " ".join(concepts)])
    matches: list[dict[str, Any]] = []
    score = 0.0
    for theme in themes:
        hits: list[str] = []
        for item in theme.get("related_industries") or []:
            item = str(item)
            if item and (item in industry or item in sub_industry or industry in item or sub_industry in item):
                hits.append(item)
        for item in theme.get("related_concepts") or []:
            item = str(item)
            if item and any(item in concept or concept in item for concept in concepts):
                hits.append(item)
        for item in theme.get("keywords") or []:
            item = str(item)
            if item and item in haystack:
                hits.append(item)
        if not hits:
            continue
        weight = min(10.0, float(theme.get("importance_score") or 0) / 3.0)
        score += weight + min(3, len(set(hits)))
        matches.append(
            {
                "theme_name": theme.get("theme_name", ""),
                "hits": sorted(set(hits))[:6],
                "importance_score": theme.get("importance_score", 0),
            }
        )
    matches.sort(key=lambda item: float(item.get("importance_score") or 0), reverse=True)
    return matches[:5], round(score, 2)


__all__ = [
    "MarketTheme",
    "match_market_themes",
    "parse_json_text",
    "read_market_themes",
    "safe_int",
    "split_text_list",
]
