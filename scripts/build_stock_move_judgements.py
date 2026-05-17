#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.analysis.activity import activity_context_from_index as build_activity_context, build_activity_index, clean_anchor
from stock_move_scout.analysis.influence import influence_payload, influence_score, initiative_score, short_term_behavior_score
from stock_move_scout.judgement import build_display_contract
from stock_move_scout.research_pool import ResearchPoolProvider, research_pool_cte

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_string,
)
from stock_move_scout.event_engine import ensure_event_engine_tables
from stock_move_scout.web import resolve_trade_date


def compact(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def parse_json(value: Any, fallback: Any) -> Any:
    if value is None or value == "":
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def ensure_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_move_judgements (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      event_time DATETIME(3) NOT NULL,
      event_type ENUM('realtime','stable','auction') NOT NULL DEFAULT 'realtime',
      event_id VARCHAR(64) NOT NULL DEFAULT '',
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      primary_anchor VARCHAR(128) NOT NULL DEFAULT '',
      anchor_type VARCHAR(64) NOT NULL DEFAULT '',
      move_explanation VARCHAR(255) NOT NULL DEFAULT '',
      explanation_strength ENUM('strong','medium','weak','none') NOT NULL DEFAULT 'none',
      sustainability_label ENUM('可持续','观察','脉冲','走弱') NOT NULL DEFAULT '观察',
      sustainability_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      hard_catalyst_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      anchor_leadership_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      tape_confirm_score DECIMAL(6,2) NOT NULL DEFAULT 0,
      anchor_risk_deduction DECIMAL(6,2) NOT NULL DEFAULT 0,
      support_items JSON NULL,
      risk_item VARCHAR(255) NOT NULL DEFAULT '',
      final_view VARCHAR(255) NOT NULL DEFAULT '',
      score_detail JSON NULL,
      market_snapshot JSON NULL,
      evidence_snapshot JSON NULL,
      source_hash CHAR(64) NOT NULL DEFAULT '',
      model VARCHAR(128) NOT NULL DEFAULT 'rule_v2',
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_move_judgement_event (event_type, event_id, code),
      KEY idx_move_judgement_day_time (trade_date, event_time),
      KEY idx_move_judgement_code_day (code, trade_date),
      KEY idx_move_judgement_score (sustainability_score)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)
    ensure_column(config, "hard_catalyst_score", "DECIMAL(6,2) NOT NULL DEFAULT 0")
    ensure_column(config, "anchor_leadership_score", "DECIMAL(6,2) NOT NULL DEFAULT 0")
    ensure_column(config, "tape_confirm_score", "DECIMAL(6,2) NOT NULL DEFAULT 0")
    ensure_column(config, "anchor_risk_deduction", "DECIMAL(6,2) NOT NULL DEFAULT 0")


def ensure_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'stock_move_judgements'
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE stock_move_judgements ADD COLUMN {column_name} {column_sql} AFTER sustainability_score;")


def sql_code_filter(alias: str, codes: list[str]) -> str:
    clean_codes = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not clean_codes:
        return ""
    return f"AND {alias}.code IN ({','.join(sql_string(code) for code in clean_codes)})"


def load_raw_events(
    config: MySqlConfig,
    trade_date: str,
    scan_top: int,
    window_top: int,
    latest_only: bool,
    limit: int,
    codes: list[str] | None = None,
    research_pool_only: bool = False,
) -> list[dict[str, Any]]:
    scan_scope = "ORDER BY scanned_at DESC LIMIT 1" if latest_only else "ORDER BY scanned_at DESC"
    window_scope = "ORDER BY ended_at DESC LIMIT 1" if latest_only else "ORDER BY ended_at DESC"
    codes = codes or []
    scan_code_filter = sql_code_filter("sm", codes)
    window_code_filter = sql_code_filter("wm", codes)
    pool_cte = f"{research_pool_cte(trade_date)}," if research_pool_only else ""
    scan_pool_join = "JOIN research_pool rp ON rp.code=sm.code" if research_pool_only else ""
    window_pool_join = "JOIN research_pool rp ON rp.code=wm.code" if research_pool_only else ""
    scan_rank_filter = f"AND sm.rank_speed <= {int(scan_top)}" if not research_pool_only else ""
    window_rank_filter = f"AND wm.rank_no <= {int(window_top)}" if not research_pool_only else ""
    sql = f"""
    WITH
    {pool_cte}
    scan_scope AS (
      SELECT id, run_id, scanned_at
      FROM scan_runs
      WHERE DATE(scanned_at)={sql_string(trade_date)}
        AND accepted=1
      {scan_scope}
    ),
    window_scope AS (
      SELECT id, window_id, started_at, ended_at
      FROM windows
      WHERE DATE(ended_at)={sql_string(trade_date)}
        AND status='done'
        AND aggregate_count > 0
      {window_scope}
    )
    SELECT *
    FROM (
      SELECT
        'realtime' AS event_type,
        sr.run_id AS event_id,
        sr.scanned_at AS event_time,
        sm.code,
        sm.name AS stock_name,
        sm.rank_speed AS rank_no,
        COALESCE(sm.pct_change, 0) AS pct_change,
        COALESCE(sm.speed, 0) AS speed,
        COALESCE(sm.amount, 0) AS amount,
        COALESCE(sm.amount_delta_15s, 0) AS amount_delta_15s,
        1 AS appearance_count,
        0 AS window_score,
        COALESCE(ssr.primary_anchor_name, '') AS primary_anchor,
        COALESCE(ssr.primary_anchor_type, '') AS anchor_type,
        COALESCE(ssr.anchor_member_count, 0) AS anchor_member_count,
        COALESCE(ssr.role_label, '') AS role_label,
        COALESCE(ssr.leader_code, '') AS leader_code,
        COALESCE(ssr.leader_name, '') AS leader_name,
        COALESCE(ssr.core_code, '') AS core_code,
        COALESCE(ssr.core_name, '') AS core_name,
        COALESCE(ssr.role_reason, '') AS role_reason,
        COALESCE(ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', '') AS stock_reason,
        COALESCE(ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', '') AS anchor_reason,
        COALESCE(aes.evidence_hash, '') AS evidence_hash,
        COALESCE(REPLACE(REPLACE(aes.final_view, CHAR(13), ' '), CHAR(10), ' '), '') AS final_view,
        COALESCE(REPLACE(REPLACE(NULLIF(aes.move_reason, ''), CHAR(13), ' '), CHAR(10), ' '), REPLACE(REPLACE(aes.move_explanation, CHAR(13), ' '), CHAR(10), ' '), '') AS async_move_explanation,
        COALESCE(aes.explanation_strength, 'none') AS async_explanation_strength,
        COALESCE(aes.anchor_match, 'weak') AS anchor_match,
        COALESCE(aes.quality_label, '') AS quality_label,
        COALESCE(aes.evidence_strength, 'pending') AS evidence_strength,
        COALESCE(aes.timeliness_label, 'unknown') AS timeliness_label,
        COALESCE(REPLACE(REPLACE(aes.impact_summary_text, CHAR(13), ' '), CHAR(10), ' '), '') AS impact_summary_text,
        COALESCE(aes.core_support, JSON_ARRAY()) AS core_support,
        COALESCE(aes.counterpoints, JSON_ARRAY()) AS counterpoints,
        COALESCE(aes.hard_catalysts, JSON_ARRAY()) AS hard_catalysts,
        COALESCE(REPLACE(REPLACE(scp.company_highlights, CHAR(13), ' '), CHAR(10), ' '), '') AS company_highlights
      FROM scan_scope sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      {scan_pool_join}
      LEFT JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id AND ssr.code=sm.code
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={sql_string(trade_date)} AND aes.code=sm.code
      LEFT JOIN stock_company_profiles scp ON scp.code=sm.code
      WHERE sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
        {scan_rank_filter}
        {scan_code_filter}

      UNION ALL

      SELECT
        'stable' AS event_type,
        rw.window_id AS event_id,
        rw.ended_at AS event_time,
        wm.code,
        wm.name AS stock_name,
        wm.rank_no,
        COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS pct_change,
        COALESCE(wm.max_speed, 0) AS speed,
        COALESCE(wm.amount, 0) AS amount,
        COALESCE(wm.max_amount_delta_15s, 0) AS amount_delta_15s,
        COALESCE(wm.appearance_count, 0) AS appearance_count,
        COALESCE(wm.window_score, 0) AS window_score,
        COALESCE(wsr.sector_key, ssr.primary_anchor_name, '') AS primary_anchor,
        COALESCE(wsr.sector_type, ssr.primary_anchor_type, '') AS anchor_type,
        COALESCE(wsr.sector_stock_count, ssr.anchor_member_count, 0) AS anchor_member_count,
        COALESCE(wsr.role_label, ssr.role_label, '') AS role_label,
        COALESCE(wss.leader_code, ssr.leader_code, '') AS leader_code,
        COALESCE(wss.leader_name, ssr.leader_name, '') AS leader_name,
        COALESCE(wss.core_code, ssr.core_code, '') AS core_code,
        COALESCE(wss.core_name, ssr.core_name, '') AS core_name,
        COALESCE(wsr.role_reason, '') AS role_reason,
        COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason', ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', '') AS stock_reason,
        COALESCE(wsr.raw_json->>'$.raw_json.anchor_reason', wsr.raw_json->>'$.anchor_reason', ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', '') AS anchor_reason,
        COALESCE(aes.evidence_hash, '') AS evidence_hash,
        COALESCE(REPLACE(REPLACE(aes.final_view, CHAR(13), ' '), CHAR(10), ' '), '') AS final_view,
        COALESCE(REPLACE(REPLACE(NULLIF(aes.move_reason, ''), CHAR(13), ' '), CHAR(10), ' '), REPLACE(REPLACE(aes.move_explanation, CHAR(13), ' '), CHAR(10), ' '), '') AS async_move_explanation,
        COALESCE(aes.explanation_strength, 'none') AS async_explanation_strength,
        COALESCE(aes.anchor_match, 'weak') AS anchor_match,
        COALESCE(aes.quality_label, '') AS quality_label,
        COALESCE(aes.evidence_strength, 'pending') AS evidence_strength,
        COALESCE(aes.timeliness_label, 'unknown') AS timeliness_label,
        COALESCE(REPLACE(REPLACE(aes.impact_summary_text, CHAR(13), ' '), CHAR(10), ' '), '') AS impact_summary_text,
        COALESCE(aes.core_support, JSON_ARRAY()) AS core_support,
        COALESCE(aes.counterpoints, JSON_ARRAY()) AS counterpoints,
        COALESCE(aes.hard_catalysts, JSON_ARRAY()) AS hard_catalysts,
        COALESCE(REPLACE(REPLACE(scp.company_highlights, CHAR(13), ' '), CHAR(10), ' '), '') AS company_highlights
      FROM window_scope rw
      JOIN window_movers wm ON wm.window_id=rw.id
      {window_pool_join}
      LEFT JOIN window_stock_roles wsr ON wsr.window_id=rw.id AND wsr.code=wm.code
      LEFT JOIN window_sector_stats wss ON wss.window_id=rw.id AND wss.sector_key=wsr.sector_key
      LEFT JOIN scan_stock_roles ssr ON ssr.id = (
        SELECT ssr2.id
        FROM scan_stock_roles ssr2
        JOIN scan_runs sr2 ON sr2.id=ssr2.scan_run_id
        WHERE ssr2.code=wm.code
          AND sr2.accepted=1
          AND sr2.scanned_at BETWEEN DATE_SUB(rw.ended_at, INTERVAL 10 MINUTE) AND rw.ended_at
        ORDER BY sr2.scanned_at DESC
        LIMIT 1
      )
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={sql_string(trade_date)} AND aes.code=wm.code
      LEFT JOIN stock_company_profiles scp ON scp.code=wm.code
      WHERE wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        {window_rank_filter}
        {window_code_filter}
    ) x
    ORDER BY event_time DESC, event_type DESC, rank_no ASC
    LIMIT {int(limit)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    keys = [
        "event_type", "event_id", "event_time", "code", "stock_name", "rank_no", "pct_change", "speed",
        "amount", "amount_delta_15s", "appearance_count", "window_score", "primary_anchor", "anchor_type",
        "anchor_member_count", "role_label", "leader_code", "leader_name", "core_code", "core_name",
        "role_reason", "stock_reason", "anchor_reason", "evidence_hash", "final_view", "async_move_explanation",
        "async_explanation_strength", "anchor_match", "quality_label", "evidence_strength", "timeliness_label",
        "impact_summary_text", "core_support", "counterpoints", "hard_catalysts", "company_highlights",
    ]
    events = [dict(zip(keys, row)) for row in rows if len(row) >= len(keys)]
    if any(item.get("event_type") == "stable" for item in events):
        return events

    # Some MySQL builds can optimize the UNION query above in a way that drops
    # the second branch when both scoped CTEs use LIMIT. Keep the stable branch
    # explicit so the judgement layer always covers the 5-minute windows.
    stable_scope = "ORDER BY ended_at DESC LIMIT 1" if latest_only else "ORDER BY ended_at DESC"
    stable_sql = f"""
    WITH
    {pool_cte}
    window_scope AS (
      SELECT id, window_id, started_at, ended_at
      FROM windows
      WHERE DATE(ended_at)={sql_string(trade_date)}
        AND status='done'
        AND aggregate_count > 0
      {stable_scope}
    )
    SELECT
      'stable' AS event_type,
      rw.window_id AS event_id,
      rw.ended_at AS event_time,
      wm.code,
      wm.name AS stock_name,
      wm.rank_no,
      COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS pct_change,
      COALESCE(wm.max_speed, 0) AS speed,
      COALESCE(wm.amount, 0) AS amount,
      COALESCE(wm.max_amount_delta_15s, 0) AS amount_delta_15s,
      COALESCE(wm.appearance_count, 0) AS appearance_count,
      COALESCE(wm.window_score, 0) AS window_score,
      COALESCE(wsr.sector_key, ssr.primary_anchor_name, '') AS primary_anchor,
      COALESCE(wsr.sector_type, ssr.primary_anchor_type, '') AS anchor_type,
      COALESCE(wsr.sector_stock_count, ssr.anchor_member_count, 0) AS anchor_member_count,
      COALESCE(wsr.role_label, ssr.role_label, '') AS role_label,
      COALESCE(wss.leader_code, ssr.leader_code, '') AS leader_code,
      COALESCE(wss.leader_name, ssr.leader_name, '') AS leader_name,
      COALESCE(wss.core_code, ssr.core_code, '') AS core_code,
      COALESCE(wss.core_name, ssr.core_name, '') AS core_name,
      COALESCE(wsr.role_reason, '') AS role_reason,
      COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason', ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', '') AS stock_reason,
      COALESCE(wsr.raw_json->>'$.raw_json.anchor_reason', wsr.raw_json->>'$.anchor_reason', ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', '') AS anchor_reason,
      COALESCE(aes.evidence_hash, '') AS evidence_hash,
      COALESCE(REPLACE(REPLACE(aes.final_view, CHAR(13), ' '), CHAR(10), ' '), '') AS final_view,
      COALESCE(REPLACE(REPLACE(NULLIF(aes.move_reason, ''), CHAR(13), ' '), CHAR(10), ' '), REPLACE(REPLACE(aes.move_explanation, CHAR(13), ' '), CHAR(10), ' '), '') AS async_move_explanation,
      COALESCE(aes.explanation_strength, 'none') AS async_explanation_strength,
      COALESCE(aes.anchor_match, 'weak') AS anchor_match,
      COALESCE(aes.quality_label, '') AS quality_label,
      COALESCE(aes.evidence_strength, 'pending') AS evidence_strength,
      COALESCE(aes.timeliness_label, 'unknown') AS timeliness_label,
      COALESCE(REPLACE(REPLACE(aes.impact_summary_text, CHAR(13), ' '), CHAR(10), ' '), '') AS impact_summary_text,
      COALESCE(aes.core_support, JSON_ARRAY()) AS core_support,
      COALESCE(aes.counterpoints, JSON_ARRAY()) AS counterpoints,
      COALESCE(aes.hard_catalysts, JSON_ARRAY()) AS hard_catalysts,
      COALESCE(REPLACE(REPLACE(scp.company_highlights, CHAR(13), ' '), CHAR(10), ' '), '') AS company_highlights
    FROM window_scope rw
    JOIN window_movers wm ON wm.window_id=rw.id
    {window_pool_join}
    LEFT JOIN window_stock_roles wsr ON wsr.window_id=rw.id AND wsr.code=wm.code
    LEFT JOIN window_sector_stats wss ON wss.window_id=rw.id AND wss.sector_key=wsr.sector_key
    LEFT JOIN scan_stock_roles ssr ON ssr.id = (
      SELECT ssr2.id
      FROM scan_stock_roles ssr2
      JOIN scan_runs sr2 ON sr2.id=ssr2.scan_run_id
      WHERE ssr2.code=wm.code
        AND sr2.accepted=1
        AND sr2.scanned_at BETWEEN DATE_SUB(rw.ended_at, INTERVAL 10 MINUTE) AND rw.ended_at
      ORDER BY sr2.scanned_at DESC
      LIMIT 1
    )
    LEFT JOIN async_evidence_summaries aes ON aes.trade_date={sql_string(trade_date)} AND aes.code=wm.code
    LEFT JOIN stock_company_profiles scp ON scp.code=wm.code
    WHERE wm.name NOT LIKE '%ST%'
      AND wm.name NOT LIKE '%退市%'
      {window_rank_filter}
      {window_code_filter}
    ORDER BY event_time DESC, rank_no ASC
    LIMIT {int(limit)};
    """
    stable_rows = mysql_rows(run_mysql(config, stable_sql, batch=True, raw=True))
    stable_events = [dict(zip(keys, row)) for row in stable_rows if len(row) >= len(keys)]
    return (events + stable_events)[:limit]


def load_events(
    config: MySqlConfig,
    trade_date: str,
    scan_top: int,
    window_top: int,
    latest_only: bool,
    limit: int,
    codes: list[str] | None = None,
    research_pool_only: bool = False,
) -> list[dict[str, Any]]:
    ensure_event_engine_tables(config)
    codes = codes or []
    event_code_filter = sql_code_filter("e", codes)
    pool_cte = f"{research_pool_cte(trade_date)}," if research_pool_only else ""
    pool_join = "JOIN research_pool rp ON rp.code=e.code" if research_pool_only else ""
    latest_cte = ""
    latest_join = ""
    if latest_only:
        latest_cte = f"""
    latest_event_time AS (
      SELECT event_type, MAX(event_time) AS event_time
      FROM stock_move_events
      WHERE trade_date={sql_string(trade_date)}
      GROUP BY event_type
    ),
"""
        latest_join = "JOIN latest_event_time let ON let.event_type=e.event_type AND let.event_time=e.event_time"
    event_rank_filter = ""
    if not research_pool_only:
        event_rank_filter = f"""
        AND (
          (e.event_type='realtime_scan' AND e.sort_rank <= {int(scan_top)})
          OR (e.event_type='stable_window' AND e.sort_rank <= {int(window_top)})
        )
        """
    sql = f"""
    WITH
    {pool_cte}
    {latest_cte}
    event_rows AS (
      SELECT
        CASE WHEN e.event_type='stable_window' THEN 'stable' ELSE 'realtime' END AS event_type,
        CASE
          WHEN e.source_table IN ('scan_movers','window_movers') THEN SUBSTRING_INDEX(e.source_key, ':', 1)
          ELSE e.event_id
        END AS event_id,
        e.event_id AS source_event_id,
        e.source_table,
        e.source_key,
        e.event_time,
        e.code,
        e.stock_name,
        e.sort_rank AS rank_no,
        COALESCE(e.trigger_pct, 0) AS pct_change,
        COALESCE(e.speed_pct, 0) AS speed,
        COALESCE(e.amount, 0) AS amount,
        COALESCE(
          e.payload->>'$.amount_delta_15s',
          e.payload->>'$.max_amount_delta_15s',
          0
        ) AS amount_delta_15s,
        CASE
          WHEN e.event_type='stable_window' THEN COALESCE(e.payload->>'$.appearance_count', 0)
          ELSE 1
        END AS appearance_count,
        CASE
          WHEN e.event_type='stable_window' THEN COALESCE(e.event_strength, 0)
          ELSE 0
        END AS window_score,
        COALESCE(e.anchor_name, '') AS primary_anchor,
        COALESCE(e.anchor_scope_type, '') AS anchor_type,
        COALESCE(
          e.payload->>'$.role_raw.anchor_member_count',
          e.payload->>'$.role_raw.raw_json.anchor_member_count',
          e.payload->>'$.role_raw.sector_stock_count',
          e.payload->>'$.role_raw.raw_json.sector_stock_count',
          0
        ) AS anchor_member_count,
        COALESCE(e.role_label, '') AS role_label,
        COALESCE(ars.leader_code, '') AS leader_code,
        COALESCE(ars.leader_name, '') AS leader_name,
        COALESCE(ars.core_code, '') AS core_code,
        COALESCE(ars.core_name, '') AS core_name,
        COALESCE(e.payload->>'$.role_reason', '') AS role_reason,
        COALESCE(
          e.payload->>'$.role_raw.stock_reason',
          e.payload->>'$.role_raw.raw_json.stock_reason',
          ''
        ) AS stock_reason,
        COALESCE(
          e.payload->>'$.role_raw.anchor_reason',
          e.payload->>'$.role_raw.raw_json.anchor_reason',
          ''
        ) AS anchor_reason,
        COALESCE(aes.evidence_hash, '') AS evidence_hash,
        COALESCE(REPLACE(REPLACE(aes.final_view, CHAR(13), ' '), CHAR(10), ' '), '') AS final_view,
        COALESCE(REPLACE(REPLACE(NULLIF(aes.move_reason, ''), CHAR(13), ' '), CHAR(10), ' '), REPLACE(REPLACE(aes.move_explanation, CHAR(13), ' '), CHAR(10), ' '), '') AS async_move_explanation,
        COALESCE(aes.explanation_strength, 'none') AS async_explanation_strength,
        COALESCE(aes.anchor_match, 'weak') AS anchor_match,
        COALESCE(aes.quality_label, '') AS quality_label,
        COALESCE(aes.evidence_strength, 'pending') AS evidence_strength,
        COALESCE(aes.timeliness_label, 'unknown') AS timeliness_label,
        COALESCE(REPLACE(REPLACE(aes.impact_summary_text, CHAR(13), ' '), CHAR(10), ' '), '') AS impact_summary_text,
        COALESCE(aes.core_support, JSON_ARRAY()) AS core_support,
        COALESCE(aes.counterpoints, JSON_ARRAY()) AS counterpoints,
        COALESCE(aes.hard_catalysts, JSON_ARRAY()) AS hard_catalysts,
        COALESCE(REPLACE(REPLACE(scp.company_highlights, CHAR(13), ' '), CHAR(10), ' '), '') AS company_highlights
      FROM stock_move_events e
      {latest_join}
      {pool_join}
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={sql_string(trade_date)} AND aes.code=e.code
      LEFT JOIN stock_company_profiles scp ON scp.code=e.code
      LEFT JOIN anchor_realtime_role_snapshots ars ON ars.id = (
        SELECT ars2.id
        FROM anchor_realtime_role_snapshots ars2
        WHERE DATE(ars2.captured_at)={sql_string(trade_date)}
          AND ars2.anchor_name=e.anchor_name
          AND ars2.source='research_pool_theme_members'
          AND ars2.captured_at <= e.event_time
        ORDER BY ars2.captured_at DESC, ars2.created_at DESC
        LIMIT 1
      )
      WHERE e.trade_date={sql_string(trade_date)}
        AND e.event_type IN ('realtime_scan','stable_window')
        AND e.stock_name NOT LIKE '%ST%'
        AND e.stock_name NOT LIKE '%退市%'
        {event_rank_filter}
        {event_code_filter}
    )
    SELECT
      event_type, event_id, event_time, code, stock_name, rank_no, pct_change, speed,
      amount, amount_delta_15s, appearance_count, window_score, primary_anchor, anchor_type,
      anchor_member_count, role_label, leader_code, leader_name, core_code, core_name,
      role_reason, stock_reason, anchor_reason, evidence_hash, final_view, async_move_explanation,
      async_explanation_strength, anchor_match, quality_label, evidence_strength, timeliness_label,
      impact_summary_text, core_support, counterpoints, hard_catalysts, company_highlights
    FROM event_rows
    ORDER BY event_time DESC, event_type DESC, rank_no ASC
    LIMIT {int(limit)};
    """
    keys = [
        "event_type", "event_id", "event_time", "code", "stock_name", "rank_no", "pct_change", "speed",
        "amount", "amount_delta_15s", "appearance_count", "window_score", "primary_anchor", "anchor_type",
        "anchor_member_count", "role_label", "leader_code", "leader_name", "core_code", "core_name",
        "role_reason", "stock_reason", "anchor_reason", "evidence_hash", "final_view", "async_move_explanation",
        "async_explanation_strength", "anchor_match", "quality_label", "evidence_strength", "timeliness_label",
        "impact_summary_text", "core_support", "counterpoints", "hard_catalysts", "company_highlights",
    ]
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    events = [dict(zip(keys, row)) for row in rows if len(row) >= len(keys)]
    if events:
        return events
    return load_raw_events(config, trade_date, scan_top, window_top, latest_only, limit, codes, research_pool_only)


def fetch_anchor_context(config: MySqlConfig, trade_date: str, event_time: str, anchor: str, code: str) -> dict[str, Any]:
    anchor = clean_anchor(anchor)
    if not anchor:
        return {}
    anchor_terms = [anchor]
    for term in re.split(r"[/、，\s]+", anchor):
        term = term.strip()
        if len(term) >= 2 and term not in anchor_terms:
            anchor_terms.append(term)
    term_sql = ",".join(sql_string(term) for term in anchor_terms)
    sql = f"""
    WITH
    anchor_members AS (
      SELECT code, MAX(stock_name) AS stock_name, MAX(confidence) AS confidence
      FROM (
        SELECT code, stock_name, confidence
        FROM (
          SELECT
            code,
            stock_name,
            GREATEST(55, 90 - LEAST(GREATEST(COALESCE(fit_rank, 1), 1), 20)) AS confidence,
            concept_name AS anchor_name
          FROM ths_stock_concept_explanations
          WHERE COALESCE(concept_name, '') <> ''
            AND COALESCE(reason_explain, '') <> ''
        ) concept_members
        WHERE anchor_name IN ({term_sql})
        UNION ALL
        SELECT code, stock_name, GREATEST(55, match_score) AS confidence
        FROM research_pool_theme_members
        WHERE trade_date={sql_string(trade_date)}
          AND theme_name IN ({term_sql})
      ) m
      GROUP BY code
    ),
    research_pool_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM research_pool_items
      WHERE trade_date <= {sql_string(trade_date)}
        AND rule='recent_limit_up_or_5d_gain_top'
        AND limit_up_days=5
        AND gain_period_days=5
        AND gain_top=30
    ),
    research_pool_strength AS (
      SELECT
        rp.code,
        MAX(rp.pct_5d) AS pct_5d,
        MIN(rp.rank_5d) AS rank_5d
      FROM research_pool_items rp
      JOIN research_pool_day d ON d.trade_date=rp.trade_date
      JOIN anchor_members am ON am.code=rp.code
      WHERE rp.rule='recent_limit_up_or_5d_gain_top'
        AND rp.limit_up_days=5
        AND rp.gain_period_days=5
        AND rp.gain_top=30
      GROUP BY rp.code
    ),
    day_scope AS (
      SELECT trade_day, ROW_NUMBER() OVER (ORDER BY trade_day DESC) AS rn
      FROM (
        SELECT DISTINCT DATE(scanned_at) AS trade_day
        FROM scan_runs
        WHERE DATE(scanned_at) <= {sql_string(trade_date)}
          AND accepted=1
      ) d
      ORDER BY trade_day DESC
      LIMIT 10
    ),
    agg AS (
      SELECT
        am.code,
        am.stock_name,
        0 AS pct_3d,
        COALESCE(MAX(rps.pct_5d), 0) AS pct_5d,
        0 AS pct_10d,
        NULL AS market_rank_3d,
        MIN(rps.rank_5d) AS market_rank_5d,
        NULL AS market_rank_10d,
        IF(MIN(rps.rank_5d) IS NOT NULL, 'research_pool', 'not_in_research_pool') AS period_rank_source
      FROM anchor_members am
      LEFT JOIN research_pool_strength rps ON rps.code=am.code
      GROUP BY am.code, am.stock_name
    ),
    latest_snapshot AS (
      SELECT snapshot_run_id
      FROM anchor_realtime_role_members
      WHERE anchor_name={sql_string(anchor)}
        AND raw_json->>'$.source'='research_pool_theme_members'
        AND DATE(captured_at)={sql_string(trade_date)}
        AND captured_at <= {sql_string(event_time)}
      ORDER BY captured_at DESC
      LIMIT 1
    ),
    today_roles AS (
      SELECT m.*
      FROM anchor_realtime_role_members m
      JOIN latest_snapshot s ON s.snapshot_run_id=m.snapshot_run_id
      WHERE m.anchor_name={sql_string(anchor)}
    ),
    ranked AS (
      SELECT
        a.*,
        RANK() OVER (ORDER BY a.pct_3d DESC) AS rank_3d,
        RANK() OVER (ORDER BY a.pct_5d DESC) AS rank_5d,
        RANK() OVER (ORDER BY a.pct_10d DESC) AS rank_10d
      FROM agg a
    ),
    today_ranked AS (
      SELECT
        tr.code,
        tr.stock_name,
        COALESCE(tr.pct_change, 0) AS today_pct,
        COALESCE(tr.speed, 0) AS today_speed,
        COALESCE(tr.amount, 0) AS today_amount,
        tr.match_level,
        tr.role_label,
        tr.rank_leader,
        tr.rank_core,
        RANK() OVER (ORDER BY COALESCE(tr.amount, 0) DESC) AS today_amount_rank,
        RANK() OVER (ORDER BY COALESCE(tr.speed, 0) DESC) AS today_speed_rank
      FROM today_roles tr
    ),
    risk AS (
      SELECT
        SUM(IF(match_level IN ('strong','medium') AND COALESCE(pct_change,0) <= -9.5, 1, 0)) AS limit_down_count,
        SUM(IF(match_level IN ('strong','medium') AND COALESCE(pct_change,0) <= -7, 1, 0)) AS crash_count,
        SUM(IF((rank_leader <= 5 OR rank_core <= 5) AND COALESCE(pct_change,0) <= -5, 1, 0)) AS core_weak_count,
        MIN(COALESCE(pct_change, 0)) AS min_pct,
        AVG(COALESCE(pct_change, 0)) AS avg_pct
      FROM today_roles
    )
    SELECT
      r.code,
      r.stock_name,
      r.pct_3d,
      r.pct_5d,
      r.pct_10d,
      r.rank_3d,
      r.rank_5d,
      r.rank_10d,
      COALESCE(r.market_rank_3d, 0),
      COALESCE(r.market_rank_5d, 0),
      COALESCE(r.market_rank_10d, 0),
      COALESCE(r.period_rank_source, ''),
      COALESCE(t.today_pct, 0),
      COALESCE(t.today_speed, 0),
      COALESCE(t.today_amount, 0),
      COALESCE(t.today_amount_rank, 0),
      COALESCE(t.today_speed_rank, 0),
      COALESCE(t.role_label, ''),
      COALESCE(t.match_level, ''),
      COALESCE((SELECT COUNT(*) FROM anchor_members), 0),
      COALESCE((SELECT limit_down_count FROM risk), 0),
      COALESCE((SELECT crash_count FROM risk), 0),
      COALESCE((SELECT core_weak_count FROM risk), 0),
      COALESCE((SELECT min_pct FROM risk), 0),
      COALESCE((SELECT avg_pct FROM risk), 0)
    FROM ranked r
    LEFT JOIN today_ranked t ON t.code=r.code
    WHERE r.code={sql_string(code)}
    LIMIT 1;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    if not rows or len(rows[0]) < 21:
        return {}
    row = rows[0]
    keys = [
        "code", "stock_name", "pct_3d", "pct_5d", "pct_10d", "rank_3d", "rank_5d", "rank_10d",
        "market_rank_3d", "market_rank_5d", "market_rank_10d", "period_rank_source",
        "today_pct", "today_speed", "today_amount", "today_amount_rank", "today_speed_rank",
        "today_role_label", "match_level", "anchor_member_total", "limit_down_count", "crash_count",
        "core_weak_count", "anchor_min_pct", "anchor_avg_pct",
    ]
    out = dict(zip(keys, row))
    for key in [
        "pct_3d", "pct_5d", "pct_10d", "today_pct", "today_speed", "today_amount", "anchor_min_pct", "anchor_avg_pct",
    ]:
        out[key] = as_float(out.get(key))
    for key in [
        "rank_3d", "rank_5d", "rank_10d", "market_rank_3d", "market_rank_5d", "market_rank_10d",
        "today_amount_rank", "today_speed_rank", "anchor_member_total",
        "limit_down_count", "crash_count", "core_weak_count",
    ]:
        out[key] = as_int(out.get(key))
    return out


def load_activity_index(
    config: MySqlConfig,
    trade_date: str,
    codes: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    stock_code_filter = f"AND code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ""
    scan_code_filter = f"AND sm.code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ""
    scan_rank_filter = "" if clean_codes else "AND sm.rank_speed <= 20"
    strong_rows = mysql_rows(run_mysql(
        config,
        f"""
        SELECT DISTINCT code
        FROM research_pool_items
        WHERE trade_date <= {sql_string(trade_date)}
          AND trade_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL 7 DAY)
          AND rule='recent_limit_up_or_5d_gain_top'
          {stock_code_filter}
        ;
        """,
        batch=True,
        raw=True,
    ))
    strong_codes = {str(row[0] or "").strip() for row in strong_rows if row and str(row[0] or "").strip()}
    sql = f"""
    WITH stock_anchors AS (
      SELECT code, anchor, MAX(confidence) AS confidence, SUBSTRING_INDEX(GROUP_CONCAT(source ORDER BY confidence DESC), ',', 1) AS source
      FROM (
        SELECT
          ssr.code,
          ssr.primary_anchor_name AS anchor,
          98 AS confidence,
          'scan_primary_anchor' AS source
        FROM scan_runs sr
        JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id
        WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
          AND sr.accepted=1
          AND COALESCE(ssr.primary_anchor_name, '') <> ''
        UNION ALL
        SELECT
          m.code AS code,
          m.theme_name AS anchor,
          GREATEST(55, m.match_score) AS confidence,
          'research_pool_theme_member' AS source
        FROM research_pool_theme_members m
        WHERE m.trade_date={sql_string(trade_date)}
          AND COALESCE(m.theme_name, '') <> ''
        UNION ALL
        SELECT
          code,
          concept_name AS anchor,
          GREATEST(55, 90 - LEAST(GREATEST(COALESCE(fit_rank, 1), 1), 20)) AS confidence,
          'ths_stock_concept' AS source
        FROM ths_stock_concept_explanations
        WHERE COALESCE(concept_name, '') <> ''
          AND COALESCE(reason_explain, '') <> ''
      ) raw
      WHERE COALESCE(anchor, '') <> ''
        AND anchor NOT IN ('未锚定', '异动')
      GROUP BY code, anchor
    ),
    raw_hits AS (
      SELECT
        sa.anchor,
        ssr.code,
        ssr.name AS stock_name,
        sr.scanned_at,
        sm.rank_speed,
        COALESCE(sm.speed, 0) AS speed,
        COALESCE(sm.amount_delta_15s, 0) AS amount_delta_15s,
        sa.confidence,
        sa.source,
        ROW_NUMBER() OVER (
          PARTITION BY sa.anchor, ssr.code
          ORDER BY sr.scanned_at ASC, sm.rank_speed ASC
        ) AS rn
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id AND ssr.code=sm.code
      JOIN stock_anchors sa ON sa.code=sm.code
      WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
        AND sr.accepted=1
        AND sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
        {scan_rank_filter}
        {scan_code_filter}
        AND COALESCE(sm.speed, 0) >= 1.5
    )
    SELECT
      anchor,
      code,
      stock_name,
      DATE_FORMAT(scanned_at, '%Y-%m-%d %H:%i:%s') AS first_at,
      rank_speed,
      speed,
      amount_delta_15s,
      confidence,
      source
    FROM raw_hits
    WHERE rn=1
    ORDER BY anchor ASC, scanned_at ASC, rank_speed ASC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    all_sql = f"""
    WITH stock_anchors AS (
      SELECT code, anchor, MAX(confidence) AS confidence, SUBSTRING_INDEX(GROUP_CONCAT(source ORDER BY confidence DESC), ',', 1) AS source
      FROM (
        SELECT
          ssr.code,
          ssr.primary_anchor_name AS anchor,
          98 AS confidence,
          'scan_primary_anchor' AS source
        FROM scan_runs sr
        JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id
        WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
          AND sr.accepted=1
          AND COALESCE(ssr.primary_anchor_name, '') <> ''
        UNION ALL
        SELECT
          m.code AS code,
          m.theme_name AS anchor,
          GREATEST(55, m.match_score) AS confidence,
          'research_pool_theme_member' AS source
        FROM research_pool_theme_members m
        WHERE m.trade_date={sql_string(trade_date)}
          AND COALESCE(m.theme_name, '') <> ''
        UNION ALL
        SELECT
          code,
          concept_name AS anchor,
          GREATEST(55, 90 - LEAST(GREATEST(COALESCE(fit_rank, 1), 1), 20)) AS confidence,
          'ths_stock_concept' AS source
        FROM ths_stock_concept_explanations
        WHERE COALESCE(concept_name, '') <> ''
          AND COALESCE(reason_explain, '') <> ''
      ) raw
      WHERE COALESCE(anchor, '') <> ''
        AND anchor NOT IN ('未锚定', '异动')
      GROUP BY code, anchor
    )
    SELECT
      sa.anchor,
      ssr.code,
      ssr.name AS stock_name,
      DATE_FORMAT(sr.scanned_at, '%Y-%m-%d %H:%i:%s') AS scanned_at,
      sm.rank_speed,
      COALESCE(sm.speed, 0) AS speed,
      COALESCE(sm.amount_delta_15s, 0) AS amount_delta_15s,
      sa.confidence,
      sa.source
    FROM scan_runs sr
    JOIN scan_movers sm ON sm.scan_run_id=sr.id
    JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id AND ssr.code=sm.code
    JOIN stock_anchors sa ON sa.code=sm.code
    WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
      AND sr.accepted=1
      AND sm.name NOT LIKE '%ST%'
      AND sm.name NOT LIKE '%退市%'
      {scan_rank_filter}
      {scan_code_filter}
      AND COALESCE(sm.speed, 0) >= 1.5
    ORDER BY anchor ASC, sr.scanned_at ASC, sm.rank_speed ASC;
    """
    all_rows = mysql_rows(run_mysql(config, all_sql, batch=True, raw=True))
    return build_activity_index(rows, all_rows, strong_codes)


def load_headline_theme_index(
    config: MySqlConfig,
    trade_date: str,
    codes: list[str] | None = None,
) -> dict[str, dict[str, dict[str, Any]]]:
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    code_filter = f"AND code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ""
    rows = mysql_rows(run_mysql(
        config,
        f"""
        SELECT
          code,
          theme_name,
          MIN(theme_rank) AS theme_rank,
          MAX(match_score) AS match_score,
          SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(concept_name, '') ORDER BY match_score DESC, fit_rank ASC SEPARATOR '\\n'), '\\n', 1) AS concept_name,
          SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(reason_explain, '') ORDER BY match_score DESC, fit_rank ASC SEPARATOR '\\n'), '\\n', 1) AS reason_explain
        FROM research_pool_theme_members
        WHERE trade_date={sql_string(trade_date)}
          AND is_headline_theme=1
          AND COALESCE(theme_name, '') <> ''
          {code_filter}
        GROUP BY code, theme_name
        ORDER BY code ASC, theme_rank ASC, match_score DESC, theme_name ASC;
        """,
        batch=True,
        raw=True,
    ))
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        code = str(row[0] or "").strip()
        theme = clean_anchor(row[1])
        if not code or not theme:
            continue
        out.setdefault(code, {})[theme] = {
            "theme_name": theme,
            "theme_rank": as_int(row[2], 999),
            "match_score": as_float(row[3]),
            "concept_name": str(row[4] or "").strip(),
            "reason_explain": str(row[5] or "").strip(),
        }
    return out


def activity_candidate_anchors(
    activity_index: dict[str, dict[str, Any]],
    event_time: str,
    primary_anchor: str,
    code: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    primary = clean_anchor(primary_anchor)
    code_text = str(code or "").strip()
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(anchor: str, source: str, confidence: float) -> None:
        cleaned = clean_anchor(anchor)
        if not cleaned or cleaned in seen:
            return
        context = build_activity_context(activity_index, event_time, cleaned, code_text)
        if not context:
            return
        seen.add(cleaned)
        candidates.append(
            {
                "anchor": cleaned,
                "source": source or context.get("anchor_source") or "",
                "confidence": max(as_float(confidence), as_float(context.get("anchor_match_confidence"), 0)),
                "activity_context": context,
            }
        )

    if primary:
        add(primary, "event_primary_anchor", 100)
    matched: list[tuple[str, dict[str, Any]]] = []
    for anchor, bucket in activity_index.items():
        by_code = bucket.get("by_code") if isinstance(bucket, dict) else {}
        hit = by_code.get(code_text) if isinstance(by_code, dict) else None
        if not hit:
            continue
        matched.append((anchor, hit))

    matched.sort(
        key=lambda item: (
            -as_float(item[1].get("anchor_confidence"), 0),
            as_int(item[1].get("wave_trigger_order"), 9999),
            as_int(item[1].get("trigger_order"), 9999),
            str(item[0] or ""),
        )
    )
    for anchor, hit in matched:
        if len(candidates) >= limit:
            break
        add(anchor, str(hit.get("anchor_source") or ""), as_float(hit.get("anchor_confidence"), 0))

    candidates.sort(
        key=lambda item: (
            0 if item["anchor"] == primary else 1,
            -as_float(item.get("confidence")),
            as_int((item.get("activity_context") or {}).get("wave_trigger_order"), 9999),
            as_int((item.get("activity_context") or {}).get("day_trigger_order"), 9999),
        )
    )
    return candidates[:limit]


def activity_selection_score(
    *,
    candidate: dict[str, Any],
    scored: dict[str, Any],
    is_primary: bool,
) -> float:
    detail = scored.get("score_detail") or {}
    context = candidate.get("activity_context") or {}
    confidence = min(12.0, as_float(candidate.get("confidence"), 0) / 8.0)
    wave_order = as_int(context.get("wave_trigger_order"), 9999)
    day_order = as_int(context.get("day_trigger_order"), 9999)
    member_total = as_int((detail.get("anchor_context") or {}).get("anchor_member_total"))
    selection = confidence
    selection += as_float(detail.get("initiative")) * 0.8
    selection += as_float(detail.get("influence")) * 0.7
    selection += as_float(detail.get("anchor_leadership")) * 0.25
    if is_primary:
        selection += 5
    if wave_order == 1:
        selection += 6
    elif wave_order <= 3:
        selection += 3
    if day_order == 1:
        selection += 2
    if member_total and member_total < 3:
        selection -= 8
    if as_int(context.get("new_after_10m")) == 0 and member_total < 3:
        selection -= 4
    return round(selection, 2)


def initiative_anchor_selection_score(item: dict[str, Any]) -> float:
    context = item.get("_activity_context") or {}
    confidence = min(10.0, as_float(item.get("confidence"), 0) / 10.0)
    wave_order = as_int(context.get("wave_trigger_order"), 9999)
    day_order = as_int(context.get("day_trigger_order"), 9999)
    score = confidence + as_float(item.get("initiative")) * 1.0
    if wave_order == 1:
        score += 8
    elif wave_order <= 3:
        score += 5
    elif wave_order <= 5:
        score += 2
    if day_order == 1:
        score += 3
    return round(score, 2)


def influence_anchor_selection_score(item: dict[str, Any]) -> float:
    context = item.get("_activity_context") or {}
    confidence = min(8.0, as_float(item.get("confidence"), 0) / 12.0)
    n3 = as_int(context.get("new_after_3m"))
    n5 = as_int(context.get("new_after_5m"))
    n10 = as_int(context.get("new_after_10m"))
    label = str(item.get("influence_label") or "")
    score = confidence + as_float(item.get("influence")) * 0.8
    if label == "疑似带动强":
        score += 10
    elif label == "疑似带动中":
        score += 7
    elif label == "同锚扩散":
        score += 5
    elif label == "弱":
        score -= 4
    if n3 >= 3:
        score += 4
    elif n3 >= 1:
        score += 2
    if n5 >= 5:
        score += 3
    if n10 >= 8:
        score += 4
    elif n10 >= 4:
        score += 2
    if n10 == 0 and label == "弱":
        score -= 6
    return round(score, 2)


def multi_theme_influence_items(
    evaluated: list[dict[str, Any]],
    headline_theme_meta: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not headline_theme_meta:
        return []
    by_anchor = {clean_anchor(item.get("anchor")): item for item in evaluated if clean_anchor(item.get("anchor"))}
    items: list[dict[str, Any]] = []
    for theme, meta in sorted(
        headline_theme_meta.items(),
        key=lambda pair: (as_int(pair[1].get("theme_rank"), 999), -as_float(pair[1].get("match_score")), pair[0]),
    ):
        item = by_anchor.get(clean_anchor(theme))
        if not item:
            continue
        scored = item.get("_scored") if isinstance(item.get("_scored"), dict) else {}
        detail = scored.get("score_detail") if isinstance(scored.get("score_detail"), dict) else {}
        context = item.get("_activity_context") if isinstance(item.get("_activity_context"), dict) else {}
        reasons = detail.get("influence_reasons") if isinstance(detail.get("influence_reasons"), list) else []
        label = str(detail.get("influence_label") or item.get("influence_label") or "")
        payload = detail.get("influence_payload") if isinstance(detail.get("influence_payload"), dict) else influence_payload(context, label, reasons)
        items.append(
            {
                "theme_name": theme,
                "theme_rank": as_int(meta.get("theme_rank"), 999),
                "concept_name": meta.get("concept_name") or "",
                "reason_explain": meta.get("reason_explain") or "",
                "match_score": round(as_float(meta.get("match_score")), 2),
                "influence": round(as_float(item.get("influence")), 2),
                "influence_label": label,
                "influence_selection_score": round(as_float(item.get("influence_selection_score")), 2),
                "selected": bool(item.get("influence_selected") or item.get("selected")),
                "day_trigger_order": as_int(item.get("day_trigger_order"), 0),
                "wave_no": as_int(item.get("wave_no"), 1),
                "wave_trigger_order": as_int(item.get("wave_trigger_order"), 0),
                "new_after_3m": as_int(item.get("new_after_3m"), 0),
                "new_after_5m": as_int(item.get("new_after_5m"), 0),
                "new_after_10m": as_int(item.get("new_after_10m"), 0),
                "payload": payload,
            }
        )
    return items[:12]


def score_best_activity_anchor(
    config: MySqlConfig,
    trade_date: str,
    row: dict[str, Any],
    activity_index: dict[str, dict[str, Any]],
    headline_theme_index: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    primary = clean_anchor(row.get("primary_anchor"))
    candidates = activity_candidate_anchors(activity_index, row.get("event_time", ""), primary, row.get("code", ""))
    evaluated: list[dict[str, Any]] = []
    for candidate in candidates:
        anchor = candidate["anchor"]
        candidate_row = dict(row)
        candidate_row["primary_anchor"] = anchor
        anchor_context = fetch_anchor_context(config, trade_date, row.get("event_time", ""), anchor, row.get("code", ""))
        if anchor_context:
            candidate_row["anchor_member_count"] = max(
                as_int(row.get("anchor_member_count")),
                as_int(anchor_context.get("anchor_member_total")),
            )
            candidate_row["role_label"] = row.get("role_label") or anchor_context.get("today_role_label") or ""
        scored = score_event(candidate_row, anchor_context, candidate["activity_context"])
        selection = activity_selection_score(candidate=candidate, scored=scored, is_primary=anchor == primary)
        evaluated.append(
            {
                "anchor": anchor,
                "source": candidate.get("source") or "",
                "confidence": round(as_float(candidate.get("confidence")), 2),
                "selection_score": selection,
                "initiative_selection_score": None,
                "influence_selection_score": None,
                "initiative": (scored.get("score_detail") or {}).get("initiative"),
                "initiative_label": (scored.get("score_detail") or {}).get("initiative_label"),
                "influence": (scored.get("score_detail") or {}).get("influence"),
                "influence_label": (scored.get("score_detail") or {}).get("influence_label"),
                "day_trigger_order": (candidate.get("activity_context") or {}).get("day_trigger_order"),
                "wave_no": (candidate.get("activity_context") or {}).get("wave_no"),
                "wave_trigger_order": (candidate.get("activity_context") or {}).get("wave_trigger_order"),
                "new_after_3m": (candidate.get("activity_context") or {}).get("new_after_3m"),
                "new_after_5m": (candidate.get("activity_context") or {}).get("new_after_5m"),
                "new_after_10m": (candidate.get("activity_context") or {}).get("new_after_10m"),
                "anchor_member_total": (scored.get("score_detail") or {}).get("anchor_context", {}).get("anchor_member_total"),
                "selected": False,
                "_row": candidate_row,
                "_scored": scored,
                "_activity_context": candidate["activity_context"],
            }
        )

    if not evaluated:
        anchor_context = fetch_anchor_context(config, trade_date, row.get("event_time", ""), primary, row.get("code", ""))
        activity_context = build_activity_context(activity_index, row.get("event_time", ""), primary, row.get("code", ""))
        return row, score_event(row, anchor_context, activity_context)

    for item in evaluated:
        item["initiative_selection_score"] = initiative_anchor_selection_score(item)
        item["influence_selection_score"] = influence_anchor_selection_score(item)
    evaluated.sort(key=lambda item: (-as_float(item.get("selection_score")), 0 if item.get("anchor") == primary else 1))
    best = evaluated[0]
    initiative_best = sorted(
        evaluated,
        key=lambda item: (-as_float(item.get("initiative_selection_score")), 0 if item.get("anchor") == primary else 1),
    )[0]
    influence_best = sorted(
        evaluated,
        key=lambda item: (-as_float(item.get("influence_selection_score")), 0 if item.get("anchor") == primary else 1),
    )[0]
    best["selected"] = True
    initiative_best["initiative_selected"] = True
    influence_best["influence_selected"] = True
    selected_activity_context = {
        "selected_context": best.get("_activity_context") or {},
        "initiative_context": initiative_best.get("_activity_context") or {},
        "influence_context": influence_best.get("_activity_context") or {},
    }
    output_row = dict(row)
    if not primary:
        output_row["primary_anchor"] = best.get("anchor") or ""
    output_anchor = clean_anchor(output_row.get("primary_anchor"))
    output_anchor_context = fetch_anchor_context(config, trade_date, row.get("event_time", ""), output_anchor, row.get("code", ""))
    scored = score_event(output_row, output_anchor_context, selected_activity_context)
    public_source: list[dict[str, Any]] = []
    seen_public: set[str] = set()
    for item in [best, initiative_best, influence_best, *evaluated]:
        anchor_key = str(item.get("anchor") or "")
        if anchor_key in seen_public:
            continue
        seen_public.add(anchor_key)
        public_source.append(item)
        if len(public_source) >= 8:
            break
    public_candidates = []
    for item in public_source:
        clone = {key: value for key, value in item.items() if not key.startswith("_")}
        public_candidates.append(clone)
    detail = scored.setdefault("score_detail", {})
    detail["activity_anchor"] = best.get("anchor") or ""
    detail["activity_anchor_source"] = best.get("source") or ""
    detail["activity_anchor_selection_score"] = best.get("selection_score")
    detail["initiative_anchor"] = initiative_best.get("anchor") or ""
    detail["initiative_anchor_source"] = initiative_best.get("source") or ""
    detail["initiative_anchor_selection_score"] = initiative_best.get("initiative_selection_score")
    detail["influence_anchor"] = influence_best.get("anchor") or ""
    detail["influence_anchor_source"] = influence_best.get("source") or ""
    detail["influence_anchor_selection_score"] = influence_best.get("influence_selection_score")
    detail["activity_anchor_candidates"] = public_candidates
    detail["multi_theme_influence"] = multi_theme_influence_items(
        evaluated,
        (headline_theme_index or {}).get(str(row.get("code") or "").strip(), {}),
    )
    detail["activity_anchor_selection_reason"] = (
        f"按「{best.get('anchor')}」口径计算：波段第{best.get('wave_trigger_order') or '-'}个触发，"
        f"{best.get('influence_label') or '带动未知'}，候选分{best.get('selection_score')}"
    )
    detail["initiative_anchor_selection_reason"] = (
        f"主动性按「{initiative_best.get('anchor')}」口径：波段第{initiative_best.get('wave_trigger_order') or '-'}个触发，"
        f"{initiative_best.get('initiative_label') or '主动性未知'}"
    )
    detail["influence_anchor_selection_reason"] = (
        f"带动性按「{influence_best.get('anchor')}」口径：10分钟新增{influence_best.get('new_after_10m') or 0}只，"
        f"{influence_best.get('influence_label') or '带动未知'}"
    )
    detail["original_primary_anchor"] = primary
    return output_row, scored


def hard_catalyst_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    evidence_strength = str(row.get("evidence_strength") or "pending")
    async_strength = str(row.get("async_explanation_strength") or "none")
    timeliness = str(row.get("timeliness_label") or "unknown")
    hard_catalysts = parse_json(row.get("hard_catalysts"), [])
    core_support = parse_json(row.get("core_support"), [])
    impact = str(row.get("impact_summary_text") or "")
    text = f"{impact} {' '.join(str(item) for item in hard_catalysts[:3])} {' '.join(str(item) for item in core_support[:2])}"
    score = 0.0
    reasons: list[str] = []
    is_fresh = timeliness in {"fresh", "recent"}
    if hard_catalysts:
        score += 10 if is_fresh else 4
        reasons.append("有硬催化材料")
    if evidence_strength == "strong":
        score += 8 if is_fresh else 4
        reasons.append("异步证据强")
    elif evidence_strength == "medium":
        score += 5 if is_fresh else 3
    elif evidence_strength == "weak":
        score += 2
    if async_strength == "strong":
        score += 5 if is_fresh else 2
        reasons.append("模型解释强")
    elif async_strength == "medium":
        score += 3
    fresh_bonus = {"fresh": 7, "recent": 4, "stale": 0, "unknown": 0}.get(timeliness, 0)
    score += fresh_bonus
    if fresh_bonus >= 4:
        reasons.append("证据时效较新")
    if re.search(r"重组|并购|收购|资产注入|控制权|借壳", text):
        score += 6 if is_fresh else 2
        reasons.append("重组并购信息")
    if re.search(r"大订单|重大合同|合同金额|中标金额|签订.*合同|采购.*合同", text):
        score += 6 if is_fresh else 2
        reasons.append("重大订单/合同")
    elif re.search(r"合同|订单|中标|采购|框架协议|合作协议|定点", text):
        score += 4 if is_fresh else 2
        reasons.append("合同/订单类信息")
    if re.search(r"业绩大增|预增|扭亏|净利润.*增长|净利润.*同比|营收.*增长|营收.*同比", text):
        score += 5 if is_fresh else 2
        reasons.append("业绩强催化")
    elif re.search(r"业绩|净利润|营收|同比|增长", text):
        score += 3 if is_fresh else 1
        reasons.append("业绩信息")
    if re.search(r"知名游资|机构净买|多机构|股通净买|龙虎榜", text):
        score += 3 if is_fresh else 1
        reasons.append("资金席位确认")
    if re.search(r"正宗|供应|客户|应用于|用于|产品|产能|明确客户|核心客户", text):
        score += 2 if is_fresh else 1
    if not is_fresh:
        score = min(score, 16.0)
        if score > 0:
            reasons.append("非新鲜信息，硬催化限分")
    return min(score, 35.0), reasons[:3]


def anchor_leadership_score(row: dict[str, Any], anchor_context: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    rank_3d = as_int(anchor_context.get("rank_3d"), 9999)
    rank_5d = as_int(anchor_context.get("rank_5d"), 9999)
    rank_10d = as_int(anchor_context.get("rank_10d"), 9999)
    amount_rank = as_int(anchor_context.get("today_amount_rank"), 9999)
    speed_rank = as_int(anchor_context.get("today_speed_rank"), 9999)
    role = str(row.get("role_label") or anchor_context.get("today_role_label") or "")
    period_rank_source = str(anchor_context.get("period_rank_source") or "")
    has_pool_strength = period_rank_source == "research_pool"
    if not has_pool_strength:
        rank_3d = rank_5d = rank_10d = 9999
    if 0 < rank_3d <= 5:
        score += 12
        reasons.append(f"3日锚点内第{rank_3d}")
    elif 0 < rank_3d <= 10:
        score += 7
    if 0 < rank_5d <= 5:
        score += 8
        reasons.append(f"5日锚点内第{rank_5d}")
    elif 0 < rank_5d <= 10:
        score += 4
    if 0 < rank_10d <= 5:
        score += 5
        reasons.append(f"10日锚点内第{rank_10d}")
    elif 0 < rank_10d <= 10:
        score += 2
    if 0 < amount_rank <= 5:
        score += 3
        reasons.append(f"今日成交锚点内第{amount_rank}")
    if 0 < speed_rank <= 5:
        score += 2
    if not has_pool_strength:
        reasons.append("未进研究池近期强势结构")
    if "领涨" in role:
        score += 5
        reasons.append("具备领涨定位")
    elif "中军" in role:
        score += 4
        reasons.append("具备中军定位")
    elif role:
        score += 2
    return min(score, 30.0), reasons[:3]


def tape_confirm_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    speed = as_float(row.get("speed"))
    pct_change = as_float(row.get("pct_change"))
    amount_delta = as_float(row.get("amount_delta_15s"))
    appearance_count = as_int(row.get("appearance_count"))
    window_score = as_float(row.get("window_score"))
    score = 0.0
    reasons: list[str] = []
    if speed >= 2:
        score += 7
        reasons.append("涨速强")
    elif speed >= 1:
        score += 5
        reasons.append("涨速有效")
    elif speed >= 0.5:
        score += 3
    if amount_delta >= 50_000_000:
        score += 6
        reasons.append("15秒成交增量强")
    elif amount_delta >= 30_000_000:
        score += 5
        reasons.append("15秒成交增量有效")
    elif amount_delta >= 10_000_000:
        score += 3
    if appearance_count >= 5:
        score += 5
        reasons.append("多次出现")
    elif appearance_count >= 3:
        score += 3
    elif appearance_count >= 2:
        score += 2
    if window_score >= 80:
        score += 2
    elif pct_change > 0:
        score += 1
    return min(score, 20.0), reasons[:3]


def anchor_risk_deduction(row: dict[str, Any], anchor_context: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    risks: list[str] = []
    anchor = clean_anchor(row.get("primary_anchor"))
    if not anchor:
        score += 7
        risks.append("缺少明确题材锚点")
    if as_int(anchor_context.get("limit_down_count")) > 0:
        score += 12
        risks.append("锚点内正宗票跌停")
    elif as_int(anchor_context.get("crash_count")) > 0:
        score += 8
        risks.append("锚点内正宗票暴跌")
    if as_int(anchor_context.get("core_weak_count")) > 0:
        score += 6
        risks.append("锚点领涨/中军走弱")
    if as_float(anchor_context.get("anchor_avg_pct")) < -2:
        score += 4
        risks.append("锚点整体偏弱")
    if str(row.get("anchor_match") or "") == "mismatch":
        score += 7
        risks.append("锚点与证据不一致")
    if str(row.get("evidence_strength") or "pending") in {"pending", "weak"} and str(row.get("async_explanation_strength") or "none") in {"none", "weak"}:
        score += 4
        risks.append("硬证据不足")
    if as_float(row.get("pct_change")) >= 15 and not parse_json(row.get("hard_catalysts"), []):
        score += 3
        risks.append("高涨幅缺少新硬催化")
    return min(score, 25.0), risks[:3]


def score_event(
    row: dict[str, Any],
    anchor_context: dict[str, Any] | None = None,
    activity_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anchor_context = anchor_context or {}
    activity_context = activity_context or {}
    selected_activity_context = activity_context
    initiative_context = activity_context
    influence_context = activity_context
    if isinstance(activity_context, dict) and (
        "initiative_context" in activity_context
        or "influence_context" in activity_context
        or "selected_context" in activity_context
    ):
        selected_activity_context = activity_context.get("selected_context") or {}
        initiative_context = activity_context.get("initiative_context") or selected_activity_context
        influence_context = activity_context.get("influence_context") or selected_activity_context
    anchor = clean_anchor(row.get("primary_anchor"))
    role = str(row.get("role_label") or "")
    member_count = max(as_int(row.get("anchor_member_count")), as_int(anchor_context.get("anchor_member_total")))
    rank_no = as_int(row.get("rank_no"), 99)
    anchor_match = str(row.get("anchor_match") or "weak")

    hard_score, hard_reasons = hard_catalyst_score(row)
    leadership_score, leadership_reasons = anchor_leadership_score(row, anchor_context)
    tape_score, tape_reasons = tape_confirm_score(row)
    risk, risks = anchor_risk_deduction(row, anchor_context)
    initiative, initiative_label, initiative_reasons = initiative_score(initiative_context)
    influence, influence_label, influence_reasons = influence_score(influence_context)
    structured_influence = influence_payload(influence_context, influence_label, influence_reasons)
    behavior_score, behavior_reasons = short_term_behavior_score(initiative, initiative_label, influence, influence_label)

    if anchor and member_count >= 3 and anchor_match in {"strong", "medium"}:
        leadership_score = min(30.0, leadership_score + 2)
    if "领涨" in role and rank_no <= 5:
        leadership_score = min(30.0, leadership_score + 2)

    total = max(0, min(100, hard_score + leadership_score + tape_score + behavior_score - risk))
    if total >= 80:
        label = "可持续"
    elif total >= 60:
        label = "观察"
    elif total >= 40:
        label = "脉冲"
    else:
        label = "走弱"
    if total >= 70 and (anchor or hard_score >= 15):
        explanation_strength = "strong"
    elif total >= 50:
        explanation_strength = "medium"
    elif total >= 30:
        explanation_strength = "weak"
    else:
        explanation_strength = "none"

    return {
        "total": total,
        "label": label,
        "explanation_strength": explanation_strength,
        "risk_item": risks[0] if risks else "",
        "score_detail": {
            "hard_catalyst": hard_score,
            "hard_catalyst_reasons": hard_reasons,
            "anchor_leadership": leadership_score,
            "anchor_leadership_reasons": leadership_reasons,
            "tape_confirm": tape_score,
            "tape_confirm_reasons": tape_reasons,
            "initiative": initiative,
            "initiative_label": initiative_label,
            "initiative_reasons": initiative_reasons,
            "influence": influence,
            "influence_label": influence_label,
            "influence_reasons": influence_reasons,
            "influence_payload": structured_influence,
            "short_term_behavior": behavior_score,
            "short_term_behavior_reasons": behavior_reasons,
            "anchor_risk_deduction": risk,
            "risk_flags": risks,
            "anchor_context": anchor_context,
            "activity_context": selected_activity_context,
            "initiative_activity_context": initiative_context,
            "influence_activity_context": influence_context,
        },
    }


def first_line(text: str) -> str:
    return compact(str(text or "").splitlines()[0] if text else "", 70)


def build_texts(row: dict[str, Any], scored: dict[str, Any]) -> dict[str, Any]:
    anchor = clean_anchor(row.get("primary_anchor"))
    stock_reason = first_line(row.get("stock_reason") or row.get("anchor_reason") or "")
    impact = first_line(row.get("impact_summary_text") or "")
    async_view = first_line(row.get("final_view") or row.get("async_move_explanation") or "")
    speed = as_float(row.get("speed"))
    amount_delta = as_float(row.get("amount_delta_15s"))
    appearance_count = as_int(row.get("appearance_count"))
    anchor_context = (scored.get("score_detail") or {}).get("anchor_context") or {}
    member_count = max(as_int(row.get("anchor_member_count")), as_int(anchor_context.get("anchor_member_total")))
    role = str(row.get("role_label") or "").strip()
    rank_no = as_int(row.get("rank_no"), 0)

    evidence_phrase = stock_reason or impact or async_view
    if anchor and evidence_phrase:
        explanation = compact(f"{anchor}有题材解释，盘口涨速和成交确认", 80)
    elif anchor:
        explanation = compact(f"{anchor}带动，主要由盘中涨速和成交增量触发", 80)
    elif evidence_phrase:
        explanation = compact("有个股材料，但题材锚点未确认，偏个股脉冲", 80)
    else:
        explanation = "缺少明确题材和硬证据，主要由盘口涨速触发"

    support_items: list[str] = []
    if anchor:
        role_text = f"，定位{role}" if role else ""
        member_text = f"，同锚{member_count}只" if member_count >= 2 else ""
        support_items.append(compact(f"锚点：{anchor}{member_text}{role_text}", 90))
    detail = scored.get("score_detail") or {}
    activity_anchor = clean_anchor(detail.get("activity_anchor"))
    if activity_anchor and activity_anchor != anchor:
        support_items.append(compact(f"活动候选：{activity_anchor}，用于多题材活动口径参考", 90))
    initiative_anchor = clean_anchor(detail.get("initiative_anchor"))
    influence_anchor = clean_anchor(detail.get("influence_anchor"))
    if initiative_anchor and influence_anchor and initiative_anchor != influence_anchor:
        support_items.append(compact(f"主动性口径：{initiative_anchor}；带动性口径：{influence_anchor}", 100))
    if evidence_phrase:
        support_items.append(compact(f"证据：{evidence_phrase}", 100))
    leadership_reasons = []
    if leadership_reasons:
        support_items.append(compact(f"区间领头：{'；'.join(str(item) for item in leadership_reasons[:3])}", 110))
    initiative_reasons = detail.get("initiative_reasons") or []
    initiative_label = detail.get("initiative_label") or ""
    if initiative_reasons and initiative_label != "未知":
        support_items.append(compact(f"主动性{initiative_label}：{'；'.join(str(item) for item in initiative_reasons[:2])}", 110))
    influence_reasons = detail.get("influence_reasons") or []
    influence_label = detail.get("influence_label") or ""
    if influence_reasons and influence_label != "未知":
        support_items.append(compact(f"带动性{influence_label}：{'；'.join(str(item) for item in influence_reasons[:2])}", 110))
    support_items.append(compact(f"盘口：排名{rank_no}，涨速{speed:.2f}%，15秒成交增量{amount_delta / 10000:.0f}万，出现{appearance_count}次", 100))
    support_items = support_items[:7]

    risk_item = scored.get("risk_item") or "暂无明显硬伤"
    final_view = compact(f"{scored['label']}，分数{scored['total']:.0f}；{explanation}", 140)
    return {
        "move_explanation": explanation,
        "support_items": support_items,
        "risk_item": risk_item,
        "final_view": final_view,
    }


def source_hash(row: dict[str, Any], scored: dict[str, Any]) -> str:
    payload = {
        "event_type": row.get("event_type"),
        "event_id": row.get("event_id"),
        "code": row.get("code"),
        "rank_no": row.get("rank_no"),
        "pct_change": row.get("pct_change"),
        "speed": row.get("speed"),
        "amount_delta_15s": row.get("amount_delta_15s"),
        "primary_anchor": row.get("primary_anchor"),
        "role_label": row.get("role_label"),
        "evidence_hash": row.get("evidence_hash"),
        "score_detail": scored.get("score_detail"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def write_judgement(config: MySqlConfig, trade_date: str, row: dict[str, Any], scored: dict[str, Any], texts: dict[str, Any]) -> None:
    market_snapshot = {
        "rank_no": as_int(row.get("rank_no")),
        "pct_change": as_float(row.get("pct_change")),
        "speed": as_float(row.get("speed")),
        "amount": as_float(row.get("amount")),
        "amount_delta_15s": as_float(row.get("amount_delta_15s")),
        "appearance_count": as_int(row.get("appearance_count")),
        "window_score": as_float(row.get("window_score")),
        "anchor_member_count": as_int(row.get("anchor_member_count")),
        "role_label": row.get("role_label") or "",
        "leader_code": row.get("leader_code") or "",
        "leader_name": row.get("leader_name") or "",
        "core_code": row.get("core_code") or "",
        "core_name": row.get("core_name") or "",
    }
    evidence_snapshot = {
        "evidence_hash": row.get("evidence_hash") or "",
        "evidence_strength": row.get("evidence_strength") or "pending",
        "async_explanation_strength": row.get("async_explanation_strength") or "none",
        "anchor_match": row.get("anchor_match") or "weak",
        "quality_label": row.get("quality_label") or "",
        "timeliness_label": row.get("timeliness_label") or "unknown",
        "stock_reason": compact(row.get("stock_reason") or "", 220),
        "anchor_reason": compact(row.get("anchor_reason") or "", 220),
        "impact_summary_text": compact(row.get("impact_summary_text") or "", 220),
    }
    h = source_hash(row, scored)
    sql = f"""
    INSERT INTO stock_move_judgements(
      trade_date, event_time, event_type, event_id, code, stock_name,
      primary_anchor, anchor_type, move_explanation, explanation_strength,
      sustainability_label, sustainability_score, hard_catalyst_score, anchor_leadership_score,
      tape_confirm_score, anchor_risk_deduction, support_items, risk_item,
      final_view, score_detail, market_snapshot, evidence_snapshot, source_hash, model
    ) VALUES (
      {sql_string(trade_date)},
      {sql_string(row['event_time'])},
      {sql_string(row['event_type'])},
      {sql_string(row['event_id'])},
      {sql_string(row['code'])},
      {sql_string(row['stock_name'])},
      {sql_string(clean_anchor(row.get('primary_anchor')))},
      {sql_string(row.get('anchor_type') or '')},
      {sql_string(texts['move_explanation'])},
      {sql_string(scored['explanation_strength'])},
      {sql_string(scored['label'])},
      {float(scored['total'])},
      {float((scored.get('score_detail') or {}).get('hard_catalyst', 0))},
      {float((scored.get('score_detail') or {}).get('anchor_leadership', 0))},
      {float((scored.get('score_detail') or {}).get('tape_confirm', 0))},
      {float((scored.get('score_detail') or {}).get('anchor_risk_deduction', 0))},
      {sql_json(texts['support_items'])},
      {sql_string(texts['risk_item'])},
      {sql_string(texts['final_view'])},
      {sql_json(scored['score_detail'])},
      {sql_json(market_snapshot)},
      {sql_json(evidence_snapshot)},
      {sql_string(h)},
      'rule_v2'
    )
    ON DUPLICATE KEY UPDATE
      event_time=VALUES(event_time),
      stock_name=VALUES(stock_name),
      primary_anchor=VALUES(primary_anchor),
      anchor_type=VALUES(anchor_type),
      move_explanation=VALUES(move_explanation),
      explanation_strength=VALUES(explanation_strength),
      sustainability_label=VALUES(sustainability_label),
      sustainability_score=VALUES(sustainability_score),
      hard_catalyst_score=VALUES(hard_catalyst_score),
      anchor_leadership_score=VALUES(anchor_leadership_score),
      tape_confirm_score=VALUES(tape_confirm_score),
      anchor_risk_deduction=VALUES(anchor_risk_deduction),
      support_items=VALUES(support_items),
      risk_item=VALUES(risk_item),
      final_view=VALUES(final_view),
      score_detail=VALUES(score_detail),
      market_snapshot=VALUES(market_snapshot),
      evidence_snapshot=VALUES(evidence_snapshot),
      source_hash=VALUES(source_hash),
      model=VALUES(model),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)


def build_judgements(
    config: MySqlConfig,
    trade_date: str,
    scan_top: int,
    window_top: int,
    latest_only: bool,
    limit: int,
    code: str = "",
    dirty_only: bool = False,
    research_pool_only: bool = False,
) -> dict[str, int]:
    ensure_table(config)
    if dirty_only:
        return {"events": 0, "written": 0, "dirty_disabled": 1}
    codes = [code.strip()] if code.strip() else []
    research_codes = None
    if research_pool_only and not code.strip():
        research_codes = ResearchPoolProvider(config).latest_codes(trade_date)
    if research_pool_only and not code.strip() and not research_codes:
        return {"events": 0, "written": 0}
    if research_pool_only and not code.strip() and research_codes:
        codes = list(research_codes)
    events = load_events(config, trade_date, scan_top, window_top, latest_only, limit, codes, research_pool_only)
    index_codes = research_codes if research_pool_only and not code.strip() else None
    activity_index = load_activity_index(config, trade_date, index_codes)
    headline_theme_index = load_headline_theme_index(config, trade_date, index_codes)
    written = 0
    processed_codes: set[str] = set()
    for row in events:
        selected_row, scored = score_best_activity_anchor(config, trade_date, row, activity_index, headline_theme_index)
        texts = build_texts(selected_row, scored)
        scored["score_detail"]["display_contract"] = build_display_contract(selected_row, scored, texts)
        write_judgement(config, trade_date, selected_row, scored, texts)
        processed_codes.add(str(row.get("code") or ""))
        written += 1
    result = {"events": len(events), "written": written}
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build stock move explanation and sustainability judgements.")
    parser.add_argument("--trade-date", default="latest")
    parser.add_argument("--scan-top", type=int, default=20)
    parser.add_argument("--window-top", type=int, default=5)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--code", default="")
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--dirty-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--research-pool-only", dest="research_pool_only", action="store_true", help="Only process active research pool stocks.")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    args.trade_date = resolve_trade_date(config, args.trade_date)
    result = build_judgements(
        config,
        args.trade_date,
        args.scan_top,
        args.window_top,
        args.latest_only,
        args.limit,
        args.code,
        args.dirty_only,
        args.research_pool_only,
    )
    print(json.dumps({"trade_date": args.trade_date, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
