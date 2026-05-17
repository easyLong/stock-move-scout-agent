from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, time as clock_time
from pathlib import Path
from typing import Any, Iterable

from stock_move_scout.analysis.realtime_judgement import build_judgement_rows
from stock_move_scout.analysis.realtime_rows import build_signal_rows
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.sources.quote_rows import build_quote_rows, safe_to_float
from stock_move_scout.sources.quotes import QuoteSnapshot, TdxQuoteProvider


OUTPUT_COLUMNS = [
    "captured_at",
    "rank_speed",
    "rank_pct_change",
    "market",
    "code",
    "name",
    "price",
    "speed",
    "pct_change",
    "last_close",
    "open",
    "high",
    "low",
    "amount",
    "amount_delta_15s",
    "vol",
    "vol_delta_15s",
    "cur_vol",
    "bid1",
    "ask1",
    "industry",
    "sub_industry",
    "industry_code",
    "sub_industry_code",
    "concepts",
    "concept_count",
    "server",
    "basis",
    "is_index",
]

JUDGEMENT_COLUMNS = OUTPUT_COLUMNS + [
    "candidate_basis",
    "freshness",
    "speed_signal",
    "pct_position",
    "amount_confirm",
    "linkage_signal",
    "industry_hot_count",
    "sub_industry_hot_count",
    "concept_hot_count",
    "hot_concepts",
    "risk_flags",
    "action_bucket",
    "value_view",
    "value_reason",
    "next_watch",
    "avoid_reason",
    "key_points",
]


@dataclass(frozen=True)
class RealtimeScanPaths:
    snapshot_json: Path
    full_market_csv: Path
    speed_latest_csv: Path
    speed_history_csv: Path
    pct_latest_csv: Path
    judgement_latest_csv: Path
    judgement_history_csv: Path
    meta_json: Path
    seen_json: Path


@dataclass(frozen=True)
class RealtimeScanConfig:
    paths: RealtimeScanPaths
    top: int = 10
    max_signal_rows: int = 50
    min_speed_signal: float = 1.5
    min_amount_delta_15s: float = 30_000_000
    min_amount_delta_speed: float = 0.5
    concept_limit: int = 8
    heat_sample_size: int = 80
    refresh_universe: bool = False
    codes: str | Iterable[str] | None = None
    research_pool_only: bool = False
    trade_date: str = ""
    fresh_snapshot_max_age_seconds: int = 120
    no_pct_change_first_run_signal: bool = False
    interval_seconds: int = 60


@dataclass(frozen=True)
class RealtimeScanResult:
    rows: list[dict[str, Any]]
    speed_rows: list[dict[str, Any]]
    pct_rows: list[dict[str, Any]]
    judgement_rows: list[dict[str, Any]]
    snapshot: QuoteSnapshot
    meta: dict[str, Any]
    summary: str


def to_float(value: Any) -> float:
    try:
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
    except Exception:
        return 0.0


def clean_code_set(values: str | Iterable[str] | None) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, str):
        raw_values = re.split(r"[,，\s]+", values)
    else:
        raw_values = list(values)
    return {str(item or "").strip() for item in raw_values if str(item or "").strip()}


def parse_datetime_text(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    return None


def market_phase(now: datetime | None = None) -> str:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return "non_trading_day"
    current = now.time()
    if clock_time(9, 30) <= current < clock_time(11, 30):
        return "trading"
    if clock_time(13, 0) <= current < clock_time(15, 0):
        return "trading"
    if clock_time(11, 30) <= current < clock_time(13, 0):
        return "lunch_break"
    return "market_closed"


def snapshot_is_fresh(meta: dict[str, Any], scope_signature: str, max_age_seconds: int) -> bool:
    if max_age_seconds <= 0:
        return True
    if str(meta.get("scope_signature") or "") != scope_signature:
        return False
    captured_at = parse_datetime_text(meta.get("captured_at"))
    if captured_at is None:
        return False
    return (datetime.now() - captured_at).total_seconds() <= max_age_seconds


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(path: Path) -> dict[str, dict[str, Any]]:
    return read_json(path)  # type: ignore[return-value]


def save_snapshot(path: Path, snapshot: dict[str, dict[str, Any]]) -> None:
    write_json(path, snapshot)


def load_seen_state(path: Path) -> dict[str, Any]:
    state = read_json(path)
    if not isinstance(state.get("items"), dict):
        state["items"] = {}
    return state


def save_seen_state(path: Path, state: dict[str, Any]) -> None:
    items = state.get("items", {})
    if isinstance(items, dict) and len(items) > 5000:
        kept = sorted(items.items(), key=lambda item: item[1].get("last_seen", ""))[-3000:]
        state["items"] = dict(kept)
    write_json(path, state)


def write_csv(path: Path, rows: list[dict[str, Any]], append: bool, columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    mode = "a" if append else "w"
    with path.open(mode, newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns or OUTPUT_COLUMNS, extrasaction="ignore")
        if not append or not exists:
            writer.writeheader()
        writer.writerows(rows)


def csv_has_data(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return any(True for _ in reader)


def csv_has_positive_speed(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return any(to_float(row.get("speed")) > 0 for row in reader)


def read_last_history_group(path: Path, require_positive_speed: bool = False) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        rows = list(csv.DictReader(file))
    if require_positive_speed:
        rows = [row for row in rows if to_float(row.get("speed")) > 0]
    if not rows:
        return []
    last_time = rows[-1].get("captured_at", "")
    return [row for row in rows if row.get("captured_at", "") == last_time]


def restore_latest_from_history(
    latest_path: Path,
    history_path: Path,
    columns: list[str],
    require_positive_speed: bool = False,
) -> bool:
    if require_positive_speed:
        if csv_has_positive_speed(latest_path):
            return False
    elif csv_has_data(latest_path):
        return False
    rows = read_last_history_group(history_path, require_positive_speed=require_positive_speed)
    if not rows:
        return False
    write_csv(latest_path, rows, append=False, columns=columns)
    return True


class RealtimeScanService:
    """Orchestrate one realtime mover scan without depending on a CLI script."""

    def __init__(
        self,
        *,
        quote_provider: TdxQuoteProvider,
        config: RealtimeScanConfig,
        industry_map: dict[str, dict[str, str]],
        concept_map: dict[str, list[str]],
        research_pool_provider: ResearchPoolProvider | None = None,
    ) -> None:
        self.quote_provider = quote_provider
        self.config = config
        self.industry_map = industry_map
        self.concept_map = concept_map
        self.research_pool_provider = research_pool_provider

    def research_codes(self) -> set[str]:
        if not self.config.research_pool_only or self.research_pool_provider is None:
            return set()
        return self.research_pool_provider.latest_code_set(str(self.config.trade_date))

    def scan_once(self) -> RealtimeScanResult:
        explicit_codes = clean_code_set(self.config.codes)
        research_codes = self.research_codes()
        scope_codes = explicit_codes | research_codes
        scope_signature = ",".join(sorted(scope_codes)) if scope_codes else "full_a_share"

        snapshot = self.quote_provider.snapshot(
            refresh_universe=self.config.refresh_universe,
            codes=scope_codes,
            include_shanghai_index=True,
            batch_size=self.quote_provider.config.batch_size,
        )

        previous = load_snapshot(self.config.paths.snapshot_json)
        previous_meta = read_json(self.config.paths.meta_json)
        fresh_snapshot = snapshot_is_fresh(
            previous_meta,
            scope_signature,
            int(self.config.fresh_snapshot_max_age_seconds),
        )
        if not fresh_snapshot:
            previous = {}

        rows, basis = build_quote_rows(
            snapshot.quotes,
            previous,
            industry_map=self.industry_map,
            concept_map=self.concept_map,
            concept_limit=int(self.config.concept_limit),
            server=snapshot.server,
            pct_change_first_run_as_speed=not self.config.no_pct_change_first_run_signal,
        )
        speed_rows = build_signal_rows(
            rows,
            min_speed_signal=float(self.config.min_speed_signal),
            min_amount_delta_15s=float(self.config.min_amount_delta_15s),
            min_amount_delta_speed=float(self.config.min_amount_delta_speed),
            max_signal_rows=int(self.config.max_signal_rows),
        )
        pct_rows = sorted(rows, key=lambda row: safe_to_float(row.get("pct_change")), reverse=True)[: int(self.config.top)]

        seen_state = load_seen_state(self.config.paths.seen_json)
        phase = market_phase()
        preserve_last_mover = phase != "trading" and not speed_rows
        judgement_rows: list[dict[str, Any]] = []
        if not preserve_last_mover:
            judgement_rows = build_judgement_rows(
                rows,
                speed_rows,
                pct_rows,
                seen_state,
                top=int(self.config.top),
                heat_sample_size=int(self.config.heat_sample_size),
            )

        write_csv(self.config.paths.full_market_csv, rows, append=False)
        write_csv(self.config.paths.pct_latest_csv, pct_rows, append=False)
        restored_speed_latest = False
        restored_judgement_latest = False
        if preserve_last_mover:
            restored_speed_latest = restore_latest_from_history(
                self.config.paths.speed_latest_csv,
                self.config.paths.speed_history_csv,
                OUTPUT_COLUMNS,
                require_positive_speed=True,
            )
            restored_judgement_latest = restore_latest_from_history(
                self.config.paths.judgement_latest_csv,
                self.config.paths.judgement_history_csv,
                JUDGEMENT_COLUMNS,
            )
        else:
            write_csv(self.config.paths.speed_latest_csv, speed_rows, append=False)
            write_csv(self.config.paths.speed_history_csv, speed_rows, append=True)
            write_csv(self.config.paths.judgement_latest_csv, judgement_rows, append=False, columns=JUDGEMENT_COLUMNS)
            write_csv(self.config.paths.judgement_history_csv, judgement_rows, append=True, columns=JUDGEMENT_COLUMNS)

        save_snapshot(self.config.paths.snapshot_json, snapshot.quotes)
        save_seen_state(self.config.paths.seen_json, seen_state)

        meta = {
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "basis": basis,
            "market_phase": phase,
            "preserve_last_mover": preserve_last_mover,
            "restored_speed_latest": restored_speed_latest,
            "restored_judgement_latest": restored_judgement_latest,
            "server": snapshot.server,
            "universe_count": snapshot.universe_count,
            "quote_count": snapshot.quote_count,
            "scope": "recent_limit_up_or_5d_gain_top30" if self.config.research_pool_only else "codes" if explicit_codes else "full_a_share",
            "scope_signature": scope_signature,
            "scope_code_count": len(scope_codes),
            "explicit_code_count": len(explicit_codes),
            "research_pool_code_count": len(research_codes),
            "fresh_snapshot": fresh_snapshot,
            "fresh_snapshot_max_age_seconds": self.config.fresh_snapshot_max_age_seconds,
            "industry_count": len(self.industry_map),
            "concept_stock_count": len(self.concept_map),
            "interval_seconds": self.config.interval_seconds,
            "min_speed_signal": self.config.min_speed_signal,
            "min_amount_delta_15s": self.config.min_amount_delta_15s,
            "min_amount_delta_speed": self.config.min_amount_delta_speed,
            "max_signal_rows": self.config.max_signal_rows,
            "full_market_csv": str(self.config.paths.full_market_csv),
            "speed_latest_csv": str(self.config.paths.speed_latest_csv),
            "pct_latest_csv": str(self.config.paths.pct_latest_csv),
            "judgement_latest_csv": str(self.config.paths.judgement_latest_csv),
        }
        write_json(self.config.paths.meta_json, meta)

        summary = self._summary(
            rows=rows,
            speed_rows=speed_rows,
            basis=basis,
            phase=phase,
            preserve_last_mover=preserve_last_mover,
            snapshot=snapshot,
        )
        return RealtimeScanResult(
            rows=rows,
            speed_rows=speed_rows,
            pct_rows=pct_rows,
            judgement_rows=judgement_rows,
            snapshot=snapshot,
            meta=meta,
            summary=summary,
        )

    def _summary(
        self,
        *,
        rows: list[dict[str, Any]],
        speed_rows: list[dict[str, Any]],
        basis: str,
        phase: str,
        preserve_last_mover: bool,
        snapshot: QuoteSnapshot,
    ) -> str:
        names = " / ".join(
            f"{row['rank_speed']}.{row['name']}({row['speed']}%, {row['industry'] or row['sub_industry']})"
            for row in speed_rows
        )
        captured_at = rows[0]["captured_at"] if rows else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"[{captured_at}] TDX movers top={len(speed_rows)} basis={basis} phase={phase} "
            f"preserve={preserve_last_mover} universe={snapshot.universe_count} quotes={snapshot.quote_count} "
            f"server={snapshot.server}: {names}"
        )


__all__ = [
    "JUDGEMENT_COLUMNS",
    "OUTPUT_COLUMNS",
    "RealtimeScanConfig",
    "RealtimeScanPaths",
    "RealtimeScanResult",
    "RealtimeScanService",
    "clean_code_set",
    "load_seen_state",
    "market_phase",
    "snapshot_is_fresh",
]
