from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import requests

from stock_move_scout.db import MySqlConfig, run_mysql, sql_int, sql_json, sql_number, sql_string


KPL_PLATE_STRENGTH_URL = "https://apphq.longhuvip.com/w1/api/index.php"
KPL_PLATE_STRENGTH_HIS_URL = "https://apphis.longhuvip.com/w1/api/index.php"
KPL_PLATE_STRENGTH_SOURCE = "kpl_real_ranking_info"


@dataclass(frozen=True)
class KplPlateStrengthConfig:
    trade_date: str = date.today().isoformat()
    timeout: int = 8
    limit: int = 80
    order: int = 1
    plate_type: int = 1
    zs_type: int = 7
    version: str = "5.11.0.1"
    api_version: str = "w33"
    user_agent: str = "lhb/5.11.1 (com.kaipanla.www; build:0; iOS 14.6.0) Alamofire/5.11.1"


def ensure_kpl_plate_strength_table(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS kpl_plate_featured_strengths (
          trade_date DATE NOT NULL,
          captured_at DATETIME(3) NOT NULL,
          market_day DATE NULL,
          market_min VARCHAR(8) NOT NULL DEFAULT '',
          market_max VARCHAR(8) NOT NULL DEFAULT '',
          row_rank INT NOT NULL DEFAULT 0,
          plate_code VARCHAR(32) NOT NULL,
          plate_name VARCHAR(128) NOT NULL DEFAULT '',
          strength DECIMAL(18,4) NULL,
          change_pct DECIMAL(12,4) NULL,
          speed DECIMAL(12,4) NULL,
          amount DECIMAL(24,4) NULL,
          main_net_amount DECIMAL(24,4) NULL,
          big_order_net_amount DECIMAL(24,4) NULL,
          source VARCHAR(64) NOT NULL DEFAULT 'kpl_real_ranking_info',
          raw_json JSON NULL,
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, captured_at, plate_code),
          KEY idx_kpl_plate_strength_latest (trade_date, captured_at, row_rank),
          KEY idx_kpl_plate_strength_plate (plate_code, trade_date, captured_at),
          KEY idx_kpl_plate_strength_rank (trade_date, row_rank)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='KPL featured plate strength snapshots from ZhiShuRanking.RealRankingInfo.';
        """,
    )


def _headers(user_agent: str) -> dict[str, str]:
    return {
        "Accept-Language": "zh-Hans-CN;q=1.0, en-CN;q=0.9",
        "Accept": "*/*",
        "Connection": "keep-alive",
        "User-Agent": user_agent,
    }


def _is_today(day: str) -> bool:
    return str(day or "").strip() == date.today().isoformat()


def fetch_plate_strength(session: requests.Session, cfg: KplPlateStrengthConfig) -> dict[str, Any]:
    request_limit = max(1, int(cfg.limit))
    data = {
        "Index": "0",
        "Order": str(int(cfg.order)),
        "PhoneOSNew": "2",
        "Type": str(int(cfg.plate_type)),
        "VerSion": cfg.version,
        "ZSType": str(int(cfg.zs_type)),
        "a": "RealRankingInfo",
        "apiv": cfg.api_version,
        "c": "ZhiShuRanking",
        "st": str(request_limit),
        "DeviceID": str(uuid.uuid4()),
    }
    url = KPL_PLATE_STRENGTH_URL
    if not _is_today(cfg.trade_date):
        url = KPL_PLATE_STRENGTH_HIS_URL
        data["Date"] = str(cfg.trade_date)
        # The historical endpoint returns only 10 rows for st=80/100, while 70 is stable.
        data["st"] = str(min(request_limit, 70))
    response = session.post(
        url,
        headers=_headers(cfg.user_agent),
        data=data,
        timeout=max(1, int(cfg.timeout)),
    )
    response.raise_for_status()
    return response.json()


def _value(row: list[Any], index: int) -> Any:
    return row[index] if len(row) > index else None


def normalize_plate_strength(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_rows = payload.get("list")
    if not isinstance(raw_rows, list):
        return []
    rows: list[dict[str, Any]] = []
    for rank, item in enumerate(raw_rows, start=1):
        if not isinstance(item, list) or len(item) < 2:
            continue
        plate_code = str(_value(item, 0) or "").strip()
        plate_name = str(_value(item, 1) or "").strip()
        if not plate_code or not plate_name:
            continue
        rows.append(
            {
                "row_rank": rank,
                "plate_code": plate_code,
                "plate_name": plate_name,
                "strength": _value(item, 2),
                "change_pct": _value(item, 3),
                "speed": _value(item, 4),
                "amount": _value(item, 5),
                "main_net_amount": _value(item, 6),
                "big_order_net_amount": _value(item, 12),
                "raw_json": item,
            }
        )
    return rows


def save_plate_strength_snapshot(
    config: MySqlConfig,
    *,
    trade_date: str,
    captured_at: str,
    payload: dict[str, Any],
    rows: list[dict[str, Any]],
) -> int:
    market_days = payload.get("Day") if isinstance(payload.get("Day"), list) else []
    market_day = str(market_days[0] or "").strip() if market_days else str(trade_date)
    market_min = str(payload.get("Min") or "").strip()
    market_max = str(payload.get("Max") or "").strip()
    statements: list[str] = []
    for row in rows:
        raw_json = {
            "row": row.get("raw_json"),
            "count": payload.get("Count"),
            "time": payload.get("Time"),
            "title": payload.get("Title"),
            "ttag": payload.get("ttag"),
        }
        statements.append(
            f"""
            INSERT INTO kpl_plate_featured_strengths(
              trade_date, captured_at, market_day, market_min, market_max,
              row_rank, plate_code, plate_name, strength, change_pct, speed,
              amount, main_net_amount, big_order_net_amount, source, raw_json
            ) VALUES (
              {sql_string(trade_date)}, {sql_string(captured_at)},
              {sql_string(market_day) if market_day else "NULL"},
              {sql_string(market_min)}, {sql_string(market_max)},
              {sql_int(row.get("row_rank"))},
              {sql_string(row.get("plate_code") or "")},
              {sql_string(row.get("plate_name") or "")},
              {sql_number(row.get("strength"))},
              {sql_number(row.get("change_pct"))},
              {sql_number(row.get("speed"))},
              {sql_number(row.get("amount"))},
              {sql_number(row.get("main_net_amount"))},
              {sql_number(row.get("big_order_net_amount"))},
              {sql_string(KPL_PLATE_STRENGTH_SOURCE)},
              {sql_json(raw_json)}
            )
            ON DUPLICATE KEY UPDATE
              market_day=VALUES(market_day),
              market_min=VALUES(market_min),
              market_max=VALUES(market_max),
              row_rank=VALUES(row_rank),
              plate_name=VALUES(plate_name),
              strength=VALUES(strength),
              change_pct=VALUES(change_pct),
              speed=VALUES(speed),
              amount=VALUES(amount),
              main_net_amount=VALUES(main_net_amount),
              big_order_net_amount=VALUES(big_order_net_amount),
              source=VALUES(source),
              raw_json=VALUES(raw_json);
            """
        )
    if statements:
        run_mysql(config, "\n".join(statements))
    return len(rows)


def collect_kpl_plate_strength(config: MySqlConfig, cfg: KplPlateStrengthConfig) -> dict[str, Any]:
    ensure_kpl_plate_strength_table(config)
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    session = requests.Session()
    payload = fetch_plate_strength(session, cfg)
    if str(payload.get("errcode", "0")) != "0":
        raise RuntimeError(f"errcode={payload.get('errcode')} errmsg={payload.get('errmsg', '')}")
    rows = normalize_plate_strength(payload)
    imported = save_plate_strength_snapshot(
        config,
        trade_date=cfg.trade_date,
        captured_at=captured_at,
        payload=payload,
        rows=rows,
    )
    return {
        "ok": True,
        "trade_date": cfg.trade_date,
        "captured_at": captured_at,
        "market_day": (payload.get("Day") or [""])[0] if isinstance(payload.get("Day"), list) else "",
        "market_min": payload.get("Min"),
        "market_max": payload.get("Max"),
        "count": payload.get("Count"),
        "imported": imported,
        "source": KPL_PLATE_STRENGTH_SOURCE,
        "top": [
            {
                "rank": row["row_rank"],
                "plate_code": row["plate_code"],
                "plate_name": row["plate_name"],
                "strength": row["strength"],
                "speed": row["speed"],
            }
            for row in rows[:10]
        ],
    }
