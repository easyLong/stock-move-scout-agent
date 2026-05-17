from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_string
from stock_move_scout.research_pool import ResearchPoolProvider


def mysql_config(args: argparse.Namespace) -> MySqlConfig:
    return MySqlConfig(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        timeout=args.mysql_timeout,
    )


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "NULL"):
            return default
        return float(value)
    except Exception:
        return default


def load_snapshot_times(config: MySqlConfig, trade_date: str) -> list[tuple[str, datetime]]:
    sql = f"""
    SELECT snapshot_id, DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s')
    FROM market_width_snapshots
    WHERE trade_date={sql_string(trade_date)}
      AND ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00.999')
        OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00.999'))
    ORDER BY captured_at ASC;
    """
    out: list[tuple[str, datetime]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) >= 2 and row[0] and row[1]:
            out.append((str(row[0]), datetime.strptime(str(row[1]), "%Y-%m-%d %H:%M:%S")))
    return out


def load_previous_closes(config: MySqlConfig, trade_date: str, codes: list[str]) -> dict[str, float]:
    if not codes:
        return {}
    code_list = ", ".join(sql_string(code) for code in codes)
    sql = f"""
    SELECT b.code, b.close_price
    FROM stock_daily_bars b
    JOIN (
      SELECT code, MAX(trade_date) AS prev_trade_date
      FROM stock_daily_bars
      WHERE trade_date < {sql_string(trade_date)}
        AND code IN ({code_list})
        AND close_price IS NOT NULL
      GROUP BY code
    ) p ON p.code=b.code AND p.prev_trade_date=b.trade_date
    WHERE b.close_price IS NOT NULL;
    """
    return {
        str(row[0]).strip(): to_float(row[1])
        for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True))
        if len(row) >= 2 and str(row[0]).strip() and to_float(row[1]) > 0
    }


def market_symbol(code: str) -> str:
    return ("sh" if str(code).startswith("6") else "sz") + str(code)


def fetch_minute_pct_series(
    code: str,
    trade_date: str,
    prev_close: float,
    *,
    provider: str = "sina",
) -> tuple[str, list[tuple[datetime, float]], str]:
    if prev_close <= 0:
        return code, [], "missing_prev_close"
    start = f"{trade_date} 09:30:00"
    end = f"{trade_date} 15:00:00"
    try:
        if provider == "eastmoney":
            df = ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period="1", adjust="")
        else:
            df = ak.stock_zh_a_minute(symbol=market_symbol(code), period="1", adjust="")
    except Exception as exc:
        return code, [], f"{type(exc).__name__}: {exc}"
    if df is None or df.empty:
        return code, [], "empty"
    if provider == "eastmoney":
        time_column = "时间"
        close_column = "收盘"
    else:
        time_column = "day"
        close_column = "close"
    if time_column not in df.columns or close_column not in df.columns:
        return code, [], "missing_columns"
    rows: list[tuple[datetime, float]] = []
    for _, item in df.iterrows():
        when = str(item.get(time_column) or "")
        if not when.startswith(trade_date):
            continue
        close_price = to_float(item.get(close_column))
        if not when or close_price <= 0:
            continue
        try:
            ts = datetime.strptime(when[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        rows.append((ts, (close_price / prev_close - 1) * 100))
    return code, rows, "" if rows else "no_valid_rows"


def pct_at(series: list[tuple[datetime, float]], target: datetime) -> float | None:
    latest: float | None = None
    for ts, pct in series:
        if ts > target:
            if latest is None and ts <= target + timedelta(seconds=70):
                return pct
            break
        latest = pct
    return latest


def stats_for_values(values: list[float]) -> dict[str, int]:
    return {
        "count": len(values),
        "up": sum(1 for value in values if value > 0),
        "down": sum(1 for value in values if value < 0),
        "flat": sum(1 for value in values if value == 0),
        "up3": sum(1 for value in values if value >= 3),
        "down3": sum(1 for value in values if value <= -3),
        "up5": sum(1 for value in values if value > 5),
        "down5": sum(1 for value in values if value < -5),
    }


def update_snapshots(config: MySqlConfig, snapshot_stats: dict[str, dict[str, int]], *, dry_run: bool) -> None:
    if dry_run:
        return
    statements: list[str] = []
    for snapshot_id, stats in snapshot_stats.items():
        statements.append(
            f"""
            UPDATE market_width_snapshots
            SET research_pool_count={sql_int(stats['count'])},
                research_pool_up_count={sql_int(stats['up'])},
                research_pool_down_count={sql_int(stats['down'])},
                research_pool_flat_count={sql_int(stats['flat'])},
                research_pool_up3_count={sql_int(stats['up3'])},
                research_pool_down3_count={sql_int(stats['down3'])},
                research_pool_up5_count={sql_int(stats['up5'])},
                research_pool_down5_count={sql_int(stats['down5'])}
            WHERE snapshot_id={sql_string(snapshot_id)};
            """
        )
    for start in range(0, len(statements), 50):
        run_mysql(config, "\n".join(statements[start : start + 50]), raw=True)


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = mysql_config(args)
    trade_date = args.trade_date
    snapshot = ResearchPoolProvider(config).latest_snapshot(trade_date)
    codes = list(snapshot.codes)
    snapshot_times = load_snapshot_times(config, trade_date)
    previous_closes = load_previous_closes(config, trade_date, codes)
    print(f"research_pool={snapshot.code_count} snapshot_times={len(snapshot_times)} prev_closes={len(previous_closes)}")
    minute_series: dict[str, list[tuple[datetime, float]]] = {}
    errors: dict[str, str] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as executor:
        futures = {
            executor.submit(
                fetch_minute_pct_series,
                code,
                trade_date,
                previous_closes.get(code, 0.0),
                provider=args.provider,
            ): code
            for code in codes
        }
        for index, future in enumerate(as_completed(futures), start=1):
            code, rows, error = future.result()
            if rows:
                minute_series[code] = rows
            if error:
                errors[code] = error
            if index % 25 == 0:
                print(f"fetched {index}/{len(futures)} ok={len(minute_series)} errors={len(errors)}")
    snapshot_stats: dict[str, dict[str, int]] = {}
    for snapshot_id, captured_at in snapshot_times:
        values = [value for code, series in minute_series.items() if (value := pct_at(series, captured_at)) is not None]
        snapshot_stats[snapshot_id] = stats_for_values(values)
    update_snapshots(config, snapshot_stats, dry_run=bool(args.dry_run))
    first = snapshot_stats.get(snapshot_times[0][0], {}) if snapshot_times else {}
    last = snapshot_stats.get(snapshot_times[-1][0], {}) if snapshot_times else {}
    result = {
        "trade_date": trade_date,
        "research_pool_trade_date": snapshot.trade_date,
        "research_pool_count": snapshot.code_count,
        "minute_series_count": len(minute_series),
        "error_count": len(errors),
        "snapshot_count": len(snapshot_stats),
        "first_stats": first,
        "last_stats": last,
        "elapsed_seconds": round(time.time() - started, 2),
        "dry_run": bool(args.dry_run),
    }
    print(result)
    if errors:
        sample = dict(list(errors.items())[:10])
        print({"sample_errors": sample})
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild market-width research-pool intraday lines from 1-minute bars.")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--provider", choices=("sina", "eastmoney"), default="sina")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mysql-host", default="127.0.0.1")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="root")
    parser.add_argument("--mysql-password", default="")
    parser.add_argument("--mysql-database", default="stock_scout")
    parser.add_argument("--mysql-timeout", type=int, default=120)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(0 if run(parse_args()) else 1)
