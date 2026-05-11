from __future__ import annotations

from datetime import date

from stock_move_scout.db import sql_string


def _score_detail_text(alias: str, path: str) -> str:
    return f"NULLIF(JSON_UNQUOTE(JSON_EXTRACT({alias}.score_detail, '{path}')), 'null')"


def _non_iwencai_leadership_reason(score_alias: str, index: int) -> str:
    value = _score_detail_text(score_alias, f"$.anchor_leadership_reasons[{index}]")
    return f"IF(COALESCE({value}, '') <> '' AND {value} NOT LIKE '问财%', {value}, NULL)"


def _iwencai_rank_line(rank_alias: str, period_days: int) -> str:
    column = f"rank_{period_days}d"
    return (
        f"IF(COALESCE({rank_alias}.{column}, 0) > 0, "
        f"CONCAT('问财近{period_days}日全市场第', {rank_alias}.{column}), NULL)"
    )


def _dynamic_leadership_body(score_alias: str, rank_alias: str) -> str:
    return "CONCAT_WS('\\n', {items})".format(
        items=", ".join(
            [
                _non_iwencai_leadership_reason(score_alias, 0),
                _non_iwencai_leadership_reason(score_alias, 1),
                _non_iwencai_leadership_reason(score_alias, 2),
                _iwencai_rank_line(rank_alias, 3),
                _iwencai_rank_line(rank_alias, 5),
                _iwencai_rank_line(rank_alias, 10),
            ]
        )
    )


def _dynamic_leadership_inline(score_alias: str, rank_alias: str) -> str:
    return f"REPLACE({_dynamic_leadership_body(score_alias, rank_alias)}, '\\n', '；')"


def _json_source_fields(
    source_table: str,
    source_key_expr: str,
    data_date_expr: str,
    updated_at_expr: str,
    source_generation: str,
) -> str:
    return f"""
              'source_table', '{source_table}',
              'source_key', {source_key_expr},
              'source_confidence', 'explicit',
              'data_date', {data_date_expr},
              'updated_at', {updated_at_expr},
              'source_generation', '{source_generation}',"""


def trade_dates_sql() -> str:
    return """
    SELECT JSON_OBJECT(
      'latest', COALESCE(MAX(day_text), DATE_FORMAT(CURDATE(), '%Y-%m-%d')),
      'dates', COALESCE(JSON_ARRAYAGG(day_text), JSON_ARRAY())
    )
    FROM (
      SELECT DISTINCT DATE_FORMAT(day_value, '%Y-%m-%d') AS day_text
      FROM (
        SELECT DATE(scanned_at) AS day_value FROM scan_runs WHERE accepted=1
        UNION ALL
        SELECT DATE(ended_at) AS day_value FROM windows WHERE status='done' AND aggregate_count > 0
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
      ORDER BY trend_score DESC, final_candidate_rank ASC
      LIMIT 10
    ) ranked;
    """


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
    if clean_kind:
        if clean_kind != "scan":
            scan_detail_filter += " AND 1=0"
        if clean_kind != "window":
            window_detail_filter += " AND 1=0"
    if clean_code:
        scan_detail_filter += f" AND code={sql_string(clean_code)}"
        window_detail_filter += f" AND code={sql_string(clean_code)}"
    if clean_event_time:
        scan_detail_filter += f" AND DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
        window_detail_filter += f" AND DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s')={sql_string(clean_event_time)}"
    leadership_body = _dynamic_leadership_body("smj", "pr")
    leadership_inline = _dynamic_leadership_inline("smj", "pr")
    scan_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', sm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "async",
    )
    window_async_source = _json_source_fields(
        "async_evidence_summaries",
        "CONCAT('async_evidence_summaries:', DATE_FORMAT(aes.trade_date, '%Y-%m-%d'), ':', wm.code)",
        "DATE_FORMAT(aes.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(aes.updated_at, '%Y-%m-%d %H:%i:%s')",
        "async",
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
    scan_period_source = _json_source_fields(
        "stock_period_rankings",
        "CONCAT('stock_period_rankings:', pr.data_date, ':', sm.code)",
        "pr.data_date",
        "DATE_FORMAT(pr.updated_at, '%Y-%m-%d %H:%i:%s')",
        "after_close",
    )
    window_period_source = _json_source_fields(
        "stock_period_rankings",
        "CONCAT('stock_period_rankings:', pr.data_date, ':', wm.code)",
        "pr.data_date",
        "DATE_FORMAT(pr.updated_at, '%Y-%m-%d %H:%i:%s')",
        "after_close",
    )
    scan_lhb_source = _json_source_fields(
        "stock_lhb_seat_evidence",
        "CONCAT('stock_lhb_seat_evidence:', DATE_FORMAT(lhb.trade_date, '%Y-%m-%d'), ':', sm.code)",
        "DATE_FORMAT(lhb.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s')",
        "after_close",
    )
    window_lhb_source = _json_source_fields(
        "stock_lhb_seat_evidence",
        "CONCAT('stock_lhb_seat_evidence:', DATE_FORMAT(lhb.trade_date, '%Y-%m-%d'), ':', wm.code)",
        "DATE_FORMAT(lhb.trade_date, '%Y-%m-%d')",
        "DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s')",
        "after_close",
    )
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
    return f"""
    SET SESSION group_concat_max_len=16384;
    WITH
    recent_scan_runs AS (
      SELECT id, run_id, scanned_at
      FROM scan_runs
      WHERE DATE(scanned_at)={day}
        AND accepted=1
      ORDER BY scanned_at DESC
      LIMIT 1
    ),
    recent_windows AS (
      SELECT id, window_id, ended_at
      FROM windows
      WHERE DATE(ended_at)={day}
        AND status='done'
        AND aggregate_count > 0
      ORDER BY ended_at DESC
    ),
    latest_anchor_role_snapshot AS (
      SELECT s.*
      FROM anchor_realtime_role_snapshots s
      JOIN (
        SELECT anchor_name, MAX(captured_at) AS max_at
        FROM anchor_realtime_role_snapshots
        WHERE DATE(captured_at)={day}
        GROUP BY anchor_name
      ) latest
        ON latest.anchor_name = s.anchor_name
       AND latest.max_at = s.captured_at
    ),
    latest_anchor_role_member AS (
      SELECT m.*
      FROM anchor_realtime_role_members m
      JOIN latest_anchor_role_snapshot s
        ON s.snapshot_run_id = m.snapshot_run_id
       AND s.anchor_name = m.anchor_name
    ),
    period_ranks AS (
      SELECT
        code,
        DATE_FORMAT(MAX(trade_date), '%Y-%m-%d') AS data_date,
        MIN(IF(period_days=3, rank_no, NULL)) AS rank_3d,
        MIN(IF(period_days=5, rank_no, NULL)) AS rank_5d,
        MIN(IF(period_days=10, rank_no, NULL)) AS rank_10d,
        MAX(updated_at) AS updated_at
      FROM (
        SELECT ranked.*
        FROM (
          SELECT
            r.*,
            ROW_NUMBER() OVER (
              PARTITION BY r.code, r.period_days
              ORDER BY r.trade_date DESC, r.updated_at DESC
            ) AS rn
          FROM stock_period_rankings r
          WHERE r.trade_date <= {day}
            AND r.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 7 DAY)
            AND r.period_days IN (3,5,10)
        ) ranked
        WHERE ranked.rn=1
      ) latest
      GROUP BY code
    ),
    latest_lhb AS (
      SELECT latest.*
      FROM (
        SELECT
          lhb.*,
          ROW_NUMBER() OVER (
            PARTITION BY lhb.code
            ORDER BY lhb.trade_date DESC, lhb.updated_at DESC
          ) AS rn
        FROM stock_lhb_seat_evidence lhb
        WHERE lhb.trade_date <= {day}
          AND lhb.trade_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 7 DAY)
      ) latest
      WHERE latest.rn=1
    ),
    root_evidence AS (
      SELECT code, updated_at AS latest_updated_at, items
      FROM stock_root_evidence_cache
      WHERE trade_date={day}
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
          IF(COALESCE(aes.impact_summary_text, '') <> '', CONCAT('【异步证据】影响要素：', aes.impact_summary_text, '\n'), ''),
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
              'label', '关键事实',
              'type', 'facts',
              'source', '事实卡',
              {scan_async_source}
              'body', CONCAT_WS('\n', aes.key_facts->>'$[0]', aes.key_facts->>'$[1]', aes.key_facts->>'$[2]'),
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
                '；硬', ROUND(smj.hard_catalyst_score, 0),
                ' 区间', ROUND(smj.anchor_leadership_score, 0),
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
                IF(COALESCE({leadership_body}, '') <> '', CONCAT('区间领头：', {leadership_inline}), NULL),
                IF(COALESCE(smj.support_items->>'$[1]', '') LIKE '区间领头：%', smj.support_items->>'$[2]', smj.support_items->>'$[1]')
              ),
              'priority', 3
            )), JSON_ARRAY()),
          IF(COALESCE({leadership_body}, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '区间领头',
              'type', 'period',
              'source', '问财区间排名',
              {scan_period_source}
              'body', {leadership_body},
              'evidence_date', pr.data_date,
              'priority', 3
            )), JSON_ARRAY()),
          IF(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons')) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '主动性',
              'type', 'initiative',
              'source', CONCAT('扫描触发 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_label')), '')),
              {scan_judgement_source}
              'body', CONCAT_WS('\n',
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[0]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[1]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[2]'))
              ),
              'priority', 3,
              'payload', JSON_EXTRACT(smj.score_detail, '$.initiative_reasons')
            )), JSON_ARRAY()),
          IF(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.influence_reasons')) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '带动性',
              'type', 'influence',
              'source', CONCAT('同锚扩散 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_label')), '')),
              {scan_judgement_source}
              'body', CONCAT_WS('\n',
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[0]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[1]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[2]'))
              ),
              'priority', 4,
              'payload', JSON_EXTRACT(smj.score_detail, '$.influence_reasons'),
              'structured_payload', JSON_EXTRACT(smj.score_detail, '$.influence_payload')
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
          IF(COALESCE(aes.impact_summary_text, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '影响要素',
              'type', 'impact',
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
        DATE_FORMAT(pr.updated_at, '%Y-%m-%d %H:%i:%s') AS period_rank_updated_at,
        DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s') AS lhb_updated_at,
        CAST('' AS CHAR) AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(sr.scanned_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(pr.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(lhb.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(ars.captured_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        100 - sm.rank_speed AS score,
        COALESCE(sm.pct_change, 0) AS change_pct,
        COALESCE(sm.speed, 0) AS speed_pct,
        ROUND(COALESCE(sm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_ARRAY(
          CONCAT('扫描Top', sm.rank_speed),
          CONCAT('题材定位:', CASE
            WHEN arm.role_label IN ('全池领涨', '全池领涨中军', '全池中军') THEN arm.role_label
            WHEN arm.id IS NOT NULL THEN '题材成员'
            WHEN ssr.role_label IN ('领涨', '领涨中军', '中军') THEN CONCAT('局部', ssr.role_label)
            WHEN ars.id IS NOT NULL THEN '扫描异动'
            ELSE ssr.role_label
          END),
          CONCAT('锚点:', COALESCE(ars.anchor_name, ssr.primary_anchor_name)),
          IF(COALESCE(ssr.raw_json->>'$.raw_json.anchor_source', ssr.raw_json->>'$.anchor_source', '') IN ('active_market_anchor', 'theme_reason_bank'), '题材锚点', ''),
          CONCAT('同锚:', COALESCE(ars.member_count, ssr.anchor_member_count), '只'),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), ''),
          CASE
            WHEN ars.id IS NOT NULL AND COALESCE(ars.leader_name, '') <> '' AND ars.leader_name <> sm.name THEN CONCAT('全池领涨:', ars.leader_name, IF(COALESCE(ars.leader_code, '') <> '', CONCAT('|', ars.leader_code), ''))
            WHEN ars.id IS NULL AND COALESCE(ssr.leader_name, '') <> '' AND ssr.leader_name <> sm.name THEN CONCAT('局部领涨:', ssr.leader_name, IF(COALESCE(ssr.leader_code, '') <> '', CONCAT('|', ssr.leader_code), ''))
            ELSE ''
          END,
          CASE
            WHEN ars.id IS NOT NULL AND COALESCE(ars.core_name, '') <> '' AND ars.core_name <> sm.name THEN CONCAT('全池中军:', ars.core_name, IF(COALESCE(ars.core_code, '') <> '', CONCAT('|', ars.core_code), ''))
            WHEN ars.id IS NULL AND COALESCE(ssr.core_name, '') <> '' AND ssr.core_name <> sm.name THEN CONCAT('局部中军:', ssr.core_name, IF(COALESCE(ssr.core_code, '') <> '', CONCAT('|', ssr.core_code), ''))
            ELSE ''
          END
        ) AS tags,
        ROW_NUMBER() OVER (
          PARTITION BY sr.id
          ORDER BY sm.rank_speed ASC
        ) AS rn
      FROM recent_scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id = sr.id
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
      LEFT JOIN period_ranks pr ON pr.code = sm.code
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = sm.code
      LEFT JOIN latest_lhb lhb ON lhb.code = sm.code
      LEFT JOIN root_evidence re ON re.code = sm.code
      LEFT JOIN latest_anchor_role_snapshot ars ON ars.anchor_name = ssr.primary_anchor_name
      LEFT JOIN latest_anchor_role_member arm
        ON arm.snapshot_run_id = ars.snapshot_run_id
       AND arm.anchor_name = ars.anchor_name
       AND arm.code = sm.code
      WHERE sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
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
          IF(COALESCE(aes.impact_summary_text, '') <> '', CONCAT('【异步证据】影响要素：', aes.impact_summary_text, '\n'), ''),
          IF(JSON_LENGTH(COALESCE(re.items, JSON_ARRAY())) > 0, CONCAT('【异步证据】基础事实：', re.items->>'$[0].body', '\n'), ''),
          IF(COALESCE(el.hard_evidence_summary, '') <> '', CONCAT('【异步证据】个股证据：', LEFT(el.hard_evidence_summary, 220), '\n'), ''),
          IF(COALESCE(el.market_evidence, '') <> '', CONCAT('【异步证据】题材证据：', LEFT(el.market_evidence, 220), '\n'), '')
        ) AS detail,
        JSON_MERGE_PRESERVE(
          IF(JSON_VALID(aes.key_facts) AND JSON_LENGTH(aes.key_facts) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '关键事实',
              'type', 'facts',
              'source', '事实卡',
              {window_async_source}
              'body', CONCAT_WS('\n', aes.key_facts->>'$[0]', aes.key_facts->>'$[1]', aes.key_facts->>'$[2]'),
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
                '；硬', ROUND(smj.hard_catalyst_score, 0),
                ' 区间', ROUND(smj.anchor_leadership_score, 0),
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
                IF(COALESCE({leadership_body}, '') <> '', CONCAT('区间领头：', {leadership_inline}), NULL),
                IF(COALESCE(smj.support_items->>'$[1]', '') LIKE '区间领头：%', smj.support_items->>'$[2]', smj.support_items->>'$[1]')
              ),
              'priority', 3
            )), JSON_ARRAY()),
          IF(COALESCE({leadership_body}, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '区间领头',
              'type', 'period',
              'source', '问财区间排名',
              {window_period_source}
              'body', {leadership_body},
              'evidence_date', pr.data_date,
              'priority', 3
            )), JSON_ARRAY()),
          IF(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons')) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '主动性',
              'type', 'initiative',
              'source', CONCAT('扫描触发 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_label')), '')),
              {window_judgement_source}
              'body', CONCAT_WS('\n',
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[0]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[1]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.initiative_reasons[2]'))
              ),
              'priority', 3,
              'payload', JSON_EXTRACT(smj.score_detail, '$.initiative_reasons')
            )), JSON_ARRAY()),
          IF(JSON_LENGTH(JSON_EXTRACT(smj.score_detail, '$.influence_reasons')) > 0, JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '带动性',
              'type', 'influence',
              'source', CONCAT('同锚扩散 / ', COALESCE(JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_label')), '')),
              {window_judgement_source}
              'body', CONCAT_WS('\n',
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[0]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[1]')),
                JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.influence_reasons[2]'))
              ),
              'priority', 4,
              'payload', JSON_EXTRACT(smj.score_detail, '$.influence_reasons'),
              'structured_payload', JSON_EXTRACT(smj.score_detail, '$.influence_payload')
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
          IF(COALESCE(aes.impact_summary_text, '') <> '', JSON_ARRAY(JSON_OBJECT(
              'layer', 'async',
              'label', '影响要素',
              'type', 'impact',
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
        DATE_FORMAT(pr.updated_at, '%Y-%m-%d %H:%i:%s') AS period_rank_updated_at,
        DATE_FORMAT(lhb.updated_at, '%Y-%m-%d %H:%i:%s') AS lhb_updated_at,
        DATE_FORMAT(el.updated_at, '%Y-%m-%d %H:%i:%s') AS evidence_layer_updated_at,
        DATE_FORMAT(GREATEST(
          COALESCE(rw.ended_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(smj.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(aes.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(re.latest_updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(pr.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(lhb.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(el.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3))),
          COALESCE(wars.captured_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        ROUND(wm.window_score, 1) AS score,
        COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS change_pct,
        COALESCE(wm.max_speed, 0) AS speed_pct,
        ROUND(COALESCE(wm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_ARRAY(
          CONCAT('Top', wm.rank_no),
          CASE WHEN warm.id IS NOT NULL THEN warm.role_label ELSE COALESCE(wsr.role_label, ssr.role_label, '') END,
          COALESCE(CONCAT('锚点:', COALESCE(wars.anchor_name, wsr.sector_key, ssr.primary_anchor_name)), ''),
           IF(COALESCE(wsr.raw_json->>'$.raw_json.anchor_source', wsr.raw_json->>'$.anchor_source', ssr.raw_json->>'$.raw_json.anchor_source', ssr.raw_json->>'$.anchor_source', '') IN ('active_market_anchor', 'theme_reason_bank'), '题材锚点', ''),
          COALESCE(CONCAT('同锚:', COALESCE(wars.member_count, wsr.sector_stock_count, ssr.anchor_member_count), '只'), ''),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), ''),
          COALESCE(IF(
            COALESCE(wars.leader_name, wss.leader_name, ssr.leader_name, '') <> ''
            AND COALESCE(wars.leader_name, wss.leader_name, ssr.leader_name, '') <> wm.name,
            CONCAT(
              '领涨:', COALESCE(wars.leader_name, wss.leader_name, ssr.leader_name),
              IF(COALESCE(wars.leader_code, wss.leader_code, ssr.leader_code, '') <> '', CONCAT('|', COALESCE(wars.leader_code, wss.leader_code, ssr.leader_code)), '')
            ),
            ''
          ), ''),
          COALESCE(IF(
            COALESCE(wars.core_name, wss.core_name, ssr.core_name, '') <> ''
            AND COALESCE(wars.core_name, wss.core_name, ssr.core_name, '') <> wm.name,
            CONCAT(
              '中军:', COALESCE(wars.core_name, wss.core_name, ssr.core_name),
              IF(COALESCE(wars.core_code, wss.core_code, ssr.core_code, '') <> '', CONCAT('|', COALESCE(wars.core_code, wss.core_code, ssr.core_code)), '')
            ),
            ''
          ), ''),
          CONCAT('证据:', COALESCE(el.evidence_strength, 'pending')),
          CONCAT('出现:', wm.appearance_count, '次')
        ) AS tags,
        ROW_NUMBER() OVER (
          PARTITION BY rw.id
          ORDER BY wm.rank_no ASC
        ) AS rn
      FROM recent_windows rw
      JOIN window_movers wm ON wm.window_id = rw.id
      LEFT JOIN stock_company_profiles scp ON scp.code = wm.code
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
      LEFT JOIN period_ranks pr ON pr.code = wm.code
      LEFT JOIN async_evidence_summaries aes ON aes.trade_date={day} AND aes.code = wm.code
      LEFT JOIN latest_lhb lhb ON lhb.code = wm.code
      LEFT JOIN root_evidence re ON re.code = wm.code
      WHERE wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        AND wm.rank_no <= 5
    )
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'event_time', DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s'),
      'kind', kind,
      'kind_label', kind_label,
      'code', code,
      'name', name,
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
      FROM scan_ranked
      WHERE rn <= 5
        {scan_detail_filter}

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM window_ranked
      WHERE rn <= 5
        {window_detail_filter}

      ORDER BY event_time DESC,
        CASE kind WHEN 'scan' THEN 2 WHEN 'window' THEN 1 ELSE 0 END DESC,
        score DESC
    ) feed;
    """


def intel_feed_list_sql(trade_date: str | None = "", window_count: int = 240) -> str:
    day = sql_string(trade_date or date.today().strftime("%Y-%m-%d"))
    window_count = max(1, int(window_count))
    return f"""
    WITH
    recent_scan_runs AS (
      SELECT id, run_id, scanned_at
      FROM scan_runs
      WHERE scanned_at >= CAST({day} AS DATETIME)
        AND scanned_at < DATE_ADD(CAST({day} AS DATE), INTERVAL 1 DAY)
        AND accepted=1
      ORDER BY scanned_at DESC
      LIMIT 1
    ),
    recent_windows AS (
      SELECT id, window_id, ended_at
      FROM windows
      WHERE ended_at >= CAST({day} AS DATETIME)
        AND ended_at < DATE_ADD(CAST({day} AS DATE), INTERVAL 1 DAY)
        AND status='done'
        AND aggregate_count > 0
      ORDER BY ended_at DESC
      LIMIT {window_count}
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
        JSON_ARRAY() AS evidence_items,
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
          COALESCE(rec.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        100 - sm.rank_speed AS score,
        COALESCE(sm.pct_change, 0) AS change_pct,
        COALESCE(sm.speed, 0) AS speed_pct,
        ROUND(COALESCE(sm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_ARRAY(
          CONCAT('扫描Top', sm.rank_speed),
          CONCAT('锚点:', COALESCE(NULLIF(ssr.primary_anchor_name, ''), JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.original_primary_anchor')), '未锚定')),
          IF(COALESCE(ssr.role_label, '') <> '', ssr.role_label, ''),
          CONCAT('同锚:', COALESCE(ssr.anchor_member_count, 0), '只'),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), '')
        ) AS tags,
        ROW_NUMBER() OVER (PARTITION BY sr.id ORDER BY sm.rank_speed ASC) AS rn
      FROM recent_scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id = sr.id
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
      LEFT JOIN stock_root_evidence_cache rec ON rec.trade_date={day} AND rec.code = sm.code
      WHERE sm.name NOT LIKE '%ST%'
        AND sm.name NOT LIKE '%退市%'
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
        JSON_ARRAY() AS evidence_items,
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
          COALESCE(rec.updated_at, CAST('1000-01-01 00:00:00' AS DATETIME(3)))
        ), '%Y-%m-%d %H:%i:%s') AS latest_source_updated_at,
        wm.window_score AS score,
        COALESCE(wm.max_pct_change, wm.latest_pct_change, 0) AS change_pct,
        COALESCE(wm.max_speed, 0) AS speed_pct,
        ROUND(COALESCE(wm.amount, 0) / 100000000, 2) AS amount_yi,
        JSON_ARRAY(
          CONCAT('窗口Top', wm.rank_no),
          CONCAT('锚点:', COALESCE(NULLIF(wsr.sector_key, ''), JSON_UNQUOTE(JSON_EXTRACT(smj.score_detail, '$.original_primary_anchor')), '未锚定')),
          IF(COALESCE(wsr.role_label, '') <> '', wsr.role_label, ''),
          IF(COALESCE(smj.sustainability_label, '') <> '', CONCAT('持续:', smj.sustainability_label, ROUND(smj.sustainability_score, 0), '分'), ''),
          CONCAT('出现:', wm.appearance_count, '次')
        ) AS tags,
        ROW_NUMBER() OVER (PARTITION BY rw.id ORDER BY wm.rank_no ASC) AS rn
      FROM recent_windows rw
      JOIN window_movers wm ON wm.window_id = rw.id
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
      LEFT JOIN stock_root_evidence_cache rec ON rec.trade_date={day} AND rec.code = wm.code
      WHERE wm.name NOT LIKE '%ST%'
        AND wm.name NOT LIKE '%退市%'
        AND wm.rank_no <= 5
    )
    SELECT COALESCE(JSON_ARRAYAGG(JSON_OBJECT(
      'event_time', DATE_FORMAT(event_time, '%Y-%m-%d %H:%i:%s'),
      'kind', kind,
      'kind_label', kind_label,
      'code', code,
      'name', name,
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
      FROM scan_ranked
      WHERE rn <= 5

      UNION ALL

      SELECT event_time, kind, kind_label, code, name, sort_rank, title, summary, detail, evidence_items, display_contract,
        intraday_source_updated_at, judgement_updated_at, async_evidence_updated_at, period_rank_updated_at, lhb_updated_at,
        evidence_layer_updated_at, latest_source_updated_at, score, change_pct, speed_pct, amount_yi, tags
      FROM window_ranked
      WHERE rn <= 5

      ORDER BY event_time DESC,
        CASE kind WHEN 'scan' THEN 2 WHEN 'window' THEN 1 ELSE 0 END DESC,
        score DESC
    ) feed;
    """

