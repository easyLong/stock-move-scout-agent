#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.sources.kpl_market_capacity import KplMarketCapacityConfig, collect_kpl_market_capacity


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect KPL market capacity forecast snapshot.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--market-type", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    result = collect_kpl_market_capacity(
        mysql_config_from_args(args),
        KplMarketCapacityConfig(
            trade_date=str(args.trade_date),
            timeout=max(1, int(args.timeout)),
            market_type=int(args.market_type),
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
