#!/usr/bin/env python
"""CLI entry for realtime TDX mover scanning."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.analysis.realtime_scan import RealtimeScanConfig, RealtimeScanPaths, RealtimeScanService
from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.sources.quotes import DEFAULT_TDX_SERVERS, QuoteProviderConfig, TdxQuoteProvider, parse_tdx_server
from stock_move_scout.sources.tdx_cache import load_tdx_label_cache


DEFAULT_TDX_DIR = Path(r"G:\D盘迁移\Tools\tdx")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Watch A-share realtime movers via TDX quote servers.")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--max-signal-rows", type=int, default=50)
    parser.add_argument("--min-speed-signal", type=float, default=1.5)
    parser.add_argument("--min-amount-delta-15s", type=float, default=30_000_000)
    parser.add_argument("--min-amount-delta-speed", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--concept-limit", type=int, default=8)
    parser.add_argument("--heat-sample-size", type=int, default=80)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--server", action="append", default=[], help="ip:port override")
    parser.add_argument("--codes", default="", help="Comma/space separated stock codes to scan. Empty means full universe unless another scope is enabled.")
    parser.add_argument("--research-pool-only", dest="research_pool_only", action="store_true", help="Scan only active research pool stocks.")
    parser.add_argument("--trade-date", default=datetime.now().date().isoformat())
    parser.add_argument("--fresh-snapshot-max-age-seconds", type=int, default=120, help="Ignore the previous quote snapshot if it is older than this or the scan scope changed.")
    parser.add_argument("--no-pct-change-first-run-signal", action="store_true", help="When there is no fresh previous snapshot, do not use day pct_change as speed.")
    parser.add_argument("--tdx-dir", type=Path, default=DEFAULT_TDX_DIR)
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "tdx_a_stock_universe.csv")
    parser.add_argument("--snapshot-json", type=Path, default=root / "data" / "stock" / "tdx_mover_last_snapshot.json")
    parser.add_argument("--full-market-csv", type=Path, default=root / "data" / "stock" / "tdx_full_market_latest.csv")
    parser.add_argument("--speed-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--speed-history-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_history.csv")
    parser.add_argument("--pct-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_pct_top10_latest.csv")
    parser.add_argument("--judgement-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_judgement_latest.csv")
    parser.add_argument("--judgement-history-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_judgement_history.csv")
    parser.add_argument("--meta-json", type=Path, default=root / "data" / "stock" / "tdx_mover_meta.json")
    parser.add_argument("--seen-json", type=Path, default=root / "data" / "stock" / "tdx_mover_seen.json")
    add_mysql_args(parser)
    return parser.parse_args()


def build_service(args: argparse.Namespace, cache: dict[str, object]) -> RealtimeScanService:
    servers = tuple(parse_tdx_server(item) for item in args.server) if args.server else DEFAULT_TDX_SERVERS
    quote_provider = TdxQuoteProvider(
        universe_csv=args.universe_csv,
        config=QuoteProviderConfig(
            servers=servers,
            timeout=int(args.timeout),
            batch_size=int(args.batch_size),
        ),
    )
    research_pool_provider = ResearchPoolProvider(mysql_config_from_args(args)) if args.research_pool_only else None
    return RealtimeScanService(
        quote_provider=quote_provider,
        research_pool_provider=research_pool_provider,
        industry_map=cache["industry_map"],  # type: ignore[arg-type]
        concept_map=cache["concept_map"],  # type: ignore[arg-type]
        config=RealtimeScanConfig(
            paths=RealtimeScanPaths(
                snapshot_json=args.snapshot_json,
                full_market_csv=args.full_market_csv,
                speed_latest_csv=args.speed_latest_csv,
                speed_history_csv=args.speed_history_csv,
                pct_latest_csv=args.pct_latest_csv,
                judgement_latest_csv=args.judgement_latest_csv,
                judgement_history_csv=args.judgement_history_csv,
                meta_json=args.meta_json,
                seen_json=args.seen_json,
            ),
            top=args.top,
            max_signal_rows=args.max_signal_rows,
            min_speed_signal=args.min_speed_signal,
            min_amount_delta_15s=args.min_amount_delta_15s,
            min_amount_delta_speed=args.min_amount_delta_speed,
            concept_limit=args.concept_limit,
            heat_sample_size=args.heat_sample_size,
            refresh_universe=args.refresh_universe,
            codes=args.codes,
            research_pool_only=args.research_pool_only,
            trade_date=str(args.trade_date),
            fresh_snapshot_max_age_seconds=args.fresh_snapshot_max_age_seconds,
            no_pct_change_first_run_signal=args.no_pct_change_first_run_signal,
            interval_seconds=args.interval,
        ),
    )


def run_once(args: argparse.Namespace, cache: dict[str, object]) -> None:
    print(build_service(args, cache).scan_once().summary)


def main() -> int:
    args = parse_args()
    cache = load_tdx_label_cache(args.tdx_dir / "T0002" / "hq_cache")

    runs = 0
    while True:
        started = time.monotonic()
        run_once(args, cache)
        if args.once:
            return 0
        runs += 1
        if args.max_runs and runs >= args.max_runs:
            return 0
        elapsed = time.monotonic() - started
        time.sleep(max(0, args.interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
