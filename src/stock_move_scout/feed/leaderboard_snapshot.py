from __future__ import annotations

import hashlib
import json
from typing import Any

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_int, sql_json, sql_string
from stock_move_scout.feed.queries import kpl_leaderboard_sql, leaderboard_sql
from stock_move_scout.research_pool import (
    DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    DEFAULT_RESEARCH_POOL_GAIN_TOP,
    DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    DEFAULT_RESEARCH_POOL_RULE,
    materialize_research_pool_snapshot,
)
from stock_move_scout.web.runtime import parse_json_output
from stock_move_scout.web.runtime import assert_weekday_trade_date


SNAPSHOT_SOURCE = "post_close_confirm"
KPL_SNAPSHOT_SOURCE = "kpl_primary_theme"


def post_close_dependency_status(config: MySqlConfig, trade_date: str) -> dict[str, Any]:
    day = sql_string(trade_date)
    sql = f"""
    SELECT
      (SELECT COUNT(*) FROM limit_up_pool_items
       WHERE trade_date={day}
         AND source='eastmoney_akshare_stock_zt_pool_em'
         AND pool_type='limit_up') AS limit_up_count,
      (SELECT COUNT(*) FROM stock_daily_bars
       WHERE trade_date={day}) AS daily_bar_count,
      (SELECT COUNT(*) FROM market_width_snapshots
       WHERE trade_date={day}
         AND source='stock_daily_bars_close') AS daily_close_snapshot_count
    ;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    row = rows[0] if rows else ["0", "0", "0"]
    limit_up_count = int(float(row[0] or 0))
    daily_bar_count = int(float(row[1] or 0))
    daily_close_snapshot_count = int(float(row[2] or 0))
    ok = limit_up_count > 0 and daily_bar_count >= 1000 and daily_close_snapshot_count > 0
    missing: list[str] = []
    if limit_up_count <= 0:
        missing.append("limit_up_pool_items")
    if daily_bar_count < 1000:
        missing.append("stock_daily_bars")
    if daily_close_snapshot_count <= 0:
        missing.append("market_width_daily_close")
    return {
        "ok": ok,
        "trade_date": trade_date,
        "limit_up_count": limit_up_count,
        "daily_bar_count": daily_bar_count,
        "daily_close_snapshot_count": daily_close_snapshot_count,
        "missing": missing,
    }


def assert_post_close_dependencies(config: MySqlConfig, trade_date: str) -> dict[str, Any]:
    status = post_close_dependency_status(config, trade_date)
    if not status["ok"]:
        raise RuntimeError(
            "post_close_leaderboard_snapshot dependencies are not ready: "
            + json.dumps(status, ensure_ascii=False, separators=(",", ":"))
        )
    return status


def ensure_leaderboard_snapshot_table(config: MySqlConfig) -> None:
    run_mysql(
        config,
        """
        CREATE TABLE IF NOT EXISTS leaderboard_snapshots (
          trade_date DATE NOT NULL,
          rule VARCHAR(64) NOT NULL DEFAULT 'recent_limit_up_or_5d_gain_top',
          limit_up_days INT NOT NULL DEFAULT 5,
          gain_period_days INT NOT NULL DEFAULT 5,
          gain_top INT NOT NULL DEFAULT 30,
          source VARCHAR(64) NOT NULL DEFAULT 'post_close_confirm',
          leader_count INT NOT NULL DEFAULT 0,
          scope_count INT NOT NULL DEFAULT 0,
          source_hash CHAR(64) NOT NULL DEFAULT '',
          payload_json JSON NOT NULL,
          generated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
          updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
          PRIMARY KEY (trade_date, rule, limit_up_days, gain_period_days, gain_top, source),
          KEY idx_leaderboard_snapshots_generated (generated_at),
          KEY idx_leaderboard_snapshots_source (source, trade_date)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
          COMMENT='Post-close confirmed leaderboard payload snapshots.';
        """,
    )


def _payload_counts(payload: dict[str, Any]) -> tuple[int, int]:
    scopes = payload.get("scopes")
    if not isinstance(scopes, list):
        return 0, 0
    leader_count = 0
    for scope in scopes:
        if isinstance(scope, dict) and isinstance(scope.get("leaders"), list):
            leader_count += len(scope["leaders"])
    return len(scopes), leader_count


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def upsert_leaderboard_snapshot_payload(
    config: MySqlConfig,
    trade_date: str,
    payload: dict[str, Any],
    *,
    source: str,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
) -> dict[str, Any]:
    ensure_leaderboard_snapshot_table(config)
    payload = dict(payload or {})
    payload["trade_date"] = payload.get("trade_date") or trade_date
    scope_count, leader_count = _payload_counts(payload)
    source_hash = _payload_hash(payload)
    sql = f"""
    INSERT INTO leaderboard_snapshots(
      trade_date, rule, limit_up_days, gain_period_days, gain_top, source,
      leader_count, scope_count, source_hash, payload_json, generated_at
    ) VALUES (
      {sql_string(trade_date)}, {sql_string(DEFAULT_RESEARCH_POOL_RULE)},
      {sql_int(limit_up_days)}, {sql_int(gain_period_days)}, {sql_int(gain_top)}, {sql_string(source)},
      {sql_int(leader_count)}, {sql_int(scope_count)}, {sql_string(source_hash)}, {sql_json(payload)}, CURRENT_TIMESTAMP(3)
    )
    ON DUPLICATE KEY UPDATE
      leader_count=VALUES(leader_count),
      scope_count=VALUES(scope_count),
      source_hash=VALUES(source_hash),
      payload_json=VALUES(payload_json),
      generated_at=VALUES(generated_at),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    return {
        "trade_date": trade_date,
        "source": source,
        "source_hash": source_hash,
        "scope_count": scope_count,
        "leader_count": leader_count,
    }


def materialize_leaderboard_snapshot(
    config: MySqlConfig,
    trade_date: str,
    *,
    limit_up_days: int = DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS,
    gain_period_days: int = DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS,
    gain_top: int = DEFAULT_RESEARCH_POOL_GAIN_TOP,
    force: bool = False,
    rebuild_research_pool: bool = True,
    check_dependencies: bool = True,
) -> dict[str, Any]:
    assert_weekday_trade_date(trade_date)
    ensure_leaderboard_snapshot_table(config)
    dependencies = assert_post_close_dependencies(config, trade_date) if check_dependencies else post_close_dependency_status(config, trade_date)
    if rebuild_research_pool:
        pool_result = materialize_research_pool_snapshot(
            config,
            trade_date,
            limit_up_days=limit_up_days,
            gain_period_days=gain_period_days,
            gain_top=gain_top,
            force=force,
        )
    else:
        pool_result = {}

    output = run_mysql(config, leaderboard_sql(trade_date), batch=True, raw=True)
    payload = parse_json_output(output)
    if not isinstance(payload, dict):
        payload = {}
    payload["trade_date"] = payload.get("trade_date") or trade_date
    payload["leader_data_trade_date"] = trade_date
    payload["leader_data_source"] = SNAPSHOT_SOURCE
    payload["leader_data_label"] = f"{trade_date} 收盘确认"
    payload["leader_snapshot_generated_at"] = ""

    scope_count, leader_count = _payload_counts(payload)
    source_hash = _payload_hash(payload)
    key_sql = " AND ".join(
        [
            f"trade_date={sql_string(trade_date)}",
            f"rule={sql_string(DEFAULT_RESEARCH_POOL_RULE)}",
            f"limit_up_days={sql_int(limit_up_days)}",
            f"gain_period_days={sql_int(gain_period_days)}",
            f"gain_top={sql_int(gain_top)}",
            f"source={sql_string(SNAPSHOT_SOURCE)}",
        ]
    )
    if not force:
        existing = mysql_rows(
            run_mysql(
                config,
                f"SELECT source_hash, leader_count, scope_count FROM leaderboard_snapshots WHERE {key_sql};",
                batch=True,
                raw=True,
            )
        )
        if existing and str(existing[0][0] or "") == source_hash:
            return {
                "trade_date": trade_date,
                "generated": False,
                "unchanged": True,
                "source_hash": source_hash,
                "scope_count": int(float(existing[0][2] or 0)),
                "leader_count": int(float(existing[0][1] or 0)),
                "research_pool": pool_result,
                "dependencies": dependencies,
            }

    sql = f"""
    INSERT INTO leaderboard_snapshots(
      trade_date, rule, limit_up_days, gain_period_days, gain_top, source,
      leader_count, scope_count, source_hash, payload_json, generated_at
    ) VALUES (
      {sql_string(trade_date)}, {sql_string(DEFAULT_RESEARCH_POOL_RULE)},
      {sql_int(limit_up_days)}, {sql_int(gain_period_days)}, {sql_int(gain_top)}, {sql_string(SNAPSHOT_SOURCE)},
      {sql_int(leader_count)}, {sql_int(scope_count)}, {sql_string(source_hash)}, {sql_json(payload)}, CURRENT_TIMESTAMP(3)
    )
    ON DUPLICATE KEY UPDATE
      leader_count=VALUES(leader_count),
      scope_count=VALUES(scope_count),
      source_hash=VALUES(source_hash),
      payload_json=VALUES(payload_json),
      generated_at=VALUES(generated_at),
      updated_at=CURRENT_TIMESTAMP(3);
    """
    run_mysql(config, sql)
    return {
        "trade_date": trade_date,
        "generated": True,
        "unchanged": False,
        "source_hash": source_hash,
        "scope_count": scope_count,
        "leader_count": leader_count,
        "research_pool": pool_result,
        "dependencies": dependencies,
    }


def latest_leaderboard_snapshot_payload_by_source(
    config: MySqlConfig,
    service_trade_date: str,
    *,
    source: str = SNAPSHOT_SOURCE,
    exact: bool = False,
) -> dict[str, Any] | None:
    ensure_leaderboard_snapshot_table(config)
    day_predicate = (
        f"trade_date = {sql_string(service_trade_date)}"
        if exact
        else f"trade_date <= {sql_string(service_trade_date)}"
    )
    rule = sql_string(DEFAULT_RESEARCH_POOL_RULE)
    limit_up_days = sql_int(DEFAULT_RESEARCH_POOL_LIMIT_UP_DAYS)
    gain_period_days = sql_int(DEFAULT_RESEARCH_POOL_GAIN_PERIOD_DAYS)
    gain_top = sql_int(DEFAULT_RESEARCH_POOL_GAIN_TOP)
    source_sql = sql_string(source)
    sql = f"""
    SELECT JSON_OBJECT(
      'trade_date', DATE_FORMAT(s.trade_date, '%Y-%m-%d'),
      'generated_at', DATE_FORMAT(s.generated_at, '%Y-%m-%d %H:%i:%s'),
      'payload', s.payload_json
    )
    FROM leaderboard_snapshots s
    JOIN (
      SELECT trade_date, rule, limit_up_days, gain_period_days, gain_top, source
      FROM leaderboard_snapshots
      WHERE {day_predicate}
        AND rule={rule}
        AND limit_up_days={limit_up_days}
        AND gain_period_days={gain_period_days}
        AND gain_top={gain_top}
        AND source={source_sql}
      ORDER BY trade_date DESC
      LIMIT 1
    ) latest
      ON latest.trade_date=s.trade_date
     AND latest.rule=s.rule
     AND latest.limit_up_days=s.limit_up_days
     AND latest.gain_period_days=s.gain_period_days
     AND latest.gain_top=s.gain_top
     AND latest.source=s.source;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    if not rows or not rows[0]:
        return None
    parsed = parse_json_output(rows[0][0])
    if not isinstance(parsed, dict):
        return None
    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        return None
    leader_day = str(parsed.get("trade_date") or payload.get("trade_date") or "")
    generated_at = str(parsed.get("generated_at") or "")
    payload = dict(payload)
    payload["leader_data_trade_date"] = leader_day
    payload["leader_data_source"] = source
    payload["leader_data_label"] = f"{leader_day} 收盘确认" if leader_day else "收盘确认"
    payload["leader_snapshot_generated_at"] = generated_at
    return payload


def latest_leaderboard_snapshot_payload(config: MySqlConfig, service_trade_date: str) -> dict[str, Any] | None:
    return latest_leaderboard_snapshot_payload_by_source(config, service_trade_date, source=SNAPSHOT_SOURCE)


def materialize_kpl_leaderboard_snapshot(config: MySqlConfig, trade_date: str) -> dict[str, Any]:
    output = run_mysql(config, kpl_leaderboard_sql(trade_date), batch=True, raw=True)
    payload = parse_json_output(output)
    if not isinstance(payload, dict):
        payload = {}
    payload["trade_date"] = payload.get("trade_date") or trade_date
    payload["leader_data_trade_date"] = trade_date
    payload["leader_data_source"] = KPL_SNAPSHOT_SOURCE
    payload["leader_data_label"] = f"{trade_date} 收盘确认"
    payload["leader_snapshot_generated_at"] = ""
    result = upsert_leaderboard_snapshot_payload(config, trade_date, payload, source=KPL_SNAPSHOT_SOURCE)
    result["generated"] = True
    return result
