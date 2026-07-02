from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Iterable

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_number, sql_string
from stock_move_scout.web.runtime import assert_weekday_trade_date


DEFAULT_RESEARCH_POOL_PERIODS = (5,)
DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS = 5
DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS = 5
DEFAULT_RESEARCH_POOL_GAIN_TOP = 30
DEFAULT_RESEARCH_POOL_RULE = "recent_limit_up_or_5d_gain_top"
RESEARCH_POOL_MA_NONE = "none"
RESEARCH_POOL_MA_TREND = "ma5_10_20_30_up"
DEFAULT_RESEARCH_POOL_MA_MODE = RESEARCH_POOL_MA_NONE
RESEARCH_POOL_THEME_SOURCE = "research_pool_theme_members"
HEADLINE_THEME_SOURCE = "ths_homepage_headline"


@dataclass(frozen=True)
class ResearchPoolSnapshot:
    trade_date: str
    periods: tuple[int, ...]
    top: int
    codes: tuple[str, ...]
    period_trade_dates: dict[int, str]
    codes_by_period: dict[int, tuple[str, ...]]
    rule: str = DEFAULT_RESEARCH_POOL_RULE
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS
    source_dates: dict[str, str] = field(default_factory=dict)
    codes_by_source: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def code_count(self) -> int:
        return len(self.codes)


def _clean_periods(periods: Iterable[int] | None = None, period_days: int | None = None) -> list[int]:
    raw = [int(period_days)] if period_days else [int(item) for item in (periods or DEFAULT_RESEARCH_POOL_PERIODS)]
    return sorted({item for item in raw if item > 0})


def normalize_research_pool_ma_mode(value: str | None) -> str:
    mode = str(value or DEFAULT_RESEARCH_POOL_MA_MODE).strip().lower()
    if mode in {"", "default"}:
        return DEFAULT_RESEARCH_POOL_MA_MODE
    if mode in {"ma", "ma5_30", "ma5_10_20_30", "ma5_10_20_30_up", "no_ma60", "bull", "bull_market", "bullish"}:
        return RESEARCH_POOL_MA_TREND
    if mode in {"none", "off", "false", "0", "loose", "no_filter", "no_ma", "bear", "bear_market", "adjust", "adjustment"}:
        return RESEARCH_POOL_MA_NONE
    raise ValueError(f"unsupported research-pool ma_mode: {value}")


def research_pool_system_label(ma_mode: str | None = None) -> str:
    """Human label for the selected research-pool system."""
    resolved = normalize_research_pool_ma_mode(ma_mode)
    if resolved == RESEARCH_POOL_MA_TREND:
        return "牛市系统"
    return "熊市系统"


def ensure_research_pool_tables(config: MySqlConfig) -> None:
    # Avoid taking metadata locks when tables already exist.
    # DDL can be blocked by unrelated long-running sessions, while normal
    # reads/writes remain usable.
    missing = False
    for table in ("research_pool_snapshots", "research_pool_items"):
        try:
            run_mysql(config, f"SELECT 1 FROM {table} LIMIT 1;")
        except Exception as exc:
            if "doesn't exist" in str(exc):
                missing = True
                break
            raise
    if not missing:
        return
    sql = """
    CREATE TABLE IF NOT EXISTS research_pool_snapshots (
      trade_date DATE NOT NULL,
      rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
      limit_up_days INT NOT NULL DEFAULT 5,
      gain_period_days INT NOT NULL DEFAULT 5,
      gain_top INT NOT NULL DEFAULT 30,
      code_count INT NOT NULL DEFAULT 0,
      source_dates_json JSON NULL,
      params_json JSON NULL,
      source_hash CHAR(64) NOT NULL DEFAULT '',
      generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top),
      KEY idx_research_pool_snapshots_generated (generated_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS research_pool_items (
      trade_date DATE NOT NULL,
      rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
      limit_up_days INT NOT NULL DEFAULT 5,
      gain_period_days INT NOT NULL DEFAULT 5,
      gain_top INT NOT NULL DEFAULT 30,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      pool_rank INT NOT NULL DEFAULT 0,
      source_kind VARCHAR(32) NOT NULL DEFAULT '',
      source_priority INT NOT NULL DEFAULT 0,
      source_rank INT NOT NULL DEFAULT 0,
      source_label VARCHAR(255) NOT NULL DEFAULT '',
      source_trade_date DATE NULL,
      limit_up_day_count INT NOT NULL DEFAULT 0,
      rank_5d INT NULL,
      pct_5d DECIMAL(10,4) NULL,
      latest_pct DECIMAL(10,4) NULL,
      raw_json JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top, code),
      KEY idx_research_pool_items_day_rank (trade_date, rule, pool_rank),
      KEY idx_research_pool_items_code_day (code, trade_date),
      KEY idx_research_pool_items_source (trade_date, source_kind, source_rank)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def ensure_research_pool_theme_tables(config: MySqlConfig) -> None:
    ensure_research_pool_tables(config)
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS research_pool_theme_members (
          trade_date DATE NOT NULL,
          code CHAR(6) NOT NULL,
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          pool_rank INT NOT NULL DEFAULT 0,
          pool_source_kind VARCHAR(32) NOT NULL DEFAULT '',
          concept_name VARCHAR(128) NOT NULL DEFAULT '',
          concept_id VARCHAR(64) NOT NULL DEFAULT '',
          reason_explain VARCHAR(2048) NOT NULL DEFAULT '',
          fit_rank INT NOT NULL DEFAULT 0,
          theme_name VARCHAR(128) NOT NULL DEFAULT '',
          theme_rank INT NOT NULL DEFAULT 999,
          is_headline_theme TINYINT NOT NULL DEFAULT 0,
          match_type VARCHAR(64) NOT NULL DEFAULT '',
          match_score DECIMAL(10,4) NOT NULL DEFAULT 0,
          source_table VARCHAR(64) NOT NULL DEFAULT 'ths_stock_concept_explanations',
          source_key VARCHAR(128) NOT NULL DEFAULT '',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, code, theme_name, concept_name),
          KEY idx_research_pool_theme_day_theme (trade_date, theme_name, is_headline_theme, match_score),
          KEY idx_research_pool_theme_day_code (trade_date, code, pool_rank),
          KEY idx_research_pool_theme_headline (trade_date, is_headline_theme, theme_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='Research-pool stocks mapped to THS concept explanations and headline theme dimensions.';
        """,
    )


def ensure_headline_theme_role_evidence_table(config: MySqlConfig) -> None:
    ensure_research_pool_theme_tables(config)
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS stock_headline_theme_role_evidence (
          trade_date DATE NOT NULL,
          code CHAR(6) NOT NULL,
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          roles JSON NOT NULL,
          role_count INT NOT NULL DEFAULT 0,
          latest_source_updated_at DATETIME(3) NULL,
          source_hash CHAR(64) NOT NULL DEFAULT '',
          generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, code),
          KEY idx_headline_theme_role_day_updated (trade_date, latest_source_updated_at),
          KEY idx_headline_theme_role_code_day (code, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='Precomputed headline-theme role evidence for evidence-detail rendering.';
        """,
    )


def research_pool_cte(
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
    cte_name: str = "research_pool",
) -> str:
    """Active pool: recent limit-up stocks plus 5-day gain TopN without recent limit-up."""
    day = sql_string(trade_date)
    limit_days = max(1, int(limit_up_days))
    period_days = max(1, int(gain_period_days))
    top = max(1, int(gain_top))
    resolved_ma_mode = normalize_research_pool_ma_mode(ma_mode)
    main_a_regexp = "'^(000|001|002|003|300|301|600|601|603|605|688|689)'"
    ma_ctes = ""
    seed_ma_join = ""
    if resolved_ma_mode == RESEARCH_POOL_MA_TREND:
        seed_ma_join = "JOIN ma_pass_codes mp ON mp.code=seed.code"
        ma_ctes = f"""
    ma_trade_days AS (
      SELECT trade_date
      FROM (
        SELECT DISTINCT trade_date
        FROM stock_daily_bars
        WHERE trade_date <= {day}
        ORDER BY trade_date DESC
        LIMIT 90
      ) d
    ),
    price_ma AS (
      SELECT
        b.code,
        b.trade_date,
        b.close_price,
        AVG(b.close_price) OVER (PARTITION BY b.code ORDER BY b.trade_date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS ma5,
        AVG(b.close_price) OVER (PARTITION BY b.code ORDER BY b.trade_date ROWS BETWEEN 9 PRECEDING AND CURRENT ROW) AS ma10,
        AVG(b.close_price) OVER (PARTITION BY b.code ORDER BY b.trade_date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS ma20,
        AVG(b.close_price) OVER (PARTITION BY b.code ORDER BY b.trade_date ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS ma30
      FROM stock_daily_bars b
      JOIN market_universe u ON u.code=b.code
      JOIN ma_trade_days td ON td.trade_date=b.trade_date
      WHERE b.trade_date <= {day}
        AND b.close_price IS NOT NULL
    ),
    price_ma_with_previous AS (
      SELECT
        m.*,
        LAG(ma5, 5) OVER (PARTITION BY code ORDER BY trade_date) AS pma5,
        LAG(ma10, 10) OVER (PARTITION BY code ORDER BY trade_date) AS pma10,
        LAG(ma20, 20) OVER (PARTITION BY code ORDER BY trade_date) AS pma20,
        LAG(ma30, 30) OVER (PARTITION BY code ORDER BY trade_date) AS pma30
      FROM price_ma m
    ),
    ma_pass_codes AS (
      SELECT
        m.code,
        m.ma5,
        m.ma10,
        m.ma20,
        m.ma30,
        m.pma5,
        m.pma10,
        m.pma20,
        m.pma30
      FROM price_ma_with_previous m
      JOIN gain_trade_days latest_day ON latest_day.rn=1 AND latest_day.trade_date=m.trade_date
      WHERE m.ma5 > m.pma5
        AND m.ma10 > m.pma10
        AND m.ma20 > m.pma20
        AND m.ma30 > m.pma30
    ),
"""
    return f"""
    market_universe AS (
      SELECT code, name
      FROM stocks
      WHERE code REGEXP {main_a_regexp}
        AND COALESCE(is_st, 0)=0
        AND name NOT LIKE '%ST%'
        AND name NOT LIKE '%退市%'
    ),
    research_trade_days AS (
      SELECT trade_date
      FROM (
        SELECT DISTINCT trade_date
        FROM limit_up_pool_items
        WHERE trade_date <= {day}
          AND pool_type='limit_up'
        ORDER BY trade_date DESC
        LIMIT {limit_days}
      ) d
    ),
    recent_limit_up_events AS (
      SELECT l.trade_date, l.code
      FROM limit_up_pool_items l
      JOIN research_trade_days td ON td.trade_date=l.trade_date
      JOIN market_universe u ON u.code=l.code
      WHERE l.pool_type='limit_up'
        AND COALESCE(l.status, '') IN ('limit_up', '涨停', '')
    ),
    recent_limit_up_codes AS (
      SELECT
        e.code,
        MAX(u.name) AS name,
        COUNT(DISTINCT e.trade_date) AS limit_up_day_count,
        DATE_FORMAT(MIN(e.trade_date), '%Y-%m-%d') AS first_limit_up_date,
        DATE_FORMAT(MAX(e.trade_date), '%Y-%m-%d') AS latest_limit_up_date,
        ROW_NUMBER() OVER (
          ORDER BY MAX(e.trade_date) DESC, COUNT(DISTINCT e.trade_date) DESC, e.code ASC
        ) AS source_rank
      FROM recent_limit_up_events e
      JOIN market_universe u ON u.code=e.code
      GROUP BY e.code
    ),
    gain_trade_days AS (
      SELECT
        trade_date,
        ROW_NUMBER() OVER (ORDER BY trade_date DESC) AS rn
      FROM (
        SELECT DISTINCT trade_date
        FROM stock_daily_bars
        WHERE trade_date <= {day}
      ) d
    ),
{ma_ctes}
    daily_gain_metrics AS (
      SELECT
        u.code,
        u.name,
        ((latest_bar.close_price / NULLIF(base_bar.close_price, 0)) - 1) * 100 AS period_pct,
        latest_bar.pct_change AS latest_pct,
        latest_day.trade_date AS gain_rank_trade_date
      FROM market_universe u
      JOIN gain_trade_days latest_day ON latest_day.rn=1
      JOIN gain_trade_days base_day ON base_day.rn={period_days}
      JOIN stock_daily_bars latest_bar
        ON latest_bar.code=u.code
       AND latest_bar.trade_date=latest_day.trade_date
      JOIN stock_daily_bars base_bar
        ON base_bar.code=u.code
       AND base_bar.trade_date=base_day.trade_date
      WHERE latest_bar.close_price IS NOT NULL
        AND base_bar.close_price IS NOT NULL
        AND base_bar.close_price > 0
    ),
    daily_gain_all AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (
          ORDER BY m.period_pct DESC, COALESCE(m.latest_pct, 0) DESC, m.code ASC
        ) AS market_rank_no
      FROM daily_gain_metrics m
    ),
    gain_rank_base AS (
      SELECT
        g.code,
        g.name,
        g.market_rank_no,
        g.period_pct,
        g.latest_pct,
        DATE_FORMAT(g.gain_rank_trade_date, '%Y-%m-%d') AS gain_rank_trade_date,
        ROW_NUMBER() OVER (
          ORDER BY g.period_pct DESC, COALESCE(g.latest_pct, 0) DESC, g.code ASC
        ) AS filtered_rank_no
      FROM daily_gain_all g
      LEFT JOIN recent_limit_up_codes lu ON lu.code=g.code
      WHERE g.period_pct IS NOT NULL
        AND lu.code IS NULL
    ),
    gain_rank_candidates AS (
      SELECT *
      FROM gain_rank_base
      WHERE filtered_rank_no <= {top}
    ),
    research_pool_seed_unfiltered AS (
      SELECT
        lu.code,
        lu.name,
        'recent_limit_up' AS source_kind,
        lu.source_rank,
        CONCAT('近{limit_days}日涨停', lu.limit_up_day_count, '天') AS source_rank_label,
        lu.latest_limit_up_date AS source_trade_date,
        lu.limit_up_day_count,
        NULL AS gain_rank,
        NULL AS gain_pct,
        NULL AS latest_pct,
        1 AS source_priority
      FROM recent_limit_up_codes lu
      UNION ALL
      SELECT
        g.code,
        g.name,
        'five_day_gain_top' AS source_kind,
        g.filtered_rank_no AS source_rank,
        CONCAT('近5日无涨停{period_days}日涨幅#', g.filtered_rank_no, '/', ROUND(g.period_pct, 2), '%', IF(g.market_rank_no <> g.filtered_rank_no, CONCAT('(全市场#', g.market_rank_no, ')'), '')) AS source_rank_label,
        g.gain_rank_trade_date AS source_trade_date,
        0 AS limit_up_day_count,
        g.filtered_rank_no AS gain_rank,
        g.period_pct AS gain_pct,
        g.latest_pct AS latest_pct,
        2 AS source_priority
      FROM gain_rank_candidates g
    ),
    research_pool_seed AS (
      SELECT seed.*
      FROM research_pool_seed_unfiltered seed
      {seed_ma_join}
    ),
    {cte_name} AS (
      SELECT
        code,
        MAX(name) AS name,
        MIN(source_priority) AS source_priority,
        SUBSTRING_INDEX(GROUP_CONCAT(source_kind ORDER BY source_priority ASC, source_rank ASC SEPARATOR ','), ',', 1) AS source_kind,
        MIN(source_rank) AS source_rank,
        SUBSTRING_INDEX(GROUP_CONCAT(source_rank_label ORDER BY source_priority ASC, source_rank ASC SEPARATOR ' / '), ' / ', 1) AS source_rank_label,
        SUBSTRING_INDEX(GROUP_CONCAT(source_trade_date ORDER BY source_priority ASC, source_rank ASC SEPARATOR ','), ',', 1) AS source_trade_date,
        MAX(limit_up_day_count) AS limit_up_day_count,
        MIN(gain_rank) AS rank_5d,
        MAX(gain_pct) AS pct_5d,
        MAX(latest_pct) AS latest_pct,
        ROW_NUMBER() OVER (
          ORDER BY MIN(source_priority) ASC, MIN(source_rank) ASC, code ASC
        ) AS pool_rank
      FROM research_pool_seed
      GROUP BY code
    )
    """


def research_pool_snapshot_cte(
    trade_date: str,
    *,
    rule: str = DEFAULT_RESEARCH_POOL_RULE,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    cte_name: str = "research_pool",
) -> str:
    """Read the materialized daily research pool for UI/runtime queries."""
    return f"""
    {cte_name} AS (
      SELECT
        code,
        stock_name AS name,
        source_priority,
        source_kind,
        source_rank,
        source_label AS source_rank_label,
        DATE_FORMAT(source_trade_date, '%Y-%m-%d') AS source_trade_date,
        limit_up_day_count,
        rank_5d,
        pct_5d,
        latest_pct,
        pool_rank
      FROM research_pool_items
      WHERE trade_date={sql_string(trade_date)}
        AND rule={sql_string(rule)}
        AND limit_up_days={sql_int(limit_up_days)}
        AND gain_period_days={sql_int(gain_period_days)}
        AND gain_top={sql_int(gain_top)}
    )
    """


def research_pool_codes(
    config: MySqlConfig,
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
) -> list[str]:
    sql = f"""
    WITH {research_pool_cte(
        trade_date,
        limit_up_days=limit_up_days,
        gain_period_days=gain_period_days,
        gain_top=gain_top,
        ma_mode=ma_mode,
    )}
    SELECT code
    FROM research_pool
    ORDER BY pool_rank ASC;
    """
    return [str(row[0]).strip() for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if row and str(row[0]).strip()]


def _snapshot_key_sql(trade_date: str, limit_up_days: int, gain_period_days: int, gain_top: int) -> str:
    return f"""
      trade_date={sql_string(trade_date)}
      AND rule={sql_string(DEFAULT_RESEARCH_POOL_RULE)}
      AND limit_up_days={int(limit_up_days)}
      AND gain_period_days={int(gain_period_days)}
      AND gain_top={int(gain_top)}
    """


def _dynamic_pool_rows(
    config: MySqlConfig,
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
) -> list[dict[str, object]]:
    sql = f"""
    WITH {research_pool_cte(
        trade_date,
        limit_up_days=limit_up_days,
        gain_period_days=gain_period_days,
        gain_top=gain_top,
        ma_mode=ma_mode,
    )}
    SELECT
      code,
      name,
      pool_rank,
      source_kind,
      source_priority,
      source_rank,
      source_rank_label,
      source_trade_date,
      limit_up_day_count,
      rank_5d,
      pct_5d,
      latest_pct
    FROM research_pool
    ORDER BY pool_rank ASC;
    """
    rows: list[dict[str, object]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 12:
            continue
        rows.append(
            {
                "code": str(row[0] or "").strip(),
                "stock_name": str(row[1] or "").strip(),
                "pool_rank": int(float(row[2] or 0)),
                "source_kind": str(row[3] or "").strip(),
                "source_priority": int(float(row[4] or 0)),
                "source_rank": int(float(row[5] or 0)),
                "source_label": str(row[6] or "").strip(),
                "source_trade_date": str(row[7] or "").strip(),
                "limit_up_day_count": int(float(row[8] or 0)),
                "rank_5d": None if row[9] in (None, "", "NULL") else int(float(row[9])),
                "pct_5d": None if row[10] in (None, "", "NULL") else float(row[10]),
                "latest_pct": None if row[11] in (None, "", "NULL") else float(row[11]),
            }
        )
    return [row for row in rows if row.get("code")]


def _persisted_pool_rows(
    config: MySqlConfig,
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
) -> list[dict[str, object]]:
    ensure_research_pool_tables(config)
    key = _snapshot_key_sql(trade_date, limit_up_days, gain_period_days, gain_top)
    sql = f"""
    SELECT
      code,
      stock_name,
      pool_rank,
      source_kind,
      source_priority,
      source_rank,
      source_label,
      COALESCE(DATE_FORMAT(source_trade_date, '%Y-%m-%d'), ''),
      limit_up_day_count,
      rank_5d,
      pct_5d,
      latest_pct
    FROM research_pool_items
    WHERE {key}
    ORDER BY pool_rank ASC;
    """
    rows: list[dict[str, object]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 12:
            continue
        rows.append(
            {
                "code": str(row[0] or "").strip(),
                "stock_name": str(row[1] or "").strip(),
                "pool_rank": int(float(row[2] or 0)),
                "source_kind": str(row[3] or "").strip(),
                "source_priority": int(float(row[4] or 0)),
                "source_rank": int(float(row[5] or 0)),
                "source_label": str(row[6] or "").strip(),
                "source_trade_date": str(row[7] or "").strip(),
                "limit_up_day_count": int(float(row[8] or 0)),
                "rank_5d": None if row[9] in (None, "", "NULL") else int(float(row[9])),
                "pct_5d": None if row[10] in (None, "", "NULL") else float(row[10]),
                "latest_pct": None if row[11] in (None, "", "NULL") else float(row[11]),
            }
        )
    return [row for row in rows if row.get("code")]


def materialize_research_pool_snapshot(
    config: MySqlConfig,
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
    force: bool = False,
) -> dict[str, object]:
    assert_weekday_trade_date(trade_date)
    ensure_research_pool_tables(config)
    resolved_ma_mode = normalize_research_pool_ma_mode(ma_mode)
    key = _snapshot_key_sql(trade_date, limit_up_days, gain_period_days, gain_top)
    if not force:
        existing = mysql_rows(
            run_mysql(
                config,
                f"""
                SELECT code_count
                FROM research_pool_snapshots
                WHERE {key}
                  AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.ma_mode')), '')={sql_string(resolved_ma_mode)};
                """,
                batch=True,
                raw=True,
            )
        )
        if existing:
            return {
                "trade_date": trade_date,
                "rule": DEFAULT_RESEARCH_POOL_RULE,
                "code_count": int(float(existing[0][0] or 0)),
                "generated": False,
            }

    rows = _dynamic_pool_rows(
        config,
        trade_date,
        limit_up_days=limit_up_days,
        gain_period_days=gain_period_days,
        gain_top=gain_top,
        ma_mode=resolved_ma_mode,
    )
    source_dates: dict[str, str] = {}
    for row in rows:
        source = str(row.get("source_kind") or "")
        source_date = str(row.get("source_trade_date") or "")
        if source and source_date:
            source_dates[source] = max(source_dates.get(source, ""), source_date)
    source_hash = hashlib.sha256(
        json.dumps(
            [(row.get("code"), row.get("source_kind"), row.get("source_rank"), row.get("source_trade_date")) for row in rows],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    params = {
        "limit_up_days": int(limit_up_days),
        "gain_period_days": int(gain_period_days),
        "gain_top": int(gain_top),
        "ma_mode": resolved_ma_mode,
    }
    statements = [
        "START TRANSACTION;",
        f"DELETE FROM research_pool_items WHERE {key};",
    ]
    if rows:
        values: list[str] = []
        for row in rows:
            raw_json = {
                "source_kind": row.get("source_kind"),
                "source_rank": row.get("source_rank"),
                "source_label": row.get("source_label"),
                "source_trade_date": row.get("source_trade_date"),
                "ma_mode": resolved_ma_mode,
            }
            values.append(
                "("
                + ",".join(
                    [
                        sql_string(trade_date),
                        sql_string(DEFAULT_RESEARCH_POOL_RULE),
                        sql_int(limit_up_days),
                        sql_int(gain_period_days),
                        sql_int(gain_top),
                        sql_string(row.get("code")),
                        sql_string(row.get("stock_name")),
                        sql_int(row.get("pool_rank")),
                        sql_string(row.get("source_kind")),
                        sql_int(row.get("source_priority")),
                        sql_int(row.get("source_rank")),
                        sql_string(row.get("source_label")),
                        sql_string(row.get("source_trade_date")) if row.get("source_trade_date") else "NULL",
                        sql_int(row.get("limit_up_day_count")),
                        "NULL" if row.get("rank_5d") is None else sql_int(row.get("rank_5d")),
                        sql_number(row.get("pct_5d")),
                        sql_number(row.get("latest_pct")),
                        sql_json(raw_json),
                    ]
                )
                + ")"
            )
        statements.append(
            """
            INSERT INTO research_pool_items(
              trade_date, rule, limit_up_days, gain_period_days, gain_top,
              code, stock_name, pool_rank, source_kind, source_priority, source_rank,
              source_label, source_trade_date, limit_up_day_count, rank_5d, pct_5d,
              latest_pct, raw_json
            ) VALUES
            """
            + ",".join(values)
            + """
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name),
              pool_rank=VALUES(pool_rank),
              source_kind=VALUES(source_kind),
              source_priority=VALUES(source_priority),
              source_rank=VALUES(source_rank),
              source_label=VALUES(source_label),
              source_trade_date=VALUES(source_trade_date),
              limit_up_day_count=VALUES(limit_up_day_count),
              rank_5d=VALUES(rank_5d),
              pct_5d=VALUES(pct_5d),
              latest_pct=VALUES(latest_pct),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    statements.append(
        f"""
        INSERT INTO research_pool_snapshots(
          trade_date, rule, limit_up_days, gain_period_days, gain_top,
          code_count, source_dates_json, params_json, source_hash, generated_at
        ) VALUES (
          {sql_string(trade_date)}, {sql_string(DEFAULT_RESEARCH_POOL_RULE)},
          {sql_int(limit_up_days)}, {sql_int(gain_period_days)}, {sql_int(gain_top)},
          {sql_int(len(rows))}, {sql_json(source_dates)}, {sql_json(params)}, {sql_string(source_hash)}, CURRENT_TIMESTAMP(3)
        )
        ON DUPLICATE KEY UPDATE
          code_count=VALUES(code_count),
          source_dates_json=VALUES(source_dates_json),
          params_json=VALUES(params_json),
          source_hash=VALUES(source_hash),
          generated_at=VALUES(generated_at),
          updated_at=CURRENT_TIMESTAMP(3);
        """
    )
    statements.append("COMMIT;")
    run_mysql(config, "\n".join(statements))
    return {
        "trade_date": trade_date,
        "rule": DEFAULT_RESEARCH_POOL_RULE,
        "code_count": len(rows),
        "source_dates": source_dates,
        "ma_mode": resolved_ma_mode,
        "source_hash": source_hash,
        "generated": True,
        "force": bool(force),
    }


def _norm_theme_text(value: object) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _latest_headline_themes(config: MySqlConfig, trade_date: str) -> list[dict[str, object]]:
    sql = f"""
    WITH latest_snapshot AS (
      SELECT snapshot_id
      FROM ths_homepage_headline_themes
      WHERE source={sql_string(HEADLINE_THEME_SOURCE)}
        AND trade_date <= {sql_string(trade_date)}
      ORDER BY trade_date DESC, collected_at DESC
      LIMIT 1
    )
    SELECT
      h.theme_name,
      h.rank_no,
      h.theme_id,
      h.index_code,
      h.block_name,
      COALESCE(h.block_gain, 0),
      DATE_FORMAT(h.trade_date, '%Y-%m-%d')
    FROM ths_homepage_headline_themes h
    JOIN latest_snapshot s ON s.snapshot_id=h.snapshot_id
    WHERE h.source={sql_string(HEADLINE_THEME_SOURCE)}
      AND COALESCE(h.theme_name, '') <> ''
    ORDER BY h.rank_no ASC, h.theme_name ASC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return []
    keys = ["theme_name", "theme_rank", "theme_id", "index_code", "block_name", "block_gain", "headline_trade_date"]
    return [dict(zip(keys, row)) for row in rows if len(row) >= len(keys) and str(row[0] or "").strip()]


def _research_pool_concept_rows(config: MySqlConfig, trade_date: str) -> list[dict[str, object]]:
    ensure_research_pool_tables(config)
    key = _snapshot_key_sql(
        trade_date,
        DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
        DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
        DEFAULT_RESEARCH_POOL_GAIN_TOP,
    )
    sql = f"""
    SELECT
      rp.code,
      COALESCE(NULLIF(c.stock_name, ''), rp.stock_name) AS stock_name,
      rp.pool_rank,
      rp.source_kind,
      c.id,
      c.concept_name,
      c.concept_id,
      c.fit_rank,
      c.reason_explain,
      DATE_FORMAT(c.fetched_at, '%Y-%m-%d %H:%i:%s') AS fetched_at
    FROM research_pool_items rp
    JOIN ths_stock_concept_explanations c ON c.code=rp.code
    WHERE {key}
      AND COALESCE(c.concept_name, '') <> ''
      AND COALESCE(c.reason_explain, '') <> ''
    ORDER BY rp.pool_rank ASC, c.fit_rank ASC, c.id ASC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    keys = [
        "code",
        "stock_name",
        "pool_rank",
        "pool_source_kind",
        "source_id",
        "concept_name",
        "concept_id",
        "fit_rank",
        "reason_explain",
        "fetched_at",
    ]
    return [dict(zip(keys, row)) for row in rows if len(row) >= len(keys) and str(row[0] or "").strip()]


def _best_headline_match(concept: dict[str, object], headline_themes: list[dict[str, object]]) -> tuple[dict[str, object] | None, str, float]:
    concept_name = str(concept.get("concept_name") or "").strip()
    reason = str(concept.get("reason_explain") or "").strip()
    concept_norm = _norm_theme_text(concept_name)
    reason_norm = _norm_theme_text(reason)
    best: tuple[float, int, dict[str, object] | None, str] = (0.0, 999, None, "")
    for theme in headline_themes:
        theme_name = str(theme.get("theme_name") or "").strip()
        block_name = str(theme.get("block_name") or "").strip()
        theme_norm = _norm_theme_text(theme_name)
        block_norm = _norm_theme_text(block_name)
        rank = int(float(theme.get("theme_rank") or 999))
        score = 0.0
        match_type = ""
        if concept_norm and concept_norm == theme_norm:
            score, match_type = 100.0, "exact_theme"
        elif concept_norm and block_norm and concept_norm == block_norm:
            score, match_type = 94.0, "exact_block"
        elif concept_norm and theme_norm and concept_norm in theme_norm:
            score, match_type = 88.0, "theme_contains_concept"
        elif concept_norm and theme_norm and theme_norm in concept_norm:
            score, match_type = 84.0, "concept_contains_theme"
        elif block_norm and concept_norm and (concept_norm in block_norm or block_norm in concept_norm):
            score, match_type = 80.0, "block_related"
        elif theme_norm and len(theme_norm) >= 2 and theme_norm in reason_norm:
            score, match_type = 72.0, "reason_contains_theme"
        if score and (score, -rank) > (best[0], -best[1]):
            best = (score, rank, theme, match_type)
    return best[2], best[3], best[0]


def materialize_research_pool_theme_members(config: MySqlConfig, trade_date: str, *, force: bool = False) -> dict[str, object]:
    ensure_research_pool_theme_tables(config)
    if not force:
        existing = mysql_rows(
            run_mysql(
                config,
                f"SELECT COUNT(*) FROM research_pool_theme_members WHERE trade_date={sql_string(trade_date)};",
                batch=True,
                raw=True,
            )
        )
        if existing and int(float(existing[0][0] or 0)) > 0:
            return {
                "trade_date": trade_date,
                "generated": False,
                "member_count": int(float(existing[0][0] or 0)),
            }
    materialize_research_pool_snapshot(config, trade_date, force=False)
    concept_rows = _research_pool_concept_rows(config, trade_date)
    headline_themes = _latest_headline_themes(config, trade_date)
    values: list[str] = []
    headline_count = 0
    fallback_count = 0
    distinct_codes: set[str] = set()
    distinct_themes: set[str] = set()
    for row in concept_rows:
        headline, match_type, match_score = _best_headline_match(row, headline_themes)
        if headline:
            theme_name = str(headline.get("theme_name") or "").strip()
            theme_rank = int(float(headline.get("theme_rank") or 999))
            is_headline = 1
            headline_count += 1
        else:
            theme_name = str(row.get("concept_name") or "").strip()
            theme_rank = 999
            is_headline = 0
            match_type = "fallback_concept"
            fit_rank = int(float(row.get("fit_rank") or 0))
            match_score = max(35.0, 68.0 - min(max(fit_rank, 0), 40))
            fallback_count += 1
        if not theme_name:
            continue
        raw_json = {
            "source": RESEARCH_POOL_THEME_SOURCE,
            "pool_source_kind": row.get("pool_source_kind"),
            "source_id": row.get("source_id"),
            "fetched_at": row.get("fetched_at"),
            "headline_theme": headline or {},
        }
        distinct_codes.add(str(row.get("code") or "").strip())
        distinct_themes.add(theme_name)
        values.append(
            "("
            + ",".join(
                [
                    sql_string(trade_date),
                    sql_string(row.get("code")),
                    sql_string(row.get("stock_name")),
                    sql_int(row.get("pool_rank")),
                    sql_string(row.get("pool_source_kind")),
                    sql_string(row.get("concept_name")),
                    sql_string(row.get("concept_id")),
                    sql_string(row.get("reason_explain")),
                    sql_int(row.get("fit_rank")),
                    sql_string(theme_name),
                    sql_int(theme_rank),
                    sql_int(is_headline),
                    sql_string(match_type),
                    sql_number(match_score),
                    sql_string("ths_stock_concept_explanations"),
                    sql_string(f"ths_stock_concept_explanations:{row.get('source_id')}"),
                    sql_json(raw_json),
                ]
            )
            + ")"
        )
    statements = [
        "START TRANSACTION;",
        f"DELETE FROM research_pool_theme_members WHERE trade_date={sql_string(trade_date)};",
    ]
    for idx in range(0, len(values), 300):
        chunk = values[idx : idx + 300]
        if not chunk:
            continue
        statements.append(
            """
            INSERT INTO research_pool_theme_members(
              trade_date, code, stock_name, pool_rank, pool_source_kind,
              concept_name, concept_id, reason_explain, fit_rank,
              theme_name, theme_rank, is_headline_theme, match_type, match_score,
              source_table, source_key, raw_json
            ) VALUES
            """
            + ",".join(chunk)
            + """
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name),
              pool_rank=VALUES(pool_rank),
              pool_source_kind=VALUES(pool_source_kind),
              concept_id=VALUES(concept_id),
              reason_explain=VALUES(reason_explain),
              fit_rank=VALUES(fit_rank),
              theme_rank=VALUES(theme_rank),
              is_headline_theme=VALUES(is_headline_theme),
              match_type=VALUES(match_type),
              match_score=VALUES(match_score),
              source_table=VALUES(source_table),
              source_key=VALUES(source_key),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    statements.append("COMMIT;")
    run_mysql(config, "\n".join(statements))
    return {
        "trade_date": trade_date,
        "generated": True,
        "member_count": len(values),
        "stock_count": len(distinct_codes),
        "theme_count": len(distinct_themes),
        "headline_theme_count": headline_count,
        "fallback_concept_count": fallback_count,
        "headline_dimension_count": len(headline_themes),
        "source": RESEARCH_POOL_THEME_SOURCE,
    }


def materialize_headline_theme_role_evidence(config: MySqlConfig, trade_date: str, *, force: bool = True) -> dict[str, object]:
    """Precompute mostly-static headline-theme payloads used by evidence detail.

    Real-time diffusion stays in stock_move_judgements.multi_theme_influence.
    This cache only carries the stock-to-headline-theme mapping and F10 concept
    explanation, so it can follow the upstream theme/member refresh cycle.
    """
    ensure_headline_theme_role_evidence_table(config)
    materialize_research_pool_theme_members(config, trade_date, force=False)
    day = sql_string(trade_date)
    if force:
        run_mysql(config, f"DELETE FROM stock_headline_theme_role_evidence WHERE trade_date={day};")
    sql = f"""
    SET SESSION group_concat_max_len=65536;
    INSERT INTO stock_headline_theme_role_evidence(
      trade_date, code, stock_name, roles, role_count, latest_source_updated_at, source_hash, generated_at, updated_at
    )
    WITH
    theme_member_counts AS (
      SELECT theme_name, COUNT(DISTINCT code) AS member_count
      FROM research_pool_theme_members
      WHERE trade_date=CAST({day} AS DATE)
        AND is_headline_theme=1
        AND COALESCE(theme_name, '') <> ''
      GROUP BY theme_name
    ),
    role_rows AS (
      SELECT
        rpm.code,
        MAX(rpm.stock_name) AS stock_name,
        rpm.theme_name,
        MIN(rpm.theme_rank) AS theme_rank,
        SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(rpm.concept_name, '') ORDER BY rpm.match_score DESC, rpm.fit_rank ASC SEPARATOR '\n'), '\n', 1) AS concept_name,
        MAX(rpm.match_score) AS match_score,
        SUBSTRING_INDEX(GROUP_CONCAT(NULLIF(rpm.reason_explain, '') ORDER BY rpm.match_score DESC, rpm.fit_rank ASC SEPARATOR '\n'), '\n', 1) AS reason_explain,
        '题材成员' AS role_label,
        NULL AS rank_leader,
        NULL AS rank_core,
        NULL AS pct_change,
        NULL AS speed,
        NULL AS amount,
        MAX(tmc.member_count) AS member_count,
        '' AS leader_code,
        '' AS leader_name,
        '' AS core_code,
        '' AS core_name,
        MAX(rpm.updated_at) AS latest_updated_at,
        CASE
          WHEN MAX(rpm.match_score) >= 90 OR COALESCE(MAX(tmc.member_count), 0) >= 3 THEN '强'
          WHEN MAX(rpm.match_score) >= 75 OR COALESCE(MAX(tmc.member_count), 0) >= 2 THEN '中'
          ELSE '弱'
        END AS explain_strength
      FROM research_pool_theme_members rpm
      LEFT JOIN theme_member_counts tmc ON tmc.theme_name=rpm.theme_name
      WHERE rpm.trade_date=CAST({day} AS DATE)
        AND rpm.is_headline_theme=1
        AND COALESCE(rpm.theme_name, '') <> ''
      GROUP BY rpm.code, rpm.theme_name
    ),
    packed AS (
      SELECT
        code,
        MAX(stock_name) AS stock_name,
        MAX(latest_updated_at) AS latest_source_updated_at,
        COUNT(*) AS role_count,
        CONCAT('[', GROUP_CONCAT(
          JSON_OBJECT(
            'theme_name', theme_name,
            'theme_rank', theme_rank,
            'concept_name', concept_name,
            'role_label', role_label,
            'rank_leader', rank_leader,
            'rank_core', rank_core,
            'pct_change', pct_change,
            'speed', speed,
            'amount_yi', ROUND(COALESCE(amount, 0) / 100000000, 2),
            'member_count', member_count,
            'leader_code', leader_code,
            'leader_name', leader_name,
            'core_code', core_code,
            'core_name', core_name,
            'reason_explain', reason_explain,
            'match_score', match_score,
            'explain_strength', explain_strength
          )
          ORDER BY theme_rank ASC, match_score DESC, theme_name ASC
          SEPARATOR ','
        ), ']') AS roles_text
      FROM role_rows
      GROUP BY code
    )
    SELECT
      CAST({day} AS DATE),
      code,
      stock_name,
      CAST(roles_text AS JSON),
      role_count,
      latest_source_updated_at,
      SHA2(roles_text, 256),
      CURRENT_TIMESTAMP(3),
      CURRENT_TIMESTAMP(3)
    FROM packed
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      roles=VALUES(roles),
      role_count=VALUES(role_count),
      latest_source_updated_at=VALUES(latest_source_updated_at),
      source_hash=VALUES(source_hash),
      generated_at=VALUES(generated_at),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*), COALESCE(SUM(role_count), 0)
            FROM stock_headline_theme_role_evidence
            WHERE trade_date=CAST({day} AS DATE);
            """,
            batch=True,
            raw=True,
        )
    )
    stock_count = int(float(rows[0][0] or 0)) if rows and rows[0] else 0
    role_count = int(float(rows[0][1] or 0)) if rows and rows[0] and len(rows[0]) > 1 else 0
    return {
        "trade_date": trade_date,
        "generated": True,
        "stock_count": stock_count,
        "role_count": role_count,
        "source": "stock_headline_theme_role_evidence",
    }


class ResearchPoolProvider:
    """Query active stock research pools from persisted source tables.

    The default bear/adjustment pool is recent 5-trading-day limit-up stocks
    plus 5-day gain Top30 stocks without a recent limit-up. Bull mode can add
    MA5/10/20/30 rising filters through ma_mode. Keeping this behind a provider
    avoids scattering pool SQL across collectors and scanners.
    """

    def __init__(
        self,
        config: MySqlConfig,
        *,
        default_periods: Iterable[int] | None = None,
        default_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    ) -> None:
        self.config = config
        self.default_periods = tuple(_clean_periods(default_periods))
        self.default_top = int(default_top)

    def latest_codes(
        self,
        trade_date: str,
        *,
        periods: Iterable[int] | None = None,
        period_days: int | None = None,
        top: int | None = None,
        ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
    ) -> list[str]:
        snapshot = self.latest_snapshot(trade_date, periods=periods, period_days=period_days, top=top, ma_mode=ma_mode)
        return list(snapshot.codes)

    def latest_code_set(
        self,
        trade_date: str,
        *,
        periods: Iterable[int] | None = None,
        period_days: int | None = None,
        top: int | None = None,
        ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
    ) -> set[str]:
        return set(
            self.latest_codes(
                trade_date,
                periods=periods,
                period_days=period_days,
                top=top,
                ma_mode=ma_mode,
            )
        )

    def latest_snapshot(
        self,
        trade_date: str,
        *,
        periods: Iterable[int] | None = None,
        period_days: int | None = None,
        top: int | None = None,
        ma_mode: str = DEFAULT_RESEARCH_POOL_MA_MODE,
    ) -> ResearchPoolSnapshot:
        pool_top = int(top or self.default_top)
        pool_period_days = int(period_days or DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS)
        resolved_ma_mode = normalize_research_pool_ma_mode(ma_mode)
        rows = _persisted_pool_rows(
            self.config,
            trade_date,
            limit_up_days=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
            gain_period_days=pool_period_days,
            gain_top=pool_top,
        )
        snapshot_params = mysql_rows(
            run_mysql(
                self.config,
                f"""
                SELECT COALESCE(JSON_UNQUOTE(JSON_EXTRACT(params_json, '$.ma_mode')), '')
                FROM research_pool_snapshots
                WHERE {_snapshot_key_sql(trade_date, DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS, pool_period_days, pool_top)}
                LIMIT 1;
                """,
                batch=True,
                raw=True,
            )
        )
        persisted_ma_mode = str(snapshot_params[0][0] or "") if snapshot_params and snapshot_params[0] else ""
        if not rows or persisted_ma_mode != resolved_ma_mode:
            materialize_research_pool_snapshot(
                self.config,
                trade_date,
                limit_up_days=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
                gain_period_days=pool_period_days,
                gain_top=pool_top,
                ma_mode=resolved_ma_mode,
                force=True,
            )
            rows = _persisted_pool_rows(
                self.config,
                trade_date,
                limit_up_days=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
                gain_period_days=pool_period_days,
                gain_top=pool_top,
            )
        codes: list[str] = []
        codes_by_source: dict[str, list[str]] = {}
        source_dates: dict[str, str] = {}
        for row in rows:
            code = str(row.get("code") or "").strip()
            source = str(row.get("source_kind") or "").strip()
            source_date = str(row.get("source_trade_date") or "").strip()
            if not code:
                continue
            codes.append(code)
            codes_by_source.setdefault(source, []).append(code)
            if source and source_date:
                source_dates[source] = max(source_dates.get(source, ""), source_date)
        return ResearchPoolSnapshot(
            trade_date=trade_date,
            periods=(pool_period_days,),
            top=pool_top,
            codes=tuple(codes),
            period_trade_dates={pool_period_days: source_dates.get("five_day_gain_top", "")},
            codes_by_period={pool_period_days: tuple(codes_by_source.get("five_day_gain_top", []))},
            source_dates=source_dates,
            codes_by_source={source: tuple(values) for source, values in codes_by_source.items()},
        )

    def previous_period_top_codes(
        self,
        trade_date: str,
        *,
        period_days: int = 3,
        top: int | None = None,
    ) -> tuple[str, list[str]]:
        pool_top = int(top or self.default_top)
        sql = f"""
        SELECT DATE_FORMAT(trade_date, '%Y-%m-%d'), code
        FROM research_pool_items
        WHERE trade_date = (
            SELECT MAX(trade_date)
            FROM research_pool_items
            WHERE trade_date < {sql_string(trade_date)}
              AND rule='recent_limit_up_or_5d_gain_top'
              AND gain_period_days = {int(period_days)}
          )
          AND rule='recent_limit_up_or_5d_gain_top'
          AND gain_period_days = {int(period_days)}
          AND pool_rank > 0
          AND pool_rank <= {pool_top}
        ORDER BY pool_rank ASC;
        """
        rows = mysql_rows(run_mysql(self.config, sql, batch=True, raw=True))
        if not rows:
            return "", []
        return rows[0][0], [row[1] for row in rows if len(row) >= 2 and row[1]]


__all__ = [
    "DEFAULT_RESEARCH_POOL_PERIODS",
    "DEFAULT_RESEARCH_POOL_RULE",
    "DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS",
    "DEFAULT_RESEARCH_POOL_GAIN_TOP",
    "DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS",
    "DEFAULT_RESEARCH_POOL_MA_MODE",
    "HEADLINE_THEME_SOURCE",
    "RESEARCH_POOL_MA_NONE",
    "RESEARCH_POOL_THEME_SOURCE",
    "ResearchPoolProvider",
    "ResearchPoolSnapshot",
    "ensure_research_pool_theme_tables",
    "ensure_research_pool_tables",
    "ensure_headline_theme_role_evidence_table",
    "materialize_research_pool_theme_members",
    "materialize_headline_theme_role_evidence",
    "materialize_research_pool_snapshot",
    "normalize_research_pool_ma_mode",
    "research_pool_system_label",
    "research_pool_codes",
    "research_pool_cte",
    "research_pool_snapshot_cte",
]
