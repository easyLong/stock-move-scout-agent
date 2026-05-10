from __future__ import annotations

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


def initiative_score(activity_context: dict[str, Any]) -> tuple[float, str, list[str]]:
    if not activity_context:
        return 0.0, "未知", []
    score = 0.0
    reasons: list[str] = []
    trigger_order = _as_int(activity_context.get("trigger_order"), 9999)
    first_rank = _as_int(activity_context.get("first_rank_speed"), 9999)
    first_speed = _as_float(activity_context.get("first_speed"))
    first_amount_delta = _as_float(activity_context.get("first_amount_delta_15s"))
    peers_before = _as_int(activity_context.get("peers_before"), 9999)
    peers_total = _as_int(activity_context.get("peers_total_until_event"), 0)
    first_at = str(activity_context.get("stock_first_at") or "")
    trigger_percent = _as_float(activity_context.get("trigger_percent"))

    if trigger_order == 1:
        score += 14
        reasons.append("同锚点最早触发")
    elif trigger_order <= 3:
        score += 10
        reasons.append(f"同锚点第{trigger_order}个触发")
    elif trigger_order <= 5:
        score += 6
        reasons.append(f"同锚点第{trigger_order}个触发")
    elif peers_total and trigger_percent <= 0.1 and trigger_order <= 15:
        score += 3
        reasons.append(f"同锚点前{trigger_percent * 100:.0f}%触发")

    if first_rank <= 3:
        score += 8
        reasons.append(f"首次涨速Top{first_rank}")
    elif first_rank <= 5:
        score += 5
        reasons.append(f"首次涨速Top{first_rank}")
    elif first_rank <= 10:
        score += 2

    if first_speed >= 1:
        score += 3
        reasons.append(f"首次涨速{first_speed:.2f}%")
    if first_amount_delta >= 30_000_000:
        score += 2
        reasons.append(f"15秒成交增量{first_amount_delta / 10000:.0f}万")

    if peers_before == 0:
        score += 8
        reasons.append("早于同锚点多数股票启动")
    elif peers_before <= 2:
        score += 4

    if first_at:
        reasons.append(f"首次触发{first_at.split(' ')[-1]}")
    label = "强" if score >= 22 else "中" if score >= 12 else "弱"
    return min(score, 30.0), label, reasons[:4]


def influence_score(activity_context: dict[str, Any]) -> tuple[float, str, list[str]]:
    if not activity_context:
        return 0.0, "未知", []
    score = 0.0
    n3 = _as_int(activity_context.get("new_after_3m"))
    n5 = _as_int(activity_context.get("new_after_5m"))
    n10 = _as_int(activity_context.get("new_after_10m"))
    trigger_order = _as_int(activity_context.get("trigger_order"), 9999)
    expansion_rate = _as_float(activity_context.get("new_after_10m_rate"))
    first_code = str(activity_context.get("anchor_first_code") or "").strip()
    first_name = str(activity_context.get("anchor_first_name") or "").strip()
    first_at = str(activity_context.get("anchor_first_at") or "").strip()
    first_time = first_at.split(" ")[-1] if first_at else ""
    first_rank = _as_int(activity_context.get("anchor_first_rank_speed"), 0)
    first_speed = _as_float(activity_context.get("anchor_first_speed"))
    first_is_strong = bool(activity_context.get("anchor_first_is_strong"))
    first_active_rank = _as_int(activity_context.get("anchor_first_active_rank"), 0)
    first_active_count = _as_int(activity_context.get("anchor_first_active_count"), 0)
    delay_text = str(activity_context.get("delay_from_anchor_first_text") or "").strip()
    delay_seconds = _as_int(activity_context.get("delay_from_anchor_first_seconds"), 0)
    before_10m_count = _as_int(activity_context.get("before_10m_count"), 0)
    before_10m_label = str(activity_context.get("before_10m_label") or "").strip()
    movers_10m = activity_context.get("movers_after_10m") if isinstance(activity_context.get("movers_after_10m"), list) else []
    time_top = activity_context.get("time_top3_plus_self") if isinstance(activity_context.get("time_top3_plus_self"), list) else []
    quantity_top = (
        activity_context.get("quantity_top3_plus_self") if isinstance(activity_context.get("quantity_top3_plus_self"), list) else []
    )
    first_text = ""
    if first_code and first_name:
        first_text = f"{first_name} {first_code}{(' ' + first_time) if first_time else ''}"
    elif first_name or first_code:
        first_text = f"{first_name or first_code}{(' ' + first_time) if first_time else ''}"

    if trigger_order == 1:
        position_label = "首发"
        score += 6
    elif trigger_order <= 5 and delay_seconds <= 60:
        position_label = "前排"
        score += 5
    elif trigger_order <= 10 and delay_seconds <= 180:
        position_label = "前排观察"
        score += 4
    elif delay_seconds <= 600:
        position_label = "跟随扩散"
        score += 2
    elif trigger_order <= 5:
        position_label = "序列靠前但晚启动"
        score += 1
    else:
        position_label = "后排跟随"

    if delay_seconds <= 60:
        score += 4
    elif delay_seconds <= 180:
        score += 3
    elif delay_seconds <= 600:
        score += 1

    if n3 >= 3 and expansion_rate >= 0.5:
        score += 5
    elif n5 >= 5:
        score += 4
    elif n10 >= 8:
        score += 3
    elif n10 >= 4:
        score += 2
    elif n3 >= 1:
        score += 1

    if len(movers_10m) >= 5:
        score += 3
    elif len(movers_10m) >= 2:
        score += 2
    elif len(movers_10m) >= 1:
        score += 1

    if first_rank and first_rank <= 5:
        score += 2
    elif first_speed >= 1:
        score += 1

    if before_10m_count == 0 and n10 >= 4:
        score += 2
    elif before_10m_count >= 5 and trigger_order > 1:
        score = max(0.0, score - 2)

    if first_is_strong and (0 < first_active_rank <= 3 or first_rank <= 5):
        first_quality = "强"
        score += 2
    elif first_is_strong or 0 < first_active_rank <= 3 or first_active_count >= 3 or first_rank <= 5:
        first_quality = "中"
        score += 1
    else:
        first_quality = "弱"

    if n3 >= 3 and expansion_rate >= 0.5:
        expansion_label = "强扩散"
    elif n5 >= 5 or n10 >= 8:
        expansion_label = "中强扩散"
    elif n10 >= 4 or n3 >= 1:
        expansion_label = "普通扩散"
    else:
        expansion_label = "弱扩散"

    leader_like = position_label == "首发"
    if leader_like and expansion_label in {"强扩散", "中强扩散"}:
        label = "疑似带动强"
    elif position_label in {"首发", "前排", "前排观察"}:
        label = "疑似带动中" if expansion_label in {"强扩散", "中强扩散", "普通扩散"} else "弱"
    elif expansion_label in {"强扩散", "中强扩散", "普通扩散"}:
        label = "同锚扩散"
    else:
        label = "弱"

    if position_label == "首发":
        position_line = f"带动定位：首发；本股为同锚全天首触发{(' ' + first_time) if first_time else ''}"
    else:
        position_line = f"带动定位：{position_label}；本股第{trigger_order}个触发，晚于首发{delay_text or '未知'}"

    def strong_mark(item: dict[str, Any]) -> str:
        return "强势榜" if bool(item.get("is_strong")) else "非强势"

    def short_time(value: Any) -> str:
        return str(value or "").split(" ")[-1]

    def stock_brief(item: dict[str, Any]) -> str:
        name = str(item.get("stock_name") or "").strip()
        item_code = str(item.get("code") or "").strip()
        base = f"{name}{(' ' + item_code) if item_code else ''}".strip() or "未知"
        prefix = "本股 " if bool(item.get("is_current")) else ""
        return f"{prefix}{base}({strong_mark(item)})"

    time_parts: list[str] = []
    for item in time_top:
        if not isinstance(item, dict):
            continue
        order = _as_int(item.get("order"), 0)
        time_parts.append(f"{order if order else '-'} {stock_brief(item)} {short_time(item.get('first_at'))}".strip())
    time_line = f"时间序列：{'；'.join(time_parts)}" if time_parts else f"时间序列：首发 {first_text or '未知'}"

    quantity_parts: list[str] = []
    for item in quantity_top:
        if not isinstance(item, dict):
            continue
        rank = _as_int(item.get("rank"), 0)
        quantity_parts.append(f"{rank if rank else '-'} {stock_brief(item)} x{_as_int(item.get('count'))}")
    quantity_line = f"活跃序列：{'；'.join(quantity_parts)}" if quantity_parts else "活跃序列：暂无有效统计"

    mover_names: list[str] = []
    for item in movers_10m[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("stock_name") or "").strip()
        code = str(item.get("code") or "").strip()
        if name or code:
            mover_names.append(f"{name}{(' ' + code) if code else ''}({'强势榜' if bool(item.get('is_strong')) else '非强势'})".strip())
    spread_line = f"扩散：3分钟+{n3}，5分钟+{n5}，10分钟+{n10}；{expansion_label}"
    preheat_line = f"本股启动前：10分钟同锚新增{before_10m_count}只；{before_10m_label or '状态未知'}"
    first_quality_line = f"首发质量：{first_quality}；{first_text or '未知'}"
    if first_is_strong:
        first_quality_line += "；强势榜"
    if first_active_rank and (first_active_rank <= 3 or first_active_count >= 3):
        first_quality_line += f"；活跃第{first_active_rank} x{first_active_count}"
    elif first_active_count:
        if first_active_count >= 3:
            first_quality_line += f"；出现{first_active_count}次"
    if first_rank:
        first_quality_line += f"；涨速Top{first_rank}"
    movers_line = f"扩散股：{'、'.join(mover_names)}" if mover_names else "扩散股：无明显高质量跟随"
    if leader_like and expansion_label in {"强扩散", "中强扩散"}:
        judgement = "判断：首发后同锚快速扩散，具备带动观察价值"
    elif leader_like:
        judgement = "判断：全天率先启动，但扩散强度一般"
    elif position_label in {"前排", "前排观察"} and expansion_label in {"强扩散", "中强扩散"}:
        judgement = "判断：前排启动后扩散较快，具备跟随带动观察价值"
    elif position_label in {"前排", "前排观察"}:
        judgement = "判断：前排同步启动，但扩散强度一般"
    elif expansion_label in {"强扩散", "中强扩散"}:
        judgement = "判断：板块扩散较强，但本股不是首发，更偏跟随"
    else:
        judgement = "判断：不是同锚领头，更像后排跟随或孤立脉冲"
    if before_10m_count >= 5 and position_label not in {"首发", "前排", "前排观察"}:
        judgement = "判断：启动前同锚已热，本股更像参与已有扩散"
    reasons = [position_line, preheat_line, first_quality_line, time_line, quantity_line, spread_line, movers_line, judgement]
    return min(score, 20.0), label, reasons[:8]


def short_term_behavior_score(
    initiative: float,
    initiative_label: str,
    influence: float,
    influence_label: str,
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    if initiative_label == "强":
        score += 7
        reasons.append("主动性强")
    elif initiative_label == "中":
        score += 4
        reasons.append("主动性中")
    elif initiative > 0:
        score += min(2.0, initiative * 0.08)

    if influence_label == "疑似带动强":
        score += 8
        reasons.append("疑似带动强")
    elif influence_label == "疑似带动中":
        score += 5
        reasons.append("疑似带动中")
    elif influence_label == "同锚扩散":
        score += 2
        reasons.append("同锚扩散")
    elif influence > 0:
        score += min(1.5, influence * 0.06)

    if initiative_label == "弱" and influence_label == "弱":
        return 0.0, ["主动性/扩散不足"]
    return min(score, 15.0), reasons[:3]


__all__ = [
    "influence_score",
    "initiative_score",
    "short_term_behavior_score",
]
