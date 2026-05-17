#!/usr/bin/env python
"""
Build a structured evidence layer for stock mover scouting.

This script does not fetch new slow sources. It normalizes existing fast market
signals and community evidence into one reviewable table, so later sources
such as announcements, news, and capital flow can be added without changing the
downstream judgement shape.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_evidence_layer_rows,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_string,
)


COLUMNS = [
    "built_at",
    "captured_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "price",
    "speed",
    "pct_change",
    "amount",
    "industry",
    "sub_industry",
    "concepts",
    "market_evidence",
    "sector_evidence",
    "hard_evidence_summary",
    "hard_evidence_strength",
    "hard_catalyst_types",
    "stone_evidence_summary",
    "order_cooperation_evidence",
    "order_cooperation_hard_evidence",
    "amount_terms_evidence",
    "partner_customer_evidence",
    "financial_evidence",
    "ma_evidence",
    "risk_evidence",
    "hard_evidence_gap",
    "supplemental_strength",
    "supplemental_summary",
    "news_evidence",
    "irm_evidence",
    "official_news_evidence",
    "order_cooperation_supplement",
    "customer_partner_supplement",
    "amount_terms_supplement",
    "supplemental_evidence_gap",
    "company_evidence",
    "company_positioning",
    "official_website",
    "official_products",
    "official_status",
    "community_main_claim",
    "community_trigger_claim",
    "community_trigger_event",
    "community_trigger_timing",
    "community_imagination_path",
    "community_verification_anchor",
    "community_evidence_type",
    "community_support_points",
    "community_disagreements",
    "community_risk_flags",
    "community_verification_need",
    "community_signal_quality",
    "community_evidence",
    "community_status",
    "community_hot_terms",
    "community_post_count",
    "why_hypothesis",
    "evidence_strength",
    "evidence_gaps",
    "next_evidence_action",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def market_symbol(code: str) -> str:
    return f"SH{code}" if code.startswith(("6", "9")) else f"SZ{code}"


def to_float(value: str) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def short_list(value: str, limit: int = 6) -> str:
    parts = [part.strip() for part in re.split(r"[,，、;；]", value or "") if part.strip()]
    return "、".join(parts[:limit])


def compact_text(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit]


def by_code(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        code = row.get("code", "").strip()
        if code and code not in result:
            result[code] = row
    return result


def parse_json_text(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def decode_hex_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        text = bytes.fromhex(value).decode("utf-8")
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def list_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str):
        parsed = parse_json_text(value)
        if isinstance(parsed, list):
            return list_text(parsed)
        return value
    return str(value)


def first_nonempty(*values: Any) -> str:
    for value in values:
        text = "" if value is None else str(value).strip()
        if text:
            return text
    return ""


def strength_label(value: str) -> str:
    mapping = {
        "strong": "强证据",
        "medium": "中等证据",
        "weak": "弱证据",
        "missing": "未采集",
        "pending": "待补证据",
    }
    return mapping.get((value or "").lower(), value or "未采集")


def latest_mysql_window_id(config: Any) -> str:
    rows = mysql_rows(
        run_mysql(
            config,
            """
            SELECT window_id
            FROM windows
            WHERE status='done'
            ORDER BY ended_at DESC
            LIMIT 1;
            """,
            batch=True,
        )
    )
    return rows[0][0] if rows and rows[0] else ""


def mysql_top_rows(config: Any, window_id: str, limit: int) -> list[dict[str, str]]:
    sql = f"""
    SELECT
      DATE_FORMAT(w.ended_at, '%Y-%m-%d %H:%i:%s') AS captured_at,
      wm.rank_no,
      '' AS rank_pct_change,
      wm.code,
      wm.name,
      COALESCE(wm.latest_price, '') AS price,
      COALESCE(wm.max_speed, '') AS speed,
      COALESCE(wm.latest_pct_change, '') AS pct_change,
      COALESCE(wm.amount, '') AS amount,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.industry')), s.industry, '') AS industry,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.sub_industry')), s.sub_industry, '') AS sub_industry,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.concepts')), '') AS concepts,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.basis')), 'mysql_window') AS basis
    FROM window_movers wm
    JOIN windows w ON w.id = wm.window_id
    LEFT JOIN stocks s ON s.code = wm.code
    WHERE w.window_id = {sql_string(window_id)}
    ORDER BY wm.rank_no
    LIMIT {int(limit)};
    """
    columns = [
        "captured_at",
        "rank_speed",
        "rank_pct_change",
        "code",
        "name",
        "price",
        "speed",
        "pct_change",
        "amount",
        "industry",
        "sub_industry",
        "concepts",
        "basis",
    ]
    return [dict(zip(columns, row)) for row in mysql_rows(run_mysql(config, sql, batch=True)) if len(row) >= len(columns)]


def mysql_community_rows(config: Any, window_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    sql = f"""
    SELECT
      ce.code,
      ce.status,
      ce.post_count,
      COALESCE(CAST(ce.hot_terms AS CHAR), ''),
      ce.main_claim,
      ce.trigger_claim,
      ce.trigger_event,
      ce.trigger_timing,
      COALESCE(ce.imagination_path, ''),
      COALESCE(ce.verification_anchor, ''),
      COALESCE(CAST(ce.support_points AS CHAR), ''),
      COALESCE(CAST(ce.disagreements AS CHAR), ''),
      COALESCE(CAST(ce.risk_flags AS CHAR), ''),
      ce.signal_quality,
      COALESCE(HEX(CAST(ce.raw_json AS CHAR)), '')
    FROM community_evidence ce
    JOIN windows w ON w.id = ce.window_id
    WHERE w.window_id = {sql_string(window_id)}
    ORDER BY ce.code;
    """
    community_rows: list[dict[str, str]] = []
    narrative_rows: list[dict[str, str]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 15:
            continue
        raw = decode_hex_json(row[14])
        narrative = raw.get("narrative") if isinstance(raw.get("narrative"), dict) else {}
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
        code = row[0]
        hot_terms = list_text(parse_json_text(row[3]) or row[3])
        support_points = list_text(parse_json_text(row[10]) or row[10])
        disagreements = list_text(parse_json_text(row[11]) or row[11])
        risk_flags = list_text(parse_json_text(row[12]) or row[12])
        main_claim = first_nonempty(row[4], narrative.get("community_main_claim"), evidence.get("community_explanation"))
        community_rows.append(
            {
                "code": code,
                "source_status": row[1] or "missing",
                "hot_post_count": row[2] or "0",
                "hot_terms": hot_terms,
                "sample_hot_posts": first_nonempty(evidence.get("sample_hot_posts"), main_claim),
                "community_explanation": main_claim,
            }
        )
        narrative_rows.append(
            {
                "code": code,
                "community_main_claim": main_claim,
                "community_trigger_claim": first_nonempty(row[5], narrative.get("community_trigger_claim")),
                "community_trigger_event": first_nonempty(row[6], narrative.get("community_trigger_event")),
                "community_trigger_timing": first_nonempty(row[7], narrative.get("community_trigger_timing")),
                "community_imagination_path": first_nonempty(row[8], narrative.get("community_imagination_path")),
                "community_verification_anchor": first_nonempty(row[9], narrative.get("community_verification_anchor")),
                "community_evidence_type": first_nonempty(narrative.get("community_evidence_type"), "社区叙事"),
                "community_support_points": support_points,
                "community_disagreements": disagreements,
                "community_risk_flags": risk_flags,
                "community_verification_need": first_nonempty(narrative.get("community_verification_need")),
                "community_signal_quality": row[13] or narrative.get("community_signal_quality", ""),
            }
        )
    return community_rows, narrative_rows


def mysql_company_rows(config: Any, window_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    sql = f"""
    SELECT
      p.code,
      p.stock_name,
      COALESCE(p.company_highlights, ''),
      COALESCE(p.main_business, ''),
      COALESCE(p.sw_industry, ''),
      COALESCE(p.concept_tags, ''),
      COALESCE(p.latest_management_business_plan, '')
    FROM stock_company_profiles p
    JOIN window_movers wm ON wm.code = p.code
    JOIN windows w ON w.id = wm.window_id
    WHERE w.window_id = {sql_string(window_id)}
    ORDER BY wm.rank_no;
    """
    official_rows: list[dict[str, str]] = []
    positioning_rows: list[dict[str, str]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 7:
            continue
        official_rows.append(
            {
                "code": row[0],
                "stock_name": row[1],
                "company_highlights": row[2],
                "main_business": row[3],
                "sw_industry": row[4],
                "concept_tags": row[5],
                "latest_management_business_plan": row[6],
                "source_status": "mysql_profile",
            }
        )
        positioning_rows.append(
            {
                "code": row[0],
                "company_positioning": first_nonempty(row[2], row[3], row[4], row[5], row[6]),
            }
        )
    return official_rows, positioning_rows


def classify_catalyst_types(text: str) -> str:
    rules = [
        ("订单/合作", ["订单", "合同", "中标", "合作", "客户", "供货", "采购", "协议"]),
        ("并购/重组", ["并购", "重组", "收购", "置入", "资产购买", "控制权"]),
        ("业绩/财务", ["业绩", "一季报", "半年报", "年报", "净利润", "营收", "分红", "分配"]),
        ("产品/技术", ["产品", "技术", "专利", "临床", "获批", "认证", "量产"]),
        ("股东/治理", ["股东", "增持", "减持", "回购", "董事", "股东大会"]),
        ("风险/诉讼", ["诉讼", "处罚", "风险", "问询", "监管", "终止"]),
        ("公告", ["公告", "披露"]),
    ]
    hits = [label for label, keywords in rules if any(keyword in text for keyword in keywords)]
    return "、".join(dict.fromkeys(hits[:4]))


def has_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def strip_stock_prefix(text: str) -> str:
    previous = None
    result = text.strip()
    while previous != result:
        previous = result
        result = re.sub(r"^[^：:]{2,16}[：:]", "", result).strip()
    return result


def format_root_item(row: dict[str, str], limit: int = 36) -> str:
    date = row.get("item_date", "")
    title = compact_text(row.get("title", ""), 90)
    content = compact_text(row.get("content", ""), 120)
    body = content if title in ("发布公告", "业绩披露", "分配预案", "异动提醒") and content else title
    body = re.sub(r"^《(.+?)》.*$", r"\1", body).strip()
    body = strip_stock_prefix(body)
    body = body.strip(" 《》")
    if date and len(date) >= 10:
        date = date[5:]
    return compact_text(f"{date} {body}".strip(), limit)


def summarize_root_items(items: list[dict[str, str]], prefix: str, limit: int = 5) -> str:
    pieces: list[str] = []
    seen: set[str] = set()
    for item in items:
        piece = format_root_item(item)
        if "�" in piece:
            continue
        key = re.sub(r"^\d{2}-\d{2}\s+", "", piece)
        if piece and key not in seen:
            pieces.append(piece)
            seen.add(key)
        if len(pieces) >= limit:
            break
    return f"{prefix}：" + "；".join(pieces) if pieces else ""


def mysql_ths_root_evidence_rows(config: Any, window_id: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    sql = f"""
    WITH ranked AS (
      SELECT
        i.code,
        i.item_kind,
        DATE_FORMAT(i.item_date, '%Y-%m-%d') AS item_date,
        i.title,
        COALESCE(i.content, '') AS content,
        i.url,
        COALESCE(CAST(i.tags AS CHAR), '') AS tags,
        i.source_rank,
        ROW_NUMBER() OVER (
          PARTITION BY i.code, i.item_kind
          ORDER BY i.item_date DESC, i.source_rank ASC, i.updated_at DESC
        ) AS rn
      FROM stock_ths_root_items i
      JOIN window_movers wm ON wm.code = i.code
      JOIN windows w ON w.id = wm.window_id
      WHERE w.window_id = {sql_string(window_id)}
        AND i.item_kind='important_event'
    )
    SELECT
      code, item_kind, item_date, title, content, url, tags, source_rank
    FROM ranked
    WHERE rn <= 5
    ORDER BY code,
      FIELD(item_kind, 'important_event'),
      item_date DESC,
      source_rank ASC;
    """
    by_stock: dict[str, dict[str, list[dict[str, str]]]] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 8:
            continue
        code, kind, item_date, title, content, url, tags, source_rank = row[:8]
        item = {
            "code": code,
            "item_kind": kind,
            "item_date": item_date,
            "title": title,
            "content": content,
            "url": url,
            "tags": tags,
            "source_rank": source_rank,
        }
        by_stock.setdefault(code, {}).setdefault(kind, []).append(item)

    hard_rows: list[dict[str, str]] = []
    supplemental_rows: list[dict[str, str]] = []
    strong_keywords = ["订单", "合同", "中标", "合作", "收购", "重组", "业绩", "净利润", "获批", "回购", "增持"]
    amount_keywords = ["元", "万元", "亿元", "金额", "收入", "利润", "产量", "订单", "合同"]
    for code, kinds in by_stock.items():
        important = kinds.get("important_event", [])
        routine_titles = {"融资融券", "股东大会", "股东人数变化", "投资互动", "发布公告"}
        signal_important = [item for item in important if item.get("title") not in routine_titles]
        if not signal_important:
            signal_important = important[:2]
        hard_items = signal_important
        hard_text = " ".join(first_nonempty(item.get("content"), item.get("title")) for item in hard_items)
        hard_summary = summarize_root_items(signal_important[:1], "事件", 3)
        if hard_summary:
            catalyst_types = classify_catalyst_types(hard_text)
            order_text = "；".join(format_root_item(item, 90) for item in hard_items if has_any(first_nonempty(item.get("content"), item.get("title")), ["订单", "合同", "中标", "合作", "客户", "协议"]))
            amount_text = "；".join(format_root_item(item, 90) for item in hard_items if has_any(first_nonempty(item.get("content"), item.get("title")), amount_keywords))
            risk_text = "；".join(format_root_item(item, 90) for item in hard_items if has_any(first_nonempty(item.get("content"), item.get("title")), ["诉讼", "处罚", "问询", "监管", "风险", "终止"]))
            hard_rows.append(
                {
                    "code": code,
                    "hard_catalyst_summary": hard_summary,
                    "hard_evidence_strength": "强硬证据" if has_any(hard_text, strong_keywords) else "中等硬证据",
                    "hard_catalyst_types": catalyst_types or "近期重要事件",
                    "stone_evidence_summary": summarize_root_items(important, "事件", 3),
                    "order_cooperation_evidence": compact_text(order_text, 260),
                    "order_cooperation_hard_evidence": compact_text(order_text, 420),
                    "amount_terms_evidence": compact_text(amount_text, 360),
                    "financial_evidence": compact_text(amount_text if has_any(hard_text, ["业绩", "净利润", "收入", "利润", "一季报", "年报"]) else "", 260),
                    "risk_evidence": compact_text(risk_text, 260),
                    "evidence_gap": "",
                }
            )
    return hard_rows, supplemental_rows


def mysql_source_rows(config: Any, window_id: str, limit: int) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
    list[dict[str, str]],
]:
    top_rows = mysql_top_rows(config, window_id, limit)
    community_rows, narrative_rows = mysql_community_rows(config, window_id)
    official_rows, positioning_rows = mysql_company_rows(config, window_id)
    hard_rows, supplemental_rows = mysql_ths_root_evidence_rows(config, window_id)
    return top_rows, community_rows, official_rows, positioning_rows, narrative_rows, hard_rows, supplemental_rows


def market_evidence(row: dict[str, str]) -> str:
    speed = row.get("speed", "")
    pct = row.get("pct_change", "")
    amount = row.get("amount", "")
    basis = row.get("basis", "")
    parts = [
        f"涨速 {speed}%",
        f"涨幅 {pct}%",
    ]
    if amount:
        parts.append(f"成交额 {amount}")
    if basis:
        parts.append(f"来源 {basis}")
    return "；".join(parts)


def sector_evidence(row: dict[str, str]) -> str:
    industry = row.get("industry", "") or row.get("sub_industry", "")
    sub = row.get("sub_industry", "")
    concepts = short_list(row.get("concepts", ""))
    parts = []
    if industry:
        parts.append(f"行业 {industry}")
    if sub and sub != industry:
        parts.append(f"细分 {sub}")
    if concepts:
        parts.append(f"概念 {concepts}")
    return "；".join(parts) if parts else "暂无板块/概念信息"


def community_evidence(row: dict[str, str] | None) -> tuple[str, str, str, str]:
    if not row:
        return "暂无社区证据", "missing", "", "0"
    status = row.get("source_status", "")
    count = row.get("hot_post_count", "") or row.get("comment_count", "") or "0"
    terms = row.get("hot_terms", "")
    sample = compact_text(row.get("sample_hot_posts", ""), 220)
    explanation = row.get("community_explanation", "")
    pieces = [piece for piece in [explanation, sample] if piece]
    return "；".join(pieces) if pieces else "暂无有效社区证据", status, terms, count


def community_narrative_fields(row: dict[str, str] | None) -> dict[str, str]:
    if not row:
        return {
            "community_main_claim": "",
            "community_trigger_claim": "",
            "community_trigger_event": "",
            "community_trigger_timing": "",
            "community_imagination_path": "",
            "community_verification_anchor": "",
            "community_evidence_type": "",
            "community_support_points": "",
            "community_disagreements": "",
            "community_risk_flags": "",
            "community_verification_need": "",
            "community_signal_quality": "",
        }
    return {
        "community_main_claim": compact_text(row.get("community_main_claim", ""), 220),
        "community_trigger_claim": compact_text(row.get("community_trigger_claim", ""), 220),
        "community_trigger_event": compact_text(row.get("community_trigger_event", ""), 240),
        "community_trigger_timing": compact_text(row.get("community_trigger_timing", ""), 220),
        "community_imagination_path": compact_text(row.get("community_imagination_path", ""), 280),
        "community_verification_anchor": compact_text(row.get("community_verification_anchor", ""), 260),
        "community_evidence_type": compact_text(row.get("community_evidence_type", ""), 120),
        "community_support_points": compact_text(row.get("community_support_points", ""), 280),
        "community_disagreements": compact_text(row.get("community_disagreements", ""), 220),
        "community_risk_flags": compact_text(row.get("community_risk_flags", ""), 220),
        "community_verification_need": compact_text(row.get("community_verification_need", ""), 220),
        "community_signal_quality": compact_text(row.get("community_signal_quality", ""), 40),
    }


def structured_community_text(narrative: dict[str, str], fallback_text: str) -> str:
    pieces = [
        narrative.get("community_main_claim", ""),
        narrative.get("community_trigger_claim", ""),
        narrative.get("community_trigger_event", ""),
        narrative.get("community_trigger_timing", ""),
        narrative.get("community_imagination_path", ""),
        narrative.get("community_verification_anchor", ""),
        narrative.get("community_support_points", ""),
        narrative.get("community_disagreements", ""),
        narrative.get("community_risk_flags", ""),
        narrative.get("community_verification_need", ""),
    ]
    text = "；".join(piece for piece in pieces if piece)
    return text or fallback_text


def official_evidence(row: dict[str, str] | None) -> tuple[str, str, str, str]:
    if not row:
        return "暂无公司画像", "", "", "missing"
    highlights = compact_text(row.get("company_highlights", ""), 180)
    main_business = compact_text(row.get("main_business", ""), 180)
    sw_industry = compact_text(row.get("sw_industry", ""), 80)
    concept_tags = short_list(row.get("concept_tags", ""), 6)
    business_plan = compact_text(row.get("latest_management_business_plan", ""), 220)
    pieces = []
    if highlights:
        pieces.append(f"亮点：{highlights}")
    if main_business:
        pieces.append(f"主营：{main_business}")
    if sw_industry:
        pieces.append(f"行业：{sw_industry}")
    if concept_tags:
        pieces.append(f"概念：{concept_tags}")
    if business_plan:
        pieces.append(f"经营计划：{business_plan}")
    return (
        "；".join(pieces) if pieces else "暂无有效公司画像",
        "",
        "",
        row.get("source_status", ""),
    )


def official_positioning(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    return compact_text(row.get("company_positioning", ""), 180)


def hard_evidence_fields(row: dict[str, str] | None) -> dict[str, str]:
    if not row:
        return {
            "hard_evidence_summary": "",
            "hard_evidence_strength": "未采集",
            "hard_catalyst_types": "",
            "stone_evidence_summary": "",
            "order_cooperation_evidence": "",
            "order_cooperation_hard_evidence": "",
            "amount_terms_evidence": "",
            "partner_customer_evidence": "",
            "financial_evidence": "",
            "ma_evidence": "",
            "risk_evidence": "",
            "hard_evidence_gap": "根页面暂无近期重要事件",
        }
    return {
        "hard_evidence_summary": compact_text(row.get("hard_catalyst_summary", ""), 170),
        "hard_evidence_strength": compact_text(row.get("hard_evidence_strength", ""), 80),
        "hard_catalyst_types": compact_text(row.get("hard_catalyst_types", ""), 160),
        "stone_evidence_summary": compact_text(row.get("stone_evidence_summary", ""), 220),
        "order_cooperation_evidence": compact_text(row.get("order_cooperation_evidence", ""), 260),
        "order_cooperation_hard_evidence": compact_text(row.get("order_cooperation_hard_evidence", ""), 420),
        "amount_terms_evidence": compact_text(row.get("amount_terms_evidence", ""), 360),
        "partner_customer_evidence": compact_text(row.get("partner_customer_evidence", ""), 360),
        "financial_evidence": compact_text(row.get("financial_evidence", ""), 260),
        "ma_evidence": compact_text(row.get("ma_evidence", ""), 260),
        "risk_evidence": compact_text(row.get("risk_evidence", ""), 260),
        "hard_evidence_gap": compact_text(row.get("evidence_gap", ""), 220),
    }


def supplemental_evidence_fields(row: dict[str, str] | None) -> dict[str, str]:
    if not row:
        return {
            "supplemental_strength": "未采集",
            "supplemental_summary": "",
            "news_evidence": "",
            "irm_evidence": "",
            "official_news_evidence": "",
            "order_cooperation_supplement": "",
            "customer_partner_supplement": "",
            "amount_terms_supplement": "",
            "supplemental_evidence_gap": "根页面暂无重要事件",
        }
    return {
        "supplemental_strength": compact_text(row.get("supplemental_strength", ""), 80),
        "supplemental_summary": compact_text(row.get("supplemental_summary", ""), 170),
        "news_evidence": compact_text(row.get("news_evidence", ""), 360),
        "irm_evidence": compact_text(row.get("irm_evidence", ""), 300),
        "official_news_evidence": compact_text(row.get("official_news_evidence", ""), 300),
        "order_cooperation_supplement": compact_text(row.get("order_cooperation_supplement", ""), 420),
        "customer_partner_supplement": compact_text(row.get("customer_partner_supplement", ""), 360),
        "amount_terms_supplement": compact_text(row.get("amount_terms_supplement", ""), 360),
        "supplemental_evidence_gap": compact_text(row.get("evidence_gap", ""), 220),
    }


def evidence_strength(
    row: dict[str, str],
    community_row: dict[str, str] | None,
    hard_row: dict[str, str] | None,
    supplemental_row: dict[str, str] | None,
) -> tuple[str, str]:
    pct = abs(to_float(row.get("pct_change", "")))
    speed = abs(to_float(row.get("speed", "")))
    has_sector = bool(row.get("industry") or row.get("concepts"))
    has_community = bool(community_row and int(float(community_row.get("hot_post_count") or 0)) > 0)
    status = community_row.get("source_status", "") if community_row else ""
    hard_strength = hard_row.get("hard_evidence_strength", "") if hard_row else ""
    hard_types = hard_row.get("hard_catalyst_types", "") if hard_row else ""
    hard_order_detail = hard_row.get("order_cooperation_hard_evidence", "") if hard_row else ""
    hard_amount = hard_row.get("amount_terms_evidence", "") if hard_row else ""
    hard_summary = hard_row.get("hard_catalyst_summary", "") if hard_row else ""
    has_direct_hard_title = any(label in hard_types for label in ["订单/合作", "并购/重组", "产品/技术"])
    supplemental_strength = supplemental_row.get("supplemental_strength", "") if supplemental_row else ""
    supplemental_summary = supplemental_row.get("supplemental_summary", "") if supplemental_row else ""
    supplemental_order = supplemental_row.get("order_cooperation_supplement", "") if supplemental_row else ""
    supplemental_customer = supplemental_row.get("customer_partner_supplement", "") if supplemental_row else ""
    supplemental_amount = supplemental_row.get("amount_terms_supplement", "") if supplemental_row else ""

    if "need_manual_verify" in status:
        return "证据中断", "社区采集触发验证，需人工验证后补采"
    if hard_order_detail and (has_community or has_sector):
        return "硬证据较强", "公告原文出现订单/合作/客户等片段，并与行情或叙事线索可交叉验证"
    if hard_summary and hard_strength == "强硬证据" and (has_community or has_sector):
        return "硬证据待核", "同花顺根页面出现近期重要事件，需点开原文确认细节"
    if supplemental_order and (supplemental_customer or supplemental_amount):
        return "补充证据较强", "补充线索命中合作/客户/金额线索，需要回原文二次核验"
    if supplemental_order or supplemental_strength in ("中等补充证据", "根页补充证据") and supplemental_summary and has_community:
        return "补充证据中等", "补充线索可支持社区叙事，但还需要近期重要事件闭环"
    if hard_amount and hard_summary and (has_community or has_sector):
        return "硬证据中等", "公告原文出现金额/条款片段，但仍需确认是否直接解释本次异动"
    if (hard_strength == "强硬证据" or has_direct_hard_title) and (has_community or has_sector):
        return "硬证据待核", "近期公告存在硬催化标题，原文细节仍需人工核对"
    if speed >= 1 and has_sector and has_community:
        return "线索较强", "行情异动、板块信息、社区解释三者同时出现"
    if pct >= 5 and (has_sector or has_community):
        return "线索中等", "有明显涨幅或主题线索，但缺少硬证据交叉验证"
    if has_community:
        return "线索偏弱", "有社区讨论，但行情或板块证据不够集中"
    return "待补证据", "目前主要只有行情异动，缺少解释性证据"


def why_hypothesis(row: dict[str, str], community_terms: str, community_text: str, company_text: str = "") -> str:
    name = row.get("name", "")
    industry = row.get("industry", "") or row.get("sub_industry", "")
    concepts = short_list(row.get("concepts", ""), 3)
    terms = short_list(community_terms, 4)
    company_hint = ""
    if company_text and company_text != "暂无官网/产品证据":
        company_hint = " 官网/产品证据可用于核对该叙事是否贴合公司真实业务。"
    if terms:
        return f"{name}的异动可能与{terms}相关，社区已有讨论，但仍需公告、新闻、板块联动和资金证据确认。{company_hint}"
    if concepts:
        return f"{name}的异动先从{concepts}概念和盘面涨速解释，社区证据暂不充分。{company_hint}"
    if industry:
        return f"{name}的异动先归入{industry}方向观察，当前缺少明确外部催化证据。{company_hint}"
    if community_text and community_text != "暂无社区证据":
        return f"{name}有社区讨论线索，但主题不集中，需要继续补硬证据。"
    return f"{name}目前只有盘面异动信号，为什么涨仍待补证据。"


def next_action(strength: str) -> str:
    if strength == "证据中断":
        return "先完成雪球人工验证，再复跑社区证据"
    if strength == "硬证据较强":
        return "优先核对公告原文金额、客户、期限、收入确认，再判断是否解释本次异动"
    if strength == "硬证据中等":
        return "核对金额/条款与主营业务、社区催化和当日板块是否一致"
    if strength == "硬证据待核":
        return "点开公告原文，确认标题级催化是否有金额、客户和履约条件"
    if strength == "补充证据较强":
        return "打开原文，确认合作方、金额、期限和是否为上市公司口径"
    if strength == "补充证据中等":
        return "用近期重要事件继续核实补充线索"
    if strength == "线索较强":
        return "优先看近期重要事件，验证社区叙事是否真实"
    if strength == "线索中等":
        return "补近期重要事件，确认是否有硬催化"
    if strength == "线索偏弱":
        return "扩大社区样本或换源到股吧/新闻"
    return "先补社区热帖，再看根页面近期重要事件"


def build_rows(
    top_rows: list[dict[str, str]],
    community_rows: list[dict[str, str]],
    official_rows: list[dict[str, str]],
    positioning_rows: list[dict[str, str]],
    narrative_rows: list[dict[str, str]],
    hard_rows: list[dict[str, str]],
    supplemental_rows: list[dict[str, str]],
    limit: int,
) -> list[dict[str, Any]]:
    built_at = now_text()
    community_map = by_code(community_rows)
    official_map = by_code(official_rows)
    positioning_map = by_code(positioning_rows)
    narrative_map = by_code(narrative_rows)
    hard_map = by_code(hard_rows)
    supplemental_map = by_code(supplemental_rows)
    rows: list[dict[str, Any]] = []
    for top in top_rows[:limit]:
        code = top.get("code", "").strip()
        if not code:
            continue
        community = community_map.get(code)
        official = official_map.get(code)
        positioning = official_positioning(positioning_map.get(code))
        narrative = community_narrative_fields(narrative_map.get(code))
        hard = hard_evidence_fields(hard_map.get(code))
        supplemental = supplemental_evidence_fields(supplemental_map.get(code))
        community_text, community_status, community_terms, community_count = community_evidence(community)
        community_text = structured_community_text(narrative, community_text)
        company_text, official_website, official_products, official_status = official_evidence(official)
        strength, gaps = evidence_strength(top, community, hard_map.get(code), supplemental_map.get(code))
        rows.append(
            {
                "built_at": built_at,
                "captured_at": top.get("captured_at", ""),
                "rank_speed": top.get("rank_speed", ""),
                "code": code,
                "name": top.get("name", ""),
                "symbol": market_symbol(code),
                "price": top.get("price", ""),
                "speed": top.get("speed", ""),
                "pct_change": top.get("pct_change", ""),
                "amount": top.get("amount", ""),
                "industry": top.get("industry", ""),
                "sub_industry": top.get("sub_industry", ""),
                "concepts": short_list(top.get("concepts", ""), 10),
                "market_evidence": market_evidence(top),
                "sector_evidence": sector_evidence(top),
                "hard_evidence_summary": hard["hard_evidence_summary"],
                "hard_evidence_strength": hard["hard_evidence_strength"],
                "hard_catalyst_types": hard["hard_catalyst_types"],
                "stone_evidence_summary": hard["stone_evidence_summary"],
                "order_cooperation_evidence": hard["order_cooperation_evidence"],
                "order_cooperation_hard_evidence": hard["order_cooperation_hard_evidence"],
                "amount_terms_evidence": hard["amount_terms_evidence"],
                "partner_customer_evidence": hard["partner_customer_evidence"],
                "financial_evidence": hard["financial_evidence"],
                "ma_evidence": hard["ma_evidence"],
                "risk_evidence": hard["risk_evidence"],
                "hard_evidence_gap": hard["hard_evidence_gap"],
                "supplemental_strength": supplemental["supplemental_strength"],
                "supplemental_summary": supplemental["supplemental_summary"],
                "news_evidence": supplemental["news_evidence"],
                "irm_evidence": supplemental["irm_evidence"],
                "official_news_evidence": supplemental["official_news_evidence"],
                "order_cooperation_supplement": supplemental["order_cooperation_supplement"],
                "customer_partner_supplement": supplemental["customer_partner_supplement"],
                "amount_terms_supplement": supplemental["amount_terms_supplement"],
                "supplemental_evidence_gap": supplemental["supplemental_evidence_gap"],
                "company_evidence": positioning or company_text,
                "company_positioning": positioning,
                "official_website": official_website,
                "official_products": official_products,
                "official_status": official_status,
                "community_main_claim": narrative["community_main_claim"],
                "community_trigger_claim": narrative["community_trigger_claim"],
                "community_trigger_event": narrative["community_trigger_event"],
                "community_trigger_timing": narrative["community_trigger_timing"],
                "community_imagination_path": narrative["community_imagination_path"],
                "community_verification_anchor": narrative["community_verification_anchor"],
                "community_evidence_type": narrative["community_evidence_type"],
                "community_support_points": narrative["community_support_points"],
                "community_disagreements": narrative["community_disagreements"],
                "community_risk_flags": narrative["community_risk_flags"],
                "community_verification_need": narrative["community_verification_need"],
                "community_signal_quality": narrative["community_signal_quality"],
                "community_evidence": community_text,
                "community_status": community_status,
                "community_hot_terms": community_terms,
                "community_post_count": community_count,
                "why_hypothesis": why_hypothesis(top, community_terms, community_text, company_text),
                "evidence_strength": strength,
                "evidence_gaps": gaps,
                "next_evidence_action": next_action(strength),
            }
        )
    return rows


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build the structured evidence layer for stock mover scouting.")
    add_mysql_args(parser)
    parser.add_argument("--mysql-window-id", default="", help="Window id to read from MySQL. Defaults to latest done window.")
    parser.add_argument("--mysql-write-evidence-layer", action="store_true", help="Write built rows back to MySQL evidence_layers.")
    parser.add_argument("--no-file-output", action="store_true", help="Skip CSV/JSON compatibility output.")
    parser.add_argument("--top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--hard-evidence-csv", type=Path, default=root / "data" / "stock" / "hard_catalyst_evidence_latest.csv")
    parser.add_argument("--supplemental-evidence-csv", type=Path, default=root / "data" / "stock" / "supplemental_hard_evidence_latest.csv")
    parser.add_argument("--community-evidence-csv", type=Path, default=root / "data" / "stock" / "xueqiu_focus_evidence_latest.csv")
    parser.add_argument("--community-narrative-csv", type=Path, default=root / "data" / "stock" / "community_narrative_latest.csv")
    parser.add_argument("--official-evidence-csv", type=Path, default=root / "data" / "stock" / "official_site_evidence_latest.csv")
    parser.add_argument("--official-positioning-csv", type=Path, default=root / "data" / "stock" / "official_site_positioning_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "runs" / "mysql_exports" / "evidence_layer.csv")
    parser.add_argument("--output-json", type=Path, default=root / "runs" / "mysql_exports" / "evidence_layer.json")
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args) if args.mysql_enabled else None
    window_id = ""
    if config:
        window_id = args.mysql_window_id or latest_mysql_window_id(config)
        if not window_id:
            raise SystemExit("mysql_window_id_missing")
        (
            top_rows,
            community_rows,
            official_rows,
            positioning_rows,
            narrative_rows,
            hard_rows,
            supplemental_rows,
        ) = mysql_source_rows(config, window_id, args.limit)
    else:
        top_rows = read_csv(args.top10_csv)
        hard_rows = read_csv(args.hard_evidence_csv)
        supplemental_rows = read_csv(args.supplemental_evidence_csv)
        community_rows = read_csv(args.community_evidence_csv)
        narrative_rows = read_csv(args.community_narrative_csv)
        official_rows = read_csv(args.official_evidence_csv)
        positioning_rows = read_csv(args.official_positioning_csv)
    rows = build_rows(top_rows, community_rows, official_rows, positioning_rows, narrative_rows, hard_rows, supplemental_rows, args.limit)
    mysql_rows_written = 0
    if config and args.mysql_write_evidence_layer:
        mysql_rows_written = import_evidence_layer_rows(config, rows, window_id)
    if not args.no_file_output:
        write_csv(args.output_csv, rows, COLUMNS)
        write_json(
            args.output_json,
            {
                "built_at": now_text(),
                "source": "mysql" if config else "csv",
                "mysql_window_id": window_id,
                "top10_csv": str(args.top10_csv),
                "hard_evidence_csv": str(args.hard_evidence_csv),
                "supplemental_evidence_csv": str(args.supplemental_evidence_csv),
                "community_evidence_csv": str(args.community_evidence_csv),
                "community_narrative_csv": str(args.community_narrative_csv),
                "official_evidence_csv": str(args.official_evidence_csv),
                "official_positioning_csv": str(args.official_positioning_csv),
                "row_count": len(rows),
                "rows": rows,
            },
        )
        print(f"evidence_layer_csv={args.output_csv}")
        print(f"evidence_layer_json={args.output_json}")
    if config:
        print(f"mysql_window_id={window_id}")
        print(f"mysql_evidence_layer_rows={mysql_rows_written}")
    print(f"rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
