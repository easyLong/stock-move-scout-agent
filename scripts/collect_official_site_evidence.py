#!/usr/bin/env python
"""
Collect official-company evidence for stock mover scouting.

This source answers: what does the company officially say it does, and do its
products match the market narrative? It is a slow and mostly stable source, so
results are cached by stock code.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, window_evidence_candidate_rows


COLUMNS = [
    "code",
    "stock_name",
    "company_highlights",
    "main_business",
    "sw_industry",
    "concept_tags",
    "latest_management_business_plan",
]

PROFILE_CACHE_VERSION = 2

PAGE_KEYWORDS = [
    "关于",
    "公司",
    "简介",
    "产品",
    "业务",
    "解决方案",
    "技术",
    "使命",
    "愿景",
    "价值观",
    "about",
    "company",
    "profile",
    "product",
    "solution",
    "business",
]

TEXT_KEYWORDS = [
    "使命",
    "愿景",
    "价值观",
    "核心价值",
    "产品",
    "解决方案",
    "主营",
    "业务",
    "研发",
    "技术",
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
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def compact(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def item_key(*parts: Any) -> str:
    text = "|".join(compact(part, 2000) for part in parts)
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def parse_date_text(value: str, fetched_at: datetime | None = None) -> str:
    text = compact(value, 32)
    anchor = fetched_at or datetime.now()
    if not text:
        return ""
    if "今天" in text:
        return anchor.strftime("%Y-%m-%d")
    match = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if match:
        year, month, day = match.groups()
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    match = re.search(r"(\d{1,2})[-/](\d{1,2})", text)
    if match:
        month, day = match.groups()
        return f"{anchor.year:04d}-{int(month):02d}-{int(day):02d}"
    return ""


def absolute_ths_url(url: str) -> str:
    text = str(url or "").strip()
    if not text or text.startswith("javascript:") or text == "###":
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):
        return "https://basic.10jqka.com.cn" + text
    return text


def normalize_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    if not re.match(r"https?://", url, re.I):
        url = "https://" + url
    return url.rstrip("/")


def cache_valid(record: dict[str, Any], ttl_days: int) -> bool:
    if ttl_days <= 0:
        return False
    try:
        fetched = datetime.strptime(str(record.get("fetched_at", "")), "%Y-%m-%d %H:%M:%S")
    except Exception:
        return False
    return datetime.now() - fetched <= timedelta(days=ttl_days)


def get_cninfo_profile(code: str) -> dict[str, str]:
    try:
        df = ak.stock_profile_cninfo(symbol=code)
    except Exception as exc:
        return {"source_status": f"cninfo_error:{type(exc).__name__}:{exc}"}
    if df.empty:
        return {"source_status": "cninfo_empty"}
    row = {str(k): str(v) for k, v in df.iloc[0].to_dict().items()}
    return {
        "company_name": row.get("公司名称", ""),
        "official_website": normalize_url(row.get("官方网站", "")),
        "main_business": compact(row.get("主营业务", ""), 800),
        "business_scope": compact(row.get("经营范围", ""), 1200),
        "company_profile": compact(row.get("机构简介", ""), 1200),
        "source_status": "cninfo_ok",
    }


def extract_profile_value(text: str, label: str, next_labels: list[str], limit: int = 800) -> str:
    start = text.find(label)
    if start < 0:
        return ""
    start += len(label)
    end = len(text)
    for next_label in next_labels:
        pos = text.find(next_label, start)
        if pos >= 0:
            end = min(end, pos)
    return compact(text[start:end], limit)


def extract_first_profile_value(text: str, labels: list[str], next_labels: list[str], limit: int = 800) -> str:
    for label in labels:
        value = extract_profile_value(text, label, next_labels, limit)
        if value:
            return value
    return ""


def clean_concept_tags(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s*\.\.\.\s*$", "", text)
    parts = [part.strip() for part in re.split(r"[,，、;；]", text) if part.strip() and part.strip() != "..."]
    return "、".join(dict.fromkeys(parts))


def make_root_item(
    code: str,
    stock_name: str,
    kind: str,
    section: str,
    rank: int,
    item_date: str,
    title: str,
    content: str = "",
    detail_content: str = "",
    url: str = "",
    tags: list[str] | None = None,
    source_status: str = "ths_root_ok",
    raw: dict[str, Any] | None = None,
    key_content: str = "",
) -> dict[str, Any]:
    key = item_key(kind, item_date, title, url, key_content or content)
    return {
        "code": code,
        "stock_name": stock_name,
        "item_kind": kind,
        "item_key": key,
        "source_section": section,
        "source_rank": rank,
        "item_date": item_date,
        "title": compact(title, 512),
        "content": compact(content, 4000),
        "detail_content": compact(detail_content, 8000),
        "url": absolute_ths_url(url),
        "tags": tags or [],
        "importance": 0,
        "source_status": source_status,
        "raw_json": raw or {},
    }


def parse_pointnew_items(soup: BeautifulSoup, code: str, stock_name: str, fetched_at: datetime) -> list[dict[str, Any]]:
    root = soup.select_one("#pointnew")
    if not root:
        return []
    items: list[dict[str, Any]] = []
    for rank, tr in enumerate(root.select("tr"), start=1):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        item_date = parse_date_text(cells[0].get_text(" ", strip=True), fetched_at)
        body = cells[1]
        label_node = body.find("strong")
        label = compact(label_node.get_text(" ", strip=True), 80).rstrip(":：") if label_node else "重要事件"
        detail_content = compact(" ".join(node.get_text(" ", strip=True) for node in body.select(".check_else")), 8000)
        for noisy in body.select("a.check_details, a.hla, span.open_btn, span.close_btn"):
            noisy.decompose()
        full_text = compact(body.get_text(" ", strip=True), 4000)
        for noisy in body.select(".check_else"):
            noisy.decompose()
        url = ""
        link = body.find("a", href=True)
        if link:
            url = absolute_ths_url(str(link.get("href") or ""))
        text = compact(body.get_text(" ", strip=True), 2000)
        text = re.sub(r"(详情>>|更多>>|详细内容\s*&nbsp|详细内容|收起|▲|�)+", " ", text)
        text = compact(text, 2000)
        if not text:
            continue
        title = label if text.startswith(label) else f"{label}：{text[:180]}"
        items.append(
            make_root_item(
                code,
                stock_name,
                "important_event",
                "#pointnew",
                rank,
                item_date,
                title,
                text,
                detail_content,
                url,
                [label],
                raw={"date_text": cells[0].get_text(" ", strip=True), "has_detail": bool(detail_content)},
                key_content=full_text,
            )
        )
    return items


def parse_lhb_detail_items(soup: BeautifulSoup, code: str, stock_name: str, fetched_at: datetime) -> list[dict[str, Any]]:
    root = soup.select_one("#payback")
    if not root:
        return []
    tab_dates: dict[str, str] = {}
    for link in root.select(".m_tab a[targ]"):
        tab_id = str(link.get("targ") or "").strip()
        date_text = parse_date_text(link.get_text(" ", strip=True), fetched_at)
        if tab_id and date_text:
            tab_dates[tab_id] = date_text
    items: list[dict[str, Any]] = []
    for rank, panel in enumerate(root.select(".m_tab_content[id]"), start=1):
        panel_id = str(panel.get("id") or "").strip()
        item_date = tab_dates.get(panel_id) or parse_date_text(panel.get_text(" ", strip=True), fetched_at)
        if not item_date:
            continue
        reason_node = panel.select_one(".the_reasons em") or panel.select_one(".the_reasons")
        reason = compact(reason_node.get_text(" ", strip=True), 260) if reason_node else ""
        seats: list[dict[str, Any]] = []
        totals: list[str] = []
        for row_no, tr in enumerate(panel.select("tbody tr"), start=1):
            cells = tr.find_all(["th", "td"])
            texts = [compact(cell.get_text(" ", strip=True), 260) for cell in cells]
            if not texts:
                continue
            if len(texts) < 6:
                total_text = compact(" ".join(texts), 120)
                if total_text:
                    totals.append(total_text)
                continue
            th = tr.find("th")
            seat_link = th.find("a") if th else None
            seat_name = compact(seat_link.get_text(" ", strip=True), 180) if seat_link else texts[0]
            labels: list[dict[str, str]] = []
            if th:
                for label in th.select("label.label"):
                    label_node = label.select_one("span")
                    label_name = compact(label_node.get_text(" ", strip=True), 48) if label_node else ""
                    tip_node = label.select_one(".label-tips")
                    label_tip = compact(tip_node.get_text(" ", strip=True), 500) if tip_node else ""
                    if label_name:
                        labels.append({"name": label_name, "explain": label_tip})
            side = ""
            marker = " ".join(
                str(value or "")
                for value in [
                    seat_link.get("newtaid") if seat_link else "",
                    seat_link.get("href") if seat_link else "",
                ]
            )
            if "mairu" in marker:
                side = "buy"
            elif "maichu" in marker:
                side = "sell"
            seats.append(
                {
                    "row_no": row_no,
                    "side": side,
                    "seat_name": seat_name,
                    "buy_amount": texts[1],
                    "buy_ratio": texts[2],
                    "sell_amount": texts[3],
                    "sell_ratio": texts[4],
                    "net_amount": texts[5],
                    "labels": labels,
                    "url": absolute_ths_url(str(seat_link.get("href") or "")) if seat_link else "",
                }
            )
        if not seats and not totals:
            continue
        tagged_lines: list[str] = []
        detail_lines: list[str] = []
        for seat in seats:
            label_text = "、".join(label.get("name", "") for label in seat.get("labels", []) if label.get("name"))
            side_text = {"buy": "买入席位", "sell": "卖出席位"}.get(str(seat.get("side") or ""), "席位")
            money_text = f"买入{seat.get('buy_amount', '')}，卖出{seat.get('sell_amount', '')}，净额{seat.get('net_amount', '')}"
            line = f"{side_text}：{seat.get('seat_name', '')}；{money_text}"
            if label_text:
                line += f"；席位标签：{label_text}"
                tagged_lines.append(line)
            detail_lines.append(line)
            for label in seat.get("labels", []):
                if label.get("name") and label.get("explain"):
                    detail_lines.append(f"{label.get('name')}解释：{label.get('explain')}")
        content_parts = []
        if reason:
            content_parts.append(f"上榜原因：{reason}")
        content_parts.extend(totals[:3])
        content_parts.extend(tagged_lines[:3])
        detail_parts = []
        if reason:
            detail_parts.append(f"上榜原因：{reason}")
        detail_parts.extend(detail_lines)
        detail_parts.extend(totals)
        items.append(
            make_root_item(
                code,
                stock_name,
                "important_event",
                "#payback",
                rank,
                item_date,
                "\u9f99 \u864e \u699c",
                "；".join(content_parts),
                "\n".join(detail_parts),
                "",
                ["\u9f99\u864e\u699c"],
                source_status="ths_lhb_detail_ok",
                raw={
                    "date_text": item_date,
                    "reason": reason,
                    "lhb_seats": seats,
                    "lhb_totals": totals,
                    "has_detail": bool(detail_parts),
                },
                key_content="\n".join(detail_parts) or "；".join(content_parts),
            )
        )
    return items


def get_ths_root_profile(code: str, timeout: int, stock_name: str = "") -> dict[str, Any]:
    url = f"https://basic.10jqka.com.cn/{code}/"
    fetched_at_dt = datetime.now()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
    except Exception as exc:
        return {"profile_status": f"ths_profile_error:{type(exc).__name__}:{exc}"}
    response.encoding = "gb2312"
    soup = BeautifulSoup(response.text, "lxml")
    profile = soup.select_one("#profile")
    if not profile:
        return {"profile_status": "ths_profile_empty"}
    market_node = soup.select_one("#marketId")
    stock_node = soup.select_one("#stockCode")
    market_id = str(market_node.get("value") or "") if market_node else ""
    page_stock_code = str(stock_node.get("value") or "") if stock_node else code
    text = compact(profile.get_text(" ", strip=True), 12000)
    highlight_node = profile.select_one(".core-view-text") or soup.select_one(".core-view-text")
    company_highlights = compact(highlight_node.get("title") or highlight_node.get_text(" ", strip=True), 800) if highlight_node else ""
    main_business = extract_profile_value(text, "主营业务：", ["所属申万行业：", "概念贴合度排名：", "详情>>"], 800)
    sw_industry = extract_profile_value(text, "所属申万行业：", ["概念贴合度排名：", "涉及概念：", "详情>>", "对比>>"], 128)
    concept_tags = clean_concept_tags(
        extract_first_profile_value(
            text,
            ["概念贴合度排名：", "涉及概念："],
            ["详情>>", "财务分析：", "对比>>", "可比公司"],
            1200,
        )
    )
    root_items = parse_pointnew_items(soup, code, stock_name, fetched_at_dt)
    root_items.extend(parse_lhb_detail_items(soup, code, stock_name, fetched_at_dt))
    status = "ths_profile_ok" if any([company_highlights, main_business, sw_industry, concept_tags]) else "ths_profile_empty"
    section_counts: dict[str, int] = {}
    for item in root_items:
        section_counts[item.get("item_kind", "other")] = section_counts.get(item.get("item_kind", "other"), 0) + 1
    profile_json = {
        "company_highlights": company_highlights,
        "main_business": main_business,
        "sw_industry": sw_industry,
        "concept_tags": concept_tags,
    }
    return {
        "market_id": market_id,
        "page_stock_code": page_stock_code,
        "company_highlights": company_highlights,
        "main_business": main_business,
        "sw_industry": sw_industry,
        "concept_tags": concept_tags,
        "profile_status": status,
        "ths_profile_url": url,
        "ths_root_items": root_items,
        "ths_root_snapshot": {
            "code": code,
            "stock_name": stock_name,
            "market_id": market_id,
            "root_url": url,
            "fetched_at": fetched_at_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "source_status": status,
            "item_count": len(root_items),
            "profile_json": profile_json,
            "sections_json": {
                "section_counts": section_counts,
                "pointnew_text": compact((soup.select_one("#pointnew") or "").get_text(" ", strip=True) if soup.select_one("#pointnew") else "", 1600),
            },
            "raw_json": {
                "page_stock_code": page_stock_code,
            },
        },
    }


def request_page(url: str, timeout: int) -> tuple[str, str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    last_error = ""
    for candidate in [url, url.replace("https://", "http://", 1)]:
        try:
            resp = requests.get(candidate, headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"http_{resp.status_code}:{candidate}"
                continue
            resp.encoding = resp.apparent_encoding or resp.encoding
            return candidate, resp.text
        except Exception as exc:
            last_error = f"{type(exc).__name__}:{candidate}:{exc}"
    raise RuntimeError(last_error)


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
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"], limit=220):
        text = tag.get_text(" ", strip=True)
        if 6 <= len(text) <= 240:
            pieces.append(text)
    return compact(" ".join(dict.fromkeys(pieces)), 4000)


def candidate_links(base_url: str, soup: BeautifulSoup, limit: int) -> list[str]:
    base_host = urlparse(base_url).netloc.lower().removeprefix("www.")
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "").strip()
        text = a.get_text(" ", strip=True).lower()
        url = urljoin(base_url + "/", href).split("#")[0].rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = parsed.netloc.lower().removeprefix("www.")
        if host != base_host:
            continue
        haystack = (text + " " + parsed.path.lower()).lower()
        if not any(keyword.lower() in haystack for keyword in PAGE_KEYWORDS):
            continue
        if url not in seen:
            seen.add(url)
            links.append(url)
        if len(links) >= limit:
            break
    return links


def snippets(text: str, keywords: list[str], limit: int = 5) -> tuple[str, str]:
    found: list[str] = []
    matched: list[str] = []
    chunks = re.split(r"[。！？!?；;\n]", text)
    for chunk in chunks:
        compacted = compact(chunk, 220)
        if len(compacted) < 8:
            continue
        hit = [keyword for keyword in keywords if keyword.lower() in compacted.lower()]
        if not hit:
            continue
        matched.extend(hit)
        if compacted not in found:
            found.append(compacted)
        if len(found) >= limit:
            break
    return " || ".join(found), "、".join(dict.fromkeys(matched))


def crawl_official_site(url: str, timeout: int, max_pages: int) -> dict[str, str]:
    if not url:
        return {"website_status": "missing_official_website"}
    try:
        resolved_url, html_text = request_page(url, timeout)
    except Exception as exc:
        return {"website_status": f"website_error:{type(exc).__name__}:{exc}"}
    soup = clean_soup(html_text)
    urls = [resolved_url] + candidate_links(resolved_url, soup, max_pages - 1)
    texts = [page_text(soup)]
    visited = [resolved_url]
    for extra_url in urls[1:max_pages]:
        try:
            time.sleep(0.4)
            actual_url, extra_html = request_page(extra_url, timeout)
            texts.append(page_text(clean_soup(extra_html)))
            visited.append(actual_url)
        except Exception:
            continue
    merged = compact(" ".join(texts), 8000)
    mv, mv_keywords = snippets(merged, ["使命", "愿景", "价值观", "核心价值", "宗旨", "理念"], 5)
    product, product_keywords = snippets(merged, ["产品", "解决方案", "业务", "服务", "芯片", "设备", "系统", "平台"], 7)
    return {
        "mission_vision_values": mv,
        "website_summary": compact(merged, 900),
        "website_product_evidence": product,
        "matched_keywords": "、".join(dict.fromkeys((mv_keywords + "、" + product_keywords).strip("、").split("、"))),
        "source_urls": " | ".join(dict.fromkeys(visited)),
        "website_status": f"website_ok:pages={len(visited)}",
    }


def collect_one(stock: dict[str, str], timeout: int, max_pages: int, use_cninfo_profile: bool = False) -> dict[str, Any]:
    code = stock.get("code", "").strip()
    stock_name = stock.get("name", "")
    profile = get_ths_root_profile(code, timeout, stock_name)
    ths_news_url = f"https://basic.10jqka.com.cn/{code}/news.html" if code else ""
    ths_status = profile.get("profile_status", "")
    row = {
        "profile_cache_version": PROFILE_CACHE_VERSION,
        "fetched_at": now_text(),
        "captured_at": stock.get("captured_at", ""),
        "rank_speed": stock.get("rank_speed", ""),
        "code": code,
        "name": stock_name,
        "symbol": market_symbol(code),
        "stock_name": stock_name,
        "company_highlights": profile.get("company_highlights", ""),
        "main_business": profile.get("main_business", ""),
        "sw_industry": profile.get("sw_industry", ""),
        "concept_tags": profile.get("concept_tags", ""),
        "industry": profile.get("sw_industry", ""),
        "concepts": profile.get("concept_tags", ""),
        "latest_core_operating_metrics": "",
        "latest_operating_metrics_period": "",
        "latest_product_revenue_profit": "",
        "latest_product_revenue_profit_period": "",
        "latest_customer_supplier_summary": "",
        "latest_customer_supplier_period": "",
        "latest_management_discussion_period": "",
        "latest_management_discussion_raw": "",
        "latest_management_business_plan": "",
        "latest_management_ai_summary": "",
        "ths_profile_url": profile.get("ths_profile_url", ""),
        "ths_news_url": ths_news_url,
        "ths_status": ths_status,
        "source_status": ths_status,
        "ths_root_snapshot": profile.get("ths_root_snapshot", {}),
        "ths_root_items": profile.get("ths_root_items", []),
        "evidence_value": "同花顺经营分析用于核对公司真实业务、产品和经营画像",
        "evidence_gap": "静态画像不能单独解释盘中异动；仍需公告、社区、板块和资金验证",
    }
    return row


def cache_missing_row(stock: dict[str, str]) -> dict[str, Any]:
    code = stock.get("code", "").strip()
    return {
        "fetched_at": "",
        "captured_at": stock.get("captured_at", ""),
        "rank_speed": stock.get("rank_speed", ""),
        "code": code,
        "name": stock.get("name", ""),
        "symbol": market_symbol(code),
        "stock_name": stock.get("name", ""),
        "company_highlights": "",
        "main_business": "",
        "sw_industry": "",
        "concept_tags": "",
        "industry": "",
        "concepts": "",
        "latest_core_operating_metrics": "",
        "latest_operating_metrics_period": "",
        "latest_product_revenue_profit": "",
        "latest_product_revenue_profit_period": "",
        "latest_customer_supplier_summary": "",
        "latest_customer_supplier_period": "",
        "latest_management_discussion_period": "",
        "latest_management_discussion_raw": "",
        "latest_management_business_plan": "",
        "latest_management_ai_summary": "",
        "ths_profile_url": "",
        "ths_news_url": "",
        "ths_status": "cache_missing",
        "source_status": "cache_missing",
        "evidence_value": "官网画像未缓存，本轮只读缓存不抓取官网。",
        "evidence_gap": "需要手动刷新官网画像后再用于公司定位核对。",
    }


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Collect official website and company-profile evidence.")
    add_mysql_args(parser)
    parser.add_argument("--mysql-window-id", default="", help="Read candidates from MySQL window instead of --top10-csv.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "data" / "stock" / "official_site_evidence_latest.csv")
    parser.add_argument("--output-json", type=Path, default=root / "data" / "stock" / "official_site_evidence_latest.json")
    parser.add_argument("--cache-json", type=Path, default=root / "data" / "stock" / "cache" / "official_site_evidence_cache.json")
    parser.add_argument("--limit", type=int, default=10, help="max rows to process; <=0 means all rows after offset")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--ttl-days", type=int, default=14)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--workers", type=int, default=1, help="parallel network workers for uncached rows")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--cache-only", action="store_true", help="only read cached company profile data; never crawl network")
    parser.add_argument("--use-cninfo-profile", action="store_true", help="fetch CNInfo static profile; disabled by default")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.mysql_enabled:
        if not args.mysql_window_id:
            print("mysql_window_id_missing")
            return 1
        input_rows = window_evidence_candidate_rows(mysql_config_from_args(args), args.mysql_window_id)
    else:
        input_rows = read_csv(args.top10_csv)
    stocks = input_rows[args.offset :]
    if args.limit > 0:
        stocks = stocks[: args.limit]
    cache = read_json(args.cache_json)
    rows_by_index: list[dict[str, Any] | None] = [None] * len(stocks)
    network_jobs: list[tuple[int, dict[str, str]]] = []
    changed = False
    for index, stock in enumerate(stocks):
        code = stock.get("code", "").strip()
        cached = cache.get(code) if code else None
        cache_is_current = int(cached.get("profile_cache_version", 0)) >= PROFILE_CACHE_VERSION if isinstance(cached, dict) else False
        if isinstance(cached, dict) and cache_is_current and not args.refresh and (args.cache_only or cache_valid(cached, args.ttl_days)):
            row = dict(cached)
            row.update(
                {
                    "captured_at": stock.get("captured_at", row.get("captured_at", "")),
                    "rank_speed": stock.get("rank_speed", row.get("rank_speed", "")),
                }
            )
            rows_by_index[index] = row
            print(f"{code} {stock.get('name', '')}: cache_hit")
            continue
        if args.cache_only and not args.refresh:
            row = cache_missing_row(stock)
            rows_by_index[index] = row
            print(f"{code} {stock.get('name', '')}: cache_missing")
            continue
        network_jobs.append((index, stock))

    def collect_job(job: tuple[int, dict[str, str]]) -> tuple[int, dict[str, str], dict[str, Any]]:
        index, stock = job
        return index, stock, collect_one(stock, args.timeout, args.max_pages, args.use_cninfo_profile)

    workers = max(1, args.workers)
    if workers == 1 or len(network_jobs) <= 1:
        results = [collect_job(job) for job in network_jobs]
    else:
        results = []
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(collect_job, job) for job in network_jobs]
            for future in as_completed(futures):
                results.append(future.result())

    for index, stock, row in sorted(results, key=lambda item: item[0]):
        code = stock.get("code", "").strip()
        rows_by_index[index] = row
        if code:
            cache[code] = row
            changed = True
        print(f"{code} {stock.get('name', '')}: {row['source_status']}")
    rows = [row for row in rows_by_index if row is not None]
    if changed:
        write_json(args.cache_json, cache)
    write_csv(args.output_csv, rows, COLUMNS)
    write_json(
        args.output_json,
        {
            "built_at": now_text(),
            "source": "mysql" if args.mysql_enabled else "csv",
            "mysql_window_id": args.mysql_window_id,
            "top10_csv": str(args.top10_csv),
            "cache_json": str(args.cache_json),
            "row_count": len(rows),
            "rows": rows,
        },
    )
    print(f"official_site_csv={args.output_csv}")
    print(f"official_site_json={args.output_json}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
