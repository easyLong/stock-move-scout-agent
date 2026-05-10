#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections import Counter
from datetime import date, datetime, time as clock_time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_auction_candidate_rows,
    import_auction_minute_analysis_rows,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_string,
)
from tdx_mover_watcher import (
    DEFAULT_SERVERS,
    DEFAULT_TDX_DIR,
    build_universe,
    connect,
    load_concept_map,
    load_industry_map,
    parse_server,
    to_float,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_auction_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return clock_time(9, 20) <= now.time() <= clock_time(9, 30)


def is_auction_minute_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return clock_time(9, 20) <= now.time() <= clock_time(9, 25, 59)


def minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def parse_hhmm(value: str) -> clock_time:
    parsed = datetime.strptime(value, "%H:%M").time()
    return clock_time(parsed.hour, parsed.minute)


def is_st_stock(name: str, concepts: Any = None) -> bool:
    text = f"{name} {' '.join(split_text_list(concepts or []))}".upper()
    return "ST" in text or name.startswith("*ST")


def limit_rate(code: str, name: str, concepts: Any = None) -> float:
    if is_st_stock(name, concepts):
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def round_price(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def limit_price(preclose: float, code: str, name: str, side: str, concepts: Any = None) -> float:
    rate = limit_rate(code, name, concepts)
    factor = 1 + rate if side == "up" else 1 - rate
    return round_price(preclose * factor)


def near_price(left: float, right: float, tolerance: float = 0.0051) -> bool:
    if left <= 0 or right <= 0:
        return False
    return abs(left - right) <= tolerance


def parse_json_text(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def read_themes(config: Any, trade_date: date, limit: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      theme_name,
      COALESCE(CAST(keywords AS CHAR), '[]'),
      COALESCE(CAST(related_industries AS CHAR), '[]'),
      COALESCE(CAST(related_concepts AS CHAR), '[]'),
      importance_score,
      COALESCE(summary, '')
    FROM daily_market_themes
    WHERE trade_date = {sql_string(trade_date.isoformat())}
    ORDER BY importance_score DESC
    LIMIT {int(limit)};
    """
    themes: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 6:
            continue
        themes.append(
            {
                "theme_name": row[0],
                "keywords": parse_json_text(row[1]) or [],
                "related_industries": parse_json_text(row[2]) or [],
                "related_concepts": parse_json_text(row[3]) or [],
                "importance_score": to_float(row[4]),
                "summary": row[5],
            }
        )
    return themes


def split_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[,，、;；]", str(value or "")) if part.strip()]


def theme_matches(row: dict[str, Any], themes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    industry = str(row.get("industry") or "")
    sub_industry = str(row.get("sub_industry") or "")
    concepts = split_text_list(row.get("concepts") or [])
    haystack = " ".join([row.get("name", ""), industry, sub_industry, " ".join(concepts)])
    matches: list[dict[str, Any]] = []
    score = 0.0
    for theme in themes:
        hits: list[str] = []
        for item in theme.get("related_industries") or []:
            item = str(item)
            if item and (item in industry or item in sub_industry or industry in item or sub_industry in item):
                hits.append(item)
        for item in theme.get("related_concepts") or []:
            item = str(item)
            if item and any(item in concept or concept in item for concept in concepts):
                hits.append(item)
        for item in theme.get("keywords") or []:
            item = str(item)
            if item and item in haystack:
                hits.append(item)
        if not hits:
            continue
        weight = min(10.0, float(theme.get("importance_score") or 0) / 3.0)
        score += weight + min(3, len(set(hits)))
        matches.append(
            {
                "theme_name": theme.get("theme_name", ""),
                "hits": sorted(set(hits))[:6],
                "importance_score": theme.get("importance_score", 0),
            }
        )
    matches.sort(key=lambda item: float(item.get("importance_score") or 0), reverse=True)
    return matches[:5], round(score, 2)


def amount_score(amount: float) -> float:
    if amount <= 0:
        return 0.0
    return min(30.0, math.log10(amount + 1) * 3.0)


def buy_pressure(item: dict[str, Any]) -> float:
    bid_vol = to_float(item.get("bid_vol1") or item.get("bid1_volume"))
    ask_vol = to_float(item.get("ask_vol1") or item.get("ask1_volume"))
    total = bid_vol + ask_vol
    if total <= 0:
        return 0.0
    return round((bid_vol - ask_vol) / total, 4)


def fetch_auction_quotes(api: Any, universe: list[dict[str, Any]], batch_size: int) -> dict[str, dict[str, Any]]:
    name_by_key = {f"{row['market']}:{row['code']}": row.get("name", "") for row in universe}
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(universe), batch_size):
        batch = universe[start : start + batch_size]
        symbols = [(int(row["market"]), str(row["code"])) for row in batch]
        data = api.get_security_quotes(symbols) or []
        for item in data:
            key = f"{item.get('market')}:{item.get('code')}"
            price = to_float(item.get("price"))
            bid1 = to_float(item.get("bid1"))
            ask1 = to_float(item.get("ask1"))
            if price <= 0 and bid1 <= 0 and ask1 <= 0:
                continue
            row = dict(item)
            row["name"] = name_by_key.get(key, "")
            result[key] = row
        time.sleep(0.03)
    return result


def indicative_price(item: dict[str, Any]) -> float:
    price = to_float(item.get("price"))
    if price > 0:
        return price
    bid1 = to_float(item.get("bid1"))
    ask1 = to_float(item.get("ask1"))
    if bid1 > 0 and ask1 > 0 and near_price(bid1, ask1, tolerance=0.0001):
        return bid1
    if bid1 > 0 and ask1 <= 0:
        return bid1
    if ask1 > 0 and bid1 <= 0:
        return ask1
    return max(bid1, ask1)


def auction_amount_value(item: dict[str, Any], price: float) -> tuple[float, int]:
    amount = to_float(item.get("amount"))
    bid_vol = int(to_float(item.get("bid_vol1") or item.get("bid1_volume")))
    ask_vol = int(to_float(item.get("ask_vol1") or item.get("ask1_volume")))
    volume = int(to_float(item.get("vol")))
    if amount > 1:
        return amount, volume
    matched_lots = volume if volume > 0 else min(bid_vol, ask_vol)
    return matched_lots * 100 * price, matched_lots


def risk_flags(row: dict[str, Any]) -> str:
    flags = []
    name = str(row.get("name", ""))
    concepts = " ".join(split_text_list(row.get("concepts") or []))
    if "ST" in name.upper() or "ST板块" in concepts:
        flags.append("ST")
    if to_float(row.get("auction_pct")) >= 9.5 and to_float(row.get("auction_amount")) < 20_000_000:
        flags.append("缩量高开")
    if to_float(row.get("auction_pct")) < 0:
        flags.append("低开")
    return ",".join(flags)


def fetch_enriched_rows(
    args: argparse.Namespace,
    config: Any,
    captured: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    captured = captured or datetime.now()
    trade_date = captured.date()
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    themes = read_themes(config, trade_date, args.theme_limit)

    servers = [parse_server(item) for item in args.server] if args.server else DEFAULT_SERVERS
    api, server = connect(servers, args.timeout)
    try:
        universe = build_universe(api, args.universe_csv, args.refresh_universe)
        current = fetch_auction_quotes(api, universe, args.batch_size)
    finally:
        api.disconnect()

    cache_dir = args.tdx_dir / "T0002" / "hq_cache"
    industry_map = load_industry_map(cache_dir)
    concept_map = load_concept_map(cache_dir)

    raw_rows: list[dict[str, Any]] = []
    for key, item in current.items():
        price = indicative_price(item)
        preclose = to_float(item.get("last_close"))
        if price <= 0 or preclose <= 0:
            continue
        auction_pct = round((price / preclose - 1) * 100, 4)
        industry = industry_map.get(key, {})
        concepts = concept_map.get(key, [])[: args.concept_limit]
        name = str(item.get("name", ""))
        if args.exclude_st and is_st_stock(name, concepts):
            continue
        up_price = limit_price(preclose, str(item.get("code", "")), name, "up", concepts)
        down_price = limit_price(preclose, str(item.get("code", "")), name, "down", concepts)
        bid1 = to_float(item.get("bid1"))
        ask1 = to_float(item.get("ask1"))
        bid_vol1 = int(to_float(item.get("bid_vol1") or item.get("bid1_volume")))
        ask_vol1 = int(to_float(item.get("ask_vol1") or item.get("ask1_volume")))
        amount, matched_volume = auction_amount_value(item, price)
        limit_side = "none"
        limit_price_value = 0.0
        seal_volume = 0
        if near_price(price, up_price) or near_price(bid1, up_price):
            limit_side = "up"
            limit_price_value = up_price
            seal_volume = bid_vol1
        elif near_price(price, down_price) or near_price(ask1, down_price):
            limit_side = "down"
            limit_price_value = down_price
            seal_volume = ask_vol1
        seal_amount = round(seal_volume * 100 * (limit_price_value or price), 2) if seal_volume > 0 else 0.0
        row = {
            "trade_date": trade_date.isoformat(),
            "captured_at": captured.strftime("%Y-%m-%d %H:%M:%S"),
            "snapshot_minute": minute_floor(captured).strftime("%Y-%m-%d %H:%M:%S"),
            "market": item.get("market", ""),
            "code": item.get("code", ""),
            "name": name,
            "stock_name": name,
            "auction_price": round(price, 4),
            "preclose": round(preclose, 4),
            "auction_pct": auction_pct,
            "auction_amount": round(amount, 2),
            "matched_volume": int(matched_volume),
            "bid1": bid1,
            "ask1": ask1,
            "bid_vol1": bid_vol1,
            "ask_vol1": ask_vol1,
            "limit_up_price": up_price,
            "limit_down_price": down_price,
            "limit_side": limit_side,
            "limit_price": round(limit_price_value, 4) if limit_price_value else None,
            "seal_volume": seal_volume,
            "seal_amount": seal_amount,
            "buy_pressure": buy_pressure(item),
            "industry": industry.get("industry", ""),
            "sub_industry": industry.get("sub_industry", ""),
            "concepts": concepts,
            "server": server,
        }
        matches, theme_score = theme_matches(row, themes)
        row["theme_matches"] = matches
        row["theme_score"] = theme_score
        raw_rows.append(row)

    industry_counts = Counter(row.get("industry") or row.get("sub_industry") or "" for row in raw_rows)
    concept_counts: Counter[str] = Counter()
    for row in raw_rows:
        concept_counts.update(split_text_list(row.get("concepts") or []))

    rows: list[dict[str, Any]] = []
    for row in raw_rows:
        sector_count = int(industry_counts.get(row.get("industry") or row.get("sub_industry") or "", 0))
        concept_count = max([concept_counts.get(item, 0) for item in split_text_list(row.get("concepts") or [])] or [0])
        resonance = min(20.0, sector_count * 2.0 + concept_count * 1.2)
        pct_score = max(0.0, min(40.0, to_float(row.get("auction_pct")) * 4.0))
        score = pct_score + amount_score(to_float(row.get("auction_amount"))) + to_float(row.get("theme_score")) + resonance
        score += max(0.0, to_float(row.get("buy_pressure")) * 5.0)
        row.update(
            {
                "sector_hot_count": sector_count,
                "concept_hot_count": concept_count,
                "resonance_score": round(resonance, 2),
                "score": round(score, 2),
                "risk_flags": risk_flags(row),
            }
        )
        rows.append(row)

    meta = {
        "trade_date": trade_date.isoformat(),
        "captured_at": captured.strftime("%Y-%m-%d %H:%M:%S"),
        "snapshot_minute": minute_floor(captured).strftime("%Y-%m-%d %H:%M:%S"),
        "server": server,
        "quote_count": len(current),
        "raw_candidate_count": len(raw_rows),
        "theme_count": len(themes),
        "min_auction_pct": args.min_auction_pct,
        "min_auction_amount": args.min_auction_amount,
        "exclude_st": args.exclude_st,
    }
    return rows, meta


def build_rows(args: argparse.Namespace, config: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, meta = fetch_enriched_rows(args, config)
    candidates = [
        row
        for row in rows
        if to_float(row.get("auction_pct")) >= args.min_auction_pct
        and to_float(row.get("auction_amount")) >= args.min_auction_amount
    ]
    candidates.sort(
        key=lambda item: (
            -to_float(item.get("score")),
            -to_float(item.get("theme_score")),
            -to_float(item.get("auction_amount")),
        )
    )
    for index, row in enumerate(candidates[: args.limit], start=1):
        row["rank_no"] = index
    meta["raw_candidate_count"] = len(candidates)
    return candidates[: args.limit], meta


def with_analysis_rank(rows: list[dict[str, Any]], analysis_kind: str, limit_side: str = "none") -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        out = dict(row)
        out["analysis_kind"] = analysis_kind
        out["rank_no"] = index
        if limit_side != "none":
            out["limit_side"] = limit_side
        ranked.append(out)
    return ranked


def build_minute_analysis_rows(args: argparse.Namespace, config: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, meta = fetch_enriched_rows(args, config)
    pct_top = sorted(
        rows,
        key=lambda item: (-to_float(item.get("auction_pct")), -to_float(item.get("auction_amount"))),
    )[: args.minute_top]
    up_seals = sorted(
        [row for row in rows if row.get("limit_side") == "up" and to_float(row.get("seal_volume")) > 0],
        key=lambda item: (-to_float(item.get("seal_amount")), -to_float(item.get("seal_volume"))),
    )[: args.seal_top]
    down_seals = sorted(
        [row for row in rows if row.get("limit_side") == "down" and to_float(row.get("seal_volume")) > 0],
        key=lambda item: (-to_float(item.get("seal_amount")), -to_float(item.get("seal_volume"))),
    )[: args.seal_top]
    analysis_rows = []
    analysis_rows.extend(with_analysis_rank(pct_top, "pct_top10"))
    analysis_rows.extend(with_analysis_rank(up_seals, "limit_up_order", "up"))
    analysis_rows.extend(with_analysis_rank(down_seals, "limit_down_order", "down"))
    meta.update(
        {
            "analysis_row_count": len(analysis_rows),
            "pct_top_count": len(pct_top),
            "limit_up_order_count": len(up_seals),
            "limit_down_order_count": len(down_seals),
            "minute_top": args.minute_top,
            "seal_top": args.seal_top,
        }
    )
    return analysis_rows, meta


def seconds_to_next_minute(interval: int) -> float:
    now = datetime.now()
    next_minute = now.replace(second=0, microsecond=0).timestamp() + max(1, interval)
    return max(0.0, next_minute - now.timestamp())


def should_continue_loop(args: argparse.Namespace, runs: int) -> bool:
    if args.max_minute_runs and runs >= args.max_minute_runs:
        return False
    if not args.loop_until:
        return False
    until = parse_hhmm(args.loop_until)
    return datetime.now().time() < until


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build 09:25 A-share auction candidates.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--min-auction-pct", type=float, default=1.0)
    parser.add_argument("--min-auction-amount", type=float, default=10_000_000)
    parser.add_argument("--theme-limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--concept-limit", type=int, default=12)
    parser.add_argument("--allow-outside-auction", action="store_true")
    parser.add_argument("--minute-analysis", action="store_true", help="Write 09:20-09:25 minute radar rows.")
    parser.add_argument("--loop-until", default="", help="HH:MM end time for minute radar loop, e.g. 09:25.")
    parser.add_argument("--minute-interval", type=int, default=60)
    parser.add_argument("--max-minute-runs", type=int, default=0)
    parser.add_argument("--minute-top", type=int, default=10)
    parser.add_argument("--seal-top", type=int, default=10)
    parser.add_argument("--include-st", dest="exclude_st", action="store_false")
    parser.set_defaults(exclude_st=True)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--server", action="append", default=[], help="ip:port override")
    parser.add_argument("--tdx-dir", type=Path, default=DEFAULT_TDX_DIR)
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "tdx_a_stock_universe.csv")
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "auction_candidates.json")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    needs_minute_window = args.minute_analysis or bool(args.loop_until)
    allowed_now = is_auction_minute_time() if needs_minute_window else is_auction_time()
    if not args.allow_outside_auction and not allowed_now:
        payload = {"ok": True, "skipped": True, "reason": "outside_auction_time", "at": now_text()}
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    config = mysql_config_from_args(args)

    minute_payloads: list[dict[str, Any]] = []
    imported_minutes = 0
    if needs_minute_window:
        runs = 0
        while True:
            analysis_rows, minute_meta = build_minute_analysis_rows(args, config)
            if analysis_rows:
                imported_minutes += import_auction_minute_analysis_rows(config, analysis_rows)
            minute_payloads.append({**minute_meta, "row_count": len(analysis_rows), "rows": analysis_rows})
            runs += 1
            print(
                json.dumps(
                    {
                        "ok": True,
                        "minute": minute_meta.get("snapshot_minute"),
                        "rows": len(analysis_rows),
                        "pct_top": minute_meta.get("pct_top_count"),
                        "limit_up_order": minute_meta.get("limit_up_order_count"),
                        "limit_down_order": minute_meta.get("limit_down_order_count"),
                    },
                    ensure_ascii=False,
                )
            )
            if not should_continue_loop(args, runs):
                break
            time.sleep(seconds_to_next_minute(args.minute_interval))

    rows, meta = build_rows(args, config)
    payload = {
        "ok": True,
        "built_at": now_text(),
        **meta,
        "row_count": len(rows),
        "rows": rows,
        "minute_imported": imported_minutes,
        "minute_runs": len(minute_payloads),
        "minute_payloads": minute_payloads,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        imported = import_auction_candidate_rows(config, rows)
    else:
        run_mysql(config, f"DELETE FROM auction_candidates WHERE trade_date={sql_string(meta['trade_date'])};")
        imported = 0
    print(
        json.dumps(
            {
                "ok": True,
                "rows": len(rows),
                "imported": imported,
                "minute_imported": imported_minutes,
                "minute_runs": len(minute_payloads),
                "output_json": str(args.output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
