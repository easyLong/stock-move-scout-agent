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
        refresh="manual_profile_and_nightly_extended_items",
        description="同花顺F10根页面，公司画像、亮点、重要事件、公告和题材要点。",
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
    "ths_theme": DataSource(
        source_id="ths_theme",
        tier="cold",
        owner_layer="theme_anchor",
        task_kinds=("ths_hot_concepts", "ths_limit_up_review", "ths_stock_concepts", "stock_theme_reason_bank"),
        batched_task_kinds=("ths_stock_concepts",),
        scripts=(
            "collect_ths_hot_concepts.py",
            "collect_ths_limit_up_review.py",
            "collect_ths_stock_concepts.py",
            "rebuild_stock_theme_reason_bank.py",
        ),
        output_tables=(
            "ths_hot_concept_events",
            "ths_hot_concept_members",
            "ths_limit_up_review_items",
            "ths_stock_concept_explanations",
            "stock_theme_reason_bank",
            "active_market_anchors",
            "active_market_anchor_members",
            "active_market_anchor_relations",
            "active_anchor_match_candidates",
        ),
        refresh="daily_after_close",
        description="同花顺今天炒什么、涨停复盘、个股概念解释和统一题材理由库。",
    ),
    "iwencai_period_rankings": DataSource(
        source_id="iwencai_period_rankings",
        tier="warm",
        owner_layer="strength_context",
        task_kinds=("iwencai_period_rankings",),
        scripts=("collect_iwencai_period_rankings.py",),
        output_tables=("stock_period_rankings",),
        refresh="daily_15_20",
        description="问财3/5/10日区间强度排名，用于题材内领头股和持续性判断。",
    ),
    "lhb_seat": DataSource(
        source_id="lhb_seat",
        tier="warm",
        owner_layer="funds_evidence",
        task_kinds=("lhb_seat_evidence",),
        scripts=("collect_lhb_seat_evidence.py",),
        output_tables=("stock_lhb_seat_evidence",),
        refresh="daily_15_35",
        description="龙虎榜席位结构，关注知名游资、机构、北向和净买入强度。",
    ),
    "announcement_effects": DataSource(
        source_id="announcement_effects",
        tier="warm",
        owner_layer="evidence_validation",
        task_kinds=("announcement_effects",),
        scripts=("build_announcement_effects.py",),
        output_tables=("stock_daily_bars", "stock_announcement_effects"),
        refresh="daily_after_ths_root",
        description="Derived market validation for announcements and important events from stock_ths_root_items.",
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
        task_kinds=("effective_facts",),
        scripts=("build_effective_facts.py",),
        output_tables=("stock_effective_facts",),
        refresh="after_lhb_rank_limitup_announcement_effects_or_dirty",
        description="Filter raw and derived facts into currently useful evidence facts for cache, UI, and model payloads.",
    ),
    "auction_market": DataSource(
        source_id="auction_market",
        tier="hot",
        owner_layer="auction_context",
        task_kinds=("auction_candidates", "auction_trend_summary"),
        scripts=("build_auction_candidates.py", "build_auction_trend_summary.py"),
        output_tables=("auction_candidates", "auction_minute_analysis", "auction_trend_summary"),
        refresh="09_20_to_09_26",
        description="集合竞价09:20-09:25分钟雷达和09:26趋势总结。",
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
