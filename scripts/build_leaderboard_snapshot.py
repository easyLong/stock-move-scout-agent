#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.feed.leaderboard_snapshot import materialize_kpl_leaderboard_snapshot, materialize_leaderboard_snapshot
from stock_move_scout.research_pool import (
    DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    DEFAULT_RESEARCH_POOL_GAIN_TOP,
    DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    normalize_research_pool_ma_mode,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Materialize post-close confirmed leaderboard snapshot.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--limit-up-days", type=int, default=DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS)
    parser.add_argument("--gain-period-days", type=int, default=DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS)
    parser.add_argument("--gain-top", type=int, default=DEFAULT_RESEARCH_POOL_GAIN_TOP)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-research-pool", action="store_true", help="Use existing research_pool_items without rebuilding it.")
    parser.add_argument("--skip-dependency-check", action="store_true", help="Allow snapshot generation before post-close source tables are complete.")
    parser.add_argument("--include-kpl", action="store_true", help="Also materialize the KPL featured-plate leaderboard cache.")
    parser.add_argument("--kpl-only", action="store_true", help="Only materialize the KPL featured-plate leaderboard cache.")
    parser.add_argument("--ma-mode", default="none", help="Research-pool MA mode, e.g. none or ma5_10_20_30_up.")
    parser.add_argument("--pool-mode", default="", help="Alias for --ma-mode: bear or bull.")
    parser.add_argument("--all-pool-modes", action="store_true", help="Materialize bull and bear snapshots; bear runs last to restore the default pool.")
    return parser.parse_args()


def selected_ma_modes(args: argparse.Namespace) -> list[str]:
    if args.all_pool_modes:
        return [normalize_research_pool_ma_mode("bull"), normalize_research_pool_ma_mode("bear")]
    return [normalize_research_pool_ma_mode(args.pool_mode or args.ma_mode)]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    modes = selected_ma_modes(args)
    results: dict[str, object] = {}
    for ma_mode in modes:
        pool_key = "bull" if ma_mode != "none" else "bear"
        if args.kpl_only:
            results[pool_key] = {
                "kpl": materialize_kpl_leaderboard_snapshot(config, str(args.trade_date), ma_mode=ma_mode)
            }
            continue
        result = materialize_leaderboard_snapshot(
            config,
            str(args.trade_date),
            limit_up_days=max(1, int(args.limit_up_days)),
            gain_period_days=max(1, int(args.gain_period_days)),
            gain_top=max(1, int(args.gain_top)),
            force=bool(args.force),
            rebuild_research_pool=not bool(args.skip_research_pool) or bool(args.all_pool_modes),
            check_dependencies=not bool(args.skip_dependency_check),
            ma_mode=ma_mode,
        )
        if args.include_kpl:
            result["kpl"] = materialize_kpl_leaderboard_snapshot(config, str(args.trade_date), ma_mode=ma_mode)
        results[pool_key] = result
    result = results if len(results) > 1 else next(iter(results.values()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
