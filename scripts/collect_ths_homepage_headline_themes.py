#!/usr/bin/env python
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import re
import sys
import time
from datetime import date, datetime
from html import unescape
from pathlib import Path
from typing import Any

import akshare as ak
import pywencai
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_int, sql_json, sql_string


SOURCE = "ths_homepage_headline"
HOME_URL = "https://www.10jqka.com.cn/"
HOT_THEME_URL = "https://news.10jqka.com.cn/app/headline/v1/hot-theme"
THEME_DETAIL_URL = "https://news.10jqka.com.cn/app/theme/v1/theme"
BLOCK_STOCK_URL = "https://news.10jqka.com.cn/app/concept_v2_api/open/api/concept/quote/v1/get_block_stock_rank"
Q_BASE_URL = "https://q.10jqka.com.cn"


def headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": HOME_URL,
    }


def compact(value: Any, limit: int = 255) -> str:
    text = "" if value is None else str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit]


def ensure_table(config: Any) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS ths_homepage_headline_themes (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          trade_date DATE NOT NULL,
          snapshot_id VARCHAR(32) NOT NULL,
          rank_no INT NOT NULL DEFAULT 0,
          theme_id VARCHAR(64) NOT NULL DEFAULT '',
          theme_name VARCHAR(128) NOT NULL DEFAULT '',
          theme_url VARCHAR(1024) NOT NULL DEFAULT '',
          index_code VARCHAR(32) NOT NULL DEFAULT '',
          block_name VARCHAR(128) NOT NULL DEFAULT '',
          block_gain DECIMAL(12,4) NULL,
          source VARCHAR(64) NOT NULL DEFAULT 'ths_homepage_headline',
          page_url VARCHAR(1024) NOT NULL DEFAULT '',
          raw_json JSON NULL,
          collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uk_ths_home_headline_snapshot_rank (snapshot_id, rank_no),
          UNIQUE KEY uk_ths_home_headline_date_theme (trade_date, source, theme_name),
          KEY idx_ths_home_headline_date_rank (trade_date, rank_no),
          KEY idx_ths_home_headline_collected (collected_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='THS homepage headline topic row themes.';
        """,
    )
    for column_name, column_sql in {
        "theme_id": "VARCHAR(64) NOT NULL DEFAULT '' AFTER rank_no",
        "theme_url": "VARCHAR(1024) NOT NULL DEFAULT '' AFTER theme_name",
        "index_code": "VARCHAR(32) NOT NULL DEFAULT '' AFTER theme_url",
        "block_name": "VARCHAR(128) NOT NULL DEFAULT '' AFTER index_code",
        "block_gain": "DECIMAL(12,4) NULL AFTER block_name",
    }.items():
        try:
            run_mysql(config, f"ALTER TABLE ths_homepage_headline_themes ADD COLUMN {column_name} {column_sql};")
        except Exception:
            pass
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS ths_homepage_headline_theme_members (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          trade_date DATE NOT NULL,
          snapshot_id VARCHAR(32) NOT NULL,
          theme_rank INT NOT NULL DEFAULT 0,
          theme_id VARCHAR(64) NOT NULL DEFAULT '',
          theme_name VARCHAR(128) NOT NULL DEFAULT '',
          index_code VARCHAR(32) NOT NULL DEFAULT '',
          block_name VARCHAR(128) NOT NULL DEFAULT '',
          stock_rank INT NOT NULL DEFAULT 0,
          stock_code CHAR(6) NOT NULL DEFAULT '',
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          stock_market_id VARCHAR(32) NOT NULL DEFAULT '',
          gain DECIMAL(12,4) NULL,
          source VARCHAR(64) NOT NULL DEFAULT 'ths_homepage_headline',
          raw_json JSON NULL,
          collected_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uk_ths_home_member_snapshot_theme_stock (snapshot_id, theme_id, stock_code),
          KEY idx_ths_home_member_date_theme (trade_date, theme_name, stock_rank),
          KEY idx_ths_home_member_date_stock (trade_date, stock_code),
          KEY idx_ths_home_member_index (index_code, stock_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='Hot component stocks shown under THS homepage headline themes.';
        """,
    )


def fetch_html(timeout: int) -> str:
    resp = requests.get(
        HOME_URL,
        headers=headers(),
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.content.decode("utf-8", errors="replace")


def request_json(url: str, timeout: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    resp = requests.get(url, params=params, headers={**headers(), "Accept": "application/json, text/plain, */*"}, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if int(data.get("status_code") or 0) != 0:
        raise RuntimeError(f"{url} status={data.get('status_code')} msg={data.get('status_msg')}")
    return data.get("data") if isinstance(data.get("data"), dict) else {"items": data.get("data")}


def fetch_hot_themes(timeout: int) -> list[dict[str, Any]]:
    data = request_json(HOT_THEME_URL, timeout)
    items = data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    return []


def to_float(value: Any) -> float | None:
    try:
        text = str(value or "").replace("%", "").replace(",", "").strip()
        return float(text) if text else None
    except Exception:
        return None


def normalize_name(value: Any) -> str:
    text = compact(value, 128)
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", "", text).lower()
    for token in ["概念股", "概念", "板块", "产业", "行业"]:
        text = text.replace(token, "")
    return text


def board_candidates() -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for board_type, path, loader in [
        ("concept", "gn", ak.stock_board_concept_name_ths),
        ("industry", "thshy", ak.stock_board_industry_name_ths),
    ]:
        try:
            df = loader()
        except Exception:
            continue
        for _, row in df.iterrows():
            name = compact(row.get("name"), 128)
            code = compact(row.get("code"), 32)
            if name and code:
                candidates.append(
                    {
                        "board_type": board_type,
                        "path": path,
                        "name": name,
                        "code": code,
                        "norm": normalize_name(name),
                    }
                )
    return candidates


def match_board(theme_name: str, block_name: str, boards: list[dict[str, str]]) -> dict[str, Any]:
    queries = [normalize_name(theme_name), normalize_name(block_name)]
    queries = [item for item in queries if item]
    best: dict[str, Any] = {}
    best_score = -1.0
    for board in boards:
        board_norm = board.get("norm", "")
        if not board_norm:
            continue
        score = 0.0
        for query in queries:
            current = SequenceMatcher(None, query, board_norm).ratio()
            if query == board_norm:
                current += 2.0
            elif query in board_norm or board_norm in query:
                current += 0.8
            score = max(score, current)
        if score > best_score:
            best_score = score
            best = {**board, "match_score": round(best_score, 4)}
    if best_score < 0.72:
        return {}
    return best


def clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", value, flags=re.S)
    return compact(unescape(text), 255)


def clean_code(value: Any) -> str:
    match = re.search(r"(\d{6})", str(value or ""))
    return match.group(1) if match else ""


def find_column(columns: list[str], *needles: str) -> str:
    for col in columns:
        text = str(col)
        if all(needle in text for needle in needles):
            return text
    return ""


def parse_amount_yi(value: str) -> float | None:
    text = compact(value, 64).replace(",", "")
    try:
        if text.endswith("亿"):
            return float(text[:-1])
        if text.endswith("万"):
            return float(text[:-1]) / 10000
        return float(text) / 100000000
    except Exception:
        return None


def parse_component_rows(html_text: str, theme_name: str, board: dict[str, Any], page: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html_text, flags=re.S | re.I):
        row_html = match.group(1)
        code_match = re.search(r"stockpage\.10jqka\.com\.cn/(\d{6})/?", row_html)
        if not code_match:
            continue
        code = code_match.group(1)
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.S | re.I)
        values = [clean_html_text(cell) for cell in cells]
        if len(values) < 5 or len(values) < 2 or values[1] != code:
            continue
        stock_name = values[2] if len(values) > 2 else ""
        rows.append(
            {
                "stockCode": code,
                "stockName": stock_name,
                "marketId": "",
                "gain": to_float(values[4] if len(values) > 4 else None),
                "amountYi": parse_amount_yi(values[10] if len(values) > 10 else ""),
                "member_source": "ths_board_full_component",
                "board_type": board.get("board_type", ""),
                "board_path": board.get("path", ""),
                "board_code": board.get("code", ""),
                "board_name": board.get("name", ""),
                "match_score": board.get("match_score"),
                "page": page,
                "raw_values": values,
                "theme_name": theme_name,
            }
        )
    return rows


def fetch_iwencai_theme_members(theme_name: str, block_name: str, timeout: int) -> list[dict[str, Any]]:
    queries = []
    for name in [theme_name, block_name]:
        name = compact(name, 128)
        if name and name not in queries:
            queries.append(name)
    for name in queries:
        query = f"{name}成分股"
        try:
            df = pywencai.get(query=query, query_type="stock", loop=True)
        except Exception:
            continue
        if df is None or getattr(df, "empty", True):
            continue
        columns = [str(col) for col in df.columns]
        code_col = find_column(columns, "股票代码") or "code"
        name_col = find_column(columns, "股票简称")
        pct_col = find_column(columns, "最新涨跌幅")
        concept_col = find_column(columns, "所属概念")
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for idx, item in df.iterrows():
            raw = {str(k): (None if str(v).lower() == "nan" else v) for k, v in item.to_dict().items()}
            code = clean_code(raw.get(code_col) or raw.get("code"))
            if not code or code in seen:
                continue
            seen.add(code)
            rows.append(
                {
                    "stockCode": code,
                    "stockName": compact(raw.get(name_col), 64),
                    "marketId": compact(raw.get("market_code"), 32),
                    "gain": to_float(raw.get(pct_col)),
                    "member_source": "iwencai_theme_full_component",
                    "query": query,
                    "concept_text": compact(raw.get(concept_col), 512),
                    "rank_no": len(rows) + 1,
                    "raw_json": raw,
                }
            )
        if rows:
            return rows
    return []


def db_concept_candidates(theme_name: str, block_name: str) -> list[str]:
    candidates: list[str] = []
    for value in [theme_name, block_name]:
        text = compact(value, 128)
        if not text:
            continue
        for item in [
            text,
            text.replace("概念", ""),
            text.replace("板块", ""),
            text.replace("产业", ""),
            f"{text}概念" if "概念" not in text else text,
        ]:
            item = compact(item, 128)
            if item and item not in candidates:
                candidates.append(item)
    return candidates


def fetch_db_stock_concept_members(config: Any, theme_name: str, block_name: str) -> list[dict[str, Any]]:
    candidates = db_concept_candidates(theme_name, block_name)
    if not candidates:
        return []
    exact_sql = ",".join(sql_string(item) for item in candidates)
    norm_values = sorted({normalize_name(item) for item in candidates if normalize_name(item)})
    norm_filter = ""
    if norm_values:
        norm_filter = " OR REPLACE(REPLACE(REPLACE(REPLACE(c.concept_name,'概念股',''),'概念',''),'板块',''),'产业','') IN (" + ",".join(sql_string(item) for item in norm_values) + ")"
    sql = f"""
    SELECT
      c.code,
      MAX(c.stock_name) AS stock_name,
      SUBSTRING_INDEX(GROUP_CONCAT(c.concept_name ORDER BY c.fit_rank ASC SEPARATOR '||'), '||', 1) AS concept_name,
      SUBSTRING_INDEX(GROUP_CONCAT(c.concept_id ORDER BY c.fit_rank ASC SEPARATOR '||'), '||', 1) AS concept_id,
      SUBSTRING_INDEX(GROUP_CONCAT(c.quote_code ORDER BY c.fit_rank ASC SEPARATOR '||'), '||', 1) AS quote_code,
      SUBSTRING_INDEX(GROUP_CONCAT(c.concept_market_id ORDER BY c.fit_rank ASC SEPARATOR '||'), '||', 1) AS concept_market_id,
      MIN(c.fit_rank) AS fit_rank
    FROM ths_stock_concept_explanations c
    JOIN stocks s ON s.code=c.code
    WHERE (
        c.concept_name IN ({exact_sql})
        {norm_filter}
      )
      AND c.fit_rank < 999
      AND c.code REGEXP '^(000|001|002|003|300|301|600|601|603|605|688|689)'
      AND COALESCE(s.is_st, 0)=0
      AND s.name NOT LIKE '%ST%'
      AND s.name NOT LIKE '%退市%'
    GROUP BY c.code
    ORDER BY MIN(c.fit_rank) ASC, c.code ASC
    """
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(mysql_rows(run_mysql(config, sql + ";", batch=True, raw=True)), 1):
        if len(row) < 7:
            continue
        rows.append(
            {
                "stockCode": compact(row[0], 6),
                "stockName": compact(row[1], 64),
                "marketId": compact(row[5], 32),
                "gain": None,
                "member_source": "ths_f10_stock_concept_reverse",
                "concept_name": compact(row[2], 128),
                "concept_id": compact(row[3], 64),
                "quote_code": compact(row[4], 32),
                "fit_rank": row[6],
                "rank_no": idx,
                "theme_name": theme_name,
            }
        )
    return rows


def fetch_board_full_members(
    theme_name: str,
    block_name: str,
    boards: list[dict[str, str]],
    timeout: int,
    pause: float,
    max_pages: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    board = match_board(theme_name, block_name, boards)
    if not board:
        return {}, []
    session = requests.Session()
    session.headers.update({**headers(), "Referer": Q_BASE_URL + "/"})
    members: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_pages = 1
    for page in range(1, max(1, int(max_pages)) + 1):
        url = f"{Q_BASE_URL}/{board['path']}/detail/code/{board['code']}/page/{page}/"
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "gbk"
        html_text = resp.text
        page_match = re.search(r'<span class="page_info">(\d+)\s*/\s*(\d+)</span>', html_text)
        if page_match:
            total_pages = int(page_match.group(2))
        rows = parse_component_rows(html_text, theme_name, board, page)
        for row in rows:
            code = compact(row.get("stockCode"), 6)
            if code and code not in seen:
                seen.add(code)
                members.append(row)
        if page >= min(total_pages, int(max_pages)):
            break
        time.sleep(max(0.0, float(pause)))
    return {**board, "page_count": total_pages, "member_count": len(members)}, members


def theme_index_code(theme_id: str, timeout: int) -> tuple[str, str, float | None, dict[str, Any]]:
    detail = request_json(THEME_DETAIL_URL, timeout, {"themeId": theme_id})
    index_code = ""
    for module in detail.get("module") or []:
        if isinstance(module, dict) and int(module.get("type") or 0) == 3 and module.get("id"):
            index_code = compact(module.get("id"), 32)
            break
    if not index_code:
        return "", "", None, detail
    block_data = request_json(BLOCK_STOCK_URL, timeout, {"indexCode": index_code, "marketId": "48"})
    block = block_data.get("block") if isinstance(block_data.get("block"), dict) else {}
    return index_code, compact(block.get("blockName"), 128), to_float(block.get("gain")), {**detail, "block_stock": block_data}


def fetch_theme_members(index_code: str, timeout: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not index_code:
        return {}, []
    data = request_json(BLOCK_STOCK_URL, timeout, {"indexCode": index_code, "marketId": "48"})
    block = data.get("block") if isinstance(data.get("block"), dict) else {}
    stocks = data.get("stockList") if isinstance(data.get("stockList"), list) else []
    return block, [item for item in stocks if isinstance(item, dict)]


def extract_headline_themes(html_text: str) -> list[str]:
    text = unescape(html_text)
    start = text.find(">头条<")
    if start < 0:
        start = text.find("头条")
    if start < 0:
        start = text.find("CPO")
    if start < 0:
        return []
    chunk = text[max(0, start - 5000) : start + 45000]
    span_texts = [re.sub(r"<[^>]+>", "", item).strip() for item in re.findall(r"<span[^>]*>(.*?)</span>", chunk, flags=re.S)]
    themes: list[str] = []
    started = False
    stop_words = {"同花顺7x24快讯", "同顺号", "财闻"}
    for item in span_texts:
        name = compact(unescape(item), 128)
        if not name:
            continue
        if name == "头条":
            started = True
            continue
        if not started:
            continue
        if name in stop_words or re.fullmatch(r"\d+", name) or "小时前" in name or "昨天" in name:
            break
        if name not in themes:
            themes.append(name)
        if len(themes) >= 50:
            break
    return themes


def extract_headline_themes(html_text: str) -> list[str]:
    text = unescape(html_text)
    anchors = [">头条<", "头条", "CPO"]
    start = next((text.find(anchor) for anchor in anchors if text.find(anchor) >= 0), -1)
    if start < 0:
        return []
    chunk = text[max(0, start - 5000) : start + 45000]
    span_texts = [re.sub(r"<[^>]+>", "", item).strip() for item in re.findall(r"<span[^>]*>(.*?)</span>", chunk, flags=re.S)]
    themes: list[str] = []
    started = False
    stop_words = {"同花顺24快讯", "同顺号", "财闻"}
    for item in span_texts:
        name = compact(unescape(item), 128)
        if not name:
            continue
        if name == "头条":
            started = True
            continue
        if not started and "CPO" in name:
            started = True
        if not started:
            continue
        if name in stop_words or re.fullmatch(r"\d+", name) or "小时前" in name or "昨天" in name:
            break
        if name not in themes:
            themes.append(name)
        if len(themes) >= 50:
            break
    return themes


def write_themes(config: Any, trade_date: str, themes: list[dict[str, Any]], replace_date: bool) -> dict[str, Any]:
    if not themes:
        return {"written": 0, "snapshot_id": "", "themes_count": 0}
    snapshot_id = datetime.now().strftime("%Y%m%d%H%M%S")
    if replace_date:
        run_mysql(config, f"DELETE FROM ths_homepage_headline_themes WHERE trade_date={sql_string(trade_date)} AND source={sql_string(SOURCE)};")
        run_mysql(config, f"DELETE FROM ths_homepage_headline_theme_members WHERE trade_date={sql_string(trade_date)} AND source={sql_string(SOURCE)};")
    statements: list[str] = []
    for idx, theme in enumerate(themes, 1):
        members = theme.get("members") if isinstance(theme.get("members"), list) else []
        statements.append(
            f"""
            INSERT INTO ths_homepage_headline_themes(
              trade_date, snapshot_id, rank_no, theme_id, theme_name, theme_url,
              index_code, block_name, block_gain, source, page_url, raw_json
            ) VALUES(
              {sql_string(trade_date)}, {sql_string(snapshot_id)}, {sql_int(idx)},
              {sql_string(theme.get('theme_id'))}, {sql_string(theme.get('theme_name'))}, {sql_string(theme.get('theme_url'))},
              {sql_string(theme.get('index_code'))}, {sql_string(theme.get('block_name'))}, {str(theme.get('block_gain')) if theme.get('block_gain') is not None else 'NULL'},
              {sql_string(SOURCE)}, {sql_string(HOME_URL)},
              {sql_json({'source': SOURCE, 'page_url': HOME_URL, 'theme': theme, 'rank_no': idx})}
            )
            ON DUPLICATE KEY UPDATE
              snapshot_id=VALUES(snapshot_id), rank_no=VALUES(rank_no), theme_id=VALUES(theme_id),
              theme_url=VALUES(theme_url), index_code=VALUES(index_code), block_name=VALUES(block_name),
              block_gain=VALUES(block_gain), page_url=VALUES(page_url), raw_json=VALUES(raw_json), updated_at=NOW(3);
            """
        )
        for stock_idx, stock in enumerate(members, 1):
            code = compact(stock.get("stockCode"), 6)
            if not re.fullmatch(r"\d{6}", code):
                continue
            gain = to_float(stock.get("gain"))
            statements.append(
                f"""
                INSERT INTO ths_homepage_headline_theme_members(
                  trade_date, snapshot_id, theme_rank, theme_id, theme_name, index_code, block_name,
                  stock_rank, stock_code, stock_name, stock_market_id, gain, source, raw_json
                ) VALUES(
                  {sql_string(trade_date)}, {sql_string(snapshot_id)}, {sql_int(idx)},
                  {sql_string(theme.get('theme_id'))}, {sql_string(theme.get('theme_name'))},
                  {sql_string(theme.get('index_code'))}, {sql_string(theme.get('block_name'))},
                  {sql_int(stock_idx)}, {sql_string(code)}, {sql_string(compact(stock.get('stockName'), 64))},
                  {sql_string(stock.get('marketId'))}, {str(gain) if gain is not None else 'NULL'},
                  {sql_string(SOURCE)}, {sql_json({**stock, 'rank_no': stock_idx, 'member_source': stock.get('member_source') or 'headline_hot'})}
                )
                ON DUPLICATE KEY UPDATE
                  theme_rank=VALUES(theme_rank), theme_name=VALUES(theme_name), index_code=VALUES(index_code),
                  block_name=VALUES(block_name), stock_rank=VALUES(stock_rank), stock_name=VALUES(stock_name),
                  stock_market_id=VALUES(stock_market_id), gain=VALUES(gain), raw_json=VALUES(raw_json), updated_at=NOW(3);
                """
            )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return {"written": len(themes), "members_written": sum(len(item.get("members") or []) for item in themes), "snapshot_id": snapshot_id, "themes_count": len(themes)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect THS homepage headline topic-row themes.")
    parser.add_argument("--trade-date", default=date.today().isoformat())
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--pause", type=float, default=0.08)
    parser.add_argument("--max-pages", type=int, default=80)
    parser.add_argument("--hot-only", action="store_true", help="Only collect the 3 hot members shown by the headline app API.")
    parser.add_argument("--use-iwencai-members", action="store_true", help="Fallback to iWenCai for full theme members when F10/Q-page members are unavailable.")
    parser.add_argument("--replace-date", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fail-on-empty", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    ensure_table(config)
    api_themes = fetch_hot_themes(args.timeout)
    boards = [] if args.hot_only else board_candidates()
    if api_themes:
        themes: list[dict[str, Any]] = []
        for item in api_themes:
            theme_id = compact(item.get("themeId"), 64)
            index_code, block_name, block_gain, raw_detail = theme_index_code(theme_id, args.timeout)
            block, hot_members = fetch_theme_members(index_code, args.timeout)
            theme_name = compact(item.get("themeName"), 128)
            resolved_block_name = block_name or compact(block.get("blockName"), 128)
            board_match: dict[str, Any] = {}
            db_members: list[dict[str, Any]] = []
            full_members: list[dict[str, Any]] = []
            iwencai_members: list[dict[str, Any]] = []
            if not args.hot_only:
                try:
                    db_members = fetch_db_stock_concept_members(config, theme_name, resolved_block_name)
                except Exception:
                    db_members = []
            if boards and not db_members:
                try:
                    board_match, full_members = fetch_board_full_members(
                        theme_name,
                        resolved_block_name,
                        boards,
                        args.timeout,
                        args.pause,
                        args.max_pages,
                    )
                except Exception as exc:
                    board_match = {"error": f"{type(exc).__name__}: {exc}"}
                    full_members = []
            if args.use_iwencai_members and not args.hot_only and (board_match or index_code):
                iwencai_members = fetch_iwencai_theme_members(theme_name, resolved_block_name, args.timeout)
                if len(iwencai_members) > 1000:
                    iwencai_members = []
            best_members = db_members or full_members or iwencai_members
            members = best_members or [
                {**member, "member_source": "headline_hot", "fallback_reason": "full_component_empty"}
                for member in hot_members
            ]
            themes.append(
                {
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "theme_url": compact(item.get("themeUrl"), 1024),
                    "index_code": index_code,
                    "block_name": resolved_block_name,
                    "block_gain": block_gain if block_gain is not None else to_float(block.get("gain")),
                    "members": members,
                    "raw_detail": raw_detail,
                    "board_match": board_match,
                    "hot_members_count": len(hot_members),
                    "db_members_count": len(db_members),
                    "full_members_count": len(full_members),
                    "iwencai_members_count": len(iwencai_members),
                    "member_source": (best_members[0].get("member_source") if best_members else "headline_hot"),
                }
            )
    else:
        html_text = fetch_html(args.timeout)
        themes = [{"theme_name": name, "members": []} for name in extract_headline_themes(html_text)]
    result = write_themes(config, str(args.trade_date), themes, bool(args.replace_date))
    print(json.dumps({"source": SOURCE, "trade_date": args.trade_date, "themes": [item.get("theme_name") for item in themes], **result}, ensure_ascii=False))
    return 1 if (args.fail_on_empty and not themes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
