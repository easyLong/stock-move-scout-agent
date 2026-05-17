from __future__ import annotations

import hashlib
import json
from typing import Any

from stock_scout_mysql import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_string
from .schema import evidence_hash
from .summary import impact_summary_text, normalize_fact_first_fields


def ensure_summary_table(config: MySqlConfig) -> None:
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
    ensure_summary_column(config, "evidence_filter_summary", "TEXT NULL")
    ensure_summary_column(config, "key_facts", "JSON NULL")
    ensure_summary_column(config, "move_reason", "TEXT NULL")
    ensure_summary_column(config, "sustainability_basis", "JSON NULL")
    ensure_summary_column(config, "main_flaw", "TEXT NULL")
    ensure_summary_column(config, "missing_evidence", "JSON NULL")
    ensure_summary_column(config, "core_evidence_items", "JSON NULL")
    ensure_summary_column(config, "timeliness_label", "VARCHAR(32) NOT NULL DEFAULT 'unknown'")
    ensure_summary_column(config, "timeliness_reason", "TEXT NULL")
    ensure_summary_column(config, "final_analysis", "TEXT NULL")
    ensure_summary_column(config, "move_explanation", "TEXT NULL")
    ensure_summary_column(config, "explanation_strength", "VARCHAR(32) NOT NULL DEFAULT 'none'")
    ensure_summary_column(config, "anchor_match", "VARCHAR(32) NOT NULL DEFAULT 'weak'")
    ensure_summary_column(config, "anchor_match_reason", "TEXT NULL")
    ensure_summary_column(config, "quality_label", "VARCHAR(64) NOT NULL DEFAULT ''")
    ensure_summary_column(config, "core_support", "JSON NULL")
    ensure_summary_column(config, "counterpoints", "JSON NULL")
    ensure_summary_column(config, "final_view", "TEXT NULL")
    ensure_summary_column(config, "impact_factors", "JSON NULL")
    ensure_summary_column(config, "impact_summary_text", "TEXT NULL")


def ensure_summary_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'async_evidence_summaries'
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE async_evidence_summaries ADD COLUMN {column_name} {column_sql};")


def existing_evidence_hash(config: MySqlConfig, trade_date: str, code: str) -> str:
    sql = f"""
    SELECT evidence_hash
    FROM async_evidence_summaries
    WHERE trade_date={sql_string(trade_date)}
      AND code={sql_string(code)}
    LIMIT 1;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    return rows[0][0] if rows and rows[0] else ""


def existing_evidence_hashes(config: MySqlConfig, trade_date: str, codes: list[str]) -> dict[str, str]:
    clean_codes = sorted({str(code or "").strip() for code in codes if str(code or "").strip()})
    if not clean_codes:
        return {}
    sql = f"""
    SELECT code, evidence_hash
    FROM async_evidence_summaries
    WHERE trade_date={sql_string(trade_date)}
      AND code IN ({",".join(sql_string(code) for code in clean_codes)});
    """
    return {
        row[0]: row[1]
        for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True))
        if len(row) >= 2 and row[0]
    }


def reuse_summary_by_hash(config: MySqlConfig, payload: dict[str, Any], current_hash: str) -> bool:
    trade_date = str(payload.get("trade_date") or "")
    code = str(payload.get("code") or "")
    if not trade_date or not code or not current_hash:
        return False
    sql = f"""
    INSERT INTO async_evidence_summaries(
      trade_date, code, stock_name, evidence_hash, summary_text, key_points,
      evidence_filter_summary, key_facts, move_reason, sustainability_basis, main_flaw, missing_evidence,
      core_evidence_items, timeliness_label, timeliness_reason, final_analysis,
      move_explanation, explanation_strength, anchor_match, anchor_match_reason, quality_label, core_support, counterpoints, final_view,
      hard_catalysts, impact_factors, impact_summary_text, risks, evidence_strength, evidence_gaps, source_counts,
      source_payload, model, status, error_message, raw_json
    )
    SELECT
      {sql_string(trade_date)},
      {sql_string(code)},
      {sql_string(str(payload.get("stock_name") or ""))},
      evidence_hash,
      summary_text,
      key_points,
      evidence_filter_summary,
      key_facts,
      move_reason,
      sustainability_basis,
      main_flaw,
      missing_evidence,
      core_evidence_items,
      timeliness_label,
      timeliness_reason,
      final_analysis,
      move_explanation,
      explanation_strength,
      anchor_match,
      anchor_match_reason,
      quality_label,
      core_support,
      counterpoints,
      final_view,
      hard_catalysts,
      impact_factors,
      impact_summary_text,
      risks,
      evidence_strength,
      evidence_gaps,
      {sql_json({'current_facts': len(payload.get('current_facts', []))})},
      {sql_string(json.dumps(payload, ensure_ascii=False))},
      IF(model LIKE '%reused%', model, CONCAT(model, '+reused')),
      status,
      '',
      raw_json
    FROM async_evidence_summaries
    WHERE code={sql_string(code)}
      AND evidence_hash={sql_string(current_hash)}
      AND trade_date < {sql_string(trade_date)}
      AND status IN ('ready', 'fallback')
    ORDER BY trade_date DESC, updated_at DESC
    LIMIT 1
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      evidence_hash=VALUES(evidence_hash),
      summary_text=VALUES(summary_text),
      key_points=VALUES(key_points),
      evidence_filter_summary=VALUES(evidence_filter_summary),
      key_facts=VALUES(key_facts),
      move_reason=VALUES(move_reason),
      sustainability_basis=VALUES(sustainability_basis),
      main_flaw=VALUES(main_flaw),
      missing_evidence=VALUES(missing_evidence),
      core_evidence_items=VALUES(core_evidence_items),
      timeliness_label=VALUES(timeliness_label),
      timeliness_reason=VALUES(timeliness_reason),
      final_analysis=VALUES(final_analysis),
      move_explanation=VALUES(move_explanation),
      explanation_strength=VALUES(explanation_strength),
      anchor_match=VALUES(anchor_match),
      anchor_match_reason=VALUES(anchor_match_reason),
      quality_label=VALUES(quality_label),
      core_support=VALUES(core_support),
      counterpoints=VALUES(counterpoints),
      final_view=VALUES(final_view),
      hard_catalysts=VALUES(hard_catalysts),
      impact_factors=VALUES(impact_factors),
      impact_summary_text=VALUES(impact_summary_text),
      risks=VALUES(risks),
      evidence_strength=VALUES(evidence_strength),
      evidence_gaps=VALUES(evidence_gaps),
      source_counts=VALUES(source_counts),
      source_payload=VALUES(source_payload),
      model=VALUES(model),
      status=VALUES(status),
      error_message=VALUES(error_message),
      raw_json=VALUES(raw_json),
      summarized_at=CURRENT_TIMESTAMP(3),
      updated_at=CURRENT_TIMESTAMP(3);

    SELECT ROW_COUNT();
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    try:
        return bool(rows and int(float(rows[-1][0] or 0)) > 0)
    except Exception:
        return False


def delete_summary(config: MySqlConfig, trade_date: str, code: str) -> None:
    sql = f"""
    DELETE FROM async_evidence_summaries
    WHERE trade_date={sql_string(trade_date)}
      AND code={sql_string(code)};
    """
    run_mysql(config, sql)


def delete_summaries_without_current_facts(config: MySqlConfig, trade_date: str, codes: list[str]) -> int:
    clean_codes = sorted({str(code or "").strip() for code in codes if str(code or "").strip()})
    if not clean_codes:
        return 0
    codes_sql = ",".join(sql_string(code) for code in clean_codes)
    sql = f"""
    DELETE aes
    FROM async_evidence_summaries aes
    LEFT JOIN stock_effective_facts ef
      ON ef.trade_date=aes.trade_date
     AND ef.code=aes.code
     AND ef.evidence_group='current_effective'
     AND ef.valid_status='active'
     AND ef.display_level IN ('primary','secondary')
    WHERE aes.trade_date={sql_string(trade_date)}
      AND aes.code IN ({codes_sql})
      AND ef.code IS NULL;
    SELECT ROW_COUNT();
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    try:
        return int(float(rows[-1][0] or 0)) if rows and rows[-1] else 0
    except Exception:
        return 0


def ensure_incremental_tables(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS evidence_source_fingerprints (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      source_hash CHAR(64) NOT NULL,
      component_hashes JSON NULL,
      source_counts JSON NULL,
      last_changed_at DATETIME(3) NULL,
      latest_source_at DATETIME NULL,
      source_payload MEDIUMTEXT NULL,
      first_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      last_seen_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_evidence_source_fp (trade_date, code),
      KEY idx_evidence_source_hash (source_hash),
      KEY idx_evidence_source_latest (latest_source_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS evidence_analysis_dirty_queue (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      source_hash CHAR(64) NOT NULL,
      previous_source_hash CHAR(64) NOT NULL DEFAULT '',
      reason VARCHAR(255) NOT NULL DEFAULT '',
      changed_sources JSON NULL,
      impact_hint VARCHAR(255) NOT NULL DEFAULT '',
      change_payload JSON NULL,
      priority INT NOT NULL DEFAULT 50,
      status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
      attempt_count INT NOT NULL DEFAULT 0,
      locked_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      last_error TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_evidence_dirty (trade_date, code, source_hash),
      KEY idx_evidence_dirty_status (status, priority, created_at),
      KEY idx_evidence_dirty_code (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    """
    run_mysql(config, sql)
    ensure_incremental_column(config, "evidence_source_fingerprints", "component_hashes", "JSON NULL")
    ensure_incremental_column(config, "evidence_source_fingerprints", "last_changed_at", "DATETIME(3) NULL")
    ensure_incremental_column(config, "evidence_analysis_dirty_queue", "previous_source_hash", "CHAR(64) NOT NULL DEFAULT ''")
    ensure_incremental_column(config, "evidence_analysis_dirty_queue", "changed_sources", "JSON NULL")
    ensure_incremental_column(config, "evidence_analysis_dirty_queue", "impact_hint", "VARCHAR(255) NOT NULL DEFAULT ''")


def ensure_incremental_column(config: MySqlConfig, table_name: str, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = {sql_string(table_name)}
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql};")


def source_hash(payload: dict[str, Any]) -> str:
    slim = {
        "trade_date": payload.get("trade_date"),
        "code": payload.get("code"),
        "current_facts": payload.get("current_facts"),
    }
    raw = json.dumps(slim, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def source_component_hashes(payload: dict[str, Any]) -> dict[str, str]:
    components = {
        "current_facts": payload.get("current_facts") or [],
    }
    return {name: stable_hash(value) for name, value in components.items()}


def payload_source_counts(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "current_facts": len(payload.get("current_facts", [])),
    }


def existing_source_state(config: MySqlConfig, trade_date: str, code: str) -> dict[str, Any]:
    sql = f"""
    SELECT source_hash, COALESCE(component_hashes, '{{}}'), COALESCE(source_payload, '')
    FROM evidence_source_fingerprints
    WHERE trade_date={sql_string(trade_date)}
      AND code={sql_string(code)}
    LIMIT 1;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    if not rows or len(rows[0]) < 3:
        return {"source_hash": "", "component_hashes": {}, "source_payload": {}}
    component_hashes: dict[str, str] = {}
    source_payload: dict[str, Any] = {}
    try:
        component_hashes = json.loads(rows[0][1] or "{}")
    except Exception:
        component_hashes = {}
    try:
        source_payload = json.loads(rows[0][2] or "{}")
    except Exception:
        source_payload = {}
    return {"source_hash": rows[0][0], "component_hashes": component_hashes, "source_payload": source_payload}


def changed_source_names(old_components: dict[str, str], new_components: dict[str, str]) -> list[str]:
    names = sorted(new_components)
    return [name for name in names if old_components.get(name) != new_components.get(name)]


def source_impact_priority(changed_sources: list[str], payload: dict[str, Any]) -> tuple[int, str]:
    changed = set(changed_sources)
    if "current_facts" in changed:
        return 20, "current_facts_updated"
    return 55, "current_facts_updated"


def record_source_fingerprint(config: MySqlConfig, payload: dict[str, Any]) -> tuple[str, bool, dict[str, Any]]:
    h = source_hash(payload)
    old_state = existing_source_state(config, payload["trade_date"], payload["code"])
    old = old_state.get("source_hash", "")
    components = source_component_hashes(payload)
    changed_sources = changed_source_names(old_state.get("component_hashes", {}) or {}, components)
    counts = payload_source_counts(payload)
    raw_payload = json.dumps(payload, ensure_ascii=False)
    sql = f"""
    INSERT INTO evidence_source_fingerprints(
      trade_date, code, stock_name, source_hash, component_hashes, source_counts, last_changed_at, source_payload
    ) VALUES (
      {sql_string(payload['trade_date'])},
      {sql_string(payload['code'])},
      {sql_string(payload['stock_name'])},
      {sql_string(h)},
      {sql_json(components)},
      {sql_json(counts)},
      CURRENT_TIMESTAMP(3),
      {sql_string(raw_payload)}
    )
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      source_hash=VALUES(source_hash),
      component_hashes=VALUES(component_hashes),
      source_counts=VALUES(source_counts),
      last_changed_at=IF(source_hash <> VALUES(source_hash), CURRENT_TIMESTAMP(3), last_changed_at),
      source_payload=VALUES(source_payload),
      last_seen_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    return h, old != h, {"previous_hash": old, "changed_sources": changed_sources, "component_hashes": components}


def enqueue_dirty_analysis(
    config: MySqlConfig,
    payload: dict[str, Any],
    h: str,
    reason: str,
    priority: int = 40,
    previous_hash: str = "",
    changed_sources: list[str] | None = None,
    impact_hint: str = "",
) -> None:
    changed_sources = changed_sources or []
    change_payload = {
        "source_counts": payload_source_counts(payload),
        "current_anchors": (payload.get("market_context") or {}).get("current_anchors", []),
        "changed_sources": changed_sources,
        "impact_hint": impact_hint,
    }
    sql = f"""
    INSERT INTO evidence_analysis_dirty_queue(
      trade_date, code, stock_name, source_hash, previous_source_hash, reason, changed_sources, impact_hint, change_payload, priority, status
    ) VALUES (
      {sql_string(payload['trade_date'])},
      {sql_string(payload['code'])},
      {sql_string(payload['stock_name'])},
      {sql_string(h)},
      {sql_string(previous_hash)},
      {sql_string(reason)},
      {sql_json(changed_sources)},
      {sql_string(impact_hint)},
      {sql_json(change_payload)},
      {int(priority)},
      'pending'
    )
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      previous_source_hash=VALUES(previous_source_hash),
      reason=VALUES(reason),
      changed_sources=VALUES(changed_sources),
      impact_hint=VALUES(impact_hint),
      change_payload=VALUES(change_payload),
      priority=LEAST(priority, VALUES(priority)),
      status=IF(status IN ('done','ignored'), 'pending', status),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)


def _dirty_code_filter(code: str = "", codes: list[str] | None = None) -> str:
    if code:
        return f"AND code={sql_string(code)}"
    clean_codes = sorted({str(item or "").strip() for item in (codes or []) if str(item or "").strip()})
    if not clean_codes:
        return "AND 1=0" if codes is not None else ""
    return f"AND code IN ({','.join(sql_string(item) for item in clean_codes)})"


def fetch_dirty_candidates(
    config: MySqlConfig,
    trade_date: str,
    limit: int,
    code: str = "",
    codes: list[str] | None = None,
) -> list[dict[str, str]]:
    code_filter = _dirty_code_filter(code, codes)
    sql = f"""
    SELECT id, code, stock_name, source_hash
    FROM evidence_analysis_dirty_queue
    WHERE trade_date={sql_string(trade_date)}
      AND (
        status='pending'
        OR (status='running' AND locked_at < DATE_SUB(NOW(3), INTERVAL 20 MINUTE))
      )
      {code_filter}
    ORDER BY priority ASC, created_at ASC
    LIMIT {int(limit)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    out: list[dict[str, str]] = []
    for row in rows:
        if len(row) >= 4:
            out.append({"dirty_id": row[0], "code": row[1], "stock_name": row[2], "source_hash": row[3]})
    ids = [str(item["dirty_id"]) for item in out if str(item.get("dirty_id", "")).isdigit()]
    if ids:
        run_mysql(
            config,
            f"""
            UPDATE evidence_analysis_dirty_queue
            SET status='running',
                locked_at=CURRENT_TIMESTAMP(3),
                updated_at=CURRENT_TIMESTAMP(3)
            WHERE id IN ({",".join(ids)})
              AND status IN ('pending','running');
            """,
        )
    return out


def mark_dirty(config: MySqlConfig, dirty_id: str, status: str, error: str = "") -> None:
    if not dirty_id:
        return
    sql = f"""
    UPDATE evidence_analysis_dirty_queue
    SET status={sql_string(status)},
        finished_at=IF({sql_string(status)} IN ('done','failed','ignored'), CURRENT_TIMESTAMP(3), finished_at),
        last_error={sql_string(error[:1000])},
        attempt_count=attempt_count + IF({sql_string(status)}='failed', 1, 0),
        updated_at=CURRENT_TIMESTAMP(3)
    WHERE id={int(dirty_id)};
    """
    run_mysql(config, sql)


def write_summary(
    config: MySqlConfig,
    payload: dict[str, Any],
    summary: dict[str, Any],
    model: str,
    status: str,
    error: str = "",
) -> None:
    summary = normalize_fact_first_fields(summary)
    h = evidence_hash(payload)
    impact_text = impact_summary_text(summary)
    sql = f"""
    INSERT INTO async_evidence_summaries(
      trade_date, code, stock_name, evidence_hash, summary_text, key_points,
      evidence_filter_summary, key_facts, move_reason, sustainability_basis, main_flaw, missing_evidence,
      core_evidence_items, timeliness_label, timeliness_reason, final_analysis,
      move_explanation, explanation_strength, anchor_match, anchor_match_reason, quality_label, core_support, counterpoints, final_view,
      hard_catalysts, impact_factors, impact_summary_text, risks, evidence_strength, evidence_gaps, source_counts,
      source_payload, model, status, error_message, raw_json
    ) VALUES (
      {sql_string(payload['trade_date'])},
      {sql_string(payload['code'])},
      {sql_string(payload['stock_name'])},
      {sql_string(h)},
      {sql_string(summary.get('summary_text', ''))},
      {sql_json(summary.get('key_points', []))},
      {sql_string(summary.get('evidence_filter_summary', ''))},
      {sql_json(summary.get('key_facts', []))},
      {sql_string(summary.get('move_reason', ''))},
      {sql_json(summary.get('sustainability_basis', []))},
      {sql_string(summary.get('main_flaw', ''))},
      {sql_json(summary.get('missing_evidence', []))},
      {sql_json(summary.get('core_evidence_items', []))},
      {sql_string(summary.get('timeliness_label', 'unknown'))},
      {sql_string(summary.get('timeliness_reason', ''))},
      {sql_string(summary.get('final_analysis', ''))},
      {sql_string(summary.get('move_explanation', ''))},
      {sql_string(summary.get('explanation_strength', 'none'))},
      {sql_string(summary.get('anchor_match', 'weak'))},
      {sql_string(summary.get('anchor_match_reason', ''))},
      {sql_string(summary.get('quality_label', ''))},
      {sql_json(summary.get('core_support', []))},
      {sql_json(summary.get('counterpoints', []))},
      {sql_string(summary.get('final_view', ''))},
      {sql_json(summary.get('hard_catalysts', []))},
      {sql_json(summary.get('impact_factors', []))},
      {sql_string(impact_text)},
      {sql_json(summary.get('risks', []))},
      {sql_string(summary.get('evidence_strength', 'pending'))},
      {sql_json(summary.get('evidence_gaps', []))},
      {sql_json({
          'current_facts': len(payload.get('current_facts', [])),
      })},
      {sql_string(json.dumps(payload, ensure_ascii=False))},
      {sql_string(model)},
      {sql_string(status)},
      {sql_string(error[:2000])},
      {sql_json(summary)}
    )
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      evidence_hash=VALUES(evidence_hash),
      summary_text=VALUES(summary_text),
      key_points=VALUES(key_points),
      evidence_filter_summary=VALUES(evidence_filter_summary),
      key_facts=VALUES(key_facts),
      move_reason=VALUES(move_reason),
      sustainability_basis=VALUES(sustainability_basis),
      main_flaw=VALUES(main_flaw),
      missing_evidence=VALUES(missing_evidence),
      core_evidence_items=VALUES(core_evidence_items),
      timeliness_label=VALUES(timeliness_label),
      timeliness_reason=VALUES(timeliness_reason),
      final_analysis=VALUES(final_analysis),
      move_explanation=VALUES(move_explanation),
      explanation_strength=VALUES(explanation_strength),
      anchor_match=VALUES(anchor_match),
      anchor_match_reason=VALUES(anchor_match_reason),
      quality_label=VALUES(quality_label),
      core_support=VALUES(core_support),
      counterpoints=VALUES(counterpoints),
      final_view=VALUES(final_view),
      hard_catalysts=VALUES(hard_catalysts),
      impact_factors=VALUES(impact_factors),
      impact_summary_text=VALUES(impact_summary_text),
      risks=VALUES(risks),
      evidence_strength=VALUES(evidence_strength),
      evidence_gaps=VALUES(evidence_gaps),
      source_counts=VALUES(source_counts),
      source_payload=VALUES(source_payload),
      model=VALUES(model),
      status=VALUES(status),
      error_message=VALUES(error_message),
      raw_json=VALUES(raw_json),
      summarized_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
