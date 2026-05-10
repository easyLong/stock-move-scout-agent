#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import akshare as ak
import requests
from bs4 import BeautifulSoup


COLUMNS = [
    "fetched_at",
    "captured_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "lookback_days",
    "supplemental_strength",
    "supplemental_summary",
    "news_hit_count",
    "news_evidence",
    "irm_hit_count",
    "irm_evidence",
    "official_news_hit_count",
    "official_news_evidence",
    "order_cooperation_supplement",
    "customer_partner_supplement",
    "amount_terms_supplement",
    "source_urls",
    "source_status",
    "evidence_gap",
]

ORDER_KEYWORDS = [
    "订单",
    "合同",
    "重大合同",
    "销售合同",
    "采购合同",
    "中标",
    "中标通知书",
    "定点",
    "客户定点",
    "供应商定点",
    "供货",
]

COOPERATION_KEYWORDS = [
    "合作",
    "合作协议",
    "战略合作",
    "深度协同",
    "框架协议",
    "联合",
    "共建",
    "生态",
    "伙伴",
]

CUSTOMER_KEYWORDS = ["客户", "合作方", "交易对方", "采购方", "供应商", "华为", "中国移动", "阿里", "腾讯", "字节"]
AMOUNT_KEYWORDS = ["合同金额", "中标金额", "交易金额", "交易对价", "交易价格", "收购价格", "整体估值", "估值为", "现金收购"]
PRODUCT_TECH_KEYWORDS = ["产品", "技术", "量产", "芯片", "AI", "人工智能", "算力", "GPU", "医疗", "机器人", "商业航天"]
RISK_KEYWORDS = ["减持", "亏损", "风险", "问询", "诉讼", "退市", "质押"]

STRICT_ORDER_COOP_KEYWORDS = [
    "订单",
    "合同",
    "中标",
    "定点",
    "供货",
    "送样",
    "签署",
    "达成战略合作",
    "合作协议",
    "战略合作",
    "客户定点",
    "供应商定点",
]

NEGATION_KEYWORDS = [
    "请关注公司公告",
    "请关注公司在巨潮资讯网",
    "请以公司公告为准",
    "若有达到法定披露标准",
    "未达到披露标准",
    "不便披露",
    "无法透露",
]

NEWS_LINK_KEYWORDS = [
    "新闻",
    "动态",
    "资讯",
    "媒体",
    "公告",
    "news",
    "media",
    "press",
    "events",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def market_symbol(code: str) -> str:
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compact(value: Any, limit: int = 260) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    return url.rstrip("/")


def parse_time(value: str) -> datetime | None:
    text = str(value or "").strip()
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"]:
        try:
            return datetime.strptime(text[:19], fmt)
        except Exception:
            pass
    return None


def date_from_row(row: dict[str, str]) -> datetime:
    for key in ["captured_at", "fetched_at"]:
        value = parse_time(row.get(key, ""))
        if value:
            return value
    return datetime.now()


def by_code(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        code = row.get("code", "").strip()
        if code and code not in result:
            result[code] = row
    return result


def split_terms(value: str, limit: int = 10) -> list[str]:
    parts = [part.strip() for part in re.split(r"[,，、;；|/ ]+", value or "") if len(part.strip()) >= 2]
    seen: list[str] = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return seen[:limit]


def trigger_terms(narrative: dict[str, str] | None, community: dict[str, str] | None) -> list[str]:
    terms: list[str] = []
    if narrative:
        for key in ["community_trigger_claim", "community_trigger_event", "community_verification_anchor", "community_support_points"]:
            text = narrative.get(key, "")
            for word in ORDER_KEYWORDS + COOPERATION_KEYWORDS + CUSTOMER_KEYWORDS + PRODUCT_TECH_KEYWORDS:
                if word in text and word not in terms:
                    terms.append(word)
    if community:
        terms.extend(split_terms(community.get("hot_terms", ""), 8))
    return terms[:16]


def keyword_hits(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword in text]


def classify_text(text: str) -> dict[str, list[str]]:
    return {
        "order": keyword_hits(text, ORDER_KEYWORDS),
        "cooperation": keyword_hits(text, COOPERATION_KEYWORDS),
        "customer": keyword_hits(text, CUSTOMER_KEYWORDS),
        "amount": keyword_hits(text, AMOUNT_KEYWORDS),
        "product": keyword_hits(text, PRODUCT_TECH_KEYWORDS),
        "risk": keyword_hits(text, RISK_KEYWORDS),
    }


def has_negation(text: str) -> bool:
    return any(keyword in text for keyword in NEGATION_KEYWORDS)


def has_strict_order_coop(text: str) -> bool:
    return any(keyword in text for keyword in STRICT_ORDER_COOP_KEYWORDS)


def has_useful_hit(text: str, extra_terms: list[str]) -> bool:
    classes = classify_text(text)
    if any(classes[key] for key in ["order", "cooperation", "customer", "amount", "product", "risk"]):
        return True
    return any(term and term in text for term in extra_terms)


def format_item(item: dict[str, str], limit: int = 220) -> str:
    date = item.get("date", "")
    source = item.get("source", "")
    title = item.get("title", "")
    text = compact(item.get("text", ""), limit)
    url = item.get("url", "")
    return f"{date} [{source}] {title}：{text} {url}".strip()


def request_page(url: str, timeout: int) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return response.url, response.text


def clean_soup(html_text: str) -> BeautifulSoup:
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return soup


def page_text(soup: BeautifulSoup) -> str:
    pieces: list[str] = []
    title = soup.find("title")
    if title:
        pieces.append(title.get_text(" ", strip=True))
    meta = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    if meta and meta.get("content"):
        pieces.append(str(meta.get("content")))
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"], limit=160):
        text = tag.get_text(" ", strip=True)
        if 6 <= len(text) <= 260:
            pieces.append(text)
    return compact(" ".join(dict.fromkeys(pieces)), 2500)


def official_news_links(base_url: str, soup: BeautifulSoup, limit: int) -> list[str]:
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    seen: set[str] = set()
    links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = str(a.get("href", "")).strip()
        text = a.get_text(" ", strip=True)
        url = urljoin(base_url + "/", href).split("#")[0].rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = parsed.netloc.lower().removeprefix("www.")
        if host != base_host:
            continue
        haystack = f"{text} {parsed.path}".lower()
        if not any(keyword.lower() in haystack for keyword in NEWS_LINK_KEYWORDS):
            continue
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= limit:
            break
    return links


def collect_stock_news(code: str, start: datetime, end: datetime, extra_terms: list[str], max_items: int) -> tuple[list[dict[str, str]], str]:
    try:
        df = ak.stock_news_em(symbol=code)
    except Exception as exc:
        return [], f"news_error:{type(exc).__name__}:{exc}"
    if df is None or df.empty:
        return [], "news_empty"
    hits: list[dict[str, str]] = []
    for row in df.to_dict("records"):
        title = compact(row.get("新闻标题", ""), 180)
        text = compact(row.get("新闻内容", ""), 700)
        date_text = str(row.get("发布时间", "") or "")
        published = parse_time(date_text)
        if published and not (start <= published <= end):
            continue
        haystack = title + " " + text
        if not has_useful_hit(haystack, extra_terms):
            continue
        hits.append(
            {
                "date": date_text,
                "source": str(row.get("文章来源", "") or "东方财富新闻"),
                "title": title,
                "text": text,
                "url": str(row.get("新闻链接", "") or ""),
            }
        )
        if len(hits) >= max_items:
            break
    return hits, "news_ok"


def collect_irm(code: str, extra_terms: list[str], max_items: int) -> tuple[list[dict[str, str]], str]:
    try:
        df = ak.stock_irm_cninfo(symbol=code)
    except Exception as exc:
        return [], f"irm_error:{type(exc).__name__}:{exc}"
    if df is None or df.empty:
        return [], "irm_empty"
    hits: list[dict[str, str]] = []
    for row in df.to_dict("records"):
        question = compact(row.get("问题", ""), 400)
        answer = compact(row.get("回答内容", ""), 700)
        if not answer or answer.lower() == "nan":
            continue
        text = question + " " + answer
        if not has_useful_hit(text, extra_terms):
            continue
        question_id = str(row.get("问题编号", "") or row.get("questionId", "") or "")
        url = f"https://irm.cninfo.com.cn/ircs/question/questionDetail?questionId={question_id}" if question_id else ""
        hits.append(
            {
                "date": str(row.get("更新时间", "") or row.get("提问时间", "") or ""),
                "source": "互动易",
                "title": question,
                "text": answer or question,
                "url": url,
            }
        )
        if len(hits) >= max_items:
            break
    return hits, "irm_ok"


def collect_official_news(website: str, extra_terms: list[str], max_pages: int, timeout: int) -> tuple[list[dict[str, str]], str]:
    website = normalize_url(website)
    if not website:
        return [], "official_news_missing_website"
    try:
        final_url, html_text = request_page(website, timeout)
        soup = clean_soup(html_text)
        links = official_news_links(final_url, soup, max_pages)
    except Exception as exc:
        return [], f"official_news_home_error:{type(exc).__name__}:{exc}"
    hits: list[dict[str, str]] = []
    statuses = [f"links={len(links)}"]
    for url in links:
        path = urlparse(url).path.lower()
        if any(marker in path for marker in ["list", "category", "classid"]):
            continue
        try:
            page_url, page_html = request_page(url, timeout)
            page_soup = clean_soup(page_html)
            text = page_text(page_soup)
        except Exception as exc:
            statuses.append(f"page_error:{type(exc).__name__}")
            continue
        if not has_useful_hit(text, extra_terms):
            continue
        title = page_soup.find("title")
        hits.append(
            {
                "date": "",
                "source": "官网新闻",
                "title": compact(title.get_text(" ", strip=True) if title else page_url, 160),
                "text": text,
                "url": page_url,
            }
        )
        if len(hits) >= max_pages:
            break
    statuses.append(f"hits={len(hits)}")
    return hits, "official_news_ok:" + ";".join(statuses)


def evidence_parts(items: list[dict[str, str]]) -> dict[str, str]:
    order_parts: list[str] = []
    customer_parts: list[str] = []
    amount_parts: list[str] = []
    for item in items:
        title = item.get("title", "")
        text = f"{title} {item.get('text', '')}"
        classes = classify_text(text)
        formatted = format_item(item)
        if item.get("source") == "互动易" and has_negation(text):
            continue
        is_official = item.get("source") == "官网新闻"
        is_official_generic = is_official and not has_strict_order_coop(title)
        if (classes["order"] or classes["cooperation"]) and not is_official_generic:
            order_parts.append(formatted)
        if classes["customer"] and not is_official_generic:
            customer_parts.append(formatted)
        if classes["amount"] and not is_official_generic:
            amount_parts.append(formatted)
    return {
        "order": " || ".join(dict.fromkeys(order_parts[:5])),
        "customer": " || ".join(dict.fromkeys(customer_parts[:5])),
        "amount": " || ".join(dict.fromkeys(amount_parts[:5])),
    }


def summarize_strength(all_items: list[dict[str, str]]) -> tuple[str, str]:
    if not all_items:
        return "未发现补充石锤", "新闻、互动易、官网新闻暂未命中订单/合作/客户等补充证据。"
    parts = evidence_parts(all_items)
    has_direct = any(has_strict_order_coop(f"{item.get('title', '')} {item.get('text', '')}") and not has_negation(f"{item.get('title', '')} {item.get('text', '')}") for item in all_items)
    if parts["order"] and (parts["customer"] or parts["amount"]) and has_direct:
        return "强补充证据", "补充源同时命中订单/合作及客户/金额线索，适合回到公告或原文核验。"
    if parts["order"]:
        return "中等补充证据", "补充源命中订单/合作线索，但客户、金额或履约条款仍需核实。"
    if parts["customer"] or parts["amount"]:
        return "弱补充证据", "补充源命中客户/金额/交易线索，但未直接形成订单或合作证据。"
    return "背景补充证据", "补充源命中产品、技术、财报或风险信息，可作为叙事背景。"


def build_one(
    stock: dict[str, str],
    community: dict[str, str] | None,
    narrative: dict[str, str] | None,
    official: dict[str, str] | None,
    lookback_days: int,
    max_news: int,
    max_irm: int,
    max_official_pages: int,
    timeout: int,
) -> dict[str, Any]:
    code = stock.get("code", "").strip()
    anchor = date_from_row(stock)
    start = anchor - timedelta(days=lookback_days)
    end = anchor + timedelta(days=1)
    terms = trigger_terms(narrative, community)

    news_items, news_status = collect_stock_news(code, start, end, terms, max_news)
    irm_items, irm_status = collect_irm(code, terms, max_irm)
    official_items, official_status = collect_official_news((official or {}).get("official_website", ""), terms, max_official_pages, timeout)
    all_items = news_items + irm_items + official_items
    strength, summary = summarize_strength(all_items)
    parts = evidence_parts(all_items)
    source_urls = " | ".join(dict.fromkeys(item.get("url", "") for item in all_items if item.get("url", "")))
    gap = (
        "补充源已提供线索，下一步要回到公告原文、合同公告、互动易原文或公司官网原文确认准确性。"
        if all_items
        else "补充源未命中，社区提到的订单/合作/客户仍缺公开证据支撑。"
    )
    return {
        "fetched_at": now_text(),
        "captured_at": stock.get("captured_at", ""),
        "rank_speed": stock.get("rank_speed", ""),
        "code": code,
        "name": stock.get("name", ""),
        "symbol": market_symbol(code),
        "lookback_days": str(lookback_days),
        "supplemental_strength": strength,
        "supplemental_summary": summary,
        "news_hit_count": str(len(news_items)),
        "news_evidence": " || ".join(format_item(item) for item in news_items),
        "irm_hit_count": str(len(irm_items)),
        "irm_evidence": " || ".join(format_item(item) for item in irm_items),
        "official_news_hit_count": str(len(official_items)),
        "official_news_evidence": " || ".join(format_item(item) for item in official_items),
        "order_cooperation_supplement": parts["order"],
        "customer_partner_supplement": parts["customer"],
        "amount_terms_supplement": parts["amount"],
        "source_urls": source_urls,
        "source_status": f"{news_status};{irm_status};{official_status}",
        "evidence_gap": gap,
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect supplemental hard evidence from stock news, irm, and official news pages.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--community-evidence-csv", type=Path, default=root / "data" / "stock" / "xueqiu_focus_evidence_latest.csv")
    parser.add_argument("--community-narrative-csv", type=Path, default=root / "data" / "stock" / "community_narrative_latest.csv")
    parser.add_argument("--official-evidence-csv", type=Path, default=root / "data" / "stock" / "official_site_evidence_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "data" / "stock" / "supplemental_hard_evidence_latest.csv")
    parser.add_argument("--output-json", type=Path, default=root / "data" / "stock" / "supplemental_hard_evidence_latest.json")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--lookback-days", type=int, default=45)
    parser.add_argument("--max-news", type=int, default=5)
    parser.add_argument("--max-irm", type=int, default=5)
    parser.add_argument("--max-official-pages", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--pause", type=float, default=0.2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    top_rows = read_csv(args.top10_csv)[: args.limit]
    community_map = by_code(read_csv(args.community_evidence_csv))
    narrative_map = by_code(read_csv(args.community_narrative_csv))
    official_map = by_code(read_csv(args.official_evidence_csv))
    rows = []
    for stock in top_rows:
        code = stock.get("code", "").strip()
        row = build_one(
            stock,
            community_map.get(code),
            narrative_map.get(code),
            official_map.get(code),
            args.lookback_days,
            args.max_news,
            args.max_irm,
            args.max_official_pages,
            args.timeout,
        )
        rows.append(row)
        print(
            f"{row['code']} {row['name']}: {row['supplemental_strength']}, "
            f"news={row['news_hit_count']}, irm={row['irm_hit_count']}, official={row['official_news_hit_count']}"
        )
        time.sleep(args.pause)
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
    print(f"supplemental_hard_evidence_csv={args.output_csv}")
    print(f"supplemental_hard_evidence_json={args.output_json}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
