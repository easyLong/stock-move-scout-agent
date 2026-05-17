#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.evidence.effective_facts import build_effective_facts
from stock_move_scout.research_pool import ResearchPoolProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Build same-day effective facts from raw and derived stock facts.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--code", default="")
    parser.add_argument("--research-pool-only", dest="research_pool_only", action="store_true", help="Only build facts for active research pool stocks.")
    args = parser.parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    explicit_code = str(args.code or "").strip()
    codes = []
    if args.research_pool_only and not explicit_code:
        codes = ResearchPoolProvider(config).latest_codes(str(args.trade_date))
        if not codes:
            print(json.dumps({"trade_date": str(args.trade_date), "codes": 0, "groups": []}, ensure_ascii=False, indent=2))
            return 0
    if explicit_code:
        result = build_effective_facts(config, str(args.trade_date), explicit_code)
        result["codes"] = 1
    elif codes:
        result = build_effective_facts(config, str(args.trade_date), "", codes=codes)
        result["codes"] = len(codes)
    else:
        result = build_effective_facts(config, str(args.trade_date), "")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
