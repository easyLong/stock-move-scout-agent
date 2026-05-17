from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import akshare as ak
import pandas as pd

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_number, sql_string


OFFICIAL_DAILY_BAR_SOURCES = {
    "akshare_stock_zh_a_hist",
    "akshare_stock_zh_a_daily",
    "akshare_stock_zh_a_hist_tx",
}


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

