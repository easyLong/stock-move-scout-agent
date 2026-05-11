#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.event_engine import (
    build_derived_signals,
    build_event_engine,
    build_event_evidence,
    build_move_events,
    ensure_event_engine_tables,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build event-driven move evidence engine layers.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--code", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument(
        "--stage",
        choices=["all", "events", "signals", "evidence", "ensure"],
        default="all",
        help="Layer to build.",
    )
    args = parser.parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    code = str(args.code or "").strip()
    if args.stage == "ensure":
        ensure_event_engine_tables(config)
        result = {"ok": True, "stage": "ensure"}
    elif args.stage == "events":
        result = build_move_events(config, str(args.trade_date), limit=args.limit, code=code)
    elif args.stage == "signals":
        result = build_derived_signals(config, str(args.trade_date), code=code)
    elif args.stage == "evidence":
        result = build_event_evidence(config, str(args.trade_date), code=code)
    else:
        result = build_event_engine(config, str(args.trade_date), code=code, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
