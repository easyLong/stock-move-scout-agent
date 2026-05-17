from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from typing import Any


EVIDENCE_VIEW_VERSION = 8

CURRENT_GROUPS = {"current_effective"}
VISIBLE_GROUPS = ("model_summary", "current_effective", "post_close_confirm", "background_fact", "historical_tag", "unknown")
BACKGROUND_SOURCE_TABLES = {"ths_stock_concept_explanations", "stock_company_profiles"}
AFTER_CLOSE_SOURCE_TABLES: set[str] = set()
INTRADAY_SOURCE_TABLES = {"scan_runs", "scan_movers", "scan_stock_roles", "windows", "window_movers", "window_stock_roles"}
MODEL_SOURCE_TABLES = {"async_evidence_summaries"}
HIDDEN_STATUSES = {"invalid"}
STALE_STATUSES = {"expired", "historical"}

LAYER_META: dict[str, tuple[str, str, str]] = {
    "model_summary": ("有效事实总结", "只基于至今仍有效的事实生成；没有有效事实不会生成本层", "model-summary"),
    "current_effective": ("今日有效", "盘中已经能直接解释今日异动的触发、硬催化和判断结果", "current-effective"),
    "post_close_confirm": ("盘后确认", "龙虎榜、涨停池、盘后市场背景等盘后或上一交易日确认材料", "after-close"),
    "background_fact": ("辅助背景", "题材归因、公司画像和长期事实，只辅助理解，不直接解释今日异动", "background-fact"),
    "historical_tag": ("历史/失效", "已验证但时效走弱、价格影响消退或仅作历史标签保留", "historical-tag"),
    "unknown": ("待核来源", "模型旧摘要或缺少明确有效性标记的材料，默认折叠", "async"),
}

TYPE_META: dict[str, tuple[str, str, str, int, int, int]] = {
    "facts": ("有效事实总结", "stock", "事实卡", 0, 120, 3),
    "move": ("异动解释", "summary", "模型解释", 1, 100, 1),
    "quality": ("异动质量", "event", "模型判断", 2, 100, 1),
    "period": ("区间领头", "theme", "研究池区间结构", 3, 120, 3),
    "initiative": ("主动性", "theme", "扫描触发", 3, 120, 3),
    "influence": ("带动性", "event", "同锚扩散", 4, 140, 8),
    "lhb": ("龙虎榜席位", "event", "东方财富龙虎榜", 3, 130, 4),
    "anchor": ("锚点一致性", "theme", "模型判断", 3, 120, 1),
    "support": ("核心支撑", "stock", "模型筛选", 4, 110, 2),
    "counter": ("瑕疵", "announcement", "模型判断", 6, 110, 1),
    "final": ("核心结论", "summary", "模型结论", 5, 100, 1),
    "timeliness": ("时效判断", "event", "模型判断", 8, 120, 1),
    "flaw": ("最大瑕疵", "announcement", "事实卡", 8, 120, 1),
    "gap": ("证据缺口", "event", "事实卡", 9, 110, 3),
    "core": ("核心证据", "stock", "过滤后证据", 9, 130, 3),
    "impact": ("影响要素", "impact", "模型判断", 10, 130, 3),
    "summary": ("异步总结", "summary", "模型总结", 20, 120, 1),
    "realtime": ("实时判断", "theme", "实时扫描", 25, 160, 1),
    "theme": ("题材证据", "theme", "题材解释", 70, 160, 2),
    "stock": ("个股证据", "stock", "个股解释", 40, 160, 2),
    "announcement": ("公告", "announcement", "公告", 5, 140, 1),
    "event": ("事件", "event", "事件", 90, 120, 1),
}

LABEL_TYPE = {
    "关键事实": "facts",
    "有效事实总结": "facts",
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
    "题材背景": "theme",
    "题材": "theme",
    "个股证据": "stock",
    "当前硬催化": "announcement",
    "公告": "announcement",
    "历史标签": "event",
    "事件": "event",
}

HARD_TYPES = {"announcement", "impact", "core"}
STRUCTURE_TYPES = {"period", "lhb", "initiative", "influence", "support", "stock", "theme"}
DECISION_TYPES = HARD_TYPES | STRUCTURE_TYPES | {"facts", "move", "quality", "anchor", "final", "timeliness", "flaw", "gap", "summary"}
HARD_KEYWORDS = re.compile(r"净利|营收|同比|订单|合同|中标|客户|供货|公告|互动易|财报|季报|年报|龙虎榜|机构|股通|游资|锚点内")


def clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\u3000", " ")).strip()


def clamp_text(value: Any, limit: int) -> str:
    text = clean(value)
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"


def parse_json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def parse_date(value: Any) -> date | None:
    text = clean(value)
    match = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def parse_datetime(value: Any) -> datetime | None:
    text = clean(value)
    for fmt, length in (("%Y-%m-%d %H:%M:%S.%f", 26), ("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def event_date(row: dict[str, Any]) -> date | None:
    return parse_date(row.get("event_time") or row.get("trade_date"))


def previous_trade_day(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def type_for(label: str, evidence_type: str) -> str:
    return clean(evidence_type) or LABEL_TYPE.get(clean(label), "event")


def meta_for(label: str, evidence_type: str) -> tuple[str, str, str, int, int, int]:
    return TYPE_META.get(type_for(label, evidence_type), TYPE_META["event"])


def payload_dict(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else {}


def normalize_item(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        text = clean(raw)
        return {"label": "事件", "type": "event", "source": "文本", "body": text, "priority": 100} if text else {}

    item = dict(raw)
    payload = payload_dict(item)
    label = clean(item.get("label"))
    evidence_type = type_for(label, clean(item.get("type")))
    meta = meta_for(label, evidence_type)

    item["type"] = evidence_type
    item["label"] = label or meta[0]
    item["source"] = clean(item.get("source")) or meta[2]
    item["body"] = clean(item.get("body") or item.get("text") or item.get("summary") or item.get("reason"))

    for key in (
        "source_table",
        "source_key",
        "source_confidence",
        "evidence_group",
        "evidence_role",
        "display_level",
        "valid_status",
        "availability",
        "freshness",
        "source_generation",
        "valid_reason",
    ):
        item[key] = clean(item.get(key) or payload.get(key))

    item["data_date"] = clean(item.get("data_date") or item.get("evidence_date") or item.get("source_date") or payload.get("fact_date"))
    item["evidence_date"] = clean(item.get("evidence_date") or item.get("data_date") or item.get("source_date") or payload.get("fact_date"))
    item["updated_at"] = clean(item.get("updated_at") or item.get("collected_at") or item.get("summarized_at"))
    item["source_registry"] = item.get("source_registry") if isinstance(item.get("source_registry"), dict) else {}
    try:
        item["valid_score"] = float(item.get("valid_score") or payload.get("valid_score") or 0)
    except Exception:
        item["valid_score"] = 0.0
    try:
        item["priority"] = int(float(item.get("priority", meta[3])))
    except Exception:
        item["priority"] = int(meta[3])
    return item if item["body"] else {}


def infer_group(item: dict[str, Any]) -> str:
    explicit = clean(item.get("evidence_group"))
    if explicit in VISIBLE_GROUPS or explicit == "hidden":
        return explicit

    source_table = clean(item.get("source_table"))
    valid_status = clean(item.get("valid_status"))
    display_level = clean(item.get("display_level"))
    freshness = clean(item.get("freshness"))
    availability = clean(item.get("availability"))
    evidence_type = clean(item.get("type"))

    if valid_status in HIDDEN_STATUSES or display_level == "hidden":
        return "hidden"
    if valid_status in STALE_STATUSES or freshness == "historical":
        return "historical_tag"
    if source_table in BACKGROUND_SOURCE_TABLES or display_level == "background":
        return "background_fact"
    if source_table in AFTER_CLOSE_SOURCE_TABLES or availability == "after_close_confirm":
        return "post_close_confirm"
    if source_table in INTRADAY_SOURCE_TABLES or availability == "intraday":
        return "current_effective"
    if source_table in MODEL_SOURCE_TABLES or availability == "async_supplement":
        return "model_summary"
    if evidence_type in {"theme", "stock"}:
        return "background_fact"
    return "unknown"


def enrich_item(item: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(item)
    group = infer_group(enriched)
    enriched["evidence_group"] = group
    if not enriched.get("availability"):
        if group == "current_effective":
            enriched["availability"] = "intraday"
        elif group == "post_close_confirm":
            enriched["availability"] = "after_close_confirm"
        elif group in {"background_fact", "historical_tag"}:
            enriched["availability"] = "cached_readable"
        else:
            enriched["availability"] = "async_supplement"

    row_date = event_date(row)
    data_date = parse_date(enriched.get("data_date") or enriched.get("evidence_date"))
    if group == "background_fact":
        enriched["data_relation_label"] = "背景"
        enriched["data_date"] = ""
        enriched["evidence_date"] = ""
    elif not data_date or not row_date:
        enriched["data_relation_label"] = "待核数据日"
    elif data_date == row_date:
        enriched["data_relation_label"] = "当日"
    elif data_date == previous_trade_day(row_date):
        enriched["data_relation_label"] = "上一交易日"
    elif data_date < row_date:
        enriched["data_relation_label"] = "历史"
    else:
        enriched["data_relation_label"] = "未来数据"
    return enriched


def dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = "|".join(
            [
                clean(item.get("source_table")),
                clean(item.get("source_key")),
                clean(item.get("label")),
                clean(item.get("body"))[:80],
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def is_deprecated_rank_item(item: dict[str, Any]) -> bool:
    label = clean(item.get("label"))
    evidence_type = clean(item.get("type"))
    source = clean(item.get("source"))
    source_table = clean(item.get("source_table"))
    body = clean(item.get("body"))
    return (
        source_table == "stock_period_rankings"
        or evidence_type == "period"
        or label == "区间领头"
        or "问财" in source
        or "问财" in body
    )


def evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    items = [normalize_item(raw) for raw in parse_json_array(row.get("evidence_items"))]
    enriched = [enrich_item(item, row) for item in dedupe_items([item for item in items if item])]
    return [item for item in enriched if item.get("evidence_group") != "hidden" and not is_deprecated_rank_item(item)]


def decision_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if infer_group(item) not in CURRENT_GROUPS:
            continue
        if clean(item.get("source_table")) in BACKGROUND_SOURCE_TABLES | MODEL_SOURCE_TABLES:
            continue
        if clean(item.get("display_level")) in {"background", "hidden"}:
            continue
        if clean(item.get("valid_status")) in STALE_STATUSES | HIDDEN_STATUSES:
            continue
        if clean(item.get("label")) == "实时判断":
            continue
        if clean(item.get("type")) not in DECISION_TYPES:
            continue
        out.append(item)
    return out


def item_priority(item: dict[str, Any]) -> int:
    return int(item.get("priority", meta_for(clean(item.get("label")), clean(item.get("type")))[3]))


def concise_body(item: dict[str, Any]) -> str:
    meta = meta_for(clean(item.get("label")), clean(item.get("type")))
    lines: list[str] = []
    for line in str(item.get("body") or "").splitlines():
        text = clean(line)
        if text and text not in lines:
            lines.append(text)
    if int(meta[5]) <= 1:
        return clamp_text("；".join(lines), int(meta[4]))
    return "\n".join(clamp_text(line, int(meta[4])) for line in lines[: int(meta[5])])


def prepare_sections(items: list[dict[str, Any]], group_id: str) -> list[dict[str, Any]]:
    limits = {"model_summary": 4, "current_effective": 8, "post_close_confirm": 6, "background_fact": 4, "historical_tag": 3, "unknown": 3}
    if group_id == "current_effective" and any(clean(item.get("label")) == "头条题材角色" for item in items):
        items = [item for item in items if clean(item.get("label")) != "领涨中军"]
    sections: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: (item_priority(value), -float(value.get("valid_score") or 0))):
        body = concise_body(item)
        if not body:
            continue
        section = dict(item)
        section["body"] = body
        section["is_decision_candidate"] = group_id in CURRENT_GROUPS and clean(section.get("display_level")) != "background"
        if group_id in {"background_fact", "historical_tag", "unknown"}:
            section["collapsed"] = True
        sections.append(section)
        if len(sections) >= limits.get(group_id, 5):
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


def first_matching(items: list[dict[str, Any]], types: set[str], pattern: re.Pattern[str] | None = None) -> dict[str, Any] | None:
    for item in sorted(items, key=item_priority):
        if clean(item.get("type")) not in types:
            continue
        if pattern and not pattern.search(clean(item.get("body"))):
            continue
        return item
    return None


def summary_facts(items: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    for item in sorted(items, key=item_priority):
        text = first_line(item, 92)
        if text and not any(text in old or old in text for old in facts):
            facts.append(text)
        if len(facts) >= 3:
            break
    return facts


def evidence_level(items: list[dict[str, Any]]) -> dict[str, str]:
    if any(clean(item.get("type")) in HARD_TYPES for item in items):
        return {"level": "强", "level_class": "strong", "basis": "只统计今日有效和盘后确认，不统计背景、历史和待核来源"}
    if any(clean(item.get("type")) in STRUCTURE_TYPES | {"move", "final", "facts"} for item in items):
        return {"level": "中", "level_class": "", "basis": "只统计今日有效和盘后确认，不统计背景、历史和待核来源"}
    return {"level": "待补全", "level_class": "weak", "basis": "缺少可解释今日异动的有效证据"}


def build_summary(items: list[dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    scoped = decision_items(items)
    by_type = {clean(item.get("type")): item for item in scoped}
    by_label = {clean(item.get("label")): item for item in scoped}
    level = evidence_level(scoped)
    reason = first_line(by_type.get("move") or by_type.get("final") or by_type.get("impact"), 90)
    hard = first_matching(scoped, HARD_TYPES, HARD_KEYWORDS) or first_matching(scoped, HARD_TYPES)
    funds = by_type.get("lhb") or by_label.get("龙虎榜席位")
    strength = by_type.get("period") or by_type.get("initiative") or by_label.get("区间领头")
    influence = by_type.get("influence") or by_label.get("带动性")
    gap = first_line(by_type.get("gap") or by_label.get("证据缺口"), 90)
    if level["level"] == "待补全" and not gap:
        gap = "建议补充：当天公告、互动易、行业消息、同题材扩散或模型归因"
    return {
        "title": "异动原因" if reason else "关键事实" if summary_facts(scoped) else "证据强度",
        "level": level["level"],
        "level_class": level["level_class"],
        "reason": reason,
        "cards": [
            {"label": "硬证据", "value": first_line(hard, 86), "tone": "good" if hard else ""},
            {"label": "资金确认", "value": first_line(funds, 86), "tone": "hot" if funds else ""},
            {"label": "强度位置", "value": first_line(strength, 86), "tone": "rank" if strength else ""},
            {"label": "带动性", "value": first_line(influence, 86), "tone": ""},
        ],
        "chips": [],
        "facts": summary_facts(scoped),
        "flaw": first_line(by_type.get("flaw") or by_label.get("最大瑕疵"), 90),
        "gap": gap,
        "basis": level["basis"],
    }


def source_tokens(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    tokens: dict[str, dict[str, str]] = {}
    for item in items:
        token = clean(item.get("updated_at") or item.get("data_date") or item.get("evidence_date"))
        key = f"{clean(item.get('source_table'))}|{clean(item.get('source_key'))}|{clean(item.get('label'))}|{token}"
        tokens[key] = {
            "source_table": clean(item.get("source_table")),
            "source_key": clean(item.get("source_key")),
            "source_confidence": clean(item.get("source_confidence")),
            "label": clean(item.get("label")),
            "availability": clean(item.get("availability")),
            "evidence_group": clean(item.get("evidence_group")),
            "valid_status": clean(item.get("valid_status")),
            "display_level": clean(item.get("display_level")),
            "data_date": clean(item.get("data_date") or item.get("evidence_date")),
            "updated_at": clean(item.get("updated_at")),
            "updated_token": token,
        }
    return sorted(tokens.values(), key=lambda value: (value["source_table"], value["source_key"], value["label"]))


def evidence_version(tokens: list[dict[str, str]], row: dict[str, Any]) -> str:
    payload = {
        "schema_version": EVIDENCE_VIEW_VERSION,
        "row": {"kind": clean(row.get("kind")), "code": clean(row.get("code")), "event_time": clean(row.get("event_time"))},
        "tokens": tokens,
    }
    return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]


def latest_source_updated_at(items: list[dict[str, Any]], row: dict[str, Any]) -> str:
    candidates = [clean(row.get("latest_source_updated_at"))]
    candidates.extend(clean(item.get("updated_at")) for item in items)
    parsed = [(parse_datetime(value), value) for value in candidates if value]
    parsed = [(dt, value) for dt, value in parsed if dt]
    return max(parsed, key=lambda pair: pair[0])[1] if parsed else ""


def build_evidence_view(row: dict[str, Any]) -> dict[str, Any]:
    items = evidence_items(row)
    tokens = source_tokens(items)
    layers = []
    for group_id in VISIBLE_GROUPS:
        group_items = [item for item in items if infer_group(item) == group_id]
        sections = prepare_sections(group_items, group_id)
        if not sections:
            continue
        title, hint, class_name = LAYER_META[group_id]
        layers.append(
            {
                "layer": group_id,
                "availability": group_id,
                "availability_label": title,
                "freshness": "live_market" if group_id == "current_effective" else "today_update" if group_id == "post_close_confirm" else "historical",
                "freshness_label": title,
                "title": title,
                "hint": hint,
                "class_name": class_name,
                "sections": sections,
            }
        )
    return {
        "schema_version": EVIDENCE_VIEW_VERSION,
        "evidence_version": evidence_version(tokens, row),
        "latest_source_updated_at": latest_source_updated_at(items, row),
        "source_tokens": tokens,
        "summary": build_summary(items, row),
        "layers": layers,
    }
