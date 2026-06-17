from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_number, sql_string
from stock_move_scout.sources.kpl_plate_strength import KPL_PLATE_STRENGTH_HIS_URL, KPL_PLATE_STRENGTH_URL


KPL_PLATE_DETAIL_SOURCE = "kpl_son_plate_info"


@dataclass(frozen=True)
class KplPlateDetailConfig:
    trade_date: str
    timeout: int = 8
    pause: float = 0.05
    limit: int = 5
    plate_code: str = ""
    version: str = "5.11.0.1"
    api_version: str = "w33"
    user_agent: str = "lhb/5.11.1 (com.kaipanla.www; build:0; iOS 14.6.0) Alamofire/5.11.1"


def ensure_kpl_plate_detail_table(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_plate_featured_details (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          source_snapshot_at DATETIME(3) NULL,
          row_rank INT NOT NULL DEFAULT 0,
          plate_code VARCHAR(32) NOT NULL,
          plate_name VARCHAR(128) NOT NULL DEFAULT '',
          strength DECIMAL(18,4) NULL,
          change_pct DECIMAL(12,4) NULL,
          speed DECIMAL(12,4) NULL,
          reason_text TEXT NULL,
          sub_plates JSON NULL,
          top_research_pool_stocks JSON NULL,
          top_research_pool_stocks_by_sub_plate JSON NULL,
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_son_plate_info',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, captured_at, plate_code),
          KEY idx_kpl_plate_detail_latest (trade_date, captured_at, row_rank),
          KEY idx_kpl_plate_detail_plate (plate_code, trade_date, captured_at),
          KEY idx_kpl_plate_detail_snapshot (trade_date, source_snapshot_at, row_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL featured plate detail rows from clicked plate page; stores explosion reason when returned and sub-plate breakdown.';
        """,
    )
    ensure_kpl_plate_detail_column(config, "top_research_pool_stocks", "JSON NULL")
    ensure_kpl_plate_detail_column(config, "top_research_pool_stocks_by_sub_plate", "JSON NULL")


def ensure_kpl_plate_detail_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'kpl_plate_featured_details'
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    exists = (run_mysql(config, sql, batch=True, raw=True) or "").splitlines()[-1].strip() == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE kpl_plate_featured_details ADD COLUMN {column_name} {column_sql};")


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "Accept-Language": "zh-Hans-CN;q=1.0, en-CN;q=0.9",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "User-Agent": user_agent,
    }


def _is_today(day: str) -> bool:
    return str(day or "").strip() == date.today().isoformat()


def load_latest_plate_rows(config: MySqlConfig, cfg: KplPlateDetailConfig) -> list[dict[str, Any]]:
    code_filter = f"AND plate_code={sql_string(cfg.plate_code)}" if cfg.plate_code else ""
    limit_sql = f"LIMIT {max(1, int(cfg.limit))}" if int(cfg.limit or 0) > 0 and not cfg.plate_code else ""
    sql = f"""
    WITH latest AS (
      SELECT MAX(captured_at) AS captured_at
      FROM kpl_plate_featured_strengths
      WHERE trade_date={sql_string(cfg.trade_date)}
    )
    SELECT
      DATE_FORMAT(s.captured_at, '%Y-%m-%d %H:%i:%s.%f'),
      s.row_rank,
      s.plate_code,
      s.plate_name,
      s.strength,
      s.change_pct,
      s.speed
    FROM kpl_plate_featured_strengths s
    JOIN latest l ON l.captured_at=s.captured_at
    WHERE s.trade_date={sql_string(cfg.trade_date)}
      {code_filter}
    ORDER BY s.row_rank ASC, s.plate_code ASC
    {limit_sql};
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 7:
            continue
        rows.append(
            {
                "source_snapshot_at": str(row[0] or "").strip(),
                "row_rank": int(float(row[1] or 0)),
                "plate_code": str(row[2] or "").strip(),
                "plate_name": str(row[3] or "").strip(),
                "strength": row[4],
                "change_pct": row[5],
                "speed": row[6],
            }
        )
    return rows


def fetch_son_plate_info(session: requests.Session, plate_code: str, cfg: KplPlateDetailConfig) -> dict[str, Any]:
    data = {
        "PlateID": str(plate_code),
        "PhoneOSNew": "2",
        "VerSion": cfg.version,
        "a": "SonPlate_Info",
        "apiv": cfg.api_version,
        "c": "ZhiShuRanking",
        "DeviceID": str(uuid.uuid4()),
    }
    url = KPL_PLATE_STRENGTH_URL
    if not _is_today(cfg.trade_date):
        url = KPL_PLATE_STRENGTH_HIS_URL
        data["Date"] = str(cfg.trade_date)
    response = session.post(
        url,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def normalize_sub_plates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = payload.get("List")
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(raw_rows, start=1):
        if not isinstance(item, list) or len(item) < 2:
            continue
        code = str(item[0] or "").strip()
        name = str(item[1] or "").strip()
        if not code or not name:
            continue
        rows.append(
            {
                "rank": rank,
                "plate_code": code,
                "plate_name": name,
                "strength": item[2] if len(item) > 2 else None,
                "raw": item,
            }
        )
    return rows


def _walk_texts(value: Any, keys: set[str]) -> list[str]:
    texts: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text in keys and isinstance(child, str) and child.strip():
                texts.append(child.strip())
            texts.extend(_walk_texts(child, keys))
    elif isinstance(value, list):
        for child in value:
            texts.extend(_walk_texts(child, keys))
    return texts


def extract_reason_text(payload: dict[str, Any]) -> str:
    keys = {
        "Reason",
        "reason",
        "reason_text",
        "BoomReason",
        "boom_reason",
        "Boom_ZS",
        "爆发原因",
        "驱动原因",
        "逻辑",
        "content",
        "Content",
        "desc",
        "Desc",
    }
    texts = []
    for text in _walk_texts(payload, keys):
        clean = " ".join(text.split())
        if clean and clean not in texts:
            texts.append(clean)
    return "；".join(texts[:3])


def _short_text(text: Any, limit: int = 120) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def load_replay_reason_text(config: MySqlConfig, *, trade_date: str, plate_code: str, plate_name: str) -> str:
    sql = f"""
    SELECT
      REPLACE(REPLACE(REPLACE(COALESCE(boom_theme, ''), CHAR(13), ' '), CHAR(10), ' '), CHAR(9), ' ') AS boom_theme
    FROM kpl_replay_limit_theme_stocks
    WHERE trade_date={sql_string(trade_date)}
      AND (theme_code={sql_string(plate_code)} OR theme_name={sql_string(plate_name)})
      AND COALESCE(boom_theme, '') <> ''
    ORDER BY replay_sample_rank ASC, limit_time ASC, code ASC
    LIMIT 8;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        rows = []
    if not rows:
        return ""

    boom_lines: list[str] = []
    for row in rows:
        boom_theme = str(row[0] or "").strip() if len(row) > 0 else ""
        if boom_theme:
            line = _short_text(boom_theme, 140)
            if line and line not in boom_lines:
                boom_lines.append(line)
    return "爆发线索：" + "；".join(boom_lines[:3]) if boom_lines else ""


def load_top_research_pool_stocks(
    config: MySqlConfig,
    *,
    trade_date: str,
    sub_plates: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    picked_sub_plates = [
        item
        for item in sub_plates[:2]
        if str(item.get("plate_code") or "").strip() and str(item.get("plate_name") or "").strip()
    ]
    if not picked_sub_plates:
        return []
    code_order = {str(item["plate_code"]): index for index, item in enumerate(picked_sub_plates, start=1)}
    name_by_code = {str(item["plate_code"]): str(item.get("plate_name") or "") for item in picked_sub_plates}
    strength_by_code = {str(item["plate_code"]): item.get("strength") for item in picked_sub_plates}
    code_list = ", ".join(sql_string(code) for code in code_order)
    sql = f"""
    SELECT
      code,
      stock_name,
      pool_rank,
      pool_source_kind,
      section_code,
      section_name,
      section_rank,
      section_score,
      leader_code,
      leader_name,
      leader_pct
    FROM kpl_stock_featured_sections
    WHERE trade_date={sql_string(trade_date)}
      AND section_code IN ({code_list})
    ORDER BY pool_rank ASC, section_rank ASC, section_score DESC, code ASC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        rows = []
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        if len(row) < 11:
            continue
        code = str(row[0] or "").strip()
        if not code:
            continue
        section_code = str(row[4] or "").strip()
        item = {
            "code": code,
            "stock_name": str(row[1] or "").strip(),
            "pool_rank": _to_int(row[2]),
            "pool_source_kind": str(row[3] or "").strip(),
            "sub_plate_rank": code_order.get(section_code, 99),
            "sub_plate_code": section_code,
            "sub_plate_name": name_by_code.get(section_code, str(row[5] or "").strip()),
            "sub_plate_strength": strength_by_code.get(section_code),
            "section_rank": _to_int(row[6]),
            "section_score": _to_float(row[7]),
            "leader_code": str(row[8] or "").strip(),
            "leader_name": str(row[9] or "").strip(),
            "leader_pct": _to_float(row[10]),
        }
        old = deduped.get(code)
        if old is None or (
            int(item["pool_rank"] or 999999),
            int(item["sub_plate_rank"] or 99),
            int(item["section_rank"] or 999999),
        ) < (
            int(old.get("pool_rank") or 999999),
            int(old.get("sub_plate_rank") or 99),
            int(old.get("section_rank") or 999999),
        ):
            deduped[code] = item
    ranked = sorted(
        deduped.values(),
        key=lambda item: (
            int(item.get("pool_rank") or 999999),
            int(item.get("sub_plate_rank") or 99),
            int(item.get("section_rank") or 999999),
            str(item.get("code") or ""),
        ),
    )[: max(1, int(limit))]
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def load_top_research_pool_stocks_by_sub_plate(
    config: MySqlConfig,
    *,
    trade_date: str,
    sub_plates: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    picked_sub_plates = [
        item
        for item in sub_plates[:2]
        if str(item.get("plate_code") or "").strip() and str(item.get("plate_name") or "").strip()
    ]
    if not picked_sub_plates:
        return []

    groups: list[dict[str, Any]] = []
    for index, sub_plate in enumerate(picked_sub_plates, start=1):
        section_code = str(sub_plate.get("plate_code") or "").strip()
        section_name = str(sub_plate.get("plate_name") or "").strip()
        ranked = load_sub_plate_leader_top_stocks(
            config,
            trade_date=trade_date,
            sub_plate_code=section_code,
            sub_plate_name=section_name,
            sub_plate_rank=index,
            sub_plate_strength=sub_plate.get("strength"),
            limit=limit,
        )
        if ranked:
            groups.append(
                {
                    "sub_plate_rank": index,
                    "sub_plate_code": section_code,
                    "sub_plate_name": section_name,
                    "sub_plate_strength": sub_plate.get("strength"),
                    "stocks": ranked,
                    "stock_count": len(ranked),
                }
            )
    return groups


def load_sub_plate_leader_top_stocks(
    config: MySqlConfig,
    *,
    trade_date: str,
    sub_plate_code: str,
    sub_plate_name: str,
    sub_plate_rank: int,
    sub_plate_strength: Any,
    limit: int = 5,
) -> list[dict[str, Any]]:
    day = sql_string(trade_date)
    section = sql_string(sub_plate_code)
    sql = f"""
    WITH
    scope_codes AS (
      SELECT
        k.code,
        k.stock_name AS name,
        k.pool_rank,
        k.pool_source_kind,
        k.section_code,
        k.section_name,
        k.section_rank,
        k.section_score,
        k.leader_code,
        k.leader_name,
        k.leader_pct,
        COALESCE(db.pct_change, 0) AS today_pct
      FROM kpl_stock_featured_sections k
      LEFT JOIN stock_daily_bars db
        ON db.code=k.code
       AND db.trade_date=CAST({day} AS DATE)
      WHERE k.trade_date=CAST({day} AS DATE)
        AND k.section_code={section}
        AND COALESCE(k.code, '') <> ''
    ),
    recent_trade_days AS (
      SELECT CAST({day} AS DATE) AS trade_date
      UNION
      SELECT trade_date
      FROM (
        SELECT DISTINCT trade_date
        FROM stock_daily_bars
        WHERE trade_date < {day}
        ORDER BY trade_date DESC
        LIMIT 9
      ) d
    ),
    limit_days_score_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM limit_up_pool_items
      WHERE trade_date <= {day}
        AND source='eastmoney_akshare_stock_zt_pool_em'
        AND pool_type='limit_up'
        AND COALESCE(status, '') IN ('limit_up', '涨停', '')
    ),
    limit_events_raw AS (
      SELECT
        i.trade_date,
        i.code,
        COALESCE(
          STR_TO_DATE(CONCAT(DATE_FORMAT(i.trade_date, '%Y-%m-%d'), ' ', NULLIF(i.last_limit_time, '')), '%Y-%m-%d %H:%i:%s'),
          STR_TO_DATE(CONCAT(DATE_FORMAT(i.trade_date, '%Y-%m-%d'), ' ', NULLIF(i.last_limit_time, '')), '%Y-%m-%d %H:%i')
        ) AS first_limit_at,
        COALESCE(i.turnover_amount, i.seal_amount, 0) AS limit_amount,
        '东方财富涨停池' AS source_name,
        1 AS source_priority
      FROM limit_up_pool_items i
      JOIN recent_trade_days td ON td.trade_date=i.trade_date
      JOIN scope_codes sc ON sc.code=i.code
      WHERE i.source='eastmoney_akshare_stock_zt_pool_em'
        AND i.pool_type='limit_up'
        AND COALESCE(i.status, '') IN ('limit_up', '涨停', '')
        AND COALESCE(NULLIF(i.last_limit_time, ''), '') <> ''
    ),
    limit_events_daily AS (
      SELECT
        trade_date,
        code,
        MIN(first_limit_at) AS first_limit_at,
        MAX(limit_amount) AS limit_amount,
        SUBSTRING_INDEX(GROUP_CONCAT(source_name ORDER BY source_priority ASC), ',', 1) AS source_name
      FROM limit_events_raw
      WHERE first_limit_at IS NOT NULL
      GROUP BY trade_date, code
    ),
    today_limit_ranked AS (
      SELECT *
      FROM (
        SELECT
          sc.code,
          CAST({day} AS DATE) AS trade_date,
          e.first_limit_at,
          e.limit_amount,
          e.source_name,
          sc.today_pct,
          ROW_NUMBER() OVER (
            ORDER BY e.first_limit_at ASC, e.limit_amount DESC, sc.code ASC
          ) AS rank_no
        FROM scope_codes sc
        JOIN limit_events_daily e
          ON e.code=sc.code
         AND e.trade_date=CAST({day} AS DATE)
      ) ranked
      WHERE rank_no <= 5
    ),
    limit_days_raw AS (
      SELECT
        sc.code,
        i.trade_date,
        GREATEST(
          COALESCE(CAST(NULLIF(SUBSTRING_INDEX(i.limit_up_stat, '/', -1), '') AS UNSIGNED), 0),
          COALESCE(i.limit_up_days, 0),
          1
        ) AS limit_up_days,
        COALESCE(NULLIF(i.limit_up_stat, ''), CONCAT(COALESCE(i.limit_up_days, 1), '连板')) AS limit_up_stat,
        COALESCE(i.turnover_amount, i.seal_amount, 0) AS limit_amount,
        COALESCE(
          STR_TO_DATE(CONCAT(DATE_FORMAT(i.trade_date, '%Y-%m-%d'), ' ', NULLIF(i.last_limit_time, '')), '%Y-%m-%d %H:%i:%s'),
          STR_TO_DATE(CONCAT(DATE_FORMAT(i.trade_date, '%Y-%m-%d'), ' ', NULLIF(i.last_limit_time, '')), '%Y-%m-%d %H:%i')
        ) AS limit_time,
        '东方财富涨停池' AS source_name,
        1 AS source_priority
      FROM scope_codes sc
      JOIN limit_days_score_day sd ON sd.trade_date IS NOT NULL
      JOIN limit_up_pool_items i
        ON i.code=sc.code
       AND i.trade_date=sd.trade_date
       AND i.source='eastmoney_akshare_stock_zt_pool_em'
       AND i.pool_type='limit_up'
       AND COALESCE(i.status, '') IN ('limit_up', '涨停', '')
       AND COALESCE(NULLIF(i.last_limit_time, ''), '') <> ''
    ),
    limit_days_per_code AS (
      SELECT
        code,
        MAX(trade_date) AS trade_date,
        MAX(limit_up_days) AS limit_up_days,
        SUBSTRING_INDEX(
          GROUP_CONCAT(limit_up_stat ORDER BY limit_up_days DESC, source_priority ASC, COALESCE(limit_time, '2099-12-31') ASC SEPARATOR '||'),
          '||',
          1
        ) AS limit_up_stat,
        MAX(limit_amount) AS limit_amount,
        MIN(limit_time) AS limit_time,
        SUBSTRING_INDEX(
          GROUP_CONCAT(source_name ORDER BY limit_up_days DESC, source_priority ASC, COALESCE(limit_time, '2099-12-31') ASC SEPARATOR '||'),
          '||',
          1
        ) AS source_name
      FROM limit_days_raw
      WHERE limit_up_days > 0
      GROUP BY code
    ),
    today_limit_days_ranked AS (
      SELECT *
      FROM (
        SELECT
          *,
          ROW_NUMBER() OVER (
            ORDER BY
              limit_up_days DESC,
              COALESCE(limit_time, '2099-12-31') ASC,
              limit_amount DESC,
              code ASC
          ) AS rank_no
        FROM limit_days_per_code
      ) ranked
      WHERE rank_no <= 5
    ),
    first_10d_per_code AS (
      SELECT *
      FROM (
        SELECT
          sc.code,
          e.trade_date,
          e.first_limit_at,
          e.limit_amount,
          e.source_name,
          ROW_NUMBER() OVER (
            PARTITION BY sc.code
            ORDER BY e.trade_date ASC, e.first_limit_at ASC, e.limit_amount DESC
          ) AS code_rn
        FROM scope_codes sc
        JOIN limit_events_daily e ON e.code=sc.code
      ) code_events
      WHERE code_rn=1
    ),
    first_10d_ranked AS (
      SELECT *
      FROM (
        SELECT
          *,
          ROW_NUMBER() OVER (
            ORDER BY trade_date ASC, first_limit_at ASC, limit_amount DESC, code ASC
          ) AS rank_no
        FROM first_10d_per_code
      ) ranked
      WHERE rank_no <= 5
    ),
    dimension_scores AS (
      SELECT
        code,
        'today_limit' AS dimension,
        '收盘日封板先后' AS dimension_label,
        rank_no,
        6 - rank_no AS score,
        ROUND(limit_amount / 100000000, 2) AS value_num,
        CONCAT(DATE_FORMAT(trade_date, '%m-%d'), ' ', DATE_FORMAT(first_limit_at, '%H:%i:%s')) AS value_text,
        source_name,
        DATE_FORMAT(trade_date, '%Y-%m-%d') AS data_date
      FROM today_limit_ranked
      UNION ALL
      SELECT
        code,
        'limit_up_days' AS dimension,
        '连板辨识度' AS dimension_label,
        rank_no,
        6 - rank_no AS score,
        limit_up_days AS value_num,
        CONCAT(DATE_FORMAT(trade_date, '%m-%d'), ' ', limit_up_days, '板 / ', limit_up_stat) AS value_text,
        source_name,
        DATE_FORMAT(trade_date, '%Y-%m-%d') AS data_date
      FROM today_limit_days_ranked
      UNION ALL
      SELECT
        code,
        'first_limit_10d' AS dimension,
        '阶段先手性' AS dimension_label,
        rank_no,
        6 - rank_no AS score,
        ROUND(limit_amount / 100000000, 2) AS value_num,
        CONCAT(DATE_FORMAT(trade_date, '%m-%d'), ' ', DATE_FORMAT(first_limit_at, '%H:%i:%s')) AS value_text,
        source_name,
        DATE_FORMAT(trade_date, '%Y-%m-%d') AS data_date
      FROM first_10d_ranked
    ),
    dimension_summary AS (
      SELECT
        code,
        SUM(score) AS total_score,
        MAX(IF(dimension='today_limit', score, 0)) AS today_limit_score,
        MAX(IF(dimension='first_limit_10d', score, 0)) AS first_limit_10d_score,
        MAX(IF(dimension='limit_up_days', score, 0)) AS limit_up_days_score,
        MAX(IF(dimension='today_limit', rank_no, NULL)) AS today_limit_rank,
        MAX(IF(dimension='first_limit_10d', rank_no, NULL)) AS first_limit_10d_rank,
        MAX(IF(dimension='limit_up_days', rank_no, NULL)) AS limit_up_days_rank,
        MAX(IF(dimension='today_limit', value_text, NULL)) AS today_limit_time,
        MAX(IF(dimension='first_limit_10d', value_text, NULL)) AS first_limit_10d_time,
        MAX(IF(dimension='limit_up_days', value_text, NULL)) AS limit_up_days_text,
        MAX(IF(dimension='today_limit', value_num, NULL)) AS today_limit_amount_yi,
        MAX(IF(dimension='first_limit_10d', value_num, NULL)) AS first_limit_10d_amount_yi,
        MAX(IF(dimension='limit_up_days', value_num, NULL)) AS limit_up_days
      FROM dimension_scores
      GROUP BY code
    ),
    scored AS (
      SELECT
        sc.*,
        COALESCE(ds.total_score, 0) AS total_score,
        COALESCE(ds.today_limit_score, 0) AS today_limit_score,
        COALESCE(ds.first_limit_10d_score, 0) AS first_limit_10d_score,
        COALESCE(ds.limit_up_days_score, 0) AS limit_up_days_score,
        ds.today_limit_rank,
        ds.first_limit_10d_rank,
        ds.limit_up_days_rank,
        COALESCE(ds.today_limit_time, '') AS today_limit_time,
        COALESCE(ds.first_limit_10d_time, '') AS first_limit_10d_time,
        COALESCE(ds.limit_up_days_text, '') AS limit_up_days_text,
        ds.today_limit_amount_yi,
        ds.first_limit_10d_amount_yi,
        ds.limit_up_days
      FROM scope_codes sc
      LEFT JOIN dimension_summary ds ON ds.code=sc.code
    ),
    ranked AS (
      SELECT
        scored.*,
        ROW_NUMBER() OVER (
          ORDER BY
            total_score DESC,
            limit_up_days_score DESC,
            today_limit_score DESC,
            first_limit_10d_score DESC,
            COALESCE(limit_up_days_rank, 999999) ASC,
            COALESCE(today_limit_rank, 999999) ASC,
            COALESCE(first_limit_10d_rank, 999999) ASC,
            today_pct DESC,
            code ASC
        ) AS leader_rank
      FROM scored
    )
    SELECT
      r.code,
      r.name,
      r.pool_rank,
      r.pool_source_kind,
      r.section_rank,
      r.section_score,
      r.leader_code,
      r.leader_name,
      r.leader_pct,
      r.today_pct,
      r.total_score,
      r.today_limit_score,
      r.first_limit_10d_score,
      r.limit_up_days_score,
      r.today_limit_rank,
      r.first_limit_10d_rank,
      r.limit_up_days_rank,
      r.today_limit_time,
      r.first_limit_10d_time,
      r.limit_up_days_text,
      r.today_limit_amount_yi,
      r.first_limit_10d_amount_yi,
      r.limit_up_days,
      COALESCE(cp.company_highlights, '') AS company_highlights,
      COALESCE(kr.reason_text, sr.reason_text, '') AS kpl_limit_reason,
      COALESCE(DATE_FORMAT(kr.reason_date, '%Y-%m-%d'), DATE_FORMAT(sr.reason_date, '%Y-%m-%d'), '') AS kpl_limit_reason_date
    FROM ranked r
    LEFT JOIN stock_company_profiles cp ON cp.code=r.code
    LEFT JOIN LATERAL (
      SELECT x.reason_text, x.reason_date
      FROM kpl_replay_limit_theme_stocks x
      WHERE x.code=r.code
        AND x.trade_date <= CAST({day} AS DATE)
        AND x.reason_date <= CAST({day} AS DATE)
        AND x.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
        AND COALESCE(x.reason_text, '') <> ''
      ORDER BY x.reason_date DESC, x.captured_at DESC, x.theme_rank ASC
      LIMIT 1
    ) kr ON TRUE
    LEFT JOIN LATERAL (
      SELECT x.reason_text, x.reason_date
      FROM kpl_stock_limit_up_reasons x
      WHERE x.code=r.code
        AND x.reason_date <= CAST({day} AS DATE)
        AND x.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
        AND COALESCE(x.reason_text, '') <> ''
      ORDER BY x.reason_date DESC, x.captured_at DESC
      LIMIT 1
    ) sr ON TRUE
    WHERE r.leader_rank <= {max(1, int(limit))}
    ORDER BY r.leader_rank ASC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        rows = []
    ranked: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 26:
            continue
        code = str(row[0] or "").strip()
        if not code:
            continue
        item = {
            "code": code,
            "stock_name": str(row[1] or "").strip(),
            "pool_rank": _to_int(row[2]),
            "pool_source_kind": str(row[3] or "").strip(),
            "sub_plate_rank": int(sub_plate_rank),
            "sub_plate_code": str(sub_plate_code),
            "sub_plate_name": str(sub_plate_name),
            "sub_plate_strength": sub_plate_strength,
            "section_rank": _to_int(row[4]),
            "section_score": _to_float(row[5]),
            "leader_code": str(row[6] or "").strip(),
            "leader_name": str(row[7] or "").strip(),
            "leader_pct": _to_float(row[8]),
            "today_pct": _to_float(row[9]),
            "total_score": _to_int(row[10]),
            "today_limit_score": _to_int(row[11]),
            "first_limit_10d_score": _to_int(row[12]),
            "limit_up_days_score": _to_int(row[13]),
            "today_limit_rank": _to_int(row[14]) or None,
            "first_limit_10d_rank": _to_int(row[15]) or None,
            "limit_up_days_rank": _to_int(row[16]) or None,
            "today_limit_time": str(row[17] or "").strip(),
            "first_limit_10d_time": str(row[18] or "").strip(),
            "limit_up_days_text": str(row[19] or "").strip(),
            "today_limit_amount_yi": _to_float(row[20]),
            "first_limit_10d_amount_yi": _to_float(row[21]),
            "limit_up_days": _to_float(row[22]),
            "company_highlights": str(row[23] or "").strip(),
            "kpl_limit_reason": str(row[24] or "").strip(),
            "kpl_limit_reason_date": str(row[25] or "").strip(),
        }
        item["dimensions"] = _leader_dimensions(item)
        ranked.append(item)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked


def _leader_dimensions(item: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions: list[dict[str, Any]] = []
    dimension_map = [
        (
            "today_limit",
            "收盘日封板先后",
            item.get("today_limit_rank"),
            item.get("today_limit_score"),
            item.get("today_limit_amount_yi"),
            item.get("today_limit_time"),
        ),
        (
            "first_limit_10d",
            "阶段先手性",
            item.get("first_limit_10d_rank"),
            item.get("first_limit_10d_score"),
            item.get("first_limit_10d_amount_yi"),
            item.get("first_limit_10d_time"),
        ),
        (
            "limit_up_days",
            "连板辨识度",
            item.get("limit_up_days_rank"),
            item.get("limit_up_days_score"),
            item.get("limit_up_days"),
            item.get("limit_up_days_text"),
        ),
    ]
    for dimension, label, rank_no, score, value_num, value_text in dimension_map:
        if not score:
            continue
        dimensions.append(
            {
                "dimension": dimension,
                "label": label,
                "rank_no": rank_no,
                "score": score,
                "value_num": value_num,
                "value_text": value_text or "",
                "source_name": "东方财富涨停池",
                "data_date": item.get("kpl_limit_reason_date") or "",
            }
        )
    return dimensions


def save_plate_details(
    config: MySqlConfig,
    *,
    trade_date: str,
    captured_at: str,
    rows: list[dict[str, Any]],
) -> int:
    statements: list[str] = []
    for row in rows:
        statements.append(
            f"""
            INSERT INTO kpl_plate_featured_details(
              trade_date, captured_at, source_snapshot_at, row_rank, plate_code, plate_name,
              strength, change_pct, speed, reason_text, sub_plates, top_research_pool_stocks,
              top_research_pool_stocks_by_sub_plate, source, raw_json
            ) VALUES (
              {sql_string(trade_date)}, {sql_string(captured_at)},
              {sql_string(row.get("source_snapshot_at") or "")},
              {sql_int(row.get("row_rank"))},
              {sql_string(row.get("plate_code") or "")},
              {sql_string(row.get("plate_name") or "")},
              {sql_number(row.get("strength"))},
              {sql_number(row.get("change_pct"))},
              {sql_number(row.get("speed"))},
              {sql_string(row.get("reason_text") or "")},
              {sql_json(row.get("sub_plates") or [])},
              {sql_json(row.get("top_research_pool_stocks") or [])},
              {sql_json(row.get("top_research_pool_stocks_by_sub_plate") or [])},
              {sql_string(KPL_PLATE_DETAIL_SOURCE)},
              {sql_json(row.get("raw_json") or {})}
            )
            ON DUPLICATE KEY UPDATE
              source_snapshot_at=VALUES(source_snapshot_at),
              row_rank=VALUES(row_rank),
              plate_name=VALUES(plate_name),
              strength=VALUES(strength),
              change_pct=VALUES(change_pct),
              speed=VALUES(speed),
              reason_text=VALUES(reason_text),
              sub_plates=VALUES(sub_plates),
              top_research_pool_stocks=VALUES(top_research_pool_stocks),
              top_research_pool_stocks_by_sub_plate=VALUES(top_research_pool_stocks_by_sub_plate),
              source=VALUES(source),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    if statements:
        run_mysql(config, "\n".join(statements))
    return len(rows)


def collect_kpl_plate_details(config: MySqlConfig, cfg: KplPlateDetailConfig) -> dict[str, Any]:
    ensure_kpl_plate_detail_table(config)
    plate_rows = load_latest_plate_rows(config, cfg)
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    session = requests.Session()
    imported_rows: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for index, plate in enumerate(plate_rows, start=1):
        code = str(plate.get("plate_code") or "").strip()
        if not code:
            continue
        try:
            payload = fetch_son_plate_info(session, code, cfg)
            if str(payload.get("errcode", "0")) != "0":
                raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
            sub_plates = normalize_sub_plates(payload)
            reason_text = extract_reason_text(payload)
            reason_source = "direct_detail"
            if not reason_text:
                reason_text = load_replay_reason_text(
                    config,
                    trade_date=cfg.trade_date,
                    plate_code=code,
                    plate_name=str(plate.get("plate_name") or ""),
                )
                reason_source = "kpl_replay_limit_theme_stocks" if reason_text else ""
            if not reason_text:
                skipped.append(
                    {
                        "plate_code": code,
                        "plate_name": str(plate.get("plate_name") or ""),
                        "reason": "empty_reason",
                    }
                )
                continue
            top_stock_groups = load_top_research_pool_stocks_by_sub_plate(
                config,
                trade_date=cfg.trade_date,
                sub_plates=sub_plates,
                limit=5,
            )
            top_stocks = [
                stock
                for group in top_stock_groups
                for stock in (group.get("stocks") or [])
            ]
            if not top_stock_groups:
                skipped.append(
                    {
                        "plate_code": code,
                        "plate_name": str(plate.get("plate_name") or ""),
                        "reason": "empty_research_pool_top_stocks",
                    }
                )
                continue
            imported_rows.append(
                {
                    **plate,
                    "reason_text": reason_text,
                    "sub_plates": sub_plates,
                    "top_research_pool_stocks": top_stocks,
                    "top_research_pool_stocks_by_sub_plate": top_stock_groups,
                    "raw_json": {
                        "son_plate_info": payload,
                        "reason_source": reason_source,
                        "top_stock_rule": "top2_sub_plates -> each sub-plate kpl_stock_featured_sections research-pool intersection -> pool_rank top5",
                    },
                }
            )
        except Exception as exc:
            failed.append({"plate_code": code, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if cfg.pause > 0 and index < len(plate_rows):
            time.sleep(float(cfg.pause))
    imported = save_plate_details(config, trade_date=cfg.trade_date, captured_at=captured_at, rows=imported_rows)
    return {
        "ok": not failed,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "source": KPL_PLATE_DETAIL_SOURCE,
        "plate_count": len(plate_rows),
        "imported": imported,
        "skipped_count": len(skipped),
        "skipped": skipped[:20],
        "failed_count": len(failed),
        "failed": failed[:20],
        "top": [
            {
                "rank": row.get("row_rank"),
                "plate_code": row.get("plate_code"),
                "plate_name": row.get("plate_name"),
                "reason_text": row.get("reason_text") or "",
                "sub_plate_count": len(row.get("sub_plates") or []),
                "top_research_pool_stocks": row.get("top_research_pool_stocks") or [],
                "top_research_pool_stocks_by_sub_plate": row.get("top_research_pool_stocks_by_sub_plate") or [],
            }
            for row in imported_rows[:10]
        ],
    }
