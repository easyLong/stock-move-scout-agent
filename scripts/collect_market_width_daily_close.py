#!/usr/bin/env python
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

from backfill_market_width_daily_history import build_snapshot, insert_snapshot, load_daily_rows, validate_daily_close_snapshot
from collect_market_width_snapshot import fetch_spot_frame, normalize_rows
from stock_move_scout.sources.daily_bars import (
    fetch_daily_bars_from_ak_hist,
    fetch_daily_bars_from_ak_daily,
    fetch_daily_bars_from_ak_tx,
    upsert_daily_bars,
)
from stock_move_scout.market_width import ensure_market_width_tables
from stock_scout_mysql import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_string


MAIN_A_PREFIX_REGEXP = "^(000|001|002|003|300|301|600|601|603|605|688|689)"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_stock_universe(config: Any) -> list[tuple[str, str]]:
    sql = f"""
    SELECT code, name
    FROM stocks
    WHERE code REGEXP {sql_string(MAIN_A_PREFIX_REGEXP)}
      AND COALESCE(is_st, 0)=0
      AND name NOT LIKE '%ST%'
      AND name NOT LIKE '%退市%'
    ORDER BY code ASC;
    """
    return [(row[0], row[1]) for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if len(row) >= 2]


def close_row_count(config: Any, trade_date: str) -> int:
    sql = f"""
    SELECT COUNT(*)
    FROM stock_daily_bars b
    JOIN stocks s ON s.code=b.code
    WHERE b.trade_date={sql_string(trade_date)}
      AND b.close_price IS NOT NULL
      AND b.code REGEXP {sql_string(MAIN_A_PREFIX_REGEXP)}
      AND COALESCE(s.is_st, 0)=0
      AND s.name NOT LIKE '%ST%'
      AND s.name NOT LIKE '%退市%'
      AND COALESCE(b.stock_name, '') NOT LIKE '%ST%'
      AND COALESCE(b.stock_name, '') NOT LIKE '%退市%';
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    try:
        return int(rows[0][0]) if rows and rows[0] else 0
    except Exception:
        return 0


def load_previous_close_map(config: Any, trade_date: str) -> dict[str, float]:
    sql = f"""
    SELECT b.code, b.close_price
    FROM stock_daily_bars b
    JOIN (
      SELECT code, MAX(trade_date) AS trade_date
      FROM stock_daily_bars
      WHERE trade_date < {sql_string(trade_date)}
        AND close_price IS NOT NULL
      GROUP BY code
    ) p ON p.code=b.code AND p.trade_date=b.trade_date;
    """
    result: dict[str, float] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 2:
            continue
        try:
            result[str(row[0]).strip()] = float(row[1])
        except Exception:
            pass
    return result


def enrich_pct_change(rows: list[dict[str, Any]], previous_close: dict[str, float]) -> list[dict[str, Any]]:
    for row in rows:
        try:
            pct = float(row.get("pct_change") or 0)
            close_price = float(row.get("close_price") or 0)
            prev_close = float(previous_close.get(str(row.get("code") or "").strip()) or 0)
        except Exception:
            continue
        if abs(pct) < 0.000001 and close_price > 0 and prev_close > 0:
            row["pct_change"] = round((close_price / prev_close - 1) * 100, 4)
    return rows


def fetch_one_daily_bar(item: tuple[str, str], trade_date: str) -> tuple[str, list[dict[str, Any]], str]:
    code, name = item
    try:
        rows = fetch_daily_bars_from_ak_hist(code, name, trade_date, trade_date)
        if rows:
            return code, rows, ""
    except Exception as exc:
        hist_error = f"hist:{type(exc).__name__}:{str(exc)[:160]}"
    else:
        hist_error = "hist:empty"
    try:
        rows = fetch_daily_bars_from_ak_tx(code, name, trade_date, trade_date)
        if rows:
            return code, rows, ""
    except Exception as exc:
        tx_error = f"tx:{type(exc).__name__}:{str(exc)[:160]}"
    else:
        tx_error = "tx:empty"
    return code, [], hist_error + "; " + tx_error + "; daily:skipped_no_timeout"


def refresh_daily_bars(config: Any, trade_date: str, *, workers: int, batch_size: int) -> dict[str, Any]:
    stocks = load_stock_universe(config)
    previous_close = load_previous_close_map(config, trade_date)
    ok_codes = 0
    empty_codes = 0
    failed: list[tuple[str, str]] = []
    buffer: list[dict[str, Any]] = []
    written = 0
    started = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        futures = [executor.submit(fetch_one_daily_bar, item, trade_date) for item in stocks]
        for fut in cf.as_completed(futures):
            code, rows, error = fut.result()
            if rows:
                ok_codes += 1
                buffer.extend(enrich_pct_change(rows, previous_close))
            else:
                empty_codes += 1
                if len(failed) < 30:
                    failed.append((code, error))
            if len(buffer) >= int(batch_size):
                written += upsert_daily_bars(config, buffer)
                buffer.clear()
    if buffer:
        written += upsert_daily_bars(config, buffer)
    return {
        "stocks": len(stocks),
        "ok_codes": ok_codes,
        "empty_codes": empty_codes,
        "written_rows": written,
        "failed_sample": failed,
        "elapsed_seconds": round(time.time() - started, 1),
    }


def refresh_daily_bars_from_spot(config: Any, trade_date: str) -> dict[str, Any]:
    source, df = fetch_spot_frame("auto")
    rows: list[dict[str, Any]] = []
    for row in normalize_rows(df, include_bj=False, include_st=False):
        latest = row.get("latest_price")
        try:
            latest_value = float(latest or 0)
        except Exception:
            latest_value = 0.0
        if latest_value <= 0 or not math.isfinite(latest_value):
            continue
        rows.append(
            {
                "code": row["code"],
                "trade_date": trade_date,
                "stock_name": row.get("name") or "",
                "open_price": latest_value,
                "high_price": latest_value,
                "low_price": latest_value,
                "close_price": latest_value,
                "pct_change": row.get("pct_change"),
                "volume": row.get("volume"),
                "amount": row.get("amount"),
                "source": "akshare_stock_zh_a_spot_em_close",
                "raw_json": {"source": source, "snapshot_kind": "daily_close_spot", "raw_row": row.get("raw_row")},
            }
        )
    written = upsert_daily_bars(config, rows)
    return {"source": source, "stocks": len(rows), "written_rows": written}


def try_refresh_daily_bars_from_spot(config: Any, trade_date: str) -> dict[str, Any]:
    try:
        return refresh_daily_bars_from_spot(config, trade_date)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


def build_daily_close_snapshot(config: Any, trade_date: str, *, min_rows: int) -> dict[str, Any]:
    rows = load_daily_rows(config, trade_date)
    if len(rows) < int(min_rows):
        return {"ok": False, "reason": "insufficient_daily_bars", "trade_date": trade_date, "rows": len(rows)}
    snapshot, top50 = build_snapshot(config, trade_date, rows)
    missing_index_fields = validate_daily_close_snapshot(snapshot)
    if missing_index_fields:
        return {
            "ok": False,
            "reason": "missing_shanghai_index_fields",
            "trade_date": trade_date,
            "rows": len(rows),
            "missing_fields": missing_index_fields,
            "shanghai_index_source": (snapshot.get("raw_meta") or {}).get("shanghai_index", {}).get("source"),
        }
    insert_snapshot(config, snapshot, top50)
    return {
        "ok": True,
        "trade_date": trade_date,
        "snapshot_id": snapshot["snapshot_id"],
        "rows": len(rows),
        "up_count": snapshot["up_count"],
        "down_count": snapshot["down_count"],
        "up5_count": snapshot["up5_count"],
        "down5_count": snapshot["down5_count"],
        "limit_up_count": snapshot["limit_up_count"],
        "limit_down_count": snapshot["limit_down_count"],
        "shanghai_index_source": (snapshot.get("raw_meta") or {}).get("shanghai_index", {}).get("source"),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect daily bars and build confirmed daily-close market width snapshot.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--min-rows", type=int, default=4800)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=900)
    parser.add_argument("--wait-minutes", type=int, default=20)
    parser.add_argument("--retry-seconds", type=int, default=120)
    parser.add_argument("--no-refresh-bars", action="store_true")
    parser.add_argument("--output-json", type=Path, default=project_root() / "runs" / "data_tasks" / "market_width_daily_close.json")
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
    ensure_market_width_tables(config)

    deadline = time.monotonic() + max(0, int(args.wait_minutes)) * 60
    attempts: list[dict[str, Any]] = []
    result: dict[str, Any] = {}
    while True:
        before_rows = close_row_count(config, args.trade_date)
        fetch_result: dict[str, Any] = {"skipped": True, "reason": "refresh_disabled_or_already_complete"}
        if not args.no_refresh_bars and before_rows < int(args.min_rows):
            spot_result = try_refresh_daily_bars_from_spot(config, args.trade_date)
            after_spot_rows = close_row_count(config, args.trade_date)
            if after_spot_rows < int(args.min_rows):
                per_stock_result = refresh_daily_bars(config, args.trade_date, workers=args.workers, batch_size=args.batch_size)
            else:
                per_stock_result = {"skipped": True, "reason": "spot_rows_sufficient"}
            fetch_result = {"spot": spot_result, "per_stock": per_stock_result}
        after_rows = close_row_count(config, args.trade_date)
        result = build_daily_close_snapshot(
            config,
            args.trade_date,
            min_rows=args.min_rows,
        )
        attempts.append({"at": now_text(), "before_rows": before_rows, "after_rows": after_rows, "fetch": fetch_result, "result": result})
        if result.get("ok") or time.monotonic() >= deadline:
            break
        time.sleep(max(10, int(args.retry_seconds)))

    payload = {"ok": bool(result.get("ok")), "generated_at": now_text(), "trade_date": args.trade_date, "attempts": attempts, "result": result}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
