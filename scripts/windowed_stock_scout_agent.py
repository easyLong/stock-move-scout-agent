#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import re
from datetime import date, datetime, timedelta, time as clock_time
from pathlib import Path
from typing import Any

from stock_scout_mysql import add_mysql_args, mysql_config_from_args
from stock_scout_mysql import record_scan_result as record_mysql_scan_result
from stock_scout_mysql import record_window_result as record_mysql_window_result
from stock_scout_mysql import latest_window_rank_rows, enqueue_hot_evidence_task, load_active_anchor_member_map, load_theme_reason_map, theme_reason_anchor_candidates, theme_reason_candidate_rank


BASE_COLUMNS = [
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
    "amount_delta_15s",
    "vol",
    "vol_delta_15s",
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

WINDOW_COLUMNS = BASE_COLUMNS + [
    "window_started_at",
    "window_ended_at",
    "first_seen_at",
    "appearance_count",
    "appearance_rate",
    "best_rank_speed",
    "avg_rank_speed",
    "max_speed",
    "max_pct_change",
    "max_amount_delta_15s",
    "max_vol_delta_15s",
    "latest_seen_at",
    "previous_window_rank",
    "rank_delta",
    "is_new_entry",
    "burst_score",
    "sustained_score",
    "window_score",
]

NOISE_CONCEPTS = {
    "ST板块", "亏损股", "微小盘股", "含H股", "含B股", "融资融券",
    "国企改革", "央企改革", "地方国企改革", "一带一路", "深股通", "沪股通",
    "周期股", "微盘优选", "活跃股", "低价股", "高价股", "机构重仓",
    "MSCI中国", "富时罗素", "标普道琼斯A股", "证金持股", "社保重仓",
}

UNANCHORED_TYPE = "unanchored"
UNANCHORED_NAME = "\u672a\u951a\u5b9a"
UNANCHORED_LABEL = "\u5f02\u52a8"

BROAD_CONCEPTS = {
    "PPP概念", "储能", "人工智能", "新型城镇", "互联金融", "养老概念",
    "碳中和", "绿色电力", "华为概念", "新能源车", "充电桩", "光伏",
    "氢能源", "数据中心", "大数据", "工业互联", "机器人概念", "智能机器",
    "物联网", "小米概念", "专精特新", "创投概念", "股东减持", "业绩预亏",
    "回购计划", "芯片", "云计算", "信息安全", "区块链", "乡村振兴",
    "物业管理", "物业管理概念", "低空经济",
}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def market_phase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "non_trading_day"
    current = now.time()
    if clock_time(9, 30) <= current < clock_time(11, 30):
        return "trading"
    if clock_time(13, 0) <= current < clock_time(15, 0):
        return "trading"
    if clock_time(11, 30) <= current < clock_time(13, 0):
        return "lunch_break"
    return "market_closed"


def next_trading_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    current = now.time()
    if now.weekday() < 5:
        if current < clock_time(9, 30):
            return now.replace(hour=9, minute=30, second=0, microsecond=0)
        if current < clock_time(13, 0):
            return now.replace(hour=13, minute=0, second=0, microsecond=0)
    days = 1
    while (now + timedelta(days=days)).weekday() >= 5:
        days += 1
    next_day = now + timedelta(days=days)
    return next_day.replace(hour=9, minute=30, second=0, microsecond=0)


def trading_seconds_remaining(now: datetime | None = None) -> int:
    now = now or datetime.now()
    current = now.time()
    if now.weekday() >= 5:
        return 0
    if clock_time(9, 30) <= current < clock_time(11, 30):
        end = now.replace(hour=11, minute=30, second=0, microsecond=0)
        return max(0, int((end - now).total_seconds()))
    if clock_time(13, 0) <= current < clock_time(15, 0):
        end = now.replace(hour=15, minute=0, second=0, microsecond=0)
        return max(0, int((end - now).total_seconds()))
    return 0


def wait_for_trading(args: argparse.Namespace) -> bool:
    if args.include_non_trading:
        return True
    phase = market_phase()
    if phase == "trading":
        return True
    if args.once:
        print(f"[{now_text()}] window_skipped phase={phase} reason=outside_a_share_trading_time")
        return False
    wake_at = next_trading_start()
    sleep_seconds = max(1, int((wake_at - datetime.now()).total_seconds()))
    print(f"[{now_text()}] waiting_for_trading phase={phase} next_start={wake_at.strftime('%Y-%m-%d %H:%M:%S')}")
    time.sleep(min(sleep_seconds, max(1, args.non_trading_sleep_seconds)))
    return False


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def append_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def maybe_record_scan_to_db(args: argparse.Namespace, result: dict[str, Any]) -> int:
    if args.mysql_enabled:
        try:
            mysql_run_id = record_mysql_scan_result(mysql_config_from_args(args), result)
            result["mysql_scan_run_id"] = mysql_run_id
            return 1
        except Exception as exc:
            print(f"[{now_text()}] mysql_scan_warning {type(exc).__name__}:{exc}")
    return 0


def maybe_record_window_to_db(
    args: argparse.Namespace,
    meta: dict[str, Any],
    aggregated: list[dict[str, Any]],
    evidence_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    mysql_result: dict[str, Any] = {"enabled": False}
    if args.mysql_enabled:
        try:
            mysql_result = record_mysql_window_result(mysql_config_from_args(args), meta, aggregated, evidence_rows)
            mysql_result["enabled"] = True
        except Exception as exc:
            mysql_result = {"enabled": True, "ok": False, "error": f"{type(exc).__name__}:{exc}"}
            print(f"[{now_text()}] mysql_window_warning {mysql_result['error']}")
    return {"enabled": False, "mysql": mysql_result}


def previous_rank_rows(args: argparse.Namespace) -> list[dict[str, str]]:
    if args.mysql_primary and args.mysql_enabled:
        try:
            return latest_window_rank_rows(mysql_config_from_args(args))
        except Exception as exc:
            print(f"[{now_text()}] mysql_previous_rank_warning {type(exc).__name__}:{exc}")
    return read_csv(args.window_top10_csv)


def safe_id(value: str) -> str:
    return value.replace("-", "").replace(":", "").replace(" ", "_")


def to_float(value: Any) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except Exception:
        return 0.0


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def split_concepts(value: Any) -> list[str]:
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
    return [item.strip() for item in re.split(r"[,，、\s]+", text) if item.strip()]


def candidate_sector_keys(row: dict[str, Any]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    industry = str(row.get("industry", "") or "").strip()
    sub = str(row.get("sub_industry", "") or "").strip()
    if sub:
        candidates.append(("sub_industry", sub))
    if industry and industry != sub:
        candidates.append(("industry", industry))
    return candidates or [("unknown", "未分组")]


def sector_key(row: dict[str, Any]) -> tuple[str, str]:
    return candidate_sector_keys(row)[0]


def amount_yi(row: dict[str, Any]) -> float:
    return to_float(row.get("amount")) / 100_000_000


def role_strength_score(row: dict[str, Any]) -> float:
    return (
        to_int(row.get("appearance_count")) * 100
        + to_float(row.get("max_speed")) * 25
        + max(0.0, to_float(row.get("max_pct_change"))) * 8
        + max(0, to_int(row.get("rank_delta"))) * 20
        + min(30.0, amount_yi(row) * 2)
    )


def sector_anchor_score(members: list[dict[str, Any]]) -> float:
    stock_count = len(members)
    total_amount_yi = sum(max(0.0, amount_yi(item)) for item in members)
    max_pct = max((to_float(item.get("max_pct_change")) for item in members), default=0.0)
    avg_pct = sum(to_float(item.get("max_pct_change")) for item in members) / max(1, stock_count)
    max_speed = max((to_float(item.get("max_speed")) for item in members), default=0.0)
    return stock_count * 80 + max(0.0, max_pct) * 18 + max(0.0, avg_pct) * 8 + max_speed * 18 + min(80.0, total_amount_yi * 4)


def dominant_count(members: list[dict[str, Any]], key: str) -> tuple[str, int]:
    counts: dict[str, int] = {}
    for row in members:
        value = str(row.get(key, "") or "").strip()
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return "", 0
    name, count = max(counts.items(), key=lambda item: item[1])
    return name, count


def concept_anchor_allowed(members: list[dict[str, Any]]) -> bool:
    stock_count = len(members)
    if stock_count < 2:
        return True
    _, dominant_industry_count = dominant_count(members, "industry")
    _, dominant_sub_count = dominant_count(members, "sub_industry")
    return dominant_sub_count >= 2 or dominant_industry_count / max(1, stock_count) >= 0.6


def anchor_type_priority(key_type: str) -> int:
    if key_type == "limit_up_theme":
        return 6
    if key_type == "hot_concept":
        return 5
    if key_type == "sub_industry":
        return 3
    if key_type == "concept":
        return 2
    if key_type == "industry":
        return 1
    return 0


def active_anchor_keys(row: dict[str, Any], active_anchor_map: dict[str, list[dict[str, Any]]] | None) -> list[tuple[str, str]]:
    if not active_anchor_map:
        return []
    code = str(row.get("code", "") or "").strip()
    out: list[tuple[str, str]] = []
    for item in active_anchor_map.get(code) or []:
        name = str(item.get("anchor_name", "") or "").strip()
        if str(item.get("match_level", "") or "") == "weak":
            continue
        if name and str(item.get("status", "")) != "expired" and all(existing != name for _, existing in out):
            out.append((str(item.get("anchor_type", "") or "hot_concept"), name))
    return out


def all_candidate_sector_keys(
    row: dict[str, Any],
    active_anchor_map: dict[str, list[dict[str, Any]]] | None,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[tuple[str, str]]:
    candidates = active_anchor_keys(row, active_anchor_map)
    code = str(row.get("code", "") or "").strip()
    for key_type, key in theme_reason_anchor_candidates(code, theme_reason_map):
        if all(existing != key for _, existing in candidates):
            candidates.append((key_type, key))
    return candidates


def active_anchor_info_for(
    code: str,
    anchor_name: str,
    active_anchor_map: dict[str, list[dict[str, Any]]] | None,
) -> dict[str, Any]:
    if not active_anchor_map:
        return {}
    for item in active_anchor_map.get(code) or []:
        if str(item.get("anchor_name", "") or "") == anchor_name:
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
    candidates = [item for item in candidates if str(item.get("reason_text", "") or "").strip()]
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
                str(item.get("anchor_name", "") or ""),
                str(item.get("theme_name", "") or ""),
                str(item.get("reason_text", "") or ""),
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
        to_float(info.get("priority")),
        to_float(info.get("confidence")),
        source_rank.get(str(info.get("source", "") or ""), 0),
    )


def row_reason_terms(row: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("industry", "sub_industry"):
        value = str(row.get(key, "") or "").strip()
        if value and value not in terms:
            terms.append(value)
    for value in split_concepts(row.get("concepts")):
        if value and value not in terms:
            terms.append(value)
    return terms


def active_reason_is_evidence(active_info: dict[str, Any]) -> bool:
    reason = str(active_info.get("latest_reason", "") or "").strip()
    if not reason:
        return False
    if str(active_info.get("match_level", "") or "") != "strong":
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
    reason = str(reason_info.get("reason_text", "") or "").strip()
    if reason:
        reason_anchor = str(reason_info.get("anchor_name", "") or "").strip()
        if reason_anchor and selected_anchor and reason_anchor != selected_anchor:
            return compact_reason_text(f"{reason_anchor}：{reason}")
        return compact_reason_text(reason)
    if active_reason_is_evidence(active_info):
        return compact_reason_text(str(active_info.get("latest_reason", "") or ""))
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
    last_seen = str(info.get("last_seen_date", "") or "")[:10]
    if last_seen == date.today().strftime("%Y-%m-%d"):
        return 2
    if last_seen:
        return 1
    return 0


def role_reason(row: dict[str, Any], label: str, sector_name: str, sector_count: int) -> str:
    return (
        f"{sector_name}共{sector_count}只；{label}，"
        f"出现{to_int(row.get('appearance_count'))}次，"
        f"涨幅{to_float(row.get('max_pct_change')):.2f}%，"
        f"成交{amount_yi(row):.2f}亿"
    )


def choose_anchor_for_row(
    row: dict[str, Any],
    candidate_buckets: dict[str, list[dict[str, Any]]],
    candidate_meta: dict[str, str],
    active_anchor_map: dict[str, list[dict[str, Any]]] | None = None,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[str, str]:
    best_type = "unknown"
    best_key = "未分组"
    best_tuple = (-1, -1, -1, -1.0, -1, -1.0, -1.0, -1.0, -1, -1.0, -1.0, -1.0)
    for key_type, key in all_candidate_sector_keys(row, active_anchor_map, theme_reason_map):
        members = candidate_buckets.get(key, [])
        if not members:
            continue
        if key_type == "concept" and not concept_anchor_allowed(members):
            continue
        max_pct = max((to_float(item.get("max_pct_change")) for item in members), default=0.0)
        avg_pct = sum(to_float(item.get("max_pct_change")) for item in members) / max(1, len(members))
        score = sector_anchor_score(members)
        linked_priority = anchor_type_priority(key_type) if (len(members) >= 2 or key_type in {"hot_concept", "limit_up_theme"}) else 0
        active_info = active_anchor_info_for(str(row.get("code", "")), key, active_anchor_map)
        match_priority = anchor_match_level_priority(str(active_info.get("match_level", "") or ""))
        recency_priority = anchor_recency_priority(active_info)
        active_confidence = to_float(active_info.get("confidence")) if active_info else 0.0
        reason_info = theme_reason_info_for(str(row.get("code", "")), key, theme_reason_map, allow_fallback=False)
        reason_rank = theme_reason_candidate_rank(reason_info) if reason_info else (0, 0.0, 0.0, 0.0, 0.0, 0, "", "")
        linked_strength = 3 if len(members) >= 6 else 2 if len(members) >= 3 else 1 if len(members) >= 2 else 0
        rank_tuple = (
            linked_strength,
            len(members),
            score,
            max_pct,
            avg_pct,
            anchor_type_priority(key_type),
            match_priority,
            recency_priority,
            active_confidence,
            reason_rank[0],
            reason_rank[1],
            reason_rank[2],
            reason_rank[3],
            reason_rank[4],
            reason_rank[5],
        )
        if rank_tuple > best_tuple:
            best_tuple = rank_tuple
            best_key = key
            best_type = candidate_meta.get(key, key_type)
    return best_type, best_key


def build_window_roles(
    rows: list[dict[str, Any]],
    active_anchor_map: dict[str, list[dict[str, Any]]] | None = None,
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_buckets: dict[str, list[dict[str, Any]]] = {}
    candidate_meta: dict[str, str] = {}
    concept_counts: dict[str, int] = {}
    for row in rows:
        for key_type, key in all_candidate_sector_keys(row, active_anchor_map, theme_reason_map):
            candidate_buckets.setdefault(key, []).append(row)
            candidate_meta.setdefault(key, key_type)
        for concept in split_concepts(row.get("concepts")):
            concept_counts[concept] = concept_counts.get(concept, 0) + 1

    buckets: dict[str, list[dict[str, Any]]] = {}
    bucket_meta: dict[str, str] = {}
    for row in rows:
        if not all_candidate_sector_keys(row, active_anchor_map, theme_reason_map):
            continue
        key_type, key = choose_anchor_for_row(row, candidate_buckets, candidate_meta, active_anchor_map, theme_reason_map)
        buckets.setdefault(key, []).append(row)
        bucket_meta[key] = key_type

    sector_stats: list[dict[str, Any]] = []
    stock_roles: list[dict[str, Any]] = []
    assigned_codes: set[str] = set()
    for key, members in buckets.items():
        members_by_leader = sorted(
            members,
            key=lambda item: (-to_float(item.get("max_pct_change")), -to_float(item.get("max_speed")), to_int(item.get("rank_speed"), 9999)),
        )
        members_by_amount = sorted(members, key=lambda item: (amount_yi(item), role_strength_score(item)), reverse=True)
        members_by_first = sorted(members, key=lambda item: str(item.get("first_seen_at") or "9999"))
        leader = members_by_leader[0]
        core = next((item for item in members_by_amount if item.get("code") != leader.get("code")), members_by_amount[0])
        pioneer = members_by_first[0]
        stock_count = len(members)
        total_amount = sum(max(0.0, to_float(item.get("amount"))) for item in members)
        avg_pct = sum(to_float(item.get("max_pct_change")) for item in members) / max(1, stock_count)
        max_speed = max(to_float(item.get("max_speed")) for item in members)
        hot_concepts = sorted(
            {
                concept
                for item in members
                for concept in split_concepts(item.get("concepts"))
                if concept_counts.get(concept, 0) > 1
            },
            key=lambda concept: concept_counts.get(concept, 0),
            reverse=True,
        )[:5]
        sector_score = sector_anchor_score(members)
        if stock_count >= 6:
            strength_label = "板块扩散"
        elif stock_count >= 3:
            strength_label = "有板块联动"
        elif stock_count >= 2:
            strength_label = "弱联动"
        else:
            strength_label = "个股孤立"
        sector_stats.append(
            {
                "sector_key": key,
                "sector_type": bucket_meta.get(key, ""),
                "stock_count": stock_count,
                "leader_code": leader.get("code", ""),
                "leader_name": leader.get("name", ""),
                "core_code": core.get("code", ""),
                "core_name": core.get("name", ""),
                "follower_count": max(0, stock_count - 2),
                "total_amount": round(total_amount, 2),
                "avg_pct_change": round(avg_pct, 4),
                "max_speed": round(max_speed, 4),
                "sector_score": round(sector_score, 2),
                "strength_label": strength_label,
                "hot_concepts": hot_concepts,
                "summary": f"{key}{strength_label}，领涨{leader.get('name', '')}，中军{core.get('name', '')}",
            }
        )

        for row in members:
            code = row.get("code", "")
            active_info = active_anchor_info_for(str(code), key, active_anchor_map)
            had_active_anchor = bool(active_info)
            reason_info = theme_reason_info_for(
                str(code),
                key,
                theme_reason_map,
                allow_fallback=False,
            )
            reason_text = best_reason_text(active_info, reason_info, key)
            stock_reason_info: dict[str, Any] = {}
            stock_reason_text = ""
            if not reason_text:
                stock_reason_info = related_theme_reason_info_for(
                    str(code),
                    key,
                    theme_reason_map,
                )
                if str(stock_reason_info.get("anchor_name", "") or "") != key:
                    stock_reason_text = best_reason_text(active_info, stock_reason_info, key)
            evidence_suffix = ""
            if reason_text:
                evidence_suffix = f"；题材证据：{reason_text}"
            elif stock_reason_text:
                evidence_suffix = f"；个股证据：{stock_reason_text}"
            active_info = dict(active_info)
            active_info["latest_reason"] = reason_text
            if reason_text:
                active_info["source"] = reason_info.get("source", active_info.get("source", ""))
                active_info["confidence"] = reason_info.get("confidence", active_info.get("confidence", 0))
            if stock_count < 2:
                label = "孤立脉冲"
            elif code == leader.get("code"):
                label = "领涨"
            elif code == core.get("code"):
                label = "中军"
            elif code == pioneer.get("code"):
                label = "先锋"
            elif to_float(row.get("max_pct_change")) >= 9.5:
                label = "高标"
            else:
                label = "跟风"
            score = role_strength_score(row)
            if label == "领涨":
                score += 80
            elif label == "中军":
                score += 60
            elif label == "先锋":
                score += 35
            stock_roles.append(
                {
                    "code": code,
                    "name": row.get("name", ""),
                    "rank_no": to_int(row.get("rank_speed")),
                    "sector_key": key,
                    "sector_type": bucket_meta.get(key, ""),
                    "sector_stock_count": stock_count,
                    "role_label": label,
                    "role_score": round(score, 2),
                    "role_reason": role_reason(row, label, key, stock_count) + evidence_suffix,
                    "risk_flags": "高位" if to_float(row.get("max_pct_change")) >= 9.5 else "",
                    "raw_json": {
                        "sector_strength": strength_label,
                        "hot_concepts": hot_concepts,
                        "amount_yi": round(amount_yi(row), 2),
                        "leader_code": leader.get("code", ""),
                        "core_code": core.get("code", ""),
                        "anchor_source": "active_market_anchor" if had_active_anchor else "theme_reason_bank" if reason_info else "no_theme_anchor",
                        "anchor_status": active_info.get("status", ""),
                        "anchor_match_level": active_info.get("match_level", ""),
                        "anchor_matched_term": active_info.get("matched_term", ""),
                        "anchor_match_source": active_info.get("source", ""),
                        "anchor_confidence": to_float(active_info.get("confidence")) if active_info else 0.0,
                        "anchor_reason": reason_text,
                        "evidence_anchor_name": reason_info.get("anchor_name", ""),
                        "stock_reason": stock_reason_text,
                        "stock_reason_anchor_name": stock_reason_info.get("anchor_name", ""),
                        "stock_reason_source": stock_reason_info.get("source", ""),
                        "theme_reason_source": reason_info.get("source", ""),
                        "theme_reason_confidence": to_float(reason_info.get("confidence")) if reason_info else 0.0,
                        "theme_reason_priority": to_float(reason_info.get("priority")) if reason_info else 0.0,
                        "anchor_method": "active_market_anchor_then_linked_static_bucket",
                    },
                }
            )
            assigned_codes.add(str(code))

    for row in rows:
        code = str(row.get("code", "") or "").strip()
        if not code or code in assigned_codes:
            continue
        score = role_strength_score(row)
        stock_roles.append(
            {
                "code": code,
                "name": row.get("name", ""),
                "rank_no": to_int(row.get("rank_speed")),
                "sector_key": UNANCHORED_NAME,
                "sector_type": UNANCHORED_TYPE,
                "sector_stock_count": 1,
                "role_label": UNANCHORED_LABEL,
                "role_score": round(score, 2),
                "role_reason": (
                    f"{UNANCHORED_NAME}\uff1b"
                    f"\u51fa\u73b0{to_int(row.get('appearance_count'))}\u6b21,"
                    f"\u6da8\u5e45{to_float(row.get('max_pct_change')):.2f}%,"
                    f"\u6210\u4ea4{amount_yi(row):.2f}\u4ebf"
                ),
                "risk_flags": "\u9ad8\u4f4d" if to_float(row.get("max_pct_change")) >= 9.5 else "",
                "raw_json": {
                    "anchor_source": "no_theme_anchor",
                    "anchor_method": "theme_anchor_only_no_static_fallback",
                    "amount_yi": round(amount_yi(row), 2),
                    "theme_reason_source": "",
                    "theme_reason_confidence": 0.0,
                    "theme_reason_priority": 0.0,
                },
            }
        )

    sector_stats.sort(key=lambda item: (-float(item.get("sector_score") or 0), -int(item.get("stock_count") or 0)))
    for index, item in enumerate(sector_stats, start=1):
        item["rank_no"] = index
    stock_roles.sort(key=lambda item: (to_int(item.get("rank_no"), 9999), -float(item.get("role_score") or 0)))
    return sector_stats, stock_roles


def run_command(command: list[str], cwd: Path, timeout: int) -> tuple[int, str, int]:
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    return result.returncode, output[-5000:], elapsed_ms(started)


def scan_once(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(root / "scripts" / "tdx_mover_watcher.py"),
        "--once",
        "--top",
        str(args.scan_top),
        "--max-signal-rows",
        str(args.scan_top),
        "--min-speed-signal",
        str(args.min_speed_signal),
        "--min-amount-delta-15s",
        str(args.min_amount_delta_15s),
        "--min-amount-delta-speed",
        str(args.min_amount_delta_speed),
        "--interval",
        str(args.scan_interval),
    ]
    returncode, output, duration_ms = run_command(command, root, args.scan_timeout)
    meta = read_json(args.tdx_meta_json)
    rows = read_csv(args.speed_latest_csv)
    phase = str(meta.get("market_phase") or meta.get("phase") or "")
    preserve_last = bool(meta.get("preserve_last_mover"))
    restored = bool(meta.get("restored_speed_latest") or meta.get("restored_judgement_latest"))
    accepted_phase = phase == "trading" or args.include_non_trading
    accepted_preserve = args.include_preserved or not (restored or preserve_last)
    accepted = returncode == 0 and rows and accepted_preserve and accepted_phase
    return {
        "scanned_at": now_text(),
        "returncode": returncode,
        "ok": returncode == 0,
        "accepted": accepted,
        "duration_ms": duration_ms,
        "phase": phase,
        "preserve_last": preserve_last,
        "restored": restored,
        "row_count": len(rows),
        "rows": rows if accepted else [],
        "output_tail": output,
    }


def pick_better_row(current: dict[str, str], candidate: dict[str, str]) -> dict[str, str]:
    if not current:
        return candidate
    candidate_speed = to_float(candidate.get("speed", ""))
    current_speed = to_float(current.get("speed", ""))
    if candidate_speed > current_speed:
        return candidate
    if candidate_speed == current_speed and to_float(candidate.get("amount_delta_15s", "")) > to_float(current.get("amount_delta_15s", "")):
        return candidate
    if candidate_speed == current_speed and to_float(candidate.get("amount", "")) > to_float(current.get("amount", "")):
        return candidate
    return current


def is_excluded_mover(row: dict[str, Any]) -> bool:
    name = str(row.get("name", "") or "")
    upper_name = name.upper()
    return "ST" in upper_name or "退市" in name


def aggregate_window(
    samples: list[list[dict[str, str]]],
    top: int,
    started_at: str,
    ended_at: str,
    previous_ranks: dict[str, int],
) -> list[dict[str, Any]]:
    total_samples = max(1, len(samples))
    stats: dict[str, dict[str, Any]] = {}
    for sample in samples:
        seen_in_sample: set[str] = set()
        for row in sample:
            code = row.get("code", "")
            if not code or code in seen_in_sample or is_excluded_mover(row):
                continue
            seen_in_sample.add(code)
            item = stats.setdefault(
                code,
                {
                    "count": 0,
                    "rank_sum": 0,
                    "best_rank": 9999,
                    "max_speed": 0.0,
                    "max_pct_change": -999.0,
                    "max_amount_delta_15s": 0.0,
                    "max_vol_delta_15s": 0,
                    "first_seen_at": "",
                    "latest_seen_at": "",
                    "best_row": {},
                },
            )
            rank = to_int(row.get("rank_speed", ""), 9999)
            speed = to_float(row.get("speed", ""))
            pct_change = to_float(row.get("pct_change", ""))
            amount_delta = to_float(row.get("amount_delta_15s", ""))
            vol_delta = to_int(row.get("vol_delta_15s", ""))
            item["count"] += 1
            item["rank_sum"] += rank
            item["best_rank"] = min(item["best_rank"], rank)
            item["max_speed"] = max(item["max_speed"], speed)
            item["max_pct_change"] = max(item["max_pct_change"], pct_change)
            item["max_amount_delta_15s"] = max(item["max_amount_delta_15s"], amount_delta)
            item["max_vol_delta_15s"] = max(item["max_vol_delta_15s"], vol_delta)
            if not item["first_seen_at"]:
                item["first_seen_at"] = row.get("captured_at", "") or started_at
            item["latest_seen_at"] = row.get("captured_at", "") or ended_at
            item["best_row"] = pick_better_row(item["best_row"], row)

    rows: list[dict[str, Any]] = []
    for code, item in stats.items():
        count = int(item["count"])
        avg_rank = item["rank_sum"] / count if count else 9999
        appearance_rate = count / total_samples
        previous_rank = previous_ranks.get(code, 0)
        rank_delta = previous_rank - item["best_rank"] if previous_rank else 0
        amount_delta_yi = item["max_amount_delta_15s"] / 100_000_000
        burst_score = item["max_speed"] * 10 + max(0.0, item["max_pct_change"]) * 2 + min(20.0, amount_delta_yi * 8) + max(0, rank_delta) * 5
        sustained_score = count * 100 + appearance_rate * 100 - avg_rank
        score = sustained_score * 10 + burst_score
        source = dict(item["best_row"])
        source.update(
            {
                "captured_at": ended_at,
                "window_started_at": started_at,
                "window_ended_at": ended_at,
                "first_seen_at": item["first_seen_at"],
                "appearance_count": count,
                "appearance_rate": f"{appearance_rate:.4f}",
                "best_rank_speed": item["best_rank"],
                "avg_rank_speed": f"{avg_rank:.2f}",
                "max_speed": f"{item['max_speed']:.4f}",
                "max_pct_change": f"{item['max_pct_change']:.4f}",
                "max_amount_delta_15s": f"{item['max_amount_delta_15s']:.2f}",
                "max_vol_delta_15s": item["max_vol_delta_15s"],
                "latest_seen_at": item["latest_seen_at"],
                "previous_window_rank": previous_rank or "",
                "rank_delta": rank_delta if previous_rank else "",
                "is_new_entry": "yes" if not previous_rank else "no",
                "burst_score": f"{burst_score:.2f}",
                "sustained_score": f"{sustained_score:.2f}",
                "window_score": f"{score:.2f}",
                "basis": "windowed_speed_frequency",
            }
        )
        rows.append(source)

    rows.sort(
        key=lambda row: (
            -to_int(row.get("appearance_count", "")),
            to_float(row.get("avg_rank_speed", "")),
            -to_float(row.get("max_speed", "")),
            -to_float(row.get("max_amount_delta_15s", "")),
            -to_float(row.get("amount", "")),
        )
    )
    for idx, row in enumerate(rows[:top], start=1):
        row["rank_speed"] = idx
    return rows[:top]


def previous_rank_map(rows: list[dict[str, str]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        code = row.get("code", "")
        rank = to_int(row.get("rank_speed", ""), 0)
        if code and rank:
            result[code] = rank
    return result


def launch_evidence_worker(args: argparse.Namespace, root: Path, request: dict[str, Any]) -> dict[str, Any]:
    if args.mysql_primary and args.mysql_enabled:
        payload = {
            "window_id": request.get("run_id", ""),
            "evidence_top": args.evidence_top,
            "community_top": args.community_top,
            "community_mode": args.community_mode,
            "community_cache_hours": args.community_cache_hours,
            "community_hot_posts_per_stock": args.community_hot_posts_per_stock,
            "official_site_mode": args.official_site_mode,
            "community_manual_verify_wait": args.community_manual_verify_wait,
            "community_verify_retries": args.community_verify_retries,
            "community_bridge_timeout": args.community_bridge_timeout,
            "community_timeout": args.community_timeout,
            "timeout_seconds": args.community_timeout + 600,
            "model": args.model,
            "openai_base_url": args.openai_base_url,
        }
        try:
            queued = enqueue_hot_evidence_task(mysql_config_from_args(args), str(request.get("run_id", "")), payload)
            return {
                "launched": False,
                "queued": True,
                "reason": "mysql_task_queue",
                **queued,
            }
        except Exception as exc:
            return {
                "launched": False,
                "queued": False,
                "reason": f"mysql_task_queue_error:{type(exc).__name__}:{exc}",
            }
    return {
        "launched": False,
        "queued": False,
        "reason": "mysql_primary_required",
    }


def evidence_candidate_rows(args: argparse.Namespace, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name", ""))
        pct_change = to_float(row.get("pct_change", ""))
        if pct_change < args.min_evidence_pct_change:
            continue
        if not args.include_st_evidence and ("ST" in name.upper() or name.startswith("*ST")):
            continue
        candidates.append(row)
    return candidates[: args.evidence_top]


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Run 15s mover scans, rank repeated appearances, and trigger evidence every window.")
    parser.add_argument("--once", action="store_true", help="run one aggregation window and exit")
    parser.add_argument("--scan-interval", type=int, default=15)
    parser.add_argument("--window-seconds", type=int, default=300)
    parser.add_argument("--scan-top", type=int, default=20)
    parser.add_argument("--min-speed-signal", type=float, default=1.0)
    parser.add_argument("--min-amount-delta-15s", type=float, default=30_000_000)
    parser.add_argument("--min-amount-delta-speed", type=float, default=0.5)
    parser.add_argument("--aggregate-top", type=int, default=5)
    parser.add_argument("--evidence-top", type=int, default=5)
    parser.add_argument("--min-evidence-pct-change", type=float, default=0.01)
    parser.add_argument("--include-st-evidence", action="store_true")
    parser.add_argument("--community-top", type=int, default=3)
    parser.add_argument("--community-mode", choices=["cache", "live", "skip"], default=os.environ.get("XUEQIU_COMMUNITY_MODE", "cache"))
    parser.add_argument("--community-cache-hours", type=int, default=int(os.environ.get("XUEQIU_COMMUNITY_CACHE_HOURS", "72")))
    parser.add_argument("--community-hot-posts-per-stock", type=int, default=8)
    parser.add_argument("--scan-timeout", type=int, default=90)
    parser.add_argument("--min-accepted-scans", type=int, default=3)
    parser.add_argument("--include-preserved", action="store_true")
    parser.add_argument("--include-non-trading", action="store_true")
    parser.add_argument("--non-trading-sleep-seconds", type=int, default=60)
    parser.add_argument("--no-evidence", action="store_true")
    parser.add_argument("--community-manual-verify-wait", type=int, default=8)
    parser.add_argument("--community-verify-retries", type=int, default=0)
    parser.add_argument("--community-bridge-timeout", type=int, default=40)
    parser.add_argument("--community-timeout", type=int, default=420)
    parser.add_argument("--official-site-mode", choices=["skip", "cache", "refresh"], default="cache")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", ""))
    parser.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "")
    parser.add_argument("--speed-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--tdx-meta-json", type=Path, default=root / "data" / "stock" / "tdx_mover_meta.json")
    parser.add_argument("--window-top10-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_window_top10_latest.csv")
    parser.add_argument("--window-history-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_window_top10_history.csv")
    parser.add_argument("--window-meta-json", type=Path, default=root / "data" / "stock" / "tdx_mover_window_meta.json")
    parser.add_argument("--snapshot-dir", type=Path, default=root / "runs" / "windowed_stock_scout")
    parser.add_argument("--mysql-primary", action="store_true", help="use MySQL for state/queue; files become compatibility artifacts only")
    parser.add_argument("--no-file-output", action="store_true", help="do not write latest/history/snapshot files except compatibility files required by legacy workers")
    add_mysql_args(parser)
    return parser.parse_args()


def run_window(args: argparse.Namespace, root: Path) -> dict[str, Any]:
    remaining_seconds = trading_seconds_remaining()
    if not args.include_non_trading and remaining_seconds <= 0:
        return {
            "updated_at": now_text(),
            "status": "skipped",
            "reason": "outside_a_share_trading_time",
            "market_phase": market_phase(),
        }
    started = time.monotonic()
    window_started_at = now_text()
    run_id = safe_id(window_started_at)
    run_dir = args.snapshot_dir / run_id
    snapshot_top10_csv = run_dir / "window_top10.csv"
    snapshot_evidence_csv = run_dir / "evidence_top10.csv"
    snapshot_meta_json = run_dir / "window_meta.json"
    previous_rows = previous_rank_rows(args)
    samples: list[list[dict[str, str]]] = []
    scan_results: list[dict[str, Any]] = []
    effective_window_seconds = args.window_seconds
    if not args.include_non_trading:
        effective_window_seconds = min(args.window_seconds, max(args.scan_interval, remaining_seconds))
    target_scans = max(1, int(effective_window_seconds / args.scan_interval))

    for idx in range(target_scans):
        scan_started = time.monotonic()
        result = scan_once(args, root)
        scan_results.append({k: v for k, v in result.items() if k != "rows"})
        db_scan_run_id = maybe_record_scan_to_db(args, result)
        if db_scan_run_id:
            scan_results[-1]["db_scan_run_id"] = db_scan_run_id
        if result["accepted"]:
            samples.append(result["rows"])
        print(
            f"[{result['scanned_at']}] window_scan {idx + 1}/{target_scans} "
            f"ok={result['ok']} accepted={result['accepted']} rows={result['row_count']} "
            f"phase={result['phase']} duration_ms={result['duration_ms']}"
        )
        if idx < target_scans - 1:
            elapsed = time.monotonic() - scan_started
            time.sleep(max(0, args.scan_interval - elapsed))

    window_ended_at = now_text()
    aggregated = aggregate_window(samples, args.aggregate_top, window_started_at, window_ended_at, previous_rank_map(previous_rows))
    active_anchor_map: dict[str, list[dict[str, Any]]] = {}
    theme_reason_map: dict[str, dict[str, dict[str, Any]]] = {}
    if args.mysql_enabled:
        codes = [str(row.get("code", "")) for row in aggregated]
        config = mysql_config_from_args(args)
        active_anchor_map = load_active_anchor_member_map(config, codes)
        theme_reason_map = load_theme_reason_map(config, codes)
    sector_stats, stock_roles = build_window_roles(aggregated, active_anchor_map, theme_reason_map)
    evidence_rows = evidence_candidate_rows(args, aggregated)
    if not args.no_file_output:
        write_csv(args.window_top10_csv, aggregated, WINDOW_COLUMNS)
        write_csv(snapshot_top10_csv, aggregated, WINDOW_COLUMNS)
        write_csv(snapshot_evidence_csv, evidence_rows, WINDOW_COLUMNS)
        append_csv(args.window_history_csv, aggregated, WINDOW_COLUMNS)
    evidence_request = {
        "queued_at": now_text(),
        "run_id": run_id,
        "window_started_at": window_started_at,
        "window_ended_at": window_ended_at,
        "top10_csv": str(snapshot_evidence_csv),
        "window_top10_csv": str(snapshot_top10_csv),
        "accepted_scans": len(samples),
        "top": [
            {
                "rank_speed": row.get("rank_speed", ""),
                "code": row.get("code", ""),
                "name": row.get("name", ""),
                "appearance_count": row.get("appearance_count", ""),
                "max_speed": row.get("max_speed", ""),
            }
            for row in aggregated
        ],
    }
    evidence = {"launched": False, "queued": False, "reason": "disabled"}
    if not aggregated:
        evidence = {"launched": False, "queued": False, "reason": "no_aggregated_rows"}
    elif not evidence_rows:
        evidence = {
            "launched": False,
            "queued": False,
            "reason": "no_evidence_candidates_after_filter",
            "min_evidence_pct_change": args.min_evidence_pct_change,
            "include_st_evidence": args.include_st_evidence,
        }
    elif len(samples) < args.min_accepted_scans:
        evidence = {
            "launched": False,
            "queued": False,
            "reason": f"accepted_scans_below_min_{len(samples)}_lt_{args.min_accepted_scans}",
        }
    elif not args.no_evidence:
        evidence = launch_evidence_worker(args, root, evidence_request)

    meta = {
        "updated_at": now_text(),
        "run_id": run_id,
        "window_started_at": window_started_at,
        "window_ended_at": window_ended_at,
        "duration_ms": elapsed_ms(started),
        "scan_interval": args.scan_interval,
        "min_speed_signal": args.min_speed_signal,
        "min_amount_delta_15s": args.min_amount_delta_15s,
        "min_amount_delta_speed": args.min_amount_delta_speed,
        "window_seconds": effective_window_seconds,
        "configured_window_seconds": args.window_seconds,
        "trading_seconds_remaining_at_start": remaining_seconds,
        "trading_time_only": not args.include_non_trading,
        "target_scans": target_scans,
        "accepted_scans": len(samples),
        "min_accepted_scans": args.min_accepted_scans,
        "aggregated_count": len(aggregated),
        "window_top10_csv": str(args.window_top10_csv),
        "window_history_csv": str(args.window_history_csv),
        "snapshot_top10_csv": str(snapshot_top10_csv),
        "snapshot_evidence_csv": str(snapshot_evidence_csv),
        "snapshot_meta_json": str(snapshot_meta_json),
        "evidence_candidate_count": len(evidence_rows),
        "min_evidence_pct_change": args.min_evidence_pct_change,
        "include_st_evidence": args.include_st_evidence,
        "evidence_request": evidence_request,
        "evidence": evidence,
        "sector_stats": sector_stats,
        "stock_roles": stock_roles,
        "latest_top": [
            {
                "rank_speed": row.get("rank_speed", ""),
                "code": row.get("code", ""),
                "name": row.get("name", ""),
                "appearance_count": row.get("appearance_count", ""),
                "appearance_rate": row.get("appearance_rate", ""),
                "avg_rank_speed": row.get("avg_rank_speed", ""),
                "max_speed": row.get("max_speed", ""),
                "is_new_entry": row.get("is_new_entry", ""),
                "rank_delta": row.get("rank_delta", ""),
                "pct_change": row.get("pct_change", ""),
            }
            for row in aggregated
        ],
        "scan_results_tail": scan_results[-10:],
    }
    meta["db"] = maybe_record_window_to_db(args, meta, aggregated, evidence_rows)
    if not args.no_file_output:
        write_json(args.window_meta_json, meta)
        write_json(snapshot_meta_json, meta)
    print(
        f"[{meta['updated_at']}] window_done accepted_scans={len(samples)} "
        f"top={len(aggregated)} evidence_launched={evidence.get('launched')}"
    )
    return meta


def main() -> int:
    args = parse_args()
    root = project_root()
    while True:
        if not wait_for_trading(args):
            if args.once:
                return 0
            continue
        run_window(args, root)
        if args.once:
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
