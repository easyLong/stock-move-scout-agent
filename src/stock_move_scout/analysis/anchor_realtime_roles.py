from __future__ import annotations

import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any, Iterable

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_number, sql_string
from stock_move_scout.research_pool import RESEARCH_POOL_THEME_SOURCE, materialize_research_pool_theme_members
from stock_move_scout.sources.quotes import DEFAULT_TDX_SERVERS, QuoteProviderConfig, QuoteSymbol, TdxQuoteProvider, finite_float as to_float


LEVEL_PRIORITY = {"strong": 3, "medium": 2, "weak": 1, "fallback": 0}
RESEARCH_POOL_HEADLINE_THEME_TYPE = "research_pool_headline_theme"
RESEARCH_POOL_CONCEPT_TYPE = "research_pool_concept"
HEADLINE_THEME_SOURCE = "ths_homepage_headline"
HEADLINE_THEME_TYPE = "ths_headline_theme"
NOISE_THEME_NAMES = (
    "融资融券",
    "转融券标的",
    "深股通",
    "沪股通",
    "富时罗素",
    "MSCI中国",
    "标普道琼斯A股",
    "证金持股",
    "养老金持股",
    "社保重仓",
    "注册制次新股",
    "新股与次新股",
    "科创次新股",
    "ST板块",
    "低价股",
    "高价股",
)


@dataclass(frozen=True)
class AnchorRealtimeRoleConfig:
    levels: tuple[str, ...] = ("strong", "medium")
    medium_cap: int = 120
    min_members: int = 2
    batch_size: int = 80
    tdx_timeout: int = 3
    servers: tuple[tuple[str, int], ...] = DEFAULT_TDX_SERVERS
    universe_csv: Path = Path("data/stock/tdx_a_stock_universe.csv")
    trading_only: bool = False
    research_pool_only: bool = False
    trade_date: str = ""


@dataclass(frozen=True)
class AnchorRealtimeRoleResult:
    payload: dict[str, Any]
    snapshots: list[dict[str, Any]]
    members: list[dict[str, Any]]


def code_market(code: str) -> int | None:
    code = str(code or "").strip()
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return 0
    if code.startswith(("600", "601", "603", "605", "688", "689")):
        return 1
    return None


def clean_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def finite(value: Any) -> float:
    try:
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return 0.0
        return parsed
    except Exception:
        return 0.0


def run_id() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S%f")[:20]


def is_trading_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    current = now.time()
    return clock_time(9, 30) <= current < clock_time(11, 30) or clock_time(13, 0) <= current < clock_time(15, 0)


def load_anchor_candidates(config: MySqlConfig, levels: set[str], codes: list[str] | None = None) -> list[dict[str, Any]]:
    level_sql = ",".join(sql_string(level) for level in sorted(levels))
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    code_filter = f"AND c.code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ("AND 1=0" if codes is not None else "")
    sql = f"""
    SELECT
      c.anchor_name,
      c.anchor_type,
      c.source,
      c.code,
      COALESCE(NULLIF(c.stock_name, ''), s.name, '') AS stock_name,
      c.match_level,
      c.match_source,
      c.matched_term,
      c.evidence_text,
      c.confidence,
      c.status,
      DATE_FORMAT(COALESCE(m.last_seen_date, a.last_seen_date), '%Y-%m-%d') AS last_seen_date
    FROM active_anchor_match_candidates c
    LEFT JOIN active_market_anchor_members m
      ON m.source=c.source AND m.anchor_name=c.anchor_name AND m.code=c.code
    LEFT JOIN active_market_anchors a
      ON a.source=c.source AND a.anchor_name=c.anchor_name
    LEFT JOIN stocks s ON s.code = c.code
    WHERE c.status IN ('active','watch','cooling')
      AND c.match_level IN ({level_sql})
      AND c.match_source <> 'stock_concept_relation'
      AND c.match_source NOT IN ('stock_industry_relation', 'stock_sub_industry_relation')
      AND COALESCE(s.is_st, 0) = 0
      AND COALESCE(NULLIF(c.stock_name, ''), s.name, '') NOT LIKE '%ST%'
      {code_filter}
      AND COALESCE(NULLIF(c.stock_name, ''), s.name, '') NOT LIKE '%退市%'
    ORDER BY
      c.anchor_name,
      c.code,
      CASE c.match_level WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 WHEN 'weak' THEN 1 ELSE 0 END DESC,
      CASE WHEN COALESCE(m.last_seen_date, a.last_seen_date)=CURDATE() THEN 1 ELSE 0 END DESC,
      c.confidence DESC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    keys = [
        "anchor_name",
        "anchor_type",
        "source",
        "code",
        "stock_name",
        "match_level",
        "match_source",
        "matched_term",
        "evidence_text",
        "confidence",
        "status",
        "last_seen_date",
    ]
    return [dict(zip(keys, row)) for row in rows if len(row) >= len(keys)]


def load_headline_theme_candidates(config: MySqlConfig, trade_date: str, codes: list[str] | None = None) -> list[dict[str, Any]]:
    return load_research_pool_theme_candidates(config, trade_date, codes)
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    code_filter = f"AND m.stock_code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ("AND 1=0" if codes is not None else "")
    day = trade_date or date.today().isoformat()
    sql = f"""
    WITH latest_snapshot AS (
      SELECT snapshot_id, trade_date
      FROM ths_homepage_headline_themes
      WHERE source={sql_string(HEADLINE_THEME_SOURCE)}
        AND trade_date <= {sql_string(day)}
      ORDER BY trade_date DESC, collected_at DESC
      LIMIT 1
    ),
    ranked_members AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (
          PARTITION BY m.theme_name, m.stock_code
          ORDER BY m.stock_rank ASC, m.updated_at DESC
        ) AS rn
      FROM ths_homepage_headline_theme_members m
      JOIN latest_snapshot s ON s.snapshot_id=m.snapshot_id
      WHERE m.source={sql_string(HEADLINE_THEME_SOURCE)}
        AND COALESCE(m.theme_name, '') <> ''
        AND COALESCE(m.stock_code, '') <> ''
        {code_filter}
    )
    SELECT
      m.theme_name AS anchor_name,
      {sql_string(HEADLINE_THEME_TYPE)} AS anchor_type,
      {sql_string(HEADLINE_THEME_SOURCE)} AS source,
      m.stock_code AS code,
      COALESCE(NULLIF(m.stock_name, ''), s.name, '') AS stock_name,
      'strong' AS match_level,
      'ths_homepage_headline_theme_member' AS match_source,
      m.theme_name AS matched_term,
      CONCAT('同花顺首页头条题材：', m.theme_name, IF(COALESCE(m.block_name, '') <> '', CONCAT(' / ', m.block_name), '')) AS evidence_text,
      GREATEST(60, 100 - LEAST(COALESCE(m.theme_rank, 99), 50) - LEAST(COALESCE(m.stock_rank, 99), 80) * 0.2) AS confidence,
      'active' AS status,
      DATE_FORMAT(m.trade_date, '%Y-%m-%d') AS last_seen_date,
      m.theme_rank,
      m.stock_rank,
      m.index_code,
      m.block_name,
      COALESCE(m.gain, 0) AS theme_gain
    FROM ranked_members m
    LEFT JOIN stocks s ON s.code = m.stock_code
    WHERE m.rn=1
      AND COALESCE(s.is_st, 0) = 0
      AND COALESCE(NULLIF(m.stock_name, ''), s.name, '') NOT LIKE '%ST%'
      AND COALESCE(NULLIF(m.stock_name, ''), s.name, '') NOT LIKE '%退市%'
    ORDER BY
      m.theme_rank ASC,
      m.theme_name,
      m.stock_rank ASC,
      confidence DESC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    keys = [
        "anchor_name",
        "anchor_type",
        "source",
        "code",
        "stock_name",
        "match_level",
        "match_source",
        "matched_term",
        "evidence_text",
        "confidence",
        "status",
        "last_seen_date",
        "theme_rank",
        "stock_rank",
        "index_code",
        "block_name",
        "theme_gain",
    ]
    return [dict(zip(keys, row)) for row in rows if len(row) >= len(keys)]


def load_research_pool_theme_candidates(config: MySqlConfig, trade_date: str, codes: list[str] | None = None) -> list[dict[str, Any]]:
    clean_codes = sorted({str(code or "").strip() for code in (codes or []) if str(code or "").strip()})
    code_filter = f"AND m.code IN ({','.join(sql_string(code) for code in clean_codes)})" if clean_codes else ("AND 1=0" if codes is not None else "")
    noise_sql = ",".join(sql_string(name) for name in NOISE_THEME_NAMES)
    day = trade_date or date.today().isoformat()
    sql = f"""
    WITH ranked_members AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (
          PARTITION BY m.theme_name, m.code
          ORDER BY m.is_headline_theme DESC, m.match_score DESC, m.fit_rank ASC, m.updated_at DESC
        ) AS rn
      FROM research_pool_theme_members m
      WHERE m.trade_date={sql_string(day)}
        AND COALESCE(m.theme_name, '') <> ''
        AND COALESCE(m.code, '') <> ''
        AND (m.is_headline_theme=1 OR m.theme_name NOT IN ({noise_sql}))
        {code_filter}
    )
    SELECT
      m.theme_name AS anchor_name,
      IF(m.is_headline_theme=1, {sql_string(RESEARCH_POOL_HEADLINE_THEME_TYPE)}, {sql_string(RESEARCH_POOL_CONCEPT_TYPE)}) AS anchor_type,
      {sql_string(RESEARCH_POOL_THEME_SOURCE)} AS source,
      m.code AS code,
      COALESCE(NULLIF(m.stock_name, ''), s.name, '') AS stock_name,
      IF(m.is_headline_theme=1, 'strong', 'medium') AS match_level,
      m.match_type AS match_source,
      m.concept_name AS matched_term,
      m.reason_explain AS evidence_text,
      GREATEST(45, m.match_score - LEAST(COALESCE(m.pool_rank, 99), 99) * 0.08 - LEAST(COALESCE(m.fit_rank, 99), 99) * 0.12) AS confidence,
      'active' AS status,
      DATE_FORMAT(m.trade_date, '%Y-%m-%d') AS last_seen_date,
      m.theme_rank,
      m.pool_rank AS stock_rank,
      '' AS index_code,
      m.concept_name AS block_name,
      0 AS theme_gain,
      m.is_headline_theme,
      m.pool_source_kind
    FROM ranked_members m
    LEFT JOIN stocks s ON s.code = m.code
    WHERE m.rn=1
      AND COALESCE(s.is_st, 0) = 0
      AND COALESCE(NULLIF(m.stock_name, ''), s.name, '') NOT LIKE '%ST%'
      AND COALESCE(NULLIF(m.stock_name, ''), s.name, '') NOT LIKE '%退市%'
    ORDER BY
      m.is_headline_theme DESC,
      m.theme_rank ASC,
      m.theme_name,
      m.pool_rank ASC,
      confidence DESC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    keys = [
        "anchor_name",
        "anchor_type",
        "source",
        "code",
        "stock_name",
        "match_level",
        "match_source",
        "matched_term",
        "evidence_text",
        "confidence",
        "status",
        "last_seen_date",
        "theme_rank",
        "stock_rank",
        "index_code",
        "block_name",
        "theme_gain",
        "is_headline_theme",
        "pool_source_kind",
    ]
    return [dict(zip(keys, row)) for row in rows if len(row) >= len(keys)]


def best_candidate(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if not current:
        return candidate
    today = date.today().strftime("%Y-%m-%d")
    current_key = (
        LEVEL_PRIORITY.get(clean_text(current.get("match_level")), 0),
        1 if clean_text(current.get("last_seen_date"))[:10] == today else 0,
        finite(current.get("confidence")),
    )
    candidate_key = (
        LEVEL_PRIORITY.get(clean_text(candidate.get("match_level")), 0),
        1 if clean_text(candidate.get("last_seen_date"))[:10] == today else 0,
        finite(candidate.get("confidence")),
    )
    return candidate if candidate_key > current_key else current


def group_anchor_pool(candidates: list[dict[str, Any]], medium_cap: int) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in candidates:
        anchor = clean_text(row.get("anchor_name"))
        code = clean_text(row.get("code"))
        if not anchor or not code or code_market(code) is None:
            continue
        grouped[anchor][code] = best_candidate(grouped[anchor].get(code), row)

    result: dict[str, list[dict[str, Any]]] = {}
    for anchor, by_code in grouped.items():
        strong = [row for row in by_code.values() if clean_text(row.get("match_level")) == "strong"]
        medium = [row for row in by_code.values() if clean_text(row.get("match_level")) == "medium"]
        weak = [row for row in by_code.values() if clean_text(row.get("match_level")) == "weak"]
        medium.sort(key=lambda row: finite(row.get("confidence")), reverse=True)
        weak.sort(key=lambda row: finite(row.get("confidence")), reverse=True)
        selected = strong + medium[: max(0, medium_cap)]
        if not selected and weak:
            selected = weak[: min(len(weak), max(20, medium_cap // 2))]
        if selected:
            result[anchor] = selected
    return result


def quote_universe(pools: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    universe: list[dict[str, Any]] = []
    for members in pools.values():
        for row in members:
            code = clean_text(row.get("code"))
            market = code_market(code)
            if market is None or code in seen:
                continue
            seen.add(code)
            universe.append({"market": market, "code": code, "name": clean_text(row.get("stock_name"))})
    return universe


def quote_symbols(rows: list[dict[str, Any]]) -> list[QuoteSymbol]:
    return [
        QuoteSymbol(
            int(row.get("market") or 0),
            clean_text(row.get("code")),
            clean_text(row.get("name")),
        )
        for row in rows
        if clean_text(row.get("code"))
    ]


def quote_by_code(quotes: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in quotes.values():
        code = clean_text(item.get("code"))
        if code:
            result[code] = item
    return result


def score_member(member: dict[str, Any], quote: dict[str, Any] | None) -> dict[str, Any] | None:
    if not quote:
        return None
    price = to_float(quote.get("price"))
    last_close = to_float(quote.get("last_close"))
    if price <= 0 or last_close <= 0:
        return None
    pct_change = (price / last_close - 1) * 100
    speed = to_float(quote.get("speed"))
    amount = to_float(quote.get("amount"))
    volume = int(to_float(quote.get("vol")))
    confidence = finite(member.get("confidence"))
    amount_yi = amount / 100000000
    level = clean_text(member.get("match_level"))
    level_bonus = {"strong": 10.0, "medium": 4.0, "weak": 1.0}.get(level, 0.0)
    limit_bonus = 30.0 if pct_change >= 9.8 else 0.0

    leader_score = pct_change * 42 + max(speed, 0) * 26 + min(amount_yi, 80) * 2 + confidence * 0.18 + level_bonus + limit_bonus
    core_score = min(amount_yi, 120) * 48 + max(pct_change, 0) * 18 + confidence * 0.16 + level_bonus

    row = dict(member)
    row.update(
        {
            "price": round(price, 4),
            "pct_change": round(pct_change, 4),
            "speed": round(speed, 4),
            "amount": round(amount, 2),
            "volume": volume,
            "leader_score": round(leader_score, 4),
            "core_score": round(core_score, 4),
        }
    )
    return row


def build_roles(
    pools: dict[str, list[dict[str, Any]]],
    quotes: dict[str, dict[str, Any]],
    min_members: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    snapshot_run_id = run_id()
    by_code = quote_by_code(quotes)
    snapshots: list[dict[str, Any]] = []
    members_out: list[dict[str, Any]] = []

    for anchor, members in pools.items():
        scored = [score_member(member, by_code.get(clean_text(member.get("code")))) for member in members]
        scored = [row for row in scored if row]
        if len(scored) < min_members:
            continue

        scored.sort(key=lambda row: (finite(row.get("leader_score")), finite(row.get("pct_change")), finite(row.get("amount"))), reverse=True)
        for idx, row in enumerate(scored, start=1):
            row["rank_leader"] = idx
        by_core = sorted(scored, key=lambda row: (finite(row.get("core_score")), finite(row.get("amount")), finite(row.get("pct_change"))), reverse=True)
        for idx, row in enumerate(by_core, start=1):
            row["rank_core"] = idx

        leader = scored[0]
        core = by_core[0]
        if clean_text(core.get("code")) == clean_text(leader.get("code")) and len(by_core) > 1:
            core = by_core[1]

        for row in scored:
            is_leader = clean_text(row.get("code")) == clean_text(leader.get("code"))
            is_core = clean_text(row.get("code")) == clean_text(core.get("code"))
            if is_leader and is_core:
                label = "???????"
            elif is_leader:
                label = "?????"
            elif is_core:
                label = "?????"
            else:
                label = "????"
            row["role_label"] = label
            row["snapshot_run_id"] = snapshot_run_id
            row["captured_at"] = captured_at
            members_out.append(row)

        strong_count = sum(1 for row in scored if clean_text(row.get("match_level")) == "strong")
        medium_count = sum(1 for row in scored if clean_text(row.get("match_level")) == "medium")
        active_count = sum(1 for row in scored if finite(row.get("pct_change")) > 0)
        total_amount = sum(finite(row.get("amount")) for row in scored)
        avg_pct = sum(finite(row.get("pct_change")) for row in scored) / len(scored)
        max_pct = max(finite(row.get("pct_change")) for row in scored)
        snapshots.append(
            {
                "snapshot_run_id": snapshot_run_id,
                "captured_at": captured_at,
                "anchor_name": anchor,
                "anchor_type": clean_text(scored[0].get("anchor_type")) or RESEARCH_POOL_CONCEPT_TYPE,
                "source": clean_text(scored[0].get("source")) or RESEARCH_POOL_THEME_SOURCE,
                "member_count": len(scored),
                "strong_count": strong_count,
                "medium_count": medium_count,
                "leader_code": clean_text(leader.get("code")),
                "leader_name": clean_text(leader.get("stock_name")),
                "leader_score": finite(leader.get("leader_score")),
                "core_code": clean_text(core.get("code")),
                "core_name": clean_text(core.get("stock_name")),
                "core_score": finite(core.get("core_score")),
                "total_amount": total_amount,
                "avg_pct_change": avg_pct,
                "max_pct_change": max_pct,
                "active_member_count": active_count,
                "status": "active" if active_count >= 2 else "watch",
                "raw_json": {
                    "algorithm": "research_pool_theme_roles_v1",
                    "pool_rule": "research_pool_items joined with ths_stock_concept_explanations",
                    "leader_rule": "pct_change + speed + amount + confidence",
                    "core_rule": "amount + positive pct_change + confidence",
                    "theme_rank": clean_text(scored[0].get("theme_rank")),
                    "index_code": clean_text(scored[0].get("index_code")),
                    "block_name": clean_text(scored[0].get("block_name")),
                },
            }
        )

    return snapshots, members_out


def insert_results(config: MySqlConfig, snapshots: list[dict[str, Any]], members: list[dict[str, Any]]) -> None:
    if not snapshots:
        return
    statements: list[str] = []
    for row in snapshots:
        statements.append(
            f"""
            INSERT INTO anchor_realtime_role_snapshots(
              snapshot_run_id, captured_at, anchor_name, anchor_type, source,
              member_count, strong_count, medium_count,
              leader_code, leader_name, leader_score,
              core_code, core_name, core_score,
              total_amount, avg_pct_change, max_pct_change, active_member_count,
              status, raw_json
            )
            VALUES(
              {sql_string(row.get("snapshot_run_id"))}, {sql_string(row.get("captured_at"))},
              {sql_string(row.get("anchor_name"))}, {sql_string(row.get("anchor_type"))}, {sql_string(row.get("source"))},
              {sql_int(row.get("member_count"))}, {sql_int(row.get("strong_count"))}, {sql_int(row.get("medium_count"))},
              {sql_string(row.get("leader_code"))}, {sql_string(row.get("leader_name"))}, {sql_number(row.get("leader_score"))},
              {sql_string(row.get("core_code"))}, {sql_string(row.get("core_name"))}, {sql_number(row.get("core_score"))},
              {sql_number(row.get("total_amount"))}, {sql_number(row.get("avg_pct_change"))}, {sql_number(row.get("max_pct_change"))},
              {sql_int(row.get("active_member_count"))}, {sql_string(row.get("status"))}, {sql_json(row.get("raw_json"))}
            )
            ON DUPLICATE KEY UPDATE
              captured_at=VALUES(captured_at),
              member_count=VALUES(member_count),
              strong_count=VALUES(strong_count),
              medium_count=VALUES(medium_count),
              leader_code=VALUES(leader_code),
              leader_name=VALUES(leader_name),
              leader_score=VALUES(leader_score),
              core_code=VALUES(core_code),
              core_name=VALUES(core_name),
              core_score=VALUES(core_score),
              total_amount=VALUES(total_amount),
              avg_pct_change=VALUES(avg_pct_change),
              max_pct_change=VALUES(max_pct_change),
              active_member_count=VALUES(active_member_count),
              status=VALUES(status),
              raw_json=VALUES(raw_json);
            """
        )
    for row in members:
        raw = {
            "evidence_text": clean_text(row.get("evidence_text")),
            "status": clean_text(row.get("status")),
            "source": clean_text(row.get("source")),
            "theme_rank": clean_text(row.get("theme_rank")),
            "stock_rank": clean_text(row.get("stock_rank")),
            "index_code": clean_text(row.get("index_code")),
            "block_name": clean_text(row.get("block_name")),
        }
        statements.append(
            f"""
            INSERT INTO anchor_realtime_role_members(
              snapshot_run_id, captured_at, anchor_name, anchor_type,
              code, stock_name, match_level, match_source, matched_term, confidence,
              pct_change, speed, amount, volume, price,
              leader_score, core_score, rank_leader, rank_core, role_label, raw_json
            )
            VALUES(
              {sql_string(row.get("snapshot_run_id"))}, {sql_string(row.get("captured_at"))},
              {sql_string(row.get("anchor_name"))}, {sql_string(row.get("anchor_type"))},
              {sql_string(row.get("code"))}, {sql_string(row.get("stock_name"))},
              {sql_string(row.get("match_level"))}, {sql_string(row.get("match_source"))},
              {sql_string(row.get("matched_term"))}, {sql_number(row.get("confidence"))},
              {sql_number(row.get("pct_change"))}, {sql_number(row.get("speed"))},
              {sql_number(row.get("amount"))}, {sql_int(row.get("volume"))}, {sql_number(row.get("price"))},
              {sql_number(row.get("leader_score"))}, {sql_number(row.get("core_score"))},
              {sql_int(row.get("rank_leader"))}, {sql_int(row.get("rank_core"))},
              {sql_string(row.get("role_label"))}, {sql_json(raw)}
            )
            ON DUPLICATE KEY UPDATE
              captured_at=VALUES(captured_at),
              stock_name=VALUES(stock_name),
              match_level=VALUES(match_level),
              match_source=VALUES(match_source),
              matched_term=VALUES(matched_term),
              confidence=VALUES(confidence),
              pct_change=VALUES(pct_change),
              speed=VALUES(speed),
              amount=VALUES(amount),
              volume=VALUES(volume),
              price=VALUES(price),
              leader_score=VALUES(leader_score),
              core_score=VALUES(core_score),
              rank_leader=VALUES(rank_leader),
              rank_core=VALUES(rank_core),
              role_label=VALUES(role_label),
              raw_json=VALUES(raw_json);
            """
        )
    run_mysql(config, "\n".join(statements))


def parse_servers(value: str) -> list[tuple[str, int]]:
    if not value.strip():
        return list(DEFAULT_TDX_SERVERS)
    servers: list[tuple[str, int]] = []
    for item in value.split(","):
        if ":" not in item:
            continue
        host, port = item.rsplit(":", 1)
        servers.append((host.strip(), int(port)))
    return servers or list(DEFAULT_TDX_SERVERS)


def clean_levels(levels: str | Iterable[str]) -> set[str]:
    if isinstance(levels, str):
        return {item.strip() for item in levels.split(",") if item.strip()}
    return {str(item or "").strip() for item in levels if str(item or "").strip()}


class AnchorRealtimeRoleService:
    def __init__(
        self,
        *,
        mysql_config: MySqlConfig,
        config: AnchorRealtimeRoleConfig,
        quote_provider: TdxQuoteProvider | None = None,
    ) -> None:
        self.mysql_config = mysql_config
        self.config = config
        self.quote_provider = quote_provider or TdxQuoteProvider(
            universe_csv=config.universe_csv,
            config=QuoteProviderConfig(
                servers=tuple(config.servers),
                timeout=int(config.tdx_timeout),
                batch_size=int(config.batch_size),
            ),
        )

    def run_once(self) -> AnchorRealtimeRoleResult:
        started = time.monotonic()
        levels = set(self.config.levels)
        if self.config.trading_only and not is_trading_time():
            payload = {"ok": True, "skipped": "outside_trading_time"}
            return AnchorRealtimeRoleResult(payload=payload, snapshots=[], members=[])

        trade_date = self.config.trade_date or date.today().isoformat()
        candidates = load_research_pool_theme_candidates(self.mysql_config, trade_date)
        if not candidates:
            try:
                materialize_research_pool_theme_members(self.mysql_config, trade_date, force=False)
                candidates = load_research_pool_theme_candidates(self.mysql_config, trade_date)
            except Exception:
                candidates = []
        pools = group_anchor_pool(candidates, int(self.config.medium_cap))
        universe = quote_universe(pools)
        if not universe:
            payload = {
                "ok": True,
                "levels": sorted(levels),
                "pool_source": RESEARCH_POOL_THEME_SOURCE,
                "anchor_pools": len(pools),
                "quote_universe": 0,
                "quotes": 0,
                "snapshots": 0,
                "members": 0,
                "duration_ms": int((time.monotonic() - started) * 1000),
            }
            return AnchorRealtimeRoleResult(payload=payload, snapshots=[], members=[])

        api, server = self.quote_provider.connect()
        try:
            quotes = self.quote_provider.fetch_quotes(api, quote_symbols(universe), batch_size=int(self.config.batch_size))
        finally:
            try:
                api.disconnect()
            except Exception:
                pass

        snapshots, members = build_roles(pools, quotes, int(self.config.min_members))
        insert_results(self.mysql_config, snapshots, members)
        payload = {
            "ok": True,
            "server": server,
            "levels": sorted(levels),
            "pool_source": RESEARCH_POOL_THEME_SOURCE,
            "anchor_pools": len(pools),
            "quote_universe": len(universe),
            "quotes": len(quotes),
            "snapshots": len(snapshots),
            "members": len(members),
            "duration_ms": int((time.monotonic() - started) * 1000),
        }
        return AnchorRealtimeRoleResult(payload=payload, snapshots=snapshots, members=members)


__all__ = [
    "AnchorRealtimeRoleConfig",
    "AnchorRealtimeRoleResult",
    "AnchorRealtimeRoleService",
    "best_candidate",
    "build_roles",
    "clean_levels",
    "code_market",
    "group_anchor_pool",
    "insert_results",
    "is_trading_time",
    "load_anchor_candidates",
    "load_headline_theme_candidates",
    "load_research_pool_theme_candidates",
    "parse_servers",
    "quote_symbols",
    "quote_universe",
    "score_member",
]
