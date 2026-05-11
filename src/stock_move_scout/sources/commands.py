from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


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

    if kind == "ths_limit_up_review":
        command = [
            python_executable,
            str(root / "scripts" / "collect_ths_limit_up_review.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--days",
            str(int(payload.get("days", 1))),
            "--timeout",
            str(int(payload.get("timeout", 12))),
            "--pause",
            str(float(payload.get("pause", 0.2))),
            "--lookback-days",
            str(int(payload.get("lookback_days", 14))),
        ]
        if payload.get("input_json"):
            command.extend(["--input-json", str(payload.get("input_json"))])
        if payload.get("cookie_file"):
            command.extend(["--cookie-file", str(payload.get("cookie_file"))])
        command.append("--replace-dates" if payload.get("replace_dates", True) else "--no-replace-dates")
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
        return command + mysql_args

    if kind == "stock_theme_reason_bank":
        command = [python_executable, str(root / "scripts" / "rebuild_stock_theme_reason_bank.py"), "--replace"]
        if not payload.get("include_stock_concepts", True):
            command.append("--no-include-stock-concepts")
        if payload.get("include_concept_tags", False):
            command.append("--include-concept-tags")
        if payload.get("chunk_size"):
            command.extend(["--chunk-size", str(int(payload.get("chunk_size", 500)))])
        return command + mysql_args

    if kind == "iwencai_period_rankings":
        command = [
            python_executable,
            str(root / "scripts" / "collect_iwencai_period_rankings.py"),
            "--periods",
            str(payload.get("periods", "3,5,10")),
            "--top",
            str(int(payload.get("top", 300))),
            "--universe",
            str(payload.get("universe", "沪深A股")),
        ]
        if payload.get("trade_date"):
            command.extend(["--trade-date", str(payload.get("trade_date"))])
        return command + mysql_args

    if kind == "lhb_seat_evidence":
        command = [
            python_executable,
            str(root / "scripts" / "collect_lhb_seat_evidence.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--limit",
            str(int(payload.get("limit", 120))),
        ]
        if payload.get("judgement_codes", True):
            command.append("--judgement-codes")
        if payload.get("codes"):
            command.extend(["--codes", str(payload.get("codes"))])
        return command + mysql_args

    if kind == "announcement_effects":
        command = [
            python_executable,
            str(root / "scripts" / "build_announcement_effects.py"),
            "--trade-date",
            str(payload.get("trade_date") or current_time.strftime("%Y-%m-%d")),
            "--lookback-days",
            str(int(payload.get("lookback_days", 240))),
            "--stale-after-days",
            str(int(payload.get("stale_after_days", 31))),
            "--sleep-seconds",
            str(float(payload.get("sleep_seconds", 0.15))),
        ]
        if payload.get("code"):
            command.extend(["--code", str(payload.get("code"))])
        if payload.get("limit"):
            command.extend(["--limit", str(int(payload.get("limit", 0)))])
        if payload.get("refresh_bars", True) is False:
            command.append("--no-refresh-bars")
        if payload.get("allow_local_fallback", False):
            command.append("--allow-local-fallback")
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
        return command + mysql_args

    if kind == "auction_candidates":
        command = [
            python_executable,
            str(root / "scripts" / "build_auction_candidates.py"),
            "--limit",
            str(int(payload.get("limit", 50))),
            "--min-auction-pct",
            str(float(payload.get("min_auction_pct", 1.0))),
            "--min-auction-amount",
            str(float(payload.get("min_auction_amount", 10_000_000))),
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
            command.extend(["--seal-top", str(int(payload.get("seal_top", 10)))])
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

    return None
