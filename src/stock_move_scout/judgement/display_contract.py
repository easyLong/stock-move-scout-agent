from __future__ import annotations

from typing import Any


DISPLAY_CONTRACT_VERSION = 1


def compact(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace("%", "").strip())
    except Exception:
        return default


def first_prefixed_line(lines: list[Any], prefix: str) -> str:
    for item in lines:
        text = compact(item, 140)
        if text.startswith(prefix):
            return text
    return ""


def strip_label(text: str) -> str:
    text = compact(text, 140)
    if "：" in text:
        return compact(text.split("：", 1)[1], 120)
    return text


def build_display_contract(row: dict[str, Any], scored: dict[str, Any], texts: dict[str, Any]) -> dict[str, Any]:
    detail = scored.get("score_detail") or {}
    influence_reasons = detail.get("influence_reasons") or []
    initiative_reasons = detail.get("initiative_reasons") or []
    leadership_reasons = detail.get("anchor_leadership_reasons") or []
    tape_reasons = detail.get("tape_confirm_reasons") or []
    risk_flags = detail.get("risk_flags") or []
    speed = as_float(row.get("speed"))
    amount_delta = as_float(row.get("amount_delta_15s"))

    influence_position = strip_label(influence_reasons[0]) if influence_reasons else ""
    preheat = strip_label(first_prefixed_line(influence_reasons, "本股启动前"))
    first_quality = strip_label(first_prefixed_line(influence_reasons, "首发质量"))
    spread = strip_label(first_prefixed_line(influence_reasons, "扩散"))
    risk = compact(risk_flags[0], 80) if risk_flags else ""

    cards = [
        {"label": "持续性", "value": f"{scored.get('label', '')} / {as_float(scored.get('total')):.0f}分"},
        {"label": "带动性", "value": influence_position},
        {"label": "启动前", "value": preheat},
        {"label": "首发", "value": first_quality},
    ]
    chips = [
        {"label": "扩散", "value": spread, "tone": "good" if "强扩散" in spread else "warn" if "弱扩散" in spread else "weak"},
        {"label": "盘口", "value": compact(f"涨速{speed:.2f}%，15秒增量{amount_delta / 10000:.0f}万", 80), "tone": "weak"},
        {"label": "风险", "value": risk, "tone": "warn" if risk and risk != "暂无明显硬伤" else "weak"},
    ]
    sections = {
        "initiative": [compact(item, 120) for item in initiative_reasons[:4]],
        "influence": [compact(item, 140) for item in influence_reasons[:8]],
        "period": [compact(item, 120) for item in leadership_reasons[:4]],
        "tape": [compact(item, 100) for item in tape_reasons[:3]],
        "support": [compact(item, 120) for item in (texts.get("support_items") or [])[:7]],
    }
    return {
        "schema_version": DISPLAY_CONTRACT_VERSION,
        "summary": compact(texts.get("move_explanation") or "", 100),
        "decision_cards": [item for item in cards if item.get("value")],
        "chips": [item for item in chips if item.get("value")],
        "sections": sections,
    }
