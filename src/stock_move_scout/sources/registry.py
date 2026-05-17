from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDefinition:
    source_table: str
    source_type: str
    source_generation: str
    default_availability: str
    default_freshness: str
    update_cycle: str
    data_date_policy: str
    mutable_intraday: bool = False
    blocks_realtime: bool = False


SOURCE_REGISTRY: dict[str, SourceDefinition] = {
    "scan_stock_roles": SourceDefinition(
        source_table="scan_stock_roles",
        source_type="market",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="scan_loop",
        data_date_policy="event_day",
        mutable_intraday=True,
    ),
    "window_stock_roles": SourceDefinition(
        source_table="window_stock_roles",
        source_type="market",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="window_loop",
        data_date_policy="event_day",
        mutable_intraday=True,
    ),
    "stock_move_judgements": SourceDefinition(
        source_table="stock_move_judgements",
        source_type="model",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="dirty_or_minute",
        data_date_policy="event_day",
        mutable_intraday=True,
    ),
    "stock_move_events": SourceDefinition(
        source_table="stock_move_events",
        source_type="move_event",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="event_engine_minute",
        data_date_policy="event_day",
        mutable_intraday=True,
    ),
    "derived_signals": SourceDefinition(
        source_table="derived_signals",
        source_type="signal",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="event_engine_minute",
        data_date_policy="signal_time",
        mutable_intraday=True,
    ),
    "stock_move_evidence": SourceDefinition(
        source_table="stock_move_evidence",
        source_type="event_evidence",
        source_generation="intraday",
        default_availability="intraday",
        default_freshness="live_market",
        update_cycle="event_engine_minute_or_dirty",
        data_date_policy="event_day",
        mutable_intraday=True,
    ),
    "stock_ths_root_items": SourceDefinition(
        source_table="stock_ths_root_items",
        source_type="event",
        source_generation="precomputed",
        default_availability="cached_readable",
        default_freshness="historical",
        update_cycle="daily_or_manual",
        data_date_policy="evidence_date",
    ),
    "stock_root_evidence_cache": SourceDefinition(
        source_table="stock_root_evidence_cache",
        source_type="announcement",
        source_generation="precomputed",
        default_availability="cached_readable",
        default_freshness="historical",
        update_cycle="dirty_or_manual",
        data_date_policy="evidence_date",
    ),
    "stock_effective_facts": SourceDefinition(
        source_table="stock_effective_facts",
        source_type="effective_fact",
        source_generation="precomputed",
        default_availability="cached_readable",
        default_freshness="today_update",
        update_cycle="after_source_or_dirty",
        data_date_policy="fact_date",
    ),
    "async_evidence_summaries": SourceDefinition(
        source_table="async_evidence_summaries",
        source_type="fact_card",
        source_generation="async",
        default_availability="async_supplement",
        default_freshness="today_update",
        update_cycle="dirty_or_manual",
        data_date_policy="analysis_trade_date",
    ),
    "evidence_layers": SourceDefinition(
        source_table="evidence_layers",
        source_type="model",
        source_generation="async",
        default_availability="async_supplement",
        default_freshness="today_update",
        update_cycle="on_demand",
        data_date_policy="event_day",
    ),
    "stock_company_profiles": SourceDefinition(
        source_table="stock_company_profiles",
        source_type="profile",
        source_generation="precomputed",
        default_availability="cached_readable",
        default_freshness="historical",
        update_cycle="manual_or_batch",
        data_date_policy="latest_profile",
    ),
}


def source_definition(source_table: str) -> SourceDefinition | None:
    return SOURCE_REGISTRY.get(str(source_table or "").strip())


def source_contract(source_table: str) -> dict[str, str | bool]:
    definition = source_definition(source_table)
    if not definition:
        return {}
    return {
        "source_table": definition.source_table,
        "source_type": definition.source_type,
        "source_generation": definition.source_generation,
        "availability": definition.default_availability,
        "freshness": definition.default_freshness,
        "update_cycle": definition.update_cycle,
        "data_date_policy": definition.data_date_policy,
        "mutable_intraday": definition.mutable_intraday,
        "blocks_realtime": definition.blocks_realtime,
    }


__all__ = ["SOURCE_REGISTRY", "SourceDefinition", "source_contract", "source_definition"]
