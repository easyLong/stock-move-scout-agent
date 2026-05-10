#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_string,
)


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def compact(value: Any, limit: int = 90) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    text = text.replace(" || ", "；")
    return text[:limit]


def brief(value: Any, limit: int = 76, max_parts: int = 2) -> str:
    text = compact(value, limit * 2)
    if not text:
        return ""
    if "：" in text:
        prefix, rest = text.split("：", 1)
        parts = [part.strip() for part in re.split(r"[；;]", rest) if part.strip()]
        text = f"{prefix}：" + "；".join(parts[:max_parts]) if parts else prefix
        if len(text) > limit and parts:
            text = f"{prefix}：{parts[0]}"
    else:
        parts = [part.strip() for part in re.split(r"[；;]", text) if part.strip()]
        text = "；".join(parts[:max_parts]) if parts else text
        if len(text) > limit and parts:
            text = parts[0]
    return compact(text, limit)


def decode_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(bytes.fromhex(value).decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def useful(value: Any) -> bool:
    text = compact(value, 120)
    if not text:
        return False
    empty_markers = {
        "暂无",
        "暂无社区证据",
        "暂无有效社区证据",
        "暂无官网/产品证据",
        "暂无有效官网/产品证据",
        "未采集",
        "待补证据",
        "尚未采集公告硬证据",
        "尚未采集新闻/互动易/官网新闻补充证据",
        "补充源未形成有效线索",
        "公告硬证据不足",
    }
    return text not in empty_markers


def first_useful(*values: Any, limit: int = 90) -> str:
    for value in values:
        text = compact(value, limit)
        if useful(text):
            return text
    return ""


def noisy_homepage_text(value: Any) -> bool:
    text = compact(value, 160)
    if not text:
        return False
    markers = ["首页", "关于我们", "新闻中心", "投资者关系", "门户网站", "联系我们", "ENGLISH"]
    return sum(1 for marker in markers if marker in text) >= 2


def first_clean(*values: Any, limit: int = 90) -> str:
    for value in values:
        text = compact(value, limit)
        if useful(text) and not noisy_homepage_text(text):
            return text
    return ""


def topic_hint(row: dict[str, Any]) -> str:
    concepts = compact(row.get("concepts"), 80)
    if concepts:
        parts = [part.strip() for part in re.split(r"[、,，;；]", concepts) if part.strip()]
        if parts:
            return "、".join(parts[:3])
    return compact(row.get("sub_industry") or row.get("industry"), 40)


def normalize_claim(row: dict[str, Any], claim: str) -> str:
    weak_markers = ["社区证据暂不充分", "缺少解释性证据", "为什么涨仍待补证据", "目前主要只有行情异动"]
    if claim and not any(marker in claim for marker in weak_markers):
        return claim
    topic = topic_hint(row)
    if topic:
        return f"{topic}方向盘面异动，硬催化还没闭环"
    return "盘面出现异动，硬催化还没闭环"


def normalize_gap(gap: str) -> str:
    text = compact(gap, 90)
    if not text:
        return ""
    if "同花顺根页面" in text or "近期事件/公告" in text:
        return "点开公告核细节"
    if "缺少解释性证据" in text or "待补证据" in text or "尚未采集" in text:
        return "补公告、新闻、社区证据"
    if "回到公告原文" in text or "核对公告原文" in text:
        return "回公告原文核金额、客户、期限"
    return text


def latest_window_id(config: Any) -> str:
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


def evidence_rows(config: Any, window_id: str, limit: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT
      el.rank_no,
      el.code,
      COALESCE(s.name, ''),
      el.evidence_strength,
      COALESCE(HEX(CAST(el.raw_json AS CHAR)), '')
    FROM evidence_layers el
    JOIN windows w ON w.id = el.window_id
    LEFT JOIN stocks s ON s.code = el.code
    WHERE w.window_id = {sql_string(window_id)}
    ORDER BY el.rank_no
    LIMIT {int(limit)};
    """
    result: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True)):
        if len(row) < 5:
            continue
        raw = decode_json(row[4])
        raw.setdefault("rank_speed", row[0])
        raw.setdefault("code", row[1])
        raw.setdefault("name", row[2])
        raw.setdefault("evidence_strength", row[3])
        result.append(raw)
    return result


def render_one(row: dict[str, Any]) -> dict[str, Any] | None:
    code = compact(row.get("code"), 16)
    name = compact(row.get("name"), 32)
    claim = first_useful(
        row.get("community_trigger_claim"),
        row.get("community_main_claim"),
        row.get("hard_evidence_summary"),
        row.get("supplemental_summary"),
        row.get("why_hypothesis"),
        limit=96,
    )
    claim = brief(normalize_claim(row, claim), 72, 2)
    evidence = first_clean(
        row.get("order_cooperation_hard_evidence"),
        row.get("amount_terms_evidence"),
        row.get("news_evidence"),
        row.get("irm_evidence"),
        row.get("official_news_evidence"),
        row.get("official_products"),
        row.get("company_positioning"),
        row.get("company_evidence"),
        row.get("sector_evidence"),
        row.get("market_evidence"),
        limit=130,
    )
    evidence = brief(evidence, 82, 2)
    gap = first_useful(
        row.get("evidence_gaps"),
        row.get("hard_evidence_gap"),
        row.get("supplemental_evidence_gap"),
        row.get("next_evidence_action"),
        limit=90,
    )
    gap = normalize_gap(gap)
    risk = first_useful(row.get("community_risk_flags"), row.get("risk_evidence"), limit=80)
    if not claim and not evidence:
        return None

    title = f"{code} {name} 异动侦察"
    lines = [f"{code} {name}", f"看点：{claim or evidence}"]
    if evidence and evidence != claim:
        lines.append(f"证据：{evidence}")
    if gap:
        lines.append(f"待核：{gap}")
    if risk:
        lines.append(f"风险：{risk}")
    lines.append("只做异动复盘，不作投资建议。")
    content = "\n".join(lines)
    return {
        "code": code,
        "title": title,
        "hook": claim or evidence,
        "content": content,
        "publish_level": "watch",
        "raw_json": {"source": "mysql_evidence_layers", "rendered_at": now_text(), "evidence": row},
    }


def write_posts(config: Any, window_id: str, rows: list[dict[str, Any]]) -> int:
    statements = [
        f"DELETE gp FROM generated_posts gp JOIN windows w ON w.id=gp.window_id WHERE w.window_id={sql_string(window_id)} AND gp.post_type='dav_info_gap';"
    ]
    count = 0
    for row in rows:
        post = render_one(row)
        if not post:
            continue
        count += 1
        statements.append(
            f"""
            INSERT INTO generated_posts(window_id, code, post_type, title, hook, content, publish_level, has_content, raw_json)
            VALUES(
              (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
              {sql_string(post["code"])}, 'dav_info_gap', {sql_string(post["title"])},
              {sql_string(post["hook"])}, {sql_string(post["content"])},
              {sql_string(post["publish_level"])}, 1, {sql_json(post["raw_json"])}
            )
            ON DUPLICATE KEY UPDATE
              title=VALUES(title),
              hook=VALUES(hook),
              content=VALUES(content),
              publish_level=VALUES(publish_level),
              has_content=VALUES(has_content),
              raw_json=VALUES(raw_json);
            """
        )
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return count


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render concise DAV info-gap posts from MySQL evidence_layers.")
    add_mysql_args(parser)
    parser.add_argument("--mysql-window-id", default="", help="Window id. Defaults to latest done window.")
    parser.add_argument("--limit", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config = mysql_config_from_args(args)
    window_id = args.mysql_window_id or latest_window_id(config)
    if not window_id:
        raise SystemExit("mysql_window_id_missing")
    rows = evidence_rows(config, window_id, args.limit)
    written = write_posts(config, window_id, rows)
    print(json.dumps({"window_id": window_id, "source_rows": len(rows), "generated_posts": written}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
