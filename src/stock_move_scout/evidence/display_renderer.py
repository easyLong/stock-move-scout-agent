from __future__ import annotations

import json
from typing import Any


def compact(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def is_zero_amount(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    normalized = text.replace(",", "").replace("元", "").replace("万", "").replace("亿", "").replace("%", "").strip()
    try:
        return abs(float(normalized)) < 1e-9
    except ValueError:
        return text in {"0", "0.0", "0.00"}


def render_lhb_amount_line(label: str, buy_amount: Any, sell_amount: Any) -> str:
    buy = compact(buy_amount, 32)
    sell = compact(sell_amount, 32)
    has_buy = not is_zero_amount(buy_amount)
    has_sell = not is_zero_amount(sell_amount)
    if has_buy and not has_sell:
        return f"{label} 买入{buy}"
    if has_sell and not has_buy:
        return f"{label} 卖出{sell}"
    if has_buy and has_sell:
        return f"{label} 买入{buy} / 卖出{sell}"
    return f"{label}"


def parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def lhb_raw_json(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def lhb_blue_label_seats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = lhb_raw_json(payload)
    seats = raw.get("lhb_seats")
    if not isinstance(seats, list):
        seats = payload.get("lhb_seats")
    return [seat for seat in seats or [] if isinstance(seat, dict) and _seat_label_names(seat)]


def _seat_label_names(seat: dict[str, Any]) -> list[str]:
    labels = seat.get("labels")
    if not isinstance(labels, list):
        return []
    names: list[str] = []
    for label in labels:
        if not isinstance(label, dict):
            continue
        name = compact(label.get("name"), 48)
        if name and name not in names:
            names.append(name)
    return names


def render_lhb_blue_label_lines(payload: dict[str, Any]) -> list[str]:
    seat_lines: list[str] = []
    for seat in lhb_blue_label_seats(payload):
        names = _seat_label_names(seat)
        for name in names:
            line = render_lhb_amount_line(name, seat.get("buy_amount"), seat.get("sell_amount"))
            if line not in seat_lines:
                seat_lines.append(line)
    return seat_lines


def render_effective_fact_display(
    *,
    fact_title: str = "",
    fact_body: str = "",
    payload: Any = None,
) -> dict[str, Any]:
    parsed = parse_payload(payload)
    raw = lhb_raw_json(parsed)
    title = str(fact_title or "")
    body = str(fact_body or "")
    is_lhb = bool(raw.get("lhb_seats")) or "龙虎榜" in title or "龙 虎 榜" in title
    if is_lhb:
        lines = render_lhb_blue_label_lines(parsed)
        if lines:
            return {
                "display_kind": "lhb_blue_label",
                "display_lines": lines,
                "display_body": "；".join(lines),
            }
    fallback = compact(body or title, 500)
    return {
        "display_kind": "plain_fact",
        "display_lines": [fallback] if fallback else [],
        "display_body": fallback,
    }
