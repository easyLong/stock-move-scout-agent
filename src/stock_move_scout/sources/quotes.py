from __future__ import annotations

import csv
import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


DEFAULT_TDX_SERVERS: tuple[tuple[str, int], ...] = (
    ("110.41.2.72", 7709),
    ("110.41.147.114", 7709),
    ("101.33.225.16", 7709),
    ("124.223.163.242", 7709),
    ("122.51.120.217", 7709),
    ("119.97.185.59", 7709),
)

MAIN_A_PREFIXES = ("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689")
SHANGHAI_INDEX_MARKET = 1
SHANGHAI_INDEX_CODE = "000001"


@dataclass(frozen=True)
class QuoteSymbol:
    market: int
    code: str
    name: str = ""
    is_index: bool = False

    @property
    def key(self) -> str:
        return quote_key(self.market, self.code)


@dataclass(frozen=True)
class QuoteProviderConfig:
    servers: tuple[tuple[str, int], ...] = DEFAULT_TDX_SERVERS
    timeout: int = 3
    batch_size: int = 80
    batch_sleep_seconds: float = 0.03
    universe_sleep_seconds: float = 0.05


@dataclass(frozen=True)
class QuoteSnapshot:
    source: str
    server: str
    captured_at: datetime
    quotes: dict[str, dict[str, Any]]
    universe_count: int
    quote_count: int
    meta: dict[str, Any] = field(default_factory=dict)

    def rows(self) -> list[dict[str, Any]]:
        return list(self.quotes.values())


def quote_key(market: int | str, code: Any) -> str:
    return f"{int(market)}:{str(code or '').strip()}"


def parse_tdx_server(value: str) -> tuple[str, int]:
    ip, port = str(value).split(":", 1)
    return ip.strip(), int(port)


def is_main_a_share(market: int, code: str) -> bool:
    code = str(code or "").strip()
    if market == 0:
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == 1:
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    return False


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def load_universe_csv(path: Path) -> list[QuoteSymbol]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    symbols: list[QuoteSymbol] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        market = str(row.get("market") or "").strip()
        if not code or market not in {"0", "1"}:
            continue
        symbols.append(QuoteSymbol(int(market), code, str(row.get("name") or "").strip(), str(row.get("is_index") or "") == "1"))
    return symbols


def save_universe_csv(path: Path, symbols: Iterable[QuoteSymbol]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["market", "code", "name", "is_index"])
        writer.writeheader()
        for symbol in symbols:
            writer.writerow(
                {
                    "market": symbol.market,
                    "code": symbol.code,
                    "name": symbol.name,
                    "is_index": "1" if symbol.is_index else "",
                }
            )


def shanghai_index_symbol() -> QuoteSymbol:
    return QuoteSymbol(SHANGHAI_INDEX_MARKET, SHANGHAI_INDEX_CODE, "Shanghai Composite", True)


def append_shanghai_index(symbols: Iterable[QuoteSymbol]) -> list[QuoteSymbol]:
    out = list(symbols)
    keys = {symbol.key for symbol in out}
    index = shanghai_index_symbol()
    if index.key not in keys:
        out.append(index)
    return out


def filter_symbols_by_codes(symbols: Iterable[QuoteSymbol], codes: Iterable[str] | None) -> list[QuoteSymbol]:
    clean_codes = {str(code or "").strip() for code in (codes or []) if str(code or "").strip()}
    if not clean_codes:
        return list(symbols)
    return [symbol for symbol in symbols if symbol.code in clean_codes]


class TdxQuoteProvider:
    """Reusable Tongdaxin realtime quote provider.

    It owns the low-level connection, universe loading, quote batching, and
    basic normalization. Feature code should depend on this provider instead of
    importing scanner scripts.
    """

    def __init__(
        self,
        *,
        universe_csv: Path,
        config: QuoteProviderConfig | None = None,
    ) -> None:
        self.universe_csv = universe_csv
        self.config = config or QuoteProviderConfig()

    def connect(self) -> tuple[Any, str]:
        from pytdx.hq import TdxHq_API

        errors: list[str] = []
        for ip, port in self.config.servers:
            api = TdxHq_API(heartbeat=True, auto_retry=True)
            try:
                if api.connect(ip, port, time_out=int(self.config.timeout)):
                    return api, f"{ip}:{port}"
            except Exception as exc:
                errors.append(f"{ip}:{port} {type(exc).__name__}: {exc}")
        raise RuntimeError("All TDX quote servers failed:\n" + "\n".join(errors))

    def load_universe(self, api: Any, *, refresh: bool = False) -> list[QuoteSymbol]:
        cached = load_universe_csv(self.universe_csv)
        if cached and not refresh:
            return cached

        symbols: list[QuoteSymbol] = []
        seen: set[str] = set()
        for market in (0, 1):
            count = api.get_security_count(market) or 0
            for start in range(0, count, 1000):
                data = api.get_security_list(market, start)
                if not data:
                    continue
                for item in data:
                    code = str(item.get("code") or "").strip()
                    if not is_main_a_share(market, code):
                        continue
                    key = quote_key(market, code)
                    if key in seen:
                        continue
                    seen.add(key)
                    symbols.append(QuoteSymbol(market, code, str(item.get("name") or "").strip()))
                time.sleep(float(self.config.universe_sleep_seconds))

        save_universe_csv(self.universe_csv, symbols)
        return symbols

    def fetch_quotes(self, api: Any, symbols: Iterable[QuoteSymbol], *, batch_size: int | None = None) -> dict[str, dict[str, Any]]:
        symbol_list = list(symbols)
        names = {symbol.key: symbol.name for symbol in symbol_list}
        index_flags = {symbol.key: "1" if symbol.is_index else "" for symbol in symbol_list}
        result: dict[str, dict[str, Any]] = {}
        size = max(1, int(batch_size or self.config.batch_size))
        for start in range(0, len(symbol_list), size):
            batch = symbol_list[start : start + size]
            data = api.get_security_quotes([(symbol.market, symbol.code) for symbol in batch]) or []
            for item in data:
                key = quote_key(item.get("market"), item.get("code"))
                if finite_float(item.get("price")) <= 0:
                    continue
                row = dict(item)
                row["name"] = names.get(key, "")
                row["is_index"] = index_flags.get(key, "")
                result[key] = row
            time.sleep(float(self.config.batch_sleep_seconds))
        return result

    def snapshot(
        self,
        *,
        refresh_universe: bool = False,
        codes: Iterable[str] | None = None,
        include_shanghai_index: bool = False,
        batch_size: int | None = None,
    ) -> QuoteSnapshot:
        api, server = self.connect()
        try:
            universe = self.load_universe(api, refresh=refresh_universe)
            symbols = filter_symbols_by_codes(universe, codes)
            if include_shanghai_index:
                symbols = append_shanghai_index(symbols)
            quotes = self.fetch_quotes(api, symbols, batch_size=batch_size)
        finally:
            api.disconnect()
        return QuoteSnapshot(
            source="tdx_get_security_quotes",
            server=server,
            captured_at=datetime.now(),
            quotes=quotes,
            universe_count=len(symbols),
            quote_count=len(quotes),
            meta={
                "batch_size": int(batch_size or self.config.batch_size),
                "timeout": int(self.config.timeout),
                "include_shanghai_index": bool(include_shanghai_index),
            },
        )

    def full_market_snapshot(
        self,
        *,
        refresh_universe: bool = False,
        include_shanghai_index: bool = False,
        batch_size: int | None = None,
    ) -> QuoteSnapshot:
        return self.snapshot(
            refresh_universe=refresh_universe,
            include_shanghai_index=include_shanghai_index,
            batch_size=batch_size,
        )

    def shanghai_index_snapshot(self) -> QuoteSnapshot:
        api, server = self.connect()
        try:
            symbol = shanghai_index_symbol()
            quotes = self.fetch_quotes(api, [symbol], batch_size=1)
        finally:
            api.disconnect()
        return QuoteSnapshot(
            source="tdx_get_security_quotes",
            server=server,
            captured_at=datetime.now(),
            quotes=quotes,
            universe_count=1,
            quote_count=len(quotes),
            meta={"scope": "shanghai_index"},
        )


__all__ = [
    "DEFAULT_TDX_SERVERS",
    "MAIN_A_PREFIXES",
    "QuoteProviderConfig",
    "QuoteSnapshot",
    "QuoteSymbol",
    "TdxQuoteProvider",
    "append_shanghai_index",
    "filter_symbols_by_codes",
    "finite_float",
    "is_main_a_share",
    "load_universe_csv",
    "parse_tdx_server",
    "quote_key",
    "save_universe_csv",
    "shanghai_index_symbol",
]
