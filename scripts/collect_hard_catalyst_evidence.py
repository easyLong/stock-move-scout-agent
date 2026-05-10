#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import akshare as ak
import requests


COLUMNS = [
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
    ("并购/重组", ["重大资产购买", "资产重组", "购买资产", "收购股权", "收购控股权", "股权转让", "增资参股", "取得控制权", "对外投资", "现金收购"]),
    ("业绩/财报", ["一季度报告", "第一季度报告", "半年度报告", "年度报告", "业绩预告", "业绩快报", "扭亏", "计提资产减值"]),
    ("产品/技术", ["产品", "研发", "专利", "注册证", "认证", "许可", "临床", "芯片", "量产"]),
    ("风险/减持", ["风险", "减持", "亏损", "问询", "监管", "诉讼", "担保", "质押", "退市", "异常波动"]),
    ("治理/激励", ["董事会", "股东大会", "股权激励", "限制性股票", "薪酬", "章程"]),
]

STRONG_TYPES = {"订单/合作", "并购/重组", "业绩/财报", "产品/技术"}

DETAIL_FETCH_TYPES = {"订单/合作", "并购/重组", "产品/技术"}

ORDER_COOPERATION_KEYWORDS = [
    "订单",
    "重大合同",
    "销售合同",
    "采购合同",
    "合同金额",
    "中标",
    "中标通知书",
    "合作协议",
    "战略合作",
    "框架协议",
    "客户定点",
    "供应商定点",
    "定点通知",
    "供货",
    "供应商",
    "客户",
]

AMOUNT_TERM_KEYWORDS = [
    "合同金额",
    "中标金额",
    "交易价格",
    "交易对价",
    "支付对价",
    "转让价款",
    "采购金额",
    "销售金额",
    "人民币",
    "万元",
    "亿元",
    "履行期限",
    "合同期限",
]

PARTNER_CUSTOMER_KEYWORDS = [
    "客户",
    "合作方",
    "交易对方",
    "采购方",
    "销售方",
    "供应商",
    "与",
    "签署",
    "签订",
]

SUPPLEMENT_KEYWORDS = ["订单", "合同", "中标", "合作", "定点", "供货", "客户", "重大资产购买", "收购", "对外投资"]

EXCLUDE_BY_TYPE: dict[str, list[str]] = {
    "订单/合作": ["募投项目", "募集资金投资项目", "项目所需资金", "投资项目所需资金"],
    "并购/重组": ["投资者关系", "投资者集体接待", "战略配售", "专项核查", "募集资金", "募投项目", "对外投资者"],
    "业绩/财报": ["利润分配", "业绩说明会"],
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def market_symbol(code: str) -> str:
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def date_from_row(row: dict[str, str]) -> datetime:
    for key in ["captured_at", "fetched_at"]:
        value = row.get(key, "")
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value[:19], fmt)
            except Exception:
                pass
    return datetime.now()


def classify_title(title: str) -> list[str]:
    hits: list[str] = []
    for label, keywords in TYPE_KEYWORDS:
        if any(keyword in title for keyword in keywords) and not any(keyword in title for keyword in EXCLUDE_BY_TYPE.get(label, [])):
            hits.append(label)
    return hits or ["其他公告"]


def clean_title(title: str) -> str:
    title = re.sub(r"<[^>]+>", "", title or "")
    title = title.replace("&nbsp;", " ").replace("&amp;", "&")
    return compact(title, 300)


def announcement_id_from_url(url: str) -> str:
    match = re.search(r"announcementId=([^&]+)", url or "")
    return match.group(1) if match else hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def should_fetch_detail(row: dict[str, str]) -> bool:
    types = set(str(row.get("types", "")).split("、"))
    if types & DETAIL_FETCH_TYPES:
        return True
    title = row.get("title", "")
    return any(keyword in title for keyword in ORDER_COOPERATION_KEYWORDS)


def cninfo_detail_params(url: str, code: str, date: str) -> dict[str, str]:
    announcement_id = announcement_id_from_url(url)
    flag = "false" if code.startswith(("6", "9")) else "true"
    return {
        "announceId": announcement_id,
        "flag": flag,
        "announceTime": str(date or "")[:10],
    }


def fetch_cninfo_pdf_url(session: requests.Session, row: dict[str, str], code: str) -> str:
    params = cninfo_detail_params(row.get("url", ""), code, row.get("date", ""))
    response = session.post(
        "http://www.cninfo.com.cn/new/announcement/bulletin_detail",
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    file_url = data.get("fileUrl", "")
    if file_url:
        return str(file_url)
    adjunct = ((data.get("announcement") or {}).get("adjunctUrl") or "").strip()
    return f"http://static.cninfo.com.cn/{adjunct}" if adjunct else ""


def extract_pdf_text(pdf_bytes: bytes, page_limit: int) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""
    try:
        import io

        reader = PdfReader(io.BytesIO(pdf_bytes))
        texts = []
        for page in reader.pages[:page_limit]:
            texts.append(page.extract_text() or "")
        return "\n".join(texts)
    except Exception:
        return ""


def fetch_announcement_detail(
    session: requests.Session,
    row: dict[str, str],
    code: str,
    cache: dict[str, Any],
    pdf_page_limit: int,
) -> dict[str, str]:
    announcement_id = announcement_id_from_url(row.get("url", ""))
    if announcement_id in cache:
        cached = cache[announcement_id]
        if isinstance(cached, dict):
            return {key: str(value or "") for key, value in cached.items()}

    detail = {
        "announcement_id": announcement_id,
        "pdf_url": "",
        "text": "",
        "status": "empty",
    }
    try:
        pdf_url = fetch_cninfo_pdf_url(session, row, code)
        detail["pdf_url"] = pdf_url
        if not pdf_url:
            detail["status"] = "no_pdf_url"
        else:
            response = session.get(pdf_url, timeout=30)
            response.raise_for_status()
            text = extract_pdf_text(response.content, pdf_page_limit)
            detail["text"] = text
            detail["status"] = "pdf_text_ok" if text else "pdf_text_empty_or_pypdf_missing"
    except Exception as exc:
        detail["status"] = f"detail_error:{type(exc).__name__}:{exc}"

    cache[announcement_id] = detail
    return detail


def clean_for_snippet(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"([一-龥])\s+([一-龥])", r"\1\2", text)
    return text


def keyword_snippets(text: str, keywords: list[str], limit: int = 3, radius: int = 70) -> list[str]:
    source = clean_for_snippet(text)
    snippets: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        for match in re.finditer(re.escape(keyword), source):
            start = max(0, match.start() - radius)
            end = min(len(source), match.end() + radius)
            snippet = source[start:end].strip(" ，。；、")
            if snippet and snippet not in seen:
                seen.add(snippet)
                snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def amount_snippets(text: str, limit: int = 3) -> list[str]:
    source = clean_for_snippet(text)
    snippets: list[str] = []
    seen: set[str] = set()
    amount_pattern = re.compile(r"(?:人民币)?\s*\d+(?:\.\d+)?\s*(?:万|亿)?元")
    for match in amount_pattern.finditer(source):
        start = max(0, match.start() - 60)
        end = min(len(source), match.end() + 80)
        snippet = source[start:end].strip(" ，。；、")
        if any(keyword in snippet for keyword in AMOUNT_TERM_KEYWORDS) and snippet not in seen:
            seen.add(snippet)
            snippets.append(snippet)
        if len(snippets) >= limit:
            break
    if snippets:
        return snippets
    return keyword_snippets(source, AMOUNT_TERM_KEYWORDS, limit=limit, radius=70)


def summarize_detail_evidence(details: list[dict[str, str]]) -> dict[str, str]:
    order_parts: list[str] = []
    amount_parts: list[str] = []
    partner_parts: list[str] = []
    pdf_urls: list[str] = []
    statuses: list[str] = []

    for detail in details:
        title = detail.get("title", "")
        date = detail.get("date", "")
        text = detail.get("text", "")
        pdf_url = detail.get("pdf_url", "")
        status = detail.get("status", "")
        if pdf_url:
            pdf_urls.append(pdf_url)
        if status:
            statuses.append(status)
        prefix = f"{date} {title}".strip()
        for snippet in keyword_snippets(text, ORDER_COOPERATION_KEYWORDS, limit=2):
            order_parts.append(f"{prefix}：{snippet}")
        for snippet in amount_snippets(text, limit=2):
            amount_parts.append(f"{prefix}：{snippet}")
        for snippet in keyword_snippets(text, PARTNER_CUSTOMER_KEYWORDS, limit=2):
            partner_parts.append(f"{prefix}：{snippet}")

    order_text = " || ".join(dict.fromkeys(order_parts[:6]))
    amount_text = " || ".join(dict.fromkeys(amount_parts[:6]))
    partner_text = " || ".join(dict.fromkeys(partner_parts[:6]))
    source_text = " | ".join(dict.fromkeys(pdf_urls))
    status_counter: dict[str, int] = {}
    for status in statuses:
        status_counter[status] = status_counter.get(status, 0) + 1
    status_text = ";".join(f"{key}={value}" for key, value in sorted(status_counter.items()))
    if order_text:
        summary = "公告原文命中订单/合作/客户等关键词，可进入人工核验金额、客户、期限和收入确认。"
    elif amount_text or partner_text:
        summary = "公告原文命中金额或交易方片段，但未形成明确订单/合作证据。"
    elif details:
        summary = "已尝试读取公告原文，暂未命中订单/合作石锤片段。"
    else:
        summary = "未进入公告原文详情抓取。"
    return {
        "stone_evidence_summary": summary,
        "order_cooperation_hard_evidence": compact(order_text, 1200),
        "amount_terms_evidence": compact(amount_text, 1200),
        "partner_customer_evidence": compact(partner_text, 1200),
        "detail_source_urls": source_text,
        "detail_source_status": status_text or "not_fetched",
    }


def fetch_announcements(code: str, start: datetime, end: datetime, keyword: str = "") -> list[dict[str, str]]:
    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(
            symbol=code,
            keyword=keyword,
            category="",
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
        )
    except KeyError:
        return []
    rows: list[dict[str, str]] = []
    if df is None or df.empty:
        return rows
    for item in df.to_dict("records"):
        title = clean_title(str(item.get("公告标题", "") or ""))
        if not title:
            continue
        rows.append(
            {
                "date": str(item.get("公告时间", "") or ""),
                "title": title,
                "url": str(item.get("公告链接", "") or ""),
                "types": "、".join(classify_title(title)),
                "source_keyword": keyword,
            }
        )
    return rows


def dedupe_announcements(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        key = row.get("url") or f"{row.get('date')}|{row.get('title')}"
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def pick_items(rows: list[dict[str, str]], wanted: set[str], limit: int = 4) -> list[dict[str, str]]:
    picked = []
    for row in rows:
        types = set(str(row.get("types", "")).split("、"))
        if types & wanted:
            picked.append(row)
        if len(picked) >= limit:
            break
    return picked


def format_items(rows: list[dict[str, str]], limit: int = 6) -> str:
    parts = []
    for row in rows[:limit]:
        parts.append(f"{row.get('date', '')} [{row.get('types', '')}] {row.get('title', '')} {row.get('url', '')}".strip())
    return " || ".join(parts)


def summarize_strength(classified_rows: list[dict[str, str]]) -> tuple[str, str, str]:
    if not classified_rows:
        return "未发现近期公告硬证据", "未发现订单/合作/并购/业绩等近期公告，需要继续查新闻和交易所公告。", ""
    type_counter: dict[str, int] = {}
    for row in classified_rows:
        for label in row.get("types", "").split("、"):
            type_counter[label] = type_counter.get(label, 0) + 1
    types = [item for item, _ in sorted(type_counter.items(), key=lambda kv: kv[1], reverse=True)]
    if pick_items(classified_rows, {"订单/合作", "并购/重组"}, 1):
        strength = "强硬证据"
    elif pick_items(classified_rows, {"业绩/财报", "产品/技术"}, 1):
        strength = "中等硬证据"
    elif pick_items(classified_rows, {"风险/减持"}, 1):
        strength = "风险硬证据"
    else:
        strength = "弱硬证据"
    summary_items = pick_items(classified_rows, STRONG_TYPES | {"风险/减持"}, 3) or classified_rows[:3]
    summary = "；".join(f"{row['date']} {row['title']}" for row in summary_items)
    return strength, summary, "、".join(types[:6])


def build_one(
    stock: dict[str, str],
    lookback_days: int,
    max_items: int,
    detail_cache: dict[str, Any],
    fetch_details: bool,
    max_detail_items: int,
    pdf_page_limit: int,
    supplement_lookback_days: int,
    supplement_keywords: list[str],
    supplement_per_keyword: int,
) -> dict[str, Any]:
    code = stock.get("code", "").strip()
    anchor = date_from_row(stock)
    end = anchor + timedelta(days=1)
    start = anchor - timedelta(days=lookback_days)
    source_status = "ok"
    supplement_status = "supplement_disabled"
    try:
        announcements = fetch_announcements(code, start, end)
        if supplement_lookback_days > 0 and supplement_keywords:
            supplement_start = anchor - timedelta(days=supplement_lookback_days)
            supplement_rows: list[dict[str, str]] = []
            errors = 0
            for keyword in supplement_keywords:
                try:
                    supplement_rows.extend(fetch_announcements(code, supplement_start, end, keyword)[:supplement_per_keyword])
                except Exception:
                    errors += 1
            announcements = dedupe_announcements(announcements + supplement_rows)
            supplement_status = f"supplement_keywords={len(supplement_keywords)};rows={len(supplement_rows)};errors={errors}"
    except Exception as exc:
        announcements = []
        source_status = f"announcement_error:{type(exc).__name__}:{exc}"
        supplement_status = "supplement_skipped"
    useful = [row for row in announcements if "其他公告" not in row.get("types", "")]
    useful = useful[:max_items]
    strength, summary, types = summarize_strength(useful)
    order_items = pick_items(useful, {"订单/合作"}, 4)
    financial_items = pick_items(useful, {"业绩/财报"}, 4)
    ma_items = pick_items(useful, {"并购/重组"}, 4)
    risk_items = pick_items(useful, {"风险/减持"}, 4)
    urls = " | ".join(dict.fromkeys(row.get("url", "") for row in useful if row.get("url", "")))
    details: list[dict[str, str]] = []
    if fetch_details:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                "Referer": "http://www.cninfo.com.cn/new/disclosure/stock",
                "Accept": "application/json, text/plain, */*",
            }
        )
        detail_candidates = [row for row in useful if should_fetch_detail(row)]
        if not detail_candidates:
            detail_candidates = order_items + ma_items
        for item in detail_candidates[:max_detail_items]:
            detail = fetch_announcement_detail(session, item, code, detail_cache, pdf_page_limit)
            detail["date"] = item.get("date", "")
            detail["title"] = item.get("title", "")
            details.append(detail)
    detail_evidence = summarize_detail_evidence(details)
    gap = (
        "公告原文已补充，下一步人工核对合作方、金额、期限、收入确认和风险条款是否支撑上涨叙事。"
        if detail_evidence["order_cooperation_hard_evidence"] or detail_evidence["amount_terms_evidence"]
        else "公告标题级证据已找到，但原文暂未命中订单/合作石锤；需继续查新闻、互动易、交易所问询和资金数据。"
        if useful
        else "未在公告标题中发现直接硬催化，需继续查新闻、互动易、交易所问询和资金数据。"
    )
    return {
        "fetched_at": now_text(),
        "captured_at": stock.get("captured_at", ""),
        "rank_speed": stock.get("rank_speed", ""),
        "code": code,
        "name": stock.get("name", ""),
        "symbol": market_symbol(code),
        "lookback_days": str(lookback_days),
        "announcement_count": str(len(announcements)),
        "hard_evidence_strength": strength,
        "hard_catalyst_summary": summary,
        "hard_catalyst_types": types,
        "hard_catalyst_items": format_items(useful, max_items),
        "stone_evidence_summary": detail_evidence["stone_evidence_summary"],
        "order_cooperation_evidence": format_items(order_items, 4),
        "order_cooperation_hard_evidence": detail_evidence["order_cooperation_hard_evidence"],
        "amount_terms_evidence": detail_evidence["amount_terms_evidence"],
        "partner_customer_evidence": detail_evidence["partner_customer_evidence"],
        "financial_evidence": format_items(financial_items, 4),
        "ma_evidence": format_items(ma_items, 4),
        "risk_evidence": format_items(risk_items, 4),
        "source_urls": urls,
        "detail_source_urls": detail_evidence["detail_source_urls"],
        "source_status": f"{source_status};{supplement_status}",
        "detail_source_status": detail_evidence["detail_source_status"],
        "evidence_gap": gap,
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect official announcement hard-catalyst evidence.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "data" / "stock" / "hard_catalyst_evidence_latest.csv")
    parser.add_argument("--output-json", type=Path, default=root / "data" / "stock" / "hard_catalyst_evidence_latest.json")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--max-items", type=int, default=8)
    parser.add_argument("--cache-json", type=Path, default=root / "data" / "stock" / "cache" / "cninfo_announcement_detail_cache.json")
    parser.add_argument("--no-detail-fetch", action="store_true")
    parser.add_argument("--max-detail-items", type=int, default=3)
    parser.add_argument("--pdf-page-limit", type=int, default=8)
    parser.add_argument("--supplement-lookback-days", type=int, default=365)
    parser.add_argument("--supplement-keywords", default=",".join(SUPPLEMENT_KEYWORDS))
    parser.add_argument("--supplement-per-keyword", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stocks = read_csv(args.top10_csv)[: args.limit]
    detail_cache = read_json(args.cache_json)
    if not isinstance(detail_cache, dict):
        detail_cache = {}
    supplement_keywords = [item.strip() for item in re.split(r"[,，、;；]", args.supplement_keywords) if item.strip()]
    rows = []
    for stock in stocks:
        row = build_one(
            stock,
            args.lookback_days,
            args.max_items,
            detail_cache,
            not args.no_detail_fetch,
            args.max_detail_items,
            args.pdf_page_limit,
            args.supplement_lookback_days,
            supplement_keywords,
            args.supplement_per_keyword,
        )
        rows.append(row)
        print(
            f"{row['code']} {row['name']}: {row['hard_evidence_strength']}, "
            f"announcements={row['announcement_count']}, types={row['hard_catalyst_types']}, "
            f"detail={row['detail_source_status']}"
        )
        time.sleep(args.pause)
    write_json(args.cache_json, detail_cache)
    write_csv(args.output_csv, rows, COLUMNS)
    write_json(
        args.output_json,
        {
            "built_at": now_text(),
            "top10_csv": str(args.top10_csv),
            "row_count": len(rows),
            "rows": rows,
        },
    )
    print(f"hard_catalyst_csv={args.output_csv}")
    print(f"hard_catalyst_json={args.output_json}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
