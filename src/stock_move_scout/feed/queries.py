from __future__ import annotations

from datetime import date

from stock_move_scout.db import sql_string
from stock_move_scout.research_pool import research_pool_snapshot_cte


def _json_source_fields(
    source_table: str,
    source_key_expr: str,
    data_date_expr: str,
    updated_at_expr: str,
    source_generation: str,
) -> str:
    if source_generation == "intraday":
        availability = "intraday"
        evidence_group = "current_effective"
        evidence_role = "market_realtime"
        display_level = "secondary"
    elif source_generation == "after_close":
        availability = "after_close_confirm"
        evidence_group = "post_close_confirm"
        evidence_role = "post_close_confirm"
        display_level = "secondary"
    elif source_generation == "model_summary":
        availability = "async_supplement"
        evidence_group = "model_summary"
        evidence_role = "model_summary"
        display_level = "primary"
    else:
        availability = "async_supplement"
        evidence_group = "unknown"
        evidence_role = "model_supplement"
        display_level = "secondary"
    return f"""
              'source_table', '{source_table}',
              'source_key', {source_key_expr},
              'source_confidence', 'explicit',
              'data_date', {data_date_expr},
              'evidence_date', {data_date_expr},
              'updated_at', {updated_at_expr},
              'source_generation', '{source_generation}',
              'availability', '{availability}',
              'evidence_group', '{evidence_group}',
              'evidence_role', '{evidence_role}',
              'display_level', '{display_level}',
              'valid_status', 'watch',
              'source_registry', JSON_OBJECT(
                'source_table', '{source_table}',
                'source_generation', '{source_generation}',
                'availability', '{availability}',
                'evidence_group', '{evidence_group}',
                'update_cycle',
                CASE '{source_generation}'
                  WHEN 'intraday' THEN 'scan_loop'
                  WHEN 'after_close' THEN 'after_close_daily'
                  ELSE 'async_task'
                END,
                'data_date_policy',
                CASE '{source_generation}'
                  WHEN 'intraday' THEN 'event_day'
                  WHEN 'after_close' THEN 'latest_confirmed_trade_day'
                  ELSE 'model_snapshot_day'
                END
              ),"""


def trade_dates_sql() -> str:
    return """
    SELECT JSON_OBJECT(
      'latest', COALESCE(
        MAX(CASE WHEN day_text <= DATE_FORMAT(CURDATE(), '%Y-%m-%d') THEN day_text END),
        MAX(day_text),
        DATE_FORMAT(CURDATE(), '%Y-%m-%d')
      ),
      'dates', COALESCE(JSON_ARRAYAGG(day_text), JSON_ARRAY())
    )
    FROM (
      SELECT DISTINCT DATE_FORMAT(day_value, '%Y-%m-%d') AS day_text
      FROM (
        SELECT trade_date AS day_value
        FROM market_width_snapshots
        WHERE source='stock_daily_bars_close'
           OR ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999')
            OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999'))
        UNION ALL
        SELECT trade_date AS day_value
        FROM stock_daily_bars
        GROUP BY trade_date
        HAVING COUNT(*) >= 1000
        UNION ALL
        SELECT DATE(scanned_at) AS day_value FROM scan_runs WHERE accepted=1
        UNION ALL
        SELECT DATE(ended_at) AS day_value FROM windows WHERE status='done' AND aggregate_count > 0
        UNION ALL
        SELECT trade_date AS day_value FROM research_pool_items
        UNION ALL
        SELECT trade_date AS day_value FROM stock_root_evidence_cache
      ) days
      WHERE day_value IS NOT NULL
        AND WEEKDAY(day_value) < 5
      ORDER BY day_text DESC
    ) ordered_days;
    """


def latest_window_sql(trade_date: str | None = "") -> str:
    where_day = f"AND DATE(ended_at)={sql_string(trade_date)}" if trade_date else ""
    return f"""
    SELECT COALESCE(JSON_OBJECT(
      'id', id,
      'window_id', window_id,
      'started_at', DATE_FORMAT(started_at, '%Y-%m-%d %H:%i:%s'),
      'ended_at', DATE_FORMAT(ended_at, '%Y-%m-%d %H:%i:%s'),
      'status', status,
      'accepted_scan_count', accepted_scan_count,
      'aggregate_count', aggregate_count,
      'evidence_candidate_count', evidence_candidate_count,
      'duration_ms', duration_ms
    ), JSON_OBJECT())
    FROM windows
    WHERE status='done'
      AND aggregate_count > 0
      {where_day}
    ORDER BY ended_at DESC
    LIMIT 1;
    """


def window_top10_sql() -> str:
    return """
    SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
    FROM (
      SELECT JSON_OBJECT(
        'rank_no', wm.rank_no,
        'code', wm.code,
        'name', wm.name,
        'appearance_count', wm.appearance_count,
        'appearance_rate', wm.appearance_rate,
        'max_speed', wm.max_speed,
        'max_pct_change', wm.max_pct_change,
        'latest_pct_change', wm.latest_pct_change,
        'amount_yi', ROUND(COALESCE(wm.amount, 0) / 100000000, 2),
        'window_score', wm.window_score,
        'industry', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.industry')), s.industry, ''),
        'sub_industry', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.sub_industry')), s.sub_industry, ''),
        'concepts', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.concepts')), ''),
        'community_status', COALESCE(el.community_status, ''),
        'community_claim', COALESCE(NULLIF(el.community_main_claim, ''), ce.main_claim, ''),
        'evidence_strength', COALESCE(el.evidence_strength, ''),
        'hard_evidence', COALESCE(el.hard_evidence_summary, ''),
        'why', COALESCE(el.why_hypothesis, ''),
        'post_title', COALESCE(gp.title, ''),
        'post_hook', COALESCE(gp.hook, ''),
        'post_content', COALESCE(gp.content, '')
      ) AS item
      FROM window_movers wm
      JOIN (
        SELECT id
        FROM windows
        WHERE status='done'
        ORDER BY ended_at DESC
        LIMIT 1
      ) latest ON latest.id = wm.window_id
      LEFT JOIN stocks s ON s.code = wm.code
      LEFT JOIN evidence_layers el ON el.window_id = wm.window_id AND el.code = wm.code
      LEFT JOIN community_evidence ce ON ce.window_id = wm.window_id AND ce.code = wm.code
      LEFT JOIN generated_posts gp ON gp.window_id = wm.window_id AND gp.code = wm.code AND gp.post_type='dav_info_gap'
      WHERE COALESCE(s.is_st, 0) = 0
        AND wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
      ORDER BY wm.rank_no ASC
      LIMIT 10
    ) ranked;
    """


def auction_top10_sql() -> str:
    return """
    SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
    FROM (
      SELECT JSON_OBJECT(
        'rank_no', final_candidate_rank,
        'code', code,
        'name', stock_name,
        'trend_score', trend_score,
        'trend_label', trend_label,
        'last_auction_pct', last_auction_pct,
        'pct_delta', pct_delta,
        'last_amount_yi', ROUND(COALESCE(last_auction_amount, 0) / 100000000, 2),
        'last_seal_yi', ROUND(COALESCE(last_seal_amount, 0) / 100000000, 2),
        'theme_score', theme_score,
        'key_points', COALESCE(JSON_EXTRACT(key_points, '$'), JSON_ARRAY()),
        'action_hint', action_hint
      ) AS item
      FROM auction_trend_summary
      WHERE trade_date = CURDATE()
        AND COALESCE(limit_up_count, 0) > 0
        AND COALESCE(last_seal_amount, 0) > 0
        AND COALESCE(last_auction_pct, 0) >= 9.5
      ORDER BY last_seal_amount DESC, final_candidate_rank ASC
      LIMIT 3
    ) ranked;
    """


def _leaderboard_sql_legacy(trade_date: str | None = "") -> str:
    return leaderboard_sql(trade_date)


def leaderboard_sql(trade_date: str | None = "") -> str:
    day = sql_string(trade_date or date.today().strftime("%Y-%m-%d"))
    main_a_regexp = "'^(000|001|002|003|300|301|600|601|603|605|688|689)'"
    return f"""
    WITH
    market_universe AS (
      SELECT
        s.code,
        COALESCE(
          CASE WHEN COALESCE(s.name, '') <> '' AND s.name NOT REGEXP '^[?]+$' THEN s.name END,
          (
            SELECT cp.stock_name
            FROM stock_company_profiles cp
            WHERE cp.code=s.code
              AND COALESCE(cp.stock_name, '') <> ''
              AND cp.stock_name NOT REGEXP '^[?]+$'
            LIMIT 1
          ),
          (
            SELECT rs.stock_name
            FROM ths_root_snapshots rs
            WHERE rs.code=s.code
              AND COALESCE(rs.stock_name, '') <> ''
              AND rs.stock_name NOT REGEXP '^[?]+$'
            ORDER BY rs.fetched_at DESC, rs.id DESC
            LIMIT 1
          ),
          (
            SELECT lu.stock_name
            FROM limit_up_pool_items lu
            WHERE lu.code=s.code
              AND COALESCE(lu.stock_name, '') <> ''
              AND lu.stock_name NOT REGEXP '^[?]+$'
            ORDER BY lu.trade_date DESC, lu.updated_at DESC
            LIMIT 1
          ),
          s.name
        ) AS name
      FROM stocks s
      WHERE s.code REGEXP {main_a_regexp}
        AND COALESCE(is_st, 0)=0
        AND s.name NOT LIKE '%ST%'
        AND s.name NOT LIKE '%退市%'
    ),
    research_pool_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM research_pool_items
      WHERE trade_date <= {day}
        AND rule='recent_limit_up_or_5d_gain_top'
        AND limit_up_days=5
        AND gain_period_days=5
        AND gain_top=30
    ),
    leader_research_pool AS (
      SELECT
        rp.code,
        COALESCE(CASE WHEN COALESCE(rp.stock_name, '') <> '' AND rp.stock_name NOT REGEXP '^[?]+$' THEN rp.stock_name END, u.name) AS name,
        rp.pool_rank,
        rp.source_kind,
        rp.source_label,
        rp.limit_up_day_count,
        NULL AS rank_3d,
        rp.rank_5d AS rank_5d,
        NULL AS rank_10d,
        NULL AS pct_3d,
        rp.pct_5d AS pct_5d,
        NULL AS pct_10d,
        COALESCE(db.pct_change, rp.latest_pct, 0) AS today_pct
      FROM research_pool_items rp
      JOIN research_pool_day d ON d.trade_date=rp.trade_date
      JOIN market_universe u ON u.code=rp.code
      LEFT JOIN stock_daily_bars db
        ON db.code=rp.code
       AND db.trade_date=CAST({day} AS DATE)
      WHERE rp.rule='recent_limit_up_or_5d_gain_top'
        AND rp.limit_up_days=5
        AND rp.gain_period_days=5
        AND rp.gain_top=30
    ),
    headline_snapshot AS (
      SELECT
        snapshot_id,
        trade_date,
        source,
        CASE
          WHEN source='ths_homepage_headline_frozen' AND trade_date=CAST({day} AS DATE) THEN 'post_close_frozen'
          WHEN source='ths_homepage_headline_frozen' THEN 'frozen_fallback'
          ELSE 'live_fallback'
        END AS snapshot_status
      FROM ths_homepage_headline_themes
      WHERE trade_date <= {day}
        AND source IN ('ths_homepage_headline_frozen', 'ths_homepage_headline')
      ORDER BY
        CASE
          WHEN source='ths_homepage_headline_frozen' AND trade_date=CAST({day} AS DATE) THEN 0
          WHEN source='ths_homepage_headline_frozen' THEN 1
          WHEN source='ths_homepage_headline' AND trade_date=CAST({day} AS DATE) THEN 2
          ELSE 3
        END ASC,
        trade_date DESC,
        collected_at DESC
      LIMIT 1
    ),
    headline_themes AS (
      SELECT
        h.snapshot_id,
        h.rank_no AS theme_rank,
        h.theme_id,
        h.theme_name,
        h.index_code,
        REPLACE(REPLACE(REPLACE(h.theme_name, '概念', ''), '板块', ''), '产业', '') AS theme_core
      FROM ths_homepage_headline_themes h
      JOIN headline_snapshot s ON s.snapshot_id=h.snapshot_id
      WHERE COALESCE(h.theme_name, '') <> ''
    ),
    headline_theme_member_candidates AS (
      SELECT
        h.theme_rank,
        h.theme_name,
        h.index_code,
        u.code,
        COALESCE(CASE WHEN COALESCE(m.stock_name, '') <> '' AND m.stock_name NOT REGEXP '^[?]+$' THEN m.stock_name END, u.name) AS name,
        m.gain AS rise_percent,
        m.stock_rank,
        IF(p.code IS NULL, 0, 1) AS in_research_pool,
        p.rank_3d,
        p.rank_5d,
        p.rank_10d,
        p.pct_3d,
        p.pct_5d,
        p.pct_10d,
        COALESCE(m.gain, p.today_pct, 0) AS today_pct,
        p.source_kind,
        p.source_label,
        p.limit_up_day_count,
        p.pool_rank
      FROM headline_themes h
      JOIN ths_homepage_headline_theme_members m
        ON m.snapshot_id=h.snapshot_id
       AND m.theme_id=h.theme_id
      JOIN market_universe u ON u.code=m.stock_code
      LEFT JOIN leader_research_pool p ON p.code=m.stock_code
    ),
    theme_top3 AS (
      SELECT
        theme_name,
        MIN(theme_rank) AS theme_rank,
        COUNT(DISTINCT code) AS member_count,
        COUNT(DISTINCT IF(in_research_pool=1, code, NULL)) AS research_pool_member_count,
        ROUND(AVG(COALESCE(rise_percent, 0)), 2) AS avg_rise,
        ROUND(MAX(COALESCE(rise_percent, 0)), 2) AS max_rise
      FROM headline_theme_member_candidates
      GROUP BY theme_name
      HAVING COUNT(DISTINCT IF(in_research_pool=1, code, NULL)) > 0
    ),
    scope_codes AS (
      SELECT
        'market' AS scope_key,
        '研究池' AS scope_name,
        1 AS scope_rank,
        NULL AS theme_name,
        NULL AS theme_avg_rise,
        NULL AS theme_member_count,
        NULL AS theme_research_pool_member_count,
        p.code,
        p.name,
        p.rank_3d AS rank_3d,
        p.rank_5d AS rank_5d,
        p.rank_10d AS rank_10d,
        p.pct_3d AS pct_3d,
        p.pct_5d AS pct_5d,
        p.pct_10d AS pct_10d,
        p.today_pct AS today_pct,
        p.source_kind,
        p.source_label,
        p.limit_up_day_count,
        p.pool_rank
      FROM leader_research_pool p
      UNION ALL
      SELECT
        CONCAT('theme:', t.theme_name) AS scope_key,
        t.theme_name AS scope_name,
        10 + t.theme_rank AS scope_rank,
        t.theme_name,
        t.avg_rise AS theme_avg_rise,
        t.member_count AS theme_member_count,
        t.research_pool_member_count AS theme_research_pool_member_count,
        p.code,
        p.name,
        MAX(p.rank_3d) AS rank_3d,
        MAX(p.rank_5d) AS rank_5d,
        MAX(p.rank_10d) AS rank_10d,
        MAX(p.pct_3d) AS pct_3d,
        MAX(p.pct_5d) AS pct_5d,
        MAX(p.pct_10d) AS pct_10d,
        MAX(p.today_pct) AS today_pct,
        MAX(p.source_kind) AS source_kind,
        MAX(p.source_label) AS source_label,
        MAX(p.limit_up_day_count) AS limit_up_day_count,
        MAX(p.pool_rank) AS pool_rank
      FROM theme_top3 t
      JOIN headline_theme_member_candidates p
        ON p.theme_name=t.theme_name
       AND p.in_research_pool=1
      GROUP BY t.theme_rank, t.theme_name, t.avg_rise, t.member_count, t.research_pool_member_count, p.code, p.name
    ),
    scope_meta AS (
      SELECT
        'market' AS scope_key,
        '研究池' AS scope_name,
        1 AS scope_rank,
        NULL AS theme_name,
        NULL AS theme_avg_rise,
        NULL AS theme_member_count,
        NULL AS theme_research_pool_member_count,
        COUNT(DISTINCT code) AS universe_count
      FROM scope_codes
      WHERE scope_key='market'
      UNION ALL
      SELECT
        CONCAT('theme:', t.theme_name) AS scope_key,
        t.theme_name AS scope_name,
        10 + t.theme_rank AS scope_rank,
        t.theme_name,
        t.avg_rise AS theme_avg_rise,
        t.member_count AS theme_member_count,
        t.research_pool_member_count AS theme_research_pool_member_count,
        COALESCE((
          SELECT COUNT(DISTINCT sc.code)
          FROM scope_codes sc
          WHERE sc.scope_key=CONCAT('theme:', t.theme_name)
      ), 0) AS universe_count
      FROM theme_top3 t
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
      JOIN (SELECT DISTINCT code FROM scope_codes) p ON p.code=i.code
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
          sc.scope_key,
          sc.scope_name,
          sc.code,
          sc.name,
          CAST({day} AS DATE) AS trade_date,
          e.first_limit_at,
          e.limit_amount,
          'limit_up' AS today_rank_kind,
          e.source_name,
          sc.today_pct,
          ROW_NUMBER() OVER (
            PARTITION BY sc.scope_key
            ORDER BY
              e.first_limit_at ASC,
              e.limit_amount DESC,
              sc.code ASC
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
        sc.scope_key,
        sc.scope_name,
        sc.code,
        sc.name,
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
        scope_key,
        MAX(scope_name) AS scope_name,
        code,
        MAX(name) AS name,
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
      GROUP BY scope_key, code
    ),
    today_limit_days_ranked AS (
      SELECT *
      FROM (
        SELECT
          scope_key,
          scope_name,
          code,
          name,
          trade_date,
          limit_up_days,
          limit_up_stat,
          limit_amount,
          limit_time,
          source_name,
          ROW_NUMBER() OVER (
            PARTITION BY scope_key
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
          sc.scope_key,
          sc.scope_name,
          sc.code,
          sc.name,
          e.trade_date,
          e.first_limit_at,
          e.limit_amount,
          e.source_name,
          ROW_NUMBER() OVER (
            PARTITION BY sc.scope_key, sc.code
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
            PARTITION BY scope_key
            ORDER BY trade_date ASC, first_limit_at ASC, limit_amount DESC, code ASC
          ) AS rank_no
        FROM first_10d_per_code
      ) ranked
      WHERE rank_no <= 5
    ),
    dimension_scores AS (
      SELECT
        scope_key,
        scope_name,
        code,
        name,
        'today_limit' AS dimension,
        '日内主动性' AS dimension_label,
        rank_no,
        6 - rank_no AS score,
        IF(today_rank_kind='limit_up', ROUND(limit_amount / 100000000, 2), today_pct) AS value_num,
        IF(
          today_rank_kind='limit_up',
          DATE_FORMAT(first_limit_at, '%H:%i:%s'),
          CONCAT('今日', ROUND(today_pct, 2), '%')
        ) AS value_text,
        source_name,
        DATE_FORMAT(trade_date, '%Y-%m-%d') AS data_date
      FROM today_limit_ranked
      UNION ALL
      SELECT
        scope_key,
        scope_name,
        code,
        name,
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
        scope_key,
        scope_name,
        code,
        name,
        'first_limit_10d' AS dimension,
        '阶段先手性' AS dimension_label,
        rank_no,
        6 - rank_no AS score,
        ROUND(limit_amount / 100000000, 2) AS value_num,
        CONCAT(DATE_FORMAT(trade_date, '%m-%d'), ' ', DATE_FORMAT(first_limit_at, '%H:%i:%s')) AS value_text,
        source_name,
        DATE_FORMAT(trade_date, '%Y-%m-%d') AS data_date
      FROM first_10d_ranked
      UNION ALL
      SELECT
        scope_key,
        scope_name,
        code,
        name,
        'trend_strength' AS dimension,
        '趋势强度' AS dimension_label,
        COALESCE(rank_5d, pool_rank) AS rank_no,
        GREATEST(1, 31 - COALESCE(rank_5d, 30)) AS score,
        pct_5d AS value_num,
        CONCAT('5日涨幅#', COALESCE(rank_5d, pool_rank), ' / ', ROUND(COALESCE(pct_5d, 0), 2), '%') AS value_text,
        'research_pool_items' AS source_name,
        DATE_FORMAT(CAST({day} AS DATE), '%Y-%m-%d') AS data_date
      FROM scope_codes
      WHERE source_kind='five_day_gain_top'
    ),
    dimension_summary AS (
      SELECT
        scope_key,
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
      GROUP BY scope_key, code
    ),
    scored AS (
      SELECT
        sc.scope_key,
        MAX(sc.scope_name) AS scope_name,
        sc.code,
        MAX(sc.name) AS name,
        COALESCE(MAX(ds.total_score), 0) AS total_score,
        COALESCE(MAX(ds.today_limit_score), 0) AS today_limit_score,
        COALESCE(MAX(ds.first_limit_10d_score), 0) AS first_limit_10d_score,
        COALESCE(MAX(ds.limit_up_days_score), 0) AS limit_up_days_score,
        MAX(ds.today_limit_rank) AS today_limit_rank,
        MAX(ds.first_limit_10d_rank) AS first_limit_10d_rank,
        MAX(ds.limit_up_days_rank) AS limit_up_days_rank,
        COALESCE(MAX(ds.today_limit_time), '') AS today_limit_time,
        COALESCE(MAX(ds.first_limit_10d_time), '') AS first_limit_10d_time,
        COALESCE(MAX(ds.limit_up_days_text), '') AS limit_up_days_text,
        MAX(ds.today_limit_amount_yi) AS today_limit_amount_yi,
        MAX(ds.first_limit_10d_amount_yi) AS first_limit_10d_amount_yi,
        MAX(ds.limit_up_days) AS limit_up_days,
        MAX(sc.rank_3d) AS rank_3d,
        MAX(sc.rank_5d) AS rank_5d,
        MAX(sc.rank_10d) AS rank_10d,
        MAX(sc.pct_3d) AS pct_3d,
        MAX(sc.pct_5d) AS pct_5d,
        MAX(sc.pct_10d) AS pct_10d,
        MAX(sc.today_pct) AS today_pct,
        MAX(sc.source_kind) AS source_kind,
        MAX(sc.source_label) AS source_label,
        MAX(sc.limit_up_day_count) AS limit_up_day_count,
        MAX(sc.pool_rank) AS pool_rank
      FROM scope_codes sc
      LEFT JOIN dimension_summary ds
        ON ds.scope_key=sc.scope_key
       AND ds.code=sc.code
      GROUP BY sc.scope_key, sc.code
    ),
    leaders AS (
      SELECT *
      FROM (
        SELECT
          scored.*,
          IF(source_kind='five_day_gain_top', 'trend', 'emotion') AS pool_type,
          IF(source_kind='five_day_gain_top', '趋势票', '情绪票') AS pool_type_label,
          ROW_NUMBER() OVER (
            PARTITION BY scope_key, IF(source_kind='five_day_gain_top', 'trend', 'emotion')
            ORDER BY
              IF(source_kind='five_day_gain_top', COALESCE(rank_5d, 999999), 0) ASC,
              IF(source_kind='five_day_gain_top', COALESCE(pct_5d, -999), total_score) DESC,
              IF(source_kind='five_day_gain_top', COALESCE(today_pct, -999), limit_up_days_score) DESC,
              IF(source_kind='five_day_gain_top', 0, today_limit_score) DESC,
              IF(source_kind='five_day_gain_top', 0, first_limit_10d_score) DESC,
              IF(source_kind='five_day_gain_top', 999999, COALESCE(limit_up_days_rank, 999999)) ASC,
              IF(source_kind='five_day_gain_top', 999999, COALESCE(today_limit_rank, 999999)) ASC,
              IF(source_kind='five_day_gain_top', 999999, COALESCE(first_limit_10d_rank, 999999)) ASC,
              today_pct DESC,
              code ASC
          ) AS leader_rank
        FROM scored
      ) ranked_leader
      WHERE (pool_type='emotion' AND leader_rank <= 3)
         OR (pool_type='trend' AND leader_rank <= 1)
    )
    SELECT JSON_OBJECT(
      'trade_date', DATE_FORMAT(CAST({day} AS DATE), '%Y-%m-%d'),
      'rule', '研究池拆为情绪票和趋势票：情绪票=近5日涨停，展示Top3；趋势票=近5日无涨停且5日涨幅Top30，展示Top1。情绪票按连板辨识度、日内主动性、阶段先手性排序；趋势票按5日涨幅排名和涨幅强度排序。',
      'theme_rule', '全市场榜=研究池全集；主题榜=同花顺首页头条题材成分 ∩ 研究池，主题内部单独重排',
      'headline_theme_snapshot_id', COALESCE((SELECT snapshot_id FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_trade_date', COALESCE((SELECT DATE_FORMAT(trade_date, '%Y-%m-%d') FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_source', COALESCE((SELECT source FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_status', COALESCE((SELECT snapshot_status FROM headline_snapshot LIMIT 1), ''),
      'scopes', COALESCE((
        SELECT JSON_ARRAYAGG(scope_item)
        FROM (
          SELECT JSON_OBJECT(
            'scope_key', sm.scope_key,
            'scope_name', sm.scope_name,
            'scope_rank', sm.scope_rank,
            'theme_name', COALESCE(sm.theme_name, ''),
            'theme_avg_rise', sm.theme_avg_rise,
            'theme_member_count', sm.theme_member_count,
            'theme_research_pool_member_count', sm.theme_research_pool_member_count,
            'universe_count', sm.universe_count,
            'leaders', COALESCE((
              SELECT JSON_ARRAYAGG(JSON_OBJECT(
                'rank_no', l.leader_rank,
                'code', l.code,
                'name', l.name,
                'pool_type', l.pool_type,
                'pool_type_label', l.pool_type_label,
                'source_kind', l.source_kind,
                'source_label', l.source_label,
                'pool_rank', l.pool_rank,
                'company_highlights', COALESCE((
                  SELECT cp.company_highlights
                  FROM stock_company_profiles cp
                  WHERE cp.code=l.code
                  LIMIT 1
                ), ''),
                'active_fact_summary', COALESCE((
                  SELECT COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, ''))
                  FROM async_evidence_summaries aes
                  WHERE aes.trade_date=CAST({day} AS DATE)
                    AND aes.code=l.code
                    AND EXISTS (
                      SELECT 1
                      FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                    )
                  LIMIT 1
                ), ''),
                'active_facts', COALESCE((
                  SELECT JSON_ARRAYAGG(JSON_OBJECT(
                    'date', COALESCE(DATE_FORMAT(ef.fact_date, '%Y-%m-%d'), ''),
                    'title', COALESCE(NULLIF(ef.fact_subtype, ''), NULLIF(ef.fact_title, ''), '有效事实'),
                    'body', LEFT(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ef.payload, '$.display_body')), ''), NULLIF(ef.fact_body, ''), ef.fact_title, ''), 220),
                    'lines', COALESCE(JSON_EXTRACT(ef.payload, '$.display_lines'), JSON_ARRAY())
                  ))
                  FROM stock_effective_facts ef
                  WHERE ef.trade_date=CAST({day} AS DATE)
                    AND ef.code=l.code
                    AND ef.evidence_group='current_effective'
                    AND ef.valid_status='active'
                    AND ef.display_level <> 'hidden'
                ), '[]'),
                'kpl_limit_reason', COALESCE((
                  SELECT r.reason_text
                  FROM kpl_replay_limit_theme_stocks r
                  WHERE r.code=l.code
                    AND r.trade_date <= CAST({day} AS DATE)
                    AND r.reason_date <= CAST({day} AS DATE)
                    AND r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
                    AND COALESCE(r.reason_text, '') <> ''
                  ORDER BY r.reason_date DESC, r.captured_at DESC, r.theme_rank ASC
                  LIMIT 1
                ), (
                  SELECT r.reason_text
                  FROM kpl_stock_limit_up_reasons r
                  WHERE r.code=l.code
                    AND r.reason_date <= CAST({day} AS DATE)
                    AND r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
                    AND COALESCE(r.reason_text, '') <> ''
                  ORDER BY r.reason_date DESC, r.captured_at DESC
                  LIMIT 1
                ), ''),
                'kpl_limit_reason_date', COALESCE((
                  SELECT DATE_FORMAT(r.reason_date, '%Y-%m-%d')
                  FROM kpl_replay_limit_theme_stocks r
                  WHERE r.code=l.code
                    AND r.trade_date <= CAST({day} AS DATE)
                    AND r.reason_date <= CAST({day} AS DATE)
                    AND r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
                    AND COALESCE(r.reason_text, '') <> ''
                  ORDER BY r.reason_date DESC, r.captured_at DESC, r.theme_rank ASC
                  LIMIT 1
                ), (
                  SELECT DATE_FORMAT(r.reason_date, '%Y-%m-%d')
                  FROM kpl_stock_limit_up_reasons r
                  WHERE r.code=l.code
                    AND r.reason_date <= CAST({day} AS DATE)
                    AND r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
                    AND COALESCE(r.reason_text, '') <> ''
                  ORDER BY r.reason_date DESC, r.captured_at DESC
                  LIMIT 1
                ), ''),
                'active_fact_count', COALESCE((
                  SELECT COUNT(*)
                  FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                ), 0),
                'main_business', COALESCE((
                  SELECT cp.main_business
                  FROM stock_company_profiles cp
                  WHERE cp.code=l.code
                  LIMIT 1
                ), ''),
                'sw_industry', COALESCE((
                  SELECT cp.sw_industry
                  FROM stock_company_profiles cp
                  WHERE cp.code=l.code
                  LIMIT 1
                ), ''),
                'concept_tags', COALESCE((
                  SELECT cp.concept_tags
                  FROM stock_company_profiles cp
                  WHERE cp.code=l.code
                  LIMIT 1
                ), ''),
                'headline_theme_tags', COALESCE((
                  SELECT GROUP_CONCAT(DISTINCT htm.theme_name ORDER BY htm.theme_rank ASC SEPARATOR '、')
                  FROM headline_theme_member_candidates htm
                  WHERE htm.code=l.code
                    AND htm.in_research_pool=1
                    AND COALESCE(htm.theme_name, '') <> ''
                ), ''),
                'theme_concept_explain', IF(COALESCE(sm.theme_name, '')='', '', COALESCE((
                  SELECT e.reason_explain
                  FROM ths_stock_concept_explanations e
                  WHERE e.code=l.code
                    AND COALESCE(e.reason_explain, '') <> ''
                    AND (
                         e.concept_name=sm.theme_name
                      OR e.concept_name LIKE CONCAT('%', REPLACE(REPLACE(REPLACE(sm.theme_name, '概念', ''), '板块', ''), '产业', ''), '%')
                      OR sm.theme_name LIKE CONCAT('%', REPLACE(REPLACE(REPLACE(e.concept_name, '概念', ''), '板块', ''), '产业', ''), '%')
                    )
                  ORDER BY e.fit_rank ASC, e.updated_at DESC
                  LIMIT 1
                ), '')),
                'total_score', l.total_score,
                'today_limit_score', l.today_limit_score,
                'first_limit_10d_score', l.first_limit_10d_score,
                'limit_up_days_score', l.limit_up_days_score,
                'today_limit_rank', l.today_limit_rank,
                'first_limit_10d_rank', l.first_limit_10d_rank,
                'limit_up_days_rank', l.limit_up_days_rank,
                'today_limit_time', l.today_limit_time,
                'first_limit_10d_time', l.first_limit_10d_time,
                'limit_up_days_text', l.limit_up_days_text,
                'today_limit_amount_yi', l.today_limit_amount_yi,
                'first_limit_10d_amount_yi', l.first_limit_10d_amount_yi,
                'limit_up_days', l.limit_up_days,
                'today_pct', l.today_pct,
                'rank_3d', l.rank_3d,
                'rank_5d', l.rank_5d,
                'rank_10d', l.rank_10d,
                'pct_3d', l.pct_3d,
                'pct_5d', l.pct_5d,
                'pct_10d', l.pct_10d,
                'dimensions', COALESCE((
                  SELECT JSON_ARRAYAGG(JSON_OBJECT(
                    'dimension', d.dimension,
                    'label', d.dimension_label,
                    'rank_no', d.rank_no,
                    'score', d.score,
                    'value_num', d.value_num,
                    'value_text', d.value_text,
                    'source_name', d.source_name,
                    'data_date', COALESCE(d.data_date, '')
                  ))
                  FROM dimension_scores d
                  WHERE d.scope_key=l.scope_key
                    AND d.code=l.code
                ), JSON_ARRAY())
              ))
              FROM leaders l
              WHERE l.scope_key=sm.scope_key
              ORDER BY l.leader_rank ASC
            ), JSON_ARRAY())
          ) AS scope_item
          FROM scope_meta sm
          ORDER BY sm.scope_rank ASC
        ) ordered_scopes
      ), JSON_ARRAY())
    ) AS payload;
    """


def _replace_sql_block(sql: str, start_marker: str, end_marker: str, replacement: str) -> str:
    start = sql.find(start_marker)
    end = sql.find(end_marker, start)
    if start < 0 or end < 0:
        raise ValueError(f"SQL block markers not found: {start_marker!r} -> {end_marker!r}")
    return sql[:start] + replacement + sql[end:]


def kpl_leaderboard_sql(trade_date: str | None = "") -> str:
    day = sql_string(trade_date or date.today().strftime("%Y-%m-%d"))
    sql = leaderboard_sql(trade_date)
    kpl_theme_block = f"""
    kpl_plate_snapshot AS (
      SELECT trade_date, captured_at, source_table
      FROM (
        SELECT
          trade_date,
          captured_at,
          'kpl_plate_featured_strengths' AS source_table,
          0 AS source_priority,
          COUNT(*) AS row_count,
          SUM(IF(COALESCE(plate_name, '') NOT REGEXP '(^|[^A-Za-z])ST([^A-Za-z]|$)|ST板块|退市', 1, 0)) AS non_st_count
        FROM kpl_plate_featured_strengths
        WHERE trade_date = {day}
        GROUP BY trade_date, captured_at
        UNION ALL
        SELECT
          trade_date,
          MAX(captured_at) AS captured_at,
          'kpl_replay_limit_theme_groups' AS source_table,
          1 AS source_priority,
          COUNT(*) AS row_count,
          COUNT(*) AS non_st_count
        FROM kpl_replay_limit_theme_groups
        WHERE trade_date = {day}
          AND COALESCE(theme_name, '') <> ''
        GROUP BY trade_date
      ) snapshots
      WHERE trade_date = {day}
      ORDER BY source_priority ASC, IF(non_st_count >= 5, 0, 1), trade_date DESC, captured_at DESC
      LIMIT 1
    ),
    kpl_plate_latest AS (
      SELECT
        p.row_rank AS theme_rank,
        p.plate_code AS theme_id,
        p.plate_name AS theme_name,
        p.plate_code AS index_code,
        p.strength AS plate_strength,
        p.change_pct AS plate_change_pct,
        p.speed AS plate_speed
      FROM kpl_plate_featured_strengths p
      JOIN kpl_plate_snapshot s
        ON s.source_table='kpl_plate_featured_strengths'
       AND s.trade_date=p.trade_date
       AND s.captured_at=p.captured_at
      WHERE p.trade_date = {day}
        AND COALESCE(p.plate_name, '') <> ''
      UNION ALL
      SELECT
        g.theme_rank AS theme_rank,
        g.theme_code AS theme_id,
        g.theme_name AS theme_name,
        g.theme_code AS index_code,
        g.limit_up_count AS plate_strength,
        NULL AS plate_change_pct,
        NULL AS plate_speed
      FROM kpl_replay_limit_theme_groups g
      JOIN kpl_plate_snapshot s
        ON s.source_table='kpl_replay_limit_theme_groups'
       AND s.trade_date=g.trade_date
       AND s.captured_at=g.captured_at
      WHERE g.trade_date = {day}
        AND COALESCE(g.theme_name, '') <> ''
    ),
    kpl_stock_sections_day AS (
      SELECT MAX(trade_date) AS trade_date
      FROM kpl_stock_featured_sections
      WHERE trade_date <= {day}
    ),
    kpl_primary_trade_days AS (
      SELECT trade_date
      FROM (
        SELECT CAST({day} AS DATE) AS trade_date
        UNION
        SELECT trade_date
        FROM (
          SELECT DISTINCT trade_date
          FROM stock_daily_bars
          WHERE trade_date < {day}
          ORDER BY trade_date DESC
          LIMIT 4
        ) prev_days
      ) days
    ),
    kpl_limit_primary_candidates AS (
      SELECT *
      FROM (
        SELECT
          r.trade_date,
          r.theme_code,
          r.theme_name,
          r.theme_rank,
          r.code,
          r.stock_name,
          r.limit_time,
          r.limit_amount,
          r.pct_change,
          r.reason_text,
          r.concept_explain,
          r.boom_theme,
          ROW_NUMBER() OVER (
            PARTITION BY r.code
            ORDER BY r.trade_date DESC, COALESCE(r.limit_time, r.captured_at) DESC, r.theme_rank ASC
          ) AS rn
        FROM kpl_replay_limit_theme_stocks r
        JOIN kpl_primary_trade_days td ON td.trade_date=r.trade_date
        WHERE COALESCE(r.theme_name, '') <> ''
      ) ranked
      WHERE rn=1
    ),
    kpl_stock_reason_candidates AS (
      SELECT *
      FROM (
        SELECT
          r.reason_date,
          r.code,
          r.stock_name,
          r.reason_text,
          r.concept_explain,
          r.boom_theme,
          ROW_NUMBER() OVER (
            PARTITION BY r.code
            ORDER BY r.reason_date DESC, r.captured_at DESC
          ) AS rn
        FROM kpl_stock_limit_up_reasons r
        JOIN kpl_primary_trade_days td ON td.trade_date=r.reason_date
        WHERE COALESCE(r.reason_text, '') <> ''
      ) ranked
      WHERE rn=1
    ),
    kpl_featured_primary_candidates AS (
      SELECT *
      FROM (
        SELECT
          k.code,
          k.stock_name,
          k.section_code,
          k.section_name,
          k.section_rank,
          k.section_score,
          COALESCE(pl.theme_rank, 9999) AS plate_rank,
          pl.plate_strength,
          pl.plate_change_pct,
          pl.plate_speed,
          ROW_NUMBER() OVER (
            PARTITION BY k.code
            ORDER BY
              IF(pl.theme_id IS NULL, 1, 0) ASC,
              COALESCE(pl.theme_rank, 9999) ASC,
              COALESCE(k.section_rank, 9999) ASC,
              COALESCE(k.section_score, -999999) DESC,
              k.section_name ASC
          ) AS rn
        FROM kpl_stock_sections_day sd
        JOIN kpl_stock_featured_sections k ON k.trade_date=sd.trade_date
        JOIN leader_research_pool rp ON rp.code=k.code
        LEFT JOIN kpl_plate_latest pl ON pl.theme_id=k.section_code
        WHERE COALESCE(k.section_name, '') <> ''
      ) ranked
      WHERE rn=1
    ),
    kpl_primary_theme AS (
      SELECT
        rp.code,
        COALESCE(
          CASE WHEN lp.code IS NOT NULL THEN NULLIF(lp.theme_name, '') END,
          NULLIF(fp.section_name, ''),
          '未归类'
        ) AS theme_name,
        COALESCE(lp.theme_code, fp.section_code, '') AS theme_id,
        COALESCE(lp.theme_rank, fp.plate_rank, 9999) AS theme_rank,
        COALESCE(lp.theme_code, fp.section_code, '') AS index_code,
        CASE
          WHEN lp.code IS NOT NULL THEN 'replay_limit_theme'
          WHEN kr.code IS NOT NULL THEN 'stock_limit_reason'
          ELSE 'featured_strength'
        END AS primary_theme_source,
        COALESCE(lp.reason_text, kr.reason_text, fp.section_name, '') AS primary_theme_reason,
        fp.plate_strength,
        fp.plate_change_pct,
        fp.plate_speed,
        fp.section_score,
        COALESCE(fp.section_rank, 0) AS stock_rank
      FROM leader_research_pool rp
      LEFT JOIN kpl_limit_primary_candidates lp ON lp.code=rp.code
      LEFT JOIN kpl_stock_reason_candidates kr ON kr.code=rp.code
      LEFT JOIN kpl_featured_primary_candidates fp ON fp.code=rp.code
    ),
    headline_theme_member_candidates AS (
      SELECT
        pt.theme_rank,
        pt.theme_name,
        pt.index_code,
        u.code,
        p.name,
        p.today_pct AS rise_percent,
        pt.stock_rank,
        1 AS in_research_pool,
        p.rank_3d,
        p.rank_5d,
        p.rank_10d,
        p.pct_3d,
        p.pct_5d,
        p.pct_10d,
        COALESCE(p.today_pct, 0) AS today_pct,
        p.source_kind,
        p.source_label,
        p.limit_up_day_count,
        p.pool_rank,
        pt.primary_theme_source,
        pt.primary_theme_reason,
        pt.plate_strength,
        pt.plate_change_pct,
        pt.plate_speed,
        pt.section_score
      FROM leader_research_pool p
      JOIN kpl_primary_theme pt ON pt.code=p.code
      JOIN market_universe u ON u.code=p.code
    ),
    theme_top3 AS (
      SELECT *
      FROM (
      SELECT
        pl.theme_name,
        pl.theme_rank,
        COALESCE((
          SELECT COUNT(DISTINCT h.code)
          FROM headline_theme_member_candidates h
          WHERE h.theme_name=pl.theme_name
        ), 0) AS member_count,
        COALESCE((
          SELECT COUNT(DISTINCT h.code)
          FROM headline_theme_member_candidates h
          WHERE h.theme_name=pl.theme_name
            AND h.in_research_pool=1
        ), 0) AS research_pool_member_count,
        COALESCE((
          SELECT ROUND(AVG(COALESCE(h.rise_percent, 0)), 2)
          FROM headline_theme_member_candidates h
          WHERE h.theme_name=pl.theme_name
        ), ROUND(COALESCE(pl.plate_change_pct, 0), 2)) AS avg_rise,
        COALESCE((
          SELECT ROUND(MAX(COALESCE(h.rise_percent, 0)), 2)
          FROM headline_theme_member_candidates h
          WHERE h.theme_name=pl.theme_name
        ), ROUND(COALESCE(pl.plate_change_pct, 0), 2)) AS max_rise,
        ROW_NUMBER() OVER (ORDER BY pl.theme_rank ASC, pl.theme_name ASC) AS top_theme_rank
      FROM kpl_plate_latest pl
      WHERE COALESCE(pl.theme_name, '') NOT REGEXP '(^|[^A-Za-z])ST([^A-Za-z]|$)|ST板块|退市'
      ) ranked_kpl_themes
      WHERE top_theme_rank <= 8
    ),
"""
    sql = _replace_sql_block(sql, "    headline_snapshot AS (", "    scope_codes AS (", kpl_theme_block)
    sql = sql.replace(
        """      'theme_rule', '全市场榜=研究池全集；主题榜=同花顺首页头条题材成分 ∩ 研究池，主题内部单独重排',
      'headline_theme_snapshot_id', COALESCE((SELECT snapshot_id FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_trade_date', COALESCE((SELECT DATE_FORMAT(trade_date, '%Y-%m-%d') FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_source', COALESCE((SELECT source FROM headline_snapshot LIMIT 1), ''),
      'headline_theme_status', COALESCE((SELECT snapshot_status FROM headline_snapshot LIMIT 1), ''),""",
        """      'theme_rule', '全市场榜=研究池全集；主题榜=开盘啦精选板块Top8，涨停票按复盘啦涨停原因归组，非涨停票按精选板块强度归组',
      'headline_theme_snapshot_id', COALESCE((SELECT DATE_FORMAT(captured_at, '%Y%m%d%H%i%s') FROM kpl_plate_snapshot LIMIT 1), ''),
      'headline_theme_trade_date', COALESCE((SELECT DATE_FORMAT(trade_date, '%Y-%m-%d') FROM kpl_plate_snapshot LIMIT 1), ''),
      'headline_theme_source', COALESCE((SELECT source_table FROM kpl_plate_snapshot LIMIT 1), 'kpl_plate_featured_strengths'),
      'headline_theme_status', 'post_close_confirm',""",
    )
    sql = sql.replace(
        """        p.pool_rank
      FROM leader_research_pool p
      UNION ALL""",
        """        p.pool_rank,
        COALESCE((SELECT pt.primary_theme_source FROM kpl_primary_theme pt WHERE pt.code=p.code LIMIT 1), '') AS primary_theme_source,
        COALESCE((SELECT pt.primary_theme_reason FROM kpl_primary_theme pt WHERE pt.code=p.code LIMIT 1), '') AS primary_theme_reason
      FROM leader_research_pool p
      UNION ALL""",
    )
    sql = sql.replace(
        """        MAX(p.pool_rank) AS pool_rank
      FROM theme_top3 t""",
        """        MAX(p.pool_rank) AS pool_rank,
        MAX(p.primary_theme_source) AS primary_theme_source,
        MAX(p.primary_theme_reason) AS primary_theme_reason
      FROM theme_top3 t""",
    )
    sql = sql.replace(
        """        MAX(sc.pool_rank) AS pool_rank
      FROM scope_codes sc""",
        """        MAX(sc.pool_rank) AS pool_rank,
        MAX(sc.primary_theme_source) AS primary_theme_source,
        MAX(sc.primary_theme_reason) AS primary_theme_reason
      FROM scope_codes sc""",
    )
    sql = sql.replace(
        """                'active_fact_summary', COALESCE((
                  SELECT COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, ''))
                  FROM async_evidence_summaries aes
                  WHERE aes.trade_date=CAST({day} AS DATE)
                    AND aes.code=l.code
                    AND EXISTS (
                      SELECT 1
                      FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                    )
                  LIMIT 1
                ), ''),""".replace("{day}", day),
        f"""                'active_fact_summary', IF(
                  l.primary_theme_source='replay_limit_theme' AND COALESCE(l.primary_theme_reason, '') <> '',
                  l.primary_theme_reason,
                  COALESCE((
                    SELECT COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, ''))
                    FROM async_evidence_summaries aes
                    WHERE aes.trade_date=CAST({day} AS DATE)
                      AND aes.code=l.code
                      AND EXISTS (
                        SELECT 1
                        FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                      )
                    LIMIT 1
                  ), '')
                ),""",
    )
    sql = sql.replace(
        "l.primary_theme_source='replay_limit_theme'",
        "l.primary_theme_source IN ('replay_limit_theme', 'stock_limit_reason')",
    )
    sql = sql.replace(
        """                'active_facts', COALESCE((
                  SELECT JSON_ARRAYAGG(JSON_OBJECT(
                    'date', COALESCE(DATE_FORMAT(ef.fact_date, '%Y-%m-%d'), ''),
                    'title', COALESCE(NULLIF(ef.fact_subtype, ''), NULLIF(ef.fact_title, ''), '有效事实'),
                    'body', LEFT(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ef.payload, '$.display_body')), ''), NULLIF(ef.fact_body, ''), ef.fact_title, ''), 220),
                    'lines', COALESCE(JSON_EXTRACT(ef.payload, '$.display_lines'), JSON_ARRAY())
                  ))
                  FROM stock_effective_facts ef
                  WHERE ef.trade_date=CAST({day} AS DATE)
                    AND ef.code=l.code
                    AND ef.evidence_group='current_effective'
                    AND ef.valid_status='active'
                    AND ef.display_level <> 'hidden'
                ), '[]'),""".replace("{day}", day),
        f"""                'active_facts', IF(
                  l.primary_theme_source='replay_limit_theme' AND COALESCE(l.primary_theme_reason, '') <> '',
                  JSON_ARRAY(JSON_OBJECT(
                    'date', DATE_FORMAT(CAST({day} AS DATE), '%Y-%m-%d'),
                    'title', '开盘啦涨停原因',
                    'body', LEFT(l.primary_theme_reason, 220),
                    'lines', JSON_ARRAY(l.primary_theme_reason)
                  )),
                  COALESCE((
                    SELECT JSON_ARRAYAGG(JSON_OBJECT(
                      'date', COALESCE(DATE_FORMAT(ef.fact_date, '%Y-%m-%d'), ''),
                      'title', COALESCE(NULLIF(ef.fact_subtype, ''), NULLIF(ef.fact_title, ''), '有效事实'),
                      'body', LEFT(COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(ef.payload, '$.display_body')), ''), NULLIF(ef.fact_body, ''), ef.fact_title, ''), 220),
                      'lines', COALESCE(JSON_EXTRACT(ef.payload, '$.display_lines'), JSON_ARRAY())
                    ))
                    FROM stock_effective_facts ef
                    WHERE ef.trade_date=CAST({day} AS DATE)
                      AND ef.code=l.code
                      AND ef.evidence_group='current_effective'
                      AND ef.valid_status='active'
                      AND ef.display_level <> 'hidden'
                  ), '[]')
                ),""",
    )
    sql = sql.replace(
        """                'active_fact_count', COALESCE((
                  SELECT COUNT(*)
                  FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                ), 0),""".replace("{day}", day),
        f"""                'active_fact_count', IF(
                  l.primary_theme_source='replay_limit_theme' AND COALESCE(l.primary_theme_reason, '') <> '',
                  1,
                  COALESCE((
                    SELECT COUNT(*)
                    FROM stock_effective_facts ef WHERE ef.trade_date=CAST({day} AS DATE) AND ef.code=l.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
                  ), 0)
                ),""",
    )
    sql = sql.replace(
        "l.primary_theme_source='replay_limit_theme'",
        "l.primary_theme_source IN ('replay_limit_theme', 'stock_limit_reason')",
    )
    sql = sql.replace(
        "'theme_rule', '全市场榜=研究池全集；主题榜=同花顺首页头条题材成分 ∩ 研究池，主题内部单独重排'",
        "'theme_rule', '全市场榜=研究池全集；板块榜=强度最高8个 primary_theme，涨停票取复盘啦涨停原因分组，非涨停票取当前强度最高的开盘啦精选板块'",
    )
    sql = sql.replace("'同花顺题材成分涨幅'", "'开盘啦精选板块成分涨幅'")
    return sql


def latest_scan_sql() -> str:
    return """
    SELECT COALESCE(JSON_OBJECT(
      'run', (
        SELECT JSON_OBJECT(
          'run_id', run_id,
          'scanned_at', DATE_FORMAT(scanned_at, '%Y-%m-%d %H:%i:%s'),
          'market_phase', market_phase,
          'accepted', accepted,
          'ok', ok,
          'duration_ms', duration_ms,
          'row_count', row_count
        )
        FROM scan_runs
        ORDER BY scanned_at DESC
        LIMIT 1
      ),
      'rows', (
        SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
        FROM (
          SELECT JSON_OBJECT(
            'rank_no', sm.visible_rank,
            'code', sm.code,
            'name', sm.name,
            'speed', sm.speed,
            'pct_change', sm.pct_change,
            'amount_yi', ROUND(COALESCE(sm.amount, 0) / 100000000, 2),
            'industry', sm.industry,
            'sub_industry', sm.sub_industry
          ) AS item
          FROM (
            SELECT
              sm.*,
              ROW_NUMBER() OVER (ORDER BY sm.rank_speed ASC) AS visible_rank
            FROM scan_movers sm
            JOIN (
              SELECT id
              FROM scan_runs
              ORDER BY scanned_at DESC
              LIMIT 1
            ) latest ON latest.id = sm.scan_run_id
            WHERE sm.name NOT LIKE '%ST%'
              AND sm.name NOT LIKE '%退市%'
          ) sm
          ORDER BY sm.visible_rank ASC
          LIMIT 10
        ) ranked
      )
    ), JSON_OBJECT('run', NULL, 'rows', JSON_ARRAY()));
    """


def status_sql(trade_date: str | None = "") -> str:
    day = sql_string(trade_date) if trade_date else "CURDATE()"
    return f"""
    SELECT JSON_OBJECT(
      'trade_date', {day},
      'scan_count_today', (SELECT COUNT(*) FROM scan_runs WHERE DATE(scanned_at)={day}),
      'latest_scan_at', (SELECT DATE_FORMAT(MAX(scanned_at), '%Y-%m-%d %H:%i:%s') FROM scan_runs WHERE DATE(scanned_at)={day}),
      'window_count_today', (SELECT COUNT(*) FROM windows WHERE DATE(started_at)={day}),
      'community_posts', (SELECT COUNT(*) FROM community_posts),
      'active_anchors', (SELECT COUNT(*) FROM active_market_anchors WHERE source='ths_hot_concept' AND status <> 'expired'),
      'auction_trend_rows', (SELECT COUNT(*) FROM auction_trend_summary WHERE trade_date={day}),
      'pending_tasks', (SELECT COUNT(*) FROM task_queue WHERE status IN ('pending','running')),
      'workers', (
        SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
          'worker_id', worker_id,
          'worker_type', worker_type,
          'status', status,
          'heartbeat_at', DATE_FORMAT(heartbeat_at, '%Y-%m-%d %H:%i:%s')
        )), JSON_ARRAY())
        FROM worker_heartbeats
        WHERE heartbeat_at >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)
      )
    );
    """


def market_width_latest_sql(trade_date: str | None = "") -> str:
    filters = [
        "((source='stock_daily_bars_close' AND total_count >= 4800) OR ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999') OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999')))"
    ]
    if trade_date:
        filters.append(f"trade_date={sql_string(trade_date)}")
    where_day = "WHERE " + " AND ".join(filters)
    return f"""
    SELECT COALESCE(JSON_OBJECT(
      'snapshot_id', snapshot_id,
      'trade_date', DATE_FORMAT(trade_date, '%Y-%m-%d'),
      'captured_at', DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
      'source', source,
      'market_scope', market_scope,
      'total_count', total_count,
      'up_count', up_count,
      'down_count', down_count,
      'flat_count', flat_count,
      'up3_count', up3_count,
      'down3_count', down3_count,
      'up5_count', up5_count,
      'down5_count', down5_count,
      'has_5pct_data', IF(up5_count > 0 OR down5_count > 0 OR (up3_count = 0 AND down3_count = 0), TRUE, FALSE),
      'limit_up_count', limit_up_count,
      'limit_down_count', limit_down_count,
      'amount_top50_count', amount_top50_count,
      'amount_top50_up_count', amount_top50_up_count,
      'amount_top50_down_count', amount_top50_down_count,
      'amount_top50_flat_count', amount_top50_flat_count,
      'amount_top50_up3_count', amount_top50_up3_count,
      'amount_top50_down3_count', amount_top50_down3_count,
      'amount_top50_up5_count', amount_top50_up5_count,
      'amount_top50_down5_count', amount_top50_down5_count,
      'research_pool_trade_date', COALESCE(DATE_FORMAT(research_pool_trade_date, '%Y-%m-%d'), ''),
      'research_pool_rule', research_pool_rule,
      'research_pool_count', research_pool_count,
      'research_pool_up_count', research_pool_up_count,
      'research_pool_down_count', research_pool_down_count,
      'research_pool_flat_count', research_pool_flat_count,
      'research_pool_up3_count', research_pool_up3_count,
      'research_pool_down3_count', research_pool_down3_count,
      'research_pool_up5_count', research_pool_up5_count,
      'research_pool_down5_count', research_pool_down5_count,
      'sh_index_price', sh_index_price,
      'sh_index_pct_change', sh_index_pct_change,
      'sh_index_amount_yi', ROUND(sh_index_amount / 100000000, 2),
      'sh_index_volume', sh_index_volume,
      'total_volume', total_volume,
      'total_volume_yi', ROUND(total_volume / 100000000, 2),
      'total_amount_yi', ROUND(total_amount / 100000000, 2),
      'top50_amount_yi', ROUND(top50_amount / 100000000, 2),
      'kpl_capacity_market_time', COALESCE((
        SELECT DATE_FORMAT(k.market_time, '%Y-%m-%d %H:%i:%s')
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ), ''),
      'kpl_capacity_forecast_yi', (
        SELECT k.forecast_amount_yi
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ),
      'kpl_capacity_current_yi', (
        SELECT ROUND(k.latest_amount_wan / 10000, 2)
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ),
      'kpl_capacity_change_pct', (
        SELECT k.forecast_change_pct
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ),
      'kpl_capacity_delta_yi', (
        SELECT k.forecast_delta_yi
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ),
      'kpl_capacity_text', COALESCE((
        SELECT k.forecast_text
        FROM kpl_market_capacity_snapshots k
        WHERE k.trade_date=market_width_snapshots.trade_date
        ORDER BY IF(k.captured_at=market_width_snapshots.captured_at, 0, 1), k.captured_at DESC
        LIMIT 1
      ), '')
    ), JSON_OBJECT())
    FROM market_width_snapshots
    {where_day}
    ORDER BY trade_date DESC, captured_at DESC
    LIMIT 1;
    """


def market_width_series_sql(trade_date: str | None = "", limit: int = 240) -> str:
    filters = []
    if trade_date:
        filters.append(f"trade_date={sql_string(trade_date)}")
    filters.append("((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999') OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999'))")
    where_clause = "WHERE " + " AND ".join(filters)
    return f"""
    SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
    FROM (
      SELECT JSON_OBJECT(
        'snapshot_id', snapshot_id,
        'time', DATE_FORMAT(captured_at, '%H:%i'),
        'captured_at', DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
        'up_count', up_count,
        'down_count', down_count,
        'flat_count', flat_count,
        'up3_count', up3_count,
        'down3_count', down3_count,
        'up5_count', up5_count,
        'down5_count', down5_count,
        'has_5pct_data', IF(up5_count > 0 OR down5_count > 0 OR (up3_count = 0 AND down3_count = 0), TRUE, FALSE),
        'limit_up_count', limit_up_count,
        'limit_down_count', limit_down_count,
        'total_count', total_count,
        'amount_top50_count', amount_top50_count,
        'amount_top50_up_count', amount_top50_up_count,
        'amount_top50_down_count', amount_top50_down_count,
        'amount_top50_flat_count', amount_top50_flat_count,
        'amount_top50_up3_count', amount_top50_up3_count,
        'amount_top50_down3_count', amount_top50_down3_count,
        'amount_top50_up5_count', amount_top50_up5_count,
        'amount_top50_down5_count', amount_top50_down5_count,
        'research_pool_trade_date', COALESCE(DATE_FORMAT(research_pool_trade_date, '%Y-%m-%d'), ''),
        'research_pool_rule', research_pool_rule,
        'research_pool_count', research_pool_count,
        'research_pool_up_count', research_pool_up_count,
        'research_pool_down_count', research_pool_down_count,
        'research_pool_flat_count', research_pool_flat_count,
        'research_pool_up3_count', research_pool_up3_count,
        'research_pool_down3_count', research_pool_down3_count,
        'research_pool_up5_count', research_pool_up5_count,
        'research_pool_down5_count', research_pool_down5_count,
        'sh_index_price', sh_index_price,
        'sh_index_pct_change', sh_index_pct_change,
        'sh_index_amount_yi', ROUND(sh_index_amount / 100000000, 2),
        'sh_index_volume', sh_index_volume,
        'total_volume', total_volume,
        'total_volume_yi', ROUND(total_volume / 100000000, 2),
        'total_amount_yi', ROUND(total_amount / 100000000, 2),
        'top50_amount_yi', ROUND(top50_amount / 100000000, 2),
        'kpl_capacity_forecast_yi', (
          SELECT k.forecast_amount_yi
          FROM kpl_market_capacity_trends k
          WHERE k.trade_date=latest_rows.trade_date
            AND k.trend_time<=DATE_FORMAT(latest_rows.captured_at, '%H:%i')
          ORDER BY k.trend_time DESC
          LIMIT 1
        ),
        'kpl_capacity_current_yi', (
          SELECT ROUND(k.latest_amount_wan / 10000, 2)
          FROM kpl_market_capacity_trends k
          WHERE k.trade_date=latest_rows.trade_date
            AND k.trend_time<=DATE_FORMAT(latest_rows.captured_at, '%H:%i')
          ORDER BY k.trend_time DESC
          LIMIT 1
        ),
        'kpl_capacity_change_pct', (
          SELECT k.forecast_change_pct
          FROM kpl_market_capacity_trends k
          WHERE k.trade_date=latest_rows.trade_date
            AND k.trend_time<=DATE_FORMAT(latest_rows.captured_at, '%H:%i')
          ORDER BY k.trend_time DESC
          LIMIT 1
        ),
        'kpl_capacity_delta_yi', (
          SELECT k.forecast_delta_yi
          FROM kpl_market_capacity_trends k
          WHERE k.trade_date=latest_rows.trade_date
            AND k.trend_time<=DATE_FORMAT(latest_rows.captured_at, '%H:%i')
          ORDER BY k.trend_time DESC
          LIMIT 1
        ),
        'kpl_capacity_text', COALESCE((
          SELECT k.forecast_text
          FROM kpl_market_capacity_trends k
          WHERE k.trade_date=latest_rows.trade_date
            AND k.trend_time<=DATE_FORMAT(latest_rows.captured_at, '%H:%i')
          ORDER BY k.trend_time DESC
          LIMIT 1
        ), '')
      ) AS item
      FROM (
        SELECT *
        FROM market_width_snapshots
        {where_clause}
        ORDER BY captured_at DESC
        LIMIT {max(1, int(limit))}
      ) latest_rows
      ORDER BY captured_at ASC
    ) series_rows;
    """


def market_width_cycle_5d_sql(trade_date: str | None = "", limit: int = 5) -> str:
    day_filter = f"AND trade_date <= {sql_string(trade_date)}" if trade_date else ""
    return f"""
    SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
    FROM (
      SELECT trade_date, JSON_OBJECT(
        'trade_date', DATE_FORMAT(trade_date, '%Y-%m-%d'),
        'captured_at', DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
        'total_count', total_count,
        'up_count', up_count,
        'down_count', down_count,
        'up5_count', up5_count,
        'down5_count', down5_count,
        'has_5pct_data', IF(up5_count > 0 OR down5_count > 0 OR (up3_count = 0 AND down3_count = 0), TRUE, FALSE),
        'limit_up_count', limit_up_count,
        'limit_down_count', limit_down_count,
        'positive_power', up_count + up5_count * 4 + limit_up_count * 12,
        'negative_power', down_count + down5_count * 4 + limit_down_count * 12,
        'positive_hot_power', up5_count + limit_up_count * 3,
        'negative_hot_power', down5_count + limit_down_count * 3,
        'amount_top50_up_count', amount_top50_up_count,
        'amount_top50_down_count', amount_top50_down_count,
        'amount_top50_up5_count', amount_top50_up5_count,
        'amount_top50_down5_count', amount_top50_down5_count,
        'research_pool_trade_date', COALESCE(DATE_FORMAT(research_pool_trade_date, '%Y-%m-%d'), ''),
        'research_pool_up_count', research_pool_up_count,
        'research_pool_down_count', research_pool_down_count,
        'research_pool_up5_count', research_pool_up5_count,
        'research_pool_down5_count', research_pool_down5_count,
        'sh_index_price', sh_index_price,
        'sh_index_pct_change', sh_index_pct_change,
        'sh_index_amount_yi', ROUND(sh_index_amount / 100000000, 2),
        'sh_index_volume', sh_index_volume,
        'total_volume', total_volume,
        'total_volume_yi', ROUND(total_volume / 100000000, 2),
        'total_amount_yi', ROUND(total_amount / 100000000, 2)
      ) AS item
      FROM (
        SELECT s.*
        FROM market_width_snapshots s
        JOIN (
          SELECT trade_date, MAX(captured_at) AS captured_at
          FROM market_width_snapshots
          WHERE source='stock_daily_bars_close'
            AND total_count >= 4800
            {day_filter}
          GROUP BY trade_date
          ORDER BY trade_date DESC
          LIMIT {max(2, int(limit))}
        ) d ON d.trade_date=s.trade_date AND d.captured_at=s.captured_at
        ORDER BY s.trade_date ASC
      ) daily_rows
    ) ordered_rows;
    """


def market_width_top50_sql(snapshot_id: str | None = "", trade_date: str | None = "") -> str:
    if snapshot_id:
        snapshot_filter = f"snapshot_id={sql_string(snapshot_id)}"
    elif trade_date:
        snapshot_filter = f"""snapshot_id=(
          SELECT snapshot_id
          FROM market_width_snapshots
          WHERE trade_date={sql_string(trade_date)}
            AND (source='stock_daily_bars_close'
              OR ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999')
                OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999')))
          ORDER BY captured_at DESC
          LIMIT 1
        )"""
    else:
        snapshot_filter = """snapshot_id=(
          SELECT snapshot_id
          FROM market_width_snapshots
          WHERE source='stock_daily_bars_close'
             OR ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999')
              OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999'))
          ORDER BY trade_date DESC, captured_at DESC
          LIMIT 1
        )"""
    return f"""
    SELECT COALESCE(JSON_ARRAYAGG(item), JSON_ARRAY())
    FROM (
      SELECT JSON_OBJECT(
        'rank_no', rank_no,
        'code', code,
        'name', name,
        'latest_price', latest_price,
        'pct_change', pct_change,
        'amount_yi', ROUND(COALESCE(amount, 0) / 100000000, 2)
      ) AS item
      FROM market_width_amount_top50
      WHERE {snapshot_filter}
      ORDER BY rank_no ASC
      LIMIT 50
    ) ranked;
    """


def intel_feed_sql(
    trade_date: str | None = "",
    *,
    kind: str = "",
    event_time: str = "",
    code: str = "",
) -> str:
    day = sql_string(trade_date or date.today().strftime("%Y-%m-%d"))
    clean_kind = (kind or "").strip()
    clean_event_time = (event_time or "").strip()
    clean_code = (code or "").strip()
    scan_detail_filter = ""
    window_detail_filter = ""
    auction_detail_filter = ""
    if clean_kind:
        if clean_kind != "scan":
            scan_detail_filter += " AND 1=0"
        if clean_kind != "window":
            window_detail_filter += " AND 1=0"
        if clean_kind != "auction":
            auction_detail_filter += " AND 1=0"
    if clean_code:
        scan_detail_filter += f" AND code={sql_string(clean_code)}"
        window_detail_filter += f" AND code={sql_string(clean_code)}"
        auction_detail_filter += f" AND code={sql_string(clean_code)}"
    if clean_event_time:
        scan_detail_filter += f" AND DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
        window_detail_filter += f" AND DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
        auction_detail_filter += f" AND DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
    scan_run_time_filter = (
        f"AND DATE_FORMAT(scanned_at, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
        if clean_event_time and (not clean_kind or clean_kind == "scan")
        else ""
    )
    window_time_filter = (
        f"AND DATE_FORMAT(ended_at, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
        if clean_event_time and (not clean_kind or clean_kind == "window")
        else ""
    )
    rank_limit = 9999 if (clean_code or clean_event_time) else 50
    scan_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', sm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "model_summary",
    )
    window_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', wm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "model_summary",
    )
    scan_judgement_source = _json_source_fields(
        "stock_move_judgements",
        "CONCAT('stock_move_judgements:', smj.event_type, ':', smj.event_id, ':', sm.code)",
        "DATE_FORMAT(smj.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s')",
        "intraday",
    )
    window_judgement_source = _json_source_fields(
        "stock_move_judgements",
        "CONCAT('stock_move_judgements:', smj.event_type, ':', smj.event_id, ':', wm.code)",
        "DATE_FORMAT(smj.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s')",
        "intraday",
    )
    scan_lhb_source = ""
    window_lhb_source = ""
    scan_role_source = _json_source_fields(
        "scan_stock_roles",
        "CONCAT('scan_stock_roles:', sr.run_id, ':', sm.code)",
        "DATE_FORMAT(sr.scanned_at, '%Y-%m-%d')",
        "DATE_FORMAT(sr.scanned_at, '%Y-%m-%d %H:%i:%s')",
        "intraday",
    )
    window_role_source = _json_source_fields(
        "window_stock_roles",
        "CONCAT('window_stock_roles:', rw.window_id, ':', wm.code)",
        "DATE_FORMAT(rw.ended_at, '%Y-%m-%d')",
        "DATE_FORMAT(rw.ended_at, '%Y-%m-%d %H:%i:%s')",
        "intraday",
    )
    window_evidence_layer_source = _json_source_fields(
        "evidence_layers",
        "CONCAT('evidence_layers:', rw.window_id, ':', wm.code)",
        "DATE_FORMAT(rw.ended_at, '%Y-%m-%d')",
        "DATE_FORMAT(el.updated_at, '%Y-%m-%d %H:%i:%s')",
        "async",
    )
    auction_source = _json_source_fields(
        "auction_trend_summary",
        "CONCAT('auction_trend_summary:', DATE_FORMAT(ats.trade_date, '%Y-%m-%d'), ':', ats.code)",
        "DATE_FORMAT(ats.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(ats.generated_at, '%Y-%m-%d %H:%i:%s')",
        "intraday",
    )
    return f"""
    SET SESSION group_concat_max_len=16384;
    WITH
    {research_pool_snapshot_cte(trade_date or date.today().strftime("%Y-%m-%d"))},
    recent_scan_runs AS (
      SELECT id, run_id, scanned_at
      FROM scan_runs
      WHERE DATE(scanned_at)={day}
        AND accepted=1
        {scan_run_time_filter}
      ORDER BY scanned_at DESC
      LIMIT {rank_limit}
    ),
    recent_windows AS (
      SELECT id, window_id, ended_at
      FROM windows
      WHERE DATE(ended_at)={day}
        AND status='done'
        AND aggregate_count > 0
        {window_time_filter}
      ORDER BY ended_at DESC
    ),
    latest_anchor_role_snapshot AS (
      SELECT s.*
      FROM anchor_realtime_role_snapshots s
      JOIN (
        SELECT anchor_name, MAX(captured_at) AS max_at
        FROM anchor_realtime_role_snapshots
        WHERE DATE(captured_at)={day}
          AND source='research_pool_theme_members'
        GROUP BY anchor_name
      ) latest
        ON latest.anchor_name = s.anchor_name
       AND latest.max_at = s.captured_at
      WHERE s.source='research_pool_theme_members'
    ),
    latest_anchor_role_member AS (
      SELECT m.*
      FROM anchor_realtime_role_members m
      JOIN latest_anchor_role_snapshot s
        ON s.snapshot_run_id = m.snapshot_run_id
       AND s.anchor_name = m.anchor_name
    ),
    stock_headline_themes AS (
      SELECT code, JSON_ARRAYAGG(theme_name) AS tags
      FROM (
        SELECT code, theme_name, MIN(theme_rank) AS theme_rank, MAX(match_score) AS match_score
        FROM research_pool_theme_members
        WHERE trade_date={day}
          AND is_headline_theme=1
          AND COALESCE(theme_name, '') <> ''
        GROUP BY code, theme_name
        ORDER BY theme_rank ASC, match_score DESC, theme_name ASC
      ) themes
      GROUP BY code
    ),
    stock_headline_theme_roles AS (
      SELECT
        code,
        latest_source_updated_at AS latest_updated_at,
        roles
      FROM stock_headline_theme_role_evidence
      WHERE trade_date={day}
    ),
    latest_lhb AS (
      SELECT
        NULL AS code,
        JSON_ARRAY() AS key_facts,
        NULL AS trade_date,
        '' AS seat_signal_label,
        NULL AS updated_at
      WHERE 1=0
    ),
    root_evidence AS (
      SELECT
        rec.code,
        rec.updated_at AS latest_updated_at,
        COALESCE((
          SELECT JSON_ARRAYAGG(t.item)
          FROM (
            SELECT jt.ord, jt.item
            FROM JSON_TABLE(rec.items, '$[*]' COLUMNS (
              ord FOR ORDINALITY,
              item JSON PATH '$',
              source_table VARCHAR(64) PATH '$.source_table' NULL ON EMPTY,
              label VARCHAR(64) PATH '$.label' NULL ON EMPTY,
              source VARCHAR(128) PATH '$.source' NULL ON EMPTY,
              type VARCHAR(64) PATH '$.type' NULL ON EMPTY,
              body VARCHAR(512) PATH '$.body' NULL ON EMPTY
            )) jt
            WHERE COALESCE(jt.source_table, '') <> 'stock_period_rankings'
              AND COALESCE(jt.label, '') <> '区间领头'
              AND COALESCE(jt.type, '') <> 'period'
              AND COALESCE(jt.source, '') NOT LIKE '%问财%'
              AND COALESCE(jt.body, '') NOT LIKE '%问财%'
            ORDER BY jt.ord
          ) t
        ), JSON_ARRAY()) AS items
      FROM stock_root_evidence_cache rec
      WHERE rec.trade_date={day}
    ),
    auction_ranked AS (
      SELECT
        MAX(COALESCE(ats.last_seen_minute, ats.generated_at)) OVER () AS event_time,
        'auction' AS kind,
        '竞价封单' AS kind_label,
        ats.code,
        ats.stock_name AS name,
        ROW_NUMBER() OVER (ORDER BY COALESCE(ats.last_seal_amount, 0) DESC, ats.final_candidate_rank ASC) AS sort_rank,
        CONCAT(ats.stock_name, ' ', ats.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          '涨停封单 ', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿；',
          '竞价涨幅 ', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%；',
          '竞价额 ', ROUND(COALESCE(ats.last_auction_amount, 0) / 100000000, 2), '亿'
        ) AS summary,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '\n'), ''),
          '【集合竞价】涨停封单 ', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿；',
          '竞价涨幅 ', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%；',
          '竞价额 ', ROUND(COALESCE(ats.last_auction_amount, 0) / 100000000, 2), '亿'
        ) AS detail,
        JSON_MERGE_PRESERVE(
          JSON_ARRAY(JSON_OBJECT(
            'layer', 'realtime',
            'label', '竞价封单',
            'type', 'realtime',
            'source', '集合竞价',
            {auction_source}
            'body', CONCAT(
              '涨停封单 ', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿；',
              '竞价涨幅 ', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%；',
              '竞价额 ', ROUND(COALESCE(ats.last_auction_amount, 0) / 100000000, 2), '亿'
            ),
            'priority', 0,
            'payload', IF(JSON_VALID(ats.raw_json), JSON_EXTRACT(ats.raw_json, '$'), JSON_OBJECT())
          )),
          COALESCE(re.items, JSON_ARRAY())
        ) AS evidence_items,
        JSON_OBJECT() AS display_contract,
        DATE_FORMAT(COALESCE(ats.last_seen_minute, ats.generated_at), '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        CAST('' AS CHAR) AS judgement_updated_at,
        CAST('' AS CHAR) AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        CAST('' AS CHAR) AS lhb_updated_at,
        CAST('' AS CHAR) AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(ats.generated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        COALESCE(ats.last_seal_amount, 0) / 100000000 AS score,
        COALESCE(ats.last_auction_pct, 0) AS change_pct,
        0 AS speed_pct,
        ROUND(COALESCE(ats.last_auction_amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
            CONCAT('封单:', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿'),
            CONCAT('竞价:', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%')
          )
        ) AS tags,
        ROW_NUMBER() OVER (ORDER BY COALESCE(ats.last_seal_amount, 0) DESC, ats.final_candidate_rank ASC) AS rn
      FROM auction_trend_summary ats
      LEFT JOIN stock_company_profiles scp ON scp.code = ats.code
      LEFT JOIN stock_headline_themes sht ON sht.code = ats.code
      LEFT JOIN root_evidence re ON re.code = ats.code
      WHERE ats.trade_date={day}
        AND COALESCE(ats.limit_up_count, 0) > 0
        AND COALESCE(ats.last_seal_amount, 0) > 0
        AND COALESCE(ats.last_auction_pct, 0) >= 9.5
    ),
    scan_ranked AS (
      SELECT
        sr.scanned_at AS event_time,
        'scan' AS kind,
        '实时领涨' AS kind_label,
        sm.code,
        sm.name,
        sm.rank_speed AS sort_rank,
        CONCAT(sm.name, ' ', sm.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          COALESCE(NULLIF(smj.move_explanation, ''), '')
        ) AS summary,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '\n'), ''),
          IF(COALESCE(smj.final_view, '') <> '', CONCAT('【判断层】', smj.final_view, '\n'), ''),
          IF(COALESCE(aes.summary_text, '') <> '' AND COALESCE(aes.impact_summary_text, '') = '', CONCAT('【异步证据】异步总结：', aes.summary_text, '\n'), ''),
          IF(COALESCE(aes.impact_summary_text, '') <> '', CONCAT('【异步证据】有效事实总结：', aes.impact_summary_text, '\n'), ''),
          IF(JSON_LENGTH(COALESCE(re.items, JSON_ARRAY())) > 0, CONCAT('【异步证据】基础事实：', re.items->>'$[0].body', '\n'), ''),
          IF(
            COALESCE(ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', '') <> '',
            CONCAT('【实时证据】题材证据：', COALESCE(ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason'), '\n'),
            ''
          ),
          IF(
            COALESCE(ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', '') <> '',
            CONCAT('【实时证据】个股证据：', COALESCE(ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason'), '\n'),
            ''
          ),
          COALESCE(sm.industry, ''), ' / ', COALESCE(sm.sub_industry, ''), '；同锚点 ', COALESCE(ars.member_count, ssr.anchor_member_count), ' 只'
        ) AS detail,
        JSON_MERGE_PRESERVE(
          IF(JSON_VALID(aes.key_facts) AND JSON_LENGTH(aes.key_facts) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '事实卡',
              {scan_async_source}
              'body', CONCAT_WS('\n', aes.key_facts->>'$[0]', aes.key_facts->>'$[1]', aes.key_facts->>'$[2]', aes.key_facts->>'$[3]', aes.key_facts->>'$[4]', aes.key_facts->>'$[5]', aes.key_facts->>'$[6]', aes.key_facts->>'$[7]', aes.key_facts->>'$[8]', aes.key_facts->>'$[9]', aes.key_facts->>'$[10]', aes.key_facts->>'$[11]'),
              'priority', 0,
              'payload', JSON_EXTRACT(aes.key_facts, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.move_reason, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '事实卡',
              {scan_async_source}
              'body', aes.move_reason,
              'priority', 1
            )), JSON_ARRAY()),
          IF(JSON_VALID(lhb.key_facts) AND JSON_LENGTH(lhb.key_facts) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '龙虎榜席位',
              'type', 'lhb',
              'source', CONCAT(IF(lhb.trade_date={day}, '当日龙虎榜', '上一交易日龙虎榜'), ' / ', lhb.seat_signal_label),
              {scan_lhb_source}
              'body', CONCAT_WS('\n', lhb.key_facts->>'$[0]', lhb.key_facts->>'$[1]', lhb.key_facts->>'$[2]', lhb.key_facts->>'$[3]'),
              'evidence_date', DATE_FORMAT(lhb.trade_date, '%Y-%m-%d'),
              'priority', 3,
              'payload', JSON_EXTRACT(lhb.key_facts, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.move_reason, '') = '' AND COALESCE(smj.move_explanation, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '判断引擎',
              {scan_judgement_source}
              'body', smj.move_explanation,
              'priority', 1
            )), JSON_ARRAY()),
          IF(COALESCE(smj.final_view, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '持续性',
              'type', 'quality',
              'source', '判断引擎',
              {scan_judgement_source}
              'body', CONCAT(
                smj.sustainability_label, ' / ', ROUND(smj.sustainability_score, 0), '分',
                '；区间', ROUND(smj.anchor_leadership_score, 0),
                ' 盘口', ROUND(smj.tape_confirm_score, 0),
                ' 行为', ROUND(COALESCE(JSON_EXTRACT(smj.score_detail, '$.short_term_behavior'), 0), 0),
                ' 风险-', ROUND(smj.anchor_risk_deduction, 0),
                IF(COALESCE(smj.risk_item, '') <> '', CONCAT('；', smj.risk_item), '')
              ),
              'priority', 2
            )), JSON_ARRAY()),
          IF(JSON_VALID(smj.support_items) AND JSON_LENGTH(smj.support_items) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心支撑',
              'type', 'support',
              'source', '判断引擎',
              {scan_judgement_source}
              'body', CONCAT_WS('\n',
                smj.support_items->>'$[0]',
                IF(COALESCE(smj.support_items->>'$[1]', '') LIKE '区间领头：%', smj.support_items->>'$[2]', smj.support_items->>'$[1]')
              ),
              'priority', 3
            )), JSON_ARRAY()),
          IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0 OR COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.influence_reasons')), 0) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '带动性',
              'type', 'influence',
              'source', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, '多题材扩散', CONCAT('同锚扩散 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_label')), ''))),
              {scan_judgement_source}
              'body', IF(
                COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0,
                CONCAT('关联头条题材带动性 ', JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), ' 个'),
                CONCAT_WS('\n',
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[0]')),
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[1]')),
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[2]'))
                )
              ),
              'priority', 4,
              'payload', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence'), JSON_EXTRACT(smj.score_detail, '$.influence_reasons')),
              'structured_payload', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, JSON_OBJECT('mode', 'multi_theme'), JSON_EXTRACT(smj.score_detail, '$.influence_payload'))
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.sustainability_basis) AND JSON_LENGTH(aes.sustainability_basis) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '持续依据',
              'type', 'support',
              'source', '事实卡',
              {scan_async_source}
              'body', CONCAT_WS('\n', aes.sustainability_basis->>'$[0]', aes.sustainability_basis->>'$[1]', aes.sustainability_basis->>'$[2]'),
              'priority', 4,
              'payload', JSON_EXTRACT(aes.sustainability_basis, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.main_flaw, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '最大瑕疵',
              'type', 'flaw',
              'source', '事实卡',
              {scan_async_source}
              'body', aes.main_flaw,
              'priority', 7
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.missing_evidence) AND JSON_LENGTH(aes.missing_evidence) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '证据缺口',
              'type', 'gap',
              'source', '事实卡',
              {scan_async_source}
              'body', CONCAT_WS('\n', aes.missing_evidence->>'$[0]', aes.missing_evidence->>'$[1]', aes.missing_evidence->>'$[2]'),
              'priority', 8,
              'payload', JSON_EXTRACT(aes.missing_evidence, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(smj.move_explanation, '') = '' AND COALESCE(NULLIF(aes.final_view, ''), NULLIF(aes.move_explanation, '')) IS NOT NULL, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '模型解释',
              {scan_async_source}
              'body', COALESCE(NULLIF(aes.final_view, ''), NULLIF(aes.move_explanation, '')),
              'priority', 1
            )), JSON_ARRAY()),
          IF(COALESCE(aes.quality_label, '') <> '' OR COALESCE(aes.explanation_strength, 'none') <> 'none', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动质量',
              'type', 'quality',
              'source', '模型判断',
              {scan_async_source}
              'body', CONCAT(COALESCE(NULLIF(aes.quality_label, ''), '未分类'), ' / 解释强度:', COALESCE(aes.explanation_strength, 'none')),
              'priority', 2
            )), JSON_ARRAY()),
          IF(COALESCE(aes.anchor_match_reason, '') <> '' OR COALESCE(aes.anchor_match, 'weak') <> 'weak', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '锚点一致性',
              'type', 'anchor',
              'source', '模型判断',
              {scan_async_source}
              'body', CONCAT(COALESCE(aes.anchor_match, 'weak'), IF(COALESCE(aes.anchor_match_reason, '') <> '', CONCAT('：', aes.anchor_match_reason), '')),
              'priority', 3
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.core_support) AND JSON_LENGTH(aes.core_support) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心支撑',
              'type', 'support',
              'source', '模型筛选',
              {scan_async_source}
              'body', CONCAT_WS('\n', aes.core_support->>'$[0]', aes.core_support->>'$[1]'),
              'priority', 4,
              'payload', JSON_EXTRACT(aes.core_support, '$')
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.counterpoints) AND JSON_LENGTH(aes.counterpoints) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '瑕疵',
              'type', 'counter',
              'source', '模型判断',
              {scan_async_source}
              'body', aes.counterpoints->>'$[0]',
              'priority', 6,
              'payload', JSON_EXTRACT(aes.counterpoints, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.final_analysis, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心结论',
              'type', 'final',
              'source', '模型结论',
              {scan_async_source}
              'body', COALESCE(aes.final_analysis, ''),
              'priority', 5
            )), JSON_ARRAY()),
          IF(COALESCE(aes.timeliness_reason, '') <> '' OR COALESCE(aes.timeliness_label, 'unknown') <> 'unknown', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '时效判断',
              'type', 'timeliness',
              'source', '模型判断',
              {scan_async_source}
              'body', CONCAT(
                CASE COALESCE(aes.timeliness_label, 'unknown')
                  WHEN 'fresh' THEN '高时效'
                  WHEN 'recent' THEN '近期有效'
                  WHEN 'stale' THEN '时效偏弱'
                  ELSE '时效未知'
                END,
                IF(COALESCE(aes.timeliness_reason, '') <> '', CONCAT('：', aes.timeliness_reason), '')
              ),
              'priority', 8
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.core_evidence_items) AND JSON_LENGTH(aes.core_evidence_items) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心证据',
              'type', 'core',
              'source', '过滤后证据',
              {scan_async_source}
              'body', COALESCE(aes.evidence_filter_summary, ''),
              'priority', 9,
              'payload', JSON_EXTRACT(aes.core_evidence_items, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.impact_summary_text, '') <> '' AND NOT (JSON_VALID(aes.key_facts) AND JSON_LENGTH(aes.key_facts) > 0), JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '模型判断',
              {scan_async_source}
              'body', COALESCE(aes.impact_summary_text, ''),
              'priority', 10,
              'payload', IF(JSON_VALID(aes.impact_factors), JSON_EXTRACT(aes.impact_factors, '$'), JSON_ARRAY())
            )), JSON_ARRAY()),
          IF(COALESCE(aes.summary_text, '') <> '' AND COALESCE(aes.impact_summary_text, '') = '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异步总结',
              'type', 'summary',
              'source', '模型总结',
              {scan_async_source}
              'body', COALESCE(aes.summary_text, ''),
              'priority', 20
            )), JSON_ARRAY()),
          COALESCE(re.items, JSON_ARRAY()),
          IF(JSON_LENGTH(COALESCE(shtr.roles, JSON_ARRAY())) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '头条题材角色',
              'type', 'headline_theme_roles',
              'source', '研究池题材角色',
              'source_table', 'research_pool_theme_members',
              'source_key', CONCAT('research_pool_theme_members:', {day}, ':', sm.code),
              'source_confidence', 'explicit',
              'data_date', {day},
              'evidence_date', {day},
              'updated_at', DATE_FORMAT(shtr.latest_updated_at, '%Y-%m-%d %H:%i:%s'),
              'source_generation', 'intraday',
              'availability', 'intraday',
              'evidence_group', 'current_effective',
              'evidence_role', 'market_realtime',
              'display_level', 'primary',
              'valid_status', 'watch',
              'source_registry', JSON_OBJECT(
                'source_table', 'research_pool_theme_members',
                'source_generation', 'intraday',
                'availability', 'intraday',
                'evidence_group', 'current_effective',
                'update_cycle', 'scan_loop',
                'data_date_policy', 'event_day'
              ),
              'body', CONCAT('关联今日头条题材 ', JSON_LENGTH(shtr.roles), ' 个'),
              'priority', 21,
              'payload', JSON_EXTRACT(shtr.roles, '$')
            )), JSON_ARRAY()),
          IF(arm.id IS NOT NULL OR ars.id IS NOT NULL OR COALESCE(ssr.role_label, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '领涨中军',
              'type', 'realtime',
              'source', '研究池题材角色',
              {scan_role_source}
              'body', CONCAT_WS('\n',
                CONCAT('题材：', COALESCE(ars.anchor_name, ssr.primary_anchor_name, '未锚定')),
                CONCAT('定位：', CASE
                  WHEN arm.role_label IN ('研究池领涨', '研究池领涨中军', '研究池中军') THEN arm.role_label
                  WHEN arm.id IS NOT NULL THEN '题材成员'
                  WHEN ssr.role_label IN ('领涨', '领涨中军', '中军') THEN CONCAT('局部', ssr.role_label)
                  WHEN ars.id IS NOT NULL THEN '扫描异动'
                  ELSE COALESCE(ssr.role_label, '')
                END),
                IF(COALESCE(ars.leader_name, ssr.leader_name, '') <> '', CONCAT('领涨：', COALESCE(ars.leader_name, ssr.leader_name), IF(COALESCE(ars.leader_code, ssr.leader_code, '') <> '', CONCAT(' ', COALESCE(ars.leader_code, ssr.leader_code)), '')), NULL),
                IF(COALESCE(ars.core_name, ssr.core_name, '') <> '', CONCAT('中军：', COALESCE(ars.core_name, ssr.core_name), IF(COALESCE(ars.core_code, ssr.core_code, '') <> '', CONCAT(' ', COALESCE(ars.core_code, ssr.core_code)), '')), NULL),
                CONCAT('题材内研究池成员：', COALESCE(ars.member_count, ssr.anchor_member_count, 0), '只')
              ),
              'priority', 22
            )), JSON_ARRAY()),
          IF(COALESCE(ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '题材证据',
              'type', 'theme',
              'source', '题材解释',
              {scan_role_source}
              'body', COALESCE(ssr.raw_json->>'$.raw_json.anchor_reason', ssr.raw_json->>'$.anchor_reason', ''),
              'priority', 30
            )), JSON_ARRAY()),
          IF(COALESCE(ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '个股证据',
              'type', 'stock',
              'source', '个股解释',
              {scan_role_source}
              'body', COALESCE(ssr.raw_json->>'$.raw_json.stock_reason', ssr.raw_json->>'$.stock_reason', ''),
              'priority', 40
            )), JSON_ARRAY())
        ) AS evidence_items,
        IF(JSON_VALID(smj.score_detail), JSON_EXTRACT(smj.score_detail, '$.display_contract'), JSON_OBJECT()) AS display_contract,
        DATE_FORMAT(sr.scanned_at, '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s') AS judgement_updated_at,
        DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s') AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s') AS lhb_updated_at,
        CAST('' AS CHAR) AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(sr.scanned_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(lhb.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(ars.captured_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(shtr.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        100 - sm.rank_speed AS score,
        COALESCE(sm.pct_change, 0) AS change_pct,
        COALESCE(sm.speed, 0) AS speed_pct,
        ROUND(COALESCE(sm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
          CONCAT('研究池:', COALESCE(rp.source_rank_label, CONCAT('#', rp.source_rank))),
          CONCAT('扫描Top', sm.rank_speed),
          CONCAT('同锚:', COALESCE(ars.member_count, ssr.anchor_member_count), '只'),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), '')
          )
        ) AS tags,
        ROW_NUMBER() OVER (
          PARTITION BY sr.id
          ORDER BY sm.rank_speed ASC
        ) AS rn
      FROM recent_scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id = sr.id
      JOIN research_pool rp ON rp.code = sm.code
      JOIN scan_stock_roles ssr ON ssr.scan_run_id = sr.id AND ssr.code = sm.code
      LEFT JOIN stock_company_profiles scp ON scp.code = sm.code
      LEFT JOIN stock_move_judgements smj ON smj.id = COALESCE(
        (
          SELECT exact_j.id
          FROM stock_move_judgements exact_j
          WHERE exact_j.event_type='realtime'
            AND exact_j.event_id=sr.run_id
            AND exact_j.code=sm.code
          LIMIT 1
        ),
        (
          SELECT recent_j.id
          FROM stock_move_judgements recent_j
          WHERE recent_j.trade_date={day}
            AND recent_j.code=sm.code
            AND recent_j.event_time <= sr.scanned_at
          ORDER BY recent_j.event_time DESC, recent_j.updated_at DESC
          LIMIT 1
        )
      )
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = sm.code
        AND EXISTS (
          SELECT 1 FROM stock_effective_facts ef WHERE ef.trade_date={day} AND ef.code=sm.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
        )
      LEFT JOIN latest_lhb lhb ON lhb.code = sm.code
      LEFT JOIN root_evidence re ON re.code = sm.code
      LEFT JOIN stock_headline_themes sht ON sht.code = sm.code
      LEFT JOIN stock_headline_theme_roles shtr ON shtr.code = sm.code
      LEFT JOIN latest_anchor_role_snapshot ars ON ars.anchor_name = ssr.primary_anchor_name
      LEFT JOIN latest_anchor_role_member arm
        ON arm.snapshot_run_id = ars.snapshot_run_id
       AND arm.anchor_name = ars.anchor_name
       AND arm.code = sm.code
      WHERE sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
        AND COALESCE(sm.speed, 0) >= 1.5
    ),
    window_ranked AS (
      SELECT
        rw.ended_at AS event_time,
        'window' AS kind,
        '稳定异动' AS kind_label,
        wm.code,
        wm.name,
        wm.rank_no AS sort_rank,
        CONCAT(wm.name, ' ', wm.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          COALESCE(NULLIF(smj.move_explanation, ''), NULLIF(el.community_main_claim, ''), NULLIF(el.why_hypothesis, ''), NULLIF(el.hard_evidence_summary, ''), '')
        ) AS summary,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '\n'), ''),
          IF(COALESCE(smj.final_view, '') <> '', CONCAT('【判断层】', smj.final_view, '\n'), ''),
          IF(COALESCE(wsr.role_reason, '') <> '', CONCAT('【实时证据】', wsr.role_reason, '\n'), ''),
          IF(
            COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason', '') <> ''
            AND COALESCE(wsr.role_reason, '') NOT LIKE '%个股证据：%',
            CONCAT('【实时证据】个股证据：', COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason'), '\n'),
            ''
          ),
          IF(COALESCE(aes.summary_text, '') <> '' AND COALESCE(aes.impact_summary_text, '') = '', CONCAT('【异步证据】异步总结：', aes.summary_text, '\n'), ''),
          IF(COALESCE(aes.impact_summary_text, '') <> '', CONCAT('【异步证据】有效事实总结：', aes.impact_summary_text, '\n'), ''),
          IF(JSON_LENGTH(COALESCE(re.items, JSON_ARRAY())) > 0, CONCAT('【异步证据】基础事实：', re.items->>'$[0].body', '\n'), ''),
          IF(COALESCE(el.hard_evidence_summary, '') <> '', CONCAT('【异步证据】个股证据：', LEFT(el.hard_evidence_summary, 220), '\n'), ''),
          IF(COALESCE(el.market_evidence, '') <> '', CONCAT('【异步证据】题材证据：', LEFT(el.market_evidence, 220), '\n'), '')
        ) AS detail,
        JSON_MERGE_PRESERVE(
          IF(JSON_VALID(aes.key_facts) AND JSON_LENGTH(aes.key_facts) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '事实卡',
              {window_async_source}
              'body', CONCAT_WS('\n', aes.key_facts->>'$[0]', aes.key_facts->>'$[1]', aes.key_facts->>'$[2]', aes.key_facts->>'$[3]', aes.key_facts->>'$[4]', aes.key_facts->>'$[5]', aes.key_facts->>'$[6]', aes.key_facts->>'$[7]', aes.key_facts->>'$[8]', aes.key_facts->>'$[9]', aes.key_facts->>'$[10]', aes.key_facts->>'$[11]'),
              'priority', 0,
              'payload', JSON_EXTRACT(aes.key_facts, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.move_reason, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '事实卡',
              {window_async_source}
              'body', aes.move_reason,
              'priority', 1
            )), JSON_ARRAY()),
          IF(JSON_VALID(lhb.key_facts) AND JSON_LENGTH(lhb.key_facts) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '龙虎榜席位',
              'type', 'lhb',
              'source', CONCAT(IF(lhb.trade_date={day}, '当日龙虎榜', '上一交易日龙虎榜'), ' / ', lhb.seat_signal_label),
              {window_lhb_source}
              'body', CONCAT_WS('\n', lhb.key_facts->>'$[0]', lhb.key_facts->>'$[1]', lhb.key_facts->>'$[2]', lhb.key_facts->>'$[3]'),
              'evidence_date', DATE_FORMAT(lhb.trade_date, '%Y-%m-%d'),
              'priority', 3,
              'payload', JSON_EXTRACT(lhb.key_facts, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.move_reason, '') = '' AND COALESCE(smj.move_explanation, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '判断引擎',
              {window_judgement_source}
              'body', smj.move_explanation,
              'priority', 1
            )), JSON_ARRAY()),
          IF(COALESCE(smj.final_view, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '持续性',
              'type', 'quality',
              'source', '判断引擎',
              {window_judgement_source}
              'body', CONCAT(
                smj.sustainability_label, ' / ', ROUND(smj.sustainability_score, 0), '分',
                '；区间', ROUND(smj.anchor_leadership_score, 0),
                ' 盘口', ROUND(smj.tape_confirm_score, 0),
                ' 行为', ROUND(COALESCE(JSON_EXTRACT(smj.score_detail, '$.short_term_behavior'), 0), 0),
                ' 风险-', ROUND(smj.anchor_risk_deduction, 0),
                IF(COALESCE(smj.risk_item, '') <> '', CONCAT('；', smj.risk_item), '')
              ),
              'priority', 2
            )), JSON_ARRAY()),
          IF(JSON_VALID(smj.support_items) AND JSON_LENGTH(smj.support_items) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心支撑',
              'type', 'support',
              'source', '判断引擎',
              {window_judgement_source}
              'body', CONCAT_WS('\n',
                smj.support_items->>'$[0]',
                IF(COALESCE(smj.support_items->>'$[1]', '') LIKE '区间领头：%', smj.support_items->>'$[2]', smj.support_items->>'$[1]')
              ),
              'priority', 3
            )), JSON_ARRAY()),
          IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0 OR COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.influence_reasons')), 0) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '带动性',
              'type', 'influence',
              'source', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, '多题材扩散', CONCAT('同锚扩散 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_label')), ''))),
              {window_judgement_source}
              'body', IF(
                COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0,
                CONCAT('关联头条题材带动性 ', JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), ' 个'),
                CONCAT_WS('\n',
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[0]')),
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[1]')),
                  JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[2]'))
                )
              ),
              'priority', 4,
              'payload', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence'), JSON_EXTRACT(smj.score_detail, '$.influence_reasons')),
              'structured_payload', IF(COALESCE(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.multi_theme_influence')), 0) > 0, JSON_OBJECT('mode', 'multi_theme'), JSON_EXTRACT(smj.score_detail, '$.influence_payload'))
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.sustainability_basis) AND JSON_LENGTH(aes.sustainability_basis) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '持续依据',
              'type', 'support',
              'source', '事实卡',
              {window_async_source}
              'body', CONCAT_WS('\n', aes.sustainability_basis->>'$[0]', aes.sustainability_basis->>'$[1]', aes.sustainability_basis->>'$[2]'),
              'priority', 4,
              'payload', JSON_EXTRACT(aes.sustainability_basis, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.main_flaw, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '最大瑕疵',
              'type', 'flaw',
              'source', '事实卡',
              {window_async_source}
              'body', aes.main_flaw,
              'priority', 7
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.missing_evidence) AND JSON_LENGTH(aes.missing_evidence) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '证据缺口',
              'type', 'gap',
              'source', '事实卡',
              {window_async_source}
              'body', CONCAT_WS('\n', aes.missing_evidence->>'$[0]', aes.missing_evidence->>'$[1]', aes.missing_evidence->>'$[2]'),
              'priority', 8,
              'payload', JSON_EXTRACT(aes.missing_evidence, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(smj.move_explanation, '') = '' AND COALESCE(NULLIF(aes.final_view, ''), NULLIF(aes.move_explanation, '')) IS NOT NULL, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动解释',
              'type', 'move',
              'source', '模型解释',
              {window_async_source}
              'body', COALESCE(NULLIF(aes.final_view, ''), NULLIF(aes.move_explanation, '')),
              'priority', 1
            )), JSON_ARRAY()),
          IF(COALESCE(aes.quality_label, '') <> '' OR COALESCE(aes.explanation_strength, 'none') <> 'none', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异动质量',
              'type', 'quality',
              'source', '模型判断',
              {window_async_source}
              'body', CONCAT(COALESCE(NULLIF(aes.quality_label, ''), '未分类'), ' / 解释强度:', COALESCE(aes.explanation_strength, 'none')),
              'priority', 2
            )), JSON_ARRAY()),
          IF(COALESCE(aes.anchor_match_reason, '') <> '' OR COALESCE(aes.anchor_match, 'weak') <> 'weak', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '锚点一致性',
              'type', 'anchor',
              'source', '模型判断',
              {window_async_source}
              'body', CONCAT(COALESCE(aes.anchor_match, 'weak'), IF(COALESCE(aes.anchor_match_reason, '') <> '', CONCAT('：', aes.anchor_match_reason), '')),
              'priority', 3
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.core_support) AND JSON_LENGTH(aes.core_support) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心支撑',
              'type', 'support',
              'source', '模型筛选',
              {window_async_source}
              'body', CONCAT_WS('\n', aes.core_support->>'$[0]', aes.core_support->>'$[1]'),
              'priority', 4,
              'payload', JSON_EXTRACT(aes.core_support, '$')
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.counterpoints) AND JSON_LENGTH(aes.counterpoints) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '瑕疵',
              'type', 'counter',
              'source', '模型判断',
              {window_async_source}
              'body', aes.counterpoints->>'$[0]',
              'priority', 6,
              'payload', JSON_EXTRACT(aes.counterpoints, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.final_analysis, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心结论',
              'type', 'final',
              'source', '模型结论',
              {window_async_source}
              'body', COALESCE(aes.final_analysis, ''),
              'priority', 5
            )), JSON_ARRAY()),
          IF(COALESCE(aes.timeliness_reason, '') <> '' OR COALESCE(aes.timeliness_label, 'unknown') <> 'unknown', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '时效判断',
              'type', 'timeliness',
              'source', '模型判断',
              {window_async_source}
              'body', CONCAT(
                CASE COALESCE(aes.timeliness_label, 'unknown')
                  WHEN 'fresh' THEN '高时效'
                  WHEN 'recent' THEN '近期有效'
                  WHEN 'stale' THEN '时效偏弱'
                  ELSE '时效未知'
                END,
                IF(COALESCE(aes.timeliness_reason, '') <> '', CONCAT('：', aes.timeliness_reason), '')
              ),
              'priority', 8
            )), JSON_ARRAY()),
          IF(JSON_VALID(aes.core_evidence_items) AND JSON_LENGTH(aes.core_evidence_items) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '核心证据',
              'type', 'core',
              'source', '过滤后证据',
              {window_async_source}
              'body', COALESCE(aes.evidence_filter_summary, ''),
              'priority', 9,
              'payload', JSON_EXTRACT(aes.core_evidence_items, '$')
            )), JSON_ARRAY()),
          IF(COALESCE(aes.impact_summary_text, '') <> '' AND NOT (JSON_VALID(aes.key_facts) AND JSON_LENGTH(aes.key_facts) > 0), JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '模型判断',
              {window_async_source}
              'body', COALESCE(aes.impact_summary_text, ''),
              'priority', 10,
              'payload', IF(JSON_VALID(aes.impact_factors), JSON_EXTRACT(aes.impact_factors, '$'), JSON_ARRAY())
            )), JSON_ARRAY()),
          IF(COALESCE(aes.summary_text, '') <> '' AND COALESCE(aes.impact_summary_text, '') = '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '异步总结',
              'type', 'summary',
              'source', '模型总结',
              {window_async_source}
              'body', COALESCE(aes.summary_text, ''),
              'priority', 20
            )), JSON_ARRAY()),
          COALESCE(re.items, JSON_ARRAY()),
          IF(JSON_LENGTH(COALESCE(shtr.roles, JSON_ARRAY())) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '头条题材角色',
              'type', 'headline_theme_roles',
              'source', '研究池题材角色',
              'source_table', 'research_pool_theme_members',
              'source_key', CONCAT('research_pool_theme_members:', {day}, ':', wm.code),
              'source_confidence', 'explicit',
              'data_date', {day},
              'evidence_date', {day},
              'updated_at', DATE_FORMAT(shtr.latest_updated_at, '%Y-%m-%d %H:%i:%s'),
              'source_generation', 'intraday',
              'availability', 'intraday',
              'evidence_group', 'current_effective',
              'evidence_role', 'market_realtime',
              'display_level', 'primary',
              'valid_status', 'watch',
              'source_registry', JSON_OBJECT(
                'source_table', 'research_pool_theme_members',
                'source_generation', 'intraday',
                'availability', 'intraday',
                'evidence_group', 'current_effective',
                'update_cycle', 'scan_loop',
                'data_date_policy', 'event_day'
              ),
              'body', CONCAT('关联今日头条题材 ', JSON_LENGTH(shtr.roles), ' 个'),
              'priority', 21,
              'payload', JSON_EXTRACT(shtr.roles, '$')
            )), JSON_ARRAY()),
          IF(warm.id IS NOT NULL OR wars.id IS NOT NULL OR COALESCE(wsr.role_label, ssr.role_label, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '领涨中军',
              'type', 'realtime',
              'source', '研究池题材角色',
              {window_role_source}
              'body', CONCAT_WS('\n',
                CONCAT('题材：', COALESCE(wars.anchor_name, wsr.sector_key, ssr.primary_anchor_name, '未锚定')),
                CONCAT('定位：', CASE WHEN warm.id IS NOT NULL THEN warm.role_label ELSE COALESCE(wsr.role_label, ssr.role_label, '') END),
                IF(COALESCE(wars.leader_name, wss.leader_name, ssr.leader_name, '') <> '', CONCAT('领涨：', COALESCE(wars.leader_name, wss.leader_name, ssr.leader_name), IF(COALESCE(wars.leader_code, wss.leader_code, ssr.leader_code, '') <> '', CONCAT(' ', COALESCE(wars.leader_code, wss.leader_code, ssr.leader_code)), '')), NULL),
                IF(COALESCE(wars.core_name, wss.core_name, ssr.core_name, '') <> '', CONCAT('中军：', COALESCE(wars.core_name, wss.core_name, ssr.core_name), IF(COALESCE(wars.core_code, wss.core_code, ssr.core_code, '') <> '', CONCAT(' ', COALESCE(wars.core_code, wss.core_code, ssr.core_code)), '')), NULL),
                CONCAT('题材内研究池成员：', COALESCE(wars.member_count, wsr.sector_stock_count, ssr.anchor_member_count, 0), '只')
              ),
              'priority', 22
            )), JSON_ARRAY()),
          IF(COALESCE(wsr.role_reason, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '实时判断',
              'type', 'theme',
              'source', '实时扫描',
              {window_role_source}
              'body', COALESCE(wsr.role_reason, ''),
              'priority', 25
            )), JSON_ARRAY()),
          IF(
            COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason', '') <> ''
            AND COALESCE(wsr.role_reason, '') NOT LIKE '%个股证据：%',
            JSON_ARRAY(JSON_OBJECT(
              'layer', 'realtime',
              'label', '个股证据',
              'type', 'stock',
              'source', '个股解释',
              {window_role_source}
              'body', COALESCE(wsr.raw_json->>'$.raw_json.stock_reason', wsr.raw_json->>'$.stock_reason'),
              'priority', 40
            )),
            JSON_ARRAY()
          ),
          IF(COALESCE(el.hard_evidence_summary, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '个股证据',
              'type', 'stock',
              'source', '证据层',
              {window_evidence_layer_source}
              'body', LEFT(COALESCE(el.hard_evidence_summary, ''), 220),
              'priority', 50
            )), JSON_ARRAY()),
          IF(COALESCE(el.market_evidence, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '题材证据',
              'type', 'theme',
              'source', '证据层',
              {window_evidence_layer_source}
              'body', LEFT(COALESCE(el.market_evidence, ''), 220),
              'priority', 55
            )), JSON_ARRAY())
        ) AS evidence_items,
        IF(JSON_VALID(smj.score_detail), JSON_EXTRACT(smj.score_detail, '$.display_contract'), JSON_OBJECT()) AS display_contract,
        DATE_FORMAT(rw.ended_at, '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s') AS judgement_updated_at,
        DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s') AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s') AS lhb_updated_at,
        DATE_FORMAT(el.updated_at, '%Y-%m-%d %H:%i:%s') AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(rw.ended_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(lhb.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(el.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(wars.captured_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(shtr.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        ROUND(wm.window_score, 1) AS score,
        COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS change_pct,
        COALESCE(wm.max_speed, 0) AS speed_pct,
        ROUND(COALESCE(wm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
          CONCAT('研究池:', COALESCE(rp.source_rank_label, CONCAT('#', rp.source_rank))),
          CONCAT('Top', wm.rank_no),
          COALESCE(CONCAT('同锚:', COALESCE(wars.member_count, wsr.sector_stock_count, ssr.anchor_member_count), '只'), ''),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), ''),
          CONCAT('证据:', COALESCE(el.evidence_strength, 'pending')),
          CONCAT('出现:', wm.appearance_count, '次')
          )
        ) AS tags,
        ROW_NUMBER() OVER (
          PARTITION BY rw.id
          ORDER BY wm.rank_no ASC
        ) AS rn
      FROM recent_windows rw
      JOIN window_movers wm ON wm.window_id = rw.id
      JOIN research_pool rp ON rp.code = wm.code
      LEFT JOIN stock_company_profiles scp ON scp.code = wm.code
      LEFT JOIN stock_headline_themes sht ON sht.code = wm.code
      LEFT JOIN stock_headline_theme_roles shtr ON shtr.code = wm.code
      LEFT JOIN window_stock_roles wsr ON wsr.window_id = rw.id AND wsr.code = wm.code
      LEFT JOIN window_sector_stats wss ON wss.window_id = rw.id AND wss.sector_key = wsr.sector_key
      LEFT JOIN scan_stock_roles ssr ON ssr.id = (
        SELECT ssr2.id
        FROM scan_stock_roles ssr2
        JOIN scan_runs sr2 ON sr2.id = ssr2.scan_run_id
        WHERE ssr2.code = wm.code
          AND sr2.accepted = 1
          AND sr2.scanned_at BETWEEN DATE_SUB(rw.ended_at, INTERVAL 10 MINUTE) AND rw.ended_at
        ORDER BY sr2.scanned_at DESC
        LIMIT 1
      )
      LEFT JOIN latest_anchor_role_snapshot wars ON wars.anchor_name = COALESCE(wsr.sector_key, ssr.primary_anchor_name)
      LEFT JOIN latest_anchor_role_member warm
        ON warm.snapshot_run_id = wars.snapshot_run_id
       AND warm.anchor_name = wars.anchor_name
       AND warm.code = wm.code
      LEFT JOIN evidence_layers el ON el.window_id = rw.id AND el.code = wm.code
      LEFT JOIN stock_move_judgements smj ON smj.id = COALESCE(
        (
          SELECT exact_j.id
          FROM stock_move_judgements exact_j
          WHERE exact_j.event_type='stable'
            AND exact_j.event_id=rw.window_id
            AND exact_j.code=wm.code
          LIMIT 1
        ),
        (
          SELECT recent_j.id
          FROM stock_move_judgements recent_j
          WHERE recent_j.trade_date={day}
            AND recent_j.code=wm.code
            AND recent_j.event_time <= rw.ended_at
          ORDER BY recent_j.event_time DESC, recent_j.updated_at DESC
          LIMIT 1
        )
      )
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = wm.code
        AND EXISTS (
          SELECT 1 FROM stock_effective_facts ef WHERE ef.trade_date={day} AND ef.code=wm.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
        )
      LEFT JOIN latest_lhb lhb ON lhb.code = wm.code
      LEFT JOIN root_evidence re ON re.code = wm.code
      WHERE wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        AND COALESCE(wm.max_speed, 0) >= 1.5
        AND wm.rank_no <= 5
    )
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'event_time', DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s'),
      'kind', kind,
      'kind_label', kind_label,
      'code', code,
      'name', name,
      'sort_rank', sort_rank,
      'title', title,
      'summary', summary,
      'detail', detail,
      'evidence_schema_version', 3,
      'evidence_items', evidence_items,
      'display_contract', display_contract,
      'intraday_source_updated_at', intraday_source_updated_at,
      'judgement_updated_at', judgement_updated_at,
      'async_evidence_updated_at', async_evidence_updated_at,
      'period_rank_updated_at', period_rank_updated_at,
      'lhb_updated_at', lhb_updated_at,
      'evidence_layer_updated_at', evidence_layer_updated_at,
      'latest_source_updated_at', latest_source_updated_at,
      'score', score,
      'change_pct', change_pct,
      'speed_pct', speed_pct,
      'amount_yi', amount_yi,
      'tags', tags
    )), JSON_ARRAY())
    FROM (
      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM auction_ranked
      WHERE rn <= 3
        {auction_detail_filter}

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM scan_ranked
      WHERE rn <= {rank_limit}
        {scan_detail_filter}

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM window_ranked
      WHERE rn <= {rank_limit}
        {window_detail_filter}

      ORDER BY event_time DESC,
        CASE kind WHEN 'scan' THEN 3 WHEN 'auction' THEN 2 WHEN 'window' THEN 1 ELSE 0 END DESC,
        CASE kind WHEN 'auction' THEN sort_rank ELSE 0 END ASC,
        score DESC
    ) feed;
    """


def intel_feed_list_sql(trade_date: str | None = "", window_count: int = 240) -> str:
    day = sql_string(trade_date or date.today().strftime("%Y-%m-%d"))
    auction_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', ats.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "model_summary",
    )
    scan_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', sm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "model_summary",
    )
    window_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', wm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "model_summary",
    )
    return f"""
    WITH
    {research_pool_snapshot_cte(trade_date or date.today().strftime("%Y-%m-%d"))},
    recent_scan_runs AS (
      SELECT id, run_id, scanned_at
      FROM scan_runs
      WHERE scanned_at >= CAST({day} AS DATETIME)
        AND scanned_at < DATE_ADD(CAST({day} AS DATE), INTERVAL 1 DAY)
        AND accepted=1
      ORDER BY scanned_at DESC
    ),
    recent_windows AS (
      SELECT id, window_id, ended_at
      FROM windows
      WHERE ended_at >= CAST({day} AS DATETIME)
        AND ended_at < DATE_ADD(CAST({day} AS DATE), INTERVAL 1 DAY)
        AND status='done'
        AND aggregate_count > 0
      ORDER BY ended_at DESC
      LIMIT 1
    ),
    stock_headline_themes AS (
      SELECT code, JSON_ARRAYAGG(theme_name) AS tags
      FROM (
        SELECT code, theme_name, MIN(theme_rank) AS theme_rank, MAX(match_score) AS match_score
        FROM research_pool_theme_members
        WHERE trade_date={day}
          AND is_headline_theme=1
          AND COALESCE(theme_name, '') <> ''
        GROUP BY code, theme_name
        ORDER BY theme_rank ASC, match_score DESC, theme_name ASC
      ) themes
      GROUP BY code
    ),
    root_evidence AS (
      SELECT
        rec.code,
        rec.updated_at AS latest_updated_at,
        COALESCE((
          SELECT JSON_ARRAYAGG(t.item)
          FROM (
            SELECT jt.ord, jt.item
            FROM JSON_TABLE(rec.items, '$[*]' COLUMNS (
              ord FOR ORDINALITY,
              item JSON PATH '$',
              source_table VARCHAR(64) PATH '$.source_table' NULL ON EMPTY,
              label VARCHAR(64) PATH '$.label' NULL ON EMPTY,
              source VARCHAR(128) PATH '$.source' NULL ON EMPTY,
              type VARCHAR(64) PATH '$.type' NULL ON EMPTY,
              body VARCHAR(512) PATH '$.body' NULL ON EMPTY
            )) jt
            WHERE COALESCE(jt.source_table, '') <> 'stock_period_rankings'
              AND COALESCE(jt.label, '') <> '区间领头'
              AND COALESCE(jt.type, '') <> 'period'
              AND COALESCE(jt.source, '') NOT LIKE '%问财%'
              AND COALESCE(jt.body, '') NOT LIKE '%问财%'
            ORDER BY jt.ord
          ) t
        ), JSON_ARRAY()) AS items
      FROM stock_root_evidence_cache rec
      WHERE rec.trade_date={day}
    ),
    auction_ranked AS (
      SELECT
        MAX(COALESCE(ats.last_seen_minute, ats.generated_at)) OVER () AS event_time,
        'auction' AS kind,
        '竞价封单' AS kind_label,
        ats.code,
        ats.stock_name AS name,
        ROW_NUMBER() OVER (ORDER BY COALESCE(ats.last_seal_amount, 0) DESC, ats.final_candidate_rank ASC) AS sort_rank,
        CONCAT(ats.stock_name, ' ', ats.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          '涨停封单 ', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿；',
          '竞价涨幅 ', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%'
        ) AS summary,
        '' AS detail,
        JSON_MERGE_PRESERVE(
          IF(COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')) IS NOT NULL, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '模型总结',
              {auction_async_source}
              'body', COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')),
              'priority', 0
            )), JSON_ARRAY()),
          COALESCE(re.items, JSON_ARRAY())
        ) AS evidence_items,
        JSON_OBJECT() AS display_contract,
        DATE_FORMAT(COALESCE(ats.last_seen_minute, ats.generated_at), '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        CAST('' AS CHAR) AS judgement_updated_at,
        DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s') AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        CAST('' AS CHAR) AS lhb_updated_at,
        CAST('' AS CHAR) AS evidence_layer_updated_at,
        DATE_FORMAT(COALESCE(ats.generated_at, ats.last_seen_minute), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        COALESCE(ats.last_seal_amount, 0) / 100000000 AS score,
        COALESCE(ats.last_auction_pct, 0) AS change_pct,
        0 AS speed_pct,
        ROUND(COALESCE(ats.last_auction_amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
            CONCAT('封单:', ROUND(COALESCE(ats.last_seal_amount, 0) / 100000000, 2), '亿'),
            CONCAT('竞价:', ROUND(COALESCE(ats.last_auction_pct, 0), 2), '%')
          )
        ) AS tags,
        ROW_NUMBER() OVER (ORDER BY COALESCE(ats.last_seal_amount, 0) DESC, ats.final_candidate_rank ASC) AS rn
      FROM auction_trend_summary ats
      LEFT JOIN stock_company_profiles scp ON scp.code = ats.code
      LEFT JOIN stock_headline_themes sht ON sht.code = ats.code
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = ats.code
        AND EXISTS (
          SELECT 1 FROM stock_effective_facts ef WHERE ef.trade_date={day} AND ef.code=ats.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
        )
      LEFT JOIN root_evidence re ON re.code = ats.code
      WHERE ats.trade_date={day}
        AND COALESCE(ats.limit_up_count, 0) > 0
        AND COALESCE(ats.last_seal_amount, 0) > 0
        AND COALESCE(ats.last_auction_pct, 0) >= 9.5
    ),
    scan_ranked AS (
      SELECT
        sr.scanned_at AS event_time,
        'scan' AS kind,
        '实时领涨' AS kind_label,
        sm.code,
        sm.name,
        sm.rank_speed AS sort_rank,
        CONCAT(sm.name, ' ', sm.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          COALESCE(NULLIF(smj.move_explanation, ''), NULLIF(aes.move_reason, ''), NULLIF(aes.final_view, ''), '')
        ) AS summary,
        '' AS detail,
        JSON_MERGE_PRESERVE(
          IF(COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')) IS NOT NULL, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '模型总结',
              {scan_async_source}
              'body', COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')),
              'priority', 0
            )), JSON_ARRAY()),
          COALESCE(re.items, JSON_ARRAY())
        ) AS evidence_items,
        JSON_OBJECT() AS display_contract,
        DATE_FORMAT(sr.scanned_at, '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s') AS judgement_updated_at,
        DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s') AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        CAST('' AS CHAR) AS lhb_updated_at,
        CAST('' AS CHAR) AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(sr.scanned_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        100 - sm.rank_speed AS score,
        COALESCE(sm.pct_change, 0) AS change_pct,
        COALESCE(sm.speed, 0) AS speed_pct,
        ROUND(COALESCE(sm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
          CONCAT('研究池:', COALESCE(rp.source_rank_label, CONCAT('#', rp.source_rank))),
          CONCAT('扫描Top', sm.rank_speed),
          CONCAT('同锚:', COALESCE(ssr.anchor_member_count, 0), '只'),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), '')
          )
        ) AS tags,
        ROW_NUMBER() OVER (PARTITION BY sr.id ORDER BY sm.rank_speed ASC) AS rn
      FROM recent_scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id = sr.id
      JOIN research_pool rp ON rp.code = sm.code
      JOIN scan_stock_roles ssr ON ssr.scan_run_id = sr.id AND ssr.code = sm.code
      LEFT JOIN stock_company_profiles scp ON scp.code = sm.code
      LEFT JOIN stock_move_judgements smj ON smj.id = COALESCE(
        (
          SELECT exact_j.id
          FROM stock_move_judgements exact_j
          WHERE exact_j.event_type='realtime'
            AND exact_j.event_id=sr.run_id
            AND exact_j.code=sm.code
          LIMIT 1
        ),
        (
          SELECT recent_j.id
          FROM stock_move_judgements recent_j
          WHERE recent_j.trade_date={day}
            AND recent_j.code=sm.code
            AND recent_j.event_time <= sr.scanned_at
          ORDER BY recent_j.event_time DESC, recent_j.updated_at DESC
          LIMIT 1
        )
      )
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = sm.code
        AND EXISTS (
          SELECT 1 FROM stock_effective_facts ef WHERE ef.trade_date={day} AND ef.code=sm.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
        )
      LEFT JOIN root_evidence re ON re.code = sm.code
      LEFT JOIN stock_headline_themes sht ON sht.code = sm.code
      WHERE sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
        AND COALESCE(sm.speed, 0) >= 1.5
    ),
    window_ranked AS (
      SELECT
        rw.ended_at AS event_time,
        'window' AS kind,
        '稳定异动' AS kind_label,
        wm.code,
        wm.name,
        wm.rank_no AS sort_rank,
        CONCAT(wm.name, ' ', wm.code) AS title,
        CONCAT(
          IF(COALESCE(scp.company_highlights, '') <> '', CONCAT('【亮点】', scp.company_highlights, '；'), ''),
          COALESCE(NULLIF(smj.move_explanation, ''), NULLIF(aes.move_reason, ''), NULLIF(el.community_main_claim, ''), NULLIF(el.hard_evidence_summary, ''), '')
        ) AS summary,
        '' AS detail,
        JSON_MERGE_PRESERVE(
          IF(COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')) IS NOT NULL, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '有效事实总结',
              'type', 'facts',
              'source', '模型总结',
              {window_async_source}
              'body', COALESCE(NULLIF(aes.impact_summary_text, ''), NULLIF(aes.summary_text, ''), NULLIF(aes.final_view, ''), NULLIF(aes.move_reason, '')),
              'priority', 0
            )), JSON_ARRAY()),
          COALESCE(re.items, JSON_ARRAY())
        ) AS evidence_items,
        JSON_OBJECT() AS display_contract,
        DATE_FORMAT(rw.ended_at, '%Y-%m-%d %H:%i:%s') AS intraday_source_updated_at,
        DATE_FORMAT(smj.updated_at, '%Y-%m-%d %H:%i:%s') AS judgement_updated_at,
        DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s') AS async_evidence_updated_at,
        CAST('' AS CHAR) AS period_rank_updated_at,
        CAST('' AS CHAR) AS lhb_updated_at,
        DATE_FORMAT(el.updated_at, '%Y-%m-%d %H:%i:%s') AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(rw.ended_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(el.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        wm.window_score AS score,
        COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS change_pct,
        COALESCE(wm.max_speed, 0) AS speed_pct,
        ROUND(COALESCE(wm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_MERGE_PRESERVE(
          COALESCE(sht.tags, JSON_ARRAY()),
          JSON_ARRAY(
          CONCAT('研究池:', COALESCE(rp.source_rank_label, CONCAT('#', rp.source_rank))),
          CONCAT('窗口Top', wm.rank_no),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), ''),
          CONCAT('出现:', wm.appearance_count, '次')
          )
        ) AS tags,
        ROW_NUMBER() OVER (PARTITION BY rw.id ORDER BY wm.rank_no ASC) AS rn
      FROM recent_windows rw
      JOIN window_movers wm ON wm.window_id = rw.id
      JOIN research_pool rp ON rp.code = wm.code
      LEFT JOIN stock_company_profiles scp ON scp.code = wm.code
      LEFT JOIN window_stock_roles wsr ON wsr.window_id = rw.id AND wsr.code = wm.code
      LEFT JOIN evidence_layers el ON el.window_id = rw.id AND el.code = wm.code
      LEFT JOIN stock_move_judgements smj ON smj.id = COALESCE(
        (
          SELECT exact_j.id
          FROM stock_move_judgements exact_j
          WHERE exact_j.event_type='stable'
            AND exact_j.event_id=rw.window_id
            AND exact_j.code=wm.code
          LIMIT 1
        ),
        (
          SELECT recent_j.id
          FROM stock_move_judgements recent_j
          WHERE recent_j.trade_date={day}
            AND recent_j.code=wm.code
            AND recent_j.event_time <= rw.ended_at
          ORDER BY recent_j.event_time DESC, recent_j.updated_at DESC
          LIMIT 1
        )
      )
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = wm.code
        AND EXISTS (
          SELECT 1 FROM stock_effective_facts ef WHERE ef.trade_date={day} AND ef.code=wm.code AND ef.evidence_group='current_effective' AND ef.valid_status='active'
        )
      LEFT JOIN root_evidence re ON re.code = wm.code
      LEFT JOIN stock_headline_themes sht ON sht.code = wm.code
      WHERE wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        AND COALESCE(wm.max_speed, 0) >= 1.5
        AND wm.rank_no <= 5
    )
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'event_time', DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s'),
      'kind', kind,
      'kind_label', kind_label,
      'code', code,
      'name', name,
      'sort_rank', sort_rank,
      'title', title,
      'summary', summary,
      'detail', detail,
      'evidence_schema_version', 3,
      'evidence_items', evidence_items,
      'display_contract', display_contract,
      'intraday_source_updated_at', intraday_source_updated_at,
      'judgement_updated_at', judgement_updated_at,
      'async_evidence_updated_at', async_evidence_updated_at,
      'period_rank_updated_at', period_rank_updated_at,
      'lhb_updated_at', lhb_updated_at,
      'evidence_layer_updated_at', evidence_layer_updated_at,
      'latest_source_updated_at', latest_source_updated_at,
      'score', score,
      'change_pct', change_pct,
      'speed_pct', speed_pct,
      'amount_yi', amount_yi,
      'tags', tags,
      'detail_loaded', false
    )), JSON_ARRAY())
    FROM (
      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM auction_ranked
      WHERE rn <= 3

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM scan_ranked
      WHERE rn <= 9999

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM window_ranked
      WHERE rn <= 9999

      ORDER BY CASE kind WHEN 'window' THEN 3 WHEN 'auction' THEN 2 WHEN 'scan' THEN 1 ELSE 0 END DESC,
        CASE kind WHEN 'window' THEN sort_rank ELSE 0 END ASC,
        CASE kind WHEN 'auction' THEN sort_rank ELSE 0 END ASC,
        event_time DESC,
        score DESC
    ) feed;
    """
