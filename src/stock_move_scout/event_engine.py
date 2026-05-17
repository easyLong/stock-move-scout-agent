from __future__ import annotations

from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string


def ensure_event_engine_tables(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_move_events (
      event_id VARCHAR(128) NOT NULL PRIMARY KEY,
      trade_date DATE NOT NULL,
      event_time DATETIME(3) NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      event_type VARCHAR(32) NOT NULL DEFAULT '',
      source_table VARCHAR(64) NOT NULL DEFAULT '',
      source_key VARCHAR(128) NOT NULL DEFAULT '',
      trigger_price DECIMAL(12,4) NULL,
      trigger_pct DECIMAL(10,4) NULL,
      speed_pct DECIMAL(10,4) NULL,
      amount DECIMAL(20,2) NULL,
      sort_rank INT NOT NULL DEFAULT 0,
      anchor_scope_type VARCHAR(32) NOT NULL DEFAULT '',
      anchor_name VARCHAR(128) NOT NULL DEFAULT '',
      role_label VARCHAR(64) NOT NULL DEFAULT '',
      role_score DECIMAL(14,4) NOT NULL DEFAULT 0,
      event_strength DECIMAL(14,4) NOT NULL DEFAULT 0,
      payload JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_stock_move_event_source (source_table, source_key, code),
      KEY idx_stock_move_events_day_time (trade_date, event_time),
      KEY idx_stock_move_events_code_day (code, trade_date),
      KEY idx_stock_move_events_anchor (trade_date, anchor_name, event_strength),
      KEY idx_stock_move_events_type (trade_date, event_type, event_strength)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS derived_signals (
      signal_id VARCHAR(160) NOT NULL PRIMARY KEY,
      trade_date DATE NOT NULL,
      signal_time DATETIME(3) NOT NULL,
      scope_type VARCHAR(32) NOT NULL DEFAULT '',
      scope_key VARCHAR(128) NOT NULL DEFAULT '',
      related_event_id VARCHAR(128) NOT NULL DEFAULT '',
      code CHAR(6) NOT NULL DEFAULT '',
      signal_type VARCHAR(64) NOT NULL DEFAULT '',
      signal_name VARCHAR(128) NOT NULL DEFAULT '',
      signal_value VARCHAR(255) NOT NULL DEFAULT '',
      signal_stage VARCHAR(64) NOT NULL DEFAULT '',
      signal_score DECIMAL(14,4) NOT NULL DEFAULT 0,
      source_table VARCHAR(64) NOT NULL DEFAULT '',
      source_key VARCHAR(128) NOT NULL DEFAULT '',
      source_tables JSON NULL,
      payload JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_derived_signal_source (source_table, source_key, signal_type, scope_type, scope_key, code),
      KEY idx_derived_signals_scope (trade_date, scope_type, scope_key, signal_type, signal_time),
      KEY idx_derived_signals_event (related_event_id),
      KEY idx_derived_signals_code (code, trade_date, signal_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_move_evidence (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      event_id VARCHAR(128) NOT NULL,
      trade_date DATE NOT NULL,
      event_time DATETIME(3) NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      evidence_scope_type VARCHAR(32) NOT NULL DEFAULT 'stock',
      evidence_scope_key VARCHAR(128) NOT NULL DEFAULT '',
      evidence_type VARCHAR(64) NOT NULL DEFAULT '',
      evidence_group ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'current_effective',
      evidence_role VARCHAR(64) NOT NULL DEFAULT '',
      source_table VARCHAR(64) NOT NULL DEFAULT '',
      source_key VARCHAR(160) NOT NULL DEFAULT '',
      evidence_title VARCHAR(512) NOT NULL DEFAULT '',
      evidence_body TEXT NULL,
      relevance_score DECIMAL(8,2) NOT NULL DEFAULT 0,
      validity_score DECIMAL(8,2) NOT NULL DEFAULT 0,
      display_priority INT NOT NULL DEFAULT 100,
      model_required TINYINT NOT NULL DEFAULT 0,
      payload JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_move_evidence_source (event_id, source_table, source_key, evidence_type),
      KEY idx_move_evidence_event (event_id, display_priority),
      KEY idx_move_evidence_code_day (code, trade_date, evidence_group, display_priority),
      KEY idx_move_evidence_source (source_table, source_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_move_evidence_dirty_queue (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      event_id VARCHAR(128) NOT NULL DEFAULT '',
      code CHAR(6) NOT NULL DEFAULT '',
      reason VARCHAR(255) NOT NULL DEFAULT '',
      changed_sources JSON NULL,
      priority INT NOT NULL DEFAULT 40,
      status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
      attempt_count INT NOT NULL DEFAULT 0,
      locked_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      last_error TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_move_evidence_dirty (trade_date, event_id, code, reason),
      KEY idx_move_evidence_dirty_status (status, priority, created_at),
      KEY idx_move_evidence_dirty_code (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def build_move_events(config: MySqlConfig, trade_date: str, *, limit: int = 0, code: str = "") -> dict[str, Any]:
    ensure_event_engine_tables(config)
    code_filter_scan = f"AND sm.code={sql_string(code)}" if code else ""
    code_filter_window = f"AND wm.code={sql_string(code)}" if code else ""
    scan_limit = f"LIMIT {int(limit)}" if int(limit or 0) > 0 else ""
    day = sql_string(trade_date)
    scan_sql = f"""
    INSERT INTO stock_move_events(
      event_id, trade_date, event_time, code, stock_name, event_type,
      source_table, source_key, trigger_price, trigger_pct, speed_pct, amount, sort_rank,
      anchor_scope_type, anchor_name, role_label, role_score, event_strength, payload
    )
    SELECT *
    FROM (
      SELECT
        CONCAT('scan:', sr.run_id, ':', sm.code) AS event_id,
        DATE(sr.scanned_at) AS trade_date,
        sr.scanned_at AS event_time,
        sm.code,
        sm.name AS stock_name,
        'realtime_scan' AS event_type,
        'scan_movers' AS source_table,
        CONCAT(sr.run_id, ':', sm.code) AS source_key,
        sm.price AS trigger_price,
        sm.pct_change AS trigger_pct,
        sm.speed AS speed_pct,
        sm.amount,
        sm.rank_speed AS sort_rank,
        COALESCE(NULLIF(ssr.primary_anchor_type, ''), 'theme') AS anchor_scope_type,
        COALESCE(NULLIF(ssr.primary_anchor_name, ''), '') AS anchor_name,
        COALESCE(ssr.role_label, '') AS role_label,
        COALESCE(ssr.role_score, 0) AS role_score,
        70
          + LEAST(COALESCE(sm.speed, 0) * 5, 15)
          + IF(COALESCE(sm.amount_delta_15s, 0) >= 30000000, 8, 0)
          - LEAST(COALESCE(sm.rank_speed, 20), 20) AS event_strength,
        JSON_OBJECT(
          'run_id', sr.run_id,
          'rank_speed', sm.rank_speed,
          'rank_pct_change', sm.rank_pct_change,
          'amount_delta_15s', sm.amount_delta_15s,
          'volume_delta_15s', sm.volume_delta_15s,
          'industry', sm.industry,
          'sub_industry', sm.sub_industry,
          'role_reason', COALESCE(ssr.role_reason, ''),
          'role_raw', COALESCE(ssr.raw_json, JSON_OBJECT())
        ) AS payload
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      LEFT JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id AND ssr.code=sm.code
      WHERE DATE(sr.scanned_at)=CAST({day} AS DATE)
        AND sr.accepted=1
        AND sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
        AND (
          sm.rank_speed <= 20
          OR COALESCE(sm.speed, 0) >= 1.5
        )
        {code_filter_scan}
      ORDER BY sr.scanned_at DESC, sm.rank_speed ASC
      {scan_limit}
    ) x
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      trigger_price=VALUES(trigger_price),
      trigger_pct=VALUES(trigger_pct),
      speed_pct=VALUES(speed_pct),
      amount=VALUES(amount),
      sort_rank=VALUES(sort_rank),
      anchor_scope_type=VALUES(anchor_scope_type),
      anchor_name=VALUES(anchor_name),
      role_label=VALUES(role_label),
      role_score=VALUES(role_score),
      event_strength=VALUES(event_strength),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    window_sql = f"""
    INSERT INTO stock_move_events(
      event_id, trade_date, event_time, code, stock_name, event_type,
      source_table, source_key, trigger_price, trigger_pct, speed_pct, amount, sort_rank,
      anchor_scope_type, anchor_name, role_label, role_score, event_strength, payload
    )
    SELECT *
    FROM (
      SELECT
        CONCAT('window:', w.window_id, ':', wm.code) AS event_id,
        DATE(w.ended_at) AS trade_date,
        w.ended_at AS event_time,
        wm.code,
        wm.name AS stock_name,
        'stable_window' AS event_type,
        'window_movers' AS source_table,
        CONCAT(w.window_id, ':', wm.code) AS source_key,
        wm.latest_price AS trigger_price,
        COALESCE(wm.max_pct_change, wm.latest_pct_change) AS trigger_pct,
        wm.max_speed AS speed_pct,
        wm.amount,
        wm.rank_no AS sort_rank,
        COALESCE(NULLIF(wsr.sector_type, ''), 'theme') AS anchor_scope_type,
        COALESCE(NULLIF(wsr.sector_key, ''), '') AS anchor_name,
        COALESCE(wsr.role_label, '') AS role_label,
        COALESCE(wsr.role_score, 0) AS role_score,
        COALESCE(wm.window_score, 0) AS event_strength,
        JSON_OBJECT(
          'window_id', w.window_id,
          'started_at', DATE_FORMAT(w.started_at, '%Y-%m-%d %H:%i:%s'),
          'ended_at', DATE_FORMAT(w.ended_at, '%Y-%m-%d %H:%i:%s'),
          'appearance_count', wm.appearance_count,
          'appearance_rate', wm.appearance_rate,
          'max_amount_delta_15s', wm.max_amount_delta_15s,
          'rank_delta', wm.rank_delta,
          'is_new_entry', wm.is_new_entry,
          'role_reason', COALESCE(wsr.role_reason, ''),
          'risk_flags', COALESCE(wsr.risk_flags, ''),
          'role_raw', COALESCE(wsr.raw_json, JSON_OBJECT())
        ) AS payload
      FROM windows w
      JOIN window_movers wm ON wm.window_id=w.id
      LEFT JOIN window_stock_roles wsr ON wsr.window_id=w.id AND wsr.code=wm.code
      WHERE DATE(w.ended_at)=CAST({day} AS DATE)
        AND w.status='done'
        AND wm.rank_no <= 5
        AND wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        {code_filter_window}
      ORDER BY w.ended_at DESC, wm.rank_no ASC
    ) x
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      trigger_price=VALUES(trigger_price),
      trigger_pct=VALUES(trigger_pct),
      speed_pct=VALUES(speed_pct),
      amount=VALUES(amount),
      sort_rank=VALUES(sort_rank),
      anchor_scope_type=VALUES(anchor_scope_type),
      anchor_name=VALUES(anchor_name),
      role_label=VALUES(role_label),
      role_score=VALUES(role_score),
      event_strength=VALUES(event_strength),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, scan_sql)
    run_mysql(config, window_sql)
    return _count_by(config, "stock_move_events", trade_date, code)


def build_derived_signals(config: MySqlConfig, trade_date: str, *, code: str = "") -> dict[str, Any]:
    ensure_event_engine_tables(config)
    day = sql_string(trade_date)
    code_filter = f"AND e.code={sql_string(code)}" if code else ""
    stock_role_sql = f"""
    INSERT INTO derived_signals(
      signal_id, trade_date, signal_time, scope_type, scope_key, related_event_id, code,
      signal_type, signal_name, signal_value, signal_stage, signal_score,
      source_table, source_key, source_tables, payload
    )
    SELECT
      CONCAT('stock_role:', e.event_id),
      e.trade_date,
      e.event_time,
      'stock',
      e.code,
      e.event_id,
      e.code,
      'intraday_role',
      '盘中角色',
      e.role_label,
      CASE
        WHEN e.role_label REGEXP '领涨|先锋|高标' THEN 'leader'
        WHEN e.role_label REGEXP '中军|核心' THEN 'core'
        WHEN e.role_label <> '' THEN 'member'
        ELSE 'unknown'
      END,
      LEAST(100, GREATEST(e.role_score, e.event_strength)),
      e.source_table,
      e.source_key,
      JSON_ARRAY(e.source_table),
      JSON_OBJECT('anchor_name', e.anchor_name, 'role_label', e.role_label, 'event_strength', e.event_strength, 'raw_role_score', e.role_score)
    FROM stock_move_events e
    WHERE e.trade_date=CAST({day} AS DATE)
      {code_filter}
      AND (e.role_label <> '' OR e.anchor_name <> '')
    ON DUPLICATE KEY UPDATE
      signal_value=VALUES(signal_value),
      signal_stage=VALUES(signal_stage),
      signal_score=VALUES(signal_score),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    scan_anchor_sql = f"""
    INSERT INTO derived_signals(
      signal_id, trade_date, signal_time, scope_type, scope_key, related_event_id, code,
      signal_type, signal_name, signal_value, signal_stage, signal_score,
      source_table, source_key, source_tables, payload
    )
    SELECT
      CONCAT('scan_anchor:', sr.run_id, ':', sas.anchor_type, ':', sas.anchor_name),
      DATE(sr.scanned_at),
      sr.scanned_at,
      'theme',
      sas.anchor_name,
      '',
      '',
      'sector_spread_intraday',
      '盘中板块扩散',
      sas.strength_label,
      CASE
        WHEN sas.rank_no <= 3 AND sas.member_count >= 5 THEN 'main_rise'
        WHEN sas.member_count >= 3 THEN 'spreading'
        ELSE 'watch'
      END,
      LEAST(100, sas.anchor_score / 10),
      'scan_anchor_stats',
      CONCAT(sr.run_id, ':', sas.anchor_type, ':', sas.anchor_name),
      JSON_ARRAY('scan_anchor_stats', 'scan_runs'),
      JSON_OBJECT(
        'rank_no', sas.rank_no,
        'anchor_type', sas.anchor_type,
        'member_count', sas.member_count,
        'leader_code', sas.leader_code,
        'leader_name', sas.leader_name,
        'core_code', sas.core_code,
        'core_name', sas.core_name,
        'max_speed', sas.max_speed,
        'avg_pct_change', sas.avg_pct_change,
        'raw_anchor_score', sas.anchor_score
      )
    FROM scan_runs sr
    JOIN scan_anchor_stats sas ON sas.scan_run_id=sr.id
    WHERE DATE(sr.scanned_at)=CAST({day} AS DATE)
      AND sr.accepted=1
      AND sas.anchor_name <> ''
    ON DUPLICATE KEY UPDATE
      signal_value=VALUES(signal_value),
      signal_stage=VALUES(signal_stage),
      signal_score=VALUES(signal_score),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    window_sector_sql = f"""
    INSERT INTO derived_signals(
      signal_id, trade_date, signal_time, scope_type, scope_key, related_event_id, code,
      signal_type, signal_name, signal_value, signal_stage, signal_score,
      source_table, source_key, source_tables, payload
    )
    SELECT
      CONCAT('window_sector:', w.window_id, ':', wss.sector_key),
      DATE(w.ended_at),
      w.ended_at,
      'theme',
      wss.sector_key,
      '',
      '',
      'sector_cycle_intraday',
      '板块周期',
      wss.strength_label,
      CASE
        WHEN wss.rank_no <= 3 AND wss.stock_count >= 5 THEN 'main_rise'
        WHEN wss.follower_count >= 3 THEN 'spreading'
        WHEN wss.stock_count >= 2 THEN 'watch'
        ELSE 'weak'
      END,
      LEAST(100, wss.sector_score / 10),
      'window_sector_stats',
      CONCAT(w.window_id, ':', wss.sector_key),
      JSON_ARRAY('window_sector_stats', 'windows'),
      JSON_OBJECT(
        'rank_no', wss.rank_no,
        'sector_type', wss.sector_type,
        'stock_count', wss.stock_count,
        'follower_count', wss.follower_count,
        'leader_code', wss.leader_code,
        'leader_name', wss.leader_name,
        'core_code', wss.core_code,
        'core_name', wss.core_name,
        'summary', wss.summary,
        'raw_sector_score', wss.sector_score
      )
    FROM windows w
    JOIN window_sector_stats wss ON wss.window_id=w.id
    WHERE DATE(w.ended_at)=CAST({day} AS DATE)
      AND w.status='done'
      AND wss.sector_key <> ''
    ON DUPLICATE KEY UPDATE
      signal_value=VALUES(signal_value),
      signal_stage=VALUES(signal_stage),
      signal_score=VALUES(signal_score),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    market_sql = f"""
    INSERT INTO derived_signals(
      signal_id, trade_date, signal_time, scope_type, scope_key, related_event_id, code,
      signal_type, signal_name, signal_value, signal_stage, signal_score,
      source_table, source_key, source_tables, payload
    )
    SELECT
      CONCAT('market_sentiment:', sr.run_id),
      DATE(sr.scanned_at),
      sr.scanned_at,
      'market',
      'A股',
      '',
      '',
      'market_sentiment_intraday',
      '盘中情绪',
      CONCAT('异动数', COUNT(sm.code), '；平均涨幅', ROUND(AVG(COALESCE(sm.pct_change, 0)), 2), '%'),
      CASE
        WHEN COUNT(sm.code) >= 20 AND AVG(COALESCE(sm.pct_change, 0)) >= 3 THEN 'hot'
        WHEN COUNT(sm.code) >= 10 THEN 'active'
        ELSE 'neutral'
      END,
      LEAST(100, COUNT(sm.code) * 3 + GREATEST(0, AVG(COALESCE(sm.pct_change, 0))) * 8),
      'scan_runs',
      sr.run_id,
      JSON_ARRAY('scan_runs', 'scan_movers'),
      JSON_OBJECT(
        'mover_count', COUNT(sm.code),
        'avg_pct_change', ROUND(AVG(COALESCE(sm.pct_change, 0)), 4),
        'max_speed', MAX(COALESCE(sm.speed, 0)),
        'accepted', sr.accepted
      )
    FROM scan_runs sr
    JOIN scan_movers sm ON sm.scan_run_id=sr.id
    WHERE DATE(sr.scanned_at)=CAST({day} AS DATE)
      AND sr.accepted=1
    GROUP BY sr.id, sr.run_id, sr.scanned_at, sr.accepted
    ON DUPLICATE KEY UPDATE
      signal_value=VALUES(signal_value),
      signal_stage=VALUES(signal_stage),
      signal_score=VALUES(signal_score),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, stock_role_sql)
    run_mysql(config, scan_anchor_sql)
    run_mysql(config, window_sector_sql)
    run_mysql(config, market_sql)
    return _count_by(config, "derived_signals", trade_date, code)


def build_event_evidence(config: MySqlConfig, trade_date: str, *, code: str = "", event_id: str = "") -> dict[str, Any]:
    ensure_event_engine_tables(config)
    day = sql_string(trade_date)
    event_filter = f"AND e.event_id={sql_string(event_id)}" if event_id else ""
    code_filter = f"AND e.code={sql_string(code)}" if code else ""
    delete_filter = f"AND event_id={sql_string(event_id)}" if event_id else f"AND code={sql_string(code)}" if code else ""
    run_mysql(
        config,
        f"""
        DELETE FROM stock_move_evidence
        WHERE trade_date=CAST({day} AS DATE)
          {delete_filter};
        """,
    )
    trigger_sql = f"""
    INSERT INTO stock_move_evidence(
      event_id, trade_date, event_time, code, stock_name, evidence_scope_type, evidence_scope_key,
      evidence_type, evidence_group, evidence_role, source_table, source_key,
      evidence_title, evidence_body, relevance_score, validity_score, display_priority, model_required, payload
    )
    SELECT
      e.event_id,
      e.trade_date,
      e.event_time,
      e.code,
      e.stock_name,
      'stock',
      e.code,
      'intraday_trigger',
      'current_effective',
      'trigger',
      'stock_move_events',
      e.event_id,
      '盘中异动触发',
      CONCAT(
        CASE e.event_type WHEN 'stable_window' THEN '稳定异动' ELSE '实时领涨' END,
        '；排名', e.sort_rank,
        IF(e.anchor_name <> '', CONCAT('；锚点 ', e.anchor_name), ''),
        IF(e.role_label <> '', CONCAT('；角色 ', e.role_label), ''),
        IF(e.trigger_pct IS NOT NULL, CONCAT('；涨幅 ', ROUND(e.trigger_pct, 2), '%'), ''),
        IF(e.speed_pct IS NOT NULL, CONCAT('；涨速 ', ROUND(e.speed_pct, 2), '%'), ''),
        IF(e.amount IS NOT NULL, CONCAT('；成交额 ', ROUND(e.amount / 100000000, 2), '亿'), '')
      ),
      100,
      e.event_strength,
      1,
      0,
      JSON_OBJECT(
        'event_type', e.event_type,
        'sort_rank', e.sort_rank,
        'anchor_name', e.anchor_name,
        'role_label', e.role_label,
        'payload', COALESCE(e.payload, JSON_OBJECT())
      )
    FROM stock_move_events e
    WHERE e.trade_date=CAST({day} AS DATE)
      {code_filter}
      {event_filter}
    ON DUPLICATE KEY UPDATE
      evidence_body=VALUES(evidence_body),
      relevance_score=VALUES(relevance_score),
      validity_score=VALUES(validity_score),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    effective_sql = f"""
    INSERT INTO stock_move_evidence(
      event_id, trade_date, event_time, code, stock_name, evidence_scope_type, evidence_scope_key,
      evidence_type, evidence_group, evidence_role, source_table, source_key,
      evidence_title, evidence_body, relevance_score, validity_score, display_priority, model_required, payload
    )
    WITH effective_ranked AS (
      SELECT
        ef.*,
        ROW_NUMBER() OVER (
          PARTITION BY ef.trade_date, ef.code, ef.evidence_group, ef.evidence_role, ef.fact_subtype
          ORDER BY
            FIELD(ef.display_level, 'primary', 'secondary', 'background', 'hidden'),
            ef.fact_date DESC,
            ef.valid_score DESC,
            ef.updated_at DESC
        ) AS fact_rn
      FROM stock_effective_facts ef
      WHERE ef.trade_date=CAST({day} AS DATE)
        AND ef.evidence_group IN ('current_effective','post_close_confirm','background_fact','historical_tag')
        AND ef.display_level IN ('primary','secondary','background')
    )
    SELECT
      e.event_id,
      e.trade_date,
      e.event_time,
      e.code,
      e.stock_name,
      'stock',
      ef.code,
      ef.fact_type,
      ef.evidence_group,
      CASE ef.evidence_role
        WHEN 'hard_catalyst' THEN 'catalyst'
        WHEN 'funds' THEN 'confirmation'
        WHEN 'strength' THEN 'confirmation'
        WHEN 'theme_confirmation' THEN 'confirmation'
        WHEN 'theme' THEN 'background'
        ELSE 'fact'
      END,
      ef.source_table,
      ef.source_key,
      ef.fact_title,
      COALESCE(NULLIF(ef.fact_body, ''), ef.fact_title),
      CASE ef.evidence_group
        WHEN 'current_effective' THEN 95
        WHEN 'post_close_confirm' THEN 86
        WHEN 'background_fact' THEN 55
        WHEN 'historical_tag' THEN 35
        ELSE 10
      END,
      ef.valid_score,
      CASE ef.evidence_group
        WHEN 'current_effective' THEN 10
        WHEN 'post_close_confirm' THEN 20
        WHEN 'background_fact' THEN 70
        WHEN 'historical_tag' THEN 90
        ELSE 120
      END,
      IF(ef.evidence_group IN ('current_effective','post_close_confirm') AND ef.valid_score >= 80, 1, 0),
      JSON_OBJECT(
        'fact_date', COALESCE(DATE_FORMAT(ef.fact_date, '%Y-%m-%d'), ''),
        'valid_status', ef.valid_status,
        'valid_reason', ef.valid_reason,
        'display_level', ef.display_level,
        'fact_payload', COALESCE(ef.payload, JSON_OBJECT())
      )
    FROM stock_move_events e
    JOIN effective_ranked ef ON ef.trade_date=e.trade_date AND ef.code=e.code AND ef.fact_rn=1
    WHERE e.trade_date=CAST({day} AS DATE)
      {code_filter}
      {event_filter}
    ON DUPLICATE KEY UPDATE
      evidence_group=VALUES(evidence_group),
      evidence_role=VALUES(evidence_role),
      evidence_title=VALUES(evidence_title),
      evidence_body=VALUES(evidence_body),
      relevance_score=VALUES(relevance_score),
      validity_score=VALUES(validity_score),
      display_priority=VALUES(display_priority),
      model_required=VALUES(model_required),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    signal_sql = f"""
    INSERT INTO stock_move_evidence(
      event_id, trade_date, event_time, code, stock_name, evidence_scope_type, evidence_scope_key,
      evidence_type, evidence_group, evidence_role, source_table, source_key,
      evidence_title, evidence_body, relevance_score, validity_score, display_priority, model_required, payload
    )
    SELECT
      event_id, trade_date, event_time, code, stock_name, evidence_scope_type, evidence_scope_key,
      evidence_type, evidence_group, evidence_role, source_table, source_key,
      evidence_title, evidence_body, relevance_score, validity_score, display_priority, model_required, payload
    FROM (
      SELECT
        e.event_id,
        e.trade_date,
        e.event_time,
        e.code,
        e.stock_name,
        ds.scope_type AS evidence_scope_type,
        ds.scope_key AS evidence_scope_key,
        ds.signal_type AS evidence_type,
        'current_effective' AS evidence_group,
        CASE
          WHEN ds.signal_type='intraday_role' THEN 'structure'
          WHEN ds.signal_type LIKE 'sector%' AND ds.signal_stage IN ('main_rise','spreading') THEN 'confirmation'
          WHEN ds.signal_type LIKE 'sector%' THEN 'risk'
          WHEN ds.signal_type LIKE 'market%' THEN 'context'
          ELSE 'signal'
        END AS evidence_role,
        ds.source_table,
        ds.signal_id AS source_key,
        ds.signal_name AS evidence_title,
        CONCAT(
          ds.signal_name, '：', ds.signal_value,
          IF(ds.signal_stage <> '', CONCAT('；阶段 ', ds.signal_stage), ''),
          IF(ds.signal_score > 0, CONCAT('；分数 ', ROUND(ds.signal_score, 0)), '')
        ) AS evidence_body,
        CASE
          WHEN ds.related_event_id=e.event_id THEN 98
          WHEN ds.scope_type='theme' AND ds.scope_key=e.anchor_name THEN 88
          WHEN ds.scope_type='market' THEN 45
          ELSE 30
        END AS relevance_score,
        ds.signal_score AS validity_score,
        CASE
          WHEN ds.related_event_id=e.event_id THEN 6
          WHEN ds.scope_type='theme' AND ds.signal_stage IN ('main_rise','spreading') THEN 16
          WHEN ds.scope_type='theme' THEN 58
          WHEN ds.scope_type='market' THEN 80
          ELSE 60
        END AS display_priority,
        IF(ds.signal_score >= 75 AND ds.scope_type IN ('stock','theme') AND ds.signal_stage NOT IN ('watch','weak','unknown'), 1, 0) AS model_required,
        JSON_OBJECT(
          'scope_type', ds.scope_type,
          'scope_key', ds.scope_key,
          'signal_stage', ds.signal_stage,
          'signal_score', ds.signal_score,
          'signal_payload', COALESCE(ds.payload, JSON_OBJECT())
        ) AS payload,
        ROW_NUMBER() OVER (
          PARTITION BY e.event_id, ds.scope_type, ds.signal_type
          ORDER BY
            IF(ds.related_event_id=e.event_id, 1, 0) DESC,
            ds.signal_time DESC,
            ds.signal_score DESC
        ) AS rn
      FROM stock_move_events e
      JOIN derived_signals ds ON ds.trade_date=e.trade_date
        AND ds.signal_time <= e.event_time
        AND (
          ds.related_event_id=e.event_id
          OR (ds.scope_type='theme' AND ds.scope_key <> '' AND ds.scope_key=e.anchor_name)
          OR ds.scope_type='market'
        )
      WHERE e.trade_date=CAST({day} AS DATE)
        {code_filter}
        {event_filter}
        AND ds.signal_time >= DATE_SUB(e.event_time, INTERVAL 30 MINUTE)
    ) x
    WHERE x.rn <= CASE
      WHEN x.evidence_scope_type='market' THEN 1
      WHEN x.evidence_scope_type='theme' THEN 1
      ELSE 1
    END
    ON DUPLICATE KEY UPDATE
      evidence_body=VALUES(evidence_body),
      relevance_score=VALUES(relevance_score),
      validity_score=VALUES(validity_score),
      display_priority=VALUES(display_priority),
      model_required=VALUES(model_required),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, trigger_sql)
    run_mysql(config, effective_sql)
    run_mysql(config, signal_sql)
    return _count_by(config, "stock_move_evidence", trade_date, code, event_id=event_id)


def build_event_engine(config: MySqlConfig, trade_date: str, *, code: str = "", limit: int = 0) -> dict[str, Any]:
    events = build_move_events(config, trade_date, limit=limit, code=code)
    signals = build_derived_signals(config, trade_date, code=code)
    evidence = build_event_evidence(config, trade_date, code=code)
    return {"trade_date": trade_date, "code": code, "events": events, "signals": signals, "evidence": evidence}


def _count_by(config: MySqlConfig, table_name: str, trade_date: str, code: str = "", event_id: str = "") -> dict[str, Any]:
    filters = [f"trade_date={sql_string(trade_date)}"]
    if code:
        filters.append(f"code={sql_string(code)}")
    if event_id and table_name in {"stock_move_events", "stock_move_evidence"}:
        filters.append(f"event_id={sql_string(event_id)}")
    where_sql = " AND ".join(filters)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*), COUNT(DISTINCT code)
            FROM {table_name}
            WHERE {where_sql};
            """,
            batch=True,
            raw=True,
        )
    )
    return {
        "table": table_name,
        "rows": int(rows[0][0]) if rows and rows[0] else 0,
        "codes": int(rows[0][1]) if rows and rows[0] and len(rows[0]) > 1 else 0,
    }


__all__ = [
    "build_derived_signals",
    "build_event_engine",
    "build_event_evidence",
    "build_move_events",
    "ensure_event_engine_tables",
]
