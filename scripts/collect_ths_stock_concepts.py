from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any

import requests

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_number,
    sql_string,
)


API_URL = "https://basic.10jqka.com.cn/fuyao/f10_stock_index/concept/v1/stock_concept_list"


def compact(value: Any, limit: int = 2048) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def sql_int(value: Any) -> str:
    try:
        return str(int(float(str(value or "0"))))
    except Exception:
        return "0"


def market_id_for(code: str, market: str = "") -> str:
    market = str(market or "")
    if market == "1" or code.startswith(("6", "9")):
        return "17"
    if code.startswith(("8", "4")):
        return "151"
    return "33"


def load_universe(config: MySqlConfig, offset: int, limit: int, code: str = "") -> list[dict[str, str]]:
    if code:
        sql = f"""
        SELECT code, name, market
        FROM stocks
        WHERE code={sql_string(code)}
        LIMIT 1;
        """
    else:
        sql = f"""
        SELECT code, name, market
        FROM stocks
        WHERE COALESCE(is_st, 0)=0
          AND name NOT LIKE '%退市%'
          AND name NOT LIKE '%ST%'
        ORDER BY code
        LIMIT {int(limit)} OFFSET {int(offset)};
        """
    rows = []
    for item in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(item) < 3:
            continue
        rows.append({"code": item[0], "stock_name": item[1], "market": item[2], "market_id": market_id_for(item[0], item[2])})
    return rows


def fetch_stock_concepts(code: str, market_id: str, timeout: int) -> tuple[list[dict[str, Any]], str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://basic.10jqka.com.cn/astockpc/astockmain/index.html",
    }
    params = {"market_id": market_id, "code": code}
    try:
        response = requests.get(API_URL, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return [], f"error:{type(exc).__name__}:{exc}"
    if int(payload.get("status_code", -1)) != 0:
        return [], f"status:{payload.get('status_code')}:{payload.get('status_msg', '')}"
    data = payload.get("data") or []
    if not isinstance(data, list):
        return [], "bad_data"
    return [item for item in data if isinstance(item, dict)], "ok"


def self_sub_reasons(code: str, sub_concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sub in sub_concepts:
        if not isinstance(sub, dict):
            continue
        sub_name = compact(sub.get("name"), 128)
        sub_id = compact(sub.get("subdivisionId") or sub.get("blockId"), 64)
        for stock in sub.get("stocks") or []:
            if not isinstance(stock, dict):
                continue
            if str(stock.get("stockCode") or "") != code:
                continue
            reason = compact(stock.get("explain"), 2048)
            if reason:
                out.append(
                    {
                        "sub_name": sub_name,
                        "sub_id": sub_id,
                        "sub_explain": compact(sub.get("explain"), 2048),
                        "reason": reason,
                        "stock_name": compact(stock.get("stockName"), 64),
                        "market_id": compact(stock.get("marketId"), 32),
                    }
                )
    return out


def normalize_row(stock: dict[str, str], item: dict[str, Any]) -> dict[str, Any]:
    sub_concepts = item.get("sub_concepts") if isinstance(item.get("sub_concepts"), list) else []
    return {
        "code": stock["code"],
        "stock_name": stock["stock_name"],
        "market_id": stock["market_id"],
        "concept_name": compact(item.get("name"), 128),
        "concept_id": compact(item.get("concept_id"), 64),
        "quote_code": compact(item.get("quote_code"), 32),
        "concept_market_id": compact(item.get("market_id"), 32),
        "fit_rank": item.get("fit_rank") or 0,
        "tags": item.get("tags") if isinstance(item.get("tags"), list) else [],
        "reason_explain": compact(item.get("explain"), 2048),
        "sub_concepts_json": sub_concepts,
        "self_sub_reasons_json": self_sub_reasons(stock["code"], sub_concepts),
        "leading_json": item.get("leading") if isinstance(item.get("leading"), list) else [],
        "raw_json": item,
    }


def write_rows(config: MySqlConfig, rows: list[dict[str, Any]], chunk_size: int) -> int:
    written = 0
    for idx in range(0, len(rows), chunk_size):
        group = rows[idx : idx + chunk_size]
        values: list[str] = []
        for row in group:
            values.append(
                "("
                + ",".join(
                    [
                        sql_string(row["code"]),
                        sql_string(row["stock_name"]),
                        sql_string(row["market_id"]),
                        sql_string(row["concept_name"]),
                        sql_string(row["concept_id"]),
                        sql_string(row["quote_code"]),
                        sql_string(row["concept_market_id"]),
                        sql_int(row["fit_rank"]),
                        sql_json(row["tags"]),
                        sql_string(row["reason_explain"]),
                        sql_json(row["sub_concepts_json"]),
                        sql_json(row["self_sub_reasons_json"]),
                        sql_json(row["leading_json"]),
                        sql_json(row["raw_json"]),
                    ]
                )
                + ")"
            )
        sql = f"""
        INSERT INTO ths_stock_concept_explanations(
          code, stock_name, market_id, concept_name, concept_id, quote_code, concept_market_id,
          fit_rank, tags, reason_explain, sub_concepts_json, self_sub_reasons_json, leading_json, raw_json
        )
        VALUES {",".join(values)}
        ON DUPLICATE KEY UPDATE
          stock_name=VALUES(stock_name),
          market_id=VALUES(market_id),
          concept_id=VALUES(concept_id),
          quote_code=VALUES(quote_code),
          concept_market_id=VALUES(concept_market_id),
          fit_rank=VALUES(fit_rank),
          tags=VALUES(tags),
          reason_explain=VALUES(reason_explain),
          sub_concepts_json=VALUES(sub_concepts_json),
          self_sub_reasons_json=VALUES(self_sub_reasons_json),
          leading_json=VALUES(leading_json),
          raw_json=VALUES(raw_json),
          fetched_at=NOW(3);
        """
        run_mysql(config, sql)
        written += len(group)
    return written


def delete_existing_codes(config: MySqlConfig, codes: list[str]) -> None:
    clean_codes = sorted({code for code in codes if code})
    if not clean_codes:
        return
    for idx in range(0, len(clean_codes), 500):
        group = clean_codes[idx : idx + 500]
        code_sql = ",".join(sql_string(code) for code in group)
        run_mysql(config, f"DELETE FROM ths_stock_concept_explanations WHERE code IN ({code_sql});")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect THS stock concept explanations from F10 concept page.")
    parser.add_argument("--code", default="", help="Collect one stock code only.")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--pause", type=float, default=0.08)
    parser.add_argument("--chunk-size", type=int, default=300)
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    stocks = load_universe(config, args.offset, args.limit, args.code.strip())
    rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    ok_codes: list[str] = []
    for stock in stocks:
        data, status = fetch_stock_concepts(stock["code"], stock["market_id"], args.timeout)
        status_counts[status] = status_counts.get(status, 0) + 1
        if status == "ok":
            ok_codes.append(stock["code"])
        rows.extend(normalize_row(stock, item) for item in data if compact(item.get("name"), 128))
        if args.pause:
            time.sleep(args.pause)
    delete_existing_codes(config, ok_codes)
    written = write_rows(config, rows, max(1, args.chunk_size)) if rows else 0
    print(
        json.dumps(
            {
                "collected_at": datetime.now().isoformat(timespec="seconds"),
                "stocks": len(stocks),
                "rows": len(rows),
                "written": written,
                "deleted_codes": len(ok_codes),
                "status_counts": status_counts,
                "offset": args.offset,
                "limit": args.limit,
                "code": args.code.strip(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
