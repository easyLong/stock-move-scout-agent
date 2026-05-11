#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import MySqlConfig, add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string


def scalar(config: MySqlConfig, sql: str) -> list[str]:
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    return rows[0] if rows else []


def rows(config: MySqlConfig, sql: str) -> list[list[str]]:
    return mysql_rows(run_mysql(config, sql, batch=True, raw=True))


def audit(config: MySqlConfig, trade_date: str, code: str = "") -> dict[str, object]:
    day = sql_string(trade_date)
    code_filter_e = f"AND e.code={sql_string(code)}" if code else ""
    code_filter_ev = f"AND code={sql_string(code)}" if code else ""
    overall = scalar(
        config,
        f"""
        SELECT COUNT(DISTINCT e.event_id),
               COUNT(ev.id),
               ROUND(COUNT(ev.id)/NULLIF(COUNT(DISTINCT e.event_id), 0), 2),
               COUNT(DISTINCT e.code)
        FROM stock_move_events e
        LEFT JOIN stock_move_evidence ev ON ev.event_id=e.event_id
        WHERE e.trade_date={day}
          {code_filter_e};
        """,
    )
    missing = scalar(
        config,
        f"""
        SELECT SUM(has_trigger=0),
               SUM(has_structure=0),
               SUM(has_confirm=0),
               SUM(has_any_hard=0)
        FROM (
          SELECT e.event_id,
                 MAX(ev.evidence_role='trigger') has_trigger,
                 MAX(ev.evidence_role='structure') has_structure,
                 MAX(ev.evidence_role='confirmation') has_confirm,
                 MAX(ev.evidence_group IN ('current_effective','post_close_confirm')) has_any_hard
          FROM stock_move_events e
          LEFT JOIN stock_move_evidence ev ON ev.event_id=e.event_id
          WHERE e.trade_date={day}
            {code_filter_e}
          GROUP BY e.event_id
        ) x;
        """,
    )
    duplicates = rows(
        config,
        f"""
        SELECT evidence_type, COUNT(*)
        FROM (
          SELECT event_id, evidence_type, COUNT(*) c
          FROM stock_move_evidence
          WHERE trade_date={day}
            AND evidence_group IN ('current_effective','post_close_confirm')
            {code_filter_ev}
          GROUP BY event_id, evidence_type
          HAVING c > 1
        ) x
        GROUP BY evidence_type
        ORDER BY COUNT(*) DESC;
        """,
    )
    group_rows = rows(
        config,
        f"""
        SELECT evidence_group, evidence_role, COUNT(*), COUNT(DISTINCT event_id)
        FROM stock_move_evidence
        WHERE trade_date={day}
          {code_filter_ev}
        GROUP BY evidence_group, evidence_role
        ORDER BY FIELD(evidence_group,'current_effective','post_close_confirm','background_fact','historical_tag'), evidence_role;
        """,
    )
    signal_scores = rows(
        config,
        f"""
        SELECT signal_type, ROUND(MIN(signal_score),2), ROUND(MAX(signal_score),2), ROUND(AVG(signal_score),2)
        FROM derived_signals
        WHERE trade_date={day}
          {code_filter_ev}
        GROUP BY signal_type
        ORDER BY signal_type;
        """,
    )
    return {
        "trade_date": trade_date,
        "code": code,
        "overall": {
            "events": int(overall[0]) if len(overall) > 0 and overall[0] else 0,
            "evidence_rows": int(overall[1]) if len(overall) > 1 and overall[1] else 0,
            "avg_evidence_per_event": float(overall[2]) if len(overall) > 2 and overall[2] else 0,
            "codes": int(overall[3]) if len(overall) > 3 and overall[3] else 0,
        },
        "missing_core": {
            "no_trigger": int(missing[0]) if len(missing) > 0 and missing[0] else 0,
            "no_structure": int(missing[1]) if len(missing) > 1 and missing[1] else 0,
            "no_confirmation": int(missing[2]) if len(missing) > 2 and missing[2] else 0,
            "no_current_or_post": int(missing[3]) if len(missing) > 3 and missing[3] else 0,
        },
        "duplicate_current_or_post_by_type": [{"evidence_type": row[0], "events": int(row[1])} for row in duplicates],
        "by_group_role": [
            {"evidence_group": row[0], "evidence_role": row[1], "rows": int(row[2]), "events": int(row[3])}
            for row in group_rows
            if len(row) >= 4
        ],
        "signal_scores": [
            {"signal_type": row[0], "min": float(row[1]), "max": float(row[2]), "avg": float(row[3])}
            for row in signal_scores
            if len(row) >= 4
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit event-level evidence quality.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--code", default="")
    args = parser.parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    result = audit(mysql_config_from_args(args), str(args.trade_date), str(args.code or "").strip())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
