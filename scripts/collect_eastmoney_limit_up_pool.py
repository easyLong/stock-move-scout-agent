#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import akshare as ak
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)


SOURCE = "eastmoney_akshare_stock_zt_pool_em"
POOL_TYPE = "limit_up"
STATUS = "limit_up"


def compact(value: Any, limit: int = 255) -> str:
    text = "" if value is None else str(value).strip()
    if text.lower() in {"nan", "none", "null"}:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def clean_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def to_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        text = str(value).replace(",", "").replace("%", "").strip()
        if not text or text.lower() in {"nan", "none", "null", "-"}:
            return None
        return float(text)
    except Exception:
        return None


def to_int(value: Any) -> int:
    try:
        parsed = to_float(value)
        return int(parsed) if parsed is not None else 0
    except Exception:
        return 0


def format_time(value: Any) -> str:
    text = compact(value, 32)
    digits = re.sub(r"\D", "", text)
    if digits and set(digits) == {"0"}:
        return ""
    if len(digits) == 6:
        return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
    if len(digits) == 4:
        return f"{digits[0:2]}:{digits[2:4]}:00"
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text):
        return text if text.count(":") == 2 else f"{text}:00"
    return ""


def ak_date(value: str) -> str:
    return str(value or "").replace("-", "")


def iso_date(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def latest_trade_dates(end_date: str, days: int) -> list[str]:
    end = pd.to_datetime(end_date).date()
    try:
        calendar = ak.tool_trade_date_hist_sina()
        dates = [pd.to_datetime(item).date() for item in calendar["trade_date"].tolist()]
        selected = [item.isoformat() for item in dates if item <= end]
        return selected[-max(1, int(days)) :]
    except Exception:
        fallback: list[str] = []
        cursor = end
        while len(fallback) < max(1, int(days)):
            if cursor.weekday() < 5:
                fallback.append(cursor.isoformat())
            cursor = cursor.fromordinal(cursor.toordinal() - 1)
        return list(reversed(fallback))


def ensure_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS limit_up_pool_items (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      pool_type VARCHAR(32) NOT NULL DEFAULT 'limit_up',
      status VARCHAR(32) NOT NULL DEFAULT 'limit_up',
      pct_change DECIMAL(10,4) NULL,
      latest_price DECIMAL(12,4) NULL,
      turnover_amount DECIMAL(20,2) NULL,
      float_market_value DECIMAL(20,2) NULL,
      total_market_value DECIMAL(20,2) NULL,
      turnover_rate DECIMAL(12,4) NULL,
      seal_amount DECIMAL(20,2) NULL,
      first_limit_time VARCHAR(32) NOT NULL DEFAULT '',
      last_limit_time VARCHAR(32) NOT NULL DEFAULT '',
      open_count INT NOT NULL DEFAULT 0,
      limit_up_stat VARCHAR(32) NOT NULL DEFAULT '',
      limit_up_days INT NOT NULL DEFAULT 0,
      industry_name VARCHAR(128) NOT NULL DEFAULT '',
      source VARCHAR(64) NOT NULL DEFAULT 'eastmoney_akshare_stock_zt_pool_em',
      raw_json JSON NULL,
      collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_limit_up_pool_day_source_code (trade_date, source, pool_type, code),
      KEY idx_limit_up_pool_day_time (trade_date, first_limit_time),
      KEY idx_limit_up_pool_code_day (code, trade_date),
      KEY idx_limit_up_pool_day_status (trade_date, status),
      KEY idx_limit_up_pool_day_industry (trade_date, industry_name)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
      COMMENT='Limit-up pool items from market data providers.';
    """
    run_mysql(config, sql)


def normalize_rows(df: pd.DataFrame, trade_date: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if df is None or df.empty:
        return rows
    for raw in df.to_dict(orient="records"):
        code = clean_code(raw.get("代码"))
        if not code:
            continue
        row = {
            "trade_date": trade_date,
            "code": code,
            "stock_name": compact(raw.get("名称"), 64),
            "pool_type": POOL_TYPE,
            "status": STATUS,
            "pct_change": to_float(raw.get("涨跌幅")),
            "latest_price": to_float(raw.get("最新价")),
            "turnover_amount": to_float(raw.get("成交额")),
            "float_market_value": to_float(raw.get("流通市值")),
            "total_market_value": to_float(raw.get("总市值")),
            "turnover_rate": to_float(raw.get("换手率")),
            "seal_amount": to_float(raw.get("封板资金") or raw.get("封单资金")),
            "first_limit_time": format_time(raw.get("首次封板时间")),
            "last_limit_time": format_time(raw.get("最后封板时间")),
            "open_count": to_int(raw.get("炸板次数") or raw.get("开板次数")),
            "limit_up_stat": compact(raw.get("涨停统计"), 32),
            "limit_up_days": to_int(raw.get("连板数")),
            "industry_name": compact(raw.get("所属行业"), 128),
            "source": SOURCE,
            "raw_json": raw,
        }
        rows.append(row)
    return rows


def fetch_rows(trade_date: str, retries: int, pause: float) -> list[dict[str, Any]]:
    last_error = ""
    for attempt in range(max(1, int(retries))):
        try:
            df = ak.stock_zt_pool_em(date=ak_date(trade_date))
            return normalize_rows(df, trade_date)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt + 1 < max(1, int(retries)):
                time.sleep(max(0.0, float(pause)) * (attempt + 1))
    raise RuntimeError(f"stock_zt_pool_em_failed trade_date={trade_date} {last_error}")


def write_items(config: MySqlConfig, rows: list[dict[str, Any]], replace_dates: bool) -> int:
    if not rows:
        return 0
    dates = sorted({row["trade_date"] for row in rows})
    if replace_dates:
        run_mysql(
            config,
            f"""
            DELETE FROM limit_up_pool_items
            WHERE source={sql_string(SOURCE)}
              AND pool_type={sql_string(POOL_TYPE)}
              AND trade_date IN ({",".join(sql_string(item) for item in dates)});
            """,
        )
    statements: list[str] = []
    for row in rows:
        statements.append(
            f"""
            INSERT INTO limit_up_pool_items(
              trade_date, code, stock_name, pool_type, status,
              pct_change, latest_price, turnover_amount, float_market_value, total_market_value,
              turnover_rate, seal_amount, first_limit_time, last_limit_time, open_count,
              limit_up_stat, limit_up_days, industry_name, source, raw_json
            ) VALUES(
              {sql_string(row['trade_date'])}, {sql_string(row['code'])}, {sql_string(row['stock_name'])},
              {sql_string(row['pool_type'])}, {sql_string(row['status'])},
              {sql_number(row['pct_change'])}, {sql_number(row['latest_price'])}, {sql_number(row['turnover_amount'])},
              {sql_number(row['float_market_value'])}, {sql_number(row['total_market_value'])},
              {sql_number(row['turnover_rate'])}, {sql_number(row['seal_amount'])},
              {sql_string(row['first_limit_time'])}, {sql_string(row['last_limit_time'])}, {sql_int(row['open_count'])},
              {sql_string(row['limit_up_stat'])}, {sql_int(row['limit_up_days'])},
              {sql_string(row['industry_name'])}, {sql_string(SOURCE)}, {sql_json(row['raw_json'])}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name), status=VALUES(status),
              pct_change=VALUES(pct_change), latest_price=VALUES(latest_price),
              turnover_amount=VALUES(turnover_amount), float_market_value=VALUES(float_market_value),
              total_market_value=VALUES(total_market_value), turnover_rate=VALUES(turnover_rate),
              seal_amount=VALUES(seal_amount), first_limit_time=VALUES(first_limit_time),
              last_limit_time=VALUES(last_limit_time), open_count=VALUES(open_count),
              limit_up_stat=VALUES(limit_up_stat), limit_up_days=VALUES(limit_up_days),
              industry_name=VALUES(industry_name), raw_json=VALUES(raw_json), updated_at=NOW(3);
            """
        )
    for idx in range(0, len(statements), 300):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 300]) + "\nCOMMIT;")
    return len(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect Eastmoney limit-up pool via AkShare stock_zt_pool_em.")
    parser.add_argument("--trade-date", default=date.today().isoformat(), help="End trade date, YYYY-MM-DD.")
    parser.add_argument("--days", type=int, default=1, help="Number of latest trading days ending at trade-date.")
    parser.add_argument("--dates", default="", help="Comma-separated explicit trade dates, overrides --days.")
    parser.add_argument("--pause", type=float, default=0.3)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--replace-dates", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-on-empty", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    ensure_table(config)
    dates = [iso_date(item.strip()) for item in str(args.dates or "").split(",") if item.strip()]
    if not dates:
        dates = latest_trade_dates(iso_date(args.trade_date), int(args.days))
    results: list[dict[str, Any]] = []
    total_written = 0
    for idx, trade_date in enumerate(dates):
        rows = fetch_rows(trade_date, args.retries, args.pause)
        written = write_items(config, rows, bool(args.replace_dates))
        total_written += written
        results.append({"trade_date": trade_date, "rows": len(rows), "written": written})
        if idx + 1 < len(dates):
            time.sleep(max(0.0, float(args.pause)))
    print(
        json.dumps(
            {
                "source": SOURCE,
                "pool_type": POOL_TYPE,
                "dates": results,
                "total_written": total_written,
            },
            ensure_ascii=False,
        )
    )
    if args.fail_on_empty and total_written <= 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
