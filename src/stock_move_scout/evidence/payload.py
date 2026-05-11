from __future__ import annotations

import json
import re
from typing import Any

from stock_move_scout.evidence.effective_facts import fetch_effective_fact_items
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
    effective_facts = fetch_effective_fact_items(config, trade_date, code, max(8, int(per_kind_limit) * 4))
    root_items = [
        {
            "kind": str(fact.get("fact_type") or fact.get("evidence_role") or "effective_fact"),
            "date": str(fact.get("fact_date") or ""),
            "title": compact(str(fact.get("title") or ""), 180),
            "content": compact(str(fact.get("body") or ""), 500),
            "url": "",
            "section": str(fact.get("evidence_role") or ""),
            "source_table": str(fact.get("source_table") or ""),
            "source_key": str(fact.get("source_key") or ""),
            "valid_status": str(fact.get("valid_status") or ""),
            "valid_score": fact.get("valid_score") or 0,
            "evidence_group": str(fact.get("evidence_group") or ""),
            "display_level": str(fact.get("display_level") or ""),
        }
        for fact in effective_facts
    ]
    announcement_effects = [
        {
            "event_date": fact.get("fact_date") or "",
            "event_type": fact.get("fact_type") or "",
            "event_subtype": fact.get("fact_subtype") or "",
            "tag": (fact.get("payload") or {}).get("tag", "") if isinstance(fact.get("payload"), dict) else "",
            "status": fact.get("valid_status") or "",
            "verify_score": (fact.get("payload") or {}).get("verify_score", "") if isinstance(fact.get("payload"), dict) else "",
            "verify_pct": (fact.get("payload") or {}).get("verify_pct", "") if isinstance(fact.get("payload"), dict) else "",
            "title": compact(str(fact.get("title") or ""), 220),
            "summary": compact(str(fact.get("body") or ""), 420),
        }
        for fact in effective_facts
        if fact.get("source_table") == "stock_announcement_effects"
    ][: max(1, int(per_kind_limit))]
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
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      seat_signal_label,
      seat_signal_score,
      famous_trader_count,
      ROUND(famous_trader_net_buy / 100000000, 2),
      institution_buy_count,
      institution_sell_count,
      ROUND(institution_net_buy / 100000000, 2),
      top_buy_seat,
      ROUND(top_buy_amount / 100000000, 2),
      key_facts,
      DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s')
    FROM stock_lhb_seat_evidence
    WHERE trade_date <= {sql_string(trade_date)}
      AND trade_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL 7 DAY)
      AND code={sql_string(code)}
    ORDER BY trade_date DESC, updated_at DESC
    LIMIT 1;
    """
    lhb_rows = mysql_rows(run_mysql(config, lhb_sql, batch=True, raw=True))
    lhb_evidence: list[dict[str, Any]] = []
    if lhb_rows and len(lhb_rows[0]) >= 12:
        row = lhb_rows[0]
        try:
            facts = json.loads(row[10] or "[]")
        except Exception:
            facts = []
        lhb_evidence.append(
            {
                "trade_date": row[0],
                "signal_label": row[1],
                "signal_score": row[2],
                "famous_trader_count": row[3],
                "famous_trader_net_buy_yi": row[4],
                "institution_buy_count": row[5],
                "institution_sell_count": row[6],
                "institution_net_buy_yi": row[7],
                "top_buy_seat": row[8],
                "top_buy_amount_yi": row[9],
                "key_facts": facts,
                "updated_at": row[11],
            }
        )
    period_sql = f"""
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      period_days,
      rank_no,
      rank_total,
      period_pct,
      latest_pct,
      DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s')
    FROM stock_period_rankings
    WHERE trade_date <= {sql_string(trade_date)}
      AND trade_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL 7 DAY)
      AND code={sql_string(code)}
      AND period_days IN (3,5,10)
    ORDER BY trade_date DESC, period_days ASC, updated_at DESC;
    """
    seen_periods: set[str] = set()
    period_rankings: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, period_sql, batch=True, raw=True)):
        if len(row) < 7 or row[1] in seen_periods:
            continue
        seen_periods.add(row[1])
        period_rankings.append(
            {
                "trade_date": row[0],
                "period_days": row[1],
                "rank_no": row[2],
                "rank_total": row[3],
                "period_pct": row[4],
                "latest_pct": row[5],
                "updated_at": row[6],
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
        "effective_facts": effective_facts,
        "announcement_effects": announcement_effects,
        "evidence_layers": evidence_layers,
        "theme_reason_bank": reason_bank,
        "lhb_seat_evidence": lhb_evidence,
        "period_rankings": period_rankings,
    }


def fetch_announcement_effects(config: MySqlConfig, trade_date: str, code: str, limit: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      DATE_FORMAT(event_date, '%Y-%m-%d'),
      event_type,
      event_subtype,
      tag,
      effect_status,
      verify_score,
      effect_score,
      verify_pct,
      current_pct_from_base,
      avg_pct_from_base,
      COALESCE(DATE_FORMAT(base_trade_date, '%Y-%m-%d'), ''),
      COALESCE(DATE_FORMAT(verify_trade_date, '%Y-%m-%d'), ''),
      COALESCE(DATE_FORMAT(faded_trade_date, '%Y-%m-%d'), ''),
      title,
      summary
    FROM stock_announcement_effects
    WHERE code={sql_string(code)}
      AND event_date <= {sql_string(trade_date)}
      AND event_date >= DATE_SUB(CAST({sql_string(trade_date)} AS DATE), INTERVAL 240 DAY)
    ORDER BY
      FIELD(effect_status, 'active', 'faded', 'ignored', 'unverified'),
      verify_score DESC,
      effect_score DESC,
      event_date DESC,
      updated_at DESC
    LIMIT {max(1, int(limit)) * 3};
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 15:
            continue
        values = ["" if str(value or "") == "NULL" else value for value in row]
        out.append(
            {
                "event_date": values[0],
                "event_type": values[1],
                "event_subtype": values[2],
                "tag": values[3],
                "status": values[4],
                "verify_score": values[5],
                "effect_score": values[6],
                "verify_pct": values[7],
                "current_pct_from_base": values[8],
                "avg_pct_from_base": values[9],
                "base_trade_date": values[10],
                "verify_trade_date": values[11],
                "faded_trade_date": values[12],
                "title": compact(values[13], 220),
                "summary": compact(values[14], 420),
            }
        )
    return out


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
