#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_auction_trend_summary_rows,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_string,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def to_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except Exception:
        return 0.0


def to_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except Exception:
        return 0


def parse_json_text(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def read_minute_rows(config: Any, trade_date: str) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      trade_date,
      DATE_FORMAT(snapshot_minute, '%Y-%m-%d %H:%i:%s'),
      DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
      analysis_kind,
      rank_no,
      code,
      stock_name,
      COALESCE(auction_price, ''),
      COALESCE(preclose, ''),
      COALESCE(auction_pct, ''),
      COALESCE(auction_amount, ''),
      COALESCE(matched_volume, ''),
      COALESCE(limit_side, ''),
      COALESCE(seal_volume, ''),
      COALESCE(seal_amount, ''),
      COALESCE(theme_score, ''),
      COALESCE(CAST(theme_matches AS CHAR), '[]'),
      COALESCE(sector_hot_count, 0),
      COALESCE(concept_hot_count, 0),
      COALESCE(score, 0),
      COALESCE(NULLIF(risk_flags, ''), '-'),
      COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.seal_amount')), ''), '0')
    FROM auction_minute_analysis
    WHERE trade_date = {sql_string(trade_date)}
    ORDER BY snapshot_minute ASC, analysis_kind ASC, rank_no ASC;
    """
    keys = [
        "trade_date",
        "snapshot_minute",
        "captured_at",
        "analysis_kind",
        "rank_no",
        "code",
        "stock_name",
        "auction_price",
        "preclose",
        "auction_pct",
        "auction_amount",
        "matched_volume",
        "limit_side",
        "seal_volume",
        "seal_amount",
        "theme_score",
        "theme_matches",
        "sector_hot_count",
        "concept_hot_count",
        "score",
        "risk_flags",
        "raw_seal_amount",
    ]
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        item = dict(zip(keys, row))
        item["rank_no"] = to_int(item.get("rank_no"))
        for key in [
            "auction_price",
            "preclose",
            "auction_pct",
            "auction_amount",
            "matched_volume",
            "seal_volume",
            "seal_amount",
            "raw_seal_amount",
            "theme_score",
            "sector_hot_count",
            "concept_hot_count",
            "score",
        ]:
            item[key] = to_float(item.get(key))
        if item["seal_amount"] <= 0 and item["raw_seal_amount"] > 0:
            item["seal_amount"] = item["raw_seal_amount"]
        item["theme_matches"] = parse_json_text(str(item.get("theme_matches") or "")) or []
        rows.append(item)
    return rows


def read_candidate_rows(config: Any, trade_date: str) -> dict[str, dict[str, Any]]:
    sql = f"""
    SELECT
      rank_no,
      code,
      stock_name,
      COALESCE(auction_pct, ''),
      COALESCE(auction_amount, ''),
      COALESCE(theme_score, ''),
      COALESCE(CAST(theme_matches AS CHAR), '[]'),
      COALESCE(sector_hot_count, 0),
      COALESCE(concept_hot_count, 0),
      COALESCE(score, 0),
      COALESCE(NULLIF(risk_flags, ''), '-'),
      COALESCE(NULLIF(JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.seal_amount')), ''), '0')
    FROM auction_candidates
    WHERE trade_date = {sql_string(trade_date)}
    ORDER BY rank_no ASC;
    """
    result: dict[str, dict[str, Any]] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 12:
            continue
        item = {
            "final_candidate_rank": to_int(row[0]),
            "code": row[1],
            "stock_name": row[2],
            "last_auction_pct": to_float(row[3]),
            "last_auction_amount": to_float(row[4]),
            "theme_score": to_float(row[5]),
            "theme_matches": parse_json_text(row[6]) or [],
            "sector_hot_count": to_int(row[7]),
            "concept_hot_count": to_int(row[8]),
            "final_score": to_float(row[9]),
            "risk_flags": row[10],
            "final_seal_amount": to_float(row[11]),
        }
        result[item["code"]] = item
    return result


def first_last(values: list[dict[str, Any]], key: str) -> tuple[float, float]:
    useful = [item for item in values if to_float(item.get(key)) != 0]
    if not useful:
        return 0.0, 0.0
    return to_float(useful[0].get(key)), to_float(useful[-1].get(key))


def build_key_points(row: dict[str, Any]) -> list[str]:
    points: list[str] = []
    pct = to_float(row.get("last_auction_pct"))
    amount = to_float(row.get("last_auction_amount"))
    pct_delta = to_float(row.get("pct_delta"))
    growth = to_float(row.get("amount_growth_ratio"))
    seal = to_float(row.get("last_seal_amount"))
    theme = to_float(row.get("theme_score"))
    if pct >= 9.5:
        points.append(f"竞价接近涨停：{pct:.2f}%")
    elif pct >= 5:
        points.append(f"高开明显：{pct:.2f}%")
    elif pct > 0:
        points.append(f"小幅高开：{pct:.2f}%")
    else:
        points.append(f"竞价偏弱：{pct:.2f}%")
    if amount >= 100_000_000:
        points.append(f"竞价金额过亿：{amount / 100_000_000:.2f}亿")
    elif amount >= 30_000_000:
        points.append(f"竞价金额可看：{amount / 100_000_000:.2f}亿")
    if pct_delta > 0.5:
        points.append(f"高开抬升：+{pct_delta:.2f}pct")
    elif pct_delta < -0.5:
        points.append(f"高开回落：{pct_delta:.2f}pct")
    if growth >= 0.5:
        points.append("金额放大")
    if seal >= 100_000_000:
        points.append(f"封单强：{seal / 100_000_000:.2f}亿")
    elif seal >= 30_000_000:
        points.append(f"有封单：{seal / 100_000_000:.2f}亿")
    if theme > 0:
        points.append(f"主题命中：{theme:.1f}分")
    return points[:6]


def trend_label_and_action(row: dict[str, Any]) -> tuple[str, str]:
    score = to_float(row.get("trend_score"))
    pct = to_float(row.get("last_auction_pct"))
    pct_delta = to_float(row.get("pct_delta"))
    amount = to_float(row.get("last_auction_amount"))
    seal = to_float(row.get("last_seal_amount"))
    theme = to_float(row.get("theme_score"))
    if score >= 85:
        return "竞价强", "优先看开盘承接，放量不回落再跟踪"
    if score >= 70:
        return "竞价偏强", "观察前5分钟承接和板块跟随"
    if pct >= 7 and pct_delta < -1:
        return "高开回落", "谨慎，等开盘重新转强"
    if amount < 30_000_000 and seal < 30_000_000 and theme <= 0:
        return "证据不足", "先放观察池，不急"
    return "观察", "等开盘量价确认"


def score_summary(row: dict[str, Any]) -> float:
    pct = to_float(row.get("last_auction_pct"))
    amount = to_float(row.get("last_auction_amount"))
    pct_delta = to_float(row.get("pct_delta"))
    growth = to_float(row.get("amount_growth_ratio"))
    seal = to_float(row.get("last_seal_amount"))
    theme = to_float(row.get("theme_score"))
    pct_top_count = to_int(row.get("pct_top_count"))
    final_rank = to_int(row.get("final_candidate_rank"))
    score = 0.0
    score += max(0.0, min(30.0, pct * 3.0))
    if amount > 0:
        score += min(20.0, amount / 10_000_000)
    score += max(-8.0, min(12.0, pct_delta * 4.0))
    score += min(10.0, max(0.0, growth) * 8.0)
    score += min(15.0, seal / 20_000_000)
    score += min(12.0, theme)
    score += min(8.0, pct_top_count * 2.0)
    if final_rank > 0:
        score += max(0.0, 8.0 - final_rank * 0.15)
    return round(max(0.0, min(100.0, score)), 2)


def build_summary_rows(args: argparse.Namespace, config: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    trade_date = args.trade_date or datetime.now().date().isoformat()
    minute_rows = read_minute_rows(config, trade_date)
    candidate_by_code = read_candidate_rows(config, trade_date)
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in minute_rows:
        by_code[str(row.get("code"))].append(row)
    for code, candidate in candidate_by_code.items():
        by_code.setdefault(code, [])

    summaries: list[dict[str, Any]] = []
    for code, rows in by_code.items():
        rows.sort(key=lambda item: str(item.get("snapshot_minute") or ""))
        pct_rows = [row for row in rows if row.get("analysis_kind") == "pct_top10"]
        up_rows = [row for row in rows if row.get("analysis_kind") == "limit_up_order"]
        down_rows = [row for row in rows if row.get("analysis_kind") == "limit_down_order"]
        metric_rows = pct_rows or rows
        candidate = candidate_by_code.get(code, {})
        first_pct, last_pct = first_last(metric_rows, "auction_pct")
        first_amount, last_amount = first_last(metric_rows, "auction_amount")
        if candidate:
            last_pct = to_float(candidate.get("last_auction_pct")) or last_pct
            last_amount = to_float(candidate.get("last_auction_amount")) or last_amount
        amount_delta = last_amount - first_amount if first_amount or last_amount else 0.0
        growth = amount_delta / first_amount if first_amount > 0 else (1.0 if last_amount > 0 else 0.0)
        stock_name = str((candidate or rows[-1] if rows else candidate).get("stock_name") or "")
        best_pct_rank = min([to_int(row.get("rank_no")) for row in pct_rows if to_int(row.get("rank_no")) > 0] or [0])
        candidate_seal = to_float(candidate.get("final_seal_amount"))
        last_seal = candidate_seal or (to_float(up_rows[-1].get("seal_amount")) if up_rows else 0.0)
        max_seal = max([to_float(row.get("seal_amount")) for row in up_rows] + [candidate_seal, 0.0])
        limit_up_count = len(up_rows) or (1 if candidate_seal > 0 else 0)
        summary = {
            "trade_date": trade_date,
            "code": code,
            "stock_name": stock_name,
            "first_seen_minute": rows[0].get("snapshot_minute", "") if rows else "",
            "last_seen_minute": rows[-1].get("snapshot_minute", "") if rows else "",
            "minute_count": len({row.get("snapshot_minute") for row in rows}),
            "pct_top_count": len(pct_rows),
            "limit_up_count": limit_up_count,
            "limit_down_count": len(down_rows),
            "best_pct_rank": best_pct_rank,
            "final_candidate_rank": to_int(candidate.get("final_candidate_rank")),
            "first_auction_pct": round(first_pct, 4),
            "last_auction_pct": round(last_pct, 4),
            "pct_delta": round(last_pct - first_pct, 4),
            "first_auction_amount": round(first_amount, 2),
            "last_auction_amount": round(last_amount, 2),
            "amount_delta": round(amount_delta, 2),
            "amount_growth_ratio": round(growth, 4),
            "max_seal_amount": round(max_seal, 2),
            "last_seal_amount": round(last_seal, 2),
            "theme_score": max([to_float(row.get("theme_score")) for row in rows] + [to_float(candidate.get("theme_score"))]),
            "theme_matches": candidate.get("theme_matches") or next((row.get("theme_matches") for row in rows if row.get("theme_matches")), []),
            "sector_hot_count": max([to_int(row.get("sector_hot_count")) for row in rows] + [to_int(candidate.get("sector_hot_count"))]),
            "concept_hot_count": max([to_int(row.get("concept_hot_count")) for row in rows] + [to_int(candidate.get("concept_hot_count"))]),
            "final_score": to_float(candidate.get("final_score")),
            "generated_at": now_text(),
        }
        summary["trend_score"] = score_summary(summary)
        summary["trend_label"], summary["action_hint"] = trend_label_and_action(summary)
        summary["key_points"] = build_key_points(summary)
        summary["raw_json"] = {"minute_rows": rows, "candidate": candidate}
        summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            -to_float(item.get("trend_score")),
            to_int(item.get("final_candidate_rank")) or 9999,
            -to_float(item.get("last_auction_amount")),
        )
    )
    return summaries[: args.limit], {
        "trade_date": trade_date,
        "minute_row_count": len(minute_rows),
        "candidate_count": len(candidate_by_code),
        "summary_count": min(len(summaries), args.limit),
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build call auction trend summary from minute radar rows.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default="")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "data_tasks" / "auction_trend_summary.json")
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
    rows, meta = build_summary_rows(args, config)
    imported = import_auction_trend_summary_rows(config, rows)
    payload = {"ok": True, "built_at": now_text(), **meta, "imported": imported, "rows": rows}
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, **meta, "imported": imported, "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
