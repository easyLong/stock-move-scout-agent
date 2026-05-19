from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, run_mysql, sql_int, sql_json, sql_number, sql_string


KPL_REPLAY_URL = "https://apphq.longhuvip.com/w1/api/index.php"
KPL_REPLAY_HIS_URL = "https://apphis.longhuvip.com/w1/api/index.php"
KPL_REPLAY_SOURCE = "kpl_replay_limit_reason"


@dataclass(frozen=True)
class KplReplayLimitThemeConfig:
    trade_date: str
    timeout: int = 8
    pause: float = 0.05
    user_agent: str = "lhb/5.2.9 (com.kaipanla.www; build:0; iOS 15.1.0) Alamofire/5.2.9"


def ensure_kpl_replay_limit_theme_tables(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_replay_limit_theme_groups (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          theme_code VARCHAR(32) NOT NULL,
          theme_name VARCHAR(128) NOT NULL DEFAULT '',
          theme_rank INT NOT NULL DEFAULT 0,
          limit_up_count INT NOT NULL DEFAULT 0,
          sample_stock_count INT NOT NULL DEFAULT 0,
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_replay_limit_reason',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, theme_code),
          KEY idx_kpl_replay_group_rank (trade_date, theme_rank),
          KEY idx_kpl_replay_group_name (trade_date, theme_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL ReplayLa limit-up reason groups from FuPanLa.GetYTFP_BKHX.';

        CREATE TABLE IF NOT EXISTS kpl_replay_limit_theme_stocks (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          theme_code VARCHAR(32) NOT NULL,
          theme_name VARCHAR(128) NOT NULL DEFAULT '',
          theme_rank INT NOT NULL DEFAULT 0,
          code CHAR(6) NOT NULL,
          stock_name VARCHAR(64) NOT NULL DEFAULT '',
          limit_time DATETIME NULL,
          limit_amount DECIMAL(24,4) NULL,
          pct_change DECIMAL(12,4) NULL,
          tags VARCHAR(255) NOT NULL DEFAULT '',
          streak_text VARCHAR(64) NOT NULL DEFAULT '',
          replay_td_type VARCHAR(16) NOT NULL DEFAULT '',
          replay_sample_rank INT NOT NULL DEFAULT 0,
          reason_date DATE NULL,
          reason_text TEXT NULL,
          concept_explain TEXT NULL,
          boom_theme TEXT NULL,
          role_label VARCHAR(64) NOT NULL DEFAULT '',
          reason_zscode JSON NULL,
          reason_pzscode VARCHAR(32) NOT NULL DEFAULT '',
          primary_source VARCHAR(64) NOT NULL DEFAULT 'kpl_replay_limit_reason',
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_replay_limit_reason',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, theme_code, code),
          KEY idx_kpl_replay_stock_day_code (trade_date, code),
          KEY idx_kpl_replay_stock_theme (trade_date, theme_name, theme_rank),
          KEY idx_kpl_replay_stock_reason_day (reason_date, code)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL ReplayLa one-primary-theme stock rows for limit-up reason display.';
        """,
    )


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
        "Accept-Language": "zh-Hans-CN;q=1.0, bo-CN;q=0.9, ar-CN;q=0.8",
        "Accept-Encoding": "gzip;q=1.0, compress;q=0.5",
    }


def _is_today(day: str) -> bool:
    return str(day or "").strip() == date.today().isoformat()


def fetch_replay_groups(session: requests.Session, cfg: KplReplayLimitThemeConfig) -> dict[str, Any]:
    data = {
        "PhoneOSNew": "2",
        "VerSion": "5.23.0.4",
        "a": "GetYTFP_BKHX",
        "apiv": "w38",
        "c": "FuPanLa",
        "DeviceID": str(uuid.uuid4()),
    }
    if not _is_today(cfg.trade_date):
        data["Date"] = cfg.trade_date
        response = session.post(
            KPL_REPLAY_HIS_URL,
            headers=_headers(cfg.user_agent),
            data=data,
            timeout=max(1, int(cfg.timeout)),
        )
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("errcode", "0")) == "0" and isinstance(payload.get("List"), list):
            return payload
    response = session.post(
        KPL_REPLAY_URL,
        headers=_headers(cfg.user_agent),
        data={k: v for k, v in data.items() if k != "Date"},
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def fetch_daily_limit_performance(session: requests.Session, cfg: KplReplayLimitThemeConfig) -> dict[str, Any]:
    data = {
        "Order": "0",
        "st": "500",
        "a": "DailyLimitPerformance",
        "PhoneOSNew": "2",
        "DeviceID": str(uuid.uuid4()),
        "VerSion": "5.23.0.4",
        "Index": "0",
        "PidType": "1",
        "apiv": "w38",
        "Type": "0",
    }
    if _is_today(cfg.trade_date):
        url = KPL_REPLAY_URL
        data["c"] = "HomeDingPan"
    else:
        url = KPL_REPLAY_HIS_URL
        data["c"] = "HisHomeDingPan"
        data["Day"] = cfg.trade_date
    response = session.post(
        url,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def fetch_stock_limit_reason(session: requests.Session, code: str, cfg: KplReplayLimitThemeConfig) -> dict[str, Any]:
    data = {
        "PhoneOSNew": "2",
        "StockID": code,
        "VerSion": "5.23.0.4",
        "a": "GetKLineZhangTing",
        "apiv": "w38",
        "c": "StockLineData",
        "DeviceID": str(uuid.uuid4()),
    }
    response = session.post(
        KPL_REPLAY_URL,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def _normalize_daily_rows(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    info = payload.get("info")
    rows = info[0] if isinstance(info, list) and info and isinstance(info[0], list) else []
    normalized: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, list) or len(item) < 21:
            continue
        code = str(item[0] or "").strip()
        if not code:
            continue
        normalized[code] = {
            "code": code,
            "stock_name": str(item[1] or "").strip(),
            "limit_timestamp": item[4] if len(item) > 4 else None,
            "main_theme_name": str(item[5] or "").strip() if len(item) > 5 else "",
            "main_theme_code": str(item[19] or "").strip() if len(item) > 19 else "",
            "limit_amount": item[7] if len(item) > 7 else None,
            "tags": str(item[12] or "").strip() if len(item) > 12 else "",
            "streak_text": str(item[18] or "").strip() if len(item) > 18 else "",
            "theme_count": item[20] if len(item) > 20 else None,
            "pct_change": item[22] if len(item) > 22 else None,
            "raw_json": item,
        }
    return normalized


def _limit_time_from_timestamp(value: Any) -> str:
    try:
        number = int(float(value))
    except Exception:
        return "NULL"
    if number <= 0:
        return "NULL"
    return sql_string(datetime.fromtimestamp(number).strftime("%Y-%m-%d %H:%M:%S"))


def _pick_reason(payload: dict[str, Any], trade_date: str) -> dict[str, Any]:
    items = payload.get("List")
    if not isinstance(items, list):
        return {}
    fallback: dict[str, Any] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        reason_date = str(item.get("Date") or "").strip()
        if reason_date == trade_date:
            return item
        if not fallback and reason_date <= trade_date:
            fallback = item
    return fallback


def _group_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("List")
    if not isinstance(groups, list):
        return []
    rows: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            continue
        theme_code = str(group.get("ZSCode") or "").strip()
        theme_name = str(group.get("ZSName") or "").strip()
        if not theme_code or not theme_name:
            continue
        sample_stocks: list[dict[str, Any]] = []
        for td in group.get("TD") if isinstance(group.get("TD"), list) else []:
            if not isinstance(td, dict):
                continue
            for rank, stock in enumerate(td.get("Stock") if isinstance(td.get("Stock"), list) else [], start=1):
                if not isinstance(stock, dict):
                    continue
                code = str(stock.get("StockID") or "").strip()
                if not code:
                    continue
                sample_stocks.append(
                    {
                        "code": code,
                        "stock_name": str(stock.get("StockName") or "").strip(),
                        "tips": str(stock.get("Tips") or "").strip(),
                        "td_type": str(td.get("TDType") or "").strip(),
                        "sample_rank": rank,
                        "raw_json": stock,
                    }
                )
        rows.append(
            {
                "theme_rank": index,
                "theme_code": theme_code,
                "theme_name": theme_name,
                "limit_up_count": int(float(group.get("Count") or 0)),
                "sample_stocks": sample_stocks,
                "raw_json": group,
            }
        )
    return rows


def _write_replay_rows(
    config: MySqlConfig,
    *,
    trade_date: str,
    captured_at: str,
    groups: list[dict[str, Any]],
    daily_rows: dict[str, dict[str, Any]],
    reason_payloads: dict[str, dict[str, Any]],
) -> dict[str, int]:
    statements = [
        f"DELETE FROM kpl_replay_limit_theme_stocks WHERE trade_date={sql_string(trade_date)};",
        f"DELETE FROM kpl_replay_limit_theme_groups WHERE trade_date={sql_string(trade_date)};",
    ]
    stock_count = 0
    for group in groups:
        theme_code = str(group.get("theme_code") or "")
        theme_name = str(group.get("theme_name") or "")
        sample_by_code = {str(s.get("code") or ""): s for s in group.get("sample_stocks") or [] if s.get("code")}
        daily_codes = [
            code
            for code, row in daily_rows.items()
            if str(row.get("main_theme_code") or "") == theme_code or str(row.get("main_theme_name") or "") == theme_name
        ]
        ordered_codes = list(dict.fromkeys(daily_codes + list(sample_by_code.keys())))
        statements.append(
            f"""
            INSERT INTO kpl_replay_limit_theme_groups(
              trade_date, captured_at, theme_code, theme_name, theme_rank, limit_up_count,
              sample_stock_count, source, raw_json
            ) VALUES (
              {sql_string(trade_date)}, {sql_string(captured_at)}, {sql_string(theme_code)},
              {sql_string(theme_name)}, {sql_int(group.get("theme_rank"))},
              {sql_int(group.get("limit_up_count"))}, {sql_int(len(sample_by_code))},
              {sql_string(KPL_REPLAY_SOURCE)}, {sql_json(group.get("raw_json"))}
            )
            ON DUPLICATE KEY UPDATE
              captured_at=VALUES(captured_at),
              theme_name=VALUES(theme_name),
              theme_rank=VALUES(theme_rank),
              limit_up_count=VALUES(limit_up_count),
              sample_stock_count=VALUES(sample_stock_count),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
        for code in ordered_codes:
            daily = daily_rows.get(code, {})
            sample = sample_by_code.get(code, {})
            reason_item = _pick_reason(reason_payloads.get(code, {}), trade_date)
            stock_name = str(daily.get("stock_name") or sample.get("stock_name") or "").strip()
            raw_json = {
                "daily": daily.get("raw_json"),
                "sample": sample.get("raw_json"),
                "reason": reason_item,
            }
            statements.append(
                f"""
                INSERT INTO kpl_replay_limit_theme_stocks(
                  trade_date, captured_at, theme_code, theme_name, theme_rank, code, stock_name,
                  limit_time, limit_amount, pct_change, tags, streak_text, replay_td_type, replay_sample_rank,
                  reason_date, reason_text, concept_explain, boom_theme, role_label,
                  reason_zscode, reason_pzscode, primary_source, source, raw_json
                ) VALUES (
                  {sql_string(trade_date)}, {sql_string(captured_at)}, {sql_string(theme_code)},
                  {sql_string(theme_name)}, {sql_int(group.get("theme_rank"))},
                  {sql_string(code)}, {sql_string(stock_name)},
                  {_limit_time_from_timestamp(daily.get("limit_timestamp"))},
                  {sql_number(daily.get("limit_amount"))},
                  {sql_number(daily.get("pct_change"))},
                  {sql_string(daily.get("tags") or "")},
                  {sql_string(daily.get("streak_text") or sample.get("tips") or "")},
                  {sql_string(sample.get("td_type") or "")},
                  {sql_int(sample.get("sample_rank"))},
                  {sql_string(reason_item.get("Date") or trade_date) if reason_item else "NULL"},
                  {sql_string(reason_item.get("Reason") or "")},
                  {sql_string(reason_item.get("GNSM") or "")},
                  {sql_string(reason_item.get("Boom_ZS") or "")},
                  {sql_string(reason_item.get("SCLT") or "")},
                  {sql_json(reason_item.get("ZSCode") if isinstance(reason_item.get("ZSCode"), list) else [])},
                  {sql_string(reason_item.get("PZSCode") or "")},
                  {sql_string(KPL_REPLAY_SOURCE)}, {sql_string(KPL_REPLAY_SOURCE)}, {sql_json(raw_json)}
                )
                ON DUPLICATE KEY UPDATE
                  captured_at=VALUES(captured_at),
                  theme_name=VALUES(theme_name),
                  theme_rank=VALUES(theme_rank),
                  stock_name=VALUES(stock_name),
                  limit_time=VALUES(limit_time),
                  limit_amount=VALUES(limit_amount),
                  pct_change=VALUES(pct_change),
                  tags=VALUES(tags),
                  streak_text=VALUES(streak_text),
                  replay_td_type=VALUES(replay_td_type),
                  replay_sample_rank=VALUES(replay_sample_rank),
                  reason_date=VALUES(reason_date),
                  reason_text=VALUES(reason_text),
                  concept_explain=VALUES(concept_explain),
                  boom_theme=VALUES(boom_theme),
                  role_label=VALUES(role_label),
                  reason_zscode=VALUES(reason_zscode),
                  reason_pzscode=VALUES(reason_pzscode),
                  raw_json=VALUES(raw_json),
                  updated_at=CURRENT_TIMESTAMP(3);
                """
            )
            stock_count += 1
    run_mysql(config, "\n".join(statements))
    return {"group_count": len(groups), "stock_count": stock_count}


def collect_kpl_replay_limit_themes(config: MySqlConfig, cfg: KplReplayLimitThemeConfig) -> dict[str, Any]:
    ensure_kpl_replay_limit_theme_tables(config)
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    session = requests.Session()
    group_payload = fetch_replay_groups(session, cfg)
    if str(group_payload.get("errcode", "0")) != "0":
        raise RuntimeError(f"FuPanLa.GetYTFP_BKHX errcode={group_payload.get('errcode')} errmsg={group_payload.get('errmsg', '')}")
    groups = _group_rows(group_payload)
    daily_payload = fetch_daily_limit_performance(session, cfg)
    daily_rows = _normalize_daily_rows(daily_payload)
    codes = sorted(
        {
            code
            for group in groups
            for code in list(daily_rows.keys())
            if str(daily_rows[code].get("main_theme_code") or "") == str(group.get("theme_code") or "")
            or str(daily_rows[code].get("main_theme_name") or "") == str(group.get("theme_name") or "")
        }
        | {str(stock.get("code") or "") for group in groups for stock in group.get("sample_stocks") or [] if stock.get("code")}
    )
    reason_payloads: dict[str, dict[str, Any]] = {}
    failed: list[dict[str, str]] = []
    for index, code in enumerate(codes, start=1):
        try:
            payload = fetch_stock_limit_reason(session, code, cfg)
            if str(payload.get("errcode", "0")) != "0":
                raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
            reason_payloads[code] = payload
        except Exception as exc:
            failed.append({"code": code, "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        if cfg.pause > 0 and index < len(codes):
            time.sleep(float(cfg.pause))
    counts = _write_replay_rows(
        config,
        trade_date=cfg.trade_date,
        captured_at=captured_at,
        groups=groups,
        daily_rows=daily_rows,
        reason_payloads=reason_payloads,
    )
    return {
        "ok": not failed,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "group_count": counts["group_count"],
        "stock_count": counts["stock_count"],
        "reason_request_count": len(codes),
        "failed_count": len(failed),
        "failed": failed[:20],
        "source": KPL_REPLAY_SOURCE,
    }
