from __future__ import annotations

import json
import re
from typing import Any


EVIDENCE_VIEW_VERSION = 1

LAYER_META = {
    "realtime": {"key": "实时证据", "title": "实时链路证据", "hint": "随扫描即时产生", "class_name": "realtime"},
    "async": {"key": "异步证据", "title": "补充验证证据", "hint": "事实卡/模型/龙虎榜补强", "class_name": "async"},
}

TYPE_META = {
    "facts": {"label": "关键事实", "class_name": "stock", "source": "事实卡", "priority": 0, "limit": 120, "max_items": 3},
    "move": {"label": "异动解释", "class_name": "summary", "source": "模型解释", "priority": 1, "limit": 100, "max_items": 1},
    "quality": {"label": "异动质量", "class_name": "event", "source": "模型判断", "priority": 2, "limit": 100, "max_items": 1},
    "period": {"label": "区间领头", "class_name": "theme", "source": "问财区间排名", "priority": 3, "limit": 120, "max_items": 3},
    "initiative": {"label": "主动性", "class_name": "theme", "source": "扫描触发", "priority": 3, "limit": 120, "max_items": 3},
    "influence": {"label": "带动性", "class_name": "event", "source": "同锚扩散", "priority": 4, "limit": 140, "max_items": 8},
    "lhb": {"label": "龙虎榜席位", "class_name": "event", "source": "东方财富龙虎榜", "priority": 3, "limit": 130, "max_items": 4},
    "anchor": {"label": "锚点一致性", "class_name": "theme", "source": "模型判断", "priority": 3, "limit": 120, "max_items": 1},
    "support": {"label": "核心支撑", "class_name": "stock", "source": "模型筛选", "priority": 4, "limit": 110, "max_items": 2},
    "counter": {"label": "瑕疵", "class_name": "announcement", "source": "模型判断", "priority": 6, "limit": 110, "max_items": 1},
    "final": {"label": "核心结论", "class_name": "summary", "source": "模型结论", "priority": 5, "limit": 100, "max_items": 1},
    "timeliness": {"label": "时效判断", "class_name": "event", "source": "模型判断", "priority": 8, "limit": 120, "max_items": 1},
    "flaw": {"label": "最大瑕疵", "class_name": "announcement", "source": "事实卡", "priority": 8, "limit": 120, "max_items": 1},
    "gap": {"label": "证据缺口", "class_name": "event", "source": "事实卡", "priority": 9, "limit": 110, "max_items": 3},
    "core": {"label": "核心证据", "class_name": "stock", "source": "过滤后证据", "priority": 9, "limit": 130, "max_items": 3},
    "impact": {"label": "影响要素", "class_name": "impact", "source": "模型判断", "priority": 10, "limit": 130, "max_items": 3},
    "summary": {"label": "异步总结", "class_name": "summary", "source": "模型总结", "priority": 20, "limit": 120, "max_items": 1},
    "realtime": {"label": "实时判断", "class_name": "theme", "source": "实时扫描", "priority": 25, "limit": 160, "max_items": 1},
    "theme": {"label": "题材证据", "class_name": "theme", "source": "题材解释", "priority": 30, "limit": 160, "max_items": 2},
    "stock": {"label": "个股证据", "class_name": "stock", "source": "个股解释", "priority": 40, "limit": 160, "max_items": 2},
    "announcement": {"label": "公告", "class_name": "announcement", "source": "公告", "priority": 80, "limit": 120, "max_items": 1},
    "event": {"label": "事件", "class_name": "event", "source": "事件", "priority": 90, "limit": 120, "max_items": 1},
}

LABEL_TYPE = {
    "关键事实": "facts",
    "异动解释": "move",
    "异动质量": "quality",
    "区间领头": "period",
    "主动性": "initiative",
    "带动性": "influence",
    "龙虎榜席位": "lhb",
    "锚点一致性": "anchor",
    "核心支撑": "support",
    "持续依据": "support",
    "瑕疵": "counter",
    "核心结论": "final",
    "时效判断": "timeliness",
    "最大瑕疵": "flaw",
    "证据缺口": "gap",
    "核心证据": "core",
    "影响要素": "impact",
    "异步总结": "summary",
    "实时判断": "realtime",
    "题材证据": "theme",
    "题材": "theme",
    "个股证据": "stock",
    "公告": "announcement",
    "事件": "event",
}

ASYNC_DECISION_PRIORITY = {
    "关键事实": 0,
    "龙虎榜席位": 1,
    "区间领头": 2,
    "主动性": 3,
    "带动性": 4,
    "最大瑕疵": 4,
    "持续依据": 5,
    "证据缺口": 5,
    "持续性": 6,
    "异动解释": 7,
    "核心证据": 20,
    "影响要素": 21,
    "核心支撑": 30,
    "瑕疵": 31,
    "时效判断": 32,
    "核心结论": 40,
    "异动质量": 41,
    "锚点一致性": 42,
    "异步总结": 50,
}

USEFUL_DECISION_LABELS = {"关键事实", "龙虎榜席位", "区间领头", "主动性", "带动性", "持续依据", "最大瑕疵", "证据缺口", "持续性"}

REALTIME_LABELS = {"实时判断", "区间领头", "主动性", "带动性"}
JUDGEMENT_REALTIME_LABELS = {"持续性", "核心支撑"}
REALTIME_SOURCE_PREFIXES = ("实时扫描", "扫描触发", "同锚扩散", "问财区间排名")
REALTIME_DIRECT_SOURCES = {"题材解释", "个股解释"}


def clean(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", "").split())


def clean_body(value: Any) -> str:
    lines = [clean(line) for line in str(value or "").replace("\r", "").splitlines()]
    return "\n".join(line for line in lines if line)


def clamp_text(value: Any, limit: int) -> str:
    text = clean(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "..."


def parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def meta_for(label: str = "", evidence_type: str = "") -> dict[str, Any]:
    return TYPE_META.get(evidence_type or LABEL_TYPE.get(label, ""), TYPE_META["event"])


def normalize_layer(value: Any) -> str:
    text = clean(value)
    if text in {"async", "异步证据", "异步补充证据"}:
        return "异步证据"
    return "实时证据"


def display_layer(origin_layer: str, label: str, source: str, evidence_type: str) -> str:
    if label in REALTIME_LABELS:
        return "实时证据"
    if label in JUDGEMENT_REALTIME_LABELS and source.startswith("判断引擎"):
        return "实时证据"
    if evidence_type == "realtime":
        return "实时证据"
    if source in REALTIME_DIRECT_SOURCES and origin_layer == "实时证据":
        return "实时证据"
    if any(source.startswith(prefix) for prefix in REALTIME_SOURCE_PREFIXES):
        return "实时证据"
    return origin_layer


def normalize_item(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    label = clean(item.get("label"))
    body = clean_body(item.get("body") or item.get("text"))
    if not label or not body:
        return None
    evidence_type = clean(item.get("type") or LABEL_TYPE.get(label, "event"))
    meta = meta_for(label, evidence_type)
    origin_layer = normalize_layer(item.get("layer"))
    source = clean(item.get("source") or meta["source"])
    return {
        "layer": display_layer(origin_layer, label, source, evidence_type),
        "origin_layer": origin_layer,
        "label": label,
        "type": evidence_type,
        "source": source,
        "body": body,
        "payload": item.get("payload"),
        "priority": int(float(item.get("priority", meta["priority"]) or meta["priority"])),
        "class_name": clean(item.get("class_name") or item.get("className") or meta["class_name"]),
    }


def normalize_detail_raw(row: dict[str, Any]) -> str:
    raw = str(row.get("detail") or "").replace("\r", "").strip()
    raw = re.sub(r"^【亮点】[^\n]*(\n|$)", "", raw)
    marker = re.search(r"【实时证据】|【异步证据】|异步总结：|影响要素：|题材证据：|个股证据：|事件：|公告：|题材：", raw)
    if not marker:
        return ""
    return re.sub(r"\n{3,}", "\n\n", raw[marker.start() :]).strip()


def parse_detail_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = normalize_detail_raw(row)
    if not raw:
        return []
    parts: list[dict[str, Any]] = []
    pattern = re.compile(r"(【实时证据】|【异步证据】)|(异步总结|影响要素|题材证据|个股证据|事件|公告|题材)：")
    matches = list(pattern.finditer(raw))
    current_layer = "实时证据"
    for index, match in enumerate(matches):
        if match.group(1):
            current_layer = match.group(1).replace("【", "").replace("】", "")
            continue
        label = match.group(2) or ""
        end = len(raw)
        for next_match in matches[index + 1 :]:
            end = next_match.start()
            break
        body = clean_body(raw[match.end() : end].replace("【实时证据】", "").replace("【异步证据】", ""))
        normalized = normalize_item({"layer": current_layer, "label": label, "body": body})
        if normalized:
            parts.append(normalized)
    return parts


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (item["layer"], item["label"], item["body"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    structured = [item for item in (normalize_item(raw) for raw in parse_json_array(row.get("evidence_items"))) if item]
    return dedupe_items(structured or parse_detail_items(row))


def item_priority(item: dict[str, Any]) -> int:
    if item.get("layer") == "异步证据" and item.get("label") in ASYNC_DECISION_PRIORITY:
        return ASYNC_DECISION_PRIORITY[str(item["label"])]
    try:
        return int(float(item.get("priority", 100)))
    except Exception:
        return int(meta_for(str(item.get("label") or ""), str(item.get("type") or "")).get("priority", 100))


def useful_decision_item(item: dict[str, Any]) -> bool:
    label = str(item.get("label") or "")
    body = str(item.get("body") or "")
    if label not in USEFUL_DECISION_LABELS:
        return False
    if label == "持续性":
        return bool(re.search(r"走弱|风险-[1-9]", body))
    if label == "证据缺口":
        return bool(clean(body))
    return True


def concise_body(item: dict[str, Any]) -> str:
    meta = meta_for(str(item.get("label") or ""), str(item.get("type") or ""))
    lines = []
    for line in str(item.get("body") or "").splitlines():
        text = clean(line)
        if text and text not in lines:
            lines.append(text)
    max_items = int(meta.get("max_items", 1))
    limit = int(meta.get("limit", 120))
    if max_items <= 1:
        return clamp_text("；".join(lines), limit)
    return "\n".join(clamp_text(line, limit) for line in lines[:max_items])


def curate_items(items: list[dict[str, Any]], layer: str) -> list[dict[str, Any]]:
    scoped = [item for item in items if item.get("layer") == layer]
    if layer == "异步证据":
        decision_items = [item for item in scoped if useful_decision_item(item)]
        if decision_items:
            return prepare_sections(decision_items, 6)
        has_impact = any(item.get("label") == "影响要素" for item in scoped)
        has_core = any(item.get("label") == "核心证据" for item in scoped)
        has_judgement = has_impact or any(item.get("label") == "异步总结" for item in scoped)
        if has_impact:
            scoped = [item for item in scoped if item.get("label") != "异步总结"]
        if has_core or has_impact:
            scoped = [item for item in scoped if item.get("label") not in {"核心结论", "异动质量", "锚点一致性"}]
        if has_judgement:
            scoped = [item for item in scoped if item.get("label") not in {"公告", "事件", "题材"}]
    return prepare_sections(scoped, 6 if layer == "异步证据" else 5)


def prepare_sections(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    sections = []
    for item in sorted(items, key=item_priority):
        body = concise_body(item)
        if not body:
            continue
        section = dict(item)
        section["body"] = body
        sections.append(section)
        if len(sections) >= limit:
            break
    return sections


def first_line(item: dict[str, Any] | None, limit: int = 96) -> str:
    if not item:
        return ""
    for line in str(item.get("body") or "").splitlines():
        text = clamp_text(line, limit)
        if text:
            return text
    return ""


def line_with_prefix(item: dict[str, Any] | None, prefix: str) -> str:
    if not item:
        return ""
    payload = item.get("payload")
    values = list(payload) if isinstance(payload, list) else []
    values.extend(str(item.get("body") or "").splitlines())
    for value in values:
        if isinstance(value, str):
            text = clean(value)
        elif isinstance(value, dict):
            text = clean(value.get("reason") or value.get("evidence"))
        else:
            text = ""
        if text.startswith(prefix):
            return text
    return ""


def strip_prefix(value: str) -> str:
    text = clean(value)
    return clean(text.split("：", 1)[1]) if "：" in text else text


def fact_text(item: Any) -> str:
    if isinstance(item, str):
        return clamp_text(item, 92)
    if not isinstance(item, dict):
        return ""
    reason = clean(item.get("reason") or item.get("evidence"))
    title = clean(item.get("title") or item.get("factor_type"))
    date = clean(item.get("source_date"))
    text = reason or title
    if title and reason and title not in reason and len(title) <= 18 and not re.search(r"公告|报告|披露|资料|龙虎榜", reason):
        text = f"{title}：{reason}"
    return clamp_text(f"{date + ' ' if date else ''}{text}", 92)


def summary_facts(items: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []

    def add(value: Any) -> None:
        text = clamp_text(value, 92)
        if not text:
            return
        if re.search(r"逻辑|较硬|推论|判断|解释强度", text) and not re.search(r"\d|同比|净买|订单|合同|客户|供货|中标|业绩|净利|营收|龙虎榜", text):
            return
        if not any(item == text or item in text or text in item for item in facts):
            facts.append(text)

    by_label = {str(item.get("label")): item for item in items}
    for label, count in (("关键事实", 3), ("区间领头", 2), ("龙虎榜席位", 2)):
        item = by_label.get(label)
        if isinstance(item.get("payload") if item else None, list):
            for value in item["payload"][:count]:
                add(fact_text(value))
        if item:
            for line in str(item.get("body") or "").splitlines()[:count]:
                add(line)
    for label, count in (("核心证据", 5), ("影响要素", 5), ("核心支撑", 3)):
        item = by_label.get(label)
        payload = item.get("payload") if item else None
        if isinstance(payload, list):
            for value in payload[:count]:
                add(fact_text(value))
    return facts[:3]


def evidence_level(items: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, str]:
    tags = parse_json_array(row.get("tags"))
    tag_texts = [clean(str(tag).split("|", 1)[0]) for tag in tags]
    explicit = next((tag.removeprefix("证据:") for tag in tag_texts if tag.startswith("证据:")), "")
    raw = str(row.get("detail") or "")
    has_announcement = bool(re.search(r"公告|年报|半年报|互动易|问询|回复", raw))
    has_direct = any(item.get("label") in {"题材证据", "个股证据"} for item in items)
    has_impact = any(item.get("label") == "影响要素" for item in items)
    has_realtime = any(item.get("layer") == "实时证据" for item in items)
    has_async = any(item.get("layer") == "异步证据" for item in items)
    source = f"{'实时链路' if has_realtime else ''}{' + ' if has_realtime and has_async else ''}{'异步补充' if has_async else ''}"
    if explicit and explicit != "pending":
        return {"level": explicit, "level_class": "strong" if "强" in explicit else "", "basis": f"{source} · 来自证据层标记"}
    if has_impact:
        return {"level": "强", "level_class": "strong", "basis": f"{source} · 已提取影响股价要素"}
    if has_announcement and has_direct:
        return {"level": "强", "level_class": "strong", "basis": f"{source} · 公司披露/互动易 + 题材解释"}
    if has_direct or items:
        return {"level": "中", "level_class": "", "basis": f"{source} · 题材/个股解释"}
    return {"level": "待补全", "level_class": "weak", "basis": "暂无可核验证据"}


def clean_judgement(value: str) -> str:
    return clean(value).removeprefix("最强解释是").removeprefix("异动主要由").removeprefix("异动主要因").removeprefix("因").removesuffix("。")


def build_summary(items: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    by_label = {str(item.get("label")): item for item in items}
    level = evidence_level(items, row)
    move = by_label.get("异动解释")
    final = by_label.get("核心结论")
    impact = by_label.get("影响要素")
    judgement = clean_judgement(first_line(move, 90) or first_line(final, 90) or first_line(impact, 90))
    quality = by_label.get("持续性")
    influence = by_label.get("带动性")
    sustain = by_label.get("持续依据") or by_label.get("区间领头")
    lhb = first_line(by_label.get("龙虎榜席位"), 90)
    initiative = first_line(by_label.get("主动性"), 90)
    tape = "；".join(value for value in [lhb, initiative] if value)[:120]
    flaw = first_line(by_label.get("最大瑕疵"), 90)
    first_gap = first_line(by_label.get("证据缺口"), 90)
    spread = strip_prefix(line_with_prefix(influence, "扩散"))
    gap = ""
    if level["level"] == "待补全":
        gap = "建议补充：公告、互动易、定期报告或同花顺题材解释。"
    elif not (flaw or first_gap or impact or any(item.get("label") in {"公告", "事件"} for item in items)):
        gap = "可继续补充公告/互动易作为硬证据。"
    return {
        "title": "异动原因" if judgement else "关键事实" if summary_facts(items) else "证据强度",
        "level": level["level"],
        "level_class": level["level_class"],
        "reason": judgement,
        "cards": [
            {"label": "持续性", "value": first_line(quality) or level["level"], "tone": ""},
            {"label": "带动性", "value": strip_prefix(first_line(influence)), "tone": ""},
            {"label": "启动前", "value": strip_prefix(line_with_prefix(influence, "本股启动前")), "tone": ""},
            {"label": "首发", "value": strip_prefix(line_with_prefix(influence, "首发质量")), "tone": ""},
        ],
        "chips": [
            {"label": "扩散", "value": spread, "tone": "good" if "强扩散" in spread else "warn" if "弱扩散" in spread else "weak"},
            {"label": "持续", "value": first_line(sustain), "tone": "good"},
            {"label": "盘口/资金", "value": tape, "tone": "weak"},
        ],
        "facts": summary_facts(items),
        "flaw": flaw,
        "gap": first_gap or gap,
        "basis": level["basis"],
    }


def build_evidence_view(row: dict[str, Any]) -> dict[str, Any]:
    items = evidence_items(row)
    layers = []
    for layer_id in ("realtime", "async"):
        meta = LAYER_META[layer_id]
        sections = curate_items(items, meta["key"])
        if sections:
            layers.append(
                {
                    "layer": layer_id,
                    "title": meta["title"],
                    "hint": meta["hint"],
                    "class_name": meta["class_name"],
                    "sections": sections,
                }
            )
    return {
        "schema_version": EVIDENCE_VIEW_VERSION,
        "summary": build_summary(items, row),
        "layers": layers,
    }
