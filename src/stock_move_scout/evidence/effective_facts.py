from __future__ import annotations

import json
import re
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_json, sql_string
from stock_move_scout.evidence.display_renderer import (
    lhb_blue_label_seats,
    render_effective_fact_display,
)


VALID_DISPLAY_LEVELS = ("primary", "secondary", "background")


DEFAULT_LHB_ALLOW_RULES = (
    ("炒股养家", "知名游资"),
    ("养家", "知名游资简称"),
    ("章盟主", "知名游资"),
    ("方新侠", "知名游资"),
    ("作手新一", "知名游资"),
    ("小鳄鱼", "知名游资"),
    ("赵老哥", "知名游资"),
    ("上塘路", "知名游资/席位"),
    ("陈小群", "知名游资"),
    ("呼家楼", "知名游资/席位"),
    ("桑田路", "知名游资/席位"),
    ("宁波桑田路", "知名游资/席位"),
    ("佛山系", "知名游资"),
    ("溧阳路", "知名游资/席位"),
    ("上海溧阳路", "知名游资/席位"),
    ("北京中关村", "活跃席位"),
    ("著名游资", "强资金标签"),
    ("知名游资", "强资金标签"),
    ("一线游资", "强资金标签"),
    ("净买入[[:space:]]*[0-9一二三四五六七八九十百千万亿\\.]+", "明确净买入金额"),
    ("买入金额[^，。；;]*[0-9一二三四五六七八九十百千万亿\\.]+", "明确买入金额"),
    ("买入总计[[:space:]]*[0-9一二三四五六七八九十百千万亿\\.]+", "龙虎榜买入汇总"),
    ("卖出总计[[:space:]]*[0-9一二三四五六七八九十百千万亿\\.]+", "龙虎榜卖出汇总"),
    ("证券营业部[^，。；;\\n]{0,30}(系|专用|游资|机构)", "龙虎榜席位带标签"),
    ("成都系", "活跃游资标签"),
)


LHB_TAG_HINTS = (
    "成都系",
    "炒股养家",
    "章盟主",
    "方新侠",
    "作手新一",
    "小鳄鱼",
    "赵老哥",
    "上塘路",
    "陈小群",
    "呼家楼",
    "桑田路",
    "宁波桑田路",
    "佛山系",
    "溧阳路",
    "上海溧阳路",
    "北京中关村",
    "机构专用",
    "深股通专用",
    "沪股通专用",
    "量化基金",
    "量化打板",
    "知名游资",
    "著名游资",
    "一线游资",
)


DEFAULT_LOW_VALUE_TITLE_RULES = (
    ("^融资融券$", "低价值例行信息"),
    ("^发布公告$", "公告入口类标题"),
    ("^投资互动$", "互动易入口类标题"),
    ("^股东人数变化$", "低价值例行信息"),
    ("^股东大会$", "低价值例行信息"),
    ("^分配预案$", "低价值例行信息"),
    ("^实施分红$", "低价值例行信息"),
    ("^异动提醒$", "低价值例行信息"),
    ("^大宗交易$", "低价值例行信息"),
)


LHB_MONEY_RE = re.compile(r"(三日)?(净买入|买入总计|卖出总计)\s*-?[\d.]+\s*[万亿]?元?")
LHB_TAG_RE = re.compile(r"(?:证券营业部|机构专用|深股通专用|沪股通专用)[^，。；;\n]{0,40}?([\u4e00-\u9fa5A-Za-z0-9]{2,12}(?:系|专用|游资|机构|基金|打板))")


def ensure_effective_facts_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS stock_effective_facts (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      source_table VARCHAR(64) NOT NULL,
      source_key VARCHAR(128) NOT NULL,
      source_confidence VARCHAR(32) NOT NULL DEFAULT 'explicit',
      fact_type VARCHAR(32) NOT NULL DEFAULT '',
      fact_subtype VARCHAR(64) NOT NULL DEFAULT '',
      fact_title VARCHAR(512) NOT NULL DEFAULT '',
      fact_body TEXT NULL,
      fact_date DATE NULL,
      valid_status ENUM('active','watch','historical','expired','invalid') NOT NULL DEFAULT 'watch',
      valid_score DECIMAL(8,2) NOT NULL DEFAULT 0,
      valid_reason VARCHAR(255) NOT NULL DEFAULT '',
      invalid_reason VARCHAR(255) NOT NULL DEFAULT '',
      evidence_role VARCHAR(64) NOT NULL DEFAULT '',
      evidence_group ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'background_fact',
      display_level ENUM('primary','secondary','background','hidden') NOT NULL DEFAULT 'secondary',
      valid_from DATE NULL,
      valid_until DATE NULL,
      payload JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_effective_fact_source (trade_date, source_table, source_key),
      KEY idx_effective_fact_code_day (code, trade_date, display_level, valid_score),
      KEY idx_effective_fact_group (trade_date, evidence_group, display_level, valid_score),
      KEY idx_effective_fact_role (trade_date, evidence_role, display_level),
      KEY idx_effective_fact_source_table (source_table, source_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

    CREATE TABLE IF NOT EXISTS stock_effective_facts_dirty_queue (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      reason VARCHAR(255) NOT NULL DEFAULT '',
      changed_sources JSON NULL,
      priority INT NOT NULL DEFAULT 35,
      status ENUM('pending','running','done','failed','ignored') NOT NULL DEFAULT 'pending',
      attempt_count INT NOT NULL DEFAULT 0,
      locked_at DATETIME(3) NULL,
      finished_at DATETIME(3) NULL,
      last_error TEXT NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_effective_facts_dirty (trade_date, code, reason),
      KEY idx_effective_facts_dirty_status (status, priority, created_at),
      KEY idx_effective_facts_dirty_code (code, trade_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)
    ensure_effective_fact_rule_tables(config)
    ensure_current_effective_facts_view(config)
    ensure_effective_facts_column(
        config,
        "evidence_group",
        "ENUM('current_effective','post_close_confirm','background_fact','historical_tag','hidden') NOT NULL DEFAULT 'background_fact' AFTER evidence_role",
    )
    ensure_effective_facts_index(config, "idx_effective_fact_group", "(trade_date, evidence_group, display_level, valid_score)")


def ensure_effective_fact_rule_tables(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS stock_effective_fact_rules (
          id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          rule_group VARCHAR(64) NOT NULL,
          rule_type VARCHAR(64) NOT NULL,
          pattern VARCHAR(512) NOT NULL,
          enabled TINYINT NOT NULL DEFAULT 1,
          note VARCHAR(255) NOT NULL DEFAULT '',
          created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          UNIQUE KEY uk_effective_fact_rule (rule_group, rule_type, pattern),
          KEY idx_effective_fact_rule_lookup (rule_group, rule_type, enabled)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
        """,
    )
    values: list[str] = []
    for pattern, note in DEFAULT_LHB_ALLOW_RULES:
        values.append(
            "("
            + ",".join(
                [
                    sql_string("lhb"),
                    sql_string("allow_keyword"),
                    sql_string(pattern),
                    "1",
                    sql_string(note),
                ]
            )
            + ")"
        )
    for pattern, note in DEFAULT_LOW_VALUE_TITLE_RULES:
        values.append(
            "("
            + ",".join(
                [
                    sql_string("important_event"),
                    sql_string("exclude_title"),
                    sql_string(pattern),
                    "1",
                    sql_string(note),
                ]
            )
            + ")"
        )
    if values:
        run_mysql(
            config,
            """
            INSERT INTO stock_effective_fact_rules(rule_group, rule_type, pattern, enabled, note)
            VALUES
            """
            + ",".join(values)
            + """
            ON DUPLICATE KEY UPDATE
              note=VALUES(note),
              updated_at=CURRENT_TIMESTAMP(3);
            """,
        )


def _effective_rule_regex(
    config: MySqlConfig,
    rule_group: str,
    rule_type: str,
    default_rules: tuple[tuple[str, str], ...],
) -> str:
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT pattern
            FROM stock_effective_fact_rules
            WHERE rule_group={sql_string(rule_group)}
              AND rule_type={sql_string(rule_type)}
              AND enabled=1
            ORDER BY id ASC;
            """,
            batch=True,
            raw=True,
        )
    )
    patterns = [str(row[0] or "").strip() for row in rows if row and str(row[0] or "").strip()]
    if not patterns:
        patterns = [pattern for pattern, _note in default_rules]
    return "|".join(patterns) or r"$^"


def ensure_current_effective_facts_view(config: MySqlConfig) -> None:
    low_value_title_regex = _effective_rule_regex(config, "important_event", "exclude_title", DEFAULT_LOW_VALUE_TITLE_RULES)
    run_mysql(
        config,
        f"""
        CREATE OR REPLACE VIEW stock_current_effective_facts_view AS
        SELECT
          i.code,
          i.stock_name,
          'stock_ths_root_items' AS source_table,
          CONCAT('stock_ths_root_items:', i.id) AS source_key,
          'important_event' AS fact_type,
          COALESCE(JSON_UNQUOTE(JSON_EXTRACT(i.tags, '$[0]')), '') AS fact_subtype,
          i.title AS fact_title,
          LEFT(
            CONCAT_WS(' ',
              COALESCE(NULLIF(i.content, ''), i.title),
              NULLIF(i.detail_content, '')
            ),
            1200
          ) AS fact_body,
          i.item_date AS fact_date,
          i.item_date AS valid_from,
          DATE_ADD(i.item_date, INTERVAL 10 DAY) AS valid_until,
          JSON_OBJECT(
            'root_item_id', i.id,
            'item_key', i.item_key,
            'source_section', i.source_section,
            'source_rank', i.source_rank,
            'url', i.url,
            'detail_content', COALESCE(i.detail_content, ''),
            'tags', COALESCE(i.tags, JSON_ARRAY()),
            'raw_json', COALESCE(i.raw_json, JSON_OBJECT()),
            'rule', 'recent_important_event_10d'
          ) AS payload
        FROM stock_ths_root_items i
        WHERE i.item_kind='important_event'
          AND i.item_date IS NOT NULL
          AND NOT (COALESCE(i.title, '') REGEXP {sql_string(low_value_title_regex)});
        """,
    )


def ensure_effective_facts_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'stock_effective_facts'
              AND COLUMN_NAME = {sql_string(column_name)};
            """,
            batch=True,
            raw=True,
        )
    )
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE stock_effective_facts ADD COLUMN {column_name} {column_sql};")


def ensure_effective_facts_index(config: MySqlConfig, index_name: str, index_sql: str) -> None:
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT COUNT(*)
            FROM INFORMATION_SCHEMA.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'stock_effective_facts'
              AND INDEX_NAME = {sql_string(index_name)};
            """,
            batch=True,
            raw=True,
        )
    )
    exists = rows and rows[0] and rows[0][0] == "1"
    if not exists:
        try:
            run_mysql(config, f"ALTER TABLE stock_effective_facts ADD KEY {index_name} {index_sql};")
        except RuntimeError as exc:
            if "Duplicate key name" not in str(exc):
                raise


def _code_filter(alias: str, code: str = "", codes: list[str] | None = None) -> str:
    if code:
        return f"AND {alias}.code={sql_string(code)}"
    if codes is not None:
        clean_codes = sorted({str(item or "").strip() for item in codes if str(item or "").strip()})
        if not clean_codes:
            return "AND 1=0"
        return f"AND {alias}.code IN ({','.join(sql_string(item) for item in clean_codes)})"
    return ""


def clear_effective_facts(config: MySqlConfig, trade_date: str, code: str = "", codes: list[str] | None = None) -> None:
    ensure_effective_facts_table(config)
    code_filter = _code_filter("", code, codes).replace("AND .code", "AND code")
    run_mysql(
        config,
        f"""
        DELETE FROM stock_effective_facts
        WHERE trade_date={sql_string(trade_date)}
          {code_filter};
        """,
    )


def clear_retired_effective_fact_sources(config: MySqlConfig, trade_date: str) -> None:
    ensure_effective_facts_table(config)
    run_mysql(
        config,
        f"""
        DELETE FROM stock_effective_facts
        WHERE trade_date={sql_string(trade_date)}
          AND source_table IN (
            'stock_lhb_seat_evidence',
            'ths_stock_concept_explanations',
            'disabled_post_close_review'
          );
        """,
    )


def build_effective_facts(config: MySqlConfig, trade_date: str, code: str = "", codes: list[str] | None = None) -> dict[str, Any]:
    ensure_effective_facts_table(config)
    clear_retired_effective_fact_sources(config, trade_date)
    clear_effective_facts(config, trade_date, code, codes)
    statements = [
        _current_important_event_sql(trade_date, code, codes),
        _kpl_limit_up_reason_sql(trade_date, code, codes),
    ]
    for sql in statements:
        run_mysql(config, sql)
    filter_lhb_effective_facts_by_blue_labels(config, trade_date, code, codes)
    annotate_lhb_effective_facts(config, trade_date, code, codes)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT evidence_group, display_level, valid_status, COUNT(*), COUNT(DISTINCT code)
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              {_code_filter('', code, codes).replace('AND .code', 'AND code')}
            GROUP BY evidence_group, display_level, valid_status
            ORDER BY evidence_group, display_level, valid_status;
            """,
            batch=True,
            raw=True,
        )
    )
    return {
        "trade_date": trade_date,
        "code": code,
        "groups": [
            {"evidence_group": row[0], "display_level": row[1], "valid_status": row[2], "facts": int(row[3]), "codes": int(row[4])}
            for row in rows
            if len(row) >= 5
        ],
    }


def _load_lhb_label_rules(config: MySqlConfig) -> list[tuple[str, str]]:
    rows = mysql_rows(
        run_mysql(
            config,
            """
            SELECT pattern, note
            FROM stock_effective_fact_rules
            WHERE rule_group='lhb'
              AND rule_type='allow_keyword'
              AND enabled=1
            ORDER BY id ASC;
            """,
            batch=True,
            raw=True,
        )
    )
    return [(str(row[0] or ""), str(row[1] or "")) for row in rows if row and str(row[0] or "").strip()]


def _extract_lhb_tags(text: str, rules: list[tuple[str, str]]) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for hint in LHB_TAG_HINTS:
        if hint and hint in text and hint not in seen:
            tags.append(hint)
            seen.add(hint)
    for match in LHB_TAG_RE.finditer(text):
        label = match.group(1).strip()
        if label and label not in seen:
            tags.append(label)
            seen.add(label)
    for pattern, note in rules:
        if not pattern or "净买入" in pattern or "买入总计" in pattern or "卖出总计" in pattern or "买入金额" in pattern:
            continue
        try:
            if re.search(pattern.replace("[[:space:]]", r"\s"), text) and note and note not in {"龙虎榜席位带标签"} and note not in seen:
                tags.append(note)
                seen.add(note)
        except re.error:
            continue
    return tags


def _extract_lhb_money_points(text: str) -> list[str]:
    points: list[str] = []
    seen: set[str] = set()
    for match in LHB_MONEY_RE.finditer(text):
        value = match.group(0).strip()
        if value and value not in seen:
            points.append(value)
            seen.add(value)
    return points[:5]


def _parse_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = {}
    return value if isinstance(value, dict) else {}


def _payload_lhb_seats(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    seats = raw.get("lhb_seats")
    if not isinstance(seats, list):
        seats = payload.get("lhb_seats")
    return [seat for seat in seats or [] if isinstance(seat, dict)]


def _has_lhb_blue_label(payload: dict[str, Any]) -> bool:
    return bool(lhb_blue_label_seats(payload))


def filter_lhb_effective_facts_by_blue_labels(config: MySqlConfig, trade_date: str, code: str = "", codes: list[str] | None = None) -> None:
    code_filter = _code_filter("", code, codes).replace("AND .code", "AND code")
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT id, fact_title, COALESCE(fact_body, ''), COALESCE(payload, JSON_OBJECT())
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              AND evidence_group='current_effective'
              AND source_table='stock_ths_root_items'
              {code_filter}
            """,
            batch=True,
            raw=True,
        )
    )
    delete_ids: list[int] = []
    for row in rows:
        if len(row) < 4:
            continue
        fact_id = int(str(row[0] or "0") or 0)
        payload = _parse_payload(row[3])
        raw = _lhb_raw_json(payload)
        text = " ".join([str(row[1] or ""), str(row[2] or ""), str(payload.get("detail_content") or "")])
        is_lhb = bool(raw.get("lhb_seats")) or "虎" in text
        if is_lhb and not _has_lhb_blue_label(payload):
            delete_ids.append(fact_id)
    if delete_ids:
        run_mysql(
            config,
            f"""
            DELETE FROM stock_effective_facts
            WHERE id IN ({','.join(str(item) for item in delete_ids)});
            """,
        )


def _extract_lhb_tagged_seats(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    tags: list[str] = []
    lines: list[str] = []
    seen_tags: set[str] = set()
    seen_lines: set[str] = set()
    for seat in _payload_lhb_seats(payload):
        labels = seat.get("labels")
        if not isinstance(labels, list):
            continue
        label_names: list[str] = []
        for label in labels:
            if not isinstance(label, dict):
                continue
            name = str(label.get("name") or "").strip()
            if name:
                label_names.append(name)
                if name not in seen_tags:
                    tags.append(name)
                    seen_tags.add(name)
        if not label_names:
            continue
        side = {"buy": "买入", "sell": "卖出"}.get(str(seat.get("side") or ""), "席位")
        buy_amount = str(seat.get("buy_amount") or "").strip()
        sell_amount = str(seat.get("sell_amount") or "").strip()
        net_amount = str(seat.get("net_amount") or "").strip()
        line = (
            f"{side}标签 {'、'.join(label_names)}"
            f"：买入{buy_amount}，卖出{sell_amount}，净额{net_amount}"
        )
        if line not in seen_lines:
            lines.append(line)
            seen_lines.add(line)
    return tags, lines


def _lhb_raw_json(payload: dict[str, Any]) -> dict[str, Any]:
    raw = payload.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _compact_lhb_body(body: str, payload: dict[str, Any], tags: list[str], seat_lines: list[str], money_points: list[str]) -> str:
    parts: list[str] = []
    if tags:
        parts.append("、".join(tags[:5]))
    for line in seat_lines[:3]:
        if line and line not in parts:
            parts.append(line)
    if not seat_lines and not tags and money_points:
        parts.append("龙虎榜金额确认：" + "、".join(money_points[:3]))
    if not parts:
        return body
    return "；".join(parts)[:500]


def annotate_lhb_effective_facts(config: MySqlConfig, trade_date: str, code: str = "", codes: list[str] | None = None) -> None:
    code_filter = _code_filter("", code, codes).replace("AND .code", "AND code")
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT id, fact_title, COALESCE(fact_body, ''), COALESCE(payload, JSON_OBJECT())
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              AND evidence_group='current_effective'
              AND source_table='stock_ths_root_items'
              {code_filter}
            """,
            batch=True,
            raw=True,
        )
    )
    if not rows:
        return
    statements: list[str] = []
    for row in rows:
        if len(row) < 4:
            continue
        fact_id = str(row[0] or "").strip()
        title = str(row[1] or "")
        body = str(row[2] or "")
        payload: Any = row[3]
        payload = _parse_payload(payload)
        detail = str(payload.get("detail_content") or "")
        text = " ".join([title, body, detail])
        raw = _lhb_raw_json(payload)
        if "虎" not in text and not raw.get("lhb_seats"):
            continue
        seat_tags, seat_lines = _extract_lhb_tagged_seats(payload)
        tags = []
        seen_tags: set[str] = set()
        for tag in seat_tags:
            if tag and tag not in seen_tags:
                tags.append(tag)
                seen_tags.add(tag)
        money_points = _extract_lhb_money_points(text)
        display = render_effective_fact_display(fact_title=title, fact_body=body, payload=payload)
        enriched_body = str(display.get("display_body") or body)
        payload["lhb_tags"] = tags
        payload["lhb_tagged_seats"] = seat_lines
        payload["lhb_money_points"] = money_points
        payload["lhb_rule"] = "blue_label_only"
        payload["display_kind"] = display.get("display_kind", "")
        payload["display_lines"] = display.get("display_lines", [])
        payload["display_body"] = display.get("display_body", "")
        statements.append(
            f"""
            UPDATE stock_effective_facts
            SET fact_body={sql_string(enriched_body)},
                payload={sql_json(payload)},
                updated_at=CURRENT_TIMESTAMP(3)
            WHERE id={int(fact_id)};
            """
        )
    if statements:
        run_mysql(config, "\n".join(statements))


def enqueue_effective_facts_dirty(
    config: MySqlConfig,
    *,
    trade_date: str,
    code: str,
    stock_name: str = "",
    reason: str = "source_fact_updated",
    changed_sources: list[str] | None = None,
    priority: int = 35,
) -> None:
    ensure_effective_facts_table(config)
    code = str(code or "").strip()
    if not code:
        return
    sql = f"""
    INSERT INTO stock_effective_facts_dirty_queue(
      trade_date, code, stock_name, reason, changed_sources, priority, status
    ) VALUES (
      {sql_string(trade_date)},
      {sql_string(code)},
      {sql_string(stock_name)},
      {sql_string(reason)},
      {sql_json(changed_sources or [])},
      {int(priority)},
      'pending'
    )
    ON DUPLICATE KEY UPDATE
      stock_name=COALESCE(NULLIF(VALUES(stock_name), ''), stock_name),
      changed_sources=VALUES(changed_sources),
      priority=LEAST(priority, VALUES(priority)),
      status=IF(status IN ('done','ignored'), 'pending', status),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)


def fetch_effective_facts_dirty(
    config: MySqlConfig,
    trade_date: str,
    limit: int,
    code: str = "",
    codes: list[str] | None = None,
) -> list[dict[str, str]]:
    ensure_effective_facts_table(config)
    code_filter = f"AND code={sql_string(code)}" if code else ""
    if not code_filter:
        clean_codes = sorted({str(item or "").strip() for item in (codes or []) if str(item or "").strip()})
        if clean_codes:
            code_filter = f"AND code IN ({','.join(sql_string(item) for item in clean_codes)})"
        elif codes is not None:
            code_filter = "AND 1=0"
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT id, code, stock_name, COALESCE(changed_sources, JSON_ARRAY())
            FROM stock_effective_facts_dirty_queue
            WHERE trade_date={sql_string(trade_date)}
              AND (
                status='pending'
                OR (status='running' AND locked_at < DATE_SUB(NOW(3), INTERVAL 5 MINUTE))
              )
              {code_filter}
            ORDER BY priority ASC, created_at ASC
            LIMIT {int(limit)};
            """,
            batch=True,
            raw=True,
        )
    )
    out: list[dict[str, str]] = []
    for row in rows:
        if len(row) >= 3:
            out.append({"dirty_id": row[0], "code": row[1], "stock_name": row[2], "changed_sources": row[3] if len(row) > 3 else "[]"})
    ids = [str(item["dirty_id"]) for item in out if str(item.get("dirty_id", "")).isdigit()]
    if ids:
        run_mysql(
            config,
            f"""
            UPDATE stock_effective_facts_dirty_queue
            SET status='running',
                locked_at=CURRENT_TIMESTAMP(3),
                updated_at=CURRENT_TIMESTAMP(3)
            WHERE id IN ({",".join(ids)})
              AND status IN ('pending','running');
            """,
        )
    return out


def mark_effective_facts_dirty(config: MySqlConfig, dirty_id: str, status: str, error: str = "") -> None:
    if not dirty_id:
        return
    run_mysql(
        config,
        f"""
        UPDATE stock_effective_facts_dirty_queue
        SET status={sql_string(status)},
            finished_at=IF({sql_string(status)} IN ('done','failed','ignored'), CURRENT_TIMESTAMP(3), finished_at),
            last_error={sql_string(error[:1000])},
            attempt_count=attempt_count + IF({sql_string(status)}='failed', 1, 0),
            updated_at=CURRENT_TIMESTAMP(3)
        WHERE id={int(dirty_id)};
        """,
    )



def _current_important_event_sql(trade_date: str, code: str = "", codes: list[str] | None = None) -> str:
    code_filter = _code_filter("f", code, codes)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      f.code,
      f.stock_name,
      f.source_table,
      f.source_key,
      'explicit',
      f.fact_type,
      f.fact_subtype,
      f.fact_title,
      LEFT(CONCAT(
        DATE_FORMAT(f.fact_date, '%m-%d'), ' ',
        COALESCE(NULLIF(f.fact_body, ''), f.fact_title)
      ), 600),
      f.fact_date,
      'active',
      CASE
        WHEN f.fact_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 3 DAY) THEN 95 - DATEDIFF(CAST({day} AS DATE), f.fact_date)
        ELSE 78 - LEAST(DATEDIFF(CAST({day} AS DATE), f.fact_date), 10)
      END,
      'recent_important_event_10d',
      '',
      'hard_catalyst',
      'current_effective',
      CASE WHEN f.fact_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 3 DAY) THEN 'primary' ELSE 'secondary' END,
      f.valid_from,
      f.valid_until,
      f.payload
    FROM stock_current_effective_facts_view f
    WHERE f.fact_date <= CAST({day} AS DATE)
      AND f.fact_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
      {code_filter}
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      valid_reason=VALUES(valid_reason),
      invalid_reason=VALUES(invalid_reason),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      valid_until=VALUES(valid_until),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def _kpl_limit_up_reason_sql(trade_date: str, code: str = "", codes: list[str] | None = None) -> str:
    code_filter = _code_filter("r", code, codes)
    day = sql_string(trade_date)
    return f"""
    INSERT INTO stock_effective_facts(
      trade_date, code, stock_name, source_table, source_key, source_confidence,
      fact_type, fact_subtype, fact_title, fact_body, fact_date,
      valid_status, valid_score, valid_reason, invalid_reason,
      evidence_role, evidence_group, display_level, valid_from, valid_until, payload
    )
    SELECT
      CAST({day} AS DATE),
      r.code,
      r.stock_name,
      'kpl_stock_limit_up_reasons',
      CONCAT('kpl_stock_limit_up_reasons:', r.code, ':', DATE_FORMAT(r.reason_date, '%Y-%m-%d')),
      'explicit',
      'limit_up_reason',
      COALESCE(NULLIF(r.role_label, ''), '开盘啦涨停归因'),
      COALESCE(NULLIF(r.reason_title, ''), '开盘啦涨停归因'),
      LEFT(CONCAT_WS('；',
        NULLIF(r.role_label, ''),
        NULLIF(r.reason_text, ''),
        IF(COALESCE(r.boom_theme, '') <> '', CONCAT('题材背景：', r.boom_theme), NULL)
      ), 900),
      r.reason_date,
      'active',
      CASE
        WHEN r.reason_date=CAST({day} AS DATE) THEN 92
        WHEN r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 3 DAY) THEN 86 - DATEDIFF(CAST({day} AS DATE), r.reason_date)
        ELSE 70 - LEAST(DATEDIFF(CAST({day} AS DATE), r.reason_date), 10)
      END,
      'kpl_limit_up_reason_10d',
      '',
      'theme_confirmation',
      'current_effective',
      CASE WHEN r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 3 DAY) THEN 'primary' ELSE 'secondary' END,
      r.reason_date,
      DATE_ADD(r.reason_date, INTERVAL 10 DAY),
      JSON_OBJECT(
        'display_body', LEFT(CONCAT_WS('；',
          NULLIF(r.role_label, ''),
          NULLIF(r.reason_text, ''),
          IF(COALESCE(r.boom_theme, '') <> '', CONCAT('题材背景：', r.boom_theme), NULL)
        ), 900),
        'display_lines', JSON_ARRAY(
          CONCAT('原因：', LEFT(COALESCE(NULLIF(r.reason_text, ''), r.reason_title), 320)),
          IF(COALESCE(r.role_label, '') <> '', CONCAT('地位：', r.role_label), NULL),
          IF(COALESCE(JSON_LENGTH(r.zscode), 0) > 0, CONCAT('题材代码：', JSON_UNQUOTE(JSON_EXTRACT(r.zscode, '$'))), NULL),
          IF(COALESCE(r.boom_theme, '') <> '', CONCAT('题材背景：', LEFT(r.boom_theme, 260)), NULL)
        ),
        'reason_date', DATE_FORMAT(r.reason_date, '%Y-%m-%d'),
        'reason_title', r.reason_title,
        'role_label', r.role_label,
        'source_position', r.source_position,
        'reason_type', r.reason_type,
        'zscode', COALESCE(r.zscode, JSON_ARRAY()),
        'concept_explain', COALESCE(r.concept_explain, ''),
        'boom_theme', COALESCE(r.boom_theme, ''),
        'raw_json', COALESCE(r.raw_json, JSON_OBJECT()),
        'rule', 'kpl_limit_up_reason_10d'
      )
    FROM kpl_stock_limit_up_reasons r
    WHERE r.reason_date <= CAST({day} AS DATE)
      AND r.reason_date >= DATE_SUB(CAST({day} AS DATE), INTERVAL 10 DAY)
      {code_filter}
    ON DUPLICATE KEY UPDATE
      stock_name=VALUES(stock_name),
      fact_subtype=VALUES(fact_subtype),
      fact_title=VALUES(fact_title),
      fact_body=VALUES(fact_body),
      valid_status=VALUES(valid_status),
      valid_score=VALUES(valid_score),
      valid_reason=VALUES(valid_reason),
      invalid_reason=VALUES(invalid_reason),
      evidence_role=VALUES(evidence_role),
      evidence_group=VALUES(evidence_group),
      display_level=VALUES(display_level),
      valid_until=VALUES(valid_until),
      payload=VALUES(payload),
      updated_at=CURRENT_TIMESTAMP(3);
    """


def fetch_effective_fact_items(config: MySqlConfig, trade_date: str, code: str, limit: int = 12) -> list[dict[str, Any]]:
    ensure_effective_facts_table(config)
    rows = mysql_rows(
        run_mysql(
            config,
            f"""
            SELECT
              source_table,
              source_key,
              fact_type,
              fact_subtype,
              fact_title,
              COALESCE(fact_body, ''),
              COALESCE(DATE_FORMAT(fact_date, '%Y-%m-%d'), ''),
              valid_status,
              valid_score,
              evidence_role,
              evidence_group,
              display_level,
              COALESCE(payload, JSON_OBJECT()),
              DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s')
            FROM stock_effective_facts
            WHERE trade_date={sql_string(trade_date)}
              AND code={sql_string(code)}
              AND display_level IN ('primary','secondary','background')
            ORDER BY
              FIELD(evidence_group, 'current_effective', 'post_close_confirm', 'background_fact', 'historical_tag', 'hidden'),
              FIELD(display_level, 'primary', 'secondary', 'background'),
              FIELD(evidence_role, 'hard_catalyst', 'funds', 'strength', 'theme_confirmation', 'theme'),
              valid_score DESC,
              fact_date DESC
            LIMIT {int(limit)};
            """,
            batch=True,
            raw=True,
        )
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if len(row) < 14:
            continue
        payload: Any = row[12]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        out.append(
            {
                "source_table": row[0],
                "source_key": row[1],
                "fact_type": row[2],
                "fact_subtype": row[3],
                "title": row[4],
                "body": row[5],
                "fact_date": row[6],
                "valid_status": row[7],
                "valid_score": row[8],
                "evidence_role": row[9],
                "evidence_group": row[10],
                "display_level": row[11],
                "payload": payload if isinstance(payload, (dict, list)) else {},
                "updated_at": row[13],
            }
        )
    return out


__all__ = [
    "build_effective_facts",
    "annotate_lhb_effective_facts",
    "clear_effective_facts",
    "enqueue_effective_facts_dirty",
    "ensure_effective_fact_rule_tables",
    "ensure_effective_facts_table",
    "fetch_effective_facts_dirty",
    "fetch_effective_fact_items",
    "mark_effective_facts_dirty",
]
