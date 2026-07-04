#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.sources.auction_candidates import (
    DEFAULT_TDX_DIR,
    AuctionCandidateConfig,
    AuctionCandidateService,
    parse_servers,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build 09:25 A-share auction candidates.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--min-auction-pct", type=float, default=0.0)
    parser.add_argument("--min-auction-amount", type=float, default=0.0)
    parser.add_argument("--theme-limit", type=int, default=20)
    parser.add_argument("--timeout", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--concept-limit", type=int, default=12)
    parser.add_argument("--allow-outside-auction", action="store_true")
    parser.add_argument("--minute-analysis", action="store_true", help="Write 09:20-09:25 minute radar rows.")
    parser.add_argument("--loop-until", default="", help="HH:MM end time for minute radar loop, e.g. 09:25.")
    parser.add_argument("--minute-interval", type=int, default=60)
    parser.add_argument("--max-minute-runs", type=int, default=0)
    parser.add_argument("--minute-top", type=int, default=20)
    parser.add_argument("--seal-top", type=int, default=0, help="0 means collect all limit-up/down sealed orders; positive values keep TopN.")
    parser.add_argument("--include-st", dest="exclude_st", action="store_false")
    parser.set_defaults(exclude_st=True)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--server", action="append", default=[], help="ip:port override")
    parser.add_argument("--tdx-dir", type=Path, default=DEFAULT_TDX_DIR)
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "tdx_a_stock_universe.csv")
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "auction_candidates.json")
    return parser.parse_args()


def build_service(args: argparse.Namespace) -> AuctionCandidateService:
    return AuctionCandidateService(
        mysql_config=mysql_config_from_args(args),
        config=AuctionCandidateConfig(
            trade_date=str(args.trade_date or ""),
            limit=int(args.limit),
            min_auction_pct=float(args.min_auction_pct),
            min_auction_amount=float(args.min_auction_amount),
            theme_limit=int(args.theme_limit),
            timeout=int(args.timeout),
            batch_size=int(args.batch_size),
            concept_limit=int(args.concept_limit),
            allow_outside_auction=bool(args.allow_outside_auction),
            minute_analysis=bool(args.minute_analysis),
            loop_until=str(args.loop_until or ""),
            minute_interval=int(args.minute_interval),
            max_minute_runs=int(args.max_minute_runs),
            minute_top=int(args.minute_top),
            seal_top=int(args.seal_top),
            exclude_st=bool(args.exclude_st),
            refresh_universe=bool(args.refresh_universe),
            servers=parse_servers(args.server),
            tdx_dir=args.tdx_dir,
            universe_csv=args.universe_csv,
            output_json=args.output_json,
        ),
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    result = build_service(args).run(lambda payload: print(json.dumps(payload, ensure_ascii=False)))
    print(
        json.dumps(
            {
                "ok": True,
                "rows": int(result.payload.get("row_count") or 0),
                "imported": result.imported,
                "minute_imported": result.minute_imported,
                "summary_imported": result.summary_imported,
                "minute_runs": int(result.payload.get("minute_runs") or 0),
                "output_json": str(args.output_json),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
