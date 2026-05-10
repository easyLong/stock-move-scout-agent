from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests


COLUMNS = [
    "source_type",
    "fetched_at",
    "captured_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "lookback_days",
    "announcement_count",
    "hard_evidence_strength",
    "hard_catalyst_summary",
    "hard_catalyst_types",
    "hard_catalyst_items",
    "stone_evidence_summary",
    "order_cooperation_evidence",
    "order_cooperation_hard_evidence",
    "amount_terms_evidence",
    "partner_customer_evidence",
    "financial_evidence",
    "ma_evidence",
    "risk_evidence",
    "source_urls",
    "detail_source_urls",
    "source_status",
    "detail_source_status",
    "evidence_gap",
]

TYPE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("订单/合作", ["订单", "重大合同", "销售合同", "采购合同", "中标", "合作协议", "战略合作", "客户定点", "供应商定点"]),
    ("并购/重组", ["重大资产购买", "资产重组", "购买资产", "收购股权", "收购控股权", "股权转让", "增资参股", "对外投资"]),
    ("业绩/财报", ["季度报告", "一季度报告", "半年度报告", "年度报告", "业绩预告", "业绩快报", "扭亏", "主要经营数据"]),
    ("产品/技术", ["产品", "研发", "专利", "注册证", "认证", "许可", "临床", "芯片", "量产"]),
    ("风险/减持", ["风险", "减持", "亏损", "问询", "监管", "诉讼", "担保", "质押", "退市", "处罚"]),
    ("治理/激励", ["董事会", "股东大会", "股权激励", "限制性股票", "回购", "薪酬", "章程"]),
]

STRONG_TYPES = {"订单/合作", "并购/重组", "业绩/财报", "产品/技术"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def market_symbol(code: str) -> str:
    if code.startswith(("6", "9")):
        return f"SH{code}"
    return f"SZ{code}"


def ths_market_id(code: str) -> str:
    return "17" if code.startswith(("6", "9")) else "33"


def date_from_row(row: dict[str, str]) -> datetime:
    for key in ["captured_at", "fetched_at", "scan_started_at"]:
        value = str(row.get(key, "")).strip()
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value[:19] if fmt.endswith("%S") else value[:10], fmt)
            except Exception:
                pass
    return datetime.now()


def classify_title(title: str) -> list[str]:
    hits = []
    for label, keywords in TYPE_KEYWORDS:
        if any(keyword in title for keyword in keywords):
            hits.append(label)
    return hits or ["其他公告"]


def fetch_ths_announcements(code: str, max_pages: int, page_size: int, timeout: int) -> tuple[list[dict[str, str]], str]:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Referer": f"https://basic.10jqka.com.cn/{code}/news.html",
        }
    )
    rows: list[dict[str, str]] = []
    status_parts: list[str] = []
    for page in range(1, max_pages + 1):
        url = (
            "https://basic.10jqka.com.cn/basicapi/notice/pub"
            f"?type=stock&limit={page_size}&page={page}&code={code}&classify=all&market={ths_market_id(code)}"
        )
        try:
            response = session.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            status_parts.append(f"page_{page}_error:{type(exc).__name__}:{exc}")
            break
        if int(payload.get("status_code", -1)) != 0:
            status_parts.append(f"page_{page}_status:{payload.get('status_code')}")
            break
        data = payload.get("data") or {}
        items = data.get("data") or []
        for item in items:
            title = compact(item.get("title", ""), 300)
            if not title:
                continue
            rows.append(
                {
                    "date": compact(item.get("date", ""), 32),
                    "title": title,
                    "url": compact(item.get("raw_url") or item.get("pc_url") or item.get("mobile_url"), 1024),
                    "pc_url": compact(item.get("pc_url", ""), 1024),
                    "seq": compact(item.get("seq", ""), 64),
                    "types": "、".join(classify_title(title)),
                }
            )
        if len(items) < page_size:
            break
        time.sleep(0.15)
    status = ";".join(status_parts) if status_parts else f"ths_ok:pages={max_pages}"
    return rows, status


def filter_recent(rows: list[dict[str, str]], start: datetime, end: datetime) -> list[dict[str, str]]:
    result = []
    for row in rows:
        try:
            day = datetime.strptime(row.get("date", "")[:10], "%Y-%m-%d")
        except Exception:
            continue
        if start <= day <= end:
            result.append(row)
    return result


def pick_items(rows: list[dict[str, str]], wanted: set[str], limit: int = 4) -> list[dict[str, str]]:
    picked = []
    for row in rows:
        if set(row.get("types", "").split("、")) & wanted:
            picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def format_items(rows: list[dict[str, str]], limit: int = 6) -> str:
    return " || ".join(
        f"{row.get('date', '')} [{row.get('types', '')}] {row.get('title', '')} {row.get('url', '')}".strip()
        for row in rows[:limit]
    )


def summarize_strength(rows: list[dict[str, str]]) -> tuple[str, str, str]:
    useful = [row for row in rows if row.get("types") != "其他公告"]
    if not useful:
        return "missing", "同花顺公告未发现近期硬催化标题。", ""
    type_counter: dict[str, int] = {}
    for row in useful:
        for label in row.get("types", "").split("、"):
            type_counter[label] = type_counter.get(label, 0) + 1
    types = [key for key, _ in sorted(type_counter.items(), key=lambda item: item[1], reverse=True)]
    if pick_items(useful, {"订单/合作", "并购/重组"}, 1):
        strength = "strong"
    elif pick_items(useful, {"业绩/财报", "产品/技术"}, 1):
        strength = "medium"
    elif pick_items(useful, {"风险/减持"}, 1):
        strength = "weak"
    else:
        strength = "weak"
    summary_items = pick_items(useful, STRONG_TYPES | {"风险/减持"}, 3) or useful[:3]
    summary = "；".join(f"{row['date']} {row['title']}" for row in summary_items)
    return strength, summary, "、".join(types[:6])


def build_one(stock: dict[str, str], lookback_days: int, max_items: int, max_pages: int, page_size: int, timeout: int) -> dict[str, Any]:
    code = stock.get("code", "").strip()
    anchor = date_from_row(stock)
    end = anchor + timedelta(days=1)
    start = anchor - timedelta(days=lookback_days)
    announcements, source_status = fetch_ths_announcements(code, max_pages, page_size, timeout)
    recent = filter_recent(announcements, start, end)[:max_items]
    useful = [row for row in recent if row.get("types") != "其他公告"]
    strength, summary, types = summarize_strength(recent)
    order_items = pick_items(useful, {"订单/合作"}, 4)
    financial_items = pick_items(useful, {"业绩/财报"}, 4)
    ma_items = pick_items(useful, {"并购/重组"}, 4)
    risk_items = pick_items(useful, {"风险/减持"}, 4)
    urls = " | ".join(dict.fromkeys(row.get("url", "") for row in useful if row.get("url", "")))
    gap = "同花顺公告已提供 PDF 原文链接；如为强催化，下一步核对金额、客户、期限和风险条款。" if useful else "未在同花顺公告标题中发现直接硬催化。"
    return {
        "source_type": "ths",
        "fetched_at": now_text(),
        "captured_at": stock.get("captured_at", ""),
        "rank_speed": stock.get("rank_speed", ""),
        "code": code,
        "name": stock.get("name", ""),
        "symbol": market_symbol(code),
        "lookback_days": str(lookback_days),
        "announcement_count": str(len(recent)),
        "hard_evidence_strength": strength,
        "hard_catalyst_summary": summary,
        "hard_catalyst_types": types,
        "hard_catalyst_items": format_items(useful, max_items),
        "stone_evidence_summary": "",
        "order_cooperation_evidence": format_items(order_items, 4),
        "order_cooperation_hard_evidence": "",
        "amount_terms_evidence": "",
        "partner_customer_evidence": "",
        "financial_evidence": format_items(financial_items, 4),
        "ma_evidence": format_items(ma_items, 4),
        "risk_evidence": format_items(risk_items, 4),
        "source_urls": urls,
        "detail_source_urls": urls,
        "source_status": source_status,
        "detail_source_status": "ths_pdf_url_only",
        "evidence_gap": gap,
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect hard-catalyst evidence from THS announcement API.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_window_top10_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "data" / "stock" / "hard_catalyst_evidence_latest.csv")
    parser.add_argument("--output-json", type=Path, default=root / "data" / "stock" / "hard_catalyst_evidence_latest.json")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument("--page-size", type=int, default=15)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--pause", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stocks = read_csv(args.top10_csv)[: args.limit]
    rows = []
    for stock in stocks:
        row = build_one(stock, args.lookback_days, args.max_items, args.max_pages, args.page_size, args.timeout)
        rows.append(row)
        print(
            f"{row['code']} {row['name']}: {row['hard_evidence_strength']}, "
            f"announcements={row['announcement_count']}, types={row['hard_catalyst_types']}"
        )
        time.sleep(args.pause)
    write_csv(args.output_csv, rows, COLUMNS)
    write_json(args.output_json, {"built_at": now_text(), "row_count": len(rows), "rows": rows})
    print(f"ths_hard_catalyst_csv={args.output_csv}")
    print(f"ths_hard_catalyst_json={args.output_json}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
