#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from datetime import date, datetime, time as clock_time
from typing import Any

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)
from tdx_mover_watcher import DEFAULT_SERVERS, connect, fetch_quotes, to_float


LEVEL_PRIORITY = {"strong": 3, "medium": 2, "weak": 1, "fallback": 0}


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


def load_anchor_candidates(config: MySqlConfig, levels: set[str]) -> list[dict[str, Any]]:
    level_sql = ",".join(sql_string(level) for level in sorted(levels))
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
                label = "全池领涨中军"
            elif is_leader:
                label = "全池领涨"
            elif is_core:
                label = "全池中军"
            else:
                label = "锚点成员"
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
                "anchor_type": clean_text(scored[0].get("anchor_type")) or "hot_concept",
                "source": clean_text(scored[0].get("source")) or "ths_hot_concept",
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
                    "algorithm": "active_anchor_pool_roles_v1",
                    "pool_rule": "active_anchor_match_candidates strong + capped medium",
                    "leader_rule": "pct_change + speed + amount + confidence",
                    "core_rule": "amount + positive pct_change + confidence",
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
        return DEFAULT_SERVERS
    servers: list[tuple[str, int]] = []
    for item in value.split(","):
        if ":" not in item:
            continue
        host, port = item.rsplit(":", 1)
        servers.append((host.strip(), int(port)))
    return servers or DEFAULT_SERVERS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build realtime leader/core roles inside active theme anchor stock pools.")
    parser.add_argument("--levels", default="strong,medium", help="Candidate match levels used as anchor pool.")
    parser.add_argument("--medium-cap", type=int, default=120)
    parser.add_argument("--min-members", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--tdx-timeout", type=int, default=3)
    parser.add_argument("--servers", default="")
    parser.add_argument("--trading-only", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    started = time.monotonic()
    if args.trading_only and not is_trading_time():
        print(json.dumps({"ok": True, "skipped": "outside_trading_time"}, ensure_ascii=False))
        return 0
    levels = {item.strip() for item in str(args.levels).split(",") if item.strip()}
    candidates = load_anchor_candidates(config, levels)
    pools = group_anchor_pool(candidates, int(args.medium_cap))
    universe = quote_universe(pools)
    api, server = connect(parse_servers(args.servers), int(args.tdx_timeout))
    try:
        quotes = fetch_quotes(api, universe, int(args.batch_size))
    finally:
        try:
            api.disconnect()
        except Exception:
            pass
    snapshots, members = build_roles(pools, quotes, int(args.min_members))
    insert_results(config, snapshots, members)
    payload = {
        "ok": True,
        "server": server,
        "levels": sorted(levels),
        "anchor_pools": len(pools),
        "quote_universe": len(universe),
        "quotes": len(quotes),
        "snapshots": len(snapshots),
        "members": len(members),
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
