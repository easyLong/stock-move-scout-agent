#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from datetime import date, datetime
from pathlib import Path
from typing import Any

import akshare as ak

from stock_scout_mysql import (
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)
from stock_move_scout.market_width import ensure_market_width_tables
from stock_move_scout.research_pool import ResearchPoolProvider


MAIN_A_PREFIX_REGEXP = "^(000|001|002|003|300|301|600|601|603|605|688|689)"
REQUIRED_SH_INDEX_FIELDS = ("price", "pct_change", "amount", "volume")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value or "").replace(",", "").replace("%", "").strip()
        return float(text) if text else default
    except Exception:
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "").strip()))
    except Exception:
        return default


def is_excluded_stock_name(name: Any) -> bool:
    text = str(name or "").strip()
    upper = text.upper()
    return "ST" in upper or "退" in text


def limit_threshold(code: Any) -> float:
    text = str(code or "").strip()
    if text.startswith(("300", "301", "688", "689")):
        return 19.5
    return 9.8


def is_limit_up_row(row: dict[str, Any]) -> bool:
    if is_excluded_stock_name(row.get("name")):
        return False
    return to_float(row.get("pct_change")) >= limit_threshold(row.get("code"))


def is_limit_down_row(row: dict[str, Any]) -> bool:
    if is_excluded_stock_name(row.get("name")):
        return False
    return to_float(row.get("pct_change")) <= -limit_threshold(row.get("code"))


def first_existing(columns: Any, candidates: list[str]) -> str | None:
    names = {str(name).strip(): name for name in columns}
    for candidate in candidates:
        if candidate in names:
            return names[candidate]
    return None


def missing_shanghai_index_fields(sh_index: dict[str, Any] | None) -> list[str]:
    if not sh_index:
        return list(REQUIRED_SH_INDEX_FIELDS)
    missing: list[str] = []
    for field in REQUIRED_SH_INDEX_FIELDS:
        value = sh_index.get(field)
        if value is None or value == "":
            missing.append(field)
            continue
        try:
            numeric = float(value)
        except Exception:
            missing.append(field)
            continue
        if field != "pct_change" and numeric <= 0:
            missing.append(field)
    return missing


@lru_cache(maxsize=1)
def load_shanghai_index_daily() -> dict[str, dict[str, Any]]:
    today = date.today().strftime("%Y%m%d")
    try:
        df = ak.index_zh_a_hist(symbol="000001", period="daily", start_date="19900101", end_date=today)
    except Exception:
        df = None
    if df is not None and not df.empty:
        date_col = first_existing(df.columns, ["日期", "date"])
        close_col = first_existing(df.columns, ["收盘", "close"])
        pct_col = first_existing(df.columns, ["涨跌幅", "pct_change", "涨跌幅%"])
        amount_col = first_existing(df.columns, ["成交额", "amount"])
        volume_col = first_existing(df.columns, ["成交量", "volume"])
    else:
        date_col = close_col = pct_col = amount_col = volume_col = None
    if df is not None and not df.empty and date_col and close_col:
        result: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            trade_day = str(row.get(date_col) or "").strip()
            if not trade_day:
                continue
            result[trade_day] = {
                "price": to_float(row.get(close_col)),
                "pct_change": to_float(row.get(pct_col), None) if pct_col else None,
                "amount": to_float(row.get(amount_col), None) if amount_col else None,
                "volume": to_int(row.get(volume_col)) * 100 if volume_col else None,
                "source": "ak.index_zh_a_hist",
            }
        if result and any(not missing_shanghai_index_fields(item) for item in result.values()):
            return result

    try:
        df = ak.stock_zh_index_daily(symbol="sh000001")
    except Exception:
        return {}
    if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
        return {}
    frame = df.copy()
    frame["date"] = frame["date"].astype(str)
    frame = frame.sort_values("date")
    result: dict[str, dict[str, Any]] = {}
    prev_close: float | None = None
    for _, row in frame.iterrows():
        trade_day = str(row.get("date") or "").strip()
        close = to_float(row.get("close"))
        pct_change = ((close / prev_close - 1) * 100) if prev_close else None
        result[trade_day] = {
            "price": close,
            "pct_change": pct_change,
            "amount": None,
            "volume": to_int(row.get("volume")),
            "source": "ak.stock_zh_index_daily",
        }
        if close:
            prev_close = close
    return result


def shanghai_index_for_day(trade_day: str) -> dict[str, Any] | None:
    return load_shanghai_index_daily().get(trade_day)


def cached_shanghai_index_for_day(config: Any, trade_day: str) -> dict[str, Any] | None:
    sql = f"""
    SELECT sh_index_price, sh_index_pct_change, sh_index_amount, sh_index_volume, source, snapshot_id
    FROM market_width_snapshots
    WHERE trade_date={sql_string(trade_day)}
      AND sh_index_price IS NOT NULL
      AND sh_index_pct_change IS NOT NULL
      AND sh_index_amount IS NOT NULL
      AND sh_index_volume IS NOT NULL
    ORDER BY IF(snapshot_id={sql_string("daily_close_" + trade_day.replace("-", ""))}, 0, 1),
             captured_at DESC
    LIMIT 1;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    if not rows:
        return None
    row = rows[0]
    if len(row) < 4:
        return None
    return {
        "price": to_float(row[0], None),
        "pct_change": to_float(row[1], None),
        "amount": to_float(row[2], None),
        "volume": to_int(row[3]),
        "source": f"market_width_snapshots:{row[4] if len(row) > 4 else ''}:{row[5] if len(row) > 5 else ''}",
    }


def trade_dates(config: Any, start_date: str, end_date: str, limit: int) -> list[str]:
    range_filter = ""
    if start_date:
        range_filter += f" AND trade_date >= {sql_string(start_date)}"
    if end_date:
        range_filter += f" AND trade_date <= {sql_string(end_date)}"
    sql = f"""
    SELECT DATE_FORMAT(trade_date, '%Y-%m-%d')
    FROM (
      SELECT trade_date, COUNT(*) AS row_count
      FROM stock_daily_bars
      WHERE close_price IS NOT NULL
        {range_filter}
      GROUP BY trade_date
      ORDER BY trade_date DESC
      LIMIT {max(1, int(limit))}
    ) recent_days
    ORDER BY trade_date ASC;
    """
    return [row[0] for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if row and row[0]]


def load_research_pool_codes(config: Any, trade_day: str) -> tuple[str, list[str], dict[str, Any]]:
    snapshot = ResearchPoolProvider(config).latest_snapshot(trade_day)
    return snapshot.trade_date, list(snapshot.codes), {
        "rule": snapshot.rule,
        "code_count": snapshot.code_count,
        "source_dates": snapshot.source_dates,
        "codes_by_source_count": {key: len(values) for key, values in snapshot.codes_by_source.items()},
    }


def load_daily_rows(config: Any, trade_day: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      b.code,
      COALESCE(NULLIF(b.stock_name, ''), s.name, '') AS stock_name,
      b.close_price,
      COALESCE(
        b.pct_change,
        IF(prev.close_price IS NULL OR prev.close_price = 0, NULL, (b.close_price / prev.close_price - 1) * 100)
      ) AS pct_change,
      b.amount,
      b.volume
    FROM stock_daily_bars b
    JOIN stocks s ON s.code=b.code
    LEFT JOIN stock_daily_bars prev
      ON prev.code=b.code
     AND prev.trade_date=(
       SELECT MAX(p.trade_date)
       FROM stock_daily_bars p
       WHERE p.code=b.code
         AND p.trade_date < b.trade_date
         AND p.close_price IS NOT NULL
     )
    WHERE b.trade_date={sql_string(trade_day)}
      AND b.close_price IS NOT NULL
      AND b.code REGEXP {sql_string(MAIN_A_PREFIX_REGEXP)}
      AND COALESCE(s.is_st, 0)=0
      AND s.name NOT LIKE '%ST%'
      AND s.name NOT LIKE '%退市%'
      AND COALESCE(b.stock_name, '') NOT LIKE '%ST%'
      AND COALESCE(b.stock_name, '') NOT LIKE '%退市%'
    ORDER BY b.code ASC;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 6:
            continue
        pct = None if row[3] in ("", "NULL", None) else to_float(row[3])
        if pct is None:
            continue
        rows.append(
            {
                "code": row[0],
                "name": row[1],
                "latest_price": to_float(row[2]),
                "pct_change": pct,
                "amount": to_float(row[4]),
                "volume": to_int(row[5]),
            }
        )
    return rows


def width_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "count": len(rows),
        "up_count": sum(1 for row in rows if to_float(row.get("pct_change")) > 0),
        "down_count": sum(1 for row in rows if to_float(row.get("pct_change")) < 0),
        "flat_count": sum(1 for row in rows if to_float(row.get("pct_change")) == 0),
        "up3_count": sum(1 for row in rows if to_float(row.get("pct_change")) >= 3),
        "down3_count": sum(1 for row in rows if to_float(row.get("pct_change")) <= -3),
        "up5_count": sum(1 for row in rows if to_float(row.get("pct_change")) > 5),
        "down5_count": sum(1 for row in rows if to_float(row.get("pct_change")) < -5),
    }


def build_snapshot(config: Any, trade_day: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_code = {row["code"]: row for row in rows}
    top50 = sorted(rows, key=lambda item: to_float(item.get("amount")), reverse=True)[:50]
    for rank_no, row in enumerate(top50, start=1):
        row["rank_no"] = rank_no
    market = width_stats(rows)
    amount_top50 = width_stats(top50)
    research_pool_trade_date, research_pool_codes, research_pool_meta = load_research_pool_codes(config, trade_day)
    research_pool_rows = [by_code[code] for code in research_pool_codes if code in by_code]
    research_pool_stats = width_stats(research_pool_rows)
    sh_index = shanghai_index_for_day(trade_day) or {}
    if missing_shanghai_index_fields(sh_index):
        cached_sh_index = cached_shanghai_index_for_day(config, trade_day) or {}
        if len(missing_shanghai_index_fields(cached_sh_index)) < len(missing_shanghai_index_fields(sh_index)):
            sh_index = cached_sh_index
    sh_index_missing = missing_shanghai_index_fields(sh_index)
    return (
        {
            "snapshot_id": "daily_close_" + trade_day.replace("-", ""),
            "trade_date": trade_day,
            "captured_at": f"{trade_day} 15:00:00.000",
            "source": "stock_daily_bars_close",
            "market_scope": "cn_a_main",
            "total_count": market["count"],
            "up_count": market["up_count"],
            "down_count": market["down_count"],
            "flat_count": market["flat_count"],
            "up3_count": market["up3_count"],
            "down3_count": market["down3_count"],
            "up5_count": market["up5_count"],
            "down5_count": market["down5_count"],
            "limit_up_count": sum(1 for row in rows if is_limit_up_row(row)),
            "limit_down_count": sum(1 for row in rows if is_limit_down_row(row)),
            "amount_top50_count": amount_top50["count"],
            "amount_top50_up_count": amount_top50["up_count"],
            "amount_top50_down_count": amount_top50["down_count"],
            "amount_top50_flat_count": amount_top50["flat_count"],
            "amount_top50_up3_count": amount_top50["up3_count"],
            "amount_top50_down3_count": amount_top50["down3_count"],
            "amount_top50_up5_count": amount_top50["up5_count"],
            "amount_top50_down5_count": amount_top50["down5_count"],
            "research_pool_trade_date": research_pool_trade_date,
            "research_pool_rule": str((research_pool_meta or {}).get("rule") or ""),
            "research_pool_count": len(research_pool_codes),
            "research_pool_up_count": research_pool_stats["up_count"],
            "research_pool_down_count": research_pool_stats["down_count"],
            "research_pool_flat_count": research_pool_stats["flat_count"],
            "research_pool_up3_count": research_pool_stats["up3_count"],
            "research_pool_down3_count": research_pool_stats["down3_count"],
            "research_pool_up5_count": research_pool_stats["up5_count"],
            "research_pool_down5_count": research_pool_stats["down5_count"],
            "sh_index_price": sh_index.get("price"),
            "sh_index_pct_change": sh_index.get("pct_change"),
            "sh_index_amount": sh_index.get("amount"),
            "sh_index_volume": sh_index.get("volume"),
            "total_volume": sum(max(0, to_int(row.get("volume"))) for row in rows),
            "total_amount": sum(max(0.0, to_float(row.get("amount"))) for row in rows),
            "top50_amount": sum(max(0.0, to_float(row.get("amount"))) for row in top50),
            "raw_meta": {
                "backfill": True,
                "source_table": "stock_daily_bars",
                "generated_at": now_text(),
                "research_pool_trade_date": research_pool_trade_date,
                "research_pool_codes": research_pool_codes,
                "research_pool_quote_count": research_pool_stats["count"],
                "research_pool_meta": research_pool_meta,
                "shanghai_index": sh_index,
                "shanghai_index_missing_fields": sh_index_missing,
            },
        },
        top50,
    )


def validate_daily_close_snapshot(snapshot: dict[str, Any]) -> list[str]:
    missing = missing_shanghai_index_fields(
        {
            "price": snapshot.get("sh_index_price"),
            "pct_change": snapshot.get("sh_index_pct_change"),
            "amount": snapshot.get("sh_index_amount"),
            "volume": snapshot.get("sh_index_volume"),
        }
    )
    return [f"sh_index_{field}" for field in missing]


def insert_snapshot(config: Any, snapshot: dict[str, Any], top50: list[dict[str, Any]]) -> None:
    sql = f"""
    INSERT INTO market_width_snapshots(
      snapshot_id, trade_date, captured_at, source, market_scope,
      total_count, up_count, down_count, flat_count, up3_count, down3_count, up5_count, down5_count,
      limit_up_count, limit_down_count,
      amount_top50_count, amount_top50_up_count, amount_top50_down_count, amount_top50_flat_count,
      amount_top50_up3_count, amount_top50_down3_count, amount_top50_up5_count, amount_top50_down5_count,
      research_pool_trade_date, research_pool_rule,
      research_pool_count, research_pool_up_count, research_pool_down_count, research_pool_flat_count,
      research_pool_up3_count, research_pool_down3_count, research_pool_up5_count, research_pool_down5_count,
      sh_index_price, sh_index_pct_change, sh_index_amount, sh_index_volume,
      total_volume, total_amount, top50_amount, raw_meta
    ) VALUES (
      {sql_string(snapshot['snapshot_id'])}, {sql_string(snapshot['trade_date'])}, {sql_string(snapshot['captured_at'])},
      {sql_string(snapshot['source'])}, {sql_string(snapshot['market_scope'])},
      {sql_int(snapshot['total_count'])}, {sql_int(snapshot['up_count'])}, {sql_int(snapshot['down_count'])},
      {sql_int(snapshot['flat_count'])}, {sql_int(snapshot['up3_count'])}, {sql_int(snapshot['down3_count'])},
      {sql_int(snapshot['up5_count'])}, {sql_int(snapshot['down5_count'])},
      {sql_int(snapshot['limit_up_count'])}, {sql_int(snapshot['limit_down_count'])},
      {sql_int(snapshot['amount_top50_count'])}, {sql_int(snapshot['amount_top50_up_count'])},
      {sql_int(snapshot['amount_top50_down_count'])}, {sql_int(snapshot['amount_top50_flat_count'])},
      {sql_int(snapshot['amount_top50_up3_count'])}, {sql_int(snapshot['amount_top50_down3_count'])},
      {sql_int(snapshot['amount_top50_up5_count'])}, {sql_int(snapshot['amount_top50_down5_count'])},
      {sql_string(snapshot['research_pool_trade_date']) if snapshot.get('research_pool_trade_date') else "NULL"},
      {sql_string(snapshot.get('research_pool_rule') or "")},
      {sql_int(snapshot['research_pool_count'])}, {sql_int(snapshot['research_pool_up_count'])},
      {sql_int(snapshot['research_pool_down_count'])}, {sql_int(snapshot['research_pool_flat_count'])},
      {sql_int(snapshot['research_pool_up3_count'])}, {sql_int(snapshot['research_pool_down3_count'])},
      {sql_int(snapshot['research_pool_up5_count'])}, {sql_int(snapshot['research_pool_down5_count'])},
      {sql_number(snapshot.get('sh_index_price'))}, {sql_number(snapshot.get('sh_index_pct_change'))},
      {sql_number(snapshot.get('sh_index_amount'))},
      {sql_int(snapshot.get('sh_index_volume')) if snapshot.get('sh_index_volume') is not None else "NULL"},
      {sql_int(snapshot.get('total_volume')) if snapshot.get('total_volume') is not None else "NULL"},
      {sql_number(snapshot['total_amount'])}, {sql_number(snapshot['top50_amount'])}, {sql_json(snapshot['raw_meta'])}
    )
    ON DUPLICATE KEY UPDATE
      source=VALUES(source),
      total_count=VALUES(total_count),
      up_count=VALUES(up_count),
      down_count=VALUES(down_count),
      flat_count=VALUES(flat_count),
      up3_count=VALUES(up3_count),
      down3_count=VALUES(down3_count),
      up5_count=VALUES(up5_count),
      down5_count=VALUES(down5_count),
      limit_up_count=VALUES(limit_up_count),
      limit_down_count=VALUES(limit_down_count),
      amount_top50_count=VALUES(amount_top50_count),
      amount_top50_up_count=VALUES(amount_top50_up_count),
      amount_top50_down_count=VALUES(amount_top50_down_count),
      amount_top50_flat_count=VALUES(amount_top50_flat_count),
      amount_top50_up3_count=VALUES(amount_top50_up3_count),
      amount_top50_down3_count=VALUES(amount_top50_down3_count),
      amount_top50_up5_count=VALUES(amount_top50_up5_count),
      amount_top50_down5_count=VALUES(amount_top50_down5_count),
      research_pool_trade_date=VALUES(research_pool_trade_date),
      research_pool_rule=VALUES(research_pool_rule),
      research_pool_count=VALUES(research_pool_count),
      research_pool_up_count=VALUES(research_pool_up_count),
      research_pool_down_count=VALUES(research_pool_down_count),
      research_pool_flat_count=VALUES(research_pool_flat_count),
      research_pool_up3_count=VALUES(research_pool_up3_count),
      research_pool_down3_count=VALUES(research_pool_down3_count),
      research_pool_up5_count=VALUES(research_pool_up5_count),
      research_pool_down5_count=VALUES(research_pool_down5_count),
      sh_index_price=VALUES(sh_index_price),
      sh_index_pct_change=VALUES(sh_index_pct_change),
      sh_index_amount=VALUES(sh_index_amount),
      sh_index_volume=VALUES(sh_index_volume),
      total_volume=VALUES(total_volume),
      total_amount=VALUES(total_amount),
      top50_amount=VALUES(top50_amount),
      raw_meta=VALUES(raw_meta);
    """
    run_mysql(config, sql)
    if not top50:
        return
    values = []
    for row in top50:
        values.append(
            "("
            + ", ".join(
                [
                    sql_string(snapshot["snapshot_id"]),
                    sql_string(snapshot["trade_date"]),
                    sql_string(snapshot["captured_at"]),
                    sql_int(row.get("rank_no")),
                    sql_string(row.get("code")),
                    sql_string(row.get("name")),
                    sql_number(row.get("latest_price")),
                    sql_number(row.get("pct_change")),
                    sql_number(row.get("amount")),
                    sql_int(row.get("volume")),
                    sql_json(row),
                ]
            )
            + ")"
        )
    run_mysql(
        config,
        f"""
        INSERT INTO market_width_amount_top50(
          snapshot_id, trade_date, captured_at, rank_no, code, name,
          latest_price, pct_change, amount, volume, raw_row
        ) VALUES
          {",\n          ".join(values)}
        ON DUPLICATE KEY UPDATE
          rank_no=VALUES(rank_no),
          latest_price=VALUES(latest_price),
          pct_change=VALUES(pct_change),
          amount=VALUES(amount),
          volume=VALUES(volume),
          raw_row=VALUES(raw_row);
        """,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill daily-close market width snapshots from stock_daily_bars.")
    add_mysql_args(parser)
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=20)
    parser.add_argument("--min-rows", type=int, default=4800)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--allow-missing-index", action="store_true")
    parser.add_argument("--output-json", type=Path, default=project_root() / "runs" / "data_tasks" / "market_width_daily_backfill.json")
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
    results: list[dict[str, Any]] = []
    for trade_day in trade_dates(config, args.start_date, args.end_date, args.days):
        rows = load_daily_rows(config, trade_day)
        if len(rows) < int(args.min_rows) and not args.allow_partial:
            results.append({"trade_date": trade_day, "ok": False, "reason": "insufficient_daily_bars", "rows": len(rows)})
            continue
        snapshot, top50 = build_snapshot(config, trade_day, rows)
        missing_index_fields = validate_daily_close_snapshot(snapshot)
        if missing_index_fields and not args.allow_missing_index:
            results.append(
                {
                    "trade_date": trade_day,
                    "ok": False,
                    "reason": "missing_shanghai_index_fields",
                    "missing_fields": missing_index_fields,
                    "shanghai_index_source": (snapshot.get("raw_meta") or {}).get("shanghai_index", {}).get("source"),
                }
            )
            continue
        insert_snapshot(config, snapshot, top50)
        results.append(
            {
                "trade_date": trade_day,
                "ok": True,
                "snapshot_id": snapshot["snapshot_id"],
                "rows": len(rows),
                "up_count": snapshot["up_count"],
                "up5_count": snapshot["up5_count"],
                "down5_count": snapshot["down5_count"],
                "limit_up_count": snapshot["limit_up_count"],
                "limit_down_count": snapshot["limit_down_count"],
                "shanghai_index_source": (snapshot.get("raw_meta") or {}).get("shanghai_index", {}).get("source"),
            }
        )
    ok = all(bool(item.get("ok")) for item in results)
    payload = {"ok": ok, "generated_at": now_text(), "results": results}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "processed": len(results), "written": sum(1 for item in results if item.get("ok")), "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
