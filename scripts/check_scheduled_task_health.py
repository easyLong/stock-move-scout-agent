#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import MySqlConfig, add_mysql_args, mysql_config_from_args, mysql_rows, run_mysql
from stock_move_scout.db import sql_json, sql_string
from stock_move_scout.scheduler.task_definitions import NEXT_RUN_SQL_BY_TASK


BAD_RUN_STATUSES = {"failed", "timeout", "dead"}


def parse_dt(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.upper() == "NULL":
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def format_dt(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""


def fixed_daily_time(task_id: str) -> time | None:
    expression = NEXT_RUN_SQL_BY_TASK.get(task_id, "")
    if "DATE_ADD(CURDATE()" not in expression:
        return None
    match = re.search(r"TIME\(NOW\(\)\)\s*<\s*'(\d{2}:\d{2}:\d{2})'", expression)
    if not match:
        return None
    return time.fromisoformat(match.group(1))


def expected_fixed_at(now: datetime, clock_time: time, grace: timedelta) -> datetime:
    today_at = datetime.combine(now.date(), clock_time)
    if now >= today_at + grace:
        return today_at
    return today_at - timedelta(days=1)


def ensure_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS scheduled_task_health_checks (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      check_date DATE NOT NULL,
      checked_at DATETIME(3) NOT NULL,
      task_id VARCHAR(64) NOT NULL,
      task_name VARCHAR(255) NOT NULL DEFAULT '',
      task_kind VARCHAR(64) NOT NULL DEFAULT '',
      task_type VARCHAR(32) NOT NULL DEFAULT '',
      enabled TINYINT NOT NULL DEFAULT 1,
      expected_at DATETIME(3) NULL,
      next_run_after DATETIME(3) NULL,
      last_enqueued_at DATETIME(3) NULL,
      last_started_at DATETIME(3) NULL,
      last_finished_at DATETIME(3) NULL,
      last_success_at DATETIME(3) NULL,
      last_status VARCHAR(32) NOT NULL DEFAULT '',
      queue_pending_count INT NOT NULL DEFAULT 0,
      queue_running_count INT NOT NULL DEFAULT 0,
      queue_dead_count INT NOT NULL DEFAULT 0,
      queue_done_count INT NOT NULL DEFAULT 0,
      health ENUM('ok','not_due','missed','failed','overdue','pending','disabled') NOT NULL DEFAULT 'ok',
      severity ENUM('info','warning','critical') NOT NULL DEFAULT 'info',
      issue_code VARCHAR(64) NOT NULL DEFAULT '',
      message VARCHAR(1024) NOT NULL DEFAULT '',
      detail_json JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uniq_task_health_check_date_task (check_date, task_id),
      KEY idx_task_health_checks_health (check_date, health, severity),
      KEY idx_task_health_checks_task (task_id, checked_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    run_mysql(config, sql)


def load_tasks(config: MySqlConfig) -> list[dict[str, Any]]:
    sql = """
    SELECT task_id, task_name, task_kind, task_type, enabled, schedule_type,
           update_interval_seconds, priority,
           DATE_FORMAT(next_run_after, '%Y-%m-%d %H:%i:%s.%f'),
           DATE_FORMAT(last_enqueued_at, '%Y-%m-%d %H:%i:%s.%f')
    FROM scheduled_tasks
    ORDER BY enabled DESC, priority ASC, task_id ASC;
    """
    keys = [
        "task_id",
        "task_name",
        "task_kind",
        "task_type",
        "enabled",
        "schedule_type",
        "update_interval_seconds",
        "priority",
        "next_run_after",
        "last_enqueued_at",
    ]
    return [dict(zip(keys, row)) for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True))]


def load_latest_runs(config: MySqlConfig) -> dict[str, dict[str, Any]]:
    sql = """
    SELECT tr.task_id,
           DATE_FORMAT(tr.started_at, '%Y-%m-%d %H:%i:%s.%f'),
           DATE_FORMAT(tr.finished_at, '%Y-%m-%d %H:%i:%s.%f'),
           tr.status,
           COALESCE(tr.return_code, ''),
           REPLACE(REPLACE(LEFT(COALESCE(tr.error_text, ''), 500), CHAR(13), ' '), CHAR(10), ' ')
    FROM task_runs tr
    JOIN (
      SELECT task_id, MAX(run_id) AS run_id
      FROM task_runs
      GROUP BY task_id
    ) latest ON latest.run_id = tr.run_id;
    """
    out: dict[str, dict[str, Any]] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 5:
            continue
        out[row[0]] = {
            "started_at": parse_dt(row[1]),
            "finished_at": parse_dt(row[2]),
            "status": row[3],
            "return_code": row[4],
            "error_text": row[5] if len(row) > 5 else "",
        }
    return out


def load_latest_success(config: MySqlConfig) -> dict[str, datetime]:
    sql = """
    SELECT task_id, DATE_FORMAT(MAX(started_at), '%Y-%m-%d %H:%i:%s.%f')
    FROM task_runs
    WHERE status='ok'
    GROUP BY task_id;
    """
    out: dict[str, datetime] = {}
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        value = parse_dt(row[1])
        if value:
            out[row[0]] = value
    return out


def load_recent_queue(config: MySqlConfig, lookback_days: int) -> list[dict[str, Any]]:
    sql = f"""
    SELECT task_id, status, DATE_FORMAT(created_at, '%Y-%m-%d %H:%i:%s.%f'),
           DATE_FORMAT(locked_until, '%Y-%m-%d %H:%i:%s.%f'),
           REPLACE(REPLACE(LEFT(COALESCE(last_error, ''), 500), CHAR(13), ' '), CHAR(10), ' ')
    FROM task_queue
    WHERE created_at >= DATE_SUB(NOW(3), INTERVAL {int(lookback_days)} DAY);
    """
    rows: list[dict[str, Any]] = []
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 3:
            continue
        rows.append(
            {
                "task_id": row[0],
                "status": row[1],
                "created_at": parse_dt(row[2]),
                "locked_until": parse_dt(row[3] if len(row) > 3 else ""),
                "last_error": row[4] if len(row) > 4 else "",
            }
        )
    return rows


def queue_counts(queue_rows: list[dict[str, Any]], task_id: str, since: datetime | None) -> dict[str, int]:
    counts = {"pending": 0, "running": 0, "dead": 0, "done": 0}
    for row in queue_rows:
        if row["task_id"] != task_id:
            continue
        created_at = row.get("created_at")
        if since and created_at and created_at < since:
            continue
        status = str(row.get("status") or "")
        if status in counts:
            counts[status] += 1
    return counts


def judge_task(
    task: dict[str, Any],
    *,
    now: datetime,
    grace: timedelta,
    latest_run: dict[str, Any] | None,
    latest_success_at: datetime | None,
    queue: dict[str, int],
) -> dict[str, str]:
    if str(task.get("enabled")) != "1":
        return {"health": "disabled", "severity": "info", "issue_code": "", "message": "任务未启用"}

    task_id = str(task["task_id"])
    expected_at = task.get("expected_at")
    last_status = str((latest_run or {}).get("status") or "")
    last_started_at = (latest_run or {}).get("started_at")

    if queue["dead"] > 0:
        return {
            "health": "failed",
            "severity": "critical",
            "issue_code": "queue_dead",
            "message": f"任务队列存在 dead 记录：{queue['dead']} 条",
        }

    if expected_at:
        cycle_start = expected_at - grace
        bad_in_cycle = last_status in BAD_RUN_STATUSES and last_started_at and last_started_at >= cycle_start
        success_in_cycle = latest_success_at is not None and latest_success_at >= cycle_start

        if bad_in_cycle:
            return {
                "health": "failed",
                "severity": "critical",
                "issue_code": "latest_run_failed",
                "message": f"最近一次执行失败：{last_status}",
            }
        if queue["running"] > 0 or queue["pending"] > 0:
            return {
                "health": "pending",
                "severity": "warning",
                "issue_code": "queue_not_finished",
                "message": f"任务已入队但未完成：pending={queue['pending']} running={queue['running']}",
            }
        if success_in_cycle:
            return {"health": "ok", "severity": "info", "issue_code": "", "message": ""}
        if queue["done"] > 0:
            return {
                "health": "failed",
                "severity": "critical",
                "issue_code": "done_without_success",
                "message": "任务队列已完成，但没有找到本周期成功运行记录",
            }
        return {
            "health": "missed",
            "severity": "critical",
            "issue_code": "not_run_after_expected_at",
            "message": f"应在 {format_dt(expected_at)} 后运行，但没有成功记录",
        }

    next_run_after = parse_dt(str(task.get("next_run_after") or ""))
    if last_status in BAD_RUN_STATUSES:
        return {
            "health": "failed",
            "severity": "critical",
            "issue_code": "latest_run_failed",
            "message": f"最近一次执行失败：{last_status}",
        }
    if next_run_after and next_run_after <= now - grace:
        if queue["pending"] > 0 or queue["running"] > 0:
            return {
                "health": "pending",
                "severity": "warning",
                "issue_code": "due_but_queue_not_finished",
                "message": f"已到执行时间但队列未完成：pending={queue['pending']} running={queue['running']}",
            }
        return {
            "health": "overdue",
            "severity": "critical",
            "issue_code": "next_run_after_overdue",
            "message": f"next_run_after 已过期：{format_dt(next_run_after)}",
        }

    interval = int(task.get("update_interval_seconds") or 0)
    if interval > 0 and latest_success_at and next_run_after is None:
        stale_after = latest_success_at + timedelta(seconds=interval) + grace
        if stale_after <= now:
            return {
                "health": "overdue",
                "severity": "warning",
                "issue_code": "success_stale",
                "message": f"最近成功时间过旧：{format_dt(latest_success_at)}",
            }

    if last_started_at is None:
        return {"health": "not_due", "severity": "info", "issue_code": "", "message": "尚未到执行窗口或尚未运行"}
    return {"health": "ok", "severity": "info", "issue_code": "", "message": ""}


def save_result(config: MySqlConfig, row: dict[str, Any]) -> None:
    expected_sql = sql_string(format_dt(row["expected_at"])) if row["expected_at"] else "NULL"
    next_sql = sql_string(format_dt(row["next_run_after"])) if row["next_run_after"] else "NULL"
    enqueued_sql = sql_string(format_dt(row["last_enqueued_at"])) if row["last_enqueued_at"] else "NULL"
    started_sql = sql_string(format_dt(row["last_started_at"])) if row["last_started_at"] else "NULL"
    finished_sql = sql_string(format_dt(row["last_finished_at"])) if row["last_finished_at"] else "NULL"
    success_sql = sql_string(format_dt(row["last_success_at"])) if row["last_success_at"] else "NULL"
    sql = f"""
    INSERT INTO scheduled_task_health_checks(
      check_date, checked_at, task_id, task_name, task_kind, task_type, enabled,
      expected_at, next_run_after, last_enqueued_at, last_started_at, last_finished_at,
      last_success_at, last_status, queue_pending_count, queue_running_count,
      queue_dead_count, queue_done_count, health, severity, issue_code, message, detail_json
    )
    VALUES(
      {sql_string(row['check_date'])}, {sql_string(format_dt(row['checked_at']))},
      {sql_string(row['task_id'])}, {sql_string(row['task_name'])},
      {sql_string(row['task_kind'])}, {sql_string(row['task_type'])}, {int(row['enabled'])},
      {expected_sql}, {next_sql}, {enqueued_sql}, {started_sql}, {finished_sql}, {success_sql},
      {sql_string(row['last_status'])}, {int(row['queue_pending_count'])},
      {int(row['queue_running_count'])}, {int(row['queue_dead_count'])}, {int(row['queue_done_count'])},
      {sql_string(row['health'])}, {sql_string(row['severity'])},
      {sql_string(row['issue_code'])}, {sql_string(row['message'])}, {sql_json(row['detail'])}
    )
    ON DUPLICATE KEY UPDATE
      checked_at=VALUES(checked_at),
      task_name=VALUES(task_name),
      task_kind=VALUES(task_kind),
      task_type=VALUES(task_type),
      enabled=VALUES(enabled),
      expected_at=VALUES(expected_at),
      next_run_after=VALUES(next_run_after),
      last_enqueued_at=VALUES(last_enqueued_at),
      last_started_at=VALUES(last_started_at),
      last_finished_at=VALUES(last_finished_at),
      last_success_at=VALUES(last_success_at),
      last_status=VALUES(last_status),
      queue_pending_count=VALUES(queue_pending_count),
      queue_running_count=VALUES(queue_running_count),
      queue_dead_count=VALUES(queue_dead_count),
      queue_done_count=VALUES(queue_done_count),
      health=VALUES(health),
      severity=VALUES(severity),
      issue_code=VALUES(issue_code),
      message=VALUES(message),
      detail_json=VALUES(detail_json);
    """
    run_mysql(config, sql)


def run_check(config: MySqlConfig, *, now: datetime, grace_minutes: int, lookback_days: int) -> dict[str, Any]:
    ensure_table(config)
    grace = timedelta(minutes=grace_minutes)
    tasks = load_tasks(config)
    latest_runs = load_latest_runs(config)
    latest_success = load_latest_success(config)
    queue_rows = load_recent_queue(config, lookback_days)

    results: list[dict[str, Any]] = []
    for task in tasks:
        if str(task.get("schedule_type") or "") != "interval":
            continue
        task_id = str(task["task_id"])
        if task_id == "scheduled_task_health_check":
            continue

        clock_time = fixed_daily_time(task_id)
        expected_at = expected_fixed_at(now, clock_time, grace) if clock_time else None
        queue_since = expected_at - grace if expected_at else now - timedelta(days=lookback_days)
        counts = queue_counts(queue_rows, task_id, queue_since)
        latest_run = latest_runs.get(task_id)
        judgement = judge_task(
            task,
            now=now,
            grace=grace,
            latest_run=latest_run,
            latest_success_at=latest_success.get(task_id),
            queue=counts,
        )
        next_run_after = parse_dt(str(task.get("next_run_after") or ""))
        last_enqueued_at = parse_dt(str(task.get("last_enqueued_at") or ""))
        row = {
            "check_date": now.date().isoformat(),
            "checked_at": now,
            "task_id": task_id,
            "task_name": str(task.get("task_name") or ""),
            "task_kind": str(task.get("task_kind") or ""),
            "task_type": str(task.get("task_type") or ""),
            "enabled": int(task.get("enabled") or 0),
            "expected_at": expected_at,
            "next_run_after": next_run_after,
            "last_enqueued_at": last_enqueued_at,
            "last_started_at": (latest_run or {}).get("started_at"),
            "last_finished_at": (latest_run or {}).get("finished_at"),
            "last_success_at": latest_success.get(task_id),
            "last_status": str((latest_run or {}).get("status") or ""),
            "queue_pending_count": counts["pending"],
            "queue_running_count": counts["running"],
            "queue_dead_count": counts["dead"],
            "queue_done_count": counts["done"],
            **judgement,
            "detail": {
                "fixed_daily_time": clock_time.isoformat() if clock_time else "",
                "grace_minutes": grace_minutes,
                "lookback_days": lookback_days,
                "last_error": str((latest_run or {}).get("error_text") or ""),
            },
        }
        save_result(config, row)
        results.append(row)

    alerts = [row for row in results if row["health"] not in {"ok", "not_due", "disabled"}]
    summary = {
        "checked_at": format_dt(now),
        "check_date": now.date().isoformat(),
        "total": len(results),
        "alert_count": len(alerts),
        "critical_count": sum(1 for row in alerts if row["severity"] == "critical"),
        "warning_count": sum(1 for row in alerts if row["severity"] == "warning"),
        "alerts": [
            {
                "task_id": row["task_id"],
                "health": row["health"],
                "severity": row["severity"],
                "issue_code": row["issue_code"],
                "message": row["message"],
                "expected_at": format_dt(row["expected_at"]),
                "last_status": row["last_status"],
                "last_success_at": format_dt(row["last_success_at"]),
            }
            for row in alerts
        ],
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Check scheduled task misses and failures.")
    add_mysql_args(parser)
    parser.add_argument("--grace-minutes", type=int, default=15)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument("--as-of", default="", help="Override check time, format: YYYY-mm-dd HH:MM:SS.")
    args = parser.parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    config: MySqlConfig = mysql_config_from_args(args)
    now = datetime.strptime(args.as_of, "%Y-%m-%d %H:%M:%S") if args.as_of else datetime.now()
    summary = run_check(config, now=now, grace_minutes=args.grace_minutes, lookback_days=args.lookback_days)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
