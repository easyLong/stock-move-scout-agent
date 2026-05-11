from __future__ import annotations

from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_string
from stock_move_scout.evidence.effective_facts import (
    build_effective_facts,
    enqueue_effective_facts_dirty,
    fetch_effective_facts_dirty,
    mark_effective_facts_dirty,
    ensure_effective_facts_table,
)


def ensure_root_evidence_cache_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_root_evidence_cache (
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      items JSON NOT NULL,
      root_count INT NOT NULL DEFAULT 0,
      latest_source_updated_at DATETIME(3) NULL,
      generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      PRIMARY KEY (trade_date, code),
      KEY idx_root_evidence_cache_code_day (code, trade_date),
      KEY idx_root_evidence_cache_generated (generated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_root_evidence_cache_dirty_queue (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
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
      UNIQUE KEY uk_root_evidence_dirty (trade_date, code, reason),
      KEY idx_root_evidence_dirty_status (status, priority, created_at),
      KEY idx_root_evidence_dirty_code (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def latest_root_evidence_trade_date(config: MySqlConfig) -> str:
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
        return ""


def root_evidence_cache_exists(config: MySqlConfig, trade_date: str) -> bool:
    sql = f"""
    SELECT COUNT(*)
    FROM stock_root_evidence_cache
    WHERE trade_date={sql_string(trade_date)};
    """
    try:
        return int((run_mysql(config, sql, batch=True, raw=True) or "0").splitlines()[-1].strip() or "0") > 0
    except Exception:
        return False


def root_evidence_cache_code_exists(config: MySqlConfig, trade_date: str, code: str) -> bool:
    sql = f"""
    SELECT COUNT(*)
    FROM stock_root_evidence_cache
    WHERE trade_date={sql_string(trade_date)}
      AND code={sql_string(code)};
    """
    try:
        return int((run_mysql(config, sql, batch=True, raw=True) or "0").splitlines()[-1].strip() or "0") > 0
    except Exception:
        return False


def enqueue_root_evidence_cache_dirty(
    config: MySqlConfig,
    *,
    trade_date: str,
    code: str,
    stock_name: str = "",
    reason: str = "stock_ths_root_items_updated",
    changed_sources: list[str] | None = None,
    priority: int = 35,
    enqueue_effective: bool = True,
) -> None:
    ensure_root_evidence_cache_table(config)
    code = str(code or "").strip()
    if not code:
        return
    sql = f"""
    INSERT INTO stock_root_evidence_cache_dirty_queue(
      trade_date, code, stock_name, reason, changed_sources, priority, status
    ) VALUES (
      {sql_string(trade_date)},
      {sql_string(code)},
      {sql_string(stock_name)},
      {sql_string(reason)},
      {sql_json(["stock_effective_facts"])},
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
    if enqueue_effective:
        enqueue_effective_facts_dirty(
            config,
            trade_date=trade_date,
            code=code,
            stock_name=stock_name,
            reason=reason,
            changed_sources=changed_sources or ["stock_ths_root_items"],
            priority=priority,
        )


def enqueue_root_evidence_cache_dirty_many(
    config: MySqlConfig,
    trade_date: str,
    rows: list[dict[str, Any]],
    *,
    reason: str = "stock_ths_root_items_updated",
    priority: int = 35,
) -> int:
    ensure_root_evidence_cache_table(config)
    seen: set[str] = set()
    count = 0
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        enqueue_root_evidence_cache_dirty(
            config,
            trade_date=trade_date,
            code=code,
            stock_name=str(row.get("stock_name") or row.get("name") or "").strip(),
            reason=reason,
            priority=priority,
        )
        count += 1
    return count


def fetch_root_evidence_cache_dirty(config: MySqlConfig, trade_date: str, limit: int, code: str = "") -> list[dict[str, str]]:
    ensure_root_evidence_cache_table(config)
    code_filter = f"AND code={sql_string(code)}" if code else ""
    sql = f"""
    SELECT id, code, stock_name
    FROM stock_root_evidence_cache_dirty_queue
    WHERE trade_date={sql_string(trade_date)}
      AND (
        status='pending'
        OR (status='running' AND locked_at < DATE_SUB(NOW(3), INTERVAL 5 MINUTE))
      )
      {code_filter}
    ORDER BY priority ASC, created_at ASC
    LIMIT {int(limit)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    out: list[dict[str, str]] = []
    for row in rows:
        if len(row) >= 3:
            out.append({"dirty_id": row[0], "code": row[1], "stock_name": row[2]})
    ids = [str(item["dirty_id"]) for item in out if str(item.get("dirty_id", "")).isdigit()]
    if ids:
        run_mysql(
            config,
            f"""
            UPDATE stock_root_evidence_cache_dirty_queue
            SET status='running',
                locked_at=CURRENT_TIMESTAMP(3),
                updated_at=CURRENT_TIMESTAMP(3)
            WHERE id IN ({",".join(ids)})
              AND status IN ('pending','running');
            """,
        )
    return out


def mark_root_evidence_cache_dirty(config: MySqlConfig, dirty_id: str, status: str, error: str = "") -> None:
    if not dirty_id:
        return
    sql = f"""
    UPDATE stock_root_evidence_cache_dirty_queue
    SET status={sql_string(status)},
        finished_at=IF({sql_string(status)} IN ('done','failed','ignored'), CURRENT_TIMESTAMP(3), finished_at),
        last_error={sql_string(error[:1000])},
        attempt_count=attempt_count + IF({sql_string(status)}='failed', 1, 0),
        updated_at=CURRENT_TIMESTAMP(3)
    WHERE id={int(dirty_id)};
    """
    run_mysql(config, sql)


def _code_filter_sql(codes: list[str] | None) -> str:
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    if not clean_codes:
        return ""
    return f"AND i.code IN ({','.join(sql_string(code) for code in clean_codes)})"


def _fact_code_filter_sql(codes: list[str] | None) -> str:
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    if not clean_codes:
        return ""
    return f"AND f.code IN ({','.join(sql_string(code) for code in clean_codes)})"


def refresh_root_evidence_cache_from_effective_facts(
    config: MySqlConfig,
    trade_date: str,
    codes: list[str] | None = None,
) -> dict[str, int]:
    ensure_root_evidence_cache_table(config)
    ensure_effective_facts_table(config)
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    day = sql_string(trade_date)
    code_filter = _fact_code_filter_sql(clean_codes)
    sql = f"""
    SET SESSION group_concat_max_len=65536;
    INSERT INTO stock_root_evidence_cache(
      trade_date, code, items, root_count, latest_source_updated_at, generated_at, updated_at
    )
    WITH eligible_codes AS (
      SELECT DISTINCT code
      FROM stock_effective_facts
      WHERE trade_date=CAST({day} AS DATE)
        AND evidence_group IN ('current_effective', 'post_close_confirm')
        AND display_level IN ('primary', 'secondary')
        AND valid_status IN ('active', 'watch')
    ),
    ranked AS (
      SELECT
        f.*,
        ROW_NUMBER() OVER (
          PARTITION BY f.code, f.evidence_role
          ORDER BY
            FIELD(f.evidence_group, 'current_effective', 'post_close_confirm', 'background_fact', 'historical_tag', 'hidden'),
            FIELD(f.display_level, 'primary', 'secondary', 'background', 'hidden'),
            FIELD(f.valid_status, 'active', 'watch', 'historical', 'expired', 'invalid'),
            f.valid_score DESC,
            f.fact_date DESC,
            f.updated_at DESC,
            f.id DESC
        ) AS role_rn,
        ROW_NUMBER() OVER (
          PARTITION BY f.code
          ORDER BY
            FIELD(f.evidence_group, 'current_effective', 'post_close_confirm', 'background_fact', 'historical_tag', 'hidden'),
            FIELD(f.display_level, 'primary', 'secondary', 'background', 'hidden'),
            FIELD(f.evidence_role, 'hard_catalyst', 'funds', 'strength', 'theme_confirmation', 'theme'),
            FIELD(f.valid_status, 'active', 'watch', 'historical', 'expired', 'invalid'),
            f.valid_score DESC,
            f.fact_date DESC,
            f.updated_at DESC,
            f.id DESC
        ) AS all_rn
      FROM stock_effective_facts f
      JOIN eligible_codes ec ON ec.code=f.code
      WHERE f.trade_date=CAST({day} AS DATE)
        AND f.evidence_group IN ('current_effective', 'post_close_confirm', 'background_fact', 'historical_tag')
        AND f.display_level IN ('primary', 'secondary', 'background')
        AND f.valid_status IN ('active', 'watch', 'historical', 'expired')
        {code_filter}
    ),
    selected AS (
      SELECT *
      FROM ranked
      WHERE all_rn <= 10
        AND role_rn <= CASE
          WHEN evidence_group='historical_tag' THEN 1
          WHEN evidence_role='hard_catalyst' THEN 2
          WHEN evidence_role='theme' THEN 1
          ELSE 1
        END
    ),
    packed AS (
      SELECT
        code,
        MAX(updated_at) AS latest_source_updated_at,
        COUNT(*) AS root_count,
        CONCAT('[', GROUP_CONCAT(
          JSON_OBJECT(
            'layer', CASE
              WHEN evidence_role IN ('hard_catalyst', 'theme') THEN 'async'
              ELSE 'async'
            END,
            'label', CASE evidence_group
              WHEN 'historical_tag' THEN '历史标签'
              WHEN 'background_fact' THEN '题材背景'
              ELSE CASE
              WHEN valid_status IN ('expired', 'historical') THEN '历史标签'
              ELSE CASE evidence_role
              WHEN 'hard_catalyst' THEN '当前硬催化'
              WHEN 'funds' THEN '龙虎榜席位'
              WHEN 'strength' THEN '区间领头'
              WHEN 'theme_confirmation' THEN '涨停复盘'
              WHEN 'theme' THEN '题材背景'
              ELSE '有效事实'
              END
              END
            END,
            'type', CASE evidence_group
              WHEN 'historical_tag' THEN 'event'
              WHEN 'background_fact' THEN 'theme'
              ELSE CASE
              WHEN valid_status IN ('expired', 'historical') THEN 'event'
              ELSE CASE evidence_role
              WHEN 'hard_catalyst' THEN 'announcement'
              WHEN 'funds' THEN 'lhb'
              WHEN 'strength' THEN 'period'
              WHEN 'theme_confirmation' THEN 'theme'
              WHEN 'theme' THEN 'theme'
              ELSE 'event'
              END
              END
            END,
            'source', CASE evidence_group
              WHEN 'historical_tag' THEN '历史标签'
              ELSE CASE
              WHEN valid_status IN ('expired', 'historical') THEN '历史标签'
              ELSE CASE source_table
              WHEN 'stock_announcement_effects' THEN '公告有效层'
              WHEN 'stock_lhb_seat_evidence' THEN '龙虎榜席位'
              WHEN 'stock_period_rankings' THEN '问财区间排名'
              WHEN 'ths_limit_up_review_items' THEN '涨停复盘'
              WHEN 'stock_theme_reason_bank' THEN '题材理由库'
              ELSE source_table
              END
              END
            END,
            'source_table', source_table,
            'source_key', source_key,
            'source_confidence', source_confidence,
            'source_generation', CASE
              WHEN source_table IN ('stock_lhb_seat_evidence', 'stock_period_rankings', 'ths_limit_up_review_items') THEN 'after_close'
              ELSE 'precomputed'
            END,
            'availability', CASE evidence_group
              WHEN 'current_effective' THEN 'cached_readable'
              WHEN 'post_close_confirm' THEN 'after_close_confirm'
              WHEN 'background_fact' THEN 'cached_readable'
              WHEN 'historical_tag' THEN 'cached_readable'
              ELSE 'cached_readable'
            END,
            'freshness', CASE
              WHEN fact_date=CAST({day} AS DATE) THEN 'today_update'
              WHEN fact_date=(
                SELECT MAX(prev_day)
                FROM (
                  SELECT DATE(scanned_at) AS prev_day FROM scan_runs WHERE accepted=1 AND DATE(scanned_at) < CAST({day} AS DATE)
                  UNION ALL
                  SELECT DATE(ended_at) AS prev_day FROM windows WHERE status='done' AND DATE(ended_at) < CAST({day} AS DATE)
                  UNION ALL
                  SELECT DATE(trade_date) AS prev_day FROM stock_period_rankings WHERE trade_date < CAST({day} AS DATE)
                  UNION ALL
                  SELECT DATE(trade_date) AS prev_day FROM stock_lhb_seat_evidence WHERE trade_date < CAST({day} AS DATE)
                ) prev_days
                WHERE WEEKDAY(prev_day) < 5
              ) THEN 'prev_trade_day'
              ELSE 'historical'
            END,
            'data_date', CASE
              WHEN evidence_group='background_fact' THEN ''
              ELSE COALESCE(DATE_FORMAT(fact_date, '%Y-%m-%d'), DATE_FORMAT(trade_date, '%Y-%m-%d'))
            END,
            'updated_at', DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s'),
            'evidence_date', CASE
              WHEN evidence_group='background_fact' THEN ''
              ELSE COALESCE(DATE_FORMAT(fact_date, '%Y-%m-%d'), '')
            END,
            'body', LEFT(COALESCE(NULLIF(fact_body, ''), fact_title), 420),
            'priority', CASE evidence_group
              WHEN 'historical_tag' THEN 95
              WHEN 'background_fact' THEN 70
              ELSE CASE
              WHEN valid_status IN ('expired', 'historical') THEN 95
              ELSE CASE evidence_role
              WHEN 'hard_catalyst' THEN 5
              WHEN 'funds' THEN 12
              WHEN 'strength' THEN 14
              WHEN 'theme_confirmation' THEN 24
              WHEN 'theme' THEN 65
              ELSE 80
              END
              END
            END,
            'evidence_group', evidence_group,
            'display_level', display_level,
            'valid_status', valid_status,
            'valid_score', valid_score,
            'valid_reason', valid_reason,
            'payload', JSON_MERGE_PATCH(
              COALESCE(payload, JSON_OBJECT()),
              JSON_OBJECT(
                'fact_type', fact_type,
                'fact_subtype', fact_subtype,
                'fact_title', fact_title,
                'fact_date', COALESCE(DATE_FORMAT(fact_date, '%Y-%m-%d'), ''),
                'evidence_role', evidence_role,
                'evidence_group', evidence_group,
                'valid_status', valid_status,
                'valid_score', valid_score
              )
            )
          )
          ORDER BY
            FIELD(evidence_group, 'current_effective', 'post_close_confirm', 'background_fact', 'historical_tag'),
            FIELD(display_level, 'primary', 'secondary', 'background'),
            FIELD(evidence_role, 'hard_catalyst', 'funds', 'strength', 'theme_confirmation', 'theme'),
            valid_score DESC,
            fact_date DESC
          SEPARATOR ','
        ), ']') AS items
      FROM selected
      GROUP BY code
    )
    SELECT
      CAST({day} AS DATE),
      code,
      CAST(items AS JSON),
      root_count,
      latest_source_updated_at,
      CURRENT_TIMESTAMP(3),
      CURRENT_TIMESTAMP(3)
    FROM packed
    ON DUPLICATE KEY UPDATE
      items=VALUES(items),
      root_count=VALUES(root_count),
      latest_source_updated_at=VALUES(latest_source_updated_at),
      generated_at=VALUES(generated_at),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    if clean_codes:
        refreshed = len(clean_codes)
    else:
        refreshed_rows = mysql_rows(
            run_mysql(
                config,
                f"SELECT COUNT(*) FROM stock_root_evidence_cache WHERE trade_date={sql_string(trade_date)};",
                batch=True,
                raw=True,
            )
        )
        refreshed = int(refreshed_rows[0][0]) if refreshed_rows and refreshed_rows[0] else 0
    return {"refreshed": refreshed, "deleted": 0, "mode": 1 if clean_codes else 2}


def refresh_root_evidence_cache(
    config: MySqlConfig,
    trade_date: str,
    *,
    codes: list[str] | None = None,
    force: bool = False,
) -> dict[str, int]:
    ensure_root_evidence_cache_table(config)
    ensure_effective_facts_table(config)
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    if not force and not clean_codes and root_evidence_cache_exists(config, trade_date):
        return {"refreshed": 0, "deleted": 0, "mode": 0}
    if clean_codes:
        run_mysql(
            config,
            f"""
            DELETE FROM stock_root_evidence_cache
            WHERE trade_date={sql_string(trade_date)}
              AND code IN ({','.join(sql_string(code) for code in clean_codes)});
            """,
        )
    elif force:
        run_mysql(
            config,
            f"""
            DELETE FROM stock_root_evidence_cache
            WHERE trade_date={sql_string(trade_date)};
            """,
        )
    effective_result = refresh_root_evidence_cache_from_effective_facts(config, trade_date, clean_codes)
    if clean_codes:
        effective_result["deleted"] = len(clean_codes)
    return effective_result
    day = sql_string(trade_date)
    code_filter = _code_filter_sql(clean_codes)
    sql = f"""
    SET SESSION group_concat_max_len=16384;
    INSERT INTO stock_root_evidence_cache(
      trade_date, code, items, root_count, latest_source_updated_at, generated_at, updated_at
    )
    WITH
    root_evidence_scored AS (
      SELECT
        i.*,
        COALESCE(ae.effect_status, '') AS announcement_effect_status,
        COALESCE(ae.faded_reason, '') AS announcement_effect_reason,
        ae.verify_score AS announcement_verify_score,
        CASE
          WHEN i.title='业绩披露' THEN 0
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '一季报|季度报告|年报|半年报|业绩预告|业绩快报|净利润|营收|利润分配|分配预案' THEN 1
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '回购|增持' THEN 2
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '龙虎榜|龙 虎 榜' THEN 3
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '合同|订单|中标|收购|重组|资产收购|资产出售|并购' THEN 4
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '异动|股票交易异常波动' THEN 5
          WHEN i.item_kind='theme_point' THEN 6
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '新增概念|投资互动|互动易' THEN 7
          WHEN i.item_kind='announcement' THEN 8
          ELSE 8
        END AS root_priority,
        CASE
          WHEN i.title='业绩披露' OR CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '一季报|季度报告|年报|半年报|业绩预告|业绩快报|净利润|营收|利润分配|分配预案' THEN 'performance'
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '回购|增持' THEN 'buyback'
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '龙虎榜|龙 虎 榜' THEN 'lhb'
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '合同|订单|中标|收购|重组|资产收购|资产出售|并购' THEN 'deal'
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '异动|股票交易异常波动' THEN 'abnormal_move'
          WHEN i.item_kind='theme_point' THEN 'theme'
          WHEN CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '新增概念|投资互动|互动易' THEN 'concept'
          WHEN i.item_kind='announcement' THEN 'announcement'
          ELSE 'event'
        END AS root_bucket
      FROM stock_ths_root_items i
      LEFT JOIN stock_announcement_effects ae ON ae.root_item_id = i.id
      WHERE i.item_kind IN ('important_event', 'announcement', 'theme_point', 'hot_news')
        {code_filter}
        AND (i.item_date IS NULL OR i.item_date <= {day})
        AND (i.item_date IS NULL OR i.item_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 240 DAY))
        AND COALESCE(ae.effect_status, '') NOT IN ('ignored', 'faded')
        AND (
          i.item_kind IN ('theme_point', 'hot_news')
          OR (i.item_kind='announcement' AND COALESCE(ae.effect_status, '')='active')
          OR (
            i.item_kind='important_event'
            AND (
              COALESCE(ae.effect_status, '')='active'
              OR CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '龙 虎 榜|龙虎榜'
            )
          )
        )
        AND (
          i.item_kind='theme_point'
          OR CONCAT(i.title, ' ', COALESCE(i.content, '')) REGEXP '业绩披露|一季报|季度报告|年报|半年报|业绩预告|业绩快报|净利润|营收|利润分配|分配预案|回购|增持|龙虎榜|龙 虎 榜|合同|订单|中标|收购|重组|资产收购|资产出售|并购|异动|股票交易异常波动|新增概念|投资互动|互动易'
        )
    ),
    root_evidence_deduped AS (
      SELECT ranked.*
      FROM (
        SELECT
          s.*,
          ROW_NUMBER() OVER (
            PARTITION BY s.code, COALESCE(NULLIF(s.url, ''), CONCAT(s.title, ':', LEFT(COALESCE(s.content, ''), 120)), s.item_key)
            ORDER BY s.root_priority ASC, COALESCE(s.item_date, DATE('1000-01-01')) DESC, s.source_rank ASC, s.updated_at DESC
          ) AS dedupe_rn
        FROM root_evidence_scored s
      ) ranked
      WHERE ranked.dedupe_rn=1
    ),
    root_evidence_bucketed AS (
      SELECT
        d.*,
        ROW_NUMBER() OVER (
          PARTITION BY d.code, d.root_bucket
          ORDER BY d.root_priority ASC, COALESCE(d.item_date, DATE('1000-01-01')) DESC, d.source_rank ASC, d.updated_at DESC
        ) AS bucket_rn
      FROM root_evidence_deduped d
    ),
    root_evidence_ranked AS (
      SELECT
        d.*,
        ROW_NUMBER() OVER (
          PARTITION BY d.code
          ORDER BY d.root_priority ASC, COALESCE(d.item_date, DATE('1000-01-01')) DESC, d.source_rank ASC, d.updated_at DESC
        ) AS root_rn
      FROM root_evidence_bucketed d
      WHERE d.bucket_rn <= CASE WHEN d.root_bucket IN ('performance', 'theme') THEN 2 ELSE 1 END
    ),
    packed AS (
      SELECT
        code,
        MAX(updated_at) AS latest_source_updated_at,
        COUNT(*) AS root_count,
        CONCAT('[', GROUP_CONCAT(
          JSON_OBJECT(
            'layer', 'async',
            'label', CASE
              WHEN item_kind='announcement' THEN '公告'
              WHEN item_kind='theme_point' THEN '题材证据'
              ELSE '事件'
            END,
            'type', CASE
              WHEN item_kind='announcement' THEN 'announcement'
              WHEN item_kind='theme_point' THEN 'theme'
              ELSE 'event'
            END,
            'source', CASE
              WHEN item_kind='announcement' THEN '公告粗筛'
              WHEN item_kind='theme_point' THEN '题材要点'
              ELSE '重要事件'
            END,
            'source_table', 'stock_ths_root_items',
            'source_key', CONCAT('stock_ths_root_items:', id),
            'source_confidence', 'explicit',
            'source_generation', 'precomputed',
            'data_date', COALESCE(DATE_FORMAT(item_date, '%Y-%m-%d'), DATE_FORMAT(collected_at, '%Y-%m-%d')),
            'updated_at', DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s'),
            'collected_at', DATE_FORMAT(collected_at, '%Y-%m-%d %H:%i:%s'),
            'evidence_date', COALESCE(DATE_FORMAT(item_date, '%Y-%m-%d'), ''),
            'body', LEFT(
              CONCAT(
                IF(item_date IS NULL, '', CONCAT(DATE_FORMAT(item_date, '%m-%d'), ' ')),
                CASE
                  WHEN COALESCE(content, '') <> '' AND title IN ('发布公告', '业绩披露', '分配预案', '融资融券', '龙 虎 榜', '新增概念', '股票回购') THEN content
                  WHEN COALESCE(content, '') <> '' AND item_kind <> 'announcement' THEN CONCAT(title, '：', content)
                  ELSE title
                END
              ),
              360
            ),
            'priority', root_priority + 70,
            'payload', JSON_OBJECT(
              'item_kind', item_kind,
              'item_key', item_key,
              'title', title,
              'content', LEFT(COALESCE(content, ''), 600),
              'url', url,
              'source_section', source_section,
              'source_rank', source_rank
              ,'announcement_effect_status', announcement_effect_status
              ,'announcement_verify_score', announcement_verify_score
            )
          )
          ORDER BY root_rn SEPARATOR ','
        ), ']') AS items
      FROM root_evidence_ranked
      WHERE root_rn <= 6
      GROUP BY code
    )
    SELECT
      CAST({day} AS DATE),
      code,
      CAST(items AS JSON),
      root_count,
      latest_source_updated_at,
      CURRENT_TIMESTAMP(3),
      CURRENT_TIMESTAMP(3)
    FROM packed
    ON DUPLICATE KEY UPDATE
      items=VALUES(items),
      root_count=VALUES(root_count),
      latest_source_updated_at=VALUES(latest_source_updated_at),
      generated_at=VALUES(generated_at),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    refreshed = len(clean_codes)
    if not clean_codes:
        refreshed_rows = mysql_rows(
            run_mysql(
                config,
                f"SELECT COUNT(*) FROM stock_root_evidence_cache WHERE trade_date={sql_string(trade_date)};",
                batch=True,
                raw=True,
            )
        )
        refreshed = int(refreshed_rows[0][0]) if refreshed_rows and refreshed_rows[0] else 0
    return {"refreshed": refreshed, "deleted": len(clean_codes), "mode": 1 if clean_codes else 2}


def process_root_evidence_cache_dirty(config: MySqlConfig, trade_date: str, limit: int = 50, code: str = "") -> dict[str, int]:
    effective_result = process_effective_facts_dirty(config, trade_date, limit, code)
    dirty_rows = fetch_root_evidence_cache_dirty(config, trade_date, limit, code)
    refreshed = 0
    failed = 0
    for item in dirty_rows:
        try:
            refresh_root_evidence_cache(config, trade_date, codes=[item["code"]], force=True)
            mark_root_evidence_cache_dirty(config, item.get("dirty_id", ""), "done")
            refreshed += 1
        except Exception as exc:
            mark_root_evidence_cache_dirty(config, item.get("dirty_id", ""), "failed", str(exc))
            failed += 1
    return {
        "effective_dirty": effective_result.get("dirty", 0),
        "effective_rebuilt": effective_result.get("rebuilt", 0),
        "effective_failed": effective_result.get("failed", 0),
        "dirty": len(dirty_rows),
        "refreshed": refreshed,
        "failed": failed,
    }


def process_effective_facts_dirty(config: MySqlConfig, trade_date: str, limit: int = 50, code: str = "") -> dict[str, int]:
    dirty_rows = fetch_effective_facts_dirty(config, trade_date, limit, code)
    rebuilt = 0
    failed = 0
    for item in dirty_rows:
        try:
            build_effective_facts(config, trade_date, item["code"])
            enqueue_root_evidence_cache_dirty(
                config,
                trade_date=trade_date,
                code=item["code"],
                stock_name=item.get("stock_name", ""),
                reason="stock_effective_facts_updated",
                changed_sources=["stock_effective_facts"],
                priority=30,
                enqueue_effective=False,
            )
            mark_effective_facts_dirty(config, item.get("dirty_id", ""), "done")
            rebuilt += 1
        except Exception as exc:
            mark_effective_facts_dirty(config, item.get("dirty_id", ""), "failed", str(exc))
            failed += 1
    return {"dirty": len(dirty_rows), "rebuilt": rebuilt, "failed": failed}


__all__ = [
    "enqueue_root_evidence_cache_dirty",
    "enqueue_root_evidence_cache_dirty_many",
    "ensure_root_evidence_cache_table",
    "fetch_root_evidence_cache_dirty",
    "latest_root_evidence_trade_date",
    "mark_root_evidence_cache_dirty",
    "process_effective_facts_dirty",
    "process_root_evidence_cache_dirty",
    "refresh_root_evidence_cache",
    "refresh_root_evidence_cache_from_effective_facts",
    "root_evidence_cache_code_exists",
    "root_evidence_cache_exists",
]
