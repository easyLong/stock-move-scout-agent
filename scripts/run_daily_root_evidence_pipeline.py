#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.db import mysql_cli_args_from_args, run_mysql, sql_string
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.web import resolve_trade_date

from stock_scout_mysql import add_mysql_args, mysql_config_from_args


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily research-pool root evidence pipeline once.")
    parser.add_argument("--trade-date", default="latest")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--request-timeout", type=int, default=12)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--per-kind-limit", type=int, default=8)
    parser.add_argument("--model-config", default=os.environ.get("MODEL_CONFIG_NAME", "default"))
    parser.add_argument("--model-timeout", type=int, default=60)
    parser.add_argument("--fallback-without-model", action="store_true", default=True)
    parser.add_argument("--no-fallback-without-model", dest="fallback_without_model", action="store_false")
    parser.add_argument("--fallback-only", action="store_true", help="Use deterministic summaries without model calls.")
    parser.add_argument("--skip-model-summary", action="store_true")
    parser.add_argument("--skip-f10-refresh", action="store_true")
    parser.add_argument("--skip-fetched-today", action="store_true", default=True, help="Skip F10 stocks already fetched today.")
    parser.add_argument("--no-skip-fetched-today", dest="skip_fetched_today", action="store_false")
    parser.add_argument("--force-model-summary", action="store_true", help="Re-run summaries even when the effective-fact hash is unchanged.")
    parser.add_argument("--preserve-trade-date", action="store_true", help="Use the requested date as the service trade date even before intraday data exists.")
    parser.add_argument(
        "--service-date-mode",
        choices=["requested", "next_trade_day"],
        default="requested",
        help="How to derive the service trade date from --trade-date.",
    )
    parser.add_argument("--force", action="store_true", default=True)
    parser.add_argument("--no-force", dest="force", action="store_false")
    add_mysql_args(parser)
    return parser.parse_args()


def run_step(name: str, command: list[str], root: Path, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=str(root),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )
    output = (result.stdout or "").strip()
    error = (result.stderr or "").strip()
    parsed: Any = None
    if output:
        try:
            parsed = json.loads(output.splitlines()[-1])
        except Exception:
            parsed = None
    return {
        "name": name,
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "json": parsed,
        "output_tail": output[-2000:],
        "error_tail": error[-2000:],
    }


def cleanup_dirty_queues(config: Any, trade_date: str, codes: list[str]) -> dict[str, int]:
    if not codes:
        return {"root_dirty_done": 0, "effective_dirty_done": 0}
    codes_sql = ",".join(sql_string(code) for code in codes)
    sql = f"""
    UPDATE stock_root_evidence_cache_dirty_queue
    SET status='done',
        finished_at=CURRENT_TIMESTAMP(3),
        last_error='',
        updated_at=CURRENT_TIMESTAMP(3)
    WHERE trade_date={sql_string(trade_date)}
      AND code IN ({codes_sql})
      AND status IN ('pending','running','failed');
    SELECT ROW_COUNT();

    UPDATE stock_effective_facts_dirty_queue
    SET status='done',
        finished_at=CURRENT_TIMESTAMP(3),
        last_error='',
        updated_at=CURRENT_TIMESTAMP(3)
    WHERE trade_date={sql_string(trade_date)}
      AND code IN ({codes_sql})
      AND status IN ('pending','running','failed');
    SELECT ROW_COUNT();
    """
    rows = [line.split("\t") for line in run_mysql(config, sql, batch=True, raw=True).splitlines() if line.strip()]
    values = [int(float(row[0] or 0)) for row in rows if row]
    return {
        "root_dirty_done": values[0] if values else 0,
        "effective_dirty_done": values[1] if len(values) > 1 else 0,
    }


def next_trade_day(value: str) -> str:
    try:
        day = date.fromisoformat(value)
    except ValueError:
        day = datetime.now().date()
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    root = project_root()
    config = mysql_config_from_args(args)
    requested_trade_date = str(args.trade_date or "").strip()
    context_trade_date = resolve_trade_date(config, requested_trade_date)
    if args.service_date_mode == "next_trade_day":
        base_date = requested_trade_date if requested_trade_date and requested_trade_date != "latest" else context_trade_date
        trade_date = next_trade_day(base_date)
    elif args.preserve_trade_date and requested_trade_date and requested_trade_date != "latest":
        trade_date = requested_trade_date
    else:
        trade_date = context_trade_date
    mysql_args = mysql_cli_args_from_args(args)
    py = sys.executable

    steps: list[dict[str, Any]] = []
    commands: list[tuple[str, list[str], int]] = [
        (
            "research_pool_snapshot",
            [
                py,
                str(root / "scripts" / "build_research_pool_snapshot.py"),
                "--trade-date",
                trade_date,
                "--limit-up-days",
                "5",
                "--gain-period-days",
                "5",
                "--gain-top",
                "30",
                "--force",
                *mysql_args,
            ],
            300,
        ),
    ]
    if not args.skip_f10_refresh:
        commands.append(
            (
                "ths_root_important_events",
                [
                    py,
                    str(root / "scripts" / "run_configured_evidence_refresh.py"),
                    "--task",
                    "ths_root_extended_items",
                    "--trade-date",
                    trade_date,
                    "--research-pool-only",
                    "--batch-size",
                    str(args.batch_size),
                    "--workers",
                    str(args.workers),
                    "--request-timeout",
                    str(args.request_timeout),
                    "--timeout",
                    str(args.timeout),
                    *mysql_args,
                ],
                args.timeout,
            )
        )
        if args.skip_fetched_today:
            commands[-1][1].append("--skip-fetched-today")
    commands.extend(
        [
            (
                "research_pool_theme_members",
                [
                    py,
                    str(root / "scripts" / "build_research_pool_theme_members.py"),
                    "--trade-date",
                    trade_date,
                    "--force",
                    *mysql_args,
                ],
                300,
            ),
            (
                "headline_theme_role_evidence",
                [
                    py,
                    str(root / "scripts" / "build_headline_theme_role_evidence.py"),
                    "--trade-date",
                    trade_date,
                    "--force",
                    *mysql_args,
                ],
                180,
            ),
            (
                "effective_facts",
                [
                    py,
                    str(root / "scripts" / "build_effective_facts.py"),
                    "--trade-date",
                    trade_date,
                    "--research-pool-only",
                    *mysql_args,
                ],
                300,
            ),
        ]
    )
    if not args.skip_model_summary:
        summary_command = [
            py,
            str(root / "scripts" / "summarize_async_evidence.py"),
            "--trade-date",
            trade_date,
            "--preserve-trade-date",
            "--research-pool-only",
            "--limit",
            "1000",
            "--per-kind-limit",
            str(args.per_kind_limit),
            "--model-config",
            args.model_config,
            "--timeout",
            str(args.model_timeout),
            *mysql_args,
        ]
        if args.force_model_summary:
            summary_command.append("--force")
        if args.fallback_only:
            summary_command.append("--fallback-only")
        elif args.fallback_without_model:
            summary_command.append("--fallback-without-model")
        commands.append(("async_evidence_summary_once", summary_command, max(args.timeout, 1800)))
    commands.append(
        (
            "root_evidence_cache",
            [
                py,
                str(root / "scripts" / "refresh_root_evidence_cache.py"),
                "--trade-date",
                trade_date,
                "--research-pool-only",
                "--force",
                *mysql_args,
            ],
            300,
        )
    )

    ok = True
    for name, command, timeout in commands:
        step = run_step(name, command, root, timeout)
        steps.append(step)
        if not step["ok"]:
            ok = False
            break

    research_codes = ResearchPoolProvider(config).latest_codes(trade_date)
    dirty_cleanup = cleanup_dirty_queues(config, trade_date, research_codes) if ok else {}
    result = {
        "trade_date": trade_date,
        "service_trade_date": trade_date,
        "context_trade_date": context_trade_date,
        "service_date_mode": args.service_date_mode,
        "ok": ok,
        "research_pool_count": len(research_codes),
        "steps": steps,
        "dirty_cleanup": dirty_cleanup,
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
