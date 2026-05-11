#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.feed.root_cache import (
    ensure_root_evidence_cache_table,
    latest_root_evidence_trade_date,
    process_root_evidence_cache_dirty,
    refresh_root_evidence_cache,
)

from stock_scout_mysql import add_mysql_args, mysql_config_from_args


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh precomputed root evidence cache.")
    parser.add_argument("--trade-date", default="latest")
    parser.add_argument("--code", default="")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dirty-only", action="store_true", help="Only process dirty queue rows.")
    parser.add_argument("--force", action="store_true", help="Force refresh cache for the requested scope.")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    ensure_root_evidence_cache_table(config)
    trade_date = latest_root_evidence_trade_date(config) if args.trade_date == "latest" else args.trade_date
    result: dict[str, int | str] = {"trade_date": trade_date}
    if args.dirty_only:
        result.update(process_root_evidence_cache_dirty(config, trade_date, args.limit, args.code))
    else:
        codes = [args.code] if args.code else None
        result.update(refresh_root_evidence_cache(config, trade_date, codes=codes, force=args.force))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
