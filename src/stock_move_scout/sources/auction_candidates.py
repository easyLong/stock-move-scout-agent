from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable

from stock_move_scout.db import MySqlConfig
from stock_move_scout.sources.auction_storage import (
    delete_auction_candidates_for_date,
    delete_auction_trend_summary_for_date,
    import_auction_candidate_rows,
    import_auction_minute_analysis_rows,
    import_auction_trend_summary_rows,
)
from stock_move_scout.sources.market_themes import match_market_themes, read_market_themes
from stock_move_scout.sources.quote_rows import safe_to_float as to_float
from stock_move_scout.sources.quotes import DEFAULT_TDX_SERVERS, QuoteProviderConfig, QuoteSymbol, TdxQuoteProvider, parse_tdx_server
from stock_move_scout.sources.tdx_cache import load_concept_map, load_industry_map


DEFAULT_TDX_DIR = Path(r"G:\D盘迁移\Tools\tdx")


@dataclass(frozen=True)
class AuctionCandidateConfig:
    trade_date: str = ""
    limit: int = 3
    min_auction_pct: float = 0.0
    min_auction_amount: float = 0.0
    theme_limit: int = 20
    timeout: int = 3
    batch_size: int = 80
    concept_limit: int = 12
    allow_outside_auction: bool = False
    minute_analysis: bool = False
    loop_until: str = ""
    minute_interval: int = 60
    max_minute_runs: int = 0
    minute_top: int = 20
    seal_top: int = 0
    exclude_st: bool = True
    refresh_universe: bool = False
    servers: tuple[tuple[str, int], ...] = DEFAULT_TDX_SERVERS
    tdx_dir: Path = DEFAULT_TDX_DIR
    universe_csv: Path = Path("data/stock/tdx_a_stock_universe.csv")
    output_json: Path = Path("runs/data_tasks/auction_candidates.json")


@dataclass(frozen=True)
class AuctionCandidateResult:
    payload: dict[str, Any]
    imported: int
    minute_imported: int
    summary_imported: int = 0


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_auction_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return clock_time(9, 15) <= now.time() <= clock_time(9, 25, 59)


def is_auction_minute_time(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return clock_time(9, 15) <= now.time() <= clock_time(9, 25, 59)


def minute_floor(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def parse_hhmm(value: str) -> clock_time:
    parsed = datetime.strptime(value, "%H:%M").time()
    return clock_time(parsed.hour, parsed.minute)


def split_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in re.split(r"[,，、;\s]+", str(value or "")) if part.strip()]


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


def quote_symbol_rows(symbols: list[QuoteSymbol]) -> list[dict[str, Any]]:
    return [{"market": symbol.market, "code": symbol.code, "name": symbol.name} for symbol in symbols]


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


def seconds_to_next_minute(interval: int) -> float:
    now = datetime.now()
    next_minute = now.replace(second=0, microsecond=0).timestamp() + max(1, interval)
    return max(0.0, next_minute - now.timestamp())


def parse_servers(values: list[str] | tuple[str, ...] | None) -> tuple[tuple[str, int], ...]:
    if not values:
        return DEFAULT_TDX_SERVERS
    return tuple(parse_tdx_server(item) for item in values)


class AuctionCandidateService:
    def __init__(
        self,
        *,
        mysql_config: MySqlConfig,
        config: AuctionCandidateConfig,
        quote_provider: TdxQuoteProvider | None = None,
        industry_map: dict[str, dict[str, str]] | None = None,
        concept_map: dict[str, list[str]] | None = None,
    ) -> None:
        self.mysql_config = mysql_config
        self.config = config
        self.quote_provider = quote_provider or TdxQuoteProvider(
            universe_csv=config.universe_csv,
            config=QuoteProviderConfig(
                servers=tuple(config.servers),
                timeout=int(config.timeout),
                batch_size=int(config.batch_size),
            ),
        )
        cache_dir = config.tdx_dir / "T0002" / "hq_cache"
        self.industry_map = industry_map if industry_map is not None else load_industry_map(cache_dir)
        self.concept_map = concept_map if concept_map is not None else load_concept_map(cache_dir)

    def trade_date_value(self, captured: datetime) -> date:
        if self.config.trade_date:
            return datetime.strptime(self.config.trade_date, "%Y-%m-%d").date()
        return captured.date()

    def fetch_enriched_rows(self, captured: datetime | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        captured = captured or datetime.now()
        trade_date = self.trade_date_value(captured)
        themes = read_market_themes(self.mysql_config, trade_date, int(self.config.theme_limit))

        api, server = self.quote_provider.connect()
        try:
            universe = self.quote_provider.load_universe(api, refresh=bool(self.config.refresh_universe))
            current = fetch_auction_quotes(api, quote_symbol_rows(universe), int(self.config.batch_size))
        finally:
            api.disconnect()

        raw_rows: list[dict[str, Any]] = []
        for key, item in current.items():
            price = indicative_price(item)
            preclose = to_float(item.get("last_close"))
            if price <= 0 or preclose <= 0:
                continue
            auction_pct = round((price / preclose - 1) * 100, 4)
            industry = self.industry_map.get(key, {})
            concepts = self.concept_map.get(key, [])[: int(self.config.concept_limit)]
            name = str(item.get("name", ""))
            if self.config.exclude_st and is_st_stock(name, concepts):
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
            matches, theme_score = match_market_themes(row, themes)
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
            "min_auction_pct": self.config.min_auction_pct,
            "min_auction_amount": self.config.min_auction_amount,
            "exclude_st": self.config.exclude_st,
        }
        return rows, meta

    def build_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows, meta = self.fetch_enriched_rows()
        candidates = [row for row in rows if row.get("limit_side") == "up" and to_float(row.get("seal_amount")) > 0]
        candidates.sort(
            key=lambda item: (
                -to_float(item.get("seal_amount")),
                -to_float(item.get("seal_volume")),
                -to_float(item.get("auction_amount")),
            )
        )
        for index, row in enumerate(candidates[: int(self.config.limit)], start=1):
            row["rank_no"] = index
            row["score"] = round(min(100.0, 70.0 + to_float(row.get("seal_amount")) / 20_000_000), 2)
        meta["raw_candidate_count"] = len(candidates)
        return candidates[: int(self.config.limit)], meta

    def build_minute_analysis_rows(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        rows, meta = self.fetch_enriched_rows()
        seal_top = int(self.config.seal_top or 0)
        up_seals = sorted(
            [row for row in rows if row.get("limit_side") == "up" and to_float(row.get("seal_volume")) > 0],
            key=lambda item: (-to_float(item.get("seal_amount")), -to_float(item.get("seal_volume"))),
        )
        down_seals = sorted(
            [row for row in rows if row.get("limit_side") == "down" and to_float(row.get("seal_volume")) > 0],
            key=lambda item: (-to_float(item.get("seal_amount")), -to_float(item.get("seal_volume"))),
        )
        if seal_top > 0:
            up_seals = up_seals[:seal_top]
            down_seals = down_seals[:seal_top]
        analysis_rows = []
        analysis_rows.extend(with_analysis_rank(up_seals, "limit_up_order", "up"))
        analysis_rows.extend(with_analysis_rank(down_seals, "limit_down_order", "down"))
        meta.update(
            {
                "analysis_row_count": len(analysis_rows),
                "pct_top_count": 0,
                "limit_up_order_count": len(up_seals),
                "limit_down_order_count": len(down_seals),
                "minute_top": self.config.minute_top,
                "seal_top": seal_top,
                "seal_scope": "all" if seal_top <= 0 else f"top{seal_top}",
            }
        )
        return analysis_rows, meta

    def build_summary_rows(self, rows: list[dict[str, Any]], minute_runs: int) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for row in rows:
            auction_pct = to_float(row.get("auction_pct"))
            auction_amount = to_float(row.get("auction_amount"))
            seal_amount = to_float(row.get("seal_amount"))
            seal_yi = seal_amount / 100_000_000
            amount_yi = auction_amount / 100_000_000
            captured_at = str(row.get("captured_at") or now_text())
            summary = {
                "trade_date": row.get("trade_date"),
                "code": row.get("code"),
                "stock_name": row.get("stock_name") or row.get("name"),
                "first_seen_minute": row.get("snapshot_minute") or captured_at,
                "last_seen_minute": row.get("snapshot_minute") or captured_at,
                "minute_count": max(1, int(minute_runs or 0)),
                "pct_top_count": 0,
                "limit_up_count": 1,
                "limit_down_count": 0,
                "best_pct_rank": 0,
                "final_candidate_rank": row.get("rank_no"),
                "first_auction_pct": auction_pct,
                "last_auction_pct": auction_pct,
                "pct_delta": 0,
                "first_auction_amount": auction_amount,
                "last_auction_amount": auction_amount,
                "amount_delta": 0,
                "amount_growth_ratio": 0,
                "max_seal_amount": seal_amount,
                "last_seal_amount": seal_amount,
                "theme_score": row.get("theme_score"),
                "theme_matches": row.get("theme_matches") or [],
                "sector_hot_count": row.get("sector_hot_count"),
                "concept_hot_count": row.get("concept_hot_count"),
                "final_score": row.get("score"),
                "trend_score": row.get("score"),
                "trend_label": "涨停封单",
                "key_points": [
                    f"涨停封单 {seal_yi:.2f}亿",
                    f"竞价涨幅 {auction_pct:.2f}%",
                    f"竞价额 {amount_yi:.2f}亿",
                ],
                "action_hint": "只看封单稳定性、炸板风险和开盘后承接。",
                "raw_json": row,
                "generated_at": now_text(),
            }
            summaries.append(summary)
        return summaries

    def should_continue_loop(self, runs: int, last_snapshot_minute: str = "") -> bool:
        if self.config.max_minute_runs and runs >= self.config.max_minute_runs:
            return False
        if not self.config.loop_until:
            return False
        target = parse_hhmm(self.config.loop_until)
        if last_snapshot_minute:
            try:
                captured = datetime.strptime(str(last_snapshot_minute), "%Y-%m-%d %H:%M:%S").time()
                if captured >= target:
                    return False
            except ValueError:
                pass
        return datetime.now().time() <= clock_time(target.hour, target.minute, 59)

    def sleep_before_next_minute(self, last_snapshot_minute: str = "") -> None:
        if last_snapshot_minute:
            try:
                captured = datetime.strptime(str(last_snapshot_minute), "%Y-%m-%d %H:%M:%S")
                if minute_floor(datetime.now()) > captured:
                    return
            except ValueError:
                pass
        time.sleep(seconds_to_next_minute(int(self.config.minute_interval)))

    def run(self, on_minute_payload: Callable[[dict[str, Any]], None] | None = None) -> AuctionCandidateResult:
        needs_minute_window = self.config.minute_analysis or bool(self.config.loop_until)
        allowed_now = is_auction_minute_time() if needs_minute_window else is_auction_time()
        if not self.config.allow_outside_auction and not allowed_now:
            payload = {"ok": True, "skipped": True, "reason": "outside_auction_time", "at": now_text()}
            self.write_payload(payload)
            return AuctionCandidateResult(payload=payload, imported=0, minute_imported=0)

        minute_payloads: list[dict[str, Any]] = []
        imported_minutes = 0
        if needs_minute_window:
            runs = 0
            while True:
                analysis_rows, minute_meta = self.build_minute_analysis_rows()
                if analysis_rows:
                    imported_minutes += import_auction_minute_analysis_rows(self.mysql_config, analysis_rows)
                minute_payload = {**minute_meta, "row_count": len(analysis_rows), "rows": analysis_rows}
                minute_payloads.append(minute_payload)
                runs += 1
                if on_minute_payload:
                    on_minute_payload(
                        {
                            "ok": True,
                            "minute": minute_meta.get("snapshot_minute"),
                            "rows": len(analysis_rows),
                            "pct_top": minute_meta.get("pct_top_count"),
                            "limit_up_order": minute_meta.get("limit_up_order_count"),
                            "limit_down_order": minute_meta.get("limit_down_order_count"),
                        }
                    )
                if not self.should_continue_loop(runs, str(minute_meta.get("snapshot_minute") or "")):
                    break
                self.sleep_before_next_minute(str(minute_meta.get("snapshot_minute") or ""))

        rows, meta = self.build_rows()
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
        self.write_payload(payload)
        if rows:
            imported = import_auction_candidate_rows(self.mysql_config, rows)
            summary_rows = self.build_summary_rows(rows, len(minute_payloads))
            summary_imported = import_auction_trend_summary_rows(self.mysql_config, summary_rows)
        else:
            delete_auction_candidates_for_date(self.mysql_config, str(meta["trade_date"]))
            imported = 0
            summary_imported = 0
        return AuctionCandidateResult(
            payload=payload,
            imported=imported,
            minute_imported=imported_minutes,
            summary_imported=summary_imported,
        )

    def write_payload(self, payload: dict[str, Any]) -> None:
        self.config.output_json.parent.mkdir(parents=True, exist_ok=True)
        self.config.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "AuctionCandidateConfig",
    "AuctionCandidateResult",
    "AuctionCandidateService",
    "amount_score",
    "auction_amount_value",
    "fetch_auction_quotes",
    "import_auction_candidate_rows",
    "import_auction_minute_analysis_rows",
    "indicative_price",
    "is_auction_minute_time",
    "is_auction_time",
    "limit_price",
    "parse_servers",
    "read_market_themes",
    "risk_flags",
    "match_market_themes",
]
