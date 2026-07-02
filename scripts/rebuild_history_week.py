#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.pipelines.history_rebuild import (
    HistoryRebuildConfig,
    latest_daily_bar_trade_dates,
    rebuild_history,
    rebuild_summary,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild recent history data through the reusable pipeline layer.")
    add_mysql_args(parser)
    parser.add_argument("--days", type=int, default=5, help="Latest daily-bar trading days to rebuild.")
    parser.add_argument("--dates", default="", help="Comma-separated trade dates. Overrides --days.")
    parser.add_argument("--ma-mode", default="none", help="Research-pool mode: none/bear or ma5_10_20_30_up/bull.")
    parser.add_argument("--no-force", action="store_true", help="Reuse existing materialized rows when supported.")
    parser.add_argument("--no-kpl", action="store_true", help="Skip KPL leaderboard snapshot.")
    parser.add_argument("--summary-only", action="store_true", help="Only print output summary; do not rebuild.")
    parser.add_argument("--json", action="store_true", help="Print full pipeline JSON.")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    dates = tuple(day.strip() for day in str(args.dates or "").split(",") if day.strip())
    if not dates:
        dates = latest_daily_bar_trade_dates(config, limit=max(1, int(args.days)))
    if args.summary_only:
        payload = {"ok": True, "dates": dates, "summary": rebuild_summary(config, dates)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    pipeline_cfg = HistoryRebuildConfig(
        dates=dates,
        ma_mode=str(args.ma_mode),
        force=not bool(args.no_force),
        include_kpl=not bool(args.no_kpl),
    )
    result = rebuild_history(config, pipeline_cfg)
    payload = result.to_dict()
    payload["summary"] = rebuild_summary(config, dates)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": result.ok, "dates": dates, "summary": payload["summary"]}, ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
