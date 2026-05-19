from __future__ import annotations

import json
from typing import Any

from stock_move_scout.research_pool import research_pool_cte
from stock_scout_mysql import MySqlConfig, mysql_rows, run_mysql, sql_string


def fetch_candidates(
    config: MySqlConfig,
    trade_date: str,
    code: str = "",
    limit: int = 50,
    research_pool_only: bool = False,
    research_pool_source_kind: str = "",
) -> list[dict[str, str]]:
    code_filter = f"AND ef.code={sql_string(code)}" if code else ""
    pool_cte = f"{research_pool_cte(trade_date)}," if research_pool_only else ""
    pool_join = "JOIN research_pool rp ON rp.code=ef.code" if research_pool_only else ""
    pool_source_filter = (
        f"AND rp.source_kind={sql_string(research_pool_source_kind)}"
        if research_pool_only and research_pool_source_kind
        else ""
    )
    sql = f"""
    WITH {pool_cte}
    candidates AS (
      SELECT
        ef.code,
        SUBSTRING_INDEX(
          GROUP_CONCAT(COALESCE(NULLIF(ef.stock_name, ''), ef.code) ORDER BY ef.fact_date DESC, ef.valid_score DESC),
          ',',
          1
        ) AS stock_name,
        MAX(ef.fact_date) AS latest_fact_date,
        MAX(ef.valid_score) AS max_score,
        COUNT(*) AS fact_count
      FROM stock_effective_facts ef
      {pool_join}
      WHERE ef.trade_date={sql_string(trade_date)}
        AND ef.evidence_group='current_effective'
        AND ef.valid_status='active'
        AND ef.display_level IN ('primary','secondary')
        {code_filter}
        {pool_source_filter}
      GROUP BY ef.code
    )
    SELECT code, stock_name
    FROM candidates
    WHERE stock_name NOT LIKE '%ST%'
      AND stock_name NOT LIKE '%退市%'
    ORDER BY latest_fact_date DESC, max_score DESC, fact_count DESC, code ASC
    LIMIT {int(limit)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    return [{"code": row[0], "stock_name": row[1]} for row in rows if len(row) >= 2]


def fetch_payload(config: MySqlConfig, trade_date: str, code: str, stock_name: str, per_kind_limit: int) -> dict[str, Any]:
    return fetch_payloads(
        config,
        trade_date,
        [{"code": code, "stock_name": stock_name}],
        per_kind_limit,
    ).get(
        code,
        {
            "trade_date": trade_date,
            "code": code,
            "stock_name": stock_name,
            "current_facts": [],
        },
    )


def fetch_payloads(
    config: MySqlConfig,
    trade_date: str,
    candidates: list[dict[str, str]],
    per_kind_limit: int,
) -> dict[str, dict[str, Any]]:
    code_to_name: dict[str, str] = {}
    for item in candidates:
        code = str(item.get("code") or "").strip()
        if code and code not in code_to_name:
            code_to_name[code] = str(item.get("stock_name") or item.get("name") or code).strip() or code
    payloads: dict[str, dict[str, Any]] = {
        code: {
            "trade_date": trade_date,
            "code": code,
            "stock_name": stock_name,
            "current_facts": [],
        }
        for code, stock_name in code_to_name.items()
    }
    if not code_to_name:
        return payloads
    limit = max(12, int(per_kind_limit) * 4)
    codes_sql = ",".join(sql_string(code) for code in sorted(code_to_name))
    sql = f"""
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
      DATE_FORMAT(updated_at, '%Y-%m-%d %H:%i:%s'),
      code,
      COALESCE(NULLIF(stock_name, ''), code)
    FROM (
      SELECT
        ef.*,
        ROW_NUMBER() OVER (
          PARTITION BY ef.code
          ORDER BY
            FIELD(ef.display_level, 'primary', 'secondary', 'background'),
            ef.valid_score DESC,
            ef.fact_date DESC,
            ef.id DESC
        ) AS rn
      FROM stock_effective_facts ef
      WHERE ef.trade_date={sql_string(trade_date)}
        AND ef.code IN ({codes_sql})
        AND ef.evidence_group='current_effective'
        AND ef.valid_status='active'
        AND ef.display_level IN ('primary','secondary')
    ) x
    WHERE rn <= {int(limit)}
    ORDER BY code, FIELD(display_level, 'primary', 'secondary'), valid_score DESC, fact_date DESC;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    for row in rows:
        if len(row) < 16:
            continue
        code = str(row[14] or "").strip()
        if code not in payloads:
            continue
        payload = _parse_payload(row[12])
        payloads[code]["stock_name"] = str(row[15] or payloads[code]["stock_name"])
        payloads[code]["current_facts"].append(
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
    return payloads


def _parse_payload(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value
