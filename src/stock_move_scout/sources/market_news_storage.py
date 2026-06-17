from __future__ import annotations

from pathlib import Path
from typing import Any

from stock_move_scout.db import MySqlConfig, run_mysql, sql_int, sql_json, sql_string


def now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key, "")
    return "" if value is None else str(value)


def limit_text(value: str, limit: int = 512) -> str:
    text = str(value or "")
    return text[:limit]


def safe_id(value: str) -> str:
    import hashlib

    return hashlib.sha1((value or "").encode("utf-8", errors="ignore")).hexdigest()


def import_market_news_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    imported = 0

    def flush(batch: list[str]) -> None:
        nonlocal imported
        if not batch:
            return
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(batch) + "\nCOMMIT;")
        imported += len(batch)

    batch_size = 120
    for row in rows:
        source = text_value(row, "source") or "other"
        if source not in {"cls", "wallstreetcn", "other"}:
            source = "other"
        item_kind = text_value(row, "item_kind") or "other"
        if item_kind not in {"headline", "important", "red", "live", "other"}:
            item_kind = "other"
        source_item_id = text_value(row, "source_item_id") or safe_id(
            "|".join([source, text_value(row, "published_at"), text_value(row, "title"), text_value(row, "url")])
        )[:128]
        statements.append(
            f"""
            INSERT INTO market_news_items(
              source, source_item_id, item_kind, published_at, title, content, url,
              tags, importance, source_status, raw_json, collected_at
            )
            VALUES(
              {sql_string(source)}, {sql_string(source_item_id)}, {sql_string(item_kind)},
              {sql_string(text_value(row, "published_at") or None)},
              {sql_string(limit_text(text_value(row, "title"), 512))},
              {sql_string(text_value(row, "content"))}, {sql_string(text_value(row, "url"))},
              {sql_json(row.get("tags") or [])}, {sql_int(row.get("importance"))},
              {sql_string(text_value(row, "source_status"))}, {sql_json(row.get("raw_json") or row)},
              {sql_string(text_value(row, "collected_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              item_kind=VALUES(item_kind),
              published_at=COALESCE(VALUES(published_at), published_at),
              title=VALUES(title),
              content=VALUES(content),
              url=VALUES(url),
              tags=VALUES(tags),
              importance=VALUES(importance),
              source_status=VALUES(source_status),
              raw_json=VALUES(raw_json),
              collected_at=VALUES(collected_at);
            """
        )
        if len(statements) >= batch_size:
            flush(statements)
            statements = []
    flush(statements)
    return imported


def import_market_news_json(config: MySqlConfig, path: Path) -> int:
    import json

    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return import_market_news_rows(config, rows if isinstance(rows, list) else [])


__all__ = [
    "import_market_news_json",
    "import_market_news_rows",
]
