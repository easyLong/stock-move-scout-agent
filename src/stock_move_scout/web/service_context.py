from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from stock_move_scout.db import MySqlConfig, sql_string
from stock_move_scout.web.runtime import json_query


def now_shanghai() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        return datetime.now()


def parse_day(value: str) -> date | None:
    try:
        return date.fromisoformat((value or "").strip()[:10])
    except ValueError:
        return None


def service_market_phase(service_trade_date: str) -> dict[str, str]:
    now = now_shanghai()
    service_day = parse_day(service_trade_date)
    today = now.date()
    current_time = now.time()
    if not service_day:
        return {"phase": "unknown", "phase_label": "未知阶段"}
    if service_day > today:
        return {"phase": "pre_market_prepare", "phase_label": "盘前底稿"}
    if service_day < today:
        return {"phase": "post_close_review", "phase_label": "收盘复盘"}
    if today.weekday() >= 5:
        return {"phase": "non_trading_day", "phase_label": "非交易日观察"}
    if current_time < time(9, 15):
        return {"phase": "pre_market", "phase_label": "盘前观察"}
    if current_time < time(9, 30):
        return {"phase": "auction", "phase_label": "竞价观察"}
    if time(9, 30) <= current_time <= time(11, 30):
        return {"phase": "intraday", "phase_label": "盘中观察"}
    if current_time < time(13, 0):
        return {"phase": "midday", "phase_label": "午间观察"}
    if time(13, 0) <= current_time <= time(15, 0):
        return {"phase": "intraday", "phase_label": "盘中观察"}
    return {"phase": "after_close_pending", "phase_label": "盘后确认中"}


def build_service_context(config: MySqlConfig, service_trade_date: str) -> dict[str, Any]:
    phase = service_market_phase(service_trade_date)
    day = sql_string(service_trade_date)
    sql = f"""
    SELECT JSON_OBJECT(
      'service_trade_date', DATE_FORMAT({day}, '%Y-%m-%d'),
      'base_trade_date', COALESCE(
        (SELECT DATE_FORMAT(MAX(source_trade_date), '%Y-%m-%d')
         FROM research_pool_items
         WHERE trade_date={day}),
        (SELECT DATE_FORMAT(MAX(fact_date), '%Y-%m-%d')
         FROM stock_effective_facts
         WHERE trade_date={day}),
        DATE_FORMAT({day}, '%Y-%m-%d')
      ),
      'research_pool_count', (SELECT COUNT(*) FROM research_pool_items WHERE trade_date={day}),
      'effective_fact_count', (SELECT COUNT(*) FROM stock_effective_facts WHERE trade_date={day}),
      'effective_stock_count', (SELECT COUNT(DISTINCT code) FROM stock_effective_facts WHERE trade_date={day}),
      'summary_count', (SELECT COUNT(*) FROM async_evidence_summaries WHERE trade_date={day}),
      'root_cache_count', (SELECT COUNT(*) FROM stock_root_evidence_cache WHERE trade_date={day}),
      'root_cache_updated_at', (
        SELECT COALESCE(DATE_FORMAT(MAX(updated_at), '%Y-%m-%d %H:%i:%s'), '')
        FROM stock_root_evidence_cache
        WHERE trade_date={day}
      ),
      'latest_realtime_at', (
        SELECT COALESCE(DATE_FORMAT(MAX(ts), '%Y-%m-%d %H:%i:%s'), '')
        FROM (
          SELECT MAX(scanned_at) AS ts FROM scan_runs WHERE DATE(scanned_at)={day} AND accepted=1
          UNION ALL
          SELECT MAX(ended_at) AS ts FROM windows WHERE DATE(ended_at)={day} AND status='done'
          UNION ALL
          SELECT MAX(captured_at) AS ts FROM market_width_snapshots WHERE trade_date={day}
        ) realtime_ts
      ),
      'daily_close_ready', IF(EXISTS(
        SELECT 1 FROM market_width_snapshots
        WHERE trade_date={day}
          AND source='stock_daily_bars_close'
        LIMIT 1
      ), TRUE, FALSE)
    );
    """
    context = json_query(config, sql, {})
    if not isinstance(context, dict):
        context = {}
    service_day = parse_day(service_trade_date)
    today = now_shanghai().date()
    daily_close_ready = bool(context.get("daily_close_ready"))
    if service_day and service_day > today:
        post_close_status = "not_started"
        post_close_status_label = "待交易"
    elif daily_close_ready:
        post_close_status = "confirmed"
        post_close_status_label = "已确认"
    elif service_day and service_day < today:
        post_close_status = "missing"
        post_close_status_label = "可能缺失"
    else:
        post_close_status = "pending"
        post_close_status_label = "待确认"
    base_trade_date = str(context.get("base_trade_date") or service_trade_date)
    context.update(
        {
            "service_trade_date": str(context.get("service_trade_date") or service_trade_date),
            "base_trade_date": base_trade_date,
            "phase": phase["phase"],
            "phase_label": phase["phase_label"],
            "base_data_label": f"基于 {base_trade_date} 收盘",
            "post_close_status": post_close_status,
            "post_close_status_label": post_close_status_label,
        }
    )
    return context
