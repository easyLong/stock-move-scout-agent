from __future__ import annotations

import json
import re
from typing import Any

from stock_scout_mysql import MySqlConfig, mysql_rows, run_mysql, sql_string


def compact(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def fetch_candidates(config: MySqlConfig, trade_date: str, code: str = "", limit: int = 50) -> list[dict[str, str]]:
    window_code_filter = f"AND wm.code={sql_string(code)}" if code else ""
    scan_code_filter = f"AND sm.code={sql_string(code)}" if code else ""
    judgement_code_filter = f"AND code={sql_string(code)}" if code else ""
    sql = f"""
    WITH candidate_events AS (
      SELECT
        wm.code,
        wm.name,
        w.ended_at AS event_at,
        COALESCE(wm.window_score, 0) AS score
      FROM windows w
      JOIN window_movers wm ON wm.window_id=w.id
      WHERE DATE(w.ended_at)={sql_string(trade_date)}
        AND w.status='done'
        AND wm.rank_no <= 5
        {window_code_filter}
      UNION
      SELECT
        sm.code,
        sm.name,
        sr.scanned_at AS event_at,
        100 - COALESCE(sm.rank_speed, 100) AS score
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
        AND sr.accepted=1
        AND sm.rank_speed <= 5
        {scan_code_filter}
      UNION
      SELECT
        sm.code,
        sm.name,
        sr.scanned_at AS event_at,
        70
          + LEAST(COALESCE(sm.speed, 0) * 5, 15)
          + IF(COALESCE(sm.amount_delta_15s, 0) >= 30000000, 8, 0)
          - COALESCE(sm.rank_speed, 20) AS score
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
        AND sr.accepted=1
        AND sm.rank_speed <= 20
        AND (
          COALESCE(sm.speed, 0) >= 1
          OR (COALESCE(sm.speed, 0) > 0.5 AND COALESCE(sm.amount_delta_15s, 0) >= 30000000)
        )
        {scan_code_filter}
      UNION
      SELECT
        code,
        stock_name AS name,
        event_time AS event_at,
        COALESCE(sustainability_score, 0)
          + IF(JSON_UNQUOTE(JSON_EXTRACT(score_detail, '$.initiative_label'))='强', 12, 0)
          + IF(JSON_UNQUOTE(JSON_EXTRACT(score_detail, '$.influence_label')) IN ('疑似带动强','疑似带动中'), 12, 0) AS score
      FROM stock_move_judgements
      WHERE trade_date={sql_string(trade_date)}
        AND (
          COALESCE(sustainability_score, 0) >= 45
          OR JSON_UNQUOTE(JSON_EXTRACT(score_detail, '$.initiative_label')) IN ('强','中')
          OR JSON_UNQUOTE(JSON_EXTRACT(score_detail, '$.influence_label')) IN ('疑似带动强','疑似带动中')
        )
        {judgement_code_filter}
    )
    SELECT code, SUBSTRING_INDEX(GROUP_CONCAT(name ORDER BY event_at DESC), ',', 1) AS name
    FROM candidate_events c
    WHERE c.name NOT LIKE '%ST%'
      AND c.name NOT LIKE '%退市%'
    GROUP BY code
    ORDER BY MAX(event_at) DESC, MAX(score) DESC, code ASC
    LIMIT {int(limit)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    return [{"code": row[0], "stock_name": row[1]} for row in rows if len(row) >= 2]


def fetch_payload(config: MySqlConfig, trade_date: str, code: str, stock_name: str, per_kind_limit: int) -> dict[str, Any]:
    current_anchors = fetch_current_anchors(config, trade_date, code)
    root_sql = f"""
    SELECT item_kind, COALESCE(DATE_FORMAT(item_date, '%Y-%m-%d'), ''), title, COALESCE(content, ''), url, source_section
    FROM stock_ths_root_items
    WHERE code={sql_string(code)}
      AND item_kind IN ('important_event','announcement','theme_point','hot_news')
    ORDER BY
      FIELD(item_kind, 'announcement', 'important_event', 'theme_point', 'hot_news'),
      item_date DESC,
      source_rank ASC
    LIMIT {int(per_kind_limit) * 4};
    """
    root_items = [
        {
            "kind": row[0],
            "date": row[1],
            "title": compact(row[2], 180),
            "content": compact(row[3], 500),
            "url": row[4],
            "section": row[5],
        }
        for row in mysql_rows(run_mysql(config, root_sql, batch=True, raw=True))
        if len(row) >= 6
    ]
    evidence_sql = f"""
    SELECT
      COALESCE(el.hard_evidence_summary, ''),
      COALESCE(el.market_evidence, ''),
      COALESCE(el.sector_evidence, ''),
      COALESCE(el.company_positioning, ''),
      COALESCE(el.evidence_gaps, ''),
      COALESCE(el.next_evidence_action, ''),
      COALESCE(el.evidence_strength, 'pending'),
      DATE_FORMAT(el.updated_at, '%Y-%m-%d %H:%i:%s')
    FROM evidence_layers el
    JOIN windows w ON w.id=el.window_id
    WHERE DATE(w.ended_at)={sql_string(trade_date)}
      AND el.code={sql_string(code)}
    ORDER BY el.updated_at DESC
    LIMIT 3;
    """
    evidence_layers = [
        {
            "hard_evidence_summary": compact(row[0], 500),
            "market_evidence": compact(row[1], 500),
            "sector_evidence": compact(row[2], 300),
            "company_positioning": compact(row[3], 300),
            "evidence_gaps": compact(row[4], 300),
            "next_evidence_action": compact(row[5], 180),
            "evidence_strength": row[6],
            "updated_at": row[7],
        }
        for row in mysql_rows(run_mysql(config, evidence_sql, batch=True, raw=True))
        if len(row) >= 8
    ]
    reason_sql = f"""
    SELECT anchor_name, theme_name, reason_text, source, COALESCE(DATE_FORMAT(source_date, '%Y-%m-%d'), ''), confidence
    FROM stock_theme_reason_bank
    WHERE code={sql_string(code)}
      AND status='active'
    ORDER BY confidence DESC, priority DESC, updated_at DESC
    LIMIT {int(per_kind_limit) * 4};
    """
    reason_bank = [
        {
            "anchor": row[0],
            "theme": row[1],
            "reason": compact(row[2], 500),
            "source": row[3],
            "source_date": row[4],
            "confidence": row[5],
        }
        for row in mysql_rows(run_mysql(config, reason_sql, batch=True, raw=True))
        if len(row) >= 6
    ]
    reason_bank = prioritize_reason_bank(reason_bank, current_anchors, per_kind_limit)
    lhb_sql = f"""
    SELECT
      seat_signal_label,
      seat_signal_score,
      famous_trader_count,
      ROUND(famous_trader_net_buy / 100000000, 2),
      institution_buy_count,
      institution_sell_count,
      ROUND(institution_net_buy / 100000000, 2),
      top_buy_seat,
      ROUND(top_buy_amount / 100000000, 2),
      key_facts
    FROM stock_lhb_seat_evidence
    WHERE trade_date={sql_string(trade_date)}
      AND code={sql_string(code)}
    LIMIT 1;
    """
    lhb_rows = mysql_rows(run_mysql(config, lhb_sql, batch=True, raw=True))
    lhb_evidence: list[dict[str, Any]] = []
    if lhb_rows and len(lhb_rows[0]) >= 10:
        row = lhb_rows[0]
        try:
            facts = json.loads(row[9] or "[]")
        except Exception:
            facts = []
        lhb_evidence.append(
            {
                "signal_label": row[0],
                "signal_score": row[1],
                "famous_trader_count": row[2],
                "famous_trader_net_buy_yi": row[3],
                "institution_buy_count": row[4],
                "institution_sell_count": row[5],
                "institution_net_buy_yi": row[6],
                "top_buy_seat": row[7],
                "top_buy_amount_yi": row[8],
                "key_facts": facts,
            }
        )
    profile_sql = f"""
    SELECT COALESCE(company_highlights, ''), COALESCE(latest_management_business_plan, '')
    FROM stock_company_profiles
    WHERE code={sql_string(code)}
    LIMIT 1;
    """
    profile_rows = mysql_rows(run_mysql(config, profile_sql, batch=True, raw=True))
    profile = {}
    if profile_rows and len(profile_rows[0]) >= 2:
        profile = {
            "company_highlights": compact(profile_rows[0][0], 300),
            "management_plan": compact(profile_rows[0][1], 500),
        }
    return {
        "trade_date": trade_date,
        "code": code,
        "stock_name": stock_name,
        "market_context": {"current_anchors": current_anchors},
        "profile": profile,
        "root_items": root_items,
        "evidence_layers": evidence_layers,
        "theme_reason_bank": reason_bank,
        "lhb_seat_evidence": lhb_evidence,
    }


def fetch_current_anchors(config: MySqlConfig, trade_date: str, code: str) -> list[str]:
    sql = f"""
    WITH anchor_events AS (
      SELECT COALESCE(wsr.sector_key, '') AS anchor_name, w.ended_at AS event_at
      FROM windows w
      JOIN window_movers wm ON wm.window_id=w.id
      LEFT JOIN window_stock_roles wsr ON wsr.window_id=w.id AND wsr.code=wm.code
      WHERE DATE(w.ended_at)={sql_string(trade_date)}
        AND wm.code={sql_string(code)}
        AND wm.rank_no <= 5
      UNION ALL
      SELECT COALESCE(ssr.primary_anchor_name, '') AS anchor_name, sr.scanned_at AS event_at
      FROM scan_runs sr
      JOIN scan_movers sm ON sm.scan_run_id=sr.id
      LEFT JOIN scan_stock_roles ssr ON ssr.scan_run_id=sr.id AND ssr.code=sm.code
      WHERE DATE(sr.scanned_at)={sql_string(trade_date)}
        AND sm.code={sql_string(code)}
        AND sm.rank_speed <= 5
    )
    SELECT anchor_name
    FROM anchor_events
    WHERE anchor_name <> ''
      AND anchor_name <> '未锚定'
    GROUP BY anchor_name
    ORDER BY MAX(event_at) DESC, COUNT(*) DESC
    LIMIT 5;
    """
    return [row[0] for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if row and row[0]]


def prioritize_reason_bank(items: list[dict[str, Any]], current_anchors: list[str], limit: int) -> list[dict[str, Any]]:
    anchors = [str(item or "").strip() for item in current_anchors if str(item or "").strip()]
    anchor_terms = anchor_match_terms(anchors)

    def rank(item: dict[str, Any]) -> tuple[int, float]:
        text = f"{item.get('anchor', '')} {item.get('theme', '')} {item.get('reason', '')}"
        anchor_hit = sum(weight for term, weight in anchor_terms if term and term in text)
        try:
            confidence = float(item.get("confidence") or 0)
        except Exception:
            confidence = 0.0
        return (anchor_hit, confidence)

    ranked = sorted(items, key=rank, reverse=True)
    if not anchors:
        return ranked[:limit]
    best_hit = rank(ranked[0])[0] if ranked else 0
    min_hit = 10 if best_hit >= 10 else 1
    matched = [item for item in ranked if rank(item)[0] >= min_hit]
    rest = [item for item in ranked if rank(item)[0] == 0]
    return (matched + rest[: max(0, 2 - len(matched))])[:limit]


def anchor_match_terms(anchors: list[str]) -> list[tuple[str, int]]:
    terms: list[tuple[str, int]] = []
    for anchor in anchors:
        for part in re.split(r"[/、,，\s]+", anchor):
            value = part.strip()
            if value and len(value) >= 2:
                terms.append((value, 10 if re.fullmatch(r"[A-Za-z0-9]+", value) else 4))
        for match in re.findall(r"[A-Za-z0-9]{2,}", anchor):
            terms.append((match, 10))
        if anchor:
            terms.append((anchor, 20))
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    for term, weight in sorted(terms, key=lambda item: item[1], reverse=True):
        if term not in seen:
            seen.add(term)
            out.append((term, weight))
    return out
