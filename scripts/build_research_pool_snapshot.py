#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.research_pool import (
    DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    DEFAULT_RESEARCH_POOL_GAIN_TOP,
    DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    materialize_research_pool_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize the active research pool into daily snapshot tables.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--limit-up-days", type=int, default=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS)
    parser.add_argument("--gain-period-days", type=int, default=DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS)
    parser.add_argument("--gain-top", type=int, default=DEFAULT_RESEARCH_POOL_GAIN_TOP)
    parser.add_argument("--force", action="store_true", help="Rebuild the snapshot even if it already exists.")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    result = materialize_research_pool_snapshot(
        mysql_config_from_args(args),
        str(args.trade_date),
        limit_up_days=max(1, int(args.limit_up_days)),
        gain_period_days=max(1, int(args.gain_period_days)),
        gain_top=max(1, int(args.gain_top)),
        force=bool(args.force),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
