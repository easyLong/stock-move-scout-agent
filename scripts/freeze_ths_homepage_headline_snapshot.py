#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string


SOURCE = "ths_homepage_headline"
FROZEN_SOURCE = "ths_homepage_headline_frozen"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze a THS homepage headline theme snapshot for post-close use.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--source-trade-date", default="")
    parser.add_argument("--allow-carry-forward", action="store_true")
    parser.add_argument("--fail-on-empty", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def latest_source_snapshot(config: object, trade_date: str, *, allow_carry_forward: bool) -> dict[str, str]:
    predicate = (
        f"trade_date <= {sql_string(trade_date)}"
        if allow_carry_forward
        else f"trade_date = {sql_string(trade_date)}"
    )
    output = run_mysql(
        config,
        f"""
        SELECT snapshot_id, DATE_FORMAT(trade_date, '%Y-%m-%d'), DATE_FORMAT(MAX(collected_at), '%Y-%m-%d %H:%i:%s')
        FROM ths_homepage_headline_themes
        WHERE {predicate}
          AND source={sql_string(SOURCE)}
        GROUP BY snapshot_id, trade_date
        ORDER BY trade_date DESC, MAX(collected_at) DESC
        LIMIT 1;
        """,
        batch=True,
        raw=True,
    )
    rows = mysql_rows(output)
    if not rows:
        return {}
    return {"snapshot_id": rows[0][0], "source_trade_date": rows[0][1], "source_collected_at": rows[0][2]}


def freeze_snapshot(config: object, trade_date: str, source_snapshot: dict[str, str]) -> dict[str, object]:
    source_snapshot_id = source_snapshot["snapshot_id"]
    frozen_snapshot_id = f"frz{trade_date.replace('-', '')}{datetime.now().strftime('%H%M%S')}"
    sql = f"""
    START TRANSACTION;
    DELETE FROM ths_homepage_headline_theme_members
    WHERE trade_date={sql_string(trade_date)}
      AND source={sql_string(FROZEN_SOURCE)};
    DELETE FROM ths_homepage_headline_themes
    WHERE trade_date={sql_string(trade_date)}
      AND source={sql_string(FROZEN_SOURCE)};

    INSERT INTO ths_homepage_headline_themes(
      trade_date, snapshot_id, rank_no, theme_id, theme_name, theme_url,
      index_code, block_name, block_gain, source, page_url, raw_json, collected_at
    )
    SELECT
      {sql_string(trade_date)}, {sql_string(frozen_snapshot_id)}, rank_no, theme_id, theme_name, theme_url,
      index_code, block_name, block_gain, {sql_string(FROZEN_SOURCE)}, page_url, raw_json, NOW(3)
    FROM ths_homepage_headline_themes
    WHERE snapshot_id={sql_string(source_snapshot_id)}
      AND source={sql_string(SOURCE)};

    INSERT INTO ths_homepage_headline_theme_members(
      trade_date, snapshot_id, theme_rank, theme_id, theme_name, index_code, block_name,
      stock_rank, stock_code, stock_name, stock_market_id, gain, source, raw_json, collected_at
    )
    SELECT
      {sql_string(trade_date)}, {sql_string(frozen_snapshot_id)}, theme_rank, theme_id, theme_name, index_code, block_name,
      stock_rank, stock_code, stock_name, stock_market_id, gain, {sql_string(FROZEN_SOURCE)}, raw_json, NOW(3)
    FROM ths_homepage_headline_theme_members
    WHERE snapshot_id={sql_string(source_snapshot_id)}
      AND source={sql_string(SOURCE)};
    COMMIT;
    """
    run_mysql(config, sql)
    counts = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT
              (SELECT COUNT(*) FROM ths_homepage_headline_themes WHERE snapshot_id={sql_string(frozen_snapshot_id)}),
              (SELECT COUNT(*) FROM ths_homepage_headline_theme_members WHERE snapshot_id={sql_string(frozen_snapshot_id)});
            """,
            batch=True,
            raw=True,
        )
    )
    theme_count = int(counts[0][0] or 0) if counts else 0
    member_count = int(counts[0][1] or 0) if counts else 0
    return {
        "trade_date": trade_date,
        "source": FROZEN_SOURCE,
        "snapshot_id": frozen_snapshot_id,
        "source_snapshot_id": source_snapshot_id,
        "source_trade_date": source_snapshot.get("source_trade_date", ""),
        "source_collected_at": source_snapshot.get("source_collected_at", ""),
        "theme_count": theme_count,
        "member_count": member_count,
    }


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    trade_date = str(args.trade_date)
    source_trade_date = str(args.source_trade_date or trade_date)
    source_snapshot = latest_source_snapshot(config, source_trade_date, allow_carry_forward=bool(args.allow_carry_forward))
    if not source_snapshot:
        result = {"trade_date": trade_date, "source": FROZEN_SOURCE, "generated": False, "reason": "source_snapshot_missing"}
        print(json.dumps(result, ensure_ascii=False))
        return 1 if args.fail_on_empty else 0
    result = freeze_snapshot(config, trade_date, source_snapshot)
    result["generated"] = bool(result["theme_count"])
    print(json.dumps(result, ensure_ascii=False))
    return 1 if (args.fail_on_empty and not result["theme_count"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
