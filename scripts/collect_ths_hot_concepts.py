#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_int,
    sql_json,
    sql_number,
    sql_string,
)


BASE_URL = "https://news.10jqka.com.cn/app/concept_v2_api/open/api"
EVENT_LIST_URL = f"{BASE_URL}/concept/event/jtcsm/v1/event/list"
EVENT_DETAIL_URL = f"{BASE_URL}/concept/event/jtcsm/v1/event/detail"
THEME_TABLE_URL = f"{BASE_URL}/concept/event/jtcsm/v1/theme/table"


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Referer": "https://news.10jqka.com.cn/app/hot_concept/v2/main",
        "Origin": "https://news.10jqka.com.cn",
        "Accept": "application/json, text/plain, */*",
    }


def compact(value: Any, limit: int = 512) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except Exception:
        return 0


def to_float(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", ""))
    except Exception:
        return 0.0


def ts_to_datetime(value: Any) -> str:
    ts = to_int(value)
    if ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def request_json(url: str, *, params: dict[str, Any] | None = None, method: str = "get", body: Any = None, timeout: int = 15) -> dict[str, Any]:
    if method == "post":
        resp = requests.post(url, headers={**headers(), "Content-Type": "application/json"}, json=body, timeout=timeout)
    else:
        resp = requests.get(url, headers=headers(), params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if int(data.get("status_code") or 0) != 0:
        raise RuntimeError(f"{url} status={data.get('status_code')} msg={data.get('status_msg')} data={data.get('data')}")
    return data


def fetch_event_groups(days: int, max_pages: int, timeout: int, pause: float) -> list[dict[str, Any]]:
    cutoff = date.today() - timedelta(days=max(1, days) - 1)
    groups_by_date: dict[str, dict[str, Any]] = {}
    cursor = ""
    for _ in range(max_pages):
        params = {"date": cursor} if cursor else {}
        data = request_json(EVENT_LIST_URL, params=params, timeout=timeout).get("data") or []
        if not isinstance(data, list) or not data:
            break
        for group in data:
            day = str(group.get("date") or "")
            if not day:
                continue
            if datetime.strptime(day, "%Y-%m-%d").date() >= cutoff:
                groups_by_date[day] = group
        oldest = min((str(group.get("date")) for group in data if group.get("date")), default="")
        if not oldest:
            break
        if datetime.strptime(oldest, "%Y-%m-%d").date() < cutoff:
            break
        cursor = oldest
        time.sleep(pause)
    return [groups_by_date[key] for key in sorted(groups_by_date.keys(), reverse=True)]


def fetch_detail(event_id: str, timeout: int) -> dict[str, Any]:
    try:
        data = request_json(EVENT_DETAIL_URL, params={"eventId": event_id}, timeout=timeout).get("data")
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"detail_error": f"{type(exc).__name__}:{exc}"}


def fetch_theme_table(event_id: str, timeout: int) -> list[dict[str, Any]]:
    try:
        data = request_json(THEME_TABLE_URL, params={"eventId": event_id}, timeout=timeout).get("data")
        return data if isinstance(data, list) else []
    except Exception:
        return []


def flatten_theme_members(event_id: str, trade_date: str, themes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def walk(theme: dict[str, Any]) -> None:
        theme_id = compact(theme.get("id"), 64)
        theme_name = compact(theme.get("showName") or theme.get("indexName"), 128)
        stocks = theme.get("topStocks") if isinstance(theme.get("topStocks"), list) else []
        for stock in stocks:
            code = compact(stock.get("stockCode"), 6)
            if not code:
                continue
            rows.append(
                {
                    "trade_date": trade_date,
                    "event_id": event_id,
                    "theme_id": theme_id,
                    "theme_name": theme_name,
                    "theme_type": compact(theme.get("type"), 64),
                    "index_code": compact(theme.get("indexCode"), 32),
                    "index_name": compact(theme.get("indexName"), 128),
                    "market_id": compact(theme.get("marketId"), 32),
                    "stock_code": code,
                    "stock_name": compact(stock.get("stockName"), 64),
                    "stock_market_id": compact(stock.get("marketId"), 32),
                    "rise_percent": to_float(stock.get("risePercent")),
                    "limit_up_state": to_int(stock.get("limitUpState")) if stock.get("limitUpState") is not None else None,
                    "reason": compact(stock.get("reason"), 1024),
                    "raw_json": {"theme": theme, "stock": stock},
                }
            )
        for child in theme.get("children") or []:
            if isinstance(child, dict):
                walk(child)

    for theme in themes:
        if isinstance(theme, dict):
            walk(theme)
    return rows


def normalize_events(groups: list[dict[str, Any]], with_details: bool, with_members: bool, timeout: int, pause: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    members: list[dict[str, Any]] = []
    for group in groups:
        trade_date = str(group.get("date") or "")
        for event in group.get("eventList") or []:
            if not isinstance(event, dict):
                continue
            event_id = compact(event.get("eventId"), 64)
            if not event_id:
                continue
            detail = fetch_detail(event_id, timeout) if with_details else {}
            theme_table = fetch_theme_table(event_id, timeout) if with_members else []
            if with_details or with_members:
                time.sleep(pause)
            merged = dict(event)
            if detail and "detail_error" not in detail:
                merged.update({k: v for k, v in detail.items() if v not in (None, "")})
            events.append(
                {
                    "trade_date": trade_date,
                    "event_id": event_id,
                    "title": compact(merged.get("title"), 512),
                    "investment_direction": compact(merged.get("investmentDirection"), 128),
                    "heat": to_int(merged.get("heat")),
                    "create_ts": to_int(merged.get("createTime")),
                    "create_time": ts_to_datetime(merged.get("createTime")),
                    "has_topped": merged.get("hasTopped"),
                    "summary": compact(merged.get("summary"), 5000),
                    "summary_items": merged.get("summaryItems") or [],
                    "jump_url": compact(merged.get("jumpUrl"), 1024),
                    "themes_json": merged.get("themes") or [],
                    "top_stocks_json": merged.get("topStocks") or [],
                    "raw_json": {"list": event, "detail": detail},
                }
            )
            if with_members:
                members.extend(flatten_theme_members(event_id, trade_date, theme_table))
    return events, members


def import_events(config: MySqlConfig, events: list[dict[str, Any]]) -> int:
    statements: list[str] = []
    for row in events:
        if not row["event_id"] or not row["trade_date"]:
            continue
        has_topped = row.get("has_topped")
        has_topped_sql = "NULL" if has_topped is None else ("1" if bool(has_topped) else "0")
        statements.append(
            f"""
            INSERT INTO ths_hot_concept_events(
              trade_date, event_id, title, investment_direction, heat, create_ts, create_time,
              has_topped, summary, summary_items, jump_url, themes_json, top_stocks_json, raw_json
            ) VALUES(
              {sql_string(row['trade_date'])}, {sql_string(row['event_id'])}, {sql_string(row['title'])},
              {sql_string(row['investment_direction'])}, {sql_int(row['heat'])}, {sql_int(row['create_ts'])},
              {sql_string(row['create_time'] or None)}, {has_topped_sql}, {sql_string(row['summary'])},
              {sql_json(row['summary_items'])}, {sql_string(row['jump_url'])}, {sql_json(row['themes_json'])},
              {sql_json(row['top_stocks_json'])}, {sql_json(row['raw_json'])}
            )
            ON DUPLICATE KEY UPDATE
              trade_date=VALUES(trade_date), title=VALUES(title), investment_direction=VALUES(investment_direction),
              heat=VALUES(heat), create_ts=VALUES(create_ts), create_time=VALUES(create_time),
              has_topped=VALUES(has_topped), summary=VALUES(summary), summary_items=VALUES(summary_items),
              jump_url=VALUES(jump_url), themes_json=VALUES(themes_json), top_stocks_json=VALUES(top_stocks_json),
              raw_json=VALUES(raw_json);
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(statements)


def import_members(config: MySqlConfig, members: list[dict[str, Any]]) -> int:
    statements: list[str] = []
    for row in members:
        if not row["event_id"] or not row["theme_id"] or not row["stock_code"]:
            continue
        limit_state = "NULL" if row.get("limit_up_state") is None else sql_int(row.get("limit_up_state"))
        statements.append(
            f"""
            INSERT INTO ths_hot_concept_members(
              trade_date, event_id, theme_id, theme_name, theme_type, index_code, index_name, market_id,
              stock_code, stock_name, stock_market_id, rise_percent, limit_up_state, reason, raw_json
            ) VALUES(
              {sql_string(row['trade_date'])}, {sql_string(row['event_id'])}, {sql_string(row['theme_id'])},
              {sql_string(row['theme_name'])}, {sql_string(row['theme_type'])}, {sql_string(row['index_code'])},
              {sql_string(row['index_name'])}, {sql_string(row['market_id'])}, {sql_string(row['stock_code'])},
              {sql_string(row['stock_name'])}, {sql_string(row['stock_market_id'])}, {sql_number(row['rise_percent'])},
              {limit_state}, {sql_string(row['reason'])}, {sql_json(row['raw_json'])}
            )
            ON DUPLICATE KEY UPDATE
              trade_date=VALUES(trade_date), theme_name=VALUES(theme_name), theme_type=VALUES(theme_type),
              index_code=VALUES(index_code), index_name=VALUES(index_name), market_id=VALUES(market_id),
              stock_name=VALUES(stock_name), stock_market_id=VALUES(stock_market_id),
              rise_percent=VALUES(rise_percent), limit_up_state=VALUES(limit_up_state),
              reason=VALUES(reason), raw_json=VALUES(raw_json);
            """
        )
    if not statements:
        return 0
    for idx in range(0, len(statements), 300):
        run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 300]) + "\nCOMMIT;")
    return len(statements)


def parse_json_cell(value: str) -> Any:
    try:
        return json.loads(value) if value else None
    except Exception:
        return None


def unique_keep_order(values: list[str], limit: int = 80) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = compact(value, 128)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def rebuild_active_anchors(config: MySqlConfig, lookback_days: int) -> int:
    today = date.today().strftime("%Y-%m-%d")
    sql = f"""
    SELECT
      DATE_FORMAT(e.trade_date, '%Y-%m-%d'), e.event_id, e.investment_direction,
      e.title, e.heat, CAST(e.themes_json AS CHAR), CAST(e.top_stocks_json AS CHAR)
    FROM ths_hot_concept_events e
    WHERE e.trade_date >= DATE_SUB(CURDATE(), INTERVAL {max(1, int(lookback_days)) - 1} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
    ORDER BY e.trade_date DESC, e.heat DESC;
    """
    event_rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    grouped: dict[str, dict[str, Any]] = {}
    event_ids_by_anchor: dict[str, list[str]] = defaultdict(list)
    for row in event_rows:
        if len(row) < 7:
            continue
        trade_date, event_id, anchor, title, heat_text, themes_text, top_stocks_text = row[:7]
        heat = to_int(heat_text)
        item = grouped.setdefault(
            anchor,
            {
                "dates": set(),
                "event_count": 0,
                "total_heat": 0,
                "today_heat": 0,
                "today_event_count": 0,
                "titles": [],
                "themes": [],
                "leader_codes": [],
                "member_codes": [],
                "today_member_codes": [],
            },
        )
        item["dates"].add(trade_date)
        item["event_count"] += 1
        item["total_heat"] += heat
        if trade_date == today:
            item["today_heat"] += heat
            item["today_event_count"] += 1
        item["titles"].append(title)
        event_ids_by_anchor[anchor].append(event_id)
        themes = parse_json_cell(themes_text) or []
        for theme in themes if isinstance(themes, list) else []:
            if isinstance(theme, dict):
                item["themes"].append(compact(theme.get("showName") or theme.get("indexName"), 128))
        top_stocks = parse_json_cell(top_stocks_text) or []
        for stock in top_stocks if isinstance(top_stocks, list) else []:
            if isinstance(stock, dict):
                code = compact(stock.get("stockCode"), 6)
                if code:
                    item["leader_codes"].append(code)
                    item["member_codes"].append(code)

    if not grouped:
        return 0

    member_sql = f"""
    SELECT e.investment_direction, m.stock_code, m.theme_name, m.limit_up_state, DATE_FORMAT(m.trade_date, '%Y-%m-%d')
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {max(1, int(lookback_days)) - 1} DAY)
      AND COALESCE(e.investment_direction, '') <> '';
    """
    for row in mysql_rows(run_mysql(config, member_sql, batch=True, raw=True)):
        if len(row) < 5:
            continue
        anchor, code, theme_name, limit_state, trade_date = row[:5]
        item = grouped.get(anchor)
        if not item:
            continue
        item["member_codes"].append(code)
        if trade_date == today:
            item["today_member_codes"].append(code)
        item["themes"].append(theme_name)
        if to_int(limit_state) == 1:
            item["limit_up_count"] = item.get("limit_up_count", 0) + 1
            if trade_date == today:
                item["today_limit_up_count"] = item.get("today_limit_up_count", 0) + 1

    statements = ["UPDATE active_market_anchors SET status='expired' WHERE source='ths_hot_concept';"]
    for anchor, item in grouped.items():
        dates = sorted(item["dates"])
        active_days = len(dates)
        last_seen = dates[-1] if dates else ""
        first_seen = dates[0] if dates else ""
        member_codes = unique_keep_order(item["member_codes"], 200)
        today_member_codes = unique_keep_order(item.get("today_member_codes", []), 200)
        leader_codes = unique_keep_order(item["leader_codes"], 20)
        themes = unique_keep_order(item["themes"], 80)
        titles = unique_keep_order(item["titles"], 30)
        limit_up_count = int(item.get("limit_up_count") or 0)
        today_limit_up_count = int(item.get("today_limit_up_count") or 0)
        score = (
            active_days * 20
            + int(item["event_count"]) * 8
            + int(item["total_heat"]) / 10000
            + int(item["today_heat"]) / 5000
            + len(member_codes) * 0.5
            + limit_up_count * 5
            + today_limit_up_count * 12
        )
        if last_seen == today and score >= 80:
            status = "active"
        elif last_seen == today:
            status = "watch"
        elif score >= 80:
            status = "cooling"
        else:
            status = "watch"
        raw = {
            "event_ids": unique_keep_order(event_ids_by_anchor.get(anchor, []), 60),
            "source": "ths_today_hot_concept",
            "algorithm": "ths_hot_concept_anchor_v1",
        }
        statements.append(
            f"""
            INSERT INTO active_market_anchors(
              anchor_name, anchor_type, source, first_seen_date, last_seen_date,
              active_days_14d, event_count_14d, total_heat_14d, today_heat, today_event_count,
              member_count_14d, today_member_count, limit_up_count_14d, today_limit_up_count,
              leader_codes, member_codes, keywords, related_themes, related_titles,
              final_score, status, raw_json, generated_at
            ) VALUES(
              {sql_string(anchor)}, 'hot_concept', 'ths_hot_concept', {sql_string(first_seen)}, {sql_string(last_seen)},
              {sql_int(active_days)}, {sql_int(item['event_count'])}, {sql_int(item['total_heat'])},
              {sql_int(item['today_heat'])}, {sql_int(item['today_event_count'])},
              {sql_int(len(member_codes))}, {sql_int(len(today_member_codes))},
              {sql_int(limit_up_count)}, {sql_int(today_limit_up_count)},
              {sql_json(leader_codes)}, {sql_json(member_codes)}, {sql_json([anchor] + themes)},
              {sql_json(themes)}, {sql_json(titles)}, {sql_number(round(score, 2))}, {sql_string(status)},
              {sql_json(raw)}, NOW(3)
            )
            ON DUPLICATE KEY UPDATE
              first_seen_date=VALUES(first_seen_date), last_seen_date=VALUES(last_seen_date),
              active_days_14d=VALUES(active_days_14d), event_count_14d=VALUES(event_count_14d),
              total_heat_14d=VALUES(total_heat_14d), today_heat=VALUES(today_heat),
              today_event_count=VALUES(today_event_count), member_count_14d=VALUES(member_count_14d),
              today_member_count=VALUES(today_member_count), limit_up_count_14d=VALUES(limit_up_count_14d),
              today_limit_up_count=VALUES(today_limit_up_count), leader_codes=VALUES(leader_codes),
              member_codes=VALUES(member_codes), keywords=VALUES(keywords), related_themes=VALUES(related_themes),
              related_titles=VALUES(related_titles), final_score=VALUES(final_score), status=VALUES(status),
              raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
            """
        )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(grouped)


def rebuild_active_anchor_members(config: MySqlConfig, lookback_days: int) -> int:
    sql = f"""
    SELECT
      e.investment_direction,
      DATE_FORMAT(e.trade_date, '%Y-%m-%d'),
      e.event_id,
      e.title,
      e.heat,
      m.stock_code,
      m.stock_name,
      m.theme_name,
      m.limit_up_state,
      m.reason
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {max(1, int(lookback_days)) - 1} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(m.stock_code, '') <> ''
    ORDER BY e.trade_date DESC, e.heat DESC;
    """
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 10:
            continue
        anchor, trade_date, event_id, title, heat_text, code, stock_name, theme_name, limit_state, reason = row[:10]
        key = (anchor, code)
        item = grouped.setdefault(
            key,
            {
                "anchor": anchor,
                "code": code,
                "stock_name": stock_name,
                "dates": set(),
                "events": set(),
                "titles": [],
                "themes": [],
                "reasons": [],
                "total_heat": 0,
                "limit_up_count": 0,
                "latest_reason": "",
            },
        )
        item["dates"].add(trade_date)
        item["events"].add(event_id)
        item["titles"].append(title)
        item["themes"].append(theme_name)
        if reason:
            item["reasons"].append(reason)
            if not item["latest_reason"]:
                item["latest_reason"] = reason
        item["total_heat"] += to_int(heat_text)
        if to_int(limit_state) == 1:
            item["limit_up_count"] += 1
        if stock_name:
            item["stock_name"] = stock_name

    statements = ["UPDATE active_market_anchor_members SET status='expired' WHERE source='ths_hot_concept';"]
    for (anchor, code), item in grouped.items():
        dates = sorted(item["dates"])
        first_seen = dates[0] if dates else ""
        last_seen = dates[-1] if dates else ""
        active_days = len(dates)
        event_count = len(item["events"])
        themes = unique_keep_order(item["themes"], 40)
        reasons = unique_keep_order(item["reasons"], 20)
        total_heat = int(item["total_heat"])
        limit_up_count = int(item["limit_up_count"])
        anchor_status = "watch"
        anchor_score = 0.0
        confidence = active_days * 20 + event_count * 12 + total_heat / 20000 + limit_up_count * 8
        raw = {
            "event_ids": unique_keep_order(list(item["events"]), 80),
            "titles": unique_keep_order(item["titles"], 30),
            "algorithm": "ths_hot_concept_member_v1",
        }
        statements.append(
            f"""
            INSERT INTO active_market_anchor_members(
              anchor_name, anchor_type, source, code, stock_name,
              first_seen_date, last_seen_date, active_days_14d, event_count_14d,
              total_heat_14d, limit_up_count_14d, theme_names, reasons,
              latest_reason, confidence, status, raw_json, generated_at
            )
            SELECT
              {sql_string(anchor)}, 'hot_concept', 'ths_hot_concept', {sql_string(code)}, {sql_string(item['stock_name'])},
              {sql_string(first_seen)}, {sql_string(last_seen)}, {sql_int(active_days)}, {sql_int(event_count)},
              {sql_int(total_heat)}, {sql_int(limit_up_count)}, {sql_json(themes)}, {sql_json(reasons)},
              {sql_string(item['latest_reason'])},
              ROUND({sql_number(round(confidence, 2))} + COALESCE(a.final_score, 0) / 20, 2),
              COALESCE(a.status, {sql_string(anchor_status)}), {sql_json(raw)}, NOW(3)
            FROM (SELECT 1) x
            LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name={sql_string(anchor)}
            ON DUPLICATE KEY UPDATE
              stock_name=VALUES(stock_name), first_seen_date=VALUES(first_seen_date),
              last_seen_date=VALUES(last_seen_date), active_days_14d=VALUES(active_days_14d),
              event_count_14d=VALUES(event_count_14d), total_heat_14d=VALUES(total_heat_14d),
              limit_up_count_14d=VALUES(limit_up_count_14d), theme_names=VALUES(theme_names),
              reasons=VALUES(reasons), latest_reason=VALUES(latest_reason),
              confidence=VALUES(confidence), status=VALUES(status), raw_json=VALUES(raw_json),
              generated_at=VALUES(generated_at);
            """
        )
    if statements:
        for idx in range(0, len(statements), 300):
            run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements[idx : idx + 300]) + "\nCOMMIT;")
    return len(grouped)


def _legacy_rebuild_active_anchor_relations(config: MySqlConfig, lookback_days: int) -> int:
    interval_days = max(1, int(lookback_days)) - 1
    sql = f"""
    DELETE FROM active_market_anchor_relations WHERE source='ths_hot_concept';

    /* Disabled: do not derive hot-concept relations from TDX stock concepts.
    /* Disabled: do not derive hot-concept relations from TDX stock concepts.
    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT anchor_name, anchor_type, source, 'anchor', anchor_name,
           100, event_count_14d, status,
           JSON_OBJECT('source_rule', 'anchor_name_exact'), NOW(3)
    FROM active_market_anchors
    WHERE source='ths_hot_concept' AND status <> 'expired' AND anchor_name <> ''
    ON DUPLICATE KEY UPDATE
      confidence=VALUES(confidence), evidence_count=VALUES(evidence_count),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
    */
    */

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT a.anchor_name, a.anchor_type, a.source, 'theme', jt.relation_name,
           LEAST(98, 78 + a.event_count_14d * 2), a.event_count_14d, a.status,
           JSON_OBJECT('source_rule', 'active_anchor_related_themes'), NOW(3)
    FROM active_market_anchors a
    JOIN JSON_TABLE(
      COALESCE(a.related_themes, JSON_ARRAY()),
      '$[*]' COLUMNS(relation_name VARCHAR(128) PATH '$')
    ) jt
    WHERE a.source='ths_hot_concept'
      AND a.status <> 'expired'
      AND COALESCE(jt.relation_name, '') <> ''
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT a.anchor_name, a.anchor_type, a.source, 'keyword', jt.relation_name,
           LEAST(92, 68 + a.event_count_14d * 2), a.event_count_14d, a.status,
           JSON_OBJECT('source_rule', 'active_anchor_keywords'), NOW(3)
    FROM active_market_anchors a
    JOIN JSON_TABLE(
      COALESCE(a.keywords, JSON_ARRAY()),
      '$[*]' COLUMNS(relation_name VARCHAR(128) PATH '$')
    ) jt
    WHERE a.source='ths_hot_concept'
      AND a.status <> 'expired'
      AND COALESCE(jt.relation_name, '') <> ''
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'theme', m.theme_name,
           LEAST(95, 65 + COUNT(*) * 2), COUNT(*), COALESCE(a.status, 'watch'),
           JSON_OBJECT('source_rule', 'ths_member_theme_name'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(m.theme_name, '') <> ''
    GROUP BY e.investment_direction, m.theme_name, COALESCE(a.status, 'watch')
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'concept', m.index_name,
           LEAST(88, 58 + COUNT(*) * 2), COUNT(*), COALESCE(a.status, 'watch'),
           JSON_OBJECT('source_rule', 'ths_member_index_name'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(m.index_name, '') <> ''
    GROUP BY e.investment_direction, m.index_name, COALESCE(a.status, 'watch')
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'sub_industry', s.sub_industry,
           LEAST(62, 25 + COUNT(DISTINCT m.stock_code) * 3), COUNT(DISTINCT m.stock_code),
           COALESCE(a.status, 'watch'), JSON_OBJECT('source_rule', 'member_stock_sub_industry_stats'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    JOIN stocks s ON s.code=m.stock_code
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(s.sub_industry, '') <> ''
    GROUP BY e.investment_direction, s.sub_industry, COALESCE(a.status, 'watch')
    HAVING COUNT(DISTINCT m.stock_code) >= 3
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'industry', s.industry,
           LEAST(55, 20 + COUNT(DISTINCT m.stock_code) * 2), COUNT(DISTINCT m.stock_code),
           COALESCE(a.status, 'watch'), JSON_OBJECT('source_rule', 'member_stock_industry_stats'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    JOIN stocks s ON s.code=m.stock_code
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(s.industry, '') <> ''
    GROUP BY e.investment_direction, s.industry, COALESCE(a.status, 'watch')
    HAVING COUNT(DISTINCT m.stock_code) >= 4
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    DELETE FROM active_market_anchor_relations
    WHERE source='ths_hot_concept'
      AND JSON_UNQUOTE(JSON_EXTRACT(raw_json, '$.source_rule'))='member_stock_concept_stats';
    """
    run_mysql(config, sql)
    rows = mysql_rows(run_mysql(config, "SELECT COUNT(*) FROM active_market_anchor_relations WHERE source='ths_hot_concept';", batch=True, raw=True))
    return to_int(rows[0][0] if rows and rows[0] else 0)


def _legacy_rebuild_active_anchor_match_candidates(config: MySqlConfig) -> int:
    sql = """
    DELETE FROM active_anchor_match_candidates WHERE source='ths_hot_concept';

    /* Disabled: legacy TDX concept matching.
    INSERT INTO active_anchor_match_candidates(
      anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
      matched_term, evidence_text, confidence, status, raw_json, generated_at
    )
    SELECT anchor_name, anchor_type, source, code, stock_name, 'ths_hot_concept_member', 'strong',
           anchor_name, latest_reason, confidence, status,
           JSON_OBJECT('source_rule', 'direct_ths_member'), NOW(3)
    FROM active_market_anchor_members
    WHERE source='ths_hot_concept' AND status <> 'expired'
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text),
      confidence=GREATEST(active_anchor_match_candidates.confidence, VALUES(confidence)), status=VALUES(status),
      raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_anchor_match_candidates(
      anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
      matched_term, evidence_text, confidence, status, raw_json, generated_at
    )
    SELECT r.anchor_name, r.anchor_type, r.source, s.code, s.name, 'stock_concept_relation', 'medium',
           jt.concept_name, CONCAT('股票概念命中：', jt.concept_name, ' -> ', r.anchor_name),
           ROUND(r.confidence * 0.72, 2), r.status,
           JSON_OBJECT('relation_type', r.relation_type, 'source_rule', 'stock_concept_exact'), NOW(3)
    FROM active_market_anchor_relations r
    JOIN stocks s
      ON COALESCE(s.is_st, 0)=0
     AND s.name NOT LIKE '%ST%'
     AND s.name NOT LIKE '%退市%'
    JOIN JSON_TABLE(
      JSON_ARRAY(),
      '$[*]' COLUMNS(concept_name VARCHAR(128) PATH '$')
    ) jt
      ON jt.concept_name = r.relation_name
    WHERE r.source='ths_hot_concept'
      AND r.status <> 'expired'
      AND r.relation_type IN ('anchor','theme','concept','keyword')
      AND COALESCE(jt.concept_name, '') <> ''
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text),
      confidence=GREATEST(active_anchor_match_candidates.confidence, VALUES(confidence)), status=VALUES(status),
      raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
    */

    INSERT INTO active_anchor_match_candidates(
      anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
      matched_term, evidence_text, confidence, status, raw_json, generated_at
    )
    SELECT r.anchor_name, r.anchor_type, r.source, s.code, s.name, 'stock_sub_industry_relation', 'weak',
           s.sub_industry, CONCAT('细分行业弱命中：', s.sub_industry, ' -> ', r.anchor_name),
           ROUND(r.confidence * 0.62, 2), r.status,
           JSON_OBJECT('relation_type', r.relation_type, 'source_rule', 'stock_sub_industry_exact'), NOW(3)
    FROM active_market_anchor_relations r
    JOIN stocks s
      ON s.sub_industry = r.relation_name
     AND COALESCE(s.is_st, 0)=0
     AND s.name NOT LIKE '%ST%'
     AND s.name NOT LIKE '%退市%'
    WHERE r.source='ths_hot_concept'
      AND r.status <> 'expired'
      AND r.relation_type='sub_industry'
      AND COALESCE(s.sub_industry, '') <> ''
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text),
      confidence=GREATEST(active_anchor_match_candidates.confidence, VALUES(confidence)), status=VALUES(status),
      raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_anchor_match_candidates(
      anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
      matched_term, evidence_text, confidence, status, raw_json, generated_at
    )
    SELECT r.anchor_name, r.anchor_type, r.source, s.code, s.name, 'stock_industry_relation', 'weak',
           s.industry, CONCAT('行业弱命中：', s.industry, ' -> ', r.anchor_name),
           ROUND(r.confidence * 0.52, 2), r.status,
           JSON_OBJECT('relation_type', r.relation_type, 'source_rule', 'stock_industry_exact'), NOW(3)
    FROM active_market_anchor_relations r
    JOIN stocks s
      ON s.industry = r.relation_name
     AND COALESCE(s.is_st, 0)=0
     AND s.name NOT LIKE '%ST%'
     AND s.name NOT LIKE '%退市%'
    WHERE r.source='ths_hot_concept'
      AND r.status <> 'expired'
      AND r.relation_type='industry'
      AND COALESCE(s.industry, '') <> ''
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text),
      confidence=GREATEST(active_anchor_match_candidates.confidence, VALUES(confidence)), status=VALUES(status),
      raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
    """
    run_mysql(config, sql)
    rows = mysql_rows(run_mysql(config, "SELECT COUNT(*) FROM active_anchor_match_candidates WHERE source='ths_hot_concept';", batch=True, raw=True))
    return to_int(rows[0][0] if rows and rows[0] else 0)


def rebuild_active_anchor_match_candidates(config: MySqlConfig) -> int:
    sql = """
    DELETE FROM active_anchor_match_candidates WHERE source='ths_hot_concept';

    INSERT INTO active_anchor_match_candidates(
      anchor_name, anchor_type, source, code, stock_name, match_source, match_level,
      matched_term, evidence_text, confidence, status, raw_json, generated_at
    )
    SELECT anchor_name, anchor_type, source, code, stock_name, 'ths_hot_concept_member', 'strong',
           anchor_name, latest_reason, confidence, status,
           JSON_OBJECT('source_rule', 'direct_ths_member'), NOW(3)
    FROM active_market_anchor_members
    WHERE source='ths_hot_concept' AND status <> 'expired'
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name), evidence_text=VALUES(evidence_text),
      confidence=GREATEST(active_anchor_match_candidates.confidence, VALUES(confidence)), status=VALUES(status),
      raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
    """
    run_mysql(config, sql)
    rows = mysql_rows(run_mysql(config, "SELECT COUNT(*) FROM active_anchor_match_candidates WHERE source='ths_hot_concept';", batch=True, raw=True))
    return to_int(rows[0][0] if rows and rows[0] else 0)


def rebuild_active_anchor_relations(config: MySqlConfig, lookback_days: int) -> int:
    interval_days = max(1, int(lookback_days))
    sql = f"""
    DELETE FROM active_market_anchor_relations WHERE source='ths_hot_concept';

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT anchor_name, anchor_type, source, 'anchor', anchor_name,
           100, event_count_14d, status,
           JSON_OBJECT('source_rule', 'anchor_name_exact'), NOW(3)
    FROM active_market_anchors
    WHERE source='ths_hot_concept' AND status <> 'expired' AND anchor_name <> ''
    ON DUPLICATE KEY UPDATE
      confidence=VALUES(confidence), evidence_count=VALUES(evidence_count),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT a.anchor_name, a.anchor_type, a.source, 'theme', jt.relation_name,
           80, a.event_count_14d, a.status,
           JSON_OBJECT('source_rule', 'active_anchor_related_themes'), NOW(3)
    FROM active_market_anchors a
    JOIN JSON_TABLE(
      COALESCE(a.related_themes, JSON_ARRAY()),
      '$[*]' COLUMNS(relation_name VARCHAR(128) PATH '$')
    ) jt
    WHERE a.source='ths_hot_concept'
      AND a.status <> 'expired'
      AND COALESCE(jt.relation_name, '') <> ''
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT a.anchor_name, a.anchor_type, a.source, 'keyword', jt.relation_name,
           70, a.event_count_14d, a.status,
           JSON_OBJECT('source_rule', 'active_anchor_keywords'), NOW(3)
    FROM active_market_anchors a
    JOIN JSON_TABLE(
      COALESCE(a.keywords, JSON_ARRAY()),
      '$[*]' COLUMNS(relation_name VARCHAR(128) PATH '$')
    ) jt
    WHERE a.source='ths_hot_concept'
      AND a.status <> 'expired'
      AND COALESCE(jt.relation_name, '') <> ''
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'theme', m.theme_name,
           LEAST(95, 65 + COUNT(*) * 2), COUNT(*), COALESCE(a.status, 'watch'),
           JSON_OBJECT('source_rule', 'ths_member_theme_name'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(m.theme_name, '') <> ''
    GROUP BY e.investment_direction, m.theme_name, COALESCE(a.status, 'watch')
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);

    INSERT INTO active_market_anchor_relations(
      anchor_name, anchor_type, source, relation_type, relation_name,
      confidence, evidence_count, status, raw_json, generated_at
    )
    SELECT e.investment_direction, 'hot_concept', 'ths_hot_concept', 'concept', m.index_name,
           LEAST(88, 58 + COUNT(*) * 2), COUNT(*), COALESCE(a.status, 'watch'),
           JSON_OBJECT('source_rule', 'ths_member_index_name'), NOW(3)
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id=m.event_id
    LEFT JOIN active_market_anchors a ON a.source='ths_hot_concept' AND a.anchor_name=e.investment_direction
    WHERE m.trade_date >= DATE_SUB(CURDATE(), INTERVAL {interval_days} DAY)
      AND COALESCE(e.investment_direction, '') <> ''
      AND COALESCE(m.index_name, '') <> ''
    GROUP BY e.investment_direction, m.index_name, COALESCE(a.status, 'watch')
    ON DUPLICATE KEY UPDATE
      confidence=GREATEST(active_market_anchor_relations.confidence, VALUES(confidence)),
      evidence_count=GREATEST(evidence_count, VALUES(evidence_count)),
      status=VALUES(status), raw_json=VALUES(raw_json), generated_at=VALUES(generated_at);
    """
    run_mysql(config, sql)
    rows = mysql_rows(run_mysql(config, "SELECT COUNT(*) FROM active_market_anchor_relations WHERE source='ths_hot_concept';", batch=True, raw=True))
    return to_int(rows[0][0] if rows and rows[0] else 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect THS '今天炒什么' hot concept events and build active anchors.")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--max-pages", type=int, default=8)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--pause", type=float, default=0.15)
    parser.add_argument("--skip-details", action="store_true")
    parser.add_argument("--skip-members", action="store_true")
    parser.add_argument("--output-json", type=Path, default=None)
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    groups = fetch_event_groups(args.days, args.max_pages, args.timeout, args.pause)
    events, members = normalize_events(groups, not args.skip_details, not args.skip_members, args.timeout, args.pause)
    imported_events = import_events(config, events)
    imported_members = import_members(config, members)
    active_anchors = rebuild_active_anchors(config, args.days)
    active_anchor_members = rebuild_active_anchor_members(config, args.days)
    active_anchor_relations = rebuild_active_anchor_relations(config, args.days)
    active_anchor_matches = rebuild_active_anchor_match_candidates(config)
    payload = {
        "ok": True,
        "collected_at": now_text(),
        "days": args.days,
        "date_groups": len(groups),
        "events": len(events),
        "members": len(members),
        "imported_events": imported_events,
        "imported_members": imported_members,
        "active_anchors": active_anchors,
        "active_anchor_members": active_anchor_members,
        "active_anchor_relations": active_anchor_relations,
        "active_anchor_matches": active_anchor_matches,
    }
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
