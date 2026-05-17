#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.analysis.anchor_realtime_roles import (
    AnchorRealtimeRoleConfig,
    AnchorRealtimeRoleService,
    clean_levels,
    parse_servers,
)
from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.sources.quotes import QuoteProviderConfig, TdxQuoteProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build realtime leader/core roles inside research-pool theme members.")
    parser.add_argument("--levels", default="strong,medium", help="Deprecated compatibility option; research-pool theme members are used as the pool.")
    parser.add_argument("--medium-cap", type=int, default=120)
    parser.add_argument("--min-members", type=int, default=2)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--tdx-timeout", type=int, default=3)
    parser.add_argument("--servers", default="")
    parser.add_argument("--trading-only", action="store_true")
    parser.add_argument("--research-pool-only", dest="research_pool_only", action="store_true", help="Deprecated compatibility option; research-pool theme members are always used.")
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--universe-csv", type=Path, default=Path("data/stock/tdx_a_stock_universe.csv"))
    add_mysql_args(parser)
    return parser.parse_args()


def build_service(args: argparse.Namespace) -> AnchorRealtimeRoleService:
    servers = tuple(parse_servers(str(args.servers)))
    return AnchorRealtimeRoleService(
        mysql_config=mysql_config_from_args(args),
        quote_provider=TdxQuoteProvider(
            universe_csv=args.universe_csv,
            config=QuoteProviderConfig(
                servers=servers,
                timeout=int(args.tdx_timeout),
                batch_size=int(args.batch_size),
            ),
        ),
        config=AnchorRealtimeRoleConfig(
            levels=tuple(sorted(clean_levels(args.levels))),
            medium_cap=int(args.medium_cap),
            min_members=int(args.min_members),
            batch_size=int(args.batch_size),
            tdx_timeout=int(args.tdx_timeout),
            servers=servers,
            universe_csv=args.universe_csv,
            trading_only=bool(args.trading_only),
            research_pool_only=bool(args.research_pool_only),
            trade_date=str(args.trade_date or ""),
        ),
    )


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    result = build_service(parse_args()).run_once()
    print(json.dumps(result.payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
