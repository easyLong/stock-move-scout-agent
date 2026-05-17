from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string


@dataclass(frozen=True)
class MarketNewsItem:
    source: str
    item_kind: str
    published_at: str
    title: str
    content: str
    url: str
    importance: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "item_kind": self.item_kind,
            "published_at": self.published_at,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "importance": self.importance,
        }


def safe_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def read_market_news_window(
    config: MySqlConfig,
    start: datetime,
    end: datetime,
    *,
    min_importance: int = 2,
    limit: int = 30,
) -> list[dict[str, Any]]:
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
            MarketNewsItem(
                source=str(row[0] or ""),
                item_kind=str(row[1] or ""),
                published_at=str(row[2] or ""),
                title=str(row[3] or ""),
                content=str(row[4] or ""),
                url=str(row[5] or ""),
                importance=safe_int(row[6]),
            ).as_dict()
        )
    return rows


__all__ = [
    "MarketNewsItem",
    "read_market_news_window",
    "safe_int",
]
