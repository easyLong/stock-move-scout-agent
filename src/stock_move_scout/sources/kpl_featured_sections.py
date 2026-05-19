from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_number, sql_string
from stock_move_scout.research_pool import (
    DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    DEFAULT_RESEARCH_POOL_GAIN_TOP,
    DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    DEFAULT_RESEARCH_POOL_RULE,
    ResearchPoolProvider,
)


KPL_FEATURED_SECTION_URL = "https://apphq.longhuvip.com/w1/api/index.php"
KPL_FEATURED_SECTION_SOURCE = "kpl_get_featured_section"


@dataclass(frozen=True)
class KplFeaturedSectionConfig:
    trade_date: str
    timeout: int = 8
    pause: float = 0.08
    limit: int = 0
    code: str = ""
    user_agent: str = "lhb/5.2.9 (com.kaipanla.www; build:0; iOS 15.1.0) Alamofire/5.2.9"


def ensure_kpl_featured_section_table(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_stock_featured_sections (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          code CHAR(6) NOT NULL,
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          pool_rank INT NOT NULL DEFAULT 0,
          pool_source_kind VARCHAR(32) NOT NULL DEFAULT '',
          section_code VARCHAR(32) NOT NULL,
          section_name VARCHAR(128) NOT NULL DEFAULT '',
          section_rank INT NOT NULL DEFAULT 0,
          section_score DECIMAL(12,4) NULL,
          leader_code CHAR(6) NOT NULL DEFAULT '',
          leader_name VARCHAR(64) NOT NULL DEFAULT '',
          leader_pct DECIMAL(10,4) NULL,
          leader_flag INT NOT NULL DEFAULT 0,
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_get_featured_section',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, code, section_code),
          KEY idx_kpl_featured_day_section (trade_date, section_name, section_rank),
          KEY idx_kpl_featured_day_code (trade_date, code, section_rank),
          KEY idx_kpl_featured_leader (trade_date, leader_code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL featured sections returned by GetFeaturedSection for research-pool stocks.';
        """,
    )


def load_research_pool_rows(config: MySqlConfig, trade_date: str, *, code: str = "", limit: int = 0) -> list[dict[str, Any]]:
    ResearchPoolProvider(config).latest_snapshot(trade_date)
    code_filter = f"AND code={sql_string(code)}" if code else ""
    limit_sql = f"LIMIT {max(1, int(limit))}" if int(limit or 0) > 0 else ""
    sql = f"""
    SELECT code, stock_name, pool_rank, source_kind
    FROM research_pool_items
    WHERE trade_date={sql_string(trade_date)}
      AND rule={sql_string(DEFAULT_RESEARCH_POOL_RULE)}
      AND limit_up_days={sql_int(DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS)}
      AND gain_period_days={sql_int(DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS)}
      AND gain_top={sql_int(DEFAULT_RESEARCH_POOL_GAIN_TOP)}
      {code_filter}
    ORDER BY pool_rank ASC, code ASC
    {limit_sql};
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 4:
            continue
        rows.append(
            {
                "code": str(row[0] or "").strip(),
                "stock_name": str(row[1] or "").strip(),
                "pool_rank": int(row[2] or 0),
                "source_kind": str(row[3] or "").strip(),
            }
        )
    return rows


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Accept-Language": "zh-Hans-CN;q=1.0, bo-CN;q=0.9, ar-CN;q=0.8",
        "Accept-Encoding": "gzip;q=1.0, compress;q=0.5",
    }


def fetch_featured_sections(session: requests.Session, stock_code: str, cfg: KplFeaturedSectionConfig) -> dict[str, Any]:
    params = {
        "PhoneOSNew": "2",
        "StockID": stock_code,
        "VerSion": "5.2.0.9",
        "a": "GetFeaturedSection",
        "apiv": "w28",
        "c": "StockL2Data",
        "DeviceID": str(uuid.uuid4()),
    }
    response = session.get(
        KPL_FEATURED_SECTION_URL,
        params=params,
        headers=_headers(cfg.user_agent),
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def normalize_featured_sections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("info")
    if not isinstance(raw_items, list):
        raw_items = payload.get("list") if isinstance(payload.get("list"), list) else []
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(raw_items, start=1):
        if not isinstance(item, list) or len(item) < 2:
            continue
        rows.append(
            {
                "section_rank": rank,
                "section_code": str(item[0] or "").strip(),
                "section_name": str(item[1] or "").strip(),
                "section_score": item[2] if len(item) > 2 else None,
                "leader_code": str(item[3] or "").strip() if len(item) > 3 else "",
                "leader_name": str(item[4] or "").strip() if len(item) > 4 else "",
                "leader_pct": item[5] if len(item) > 5 else None,
                "leader_flag": int(float(item[6] or 0)) if len(item) > 6 and str(item[6]).strip() else 0,
                "raw_json": item,
            }
        )
    return [row for row in rows if row["section_code"] and row["section_name"]]


def replace_stock_sections(
    config: MySqlConfig,
    *,
    trade_date: str,
    captured_at: str,
    stock: dict[str, Any],
    sections: list[dict[str, Any]],
    payload: dict[str, Any],
) -> int:
    code = str(stock.get("code") or "").strip()
    if not code:
        return 0
    statements = [
        f"""
        DELETE FROM kpl_stock_featured_sections
        WHERE trade_date={sql_string(trade_date)}
          AND code={sql_string(code)};
        """
    ]
    for section in sections:
        raw_json = {
            "item": section.get("raw_json"),
            "response_errcode": payload.get("errcode"),
            "response_ttag": payload.get("ttag"),
        }
        statements.append(
            f"""
            INSERT INTO kpl_stock_featured_sections(
              trade_date, captured_at, code, stock_name, pool_rank, pool_source_kind,
              section_code, section_name, section_rank, section_score,
              leader_code, leader_name, leader_pct, leader_flag, source, raw_json
            ) VALUES (
              {sql_string(trade_date)}, {sql_string(captured_at)}, {sql_string(code)},
              {sql_string(stock.get("stock_name") or "")}, {sql_int(stock.get("pool_rank"))},
              {sql_string(stock.get("source_kind") or "")},
              {sql_string(section.get("section_code") or "")},
              {sql_string(section.get("section_name") or "")},
              {sql_int(section.get("section_rank"))},
              {sql_number(section.get("section_score"))},
              {sql_string(section.get("leader_code") or "")},
              {sql_string(section.get("leader_name") or "")},
              {sql_number(section.get("leader_pct"))},
              {sql_int(section.get("leader_flag"))},
              {sql_string(KPL_FEATURED_SECTION_SOURCE)},
              {sql_json(raw_json)}
            )
            ON DUPLICATE KEY UPDATE
              captured_at=VALUES(captured_at),
              stock_name=VALUES(stock_name),
              pool_rank=VALUES(pool_rank),
              pool_source_kind=VALUES(pool_source_kind),
              section_name=VALUES(section_name),
              section_rank=VALUES(section_rank),
              section_score=VALUES(section_score),
              leader_code=VALUES(leader_code),
              leader_name=VALUES(leader_name),
              leader_pct=VALUES(leader_pct),
              leader_flag=VALUES(leader_flag),
              source=VALUES(source),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    run_mysql(config, "\n".join(statements))
    return len(sections)


def collect_kpl_stock_featured_sections(config: MySqlConfig, cfg: KplFeaturedSectionConfig) -> dict[str, Any]:
    ensure_kpl_featured_section_table(config)
    stocks = load_research_pool_rows(config, cfg.trade_date, code=cfg.code, limit=cfg.limit)
    if not cfg.code and int(cfg.limit or 0) <= 0:
        run_mysql(
            config,
            f"DELETE FROM kpl_stock_featured_sections WHERE trade_date={sql_string(cfg.trade_date)};",
        )
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    imported = 0
    ok_count = 0
    empty_count = 0
    failed: list[dict[str, str]] = []
    session = requests.Session()
    for index, stock in enumerate(stocks, start=1):
        code = str(stock.get("code") or "").strip()
        if not code:
            continue
        try:
            payload = fetch_featured_sections(session, code, cfg)
            if str(payload.get("errcode", "0")) != "0":
                raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
            sections = normalize_featured_sections(payload)
            imported += replace_stock_sections(
                config,
                trade_date=cfg.trade_date,
                captured_at=captured_at,
                stock=stock,
                sections=sections,
                payload=payload,
            )
            ok_count += 1
            if not sections:
                empty_count += 1
        except Exception as exc:
            failed.append({"code": code, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if cfg.pause > 0 and index < len(stocks):
            time.sleep(float(cfg.pause))
    return {
        "ok": not failed,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "stock_count": len(stocks),
        "ok_count": ok_count,
        "empty_count": empty_count,
        "imported": imported,
        "failed_count": len(failed),
        "failed": failed[:20],
        "source": KPL_FEATURED_SECTION_SOURCE,
    }
