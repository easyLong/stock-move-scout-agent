from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DataSource:
    source_id: str
    tier: str
    owner_layer: str
    task_kinds: tuple[str, ...]
    scripts: tuple[str, ...]
    output_tables: tuple[str, ...]
    refresh: str
    description: str
    batched_task_kinds: tuple[str, ...] = ()


DATA_SOURCES: dict[str, DataSource] = {
    "tdx_market": DataSource(
        source_id="tdx_market",
        tier="hot",
        owner_layer="market_scan",
        task_kinds=("realtime_mover_scan",),
        scripts=("tdx_mover_watcher.py", "windowed_stock_scout_agent.py"),
        output_tables=("scan_runs", "scan_movers", "windows", "window_movers"),
        refresh="trading_time_5s_scan_inside_minute_task",
        description="通达信实时快照。盘中按研究池扫描，生成异动点和开盘至今窗口强度。",
    ),
    "ths_root": DataSource(
        source_id="ths_root",
        tier="cold",
        owner_layer="company_profile",
        task_kinds=("cold_company_profile", "ths_root_extended_items"),
        batched_task_kinds=("cold_company_profile", "ths_root_extended_items"),
        scripts=("run_configured_evidence_refresh.py",),
        output_tables=("stock_company_profiles", "stock_ths_root_items"),
        refresh="manual_profile_and_daily_research_pool_incremental",
        description="同花顺 F10 根页面。当前主链路只使用公司画像和近期重要事件。",
    ),
    "market_news": DataSource(
        source_id="market_news",
        tier="warm",
        owner_layer="morning_context",
        task_kinds=("morning_market_news", "daily_market_themes", "morning_reference_post"),
        scripts=("collect_market_news_digest.py", "build_daily_market_themes.py", "build_morning_reference_post.py"),
        output_tables=("market_news_items", "daily_market_themes"),
        refresh="daily_08_30_08_32_08_35",
        description="财联社、华尔街见闻等盘前消息，以及由消息生成的早参主题和帖子。",
    ),
    "ops_health": DataSource(
        source_id="ops_health",
        tier="warm",
        owner_layer="scheduler_ops",
        task_kinds=("scheduled_task_health_check",),
        scripts=("check_scheduled_task_health.py",),
        output_tables=("scheduled_task_health_checks",),
        refresh="daily_08_00",
        description="调度健康检查。检查到点未跑、失败和超时任务。",
    ),
    "ths_after_close_summary": DataSource(
        source_id="ths_after_close_summary",
        tier="warm",
        owner_layer="morning_context",
        task_kinds=("ths_market_after_close_summary",),
        scripts=("collect_ths_market_after_close_summary.py",),
        output_tables=("ths_market_after_close_summaries",),
        refresh="daily_after_close_16_10",
        description="同花顺盘后市场小结，作为次日早参的前一交易日背景。",
    ),
    "ths_theme": DataSource(
        source_id="ths_theme",
        tier="cold",
        owner_layer="theme_anchor",
        task_kinds=(
            "ths_hot_concepts",
            "ths_homepage_headline_themes",
            "ths_homepage_headline_freeze",
            "ths_stock_concepts",
        ),
        batched_task_kinds=("ths_stock_concepts",),
        scripts=(
            "collect_ths_hot_concepts.py",
            "collect_ths_homepage_headline_themes.py",
            "freeze_ths_homepage_headline_snapshot.py",
            "collect_ths_stock_concepts.py",
        ),
        output_tables=(
            "ths_hot_concept_events",
            "ths_hot_concept_members",
            "ths_homepage_headline_themes",
            "ths_homepage_headline_theme_members",
            "ths_stock_concept_explanations",
        ),
        refresh="daily_after_close",
        description="同花顺题材数据。首页头条题材盘后冻结，个股概念解释用于趋势票解释和同花顺领头羊关系。",
    ),
    "eastmoney_limit_up": DataSource(
        source_id="eastmoney_limit_up",
        tier="warm",
        owner_layer="post_close_confirmation",
        task_kinds=("eastmoney_limit_up_pool",),
        scripts=("collect_eastmoney_limit_up_pool.py",),
        output_tables=("limit_up_pool_items",),
        refresh="daily_15_25",
        description="东方财富涨停池。用于研究池、封板维度、连板辨识度和领头羊得分。",
    ),
    "kpl_theme": DataSource(
        source_id="kpl_theme",
        tier="warm",
        owner_layer="kpl_leaderboard",
        task_kinds=(
            "kpl_limit_up_reasons",
            "kpl_replay_limit_themes",
            "kpl_stock_featured_sections",
            "kpl_plate_strength",
            "kpl_market_capacity",
        ),
        scripts=(
            "collect_kpl_limit_up_reasons.py",
            "collect_kpl_replay_limit_themes.py",
            "collect_kpl_stock_featured_sections.py",
            "collect_kpl_plate_strength.py",
            "collect_kpl_market_capacity.py",
        ),
        output_tables=(
            "kpl_stock_limit_up_reasons",
            "kpl_replay_limit_theme_groups",
            "kpl_replay_limit_theme_stocks",
            "kpl_stock_featured_sections",
            "kpl_plate_featured_strengths",
            "kpl_market_capacity_snapshots",
            "kpl_market_capacity_trends",
        ),
        refresh="trading_time_and_post_close_after_20_00",
        description="开盘啦精选板块、复盘啦涨停原因、个股精选板块和预测量能。",
    ),
    "research_pool_snapshot": DataSource(
        source_id="research_pool_snapshot",
        tier="warm",
        owner_layer="research_scope",
        task_kinds=("research_pool_snapshot", "research_pool_theme_members", "headline_theme_role_evidence"),
        scripts=("build_research_pool_snapshot.py", "build_research_pool_theme_members.py", "build_headline_theme_role_evidence.py"),
        output_tables=("research_pool_snapshots", "research_pool_items", "research_pool_theme_members", "stock_headline_theme_role_evidence"),
        refresh="daily_after_close_16_25_and_inside_evidence_pipeline",
        description="每日研究池快照。主题成员和题材角色不独立调度，作为证据底稿 pipeline 的内部步骤刷新。",
    ),
    "leaderboard_snapshot": DataSource(
        source_id="leaderboard_snapshot",
        tier="warm",
        owner_layer="leaderboard",
        task_kinds=("post_close_leaderboard_snapshot", "kpl_leaderboard_snapshot"),
        scripts=("build_leaderboard_snapshot.py",),
        output_tables=("leaderboard_snapshots",),
        refresh="daily_after_close_20_45_21_00",
        description="收盘确认版领头羊快照。同花顺领头羊和开盘啦领头羊都从这里给页面读取。",
    ),
    "event_engine": DataSource(
        source_id="event_engine",
        tier="hot",
        owner_layer="event_evidence_engine",
        task_kinds=("event_engine",),
        scripts=("build_event_engine.py",),
        output_tables=("stock_move_events", "derived_signals", "stock_move_evidence"),
        refresh="trading_time_minute",
        description="标准化盘中异动事件，生成个股、题材和市场维度的衍生信号。",
    ),
    "effective_facts": DataSource(
        source_id="effective_facts",
        tier="warm",
        owner_layer="effective_fact_layer",
        task_kinds=("daily_root_evidence_pipeline", "pre_trade_night_evidence_prepare", "effective_facts", "async_evidence_summary", "root_evidence_cache_dirty"),
        scripts=("run_daily_root_evidence_pipeline.py", "build_effective_facts.py", "summarize_async_evidence.py", "refresh_root_evidence_cache.py"),
        output_tables=("stock_effective_facts", "async_evidence_summaries", "stock_root_evidence_cache"),
        refresh="daily_22_30_for_next_trade_day",
        description="证据底稿 pipeline。有效事实、模型/兜底总结、根证据缓存的独立任务已禁用，当前由 22:30 pipeline 批量调用。",
    ),
    "auction_market": DataSource(
        source_id="auction_market",
        tier="hot",
        owner_layer="auction_context",
        task_kinds=("auction_candidates",),
        scripts=("build_auction_candidates.py",),
        output_tables=("auction_candidates", "auction_minute_analysis"),
        refresh="09_15_to_09_25",
        description="集合竞价候选。只保留涨停且封单额最大的 Top3。",
    ),
    "market_width": DataSource(
        source_id="market_width",
        tier="hot",
        owner_layer="market_width",
        task_kinds=("market_width_snapshot", "market_width_daily_close"),
        scripts=("collect_market_width_snapshot.py", "collect_market_width_daily_close.py"),
        output_tables=("market_width_snapshots", "market_width_amount_top50", "kpl_market_capacity_snapshots", "kpl_market_capacity_trends"),
        refresh="trading_time_minute_and_daily_close",
        description="市场概览。盘中统计全市场、成交额 Top50、研究池宽度，并同步开盘啦预测量能。",
    ),
    "anchor_roles": DataSource(
        source_id="anchor_roles",
        tier="hot",
        owner_layer="theme_realtime_roles",
        task_kinds=("anchor_realtime_roles", "stock_move_judgements"),
        scripts=("build_anchor_realtime_roles.py", "build_stock_move_judgements.py"),
        output_tables=("anchor_realtime_roles", "stock_move_judgements"),
        refresh="trading_time_minute",
        description="盘中题材角色和异动判断，服务异动情报流和证据详情。",
    ),
}


TASK_KIND_TO_SOURCE_ID = {
    task_kind: source_id
    for source_id, source in DATA_SOURCES.items()
    for task_kind in source.task_kinds
}


BATCHED_SOURCE_TASK_KINDS = frozenset(
    task_kind
    for source in DATA_SOURCES.values()
    for task_kind in source.batched_task_kinds
)


def source_for_task_kind(task_kind: str) -> DataSource | None:
    source_id = TASK_KIND_TO_SOURCE_ID.get(task_kind)
    return DATA_SOURCES.get(source_id or "")


def is_batched_source_task(task_kind: str) -> bool:
    return task_kind in BATCHED_SOURCE_TASK_KINDS
