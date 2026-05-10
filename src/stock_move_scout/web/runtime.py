from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from stock_move_scout.db import MySqlConfig, run_mysql


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


def latest_data_date(config: MySqlConfig) -> str:
    sql = """
    SELECT COALESCE(DATE_FORMAT(MAX(day_value), '%Y-%m-%d'), DATE_FORMAT(CURDATE(), '%Y-%m-%d'))
    FROM (
      SELECT DATE(scanned_at) AS day_value FROM scan_runs WHERE accepted=1
      UNION ALL
      SELECT DATE(ended_at) AS day_value FROM windows WHERE status='done'
    ) days
    WHERE WEEKDAY(day_value) < 5;
    """
    try:
        return (run_mysql(config, sql, batch=True, raw=True) or "").splitlines()[-1].strip()
    except Exception:
        return date.today().strftime("%Y-%m-%d")


def resolve_trade_date(config: MySqlConfig, value: str | None = "") -> str:
    text = (value or "").strip().lower()
    if text == "yesterday":
        return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
    if text and text != "latest":
        try:
            return date.fromisoformat(text).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return latest_data_date(config)
