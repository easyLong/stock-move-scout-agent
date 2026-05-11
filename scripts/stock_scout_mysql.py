#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import (
    DEFAULT_MYSQL_EXE,
    MySqlConfig,
    add_mysql_args,
    mysql_cli_args_from_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_bool,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)
from stock_move_scout.feed.root_cache import (
    enqueue_root_evidence_cache_dirty_many,
    latest_root_evidence_trade_date,
)


UNANCHORED_TYPE = "unanchored"
UNANCHORED_NAME = "\u672a\u951a\u5b9a"
UNANCHORED_LABEL = "\u5f02\u52a8"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not str(path) or str(path) == "." or not path.exists() or not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def safe_id(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]+", "_", value).strip("_")


def to_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value).replace("%", "").strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def to_float(value: Any) -> float | None:
    try:
        text = str(value).replace("%", "").strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def text_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key, "")
    return "" if value is None else str(value)


NOISE_CONCEPTS = {
    "ST板块", "亏损股", "微小盘股", "含H股", "含B股", "融资融券",
    "国企改革", "央企改革", "地方国企改革", "一带一路", "深股通", "沪股通",
    "周期股", "微盘优选", "活跃股", "低价股", "高价股", "机构重仓",
    "MSCI中国", "富时罗素", "标普道琼斯A股", "证金持股", "社保重仓",
}

BROAD_CONCEPTS = {
    "PPP概念", "储能", "人工智能", "新型城镇", "互联金融", "养老概念",
    "碳中和", "绿色电力", "华为概念", "新能源车", "充电桩", "光伏",
    "氢能源", "数据中心", "大数据", "工业互联", "机器人概念", "智能机器",
    "物联网", "小米概念", "专精特新", "创投概念", "股东减持", "业绩预亏",
    "回购计划", "芯片", "云计算", "信息安全", "区块链", "乡村振兴",
    "物业管理", "物业管理概念", "低空经济",
}


def is_excluded_stock_name(name: str) -> bool:
    text = (name or "").strip()
    return "ST" in text.upper() or "退市" in text


def split_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:
            pass
    return [item.strip() for item in re.split(r"[,，、;\s]+", text) if item.strip()]


def unique_keep_order(values: list[str], limit: int = 80) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def num_value(value: Any) -> float:
    parsed = to_float(value)
    return parsed if parsed is not None else 0.0


def amount_value(row: dict[str, Any]) -> float:
    return num_value(row.get("amount"))


def anchor_type_priority(anchor_type: str) -> int:
    if anchor_type == "limit_up_theme":
        return 6
    if anchor_type == "hot_concept":
        return 5
    if anchor_type == "sub_industry":
        return 3
    if anchor_type == "concept":
        return 2
    if anchor_type == "industry":
        return 1
    return 0


def static_anchor_candidates(row: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    sub = text_value(row, "sub_industry").strip()
    industry = text_value(row, "industry").strip()
    if sub:
        candidates.append(("sub_industry", sub))
    if industry and industry != sub and all(name != industry for _, name in candidates):
        candidates.append(("industry", industry))
    return candidates or [("unknown", "未分组")]


def active_anchor_candidates(row: dict[str, Any], active_anchor_map: dict[str, list[dict[str, Any]]] | None) -> list[tuple[str, str]]:
    if not active_anchor_map:
        return []
    out: list[tuple[str, str]] = []
    for anchor in active_anchor_map.get(text_value(row, "code")) or []:
        name = text_value(anchor, "anchor_name").strip()
        if text_value(anchor, "match_level") == "weak":
            continue
        if name and text_value(anchor, "status") != "expired" and all(existing != name for _, existing in out):
            out.append((text_value(anchor, "anchor_type") or "hot_concept", name))
    return out


def theme_reason_anchor_type(info: dict[str, Any]) -> str:
    if text_value(info, "source") == "ths_limit_up_review":
        return "limit_up_theme"
    return "hot_concept"


def active_status_priority(status: str) -> int:
    if status == "active":
        return 3
    if status == "watch":
        return 2
    if status == "cooling":
        return 1
    return 0


def theme_reason_candidate_rank(info: dict[str, Any]) -> tuple[int, float, float, float, float, int, str, str]:
    return (
        active_status_priority(text_value(info, "active_anchor_status")),
        num_value(info.get("active_anchor_final_score")),
        num_value(info.get("canonical_relation_confidence")),
        num_value(info.get("priority")),
        num_value(info.get("confidence")),
        theme_reason_rank(info)[2],
        text_value(info, "source_date"),
        text_value(info, "canonical_anchor_name") or text_value(info, "anchor_name"),
    )


def canonical_anchor_name(info: dict[str, Any]) -> str:
    return text_value(info, "canonical_anchor_name").strip() or text_value(info, "anchor_name").strip()


def theme_reason_anchor_candidates(
    code: str,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None,
) -> list[tuple[str, str]]:
    if not theme_reason_map:
        return []
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for item in (theme_reason_map.get(code) or {}).values():
        anchor_name = text_value(item, "canonical_anchor_name").strip()
        if not anchor_name or anchor_name in seen:
            continue
        if not text_value(item, "reason_text").strip():
            continue
        seen.add(anchor_name)
        items.append(item)
    items.sort(key=theme_reason_candidate_rank, reverse=True)
    return [(theme_reason_anchor_type(item), canonical_anchor_name(item)) for item in items]


def all_anchor_candidates(
    row: dict[str, Any],
    active_anchor_map: dict[str, list[dict[str, Any]]] | None,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[tuple[str, str]]:
    candidates = active_anchor_candidates(row, active_anchor_map)
    code = text_value(row, "code")
    for anchor_type, anchor_name in theme_reason_anchor_candidates(code, theme_reason_map):
        if all(existing != anchor_name for _, existing in candidates):
            candidates.append((anchor_type, anchor_name))
    return candidates


def active_anchor_info_for(
    code: str,
    anchor_name: str,
    active_anchor_map: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not active_anchor_map:
        return {}
    for item in active_anchor_map.get(code) or []:
        if text_value(item, "anchor_name") == anchor_name:
            return item
    return {}


def theme_reason_info_for(
    code: str,
    anchor_name: str,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None,
    preferred_terms: list[str] | None = None,
    allow_fallback: bool = False,
) -> dict[str, Any]:
    if not theme_reason_map:
        return {}
    reasons = theme_reason_map.get(code) or {}
    exact = reasons.get(anchor_name) or {}
    if exact or not allow_fallback:
        return exact
    preferred_terms = [term for term in (preferred_terms or []) if term]
    preferred = [reasons[term] for term in preferred_terms if term in reasons]
    if preferred:
        return sorted(preferred, key=theme_reason_rank, reverse=True)[0]
    candidates = list({id(item): item for item in reasons.values()}.values())
    candidates = [item for item in candidates if text_value(item, "reason_text")]
    if not candidates:
        return {}
    return sorted(candidates, key=theme_reason_rank, reverse=True)[0]


def anchor_related_terms(anchor_name: str) -> list[str]:
    name = str(anchor_name or "").strip()
    terms = [name] if name else []
    aliases = {
        "人形机器人": ["机器人", "减速器", "伺服", "关节", "执行器", "灵巧手", "电机", "丝杠", "外骨骼", "四足"],
        "商业航天": ["航天", "火箭", "卫星", "可回收火箭", "火箭回收", "卫星互联网"],
        "光通信/CPO": ["光通信", "CPO", "共封装光学", "光模块", "光芯片", "算力"],
        "AI PCB": ["PCB", "印制电路板", "高速板", "服务器", "算力"],
        "芯片": ["芯片", "半导体", "集成电路", "存储", "封测"],
    }
    for key, values in aliases.items():
        if key == name or key in name or name in key:
            terms.extend(values)
    out: list[str] = []
    for term in terms:
        if term and term not in out:
            out.append(term)
    return out


def related_theme_reason_info_for(
    code: str,
    anchor_name: str,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not theme_reason_map:
        return {}
    reasons = list({id(item): item for item in (theme_reason_map.get(code) or {}).values()}.values())
    terms = anchor_related_terms(anchor_name)
    scored: list[tuple[int, tuple[float, float, int], dict[str, Any]]] = []
    for item in reasons:
        haystack = " ".join(
            [
                text_value(item, "anchor_name"),
                text_value(item, "theme_name"),
                text_value(item, "reason_text"),
            ]
        )
        hit_count = sum(1 for term in terms if term and term in haystack)
        if hit_count:
            scored.append((hit_count, theme_reason_rank(item), item))
    if not scored:
        return {}
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return scored[0][2]


def theme_reason_rank(info: dict[str, Any]) -> tuple[float, float, int]:
    source_rank = {
        "ths_limit_up_review": 5,
        "ths_hot_concept": 4,
        "ths_stock_concept": 3,
        "ths_root_theme_point": 2,
        "concept_tag": 1,
    }
    return (
        num_value(info.get("priority")),
        num_value(info.get("confidence")),
        source_rank.get(text_value(info, "source"), 0),
    )


def row_reason_terms(row: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("industry", "sub_industry"):
        value = text_value(row, key).strip()
        if value and value not in terms:
            terms.append(value)
    for value in split_text_list(row.get("concepts")):
        if value and value not in terms:
            terms.append(value)
    return terms


def active_reason_is_evidence(active_info: dict[str, Any]) -> bool:
    reason = text_value(active_info, "latest_reason").strip()
    if not reason:
        return False
    if text_value(active_info, "match_level") != "strong":
        return False
    noise_terms = ("弱命中", "中命中", "股票概念命中", "行业命中", "概念命中", "->")
    return not any(term in reason for term in noise_terms)


def compact_reason_text(text: str, max_len: int = 180) -> str:
    text = re.sub(r"<br\s*/?>", " ", str(text or ""), flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip(" ,，;；。") + "..."


def best_reason_text(active_info: dict[str, Any], reason_info: dict[str, Any], selected_anchor: str = "") -> str:
    reason = text_value(reason_info, "reason_text").strip()
    if reason:
        reason_anchor = text_value(reason_info, "anchor_name").strip()
        if reason_anchor and selected_anchor and reason_anchor != selected_anchor:
            return compact_reason_text(f"{reason_anchor}：{reason}")
        return compact_reason_text(reason)
    if active_reason_is_evidence(active_info):
        return compact_reason_text(text_value(active_info, "latest_reason"))
    return ""


def anchor_match_level_priority(level: str) -> int:
    if level == "strong":
        return 3
    if level == "medium":
        return 2
    if level == "weak":
        return 1
    return 0


def anchor_recency_priority(info: dict[str, Any]) -> int:
    last_seen = text_value(info, "last_seen_date")[:10]
    if last_seen == date.today().strftime("%Y-%m-%d"):
        return 2
    if last_seen:
        return 1
    return 0


def dominant_count(members: list[dict[str, Any]], key: str) -> tuple[str, int]:
    counts: dict[str, int] = {}
    for row in members:
        value = text_value(row, key).strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return "", 0
    name, count = max(counts.items(), key=lambda item: item[1])
    return name, count


def concept_anchor_allowed(members: list[dict[str, Any]]) -> bool:
    member_count = len(members)
    if member_count < 2:
        return True
    _, dominant_industry_count = dominant_count(members, "industry")
    _, dominant_sub_count = dominant_count(members, "sub_industry")
    return dominant_sub_count >= 2 or dominant_industry_count / max(1, member_count) >= 0.6


def scan_anchor_score(members: list[dict[str, Any]]) -> float:
    member_count = len(members)
    max_pct = max((num_value(row.get("pct_change")) for row in members), default=0.0)
    avg_pct = sum(num_value(row.get("pct_change")) for row in members) / max(1, member_count)
    max_speed = max((num_value(row.get("speed")) for row in members), default=0.0)
    total_amount_yi = sum(max(0.0, amount_value(row)) for row in members) / 100_000_000
    return member_count * 100 + max(0.0, max_pct) * 12 + max(0.0, avg_pct) * 8 + max_speed * 20 + min(80.0, total_amount_yi * 3)


def anchor_strength_label(member_count: int) -> str:
    if member_count >= 6:
        return "板块扩散"
    if member_count >= 3:
        return "有联动"
    if member_count >= 2:
        return "弱联动"
    return "孤立"


def build_scan_roles(
    rows: list[dict[str, Any]],
    active_anchor_map: dict[str, list[dict[str, Any]]] | None = None,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    clean_rows = [row for row in rows if text_value(row, "code") and not is_excluded_stock_name(text_value(row, "name"))]
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in clean_rows:
        for anchor_type, anchor_name in all_anchor_candidates(row, active_anchor_map, theme_reason_map):
            buckets.setdefault((anchor_type, anchor_name), []).append(row)

    anchor_stats_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for (anchor_type, anchor_name), members in buckets.items():
        if anchor_type == "concept" and not concept_anchor_allowed(members):
            continue
        sorted_by_leader = sorted(
            members,
            key=lambda row: (-num_value(row.get("pct_change")), -num_value(row.get("speed")), to_int(row.get("rank_speed"), 9999)),
        )
        sorted_by_amount = sorted(
            members,
            key=lambda row: (-amount_value(row), -num_value(row.get("pct_change")), to_int(row.get("rank_speed"), 9999)),
        )
        leader = sorted_by_leader[0]
        core = sorted_by_amount[0]
        member_count = len(members)
        total_amount = sum(max(0.0, amount_value(row)) for row in members)
        avg_pct = sum(num_value(row.get("pct_change")) for row in members) / max(1, member_count)
        avg_speed = sum(num_value(row.get("speed")) for row in members) / max(1, member_count)
        dominant_industry, dominant_industry_count = dominant_count(members, "industry")
        dominant_sub_industry, dominant_sub_count = dominant_count(members, "sub_industry")
        stat = {
            "anchor_type": anchor_type,
            "anchor_name": anchor_name,
            "member_count": member_count,
            "leader_code": text_value(leader, "code"),
            "leader_name": text_value(leader, "name"),
            "core_code": text_value(core, "code"),
            "core_name": text_value(core, "name"),
            "total_amount": round(total_amount, 2),
            "max_pct_change": round(max(num_value(row.get("pct_change")) for row in members), 4),
            "avg_pct_change": round(avg_pct, 4),
            "max_speed": round(max(num_value(row.get("speed")) for row in members), 4),
            "avg_speed": round(avg_speed, 4),
            "anchor_score": round(scan_anchor_score(members), 2),
            "strength_label": anchor_strength_label(member_count),
            "dominant_industry": dominant_industry,
            "dominant_industry_count": dominant_industry_count,
            "dominant_sub_industry": dominant_sub_industry,
            "dominant_sub_industry_count": dominant_sub_count,
            "members": [{"code": text_value(row, "code"), "name": text_value(row, "name")} for row in members],
        }
        anchor_stats_by_key[(anchor_type, anchor_name)] = stat

    anchor_stats = list(anchor_stats_by_key.values())
    anchor_stats.sort(
        key=lambda row: (
            0 if int(row.get("member_count") or 0) >= 2 else 1,
            -anchor_type_priority(text_value(row, "anchor_type")),
            -int(row.get("member_count") or 0),
            -float(row.get("anchor_score") or 0),
        )
    )
    for idx, row in enumerate(anchor_stats, start=1):
        row["rank_no"] = idx

    stock_roles: list[dict[str, Any]] = []
    for row in clean_rows:
        possible = []
        for anchor_type, anchor_name in all_anchor_candidates(row, active_anchor_map, theme_reason_map):
            stat = anchor_stats_by_key.get((anchor_type, anchor_name))
            if not stat:
                continue
            member_count = int(stat.get("member_count") or 0)
            linked_priority = anchor_type_priority(anchor_type) if (member_count >= 2 or anchor_type in {"hot_concept", "limit_up_theme"}) else 0
            active_info = active_anchor_info_for(text_value(row, "code"), anchor_name, active_anchor_map)
            match_priority = anchor_match_level_priority(text_value(active_info, "match_level"))
            recency_priority = anchor_recency_priority(active_info)
            active_confidence = num_value(active_info.get("confidence")) if active_info else 0.0
            reason_info = theme_reason_info_for(text_value(row, "code"), anchor_name, theme_reason_map, allow_fallback=False)
            reason_rank = theme_reason_candidate_rank(reason_info) if reason_info else (0, 0.0, 0.0, 0.0, 0.0, 0, "", "")
            linked_strength = 3 if member_count >= 6 else 2 if member_count >= 3 else 1 if member_count >= 2 else 0
            possible.append((
                linked_strength,
                member_count,
                float(stat.get("anchor_score") or 0),
                num_value(stat.get("max_pct_change")),
                num_value(stat.get("avg_pct_change")),
                anchor_type_priority(anchor_type),
                match_priority,
                recency_priority,
                active_confidence,
                reason_rank[0],
                reason_rank[1],
                reason_rank[2],
                reason_rank[3],
                reason_rank[4],
                reason_rank[5],
                stat,
            ))
        if not possible:
            amount_yi = amount_value(row) / 100_000_000
            role_score = (
                max(0.0, num_value(row.get("pct_change"))) * 10
                + max(0.0, num_value(row.get("speed"))) * 30
                + min(80.0, amount_yi * 5)
            )
            stock_roles.append(
                {
                    "code": text_value(row, "code"),
                    "name": text_value(row, "name"),
                    "rank_no": to_int(row.get("rank_speed")),
                    "primary_anchor_type": UNANCHORED_TYPE,
                    "primary_anchor_name": UNANCHORED_NAME,
                    "anchor_member_count": 1,
                    "role_label": UNANCHORED_LABEL,
                    "leader_code": "",
                    "leader_name": "",
                    "core_code": "",
                    "core_name": "",
                    "role_score": round(role_score, 2),
                    "role_reason": (
                        f"{UNANCHORED_NAME}\uff1b"
                        f"\u6da8\u901f{num_value(row.get('speed')):.2f}%,"
                        f"\u6da8\u5e45{num_value(row.get('pct_change')):.2f}%,"
                        f"\u6210\u4ea4{amount_yi:.2f}\u4ebf"
                    ),
                    "raw_json": {
                        "anchor_source": "no_theme_anchor",
                        "anchor_method": "theme_anchor_only_no_static_fallback",
                        "pct_change": num_value(row.get("pct_change")),
                        "speed": num_value(row.get("speed")),
                        "amount_yi": round(amount_yi, 2),
                        "algorithm": "scan_topn_anchor_roles_v3_theme_only",
                    },
                }
            )
            continue
        possible.sort(key=lambda item: item[:-1], reverse=True)
        stat = possible[0][-1]
        code = text_value(row, "code")
        selected_anchor = text_value(stat, "anchor_name")
        active_info = active_anchor_info_for(code, selected_anchor, active_anchor_map)
        had_active_anchor = bool(active_info)
        reason_info = theme_reason_info_for(
            code,
            selected_anchor,
            theme_reason_map,
            allow_fallback=False,
        )
        reason_text = best_reason_text(active_info, reason_info, selected_anchor)
        stock_reason_info: dict[str, Any] = {}
        stock_reason_text = ""
        if not reason_text:
            stock_reason_info = related_theme_reason_info_for(
                code,
                selected_anchor,
                theme_reason_map,
            )
            if text_value(stock_reason_info, "anchor_name") != selected_anchor:
                stock_reason_text = best_reason_text(active_info, stock_reason_info, selected_anchor)
        evidence_suffix = ""
        if reason_text:
            evidence_suffix = f"；题材证据：{reason_text}"
        elif stock_reason_text:
            evidence_suffix = f"；个股证据：{stock_reason_text}"
        active_info = dict(active_info)
        active_info["latest_reason"] = reason_text
        if reason_text:
            active_info["source"] = text_value(reason_info, "source") or text_value(active_info, "source")
            active_info["confidence"] = reason_info.get("confidence", active_info.get("confidence", 0))
        leader_code = text_value(stat, "leader_code")
        core_code = text_value(stat, "core_code")
        member_count = int(stat.get("member_count") or 0)
        if member_count < 2:
            label = "孤立脉冲"
        elif code == leader_code and code == core_code:
            label = "领涨中军"
        elif code == leader_code:
            label = "领涨"
        elif code == core_code:
            label = "中军"
        else:
            label = "跟风"
        role_score = (
            max(0.0, num_value(row.get("pct_change"))) * 10
            + max(0.0, num_value(row.get("speed"))) * 30
            + min(80.0, amount_value(row) / 100_000_000 * 5)
            + member_count * 20
            + (80 if "领涨" in label else 60 if "中军" in label else 0)
        )
        stock_roles.append(
            {
                "code": code,
                "name": text_value(row, "name"),
                "rank_no": to_int(row.get("rank_speed")),
                "primary_anchor_type": text_value(stat, "anchor_type"),
                "primary_anchor_name": text_value(stat, "anchor_name"),
                "anchor_member_count": member_count,
                "role_label": label,
                "leader_code": leader_code,
                "leader_name": text_value(stat, "leader_name"),
                "core_code": core_code,
                "core_name": text_value(stat, "core_name"),
                "role_score": round(role_score, 2),
                "role_reason": (
                    f"锚点{text_value(stat, 'anchor_name')}共{member_count}只；"
                    f"领涨{text_value(stat, 'leader_name')}，中军{text_value(stat, 'core_name')}"
                    + evidence_suffix
                ),
                "raw_json": {
                    "anchor_strength": text_value(stat, "strength_label"),
                    "anchor_source": "active_market_anchor" if had_active_anchor else "theme_reason_bank" if reason_info else "no_theme_anchor",
                    "anchor_status": text_value(active_info, "status"),
                    "anchor_match_level": text_value(active_info, "match_level"),
                    "anchor_matched_term": text_value(active_info, "matched_term"),
                    "anchor_match_source": text_value(active_info, "source"),
                    "anchor_confidence": num_value(active_info.get("confidence")) if active_info else 0.0,
                    "anchor_reason": reason_text,
                    "evidence_anchor_name": text_value(reason_info, "anchor_name"),
                    "stock_reason": stock_reason_text,
                    "stock_reason_anchor_name": text_value(stock_reason_info, "anchor_name"),
                    "stock_reason_source": text_value(stock_reason_info, "source"),
                    "theme_reason_source": text_value(reason_info, "source"),
                    "theme_reason_confidence": num_value(reason_info.get("confidence")) if reason_info else 0.0,
                    "theme_reason_priority": num_value(reason_info.get("priority")) if reason_info else 0.0,
                    "pct_change": num_value(row.get("pct_change")),
                    "speed": num_value(row.get("speed")),
                    "amount_yi": round(amount_value(row) / 100_000_000, 2),
                    "algorithm": "scan_topn_anchor_roles_v2_coherent_anchor",
                    "dominant_industry": text_value(stat, "dominant_industry"),
                    "dominant_sub_industry": text_value(stat, "dominant_sub_industry"),
                },
            }
        )
    stock_roles.sort(key=lambda row: (to_int(row.get("rank_no"), 9999), -float(row.get("role_score") or 0)))
    return anchor_stats, stock_roles


def load_active_anchor_relation_alias_map(config: MySqlConfig) -> dict[str, dict[str, Any]]:
    sql = """
    SELECT
      r.relation_name,
      r.anchor_name,
      r.relation_type,
      r.confidence,
      r.status,
      COALESCE(a.status, ''),
      COALESCE(a.final_score, 0),
      COALESCE(DATE_FORMAT(a.last_seen_date, '%Y-%m-%d'), ''),
      COALESCE(a.today_event_count, 0)
    FROM active_market_anchor_relations r
    LEFT JOIN active_market_anchors a
      ON a.source=r.source AND a.anchor_name=r.anchor_name
    WHERE r.source='ths_hot_concept'
      AND r.status <> 'expired'
      AND r.relation_type IN ('anchor','theme','concept')
      AND r.confidence >= 80
      AND COALESCE(r.relation_name, '') <> ''
      AND COALESCE(r.anchor_name, '') <> ''
    ORDER BY
      r.relation_name,
      CASE COALESCE(a.status, r.status) WHEN 'active' THEN 3 WHEN 'watch' THEN 2 WHEN 'cooling' THEN 1 ELSE 0 END DESC,
      COALESCE(a.final_score, 0) DESC,
      r.confidence DESC,
      r.evidence_count DESC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return {}
    result: dict[str, dict[str, Any]] = {}
    keys = [
        "relation_name",
        "anchor_name",
        "relation_type",
        "confidence",
        "relation_status",
        "active_anchor_status",
        "active_anchor_final_score",
        "active_anchor_last_seen_date",
        "active_anchor_today_event_count",
    ]
    for row in rows:
        item = dict(zip(keys, row))
        relation_name = text_value(item, "relation_name")
        if relation_name and relation_name not in result:
            result[relation_name] = item
    return result


def normalized_reason_item(
    item: dict[str, Any],
    term: str,
    alias_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    out = dict(item)
    alias = alias_map.get(str(term or "").strip()) or {}
    if not alias:
        out.setdefault("canonical_anchor_name", "")
        out.setdefault("canonical_from", "")
        out.setdefault("canonical_relation_type", "")
        out.setdefault("canonical_relation_confidence", 0)
        return out
    out["canonical_anchor_name"] = text_value(alias, "anchor_name")
    out["canonical_from"] = str(term or "").strip()
    out["canonical_relation_type"] = text_value(alias, "relation_type")
    out["canonical_relation_confidence"] = num_value(alias.get("confidence"))
    out["active_anchor_status"] = text_value(alias, "active_anchor_status") or text_value(item, "active_anchor_status")
    out["active_anchor_final_score"] = num_value(alias.get("active_anchor_final_score")) or num_value(item.get("active_anchor_final_score"))
    out["active_anchor_last_seen_date"] = text_value(alias, "active_anchor_last_seen_date") or text_value(item, "active_anchor_last_seen_date")
    out["active_anchor_today_event_count"] = num_value(alias.get("active_anchor_today_event_count")) or num_value(item.get("active_anchor_today_event_count"))
    return out


def ensure_schema(config: MySqlConfig, root: Path | None = None) -> None:
    base = root or project_root()
    schema = base / "database" / "mysql" / "stock_scout_schema.sql"
    sql = schema.read_text(encoding="utf-8")
    run_mysql(config, sql, database=False)
    ensure_realtime_delta_columns(config)


def mysql_scalar(config: MySqlConfig, sql: str) -> str:
    return run_mysql(config, sql, batch=True, raw=True).strip()


def mysql_column_exists(config: MySqlConfig, table_name: str, column_name: str) -> bool:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE()
      AND TABLE_NAME={sql_string(table_name)}
      AND COLUMN_NAME={sql_string(column_name)};
    """
    return mysql_scalar(config, sql) != "0"


def mysql_index_exists(config: MySqlConfig, table_name: str, index_name: str) -> bool:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA=DATABASE()
      AND TABLE_NAME={sql_string(table_name)}
      AND INDEX_NAME={sql_string(index_name)};
    """
    return mysql_scalar(config, sql) != "0"


def ensure_realtime_delta_columns(config: MySqlConfig) -> None:
    statements: list[str] = []
    if not mysql_column_exists(config, "scan_movers", "amount_delta_15s"):
        statements.append("ALTER TABLE scan_movers ADD COLUMN amount_delta_15s DECIMAL(20,2) NULL AFTER amount;")
    if not mysql_column_exists(config, "scan_movers", "volume_delta_15s"):
        statements.append("ALTER TABLE scan_movers ADD COLUMN volume_delta_15s BIGINT NULL AFTER volume;")
    if not mysql_index_exists(config, "scan_movers", "idx_scan_movers_amount_delta"):
        statements.append("ALTER TABLE scan_movers ADD KEY idx_scan_movers_amount_delta (captured_at, amount_delta_15s);")
    if not mysql_column_exists(config, "window_movers", "max_amount_delta_15s"):
        statements.append("ALTER TABLE window_movers ADD COLUMN max_amount_delta_15s DECIMAL(20,2) NULL AFTER amount;")
    if not mysql_column_exists(config, "window_movers", "max_volume_delta_15s"):
        statements.append("ALTER TABLE window_movers ADD COLUMN max_volume_delta_15s BIGINT NULL AFTER max_amount_delta_15s;")
    if statements:
        run_mysql(config, "\n".join(statements))


def load_active_anchor_member_map(config: MySqlConfig, codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    clean_codes = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not clean_codes:
        return {}
    code_sql = ",".join(sql_string(code) for code in clean_codes)
    sql = f"""
    SELECT
      c.code, c.anchor_name, c.anchor_type, c.match_source, c.stock_name,
      DATE_FORMAT(COALESCE(m.last_seen_date, a.last_seen_date), '%Y-%m-%d'),
      COALESCE(m.event_count_14d, a.event_count_14d, 0),
      COALESCE(m.active_days_14d, a.active_days_14d, 0),
      COALESCE(m.total_heat_14d, a.total_heat_14d, 0),
      COALESCE(m.limit_up_count_14d, a.limit_up_count_14d, 0),
      c.evidence_text, c.confidence, c.status,
      c.match_level, c.matched_term
    FROM active_anchor_match_candidates c
    LEFT JOIN active_market_anchor_members m
      ON m.source=c.source AND m.anchor_name=c.anchor_name AND m.code=c.code
    LEFT JOIN active_market_anchors a
      ON a.source=c.source AND a.anchor_name=c.anchor_name
    WHERE c.code IN ({code_sql})
      AND c.status IN ('active','watch','cooling')
      AND c.match_level IN ('strong','medium')
      AND c.match_source <> 'stock_concept_relation'
    ORDER BY
      c.code,
      CASE c.match_level WHEN 'strong' THEN 3 WHEN 'medium' THEN 2 WHEN 'weak' THEN 1 ELSE 0 END DESC,
      CASE WHEN COALESCE(m.last_seen_date, a.last_seen_date)=CURDATE() THEN 1 ELSE 0 END DESC,
      CASE c.status WHEN 'active' THEN 3 WHEN 'watch' THEN 2 WHEN 'cooling' THEN 1 ELSE 0 END DESC,
      c.confidence DESC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        fallback_sql = f"""
        SELECT
          code, anchor_name, anchor_type, source, stock_name,
          DATE_FORMAT(last_seen_date, '%Y-%m-%d'), event_count_14d, active_days_14d,
          total_heat_14d, limit_up_count_14d, latest_reason, confidence, status,
          'strong', anchor_name
        FROM active_market_anchor_members
        WHERE code IN ({code_sql})
          AND status IN ('active','watch','cooling')
        ORDER BY
          code,
          CASE status WHEN 'active' THEN 3 WHEN 'watch' THEN 2 WHEN 'cooling' THEN 1 ELSE 0 END DESC,
          confidence DESC,
          last_seen_date DESC;
        """
        try:
            rows = mysql_rows(run_mysql(config, fallback_sql, batch=True, raw=True))
        except Exception:
            return {}
    result: dict[str, list[dict[str, Any]]] = {}
    keys = [
        "code",
        "anchor_name",
        "anchor_type",
        "source",
        "stock_name",
        "last_seen_date",
        "event_count_14d",
        "active_days_14d",
        "total_heat_14d",
        "limit_up_count_14d",
        "latest_reason",
        "confidence",
        "status",
        "match_level",
        "matched_term",
    ]
    for row in rows:
        item = dict(zip(keys, row))
        code = text_value(item, "code")
        if code:
            result.setdefault(code, []).append(item)
    return result


def load_theme_reason_map(config: MySqlConfig, codes: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    clean_codes = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not clean_codes:
        return {}
    alias_map = load_active_anchor_relation_alias_map(config)
    code_sql = ",".join(sql_string(code) for code in clean_codes)
    sql = f"""
    SELECT
      ranked.code, ranked.stock_name, ranked.anchor_name, ranked.theme_name, ranked.reason_text, ranked.source,
      COALESCE(DATE_FORMAT(ranked.source_date, '%Y-%m-%d'), ''),
      ranked.confidence, ranked.priority, ranked.source_key,
      COALESCE(a.status, ''),
      COALESCE(a.final_score, 0),
      COALESCE(DATE_FORMAT(a.last_seen_date, '%Y-%m-%d'), ''),
      COALESCE(a.today_event_count, 0)
    FROM (
      SELECT r.*,
             ROW_NUMBER() OVER (
               PARTITION BY r.code, r.anchor_name
               ORDER BY r.priority DESC, r.confidence DESC, r.source_date DESC, r.updated_at DESC
             ) AS rn
      FROM stock_theme_reason_bank r
      WHERE r.code IN ({code_sql})
        AND r.status='active'
        AND COALESCE(r.reason_text, '') <> ''
    ) ranked
    LEFT JOIN active_market_anchors a
      ON a.source='ths_hot_concept'
     AND a.anchor_name=ranked.anchor_name
     AND a.status <> 'expired'
    WHERE ranked.rn = 1
    ORDER BY
      ranked.code,
      CASE a.status WHEN 'active' THEN 3 WHEN 'watch' THEN 2 WHEN 'cooling' THEN 1 ELSE 0 END DESC,
      COALESCE(a.final_score, 0) DESC,
      ranked.priority DESC,
      ranked.confidence DESC;
    """
    try:
        rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return {}
    keys = [
        "code",
        "stock_name",
        "anchor_name",
        "theme_name",
        "reason_text",
        "source",
        "source_date",
        "confidence",
        "priority",
        "source_key",
        "active_anchor_status",
        "active_anchor_final_score",
        "active_anchor_last_seen_date",
        "active_anchor_today_event_count",
    ]
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        item = dict(zip(keys, row))
        code = text_value(item, "code")
        anchor_name = text_value(item, "anchor_name")
        theme_name = text_value(item, "theme_name")
        if not code:
            continue
        for term in unique_keep_order([anchor_name, theme_name], 4):
            normalized = normalized_reason_item(item, term, alias_map)
            key = canonical_anchor_name(normalized)
            if not key:
                continue
            existing = result.setdefault(code, {}).get(key)
            if not existing or theme_reason_candidate_rank(normalized) > theme_reason_candidate_rank(existing):
                result[code][key] = normalized
            if term and term != key:
                result[code].setdefault(term, normalized)
    return result


def upsert_stock_sql(row: dict[str, Any]) -> str:
    code = text_value(row, "code")
    if not code:
        return ""
    concepts = [item.strip() for item in text_value(row, "concepts").replace("、", ",").split(",") if item.strip()]
    name = text_value(row, "name")
    is_st = int("ST" in name.upper() or name.startswith("*ST"))
    return f"""
    INSERT INTO stocks(code, symbol, market, name, industry, sub_industry, is_st)
    VALUES({sql_string(code)}, {sql_string(text_value(row, "symbol"))}, {sql_string(text_value(row, "market"))},
           {sql_string(name)}, {sql_string(text_value(row, "industry"))}, {sql_string(text_value(row, "sub_industry"))},
           {is_st})
    ON DUPLICATE KEY UPDATE
      symbol=IF(VALUES(symbol)='', symbol, VALUES(symbol)),
      market=IF(VALUES(market)='', market, VALUES(market)),
      name=IF(VALUES(name)='', name, VALUES(name)),
      industry=IF(VALUES(industry)='', industry, VALUES(industry)),
      sub_industry=IF(VALUES(sub_industry)='', sub_industry, VALUES(sub_industry)),
      is_st=VALUES(is_st);
    """


def scan_run_id(result: dict[str, Any]) -> str:
    scanned_at = text_value(result, "scanned_at") or now_text()
    return "tdx_" + safe_id(scanned_at)


def record_scan_result(config: MySqlConfig, result: dict[str, Any]) -> str:
    run_id = scan_run_id(result)
    rows = result.get("rows") or []
    codes = [text_value(row, "code") for row in rows]
    active_anchor_map = load_active_anchor_member_map(config, codes)
    theme_reason_map = load_theme_reason_map(config, codes)
    anchor_stats, stock_roles = build_scan_roles(rows, active_anchor_map, theme_reason_map)
    statements = [
        f"""
        INSERT INTO scan_runs(
          run_id, scanned_at, source, scan_top, market_phase, accepted, ok,
          return_code, duration_ms, row_count, preserve_last, restored, error_text, raw_meta
        )
        VALUES(
          {sql_string(run_id)}, {sql_string(text_value(result, "scanned_at") or now_text())}, 'tdx_mover_watcher',
          {max(len(rows), to_int(result.get("row_count")))}, {sql_string(text_value(result, "phase"))},
          {sql_bool(result.get("accepted"))}, {sql_bool(result.get("ok"))}, {sql_int(result.get("returncode"))},
          {sql_int(result.get("duration_ms"))}, {sql_int(result.get("row_count"))},
          {sql_bool(result.get("preserve_last"))}, {sql_bool(result.get("restored"))},
          {sql_string(text_value(result, "output_tail"))}, {sql_json({k: v for k, v in result.items() if k != "rows"})}
        )
        ON DUPLICATE KEY UPDATE
          accepted=VALUES(accepted), ok=VALUES(ok), return_code=VALUES(return_code),
          duration_ms=VALUES(duration_ms), row_count=VALUES(row_count),
          preserve_last=VALUES(preserve_last), restored=VALUES(restored),
          error_text=VALUES(error_text), raw_meta=VALUES(raw_meta);
        """,
        f"DELETE FROM scan_stock_roles WHERE scan_run_id=(SELECT id FROM scan_runs WHERE run_id={sql_string(run_id)});",
        f"DELETE FROM scan_anchor_stats WHERE scan_run_id=(SELECT id FROM scan_runs WHERE run_id={sql_string(run_id)});",
    ]
    for row in rows:
        if not row.get("code"):
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO scan_movers(
              scan_run_id, captured_at, code, name, rank_speed, rank_pct_change, price, speed,
              pct_change, amount, amount_delta_15s, volume, volume_delta_15s, current_volume,
              bid1, ask1, industry, sub_industry, concepts, basis, raw_row
            )
            VALUES(
              (SELECT id FROM scan_runs WHERE run_id={sql_string(run_id)}),
              {sql_string(text_value(row, "captured_at") or text_value(result, "scanned_at") or now_text())},
              {sql_string(text_value(row, "code"))}, {sql_string(text_value(row, "name"))},
              {sql_int(row.get("rank_speed"))}, {sql_int(row.get("rank_pct_change"))},
              {sql_number(row.get("price"))}, {sql_number(row.get("speed"))}, {sql_number(row.get("pct_change"))},
              {sql_number(row.get("amount"))}, {sql_number(row.get("amount_delta_15s"))},
              {sql_int(row.get("vol"))}, {sql_int(row.get("vol_delta_15s"))}, {sql_int(row.get("cur_vol"))},
              {sql_number(row.get("bid1"))}, {sql_number(row.get("ask1"))},
              {sql_string(text_value(row, "industry"))}, {sql_string(text_value(row, "sub_industry"))},
              {sql_json([item.strip() for item in text_value(row, "concepts").replace("、", ",").split(",") if item.strip()])},
              {sql_string(text_value(row, "basis"))}, {sql_json(row)}
            )
            ON DUPLICATE KEY UPDATE
              rank_speed=VALUES(rank_speed), speed=VALUES(speed), pct_change=VALUES(pct_change),
              amount=VALUES(amount), amount_delta_15s=VALUES(amount_delta_15s),
              volume_delta_15s=VALUES(volume_delta_15s), raw_row=VALUES(raw_row);
            """
        )
    for row in anchor_stats:
        statements.append(
            f"""
            INSERT INTO scan_anchor_stats(
              scan_run_id, rank_no, anchor_type, anchor_name, member_count,
              leader_code, leader_name, core_code, core_name, total_amount,
              max_pct_change, avg_pct_change, max_speed, avg_speed,
              anchor_score, strength_label, raw_json
            )
            VALUES(
              (SELECT id FROM scan_runs WHERE run_id={sql_string(run_id)}),
              {sql_int(row.get("rank_no"))}, {sql_string(text_value(row, "anchor_type"))},
              {sql_string(text_value(row, "anchor_name"))}, {sql_int(row.get("member_count"))},
              {sql_string(text_value(row, "leader_code"))}, {sql_string(text_value(row, "leader_name"))},
              {sql_string(text_value(row, "core_code"))}, {sql_string(text_value(row, "core_name"))},
              {sql_number(row.get("total_amount"))}, {sql_number(row.get("max_pct_change"))},
              {sql_number(row.get("avg_pct_change"))}, {sql_number(row.get("max_speed"))},
              {sql_number(row.get("avg_speed"))}, {sql_number(row.get("anchor_score"))},
              {sql_string(text_value(row, "strength_label"))}, {sql_json(row)}
            );
            """
        )
    for row in stock_roles:
        statements.append(
            f"""
            INSERT INTO scan_stock_roles(
              scan_run_id, code, name, rank_no, primary_anchor_type, primary_anchor_name,
              anchor_member_count, role_label, leader_code, leader_name, core_code, core_name,
              role_score, role_reason, raw_json
            )
            VALUES(
              (SELECT id FROM scan_runs WHERE run_id={sql_string(run_id)}),
              {sql_string(text_value(row, "code"))}, {sql_string(text_value(row, "name"))},
              {sql_int(row.get("rank_no"))}, {sql_string(text_value(row, "primary_anchor_type"))},
              {sql_string(text_value(row, "primary_anchor_name"))}, {sql_int(row.get("anchor_member_count"))},
              {sql_string(text_value(row, "role_label"))}, {sql_string(text_value(row, "leader_code"))},
              {sql_string(text_value(row, "leader_name"))}, {sql_string(text_value(row, "core_code"))},
              {sql_string(text_value(row, "core_name"))}, {sql_number(row.get("role_score"))},
              {sql_string(text_value(row, "role_reason"))}, {sql_json(row)}
            );
            """
        )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return run_id


def evidence_status(meta: dict[str, Any]) -> str:
    evidence = meta.get("evidence") or {}
    if evidence.get("launched"):
        return "running"
    if evidence.get("queued"):
        return "pending"
    if evidence.get("reason") in ("disabled", "no_aggregated_rows", "no_evidence_candidates_after_filter"):
        return "skipped"
    return "pending"


def record_window_result(
    config: MySqlConfig,
    meta: dict[str, Any],
    window_rows: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    window_id = text_value(meta, "run_id")
    if not window_id:
        raise ValueError("window meta missing run_id")
    statements = [
        f"""
        INSERT INTO windows(
          window_id, started_at, ended_at, scan_interval_seconds, window_seconds,
          target_scan_count, accepted_scan_count, min_accepted_scan_count, status,
          aggregate_count, evidence_candidate_count, duration_ms, snapshot_dir, raw_meta
        )
        VALUES(
          {sql_string(window_id)}, {sql_string(text_value(meta, "window_started_at"))},
          {sql_string(text_value(meta, "window_ended_at"))}, {sql_int(meta.get("scan_interval"))},
          {sql_int(meta.get("window_seconds"))}, {sql_int(meta.get("target_scans"))},
          {sql_int(meta.get("accepted_scans"))}, {sql_int(meta.get("min_accepted_scans"))}, 'done',
          {len(window_rows)}, {len(evidence_rows)}, {sql_int(meta.get("duration_ms"))},
          {sql_string(str(Path(text_value(meta, "snapshot_top10_csv")).parent) if text_value(meta, "snapshot_top10_csv") else "")},
          {sql_json(meta)}
        )
        ON DUPLICATE KEY UPDATE
          started_at=VALUES(started_at), ended_at=VALUES(ended_at), accepted_scan_count=VALUES(accepted_scan_count),
          aggregate_count=VALUES(aggregate_count), evidence_candidate_count=VALUES(evidence_candidate_count),
          duration_ms=VALUES(duration_ms), snapshot_dir=VALUES(snapshot_dir), raw_meta=VALUES(raw_meta),
          status=VALUES(status);
        """,
        f"DELETE wm FROM window_movers wm JOIN windows w ON w.id=wm.window_id WHERE w.window_id={sql_string(window_id)};",
        f"DELETE wsr FROM window_stock_roles wsr JOIN windows w ON w.id=wsr.window_id WHERE w.window_id={sql_string(window_id)};",
        f"DELETE wss FROM window_sector_stats wss JOIN windows w ON w.id=wss.window_id WHERE w.window_id={sql_string(window_id)};",
        f"DELETE ec FROM evidence_candidates ec JOIN windows w ON w.id=ec.window_id WHERE w.window_id={sql_string(window_id)};",
    ]
    for row in window_rows:
        if not row.get("code"):
            continue
        if is_excluded_stock_name(text_value(row, "name")):
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO window_movers(
              window_id, code, name, rank_no, appearance_count, appearance_rate, best_rank_speed,
              avg_rank_speed, max_speed, max_pct_change, latest_price, latest_pct_change, amount,
              max_amount_delta_15s, max_volume_delta_15s,
              first_seen_at, latest_seen_at, previous_window_rank, rank_delta, is_new_entry,
              burst_score, sustained_score, window_score, raw_row
            )
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(text_value(row, "code"))}, {sql_string(text_value(row, "name"))},
              {sql_int(row.get("rank_speed"))}, {sql_int(row.get("appearance_count"))},
              {sql_number(row.get("appearance_rate"))}, {sql_int(row.get("best_rank_speed"))},
              {sql_number(row.get("avg_rank_speed"))}, {sql_number(row.get("max_speed"))},
              {sql_number(row.get("max_pct_change"))}, {sql_number(row.get("price"))},
              {sql_number(row.get("pct_change"))}, {sql_number(row.get("amount"))},
              {sql_number(row.get("max_amount_delta_15s"))}, {sql_int(row.get("max_vol_delta_15s"))},
              {sql_string(text_value(row, "first_seen_at"))}, {sql_string(text_value(row, "latest_seen_at"))},
              {sql_int(row.get("previous_window_rank"))}, {sql_int(row.get("rank_delta"))},
              {1 if str(row.get("is_new_entry", "")).lower() == "yes" else 0},
              {sql_number(row.get("burst_score"))}, {sql_number(row.get("sustained_score"))},
              {sql_number(row.get("window_score"))}, {sql_json(row)}
            );
            """
        )
    for row in meta.get("sector_stats") or []:
        sector_key = text_value(row, "sector_key")
        if not sector_key:
            continue
        statements.append(
            f"""
            INSERT INTO window_sector_stats(
              window_id, rank_no, sector_key, sector_type, stock_count,
              leader_code, leader_name, core_code, core_name, follower_count,
              total_amount, avg_pct_change, max_speed, sector_score,
              strength_label, hot_concepts, summary, raw_json
            )
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_int(row.get("rank_no"))}, {sql_string(sector_key)}, {sql_string(text_value(row, "sector_type"))},
              {sql_int(row.get("stock_count"))}, {sql_string(text_value(row, "leader_code"))},
              {sql_string(text_value(row, "leader_name"))}, {sql_string(text_value(row, "core_code"))},
              {sql_string(text_value(row, "core_name"))}, {sql_int(row.get("follower_count"))},
              {sql_number(row.get("total_amount"))}, {sql_number(row.get("avg_pct_change"))},
              {sql_number(row.get("max_speed"))}, {sql_number(row.get("sector_score"))},
              {sql_string(text_value(row, "strength_label"))}, {sql_json(row.get("hot_concepts") or [])},
              {sql_string(text_value(row, "summary"))}, {sql_json(row)}
            );
            """
        )
    for row in meta.get("stock_roles") or []:
        code = text_value(row, "code")
        if not code:
            continue
        if is_excluded_stock_name(text_value(row, "name")):
            continue
        statements.append(
            f"""
            INSERT INTO window_stock_roles(
              window_id, code, name, rank_no, sector_key, sector_type,
              sector_stock_count, role_label, role_score, role_reason,
              risk_flags, raw_json
            )
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(code)}, {sql_string(text_value(row, "name"))}, {sql_int(row.get("rank_no"))},
              {sql_string(text_value(row, "sector_key"))}, {sql_string(text_value(row, "sector_type"))},
              {sql_int(row.get("sector_stock_count"))}, {sql_string(text_value(row, "role_label"))},
              {sql_number(row.get("role_score"))}, {sql_string(text_value(row, "role_reason"))},
              {sql_string(text_value(row, "risk_flags"))}, {sql_json(row)}
            );
            """
        )
    for row in evidence_rows:
        if not row.get("code"):
            continue
        if is_excluded_stock_name(text_value(row, "name")):
            continue
        pct = to_float(row.get("pct_change")) or 0.0
        name = text_value(row, "name")
        is_st = int("ST" in name.upper() or name.startswith("*ST"))
        statements.append(
            f"""
            INSERT INTO evidence_candidates(window_id, code, rank_no, selection_reason, min_pct_pass, is_st, status)
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(text_value(row, "code"))}, {sql_int(row.get("rank_speed"))},
              'window_top10_after_filter', {1 if pct >= 0.01 else 0}, {is_st}, 'pending'
            )
            ON DUPLICATE KEY UPDATE rank_no=VALUES(rank_no), min_pct_pass=VALUES(min_pct_pass),
              is_st=VALUES(is_st), status=VALUES(status);
            """
        )
    evidence = meta.get("evidence") or {}
    status = evidence_status(meta)
    job_id = "ev_" + window_id
    statements.append(
        f"""
        INSERT INTO evidence_jobs(
          job_id, window_id, status, started_at, worker_pid, error_text, request_json, result_json
        )
        VALUES(
          {sql_string(job_id)}, (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
          {sql_string(status)}, {sql_string(text_value(meta, "updated_at")) if status == "running" else "NULL"},
          {sql_int(evidence.get("pid"))}, {sql_string(text_value(evidence, "reason"))},
          {sql_json(meta.get("evidence_request") or {})}, {sql_json(evidence)}
        )
        ON DUPLICATE KEY UPDATE
          status=VALUES(status), started_at=COALESCE(VALUES(started_at), started_at),
          worker_pid=VALUES(worker_pid), error_text=VALUES(error_text),
          request_json=VALUES(request_json), result_json=VALUES(result_json);
        """
    )
    statements.append(
        f"""
        INSERT INTO pipeline_events(window_id, event_type, stage, status, duration_ms, message, payload_json)
        VALUES((SELECT id FROM windows WHERE window_id={sql_string(window_id)}), 'window_done', 'window_aggregate',
               'ok', {sql_int(meta.get("duration_ms"))}, '', {sql_json(meta)});
        """
    )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return {
        "mysql": True,
        "window_id": window_id,
        "window_rows": len(window_rows),
        "sector_stats": len(meta.get("sector_stats") or []),
        "stock_roles": len(meta.get("stock_roles") or []),
        "evidence_candidates": len(evidence_rows),
    }


def latest_window_rank_rows(config: MySqlConfig) -> list[dict[str, str]]:
    sql = """
    SELECT rank_no, code
    FROM v_latest_window_movers
    ORDER BY rank_no;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    return [{"rank_speed": row[0], "code": row[1]} for row in rows if len(row) >= 2]


def enqueue_hot_evidence_task(config: MySqlConfig, window_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    dedupe_key = f"hot_evidence_worker:{window_id}"
    task_id = f"hot_evidence_worker_{window_id}"
    statements = f"""
    INSERT INTO scheduled_tasks(
      task_id, task_name, task_description, task_kind, task_type, enabled,
      schedule_type, update_interval_seconds, priority, timeout_seconds,
      max_attempts, payload_template_json, dedupe_key_template
    )
    VALUES(
      {sql_string(task_id)}, {sql_string("Hot Evidence Worker " + window_id)},
      {sql_string("Run evidence worker for one completed mover window.")},
      'hot_evidence_worker', 'hot', 0, 'manual', 600, 10,
      {int(payload.get("timeout_seconds", 1200))}, 2, {sql_json(payload)}, {sql_string(dedupe_key)}
    )
    ON DUPLICATE KEY UPDATE
      payload_template_json=VALUES(payload_template_json),
      timeout_seconds=VALUES(timeout_seconds),
      updated_at=NOW(3);

    INSERT IGNORE INTO task_queue(
      task_id, task_kind, task_type, priority, status, payload_json, dedupe_key,
      not_before, max_attempts, timeout_seconds
    )
    VALUES(
      {sql_string(task_id)}, 'hot_evidence_worker', 'hot', 10, 'pending',
      {sql_json(payload)}, {sql_string(dedupe_key)}, NOW(3), 2,
      {int(payload.get("timeout_seconds", 1200))}
    );
    SELECT ROW_COUNT();
    """
    rows = mysql_rows(run_mysql(config, statements, batch=True))
    inserted = bool(rows and rows[-1] and int(rows[-1][0]) > 0)
    return {"queued": True, "inserted": inserted, "task_id": task_id, "dedupe_key": dedupe_key}


def window_evidence_candidate_rows(config: MySqlConfig, window_id: str) -> list[dict[str, str]]:
    sql = f"""
    SELECT
      DATE_FORMAT(w.ended_at, '%Y-%m-%d %H:%i:%s') AS captured_at,
      ec.rank_no,
      '' AS rank_pct_change,
      '' AS market,
      wm.code,
      wm.name,
      COALESCE(wm.latest_price, '') AS price,
      COALESCE(wm.max_speed, '') AS speed,
      COALESCE(wm.latest_pct_change, '') AS pct_change,
      '' AS last_close,
      '' AS open_price,
      '' AS high,
      '' AS low,
      COALESCE(wm.amount, '') AS amount,
      '' AS vol,
      '' AS cur_vol,
      '' AS bid1,
      '' AS ask1,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.industry')), s.industry, '') AS industry,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.sub_industry')), s.sub_industry, '') AS sub_industry,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.industry_code')), '') AS industry_code,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.sub_industry_code')), '') AS sub_industry_code,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.concepts')), '') AS concepts,
      COALESCE(JSON_UNQUOTE(JSON_EXTRACT(wm.raw_row, '$.concept_count')), '') AS concept_count,
      '' AS server,
      'mysql_window_evidence_candidate' AS basis
    FROM evidence_candidates ec
    JOIN windows w ON w.id = ec.window_id
    JOIN window_movers wm ON wm.window_id = ec.window_id AND wm.code = ec.code
    LEFT JOIN stocks s ON s.code = wm.code
    WHERE w.window_id = {sql_string(window_id)}
      AND ec.status IN ('pending', 'running', 'done')
    ORDER BY ec.rank_no;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    columns = [
        "captured_at",
        "rank_speed",
        "rank_pct_change",
        "market",
        "code",
        "name",
        "price",
        "speed",
        "pct_change",
        "last_close",
        "open",
        "high",
        "low",
        "amount",
        "vol",
        "cur_vol",
        "bid1",
        "ask1",
        "industry",
        "sub_industry",
        "industry_code",
        "sub_industry_code",
        "concepts",
        "concept_count",
        "server",
        "basis",
    ]
    return [dict(zip(columns, row)) for row in rows if len(row) >= len(columns)]


def strength_to_enum(value: str) -> str:
    mapping = {
        "待补证据": "pending",
        "弱证据": "weak",
        "中等证据": "medium",
        "强证据": "strong",
        "证据中断": "pending",
        "硬证据较强": "strong",
        "硬证据中等": "medium",
        "硬证据待核": "medium",
        "补充证据较强": "medium",
        "补充证据中等": "medium",
        "线索较强": "medium",
        "线索中等": "weak",
        "线索偏弱": "weak",
        "pending": "pending",
        "weak": "weak",
        "medium": "medium",
        "strong": "strong",
    }
    return mapping.get(value, "pending")


def import_evidence_layer_rows(config: MySqlConfig, rows: list[dict[str, Any]], window_id: str) -> int:
    if not rows or not window_id:
        return 0
    statements = [
        f"DELETE el FROM evidence_layers el JOIN windows w ON w.id=el.window_id WHERE w.window_id={sql_string(window_id)};"
    ]
    for row in rows:
        if not row.get("code"):
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO evidence_layers(
              window_id, code, rank_no, market_evidence, sector_evidence, community_status,
              community_main_claim, official_status, company_positioning, hard_evidence_summary,
              evidence_strength, evidence_gaps, next_evidence_action, why_hypothesis, raw_json, built_at
            )
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(text_value(row, "code"))}, {sql_int(row.get("rank_speed"))},
              {sql_string(text_value(row, "market_evidence"))}, {sql_string(text_value(row, "sector_evidence"))},
              {sql_string(text_value(row, "community_status") if text_value(row, "community_status") in ("missing", "weak", "medium", "strong") else "missing")},
              {sql_string(text_value(row, "community_main_claim"))}, {sql_string(text_value(row, "official_status"))},
              {sql_string(text_value(row, "company_positioning"))}, {sql_string(text_value(row, "hard_evidence_summary"))},
              {sql_string(strength_to_enum(text_value(row, "evidence_strength")))}, {sql_string(text_value(row, "evidence_gaps"))},
              {sql_string(text_value(row, "next_evidence_action"))}, {sql_string(text_value(row, "why_hypothesis"))},
              {sql_json(row)}, {sql_string(text_value(row, "built_at") or now_text())}
            );
            """
        )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(rows)


def import_evidence_layer_csv(config: MySqlConfig, path: Path, window_id: str) -> int:
    return import_evidence_layer_rows(config, read_csv(path), window_id)


def source_part(source_status: str, prefix: str) -> str:
    for part in source_status.split(";"):
        text = part.strip()
        if text.startswith(prefix):
            return text
    return ""


def import_company_profiles_csv(config: MySqlConfig, path: Path) -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        code = text_value(row, "code")
        if not code:
            continue
        stock_name = text_value(row, "stock_name") or text_value(row, "name")
        has_profile = any(
            text_value(row, key).strip()
            for key in ["stock_name", "name", "company_highlights", "main_business", "sw_industry", "concept_tags", "latest_management_business_plan"]
        )
        if text_value(row, "source_status") == "cache_missing" and not has_profile:
            continue
        stock_row = dict(row)
        if not text_value(stock_row, "industry"):
            stock_row["industry"] = text_value(row, "sw_industry")
        if not text_value(stock_row, "concepts"):
            stock_row["concepts"] = text_value(row, "concept_tags")
        statements.append(upsert_stock_sql(stock_row))
        statements.append(
            f"""
            INSERT INTO stock_company_profiles(
              code, stock_name, company_highlights, main_business, sw_industry, concept_tags, latest_management_business_plan
            )
            VALUES(
              {sql_string(code)}, {sql_string(stock_name)}, {sql_string(text_value(row, "company_highlights"))},
              {sql_string(text_value(row, "main_business"))}, {sql_string(text_value(row, "sw_industry"))},
              {sql_string(text_value(row, "concept_tags"))}, {sql_string(text_value(row, "latest_management_business_plan"))}
            )
            ON DUPLICATE KEY UPDATE
              stock_name=IF(VALUES(stock_name)='', stock_name, VALUES(stock_name)),
              company_highlights=IF(VALUES(company_highlights)='', company_highlights, VALUES(company_highlights)),
              main_business=IF(VALUES(main_business)='', main_business, VALUES(main_business)),
              sw_industry=IF(VALUES(sw_industry)='', sw_industry, VALUES(sw_industry)),
              concept_tags=IF(VALUES(concept_tags)='', concept_tags, VALUES(concept_tags)),
              latest_management_business_plan=IF(VALUES(latest_management_business_plan)='', latest_management_business_plan, VALUES(latest_management_business_plan));
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements) // 2


def import_ths_root_evidence_json(config: MySqlConfig, path: Path) -> dict[str, int]:
    payload = read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    if not isinstance(rows, list) or not rows:
        return {"snapshots": 0, "items": 0}
    statements: list[str] = []
    affected_cache_rows: list[dict[str, Any]] = []
    affected_codes: set[str] = set()
    snapshot_count = 0
    item_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = text_value(row, "code")
        if not code:
            continue
        statements.append(upsert_stock_sql(row))
        snapshot = row.get("ths_root_snapshot") if isinstance(row.get("ths_root_snapshot"), dict) else {}
        if snapshot:
            statements.append(
                f"""
                INSERT INTO ths_root_snapshots(
                  code, stock_name, market_id, root_url, fetched_at, source_status, item_count,
                  profile_json, sections_json, raw_json
                )
                VALUES(
                  {sql_string(code)}, {sql_string(text_value(snapshot, "stock_name") or text_value(row, "stock_name") or text_value(row, "name"))},
                  {sql_string(text_value(snapshot, "market_id"))}, {sql_string(text_value(snapshot, "root_url"))},
                  {sql_string(text_value(snapshot, "fetched_at") or text_value(row, "fetched_at") or now_text())},
                  {sql_string(text_value(snapshot, "source_status") or text_value(row, "source_status"))},
                  {sql_int(snapshot.get("item_count"))},
                  {sql_json(snapshot.get("profile_json") or {})},
                  {sql_json(snapshot.get("sections_json") or {})},
                  {sql_json(snapshot.get("raw_json") or {})}
                )
                ON DUPLICATE KEY UPDATE
                  stock_name=VALUES(stock_name),
                  market_id=VALUES(market_id),
                  root_url=VALUES(root_url),
                  source_status=VALUES(source_status),
                  item_count=VALUES(item_count),
                  profile_json=VALUES(profile_json),
                  sections_json=VALUES(sections_json),
                  raw_json=VALUES(raw_json);
                """
            )
            snapshot_count += 1
        items = row.get("ths_root_items") if isinstance(row.get("ths_root_items"), list) else []
        if items and code not in affected_codes:
            affected_codes.add(code)
            affected_cache_rows.append(
                {
                    "code": code,
                    "stock_name": text_value(row, "stock_name") or text_value(row, "name"),
                }
            )
        for item in items:
            if not isinstance(item, dict):
                continue
            item_kind = text_value(item, "item_kind") or "other"
            if item_kind not in {"important_event", "hot_news", "announcement", "theme_point", "other"}:
                item_kind = "other"
            item_key_value = text_value(item, "item_key") or safe_id(
                "|".join(
                    [
                        item_kind,
                        text_value(item, "item_date"),
                        text_value(item, "title"),
                        text_value(item, "url"),
                        text_value(item, "content"),
                    ]
                )
            )[:64]
            statements.append(
                f"""
                INSERT INTO stock_ths_root_items(
                  code, stock_name, item_kind, item_key, source_section, source_rank, item_date,
                  title, content, url, tags, importance, source_status, raw_json, collected_at
                )
                VALUES(
                  {sql_string(code)}, {sql_string(text_value(item, "stock_name") or text_value(row, "stock_name") or text_value(row, "name"))},
                  {sql_string(item_kind)}, {sql_string(item_key_value)}, {sql_string(text_value(item, "source_section"))},
                  {sql_int(item.get("source_rank"))}, {sql_string(text_value(item, "item_date") or None)},
                  {sql_string(limit_text(text_value(item, "title"), 512))}, {sql_string(text_value(item, "content"))},
                  {sql_string(text_value(item, "url"))}, {sql_json(item.get("tags") or [])},
                  {sql_int(item.get("importance"))}, {sql_string(text_value(item, "source_status"))},
                  {sql_json(item.get("raw_json") or {})}, {sql_string(text_value(row, "fetched_at") or now_text())}
                )
                ON DUPLICATE KEY UPDATE
                  stock_name=VALUES(stock_name),
                  source_section=VALUES(source_section),
                  source_rank=VALUES(source_rank),
                  item_date=COALESCE(VALUES(item_date), item_date),
                  title=VALUES(title),
                  content=VALUES(content),
                  url=VALUES(url),
                  tags=VALUES(tags),
                  importance=VALUES(importance),
                  source_status=VALUES(source_status),
                  raw_json=VALUES(raw_json),
                  collected_at=VALUES(collected_at);
                """
            )
            item_count += 1
    if not statements:
        return {"snapshots": 0, "items": 0}
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    dirty_cache = 0
    if affected_cache_rows:
        trade_date = latest_root_evidence_trade_date(config) or date.today().strftime("%Y-%m-%d")
        dirty_cache = enqueue_root_evidence_cache_dirty_many(
            config,
            trade_date,
            affected_cache_rows,
            reason="stock_ths_root_items_updated",
            priority=25,
        )
    return {"snapshots": snapshot_count, "items": item_count, "dirty_cache": dirty_cache}


def import_market_news_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        source = text_value(row, "source") or "other"
        if source not in {"cls", "wallstreetcn", "other"}:
            source = "other"
        item_kind = text_value(row, "item_kind") or "other"
        if item_kind not in {"headline", "important", "red", "live", "other"}:
            item_kind = "other"
        source_item_id = text_value(row, "source_item_id") or safe_id(
            "|".join([source, text_value(row, "published_at"), text_value(row, "title"), text_value(row, "url")])
        )[:128]
        statements.append(
            f"""
            INSERT INTO market_news_items(
              source, source_item_id, item_kind, published_at, title, content, url,
              tags, importance, source_status, raw_json, collected_at
            )
            VALUES(
              {sql_string(source)}, {sql_string(source_item_id)}, {sql_string(item_kind)},
              {sql_string(text_value(row, "published_at") or None)},
              {sql_string(limit_text(text_value(row, "title"), 512))},
              {sql_string(text_value(row, "content"))}, {sql_string(text_value(row, "url"))},
              {sql_json(row.get("tags") or [])}, {sql_int(row.get("importance"))},
              {sql_string(text_value(row, "source_status"))}, {sql_json(row.get("raw_json") or row)},
              {sql_string(text_value(row, "collected_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              item_kind=VALUES(item_kind),
              published_at=COALESCE(VALUES(published_at), published_at),
              title=VALUES(title),
              content=VALUES(content),
              url=VALUES(url),
              tags=VALUES(tags),
              importance=VALUES(importance),
              source_status=VALUES(source_status),
              raw_json=VALUES(raw_json),
              collected_at=VALUES(collected_at);
            """
        )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements)


def import_market_news_json(config: MySqlConfig, path: Path) -> int:
    payload = read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return import_market_news_rows(config, rows if isinstance(rows, list) else [])


def import_daily_market_theme_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        trade_date = text_value(row, "trade_date")
        theme_name = text_value(row, "theme_name")
        if not trade_date or not theme_name:
            continue
        statements.append(
            f"""
            INSERT INTO daily_market_themes(
              trade_date, theme_name, keywords, source_count, source_titles, source_item_ids,
              related_industries, related_concepts, importance_score, summary, raw_json, generated_at
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(theme_name)}, {sql_json(row.get("keywords") or [])},
              {sql_int(row.get("source_count"))}, {sql_json(row.get("source_titles") or [])},
              {sql_json(row.get("source_item_ids") or [])}, {sql_json(row.get("related_industries") or [])},
              {sql_json(row.get("related_concepts") or [])}, {sql_number(row.get("importance_score"))},
              {sql_string(text_value(row, "summary"))}, {sql_json(row)}, {sql_string(text_value(row, "generated_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              keywords=VALUES(keywords),
              source_count=VALUES(source_count),
              source_titles=VALUES(source_titles),
              source_item_ids=VALUES(source_item_ids),
              related_industries=VALUES(related_industries),
              related_concepts=VALUES(related_concepts),
              importance_score=VALUES(importance_score),
              summary=VALUES(summary),
              raw_json=VALUES(raw_json),
              generated_at=VALUES(generated_at);
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements)


def import_daily_market_themes_json(config: MySqlConfig, path: Path) -> int:
    payload = read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return import_daily_market_theme_rows(config, rows if isinstance(rows, list) else [])


def import_auction_candidate_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    trade_dates = sorted({text_value(row, "trade_date") for row in rows if text_value(row, "trade_date")})
    for trade_date in trade_dates:
        statements.append(f"DELETE FROM auction_candidates WHERE trade_date={sql_string(trade_date)};")
    for row in rows:
        trade_date = text_value(row, "trade_date")
        code = text_value(row, "code")
        if not trade_date or not code:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_candidates(
              trade_date, captured_at, rank_no, code, stock_name, auction_price, preclose,
              auction_pct, auction_amount, matched_volume, buy_pressure, industry, sub_industry,
              concepts, theme_matches, theme_score, sector_hot_count, concept_hot_count,
              resonance_score, score, risk_flags, raw_json
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(text_value(row, "captured_at") or now_text())},
              {sql_int(row.get("rank_no"))}, {sql_string(code)}, {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_number(row.get("auction_price"))}, {sql_number(row.get("preclose"))},
              {sql_number(row.get("auction_pct"))}, {sql_number(row.get("auction_amount"))},
              {sql_int(row.get("matched_volume"))}, {sql_number(row.get("buy_pressure"))},
              {sql_string(text_value(row, "industry"))}, {sql_string(text_value(row, "sub_industry"))},
              {sql_json(row.get("concepts") or [])}, {sql_json(row.get("theme_matches") or [])},
              {sql_number(row.get("theme_score"))}, {sql_int(row.get("sector_hot_count"))},
              {sql_int(row.get("concept_hot_count"))}, {sql_number(row.get("resonance_score"))},
              {sql_number(row.get("score"))}, {sql_string(text_value(row, "risk_flags"))}, {sql_json(row)}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len([row for row in rows if text_value(row, "trade_date") and text_value(row, "code")])


def import_auction_minute_analysis_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    minute_keys = sorted(
        {
            (text_value(row, "trade_date"), text_value(row, "snapshot_minute"))
            for row in rows
            if text_value(row, "trade_date") and text_value(row, "snapshot_minute")
        }
    )
    for trade_date, snapshot_minute in minute_keys:
        statements.append(
            f"""
            DELETE FROM auction_minute_analysis
            WHERE trade_date={sql_string(trade_date)}
              AND snapshot_minute={sql_string(snapshot_minute)};
            """
        )
    for row in rows:
        trade_date = text_value(row, "trade_date")
        snapshot_minute = text_value(row, "snapshot_minute")
        code = text_value(row, "code")
        analysis_kind = text_value(row, "analysis_kind")
        if not trade_date or not snapshot_minute or not code or not analysis_kind:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_minute_analysis(
              trade_date, snapshot_minute, captured_at, analysis_kind, rank_no, code, stock_name,
              auction_price, preclose, auction_pct, auction_amount, matched_volume,
              bid1, ask1, bid_vol1, ask_vol1, limit_side, limit_price, seal_volume, seal_amount,
              buy_pressure, industry, sub_industry, concepts, theme_matches, theme_score,
              sector_hot_count, concept_hot_count, resonance_score, score, risk_flags, raw_json
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(snapshot_minute)}, {sql_string(text_value(row, "captured_at") or now_text())},
              {sql_string(analysis_kind)}, {sql_int(row.get("rank_no"))}, {sql_string(code)},
              {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_number(row.get("auction_price"))}, {sql_number(row.get("preclose"))},
              {sql_number(row.get("auction_pct"))}, {sql_number(row.get("auction_amount"))},
              {sql_int(row.get("matched_volume"))}, {sql_number(row.get("bid1"))}, {sql_number(row.get("ask1"))},
              {sql_int(row.get("bid_vol1"))}, {sql_int(row.get("ask_vol1"))},
              {sql_string(text_value(row, "limit_side") or "none")}, {sql_number(row.get("limit_price"))},
              {sql_int(row.get("seal_volume"))}, {sql_number(row.get("seal_amount"))},
              {sql_number(row.get("buy_pressure"))}, {sql_string(text_value(row, "industry"))},
              {sql_string(text_value(row, "sub_industry"))}, {sql_json(row.get("concepts") or [])},
              {sql_json(row.get("theme_matches") or [])}, {sql_number(row.get("theme_score"))},
              {sql_int(row.get("sector_hot_count"))}, {sql_int(row.get("concept_hot_count"))},
              {sql_number(row.get("resonance_score"))}, {sql_number(row.get("score"))},
              {sql_string(text_value(row, "risk_flags"))}, {sql_json(row)}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(
        [
            row
            for row in rows
            if text_value(row, "trade_date")
            and text_value(row, "snapshot_minute")
            and text_value(row, "analysis_kind")
            and text_value(row, "code")
        ]
    )


def import_auction_trend_summary_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    trade_dates = sorted({text_value(row, "trade_date") for row in rows if text_value(row, "trade_date")})
    for trade_date in trade_dates:
        statements.append(f"DELETE FROM auction_trend_summary WHERE trade_date={sql_string(trade_date)};")
    for row in rows:
        trade_date = text_value(row, "trade_date")
        code = text_value(row, "code")
        if not trade_date or not code:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_trend_summary(
              trade_date, code, stock_name, first_seen_minute, last_seen_minute,
              minute_count, pct_top_count, limit_up_count, limit_down_count,
              best_pct_rank, final_candidate_rank, first_auction_pct, last_auction_pct,
              pct_delta, first_auction_amount, last_auction_amount, amount_delta,
              amount_growth_ratio, max_seal_amount, last_seal_amount, theme_score,
              theme_matches, sector_hot_count, concept_hot_count, final_score,
              trend_score, trend_label, key_points, action_hint, raw_json, generated_at
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(code)}, {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_string(text_value(row, "first_seen_minute") or None)}, {sql_string(text_value(row, "last_seen_minute") or None)},
              {sql_int(row.get("minute_count"))}, {sql_int(row.get("pct_top_count"))},
              {sql_int(row.get("limit_up_count"))}, {sql_int(row.get("limit_down_count"))},
              {sql_int(row.get("best_pct_rank"))}, {sql_int(row.get("final_candidate_rank"))},
              {sql_number(row.get("first_auction_pct"))}, {sql_number(row.get("last_auction_pct"))},
              {sql_number(row.get("pct_delta"))}, {sql_number(row.get("first_auction_amount"))},
              {sql_number(row.get("last_auction_amount"))}, {sql_number(row.get("amount_delta"))},
              {sql_number(row.get("amount_growth_ratio"))}, {sql_number(row.get("max_seal_amount"))},
              {sql_number(row.get("last_seal_amount"))}, {sql_number(row.get("theme_score"))},
              {sql_json(row.get("theme_matches") or [])}, {sql_int(row.get("sector_hot_count"))},
              {sql_int(row.get("concept_hot_count"))}, {sql_number(row.get("final_score"))},
              {sql_number(row.get("trend_score"))}, {sql_string(text_value(row, "trend_label"))},
              {sql_json(row.get("key_points") or [])}, {sql_string(text_value(row, "action_hint"))},
              {sql_json(row)}, {sql_string(text_value(row, "generated_at") or now_text())}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len([row for row in rows if text_value(row, "trade_date") and text_value(row, "code")])


def import_auction_candidates_json(config: MySqlConfig, path: Path) -> int:
    payload = read_json(path)
    rows = payload.get("rows") if isinstance(payload, dict) else []
    return import_auction_candidate_rows(config, rows if isinstance(rows, list) else [])


def import_stock_universe_csv(config: MySqlConfig, path: Path) -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        code = text_value(row, "code")
        if not code:
            continue
        statements.append(upsert_stock_sql(row))
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements)


def cold_profile_missing_codes(config: MySqlConfig, limit: int, offset: int = 0) -> list[str]:
    sql = f"""
    SELECT s.code
    FROM stocks s
    LEFT JOIN stock_company_profiles p ON p.code = s.code
    WHERE s.is_st = 0
      AND (
        p.code IS NULL
        OR COALESCE(p.main_business, '') = ''
        OR COALESCE(p.company_highlights, '') = ''
      )
    ORDER BY s.code
    LIMIT {int(limit)} OFFSET {int(offset)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    return [row[0] for row in rows if row]


def cold_profile_counts(config: MySqlConfig) -> dict[str, int]:
    sql = """
    SELECT 'stocks', COUNT(*) FROM stocks;
    SELECT 'profiles', COUNT(*) FROM stock_company_profiles;
    SELECT 'with_stock_name', COUNT(*) FROM stock_company_profiles WHERE COALESCE(stock_name, '') <> '';
    SELECT 'with_company_highlights', COUNT(*) FROM stock_company_profiles WHERE COALESCE(company_highlights, '') <> '';
    SELECT 'with_main_business', COUNT(*) FROM stock_company_profiles WHERE COALESCE(main_business, '') <> '';
    SELECT 'with_sw_industry', COUNT(*) FROM stock_company_profiles WHERE COALESCE(sw_industry, '') <> '';
    SELECT 'with_concept_tags', COUNT(*) FROM stock_company_profiles WHERE COALESCE(concept_tags, '') <> '';
    SELECT 'with_management_business_plan', COUNT(*) FROM stock_company_profiles WHERE COALESCE(latest_management_business_plan, '') <> '';
    SELECT 'missing_core_profile', COUNT(*)
    FROM stocks s
    LEFT JOIN stock_company_profiles p ON p.code = s.code
    WHERE s.is_st = 0
      AND (
        p.code IS NULL
        OR COALESCE(p.main_business, '') = ''
        OR COALESCE(p.company_highlights, '') = ''
      );
    """
    values: dict[str, int] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) >= 2:
            values[row[0]] = to_int(row[1])
    return values


def post_id_from_url(url: str) -> str:
    match = re.search(r"/(\d+)$", url.strip())
    if match:
        return match.group(1)
    return url.strip()[-128:]


def signal_quality_enum(value: str) -> str:
    text = value.strip().lower()
    if text in {"high", "strong"}:
        return "strong"
    if text in {"medium", "mid"}:
        return "medium"
    if text in {"low", "weak", "noise", "interrupted"}:
        return "weak"
    return "missing"


def split_terms(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[、,，|;；]+", value or "") if item.strip()]


def limit_text(value: str, max_len: int) -> str:
    text = value or ""
    return text[:max_len]


def has_replacement_garble(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values)
    return "�" in text or "\ufffd" in text


def import_community_posts_csv(config: MySqlConfig, path: Path) -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        code = text_value(row, "code")
        url = text_value(row, "detail_url")
        post_id = post_id_from_url(url)
        if not code or not post_id:
            continue
        if has_replacement_garble(text_value(row, "user"), text_value(row, "title"), text_value(row, "text")):
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO community_posts(
              code, platform, post_id, url, author_name, title, content, post_time,
              like_count, comment_count, repost_count, raw_json, collected_at
            )
            VALUES(
              {sql_string(code)}, 'xueqiu', {sql_string(post_id)}, {sql_string(url)},
              {sql_string(text_value(row, "user"))}, {sql_string(text_value(row, "title"))},
              {sql_string(text_value(row, "text"))}, {sql_string(text_value(row, "time_hint") or None)},
              {sql_int(row.get("like_count"))}, {sql_int(row.get("comment_count"))},
              {sql_int(row.get("repost_count"))}, {sql_json(row)},
              {sql_string(text_value(row, "fetched_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              code=VALUES(code),
              url=IF(VALUES(url)='', url, VALUES(url)),
              author_name=IF(VALUES(author_name)='', author_name, VALUES(author_name)),
              title=IF(VALUES(title)='', title, VALUES(title)),
              content=IF(VALUES(content)='', content, VALUES(content)),
              post_time=COALESCE(VALUES(post_time), post_time),
              like_count=VALUES(like_count),
              comment_count=VALUES(comment_count),
              repost_count=VALUES(repost_count),
              raw_json=VALUES(raw_json),
              collected_at=VALUES(collected_at);
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements) // 2


def import_community_evidence_csv(
    config: MySqlConfig,
    evidence_path: Path,
    narrative_path: Path,
    hot_posts_path: Path,
    window_id: str,
) -> int:
    evidence_rows = read_csv(evidence_path)
    if not evidence_rows or not window_id:
        return 0
    candidate_rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT ec.code
            FROM evidence_candidates ec
            JOIN windows w ON w.id = ec.window_id
            WHERE w.window_id = {sql_string(window_id)};
            """,
            batch=True,
        )
    )
    candidate_codes = {row[0] for row in candidate_rows if row}
    if not candidate_codes:
        return 0
    narrative_by_code = {text_value(row, "code"): row for row in read_csv(narrative_path) if text_value(row, "code")}
    posts_by_code: dict[str, list[dict[str, str]]] = {}
    for post in read_csv(hot_posts_path):
        posts_by_code.setdefault(text_value(post, "code"), []).append(post)

    statements: list[str] = []
    for row in evidence_rows:
        code = text_value(row, "code")
        if not code:
            continue
        if code not in candidate_codes:
            continue
        narrative = narrative_by_code.get(code, {})
        main_claim = text_value(narrative, "community_main_claim") or text_value(row, "community_explanation")
        signal_quality = signal_quality_enum(text_value(narrative, "community_signal_quality"))
        status = "summarized" if narrative else "collected"
        statements.append(
            f"""
            INSERT INTO community_evidence(
              window_id, code, main_claim, trigger_claim, trigger_event, trigger_timing,
              imagination_path, verification_anchor, support_points, disagreements,
              risk_flags, hot_terms, post_count, signal_quality, status, raw_json
            )
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(code)}, {sql_string(limit_text(main_claim, 512))},
              {sql_string(limit_text(text_value(narrative, "community_trigger_claim"), 512))},
              {sql_string(limit_text(text_value(narrative, "community_trigger_event"), 512))},
              {sql_string(limit_text(text_value(narrative, "community_trigger_timing"), 128))},
              {sql_string(text_value(narrative, "community_imagination_path"))},
              {sql_string(text_value(narrative, "community_verification_anchor"))},
              {sql_json(split_terms(text_value(narrative, "community_support_points")))},
              {sql_json(split_terms(text_value(narrative, "community_disagreements")))},
              {sql_json(split_terms(text_value(narrative, "community_risk_flags")))},
              {sql_json(split_terms(text_value(row, "hot_terms")))},
              {sql_int(row.get("hot_post_count"))},
              {sql_string(signal_quality)}, {sql_string(status)},
              {sql_json({"evidence": row, "narrative": narrative})}
            )
            ON DUPLICATE KEY UPDATE
              main_claim=VALUES(main_claim),
              trigger_claim=VALUES(trigger_claim),
              trigger_event=VALUES(trigger_event),
              trigger_timing=VALUES(trigger_timing),
              imagination_path=VALUES(imagination_path),
              verification_anchor=VALUES(verification_anchor),
              support_points=VALUES(support_points),
              disagreements=VALUES(disagreements),
              risk_flags=VALUES(risk_flags),
              hot_terms=VALUES(hot_terms),
              post_count=VALUES(post_count),
              signal_quality=VALUES(signal_quality),
              status=VALUES(status),
              raw_json=VALUES(raw_json);
            """
        )
        for post in posts_by_code.get(code, [])[:10]:
            post_id = post_id_from_url(text_value(post, "detail_url"))
            if not post_id:
                continue
            statements.append(
                f"""
                INSERT IGNORE INTO community_evidence_posts(community_evidence_id, community_post_id, relevance_score, quote_digest)
                SELECT ce.id, cp.id, {sql_number(post.get("heat_score"))}, {sql_string(text_value(post, "title")[:512])}
                FROM community_evidence ce
                JOIN windows w ON w.id = ce.window_id
                JOIN community_posts cp ON cp.platform='xueqiu' AND cp.post_id={sql_string(post_id)}
                WHERE w.window_id={sql_string(window_id)} AND ce.code={sql_string(code)}
                LIMIT 1;
                """
            )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return sum(1 for row in evidence_rows if text_value(row, "code") in candidate_codes)


def evidence_strength_enum(value: str) -> str:
    text = value.lower()
    if "强" in value or "strong" in text:
        return "strong"
    if "中" in value or "medium" in text:
        return "medium"
    if "弱" in value or "weak" in text or "风险" in value:
        return "weak"
    return "missing"


def first_url(value: str) -> str:
    for part in re.split(r"\s*\|\s*|\s*;\s*", value or ""):
        text = part.strip()
        if text.startswith(("http://", "https://")):
            return text
    return ""


def import_hard_evidence_csv(config: MySqlConfig, path: Path) -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        code = text_value(row, "code")
        if not code:
            continue
        summary = " || ".join(
            item
            for item in [
                text_value(row, "hard_catalyst_summary"),
                text_value(row, "stone_evidence_summary"),
                text_value(row, "order_cooperation_evidence"),
                text_value(row, "order_cooperation_hard_evidence"),
                text_value(row, "amount_terms_evidence"),
                text_value(row, "partner_customer_evidence"),
                text_value(row, "risk_evidence"),
                text_value(row, "evidence_gap"),
            ]
            if item
        )
        url = first_url(text_value(row, "detail_source_urls") or text_value(row, "source_urls"))
        source_type = text_value(row, "source_type") or "cninfo"
        if source_type not in {"cninfo", "exchange", "official_site", "irm", "news", "policy", "ths", "other"}:
            source_type = "other"
        if not url:
            url = f"mysql://{source_type}-hard/{code}/{safe_id(text_value(row, 'fetched_at') or now_text())}"
        title = text_value(row, "hard_catalyst_summary") or ("同花顺公告硬证据" if source_type == "ths" else "公告硬证据")
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO official_evidence(
              code, source_type, title, summary, url, published_at, evidence_type, strength, raw_json, collected_at
            )
            VALUES(
              {sql_string(code)}, {sql_string(source_type)}, {sql_string(limit_text(title, 512))},
              {sql_string(summary)}, {sql_string(url)}, {sql_string(text_value(row, "fetched_at") or None)},
              {sql_string(limit_text(text_value(row, "hard_catalyst_types"), 128))},
              {sql_string(evidence_strength_enum(text_value(row, "hard_evidence_strength")))},
              {sql_json(row)}, {sql_string(text_value(row, "fetched_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              title=VALUES(title),
              summary=VALUES(summary),
              published_at=COALESCE(VALUES(published_at), published_at),
              evidence_type=VALUES(evidence_type),
              strength=VALUES(strength),
              raw_json=VALUES(raw_json),
              collected_at=VALUES(collected_at);
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements) // 2


def import_supplemental_evidence_csv(config: MySqlConfig, path: Path) -> int:
    rows = read_csv(path)
    if not rows:
        return 0
    statements: list[str] = []
    for row in rows:
        code = text_value(row, "code")
        if not code:
            continue
        summary = " || ".join(
            item
            for item in [
                text_value(row, "supplemental_summary"),
                text_value(row, "news_evidence"),
                text_value(row, "irm_evidence"),
                text_value(row, "official_news_evidence"),
                text_value(row, "order_cooperation_supplement"),
                text_value(row, "customer_partner_supplement"),
                text_value(row, "amount_terms_supplement"),
                text_value(row, "evidence_gap"),
            ]
            if item
        )
        url = first_url(text_value(row, "source_urls"))
        if not url:
            url = f"mysql://supplemental/{code}/{safe_id(text_value(row, 'fetched_at') or now_text())}"
        source_type = "news"
        if text_value(row, "irm_evidence"):
            source_type = "irm"
        elif text_value(row, "official_news_evidence"):
            source_type = "official_site"
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO official_evidence(
              code, source_type, title, summary, url, published_at, evidence_type, strength, raw_json, collected_at
            )
            VALUES(
              {sql_string(code)}, {sql_string(source_type)}, {sql_string("补充硬证据")},
              {sql_string(summary)}, {sql_string(url)}, {sql_string(text_value(row, "fetched_at") or None)},
              'supplemental', {sql_string(evidence_strength_enum(text_value(row, "supplemental_strength")))},
              {sql_json(row)}, {sql_string(text_value(row, "fetched_at") or now_text())}
            )
            ON DUPLICATE KEY UPDATE
              summary=VALUES(summary),
              published_at=COALESCE(VALUES(published_at), published_at),
              strength=VALUES(strength),
              raw_json=VALUES(raw_json),
              collected_at=VALUES(collected_at);
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements) // 2


def import_official_evidence_files(config: MySqlConfig, root: Path) -> dict[str, int]:
    stock_dir = root / "data" / "stock"
    hard_rows = import_hard_evidence_csv(config, stock_dir / "hard_catalyst_evidence_latest.csv")
    supplemental_rows = import_supplemental_evidence_csv(config, stock_dir / "supplemental_hard_evidence_latest.csv")
    return {"hard_evidence_rows": hard_rows, "supplemental_evidence_rows": supplemental_rows}


def summary(config: MySqlConfig) -> dict[str, Any]:
    sql = """
    SELECT TABLE_TYPE, COUNT(*) FROM information_schema.TABLES
    WHERE TABLE_SCHEMA = DATABASE()
    GROUP BY TABLE_TYPE
    ORDER BY TABLE_TYPE;
    SELECT 'windows', COUNT(*) FROM windows;
    SELECT 'window_movers', COUNT(*) FROM window_movers;
    SELECT 'evidence_jobs', COUNT(*) FROM evidence_jobs;
    SELECT 'evidence_layers', COUNT(*) FROM evidence_layers;
    SELECT 'generated_posts', COUNT(*) FROM generated_posts;
    """
    output = run_mysql(config, sql, batch=True)
    return {"database": config.database, "output": output}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Write stock scout data into MySQL.")
    add_mysql_args(parser)
    parser.add_argument("--ensure-schema", action="store_true")
    parser.add_argument("--summary", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    root = project_root()
    if args.ensure_schema:
        ensure_schema(config, root)
        print(json.dumps({"schema": "ok", "database": config.database}, ensure_ascii=False))
    if args.summary:
        print(json.dumps(summary(config), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
