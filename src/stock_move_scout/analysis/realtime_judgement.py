from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_move_scout.sources.quote_rows import safe_to_float


def split_concepts(row: dict[str, Any]) -> list[str]:
    raw = str(row.get("concepts", ""))
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_heat_counts(rows: list[dict[str, Any]], sample_size: int) -> dict[str, dict[str, int]]:
    positive_rows = [row for row in rows if safe_to_float(row.get("pct_change")) > 0]
    sample = sorted(positive_rows, key=lambda row: safe_to_float(row.get("pct_change")), reverse=True)[:sample_size]
    sample += [row for row in rows if safe_to_float(row.get("speed")) > 0]

    industry_counts: dict[str, int] = {}
    sub_industry_counts: dict[str, int] = {}
    concept_counts: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for row in sample:
        key = f"{row.get('market')}:{row.get('code')}"
        if ("row", key) in seen:
            continue
        seen.add(("row", key))
        industry = str(row.get("industry", "")).strip()
        sub_industry = str(row.get("sub_industry", "")).strip()
        if industry:
            industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if sub_industry:
            sub_industry_counts[sub_industry] = sub_industry_counts.get(sub_industry, 0) + 1
        for concept in split_concepts(row):
            concept_counts[concept] = concept_counts.get(concept, 0) + 1

    return {
        "industry": industry_counts,
        "sub_industry": sub_industry_counts,
        "concept": concept_counts,
    }


def freshness_label(seen_count: int) -> str:
    if seen_count <= 0:
        return "新上榜"
    if seen_count <= 2:
        return "重复出现"
    return "反复出现"


def speed_signal(speed: float) -> str:
    if speed >= 1.5:
        return "急拉"
    if speed >= 0.8:
        return "明显拉升"
    if speed > 0:
        return "轻微异动"
    return "暂无快照涨速"


def pct_position(pct_change: float) -> str:
    if pct_change >= 19:
        return "20cm涨停附近"
    if pct_change >= 9.5:
        return "10cm涨停附近"
    if pct_change >= 7:
        return "涨幅偏高"
    if pct_change >= 3:
        return "中段拉升"
    if pct_change > 0:
        return "初动观察"
    return "未走强"


def amount_confirm(amount: float) -> str:
    if amount >= 1_000_000_000:
        return "成交强确认"
    if amount >= 300_000_000:
        return "成交有效"
    if amount >= 100_000_000:
        return "成交一般"
    return "成交偏弱"


def linkage_signal(linkage_count: int) -> str:
    if linkage_count >= 6:
        return "板块扩散"
    if linkage_count >= 3:
        return "有联动"
    return "个股孤立"


def choose_action(
    candidate_basis: str,
    speed: float,
    pct_change: float,
    amount_label: str,
    linkage_count: int,
) -> tuple[str, list[str]]:
    risks: list[str] = []
    if candidate_basis.startswith("pct_fallback"):
        risks.append("当前无快照涨速")
    if pct_change >= 18:
        risks.append("涨幅已高")
    if amount_label == "成交偏弱":
        risks.append("成交不足")
    if linkage_count < 3:
        risks.append("板块联动弱")

    if pct_change >= 18 and (amount_label == "成交偏弱" or linkage_count < 3):
        return "回避池", risks
    if speed > 0 and linkage_count >= 3 and amount_label in {"成交有效", "成交强确认"} and pct_change < 15:
        return "观察池", risks
    return "等待验证", risks


def build_value_judgement(
    row: dict[str, Any],
    *,
    speed: float,
    pct_change: float,
    amount_label: str,
    linkage_count: int,
    concepts: list[str],
) -> dict[str, str]:
    name = str(row.get("name", ""))
    industry = str(row.get("industry", ""))
    sub_industry = str(row.get("sub_industry", ""))
    concept_text = "、".join(concepts[:3]) if concepts else "暂无核心概念"

    positives: list[str] = []
    negatives: list[str] = []
    next_watch: list[str] = []

    if speed >= 1.2:
        positives.append("分钟级拉升强")
        next_watch.append("下一轮涨速是否继续保持在1%以上")
    elif speed > 0:
        positives.append("有短线异动")
        next_watch.append("下一轮是否继续上榜")

    if amount_label in {"成交有效", "成交强确认"}:
        positives.append(amount_label)
    else:
        negatives.append(amount_label)
        next_watch.append("成交额是否放大到3亿以上")

    if linkage_count >= 6:
        positives.append("板块/概念扩散明显")
    elif linkage_count >= 3:
        positives.append("有板块联动")
    else:
        negatives.append("联动不足")
        next_watch.append("同题材是否出现更多个股跟随")

    if pct_change >= 10:
        negatives.append("位置偏高")
        next_watch.append("是否封住高位或放量回落")
    elif pct_change < 0:
        negatives.append("日内仍未走强")
        next_watch.append("能否翻红并站稳")
    elif pct_change <= 3:
        positives.append("位置仍偏早")
    else:
        positives.append("已有一定日内强度")

    if "ST" in name.upper() or "ST板块" in concepts:
        negatives.append("ST属性")
    if amount_label == "成交偏弱":
        negatives.append("量能不足")

    if amount_label in {"成交有效", "成交强确认"} and linkage_count >= 3 and 0 < pct_change < 10:
        value_view = "优先观察"
    elif amount_label in {"成交有效", "成交强确认"} and pct_change >= 10:
        value_view = "高位验证"
    elif pct_change < 0:
        value_view = "反抽观察"
    elif "ST" in name.upper() or amount_label == "成交偏弱":
        value_view = "谨慎过滤"
    else:
        value_view = "等待确认"

    return {
        "value_view": value_view,
        "value_reason": (
            f"{industry}/{sub_industry}，关联{concept_text}；"
            f"好处：{'、'.join(positives) or '暂无明显优势'}；"
            f"不足：{'、'.join(negatives) or '暂未发现明显硬伤'}"
        ),
        "next_watch": "；".join(dict.fromkeys(next_watch)) or "观察是否持续上榜并保持量价配合",
        "avoid_reason": "、".join(negatives),
    }


def build_judgement_rows(
    rows: list[dict[str, Any]],
    speed_rows: list[dict[str, Any]],
    pct_rows: list[dict[str, Any]],
    seen_state: dict[str, Any],
    *,
    top: int,
    heat_sample_size: int,
) -> list[dict[str, Any]]:
    candidate_basis = "speed"
    candidates = speed_rows[:top]
    if not candidates:
        candidate_basis = "pct_fallback_no_speed"
        candidates = pct_rows[:top]

    heat_counts = build_heat_counts(rows, heat_sample_size)
    items = seen_state.setdefault("items", {})
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    judgement_rows: list[dict[str, Any]] = []

    for row in candidates:
        key = f"{row.get('market')}:{row.get('code')}"
        seen_item = items.get(key, {})
        seen_count = int(seen_item.get("count", 0) or 0)
        industry = str(row.get("industry", "")).strip()
        sub_industry = str(row.get("sub_industry", "")).strip()
        concepts = split_concepts(row)
        concept_pairs = [(name, heat_counts["concept"].get(name, 0)) for name in concepts]
        hot_pairs = sorted(concept_pairs, key=lambda item: item[1], reverse=True)[:3]
        industry_count = heat_counts["industry"].get(industry, 0)
        sub_count = heat_counts["sub_industry"].get(sub_industry, 0)
        concept_count = hot_pairs[0][1] if hot_pairs else 0
        linkage_count = max(industry_count, sub_count, concept_count)
        speed_value = safe_to_float(row.get("speed"))
        pct_value = safe_to_float(row.get("pct_change"))
        amount_value = safe_to_float(row.get("amount"))
        amount_label = amount_confirm(amount_value)
        action, risks = choose_action(candidate_basis, speed_value, pct_value, amount_label, linkage_count)
        value = build_value_judgement(
            row,
            speed=speed_value,
            pct_change=pct_value,
            amount_label=amount_label,
            linkage_count=linkage_count,
            concepts=concepts,
        )
        hot_concepts = ",".join(name for name, count in hot_pairs if count > 0)
        points = [
            speed_signal(speed_value),
            pct_position(pct_value),
            amount_label,
            linkage_signal(linkage_count),
            freshness_label(seen_count),
        ]

        out = dict(row)
        out.update(
            {
                "candidate_basis": candidate_basis,
                "freshness": freshness_label(seen_count),
                "speed_signal": speed_signal(speed_value),
                "pct_position": pct_position(pct_value),
                "amount_confirm": amount_label,
                "linkage_signal": linkage_signal(linkage_count),
                "industry_hot_count": industry_count,
                "sub_industry_hot_count": sub_count,
                "concept_hot_count": concept_count,
                "hot_concepts": hot_concepts,
                "risk_flags": ",".join(risks),
                "action_bucket": action,
                "value_view": value["value_view"],
                "value_reason": value["value_reason"],
                "next_watch": value["next_watch"],
                "avoid_reason": value["avoid_reason"],
                "key_points": " / ".join(points),
            }
        )
        judgement_rows.append(out)
        items[key] = {
            "count": seen_count + 1,
            "last_seen": captured_at,
            "name": row.get("name", ""),
            "last_action_bucket": action,
        }

    return judgement_rows


__all__ = [
    "amount_confirm",
    "build_heat_counts",
    "build_judgement_rows",
    "build_value_judgement",
    "choose_action",
    "freshness_label",
    "linkage_signal",
    "pct_position",
    "speed_signal",
    "split_concepts",
]
