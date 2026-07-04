from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string


def parse_trade_day(value: str) -> date:
    return date.fromisoformat((value or "").strip()[:10])


def is_weekday_trade_date(value: str) -> bool:
    return parse_trade_day(value).weekday() < 5


def assert_weekday_trade_date(value: str, *, label: str = "trade_date") -> None:
    if not is_weekday_trade_date(value):
        raise ValueError(f"{label} must be a weekday trade date, got {value}")


def parse_json_output(value: str) -> Any:
    text = (value or "").strip()
    if not text or text.upper() == "NULL":
        return []
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []
        return json.loads(lines[-1])


def json_query(config: MySqlConfig, sql: str, default: Any) -> Any:
    try:
        output = run_mysql(config, sql, batch=True, raw=True)
        parsed = parse_json_output(output)
        return default if parsed is None else parsed
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def latest_data_date(config: MySqlConfig, upper_bound: str | None = "") -> str:
    filters = ["day_value IS NOT NULL", "WEEKDAY(day_value) < 5"]
    if upper_bound:
        filters.append(f"day_value <= {sql_string(upper_bound)}")
    where_clause = "WHERE " + " AND ".join(filters)
    sql = f"""
    SELECT COALESCE(DATE_FORMAT(MAX(day_value), '%Y-%m-%d'), DATE_FORMAT(CURDATE(), '%Y-%m-%d'))
    FROM (
      SELECT trade_date AS day_value
      FROM market_width_snapshots
      WHERE source='stock_daily_bars_close'
         OR ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999')
          OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999'))
      UNION ALL
      SELECT DISTINCT trade_date AS day_value
      FROM stock_daily_bars
      WHERE trade_date <= CURDATE()
    ) days
    {where_clause};
    """
    try:
        return (run_mysql(config, sql, batch=True, raw=True) or "").splitlines()[-1].strip()
    except Exception:
        return date.today().strftime("%Y-%m-%d")


def trade_date_has_data(config: MySqlConfig, trade_date: str) -> bool:
    sql = f"""
    SELECT
      IF(EXISTS(SELECT 1 FROM market_width_snapshots WHERE trade_date={sql_string(trade_date)} LIMIT 1), 1, 0)
      + IF((SELECT COUNT(*) FROM stock_daily_bars WHERE trade_date={sql_string(trade_date)}) >= 1000, 1, 0)
      + IF(EXISTS(SELECT 1 FROM scan_runs WHERE accepted=1 AND DATE(scanned_at)={sql_string(trade_date)} LIMIT 1), 1, 0)
      + IF(EXISTS(SELECT 1 FROM windows WHERE status='done' AND DATE(ended_at)={sql_string(trade_date)} LIMIT 1), 1, 0)
      + IF(EXISTS(SELECT 1 FROM research_pool_items WHERE trade_date={sql_string(trade_date)} LIMIT 1), 1, 0)
      + IF(EXISTS(SELECT 1 FROM stock_root_evidence_cache WHERE trade_date={sql_string(trade_date)} LIMIT 1), 1, 0);
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
        return bool(rows and rows[0] and int(float(rows[0][0] or 0)) > 0)
    except Exception:
        return False


def resolve_trade_date(config: MySqlConfig, value: str | None = "") -> str:
    text = (value or "").strip().lower()
    if text == "yesterday":
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        return latest_data_date(config, yesterday)
    if text and text != "latest":
        try:
            parsed = date.fromisoformat(text)
            normalized = parsed.strftime("%Y-%m-%d")
            if parsed.weekday() >= 5 or not trade_date_has_data(config, normalized):
                return latest_data_date(config, normalized)
            return normalized
        except ValueError:
            pass
    return latest_data_date(config)
