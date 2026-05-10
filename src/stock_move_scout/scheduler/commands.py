from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_move_scout.analysis import build_analysis_command
from stock_move_scout.db import mysql_cli_args_from_args
from stock_move_scout.evidence import build_evidence_command
from stock_move_scout.judgement import build_judgement_command
from stock_move_scout.sources import build_source_command


def build_task_command(
    task: dict[str, Any],
    args: argparse.Namespace,
    *,
    root: Path,
    python_executable: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    payload = task.get("payload") or {}
    kind = task["task_kind"]
    mysql_args = mysql_cli_args_from_args(args)
    executable = python_executable or sys.executable
    current_time = now or datetime.now()

    source_command = build_source_command(
        kind=kind,
        payload=payload,
        root=root,
        python_executable=executable,
        mysql_args=mysql_args,
        now=current_time,
    )
    if source_command is not None:
        return source_command

    evidence_command = build_evidence_command(
        kind=kind,
        payload=payload,
        root=root,
        python_executable=executable,
        mysql_args=mysql_args,
        now=current_time,
    )
    if evidence_command is not None:
        return evidence_command

    analysis_command = build_analysis_command(
        kind=kind,
        payload=payload,
        root=root,
        python_executable=executable,
        mysql_args=mysql_args,
    )
    if analysis_command is not None:
        return analysis_command

    judgement_command = build_judgement_command(
        kind=kind,
        payload=payload,
        root=root,
        python_executable=executable,
        mysql_args=mysql_args,
        now=current_time,
    )
    if judgement_command is not None:
        return judgement_command

    raise ValueError(f"unsupported task_kind: {kind}")
