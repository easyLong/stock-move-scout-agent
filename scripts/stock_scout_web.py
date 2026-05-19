#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stock_move_scout.feed import (
    auction_top10_sql,
    build_evidence_view,
    intel_feed_list_sql,
    intel_feed_sql,
    kpl_leaderboard_sql,
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
    latest_leaderboard_snapshot_payload,
    latest_leaderboard_snapshot_payload_by_source,
    materialize_kpl_leaderboard_snapshot,
)
from stock_move_scout.market_width import ensure_market_width_tables
from stock_move_scout.research_pool import ResearchPoolProvider, ensure_headline_theme_role_evidence_table
from stock_move_scout.sources.kpl_featured_sections import ensure_kpl_featured_section_table
from stock_move_scout.sources.kpl_market_capacity import ensure_kpl_market_capacity_tables
from stock_move_scout.sources.kpl_plate_strength import ensure_kpl_plate_strength_table
from stock_move_scout.sources.kpl_replay_limit_themes import ensure_kpl_replay_limit_theme_tables
from stock_move_scout.web import json_query, latest_data_date, resolve_trade_date
from stock_move_scout.web.service_context import build_service_context

from stock_scout_mysql import MySqlConfig, add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string


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


def resolve_leader_data_date(config: MySqlConfig, service_trade_date: str) -> str:
    """Leaderboards are post-close conclusions for the next trading day."""
    sql = f"""
    SELECT DATE_FORMAT(MAX(trade_date), '%Y-%m-%d')
    FROM (
      SELECT trade_date FROM leaderboard_snapshots WHERE trade_date < {sql_string(service_trade_date)}
      UNION
      SELECT trade_date FROM limit_up_pool_items WHERE trade_date < {sql_string(service_trade_date)}
      UNION
      SELECT trade_date FROM research_pool_items WHERE trade_date < {sql_string(service_trade_date)}
      UNION
      SELECT trade_date FROM stock_daily_bars WHERE trade_date < {sql_string(service_trade_date)}
    ) leader_days;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        rows = []
    leader_day = str(rows[0][0] or "").strip() if rows and rows[0] else ""
    return leader_day or service_trade_date


def explicit_leader_data_date(config: MySqlConfig, requested_trade_date: str, target_date: str, source: str) -> str:
    """Historical page selections should show that date's post-close snapshot directly."""
    requested = str(requested_trade_date or "").strip()
    if not requested or requested.lower() == "latest":
        return resolve_leader_data_date(config, target_date)
    sql = f"""
    SELECT 1
    FROM leaderboard_snapshots
    WHERE trade_date={sql_string(target_date)}
      AND source={sql_string(source)}
    LIMIT 1;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        rows = []
    return target_date if rows else resolve_leader_data_date(config, target_date)


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





def create_app(config: MySqlConfig) -> FastAPI:
    ensure_async_evidence_summary_table(config)
    root_cache.ensure_root_evidence_cache_table(config)
    ensure_market_width_tables(config)
    ensure_headline_theme_role_evidence_table(config)
    ensure_leaderboard_snapshot_table(config)
    ensure_kpl_featured_section_table(config)
    ensure_kpl_market_capacity_tables(config)
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

    @app.get("/api/top10")
    def api_top10() -> JSONResponse:
        status = json_query(config, status_sql(), {})
        payload = {
            "window": json_query(config, latest_window_sql(), {}),
            "window_top10": json_query(config, window_top10_sql(), []),
            "auction_top10": json_query(config, auction_top10_sql(), []),
            "latest_scan": json_query(config, latest_scan_sql(), {"run": None, "rows": []}),
            "status": attach_data_source_health(status, config),
        }
        return JSONResponse(payload)

    @app.get("/api/feed")
    def api_feed(trade_date: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        research_codes = ResearchPoolProvider(config).latest_codes(target_date)
        missing_codes = root_cache.root_evidence_cache_missing_codes(config, target_date, research_codes)
        if missing_codes:
            root_cache.refresh_root_evidence_cache(config, target_date, codes=missing_codes, force=True)
        feed = json_query(config, intel_feed_list_sql(target_date), [])
        status = json_query(config, status_sql(target_date), {})
        feed_rows = feed if isinstance(feed, list) else []
        payload = {
            "trade_date": target_date,
            "service_context": build_service_context(config, target_date),
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
    def api_feed_detail(trade_date: str = "", kind: str = "", event_time: str = "", code: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        if code:
            if not root_cache.root_evidence_cache_code_exists(config, target_date, code):
                root_cache.refresh_root_evidence_cache(config, target_date, codes=[code], force=True)
        else:
            research_codes = ResearchPoolProvider(config).latest_codes(target_date)
            missing_codes = root_cache.root_evidence_cache_missing_codes(config, target_date, research_codes)
            if missing_codes:
                root_cache.refresh_root_evidence_cache(config, target_date, codes=missing_codes, force=True)
        feed = json_query(config, intel_feed_sql(target_date, kind=kind, event_time=event_time, code=code), [])
        rows = attach_evidence_views(feed)
        row = rows[0] if isinstance(rows, list) and rows else {}
        return JSONResponse({"trade_date": target_date, "service_context": build_service_context(config, target_date), "row": row})

    @app.get("/api/trade_dates")
    def api_trade_dates() -> JSONResponse:
        return JSONResponse(json_query(config, trade_dates_sql(), {"latest": latest_data_date(config), "dates": []}))

    @app.get("/api/market-width")
    def api_market_width(trade_date: str = "", limit: int = 240) -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        market_data_date = target_date
        latest = json_query(config, market_width_latest_sql(target_date), {})
        if not isinstance(latest, dict) or not latest:
            latest = json_query(config, market_width_latest_sql(), {})
            if isinstance(latest, dict) and latest.get("trade_date"):
                market_data_date = str(latest.get("trade_date"))
        snapshot_id = str(latest.get("snapshot_id") or "") if isinstance(latest, dict) else ""
        series = sort_market_width_series(
            json_query(config, market_width_series_sql(market_data_date, limit=min(max(int(limit or 240), 10), 720)), [])
        )
        payload = {
            "trade_date": target_date,
            "market_data_trade_date": market_data_date,
            "service_context": build_service_context(config, target_date),
            "latest": latest,
            "series": series,
            "cycle_5d": sort_market_width_cycle(json_query(config, market_width_cycle_5d_sql(market_data_date), [])),
            "top50": json_query(config, market_width_top50_sql(snapshot_id=snapshot_id, trade_date=market_data_date), []),
        }
        return JSONResponse(payload)

    @app.get("/api/leaders")
    def api_leaders(trade_date: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        leader_data_date = explicit_leader_data_date(config, trade_date, target_date, SNAPSHOT_SOURCE)
        snapshot_payload = latest_leaderboard_snapshot_payload(config, leader_data_date)
        if snapshot_payload is not None:
            payload = snapshot_payload
        else:
            ResearchPoolProvider(config).latest_snapshot(leader_data_date)
            payload = json_query(config, leaderboard_sql(leader_data_date), {})
        if not isinstance(payload, dict):
            payload = {}
        payload["service_trade_date"] = target_date
        payload["leader_data_trade_date"] = payload.get("leader_data_trade_date") or payload.get("trade_date") or leader_data_date
        payload["leader_data_source"] = payload.get("leader_data_source") or ("dynamic_sql" if snapshot_payload is None else "post_close_confirm")
        payload["leader_data_label"] = f"{payload['leader_data_trade_date']} 收盘确认，服务 {target_date}"
        payload["trade_date"] = target_date
        payload["service_context"] = build_service_context(config, target_date)
        return JSONResponse(payload)

    @app.get("/api/kpl-leaders")
    def api_kpl_leaders(trade_date: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        leader_data_date = explicit_leader_data_date(config, trade_date, target_date, KPL_SNAPSHOT_SOURCE)
        snapshot_payload = latest_leaderboard_snapshot_payload_by_source(
            config,
            leader_data_date,
            source=KPL_SNAPSHOT_SOURCE,
            exact=True,
        )
        if snapshot_payload is not None:
            payload = snapshot_payload
        else:
            ResearchPoolProvider(config).latest_snapshot(leader_data_date)
            materialize_kpl_leaderboard_snapshot(config, leader_data_date)
            payload = latest_leaderboard_snapshot_payload_by_source(
                config,
                leader_data_date,
                source=KPL_SNAPSHOT_SOURCE,
                exact=True,
            ) or {}
        if not isinstance(payload, dict):
            payload = {}
        payload["service_trade_date"] = target_date
        payload["leader_data_trade_date"] = payload.get("trade_date") or leader_data_date
        payload["leader_data_source"] = KPL_SNAPSHOT_SOURCE
        payload["leader_data_label"] = f"{payload['leader_data_trade_date']} 收盘确认，服务 {target_date}"
        payload["trade_date"] = target_date
        payload["service_context"] = build_service_context(config, target_date)
        return JSONResponse(payload)

    return app


from stock_scout_web_templates import HTML, MARKET_WIDTH_HTML, LEADERS_HTML, KPL_LEADERS_HTML


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
