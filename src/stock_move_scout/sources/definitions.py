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
        task_kinds=("build_cold_universe",),
        scripts=("build_stock_scout_universe.py", "tdx_mover_watcher.py", "windowed_stock_scout_agent.py"),
        output_tables=("stocks", "scan_runs", "scan_movers", "windows", "window_movers"),
        refresh="trading_time_15s_scan",
        description="通达信行情、股票池、15秒异动扫描和5分钟窗口聚合。",
    ),
    "ths_root": DataSource(
        source_id="ths_root",
        tier="cold",
        owner_layer="company_profile",
        task_kinds=("cold_company_profile", "cold_company_profile_batch", "ths_root_extended_items"),
        batched_task_kinds=("cold_company_profile", "cold_company_profile_batch", "ths_root_extended_items"),
        scripts=("run_configured_evidence_refresh.py",),
        output_tables=("stock_company_profiles", "stock_ths_root_items"),
        refresh="manual_full_profile_and_daily_research_pool_incremental",
        description="同花顺F10根页面，公司画像、亮点和重要事件。",
    ),
    "market_news": DataSource(
        source_id="market_news",
        tier="warm",
        owner_layer="morning_context",
        task_kinds=("morning_market_news", "daily_market_themes", "morning_reference_post"),
        scripts=("collect_market_news_digest.py", "build_daily_market_themes.py", "build_morning_reference_post.py"),
        output_tables=("market_news_items", "daily_market_themes"),
        refresh="daily_08_30_08_32_08_35",
        description="财联社、华尔街见闻盘前重要资讯，以及由资讯加工出的每日主题。",
    ),
    "ths_after_close_summary": DataSource(
        source_id="ths_after_close_summary",
        tier="warm",
        owner_layer="morning_context",
        task_kinds=("ths_market_after_close_summary",),
        scripts=("collect_ths_market_after_close_summary.py",),
        output_tables=("ths_market_after_close_summaries",),
        refresh="daily_after_close_15_45",
        description="THS after-close market review summary for previous-day morning context.",
    ),
    "ths_theme": DataSource(
        source_id="ths_theme",
        tier="cold",
        owner_layer="theme_anchor",
        task_kinds=("ths_hot_concepts", "ths_homepage_headline_themes", "eastmoney_limit_up_pool", "ths_stock_concepts"),
        batched_task_kinds=("ths_stock_concepts",),
        scripts=(
            "collect_ths_hot_concepts.py",
            "collect_ths_homepage_headline_themes.py",
            "collect_eastmoney_limit_up_pool.py",
            "collect_ths_stock_concepts.py",
        ),
        output_tables=(
            "ths_hot_concept_events",
            "ths_hot_concept_members",
            "ths_homepage_headline_themes",
            "ths_homepage_headline_theme_members",
            "limit_up_pool_items",
            "ths_stock_concept_explanations",
            "active_market_anchors",
            "active_market_anchor_members",
            "active_market_anchor_relations",
            "active_anchor_match_candidates",
        ),
        refresh="daily_research_pool_incremental_after_close",
        description="同花顺主题数据、涨停池、个股概念解释；题材理由直接读取个股F10概念解释。",
    ),
    "research_pool_snapshot": DataSource(
        source_id="research_pool_snapshot",
        tier="warm",
        owner_layer="research_scope",
        task_kinds=("research_pool_snapshot", "research_pool_theme_members", "headline_theme_role_evidence"),
        scripts=("build_research_pool_snapshot.py", "build_research_pool_theme_members.py", "build_headline_theme_role_evidence.py"),
        output_tables=("research_pool_snapshots", "research_pool_items", "research_pool_theme_members", "stock_headline_theme_role_evidence"),
        refresh="research_pool_daily_after_close_theme_members_daily_preopen",
        description="Materialized daily research pool and its THS concept theme-member layer used by incremental collectors and hot scanners.",
    ),
    "event_engine": DataSource(
        source_id="event_engine",
        tier="hot",
        owner_layer="event_evidence_engine",
        task_kinds=("event_engine",),
        scripts=("build_event_engine.py",),
        output_tables=("stock_move_events", "derived_signals", "stock_move_evidence"),
        refresh="trading_time_minute_or_dirty",
        description="Normalize move events, derive stock/theme/market signals, and match event-level evidence.",
    ),
    "effective_facts": DataSource(
        source_id="effective_facts",
        tier="warm",
        owner_layer="effective_fact_layer",
        task_kinds=("daily_root_evidence_pipeline", "effective_facts"),
        scripts=("run_daily_root_evidence_pipeline.py", "build_effective_facts.py", "summarize_async_evidence.py", "refresh_root_evidence_cache.py"),
        output_tables=("stock_effective_facts", "async_evidence_summaries", "stock_root_evidence_cache"),
        refresh="daily_after_close_once_for_research_pool",
        description="Daily research-pool root evidence pipeline: THS F10 important events -> effective facts -> one summary pass -> cached UI reads.",
    ),
    "auction_market": DataSource(
        source_id="auction_market",
        tier="hot",
        owner_layer="auction_context",
        task_kinds=("auction_candidates",),
        scripts=("build_auction_candidates.py",),
        output_tables=("auction_candidates", "auction_minute_analysis", "auction_trend_summary"),
        refresh="09_15_to_09_25",
        description="集合竞价09:20-09:25分钟雷达和09:26趋势总结。",
    ),
    "market_width": DataSource(
        source_id="market_width",
        tier="hot",
        owner_layer="market_width",
        task_kinds=("market_width_snapshot", "market_width_daily_close"),
        scripts=("collect_market_width_snapshot.py", "collect_market_width_daily_close.py"),
        output_tables=("market_width_snapshots", "market_width_amount_top50"),
        refresh="trading_time_minute_and_daily_close",
        description="全市场涨跌宽度和成交额Top50的分钟级快照。",
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
