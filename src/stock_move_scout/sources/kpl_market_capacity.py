from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, run_mysql, sql_json, sql_number, sql_string


KPL_MARKET_CAPACITY_URL = "https://apphq.longhuvip.com/w1/api/index.php"
KPL_MARKET_CAPACITY_SOURCE = "kpl_market_capacity"


@dataclass(frozen=True)
class KplMarketCapacityConfig:
    trade_date: str = date.today().isoformat()
    timeout: int = 8
    market_type: int = 0
    version: str = "5.11.0.1"
    api_version: str = "w33"
    user_agent: str = "lhb/5.11.1 (com.kaipanla.www; build:0; iOS 14.6.0) Alamofire/5.11.1"


def ensure_kpl_market_capacity_tables(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_market_capacity_snapshots (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          market_time DATETIME NULL,
          latest_amount_wan DECIMAL(24,4) NULL,
          yesterday_same_time_amount_wan DECIMAL(24,4) NULL,
          yesterday_total_amount_wan DECIMAL(24,4) NULL,
          three_day_avg_total_amount_wan DECIMAL(24,4) NULL,
          forecast_amount_yuan DECIMAL(24,4) NULL,
          forecast_amount_yi DECIMAL(18,4) NULL,
          forecast_change_pct DECIMAL(12,4) NULL,
          forecast_delta_yi DECIMAL(18,4) NULL,
          forecast_text VARCHAR(255) NOT NULL DEFAULT '',
          color VARCHAR(16) NOT NULL DEFAULT '',
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_market_capacity',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, captured_at),
          KEY idx_kpl_market_capacity_latest (trade_date, captured_at),
          KEY idx_kpl_market_capacity_time (market_time)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL market capacity forecast snapshots from HomeDingPan.MarketCapacity.';

        CREATE TABLE IF NOT EXISTS kpl_market_capacity_trends (
          trade_date DATE NOT NULL,
          trend_time CHAR(5) NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          latest_amount_wan DECIMAL(24,4) NULL,
          yesterday_same_time_amount_wan DECIMAL(24,4) NULL,
          three_day_same_time_amount_wan DECIMAL(24,4) NULL,
          forecast_change_pct DECIMAL(12,4) NULL,
          forecast_amount_yi DECIMAL(18,4) NULL,
          forecast_delta_yi DECIMAL(18,4) NULL,
          forecast_text VARCHAR(255) NOT NULL DEFAULT '',
          color VARCHAR(16) NOT NULL DEFAULT '',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, trend_time),
          KEY idx_kpl_market_capacity_trend_latest (trade_date, captured_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='Minute-level KPL market capacity forecast trend points.';
        """,
    )


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "Host": "apphq.longhuvip.com",
        "Accept-Language": "zh-Hans-CN;q=1.0, en-CN;q=0.9",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "User-Agent": user_agent,
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
    }


def fetch_market_capacity(session: requests.Session, cfg: KplMarketCapacityConfig) -> dict[str, Any]:
    data = {
        "PhoneOSNew": "2",
        "Type": str(int(cfg.market_type)),
        "VerSion": cfg.version,
        "a": "MarketCapacity",
        "apiv": cfg.api_version,
        "c": "HomeDingPan",
        "DeviceID": str(uuid.uuid4()),
    }
    response = session.post(
        KPL_MARKET_CAPACITY_URL,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def _value(row: list[Any], index: int) -> Any:
    return row[index] if len(row) > index else None


def _pct_from_forecast_text(text: str) -> float | None:
    match = re.search(r"\((-?\d+(?:\.\d+)?)%", text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _delta_yi_from_forecast_text(text: str) -> float | None:
    match = re.search(r"(?:放量|缩量)(\d+(?:\.\d+)?)亿", text or "")
    if not match:
        return None
    try:
        amount = float(match.group(1))
    except Exception:
        return None
    return -amount if "缩量" in (text or "") else amount


def _forecast_yi_from_text(text: str) -> float | None:
    match = re.search(r"(\d+(?:\.\d+)?)亿", text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except Exception:
        return None


def _market_datetime(trade_date: str, value: Any) -> str:
    try:
        timestamp = int(float(value))
    except Exception:
        return ""
    if timestamp <= 0:
        return ""
    parsed = datetime.fromtimestamp(timestamp)
    if parsed.date().isoformat() != trade_date:
        return f"{trade_date} {parsed.strftime('%H:%M:%S')}"
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def normalize_market_capacity(payload: dict[str, Any], trade_date: str, captured_at: str) -> dict[str, Any]:
    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
    forecast_text = str(info.get("yclnstr") or "").strip()
    forecast_amount_yuan = info.get("ycln")
    forecast_amount_yi = None
    try:
        forecast_amount_yi = float(forecast_amount_yuan) / 100_000_000
    except Exception:
        forecast_amount_yi = _forecast_yi_from_text(forecast_text)
    trends: list[dict[str, Any]] = []
    for item in info.get("trends") if isinstance(info.get("trends"), list) else []:
        if not isinstance(item, list) or len(item) < 2:
            continue
        trend_text = str(_value(item, 5) or "").strip()
        trend_yi = _forecast_yi_from_text(trend_text)
        trends.append(
            {
                "trade_date": trade_date,
                "trend_time": str(_value(item, 0) or "").strip(),
                "captured_at": captured_at,
                "latest_amount_wan": _value(item, 1),
                "yesterday_same_time_amount_wan": _value(item, 2),
                "three_day_same_time_amount_wan": _value(item, 3),
                "forecast_change_pct": _value(item, 4),
                "forecast_amount_yi": trend_yi,
                "forecast_delta_yi": _delta_yi_from_forecast_text(trend_text),
                "forecast_text": trend_text,
                "color": str(_value(item, 6) or "").strip(),
                "raw_json": item,
            }
        )
    snapshot = {
        "trade_date": trade_date,
        "captured_at": captured_at,
        "market_time": _market_datetime(trade_date, info.get("time")),
        "latest_amount_wan": info.get("last"),
        "yesterday_same_time_amount_wan": info.get("s_zrcs"),
        "yesterday_total_amount_wan": info.get("s_zrtj"),
        "three_day_avg_total_amount_wan": info.get("s3_zrtj"),
        "forecast_amount_yuan": forecast_amount_yuan,
        "forecast_amount_yi": forecast_amount_yi,
        "forecast_change_pct": _pct_from_forecast_text(forecast_text),
        "forecast_delta_yi": _delta_yi_from_forecast_text(forecast_text),
        "forecast_text": forecast_text,
        "color": str(info.get("color") or "").strip(),
        "raw_json": {"info": info, "ttag": payload.get("ttag")},
    }
    return {"snapshot": snapshot, "trends": trends}


def save_market_capacity(
    config: MySqlConfig,
    *,
    snapshot: dict[str, Any],
    trends: list[dict[str, Any]],
) -> int:
    market_time_sql = sql_string(snapshot.get("market_time")) if snapshot.get("market_time") else "NULL"
    statements = [
        f"""
        INSERT INTO kpl_market_capacity_snapshots(
          trade_date, captured_at, market_time, latest_amount_wan,
          yesterday_same_time_amount_wan, yesterday_total_amount_wan,
          three_day_avg_total_amount_wan, forecast_amount_yuan,
          forecast_amount_yi, forecast_change_pct, forecast_delta_yi,
          forecast_text, color, source, raw_json
        ) VALUES (
          {sql_string(snapshot.get("trade_date"))}, {sql_string(snapshot.get("captured_at"))},
          {market_time_sql}, {sql_number(snapshot.get("latest_amount_wan"))},
          {sql_number(snapshot.get("yesterday_same_time_amount_wan"))},
          {sql_number(snapshot.get("yesterday_total_amount_wan"))},
          {sql_number(snapshot.get("three_day_avg_total_amount_wan"))},
          {sql_number(snapshot.get("forecast_amount_yuan"))},
          {sql_number(snapshot.get("forecast_amount_yi"))},
          {sql_number(snapshot.get("forecast_change_pct"))},
          {sql_number(snapshot.get("forecast_delta_yi"))},
          {sql_string(snapshot.get("forecast_text") or "")},
          {sql_string(snapshot.get("color") or "")},
          {sql_string(KPL_MARKET_CAPACITY_SOURCE)},
          {sql_json(snapshot.get("raw_json"))}
        )
        ON DUPLICATE KEY UPDATE
          market_time=VALUES(market_time),
          latest_amount_wan=VALUES(latest_amount_wan),
          yesterday_same_time_amount_wan=VALUES(yesterday_same_time_amount_wan),
          yesterday_total_amount_wan=VALUES(yesterday_total_amount_wan),
          three_day_avg_total_amount_wan=VALUES(three_day_avg_total_amount_wan),
          forecast_amount_yuan=VALUES(forecast_amount_yuan),
          forecast_amount_yi=VALUES(forecast_amount_yi),
          forecast_change_pct=VALUES(forecast_change_pct),
          forecast_delta_yi=VALUES(forecast_delta_yi),
          forecast_text=VALUES(forecast_text),
          color=VALUES(color),
          raw_json=VALUES(raw_json);
        """
    ]
    for row in trends:
        if not row.get("trend_time"):
            continue
        statements.append(
            f"""
            INSERT INTO kpl_market_capacity_trends(
              trade_date, trend_time, captured_at, latest_amount_wan,
              yesterday_same_time_amount_wan, three_day_same_time_amount_wan,
              forecast_change_pct, forecast_amount_yi, forecast_delta_yi,
              forecast_text, color, raw_json
            ) VALUES (
              {sql_string(row.get("trade_date"))},
              {sql_string(row.get("trend_time"))},
              {sql_string(row.get("captured_at"))},
              {sql_number(row.get("latest_amount_wan"))},
              {sql_number(row.get("yesterday_same_time_amount_wan"))},
              {sql_number(row.get("three_day_same_time_amount_wan"))},
              {sql_number(row.get("forecast_change_pct"))},
              {sql_number(row.get("forecast_amount_yi"))},
              {sql_number(row.get("forecast_delta_yi"))},
              {sql_string(row.get("forecast_text") or "")},
              {sql_string(row.get("color") or "")},
              {sql_json(row.get("raw_json"))}
            )
            ON DUPLICATE KEY UPDATE
              captured_at=VALUES(captured_at),
              latest_amount_wan=VALUES(latest_amount_wan),
              yesterday_same_time_amount_wan=VALUES(yesterday_same_time_amount_wan),
              three_day_same_time_amount_wan=VALUES(three_day_same_time_amount_wan),
              forecast_change_pct=VALUES(forecast_change_pct),
              forecast_amount_yi=VALUES(forecast_amount_yi),
              forecast_delta_yi=VALUES(forecast_delta_yi),
              forecast_text=VALUES(forecast_text),
              color=VALUES(color),
              raw_json=VALUES(raw_json),
              updated_at=CURRENT_TIMESTAMP(3);
            """
        )
    run_mysql(config, "\n".join(statements))
    return len(trends)


def collect_kpl_market_capacity(config: MySqlConfig, cfg: KplMarketCapacityConfig) -> dict[str, Any]:
    ensure_kpl_market_capacity_tables(config)
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    session = requests.Session()
    payload = fetch_market_capacity(session, cfg)
    if str(payload.get("errcode", "0")) != "0":
        raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
    normalized = normalize_market_capacity(payload, cfg.trade_date, captured_at)
    trend_count = save_market_capacity(
        config,
        snapshot=normalized["snapshot"],
        trends=normalized["trends"],
    )
    snapshot = normalized["snapshot"]
    return {
        "ok": True,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "market_time": snapshot.get("market_time"),
        "forecast_text": snapshot.get("forecast_text"),
        "forecast_amount_yi": snapshot.get("forecast_amount_yi"),
        "forecast_change_pct": snapshot.get("forecast_change_pct"),
        "forecast_delta_yi": snapshot.get("forecast_delta_yi"),
        "trend_count": trend_count,
        "source": KPL_MARKET_CAPACITY_SOURCE,
    }
