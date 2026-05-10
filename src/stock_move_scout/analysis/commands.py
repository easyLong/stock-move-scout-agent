from __future__ import annotations

from pathlib import Path
from typing import Any


def build_analysis_command(
    *,
    kind: str,
    payload: dict[str, Any],
    root: Path,
    python_executable: str,
    mysql_args: list[str],
) -> list[str] | None:
    if kind == "anchor_realtime_roles":
        command = [
            python_executable,
            str(root / "scripts" / "build_anchor_realtime_roles.py"),
            "--levels",
            str(payload.get("levels", "strong,medium")),
            "--medium-cap",
            str(int(payload.get("medium_cap", 120))),
            "--min-members",
            str(int(payload.get("min_members", 2))),
            "--batch-size",
            str(int(payload.get("batch_size", 80))),
            "--tdx-timeout",
            str(int(payload.get("tdx_timeout", 3))),
        ]
        if payload.get("trading_only", True):
            command.append("--trading-only")
        if payload.get("servers"):
            command.extend(["--servers", str(payload.get("servers"))])
        return command + mysql_args

    return None
