from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


DEFAULT_MIN_SPEED_SIGNAL = 1.5
DEFAULT_MIN_AMOUNT_DELTA_15S = 30_000_000.0
DEFAULT_MIN_AMOUNT_DELTA_SPEED = 0.5


@dataclass(frozen=True)
class RealtimeFilterConfig:
    min_speed_signal: float = DEFAULT_MIN_SPEED_SIGNAL
    min_amount_delta_15s: float = DEFAULT_MIN_AMOUNT_DELTA_15S
    min_amount_delta_speed: float = DEFAULT_MIN_AMOUNT_DELTA_SPEED


@dataclass(frozen=True)
class RealtimeSignal:
    matched: bool
    reasons: tuple[str, ...]

    @property
    def basis(self) -> str:
        return "signal_" + "+".join(self.reasons) if self.reasons else ""


def is_excluded_stock_name(name: Any) -> bool:
    text = str(name or "").strip()
    upper = text.upper()
    return "ST" in upper or "退市" in text


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def realtime_signal(
    *,
    stock_name: Any,
    speed: Any,
    amount_delta_15s: Any,
    config: RealtimeFilterConfig | None = None,
) -> RealtimeSignal:
    if is_excluded_stock_name(stock_name):
        return RealtimeSignal(False, ())

    cfg = config or RealtimeFilterConfig()
    speed_value = safe_float(speed)
    reasons: list[str] = []

    if speed_value >= cfg.min_speed_signal:
        reasons.append(f"speed>={cfg.min_speed_signal:g}")

    return RealtimeSignal(bool(reasons), tuple(reasons))


__all__ = [
    "DEFAULT_MIN_AMOUNT_DELTA_15S",
    "DEFAULT_MIN_AMOUNT_DELTA_SPEED",
    "DEFAULT_MIN_SPEED_SIGNAL",
    "RealtimeFilterConfig",
    "RealtimeSignal",
    "is_excluded_stock_name",
    "realtime_signal",
    "safe_float",
]
