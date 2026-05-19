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
    if kind == "realtime_mover_scan":
        command = [
            python_executable,
            str(root / "scripts" / "windowed_stock_scout_agent.py"),
            "--once",
            "--scan-interval",
            str(int(payload.get("scan_interval", 5))),
            "--window-seconds",
            str(int(payload.get("window_seconds", 15))),
            "--scan-top",
            str(int(payload.get("scan_top", 20))),
            "--aggregate-top",
            str(int(payload.get("aggregate_top", 5))),
            "--min-speed-signal",
            str(float(payload.get("min_speed_signal", 1.5))),
            "--min-single-speed",
            str(float(payload.get("min_single_speed", 1.5))),
            "--min-15s-speed",
            str(float(payload.get("min_15s_speed", 1.5))),
            "--min-accepted-scans",
            str(int(payload.get("min_accepted_scans", 1))),
            "--scan-timeout",
            str(int(payload.get("scan_timeout", 90))),
            "--mysql-primary",
        ]
        if payload.get("research_pool_only", True):
            command.append("--research-pool-only")
        if payload.get("no_evidence", True):
            command.append("--no-evidence")
        if payload.get("no_file_output", True):
            command.append("--no-file-output")
        if payload.get("include_non_trading"):
            command.append("--include-non-trading")
        return command + mysql_args

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
