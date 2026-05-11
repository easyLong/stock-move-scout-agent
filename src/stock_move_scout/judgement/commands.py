from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def build_judgement_command(
    *,
    kind: str,
    payload: dict[str, Any],
    root: Path,
    python_executable: str,
    mysql_args: list[str],
    now: datetime | None = None,
) -> list[str] | None:
    current_time = now or datetime.now()

    if kind == "stock_move_judgements":
        command = [
            python_executable,
            str(root / "scripts" / "build_stock_move_judgements.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--scan-top",
            str(int(payload.get("scan_top", 20))),
            "--window-top",
            str(int(payload.get("window_top", 5))),
            "--limit",
            str(int(payload.get("limit", 500))),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if payload.get("latest_only", True):
            command.append("--latest-only")
        if payload.get("dirty_only"):
            command.append("--dirty-only")
        return command + mysql_args

    return None
