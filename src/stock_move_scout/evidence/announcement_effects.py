from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_number, sql_string
from stock_move_scout.feed.root_cache import enqueue_root_evidence_cache_dirty_many


NOISE_KEYWORDS = [
    "\u80a1\u4e1c\u5927\u4f1a",
    "\u8463\u4e8b\u4f1a",
    "\u76d1\u4e8b\u4f1a",
    "\u7ae0\u7a0b",
    "\u5236\u5ea6",
    "\u72ec\u7acb\u8463\u4e8b",
    "\u6cd5\u5f8b\u610f\u89c1\u4e66",
    "\u4f1a\u8bae\u8d44\u6599",
    "\u503a\u5238\u4ed8\u606f",
    "\u66f4\u6b63\u516c\u544a",
    "\u63d0\u793a\u6027\u516c\u544a",
    "\u80a1\u4ef7\u5f02\u52a8",
    "\u5f02\u5e38\u6ce2\u52a8",
]

CATALYST_RULES = [
    (
        "restructuring",
        "\u91cd\u7ec4/\u6536\u8d2d",
        [
            "\u91cd\u5927\u8d44\u4ea7\u91cd\u7ec4",
            "\u8d44\u4ea7\u91cd\u7ec4",
            "\u6536\u8d2d",
            "\u5e76\u8d2d",
            "\u8d44\u4ea7\u6ce8\u5165",
            "\u63a7\u5236\u6743",
            "\u80a1\u6743\u8f6c\u8ba9",
            "\u8d2d\u4e70\u8d44\u4ea7",
            "\u73b0\u91d1\u6536\u8d2d",
        ],
    ),
    (
        "contract",
        "\u5408\u540c/\u8ba2\u5355",
        [
            "\u91cd\u5927\u5408\u540c",
            "\u7ecf\u8425\u5408\u540c",
            "\u9500\u552e\u5408\u540c",
            "\u91c7\u8d2d\u5408\u540c",
            "\u8ba2\u5355",
            "\u4e2d\u6807",
            "\u5b9a\u70b9",
            "\u4f9b\u8d27",
            "\u6218\u7565\u5408\u4f5c",
            "\u6846\u67b6\u534f\u8bae",
        ],
    ),
    (
        "earnings",
        "\u4e1a\u7ee9",
        [
            "\u4e1a\u7ee9\u9884\u544a",
            "\u4e1a\u7ee9\u5feb\u62a5",
            "\u51c0\u5229\u6da6",
            "\u626d\u4e8f",
            "\u8425\u6536",
            "\u540c\u6bd4\u589e\u957f",
            "\u4e00\u5b63\u62a5",
            "\u534a\u5e74\u62a5",
            "\u5e74\u5ea6\u62a5\u544a",
        ],
    ),
    (
        "buyback",
        "\u56de\u8d2d/\u589e\u6301",
        ["\u56de\u8d2d", "\u589e\u6301", "\u5458\u5de5\u6301\u80a1", "\u80a1\u6743\u6fc0\u52b1"],
    ),
    (
        "capacity_customer",
        "\u4ea7\u80fd/\u5ba2\u6237",
        [
            "\u6295\u4ea7",
            "\u4ea7\u80fd",
            "\u91cf\u4ea7",
            "\u5ba2\u6237",
            "\u4f9b\u5e94\u5546",
            "\u4ea7\u54c1\u8ba4\u8bc1",
            "\u6ce8\u518c\u8bc1",
            "\u4e34\u5e8a",
        ],
    ),
]


@dataclass
class Candidate:
    root_item_id: int
    code: str
    stock_name: str
    item_kind: str
    item_key: str
    item_date: str
    title: str
    content: str
    url: str
    tags: str
    importance: int
    collected_at: str
    updated_at: str
    event_subtype: str
    tag: str
    event_key: str


def ensure_tables(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_daily_bars (
      code CHAR(6) NOT NULL,
      trade_date DATE NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      open_price DECIMAL(12,4) NULL,
      high_price DECIMAL(12,4) NULL,
      low_price DECIMAL(12,4) NULL,
      close_price DECIMAL(12,4) NULL,
      pct_change DECIMAL(10,4) NULL,
      volume BIGINT NULL,
      amount DECIMAL(20,2) NULL,
      source VARCHAR(64) NOT NULL DEFAULT 'akshare_stock_zh_a_hist',
      raw_json JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      PRIMARY KEY (code, trade_date),
      KEY idx_stock_daily_bars_day (trade_date),
      KEY idx_stock_daily_bars_code_day (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_announcement_effects (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      root_item_id BIGINT UNSIGNED NOT NULL,
      event_key VARCHAR(96) NOT NULL,
      event_date DATE NOT NULL,
      event_type VARCHAR(32) NOT NULL DEFAULT '',
      event_subtype VARCHAR(64) NOT NULL DEFAULT '',
      title VARCHAR(512) NOT NULL DEFAULT '',
      summary TEXT NULL,
      tag VARCHAR(64) NOT NULL DEFAULT '',
      base_trade_date DATE NULL,
      base_close DECIMAL(12,4) NULL,
      verify_trade_date DATE NULL,
      verify_close DECIMAL(12,4) NULL,
      verify_pct DECIMAL(10,4) NULL,
      verify_limit_up TINYINT NOT NULL DEFAULT 0,
      verify_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      current_trade_date DATE NULL,
      current_close DECIMAL(12,4) NULL,
      current_pct_from_base DECIMAL(10,4) NULL,
      avg_pct_from_base DECIMAL(10,4) NULL,
      max_pct_from_base DECIMAL(10,4) NULL,
      min_low_pct_from_base DECIMAL(10,4) NULL,
      effect_status ENUM('unverified','active','faded','ignored') NOT NULL DEFAULT 'unverified',
      effect_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      faded_trade_date DATE NULL,
      faded_reason VARCHAR(255) NOT NULL DEFAULT '',
      last_checked_trade_date DATE NULL,
      raw_json JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_announcement_effect_root (root_item_id),
      UNIQUE KEY uk_announcement_effect_key (code, event_key),
      KEY idx_announcement_effect_code_status (code, effect_status, event_date),
      KEY idx_announcement_effect_date_status (event_date, effect_status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def clean_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def to_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        text = str(value or "").replace("%", "").replace(",", "").strip()
        return float(text) if text else None
    except Exception:
        return None


def to_int(value: Any) -> int:
    parsed = to_float(value)
    return int(parsed) if parsed is not None else 0


def compact(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
        except Exception:
            return ""
    return ""


def event_key_for(row: dict[str, Any]) -> str:
    raw_key = str(row.get("item_key") or "").strip()
    if raw_key:
        seed = f"{row.get('code')}|{row.get('item_kind')}|{row.get('item_date')}|{raw_key}"
    else:
        seed = f"{row.get('code')}|{row.get('item_kind')}|{row.get('item_date')}|{row.get('title')}|{row.get('url')}"
    return hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:40]


def classify_candidate(row: dict[str, Any]) -> tuple[str, str] | None:
    text = compact(
        " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("content") or ""),
                str(row.get("tags") or ""),
                str(row.get("source_section") or ""),
            ]
        ),
        3000,
    )
    if not text:
        return None
    if any(keyword in text for keyword in NOISE_KEYWORDS):
        if not any(keyword in text for _, _, keywords in CATALYST_RULES for keyword in keywords):
            return None
    for subtype, tag, keywords in CATALYST_RULES:
        if any(keyword in text for keyword in keywords):
            return subtype, tag
    return None


def fetch_candidates(
    config: MySqlConfig,
    *,
    trade_date: str,
    lookback_days: int,
    code: str = "",
    limit: int = 0,
) -> list[Candidate]:
    code_filter = f"AND code={sql_string(code)}" if code else ""
    limit_sql = f"LIMIT {int(limit)}" if limit and int(limit) > 0 else ""
    sql = f"""
    SELECT
      id,
      code,
      stock_name,
      item_kind,
      COALESCE(item_key, ''),
      DATE_FORMAT(item_date, '%Y-%m-%d'),
      COALESCE(title, ''),
      COALESCE(content, ''),
      COALESCE(url, ''),
      COALESCE(tags, ''),
      COALESCE(importance, 0),
      DATE_FORMAT(collected_at, '%Y-%m-%d %H:%i:%s'),
      DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s'),
      COALESCE(source_section, '')
    FROM stock_ths_root_items
    WHERE item_kind IN ('announcement','important_event')
      AND item_date IS NOT NULL
      AND item_date <= {sql_string(trade_date)}
      AND item_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL {int(lookback_days)} DAY)
      {code_filter}
    ORDER BY item_date DESC, source_rank ASC, updated_at DESC
    {limit_sql};
    """
    out: list[Candidate] = []
    for raw in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(raw) < 14:
            continue
        row = {
            "id": raw[0],
            "code": raw[1],
            "stock_name": raw[2],
            "item_kind": raw[3],
            "item_key": raw[4],
            "item_date": raw[5],
            "title": raw[6],
            "content": raw[7],
            "url": raw[8],
            "tags": raw[9],
            "importance": raw[10],
            "collected_at": raw[11],
            "updated_at": raw[12],
            "source_section": raw[13],
        }
        classified = classify_candidate(row)
        if not classified:
            continue
        subtype, tag = classified
        out.append(
            Candidate(
                root_item_id=int(row["id"]),
                code=clean_code(row["code"]),
                stock_name=row["stock_name"],
                item_kind=row["item_kind"],
                item_key=row["item_key"],
                item_date=row["item_date"],
                title=compact(row["title"], 500),
                content=compact(row["content"], 1200),
                url=row["url"],
                tags=row["tags"],
                importance=to_int(row["importance"]),
                collected_at=row["collected_at"],
                updated_at=row["updated_at"],
                event_subtype=subtype,
                tag=tag,
                event_key=event_key_for(row),
            )
        )
    return out


def fetch_root_stock_codes(
    config: MySqlConfig,
    *,
    trade_date: str,
    lookback_days: int,
    code: str = "",
) -> dict[str, str]:
    code_filter = f"AND code={sql_string(code)}" if code else ""
    sql = f"""
    SELECT code, SUBSTRING_INDEX(GROUP_CONCAT(stock_name ORDER BY item_date DESC, updated_at DESC), ',', 1) AS stock_name
    FROM stock_ths_root_items
    WHERE item_kind IN ('announcement','important_event')
      AND item_date IS NOT NULL
      AND item_date <= {sql_string(trade_date)}
      AND item_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL {int(lookback_days)} DAY)
      {code_filter}
    GROUP BY code;
    """
    return {
        clean_code(row[0]): row[1]
        for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True))
        if len(row) >= 2 and clean_code(row[0])
    }


def _ak_col(row: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return None


def json_safe(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    return value


OFFICIAL_DAILY_BAR_SOURCES = {
    "akshare_stock_zh_a_hist",
    "akshare_stock_zh_a_daily",
    "akshare_stock_zh_a_hist_tx",
}


def market_symbol(code: str) -> str:
    prefix = "sh" if code.startswith(("6", "9")) else "bj" if code.startswith(("4", "8", "920")) else "sz"
    return f"{prefix}{code}"


def _daily_bars_from_df(df: pd.DataFrame, code: str, stock_name: str, source: str) -> list[dict[str, Any]]:
    if df is None or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for item in df.to_dict("records"):
        trade_date = normalize_date(_ak_col(item, "\u65e5\u671f", "date", "trade_date"))
        if not trade_date:
            continue
        rows.append(
            {
                "code": code,
                "trade_date": trade_date,
                "stock_name": stock_name,
                "open_price": to_float(_ak_col(item, "\u5f00\u76d8", "open")),
                "high_price": to_float(_ak_col(item, "\u6700\u9ad8", "high")),
                "low_price": to_float(_ak_col(item, "\u6700\u4f4e", "low")),
                "close_price": to_float(_ak_col(item, "\u6536\u76d8", "close")),
                "pct_change": to_float(_ak_col(item, "\u6da8\u8dcc\u5e45", "pct_change")),
                "volume": to_int(_ak_col(item, "\u6210\u4ea4\u91cf", "volume")),
                "amount": to_float(_ak_col(item, "\u6210\u4ea4\u989d", "amount")),
                "source": source,
                "raw_json": {str(k): json_safe(v) for k, v in item.items()},
            }
        )
    return rows


def fetch_daily_bars_from_ak_hist(code: str, stock_name: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="",
        timeout=15,
    )
    return _daily_bars_from_df(df, code, stock_name, "akshare_stock_zh_a_hist")


def fetch_daily_bars_from_ak_daily(code: str, stock_name: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    df = ak.stock_zh_a_daily(
        symbol=market_symbol(code),
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="",
    )
    return _daily_bars_from_df(df, code, stock_name, "akshare_stock_zh_a_daily")


def fetch_daily_bars_from_ak_tx(code: str, stock_name: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    df = ak.stock_zh_a_hist_tx(
        symbol=market_symbol(code),
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="",
        timeout=15,
    )
    return _daily_bars_from_df(df, code, stock_name, "akshare_stock_zh_a_hist_tx")


def fetch_daily_bars_from_ak(
    code: str,
    stock_name: str,
    start_date: str,
    end_date: str,
    *,
    disabled_sources: set[str] | None = None,
    source_failures: dict[str, int] | None = None,
    disable_after_failures: int = 3,
) -> list[dict[str, Any]]:
    errors: list[str] = []
    fetchers = (
        ("akshare_stock_zh_a_hist", fetch_daily_bars_from_ak_hist),
        ("akshare_stock_zh_a_daily", fetch_daily_bars_from_ak_daily),
        ("akshare_stock_zh_a_hist_tx", fetch_daily_bars_from_ak_tx),
    )
    disabled = disabled_sources or set()
    for source, fetcher in fetchers:
        if source in disabled:
            continue
        try:
            rows = fetcher(code, stock_name, start_date, end_date)
            if rows:
                return rows
        except Exception as exc:
            errors.append(f"{source}: {str(exc)[:180]}")
            if source_failures is not None:
                source_failures[source] = source_failures.get(source, 0) + 1
                if source == "akshare_stock_zh_a_hist" and source_failures[source] >= disable_after_failures:
                    disabled.add(source)
    raise RuntimeError("; ".join(errors) or "akshare_daily_bars_empty")


def fetch_daily_bars_from_local_ticks(config: MySqlConfig, code: str, start_date: str, end_date: str) -> list[dict[str, Any]]:
    sql = f"""
    WITH ticks AS (
      SELECT
        DATE(sr.scanned_at) AS trade_date,
        sr.scanned_at AS tick_at,
        sm.name AS stock_name,
        sm.price AS price,
        sm.pct_change AS pct_change,
        sm.amount AS amount,
        sm.volume AS volume
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      WHERE sm.code={sql_string(code)}
        AND DATE(sr.scanned_at) >= {sql_string(start_date)}
        AND DATE(sr.scanned_at) <= {sql_string(end_date)}
        AND sm.price IS NOT NULL
      UNION ALL
      SELECT
        DATE(w.ended_at) AS trade_date,
        w.ended_at AS tick_at,
        wm.name AS stock_name,
        wm.latest_price AS price,
        wm.latest_pct_change AS pct_change,
        wm.amount AS amount,
        NULL AS volume
      FROM windows w
      JOIN window_movers wm ON wm.window_id=w.id
      WHERE wm.code={sql_string(code)}
        AND DATE(w.ended_at) >= {sql_string(start_date)}
        AND DATE(w.ended_at) <= {sql_string(end_date)}
        AND w.status='done'
        AND wm.latest_price IS NOT NULL
    )
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      SUBSTRING_INDEX(GROUP_CONCAT(stock_name ORDER BY tick_at DESC), ',', 1),
      SUBSTRING_INDEX(GROUP_CONCAT(price ORDER BY tick_at ASC), ',', 1) AS open_price,
      MAX(price) AS high_price,
      MIN(price) AS low_price,
      SUBSTRING_INDEX(GROUP_CONCAT(price ORDER BY tick_at DESC), ',', 1) AS close_price,
      SUBSTRING_INDEX(GROUP_CONCAT(pct_change ORDER BY tick_at DESC), ',', 1) AS pct_change,
      MAX(volume) AS volume,
      MAX(amount) AS amount,
      COUNT(*) AS tick_count,
      MIN(tick_at),
      MAX(tick_at)
    FROM ticks
    GROUP BY trade_date
    ORDER BY trade_date ASC;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 12:
            continue
        rows.append(
            {
                "code": code,
                "trade_date": row[0],
                "stock_name": row[1],
                "open_price": to_float(row[2]),
                "high_price": to_float(row[3]),
                "low_price": to_float(row[4]),
                "close_price": to_float(row[5]),
                "pct_change": to_float(row[6]),
                "volume": to_int(row[7]),
                "amount": to_float(row[8]),
                "source": "local_intraday_ticks",
                "raw_json": {"tick_count": to_int(row[9]), "first_tick_at": row[10], "last_tick_at": row[11]},
            }
        )
    return rows


def upsert_daily_bars(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        incoming_source = str(row.get("source") or "akshare_stock_zh_a_hist")
        incoming_is_fallback = incoming_source == "local_intraday_ticks"
        statements.append(
            f"""
            INSERT INTO stock_daily_bars(
              code, trade_date, stock_name, open_price, high_price, low_price, close_price,
              pct_change, volume, amount, source, raw_json
            ) VALUES (
              {sql_string(row['code'])},
              {sql_string(row['trade_date'])},
              {sql_string(row.get('stock_name') or '')},
              {sql_number(row.get('open_price'))},
              {sql_number(row.get('high_price'))},
              {sql_number(row.get('low_price'))},
              {sql_number(row.get('close_price'))},
              {sql_number(row.get('pct_change'))},
              {to_int(row.get('volume'))},
              {sql_number(row.get('amount'))},
              {sql_string(incoming_source)},
              {sql_json(row.get('raw_json') or {})}
            )
            ON DUPLICATE KEY UPDATE
              source=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.source, VALUES(source)),
              stock_name=COALESCE(NULLIF(VALUES(stock_name), ''), stock_name),
              open_price=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.open_price, VALUES(open_price)),
              high_price=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.high_price, VALUES(high_price)),
              low_price=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.low_price, VALUES(low_price)),
              close_price=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.close_price, VALUES(close_price)),
              pct_change=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.pct_change, VALUES(pct_change)),
              volume=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.volume, VALUES(volume)),
              amount=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.amount, VALUES(amount)),
              raw_json=IF({1 if incoming_is_fallback else 0}=1 AND stock_daily_bars.source <> 'local_intraday_ticks', stock_daily_bars.raw_json, VALUES(raw_json)),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    for idx in range(0, len(statements), 300):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 300]) + "\nCOMMIT;")
    return len(rows)


def fetch_existing_effects(config: MySqlConfig, root_ids: list[int]) -> dict[int, dict[str, str]]:
    clean_ids = [str(int(item)) for item in root_ids if int(item) > 0]
    if not clean_ids:
        return {}
    sql = f"""
    SELECT
      root_item_id,
      effect_status,
      COALESCE(DATE_FORMAT(faded_trade_date, '%Y-%m-%d'), ''),
      COALESCE(faded_reason, '')
    FROM stock_announcement_effects
    WHERE root_item_id IN ({','.join(clean_ids)});
    """
    out: dict[int, dict[str, str]] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) >= 4:
            out[int(row[0])] = {"status": row[1], "faded_trade_date": row[2], "faded_reason": row[3]}
    return out


def read_daily_bars(
    config: MySqlConfig,
    code: str,
    start_date: str,
    end_date: str,
    *,
    official_only: bool = True,
) -> list[dict[str, Any]]:
    source_filter = (
        "AND source IN ('akshare_stock_zh_a_hist','akshare_stock_zh_a_daily','akshare_stock_zh_a_hist_tx')"
        if official_only
        else ""
    )
    sql = f"""
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      close_price,
      low_price,
      pct_change
    FROM stock_daily_bars
    WHERE code={sql_string(code)}
      AND trade_date >= {sql_string(start_date)}
      AND trade_date <= {sql_string(end_date)}
      AND close_price IS NOT NULL
      {source_filter}
    ORDER BY trade_date ASC;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) >= 4:
            rows.append(
                {
                    "trade_date": row[0],
                    "close": to_float(row[1]),
                    "low": to_float(row[2]),
                    "pct_change": to_float(row[3]),
                }
            )
    return rows


def limit_up_threshold(code: str) -> float:
    if code.startswith(("30", "68")):
        return 19.0
    if code.startswith(("4", "8", "920")):
        return 29.0
    return 9.5


def pct_from_base(value: float | None, base: float | None) -> float | None:
    if value is None or base is None or base <= 0:
        return None
    return (value / base - 1.0) * 100.0


def verify_score(code: str, pct_change: float | None) -> tuple[float, bool]:
    if pct_change is None:
        return 0.0, False
    is_limit = pct_change >= limit_up_threshold(code)
    if is_limit:
        return 100.0, True
    if pct_change >= 8:
        return 85.0, False
    if pct_change >= 5:
        return 70.0, False
    return 0.0, False


def compute_effect(
    candidate: Candidate,
    bars: list[dict[str, Any]],
    *,
    checked_trade_date: str,
    existing: dict[str, str] | None = None,
    stale_after_days: int = 31,
) -> dict[str, Any]:
    base = None
    verify = None
    for bar in bars:
        if bar["trade_date"] <= candidate.item_date:
            base = bar
        elif verify is None:
            verify = bar
            break
    if not base or not verify or not base.get("close") or not verify.get("close"):
        status = "unverified"
        score = 0.0
        limit_flag = False
        verify_pct = None
    else:
        verify_pct = pct_from_base(verify.get("close"), base.get("close"))
        score, limit_flag = verify_score(candidate.code, verify_pct)
        status = "active" if score > 0 else "ignored"

    post_rows: list[dict[str, Any]] = []
    if base and verify:
        post_rows = [bar for bar in bars if verify["trade_date"] <= bar["trade_date"] <= checked_trade_date]

    current = post_rows[-1] if post_rows else None
    close_pcts = [pct_from_base(bar.get("close"), base.get("close") if base else None) for bar in post_rows]
    low_pcts = [pct_from_base(bar.get("low"), base.get("close") if base else None) for bar in post_rows]
    close_pcts = [value for value in close_pcts if value is not None]
    low_pcts = [value for value in low_pcts if value is not None]
    avg_pct = sum(close_pcts) / len(close_pcts) if close_pcts else None
    max_pct = max(close_pcts) if close_pcts else None
    min_low_pct = min(low_pcts) if low_pcts else None

    faded_date = ""
    faded_reason = ""
    if status == "active" and base:
        for bar in post_rows:
            low = bar.get("low")
            if low is not None and low <= float(base["close"]):
                status = "faded"
                faded_date = bar["trade_date"]
                faded_reason = "price_back_to_announcement_base"
                break

    if existing and existing.get("status") == "faded":
        status = "faded"
        faded_date = existing.get("faded_trade_date", "") or faded_date
        faded_reason = existing.get("faded_reason", "") or faded_reason or "previously_faded"

    stale_cutoff = datetime.strptime(checked_trade_date, "%Y-%m-%d").date() - timedelta(days=max(1, int(stale_after_days)))
    event_day = datetime.strptime(candidate.item_date, "%Y-%m-%d").date()
    if event_day < stale_cutoff:
        if score > 0:
            status = "faded"
            faded_date = faded_date or stale_cutoff.isoformat()
            faded_reason = faded_reason or f"expired_after_{int(stale_after_days)}_days"
        else:
            status = "ignored"
            faded_date = faded_date or ""
            faded_reason = faded_reason or f"stale_after_{int(stale_after_days)}_days"

    if status == "active":
        effect_score = min(100.0, max(0.0, (avg_pct or 0.0) * 10.0))
    else:
        effect_score = 0.0

    return {
        "candidate": candidate,
        "base_trade_date": base["trade_date"] if base else "",
        "base_close": base.get("close") if base else None,
        "verify_trade_date": verify["trade_date"] if verify else "",
        "verify_close": verify.get("close") if verify else None,
        "verify_pct": verify_pct,
        "verify_limit_up": limit_flag,
        "verify_score": score,
        "current_trade_date": current["trade_date"] if current else "",
        "current_close": current.get("close") if current else None,
        "current_pct_from_base": pct_from_base(current.get("close"), base.get("close")) if current and base else None,
        "avg_pct_from_base": avg_pct,
        "max_pct_from_base": max_pct,
        "min_low_pct_from_base": min_low_pct,
        "effect_status": status,
        "effect_score": effect_score,
        "faded_trade_date": faded_date,
        "faded_reason": faded_reason,
        "last_checked_trade_date": checked_trade_date,
    }


def upsert_effects(config: MySqlConfig, effects: list[dict[str, Any]]) -> int:
    if not effects:
        return 0
    statements: list[str] = []
    for effect in effects:
        item: Candidate = effect["candidate"]
        base_trade_date_sql = sql_string(effect.get("base_trade_date") or None)
        verify_trade_date_sql = sql_string(effect.get("verify_trade_date") or None)
        current_trade_date_sql = sql_string(effect.get("current_trade_date") or None)
        faded_trade_date_sql = sql_string(effect.get("faded_trade_date") or None)
        last_checked_trade_date_sql = sql_string(effect.get("last_checked_trade_date") or None)
        raw_json = {
            "source_table": "stock_ths_root_items",
            "source_key": f"stock_ths_root_items:{item.root_item_id}",
            "item_key": item.item_key,
            "url": item.url,
            "tags": item.tags,
            "importance": item.importance,
            "collected_at": item.collected_at,
            "updated_at": item.updated_at,
        }
        statements.append(
            f"""
            INSERT INTO stock_announcement_effects(
              code, stock_name, root_item_id, event_key, event_date, event_type, event_subtype,
              title, summary, tag, base_trade_date, base_close, verify_trade_date, verify_close,
              verify_pct, verify_limit_up, verify_score, current_trade_date, current_close,
              current_pct_from_base, avg_pct_from_base, max_pct_from_base, min_low_pct_from_base,
              effect_status, effect_score, faded_trade_date, faded_reason, last_checked_trade_date, raw_json
            ) VALUES (
              {sql_string(item.code)},
              {sql_string(item.stock_name)},
              {int(item.root_item_id)},
              {sql_string(item.event_key)},
              {sql_string(item.item_date)},
              {sql_string(item.item_kind)},
              {sql_string(item.event_subtype)},
              {sql_string(item.title)},
              {sql_string(item.content)},
              {sql_string(item.tag)},
              {base_trade_date_sql},
              {sql_number(effect.get('base_close'))},
              {verify_trade_date_sql},
              {sql_number(effect.get('verify_close'))},
              {sql_number(effect.get('verify_pct'))},
              {1 if effect.get('verify_limit_up') else 0},
              {sql_number(effect.get('verify_score'))},
              {current_trade_date_sql},
              {sql_number(effect.get('current_close'))},
              {sql_number(effect.get('current_pct_from_base'))},
              {sql_number(effect.get('avg_pct_from_base'))},
              {sql_number(effect.get('max_pct_from_base'))},
              {sql_number(effect.get('min_low_pct_from_base'))},
              {sql_string(effect['effect_status'])},
              {sql_number(effect.get('effect_score'))},
              {faded_trade_date_sql},
              {sql_string(effect.get('faded_reason') or '')},
              {last_checked_trade_date_sql},
              {sql_json(raw_json)}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name),
              event_key=VALUES(event_key),
              event_date=VALUES(event_date),
              event_type=VALUES(event_type),
              event_subtype=VALUES(event_subtype),
              title=VALUES(title),
              summary=VALUES(summary),
              tag=VALUES(tag),
              base_trade_date=VALUES(base_trade_date),
              base_close=VALUES(base_close),
              verify_trade_date=VALUES(verify_trade_date),
              verify_close=VALUES(verify_close),
              verify_pct=VALUES(verify_pct),
              verify_limit_up=VALUES(verify_limit_up),
              verify_score=VALUES(verify_score),
              current_trade_date=VALUES(current_trade_date),
              current_close=VALUES(current_close),
              current_pct_from_base=VALUES(current_pct_from_base),
              avg_pct_from_base=VALUES(avg_pct_from_base),
              max_pct_from_base=VALUES(max_pct_from_base),
              min_low_pct_from_base=VALUES(min_low_pct_from_base),
              effect_status=IF(stock_announcement_effects.effect_status='faded', 'faded', VALUES(effect_status)),
              effect_score=IF(stock_announcement_effects.effect_status='faded', 0, VALUES(effect_score)),
              faded_trade_date=IF(
                stock_announcement_effects.effect_status='faded',
                COALESCE(stock_announcement_effects.faded_trade_date, VALUES(faded_trade_date)),
                VALUES(faded_trade_date)
              ),
              faded_reason=IF(
                stock_announcement_effects.effect_status='faded',
                COALESCE(NULLIF(stock_announcement_effects.faded_reason, ''), VALUES(faded_reason)),
                VALUES(faded_reason)
              ),
              last_checked_trade_date=VALUES(last_checked_trade_date),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    for idx in range(0, len(statements), 200):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 200]) + "\nCOMMIT;")
    return len(effects)


def mark_stale_effects(
    config: MySqlConfig,
    *,
    trade_date: str,
    stale_after_days: int = 31,
    code: str = "",
) -> int:
    code_filter = f"AND code={sql_string(code)}" if code else ""
    stale_days = max(1, int(stale_after_days))
    sql = f"""
    UPDATE stock_announcement_effects
    SET effect_status = CASE
          WHEN verify_score > 0 THEN 'faded'
          ELSE 'ignored'
        END,
        effect_score = 0,
        faded_trade_date = CASE
          WHEN verify_score > 0 THEN COALESCE(faded_trade_date, DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL {stale_days} DAY))
          ELSE faded_trade_date
        END,
        faded_reason = CASE
          WHEN verify_score > 0 THEN COALESCE(NULLIF(faded_reason, ''), {sql_string(f"expired_after_{stale_days}_days")})
          ELSE COALESCE(NULLIF(faded_reason, ''), {sql_string(f"stale_after_{stale_days}_days")})
        END,
        last_checked_trade_date = CAST({sql_string(trade_date)} AS DATE),
        raw_json = JSON_SET(COALESCE(raw_json, JSON_OBJECT()), '$.stale_after_days', {stale_days}),
        updated_at = CURRENT_TIMESTAMP(3)
    WHERE event_date < DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL {stale_days} DAY)
      AND effect_status <> 'ignored'
      {code_filter};
    """
    run_mysql(config, sql)
    rows = mysql_rows(run_mysql(config, "SELECT ROW_COUNT();", batch=True, raw=True))
    try:
        return int(rows[0][0]) if rows and rows[0] else 0
    except Exception:
        return 0


def _date_shift(day: str, days: int) -> str:
    parsed = datetime.strptime(day, "%Y-%m-%d").date()
    return (parsed + timedelta(days=days)).isoformat()


def build_announcement_effects(
    config: MySqlConfig,
    *,
    trade_date: str,
    lookback_days: int = 180,
    code: str = "",
    limit: int = 0,
    refresh_bars: bool = True,
    allow_local_fallback: bool = False,
    stale_after_days: int = 31,
    sleep_seconds: float = 0.15,
) -> dict[str, Any]:
    ensure_tables(config)
    root_stock_names = fetch_root_stock_codes(config, trade_date=trade_date, lookback_days=lookback_days, code=code)
    candidates = fetch_candidates(config, trade_date=trade_date, lookback_days=lookback_days, code=code, limit=limit)
    by_code: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.code:
            by_code.setdefault(candidate.code, []).append(candidate)
            root_stock_names.setdefault(candidate.code, candidate.stock_name)

    bars_written = 0
    fetch_errors: list[dict[str, str]] = []
    source_failures: dict[str, int] = {}
    disabled_sources: set[str] = set()
    if refresh_bars:
        for stock_code, stock_name in root_stock_names.items():
            items = by_code.get(stock_code) or []
            start = min([item.item_date for item in items] or [_date_shift(trade_date, -lookback_days)])
            start = _date_shift(start, -10)
            end = trade_date
            try:
                rows = fetch_daily_bars_from_ak(
                    stock_code,
                    stock_name,
                    start,
                    end,
                    disabled_sources=disabled_sources,
                    source_failures=source_failures,
                )
                bars_written += upsert_daily_bars(config, rows)
                if sleep_seconds > 0:
                    time.sleep(float(sleep_seconds))
            except Exception as exc:
                error_text = str(exc)
                if not allow_local_fallback:
                    fetch_errors.append({"code": stock_code, "error": error_text[:300], "fallback": ""})
                else:
                    try:
                        rows = fetch_daily_bars_from_local_ticks(config, stock_code, start, end)
                        bars_written += upsert_daily_bars(config, rows)
                        fetch_errors.append({"code": stock_code, "error": str(exc)[:300], "fallback": "local_intraday_ticks"})
                    except Exception as fallback_exc:
                        fetch_errors.append(
                            {
                                "code": stock_code,
                                "error": str(exc)[:220],
                                "fallback_error": str(fallback_exc)[:220],
                            }
                        )

    existing = fetch_existing_effects(config, [item.root_item_id for item in candidates])
    effects: list[dict[str, Any]] = []
    for stock_code, items in by_code.items():
        start = _date_shift(min(item.item_date for item in items), -10)
        bars = read_daily_bars(config, stock_code, start, trade_date, official_only=True)
        for item in items:
            effects.append(
                compute_effect(
                    item,
                    bars,
                    checked_trade_date=trade_date,
                    existing=existing.get(item.root_item_id),
                    stale_after_days=stale_after_days,
                )
            )

    written = upsert_effects(config, effects)
    stale_marked = mark_stale_effects(
        config,
        trade_date=trade_date,
        stale_after_days=stale_after_days,
        code=code,
    )
    status_counts: dict[str, int] = {"unverified": 0, "active": 0, "faded": 0, "ignored": 0}
    changed_rows = []
    for effect in effects:
        status = str(effect.get("effect_status") or "unverified")
        status_counts[status] = status_counts.get(status, 0) + 1
        item: Candidate = effect["candidate"]
        changed_rows.append({"code": item.code, "stock_name": item.stock_name})
    dirty = enqueue_root_evidence_cache_dirty_many(
        config,
        trade_date,
        changed_rows,
        reason="stock_announcement_effects_updated",
        priority=32,
    )

    return {
        "trade_date": trade_date,
        "lookback_days": lookback_days,
        "candidates": len(candidates),
        "codes": len(root_stock_names),
        "effect_candidate_codes": len(by_code),
        "bars_written": bars_written,
        "effects_written": written,
        "stale_marked": stale_marked,
        "dirty_enqueued": dirty,
        "status_counts": status_counts,
        "disabled_sources": sorted(disabled_sources),
        "source_failures": source_failures,
        "fetch_errors": fetch_errors[:10],
    }


__all__ = [
    "build_announcement_effects",
    "classify_candidate",
    "compute_effect",
    "ensure_tables",
    "fetch_candidates",
    "fetch_daily_bars_from_ak",
    "fetch_daily_bars_from_ak_daily",
    "fetch_daily_bars_from_ak_hist",
    "fetch_daily_bars_from_ak_tx",
    "fetch_daily_bars_from_local_ticks",
    "fetch_root_stock_codes",
    "mark_stale_effects",
    "upsert_daily_bars",
]
