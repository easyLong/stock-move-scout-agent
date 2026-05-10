from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from stock_scout_mysql import (  # noqa: E402
    MySqlConfig,
    add_mysql_args,
    load_active_anchor_member_map,
    load_theme_reason_map,
    mysql_config_from_args,
    mysql_rows,
    record_window_result,
    run_mysql,
    sql_string,
)
from windowed_stock_scout_agent import (  # noqa: E402
    aggregate_window,
    build_window_roles,
    evidence_candidate_rows,
    is_excluded_mover,
    previous_rank_map,
    safe_id,
    to_float,
)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_dt(value: str) -> datetime:
    return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")


def dt_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def read_scan_rows(config: MySqlConfig, trade_date: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      sr.run_id,
      DATE_FORMAT(sr.scanned_at, '%Y-%m-%d %H:%i:%s'),
      DATE_FORMAT(sm.captured_at, '%Y-%m-%d %H:%i:%s'),
      sm.rank_speed,
      sm.rank_pct_change,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.market')), ''),
      sm.code,
      sm.name,
      sm.price,
      sm.speed,
      sm.pct_change,
      sm.amount,
      COALESCE(sm.amount_delta_15s, ''),
      sm.volume,
      COALESCE(sm.volume_delta_15s, ''),
      sm.current_volume,
      sm.bid1,
      sm.ask1,
      sm.industry,
      sm.sub_industry,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.industry_code')), ''),
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.sub_industry_code')), ''),
      COALESCE(
        NULLIF(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.concepts')), ''),
        REPLACE(REPLACE(REPLACE(CAST(sm.concepts AS CHAR), '[', ''), ']', ''), '"', '')
      ),
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.concept_count')), '0'),
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(sm.raw_row, '$.server')), ''),
      sm.basis
    FROM scan_runs sr
    JOIN scan_movers sm ON sm.scan_run_id = sr.id
    WHERE DATE(sr.scanned_at) = {sql_string(trade_date)}
      AND sr.accepted = 1
    ORDER BY sr.scanned_at ASC, sm.rank_speed ASC;
    """
    fields = [
        "scan_run_id",
        "scanned_at",
        "captured_at",
        "rank_speed",
        "rank_pct_change",
        "market",
        "code",
        "name",
        "price",
        "speed",
        "pct_change",
        "amount",
        "amount_delta_15s",
        "vol",
        "vol_delta_15s",
        "cur_vol",
        "bid1",
        "ask1",
        "industry",
        "sub_industry",
        "industry_code",
        "sub_industry_code",
        "concepts",
        "concept_count",
        "server",
        "basis",
    ]
    return [dict(zip(fields, row)) for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True))]


def apply_signal_filter(
    rows: list[dict[str, Any]],
    min_speed_signal: float,
    min_amount_delta_15s: float,
    min_amount_delta_speed: float,
) -> list[dict[str, Any]]:
    previous_by_code: dict[str, tuple[datetime, float, float]] = {}
    filtered: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("code", ""))
        scanned_at = parse_dt(str(row.get("scanned_at")))
        amount = to_float(row.get("amount"))
        vol = to_float(row.get("vol"))
        amount_delta = to_float(row.get("amount_delta_15s"))
        vol_delta = to_float(row.get("vol_delta_15s"))
        previous = previous_by_code.get(code)
        if previous and not amount_delta:
            prev_at, prev_amount, prev_vol = previous
            if (scanned_at - prev_at).total_seconds() <= 45:
                amount_delta = max(0.0, amount - prev_amount)
                vol_delta = max(0.0, vol - prev_vol)
        previous_by_code[code] = (scanned_at, amount, vol)

        row["amount_delta_15s"] = f"{amount_delta:.2f}"
        row["vol_delta_15s"] = str(int(vol_delta))
        if is_excluded_mover(row):
            continue
        speed = to_float(row.get("speed"))
        if speed >= min_speed_signal or (amount_delta >= min_amount_delta_15s and speed > min_amount_delta_speed):
            filtered.append(row)

    return filtered


def session_windows(trade_date: str, window_seconds: int) -> list[tuple[datetime, datetime]]:
    day = datetime.strptime(trade_date, "%Y-%m-%d").date()
    sessions = [
        (clock_time(9, 30), clock_time(11, 30)),
        (clock_time(13, 0), clock_time(15, 0)),
    ]
    out: list[tuple[datetime, datetime]] = []
    for start_time, end_time in sessions:
        cursor = datetime.combine(day, start_time)
        end = datetime.combine(day, end_time)
        while cursor < end:
            bucket_end = min(cursor + timedelta(seconds=window_seconds), end)
            out.append((cursor, bucket_end))
            cursor = bucket_end
    return out


def group_samples(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("scanned_at")), []).append(row)
    for sample in grouped.values():
        sample.sort(
            key=lambda item: (
                -to_float(item.get("speed")),
                -to_float(item.get("amount_delta_15s")),
                -to_float(item.get("amount")),
            )
        )
        for idx, item in enumerate(sample, start=1):
            item["rank_speed"] = idx
    return grouped


def replace_windows_for_date(config: MySqlConfig, trade_date: str) -> None:
    run_mysql(config, f"DELETE FROM windows WHERE DATE(started_at)={sql_string(trade_date)};")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay stored MySQL scan_movers into historical window_movers.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--window-seconds", type=int, default=300)
    parser.add_argument("--scan-interval", type=int, default=15)
    parser.add_argument("--aggregate-top", type=int, default=5)
    parser.add_argument("--evidence-top", type=int, default=5)
    parser.add_argument("--min-accepted-scans", type=int, default=3)
    parser.add_argument("--min-speed-signal", type=float, default=1.0)
    parser.add_argument("--min-amount-delta-15s", type=float, default=30_000_000)
    parser.add_argument("--min-amount-delta-speed", type=float, default=0.5)
    parser.add_argument("--min-evidence-pct-change", type=float, default=0.0)
    parser.add_argument("--include-st-evidence", action="store_true")
    parser.add_argument("--replace", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    raw_rows = read_scan_rows(config, args.trade_date)
    signal_rows = apply_signal_filter(
        raw_rows,
        args.min_speed_signal,
        args.min_amount_delta_15s,
        args.min_amount_delta_speed,
    )
    grouped = group_samples(signal_rows)
    if args.replace:
        replace_windows_for_date(config, args.trade_date)

    previous_rows: list[dict[str, Any]] = []
    written = 0
    skipped = 0
    accepted_scan_total = 0
    aggregate_total = 0
    for started_at, ended_at in session_windows(args.trade_date, args.window_seconds):
        samples = [
            rows
            for scanned_at, rows in grouped.items()
            if started_at <= parse_dt(scanned_at) < ended_at
        ]
        if len(samples) < args.min_accepted_scans:
            skipped += 1
            continue
        window_id = safe_id(dt_text(started_at))
        aggregated = aggregate_window(
            samples,
            args.aggregate_top,
            dt_text(started_at),
            dt_text(ended_at),
            previous_rank_map(previous_rows),
        )
        if not aggregated:
            skipped += 1
            continue
        active_anchor_map = load_active_anchor_member_map(config, [str(row.get("code", "")) for row in aggregated])
        theme_reason_map = load_theme_reason_map(config, [str(row.get("code", "")) for row in aggregated])
        sector_stats, stock_roles = build_window_roles(aggregated, active_anchor_map, theme_reason_map)
        evidence_rows = evidence_candidate_rows(args, aggregated)[: args.evidence_top]
        meta = {
            "run_id": window_id,
            "window_started_at": dt_text(started_at),
            "window_ended_at": dt_text(ended_at),
            "scan_interval": args.scan_interval,
            "window_seconds": args.window_seconds,
            "target_scans": max(1, int(args.window_seconds / args.scan_interval)),
            "accepted_scans": len(samples),
            "min_accepted_scans": args.min_accepted_scans,
            "duration_ms": 0,
            "snapshot_top10_csv": "",
            "source": "mysql_scan_replay",
            "trade_date": args.trade_date,
            "min_speed_signal": args.min_speed_signal,
            "min_amount_delta_15s": args.min_amount_delta_15s,
            "min_amount_delta_speed": args.min_amount_delta_speed,
            "sector_stats": sector_stats,
            "stock_roles": stock_roles,
            "evidence": {"reason": "disabled"},
        }
        record_window_result(config, meta, aggregated, evidence_rows)
        previous_rows = aggregated
        written += 1
        accepted_scan_total += len(samples)
        aggregate_total += len(aggregated)
        print(
            f"[{now_text()}] replay_window {window_id} scans={len(samples)} "
            f"movers={len(aggregated)}"
        )

    print(
        f"replay_done trade_date={args.trade_date} raw_rows={len(raw_rows)} "
        f"signal_rows={len(signal_rows)} windows={written} skipped={skipped} "
        f"accepted_scans={accepted_scan_total} aggregate_rows={aggregate_total}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
