from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def build_evidence_command(
    *,
    kind: str,
    payload: dict[str, Any],
    root: Path,
    python_executable: str,
    mysql_args: list[str],
    now: datetime | None = None,
) -> list[str] | None:
    current_time = now or datetime.now()

    if kind in {"async_evidence_source_sync", "async_evidence_summary"}:
        command = [
            python_executable,
            str(root / "scripts" / "summarize_async_evidence.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit",
            str(int(payload.get("limit", 20))),
            "--per-kind-limit",
            str(int(payload.get("per_kind_limit", 8))),
            "--model-config",
            str(payload.get("model_config", "default")),
            "--timeout",
            str(int(payload.get("timeout", 60))),
        ]
        if payload.get("model"):
            command.extend(["--model", str(payload.get("model"))])
        if payload.get("base_url"):
            command.extend(["--base-url", str(payload.get("base_url"))])
        if payload.get("api_key_file"):
            command.extend(["--api-key-file", str(payload.get("api_key_file"))])
        if payload.get("fallback_without_model", True):
            command.append("--fallback-without-model")
        if payload.get("force"):
            command.append("--force")
        if kind == "async_evidence_source_sync" or payload.get("sync_dirty"):
            command.append("--sync-dirty")
        if payload.get("dirty_only"):
            command.append("--dirty-only")
        return command + mysql_args

    if kind == "root_evidence_cache_dirty":
        command = [
            python_executable,
            str(root / "scripts" / "refresh_root_evidence_cache.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit",
            str(int(payload.get("limit", 50))),
            "--dirty-only",
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        return command + mysql_args

    if kind in {"stock_move_events", "derived_signals", "stock_move_evidence", "event_engine"}:
        stage = {
            "stock_move_events": "events",
            "derived_signals": "signals",
            "stock_move_evidence": "evidence",
            "event_engine": "all",
        }[kind]
        command = [
            python_executable,
            str(root / "scripts" / "build_event_engine.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--stage",
            stage,
        ]
        if payload.get("limit"):
            command.extend(["--limit", str(int(payload.get("limit", 0)))])
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        return command + mysql_args

    if kind == "hot_evidence_worker":
        command = [
            python_executable,
            str(root / "scripts" / "run_mysql_window_evidence_worker.py"),
            "--window-id",
            str(payload.get("window_id", "")),
            "--evidence-top",
            str(int(payload.get("evidence_top", 10))),
            "--community-top",
            str(int(payload.get("community_top", 3))),
            "--community-mode",
            str(payload.get("community_mode", "cache")),
            "--community-cache-hours",
            str(int(payload.get("community_cache_hours", 72))),
            "--community-hot-posts-per-stock",
            str(int(payload.get("community_hot_posts_per_stock", 8))),
            "--official-site-mode",
            str(payload.get("official_site_mode", "cache")),
            "--community-manual-verify-wait",
            str(int(payload.get("community_manual_verify_wait", 8))),
            "--community-verify-retries",
            str(int(payload.get("community_verify_retries", 0))),
            "--community-bridge-timeout",
            str(int(payload.get("community_bridge_timeout", 40))),
            "--community-timeout",
            str(int(payload.get("community_timeout", 420))),
            "--timeout",
            str(int(payload.get("timeout_seconds", 1200))),
        ]
        if payload.get("model"):
            command.extend(["--model", str(payload.get("model"))])
        if payload.get("openai_base_url"):
            command.extend(["--openai-base-url", str(payload.get("openai_base_url"))])
        return command + mysql_args

    return None
