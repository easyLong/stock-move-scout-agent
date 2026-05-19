#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.scheduler.task_definitions import DEPRECATED_TASK_IDS, SCHEDULED_TASKS
from stock_move_scout.scheduler.commands import build_task_command
from stock_move_scout.sources.definitions import DATA_SOURCES, source_for_task_kind


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def collect_issues() -> list[dict[str, Any]]:
    root = project_root()
    issues: list[dict[str, Any]] = []
    deprecated = set(DEPRECATED_TASK_IDS)
    task_ids_seen: set[str] = set()
    dummy_args = Namespace(
        mysql_exe="mysql",
        mysql_host="127.0.0.1",
        mysql_port=3306,
        mysql_user="root",
        mysql_password="",
        mysql_database="stock_scout",
        mysql_timeout=30,
    )

    for task in SCHEDULED_TASKS:
        task_id = str(task.get("task_id") or "")
        task_kind = str(task.get("task_kind") or "")
        enabled = bool(task.get("enabled"))

        if task_id in task_ids_seen:
            issues.append({"level": "error", "code": "duplicate_task_id", "task_id": task_id})
        task_ids_seen.add(task_id)

        if task_id in deprecated:
            issues.append({"level": "error", "code": "deprecated_task_scheduled", "task_id": task_id})

        if enabled and not source_for_task_kind(task_kind):
            issues.append(
                {
                    "level": "error",
                    "code": "enabled_task_missing_source",
                    "task_id": task_id,
                    "task_kind": task_kind,
                }
            )

        if enabled:
            task_for_command = {
                "task_kind": task_kind,
                "payload": dict(task.get("payload") or {}),
            }
            try:
                command = build_task_command(
                    task_for_command,
                    dummy_args,
                    root=root,
                    python_executable=sys.executable,
                    now=datetime(2026, 5, 19, 10, 0, 0),
                )
            except Exception as exc:
                command = None
                issues.append(
                    {
                        "level": "error",
                        "code": "enabled_task_command_error",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
            if not command:
                issues.append(
                    {
                        "level": "error",
                        "code": "enabled_task_missing_command",
                        "task_id": task_id,
                        "task_kind": task_kind,
                    }
                )
            elif not Path(command[1]).exists():
                issues.append(
                    {
                        "level": "error",
                        "code": "enabled_task_script_missing",
                        "task_id": task_id,
                        "task_kind": task_kind,
                        "script": command[1],
                    }
                )

    source_task_kinds: dict[str, list[str]] = {}
    for source_id, source in DATA_SOURCES.items():
        if not source.task_kinds:
            issues.append({"level": "warning", "code": "source_without_task_kind", "source_id": source_id})
        if not source.output_tables:
            issues.append({"level": "warning", "code": "source_without_output_tables", "source_id": source_id})
        for task_kind in source.task_kinds:
            source_task_kinds.setdefault(task_kind, []).append(source_id)

    for task_kind, source_ids in sorted(source_task_kinds.items()):
        if len(source_ids) > 1:
            issues.append(
                {
                    "level": "warning",
                    "code": "task_kind_mapped_to_multiple_sources",
                    "task_kind": task_kind,
                    "source_ids": source_ids,
                }
            )

    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Check scheduler/data-source architecture consistency.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()

    issues = collect_issues()
    if args.json:
        print(json.dumps({"ok": not issues, "issues": issues}, ensure_ascii=False, indent=2))
    elif issues:
        print("Architecture consistency issues:")
        for issue in issues:
            print(f"- [{issue['level']}] {issue['code']}: {issue}")
    else:
        print("Architecture consistency OK.")
    return 1 if any(issue.get("level") == "error" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
