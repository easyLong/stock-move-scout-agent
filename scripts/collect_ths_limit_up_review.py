#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)


SOURCE = "ths_limit_up_review"
ANCHOR_TYPE = "limit_up_theme"
SUMMARY_URL = "https://apigate.10jqka.com.cn/d/charge/limit_up/market/query/v1/pool/ztfp"
FAUCET_URL = "https://vaserviece.10jqka.com.cn/priceslimithelper/pool/gnlt?from=mobile"
BOARD_DETAIL_URL = "https://vaserviece.10jqka.com.cn/priceslimithelper/stock/ztstockdatadetail"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def compact(value: Any, limit: int = 1024) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def to_int(value: Any) -> int:
    try:
        text = str(value or "0").replace(",", "").replace("板", "").strip()
        if not text:
            return 0
        return int(float(text))
    except Exception:
        return 0


def to_float(value: Any) -> float | None:
    try:
        text = str(value or "").replace(",", "").replace("%", "").strip()
        if not text or text == "--":
            return None
        number = float(text)
        if "亿" in str(value):
            return number * 100_000_000
        if "万" in str(value):
            return number * 10_000
        return number
    except Exception:
        return None


def normalize_trade_date(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return fallback


def read_cookie(path: str) -> str:
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8", errors="ignore").strip()
    return os.environ.get("THS_COOKIE", "").strip()


def headers(cookie: str = "") -> dict[str, str]:
    out = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Referer": "https://vaserviece.10jqka.com.cn/limitUp/index.html",
        "Origin": "https://vaserviece.10jqka.com.cn",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        out["Cookie"] = cookie
    return out


def request_json(url: str, params: dict[str, Any], cookie: str, timeout: int) -> dict[str, Any]:
    response = requests.get(url, params=params, headers=headers(cookie), timeout=timeout)
    response.raise_for_status()
    data = response.json()
    denied = (
        str(data.get("status_code") or data.get("code") or data.get("errorcode") or "") in {"403", "-1001"}
        or "denied" in str(data).lower()
        or "无权限" in str(data)
    )
    if denied:
        raise PermissionError(f"ths_limit_up_api_denied url={url} response={compact(data, 500)}")
    return data


def candidate_request_params(trade_date: str) -> list[dict[str, Any]]:
    compact_day = trade_date.replace("-", "")
    return [
        {},
        {"date": compact_day},
        {"date": trade_date},
        {"day": compact_day},
        {"tradeDate": compact_day},
        {"trade_date": trade_date},
    ]


def fetch_remote(trade_date: str, cookie: str, timeout: int, pause: float) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    errors: list[str] = []
    for url in [SUMMARY_URL, FAUCET_URL, BOARD_DETAIL_URL]:
        for params in candidate_request_params(trade_date):
            try:
                data = request_json(url, params, cookie, timeout)
                payloads.append({"url": url, "params": params, "data": data})
                break
            except PermissionError:
                raise
            except Exception as exc:
                errors.append(f"{url} {params}: {type(exc).__name__}:{exc}")
            time.sleep(pause)
    if not payloads and errors:
        raise RuntimeError("; ".join(errors[-5:]))
    return payloads


def load_json_payloads(path: str) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(data, list):
        return [{"url": "local_json", "params": {}, "data": item} for item in data]
    return [{"url": "local_json", "params": {}, "data": data}]


def first_value(item: dict[str, Any], names: list[str]) -> Any:
    for name in names:
        if name in item and item.get(name) not in (None, ""):
            return item.get(name)
    return ""


def iter_dicts(value: Any, trade_date_hint: str = ""):
    if isinstance(value, dict):
        next_hint = trade_date_hint
        for key in value.keys():
            parsed = normalize_trade_date(key, "")
            if parsed:
                next_hint = parsed
                break
        yield value, next_hint
        for key, child in value.items():
            parsed = normalize_trade_date(key, next_hint)
            yield from iter_dicts(child, parsed)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child, trade_date_hint)


def normalize_item(item: dict[str, Any], trade_date_hint: str, default_trade_date: str) -> dict[str, Any] | None:
    code = compact(first_value(item, ["stockcode", "stockCode", "code", "stock_code", "股票代码"]), 6)
    if not re.fullmatch(r"\d{6}", code):
        return None
    trade_date = normalize_trade_date(
        first_value(item, ["trade_date", "tradeDate", "date", "day", "交易日"]),
        trade_date_hint or default_trade_date,
    )
    if not trade_date:
        trade_date = default_trade_date
    stock_name = compact(first_value(item, ["stockname", "stockName", "name", "stock_name", "股票名称"]), 64)
    raw_reason = first_value(item, ["ztyy", "limitGene", "reason", "theme_name", "boardName", "概念", "涨停原因"])
    reason = compact(raw_reason, 1024)
    theme = compact(first_value(item, ["theme_name", "boardName", "ztyy", "limitGene", "reason", "概念", "涨停原因"]), 128)
    if not theme:
        theme = reason[:128]
    if not theme:
        return None
    first_time = compact(first_value(item, ["scztsj", "firstLimitTime", "first_limit_time", "首次涨停时间"]), 32)
    last_time = compact(first_value(item, ["zzdtsj", "lastLimitTime", "last_limit_time", "最后涨停时间"]), 32)
    limit_days = to_int(first_value(item, ["lbzs", "lbts", "stockContinueLimitUpDays", "limit_up_days", "连板数"]))
    open_count = to_int(first_value(item, ["openCount", "open_count", "炸板次数"]))
    is_open = first_value(item, ["isopen", "wkb"])
    status = "broken" if str(is_open) in {"1", "true", "True"} else "limit_up"
    return {
        "trade_date": trade_date,
        "code": code,
        "stock_name": stock_name,
        "theme_name": theme,
        "reason": reason or theme,
        "limit_up_days": limit_days,
        "first_limit_time": first_time,
        "last_limit_time": last_time,
        "open_count": open_count,
        "seal_amount": to_float(first_value(item, ["sealAmount", "seal_amount", "封单金额"])),
        "turnover_amount": to_float(first_value(item, ["jye", "turnoverAmount", "amount", "成交额"])),
        "turnover_rate": to_float(first_value(item, ["hsl", "turnoverRate", "换手率"])),
        "free_float_value": to_float(first_value(item, ["ltz", "freeFloatValue", "流通市值"])),
        "total_market_value": to_float(first_value(item, ["zsz", "totalMarketValue", "总市值"])),
        "status": status,
        "source": SOURCE,
        "raw_json": item,
    }


def normalize_payloads(payloads: list[dict[str, Any]], default_trade_date: str) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        data = payload.get("data")
        for item, trade_date_hint in iter_dicts(data, default_trade_date):
            row = normalize_item(item, trade_date_hint, default_trade_date)
            if not row:
                continue
            key = (row["trade_date"], row["code"], row["theme_name"])
            if key in seen:
                continue
            seen.add(key)
            row["raw_json"] = {"source_payload": {"url": payload.get("url"), "params": payload.get("params")}, "item": row["raw_json"]}
            rows.append(row)
    return rows


def ensure_schema(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS ths_limit_up_review_items (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL DEFAULT '',
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      theme_name VARCHAR(128) NOT NULL DEFAULT '',
      reason VARCHAR(1024) NOT NULL DEFAULT '',
      limit_up_days INT NOT NULL DEFAULT 0,
      first_limit_time VARCHAR(32) NOT NULL DEFAULT '',
      last_limit_time VARCHAR(32) NOT NULL DEFAULT '',
      open_count INT NOT NULL DEFAULT 0,
      seal_amount DECIMAL(20,2) NULL,
      turnover_amount DECIMAL(20,2) NULL,
      turnover_rate DECIMAL(12,4) NULL,
      free_float_value DECIMAL(20,2) NULL,
      total_market_value DECIMAL(20,2) NULL,
      status VARCHAR(32) NOT NULL DEFAULT 'limit_up',
      source VARCHAR(64) NOT NULL DEFAULT 'ths_limit_up_review',
      raw_json JSON NULL,
      collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      PRIMARY KEY (id),
      UNIQUE KEY uk_ths_limit_review_date_stock_theme (trade_date, code, theme_name),
      KEY idx_ths_limit_review_date_theme (trade_date, theme_name),
      KEY idx_ths_limit_review_code_date (code, trade_date),
      KEY idx_ths_limit_review_source (source, trade_date)
    ) ENGINE=InnoDB COMMENT='THS limit-up review items: market attribution for limit-up stocks.';
    """
    run_mysql(config, sql)
    try:
        run_mysql(
            config,
            """
            ALTER TABLE stock_theme_reason_bank
            MODIFY source ENUM('ths_limit_up_review','ths_hot_concept','ths_stock_concept','ths_root_theme_point','concept_tag')
            NOT NULL DEFAULT 'ths_root_theme_point';
            """,
        )
    except Exception:
        pass


def write_items(config: MySqlConfig, rows: list[dict[str, Any]], replace_dates: bool) -> int:
    if not rows:
        return 0
    if replace_dates:
        dates = sorted({row["trade_date"] for row in rows if row.get("trade_date")})
        if dates:
            run_mysql(config, f"DELETE FROM ths_limit_up_review_items WHERE trade_date IN ({','.join(sql_string(day) for day in dates)});")
    statements: list[str] = []
    for row in rows:
        statements.append(
            f"""
            INSERT INTO ths_limit_up_review_items(
              trade_date, code, stock_name, theme_name, reason, limit_up_days,
              first_limit_time, last_limit_time, open_count, seal_amount,
              turnover_amount, turnover_rate, free_float_value, total_market_value,
              status, source, raw_json
            ) VALUES(
              {sql_string(row['trade_date'])}, {sql_string(row['code'])}, {sql_string(row['stock_name'])},
              {sql_string(row['theme_name'])}, {sql_string(row['reason'])}, {sql_int(row['limit_up_days'])},
              {sql_string(row['first_limit_time'])}, {sql_string(row['last_limit_time'])}, {sql_int(row['open_count'])},
              {sql_number(row['seal_amount'])}, {sql_number(row['turnover_amount'])}, {sql_number(row['turnover_rate'])},
              {sql_number(row['free_float_value'])}, {sql_number(row['total_market_value'])},
              {sql_string(row['status'])}, {sql_string(SOURCE)}, {sql_json(row['raw_json'])}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name), reason=VALUES(reason), limit_up_days=VALUES(limit_up_days),
              first_limit_time=VALUES(first_limit_time), last_limit_time=VALUES(last_limit_time),
              open_count=VALUES(open_count), seal_amount=VALUES(seal_amount),
              turnover_amount=VALUES(turnover_amount), turnover_rate=VALUES(turnover_rate),
              free_float_value=VALUES(free_float_value), total_market_value=VALUES(total_market_value),
              status=VALUES(status), raw_json=VALUES(raw_json), updated_at=NOW(3);
            """
        )
    for idx in range(0, len(statements), 300):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 300]) + "\nCOMMIT;")
    return len(rows)


def unique(values: list[str], limit: int = 200) -> list[str]:
    out: list[str] = []
    for value in values:
        text = compact(value, 128)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def rebuild_active_anchors(config: MySqlConfig, lookback_days: int) -> dict[str, int]:
    interval_days = max(1, int(lookback_days)) - 1
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT theme_name, code, stock_name, reason, DATE_FORMAT(trade_date, '%Y-%m-%d'),
                   limit_up_days, COALESCE(seal_amount, 0), COALESCE(turnover_amount, 0), status
            FROM ths_limit_up_review_items
            WHERE trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
              AND COALESCE(theme_name, '') <> ''
              AND COALESCE(code, '') <> '';
            """,
            batch=True,
            raw=True,
        )
    )
    by_anchor: dict[str, dict[str, Any]] = defaultdict(lambda: {"dates": set(), "codes": [], "reasons": [], "limit_count": 0, "max_days": 0, "seal": 0.0, "turnover": 0.0})
    by_member: dict[tuple[str, str], dict[str, Any]] = {}
    today = date.today().strftime("%Y-%m-%d")
    for row in rows:
        if len(row) < 9:
            continue
        theme, code, stock_name, reason, trade_date, days_text, seal_text, turnover_text, status = row[:9]
        days = to_int(days_text)
        seal = to_float(seal_text) or 0.0
        turnover = to_float(turnover_text) or 0.0
        item = by_anchor[theme]
        item["dates"].add(trade_date)
        item["codes"].append(code)
        item["reasons"].append(reason)
        item["limit_count"] += 1
        item["max_days"] = max(int(item["max_days"]), days)
        item["seal"] += seal
        item["turnover"] += turnover
        member = by_member.setdefault(
            (theme, code),
            {
                "theme": theme,
                "code": code,
                "stock_name": stock_name,
                "dates": set(),
                "reasons": [],
                "limit_count": 0,
                "max_days": 0,
                "seal": 0.0,
                "turnover": 0.0,
                "latest_reason": "",
            },
        )
        member["dates"].add(trade_date)
        member["reasons"].append(reason)
        member["limit_count"] += 1
        member["max_days"] = max(int(member["max_days"]), days)
        member["seal"] += seal
        member["turnover"] += turnover
        if reason:
            member["latest_reason"] = reason
        if stock_name:
            member["stock_name"] = stock_name

    statements: list[str] = [
        f"UPDATE active_market_anchors SET status='expired' WHERE source={sql_string(SOURCE)};",
        f"UPDATE active_market_anchor_members SET status='expired' WHERE source={sql_string(SOURCE)};",
        f"DELETE FROM active_anchor_match_candidates WHERE source={sql_string(SOURCE)};",
    ]
    for anchor, item in by_anchor.items():
        dates = sorted(item["dates"])
        first_seen = dates[0] if dates else ""
        last_seen = dates[-1] if dates else ""
        active_days = len(dates)
        codes = unique(item["codes"], 300)
        today_codes = []
        limit_count = int(item["limit_count"])
        score = active_days * 35 + len(codes) * 5 + limit_count * 10 + int(item["max_days"]) * 15 + min(100, float(item["seal"]) / 100_000_000)
        status = "active" if last_seen == today else ("cooling" if score >= 80 else "watch")
        statements.append(
            f"""
            INSERT INTO active_market_anchors(
              anchor_name, anchor_type, source, first_seen_date, last_seen_date,
              active_days_14d, event_count_14d, total_heat_14d, today_heat, today_event_count,
              member_count_14d, today_member_count, limit_up_count_14d, today_limit_up_count,
              leader_codes, member_codes, keywords, related_themes, related_titles,
              final_score, status, raw_json, generated_at
            ) VALUES(
              {sql_string(anchor)}, {sql_string(ANCHOR_TYPE)}, {sql_string(SOURCE)}, {sql_string(first_seen)}, {sql_string(last_seen)},
              {sql_int(active_days)}, {sql_int(limit_count)}, {sql_int(round(float(item['turnover']) / 10000))}, 0, 0,
              {sql_int(len(codes))}, {sql_int(len(today_codes))}, {sql_int(limit_count)}, 0,
              {sql_json(codes[:20])}, {sql_json(codes)}, {sql_json([anchor])}, {sql_json([anchor])},
              {sql_json(unique(item['reasons'], 30))}, {sql_number(round(score, 2))}, {sql_string(status)},
              {sql_json({'algorithm': 'ths_limit_up_review_anchor_v1', 'source_table': 'ths_limit_up_review_items', 'max_limit_up_days': item['max_days']})}, NOW(3)
            )
            ON DUPLICATE KEY UPDATE
              first_seen_date=VALUES(first_seen_date), last_seen_date=VALUES(last_seen_date),
              active_days_14d=VALUES(active_days_14d), event_count_14d=VALUES(event_count_14d),
              total_heat_14d=VALUES(total_heat_14d), member_count_14d=VALUES(member_count_14d),
              limit_up_count_14d=VALUES(limit_up_count_14d), leader_codes=VALUES(leader_codes),
              member_codes=VALUES(member_codes), keywords=VALUES(keywords), related_themes=VALUES(related_themes),
              related_titles=VALUES(related_titles), final_score=VALUES(final_score), status=VALUES(status),
              raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
            """
        )
    for (anchor, code), item in by_member.items():
        dates = sorted(item["dates"])
        first_seen = dates[0] if dates else ""
        last_seen = dates[-1] if dates else ""
        active_days = len(dates)
        confidence = 98 + min(20, int(item["limit_count"]) * 4 + int(item["max_days"]) * 2)
        status = "active" if last_seen == today else "cooling"
        reasons = unique(item["reasons"], 20)
        latest_reason = compact(item["latest_reason"] or (reasons[0] if reasons else ""), 1024)
        statements.append(
            f"""
            INSERT INTO active_market_anchor_members(
              anchor_name, anchor_type, source, code, stock_name,
              first_seen_date, last_seen_date, active_days_14d, event_count_14d,
              total_heat_14d, limit_up_count_14d, theme_names, reasons,
              latest_reason, confidence, status, raw_json, generated_at
            ) VALUES(
              {sql_string(anchor)}, {sql_string(ANCHOR_TYPE)}, {sql_string(SOURCE)}, {sql_string(code)}, {sql_string(item['stock_name'])},
              {sql_string(first_seen)}, {sql_string(last_seen)}, {sql_int(active_days)}, {sql_int(item['limit_count'])},
              {sql_int(round(float(item['turnover']) / 10000))}, {sql_int(item['limit_count'])},
              {sql_json([anchor])}, {sql_json(reasons)}, {sql_string(latest_reason)},
              {sql_number(confidence)}, {sql_string(status)},
              {sql_json({'algorithm': 'ths_limit_up_review_member_v1', 'max_limit_up_days': item['max_days'], 'seal_amount': item['seal']})}, NOW(3)
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name), first_seen_date=VALUES(first_seen_date), last_seen_date=VALUES(last_seen_date),
              active_days_14d=VALUES(active_days_14d), event_count_14d=VALUES(event_count_14d),
              total_heat_14d=VALUES(total_heat_14d), limit_up_count_14d=VALUES(limit_up_count_14d),
              theme_names=VALUES(theme_names), reasons=VALUES(reasons), latest_reason=VALUES(latest_reason),
              confidence=VALUES(confidence), status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
            """
        )
        statements.append(
            f"""
            INSERT INTO active_anchor_match_candidates(
              anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
              matched_term, evidence_text, confidence, status, raw_json, generated_at
            ) VALUES(
              {sql_string(anchor)}, {sql_string(ANCHOR_TYPE)}, {sql_string(SOURCE)}, {sql_string(code)}, {sql_string(item['stock_name'])},
              'ths_limit_up_review_member', 'strong', {sql_string(anchor)}, {sql_string(latest_reason)},
              {sql_number(confidence)}, {sql_string(status)},
              {sql_json({'source_rule': 'direct_limit_up_review_member'})}, NOW(3)
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text), confidence=VALUES(confidence),
              status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
            """
        )
    for idx in range(0, len(statements), 250):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 250]) + "\nCOMMIT;")
    return {"anchors": len(by_anchor), "members": len(by_member), "matches": len(by_member)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect/import THS limit-up review and build active limit-up theme anchors.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--input-json", default="")
    parser.add_argument("--cookie-file", default="")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--replace-dates", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--fail-on-empty", action="store_true")
    parser.add_argument("--lookback-days", type=int, default=14)
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    ensure_schema(config)
    payloads: list[dict[str, Any]] = []
    if args.input_json:
        payloads.extend(load_json_payloads(args.input_json))
    elif not args.skip_fetch:
        cookie = read_cookie(args.cookie_file)
        start_day = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
        for offset in range(max(1, int(args.days))):
            day = (start_day - timedelta(days=offset)).isoformat()
            try:
                payloads.extend(fetch_remote(day, cookie, args.timeout, args.pause))
            except PermissionError as exc:
                print(f"[{now_text()}] {exc}", file=sys.stderr)
                break
            time.sleep(args.pause)
    rows = normalize_payloads(payloads, args.trade_date)
    written = write_items(config, rows, args.replace_dates)
    active_counts = rebuild_active_anchors(config, args.lookback_days)
    print(
        json.dumps(
            {
                "payloads": len(payloads),
                "normalized_rows": len(rows),
                "written": written,
                "active": active_counts,
                "trade_date": args.trade_date,
                "source": SOURCE,
            },
            ensure_ascii=False,
        )
    )
    return 1 if (args.fail_on_empty and not rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
