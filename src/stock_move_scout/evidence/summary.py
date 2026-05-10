from __future__ import annotations

from typing import Any


def compact(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def fallback_summary(payload: dict[str, Any]) -> dict[str, Any]:
    points: list[str] = []
    catalysts: list[str] = []
    risks: list[str] = []
    impact_factors: list[dict[str, str]] = []
    for item in payload.get("root_items", [])[:4]:
        text = compact(f"{item.get('date', '')} {item.get('title', '')} {item.get('content', '')}", 120)
        if text:
            points.append(text)
            factor = infer_impact_factor(text, item.get("kind", ""), item.get("date", ""))
            if factor:
                impact_factors.append(factor)
        if item.get("kind") in {"announcement", "important_event"} and text:
            catalysts.append(text)
    for item in payload.get("theme_reason_bank", [])[:3]:
        text = compact(f"{item.get('anchor', '')}：{item.get('reason', '')}", 120)
        if text:
            points.append(text)
            impact_factors.append(
                {
                    "factor_type": "题材正宗性",
                    "direction": "正向",
                    "importance": "中",
                    "evidence": text,
                    "source_type": item.get("source", "题材解释"),
                    "source_date": item.get("source_date", ""),
                }
            )
    for item in payload.get("evidence_layers", []):
        if item.get("evidence_gaps"):
            risks.append(item["evidence_gaps"])
    summary_text = points[0] if points else "异步证据待补全"
    strength = "medium" if points else "pending"
    explanation = compact(summary_text, 80)
    core_items = [
        {
            "source_type": "fallback",
            "source_date": "",
            "title": compact(point, 60),
            "reason": "规则兜底识别为可能相关材料",
            "timeliness": "unknown",
            "importance": "medium",
            "validity": "core",
        }
        for point in points[:3]
    ]
    return {
        "summary_text": compact(summary_text, 80),
        "evidence_filter_summary": "规则兜底：保留公告、事件、题材解释中含业绩/合同/题材相关关键词的材料。",
        "key_facts": points[:3],
        "move_reason": explanation,
        "sustainability_basis": catalysts[:2] + points[: max(0, 3 - len(catalysts[:2]))],
        "main_flaw": "" if points else "缺少可核验核心证据",
        "missing_evidence": [] if points else ["缺少公告、互动易、定期报告或题材解释"],
        "core_evidence_items": core_items,
        "timeliness_label": "unknown",
        "timeliness_reason": "规则兜底未做精确时效判断",
        "final_analysis": explanation,
        "move_explanation": explanation,
        "explanation_strength": "weak" if points else "none",
        "anchor_match": "weak",
        "anchor_match_reason": "规则兜底未做锚点一致性判断",
        "quality_label": "无法解释" if not points else "个股脉冲",
        "core_support": points[:2],
        "counterpoints": [] if points else ["缺少可核验核心证据"],
        "final_view": explanation,
        "key_points": points[:4],
        "hard_catalysts": catalysts[:3],
        "impact_factors": dedupe_impact_factors(impact_factors)[:5],
        "risks": risks[:3],
        "evidence_strength": strength,
        "evidence_gaps": [] if points else ["缺少公告、互动易、定期报告或题材解释"],
    }


def infer_impact_factor(text: str, source_type: str, source_date: str) -> dict[str, str] | None:
    value = text or ""
    rules = [
        ("重组", "高", ["重组", "并购", "收购", "资产注入", "控制权", "借壳"]),
        ("合同订单", "高", ["合同", "订单", "中标", "框架协议", "采购协议", "大订单"]),
        ("业绩", "高", ["净利润", "营收", "同比", "扭亏", "预增", "增长", "毛利率", "一季度报告", "年报"]),
        ("增减持", "中", ["增持", "减持", "回购", "解禁"]),
        ("产能", "中", ["产能", "投产", "扩产", "产线", "满产"]),
        ("题材正宗性", "中", ["互动易", "回复", "应用于", "产品为", "合作关系", "客户"]),
        ("政策行业", "中", ["政策", "规划", "补贴", "行业", "试点"]),
        ("风险", "高", ["风险", "问询", "立案", "处罚", "亏损", "下降"]),
    ]
    for factor_type, importance, keywords in rules:
        if any(keyword in value for keyword in keywords):
            direction = "负向" if factor_type == "风险" or any(keyword in value for keyword in ["减持", "亏损", "下降", "处罚", "立案"]) else "正向"
            return {
                "factor_type": factor_type,
                "direction": direction,
                "importance": importance,
                "evidence": compact(value, 120),
                "source_type": source_type or "异步材料",
                "source_date": source_date or "",
            }
    return None


def dedupe_impact_factors(items: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    priority = {"高": 3, "中": 2, "低": 1}
    for item in sorted(items, key=lambda value: priority.get(value.get("importance", ""), 0), reverse=True):
        evidence = item.get("evidence", "")
        topic = evidence.split("：", 1)[0][:30] if "：" in evidence else evidence[:40]
        key = (item.get("factor_type", ""), topic)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def impact_summary_text(summary: dict[str, Any]) -> str:
    factors = summary.get("impact_factors") or []
    if not isinstance(factors, list):
        return ""
    lines: list[str] = []
    for item in factors[:5]:
        if not isinstance(item, dict):
            continue
        factor_type = str(item.get("factor_type", "")).strip()
        direction = str(item.get("direction", "")).strip()
        importance = str(item.get("importance", "")).strip()
        evidence = compact(str(item.get("evidence", "")).strip(), 90)
        if not factor_type or not evidence:
            continue
        prefix = f"{factor_type}"
        if direction:
            prefix += f"/{direction}"
        if importance:
            prefix += f"/{importance}"
        lines.append(f"{prefix}：{evidence}")
    return "\n".join(lines)


def as_text_list(value: Any, limit: int, text_limit: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = compact(str(item or ""), text_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def fact_from_core_item(item: Any) -> str:
    if not isinstance(item, dict):
        return compact(str(item or ""), 110)
    reason = compact(str(item.get("reason") or item.get("evidence") or item.get("title") or ""), 95)
    source_date = str(item.get("source_date") or "").strip()
    source_type = str(item.get("source_type") or "").strip()
    prefix = source_date or source_type
    if prefix and reason:
        return compact(f"{prefix} {reason}", 110)
    return reason


def fact_from_impact_item(item: Any) -> str:
    if not isinstance(item, dict):
        return compact(str(item or ""), 110)
    evidence = compact(str(item.get("evidence") or ""), 95)
    source_date = str(item.get("source_date") or "").strip()
    factor_type = str(item.get("factor_type") or "").strip()
    prefix = source_date or factor_type
    if prefix and evidence:
        return compact(f"{prefix} {evidence}", 110)
    return evidence


def normalize_fact_first_fields(summary: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(summary)
    key_facts = as_text_list(normalized.get("key_facts"), 3, 110)
    if not key_facts:
        for item in normalized.get("core_evidence_items") or []:
            text = fact_from_core_item(item)
            if text and text not in key_facts:
                key_facts.append(text)
            if len(key_facts) >= 3:
                break
    if len(key_facts) < 3:
        for item in normalized.get("impact_factors") or []:
            text = fact_from_impact_item(item)
            if text and text not in key_facts:
                key_facts.append(text)
            if len(key_facts) >= 3:
                break
    if len(key_facts) < 3:
        for item in normalized.get("lhb_seat_evidence") or []:
            if not isinstance(item, dict):
                continue
            for text in as_text_list(item.get("key_facts"), 4, 110):
                if text and text not in key_facts:
                    key_facts.append(text)
                if len(key_facts) >= 3:
                    break
            if len(key_facts) >= 3:
                break
    if len(key_facts) < 3:
        for text in as_text_list(normalized.get("core_support"), 3, 110):
            if text not in key_facts:
                key_facts.append(text)
            if len(key_facts) >= 3:
                break
    normalized["key_facts"] = key_facts[:3]

    move_reason = compact(str(normalized.get("move_reason") or normalized.get("move_explanation") or normalized.get("final_analysis") or normalized.get("summary_text") or ""), 120)
    normalized["move_reason"] = move_reason

    basis = as_text_list(normalized.get("sustainability_basis"), 3, 110)
    if not basis:
        basis.extend(as_text_list(normalized.get("hard_catalysts"), 2, 110))
    if len(basis) < 3:
        basis.extend(text for text in as_text_list(normalized.get("core_support"), 3, 110) if text not in basis)
    normalized["sustainability_basis"] = basis[:3]

    counterpoints = as_text_list(normalized.get("counterpoints"), 1, 120)
    risks = as_text_list(normalized.get("risks"), 1, 120)
    normalized["main_flaw"] = compact(str(normalized.get("main_flaw") or (counterpoints[0] if counterpoints else "") or (risks[0] if risks else "")), 120)

    missing = as_text_list(normalized.get("missing_evidence"), 3, 110)
    if not missing:
        missing = as_text_list(normalized.get("evidence_gaps"), 3, 110)
    normalized["missing_evidence"] = missing[:3]
    return normalized
