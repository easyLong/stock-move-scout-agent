#!/usr/bin/env python
"""
Watch full-market A-share movers via Tongdaxin quote servers.

Realtime quotes come from TDX quote servers. Industry and concept labels are
loaded from the local Tongdaxin cache, so the output can explain each mover
without scraping the desktop UI.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pytdx.hq import TdxHq_API

from stock_move_scout.analysis.realtime_filter import RealtimeFilterConfig, realtime_signal


DEFAULT_SERVERS = [
    ("110.41.2.72", 7709),
    ("110.41.147.114", 7709),
    ("101.33.225.16", 7709),
    ("124.223.163.242", 7709),
    ("122.51.120.217", 7709),
    ("119.97.185.59", 7709),
]

DEFAULT_TDX_DIR = Path(r"G:\D盘迁移\Tools\tdx")

OUTPUT_COLUMNS = [
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

JUDGEMENT_COLUMNS = OUTPUT_COLUMNS + [
    "candidate_basis",
    "freshness",
    "speed_signal",
    "pct_position",
    "amount_confirm",
    "linkage_signal",
    "industry_hot_count",
    "sub_industry_hot_count",
    "concept_hot_count",
    "hot_concepts",
    "risk_flags",
    "action_bucket",
    "value_view",
    "value_reason",
    "next_watch",
    "avoid_reason",
    "key_points",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_a_share(market: int, code: str) -> bool:
    if market == 0:
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == 1:
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    return False


def to_float(value: Any) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
    except Exception:
        return 0.0


def connect(servers: list[tuple[str, int]], timeout: int) -> tuple[TdxHq_API, str]:
    errors: list[str] = []
    for ip, port in servers:
        api = TdxHq_API(heartbeat=True, auto_retry=True)
        try:
            if api.connect(ip, port, time_out=timeout):
                return api, f"{ip}:{port}"
        except Exception as exc:
            errors.append(f"{ip}:{port} {type(exc).__name__}: {exc}")
    raise RuntimeError("All TDX quote servers failed:\n" + "\n".join(errors))


def load_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def save_universe(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["market", "code", "name"])
        writer.writeheader()
        writer.writerows(rows)


def build_universe(api: TdxHq_API, path: Path, refresh: bool) -> list[dict[str, Any]]:
    cached = load_csv(path)
    if cached and not refresh:
        return cached

    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for market in [0, 1]:
        count = api.get_security_count(market) or 0
        for start in range(0, count, 1000):
            data = api.get_security_list(market, start)
            if not data:
                continue
            for item in data:
                code = str(item.get("code", ""))
                if not is_a_share(market, code):
                    continue
                key = (market, code)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({"market": market, "code": code, "name": item.get("name", "")})
            time.sleep(0.05)

    save_universe(path, rows)
    return rows


def chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[idx : idx + size] for idx in range(0, len(rows), size)]


def fetch_quotes(api: TdxHq_API, universe: list[dict[str, Any]], batch_size: int) -> dict[str, dict[str, Any]]:
    name_by_key = {f"{row['market']}:{row['code']}": row.get("name", "") for row in universe}
    result: dict[str, dict[str, Any]] = {}
    for batch in chunks(universe, batch_size):
        symbols = [(int(row["market"]), str(row["code"])) for row in batch]
        data = api.get_security_quotes(symbols) or []
        for item in data:
            key = f"{item.get('market')}:{item.get('code')}"
            if to_float(item.get("price")) <= 0:
                continue
            row = dict(item)
            row["name"] = name_by_key.get(key, "")
            result[key] = row
        time.sleep(0.03)
    return result


def read_text_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="gbk", errors="ignore").splitlines()


def load_industry_names(cache_dir: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for filename in ["tdxzs.cfg", "tdxzs3.cfg"]:
        for line in read_text_lines(cache_dir / filename):
            parts = line.strip().split("|")
            if len(parts) >= 6 and parts[5]:
                names[parts[5]] = parts[0]
    return names


def load_industry_map(cache_dir: Path) -> dict[str, dict[str, str]]:
    code_names = load_industry_names(cache_dir)
    result: dict[str, dict[str, str]] = {}
    for line in read_text_lines(cache_dir / "tdxhy.cfg"):
        parts = line.strip().split("|")
        if len(parts) < 6:
            continue
        market, code, industry_code, _, _, sub_code = parts[:6]
        key = f"{market}:{code}"
        result[key] = {
            "industry_code": industry_code,
            "sub_industry_code": sub_code,
            "industry": code_names.get(industry_code, ""),
            "sub_industry": code_names.get(sub_code, ""),
        }
    return result


def add_concept(concepts: dict[str, set[str]], key: str, name: str) -> None:
    name = name.strip()
    if not name:
        return
    if name.startswith(("FG_", "ZS_")):
        return
    concepts[key].add(name)


def load_spec_concepts(cache_dir: Path, concepts: dict[str, set[str]]) -> None:
    path = cache_dir / "specgpsxzt.txt"
    for line in read_text_lines(path):
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        market, code, raw = parts[:3]
        if not is_a_share(int(market), code):
            continue
        for name in raw.split(","):
            add_concept(concepts, f"{market}:{code}", name)


def load_infoharbor_concepts(cache_dir: Path, concepts: dict[str, set[str]]) -> None:
    path = cache_dir / "infoharbor_block.dat"
    current_name = ""
    stock_pattern = re.compile(r"([012])#(\d{6})")
    for line in read_text_lines(path):
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts = line.split(",")
            current_name = parts[0].replace("#GN_", "").replace("#", "").strip()
            continue
        if not current_name:
            continue
        for market, code in stock_pattern.findall(line):
            market_num = int(market)
            if is_a_share(market_num, code):
                add_concept(concepts, f"{market}:{code}", current_name)


def load_concept_map(cache_dir: Path) -> dict[str, list[str]]:
    concepts: dict[str, set[str]] = defaultdict(set)
    load_spec_concepts(cache_dir, concepts)
    load_infoharbor_concepts(cache_dir, concepts)
    return {key: sorted(values, key=concept_sort_key) for key, values in concepts.items()}


def concept_sort_key(name: str) -> tuple[int, str]:
    topic_words = ("AI", "ChatGPT", "DeepSeek", "机器人", "半导体", "芯片", "算力", "CPO", "英伟达")
    if any(word in name for word in topic_words):
        return (0, name)
    return (1, name)


def load_snapshot(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_snapshot(path: Path, snapshot: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")


def build_rows(
    current: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    industry_map: dict[str, dict[str, str]],
    concept_map: dict[str, list[str]],
    concept_limit: int,
    server: str,
) -> tuple[list[dict[str, Any]], str]:
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_previous = bool(previous)
    basis = "snapshot_speed" if has_previous else "pct_change_first_run"
    rows: list[dict[str, Any]] = []

    for key, item in current.items():
        price = to_float(item.get("price"))
        last_close = to_float(item.get("last_close"))
        prev_price = to_float(previous.get(key, {}).get("price")) if has_previous else 0.0
        prev_amount = to_float(previous.get(key, {}).get("amount")) if has_previous else 0.0
        prev_vol = to_float(previous.get(key, {}).get("vol")) if has_previous else 0.0
        amount = to_float(item.get("amount"))
        vol = to_float(item.get("vol"))
        amount_delta = max(0.0, amount - prev_amount) if has_previous and prev_amount > 0 else 0.0
        vol_delta = max(0.0, vol - prev_vol) if has_previous and prev_vol > 0 else 0.0
        pct_change = ((price / last_close - 1) * 100) if last_close else 0.0
        speed = ((price / prev_price - 1) * 100) if prev_price else pct_change
        industry = industry_map.get(key, {})
        concepts = concept_map.get(key, [])
        rows.append(
            {
                "captured_at": captured_at,
                "rank_speed": "",
                "rank_pct_change": "",
                "market": item.get("market", ""),
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "price": round(price, 4),
                "speed": round(speed, 4),
                "pct_change": round(pct_change, 4),
                "last_close": last_close,
                "open": item.get("open", ""),
                "high": item.get("high", ""),
                "low": item.get("low", ""),
                "amount": item.get("amount", ""),
                "amount_delta_15s": round(amount_delta, 2),
                "vol": item.get("vol", ""),
                "vol_delta_15s": int(vol_delta),
                "cur_vol": item.get("cur_vol", ""),
                "bid1": item.get("bid1", ""),
                "ask1": item.get("ask1", ""),
                "industry": industry.get("industry", ""),
                "sub_industry": industry.get("sub_industry", ""),
                "industry_code": industry.get("industry_code", ""),
                "sub_industry_code": industry.get("sub_industry_code", ""),
                "concepts": ",".join(concepts[:concept_limit]),
                "concept_count": len(concepts),
                "server": server,
                "basis": basis,
            }
        )

    for idx, row in enumerate(sorted(rows, key=lambda row: to_float(row["speed"]), reverse=True), start=1):
        row["rank_speed"] = idx
    for idx, row in enumerate(sorted(rows, key=lambda row: to_float(row["pct_change"]), reverse=True), start=1):
        row["rank_pct_change"] = idx
    return rows, basis


def write_csv(path: Path, rows: list[dict[str, Any]], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def csv_has_data(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return any(True for _ in reader)


def csv_has_positive_speed(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return any(to_float(row.get("speed")) > 0 for row in reader)


def read_last_history_group(path: Path, require_positive_speed: bool = False) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    if require_positive_speed:
        rows = [row for row in rows if to_float(row.get("speed")) > 0]
    if not rows:
        return []
    last_time = rows[-1].get("captured_at", "")
    return [row for row in rows if row.get("captured_at", "") == last_time]


def restore_latest_from_history(
    latest_path: Path,
    history_path: Path,
    columns: list[str],
    require_positive_speed: bool = False,
) -> bool:
    if require_positive_speed:
        if csv_has_positive_speed(latest_path):
            return False
    elif csv_has_data(latest_path):
        return False
    rows = read_last_history_group(history_path, require_positive_speed=require_positive_speed)
    if not rows:
        return False
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    with latest_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return True


def split_concepts(row: dict[str, Any]) -> list[str]:
    raw = str(row.get("concepts", ""))
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_heat_counts(rows: list[dict[str, Any]], sample_size: int) -> dict[str, dict[str, int]]:
    positive_rows = [row for row in rows if to_float(row.get("pct_change")) > 0]
    sample = sorted(positive_rows, key=lambda row: to_float(row["pct_change"]), reverse=True)[:sample_size]
    sample += [row for row in rows if to_float(row.get("speed")) > 0]

    industry_counts: dict[str, int] = defaultdict(int)
    sub_industry_counts: dict[str, int] = defaultdict(int)
    concept_counts: dict[str, int] = defaultdict(int)
    seen: set[tuple[str, str]] = set()
    for row in sample:
        key = f"{row.get('market')}:{row.get('code')}"
        if ("row", key) in seen:
            continue
        seen.add(("row", key))
        industry = str(row.get("industry", "")).strip()
        sub_industry = str(row.get("sub_industry", "")).strip()
        if industry:
            industry_counts[industry] += 1
        if sub_industry:
            sub_industry_counts[sub_industry] += 1
        for concept in split_concepts(row):
            concept_counts[concept] += 1

    return {
        "industry": dict(industry_counts),
        "sub_industry": dict(sub_industry_counts),
        "concept": dict(concept_counts),
    }


def load_seen_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"items": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("items"), dict):
            return payload
    except Exception:
        pass
    return {"items": {}}


def save_seen_state(path: Path, state: dict[str, Any]) -> None:
    items = state.get("items", {})
    if isinstance(items, dict) and len(items) > 5000:
        kept = sorted(items.items(), key=lambda item: item[1].get("last_seen", ""))[-3000:]
        state["items"] = dict(kept)
    write_json(path, state)


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
    if speed > 0 and linkage_count >= 3 and amount_label in ("成交有效", "成交强确认") and pct_change < 15:
        return "观察池", risks
    return "等待验证", risks


def build_value_judgement(
    row: dict[str, Any],
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

    if amount_label in ("成交有效", "成交强确认"):
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

    if amount_label in ("成交有效", "成交强确认") and linkage_count >= 3 and 0 < pct_change < 10:
        value_view = "优先观察"
    elif amount_label in ("成交有效", "成交强确认") and pct_change >= 10:
        value_view = "高位验证"
    elif pct_change < 0:
        value_view = "反抽观察"
    elif "ST" in name.upper() or amount_label == "成交偏弱":
        value_view = "谨慎过滤"
    else:
        value_view = "等待确认"

    value_reason = f"{industry}/{sub_industry}，关联{concept_text}；好处：{'、'.join(positives) or '暂无明显优势'}；不足：{'、'.join(negatives) or '暂未发现明显硬伤'}"
    avoid_reason = "、".join(negatives)
    return {
        "value_view": value_view,
        "value_reason": value_reason,
        "next_watch": "；".join(dict.fromkeys(next_watch)) or "观察是否持续上榜并保持量价配合",
        "avoid_reason": avoid_reason,
    }


def build_judgement_rows(
    rows: list[dict[str, Any]],
    speed_rows: list[dict[str, Any]],
    pct_rows: list[dict[str, Any]],
    seen_state: dict[str, Any],
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
        speed_value = to_float(row.get("speed"))
        pct_value = to_float(row.get("pct_change"))
        amount_value = to_float(row.get("amount"))
        amount_label = amount_confirm(amount_value)
        action, risks = choose_action(candidate_basis, speed_value, pct_value, amount_label, linkage_count)
        value = build_value_judgement(row, speed_value, pct_value, amount_label, linkage_count, concepts)
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


def build_signal_rows(rows: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    signal_rows: list[dict[str, Any]] = []
    signal_config = RealtimeFilterConfig(
        min_speed_signal=args.min_speed_signal,
        min_amount_delta_15s=args.min_amount_delta_15s,
        min_amount_delta_speed=args.min_amount_delta_speed,
    )
    for row in rows:
        speed = to_float(row.get("speed"))
        amount_delta = to_float(row.get("amount_delta_15s"))
        signal = realtime_signal(
            stock_name=row.get("name"),
            speed=speed,
            amount_delta_15s=amount_delta,
            config=signal_config,
        )
        if not signal.matched:
            continue
        out = dict(row)
        out["basis"] = signal.basis
        signal_rows.append(out)

    signal_rows.sort(
        key=lambda row: (
            -to_float(row.get("speed")),
            -to_float(row.get("amount_delta_15s")),
            -to_float(row.get("amount")),
        )
    )
    if args.max_signal_rows > 0:
        signal_rows = signal_rows[: args.max_signal_rows]
    for idx, row in enumerate(signal_rows, start=1):
        row["rank_speed"] = idx
    return signal_rows


def run_once(args: argparse.Namespace, cache: dict[str, Any]) -> None:
    servers = [parse_server(item) for item in args.server] if args.server else DEFAULT_SERVERS
    api, server = connect(servers, args.timeout)
    try:
        universe = build_universe(api, args.universe_csv, args.refresh_universe)
        current = fetch_quotes(api, universe, args.batch_size)
    finally:
        api.disconnect()

    previous = load_snapshot(args.snapshot_json)
    rows, basis = build_rows(
        current=current,
        previous=previous,
        industry_map=cache["industry_map"],
        concept_map=cache["concept_map"],
        concept_limit=args.concept_limit,
        server=server,
    )
    speed_rows = build_signal_rows(rows, args)
    pct_rows = sorted(rows, key=lambda row: to_float(row["pct_change"]), reverse=True)[: args.top]
    seen_state = load_seen_state(args.seen_json)
    phase = market_phase()
    preserve_last_mover = phase != "trading" and not speed_rows
    judgement_rows: list[dict[str, Any]] = []
    if not preserve_last_mover:
        judgement_rows = build_judgement_rows(
            rows=rows,
            speed_rows=speed_rows,
            pct_rows=pct_rows,
            seen_state=seen_state,
            top=args.top,
            heat_sample_size=args.heat_sample_size,
        )

    write_csv(args.full_market_csv, rows, append=False)
    write_csv(args.pct_latest_csv, pct_rows, append=False)
    restored_speed_latest = False
    restored_judgement_latest = False
    if preserve_last_mover:
        restored_speed_latest = restore_latest_from_history(
            args.speed_latest_csv,
            args.speed_history_csv,
            OUTPUT_COLUMNS,
            require_positive_speed=True,
        )
        restored_judgement_latest = restore_latest_from_history(
            args.judgement_latest_csv,
            args.judgement_history_csv,
            JUDGEMENT_COLUMNS,
        )
    else:
        write_csv(args.speed_latest_csv, speed_rows, append=False)
        write_csv(args.speed_history_csv, speed_rows, append=True)
        write_judgement_csv(args.judgement_latest_csv, judgement_rows, append=False)
        write_judgement_csv(args.judgement_history_csv, judgement_rows, append=True)
    save_snapshot(args.snapshot_json, current)
    save_seen_state(args.seen_json, seen_state)
    write_json(
        args.meta_json,
        {
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "basis": basis,
            "market_phase": phase,
            "preserve_last_mover": preserve_last_mover,
            "restored_speed_latest": restored_speed_latest,
            "restored_judgement_latest": restored_judgement_latest,
            "server": server,
            "universe_count": len(universe),
            "quote_count": len(current),
            "industry_count": len(cache["industry_map"]),
            "concept_stock_count": len(cache["concept_map"]),
            "interval_seconds": args.interval,
            "min_speed_signal": args.min_speed_signal,
            "min_amount_delta_15s": args.min_amount_delta_15s,
            "min_amount_delta_speed": args.min_amount_delta_speed,
            "max_signal_rows": args.max_signal_rows,
            "full_market_csv": str(args.full_market_csv),
            "speed_latest_csv": str(args.speed_latest_csv),
            "pct_latest_csv": str(args.pct_latest_csv),
            "judgement_latest_csv": str(args.judgement_latest_csv),
        },
    )

    names = " / ".join(
        f"{row['rank_speed']}.{row['name']}({row['speed']}%, {row['industry'] or row['sub_industry']})"
        for row in speed_rows
    )
    print(
        f"[{rows[0]['captured_at'] if rows else datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
        f"TDX movers top={len(speed_rows)} basis={basis} phase={phase} preserve={preserve_last_mover} "
        f"universe={len(universe)} quotes={len(current)} "
        f"server={server}: {names}"
    )


def parse_server(value: str) -> tuple[str, int]:
    ip, port = value.split(":", 1)
    return ip, int(port)


def write_judgement_csv(path: Path, rows: list[dict[str, Any]], append: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=JUDGEMENT_COLUMNS, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Watch full-market A-share movers via TDX quote servers.")
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--max-signal-rows", type=int, default=50)
    parser.add_argument("--min-speed-signal", type=float, default=1.0)
    parser.add_argument("--min-amount-delta-15s", type=float, default=30_000_000)
    parser.add_argument("--min-amount-delta-speed", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--concept-limit", type=int, default=8)
    parser.add_argument("--heat-sample-size", type=int, default=80)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--refresh-universe", action="store_true")
    parser.add_argument("--server", action="append", default=[], help="ip:port override")
    parser.add_argument("--tdx-dir", type=Path, default=DEFAULT_TDX_DIR)
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "tdx_a_stock_universe.csv")
    parser.add_argument("--snapshot-json", type=Path, default=root / "data" / "stock" / "tdx_mover_last_snapshot.json")
    parser.add_argument("--full-market-csv", type=Path, default=root / "data" / "stock" / "tdx_full_market_latest.csv")
    parser.add_argument("--speed-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_latest.csv")
    parser.add_argument("--speed-history-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_speed_top10_history.csv")
    parser.add_argument("--pct-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_pct_top10_latest.csv")
    parser.add_argument("--judgement-latest-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_judgement_latest.csv")
    parser.add_argument("--judgement-history-csv", type=Path, default=root / "data" / "stock" / "tdx_mover_judgement_history.csv")
    parser.add_argument("--meta-json", type=Path, default=root / "data" / "stock" / "tdx_mover_meta.json")
    parser.add_argument("--seen-json", type=Path, default=root / "data" / "stock" / "tdx_mover_seen.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cache_dir = args.tdx_dir / "T0002" / "hq_cache"
    cache = {
        "industry_map": load_industry_map(cache_dir),
        "concept_map": load_concept_map(cache_dir),
    }

    runs = 0
    while True:
        started = time.monotonic()
        run_once(args, cache)
        if args.once:
            return 0
        runs += 1
        if args.max_runs and runs >= args.max_runs:
            return 0
        elapsed = time.monotonic() - started
        time.sleep(max(0, args.interval - elapsed))


if __name__ == "__main__":
    raise SystemExit(main())
