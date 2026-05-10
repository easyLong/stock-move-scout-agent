from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    text = text.split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{rest:02d}秒" if rest else f"{minutes}分钟"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分钟" if minutes else f"{hours}小时"


def clean_anchor(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in {"未锚定", "異动", "异动"}:
        return ""
    if "未锚" in text or "鏈敋" in text:
        return ""
    return text


def build_activity_index(
    first_rows: list[list[Any]] | list[tuple[Any, ...]],
    all_rows: list[list[Any]] | list[tuple[Any, ...]],
    strong_codes: set[str],
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in first_rows:
        if len(row) < 7:
            continue
        anchor = clean_anchor(row[0])
        first_at = parse_dt(row[3])
        code = str(row[1] or "").strip()
        if not anchor or not code or not first_at:
            continue
        grouped.setdefault(anchor, []).append(
            {
                "anchor": anchor,
                "code": code,
                "stock_name": row[2] or "",
                "is_strong": code in strong_codes,
                "first_at": row[3],
                "first_at_dt": first_at,
                "first_rank_speed": _as_int(row[4]),
                "first_speed": _as_float(row[5]),
                "first_amount_delta_15s": _as_float(row[6]),
            }
        )

    all_grouped: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        if len(row) < 7:
            continue
        anchor = clean_anchor(row[0])
        scanned_at = parse_dt(row[3])
        code = str(row[1] or "").strip()
        if not anchor or not code or not scanned_at:
            continue
        all_grouped.setdefault(anchor, []).append(
            {
                "anchor": anchor,
                "code": code,
                "stock_name": row[2] or "",
                "is_strong": code in strong_codes,
                "scanned_at": row[3],
                "scanned_at_dt": scanned_at,
                "rank_speed": _as_int(row[4]),
                "speed": _as_float(row[5]),
                "amount_delta_15s": _as_float(row[6]),
            }
        )

    index: dict[str, dict[str, Any]] = {}
    quiet_restart_seconds = 60 * 60
    for anchor, hits in grouped.items():
        hits.sort(key=lambda item: (item["first_at_dt"], _as_int(item.get("first_rank_speed"), 9999)))
        wave_no = 0
        wave_order = 0
        wave_first_hit: dict[str, Any] | None = None
        wave_quiet_before_seconds = 0
        prev_dt: datetime | None = None
        for i, hit in enumerate(hits, start=1):
            hit["trigger_order"] = i
            item_dt = hit.get("first_at_dt")
            gap_seconds = int((item_dt - prev_dt).total_seconds()) if isinstance(item_dt, datetime) and isinstance(prev_dt, datetime) else 0
            if wave_first_hit is None or gap_seconds >= quiet_restart_seconds:
                wave_no += 1
                wave_order = 1
                wave_first_hit = hit
                wave_quiet_before_seconds = gap_seconds if prev_dt is not None else 0
            else:
                wave_order += 1
            hit["wave_no"] = wave_no
            hit["wave_trigger_order"] = wave_order
            hit["wave_first_hit"] = wave_first_hit
            hit["wave_quiet_before_seconds"] = wave_quiet_before_seconds
            if isinstance(item_dt, datetime):
                prev_dt = item_dt
        first_hit = hits[0] if hits else {}
        index[anchor] = {
            "hits": hits,
            "all_hits": all_grouped.get(anchor) or [],
            "times": [item["first_at_dt"] for item in hits],
            "by_code": {str(item["code"]): item for item in hits},
            "first_hit": first_hit,
        }
    return index


def activity_context_from_index(
    activity_index: dict[str, dict[str, Any]],
    event_time: Any,
    anchor: Any,
    code: Any,
) -> dict[str, Any]:
    anchor_name = clean_anchor(anchor)
    code_text = str(code or "").strip()
    event_dt = parse_dt(event_time)
    if not anchor_name or not code_text or not event_dt:
        return {}
    bucket = activity_index.get(anchor_name) or {}
    hit = (bucket.get("by_code") or {}).get(code_text)
    first_hit = bucket.get("first_hit") or {}
    wave_first_hit = (hit or {}).get("wave_first_hit") or first_hit
    hits: list[dict[str, Any]] = bucket.get("hits") or []
    all_hits: list[dict[str, Any]] = bucket.get("all_hits") or []
    times: list[datetime] = bucket.get("times") or []
    if not hit or not times:
        return {}
    first_dt = hit.get("first_at_dt")
    if not isinstance(first_dt, datetime) or first_dt > event_dt:
        return {}

    peers_total = bisect_right(times, event_dt)
    peers_before = bisect_left(times, first_dt)
    anchor_count_at_start = bisect_right(times, first_dt)
    before_10m_start = first_dt - timedelta(minutes=10)
    end_3m = min(first_dt + timedelta(minutes=3), event_dt)
    end_5m = min(first_dt + timedelta(minutes=5), event_dt)
    end_10m = min(first_dt + timedelta(minutes=10), event_dt)
    first_end = bisect_right(times, first_dt)
    n3 = max(0, bisect_right(times, end_3m) - first_end)
    n5 = max(0, bisect_right(times, end_5m) - first_end)
    n10 = max(0, bisect_right(times, end_10m) - first_end)
    trigger_order = _as_int(hit.get("trigger_order"))
    trigger_percent = trigger_order / peers_total if peers_total > 0 else 0.0
    first_anchor_dt = first_hit.get("first_at_dt")
    delay_seconds = int((first_dt - first_anchor_dt).total_seconds()) if isinstance(first_anchor_dt, datetime) else 0
    wave_first_dt = wave_first_hit.get("first_at_dt")
    wave_delay_seconds = int((first_dt - wave_first_dt).total_seconds()) if isinstance(wave_first_dt, datetime) else 0
    wave_trigger_order = _as_int(hit.get("wave_trigger_order"), trigger_order)
    wave_quiet_before_seconds = _as_int(hit.get("wave_quiet_before_seconds"), 0)

    def movers_until(end_dt: datetime, limit: int = 8) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in hits:
            item_dt = item.get("first_at_dt")
            if not isinstance(item_dt, datetime) or item_dt <= first_dt or item_dt > end_dt:
                continue
            out.append(
                {
                    "code": item.get("code") or "",
                    "stock_name": item.get("stock_name") or "",
                    "is_strong": bool(item.get("is_strong")),
                    "first_at": item.get("first_at") or "",
                    "first_rank_speed": _as_int(item.get("first_rank_speed")),
                    "first_speed": _as_float(item.get("first_speed")),
                }
            )
            if len(out) >= limit:
                break
        return out

    after_3m = movers_until(end_3m, 8)
    after_5m = movers_until(end_5m, 8)
    after_10m = movers_until(end_10m, 10)
    before_10m_hits = [
        item
        for item in hits
        if isinstance(item.get("first_at_dt"), datetime) and before_10m_start <= item["first_at_dt"] < first_dt
    ]

    time_ranked = [item for item in hits if isinstance(item.get("first_at_dt"), datetime) and item["first_at_dt"] <= event_dt]
    time_top: list[dict[str, Any]] = []
    for item in time_ranked[:3]:
        time_top.append(
            {
                "order": _as_int(item.get("trigger_order")),
                "wave_order": _as_int(item.get("wave_trigger_order")),
                "code": item.get("code") or "",
                "stock_name": item.get("stock_name") or "",
                "is_strong": bool(item.get("is_strong")),
                "first_at": item.get("first_at") or "",
                "is_current": str(item.get("code") or "") == code_text,
            }
        )
    if not any(str(item.get("code") or "") == code_text for item in time_top):
        time_top.append(
            {
                "order": trigger_order,
                "wave_order": wave_trigger_order,
                "code": hit.get("code") or "",
                "stock_name": hit.get("stock_name") or "",
                "is_strong": bool(hit.get("is_strong")),
                "first_at": hit.get("first_at") or "",
                "is_current": True,
            }
        )

    count_map: dict[str, dict[str, Any]] = {}
    for item in all_hits:
        item_dt = item.get("scanned_at_dt")
        item_code = str(item.get("code") or "")
        if not isinstance(item_dt, datetime) or item_dt > event_dt or not item_code:
            continue
        current = count_map.setdefault(
            item_code,
            {
                "code": item_code,
                "stock_name": item.get("stock_name") or "",
                "is_strong": bool(item.get("is_strong")),
                "count": 0,
                "latest_at": item.get("scanned_at") or "",
                "latest_dt": item_dt,
                "best_rank_speed": _as_int(item.get("rank_speed"), 9999),
            },
        )
        current["count"] = _as_int(current.get("count")) + 1
        if item_dt >= current.get("latest_dt"):
            current["latest_dt"] = item_dt
            current["latest_at"] = item.get("scanned_at") or ""
        current["best_rank_speed"] = min(_as_int(current.get("best_rank_speed"), 9999), _as_int(item.get("rank_speed"), 9999))
    quantity_ranked = sorted(
        count_map.values(),
        key=lambda item: (-_as_int(item.get("count")), _as_int(item.get("best_rank_speed"), 9999), str(item.get("latest_at") or "")),
    )
    quantity_top: list[dict[str, Any]] = []
    for idx, item in enumerate(quantity_ranked[:3], start=1):
        quantity_top.append(
            {
                "rank": idx,
                "code": item.get("code") or "",
                "stock_name": item.get("stock_name") or "",
                "is_strong": bool(item.get("is_strong")),
                "count": _as_int(item.get("count")),
                "latest_at": item.get("latest_at") or "",
                "is_current": str(item.get("code") or "") == code_text,
            }
        )
    if code_text in count_map and not any(str(item.get("code") or "") == code_text for item in quantity_top):
        item = count_map[code_text]
        rank = next((idx for idx, row in enumerate(quantity_ranked, start=1) if str(row.get("code") or "") == code_text), 0)
        quantity_top.append(
            {
                "rank": rank,
                "code": item.get("code") or "",
                "stock_name": item.get("stock_name") or "",
                "is_strong": bool(item.get("is_strong")),
                "count": _as_int(item.get("count")),
                "latest_at": item.get("latest_at") or "",
                "is_current": True,
            }
        )
    first_code_text = str(first_hit.get("code") or "")
    first_active_rank = next((idx for idx, row in enumerate(quantity_ranked, start=1) if str(row.get("code") or "") == first_code_text), 0)
    first_active_count = _as_int((count_map.get(first_code_text) or {}).get("count"), 0)

    return {
        "anchor": anchor_name,
        "stock_first_at": hit.get("first_at"),
        "is_strong": bool(hit.get("is_strong")),
        "first_rank_speed": _as_int(hit.get("first_rank_speed")),
        "first_speed": _as_float(hit.get("first_speed")),
        "first_amount_delta_15s": _as_float(hit.get("first_amount_delta_15s")),
        "anchor_first_code": str(first_hit.get("code") or ""),
        "anchor_first_name": str(first_hit.get("stock_name") or ""),
        "anchor_first_at": first_hit.get("first_at") or "",
        "anchor_first_rank_speed": _as_int(first_hit.get("first_rank_speed")),
        "anchor_first_speed": _as_float(first_hit.get("first_speed")),
        "anchor_first_is_strong": bool(first_hit.get("is_strong")),
        "anchor_first_active_rank": first_active_rank,
        "anchor_first_active_count": first_active_count,
        "delay_from_anchor_first_seconds": delay_seconds,
        "delay_from_anchor_first_text": format_seconds(delay_seconds),
        "wave_no": _as_int(hit.get("wave_no"), 1),
        "wave_trigger_order": wave_trigger_order,
        "wave_first_code": str(wave_first_hit.get("code") or ""),
        "wave_first_name": str(wave_first_hit.get("stock_name") or ""),
        "wave_first_at": wave_first_hit.get("first_at") or "",
        "wave_first_rank_speed": _as_int(wave_first_hit.get("first_rank_speed")),
        "wave_first_speed": _as_float(wave_first_hit.get("first_speed")),
        "delay_from_wave_first_seconds": wave_delay_seconds,
        "delay_from_wave_first_text": format_seconds(wave_delay_seconds),
        "quiet_before_wave_seconds": wave_quiet_before_seconds,
        "quiet_before_wave_text": format_seconds(wave_quiet_before_seconds),
        "is_restart_wave": wave_quiet_before_seconds >= 60 * 60,
        "wave_count_at_start": wave_trigger_order,
        "trigger_order": trigger_order,
        "peers_before": peers_before,
        "peers_total_until_event": peers_total,
        "anchor_count_at_start": anchor_count_at_start,
        "trigger_percent": round(trigger_percent, 4),
        "before_10m_count": len(before_10m_hits),
        "before_10m_label": "冷启动" if len(before_10m_hits) == 0 else "温启动" if len(before_10m_hits) <= 2 else "热启动",
        "new_after_3m": n3,
        "new_after_5m": n5,
        "new_after_10m": n10,
        "new_after_10m_rate": round(n10 / max(anchor_count_at_start, 1), 4),
        "new_after_10m_wave_rate": round(n10 / max(wave_trigger_order, 1), 4),
        "movers_after_3m": after_3m,
        "movers_after_5m": after_5m,
        "movers_after_10m": after_10m,
        "time_top3_plus_self": time_top,
        "quantity_top3_plus_self": quantity_top,
    }


__all__ = [
    "activity_context_from_index",
    "build_activity_index",
    "clean_anchor",
    "format_seconds",
    "parse_dt",
]
