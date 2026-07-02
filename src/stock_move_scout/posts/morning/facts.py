from __future__ import annotations

"""Fact readers used by the morning reference post workflow.

This module only reads and shapes factual inputs. It deliberately avoids prompt
text, strategy prose, review rules, and filesystem artifact writes.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from stock_move_scout.db import mysql_rows, run_mysql, sql_string


__all__ = [
    "acceleration_state",
    "compact_text",
    "format_pct",
    "json_list",
    "pct_ratio",
    "read_fallback",
    "read_json_payload",
    "read_market_acceleration_model",
    "read_top3_concept_cache",
    "read_top3_concept_new_high",
    "to_float",
    "to_int",
    "top3_rows_from_cache",
]


def compact_text(value: Any, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def to_float(value: Any) -> float:
    try:
        return float(str(value or "0"))
    except Exception:
        return 0.0


def to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0")))
    except Exception:
        return 0


def pct_ratio(numerator: Any, denominator: Any) -> float:
    den = to_float(denominator)
    if den <= 0:
        return 0.0
    return round(to_float(numerator) / den, 4)


def format_pct(value: float) -> str:
    return f"{round(value * 100, 2):.2f}%"


def acceleration_state(momentum: float, speed: float, acceleration: float) -> tuple[str, str]:
    if momentum >= 0.5:
        if speed > 0 and acceleration > 0:
            return "向上加速度增大", "右侧买"
        return "向上加速度减小", "左侧卖/降低激进"
    if speed > 0 or acceleration > 0:
        return "向下加速度减小", "左侧买/观察支撑"
    return "向下加速度增大", "右侧卖/管住手"


def read_market_acceleration_model(config: Any | None, trade_day: date) -> dict[str, Any]:
    """Build the user's stock-market acceleration model result for the previous valid trade day."""
    if config is None:
        return {"source": "missing:mysql_disabled", "ok": False}
    sql = f"""
    WITH latest AS (
      SELECT
        m.*,
        ROW_NUMBER() OVER (PARTITION BY trade_date ORDER BY captured_at DESC, id DESC) rn
      FROM market_width_snapshots m
      WHERE trade_date < {sql_string(trade_day.isoformat())}
        AND ((TIME(captured_at) >= '09:30:00' AND TIME(captured_at) <= '11:30:00')
          OR (TIME(captured_at) >= '13:00:00' AND TIME(captured_at) <= '15:00:00'))
    )
    SELECT
      DATE_FORMAT(trade_date, '%Y-%m-%d'),
      DATE_FORMAT(captured_at, '%Y-%m-%d %H:%i:%s'),
      total_count, up_count, down_count, flat_count,
      up5_count, down5_count, limit_up_count, limit_down_count,
      amount_top50_count, amount_top50_up_count, amount_top50_down_count,
      research_pool_count, research_pool_up_count, research_pool_down_count
    FROM latest
    WHERE rn = 1
    ORDER BY trade_date DESC
    LIMIT 8;
    """
    try:
        raw_rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception as exc:
        return {"source": "market_width_snapshots", "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    rows: list[dict[str, Any]] = []
    for row in reversed(raw_rows):
        if len(row) < 16:
            continue
        total_count = to_int(row[2])
        top50_count = to_int(row[10])
        pool_count = to_int(row[13])
        market_ratio = pct_ratio(row[3], total_count)
        top50_ratio = pct_ratio(row[11], top50_count)
        pool_ratio = pct_ratio(row[14], pool_count)
        rows.append(
            {
                "trade_date": str(row[0]),
                "captured_at": str(row[1]),
                "market_ratio": market_ratio,
                "top50_ratio": top50_ratio,
                "research_pool_ratio": pool_ratio,
                "style_gap": round(top50_ratio - market_ratio, 4),
                "total_count": total_count,
                "up_count": to_int(row[3]),
                "down_count": to_int(row[4]),
                "flat_count": to_int(row[5]),
                "up5_count": to_int(row[6]),
                "down5_count": to_int(row[7]),
                "limit_up_count": to_int(row[8]),
                "limit_down_count": to_int(row[9]),
                "amount_top50_count": top50_count,
                "amount_top50_up_count": to_int(row[11]),
                "amount_top50_down_count": to_int(row[12]),
                "research_pool_count": pool_count,
                "research_pool_up_count": to_int(row[14]),
                "research_pool_down_count": to_int(row[15]),
            }
        )
    if not rows:
        return {"source": "market_width_snapshots", "ok": False, "error": "no valid rows"}
    for idx, item in enumerate(rows):
        if idx == 0:
            item["market_speed"] = None
            item["top50_speed"] = None
            item["research_pool_speed"] = None
            item["market_acceleration"] = None
            item["top50_acceleration"] = None
            item["research_pool_acceleration"] = None
            continue
        prev = rows[idx - 1]
        item["market_speed"] = round(item["market_ratio"] - prev["market_ratio"], 4)
        item["top50_speed"] = round(item["top50_ratio"] - prev["top50_ratio"], 4)
        item["research_pool_speed"] = round(item["research_pool_ratio"] - prev["research_pool_ratio"], 4)
        if idx >= 2:
            prev_speed = rows[idx - 1]
            item["market_acceleration"] = round(item["market_speed"] - to_float(prev_speed.get("market_speed")), 4)
            item["top50_acceleration"] = round(item["top50_speed"] - to_float(prev_speed.get("top50_speed")), 4)
            item["research_pool_acceleration"] = round(
                item["research_pool_speed"] - to_float(prev_speed.get("research_pool_speed")),
                4,
            )
        else:
            item["market_acceleration"] = None
            item["top50_acceleration"] = None
            item["research_pool_acceleration"] = None
    recent5 = rows[-5:]
    weight_days = sum(1 for item in recent5 if item["style_gap"] > 0.05)
    emotion_days = sum(1 for item in recent5 if item["style_gap"] < -0.05)
    if weight_days >= 4:
        leader = "权重核心"
        follower = "全市场情绪"
        leader_key = "top50"
    elif emotion_days >= 4:
        leader = "全市场情绪"
        follower = "权重核心"
        leader_key = "market"
    else:
        leader = "混合轮动"
        follower = ""
        leader_key = "top50" if recent5[-1]["style_gap"] >= 0 else "market"
    latest = rows[-1]
    if leader_key == "top50":
        momentum = latest["top50_ratio"]
        speed = to_float(latest.get("top50_speed"))
        acceleration = to_float(latest.get("top50_acceleration"))
        leader_ratios = [item["top50_ratio"] for item in recent5]
    else:
        momentum = latest["market_ratio"]
        speed = to_float(latest.get("market_speed"))
        acceleration = to_float(latest.get("market_acceleration"))
        leader_ratios = [item["market_ratio"] for item in recent5]
    acceleration_label, action_state = acceleration_state(momentum, speed, acceleration)
    if leader == "权重核心" and action_state.startswith("左侧卖"):
        conclusion = "权重核心仍是龙头，但向上加速度已经减小，追高性价比下降"
    elif leader == "权重核心" and action_state.startswith("右侧买"):
        conclusion = "权重核心继续主导，向上加速度仍在增大"
    elif leader == "全市场情绪":
        conclusion = "全市场情绪成为龙头，重点看情绪个股能否持续扩散"
    else:
        conclusion = "市场处在混合轮动，先看谁在分歧后能重新承接"
    return {
        "agent": "stock-market-acceleration-model",
        "source": "market_width_snapshots",
        "ok": True,
        "trade_date": latest["trade_date"],
        "current": latest,
        "recent5": recent5,
        "style_strength": {
            "market_ratio": latest["market_ratio"],
            "top50_ratio": latest["top50_ratio"],
            "research_pool_ratio": latest["research_pool_ratio"],
            "style_gap": latest["style_gap"],
            "style_gap_label": format_pct(latest["style_gap"]),
        },
        "five_day_leader": {
            "weight_stronger_days": weight_days,
            "emotion_stronger_days": emotion_days,
            "leader": leader,
            "follower": follower,
            "leader_key": leader_key,
        },
        "leader_acceleration": {
            "ratios": leader_ratios,
            "momentum": momentum,
            "speed": speed,
            "acceleration": acceleration,
            "state": acceleration_label,
            "action_state": action_state,
        },
        "conclusion": conclusion,
    }


def json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    text = str(value or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


def read_json_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def read_top3_concept_cache(root: Path, signal_date: str) -> dict[str, Any]:
    candidates = [
        root / "runs" / "data_tasks" / f"stock_top3_concept_new_high_{signal_date}.json",
        root / "runs" / "data_tasks" / f"top3_concept_new_high_{signal_date}.json",
        root / "runs" / "data_tasks" / "stock_top3_concept_new_high_latest.json",
    ]
    for path in candidates:
        payload = read_json_payload(path)
        if not payload:
            continue
        start_date = str(payload.get("start_date") or payload.get("trade_date") or "")
        end_date = str(payload.get("end_date") or payload.get("trade_date") or "")
        if signal_date in {start_date, end_date}:
            payload["cache_path"] = str(path)
            return payload
    return {}


def top3_rows_from_cache(cache: dict[str, Any]) -> list[dict[str, Any]]:
    rows = cache.get("rows") if isinstance(cache.get("rows"), list) else []
    compact_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        compact_rows.append(
            {
                "sub_name": row.get("sub_name"),
                "sub_pick": row.get("sub_pick"),
                "stock_name": row.get("stock_name"),
                "code": row.get("code"),
                "xueqiu_url": row.get("xueqiu_url"),
                "support": row.get("strong_line_support"),
                "industrial_chain_position": row.get("industrial_chain_position"),
                "pool_rank": row.get("pool_rank"),
                "pool_count": row.get("pool_count"),
                "section_rank": row.get("section_rank"),
                "core_reason": compact_text(row.get("core_reason"), 220),
                "support_basis": compact_text(row.get("support_basis"), 260),
            }
        )
    return compact_rows


def read_top3_concept_new_high(config: Any | None, root: Path, trade_day: date) -> dict[str, Any]:
    """Read the Top3 concept/new-high skill result or a DB summary for the previous valid trade day."""
    if config is None:
        return {"agent": "stock-top3-concept-new-high", "ok": False, "source": "missing:mysql_disabled"}
    day_sql = f"""
    SELECT DATE_FORMAT(MAX(trade_date), '%Y-%m-%d')
    FROM kpl_plate_featured_details
    WHERE trade_date < {sql_string(trade_day.isoformat())};
    """
    try:
        day_rows = mysql_rows(run_mysql(config, day_sql, batch=True, raw=True))
    except Exception as exc:
        return {"agent": "stock-top3-concept-new-high", "ok": False, "source": "kpl_plate_featured_details", "error": f"{type(exc).__name__}: {exc}"}
    signal_date = str(day_rows[0][0] or "").strip() if day_rows and day_rows[0] else ""
    if not signal_date:
        return {"agent": "stock-top3-concept-new-high", "ok": False, "source": "kpl_plate_featured_details", "error": "no signal date"}
    cache = read_top3_concept_cache(root, signal_date)
    if cache:
        return {
            "agent": "stock-top3-concept-new-high",
            "ok": True,
            "source": "cache",
            "cache_path": cache.get("cache_path"),
            "trade_date": signal_date,
            "llm_review": cache.get("llm_review"),
            "support_gate": cache.get("support_gate"),
            "top3_sub_plates": cache.get("top3_sub_plates") if isinstance(cache.get("top3_sub_plates"), list) else [],
            "rows": top3_rows_from_cache(cache),
            "strong_support_clean": cache.get("strong_support_clean") if isinstance(cache.get("strong_support_clean"), list) else [],
        }
    sql = f"""
    WITH picked AS (
      SELECT captured_at
      FROM kpl_plate_featured_details
      WHERE trade_date = {sql_string(signal_date)}
      GROUP BY captured_at
      ORDER BY COUNT(*) DESC, captured_at DESC
      LIMIT 1
    )
    SELECT
      DATE_FORMAT(d.trade_date, '%Y-%m-%d'),
      DATE_FORMAT(d.captured_at, '%Y-%m-%d %H:%i:%s'),
      d.plate_code,
      d.plate_name,
      d.reason_text,
      d.sub_plates,
      d.top_research_pool_stocks_by_sub_plate
    FROM kpl_plate_featured_details d
    JOIN picked p ON p.captured_at = d.captured_at
    WHERE d.trade_date = {sql_string(signal_date)}
    ORDER BY d.row_rank ASC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception as exc:
        return {"agent": "stock-top3-concept-new-high", "ok": False, "source": "kpl_plate_featured_details", "trade_date": signal_date, "error": f"{type(exc).__name__}: {exc}"}
    sub_map: dict[str, dict[str, Any]] = {}
    stock_groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if len(row) < 7:
            continue
        parent_name = str(row[3] or "").strip()
        reason_text = str(row[4] or "").strip()
        for item in json_list(row[5]):
            if not isinstance(item, dict):
                continue
            sub_code = str(item.get("plate_code") or "").strip()
            sub_name = str(item.get("plate_name") or "").strip()
            if not sub_code or not sub_name:
                continue
            strength = to_float(item.get("strength"))
            old = sub_map.get(sub_code)
            if old is None or strength > to_float(old.get("sub_strength")):
                sub_map[sub_code] = {
                    "trade_date": signal_date,
                    "sub_code": sub_code,
                    "sub_name": sub_name,
                    "sub_strength": strength,
                    "parent_plate_name": parent_name,
                    "explosion_reason": reason_text,
                }
        for group in json_list(row[6]):
            if not isinstance(group, dict):
                continue
            sub_code = str(group.get("sub_plate_code") or "").strip()
            if not sub_code:
                continue
            stocks = group.get("stocks") if isinstance(group.get("stocks"), list) else []
            stock_groups[sub_code] = stocks
    top3 = sorted(sub_map.values(), key=lambda item: to_float(item.get("sub_strength")), reverse=True)[:3]
    compact_rows: list[dict[str, Any]] = []
    for rank, sub in enumerate(top3, start=1):
        sub["sub_rank"] = rank
        stocks = stock_groups.get(str(sub.get("sub_code"))) or []
        for pick, stock in enumerate(stocks[:3], start=1):
            if not isinstance(stock, dict):
                continue
            compact_rows.append(
                {
                    "sub_name": sub.get("sub_name"),
                    "sub_pick": pick,
                    "stock_name": stock.get("stock_name") or stock.get("leader_name"),
                    "code": stock.get("code") or stock.get("leader_code"),
                    "support": "未经过LLM复核",
                    "pool_rank": stock.get("pool_rank"),
                    "section_rank": stock.get("section_rank"),
                    "core_reason": compact_text(stock.get("kpl_limit_reason"), 220),
                    "support_basis": "来自KPL板块详情的研究池Top股票摘要，未经过stock-top3-concept-new-high的LLM支撑度复核。",
                }
            )
    return {
        "agent": "stock-top3-concept-new-high",
        "ok": True,
        "source": "db:kpl_plate_featured_details",
        "trade_date": signal_date,
        "llm_review": {"ok": False, "error": "no cache; using KPL detail summary"},
        "support_gate": {},
        "top3_sub_plates": top3,
        "rows": compact_rows,
    }


def read_fallback(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    theme_payload = read_json_payload(root / "runs" / "data_tasks" / "daily_market_themes.json")
    news_payload = read_json_payload(root / "runs" / "data_tasks" / "morning_market_news.json")
    themes = theme_payload.get("rows") if isinstance(theme_payload.get("rows"), list) else []
    news = news_payload.get("rows") if isinstance(news_payload.get("rows"), list) else []
    return themes, news
