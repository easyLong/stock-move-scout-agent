#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.sources.kpl_featured_sections import (
    KplFeaturedSectionConfig,
    collect_kpl_stock_featured_sections,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect KPL featured sections for research-pool stocks.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--code", default="", help="Collect a single stock code for debugging.")
    parser.add_argument("--limit", type=int, default=0, help="Limit research-pool stocks for smoke tests.")
    parser.add_argument("--ma-mode", default="none", help="Research-pool MA mode: none/bear or ma5_10_20_30_up/bull.")
    parser.add_argument("--pool-mode", default="", help="Alias for --ma-mode, accepts bear/bull.")
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--pause", type=float, default=0.08)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    result = collect_kpl_stock_featured_sections(
        mysql_config_from_args(args),
        KplFeaturedSectionConfig(
            trade_date=str(args.trade_date),
            timeout=max(1, int(args.timeout)),
            pause=max(0.0, float(args.pause)),
            limit=max(0, int(args.limit)),
            code=str(args.code or "").strip(),
            ma_mode=str(args.pool_mode or args.ma_mode),
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
