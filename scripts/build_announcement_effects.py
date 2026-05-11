#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.evidence.announcement_effects import build_announcement_effects


def main() -> int:
    parser = argparse.ArgumentParser(description="Build market-validated announcement effects from stock_ths_root_items.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--code", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-refresh-bars", action="store_true")
    parser.add_argument("--allow-local-fallback", action="store_true")
    parser.add_argument("--stale-after-days", type=int, default=31)
    parser.add_argument("--sleep-seconds", type=float, default=0.15)
    args = parser.parse_args()

    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    result = build_announcement_effects(
        config,
        trade_date=str(args.trade_date),
        lookback_days=max(1, int(args.lookback_days)),
        code=str(args.code or "").strip(),
        limit=max(0, int(args.limit)),
        refresh_bars=not args.no_refresh_bars,
        allow_local_fallback=bool(args.allow_local_fallback),
        stale_after_days=max(1, int(args.stale_after_days)),
        sleep_seconds=max(0.0, float(args.sleep_seconds)),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
