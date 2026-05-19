from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def _research_pool_only(payload: dict[str, Any]) -> bool:
    return bool(payload.get("research_pool_only"))


def build_source_command(
    *,
    kind: str,
    payload: dict[str, Any],
    root: Path,
    python_executable: str,
    mysql_args: list[str],
    now: datetime | None = None,
) -> list[str] | None:
    current_time = now or datetime.now()

    if kind in {"iwencai_period_rankings", "ths_limit_up_review", "stock_theme_reason_bank"}:
        return None

    if kind == "build_cold_universe":
        return [python_executable, str(root / "scripts" / "build_stock_scout_universe.py")]

    if kind in {"cold_company_profile_batch", "cold_company_profile"}:
        command = [
            python_executable,
            str(root / "scripts" / "run_configured_evidence_refresh.py"),
            "--task",
            "cold_company_profile",
            "--offset",
            str(int(payload.get("offset", 0))),
            "--batch-size",
            str(int(payload.get("batch_size", 100))),
        ]
        if payload.get("cache_only"):
            command.append("--cache-only")
        if payload.get("workers"):
            command.extend(["--workers", str(int(payload.get("workers", 1)))])
        return command + mysql_args

    if kind == "ths_root_extended_items":
        command = [
            python_executable,
            str(root / "scripts" / "run_configured_evidence_refresh.py"),
            "--task",
            "ths_root_extended_items",
            "--offset",
            str(int(payload.get("offset", 0))),
            "--batch-size",
            str(int(payload.get("batch_size", 100))),
        ]
        if payload.get("cache_only"):
            command.append("--cache-only")
        if payload.get("workers"):
            command.extend(["--workers", str(int(payload.get("workers", 1)))])
        if payload.get("request_timeout"):
            command.extend(["--request-timeout", str(int(payload.get("request_timeout", 8)))])
        if payload.get("max_pages"):
            command.extend(["--max-pages", str(int(payload.get("max_pages", 4)))])
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if _research_pool_only(payload):
            command.append("--research-pool-only")
        return command + mysql_args

    if kind == "daily_root_evidence_pipeline":
        command = [
            python_executable,
            str(root / "scripts" / "run_daily_root_evidence_pipeline.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--batch-size",
            str(int(payload.get("batch_size", 500))),
            "--workers",
            str(int(payload.get("workers", 4))),
            "--request-timeout",
            str(int(payload.get("request_timeout", 12))),
            "--timeout",
            str(int(payload.get("timeout", 1200))),
            "--per-kind-limit",
            str(int(payload.get("per_kind_limit", 8))),
            "--model-config",
            str(payload.get("model_config", "default")),
            "--model-timeout",
            str(int(payload.get("model_timeout", 60))),
        ]
        if payload.get("fallback_only"):
            command.append("--fallback-only")
        if payload.get("skip_model_summary"):
            command.append("--skip-model-summary")
        if payload.get("skip_f10_refresh"):
            command.append("--skip-f10-refresh")
        if payload.get("preserve_trade_date"):
            command.append("--preserve-trade-date")
        if payload.get("service_date_mode"):
            command.extend(["--service-date-mode", str(payload.get("service_date_mode"))])
        if payload.get("fallback_without_model", True):
            command.append("--fallback-without-model")
        else:
            command.append("--no-fallback-without-model")
        return command + mysql_args

    if kind == "morning_market_news":
        command = [
            python_executable,
            str(root / "scripts" / "collect_market_news_digest.py"),
            "--limit",
            str(int(payload.get("limit", 80))),
            "--limit-per-source",
            str(int(payload.get("limit_per_source", 50))),
            "--timeout",
            str(int(payload.get("timeout", 10))),
            "--after-close-hour",
            str(int(payload.get("after_close_hour", 15))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "morning_market_news.json"),
        ]
        command.append("--important-only" if payload.get("important_only", True) else "--no-important-only")
        if payload.get("since"):
            command.extend(["--since", str(payload.get("since"))])
        if payload.get("until"):
            command.extend(["--until", str(payload.get("until"))])
        return command + mysql_args

    if kind == "daily_market_themes":
        command = [
            python_executable,
            str(root / "scripts" / "build_daily_market_themes.py"),
            "--after-close-hour",
            str(int(payload.get("after_close_hour", 15))),
            "--min-importance",
            str(int(payload.get("min_importance", 2))),
            "--limit-titles",
            str(int(payload.get("limit_titles", 5))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "daily_market_themes.json"),
        ]
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if payload.get("since"):
            command.extend(["--since", str(payload.get("since"))])
        if payload.get("until"):
            command.extend(["--until", str(payload.get("until"))])
        return command + mysql_args

    if kind == "morning_reference_post":
        command = [
            python_executable,
            str(root / "scripts" / "build_morning_reference_post.py"),
            "--after-close-hour",
            str(int(payload.get("after_close_hour", 15))),
            "--theme-limit",
            str(int(payload.get("theme_limit", 8))),
            "--news-limit",
            str(int(payload.get("news_limit", 30))),
            "--min-importance",
            str(int(payload.get("min_importance", 2))),
            "--output-dir",
            str(root / "runs" / "posts"),
            "--model-config",
            str(payload.get("model_config", "default")),
            "--model-timeout",
            str(int(payload.get("model_timeout", payload.get("timeout", 60)))),
        ]
        if payload.get("model"):
            command.extend(["--model", str(payload.get("model"))])
        if payload.get("base_url"):
            command.extend(["--base-url", str(payload.get("base_url"))])
        if payload.get("api_key_file"):
            command.extend(["--api-key-file", str(payload.get("api_key_file"))])
        if payload.get("fallback_without_model", True):
            command.append("--fallback-without-model")
        else:
            command.append("--no-fallback-without-model")
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if payload.get("since"):
            command.extend(["--since", str(payload.get("since"))])
        if payload.get("until"):
            command.extend(["--until", str(payload.get("until"))])
        return command + mysql_args

    if kind == "scheduled_task_health_check":
        command = [
            python_executable,
            str(root / "scripts" / "check_scheduled_task_health.py"),
            "--grace-minutes",
            str(int(payload.get("grace_minutes", 15))),
            "--lookback-days",
            str(int(payload.get("lookback_days", 3))),
        ]
        if payload.get("as_of"):
            command.extend(["--as-of", str(payload.get("as_of"))])
        return command + mysql_args

    if kind == "ths_market_after_close_summary":
        command = [
            python_executable,
            str(root / "scripts" / "collect_ths_market_after_close_summary.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 12))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "ths_market_after_close_summary.json"),
        ]
        return command + mysql_args

    if kind == "ths_hot_concepts":
        command = [
            python_executable,
            str(root / "scripts" / "collect_ths_hot_concepts.py"),
            "--days",
            str(int(payload.get("days", 14))),
            "--max-pages",
            str(int(payload.get("max_pages", 8))),
            "--timeout",
            str(int(payload.get("timeout", 15))),
            "--pause",
            str(float(payload.get("pause", 0.15))),
        ]
        if payload.get("skip_details"):
            command.append("--skip-details")
        if payload.get("skip_members"):
            command.append("--skip-members")
        return command + mysql_args

    if kind == "ths_homepage_headline_themes":
        command = [
            python_executable,
            str(root / "scripts" / "collect_ths_homepage_headline_themes.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 15))),
            "--pause",
            str(float(payload.get("pause", 0.03))),
            "--max-pages",
            str(int(payload.get("max_pages", 80))),
        ]
        if payload.get("fail_on_empty"):
            command.append("--fail-on-empty")
        if payload.get("hot_only"):
            command.append("--hot-only")
        command.append("--replace-date" if payload.get("replace_date", True) else "--no-replace-date")
        return command + mysql_args

    if kind == "ths_homepage_headline_freeze":
        command = [
            python_executable,
            str(root / "scripts" / "freeze_ths_homepage_headline_snapshot.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
        ]
        if payload.get("source_trade_date"):
            command.extend(["--source-trade-date", str(payload.get("source_trade_date"))])
        if payload.get("allow_carry_forward"):
            command.append("--allow-carry-forward")
        if payload.get("fail_on_empty"):
            command.append("--fail-on-empty")
        return command + mysql_args

    if kind == "eastmoney_limit_up_pool":
        command = [
            python_executable,
            str(root / "scripts" / "collect_eastmoney_limit_up_pool.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--days",
            str(int(payload.get("days", 1))),
            "--pause",
            str(float(payload.get("pause", 0.3))),
            "--retries",
            str(int(payload.get("retries", 3))),
        ]
        if payload.get("dates"):
            command.extend(["--dates", str(payload.get("dates"))])
        if payload.get("fail_on_empty"):
            command.append("--fail-on-empty")
        command.append("--replace-dates" if payload.get("replace_dates", True) else "--no-replace-dates")
        return command + mysql_args

    if kind == "kpl_limit_up_reasons":
        command = [
            python_executable,
            str(root / "scripts" / "collect_kpl_limit_up_reasons.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 8))),
            "--pause",
            str(float(payload.get("pause", 0.08))),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if payload.get("limit"):
            command.extend(["--limit", str(int(payload.get("limit", 0)))])
        return command + mysql_args

    if kind == "kpl_replay_limit_themes":
        command = [
            python_executable,
            str(root / "scripts" / "collect_kpl_replay_limit_themes.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 8))),
            "--pause",
            str(float(payload.get("pause", 0.05))),
        ]
        return command + mysql_args

    if kind == "kpl_plate_strength":
        command = [
            python_executable,
            str(root / "scripts" / "collect_kpl_plate_strength.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit",
            str(int(payload.get("limit", 80))),
            "--timeout",
            str(int(payload.get("timeout", 8))),
        ]
        return command + mysql_args

    if kind == "kpl_market_capacity":
        command = [
            python_executable,
            str(root / "scripts" / "collect_kpl_market_capacity.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 8))),
            "--market-type",
            str(int(payload.get("market_type", 0))),
        ]
        return command + mysql_args

    if kind == "kpl_stock_featured_sections":
        command = [
            python_executable,
            str(root / "scripts" / "collect_kpl_stock_featured_sections.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--timeout",
            str(int(payload.get("timeout", 8))),
            "--pause",
            str(float(payload.get("pause", 0.08))),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if payload.get("limit"):
            command.extend(["--limit", str(int(payload.get("limit", 0)))])
        return command + mysql_args

    if kind == "post_close_leaderboard_snapshot":
        command = [
            python_executable,
            str(root / "scripts" / "build_leaderboard_snapshot.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit-up-days",
            str(int(payload.get("limit_up_days", 5))),
            "--gain-period-days",
            str(int(payload.get("gain_period_days", 5))),
            "--gain-top",
            str(int(payload.get("gain_top", 30))),
        ]
        if payload.get("force", True):
            command.append("--force")
        if payload.get("skip_research_pool"):
            command.append("--skip-research-pool")
        return command + mysql_args

    if kind == "kpl_leaderboard_snapshot":
        command = [
            python_executable,
            str(root / "scripts" / "build_leaderboard_snapshot.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--kpl-only",
        ]
        return command + mysql_args

    if kind == "ths_stock_concepts":
        command = [
            python_executable,
            str(root / "scripts" / "collect_ths_stock_concepts.py"),
            "--offset",
            str(int(payload.get("offset", 0))),
            "--limit",
            str(int(payload.get("batch_size", 200))),
            "--timeout",
            str(int(payload.get("timeout", 10))),
            "--pause",
            str(float(payload.get("pause", 0.08))),
            "--chunk-size",
            str(int(payload.get("chunk_size", 300))),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if _research_pool_only(payload):
            command.append("--research-pool-only")
        return command + mysql_args

    if kind == "research_pool_snapshot":
        command = [
            python_executable,
            str(root / "scripts" / "build_research_pool_snapshot.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit-up-days",
            str(int(payload.get("limit_up_days", 5))),
            "--gain-period-days",
            str(int(payload.get("gain_period_days", 5))),
            "--gain-top",
            str(int(payload.get("gain_top", 30))),
        ]
        if payload.get("force", True):
            command.append("--force")
        return command + mysql_args

    if kind == "research_pool_theme_members":
        command = [
            python_executable,
            str(root / "scripts" / "build_research_pool_theme_members.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
        ]
        if payload.get("force", True):
            command.append("--force")
        return command + mysql_args

    if kind == "effective_facts":
        command = [
            python_executable,
            str(root / "scripts" / "build_effective_facts.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if _research_pool_only(payload):
            command.append("--research-pool-only")
        return command + mysql_args

    if kind == "auction_candidates":
        command = [
            python_executable,
            str(root / "scripts" / "build_auction_candidates.py"),
            "--limit",
            str(int(payload.get("limit", 3))),
            "--min-auction-pct",
            str(float(payload.get("min_auction_pct", 0.0))),
            "--min-auction-amount",
            str(float(payload.get("min_auction_amount", 0.0))),
            "--theme-limit",
            str(int(payload.get("theme_limit", 20))),
            "--timeout",
            str(int(payload.get("timeout", 3))),
            "--batch-size",
            str(int(payload.get("batch_size", 80))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "auction_candidates.json"),
        ]
        if payload.get("minute_analysis", True):
            command.append("--minute-analysis")
        if payload.get("loop_until"):
            command.extend(["--loop-until", str(payload.get("loop_until"))])
        if payload.get("minute_interval"):
            command.extend(["--minute-interval", str(int(payload.get("minute_interval", 60)))])
        if payload.get("max_minute_runs"):
            command.extend(["--max-minute-runs", str(int(payload.get("max_minute_runs", 0)))])
        if payload.get("minute_top"):
            command.extend(["--minute-top", str(int(payload.get("minute_top", 10)))])
        if payload.get("seal_top"):
            command.extend(["--seal-top", str(int(payload.get("seal_top", 3)))])
        if payload.get("include_st"):
            command.append("--include-st")
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        if payload.get("allow_outside_auction"):
            command.append("--allow-outside-auction")
        return command + mysql_args

    if kind == "auction_trend_summary":
        command = [
            python_executable,
            str(root / "scripts" / "build_auction_trend_summary.py"),
            "--limit",
            str(int(payload.get("limit", 80))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "auction_trend_summary.json"),
        ]
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        return command + mysql_args

    if kind == "market_width_snapshot":
        command = [
            python_executable,
            str(root / "scripts" / "collect_market_width_snapshot.py"),
            "--source",
            str(payload.get("source", "tdx")),
            "--output-json",
            str(root / "runs" / "data_tasks" / "market_width_latest.json"),
        ]
        if payload.get("batch_size"):
            command.extend(["--batch-size", str(int(payload.get("batch_size", 80)))])
        if payload.get("tdx_timeout"):
            command.extend(["--tdx-timeout", str(int(payload.get("tdx_timeout", 3)))])
        if payload.get("include_bj", False):
            command.append("--include-bj")
        if payload.get("include_st", False):
            command.append("--include-st")
        return command + mysql_args

    if kind == "market_width_daily_close":
        command = [
            python_executable,
            str(root / "scripts" / "collect_market_width_daily_close.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--min-rows",
            str(int(payload.get("min_rows", 4800))),
            "--workers",
            str(int(payload.get("workers", 12))),
            "--batch-size",
            str(int(payload.get("batch_size", 900))),
            "--wait-minutes",
            str(int(payload.get("wait_minutes", 20))),
            "--retry-seconds",
            str(int(payload.get("retry_seconds", 120))),
            "--output-json",
            str(root / "runs" / "data_tasks" / "market_width_daily_close.json"),
        ]
        if payload.get("refresh_bars", True) is False:
            command.append("--no-refresh-bars")
        return command + mysql_args

    return None
