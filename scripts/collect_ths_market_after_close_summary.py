#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, run_mysql, sql_json, sql_string


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
}

LIST_URLS = (
    "https://stock.10jqka.com.cn/",
    "http://stock.10jqka.com.cn/",
    "https://news.10jqka.com.cn/today_list/",
    "https://news.10jqka.com.cn/cjzx_list/",
)

TITLE_KEYWORDS = ("涨停复盘", "收评", "A股三大指数", "沪指", "创业板指")
SUMMARY_KEYWORDS = ("全市场", "成交额", "涨幅居前", "跌幅居前", "板块题材", "下跌")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 2000) -> str:
    text = html.unescape("" if value is None else str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def decode_response(response: requests.Response) -> str:
    content_type = response.headers.get("content-type", "").lower()
    if "gbk" in content_type or "gb2312" in content_type:
        return response.content.decode("gbk", errors="replace")
    return response.content.decode(response.encoding or "utf-8", errors="replace")


def fetch_text(url: str, timeout: int) -> str:
    response = requests.get(url, headers=HEADERS, timeout=timeout)
    response.raise_for_status()
    return decode_response(response)


def source_item_id(url: str, title: str) -> str:
    match = re.search(r"/(\d{8})/c(\d+)\.shtml", url)
    if match:
        return f"{match.group(1)}_c{match.group(2)}"
    return hashlib.sha1(f"{url}|{title}".encode("utf-8", errors="ignore")).hexdigest()[:40]


def extract_links(page_url: str, text: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(text, "html.parser")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a"):
        href = compact(anchor.get("href"), 1024)
        title = compact(anchor.get_text(" "), 512)
        if not href or not title:
            continue
        url = urljoin(page_url, href)
        if not re.search(r"10jqka\.com\.cn/\d{8}/c\d+\.shtml", url):
            continue
        if not any(keyword in title for keyword in TITLE_KEYWORDS):
            continue
        key = f"{url}|{title}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"title": title, "url": url})
    return rows


def meta_content(soup: BeautifulSoup, key: str) -> str:
    for attrs in ({"name": key}, {"property": key}):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            return compact(node.get("content"), 2000)
    return ""


def parse_article(url: str, html_text: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    title = meta_content(soup, "og:title")
    if not title and soup.title:
        title = compact(soup.title.get_text(" "), 512)
    summary = meta_content(soup, "description") or meta_content(soup, "og:description")

    content_node = soup.select_one(".article-content") or soup.select_one(".news-content")
    paragraphs: list[str] = []
    if content_node:
        nodes = content_node.find_all("p", recursive=True)
        if not nodes:
            nodes = content_node.find_all("div", recursive=True)
        for node in nodes:
            text = compact(node.get_text(" "), 800)
            if len(text) >= 20 and text not in paragraphs:
                paragraphs.append(text)
    if not paragraphs and summary:
        paragraphs = [summary]

    content = compact("\n".join(paragraphs), 6000)
    if not summary and paragraphs:
        summary = compact(paragraphs[0], 1000)
    published_at = meta_content(soup, "publishdate") or meta_content(soup, "pubdate")
    if not published_at:
        date_match = re.search(r"/(\d{4})(\d{2})(\d{2})/", url)
        if date_match:
            published_at = f"{date_match.group(1)}-{date_match.group(2)}-{date_match.group(3)} 15:30:00"
    return {
        "title": title,
        "summary": summary,
        "content": content,
        "published_at": published_at,
        "url": url,
    }


def score_candidate(row: dict[str, Any], trade_date: date) -> int:
    text = f"{row.get('title', '')} {row.get('summary', '')} {row.get('content', '')}"
    score = 0
    if trade_date.strftime("%Y%m%d") in str(row.get("url", "")):
        score += 100
    if "涨停复盘" in text:
        score += 50
    if "收评" in text:
        score += 30
    score += sum(8 for keyword in SUMMARY_KEYWORDS if keyword in text)
    if "A股三大指数" in text:
        score += 20
    return score


def collect_summary(trade_date: date, timeout: int) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for list_url in LIST_URLS:
        try:
            for link in extract_links(list_url, fetch_text(list_url, timeout)):
                if trade_date.strftime("%Y%m%d") not in link["url"]:
                    continue
                article = parse_article(link["url"], fetch_text(link["url"], timeout))
                merged = {**link, **article}
                candidates.append(merged)
        except Exception as exc:
            candidates.append(
                {
                    "title": "",
                    "url": list_url,
                    "summary": "",
                    "content": "",
                    "source_status": f"list_error:{type(exc).__name__}:{exc}",
                }
            )
    valid = [row for row in candidates if row.get("title") and row.get("summary")]
    if not valid:
        return {
            "ok": False,
            "trade_date": trade_date.isoformat(),
            "source": "ths_after_close_summary",
            "source_status": "not_found",
            "candidates": candidates[:20],
            "collected_at": now_text(),
        }
    best = max(valid, key=lambda row: score_candidate(row, trade_date))
    return {
        "ok": True,
        "trade_date": trade_date.isoformat(),
        "source": "ths_after_close_summary",
        "source_item_id": source_item_id(best["url"], best["title"]),
        "title": compact(best.get("title"), 512),
        "summary": compact(best.get("summary"), 2000),
        "content": compact(best.get("content"), 6000),
        "published_at": best.get("published_at") or f"{trade_date.isoformat()} 15:30:00",
        "url": best.get("url", ""),
        "source_status": "ok",
        "candidates": valid[:20],
        "collected_at": now_text(),
    }


def ensure_table(config: Any) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS ths_market_after_close_summaries (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
          trade_date DATE NOT NULL,
          source VARCHAR(64) NOT NULL DEFAULT 'ths_after_close_summary',
          source_item_id VARCHAR(128) NOT NULL DEFAULT '',
          title VARCHAR(512) NOT NULL DEFAULT '',
          summary TEXT NULL,
          content MEDIUMTEXT NULL,
          url VARCHAR(1024) NOT NULL DEFAULT '',
          published_at DATETIME(3) NULL,
          source_status VARCHAR(255) NOT NULL DEFAULT '',
          raw_json JSON NULL,
          collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (id),
          UNIQUE KEY uk_ths_market_after_close_item (trade_date, source_item_id),
          KEY idx_ths_market_after_close_date (trade_date, published_at),
          KEY idx_ths_market_after_close_collected (collected_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='THS after-close market review summary';
        """,
    )


def import_summary(config: Any, row: dict[str, Any]) -> int:
    if not row.get("ok"):
        return 0
    sql = f"""
    INSERT INTO ths_market_after_close_summaries(
      trade_date, source, source_item_id, title, summary, content, url,
      published_at, source_status, raw_json, collected_at
    )
    VALUES(
      {sql_string(row.get("trade_date"))}, {sql_string(row.get("source"))}, {sql_string(row.get("source_item_id"))},
      {sql_string(row.get("title"))}, {sql_string(row.get("summary"))}, {sql_string(row.get("content"))},
      {sql_string(row.get("url"))}, {sql_string(row.get("published_at"))}, {sql_string(row.get("source_status"))},
      {sql_json(row)}, {sql_string(row.get("collected_at") or now_text())}
    )
    ON DUPLICATE KEY UPDATE
      title=VALUES(title),
      summary=VALUES(summary),
      content=VALUES(content),
      url=VALUES(url),
      published_at=VALUES(published_at),
      source_status=VALUES(source_status),
      raw_json=VALUES(raw_json),
      collected_at=VALUES(collected_at);
    """
    run_mysql(config, sql)
    return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect THS after-close market review summary.")
    add_mysql_args(parser)
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--output-json", type=Path, default=project_root() / "runs" / "data_tasks" / "ths_market_after_close_summary.json")
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    payload = collect_summary(trade_date, args.timeout)
    imported = 0
    if args.mysql_enabled:
        config = mysql_config_from_args(args)
        ensure_table(config)
        imported = import_summary(config, payload)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps({**payload, "imported": imported}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": payload.get("ok", False), "trade_date": payload.get("trade_date"), "imported": imported, "output_json": str(args.output_json)}, ensure_ascii=False))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
