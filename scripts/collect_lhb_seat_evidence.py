#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
import json
import re
from typing import Any

import akshare as ak
import pandas as pd

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_string,
)


FAMOUS_TRADER_SEATS: dict[str, str] = {
    "开源证券股份有限公司西安太华路证券营业部": "西安太华路",
    "国泰君安证券股份有限公司南京太平南路证券营业部": "南京太平南路",
    "中国银河证券股份有限公司北京中关村大街证券营业部": "北京中关村",
    "财通证券股份有限公司杭州上塘路证券营业部": "杭州上塘路",
    "国盛证券有限责任公司宁波桑田路证券营业部": "宁波桑田路",
    "华鑫证券有限责任公司上海分公司": "华鑫上海分",
    "东亚前海证券有限责任公司上海分公司": "东亚前海上海分",
    "中信证券股份有限公司上海溧阳路证券营业部": "上海溧阳路",
    "国泰君安证券股份有限公司上海江苏路证券营业部": "上海江苏路",
    "中信建投证券股份有限公司北京广渠门内大街证券营业部": "北京广渠门",
    "东方财富证券股份有限公司拉萨团结路第一证券营业部": "拉萨团结路一",
    "东方财富证券股份有限公司拉萨团结路第二证券营业部": "拉萨团结路二",
    "东方财富证券股份有限公司拉萨东环路第一证券营业部": "拉萨东环路一",
    "东方财富证券股份有限公司拉萨东环路第二证券营业部": "拉萨东环路二",
}

TRADER_ALIAS_RULES: list[dict[str, str]] = [
    {
        "keyword": "开源证券股份有限公司西安太华路证券营业部",
        "alias": "炒股养家",
        "style": "知名游资",
    },
    {
        "keyword": "东北证券股份有限公司佛山分公司",
        "alias": "佛山系",
        "style": "活跃游资",
    },
    {
        "keyword": "国泰君安证券股份有限公司南京太平南路证券营业部",
        "alias": "作手新一",
        "style": "知名游资",
    },
    {
        "keyword": "财通证券股份有限公司杭州上塘路证券营业部",
        "alias": "上塘路",
        "style": "知名游资",
    },
    {
        "keyword": "国盛证券有限责任公司宁波桑田路证券营业部",
        "alias": "宁波桑田路",
        "style": "知名游资",
    },
    {
        "keyword": "中信证券股份有限公司上海溧阳路证券营业部",
        "alias": "孙哥",
        "style": "知名游资",
    },
]


def ensure_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_lhb_seat_evidence (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      close_price DECIMAL(12,4) NULL,
      pct_change DECIMAL(10,4) NULL,
      lhb_reason VARCHAR(512) NOT NULL DEFAULT '',
      lhb_interpretation VARCHAR(512) NOT NULL DEFAULT '',
      total_net_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      total_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      total_sell DECIMAL(18,2) NOT NULL DEFAULT 0,
      net_buy_ratio DECIMAL(10,4) NULL,
      famous_trader_count INT NOT NULL DEFAULT 0,
      famous_trader_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      famous_trader_net_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      famous_trader_summary TEXT NULL,
      institution_buy_count INT NOT NULL DEFAULT 0,
      institution_sell_count INT NOT NULL DEFAULT 0,
      institution_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      institution_net_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      institution_summary TEXT NULL,
      northbound_net_buy DECIMAL(18,2) NOT NULL DEFAULT 0,
      top_buy_seat VARCHAR(255) NOT NULL DEFAULT '',
      top_buy_amount DECIMAL(18,2) NOT NULL DEFAULT 0,
      seat_signal_label VARCHAR(64) NOT NULL DEFAULT '',
      seat_signal_score DECIMAL(8,2) NOT NULL DEFAULT 0,
      key_facts JSON NULL,
      buy_seats JSON NULL,
      sell_seats JSON NULL,
      raw_json JSON NULL,
      source VARCHAR(64) NOT NULL DEFAULT 'eastmoney_akshare',
      collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_lhb_seat_day_code (trade_date, code),
      KEY idx_lhb_seat_code (code, trade_date),
      KEY idx_lhb_seat_signal (trade_date, seat_signal_score)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def clean_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def to_float(value: Any) -> float:
    try:
        if pd.isna(value):
            return 0.0
        text = str(value or "").replace("%", "").replace(",", "").strip()
        return float(text) if text else 0.0
    except Exception:
        return 0.0


def sql_number(value: Any) -> str:
    return str(to_float(value))


def normalize_date(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text or date.today().strftime("%Y-%m-%d")


def compact(text: str, limit: int = 120) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def df_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, item in df.iterrows():
        raw = {str(k): (None if pd.isna(v) else v) for k, v in item.to_dict().items()}
        seat = str(raw.get("交易营业部名称") or "").strip()
        if not seat:
            continue
        out.append(
            {
                "rank_no": int(to_float(raw.get("序号"))),
                "seat_name": seat,
                "buy_amount": to_float(raw.get("买入金额")),
                "buy_ratio": to_float(raw.get("买入金额-占总成交比例")),
                "sell_amount": to_float(raw.get("卖出金额")),
                "sell_ratio": to_float(raw.get("卖出金额-占总成交比例")),
                "net_buy": to_float(raw.get("净额")),
                "reason": str(raw.get("类型") or "").strip(),
            }
        )
    return dedupe_seats(out)


def dedupe_seats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("seat_name") or "").strip()
        if not key:
            continue
        current = merged.get(key)
        if not current:
            merged[key] = dict(row)
            continue
        current["rank_no"] = min(int(current.get("rank_no") or 999), int(row.get("rank_no") or 999))
        for field in ["buy_amount", "sell_amount", "net_buy", "buy_ratio", "sell_ratio"]:
            current[field] = max(float(current.get(field) or 0), float(row.get(field) or 0), key=abs)
        if row.get("reason") and str(row.get("reason")) not in str(current.get("reason") or ""):
            current["reason"] = f"{current.get('reason', '')};{row.get('reason')}"
    return sorted(merged.values(), key=lambda item: int(item.get("rank_no") or 999))


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if pd.isna(value) if not isinstance(value, (dict, list, tuple, str, bytes)) else False:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return value


def famous_label(seat_name: str) -> str:
    alias = trader_alias(seat_name).get("alias", "")
    if alias:
        return alias
    return ""


def trader_alias(seat_name: str) -> dict[str, str]:
    text = str(seat_name or "")
    for rule in TRADER_ALIAS_RULES:
        if rule["keyword"] in text:
            return rule
    return {}


def seat_evidence_name(seat: dict[str, Any]) -> str:
    alias = trader_alias(seat.get("seat_name", ""))
    if alias:
        return f"{alias['alias']}({alias.get('style', '游资')})"
    name = str(seat.get("seat_name") or "")
    if "股通专用" in name:
        return "股通"
    if "机构专用" in name:
        return "机构"
    return ""


def seat_brief(seat: dict[str, Any]) -> str:
    label = seat_evidence_name(seat)
    if not label:
        return ""
    return f"{label}买{seat['buy_amount'] / 100000000:.2f}亿，净买{seat['net_buy'] / 100000000:.2f}亿"


def build_signal(row: dict[str, Any], buy_seats: list[dict[str, Any]], sell_seats: list[dict[str, Any]]) -> dict[str, Any]:
    famous = [seat for seat in buy_seats if famous_label(seat["seat_name"]) and seat["buy_amount"] >= 30_000_000 and seat["net_buy"] > 0]
    institutions_buy = [seat for seat in buy_seats if "机构专用" in seat["seat_name"] and seat["net_buy"] > 0]
    institutions_sell = [seat for seat in sell_seats if "机构专用" in seat["seat_name"] and seat["net_buy"] < 0]
    northbound = [seat for seat in buy_seats + sell_seats if "股通专用" in seat["seat_name"]]
    top_buy = buy_seats[0] if buy_seats else {}

    famous_buy = sum(seat["buy_amount"] for seat in famous)
    famous_net = sum(seat["net_buy"] for seat in famous)
    inst_buy = sum(seat["buy_amount"] for seat in institutions_buy)
    inst_net = sum(seat["net_buy"] for seat in institutions_buy)
    northbound_net = sum(seat["net_buy"] for seat in northbound)

    score = 0.0
    facts: list[str] = []
    trade_date = str(row.get("trade_date") or "").strip()
    time_prefix = f"当日龙虎榜{trade_date}：" if trade_date else "当日龙虎榜："
    def add_fact(text: str) -> None:
        value = compact(text, 160)
        if not value:
            return
        facts.append((time_prefix if not facts else "") + value)

    if famous:
        score += min(35.0, 12 + famous_buy / 20_000_000)
        briefs = [seat_brief(seat) for seat in famous[:3]]
        briefs = [item for item in briefs if item]
        if briefs:
            add_fact("；".join(briefs))
    if len(institutions_buy) >= 2:
        score += min(30.0, 10 + inst_net / 30_000_000)
        add_fact(f"{len(institutions_buy)}家机构净买，合计净买{inst_net / 100000000:.2f}亿")
    elif len(institutions_buy) == 1:
        score += min(16.0, 6 + inst_net / 50_000_000)
        add_fact(f"1家机构净买{inst_net / 100000000:.2f}亿")
    if northbound_net > 50_000_000:
        score += 8
        add_fact(f"股通净买{northbound_net / 100000000:.2f}亿")
    top_buy_name = seat_evidence_name(top_buy) if top_buy else ""
    if top_buy_name and top_buy and top_buy.get("buy_amount", 0) >= 100_000_000 and top_buy.get("net_buy", 0) > 0:
        score += 8
        add_fact(f"买一{top_buy_name}买入{top_buy['buy_amount'] / 100000000:.2f}亿")
    if institutions_sell:
        score -= min(18.0, len(institutions_sell) * 5 + abs(sum(seat["net_buy"] for seat in institutions_sell)) / 50_000_000)
        add_fact(f"{len(institutions_sell)}家机构净卖")

    if institutions_buy and institutions_sell and len(institutions_sell) >= len(institutions_buy):
        label = "机构分歧"
    elif famous and len(institutions_buy) >= 2:
        label = "游资+机构共买"
    elif famous:
        label = "知名游资大买"
    elif len(institutions_buy) >= 2:
        label = "多机构买入"
    elif northbound_net > 50_000_000:
        label = "股通买入"
    elif score > 0:
        label = "席位偏强"
    else:
        label = "席位一般"

    return {
        "famous_trader_count": len(famous),
        "famous_trader_buy": famous_buy,
        "famous_trader_net_buy": famous_net,
        "famous_trader_summary": "；".join(seat_brief(seat) for seat in famous[:3]),
        "institution_buy_count": len(institutions_buy),
        "institution_sell_count": len(institutions_sell),
        "institution_buy": inst_buy,
        "institution_net_buy": inst_net,
        "institution_summary": f"{len(institutions_buy)}买/{len(institutions_sell)}卖，机构净买{inst_net / 100000000:.2f}亿" if institutions_buy or institutions_sell else "",
        "northbound_net_buy": northbound_net,
        "top_buy_seat": str(top_buy.get("seat_name") or ""),
        "top_buy_amount": float(top_buy.get("buy_amount") or 0),
        "seat_signal_label": label,
        "seat_signal_score": max(0.0, min(100.0, score)),
        "key_facts": facts[:4],
    }


def daily_rows(trade_date: str) -> list[dict[str, Any]]:
    df = ak.stock_lhb_detail_em(start_date=trade_date.replace("-", ""), end_date=trade_date.replace("-", ""))
    if not isinstance(df, pd.DataFrame) or df.empty:
        return []
    rows: list[dict[str, Any]] = []
    for _, item in df.iterrows():
        raw = {str(k): (None if pd.isna(v) else v) for k, v in item.to_dict().items()}
        code = clean_code(raw.get("代码"))
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "stock_name": str(raw.get("名称") or "").strip(),
                "close_price": to_float(raw.get("收盘价")),
                "pct_change": to_float(raw.get("涨跌幅")),
                "lhb_reason": str(raw.get("上榜原因") or "").strip(),
                "lhb_interpretation": str(raw.get("解读") or "").strip(),
                "total_net_buy": to_float(raw.get("龙虎榜净买额")),
                "total_buy": to_float(raw.get("龙虎榜买入额")),
                "total_sell": to_float(raw.get("龙虎榜卖出额")),
                "net_buy_ratio": to_float(raw.get("净买额占总成交比")),
                "raw_daily": raw,
            }
        )
    return rows


def collect_stock(row: dict[str, Any], trade_date: str) -> dict[str, Any]:
    code = row["code"]
    date_key = trade_date.replace("-", "")
    row = {**row, "trade_date": trade_date}
    buy_seats = df_records(ak.stock_lhb_stock_detail_em(symbol=code, date=date_key, flag="买入"))
    sell_seats = df_records(ak.stock_lhb_stock_detail_em(symbol=code, date=date_key, flag="卖出"))
    signal = build_signal(row, buy_seats, sell_seats)
    return {**row, **signal, "buy_seats": buy_seats, "sell_seats": sell_seats}


def upsert(config: MySqlConfig, rows: list[dict[str, Any]], trade_date: str) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        statements.append(
            f"""
            INSERT INTO stock_lhb_seat_evidence(
              trade_date, code, stock_name, close_price, pct_change, lhb_reason, lhb_interpretation,
              total_net_buy, total_buy, total_sell, net_buy_ratio,
              famous_trader_count, famous_trader_buy, famous_trader_net_buy, famous_trader_summary,
              institution_buy_count, institution_sell_count, institution_buy, institution_net_buy, institution_summary,
              northbound_net_buy, top_buy_seat, top_buy_amount, seat_signal_label, seat_signal_score,
              key_facts, buy_seats, sell_seats, raw_json
            ) VALUES (
              {sql_string(trade_date)},
              {sql_string(row['code'])},
              {sql_string(row.get('stock_name') or '')},
              {sql_number(row.get('close_price'))},
              {sql_number(row.get('pct_change'))},
              {sql_string(row.get('lhb_reason') or '')},
              {sql_string(row.get('lhb_interpretation') or '')},
              {sql_number(row.get('total_net_buy'))},
              {sql_number(row.get('total_buy'))},
              {sql_number(row.get('total_sell'))},
              {sql_number(row.get('net_buy_ratio'))},
              {int(row.get('famous_trader_count') or 0)},
              {sql_number(row.get('famous_trader_buy'))},
              {sql_number(row.get('famous_trader_net_buy'))},
              {sql_string(row.get('famous_trader_summary') or '')},
              {int(row.get('institution_buy_count') or 0)},
              {int(row.get('institution_sell_count') or 0)},
              {sql_number(row.get('institution_buy'))},
              {sql_number(row.get('institution_net_buy'))},
              {sql_string(row.get('institution_summary') or '')},
              {sql_number(row.get('northbound_net_buy'))},
              {sql_string(row.get('top_buy_seat') or '')},
              {sql_number(row.get('top_buy_amount'))},
              {sql_string(row.get('seat_signal_label') or '')},
              {sql_number(row.get('seat_signal_score'))},
              {sql_json(row.get('key_facts') or [])},
              {sql_json(json_safe(row.get('buy_seats') or []))},
              {sql_json(json_safe(row.get('sell_seats') or []))},
              {sql_json(json_safe(row.get('raw_daily') or {}))}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name),
              close_price=VALUES(close_price),
              pct_change=VALUES(pct_change),
              lhb_reason=VALUES(lhb_reason),
              lhb_interpretation=VALUES(lhb_interpretation),
              total_net_buy=VALUES(total_net_buy),
              total_buy=VALUES(total_buy),
              total_sell=VALUES(total_sell),
              net_buy_ratio=VALUES(net_buy_ratio),
              famous_trader_count=VALUES(famous_trader_count),
              famous_trader_buy=VALUES(famous_trader_buy),
              famous_trader_net_buy=VALUES(famous_trader_net_buy),
              famous_trader_summary=VALUES(famous_trader_summary),
              institution_buy_count=VALUES(institution_buy_count),
              institution_sell_count=VALUES(institution_sell_count),
              institution_buy=VALUES(institution_buy),
              institution_net_buy=VALUES(institution_net_buy),
              institution_summary=VALUES(institution_summary),
              northbound_net_buy=VALUES(northbound_net_buy),
              top_buy_seat=VALUES(top_buy_seat),
              top_buy_amount=VALUES(top_buy_amount),
              seat_signal_label=VALUES(seat_signal_label),
              seat_signal_score=VALUES(seat_signal_score),
              key_facts=VALUES(key_facts),
              buy_seats=VALUES(buy_seats),
              sell_seats=VALUES(sell_seats),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    run_mysql(config, "\n".join(statements))
    return len(rows)


def target_codes_from_judgements(config: MySqlConfig, trade_date: str, limit: int) -> set[str]:
    sql = f"""
    SELECT code
    FROM stock_move_judgements
    WHERE trade_date={sql_string(trade_date)}
    GROUP BY code
    ORDER BY MAX(sustainability_score) DESC
    LIMIT {int(limit)};
    """
    return {row[0] for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)) if row}


def collect(config: MySqlConfig, trade_date: str, limit: int, codes: set[str] | None = None) -> dict[str, Any]:
    ensure_table(config)
    rows = daily_rows(trade_date)
    if codes:
        rows = [row for row in rows if row["code"] in codes]
    rows = rows[: int(limit)] if limit > 0 else rows
    collected: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in rows:
        try:
            collected.append(collect_stock(row, trade_date))
        except Exception as exc:
            errors.append(f"{row['code']}:{type(exc).__name__}:{exc}")
    written = upsert(config, collected, trade_date)
    return {"trade_date": trade_date, "candidates": len(rows), "written": written, "errors": errors[:5]}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect useful LHB seat-structure evidence.")
    parser.add_argument("--trade-date", default=date.today().strftime("%Y-%m-%d"))
    parser.add_argument("--limit", type=int, default=120)
    parser.add_argument("--codes", default="", help="Comma separated stock codes. Defaults to all LHB daily rows.")
    parser.add_argument("--judgement-codes", action="store_true", help="Only collect stocks in stock_move_judgements for the date.")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    trade_date = normalize_date(args.trade_date)
    codes = {clean_code(item) for item in args.codes.split(",") if clean_code(item)}
    if args.judgement_codes:
        codes |= target_codes_from_judgements(config, trade_date, args.limit)
    result = collect(config, trade_date, args.limit, codes or None)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
