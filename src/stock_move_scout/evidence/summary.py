from __future__ import annotations

from typing import Any


def compact(text: str, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "..."


def current_fact_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("current_facts") or []
    if not isinstance(items, list):
        return []
    facts = [item for item in items if isinstance(item, dict)]
    return sorted(facts, key=lambda item: str(item.get("fact_date") or ""), reverse=True)


def fact_line(item: dict[str, Any], limit: int = 360) -> str:
    date = str(item.get("fact_date") or "").strip()
    title = str(item.get("title") or item.get("fact_title") or "").strip()
    body = str(item.get("body") or item.get("fact_body") or "").strip()
    text = title
    if body and body != title:
        text = f"{title}：{body}" if title else body
    return compact(f"{date}｜{text}" if date else text, limit)


def infer_factor_type(text: str, fallback: str = "") -> str:
    value = text or ""
    rules = [
        ("业绩", ["净利润", "营收", "同比", "扭亏", "预增", "增长", "毛利率", "年报", "季报"]),
        ("重组", ["重组", "并购", "收购", "资产注入", "控制权", "借壳", "股权转让"]),
        ("合同订单", ["合同", "订单", "中标", "定点", "供货", "采购", "客户", "框架协议"]),
        ("产能", ["产能", "投产", "扩产", "生产线", "满产", "项目建设"]),
        ("增减持", ["增持", "减持", "回购", "解禁"]),
        ("政策行业", ["政策", "规划", "补贴", "行业", "试点"]),
        ("风险", ["风险", "问询", "立案", "处罚", "亏损", "下降", "减持"]),
    ]
    for factor_type, keywords in rules:
        if any(keyword in value for keyword in keywords):
            return factor_type
    return fallback if fallback in {"业绩", "重组", "合同订单", "题材正宗性", "增减持", "产能", "政策行业", "风险", "其他"} else "其他"


def direction_for(text: str, factor_type: str) -> str:
    if factor_type == "风险" or any(keyword in text for keyword in ["减持", "亏损", "下降", "处罚", "立案", "问询"]):
        return "负向"
    return "正向"


def importance_for(text: str) -> str:
    high_keywords = ["重大", "控制权", "重组", "收购", "中标", "大额", "大幅增长", "扭亏", "预增"]
    medium_keywords = ["合同", "订单", "同比", "客户", "定点", "投产", "扩产", "回购", "增持"]
    if any(keyword in text for keyword in high_keywords):
        return "高"
    if any(keyword in text for keyword in medium_keywords):
        return "中"
    return "低"


def fallback_summary(payload: dict[str, Any]) -> dict[str, Any]:
    facts = current_fact_items(payload)
    lines = [fact_line(item) for item in facts]
    lines = [line for line in lines if line]
    summary_text = f"按时间线整理{len(lines)}条近10日有效事实" if lines else "暂无近10日有效事实"
    core_items = []
    impact_factors = []
    for item, line in zip(facts, lines):
        factor_type = infer_factor_type(line, str(item.get("fact_type") or ""))
        core_items.append(
            {
                "source_type": str(item.get("source_table") or item.get("fact_type") or "有效事实"),
                "source_date": str(item.get("fact_date") or ""),
                "title": compact(str(item.get("title") or ""), 120),
                "reason": line,
                "timeliness": "fresh" if str(item.get("display_level") or "") == "primary" else "recent",
                "importance": {"高": "high", "中": "medium", "低": "low"}.get(importance_for(line), "medium"),
                "validity": "core",
            }
        )
        impact_factors.append(
            {
                "factor_type": factor_type,
                "direction": direction_for(line, factor_type),
                "importance": importance_for(line),
                "evidence": line,
                "source_type": str(item.get("source_table") or item.get("fact_type") or "有效事实"),
                "source_date": str(item.get("fact_date") or ""),
            }
        )
    first_line = lines[0] if lines else ""
    return {
        "summary_text": summary_text,
        "evidence_filter_summary": "仅整理 current_facts 中的近10日有效事实，按日期倒序保留原文关键数字、金额、比例、客户、产品和事件对象。",
        "key_facts": lines[:12],
        "move_reason": first_line,
        "sustainability_basis": lines[:12],
        "main_flaw": "" if lines else "缺少近10日有效事实",
        "missing_evidence": [] if lines else ["等待 F10 重要事件等有效事实补充"],
        "core_evidence_items": core_items[:12],
        "timeliness_label": "recent" if lines else "unknown",
        "timeliness_reason": "近10日重要事件按日期倒序整理" if lines else "没有可整理的 current_facts",
        "final_analysis": summary_text,
        "move_explanation": first_line,
        "explanation_strength": "medium" if lines else "none",
        "anchor_match": "weak",
        "anchor_match_reason": "本层只做事实时间线整理，不判断题材锚点匹配强弱。",
        "quality_label": "公告驱动" if lines else "无法解释",
        "core_support": lines[:12],
        "counterpoints": [],
        "final_view": summary_text,
        "key_points": lines[:12],
        "hard_catalysts": lines[:12],
        "impact_factors": impact_factors[:12],
        "risks": [],
        "evidence_strength": "medium" if lines else "pending",
        "evidence_gaps": [] if lines else ["缺少近10日有效事实"],
    }


def impact_summary_text(summary: dict[str, Any]) -> str:
    factors = summary.get("impact_factors") or []
    if not isinstance(factors, list):
        return ""
    lines: list[str] = []
    for item in factors[:12]:
        if not isinstance(item, dict):
            continue
        source_date = str(item.get("source_date") or "").strip()
        evidence = compact(str(item.get("evidence") or "").strip(), 260)
        if not evidence:
            continue
        if source_date and not evidence.startswith(source_date):
            evidence = f"{source_date}｜{evidence}"
        if evidence not in lines:
            lines.append(evidence)
    return "\n".join(lines)


def as_text_list(value: Any, limit: int, text_limit: int = 240) -> list[str]:
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
        return compact(str(item or ""), 240)
    reason = compact(str(item.get("reason") or item.get("evidence") or item.get("title") or ""), 240)
    source_date = str(item.get("source_date") or "").strip()
    if source_date and reason and not reason.startswith(source_date):
        return compact(f"{source_date}｜{reason}", 260)
    return reason


def fact_from_impact_item(item: Any) -> str:
    if not isinstance(item, dict):
        return compact(str(item or ""), 240)
    evidence = compact(str(item.get("evidence") or ""), 240)
    source_date = str(item.get("source_date") or "").strip()
    if source_date and evidence and not evidence.startswith(source_date):
        return compact(f"{source_date}｜{evidence}", 260)
    return evidence


def normalize_fact_first_fields(summary: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(summary)
    key_facts = as_text_list(normalized.get("key_facts"), 12, 260)
    if not key_facts:
        for item in normalized.get("core_evidence_items") or []:
            text = fact_from_core_item(item)
            if text and text not in key_facts:
                key_facts.append(text)
            if len(key_facts) >= 12:
                break
    if len(key_facts) < 12:
        for item in normalized.get("impact_factors") or []:
            text = fact_from_impact_item(item)
            if text and text not in key_facts:
                key_facts.append(text)
            if len(key_facts) >= 12:
                break
    normalized["key_facts"] = key_facts[:12]

    if not normalized.get("summary_text"):
        normalized["summary_text"] = f"按时间线整理{len(key_facts)}条有效事实" if key_facts else "暂无有效事实"
    if not normalized.get("move_reason"):
        normalized["move_reason"] = key_facts[0] if key_facts else ""
    if not normalized.get("move_explanation"):
        normalized["move_explanation"] = normalized.get("move_reason", "")
    if not normalized.get("final_analysis"):
        normalized["final_analysis"] = normalized.get("summary_text", "")
    if not normalized.get("final_view"):
        normalized["final_view"] = normalized.get("summary_text", "")
    if not normalized.get("hard_catalysts"):
        normalized["hard_catalysts"] = key_facts[:12]
    if not normalized.get("core_support"):
        normalized["core_support"] = key_facts[:12]
    if not normalized.get("sustainability_basis"):
        normalized["sustainability_basis"] = key_facts[:12]
    if not normalized.get("main_flaw"):
        normalized["main_flaw"] = ""
    if not normalized.get("missing_evidence"):
        normalized["missing_evidence"] = []
    if not normalized.get("counterpoints"):
        normalized["counterpoints"] = []
    if not normalized.get("risks"):
        normalized["risks"] = []
    if not normalized.get("evidence_gaps"):
        normalized["evidence_gaps"] = []
    return normalized
