#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import add_mysql_args, mysql_config_from_args
from stock_move_scout.research_pool import materialize_headline_theme_role_evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build precomputed headline-theme role evidence for evidence detail.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--force", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    result = materialize_headline_theme_role_evidence(
        mysql_config_from_args(args),
        str(args.trade_date),
        force=bool(args.force),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
