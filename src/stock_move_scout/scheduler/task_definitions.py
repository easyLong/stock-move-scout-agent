from __future__ import annotations

from typing import Any


DEPRECATED_TASK_IDS = (
    "build_cold_universe",
    "cold_company_profile_batch_0",
    "import_latest_mysql",
    "warm_hard_evidence_auto",
    "ths_root_extended_items",
    "research_pool_theme_members",
    "headline_theme_role_evidence",
    "effective_facts",
    "async_evidence_source_sync",
    "async_evidence_summary",
    "root_evidence_cache_dirty",
    "stock_theme_reason_bank",
    "iwencai_period_rankings",
    "ths_limit_up_review",
    "stock_move_judgement_dirty",
    "daily_root_evidence_pipeline",
    "next_trade_day_evidence_prepare",
    "post_close_next_trade_day_evidence_prepare",
)


ARCHIVED_TASK_PREFIXES = (
    "hot_evidence_worker_",
)


TRADING_TIME_TASK_IDS = frozenset(
    {
        "anchor_realtime_roles",
        "event_engine",
        "kpl_plate_strength",
        "market_width_snapshot",
        "realtime_mover_scan",
        "stock_move_judgements",
    }
)


PREOPEN_TIME_TASK_IDS = frozenset({"auction_candidates"})


SCHEDULED_TASKS: list[dict[str, Any]] = [
    {
        "task_id": "cold_company_profile",
        "task_name": "冷数据：公司画像",
        "task_description": "初始化或按需补全公司画像；默认不自动执行。",
        "task_kind": "cold_company_profile",
        "task_type": "cold",
        "enabled": 0,
        "schedule_type": "manual",
        "interval": 315360000,
        "priority": 80,
        "timeout": 1800,
        "payload": {"batch_size": 100, "cache_only": False, "workers": 4},
        "dedupe": "cold_company_profile:{run_key}:{offset}",
    },
    {
        "task_id": "ths_root_extended_items",
        "task_name": "根页面扩展信息",
        "task_description": "每天刷新研究池股票的同花顺F10根页面重要事件。",
        "task_kind": "ths_root_extended_items",
        "task_type": "cold",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 70,
        "timeout": 1800,
        "payload": {"batch_size": 100, "cache_only": False, "workers": 4, "request_timeout": 8, "max_pages": 1, "research_pool_only": True},
        "dedupe": "ths_root_extended_items:{run_key}:{offset}",
    },
    {
        "task_id": "pre_trade_night_evidence_prepare",
        "task_name": "交易日前夜证据准备",
        "task_description": "交易日前一晚刷新研究池、同花顺F10重要事件、有效事实、模型总结和根证据缓存，服务下一个交易日。",
        "task_kind": "daily_root_evidence_pipeline",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 47,
        "timeout": 3600,
        "payload": {
            "batch_size": 500,
            "workers": 4,
            "request_timeout": 12,
            "timeout": 1200,
            "per_kind_limit": 8,
            "model_config": "default",
            "model_timeout": 60,
            "fallback_without_model": True,
            "preserve_trade_date": True,
            "service_date_mode": "next_trade_day",
        },
        "dedupe": "pre_trade_night_evidence_prepare:{run_key}",
    },
    {
        "task_id": "morning_market_news",
        "task_name": "早盘资讯：财联社/华尔街见闻",
        "task_description": "每天8:30抓取财联社和华尔街见闻头条、昨日盘后至今的重要快讯。",
        "task_kind": "morning_market_news",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 50,
        "timeout": 300,
        "payload": {"important_only": True, "limit": 80, "limit_per_source": 50, "after_close_hour": 15, "timeout": 10},
        "dedupe": "morning_market_news:{run_key}",
    },
    {
        "task_id": "daily_market_themes",
        "task_name": "早盘主题雷达",
        "task_description": "每天8:32把早盘资讯加工成今日催化主题池。",
        "task_kind": "daily_market_themes",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 55,
        "timeout": 180,
        "payload": {"min_importance": 2, "limit_titles": 5, "after_close_hour": 15},
        "dedupe": "daily_market_themes:{run_key}",
    },
    {
        "task_id": "morning_reference_post",
        "task_name": "早参帖子",
        "task_description": "每天8:35基于上一个交易日收盘后至今的盘前消息，用模型总结生成早参帖子。",
        "task_kind": "morning_reference_post",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 56,
        "timeout": 180,
        "payload": {
            "min_importance": 2,
            "theme_limit": 8,
            "news_limit": 30,
            "after_close_hour": 15,
            "model_config": "default",
            "model_timeout": 60,
            "fallback_without_model": True,
            "workflow": True,
            "review_max_rewrites": 1,
            "loop_until": "07:20",
            "loop_interval_seconds": 60,
            "min_themes": 1,
        },
        "dedupe": "morning_reference_post:{run_key}",
    },
    {
        "task_id": "scheduled_task_health_check",
        "task_name": "Scheduled Task Health Check",
        "task_description": "Daily 08:00 check for scheduled tasks that missed their due time or failed recently.",
        "task_kind": "scheduled_task_health_check",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 40,
        "timeout": 120,
        "payload": {"grace_minutes": 15, "lookback_days": 3},
        "dedupe": "scheduled_task_health_check:{run_key}",
    },
    {
        "task_id": "ths_market_after_close_summary",
        "task_name": "THS After-Close Market Summary",
        "task_description": "Collect THS after-close market review as previous-day market context for morning reference posts.",
        "task_kind": "ths_market_after_close_summary",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 54,
        "timeout": 180,
        "payload": {"timeout": 12},
        "dedupe": "ths_market_after_close_summary:{run_key}",
    },
    {
        "task_id": "ths_hot_concepts",
        "task_name": "低频数据：同花顺今天炒什么",
        "task_description": "每天收盘后更新一次同花顺今天炒什么事件、主题成分股，并重建近14日有效锚点池。",
        "task_kind": "ths_hot_concepts",
        "task_type": "cold",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 65,
        "timeout": 900,
        "payload": {"days": 14, "max_pages": 8, "timeout": 15, "pause": 0.15},
        "dedupe": "ths_hot_concepts:{run_key}",
    },
    {
        "task_id": "ths_homepage_headline_themes",
        "task_name": "THS Homepage Headline Themes",
        "task_description": "盘后采集一次同花顺首页头条题材，作为冻结快照的数据源。",
        "task_kind": "ths_homepage_headline_themes",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 43,
        "timeout": 300,
        "payload": {"timeout": 15, "pause": 0.03, "max_pages": 80, "fail_on_empty": True},
        "dedupe": "ths_homepage_headline_themes:{minute_key}",
    },
    {
        "task_id": "ths_homepage_headline_freeze",
        "task_name": "THS Homepage Headline Freeze",
        "task_description": "盘后冻结同花顺首页题材快照，作为下一交易日同花顺领头羊的题材口径。",
        "task_kind": "ths_homepage_headline_freeze",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 44,
        "timeout": 120,
        "payload": {"fail_on_empty": True},
        "dedupe": "ths_homepage_headline_freeze:{run_key}",
    },
    {
        "task_id": "eastmoney_limit_up_pool",
        "task_name": "Eastmoney Limit-Up Pool",
        "task_description": "Collect confirmed limit-up pool from AkShare stock_zt_pool_em.",
        "task_kind": "eastmoney_limit_up_pool",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 44,
        "timeout": 300,
        "payload": {"days": 1, "pause": 0.3, "retries": 3, "fail_on_empty": True},
        "dedupe": "eastmoney_limit_up_pool:{run_key}",
    },
    {
        "task_id": "ths_stock_concepts",
        "task_name": "THS Stock Concept Explanations",
        "task_description": "Collect THS per-stock concept explanations from F10 concept page.",
        "task_kind": "ths_stock_concepts",
        "task_type": "cold",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 64,
        "timeout": 900,
        "payload": {"batch_size": 200, "timeout": 10, "pause": 0.08, "chunk_size": 300, "research_pool_only": True},
        "dedupe": "ths_stock_concepts:{run_key}:{offset}",
    },
    {
        "task_id": "research_pool_snapshot",
        "task_name": "Research Pool Snapshot",
        "task_description": "Materialize the daily research pool after close, once limit-up pool and daily bars are refreshed.",
        "task_kind": "research_pool_snapshot",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 45,
        "timeout": 180,
        "payload": {"limit_up_days": 5, "gain_period_days": 5, "gain_top": 30, "force": True},
        "dedupe": "research_pool_snapshot:{run_key}",
    },
    {
        "task_id": "post_close_leaderboard_snapshot",
        "task_name": "Post-Close Leaderboard Snapshot",
        "task_description": "After research pool, THS frozen themes and KPL limit-up reasons are ready, materialize the THS leaderboard snapshot.",
        "task_kind": "post_close_leaderboard_snapshot",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 51,
        "timeout": 1200,
        "payload": {"limit_up_days": 5, "gain_period_days": 5, "gain_top": 30, "force": True, "all_pool_modes": True},
        "dedupe": "post_close_leaderboard_snapshot:{run_key}",
    },
    {
        "task_id": "kpl_limit_up_reasons",
        "task_name": "KPL Limit-Up Reasons",
        "task_description": "Collect KPL per-stock limit-up reasons for the confirmed research pool after the post-close pool is refreshed.",
        "task_kind": "kpl_limit_up_reasons",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 47,
        "timeout": 1200,
        "payload": {"timeout": 8, "pause": 0.05},
        "dedupe": "kpl_limit_up_reasons:{run_key}",
    },
    {
        "task_id": "kpl_replay_limit_themes",
        "task_name": "KPL ReplayLa Limit Themes",
        "task_description": "Collect ReplayLa limit-up reason groups and materialize one primary theme for limit-up stocks.",
        "task_kind": "kpl_replay_limit_themes",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 48,
        "timeout": 1200,
        "payload": {"timeout": 8, "pause": 0.05},
        "dedupe": "kpl_replay_limit_themes:{run_key}",
    },
    {
        "task_id": "kpl_stock_featured_sections",
        "task_name": "KPL Stock Featured Sections",
        "task_description": "Collect KPL featured sections for the daily research pool; used as the primary theme fallback for non-limit-up stocks.",
        "task_kind": "kpl_stock_featured_sections",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 49,
        "timeout": 1200,
        "payload": {"timeout": 8, "pause": 0.08},
        "dedupe": "kpl_stock_featured_sections:{run_key}",
    },
    {
        "task_id": "kpl_leaderboard_snapshot",
        "task_name": "KPL Leaderboard Snapshot",
        "task_description": "Materialize the post-close KPL featured leaderboard cache after KPL reason and section sources are ready.",
        "task_kind": "kpl_leaderboard_snapshot",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 52,
        "timeout": 1200,
        "payload": {"all_pool_modes": True},
        "dedupe": "kpl_leaderboard_snapshot:{run_key}",
    },
    {
        "task_id": "kpl_plate_strength",
        "task_name": "KPL Featured Plate Strength",
        "task_description": "Refresh KPL featured plate strength during trading time so non-limit-up primary themes follow the strongest current section.",
        "task_kind": "kpl_plate_strength",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 37,
        "timeout": 90,
        "payload": {"limit": 100, "timeout": 8},
        "dedupe": "kpl_plate_strength:{minute_key}",
    },
    {
        "task_id": "kpl_plate_details",
        "task_name": "KPL Featured Plate Details",
        "task_description": "Collect clicked-detail data for top 5 KPL featured plates, including sub-plates and plate-level explosion reason text when available.",
        "task_kind": "kpl_plate_details",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 38,
        "timeout": 300,
        "payload": {"limit": 5, "timeout": 8, "pause": 0.05},
        "dedupe": "kpl_plate_details:{run_key}",
    },
    {
        "task_id": "kpl_market_capacity",
        "task_name": "KPL Market Capacity Forecast",
        "task_description": "Manual fallback only. Market overview now collects KPL predicted turnover inside market_width_snapshot for synchronized timestamps.",
        "task_kind": "kpl_market_capacity",
        "task_type": "hot",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 36,
        "timeout": 90,
        "payload": {"timeout": 8, "market_type": 0},
        "dedupe": "kpl_market_capacity:{minute_key}",
    },
    {
        "task_id": "research_pool_theme_members",
        "task_name": "Research Pool Theme Members",
        "task_description": "Materialize research-pool stocks mapped to THS F10 concept explanations and today's headline theme dimension.",
        "task_kind": "research_pool_theme_members",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 46,
        "timeout": 180,
        "payload": {"force": True},
        "dedupe": "research_pool_theme_members:{run_key}",
    },
    {
        "task_id": "effective_facts",
        "task_name": "Effective Facts",
        "task_description": "Build filtered effective fact layer for evidence cache, UI, and model payloads.",
        "task_kind": "effective_facts",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 49,
        "timeout": 300,
        "payload": {"research_pool_only": True},
        "dedupe": "effective_facts:{run_key}",
    },
    {
        "task_id": "async_evidence_source_sync",
        "task_name": "Async Evidence Source Sync",
        "task_description": "Refresh per-stock evidence fingerprints and enqueue only changed stocks for model analysis.",
        "task_kind": "async_evidence_source_sync",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 46,
        "timeout": 120,
        "payload": {"limit": 50, "per_kind_limit": 8, "model_config": "default", "timeout": 30, "fallback_without_model": True, "research_pool_only": True},
        "dedupe": "async_evidence_source_sync:{minute_key}",
    },
    {
        "task_id": "root_evidence_cache_dirty",
        "task_name": "Root Evidence Cache Dirty",
        "task_description": "Consume changed stock root evidence rows and refresh affected stock cache only.",
        "task_kind": "root_evidence_cache_dirty",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 30,
        "priority": 45,
        "timeout": 120,
        "payload": {"limit": 50, "research_pool_only": True},
        "dedupe": "root_evidence_cache_dirty:{minute_key}",
    },
    {
        "task_id": "realtime_mover_scan",
        "task_name": "Realtime Mover Scan",
        "task_description": "Scan research-pool quotes during trading, persist scan_runs/windows, and provide inputs for event_engine.",
        "task_kind": "realtime_mover_scan",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 34,
        "timeout": 90,
        "payload": {
            "research_pool_only": True,
            "scan_interval": 5,
            "window_seconds": 15,
            "scan_top": 20,
            "aggregate_top": 5,
            "min_speed_signal": 1.5,
            "min_single_speed": 1.5,
            "min_15s_speed": 1.5,
            "min_accepted_scans": 1,
            "scan_timeout": 90,
            "no_evidence": True,
            "no_file_output": True,
        },
        "dedupe": "realtime_mover_scan:{minute_key}",
    },
    {
        "task_id": "event_engine",
        "task_name": "Event Evidence Engine",
        "task_description": "Build normalized move events, derived signals, and event-level evidence.",
        "task_kind": "event_engine",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 44,
        "timeout": 120,
        "payload": {"limit": 800},
        "dedupe": "event_engine:{minute_key}",
    },
    {
        "task_id": "async_evidence_summary",
        "task_name": "Async Evidence Summary",
        "task_description": "Consume pending changed evidence rows and summarize them with model analysis.",
        "task_kind": "async_evidence_summary",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 300,
        "priority": 48,
        "timeout": 300,
        "payload": {"limit": 10, "per_kind_limit": 8, "model_config": "default", "timeout": 60, "dirty_only": True, "fallback_without_model": True, "research_pool_only": True},
        "dedupe": "async_evidence_summary:{minute_key}",
    },
    {
        "task_id": "stock_move_judgements",
        "task_name": "Stock Move Judgements",
        "task_description": "Build concise move explanation and sustainability judgement from tape, anchor, role, and async evidence.",
        "task_kind": "stock_move_judgements",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 49,
        "timeout": 120,
        "payload": {"scan_top": 20, "window_top": 5, "limit": 500, "latest_only": True, "research_pool_only": True},
        "dedupe": "stock_move_judgements:{minute_key}",
    },
    {
        "task_id": "anchor_realtime_roles",
        "task_name": "热数据：题材锚点全池角色",
        "task_description": "交易时段按题材锚点股票池计算全池领涨/中军，供实时扫描和Web展示引用。",
        "task_kind": "anchor_realtime_roles",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 35,
        "timeout": 90,
        "payload": {"levels": "strong,medium", "medium_cap": 240, "min_members": 2, "batch_size": 80, "tdx_timeout": 3, "trading_only": True},
        "dedupe": "anchor_realtime_roles:{minute_key}",
    },
    {
        "task_id": "headline_theme_role_evidence",
        "task_name": "Headline Theme Role Evidence",
        "task_description": "Precompute mostly-static headline-theme concept explanation payloads for evidence detail.",
        "task_kind": "headline_theme_role_evidence",
        "task_type": "warm",
        "enabled": 0,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 47,
        "timeout": 90,
        "payload": {"force": True},
        "dedupe": "headline_theme_role_evidence:{run_key}",
    },
    {
        "task_id": "market_width_snapshot",
        "task_name": "市场概览快照",
        "task_description": "盘中分钟级采集全市场涨跌家数、3%强弱家数和成交额Top50。",
        "task_kind": "market_width_snapshot",
        "task_type": "hot",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 60,
        "priority": 36,
        "timeout": 90,
        "payload": {"source": "tdx", "include_bj": False, "include_st": False, "batch_size": 80, "tdx_timeout": 3},
        "dedupe": "market_width_snapshot:{minute_key}",
    },
    {
        "task_id": "market_width_daily_close",
        "task_name": "Market Width Daily Close",
        "task_description": "After close, refresh daily bars and build confirmed close market-width snapshot for five-day structure.",
        "task_kind": "market_width_daily_close",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 44,
        "timeout": 1800,
        "payload": {
            "min_rows": 4800,
            "workers": 4,
            "batch_size": 300,
            "wait_minutes": 20,
            "retry_seconds": 120,
            "refresh_bars": True,
        },
        "dedupe": "market_width_daily_close:{run_key}",
    },
    {
        "task_id": "auction_candidates",
        "task_name": "09:15竞价封单全量",
        "task_description": "09:15-09:25持续刷新集合竞价，每分钟保存所有涨停/跌停封单，最终候选保留涨停封单Top3。",
        "task_kind": "auction_candidates",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 58,
        "timeout": 900,
        "payload": {
            "limit": 3,
            "min_auction_pct": 0.0,
            "min_auction_amount": 0,
            "theme_limit": 20,
            "timeout": 3,
            "batch_size": 80,
            "minute_analysis": True,
            "loop_until": "09:25",
            "minute_interval": 60,
            "minute_top": 20,
            "seal_top": 0,
        },
        "dedupe": "auction_candidates:{run_key}",
    },
    {
        "task_id": "auction_trend_summary",
        "task_name": "09:26 auction trend summary",
        "task_description": "Compress 09:20-09:25 auction minute radar into per-stock trend score, label, key points, and action hint.",
        "task_kind": "auction_trend_summary",
        "task_type": "warm",
        "enabled": 1,
        "schedule_type": "interval",
        "interval": 86400,
        "priority": 59,
        "timeout": 180,
        "payload": {"limit": 80},
        "dedupe": "auction_trend_summary:{run_key}",
    },
]


SCHEDULED_TASKS = [
    task
    for task in SCHEDULED_TASKS
    if str(task.get("task_id") or "") not in DEPRECATED_TASK_IDS
    and not any(str(task.get("task_id") or "").startswith(prefix) for prefix in ARCHIVED_TASK_PREFIXES)
]


def _next_trade_day_timestamp(time_text: str) -> str:
    return (
        "TIMESTAMP("
        "CASE "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 1 DAY)) = 5 THEN DATE_ADD(CURDATE(), INTERVAL 3 DAY) "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 1 DAY)) = 6 THEN DATE_ADD(CURDATE(), INTERVAL 2 DAY) "
        "ELSE DATE_ADD(CURDATE(), INTERVAL 1 DAY) END, "
        f"'{time_text}')"
    )


def _today_or_next_trade_day_timestamp(time_text: str) -> str:
    next_day = _next_trade_day_timestamp(time_text)
    return (
        "CASE "
        f"WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '{time_text}') "
        f"WHEN TIME(NOW()) < '{time_text}' THEN TIMESTAMP(CURDATE(), '{time_text}') "
        f"ELSE {next_day} END"
    )


def _next_pre_trade_night_timestamp(time_text: str) -> str:
    return (
        "TIMESTAMP("
        "DATE_ADD(CURDATE(), INTERVAL "
        "CASE "
        f"WHEN TIME(NOW()) < '{time_text}' AND WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 1 DAY)) < 5 THEN 0 "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 2 DAY)) < 5 THEN 1 "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 3 DAY)) < 5 THEN 2 "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 4 DAY)) < 5 THEN 3 "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 5 DAY)) < 5 THEN 4 "
        "WHEN WEEKDAY(DATE_ADD(CURDATE(), INTERVAL 6 DAY)) < 5 THEN 5 "
        "ELSE 6 END DAY), "
        f"'{time_text}')"
    )


NEXT_RUN_SQL_BY_TASK = {
    "ths_root_extended_items": _today_or_next_trade_day_timestamp("22:00:00"),
    "pre_trade_night_evidence_prepare": _next_pre_trade_night_timestamp("22:30:00"),
    "morning_market_news": _today_or_next_trade_day_timestamp("07:00:00"),
    "daily_market_themes": _today_or_next_trade_day_timestamp("07:02:00"),
    "morning_reference_post": _today_or_next_trade_day_timestamp("07:05:00"),
    "scheduled_task_health_check": _today_or_next_trade_day_timestamp("08:00:00"),
    "ths_market_after_close_summary": _today_or_next_trade_day_timestamp("16:10:00"),
    "ths_hot_concepts": _today_or_next_trade_day_timestamp("15:30:00"),
    "ths_homepage_headline_themes": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '15:55:00') "
        "WHEN TIME(NOW()) < '15:55:00' THEN TIMESTAMP(CURDATE(), '15:55:00') "
        f"ELSE {_next_trade_day_timestamp('15:55:00')} END"
    ),
    "ths_homepage_headline_freeze": _today_or_next_trade_day_timestamp("16:05:00"),
    "eastmoney_limit_up_pool": _today_or_next_trade_day_timestamp("15:25:00"),
    "research_pool_snapshot": _today_or_next_trade_day_timestamp("16:25:00"),
    "kpl_limit_up_reasons": _today_or_next_trade_day_timestamp("20:05:00"),
    "kpl_replay_limit_themes": _today_or_next_trade_day_timestamp("20:15:00"),
    "kpl_stock_featured_sections": _today_or_next_trade_day_timestamp("20:20:00"),
    "post_close_leaderboard_snapshot": _today_or_next_trade_day_timestamp("20:35:00"),
    "kpl_leaderboard_snapshot": _today_or_next_trade_day_timestamp("20:45:00"),
    "kpl_plate_strength": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:30:00') "
        "WHEN TIME(NOW()) < '09:30:00' THEN TIMESTAMP(CURDATE(), '09:30:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:30:00')} END"
    ),
    "kpl_plate_details": _today_or_next_trade_day_timestamp("20:25:00"),
    "kpl_market_capacity": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:30:00') "
        "WHEN TIME(NOW()) < '09:30:00' THEN TIMESTAMP(CURDATE(), '09:30:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:30:00')} END"
    ),
    "ths_stock_concepts": _today_or_next_trade_day_timestamp("16:30:00"),
    "research_pool_theme_members": _today_or_next_trade_day_timestamp("09:15:00"),
    "headline_theme_role_evidence": _today_or_next_trade_day_timestamp("09:16:00"),
    "effective_facts": _today_or_next_trade_day_timestamp("23:05:00"),
    "async_evidence_source_sync": "IF(TIME(NOW()) < '09:35:00', TIMESTAMP(CURDATE(), '09:35:00'), NOW(3))",
    "root_evidence_cache_dirty": "IF(TIME(NOW()) < '09:35:00', TIMESTAMP(CURDATE(), '09:35:00'), NOW(3))",
    "realtime_mover_scan": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:30:00') "
        "WHEN TIME(NOW()) < '09:30:00' THEN TIMESTAMP(CURDATE(), '09:30:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:30:00')} END"
    ),
    "event_engine": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:35:00') "
        "WHEN TIME(NOW()) < '09:35:00' THEN TIMESTAMP(CURDATE(), '09:35:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:35:00')} END"
    ),
    "async_evidence_summary": "IF(TIME(NOW()) < '09:35:00', TIMESTAMP(CURDATE(), '09:35:00'), NOW(3))",
    "stock_move_judgements": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:35:00') "
        "WHEN TIME(NOW()) < '09:35:00' THEN TIMESTAMP(CURDATE(), '09:35:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:35:00')} END"
    ),
    "anchor_realtime_roles": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:30:00') "
        "WHEN TIME(NOW()) < '09:30:00' THEN TIMESTAMP(CURDATE(), '09:30:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:30:00')} END"
    ),
    "market_width_snapshot": (
        "CASE "
        "WHEN WEEKDAY(CURDATE()) >= 5 THEN TIMESTAMP(DATE_ADD(CURDATE(), INTERVAL 7 - WEEKDAY(CURDATE()) DAY), '09:30:00') "
        "WHEN TIME(NOW()) < '09:30:00' THEN TIMESTAMP(CURDATE(), '09:30:00') "
        "WHEN TIME(NOW()) < '11:29:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        "WHEN TIME(NOW()) < '13:00:00' THEN TIMESTAMP(CURDATE(), '13:00:00') "
        "WHEN TIME(NOW()) < '14:59:00' THEN DATE_ADD(NOW(3), INTERVAL 60 SECOND) "
        f"ELSE {_next_trade_day_timestamp('09:30:00')} END"
    ),
    "market_width_daily_close": _today_or_next_trade_day_timestamp("16:05:00"),
    "auction_candidates": _today_or_next_trade_day_timestamp("09:15:00"),
    "auction_trend_summary": _today_or_next_trade_day_timestamp("09:26:00"),
}


def next_run_sql_for_task(task_id: str) -> str:
    return NEXT_RUN_SQL_BY_TASK.get(task_id, "NULL")
