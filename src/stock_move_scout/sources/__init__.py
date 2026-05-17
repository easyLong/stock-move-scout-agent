"""Data source registry and command builders."""

from .commands import build_source_command
from .auction_candidates import AuctionCandidateConfig, AuctionCandidateResult, AuctionCandidateService
from .auction_storage import import_auction_candidate_rows, import_auction_minute_analysis_rows
from .definitions import DATA_SOURCES, BATCHED_SOURCE_TASK_KINDS, DataSource, is_batched_source_task, source_for_task_kind
from .market_news import MarketNewsItem, read_market_news_window
from .market_news_storage import import_market_news_json, import_market_news_rows
from .market_themes import MarketTheme, match_market_themes, read_market_themes
from .quotes import (
    DEFAULT_TDX_SERVERS,
    QuoteProviderConfig,
    QuoteSnapshot,
    QuoteSymbol,
    TdxQuoteProvider,
    append_shanghai_index,
    filter_symbols_by_codes,
    parse_tdx_server,
    quote_key,
    shanghai_index_symbol,
)
from .quote_rows import build_quote_rows
from .tdx_cache import load_concept_map, load_industry_map, load_tdx_label_cache
from .registry import SOURCE_REGISTRY, SourceDefinition, source_contract, source_definition

__all__ = [
    "BATCHED_SOURCE_TASK_KINDS",
    "AuctionCandidateConfig",
    "AuctionCandidateResult",
    "AuctionCandidateService",
    "DATA_SOURCES",
    "SOURCE_REGISTRY",
    "DataSource",
    "MarketTheme",
    "MarketNewsItem",
    "DEFAULT_TDX_SERVERS",
    "QuoteProviderConfig",
    "QuoteSnapshot",
    "QuoteSymbol",
    "SourceDefinition",
    "TdxQuoteProvider",
    "append_shanghai_index",
    "build_quote_rows",
    "build_source_command",
    "filter_symbols_by_codes",
    "import_auction_candidate_rows",
    "import_auction_minute_analysis_rows",
    "import_market_news_json",
    "import_market_news_rows",
    "is_batched_source_task",
    "load_concept_map",
    "load_industry_map",
    "load_tdx_label_cache",
    "match_market_themes",
    "parse_tdx_server",
    "quote_key",
    "read_market_themes",
    "read_market_news_window",
    "source_contract",
    "source_definition",
    "source_for_task_kind",
    "shanghai_index_symbol",
]
