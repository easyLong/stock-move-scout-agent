#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.scheduler import (
    ARCHIVED_TASK_PREFIXES,
    DEPRECATED_TASK_IDS,
    PREOPEN_TIME_TASK_IDS,
    SCHEDULED_TASKS,
    TRADING_TIME_TASK_IDS,
    build_task_command,
    next_run_sql_for_task,
)
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.sources import is_batched_source_task

from stock_scout_mysql import (
    MySqlConfig,
    add_mysql_args,
    mysql_config_from_args,
    mysql_rows,
    run_mysql,
    sql_json,
    sql_string,
)


TASK_TYPES = {"hot", "warm", "cold", "render", "maintenance"}


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def is_transient_mysql_error(exc: Exception) -> bool:
    text = str(exc)
    return "ERROR 1213" in text or "Deadlock found" in text or "ERROR 1205" in text or "Lock wait timeout" in text


def mysql_retry(operation: Any, *, attempts: int = 3, base_sleep: float = 0.25) -> Any:
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return operation()
        except Exception as exc:
            if not is_transient_mysql_error(exc) or attempt >= attempts - 1:
                raise
            last_exc = exc
            time.sleep(base_sleep * (attempt + 1))
    if last_exc:
        raise last_exc
    return None


def parse_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    if value.startswith("{") and value.endswith("}") and ":" in value:
        result: dict[str, Any] = {}
        for part in value[1:-1].split(","):
            if ":" not in part:
                continue
            key, raw = part.split(":", 1)
            key = key.strip().strip("'\"")
            raw = raw.strip().strip("'\"")
            if raw.lower() == "true":
                result[key] = True
            elif raw.lower() == "false":
                result[key] = False
            else:
                try:
                    result[key] = int(raw)
                except ValueError:
                    result[key] = raw
        return result
    return {}


def task_type_list(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    bad = [item for item in items if item not in TASK_TYPES]
    if bad:
        raise ValueError(f"unsupported task_type: {', '.join(bad)}")
    return items or ["maintenance"]


def is_trade_weekday(value: datetime | None = None) -> bool:
    return (value or datetime.now()).weekday() < 5


def in_regular_trading_time(value: datetime | None = None) -> bool:
    now = value or datetime.now()
    if not is_trade_weekday(now):
        return False
    current = now.time()
    return clock_time(9, 30) <= current < clock_time(11, 30) or clock_time(13, 0) <= current < clock_time(15, 0)


def in_preopen_auction_time(value: datetime | None = None) -> bool:
    now = value or datetime.now()
    if not is_trade_weekday(now):
        return False
    current = now.time()
    return clock_time(9, 15) <= current <= clock_time(9, 25, 59)


def in_headline_theme_checkpoint(value: datetime | None = None) -> bool:
    now = value or datetime.now()
    if not is_trade_weekday(now):
        return False
    current = now.time()
    return (
        clock_time(9, 10) <= current < clock_time(9, 20)
        or clock_time(11, 45) <= current < clock_time(11, 55)
    )


def should_enqueue_now(task: dict[str, str], value: datetime | None = None) -> tuple[bool, str]:
    task_id = str(task.get("task_id") or "")
    now = value or datetime.now()
    if task_id in TRADING_TIME_TASK_IDS and not in_regular_trading_time(now):
        return False, "outside_trading_time"
    if task_id in PREOPEN_TIME_TASK_IDS and not in_preopen_auction_time(now):
        return False, "outside_preopen_time"
    if task_id == "ths_homepage_headline_themes" and not in_headline_theme_checkpoint(now):
        return False, "outside_headline_theme_checkpoint"
    return True, ""


def render_dedupe_key(task: dict[str, str]) -> str:
    template = task.get("dedupe_key_template") or ""
    payload = parse_json(task.get("payload_template_json") or "")
    if not template:
        return task["task_id"]
    payload.setdefault("run_key", datetime.now().strftime("%Y%m%d"))
    payload.setdefault("minute_key", datetime.now().strftime("%Y%m%d%H%M"))
    payload.setdefault("hour_key", datetime.now().strftime("%Y%m%d%H"))
    values = {"task_id": task["task_id"], **payload}
    try:
        return template.format(**values)
    except Exception:
        return task["task_id"]


def active_universe_count(config: MySqlConfig) -> int:
    sql = """
    SELECT COUNT(*)
    FROM stocks
    WHERE is_st = 0
      AND name NOT LIKE '%退市%';
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    try:
        return int(rows[0][0]) if rows and rows[0] else 0
    except Exception:
        return 0


def research_pool_count(config: MySqlConfig, trade_date: str) -> int:
    try:
        return ResearchPoolProvider(config).latest_snapshot(trade_date).code_count
    except Exception:
        return 0


def due_tasks(config: MySqlConfig, limit: int) -> list[dict[str, str]]:
    sql = f"""
    SELECT task_id, task_name, task_description, task_kind, task_type, update_interval_seconds,
           priority, timeout_seconds, max_attempts, payload_template_json, dedupe_key_template
    FROM scheduled_tasks
    WHERE enabled = 1
      AND schedule_type = 'interval'
      AND (next_run_after IS NULL OR next_run_after <= NOW(3))
    ORDER BY priority ASC, COALESCE(next_run_after, '1970-01-01') ASC
    LIMIT {int(limit)};
    """
    output = run_mysql(config, sql, batch=True)
    keys = [
        "task_id",
        "task_name",
        "task_description",
        "task_kind",
        "task_type",
        "update_interval_seconds",
        "priority",
        "timeout_seconds",
        "max_attempts",
        "payload_template_json",
        "dedupe_key_template",
    ]
    return [dict(zip(keys, row)) for row in mysql_rows(output)]


def task_by_id(config: MySqlConfig, task_id: str) -> dict[str, str] | None:
    sql = f"""
    SELECT task_id, task_name, task_description, task_kind, task_type, update_interval_seconds,
           priority, timeout_seconds, max_attempts, payload_template_json, dedupe_key_template
    FROM scheduled_tasks
    WHERE task_id = {sql_string(task_id)}
    LIMIT 1;
    """
    keys = [
        "task_id",
        "task_name",
        "task_description",
        "task_kind",
        "task_type",
        "update_interval_seconds",
        "priority",
        "timeout_seconds",
        "max_attempts",
        "payload_template_json",
        "dedupe_key_template",
    ]
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    return dict(zip(keys, rows[0])) if rows else None


def next_run_update_sql(task_id: str) -> str:
    expression = next_run_sql_for_task(task_id)
    if expression and expression != "NULL":
        return expression
    return "DATE_ADD(NOW(3), INTERVAL update_interval_seconds SECOND)"


def update_task_next_run(config: MySqlConfig, task_id: str) -> None:
    sql = f"""
    UPDATE scheduled_tasks
    SET last_enqueued_at = NOW(3),
        next_run_after = {next_run_update_sql(task_id)},
        last_message = CONCAT('scheduler_checked ', DATE_FORMAT(NOW(3), '%Y-%m-%d %H:%i:%s'))
    WHERE task_id = {sql_string(task_id)};
    """
    run_mysql(config, sql)


def update_task_skipped_next_run(config: MySqlConfig, task_id: str, reason: str) -> None:
    sql = f"""
    UPDATE scheduled_tasks
    SET next_run_after = {next_run_update_sql(task_id)},
        last_message = CONCAT('scheduler_skipped:{sql_string(reason)[1:-1]} ', DATE_FORMAT(NOW(3), '%Y-%m-%d %H:%i:%s'))
    WHERE task_id = {sql_string(task_id)};
    """
    run_mysql(config, sql)


def enqueue_single_task(config: MySqlConfig, task: dict[str, str]) -> bool:
    payload = parse_json(task.get("payload_template_json") or "")
    dedupe_key = render_dedupe_key(task)
    task_id = task["task_id"]
    sql = f"""
    INSERT IGNORE INTO task_queue(
      task_id, task_kind, task_type, priority, status, payload_json, dedupe_key,
      not_before, max_attempts, timeout_seconds
    )
    VALUES(
      {sql_string(task_id)}, {sql_string(task["task_kind"])}, {sql_string(task["task_type"])},
      {int(task["priority"] or 100)}, 'pending', {sql_json(payload)}, {sql_string(dedupe_key)},
      NOW(3), {int(task["max_attempts"] or 2)}, {int(task["timeout_seconds"] or 1800)}
    );

    SELECT ROW_COUNT();

    """
    output = run_mysql(config, sql, batch=True)
    rows = mysql_rows(output)
    update_task_next_run(config, task_id)
    try:
        return bool(rows and int(rows[0][0]) > 0)
    except Exception:
        return False


def enqueue_batched_stock_task(config: MySqlConfig, task: dict[str, str]) -> bool:
    payload = parse_json(task.get("payload_template_json") or "")
    batch_size = max(1, int(payload.get("batch_size") or 100))
    trade_date = str(payload.get("trade_date") or datetime.now().date().isoformat())
    pool_only = bool(payload.get("research_pool_only"))
    total = int(payload.get("total") or (research_pool_count(config, trade_date) if pool_only else active_universe_count(config)))
    if total <= 0:
        update_task_next_run(config, task["task_id"])
        return False
    max_batches = int(payload.get("max_batches") or 0)
    start_offset = max(0, int(payload.get("offset") or 0))
    run_key = str(payload.get("run_key") or datetime.now().strftime("%Y%m%d"))
    offsets = list(range(start_offset, total, batch_size))
    if max_batches > 0:
        offsets = offsets[:max_batches]

    statements: list[str] = []
    for batch_index, offset in enumerate(offsets):
        item_payload = dict(payload)
        item_payload.update(
            {
                "offset": offset,
                "batch_size": batch_size,
                "run_key": run_key,
                "batch_index": batch_index,
                "total": total,
            }
        )
        values = {"task_id": task["task_id"], **item_payload}
        template = task.get("dedupe_key_template") or "{task_id}:{run_key}:{offset}"
        try:
            dedupe_key = template.format(**values)
        except Exception:
            dedupe_key = f"{task['task_id']}:{run_key}:{offset}"
        statements.append(
            f"""
            INSERT IGNORE INTO task_queue(
              task_id, task_kind, task_type, priority, status, payload_json, dedupe_key,
              not_before, max_attempts, timeout_seconds
            )
            VALUES(
              {sql_string(task["task_id"])}, {sql_string(task["task_kind"])}, {sql_string(task["task_type"])},
              {int(task["priority"] or 100)}, 'pending', {sql_json(item_payload)}, {sql_string(dedupe_key)},
              NOW(3), {int(task["max_attempts"] or 2)}, {int(task["timeout_seconds"] or 1800)}
            );
            """
        )
    statements.append(
        f"""
        UPDATE scheduled_tasks
        SET last_enqueued_at = NOW(3),
            next_run_after = {next_run_update_sql(str(task["task_id"]))},
            last_message = CONCAT('enqueued_batches=', {len(offsets)}, ' total=', {total}, ' at ', DATE_FORMAT(NOW(3), '%Y-%m-%d %H:%i:%s'))
        WHERE task_id = {sql_string(task["task_id"])};
        """
    )
    run_mysql(config, "\n".join(statements))
    return bool(offsets)


def enqueue_task(config: MySqlConfig, task: dict[str, str]) -> bool:
    if is_batched_source_task(task["task_kind"]):
        return enqueue_batched_stock_task(config, task)
    return enqueue_single_task(config, task)


def release_expired_locks(config: MySqlConfig) -> dict[str, int]:
    sql = """
    UPDATE task_queue
    SET status = 'pending',
        locked_by = '',
        locked_until = NULL,
        claim_token = '',
        last_error = CONCAT(COALESCE(last_error, ''), '\nlock_expired')
    WHERE status = 'running'
      AND locked_until IS NOT NULL
      AND locked_until < NOW(3)
      AND attempt_count < max_attempts;
    SELECT ROW_COUNT();

    UPDATE task_queue
    SET status = 'dead',
        finished_at = NOW(3),
        locked_by = '',
        locked_until = NULL,
        claim_token = '',
        last_error = CONCAT(COALESCE(last_error, ''), '\nlock_expired_max_attempts')
    WHERE status = 'running'
      AND locked_until IS NOT NULL
      AND locked_until < NOW(3)
      AND attempt_count >= max_attempts;
    SELECT ROW_COUNT();
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    values = [int(row[0]) for row in rows if row]
    return {"requeued": values[0] if values else 0, "dead": values[1] if len(values) > 1 else 0}


def scheduler_loop(args: argparse.Namespace, config: MySqlConfig) -> int:
    loops = 0
    while True:
        try:
            mysql_retry(lambda: release_expired_locks(config))
            tasks = mysql_retry(lambda: due_tasks(config, args.scheduler_limit))
            enqueued = 0
            skipped = 0
            for task in tasks:
                allowed, reason = should_enqueue_now(task)
                if not allowed:
                    skipped += 1
                    mysql_retry(lambda task=task, reason=reason: update_task_skipped_next_run(config, task["task_id"], reason))
                    if args.verbose:
                        print(json.dumps({"at": now_text(), "task_id": task["task_id"], "skipped": reason}, ensure_ascii=False))
                    continue
                if mysql_retry(lambda task=task: enqueue_task(config, task)):
                    enqueued += 1
            if tasks or args.verbose:
                print(json.dumps({"at": now_text(), "due": len(tasks), "enqueued": enqueued, "skipped": skipped}, ensure_ascii=False))
        except Exception as exc:
            if not is_transient_mysql_error(exc):
                raise
            print(json.dumps({"at": now_text(), "db_retry_skipped": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False))
        loops += 1
        if args.once or (args.max_loops and loops >= args.max_loops):
            return 0
        time.sleep(args.poll_seconds)


def enqueue_named_task(args: argparse.Namespace, config: MySqlConfig) -> int:
    if not args.task_id:
        raise SystemExit("--task-id is required for --mode enqueue")
    task = task_by_id(config, args.task_id)
    if not task:
        raise SystemExit(f"task_not_found:{args.task_id}")
    if args.payload_json:
        payload = parse_json(task.get("payload_template_json") or "")
        payload.update(parse_json(args.payload_json))
        task["payload_template_json"] = json.dumps(payload, ensure_ascii=False)
    enqueued = enqueue_task(config, task)
    print(json.dumps({"task_id": args.task_id, "enqueued": enqueued}, ensure_ascii=False))
    return 0


def heartbeat(config: MySqlConfig, worker_id: str, worker_type: str, status: str, queue_id: int | None = None) -> None:
    current_queue = "NULL" if queue_id is None else str(int(queue_id))
    sql = f"""
    INSERT INTO worker_heartbeats(worker_id, worker_type, hostname, pid, status, current_queue_id, heartbeat_at, started_at, meta_json)
    VALUES({sql_string(worker_id)}, {sql_string(worker_type)}, {sql_string(socket.gethostname())}, {os.getpid()},
           {sql_string(status)}, {current_queue}, NOW(3), NOW(3), {sql_json({"python": sys.version.split()[0]})})
    ON DUPLICATE KEY UPDATE
      status = VALUES(status),
      current_queue_id = VALUES(current_queue_id),
      heartbeat_at = VALUES(heartbeat_at),
      meta_json = VALUES(meta_json);
    """
    run_mysql(config, sql)


def claim_task(config: MySqlConfig, worker_id: str, worker_types: list[str]) -> dict[str, Any] | None:
    claim_token = uuid.uuid4().hex
    type_sql = ",".join(sql_string(item) for item in worker_types)
    sql = f"""
    UPDATE task_queue
    SET status = 'running',
        locked_by = {sql_string(worker_id)},
        locked_until = DATE_ADD(NOW(3), INTERVAL timeout_seconds SECOND),
        claim_token = {sql_string(claim_token)},
        attempt_count = attempt_count + 1,
        started_at = NOW(3),
        last_error = NULL
    WHERE status = 'pending'
      AND task_type IN ({type_sql})
      AND not_before <= NOW(3)
    ORDER BY priority ASC, created_at ASC
    LIMIT 1;

    SELECT queue_id, task_id, task_kind, task_type, priority, payload_json, attempt_count,
           max_attempts, timeout_seconds
    FROM task_queue
    WHERE claim_token = {sql_string(claim_token)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    if not rows:
        return None
    row = rows[-1]
    if len(row) < 9:
        return None
    return {
        "queue_id": int(row[0]),
        "task_id": row[1],
        "task_kind": row[2],
        "task_type": row[3],
        "priority": int(row[4] or 100),
        "payload": parse_json(row[5]),
        "attempt_count": int(row[6] or 0),
        "max_attempts": int(row[7] or 0),
        "timeout_seconds": int(row[8] or 1800),
    }


def command_for_task(task: dict[str, Any], args: argparse.Namespace) -> list[str]:
    return build_task_command(task, args, root=project_root(), python_executable=sys.executable)


def finish_task(
    config: MySqlConfig,
    task: dict[str, Any],
    worker_id: str,
    started: float,
    return_code: int,
    output: str,
    error: str,
) -> None:
    ok = return_code == 0
    attempts = int(task.get("attempt_count") or 1)
    max_attempts = int(task.get("max_attempts") or 1)
    queue_status = "done" if ok else ("pending" if attempts < max_attempts else "dead")
    run_status = "ok" if ok else ("failed" if attempts < max_attempts else "dead")
    not_before_update = ""
    if queue_status == "pending":
        delay = min(300, 10 * attempts)
        not_before_update = f", not_before = DATE_ADD(NOW(3), INTERVAL {delay} SECOND)"
    duration_ms = int((time.monotonic() - started) * 1000)
    queue_id = int(task["queue_id"])
    tail = (output + "\n" + error).strip()[-5000:]
    sql = f"""
    UPDATE task_queue
    SET status = {sql_string(queue_status)},
        finished_at = NOW(3),
        locked_by = '',
        locked_until = NULL,
        claim_token = '',
        last_error = {sql_string(error[-2000:] if error else '')}
        {not_before_update}
    WHERE queue_id = {queue_id};

    INSERT INTO task_runs(
      queue_id, task_id, task_kind, task_type, worker_id, started_at, finished_at,
      status, duration_ms, return_code, output_tail, error_text, payload_json
    )
    VALUES(
      {queue_id}, {sql_string(task["task_id"])}, {sql_string(task["task_kind"])},
      {sql_string(task["task_type"])}, {sql_string(worker_id)},
      FROM_UNIXTIME({time.time() - (duration_ms / 1000.0):.3f}), NOW(3),
      {sql_string(run_status)}, {duration_ms}, {return_code},
      {sql_string(tail)}, {sql_string(error[-5000:] if error else '')}, {sql_json(task.get("payload") or {})}
    );
    """
    run_mysql(config, sql)


def execute_task(task: dict[str, Any], args: argparse.Namespace) -> tuple[int, str, str]:
    command = command_for_task(task, args)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if args.mysql_password:
        env["MYSQL_PWD"] = args.mysql_password
    result = subprocess.run(
        command,
        cwd=str(project_root()),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(task.get("timeout_seconds") or args.task_timeout),
        env=env,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def worker_loop(args: argparse.Namespace, config: MySqlConfig) -> int:
    worker_types = task_type_list(args.worker_types)
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}:{','.join(worker_types)}"
    loops = 0
    mysql_retry(lambda: heartbeat(config, worker_id, ",".join(worker_types), "idle"))
    while True:
        try:
            mysql_retry(lambda: release_expired_locks(config))
            task = mysql_retry(lambda: claim_task(config, worker_id, worker_types))
        except Exception as exc:
            if not is_transient_mysql_error(exc):
                raise
            print(json.dumps({"at": now_text(), "worker_db_retry_skipped": f"{type(exc).__name__}: {str(exc)[:300]}"}, ensure_ascii=False))
            loops += 1
            if args.once or (args.max_loops and loops >= args.max_loops):
                return 0
            time.sleep(args.poll_seconds)
            continue
        if not task:
            mysql_retry(lambda: heartbeat(config, worker_id, ",".join(worker_types), "idle"))
            loops += 1
            if args.once or (args.max_loops and loops >= args.max_loops):
                return 0
            time.sleep(args.poll_seconds)
            continue
        mysql_retry(lambda: heartbeat(config, worker_id, ",".join(worker_types), "running", int(task["queue_id"])))
        started = time.monotonic()
        try:
            return_code, stdout, stderr = execute_task(task, args)
        except subprocess.TimeoutExpired as exc:
            return_code = 124
            stdout = exc.stdout or ""
            stderr = (exc.stderr or "") + "\ntimeout"
        except Exception as exc:
            return_code = 1
            stdout = ""
            stderr = f"{type(exc).__name__}:{exc}"
        mysql_retry(lambda: finish_task(config, task, worker_id, started, return_code, stdout, stderr))
        mysql_retry(lambda: heartbeat(config, worker_id, ",".join(worker_types), "idle"))
        print(
            json.dumps(
                {
                    "at": now_text(),
                    "queue_id": task["queue_id"],
                    "task_id": task["task_id"],
                    "task_kind": task["task_kind"],
                    "return_code": return_code,
                },
                ensure_ascii=False,
            )
        )
        loops += 1
        if args.once or (args.max_loops and loops >= args.max_loops):
            return 0


def seed_tasks(config: MySqlConfig) -> None:
    deprecated_ids = ", ".join(sql_string(task_id) for task_id in DEPRECATED_TASK_IDS)
    archived_prefix_filter = " OR ".join(f"task_id LIKE {sql_string(prefix + '%')}" for prefix in ARCHIVED_TASK_PREFIXES)
    statements = [
        f"""
        UPDATE scheduled_tasks
        SET enabled = 0,
            last_message = 'deprecated by current scheduler task definitions'
        WHERE task_id IN ({deprecated_ids});
        DELETE FROM scheduled_tasks
        WHERE task_id IN ({deprecated_ids});
        """
    ]
    if archived_prefix_filter:
        statements.append(
            f"""
            DELETE FROM scheduled_tasks
            WHERE {archived_prefix_filter};
            """
        )
    for task in SCHEDULED_TASKS:
        if str(task.get("task_id") or "") in DEPRECATED_TASK_IDS:
            continue
        next_run_sql = next_run_sql_for_task(str(task["task_id"]))
        statements.append(
            f"""
            INSERT INTO scheduled_tasks(
              task_id, task_name, task_description, task_kind, task_type, enabled,
              schedule_type, update_interval_seconds, priority, timeout_seconds,
              max_attempts, next_run_after, payload_template_json, dedupe_key_template
            )
            VALUES(
              {sql_string(task["task_id"])}, {sql_string(task["task_name"])},
              {sql_string(task["task_description"])}, {sql_string(task["task_kind"])},
              {sql_string(task["task_type"])}, {int(task["enabled"])}, {sql_string(task["schedule_type"])},
              {int(task["interval"])}, {int(task["priority"])}, {int(task["timeout"])},
              2, {next_run_sql}, {sql_json(task["payload"])}, {sql_string(task["dedupe"])}
            )
            ON DUPLICATE KEY UPDATE
              task_name=VALUES(task_name),
              task_description=VALUES(task_description),
              task_kind=VALUES(task_kind),
              task_type=VALUES(task_type),
              enabled=VALUES(enabled),
              schedule_type=VALUES(schedule_type),
              update_interval_seconds=VALUES(update_interval_seconds),
              priority=VALUES(priority),
              timeout_seconds=VALUES(timeout_seconds),
              next_run_after=VALUES(next_run_after),
              payload_template_json=VALUES(payload_template_json),
              dedupe_key_template=VALUES(dedupe_key_template);
            """
        )
    run_mysql(config, "\n".join(statements))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Database-driven scheduler and worker for stock scout tasks.")
    parser.add_argument("--mode", choices=["scheduler", "worker", "seed", "enqueue"], required=True)
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--max-loops", type=int, default=0)
    parser.add_argument("--scheduler-limit", type=int, default=20)
    parser.add_argument("--worker-types", default="maintenance")
    parser.add_argument("--worker-id", default="")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--payload-json", default="", help="Optional JSON payload override for --mode enqueue.")
    parser.add_argument("--task-timeout", type=int, default=1800)
    parser.add_argument("--verbose", action="store_true")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    if args.mode == "seed":
        seed_tasks(config)
        print(json.dumps({"seeded": True}, ensure_ascii=False))
        return 0
    if args.mode == "scheduler":
        return scheduler_loop(args, config)
    if args.mode == "enqueue":
        return enqueue_named_task(args, config)
    return worker_loop(args, config)


if __name__ == "__main__":
    raise SystemExit(main())



