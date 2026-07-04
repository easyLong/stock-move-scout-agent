#!/usr/bin/env python
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stock_move_scout.feed import (
    build_evidence_view,
    intel_feed_list_sql,
    intel_feed_sql,
    kpl_leaderboard_sql,
    kpl_plate_breakout_sql,
    leaderboard_sql,
    latest_scan_sql,
    latest_window_sql,
    market_width_latest_sql,
    market_width_cycle_5d_sql,
    market_width_series_sql,
    market_width_top50_sql,
    status_sql,
    trade_dates_sql,
    window_top10_sql,
)
from stock_move_scout.feed import root_cache
from stock_move_scout.feed.leaderboard_snapshot import (
    KPL_SNAPSHOT_SOURCE,
    SNAPSHOT_SOURCE,
    ensure_leaderboard_snapshot_table,
    latest_leaderboard_snapshot_payload_by_source,
    latest_leaderboard_snapshot_trade_date,
)
from stock_move_scout.market_width import ensure_market_width_tables
from stock_move_scout.research_pool import (
    ResearchPoolProvider,
    ensure_headline_theme_role_evidence_table,
    normalize_research_pool_ma_mode,
    research_pool_system_label,
)
from stock_move_scout.sources.kpl_featured_sections import ensure_kpl_featured_section_table
from stock_move_scout.sources.kpl_market_capacity import ensure_kpl_market_capacity_tables
from stock_move_scout.sources.kpl_plate_details import ensure_kpl_plate_detail_table
from stock_move_scout.sources.kpl_plate_strength import ensure_kpl_plate_strength_table
from stock_move_scout.sources.kpl_replay_limit_themes import ensure_kpl_replay_limit_theme_tables
from stock_move_scout.web import json_query, latest_data_date, resolve_trade_date
from stock_move_scout.web.service_context import build_service_context

from stock_scout_mysql import MySqlConfig, add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string

_TRADE_DATES_CACHE: dict[str, object] = {"ts": 0.0, "payload": None}
_TRADE_DATES_CACHE_TTL_SECONDS = 60.0
_RESPONSE_CACHE: dict[str, tuple[float, dict]] = {}
_RESPONSE_CACHE_TTL_SECONDS = 30.0
_RESEARCH_POOL_ENSURE_CACHE: dict[str, float] = {}
_RESEARCH_POOL_ENSURE_CACHE_TTL_SECONDS = 300.0


def cached_response(cache_key: str) -> dict | None:
    item = _RESPONSE_CACHE.get(cache_key)
    if not item:
        return None
    cached_at, payload = item
    if time.monotonic() - cached_at > _RESPONSE_CACHE_TTL_SECONDS:
        _RESPONSE_CACHE.pop(cache_key, None)
        return None
    return payload


def store_response(cache_key: str, payload: dict) -> dict:
    _RESPONSE_CACHE[cache_key] = (time.monotonic(), payload)
    return payload


APP_TITLE = "异动情报引擎"
CRITICAL_DATA_TASK_IDS = (
    "effective_facts",
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_async_evidence_summary_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS async_evidence_summaries (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      evidence_hash CHAR(64) NOT NULL,
      summary_text TEXT NULL,
      evidence_filter_summary TEXT NULL,
      key_facts JSON NULL,
      move_reason TEXT NULL,
      sustainability_basis JSON NULL,
      main_flaw TEXT NULL,
      missing_evidence JSON NULL,
      core_evidence_items JSON NULL,
      timeliness_label VARCHAR(32) NOT NULL DEFAULT 'unknown',
      timeliness_reason TEXT NULL,
      final_analysis TEXT NULL,
      move_explanation TEXT NULL,
      explanation_strength VARCHAR(32) NOT NULL DEFAULT 'none',
      anchor_match VARCHAR(32) NOT NULL DEFAULT 'weak',
      anchor_match_reason TEXT NULL,
      quality_label VARCHAR(64) NOT NULL DEFAULT '',
      core_support JSON NULL,
      counterpoints JSON NULL,
      final_view TEXT NULL,
      key_points JSON NULL,
      hard_catalysts JSON NULL,
      impact_factors JSON NULL,
      impact_summary_text TEXT NULL,
      risks JSON NULL,
      evidence_strength ENUM('pending','weak','medium','strong') NOT NULL DEFAULT 'pending',
      evidence_gaps JSON NULL,
      source_counts JSON NULL,
      source_payload MEDIUMTEXT NULL,
      model VARCHAR(128) NOT NULL DEFAULT '',
      status ENUM('ready','fallback','failed') NOT NULL DEFAULT 'ready',
      error_message TEXT NULL,
      raw_json JSON NULL,
      summarized_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_async_evidence_day_code (trade_date, code),
      KEY idx_async_evidence_code (code),
      KEY idx_async_evidence_hash (evidence_hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)
    ensure_async_evidence_summary_column(config, "evidence_filter_summary", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "key_facts", "JSON NULL")
    ensure_async_evidence_summary_column(config, "move_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "sustainability_basis", "JSON NULL")
    ensure_async_evidence_summary_column(config, "main_flaw", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "missing_evidence", "JSON NULL")
    ensure_async_evidence_summary_column(config, "core_evidence_items", "JSON NULL")
    ensure_async_evidence_summary_column(config, "timeliness_label", "VARCHAR(32) NOT NULL DEFAULT 'unknown'")
    ensure_async_evidence_summary_column(config, "timeliness_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "final_analysis", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "move_explanation", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "explanation_strength", "VARCHAR(32) NOT NULL DEFAULT 'none'")
    ensure_async_evidence_summary_column(config, "anchor_match", "VARCHAR(32) NOT NULL DEFAULT 'weak'")
    ensure_async_evidence_summary_column(config, "anchor_match_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "quality_label", "VARCHAR(64) NOT NULL DEFAULT ''")
    ensure_async_evidence_summary_column(config, "core_support", "JSON NULL")
    ensure_async_evidence_summary_column(config, "counterpoints", "JSON NULL")
    ensure_async_evidence_summary_column(config, "final_view", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "impact_factors", "JSON NULL")
    ensure_async_evidence_summary_column(config, "impact_summary_text", "TEXT NULL")


def resolve_leader_data_date(config: MySqlConfig, service_trade_date: str, source: str = SNAPSHOT_SOURCE, ma_mode: str = "none") -> str:
    """Use same-day post-close snapshots once they exist; otherwise fall back to the previous trade day."""
    try:
        same_day = latest_leaderboard_snapshot_trade_date(config, service_trade_date, source=source, exact=True, ma_mode=ma_mode)
    except Exception:
        same_day = ""
    if same_day:
        return same_day
    try:
        leader_day = latest_leaderboard_snapshot_trade_date(config, service_trade_date, source=source, ma_mode=ma_mode)
    except Exception:
        leader_day = ""
    return leader_day or service_trade_date


def explicit_leader_data_date(config: MySqlConfig, requested_trade_date: str, target_date: str, source: str, ma_mode: str = "none") -> str:
    """Historical page selections should show that date's post-close snapshot directly."""
    requested = str(requested_trade_date or "").strip()
    if not requested or requested.lower() == "latest":
        return resolve_leader_data_date(config, target_date, source, ma_mode)
    try:
        exact_day = latest_leaderboard_snapshot_trade_date(config, target_date, source=source, exact=True, ma_mode=ma_mode)
    except Exception:
        exact_day = ""
    return exact_day or resolve_leader_data_date(config, target_date, source, ma_mode)


def resolve_pool_ma_mode(pool_mode: str = "") -> str:
    value = str(pool_mode or "").strip()
    if value in {"", "latest"}:
        value = "bear"
    return normalize_research_pool_ma_mode(value)


def pool_mode_payload(ma_mode: str) -> dict[str, str]:
    system = "bull" if ma_mode != "none" else "bear"
    return {
        "pool_mode": system,
        "research_pool_ma_mode": ma_mode,
        "research_pool_system": system,
        "research_pool_system_label": research_pool_system_label(ma_mode),
    }


def full_pool_ma_mode() -> str:
    """Market overview and intraday feed always use the broadest research pool."""
    return "none"


def compact_leaderboard_payload(payload: dict) -> dict:
    """Trim fields that are not rendered on the leaderboard list page."""
    if not isinstance(payload, dict):
        return {}
    drop_keys = {
        "active_facts",
        "active_fact_count",
        "concept_tags",
        "main_business",
        "sw_industry",
        "source_label",
        "rank_3d",
        "rank_10d",
        "pct_3d",
        "pct_10d",
        "today_limit_amount_yi",
        "first_limit_10d_amount_yi",
        "limit_up_days",
    }
    for scope in payload.get("scopes") or []:
        if not isinstance(scope, dict):
            continue
        for leader in scope.get("leaders") or []:
            if not isinstance(leader, dict):
                continue
            for key in drop_keys:
                leader.pop(key, None)
    return payload


def ensure_selected_research_pool(config: MySqlConfig, trade_date: str, ma_mode: str) -> None:
    cache_key = f"{trade_date}:{normalize_research_pool_ma_mode(ma_mode)}"
    cached_at = _RESEARCH_POOL_ENSURE_CACHE.get(cache_key)
    if cached_at and time.monotonic() - cached_at < _RESEARCH_POOL_ENSURE_CACHE_TTL_SECONDS:
        return
    ResearchPoolProvider(config).latest_snapshot(trade_date, ma_mode=ma_mode)
    _RESEARCH_POOL_ENSURE_CACHE[cache_key] = time.monotonic()


def ensure_async_evidence_summary_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'async_evidence_summaries'
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    exists = (run_mysql(config, sql, batch=True, raw=True) or "").splitlines()[-1].strip() == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE async_evidence_summaries ADD COLUMN {column_name} {column_sql};")


def attach_evidence_views(feed: object) -> object:
    if not isinstance(feed, list):
        return feed
    for row in feed:
        if isinstance(row, dict):
            view = build_evidence_view(row)
            row["evidence_view"] = view
            row["evidence_version"] = view.get("evidence_version", "")
            row["latest_source_updated_at"] = view.get("latest_source_updated_at") or row.get("latest_source_updated_at", "")
    return feed


def data_source_health(config: MySqlConfig) -> list[dict[str, object]]:
    task_ids = ", ".join(sql_string(task_id) for task_id in CRITICAL_DATA_TASK_IDS)
    sql = f"""
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'task_id', task_id,
      'task_name', task_name,
      'task_kind', task_kind,
      'enabled', enabled,
      'next_run_after', next_run_after,
      'last_started_at', last_started_at,
      'last_status', last_status,
      'health', health,
      'message', message
    )), JSON_ARRAY())
    FROM (
      SELECT
        st.task_id,
        st.task_name,
        st.task_kind,
        st.enabled,
        COALESCE(DATE_FORMAT(st.next_run_after, '%Y-%m-%d %H:%i:%s'), '') AS next_run_after,
        COALESCE(DATE_FORMAT(last_run.started_at, '%Y-%m-%d %H:%i:%s'), '') AS last_started_at,
        COALESCE(last_run.status, '') AS last_status,
        CASE
          WHEN st.enabled = 0 THEN 'disabled'
          WHEN COALESCE(last_run.status, '') IN ('failed', 'timeout', 'dead') THEN 'failed'
          WHEN last_run.started_at IS NULL THEN 'never_run'
          WHEN last_run.started_at < DATE_SUB(NOW(), INTERVAL 2 DAY) THEN 'stale'
          ELSE 'ok'
        END AS health,
        CASE
          WHEN st.enabled = 0 THEN '任务未启用'
          WHEN COALESCE(last_run.status, '') IN ('failed', 'timeout', 'dead') THEN CONCAT('最近运行异常：', last_run.status)
          WHEN last_run.started_at IS NULL THEN '尚未运行'
          WHEN last_run.started_at < DATE_SUB(NOW(), INTERVAL 2 DAY) THEN '超过2天未成功刷新'
          ELSE ''
        END AS message
      FROM scheduled_tasks st
      LEFT JOIN (
        SELECT tr.*
        FROM task_runs tr
        JOIN (
          SELECT task_id, MAX(started_at) AS started_at
          FROM task_runs
          GROUP BY task_id
        ) latest ON latest.task_id = tr.task_id AND latest.started_at = tr.started_at
      ) last_run ON last_run.task_id = st.task_id
      WHERE st.task_id IN ({task_ids})
      ORDER BY FIELD(st.task_id, {task_ids})
    ) health_rows;
    """
    result = json_query(config, sql, [])
    return result if isinstance(result, list) else []


def attach_data_source_health(status: object, config: MySqlConfig) -> object:
    if not isinstance(status, dict):
        return status
    health = data_source_health(config)
    status["data_source_health"] = health
    status["data_source_alerts"] = [item for item in health if item.get("health") not in {"ok", "disabled"}]
    return status


def sort_market_width_series(series: object) -> list[object]:
    if not isinstance(series, list):
        return []
    return sorted(
        series,
        key=lambda item: str(item.get("captured_at") or item.get("time") or "") if isinstance(item, dict) else "",
    )


def sort_market_width_cycle(rows: object) -> list[object]:
    if not isinstance(rows, list):
        return []
    return sorted(rows, key=lambda item: str(item.get("trade_date") or "") if isinstance(item, dict) else "")


def feed_runtime_sql(trade_date: str) -> str:
    day = sql_string(trade_date)
    return f"""
    SELECT COALESCE(JSON_OBJECT(
      'latest_scan_at', (
        SELECT DATE_FORMAT(MAX(scanned_at), '%Y-%m-%d %H:%i:%s')
        FROM scan_runs
        WHERE DATE(scanned_at)=CAST({day} AS DATE)
      ),
      'scan_count', (
        SELECT COUNT(*)
        FROM scan_runs
        WHERE DATE(scanned_at)=CAST({day} AS DATE)
      ),
      'accepted_scan_count', (
        SELECT COUNT(*)
        FROM scan_runs
        WHERE DATE(scanned_at)=CAST({day} AS DATE)
          AND accepted=1
      ),
      'latest_scan_row_count', (
        SELECT row_count
        FROM scan_runs
        WHERE DATE(scanned_at)=CAST({day} AS DATE)
        ORDER BY scanned_at DESC
        LIMIT 1
      ),
      'latest_scan_phase', COALESCE((
        SELECT market_phase
        FROM scan_runs
        WHERE DATE(scanned_at)=CAST({day} AS DATE)
        ORDER BY scanned_at DESC
        LIMIT 1
      ), ''),
      'latest_event_at', (
        SELECT DATE_FORMAT(MAX(event_time), '%Y-%m-%d %H:%i:%s')
        FROM stock_move_events
        WHERE trade_date=CAST({day} AS DATE)
      ),
      'event_count', (
        SELECT COUNT(*)
        FROM stock_move_events
        WHERE trade_date=CAST({day} AS DATE)
      ),
      'judgement_count', (
        SELECT COUNT(*)
        FROM stock_move_judgements
        WHERE trade_date=CAST({day} AS DATE)
      )
    ), JSON_OBJECT());
    """


def _number_value(value: object, default: float = 0.0) -> float:
    try:
        if value in {None, ""}:
            return default
        return float(value)
    except Exception:
        return default


def _int_value(value: object, default: int = 0) -> int:
    try:
        if value in {None, ""}:
            return default
        return int(float(value))
    except Exception:
        return default


def _json_value(value: object, default: object | None = None) -> object:
    if default is None:
        default = []
    try:
        text = str(value or "").strip()
        return json.loads(text) if text else default
    except Exception:
        return default


def _auction_key(row: dict[str, object]) -> tuple[str, str]:
    return str(row.get("auction_side") or ""), str(row.get("code") or "")


def _timeline_key(item: dict[str, object]) -> tuple[str, str]:
    return str(item.get("limit_side") or ""), str(item.get("code") or "")


def _point_number(points: list[dict[str, object]], minute: str, key: str) -> float | None:
    for item in points:
        if str(item.get("minute") or "") == minute:
            return _number_value(item.get(key))
    return None


def _point_int(points: list[dict[str, object]], minute: str, key: str) -> int | None:
    for item in points:
        if str(item.get("minute") or "") == minute:
            return _int_value(item.get(key))
    return None


def _delta_value(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(right - left, 4)


def _enrich_auction_points(rows: list[dict[str, object]], timeline: list[dict[str, object]]) -> None:
    by_key: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for item in timeline:
        by_key[_timeline_key(item)].append(item)
    for points in by_key.values():
        points.sort(key=lambda item: str(item.get("minute") or ""))

    for row in rows:
        points = by_key.get(_auction_key(row), [])
        seal_19 = _point_number(points, "09:19", "seal_yi")
        seal_20 = _point_number(points, "09:20", "seal_yi")
        seal_25 = _point_number(points, "09:25", "seal_yi")
        amount_25 = _point_number(points, "09:25", "amount_yi")
        pct_25 = _point_number(points, "09:25", "auction_pct")
        rank_25 = _point_int(points, "09:25", "rank_no")

        # The final涨停Top3 may come from auction_candidates even when the historical
        # minute table lacks a 09:25 row. Use the final candidate as the 25 point only
        # for the up-side final list; missing 19/20 points remain blank.
        if seal_25 is None and str(row.get("auction_side") or "") == "up" and _int_value(row.get("final_candidate_rank")) > 0:
            seal_25 = _number_value(row.get("last_seal_yi"))
            amount_25 = _number_value(row.get("last_amount_yi"))
            pct_25 = _number_value(row.get("last_auction_pct"))
            rank_25 = _int_value(row.get("final_candidate_rank"))

        row["seal_19_yi"] = seal_19
        row["seal_20_yi"] = seal_20
        row["seal_25_yi"] = seal_25
        row["change_19_20_yi"] = _delta_value(seal_19, seal_20)
        row["change_20_25_yi"] = _delta_value(seal_20, seal_25)
        row["rank_25"] = rank_25
        row["amount_25_yi"] = amount_25
        row["pct_25"] = pct_25


def _auction_level(row: dict[str, object]) -> tuple[str, str]:
    minute_count = _int_value(row.get("minute_count"))
    final_rank = _int_value(row.get("final_candidate_rank"))
    ratio = _number_value(row.get("stability_ratio"))
    withdraw_pct = _number_value(row.get("last_withdraw_pct"))
    peak_drawdown_pct = _number_value(row.get("peak_drawdown_pct"))
    theme_score = _number_value(row.get("theme_score"))
    prev_limit_up_days = _int_value(row.get("prev_limit_up_days"))
    if minute_count < 2 and final_rank > 0:
        return "最终突入", "只在最后快照出现，封单强弱要结合开盘后承接确认"
    if minute_count < 2:
        return "样本不足", "分钟样本太少，暂不判断封单稳定性"
    if ratio >= 0.8 and withdraw_pct <= 0.2 and peak_drawdown_pct <= 0.2 and (theme_score > 0 or prev_limit_up_days > 0):
        return "强一致", "封单留得住，题材或昨日地位有支撑"
    if ratio >= 0.6 and withdraw_pct <= 0.35 and peak_drawdown_pct <= 0.4:
        return "可观察", "封单仍在，但需要开盘后承接确认"
    if withdraw_pct >= 0.35 or peak_drawdown_pct >= 0.5 or ratio < 0.5:
        return "撤单风险", "封单回落明显，容易开盘兑现或炸板"
    return "一般", "信号不够干净，先看开盘后的真实成交"


def auction_detail_payload(config: MySqlConfig, trade_date: str) -> dict[str, object]:
    day = sql_string(trade_date)
    sql = f"""
    WITH
    prev_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM stock_daily_bars
      WHERE trade_date < CAST({day} AS DATE)
    ),
    minute_rows AS (
      SELECT *
      FROM auction_minute_analysis
      WHERE trade_date=CAST({day} AS DATE)
        AND analysis_kind='limit_up_order'
    ),
    ranked AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY snapshot_minute ASC, rank_no ASC) AS rn_first,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY snapshot_minute DESC, rank_no ASC) AS rn_last
      FROM minute_rows m
    ),
    stats AS (
      SELECT
        code,
        MAX(stock_name) AS stock_name,
        COUNT(DISTINCT snapshot_minute) AS minute_count,
        MIN(rank_no) AS best_rank,
        MAX(COALESCE(seal_amount, 0)) AS max_seal_amount,
        MAX(COALESCE(auction_amount, 0)) AS max_auction_amount
      FROM minute_rows
      GROUP BY code
    ),
    all_codes AS (
      SELECT code FROM stats
      UNION
      SELECT code FROM auction_trend_summary WHERE trade_date=CAST({day} AS DATE)
      UNION
      SELECT code FROM auction_candidates WHERE trade_date=CAST({day} AS DATE)
    )
    SELECT
      c.code,
      COALESCE(l.stock_name, f.stock_name, s.stock_name, ats.stock_name, ac.stock_name, '') AS stock_name,
      DATE_FORMAT(COALESCE(f.snapshot_minute, ats.first_seen_minute, ac.captured_at), '%H:%i') AS first_minute,
      DATE_FORMAT(COALESCE(l.snapshot_minute, ats.last_seen_minute, ac.captured_at), '%H:%i') AS last_minute,
      COALESCE(s.minute_count, IF(COALESCE(ats.final_candidate_rank, ac.rank_no, 0) > 0, 1, 0), 0) AS minute_count,
      COALESCE(s.best_rank, ats.best_pct_rank, 0) AS best_rank,
      COALESCE(l.rank_no, ats.final_candidate_rank, ac.rank_no, 0) AS last_rank,
      COALESCE(ats.final_candidate_rank, ac.rank_no, 0) AS final_candidate_rank,
      COALESCE(f.auction_pct, ats.first_auction_pct, 0) AS first_auction_pct,
      COALESCE(l.auction_pct, ats.last_auction_pct, ac.auction_pct, 0) AS last_auction_pct,
      COALESCE(l.auction_pct, ats.last_auction_pct, ac.auction_pct, 0) - COALESCE(f.auction_pct, ats.first_auction_pct, ac.auction_pct, 0) AS pct_delta,
      COALESCE(f.auction_amount, ats.first_auction_amount, 0) AS first_auction_amount,
      COALESCE(l.auction_amount, ats.last_auction_amount, ac.auction_amount, 0) AS last_auction_amount,
      COALESCE(l.auction_amount, ats.last_auction_amount, ac.auction_amount, 0) - COALESCE(f.auction_amount, ats.first_auction_amount, ac.auction_amount, 0) AS amount_delta,
      COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) AS max_seal_amount,
      COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) AS last_seal_amount,
      GREATEST(
        0,
        COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
        - COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
      ) AS peak_drawdown_amount,
      CASE
        WHEN COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) > 0 THEN
          GREATEST(
            0,
            COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
            - COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
          ) / COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
        ELSE 0
      END AS peak_drawdown_pct,
      GREATEST(0, COALESCE(p.seal_amount, 0) - COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)) AS last_withdraw_amount,
      CASE
        WHEN COALESCE(p.seal_amount, 0) > 0 THEN GREATEST(0, COALESCE(p.seal_amount, 0) - COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)) / COALESCE(p.seal_amount, 0)
        ELSE 0
      END AS last_withdraw_pct,
      CASE
        WHEN COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) > 0 THEN
          COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
          / COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0)
        ELSE 0
      END AS stability_ratio,
      COALESCE(l.theme_score, ats.theme_score, ac.theme_score, 0) AS theme_score,
      COALESCE(CAST(l.theme_matches AS CHAR), CAST(ats.theme_matches AS CHAR), CAST(ac.theme_matches AS CHAR), '[]') AS theme_matches,
      COALESCE(l.sector_hot_count, ats.sector_hot_count, ac.sector_hot_count, 0) AS sector_hot_count,
      COALESCE(l.concept_hot_count, ats.concept_hot_count, ac.concept_hot_count, 0) AS concept_hot_count,
      COALESCE(l.industry, ac.industry, '') AS industry,
      COALESCE(l.sub_industry, ac.sub_industry, '') AS sub_industry,
      COALESCE(ac.risk_flags, l.risk_flags, '') AS risk_flags,
      COALESCE(ats.trend_score, ac.score, l.score, 0) AS trend_score,
      COALESCE(ats.trend_label, '') AS trend_label,
      COALESCE(ats.action_hint, '') AS action_hint,
      COALESCE(l.raw_json->>'$.limit_price', ac.raw_json->>'$.limit_price', '') AS limit_price,
      COALESCE(prev_lu.limit_up_days, 0) AS prev_limit_up_days,
      COALESCE(prev_lu.first_limit_time, '') AS prev_first_limit_time,
      COALESCE(prev_lu.open_count, 0) AS prev_open_count,
      COALESCE(prev_lu.seal_amount, 0) AS prev_close_seal_amount,
      COALESCE(prev_lu.industry_name, '') AS prev_limit_industry,
      COALESCE(prev_rp.pool_rank, 0) AS prev_pool_rank,
      COALESCE(prev_rp.source_kind, '') AS prev_pool_source_kind,
      COALESCE(prev_rp.pct_5d, 0) AS prev_pct_5d,
      DATE_FORMAT((SELECT trade_date FROM prev_day), '%Y-%m-%d') AS prev_trade_date
    FROM all_codes c
    LEFT JOIN stats s ON s.code=c.code
    LEFT JOIN ranked f ON f.code=c.code AND f.rn_first=1
    LEFT JOIN ranked l ON l.code=c.code AND l.rn_last=1
    LEFT JOIN ranked p ON p.code=c.code AND p.rn_last=2
    LEFT JOIN auction_trend_summary ats ON ats.trade_date=CAST({day} AS DATE) AND ats.code=c.code
    LEFT JOIN auction_candidates ac ON ac.trade_date=CAST({day} AS DATE) AND ac.code=c.code
    LEFT JOIN prev_day pd ON 1=1
    LEFT JOIN limit_up_pool_items prev_lu ON prev_lu.trade_date=pd.trade_date AND prev_lu.code=c.code
    LEFT JOIN research_pool_items prev_rp ON prev_rp.trade_date=pd.trade_date AND prev_rp.code=c.code
    ORDER BY
      CASE WHEN COALESCE(ats.final_candidate_rank, ac.rank_no, 0) > 0 THEN 0 ELSE 1 END,
      COALESCE(ats.final_candidate_rank, ac.rank_no, 9999) ASC,
      COALESCE(NULLIF(l.seal_amount, 0), NULLIF(ats.last_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) DESC,
      COALESCE(NULLIF(s.max_seal_amount, 0), NULLIF(ats.max_seal_amount, 0), CAST(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ac.raw_json, '$.seal_amount')), ''), '0') AS DECIMAL(20,2)), 0) DESC;
    """
    timeline_sql = f"""
    SELECT
      code,
      stock_name,
      DATE_FORMAT(snapshot_minute, '%H:%i') AS snapshot_minute,
      rank_no,
      auction_pct,
      auction_amount,
      analysis_kind,
      limit_side,
      seal_amount,
      bid_vol1,
      ask_vol1,
      score
    FROM auction_minute_analysis
    WHERE trade_date=CAST({day} AS DATE)
      AND analysis_kind IN ('limit_up_order', 'limit_down_order')
    ORDER BY snapshot_minute ASC, analysis_kind ASC, rank_no ASC, code ASC;
    """
    risk_sql = f"""
    WITH
    prev_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM stock_daily_bars
      WHERE trade_date < CAST({day} AS DATE)
    ),
    minute_rows AS (
      SELECT *
      FROM auction_minute_analysis
      WHERE trade_date=CAST({day} AS DATE)
        AND analysis_kind='limit_down_order'
    ),
    ranked AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY snapshot_minute ASC, rank_no ASC) AS rn_first,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY snapshot_minute DESC, rank_no ASC) AS rn_last,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY snapshot_minute DESC, rank_no DESC) AS rn_prev
      FROM minute_rows m
    ),
    stats AS (
      SELECT
        code,
        MAX(stock_name) AS stock_name,
        COUNT(DISTINCT snapshot_minute) AS minute_count,
        MIN(rank_no) AS best_rank,
        MAX(COALESCE(seal_amount, 0)) AS max_seal_amount
      FROM minute_rows
      GROUP BY code
    )
    SELECT
      l.code,
      COALESCE(l.stock_name, s.stock_name, '') AS stock_name,
      DATE_FORMAT(f.snapshot_minute, '%H:%i') AS first_minute,
      DATE_FORMAT(l.snapshot_minute, '%H:%i') AS last_minute,
      COALESCE(s.minute_count, 0) AS minute_count,
      COALESCE(s.best_rank, 0) AS best_rank,
      COALESCE(l.rank_no, 0) AS last_rank,
      COALESCE(f.auction_pct, 0) AS first_auction_pct,
      COALESCE(l.auction_pct, 0) AS last_auction_pct,
      COALESCE(l.auction_pct, 0) - COALESCE(f.auction_pct, 0) AS pct_delta,
      COALESCE(f.auction_amount, 0) AS first_auction_amount,
      COALESCE(l.auction_amount, 0) AS last_auction_amount,
      COALESCE(l.auction_amount, 0) - COALESCE(f.auction_amount, 0) AS amount_delta,
      COALESCE(s.max_seal_amount, 0) AS max_seal_amount,
      COALESCE(l.seal_amount, 0) AS last_seal_amount,
      GREATEST(0, COALESCE(s.max_seal_amount, 0) - COALESCE(l.seal_amount, 0)) AS peak_drawdown_amount,
      CASE WHEN COALESCE(s.max_seal_amount, 0) > 0
        THEN GREATEST(0, COALESCE(s.max_seal_amount, 0) - COALESCE(l.seal_amount, 0)) / COALESCE(s.max_seal_amount, 0)
        ELSE 0
      END AS peak_drawdown_pct,
      GREATEST(0, COALESCE(p.seal_amount, 0) - COALESCE(l.seal_amount, 0)) AS last_withdraw_amount,
      CASE WHEN COALESCE(p.seal_amount, 0) > 0
        THEN GREATEST(0, COALESCE(p.seal_amount, 0) - COALESCE(l.seal_amount, 0)) / COALESCE(p.seal_amount, 0)
        ELSE 0
      END AS last_withdraw_pct,
      CASE WHEN COALESCE(s.max_seal_amount, 0) > 0 THEN COALESCE(l.seal_amount, 0) / COALESCE(s.max_seal_amount, 0) ELSE 0 END AS stability_ratio,
      COALESCE(l.theme_score, 0) AS theme_score,
      COALESCE(CAST(l.theme_matches AS CHAR), '[]') AS theme_matches,
      COALESCE(l.sector_hot_count, 0) AS sector_hot_count,
      COALESCE(l.concept_hot_count, 0) AS concept_hot_count,
      COALESCE(l.industry, '') AS industry,
      COALESCE(l.sub_industry, '') AS sub_industry,
      COALESCE(l.risk_flags, '') AS risk_flags,
      COALESCE(l.score, 0) AS trend_score,
      COALESCE(l.raw_json->>'$.limit_price', '') AS limit_price,
      COALESCE(prev_lu.limit_up_days, 0) AS prev_limit_up_days,
      COALESCE(prev_lu.first_limit_time, '') AS prev_first_limit_time,
      COALESCE(prev_lu.open_count, 0) AS prev_open_count,
      COALESCE(prev_lu.seal_amount, 0) AS prev_close_seal_amount,
      COALESCE(prev_lu.industry_name, '') AS prev_limit_industry,
      COALESCE(prev_rp.pool_rank, 0) AS prev_pool_rank,
      COALESCE(prev_rp.source_kind, '') AS prev_pool_source_kind,
      COALESCE(prev_rp.pct_5d, 0) AS prev_pct_5d,
      DATE_FORMAT((SELECT trade_date FROM prev_day), '%Y-%m-%d') AS prev_trade_date
    FROM ranked l
    LEFT JOIN ranked f ON f.code=l.code AND f.rn_first=1
    LEFT JOIN ranked p ON p.code=l.code AND p.rn_prev=2
    LEFT JOIN stats s ON s.code=l.code
    LEFT JOIN prev_day pd ON 1=1
    LEFT JOIN limit_up_pool_items prev_lu ON prev_lu.trade_date=pd.trade_date AND prev_lu.code=l.code
    LEFT JOIN research_pool_items prev_rp ON prev_rp.trade_date=pd.trade_date AND prev_rp.code=l.code
    WHERE l.rn_last=1
    ORDER BY COALESCE(l.rank_no, 9999) ASC, COALESCE(l.seal_amount, 0) DESC
    LIMIT 20;
    """
    rows: list[dict[str, object]] = []
    for raw in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(raw) < 41:
            continue
        row = {
            "auction_side": "up",
            "code": raw[0],
            "stock_name": raw[1],
            "first_minute": raw[2],
            "last_minute": raw[3],
            "minute_count": _int_value(raw[4]),
            "best_rank": _int_value(raw[5]),
            "last_rank": _int_value(raw[6]),
            "final_candidate_rank": _int_value(raw[7]),
            "first_auction_pct": _number_value(raw[8]),
            "last_auction_pct": _number_value(raw[9]),
            "pct_delta": _number_value(raw[10]),
            "first_amount_yi": round(_number_value(raw[11]) / 100000000, 4),
            "last_amount_yi": round(_number_value(raw[12]) / 100000000, 4),
            "amount_delta_yi": round(_number_value(raw[13]) / 100000000, 4),
            "max_seal_yi": round(_number_value(raw[14]) / 100000000, 4),
            "last_seal_yi": round(_number_value(raw[15]) / 100000000, 4),
            "peak_drawdown_yi": round(_number_value(raw[16]) / 100000000, 4),
            "peak_drawdown_pct": round(_number_value(raw[17]), 4),
            "last_withdraw_yi": round(_number_value(raw[18]) / 100000000, 4),
            "last_withdraw_pct": round(_number_value(raw[19]), 4),
            "stability_ratio": round(_number_value(raw[20]), 4),
            "theme_score": _number_value(raw[21]),
            "theme_matches": _json_value(raw[22], []),
            "sector_hot_count": _int_value(raw[23]),
            "concept_hot_count": _int_value(raw[24]),
            "industry": raw[25],
            "sub_industry": raw[26],
            "risk_flags": raw[27],
            "trend_score": _number_value(raw[28]),
            "trend_label": raw[29],
            "action_hint": raw[30],
            "limit_price": raw[31],
            "prev_limit_up_days": _int_value(raw[32]),
            "prev_first_limit_time": raw[33],
            "prev_open_count": _int_value(raw[34]),
            "prev_close_seal_yi": round(_number_value(raw[35]) / 100000000, 4),
            "prev_limit_industry": raw[36],
            "prev_pool_rank": _int_value(raw[37]),
            "prev_pool_source_kind": raw[38],
            "prev_pct_5d": _number_value(raw[39]),
            "prev_trade_date": raw[40],
        }
        label, reason = _auction_level(row)
        row["auction_label"] = label
        row["auction_reason"] = reason
        rows.append(row)

    risk_rows: list[dict[str, object]] = []
    for raw in mysql_rows(run_mysql(config, risk_sql, batch=True, raw=True)):
        if len(raw) < 36:
            continue
        row = {
            "auction_side": "down",
            "code": raw[0],
            "stock_name": raw[1],
            "first_minute": raw[2],
            "last_minute": raw[3],
            "minute_count": _int_value(raw[4]),
            "best_rank": _int_value(raw[5]),
            "last_rank": _int_value(raw[6]),
            "final_candidate_rank": 0,
            "risk_rank": _int_value(raw[6]),
            "first_auction_pct": _number_value(raw[7]),
            "last_auction_pct": _number_value(raw[8]),
            "pct_delta": _number_value(raw[9]),
            "first_amount_yi": round(_number_value(raw[10]) / 100000000, 4),
            "last_amount_yi": round(_number_value(raw[11]) / 100000000, 4),
            "amount_delta_yi": round(_number_value(raw[12]) / 100000000, 4),
            "max_seal_yi": round(_number_value(raw[13]) / 100000000, 4),
            "last_seal_yi": round(_number_value(raw[14]) / 100000000, 4),
            "peak_drawdown_yi": round(_number_value(raw[15]) / 100000000, 4),
            "peak_drawdown_pct": round(_number_value(raw[16]), 4),
            "last_withdraw_yi": round(_number_value(raw[17]) / 100000000, 4),
            "last_withdraw_pct": round(_number_value(raw[18]), 4),
            "stability_ratio": round(_number_value(raw[19]), 4),
            "theme_score": _number_value(raw[20]),
            "theme_matches": _json_value(raw[21], []),
            "sector_hot_count": _int_value(raw[22]),
            "concept_hot_count": _int_value(raw[23]),
            "industry": raw[24],
            "sub_industry": raw[25],
            "risk_flags": raw[26],
            "trend_score": _number_value(raw[27]),
            "trend_label": "跌停封单",
            "action_hint": "观察是否扩散到同题材或昨日核心，风险侧强时先看承接再动手。",
            "limit_price": raw[28],
            "prev_limit_up_days": _int_value(raw[29]),
            "prev_first_limit_time": raw[30],
            "prev_open_count": _int_value(raw[31]),
            "prev_close_seal_yi": round(_number_value(raw[32]) / 100000000, 4),
            "prev_limit_industry": raw[33],
            "prev_pool_rank": _int_value(raw[34]),
            "prev_pool_source_kind": raw[35],
            "prev_pct_5d": _number_value(raw[36]) if len(raw) > 36 else 0,
            "prev_trade_date": raw[37] if len(raw) > 37 else "",
            "auction_label": "跌停封单",
            "auction_reason": "竞价跌停侧封单靠前，主要用于识别开盘风险和负反馈来源",
        }
        risk_rows.append(row)

    timeline: list[dict[str, object]] = []
    for raw in mysql_rows(run_mysql(config, timeline_sql, batch=True, raw=True)):
        if len(raw) < 12:
            continue
        timeline.append(
            {
                "code": raw[0],
                "stock_name": raw[1],
                "minute": raw[2],
                "rank_no": _int_value(raw[3]),
                "auction_pct": _number_value(raw[4]),
                "amount_yi": round(_number_value(raw[5]) / 100000000, 4),
                "analysis_kind": raw[6],
                "limit_side": raw[7],
                "seal_yi": round(_number_value(raw[8]) / 100000000, 4),
                "bid_vol1": _int_value(raw[9]),
                "ask_vol1": _int_value(raw[10]),
                "score": _number_value(raw[11]),
            }
        )
    minutes = sorted({str(item.get("minute") or "") for item in timeline if item.get("minute")})
    latest_minute = minutes[-1] if minutes else ""
    final_count = sum(1 for row in rows if _int_value(row.get("final_candidate_rank")) > 0)
    missing_final_snapshot = bool(latest_minute and latest_minute < "09:25" and final_count <= 0)
    for row in rows:
        if (
            latest_minute
            and str(row.get("last_minute") or "") < latest_minute
            and _int_value(row.get("final_candidate_rank")) <= 0
        ):
            row["last_withdraw_yi"] = max(_number_value(row.get("last_withdraw_yi")), _number_value(row.get("last_seal_yi")))
            row["last_withdraw_pct"] = 1.0
            row["peak_drawdown_yi"] = max(_number_value(row.get("peak_drawdown_yi")), _number_value(row.get("max_seal_yi")))
            row["peak_drawdown_pct"] = 1.0
            row["stability_ratio"] = 0.0
            row["auction_label"] = "尾盘掉榜"
            row["auction_reason"] = "前面进过封单榜，但最后一分钟不在榜内，说明封单被撤或被更强封单挤出"
        elif (
            missing_final_snapshot
            and str(row.get("auction_label") or "") in {"强一致", "可观察"}
            and _int_value(row.get("final_candidate_rank")) <= 0
        ):
            row["auction_label"] = "过程强"
            row["auction_reason"] = "分钟雷达里表现强，但缺少09:25最终快照，不能确认最终封单强度"
    _enrich_auction_points(rows + risk_rows, timeline)

    opportunity_top3 = sorted(
        [row for row in rows if _int_value(row.get("final_candidate_rank")) > 0],
        key=lambda item: _int_value(item.get("final_candidate_rank")) or 9999,
    )[:3]
    if not opportunity_top3:
        opportunity_top3 = sorted(rows, key=lambda item: -_number_value(item.get("last_seal_yi")))[:3]
    risk_top3 = sorted(risk_rows, key=lambda item: (_int_value(item.get("risk_rank")) or 9999, -_number_value(item.get("last_seal_yi"))))[:3]
    display_rows = rows + risk_rows
    label_counts: dict[str, int] = {}
    for row in display_rows:
        label = str(row.get("auction_label") or "未分类")
        label_counts[label] = label_counts.get(label, 0) + 1
    minute_rank_depth = max((_int_value(item.get("rank_no")) for item in timeline), default=0)
    high_risk_count = sum(1 for row in rows if str(row.get("auction_label") or "") in {"撤单风险", "尾盘掉榜"})
    return {
        "trade_date": trade_date,
        "source": "auction_minute_analysis + auction_candidates + auction_trend_summary",
        "row_count": len(display_rows),
        "final_count": final_count,
        "risk_final_count": len(risk_top3),
        "strong_count": sum(1 for row in rows if str(row.get("auction_label") or "") == "强一致"),
        "high_risk_count": high_risk_count + len(risk_top3),
        "risk_count": high_risk_count + len(risk_top3),
        "label_counts": label_counts,
        "minute_rank_depth": minute_rank_depth,
        "minutes": minutes,
        "opportunity_top3": opportunity_top3,
        "risk_top3": risk_top3,
        "rows": display_rows,
        "timeline": timeline,
    }





def create_app(config: MySqlConfig) -> FastAPI:
    ensure_async_evidence_summary_table(config)
    root_cache.ensure_root_evidence_cache_table(config)
    ensure_market_width_tables(config)
    ensure_headline_theme_role_evidence_table(config)
    ensure_leaderboard_snapshot_table(config)
    ensure_kpl_featured_section_table(config)
    ensure_kpl_market_capacity_tables(config)
    ensure_kpl_plate_detail_table(config)
    ensure_kpl_plate_strength_table(config)
    ensure_kpl_replay_limit_theme_tables(config)
    app = FastAPI(title=APP_TITLE)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return HTML

    @app.get("/favicon.ico")
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/market-width", response_class=HTMLResponse)
    def market_width_page() -> str:
        return MARKET_WIDTH_HTML

    @app.get("/leaders", response_class=HTMLResponse)
    def leaders_page() -> str:
        return LEADERS_HTML

    @app.get("/kpl-leaders", response_class=HTMLResponse)
    def kpl_leaders_page() -> str:
        return KPL_LEADERS_HTML

    @app.get("/plate-breakouts", response_class=HTMLResponse)
    def plate_breakouts_page() -> str:
        return PLATE_BREAKOUT_HTML

    @app.get("/auction-detail", response_class=HTMLResponse)
    def auction_detail_page() -> str:
        return AUCTION_DETAIL_HTML

    @app.get("/api/top10")
    def api_top10() -> JSONResponse:
        status = json_query(config, status_sql(), {})
        payload = {
            "window": json_query(config, latest_window_sql(), {}),
            "window_top10": json_query(config, window_top10_sql(), []),
            "latest_scan": json_query(config, latest_scan_sql(), {"run": None, "rows": []}),
            "status": attach_data_source_health(status, config),
        }
        return JSONResponse(payload)

    @app.get("/api/feed")
    def api_feed(trade_date: str = "", pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = full_pool_ma_mode()
        research_codes = ResearchPoolProvider(config).latest_codes(target_date, ma_mode=ma_mode)
        missing_codes = root_cache.root_evidence_cache_missing_codes(config, target_date, research_codes)
        if missing_codes:
            root_cache.refresh_root_evidence_cache(config, target_date, codes=missing_codes, force=True)
        feed = json_query(config, intel_feed_list_sql(target_date), [])
        status = json_query(config, status_sql(target_date), {})
        feed_rows = feed if isinstance(feed, list) else []
        payload = {
            "trade_date": target_date,
            "service_context": build_service_context(config, target_date, ma_mode=ma_mode),
            **pool_mode_payload(ma_mode),
            "feed": feed,
            "feed_meta": {
                "count": len(feed_rows),
                "scan_count": sum(1 for item in feed_rows if isinstance(item, dict) and item.get("kind") == "scan"),
                "window_count": sum(1 for item in feed_rows if isinstance(item, dict) and item.get("kind") == "window"),
            },
            "status": attach_data_source_health(status, config),
            "window": json_query(config, latest_window_sql(target_date), {}),
            "feed_runtime": json_query(config, feed_runtime_sql(target_date), {}),
        }
        return JSONResponse(payload)

    @app.get("/api/feed/detail")
    def api_feed_detail(trade_date: str = "", kind: str = "", event_time: str = "", code: str = "", pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = full_pool_ma_mode()
        if code:
            if not root_cache.root_evidence_cache_code_exists(config, target_date, code):
                root_cache.refresh_root_evidence_cache(config, target_date, codes=[code], force=True)
        else:
            research_codes = ResearchPoolProvider(config).latest_codes(target_date, ma_mode=ma_mode)
            missing_codes = root_cache.root_evidence_cache_missing_codes(config, target_date, research_codes)
            if missing_codes:
                root_cache.refresh_root_evidence_cache(config, target_date, codes=missing_codes, force=True)
        feed = json_query(config, intel_feed_sql(target_date, kind=kind, event_time=event_time, code=code), [])
        rows = attach_evidence_views(feed)
        row = rows[0] if isinstance(rows, list) and rows else {}
        return JSONResponse({
            "trade_date": target_date,
            "service_context": build_service_context(config, target_date, ma_mode=ma_mode),
            **pool_mode_payload(ma_mode),
            "row": row,
        })

    @app.get("/api/trade_dates")
    def api_trade_dates() -> JSONResponse:
        now = time.monotonic()
        cached = _TRADE_DATES_CACHE.get("payload")
        if isinstance(cached, dict) and now - float(_TRADE_DATES_CACHE.get("ts") or 0) < _TRADE_DATES_CACHE_TTL_SECONDS:
            return JSONResponse(cached)
        payload = json_query(config, trade_dates_sql(), {"latest": latest_data_date(config), "dates": []})
        if not isinstance(payload, dict):
            payload = {"latest": latest_data_date(config), "dates": []}
        _TRADE_DATES_CACHE.update({"ts": now, "payload": payload})
        return JSONResponse(payload)

    @app.get("/api/market-width")
    def api_market_width(trade_date: str = "", limit: int = 240, pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = full_pool_ma_mode()
        series_limit = min(max(int(limit or 240), 10), 720)
        cache_key = f"market-width:{target_date}:{ma_mode}:{series_limit}"
        cached = cached_response(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        market_data_date = target_date
        latest = json_query(config, market_width_latest_sql(target_date, ma_mode=ma_mode), {})
        if not isinstance(latest, dict) or not latest:
            latest = json_query(config, market_width_latest_sql(ma_mode=ma_mode), {})
            if isinstance(latest, dict) and latest.get("trade_date"):
                market_data_date = str(latest.get("trade_date"))
        snapshot_id = str(latest.get("snapshot_id") or "") if isinstance(latest, dict) else ""
        series = sort_market_width_series(
            json_query(config, market_width_series_sql(market_data_date, limit=series_limit, ma_mode=ma_mode), [])
        )
        payload = {
            "trade_date": target_date,
            "market_data_trade_date": market_data_date,
            "service_context": build_service_context(config, target_date, ma_mode=ma_mode),
            **pool_mode_payload(ma_mode),
            "latest": latest,
            "series": series,
            "cycle_5d": sort_market_width_cycle(json_query(config, market_width_cycle_5d_sql(market_data_date, ma_mode=ma_mode), [])),
            "top50": json_query(config, market_width_top50_sql(snapshot_id=snapshot_id, trade_date=market_data_date, ma_mode=ma_mode), []),
        }
        store_response(cache_key, payload)
        return JSONResponse(payload)

    @app.get("/api/leaders")
    def api_leaders(trade_date: str = "", pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = resolve_pool_ma_mode(pool_mode)
        cache_key = f"leaders:{target_date}:{ma_mode}"
        cached = cached_response(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        payload = latest_leaderboard_snapshot_payload_by_source(config, target_date, source=SNAPSHOT_SOURCE, exact=False, ma_mode=ma_mode)
        leader_data_date = str(payload.get("leader_data_trade_date") or payload.get("trade_date") or "") if isinstance(payload, dict) else ""
        if payload is None:
            leader_data_date = explicit_leader_data_date(config, trade_date, target_date, SNAPSHOT_SOURCE, ma_mode)
            ensure_selected_research_pool(config, leader_data_date, ma_mode)
            if leader_data_date != target_date:
                ensure_selected_research_pool(config, target_date, ma_mode)
            payload = json_query(config, leaderboard_sql(leader_data_date), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["service_trade_date"] = target_date
        payload["leader_data_trade_date"] = payload.get("leader_data_trade_date") or payload.get("trade_date") or leader_data_date
        payload["leader_data_source"] = payload.get("leader_data_source") or "dynamic_sql"
        payload["leader_data_label"] = f"{payload['leader_data_trade_date']} 收盘确认，服务 {target_date}"
        payload["trade_date"] = target_date
        payload["service_context"] = build_service_context(config, target_date, ma_mode=ma_mode)
        payload.update(pool_mode_payload(ma_mode))
        payload = compact_leaderboard_payload(payload)
        store_response(cache_key, payload)
        return JSONResponse(payload)

    @app.get("/api/kpl-leaders")
    def api_kpl_leaders(trade_date: str = "", pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = resolve_pool_ma_mode(pool_mode)
        cache_key = f"kpl-leaders:{target_date}:{ma_mode}"
        cached = cached_response(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        payload = latest_leaderboard_snapshot_payload_by_source(config, target_date, source=KPL_SNAPSHOT_SOURCE, exact=False, ma_mode=ma_mode)
        leader_data_date = str(payload.get("leader_data_trade_date") or payload.get("trade_date") or "") if isinstance(payload, dict) else ""
        if payload is None:
            leader_data_date = explicit_leader_data_date(config, trade_date, target_date, KPL_SNAPSHOT_SOURCE, ma_mode)
            ensure_selected_research_pool(config, leader_data_date, ma_mode)
            if leader_data_date != target_date:
                ensure_selected_research_pool(config, target_date, ma_mode)
            payload = json_query(config, kpl_leaderboard_sql(leader_data_date, ma_mode=ma_mode), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["service_trade_date"] = target_date
        payload["leader_data_trade_date"] = payload.get("trade_date") or leader_data_date
        payload["leader_data_source"] = KPL_SNAPSHOT_SOURCE
        payload["leader_data_label"] = f"{payload['leader_data_trade_date']} 收盘确认，服务 {target_date}"
        payload["trade_date"] = target_date
        payload["service_context"] = build_service_context(config, target_date, ma_mode=ma_mode)
        payload.update(pool_mode_payload(ma_mode))
        payload = compact_leaderboard_payload(payload)
        store_response(cache_key, payload)
        return JSONResponse(payload)

    @app.get("/api/plate-breakouts")
    def api_plate_breakouts(trade_date: str = "", pool_mode: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        ma_mode = resolve_pool_ma_mode(pool_mode)
        cache_key = f"plate-breakouts:{target_date}:{ma_mode}"
        cached = cached_response(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        payload = json_query(config, kpl_plate_breakout_sql(target_date, ma_mode=ma_mode), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["trade_date"] = target_date
        payload["service_context"] = build_service_context(config, target_date, ma_mode=ma_mode)
        payload.update(pool_mode_payload(ma_mode))
        store_response(cache_key, payload)
        return JSONResponse(payload)

    @app.get("/api/auction-detail")
    def api_auction_detail(trade_date: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        cache_key = f"auction-detail:{target_date}"
        cached = cached_response(cache_key)
        if cached is not None:
            return JSONResponse(cached)
        payload = auction_detail_payload(config, target_date)
        payload["service_context"] = build_service_context(config, target_date, ma_mode=full_pool_ma_mode())
        store_response(cache_key, payload)
        return JSONResponse(payload)

    return app


from stock_scout_web_templates import AUCTION_DETAIL_HTML, HTML, MARKET_WIDTH_HTML, LEADERS_HTML, KPL_LEADERS_HTML, PLATE_BREAKOUT_HTML


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stock Scout web dashboard.")
    parser.add_argument("--host", default=os.environ.get("STOCK_SCOUT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STOCK_SCOUT_WEB_PORT", "8788")))
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    app = create_app(config)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
