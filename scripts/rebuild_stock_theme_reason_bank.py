from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from typing import Any

from stock_scout_mysql import add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql, sql_json, sql_number, sql_string


def compact(value: Any, limit: int = 1024) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    return text[:limit]


def sql_int(value: Any) -> str:
    try:
        return str(int(float(str(value or "0"))))
    except Exception:
        return "0"


def theme_from_title(title: str) -> str:
    text = compact(title, 128)
    text = re.sub(r"^要点[一二三四五六七八九十\d]+[:：]\s*", "", text)
    text = re.sub(r"^题材[:：]\s*", "", text)
    return compact(text, 128)


def company_reason(content: str) -> str:
    text = compact(content, 5000)
    for marker in ["公司原因：", "公司原因:", "个股原因：", "个股原因:", "公司看点：", "公司看点:"]:
        if marker in text:
            out = text.split(marker, 1)[1]
            out = re.split(r"(?:\s热点事件：|\s行业原因：|\s风险提示：|\s免责声明：)", out, maxsplit=1)[0]
            return compact(out, 1024)
    return compact(text, 360)


def source_key(*parts: Any) -> str:
    return "|".join(compact(part, 80) for part in parts)


def stable_source_key(source: str, *parts: Any) -> str:
    text = "|".join(compact(part, 1024) for part in parts)
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()
    return f"{source}:{digest}"


GENERIC_CONCEPT_TAGS = {
    "融资融券",
    "转融券",
    "深股通",
    "沪股通",
    "陆股通",
    "标普道琼斯A股",
    "富时罗素",
    "MSCI概念",
    "证金持股",
    "养老金持股",
    "社保重仓",
    "QFII重仓",
    "新股与次新股",
    "注册制次新股",
    "央企国企改革",
    "国企改革",
    "地方国企改革",
}


EVENT_ANCHOR_KEYWORDS = [
    "收购",
    "拟",
    "股权",
    "股份",
    "质押",
    "减持",
    "增持",
    "转让",
    "分配",
    "预案",
    "股东",
    "公告",
    "诉讼",
    "仲裁",
    "违规",
    "处罚",
    "龙虎榜",
    "融资融券",
    "业绩",
    "年报",
    "季报",
    "中报",
]


COMPANY_FEATURE_KEYWORDS = [
    "优势",
    "能力",
    "覆盖",
    "龙头",
    "领导者",
    "多样性",
    "生产",
    "主营",
    "品牌",
    "客户",
    "市场",
    "研发",
]


def split_tags(tags: str) -> list[str]:
    out: list[str] = []
    for tag in re.split(r"[,，、\s]+", tags or ""):
        tag = compact(tag, 128)
        if tag and tag not in GENERIC_CONCEPT_TAGS:
            out.append(tag)
    return out


def concept_tags_by_code(config: Any) -> dict[str, list[str]]:
    sql = """
    SELECT p.code, COALESCE(p.concept_tags, '')
    FROM stock_company_profiles p
    JOIN stocks s ON s.code = p.code
    WHERE COALESCE(p.concept_tags, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    tag_map: dict[str, list[str]] = {}
    for code, tags in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        tag_map[str(code)] = split_tags(tags)
    return tag_map


def active_anchor_names(config: Any) -> set[str]:
    try:
        rows = mysql_rows(
            run_mysql(
                config,
                "SELECT DISTINCT anchor_name FROM active_market_anchors WHERE COALESCE(anchor_name, '') <> '' AND status <> 'expired';",
                batch=True,
                raw=True,
            )
        )
    except Exception:
        return set()
    return {compact(row[0], 128) for row in rows if row and compact(row[0], 128)}


def matched_concept_tag(anchor: str, tags: list[str]) -> str:
    candidates: list[str] = []
    for tag in tags:
        if len(tag) < 2:
            continue
        base = tag.replace("概念", "")
        if len(base) < 2:
            continue
        if tag == anchor or base == anchor or tag in anchor or base in anchor or anchor in tag:
            candidates.append(tag)
    return max(candidates, key=len) if candidates else ""


def normalize_root_anchor(anchor: str, tags: list[str], active_names: set[str]) -> str:
    anchor = compact(anchor, 128)
    if not anchor:
        return ""
    if any(word in anchor for word in EVENT_ANCHOR_KEYWORDS):
        return ""
    tag = matched_concept_tag(anchor, tags)
    if tag:
        return tag
    if anchor in active_names:
        return anchor
    if anchor.endswith("概念") or anchor.endswith("经济"):
        return anchor
    if any(word in anchor for word in COMPANY_FEATURE_KEYWORDS):
        return ""
    return ""


def read_hot_reason_rows(config: Any) -> list[dict[str, Any]]:
    sql = """
    SELECT
      m.stock_code,
      m.stock_name,
      COALESCE(NULLIF(m.index_name, ''), e.investment_direction, m.theme_name) AS anchor_name,
      COALESCE(NULLIF(m.theme_name, ''), NULLIF(m.index_name, ''), e.investment_direction) AS theme_name,
      m.reason,
      DATE_FORMAT(m.trade_date, '%Y-%m-%d'),
      m.event_id,
      m.theme_id,
      m.index_code,
      m.raw_json
    FROM ths_hot_concept_members m
    JOIN ths_hot_concept_events e ON e.event_id = m.event_id
    JOIN stocks s ON s.code = m.stock_code
    WHERE COALESCE(m.reason, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 10:
            continue
        rows.append(
            {
                "code": row[0],
                "stock_name": row[1],
                "anchor_name": row[2],
                "theme_name": row[3],
                "reason_text": row[4],
                "source": "ths_hot_concept",
                "source_date": row[5],
                "source_key": stable_source_key("ths_hot_concept", row[2], row[3]),
                "confidence": 95,
                "priority": 100,
                "raw_json": {"source_table": "ths_hot_concept_members", "event_id": row[6], "theme_id": row[7], "index_code": row[8]},
            }
        )
    return rows


def read_limit_up_review_rows(config: Any) -> list[dict[str, Any]]:
    sql = """
    SELECT
      i.code,
      i.stock_name,
      i.theme_name,
      i.reason,
      DATE_FORMAT(i.trade_date, '%Y-%m-%d'),
      i.limit_up_days,
      i.first_limit_time,
      i.last_limit_time,
      i.open_count,
      i.status,
      i.raw_json
    FROM ths_limit_up_review_items i
    JOIN stocks s ON s.code = i.code
    WHERE COALESCE(i.theme_name, '') <> ''
      AND COALESCE(i.reason, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    rows: list[dict[str, Any]] = []
    try:
        raw_rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    except Exception:
        return rows
    for row in raw_rows:
        if len(row) < 11:
            continue
        code = row[0]
        stock_name = row[1]
        theme_name = compact(row[2], 128)
        reason = compact(row[3], 1024)
        source_date = row[4] or None
        if not code or not theme_name or not reason:
            continue
        rows.append(
            {
                "code": code,
                "stock_name": stock_name,
                "anchor_name": theme_name,
                "theme_name": theme_name,
                "reason_text": reason,
                "source": "ths_limit_up_review",
                "source_date": source_date,
                "source_key": stable_source_key("ths_limit_up_review", source_date or "", code, theme_name),
                "confidence": 98,
                "priority": 110,
                "raw_json": {
                    "source_table": "ths_limit_up_review_items",
                    "limit_up_days": row[5],
                    "first_limit_time": row[6],
                    "last_limit_time": row[7],
                    "open_count": row[8],
                    "status": row[9],
                },
            }
        )
    return rows


def parse_json_value(value: Any) -> Any:
    if value in (None, ""):
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return None


def read_stock_concept_rows(config: Any) -> list[dict[str, Any]]:
    sql = """
    SELECT
      c.code,
      c.stock_name,
      c.concept_name,
      c.concept_id,
      c.quote_code,
      c.fit_rank,
      COALESCE(c.reason_explain, ''),
      CAST(c.tags AS CHAR),
      CAST(c.self_sub_reasons_json AS CHAR),
      CAST(c.leading_json AS CHAR),
      DATE_FORMAT(c.fetched_at, '%Y-%m-%d')
    FROM ths_stock_concept_explanations c
    JOIN stocks s ON s.code = c.code
    WHERE COALESCE(c.concept_name, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 11:
            continue
        code = row[0]
        stock_name = row[1]
        concept_name = compact(row[2], 128)
        if not concept_name or concept_name in GENERIC_CONCEPT_TAGS:
            continue
        concept_id = row[3]
        quote_code = row[4]
        fit_rank = row[5]
        concept_reason = compact(row[6], 1024)
        tags = parse_json_value(row[7]) or []
        self_reasons = parse_json_value(row[8]) or []
        leading = parse_json_value(row[9]) or []
        source_date = row[10] or None
        if concept_reason:
            rows.append(
                {
                    "code": code,
                    "stock_name": stock_name,
                    "anchor_name": concept_name,
                    "theme_name": concept_name,
                    "reason_text": concept_reason,
                    "source": "ths_stock_concept",
                    "source_date": source_date,
                    "source_key": stable_source_key("ths_stock_concept", concept_id or concept_name, concept_name),
                    "confidence": 88,
                    "priority": 90,
                    "raw_json": {
                        "source_table": "ths_stock_concept_explanations",
                        "concept_id": concept_id,
                        "quote_code": quote_code,
                        "fit_rank": fit_rank,
                        "tags": tags,
                        "leading": leading[:5] if isinstance(leading, list) else [],
                        "reason_level": "concept",
                    },
                }
            )
        if isinstance(self_reasons, list):
            for item in self_reasons:
                if not isinstance(item, dict):
                    continue
                sub_name = compact(item.get("sub_name"), 128)
                reason = compact(item.get("reason"), 1024)
                if not sub_name or not reason:
                    continue
                rows.append(
                    {
                        "code": code,
                        "stock_name": stock_name,
                        "anchor_name": concept_name,
                        "theme_name": sub_name,
                        "reason_text": reason,
                        "source": "ths_stock_concept",
                        "source_date": source_date,
                        "source_key": stable_source_key("ths_stock_concept", concept_id or concept_name, sub_name),
                        "confidence": 92,
                        "priority": 95,
                        "raw_json": {
                            "source_table": "ths_stock_concept_explanations",
                            "concept_id": concept_id,
                            "quote_code": quote_code,
                            "fit_rank": fit_rank,
                            "tags": tags,
                            "sub_name": sub_name,
                            "sub_id": item.get("sub_id", ""),
                            "sub_explain": item.get("sub_explain", ""),
                            "reason_level": "sub_concept_self_reason",
                        },
                    }
                )
    return rows


def read_root_theme_rows(config: Any) -> list[dict[str, Any]]:
    tag_map = concept_tags_by_code(config)
    active_names = active_anchor_names(config)
    sql = """
    SELECT
      i.code,
      i.stock_name,
      i.title,
      COALESCE(i.content, ''),
      COALESCE(DATE_FORMAT(i.item_date, '%Y-%m-%d'), ''),
      i.item_key,
      i.source_section,
      i.source_rank
    FROM stock_ths_root_items i
    JOIN stocks s ON s.code = i.code
    WHERE i.item_kind = 'theme_point'
      AND COALESCE(i.content, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 8:
            continue
        original_anchor = theme_from_title(row[2])
        anchor = normalize_root_anchor(original_anchor, tag_map.get(str(row[0]), []), active_names)
        reason = company_reason(row[3])
        if not anchor or not reason:
            continue
        has_company_reason = any(marker in row[3] for marker in ["公司原因：", "公司原因:", "个股原因：", "个股原因:"])
        rows.append(
            {
                "code": row[0],
                "stock_name": row[1],
                "anchor_name": anchor,
                "theme_name": anchor,
                "reason_text": reason,
                "source": "ths_root_theme_point",
                "source_date": row[4] or None,
                "source_key": stable_source_key("ths_root_theme_point", anchor),
                "confidence": 78 if has_company_reason else 62,
                "priority": 70 if has_company_reason else 55,
                "raw_json": {
                    "source_table": "stock_ths_root_items",
                    "title": row[2],
                    "original_anchor": original_anchor,
                    "source_section": row[6],
                    "source_rank": row[7],
                    "has_company_reason": has_company_reason,
                },
            }
        )
    return rows


def read_concept_tag_rows(config: Any) -> list[dict[str, Any]]:
    sql = """
    SELECT p.code, p.stock_name, COALESCE(p.concept_tags, '')
    FROM stock_company_profiles p
    JOIN stocks s ON s.code = p.code
    WHERE COALESCE(p.concept_tags, '') <> ''
      AND COALESCE(s.is_st, 0) = 0;
    """
    rows: list[dict[str, Any]] = []
    for code, stock_name, tags in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        for tag in re.split(r"[,，、\s]+", tags or ""):
            tag = compact(tag, 128)
            if not tag or tag in GENERIC_CONCEPT_TAGS:
                continue
            rows.append(
                {
                    "code": code,
                    "stock_name": stock_name,
                    "anchor_name": tag,
                    "theme_name": tag,
                    "reason_text": f"股票概念标签命中：{tag}",
                    "source": "concept_tag",
                    "source_date": None,
                    "source_key": stable_source_key("concept_tag", tag),
                    "confidence": 40,
                    "priority": 20,
                    "raw_json": {"source_table": "stock_company_profiles", "concept_tag": tag},
                }
            )
    return rows


def chunked(items: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [items[idx : idx + size] for idx in range(0, len(items), size)]


def row_date_value(row: dict[str, Any]) -> str:
    return str(row.get("source_date") or "")


def dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("code") or ""),
            compact(row.get("anchor_name"), 128),
            compact(row.get("theme_name"), 128),
            str(row.get("source") or ""),
        )
        current = best.get(key)
        if current is None:
            best[key] = row
            continue
        current_score = (int(current.get("priority") or 0), float(current.get("confidence") or 0), row_date_value(current))
        row_score = (int(row.get("priority") or 0), float(row.get("confidence") or 0), row_date_value(row))
        if row_score >= current_score:
            best[key] = row
    return list(best.values())


def write_rows(config: Any, rows: list[dict[str, Any]], replace: bool, chunk_size: int) -> int:
    statements: list[str] = []
    if replace:
        statements.append("TRUNCATE TABLE stock_theme_reason_bank;")
    if statements:
        run_mysql(config, "\n".join(statements))
    written = 0
    for group in chunked(rows, chunk_size):
        values: list[str] = []
        for row in group:
            values.append(
                "("
                + ", ".join(
                    [
                        sql_string(row["code"]),
                        sql_string(row["stock_name"]),
                        sql_string(row["anchor_name"]),
                        sql_string(row["theme_name"]),
                        sql_string(row["reason_text"]),
                        sql_string(row["source"]),
                        sql_string(row["source_date"]) if row.get("source_date") else "NULL",
                        sql_string(row["source_key"]),
                        sql_number(row["confidence"]),
                        sql_int(row["priority"]),
                        "'active'",
                        sql_json(row["raw_json"]),
                    ]
                )
                + ")"
            )
        sql = f"""
        INSERT INTO stock_theme_reason_bank(
          code, stock_name, anchor_name, theme_name, reason_text, source, source_date,
          source_key, confidence, priority, status, raw_json
        )
        VALUES {",".join(values)}
        ON DUPLICATE KEY UPDATE
          stock_name=VALUES(stock_name),
          reason_text=VALUES(reason_text),
          source_date=VALUES(source_date),
          confidence=VALUES(confidence),
          priority=VALUES(priority),
          status=VALUES(status),
          raw_json=VALUES(raw_json);
        """
        run_mysql(config, sql)
        written += len(group)
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build global stock-theme reason bank from THS hot concepts, stock concepts, and fallback tags.")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--include-stock-concepts", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-concept-tags", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--chunk-size", type=int, default=500)
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = mysql_config_from_args(args)
    root_rows = read_root_theme_rows(config)
    limit_up_rows = read_limit_up_review_rows(config)
    hot_rows = read_hot_reason_rows(config)
    stock_concept_rows = read_stock_concept_rows(config) if args.include_stock_concepts else []
    concept_rows: list[dict[str, Any]] = []
    rows = list(root_rows)
    rows.extend(limit_up_rows)
    rows.extend(stock_concept_rows)
    rows.extend(hot_rows)
    if args.include_concept_tags:
        concept_rows = read_concept_tag_rows(config)
        rows.extend(concept_rows)
    raw_rows = len(rows)
    rows = dedupe_rows(rows)
    written = write_rows(config, rows, args.replace, max(1, args.chunk_size))
    print(
        json.dumps(
            {
                "written": written,
                "raw_rows": raw_rows,
                "root_theme_rows": len(root_rows),
                "limit_up_review_rows": len(limit_up_rows),
                "stock_concept_rows": len(stock_concept_rows),
                "hot_reason_rows": len(hot_rows),
                "concept_tag_rows": len(concept_rows),
                "deduped_rows": len(rows),
                "include_concept_tags": args.include_concept_tags,
                "built_at": date.today().isoformat(),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
