#!/usr/bin/env python
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import re
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import akshare as ak
import pandas as pd
import requests

from stock_move_scout.market_width import ensure_market_width_tables
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.sources import (
    DEFAULT_TDX_SERVERS,
    QuoteProviderConfig,
    TdxQuoteProvider,
    parse_tdx_server,
)
from stock_move_scout.sources.kpl_market_capacity import (
    KplMarketCapacityConfig,
    ensure_kpl_market_capacity_tables,
    fetch_market_capacity,
    normalize_market_capacity,
    save_market_capacity,
)

from stock_scout_mysql import (
    add_mysql_args,
    mysql_config_from_args,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)


MAIN_A_PREFIXES = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689")
TRADING_SESSIONS = ((time(9, 30), time(11, 30)), (time(13, 0), time(15, 0)))
DEFAULT_TDX_DIR = Path(r"G:\Tools\tdx")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_trading_time(value: datetime) -> bool:
    current = value.time()
    return any(start <= current <= end for start, end in TRADING_SESSIONS)


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).replace(",", "").replace("%", "").strip()
        if not text or text.lower() in {"nan", "none", "--"}:
            return default
        parsed = float(text)
        if math.isnan(parsed) or math.isinf(parsed):
            return default
        return parsed
    except Exception:
        return default


def finite_int(value: Any, default: int = 0) -> int:
    try:
        return int(finite_float(value, float(default)))
    except Exception:
        return default


def normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{6})", text)
    return match.group(1) if match else ""


def is_main_a_code(code: str) -> bool:
    return code.startswith(MAIN_A_PREFIXES)


def is_excluded_stock_name(name: Any) -> bool:
    text = clean_name(name)
    upper = text.upper()
    return "ST" in upper or "退" in text


def limit_threshold(code: Any) -> float:
    text = normalize_code(code)
    if text.startswith(("300", "301", "688", "689")):
        return 19.5
    return 9.8


def is_limit_up_row(row: dict[str, Any]) -> bool:
    if is_excluded_stock_name(row.get("name")):
        return False
    return finite_float(row.get("pct_change")) >= limit_threshold(row.get("code"))


def is_limit_down_row(row: dict[str, Any]) -> bool:
    if is_excluded_stock_name(row.get("name")):
        return False
    return finite_float(row.get("pct_change")) <= -limit_threshold(row.get("code"))


def clean_name(value: Any) -> str:
    return str(value or "").strip()


def json_safe(value: Any) -> Any:
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def fetch_spot_frame(source: str) -> tuple[str, pd.DataFrame]:
    def quiet_fetch(fetcher: Any) -> pd.DataFrame:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fetcher()

    if source == "akshare_stock_zh_a_spot_em":
        return source, quiet_fetch(ak.stock_zh_a_spot_em)
    if source == "akshare_stock_zh_a_spot":
        return source, quiet_fetch(ak.stock_zh_a_spot)

    errors: list[str] = []
    for source_name, fetcher in (
        ("akshare_stock_zh_a_spot", ak.stock_zh_a_spot),
        ("akshare_stock_zh_a_spot_em", ak.stock_zh_a_spot_em),
    ):
        try:
            return source_name, quiet_fetch(fetcher)
        except Exception as exc:
            errors.append(f"{source_name}: {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "fetch_spot_frame_failed")


def normalize_tdx_quote_rows(quotes: dict[str, dict[str, Any]], *, include_st: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in quotes.values():
        code = normalize_code(item.get("code"))
        name = clean_name(item.get("name"))
        if not code or not name:
            continue
        if str(item.get("is_index") or "") == "1":
            continue
        if not include_st and is_excluded_stock_name(name):
            continue
        latest_price = finite_float(item.get("price"))
        last_close = finite_float(item.get("last_close"))
        pct_change = ((latest_price / last_close - 1) * 100) if last_close else 0.0
        rows.append(
            {
                "code": code,
                "name": name[:64],
                "latest_price": latest_price,
                "pct_change": pct_change,
                "amount": finite_float(item.get("amount")),
                "volume": finite_int(item.get("vol")) * 100,
                "raw_row": {str(key): json_safe(value) for key, value in item.items()},
            }
        )
    return rows


def normalize_shanghai_index_quote(quotes: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for item in quotes.values():
        if str(item.get("is_index") or "") != "1" or normalize_code(item.get("code")) != "000001":
            continue
        latest_price = finite_float(item.get("price"))
        last_close = finite_float(item.get("last_close"))
        pct_change = ((latest_price / last_close - 1) * 100) if last_close else None
        return {
            "price": latest_price,
            "pct_change": pct_change,
            "amount": finite_float(item.get("amount")),
            "volume": finite_int(item.get("vol")),
            "raw_row": {str(key): json_safe(value) for key, value in item.items()},
        }
    return None


def fetch_tdx_rows(args: argparse.Namespace) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    servers = tuple(parse_tdx_server(item) for item in args.server) if args.server else DEFAULT_TDX_SERVERS
    provider = TdxQuoteProvider(
        universe_csv=args.universe_csv,
        config=QuoteProviderConfig(
            servers=servers,
            timeout=int(args.tdx_timeout),
            batch_size=int(args.batch_size),
        ),
    )
    snapshot = provider.full_market_snapshot(
        refresh_universe=bool(args.refresh_universe),
        include_shanghai_index=True,
        batch_size=int(args.batch_size),
    )
    rows = normalize_tdx_quote_rows(snapshot.quotes, include_st=args.include_st)
    shanghai_index = normalize_shanghai_index_quote(snapshot.quotes)
    if not shanghai_index:
        # Some TDX servers silently omit index quotes when they are mixed into
        # a large stock batch. Fetch the Shanghai index separately so the
        # market overview keeps its intraday index strip populated.
        try:
            index_snapshot = provider.shanghai_index_snapshot()
            shanghai_index = normalize_shanghai_index_quote(index_snapshot.quotes)
        except Exception as exc:
            shanghai_index = None
            index_error = f"{type(exc).__name__}: {exc}"
        else:
            index_error = ""
    else:
        index_error = ""
    return (
        snapshot.source,
        rows,
        {
            "server": snapshot.server,
            "universe_count": snapshot.universe_count,
            "quote_count": snapshot.quote_count,
            "batch_size": int(args.batch_size),
            "tdx_timeout": int(args.tdx_timeout),
            "shanghai_index": shanghai_index,
            "shanghai_index_fallback_error": index_error,
        },
    )


def fetch_market_rows(args: argparse.Namespace) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    if args.source in {"tdx", "auto"}:
        try:
            return fetch_tdx_rows(args)
        except Exception as exc:
            if args.source == "tdx":
                raise
            tdx_error = f"{type(exc).__name__}: {exc}"
        else:
            tdx_error = ""
    else:
        tdx_error = ""
    source, frame = fetch_spot_frame(args.source)
    rows = normalize_rows(frame, include_bj=args.include_bj, include_st=args.include_st)
    return source, rows, {"fallback_from_tdx_error": tdx_error} if tdx_error else {}


def column_value(row: pd.Series, *names: str) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def normalize_rows(df: pd.DataFrame, *, include_bj: bool, include_st: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        code = normalize_code(column_value(row, "代码", "code", "symbol"))
        name = clean_name(column_value(row, "名称", "name"))
        if not code or not name:
            continue
        if not include_bj and not is_main_a_code(code):
            continue
        if not include_st and is_excluded_stock_name(name):
            continue
        amount = finite_float(column_value(row, "成交额", "amount", "成交金额"))
        pct_change = finite_float(column_value(row, "涨跌幅", "pct_change", "涨幅"))
        latest_price = finite_float(column_value(row, "最新价", "price", "最新"))
        volume = finite_int(column_value(row, "成交量", "volume"))
        rows.append(
            {
                "code": code,
                "name": name[:64],
                "latest_price": latest_price,
                "pct_change": pct_change,
                "amount": amount,
                "volume": volume,
                "raw_row": {str(key): json_safe(value) for key, value in row.to_dict().items()},
            }
        )
    return rows


def width_stats(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "count": len(rows),
        "up_count": sum(1 for row in rows if finite_float(row.get("pct_change")) > 0),
        "down_count": sum(1 for row in rows if finite_float(row.get("pct_change")) < 0),
        "flat_count": sum(1 for row in rows if finite_float(row.get("pct_change")) == 0),
        "up3_count": sum(1 for row in rows if finite_float(row.get("pct_change")) >= 3),
        "down3_count": sum(1 for row in rows if finite_float(row.get("pct_change")) <= -3),
        "up5_count": sum(1 for row in rows if finite_float(row.get("pct_change")) > 5),
        "down5_count": sum(1 for row in rows if finite_float(row.get("pct_change")) < -5),
    }


def load_research_pool_codes(config: Any, trade_day: date) -> tuple[str, list[str], dict[str, Any]]:
    snapshot = ResearchPoolProvider(config).latest_snapshot(trade_day.isoformat())
    return snapshot.trade_date, list(snapshot.codes), {
        "rule": snapshot.rule,
        "code_count": snapshot.code_count,
        "source_dates": snapshot.source_dates,
        "codes_by_source_count": {key: len(values) for key, values in snapshot.codes_by_source.items()},
    }


def build_snapshot(
    rows: list[dict[str, Any]],
    *,
    snapshot_id: str,
    captured_at: datetime,
    source: str,
    market_scope: str,
    research_pool_trade_date: str,
    research_pool_codes: list[str],
    research_pool_meta: dict[str, Any] | None = None,
    source_meta: dict[str, Any] | None = None,
    shanghai_index: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_code = {str(row.get("code")): row for row in rows}
    top50 = sorted(rows, key=lambda item: finite_float(item.get("amount")), reverse=True)[:50]
    for rank_no, row in enumerate(top50, start=1):
        row["rank_no"] = rank_no
    market = width_stats(rows)
    amount_top50 = width_stats(top50)
    research_pool_rows = [by_code[code] for code in research_pool_codes if code in by_code]
    research_pool_stats = width_stats(research_pool_rows)
    snapshot = {
        "snapshot_id": snapshot_id,
        "trade_date": captured_at.date().isoformat(),
        "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
        "source": source,
        "market_scope": market_scope,
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
        "sh_index_price": (shanghai_index or {}).get("price"),
        "sh_index_pct_change": (shanghai_index or {}).get("pct_change"),
        "sh_index_amount": (shanghai_index or {}).get("amount"),
        "sh_index_volume": (shanghai_index or {}).get("volume"),
        "total_volume": sum(max(0, finite_int(row.get("volume"))) for row in rows),
        "total_amount": sum(max(0.0, finite_float(row.get("amount"))) for row in rows),
        "top50_amount": sum(max(0.0, finite_float(row.get("amount"))) for row in top50),
        "raw_meta": {
            "row_count": len(rows),
            "top50_count": len(top50),
            "research_pool_trade_date": research_pool_trade_date,
            "research_pool_quote_count": research_pool_stats["count"],
            "research_pool_meta": {
                "rule": (research_pool_meta or {}).get("rule"),
                "code_count": (research_pool_meta or {}).get("code_count"),
                "source_dates": (research_pool_meta or {}).get("source_dates"),
                "codes_by_source_count": (research_pool_meta or {}).get("codes_by_source_count"),
            },
            "source_meta": {
                "server": (source_meta or {}).get("server"),
                "universe_count": (source_meta or {}).get("universe_count"),
                "quote_count": (source_meta or {}).get("quote_count"),
                "batch_size": (source_meta or {}).get("batch_size"),
                "tdx_timeout": (source_meta or {}).get("tdx_timeout"),
                "fallback_from_tdx_error": (source_meta or {}).get("fallback_from_tdx_error"),
                "shanghai_index_fallback_error": (source_meta or {}).get("shanghai_index_fallback_error"),
            },
            "generated_at": now_text(),
        },
    }
    return snapshot, top50


def insert_snapshot(config: Any, snapshot: dict[str, Any], top50: list[dict[str, Any]]) -> None:
    snapshot_sql = f"""
    INSERT INTO market_width_snapshots(
      snapshot_id, trade_date, captured_at, source, market_scope,
      total_count, up_count, down_count, flat_count, up3_count, down3_count,
      up5_count, down5_count,
      limit_up_count, limit_down_count,
      amount_top50_count, amount_top50_up_count, amount_top50_down_count, amount_top50_flat_count,
      amount_top50_up3_count, amount_top50_down3_count, amount_top50_up5_count, amount_top50_down5_count,
      research_pool_trade_date, research_pool_rule,
      research_pool_count, research_pool_up_count, research_pool_down_count, research_pool_flat_count,
      research_pool_up3_count, research_pool_down3_count, research_pool_up5_count, research_pool_down5_count,
      sh_index_price, sh_index_pct_change, sh_index_amount, sh_index_volume,
      total_volume,
      total_amount, top50_amount, raw_meta
    ) VALUES (
      {sql_string(snapshot['snapshot_id'])},
      {sql_string(snapshot['trade_date'])},
      {sql_string(snapshot['captured_at'])},
      {sql_string(snapshot['source'])},
      {sql_string(snapshot['market_scope'])},
      {sql_int(snapshot['total_count'])},
      {sql_int(snapshot['up_count'])},
      {sql_int(snapshot['down_count'])},
      {sql_int(snapshot['flat_count'])},
      {sql_int(snapshot['up3_count'])},
      {sql_int(snapshot['down3_count'])},
      {sql_int(snapshot['up5_count'])},
      {sql_int(snapshot['down5_count'])},
      {sql_int(snapshot['limit_up_count'])},
      {sql_int(snapshot['limit_down_count'])},
      {sql_int(snapshot['amount_top50_count'])},
      {sql_int(snapshot['amount_top50_up_count'])},
      {sql_int(snapshot['amount_top50_down_count'])},
      {sql_int(snapshot['amount_top50_flat_count'])},
      {sql_int(snapshot['amount_top50_up3_count'])},
      {sql_int(snapshot['amount_top50_down3_count'])},
      {sql_int(snapshot['amount_top50_up5_count'])},
      {sql_int(snapshot['amount_top50_down5_count'])},
      {sql_string(snapshot['research_pool_trade_date']) if snapshot.get('research_pool_trade_date') else "NULL"},
      {sql_string(snapshot.get('research_pool_rule') or "")},
      {sql_int(snapshot['research_pool_count'])},
      {sql_int(snapshot['research_pool_up_count'])},
      {sql_int(snapshot['research_pool_down_count'])},
      {sql_int(snapshot['research_pool_flat_count'])},
      {sql_int(snapshot['research_pool_up3_count'])},
      {sql_int(snapshot['research_pool_down3_count'])},
      {sql_int(snapshot['research_pool_up5_count'])},
      {sql_int(snapshot['research_pool_down5_count'])},
      {sql_number(snapshot.get('sh_index_price'))},
      {sql_number(snapshot.get('sh_index_pct_change'))},
      {sql_number(snapshot.get('sh_index_amount'))},
      {sql_int(snapshot.get('sh_index_volume')) if snapshot.get('sh_index_volume') is not None else "NULL"},
      {sql_int(snapshot.get('total_volume')) if snapshot.get('total_volume') is not None else "NULL"},
      {sql_number(snapshot['total_amount'])},
      {sql_number(snapshot['top50_amount'])},
      {sql_json(snapshot['raw_meta'])}
    )
    ON DUPLICATE KEY UPDATE
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
    run_mysql(config, snapshot_sql)

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
                    "NULL",
                ]
            )
            + ")"
        )
    top_sql = f"""
    INSERT INTO market_width_amount_top50(
      snapshot_id, trade_date, captured_at, rank_no, code, name,
      latest_price, pct_change, amount, volume, raw_row
    ) VALUES
      {",\n      ".join(values)}
    ON DUPLICATE KEY UPDATE
      rank_no=VALUES(rank_no),
      latest_price=VALUES(latest_price),
      pct_change=VALUES(pct_change),
      amount=VALUES(amount),
      volume=VALUES(volume),
      raw_row=VALUES(raw_row);
    """
    run_mysql(config, top_sql)


def collect_synced_kpl_market_capacity(
    config: Any,
    *,
    trade_date: str,
    captured_at: str,
    timeout: int,
) -> dict[str, Any]:
    ensure_kpl_market_capacity_tables(config)
    cfg = KplMarketCapacityConfig(trade_date=trade_date, timeout=max(1, int(timeout)))
    payload = fetch_market_capacity(requests.Session(), cfg)
    if str(payload.get("errcode", "0")) != "0":
        raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
    normalized = normalize_market_capacity(payload, trade_date, captured_at)
    trend_count = save_market_capacity(
        config,
        snapshot=normalized["snapshot"],
        trends=normalized["trends"],
    )
    snapshot = normalized["snapshot"]
    return {
        "ok": True,
        "source": "kpl_market_capacity",
        "market_time": snapshot.get("market_time"),
        "forecast_text": snapshot.get("forecast_text"),
        "forecast_amount_yi": snapshot.get("forecast_amount_yi"),
        "forecast_change_pct": snapshot.get("forecast_change_pct"),
        "forecast_delta_yi": snapshot.get("forecast_delta_yi"),
        "current_amount_yi": round(finite_float(snapshot.get("latest_amount_wan")) / 10000, 2),
        "trend_count": trend_count,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect market width snapshot and amount Top50.")
    add_mysql_args(parser)
    root = project_root()
    parser.add_argument("--source", choices=["auto", "tdx", "akshare_stock_zh_a_spot", "akshare_stock_zh_a_spot_em"], default="tdx")
    parser.add_argument("--include-bj", action="store_true", help="Include BSE stocks; default only keeps Shanghai/Shenzhen A shares.")
    parser.add_argument("--include-st", action="store_true")
    parser.add_argument("--allow-outside-trading", action="store_true", help="Allow writing snapshots outside 09:30-11:30 and 13:00-15:00.")
    parser.add_argument("--server", action="append", default=[], help="TDX ip:port override.")
    parser.add_argument("--tdx-timeout", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--tdx-dir", type=Path, default=DEFAULT_TDX_DIR)
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "tdx_a_stock_universe.csv")
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "market_width_latest.json")
    parser.add_argument("--skip-kpl-market-capacity", action="store_true", help="Do not collect KPL market capacity in the same snapshot batch.")
    parser.add_argument("--skip-ensure-tables", action="store_true", help="Skip DDL checks when tables are already initialized.")
    parser.add_argument("--kpl-timeout", type=int, default=8)
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
    if not args.skip_ensure_tables:
        ensure_market_width_tables(config)

    captured_at = datetime.now()
    if not args.allow_outside_trading and not is_trading_time(captured_at):
        payload = {
            "ok": True,
            "skipped": True,
            "reason": "outside_trading_time",
            "captured_at": captured_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "trading_sessions": ["09:30-11:30", "13:00-15:00"],
        }
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    snapshot_id = captured_at.strftime("%Y%m%d%H%M%S%f")[:20]
    source, rows, source_meta = fetch_market_rows(args)
    research_pool_trade_date, research_pool_codes, research_pool_meta = load_research_pool_codes(config, captured_at.date())
    snapshot, top50 = build_snapshot(
        rows,
        snapshot_id=snapshot_id,
        captured_at=captured_at,
        source=source,
        market_scope="cn_a_all" if args.include_bj else "cn_a_main",
        research_pool_trade_date=research_pool_trade_date,
        research_pool_codes=research_pool_codes,
        research_pool_meta=research_pool_meta,
        source_meta=source_meta,
        shanghai_index=source_meta.get("shanghai_index") if isinstance(source_meta, dict) else None,
    )
    insert_snapshot(config, snapshot, top50)
    kpl_capacity: dict[str, Any] = {"ok": False, "skipped": True}
    if not args.skip_kpl_market_capacity:
        try:
            kpl_capacity = collect_synced_kpl_market_capacity(
                config,
                trade_date=snapshot["trade_date"],
                captured_at=snapshot["captured_at"],
                timeout=args.kpl_timeout,
            )
        except Exception as exc:
            kpl_capacity = {
                "ok": False,
                "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            }

    payload = {
        "ok": True,
        "snapshot": {
            **snapshot,
            "total_amount_yi": round(snapshot["total_amount"] / 100000000, 2),
            "top50_amount_yi": round(snapshot["top50_amount"] / 100000000, 2),
            "total_volume_yi": round(snapshot["total_volume"] / 100000000, 2) if snapshot.get("total_volume") is not None else None,
        },
        "kpl_capacity": kpl_capacity,
        "top50": [
            {
                "rank_no": row.get("rank_no"),
                "code": row.get("code"),
                "name": row.get("name"),
                "pct_change": row.get("pct_change"),
                "amount_yi": round(finite_float(row.get("amount")) / 100000000, 2),
            }
            for row in top50
        ],
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "snapshot_id": snapshot_id, "trade_date": date.today().isoformat(), "rows": len(rows), "top50": len(top50), "source": source, "kpl_capacity": kpl_capacity}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
