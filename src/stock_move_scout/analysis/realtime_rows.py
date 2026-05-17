from __future__ import annotations

from typing import Any

from stock_move_scout.analysis.realtime_filter import RealtimeFilterConfig, realtime_signal
from stock_move_scout.sources.quote_rows import safe_to_float


def build_signal_rows(
    rows: list[dict[str, Any]],
    *,
    min_speed_signal: float = 1.5,
    min_amount_delta_15s: float = 30_000_000,
    min_amount_delta_speed: float = 0.5,
    max_signal_rows: int = 50,
) -> list[dict[str, Any]]:
    signal_rows: list[dict[str, Any]] = []
    signal_config = RealtimeFilterConfig(
        min_speed_signal=min_speed_signal,
        min_amount_delta_15s=min_amount_delta_15s,
        min_amount_delta_speed=min_amount_delta_speed,
    )
    for row in rows:
        if str(row.get("is_index") or "") == "1":
            continue
        speed = safe_to_float(row.get("speed"))
        amount_delta = safe_to_float(row.get("amount_delta_15s"))
        signal = realtime_signal(
            stock_name=row.get("name"),
            speed=speed,
            amount_delta_15s=amount_delta,
            config=signal_config,
        )
        if not signal.matched:
            continue
        out = dict(row)
        out["basis"] = signal.basis
        signal_rows.append(out)

    signal_rows.sort(
        key=lambda row: (
            -safe_to_float(row.get("speed")),
            -safe_to_float(row.get("amount_delta_15s")),
            -safe_to_float(row.get("amount")),
        )
    )
    if max_signal_rows > 0:
        signal_rows = signal_rows[:max_signal_rows]
    for idx, row in enumerate(signal_rows, start=1):
        row["rank_speed"] = idx
    return signal_rows


def build_signal_rows_from_args(rows: list[dict[str, Any]], args: Any) -> list[dict[str, Any]]:
    return build_signal_rows(
        rows,
        min_speed_signal=float(getattr(args, "min_speed_signal", 1.5)),
        min_amount_delta_15s=float(getattr(args, "min_amount_delta_15s", 30_000_000)),
        min_amount_delta_speed=float(getattr(args, "min_amount_delta_speed", 0.5)),
        max_signal_rows=int(getattr(args, "max_signal_rows", 50)),
    )


__all__ = ["build_signal_rows", "build_signal_rows_from_args"]
