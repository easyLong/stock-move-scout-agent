from __future__ import annotations

from pathlib import Path
from typing import Any


def _research_pool_only(payload: dict[str, Any]) -> bool:
    return bool(payload.get("research_pool_only"))


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
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if _research_pool_only(payload):
            command.append("--research-pool-only")
        return command + mysql_args

    if kind == "headline_theme_role_evidence":
        command = [
            python_executable,
            str(root / "scripts" / "build_headline_theme_role_evidence.py"),
        ]
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if payload.get("force", True):
            command.append("--force")
        return command + mysql_args

    return None
