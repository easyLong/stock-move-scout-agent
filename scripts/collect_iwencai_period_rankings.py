#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
import json
import re
import sys
from typing import Any

import pandas as pd
import pywencai

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_string,
)


def ensure_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_period_rankings (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      period_days INT NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      rank_no INT NOT NULL DEFAULT 0,
      rank_total INT NOT NULL DEFAULT 0,
      period_pct DECIMAL(10,4) NULL,
      latest_pct DECIMAL(10,4) NULL,
      latest_price DECIMAL(12,4) NULL,
      market_code VARCHAR(16) NOT NULL DEFAULT '',
      source VARCHAR(32) NOT NULL DEFAULT 'iwencai',
      source_query VARCHAR(255) NOT NULL DEFAULT '',
      raw_json JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_stock_period_rank (trade_date, period_days, code),
      KEY idx_stock_period_rank_period (trade_date, period_days, rank_no),
      KEY idx_stock_period_rank_code (code, trade_date),
      KEY idx_stock_period_rank_pct (trade_date, period_days, period_pct)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def to_float(value: Any) -> float | None:
    try:
        text = str(value or "").replace("%", "").strip()
        if not text or text.lower() == "nan":
            return None
        return float(text)
    except Exception:
        return None


def to_int(value: Any) -> int:
    try:
        text = str(value or "").strip()
        if not text or text.lower() == "nan":
            return 0
        return int(float(text))
    except Exception:
        return 0


def sql_number(value: Any) -> str:
    parsed = to_float(value)
    return "NULL" if parsed is None else str(parsed)


def clean_code(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


BEIJING_EXCHANGE_MARKET_CODES = {"BJ", "BSE", "北交所", "北证"}
BEIJING_EXCHANGE_MARKET_MARKERS = ("北交", "北证", "北京证券")


def is_beijing_exchange_code(code: str, market_code: Any = "") -> bool:
    code_text = clean_code(code) or str(code or "").strip()
    market_text = str(market_code or "").strip().upper()
    if market_text in BEIJING_EXCHANGE_MARKET_CODES or any(
        marker in market_text for marker in BEIJING_EXCHANGE_MARKET_MARKERS
    ):
        return True
    return code_text.startswith(("4", "8", "920"))


def find_column(columns: list[str], *needles: str) -> str:
    for col in columns:
        if all(needle in col for needle in needles):
            return col
    return ""


def parse_rank(value: Any) -> tuple[int, int]:
    text = str(value or "")
    match = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if match:
        return int(match.group(1)), int(match.group(2))
    return to_int(text), 0


def parse_trade_date_from_columns(columns: list[str], fallback: str) -> str:
    for col in columns:
        match = re.search(r"\[(\d{8})-(\d{8})\]", col)
        if match:
            end = match.group(2)
            return f"{end[:4]}-{end[4:6]}-{end[6:8]}"
    return fallback


def query_iwencai(period_days: int, top: int, universe: str) -> tuple[str, pd.DataFrame]:
    top_values = []
    for value in [int(top), 100, 20]:
        if value not in top_values:
            top_values.append(value)
    universes = []
    for value in [universe, "沪深A股", "A股"]:
        if value and value not in universes:
            universes.append(value)
    suffixes = [
        "非ST，未退市，剔除北交所",
        "非ST，未退市，非北交所",
        "非ST，剔除北交所",
        "非ST，未退市",
        "非ST",
    ]
    errors: list[str] = []
    for current_top in top_values:
        for current_universe in universes:
            for suffix in suffixes:
                query = f"近{int(period_days)}日涨幅排名前{current_top}，{current_universe}，{suffix}"
                df = pywencai.get(query=query, query_type="stock", loop=False)
                if isinstance(df, pd.DataFrame) and not df.empty:
                    return query, df
                errors.append(f"{query}:{type(df).__name__}:{str(df)[:120]}")
    raise RuntimeError("iwencai_no_stock_dataframe:" + " | ".join(errors[:5]))


def normalize_rows(df: pd.DataFrame, period_days: int, source_query: str, trade_date: str = "") -> tuple[str, list[dict[str, Any]]]:
    columns = [str(col) for col in df.columns]
    code_col = find_column(columns, "股票代码") or "code"
    name_col = find_column(columns, "股票简称")
    latest_price_col = find_column(columns, "最新价")
    latest_pct_col = find_column(columns, "最新涨跌幅")
    period_pct_col = find_column(columns, "区间涨跌幅")
    period_rank_col = find_column(columns, "区间涨跌幅", "排名")
    market_code_col = find_column(columns, "market_code")
    actual_trade_date = trade_date or parse_trade_date_from_columns(columns, date.today().strftime("%Y-%m-%d"))
    rows: list[dict[str, Any]] = []
    for _, item in df.iterrows():
        raw = {str(k): (None if pd.isna(v) else v) for k, v in item.to_dict().items()}
        code = clean_code(raw.get(code_col) or raw.get("code"))
        if not code:
            continue
        market_code = str(raw.get(market_code_col) or "").strip()
        if is_beijing_exchange_code(code, market_code):
            continue
        _, rank_total = parse_rank(raw.get(period_rank_col))
        rank_no = len(rows) + 1
        rows.append(
            {
                "trade_date": actual_trade_date,
                "period_days": int(period_days),
                "code": code,
                "stock_name": str(raw.get(name_col) or "").strip(),
                "rank_no": rank_no,
                "rank_total": rank_total,
                "period_pct": to_float(raw.get(period_pct_col)),
                "latest_pct": to_float(raw.get(latest_pct_col)),
                "latest_price": to_float(raw.get(latest_price_col)),
                "market_code": market_code,
                "source_query": source_query,
                "raw_json": raw,
            }
        )
    return actual_trade_date, rows


def upsert_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        statements.append(
            f"""
            INSERT INTO stock_period_rankings(
              trade_date, period_days, code, stock_name, rank_no, rank_total,
              period_pct, latest_pct, latest_price, market_code, source, source_query, raw_json
            ) VALUES (
              {sql_string(row['trade_date'])},
              {int(row['period_days'])},
              {sql_string(row['code'])},
              {sql_string(row['stock_name'])},
              {int(row['rank_no'])},
              {int(row['rank_total'])},
              {sql_number(row.get('period_pct'))},
              {sql_number(row.get('latest_pct'))},
              {sql_number(row.get('latest_price'))},
              {sql_string(row.get('market_code') or '')},
              'iwencai',
              {sql_string(row.get('source_query') or '')},
              {sql_json(row.get('raw_json') or {})}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name),
              rank_no=VALUES(rank_no),
              rank_total=VALUES(rank_total),
              period_pct=VALUES(period_pct),
              latest_pct=VALUES(latest_pct),
              latest_price=VALUES(latest_price),
              market_code=VALUES(market_code),
              source=VALUES(source),
              source_query=VALUES(source_query),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    run_mysql(config, "\n".join(statements))
    return len(rows)


def delete_beijing_exchange_rows(config: MySqlConfig, trade_date: str, period_days: int) -> None:
    sql = f"""
    DELETE FROM stock_period_rankings
    WHERE trade_date={sql_string(trade_date)}
      AND period_days={int(period_days)}
      AND (
        code LIKE '4%%'
        OR code LIKE '8%%'
        OR code LIKE '920%%'
        OR UPPER(market_code) IN ('BJ', 'BSE')
        OR market_code LIKE '%%北交%%'
        OR market_code LIKE '%%北证%%'
        OR market_code LIKE '%%北京证券%%'
      );
    """
    run_mysql(config, sql)


def collect(config: MySqlConfig, periods: list[int], top: int, universe: str, trade_date: str = "") -> dict[str, Any]:
    ensure_table(config)
    result: dict[str, Any] = {"periods": {}, "written": 0}
    for period in periods:
        source_query, df = query_iwencai(period, top, universe)
        actual_trade_date, rows = normalize_rows(df, period, source_query, trade_date)
        delete_beijing_exchange_rows(config, actual_trade_date, period)
        written = upsert_rows(config, rows)
        result["periods"][str(period)] = {
            "trade_date": actual_trade_date,
            "query": source_query,
            "rows": len(rows),
            "written": written,
            "columns": [str(col) for col in df.columns],
        }
        result["written"] += written
    return result


def parse_periods(value: str) -> list[int]:
    out: list[int] = []
    for item in str(value or "").split(","):
        item = item.strip()
        if item:
            out.append(int(item))
    return out or [3, 5, 10]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect iWenCai period gain rankings.")
    parser.add_argument("--periods", default="3,5,10")
    parser.add_argument("--top", type=int, default=50)
    parser.add_argument("--universe", default="沪深A股")
    parser.add_argument("--trade-date", default="")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    result = collect(config, parse_periods(args.periods), args.top, args.universe, args.trade_date)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
