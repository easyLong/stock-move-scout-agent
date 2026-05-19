from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_string
from stock_move_scout.sources.kpl_featured_sections import load_research_pool_rows


KPL_LIMIT_UP_REASON_URL = "https://apphq.longhuvip.com/w1/api/index.php"
KPL_LIMIT_UP_REASON_SOURCE = "kpl_get_kline_zhangting"


@dataclass(frozen=True)
class KplLimitUpReasonConfig:
    trade_date: str
    timeout: int = 8
    pause: float = 0.08
    limit: int = 0
    code: str = ""
    user_agent: str = "lhb/5.12.0.3 (com.kaipanla.www; build:0; iOS 15.1.0) Alamofire/5.12.0.3"


def ensure_kpl_limit_up_reason_table(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_stock_limit_up_reasons (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          code CHAR(6) NOT NULL,
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          pool_rank INT NOT NULL DEFAULT 0,
          pool_source_kind VARCHAR(32) NOT NULL DEFAULT '',
          reason_date DATE NOT NULL,
          reason_title VARCHAR(255) NOT NULL DEFAULT '',
          reason_text TEXT NULL,
          concept_explain TEXT NULL,
          boom_theme TEXT NULL,
          role_label VARCHAR(64) NOT NULL DEFAULT '',
          source_position VARCHAR(64) NOT NULL DEFAULT '',
          reason_type VARCHAR(32) NOT NULL DEFAULT '',
          zscode JSON NULL,
          pzscode VARCHAR(32) NOT NULL DEFAULT '',
          group_text VARCHAR(255) NOT NULL DEFAULT '',
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_get_kline_zhangting',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, code, reason_date, source),
          KEY idx_kpl_lu_reason_day (reason_date, code),
          KEY idx_kpl_lu_reason_trade_day (trade_date, pool_rank),
          KEY idx_kpl_lu_reason_role (reason_date, role_label)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL per-stock limit-up reasons from StockLineData.GetKLineZhangTing.';
        """,
    )


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Accept-Language": "zh-Hans-CN;q=1.0, en-CN;q=0.9",
        "Accept": "*/*",
    }


def fetch_limit_up_reasons(session: requests.Session, stock_code: str, cfg: KplLimitUpReasonConfig) -> dict[str, Any]:
    data = {
        "PhoneOSNew": "2",
        "StockID": stock_code,
        "VerSion": "5.12.0.3",
        "a": "GetKLineZhangTing",
        "apiv": "w34",
        "c": "StockLineData",
        "DeviceID": str(uuid.uuid4()),
    }
    response = session.post(
        KPL_LIMIT_UP_REASON_URL,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def _reason_title(reason_text: str) -> str:
    title = reason_text.split("；", 1)[0].split(";", 1)[0].strip()
    return title[:120] if title else "开盘啦涨停原因"


def normalize_limit_up_reasons(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = payload.get("List")
    if not isinstance(raw_items, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        reason_date = str(item.get("Date") or "").strip()
        reason_text = str(item.get("Reason") or "").strip()
        if not reason_date or not reason_text:
            continue
        rows.append(
            {
                "reason_date": reason_date,
                "reason_title": _reason_title(reason_text),
                "reason_text": reason_text,
                "concept_explain": str(item.get("GNSM") or "").strip(),
                "boom_theme": str(item.get("Boom_ZS") or "").strip(),
                "role_label": str(item.get("SCLT") or "").strip(),
                "source_position": str(item.get("SCDW") or "").strip(),
                "reason_type": str(item.get("Type") or "").strip(),
                "zscode": item.get("ZSCode") if isinstance(item.get("ZSCode"), list) else [],
                "pzscode": str(item.get("PZSCode") or "").strip(),
                "group_text": str(item.get("Group_Str") or "").strip(),
                "raw_json": item,
            }
        )
    return rows


def replace_stock_limit_up_reasons(
    config: MySqlConfig,
    *,
    trade_date: str,
    captured_at: str,
    stock: dict[str, Any],
    rows: list[dict[str, Any]],
    payload: dict[str, Any],
) -> int:
    code = str(stock.get("code") or "").strip()
    if not code:
        return 0
    statements: list[str] = []
    for row in rows:
        raw_json = {
            "item": row.get("raw_json"),
            "response_errcode": payload.get("errcode"),
            "response_time": payload.get("Time"),
            "response_ttag": payload.get("ttag"),
        }
        statements.append(
            f"""
            INSERT INTO kpl_stock_limit_up_reasons(
              trade_date, captured_at, code, stock_name, pool_rank, pool_source_kind,
              reason_date, reason_title, reason_text, concept_explain, boom_theme,
              role_label, source_position, reason_type, zscode, pzscode, group_text, source, raw_json
            ) VALUES (
              {sql_string(trade_date)}, {sql_string(captured_at)}, {sql_string(code)},
              {sql_string(stock.get("stock_name") or "")}, {sql_int(stock.get("pool_rank"))},
              {sql_string(stock.get("source_kind") or "")},
              {sql_string(row.get("reason_date") or "")},
              {sql_string(row.get("reason_title") or "")},
              {sql_string(row.get("reason_text") or "")},
              {sql_string(row.get("concept_explain") or "")},
              {sql_string(row.get("boom_theme") or "")},
              {sql_string(row.get("role_label") or "")},
              {sql_string(row.get("source_position") or "")},
              {sql_string(row.get("reason_type") or "")},
              {sql_json(row.get("zscode") or [])},
              {sql_string(row.get("pzscode") or "")},
              {sql_string(row.get("group_text") or "")},
              {sql_string(KPL_LIMIT_UP_REASON_SOURCE)},
              {sql_json(raw_json)}
            )
            ON DUPLICATE KEY UPDATE
              trade_date=VALUES(trade_date),
              captured_at=VALUES(captured_at),
              stock_name=VALUES(stock_name),
              pool_rank=VALUES(pool_rank),
              pool_source_kind=VALUES(pool_source_kind),
              reason_title=VALUES(reason_title),
              reason_text=VALUES(reason_text),
              concept_explain=VALUES(concept_explain),
              boom_theme=VALUES(boom_theme),
              role_label=VALUES(role_label),
              source_position=VALUES(source_position),
              reason_type=VALUES(reason_type),
              zscode=VALUES(zscode),
              pzscode=VALUES(pzscode),
              group_text=VALUES(group_text),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    run_mysql(config, "\n".join(statements))
    return len(rows)


def collect_kpl_limit_up_reasons(config: MySqlConfig, cfg: KplLimitUpReasonConfig) -> dict[str, Any]:
    ensure_kpl_limit_up_reason_table(config)
    stocks = load_research_pool_rows(config, cfg.trade_date, code=cfg.code, limit=cfg.limit)
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
            payload = fetch_limit_up_reasons(session, code, cfg)
            if str(payload.get("errcode", "0")) != "0":
                raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
            rows = normalize_limit_up_reasons(payload)
            imported += replace_stock_limit_up_reasons(
                config,
                trade_date=cfg.trade_date,
                captured_at=captured_at,
                stock=stock,
                rows=rows,
                payload=payload,
            )
            ok_count += 1
            if not rows:
                empty_count += 1
        except Exception as exc:
            failed.append({"code": code, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if cfg.pause > 0 and index < len(stocks):
            time.sleep(float(cfg.pause))
    summary_rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*), COUNT(DISTINCT code), MAX(reason_date)
            FROM kpl_stock_limit_up_reasons
            WHERE trade_date={sql_string(cfg.trade_date)};
            """,
            batch=True,
            raw=True,
        )
    )
    summary = summary_rows[0] if summary_rows else ["0", "0", ""]
    return {
        "ok": not failed,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "stock_count": len(stocks),
        "ok_count": ok_count,
        "empty_count": empty_count,
        "imported": imported,
        "stored_rows": int(float(summary[0] or 0)),
        "stored_stocks": int(float(summary[1] or 0)),
        "latest_reason_date": str(summary[2] or ""),
        "failed_count": len(failed),
        "failed": failed[:20],
        "source": KPL_LIMIT_UP_REASON_SOURCE,
    }
