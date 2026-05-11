from __future__ import annotations

import json
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_string


VALID_DISPLAY_LEVELS = ("primary", "secondary", "background")


def ensure_effective_facts_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_effective_facts (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      source_table VARCHAR(64) NOT NULL,
      source_key VARCHAR(128) NOT NULL,
      source_confidence VARCHAR(32) NOT NULL DEFAULT 'explicit',
      fact_type VARCHAR(32) NOT NULL DEFAULT '',
      fact_subtype VARCHAR(64) NOT NULL DEFAULT '',
      fact_title VARCHAR(512) NOT NULL DEFAULT '',
      fact_body TEXT NULL,
      fact_date DATE NULL,
      valid_status ENUM('active','watch','historical','expired','invalid') NOT NULL DEFAULT 'watch',
      valid_score DECIMAL(8,2) NOT NULL DEFAULT 0,
      valid_reason VARCHAR(255) NOT NULL DEFAULT '',
      invalid_reason VARCHAR(255) NOT NULL DEFAULT '',
      evidence_role VARCHAR(64) NOT NULL DEFAULT '',
      evidence_group ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'background_fact',
      display_level ENUM('primary','secondary','background','hidden') NOT NULL DEFAULT 'secondary',
      valid_from DATE NULL,
      valid_until DATE NULL,
      payload JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_effective_fact_source (trade_date, source_table, source_key),
      KEY idx_effective_fact_code_day (code, trade_date, display_level, valid_score),
      KEY idx_effective_fact_group (trade_date, evidence_group, display_level, valid_score),
      KEY idx_effective_fact_role (trade_date, evidence_role, display_level),
      KEY idx_effective_fact_source_table (source_table, source_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_effective_facts_dirty_queue (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      reason VARCHAR(255) NOT NULL DEFAULT '',
      changed_sources JSON NULL,
      priority INT NOT NULL DEFAULT 35,
      status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
      attempt_count INT NOT NULL DEFAULT 0,
      locked_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      last_error TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_effective_facts_dirty (trade_date, code, reason),
      KEY idx_effective_facts_dirty_status (status, priority, created_at),
      KEY idx_effective_facts_dirty_code (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)
    ensure_effective_facts_column(
        config,
        "evidence_group",
        "ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'background_fact' AFTER evidence_role",
    )
    ensure_effective_facts_index(config, "idx_effective_fact_group", "(trade_date, evidence_group, display_level, valid_score)")


def ensure_effective_facts_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'stock_effective_facts'
              AND COLUMN_NAME = {sql_string(column_name)};
            """,
            batch=True,
            raw=True,
        )
    )
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE stock_effective_facts ADD COLUMN {column_name} {column_sql};")


def ensure_effective_facts_index(config: MySqlConfig, index_name: str, index_sql: str) -> None:
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'stock_effective_facts'
              AND INDEX_NAME = {sql_string(index_name)};
            """,
            batch=True,
            raw=True,
        )
    )
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        try:
            run_mysql(config, f"ALTER TABLE stock_effective_facts ADD KEY {index_name} {index_sql};")
        except RuntimeError as exc:
            if "Duplicate key name" not in str(exc):
                raise


def _code_filter(alias: str, code: str = "") -> str:
    return f"AND {alias}.code={sql_string(code)}" if code else ""


def clear_effective_facts(config: MySqlConfig, trade_date: str, code: str = "") -> None:
    ensure_effective_facts_table(config)
    code_filter = f"AND code={sql_string(code)}" if code else ""
    run_mysql(
        config,
        f"""
        DELETE FROM stock_effective_facts
        WHERE trade_date={sql_string(trade_date)}
          {code_filter};
        """,
    )


def build_effective_facts(config: MySqlConfig, trade_date: str, code: str = "") -> dict[str, Any]:
    ensure_effective_facts_table(config)
    clear_effective_facts(config, trade_date, code)
    statements = [
        _announcement_sql(trade_date, code),
        _lhb_sql(trade_date, code),
        _period_rank_sql(trade_date, code),
        _limit_up_review_sql(trade_date, code),
        _theme_background_sql(trade_date, code),
    ]
    for sql in statements:
        run_mysql(config, sql)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT evidence_group, display_level, valid_status, COUNT(*), COUNT(DISTINCT code)
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              {f"AND code={sql_string(code)}" if code else ""}
            GROUP BY evidence_group, display_level, valid_status
            ORDER BY evidence_group, display_level, valid_status;
            """,
            batch=True,
            raw=True,
        )
    )
    return {
        "trade_date": trade_date,
        "code": code,
        "groups": [
            {"evidence_group": row[0], "display_level": row[1], "valid_status": row[2], "facts": int(row[3]), "codes": int(row[4])}
            for row in rows
            if len(row) >= 5
        ],
    }


def enqueue_effective_facts_dirty(
    config: MySqlConfig,
    *,
    trade_date: str,
    code: str,
    stock_name: str = "",
    reason: str = "source_fact_updated",
    changed_sources: list[str] | None = None,
    priority: int = 35,
) -> None:
    ensure_effective_facts_table(config)
    code = str(code or "").strip()
    if not code:
        return
    sql = f"""
    INSERT INTO stock_effective_facts_dirty_queue(
      trade_date, code, stock_name, reason, changed_sources, priority, status
    ) VALUES (
      {sql_string(trade_date)},
      {sql_string(code)},
      {sql_string(stock_name)},
      {sql_string(reason)},
      {sql_json(changed_sources or [])},
      {int(priority)},
      'pending'
    )
    ON DUPLICATE KEY UPDATE
      stock_name=COALESCE(NULLIF(VALUES(stock_name), ''), stock_name),
      changed_sources=VALUES(changed_sources),
      priority=LEAST(priority, VALUES(priority)),
      status=IF(status IN ('done','ignored'), 'pending', status),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)


def fetch_effective_facts_dirty(config: MySqlConfig, trade_date: str, limit: int, code: str = "") -> list[dict[str, str]]:
    ensure_effective_facts_table(config)
    code_filter = f"AND code={sql_string(code)}" if code else ""
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT id, code, stock_name, COALESCE(changed_sources, JSON_ARRAY())
            FROM stock_effective_facts_dirty_queue
            WHERE trade_date={sql_string(trade_date)}
              AND (
                status='pending'
                OR (status='running' AND locked_at < DATE_SUB(NOW(3), INTERVAL 5 MINUTE))
              )
              {code_filter}
            ORDER BY priority ASC, created_at ASC
            LIMIT {int(limit)};
            """,
            batch=True,
            raw=True,
        )
    )
    out: list[dict[str, str]] = []
    for row in rows:
        if len(row) >= 3:
            out.append({"dirty_id": row[0], "code": row[1], "stock_name": row[2], "changed_sources": row[3] if len(row) > 3 else "[]"})
    ids = [str(item["dirty_id"]) for item in out if str(item.get("dirty_id", "")).isdigit()]
    if ids:
        run_mysql(
            config,
            f"""
            UPDATE stock_effective_facts_dirty_queue
            SET status='running',
                locked_at=CURRENT_TIMESTAMP(3),
                updated_at=CURRENT_TIMESTAMP(3)
            WHERE id IN ({",".join(ids)})
              AND status IN ('pending','running');
            """,
        )
    return out


def mark_effective_facts_dirty(config: MySqlConfig, dirty_id: str, status: str, error: str = "") -> None:
    if not dirty_id:
        return
    run_mysql(
        config,
        f"""
        UPDATE stock_effective_facts_dirty_queue
        SET status={sql_string(status)},
            finished_at=IF({sql_string(status)} IN ('done','failed','ignored'), CURRENT_TIMESTAMP(3), finished_at),
            last_error={sql_string(error[:1000])},
            attempt_count=attempt_count + IF({sql_string(status)}='failed', 1, 0),
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE id={int(dirty_id)};
        """,
    )


def _announcement_sql(trade_date: str, code: str = "") -> str:
    code_filter = _code_filter("ae", code)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      ae.code,
      ae.stock_name,
      'stock_announcement_effects',
      CONCAT('stock_announcement_effects:', ae.id),
      'explicit',
      'announcement',
      ae.event_subtype,
      ae.title,
      LEFT(CONCAT(
        DATE_FORMAT(ae.event_date, '%m-%d'), ' ',
        ae.tag, '：', COALESCE(NULLIF(ae.summary, ''), ae.title),
        IF(ae.verify_score > 0, CONCAT('；次日验证 ', ROUND(ae.verify_pct, 2), '%'), ''),
        IF(ae.effect_status='faded', CONCAT('；已失效 ', COALESCE(ae.faded_reason, '')), '')
      ), 600),
      ae.event_date,
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN 'active'
        WHEN ae.effect_status='active' THEN 'historical'
        WHEN ae.effect_status='faded' THEN 'expired'
        WHEN ae.effect_status='ignored' THEN 'invalid'
        ELSE 'historical'
      END,
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN GREATEST(ae.verify_score, ae.effect_score)
        WHEN ae.effect_status='active' THEN LEAST(60, GREATEST(ae.verify_score, ae.effect_score))
        ELSE 0
      END,
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN 'announcement_market_validated'
        WHEN ae.effect_status='active' THEN 'announcement_market_validated_stale'
        WHEN ae.effect_status='faded' THEN 'announcement_effect_faded'
        WHEN ae.effect_status='ignored' THEN 'announcement_market_ignored'
        ELSE 'announcement_unverified'
      END,
      CASE
        WHEN ae.effect_status='active'
          AND NOT (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN 'stale_active_hard_catalyst_demoted'
        WHEN ae.effect_status IN ('ignored','faded','unverified') THEN COALESCE(NULLIF(ae.faded_reason, ''), ae.effect_status)
        ELSE ''
      END,
      'hard_catalyst',
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN 'current_effective'
        WHEN ae.effect_status='active' THEN 'historical_tag'
        WHEN ae.effect_status='faded' THEN 'historical_tag'
        WHEN ae.effect_status IN ('ignored','unverified') THEN 'hidden'
        ELSE 'historical_tag'
      END,
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          )
          AND ae.verify_score >= 85 THEN 'primary'
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN 'secondary'
        WHEN ae.effect_status='active' THEN 'background'
        WHEN ae.effect_status='faded' THEN 'background'
        ELSE 'hidden'
      END,
      ae.event_date,
      CASE
        WHEN ae.effect_status='active'
          AND (
            ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 5 DAY)
          ) THEN CAST({day} AS DATE)
        WHEN ae.effect_status='active' THEN DATE_ADD(ae.event_date, INTERVAL 5 DAY)
        ELSE COALESCE(ae.faded_trade_date, ae.last_checked_trade_date)
      END,
      JSON_OBJECT(
        'root_item_id', ae.root_item_id,
        'event_type', ae.event_type,
        'tag', ae.tag,
        'verify_pct', ae.verify_pct,
        'verify_score', ae.verify_score,
        'current_pct_from_base', ae.current_pct_from_base,
        'avg_pct_from_base', ae.avg_pct_from_base,
        'effect_status', ae.effect_status,
        'base_trade_date', DATE_FORMAT(ae.base_trade_date, '%Y-%m-%d'),
        'verify_trade_date', DATE_FORMAT(ae.verify_trade_date, '%Y-%m-%d'),
        'faded_trade_date', DATE_FORMAT(ae.faded_trade_date, '%Y-%m-%d')
      )
    FROM stock_announcement_effects ae
    WHERE ae.event_date <= {day}
      AND ae.event_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 240 DAY)
      {code_filter}
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      valid_reason=VALUES(valid_reason),
      invalid_reason=VALUES(invalid_reason),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      valid_until=VALUES(valid_until),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def _lhb_sql(trade_date: str, code: str = "") -> str:
    code_filter = _code_filter("lhb", code)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      lhb.code,
      lhb.stock_name,
      'stock_lhb_seat_evidence',
      CONCAT('stock_lhb_seat_evidence:', DATE_FORMAT(lhb.trade_date, '%Y-%m-%d'), ':', lhb.code),
      'explicit',
      'lhb',
      'seat_structure',
      CONCAT(IF(lhb.trade_date=CAST({day} AS DATE), '当日龙虎榜', '上一交易日龙虎榜'), ' / ', COALESCE(NULLIF(lhb.seat_signal_label, ''), '席位结构')),
      LEFT(CONCAT_WS('\\n',
        JSON_UNQUOTE(JSON_EXTRACT(lhb.key_facts, '$[0]')),
        JSON_UNQUOTE(JSON_EXTRACT(lhb.key_facts, '$[1]')),
        JSON_UNQUOTE(JSON_EXTRACT(lhb.key_facts, '$[2]')),
        JSON_UNQUOTE(JSON_EXTRACT(lhb.key_facts, '$[3]'))
      ), 600),
      lhb.trade_date,
      CASE WHEN lhb.seat_signal_score >= 60 OR lhb.total_net_buy > 0 OR lhb.famous_trader_count > 0 OR lhb.institution_buy_count > 0 THEN 'active' ELSE 'watch' END,
      GREATEST(lhb.seat_signal_score, IF(lhb.total_net_buy > 0, 60, 0), IF(lhb.famous_trader_count > 0, 75, 0), IF(lhb.institution_buy_count > 0, 70, 0)),
      'lhb_recent_funds_confirmed',
      '',
      'funds',
      'post_close_confirm',
      CASE
        WHEN lhb.trade_date=CAST({day} AS DATE) AND (lhb.seat_signal_score >= 60 OR lhb.total_net_buy > 0) THEN 'primary'
        WHEN lhb.seat_signal_score >= 40 OR lhb.total_net_buy > 0 THEN 'secondary'
        ELSE 'background'
      END,
      lhb.trade_date,
      DATE_ADD(lhb.trade_date, INTERVAL 3 DAY),
      JSON_OBJECT(
        'seat_signal_label', lhb.seat_signal_label,
        'seat_signal_score', lhb.seat_signal_score,
        'total_net_buy', lhb.total_net_buy,
        'famous_trader_count', lhb.famous_trader_count,
        'institution_buy_count', lhb.institution_buy_count,
        'key_facts', lhb.key_facts
      )
    FROM stock_lhb_seat_evidence lhb
    WHERE lhb.trade_date <= {day}
      AND lhb.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 7 DAY)
      {code_filter}
    ON DUPLICATE KEY UPDATE
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def _period_rank_sql(trade_date: str, code: str = "") -> str:
    code_filter = _code_filter("r", code)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      r.code,
      r.stock_name,
      'stock_period_rankings',
      CONCAT('stock_period_rankings:', DATE_FORMAT(r.trade_date, '%Y-%m-%d'), ':', r.period_days, ':', r.code),
      'explicit',
      'period_rank',
      CONCAT(r.period_days, 'd'),
      CONCAT('近', r.period_days, '日区间强度'),
      CONCAT('近', r.period_days, '日全市场第', r.rank_no, IF(r.rank_total > 0, CONCAT('/', r.rank_total), ''), '；区间涨幅', ROUND(r.period_pct, 2), '%'),
      r.trade_date,
      CASE WHEN r.rank_no BETWEEN 1 AND 50 THEN 'active' ELSE 'watch' END,
      GREATEST(0, 100 - r.rank_no),
      'iwencai_period_rank_recent',
      '',
      'strength',
      'post_close_confirm',
      CASE WHEN r.rank_no BETWEEN 1 AND 20 THEN 'primary' WHEN r.rank_no BETWEEN 1 AND 80 THEN 'secondary' ELSE 'background' END,
      r.trade_date,
      DATE_ADD(r.trade_date, INTERVAL 3 DAY),
      JSON_OBJECT(
        'period_days', r.period_days,
        'rank_no', r.rank_no,
        'rank_total', r.rank_total,
        'period_pct', r.period_pct,
        'latest_pct', r.latest_pct
      )
    FROM stock_period_rankings r
    WHERE r.trade_date <= {day}
      AND r.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 7 DAY)
      AND r.period_days IN (3,5,10)
      AND r.rank_no > 0
      AND r.rank_no <= 120
      {code_filter}
    ON DUPLICATE KEY UPDATE
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def _limit_up_review_sql(trade_date: str, code: str = "") -> str:
    code_filter = _code_filter("i", code)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      i.code,
      i.stock_name,
      'ths_limit_up_review_items',
      CONCAT('ths_limit_up_review_items:', DATE_FORMAT(i.trade_date, '%Y-%m-%d'), ':', i.code, ':', i.theme_name),
      'explicit',
      'limit_up_review',
      COALESCE(NULLIF(i.theme_name, ''), 'limit_up'),
      CONCAT(IF(i.trade_date=CAST({day} AS DATE), '当日涨停复盘', '上一交易日涨停复盘'), ' / ', i.theme_name),
      LEFT(CONCAT_WS('；',
        i.reason,
        IF(i.limit_up_days > 1, CONCAT(i.limit_up_days, '连板'), NULL),
        IF(i.seal_amount IS NOT NULL, CONCAT('封单', ROUND(i.seal_amount / 100000000, 2), '亿'), NULL)
      ), 600),
      i.trade_date,
      CASE WHEN i.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 1 DAY) THEN 'active' ELSE 'watch' END,
      LEAST(95, 55 + i.limit_up_days * 10 + IF(COALESCE(i.seal_amount, 0) >= 100000000, 10, 0)),
      'limit_up_review_recent',
      '',
      'theme_confirmation',
      'post_close_confirm',
      CASE WHEN i.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 1 DAY) THEN 'secondary' ELSE 'background' END,
      i.trade_date,
      DATE_ADD(i.trade_date, INTERVAL 2 DAY),
      JSON_OBJECT(
        'theme_name', i.theme_name,
        'limit_up_days', i.limit_up_days,
        'seal_amount', i.seal_amount,
        'status', i.status,
        'source', i.source
      )
    FROM ths_limit_up_review_items i
    WHERE i.trade_date <= {day}
      AND i.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 7 DAY)
      AND i.code <> ''
      {code_filter}
    ON DUPLICATE KEY UPDATE
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def _theme_background_sql(trade_date: str, code: str = "") -> str:
    code_filter = _code_filter("r", code)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    FROM (
      SELECT
        y.*,
        ROW_NUMBER() OVER (
          PARTITION BY y.code
          ORDER BY y.valid_score DESC, JSON_EXTRACT(y.payload, '$.priority') DESC, y.fact_date DESC
        ) AS rn
      FROM (
        SELECT
          CAST({day} AS DATE) AS trade_date,
          r.code,
          r.stock_name,
          'stock_theme_reason_bank' AS source_table,
          CONCAT('stock_theme_reason_bank:', r.id) AS source_key,
          'explicit' AS source_confidence,
          'theme_reason' AS fact_type,
          COALESCE(NULLIF(r.anchor_name, ''), r.theme_name) AS fact_subtype,
          CONCAT('题材解释 / ', COALESCE(NULLIF(r.anchor_name, ''), r.theme_name)) AS fact_title,
          LEFT(r.reason_text, 600) AS fact_body,
          r.source_date AS fact_date,
          'watch' AS valid_status,
          r.confidence AS valid_score,
          'theme_reason_background' AS valid_reason,
          '' AS invalid_reason,
          'theme' AS evidence_role,
          'background_fact' AS evidence_group,
          'background' AS display_level,
          r.source_date AS valid_from,
          DATE_ADD(CAST({day} AS DATE), INTERVAL 30 DAY) AS valid_until,
          JSON_OBJECT('anchor_name', r.anchor_name, 'theme_name', r.theme_name, 'source', r.source, 'priority', r.priority) AS payload
        FROM stock_theme_reason_bank r
        WHERE r.status='active'
          AND r.code <> ''
          {code_filter}
      ) y
    ) x
    WHERE rn <= 3
    ON DUPLICATE KEY UPDATE
      fact_body=VALUES(fact_body),
      valid_score=VALUES(valid_score),
      evidence_group=VALUES(evidence_group),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def fetch_effective_fact_items(config: MySqlConfig, trade_date: str, code: str, limit: int = 12) -> list[dict[str, Any]]:
    ensure_effective_facts_table(config)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT
              source_table,
              source_key,
              fact_type,
              fact_subtype,
              fact_title,
              COALESCE(fact_body, ''),
              COALESCE(DATE_FORMAT(fact_date, '%Y-%m-%d'), ''),
              valid_status,
              valid_score,
              evidence_role,
              evidence_group,
              display_level,
              COALESCE(payload, JSON_OBJECT()),
              DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s')
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              AND code={sql_string(code)}
              AND display_level IN ('primary','secondary','background')
            ORDER BY
              FIELD(evidence_group, 'current_effective', 'post_close_confirm', 'background_fact', 'historical_tag', 'hidden'),
              FIELD(display_level, 'primary', 'secondary', 'background'),
              FIELD(evidence_role, 'hard_catalyst', 'funds', 'strength', 'theme_confirmation', 'theme'),
              valid_score DESC,
              fact_date DESC
            LIMIT {int(limit)};
            """,
            batch=True,
            raw=True,
        )
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 14:
            continue
        payload: Any = row[12]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        out.append(
            {
                "source_table": row[0],
                "source_key": row[1],
                "fact_type": row[2],
                "fact_subtype": row[3],
                "title": row[4],
                "body": row[5],
                "fact_date": row[6],
                "valid_status": row[7],
                "valid_score": row[8],
                "evidence_role": row[9],
                "evidence_group": row[10],
                "display_level": row[11],
                "payload": payload if isinstance(payload, (dict, list)) else {},
                "updated_at": row[13],
            }
        )
    return out


__all__ = [
    "build_effective_facts",
    "clear_effective_facts",
    "enqueue_effective_facts_dirty",
    "ensure_effective_facts_table",
    "fetch_effective_facts_dirty",
    "fetch_effective_fact_items",
    "mark_effective_facts_dirty",
]
