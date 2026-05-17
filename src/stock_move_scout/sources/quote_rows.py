from __future__ import annotations

from datetime import datetime
from typing import Any

from .quotes import finite_float


def safe_to_float(value: Any) -> float:
    return finite_float(value, 0.0)


def build_quote_rows(
    current: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    *,
    industry_map: dict[str, dict[str, str]],
    concept_map: dict[str, list[str]],
    concept_limit: int,
    server: str,
    pct_change_first_run_as_speed: bool = True,
) -> tuple[list[dict[str, Any]], str]:
    captured_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    has_previous = bool(previous)
    basis = "snapshot_speed" if has_previous else "pct_change_first_run"
    rows: list[dict[str, Any]] = []

    for key, item in current.items():
        price = safe_to_float(item.get("price"))
        last_close = safe_to_float(item.get("last_close"))
        prev_price = safe_to_float(previous.get(key, {}).get("price")) if has_previous else 0.0
        prev_amount = safe_to_float(previous.get(key, {}).get("amount")) if has_previous else 0.0
        prev_vol = safe_to_float(previous.get(key, {}).get("vol")) if has_previous else 0.0
        amount = safe_to_float(item.get("amount"))
        vol = safe_to_float(item.get("vol"))
        amount_delta = max(0.0, amount - prev_amount) if has_previous and prev_amount > 0 else 0.0
        vol_delta = max(0.0, vol - prev_vol) if has_previous and prev_vol > 0 else 0.0
        pct_change = ((price / last_close - 1) * 100) if last_close else 0.0
        speed = ((price / prev_price - 1) * 100) if prev_price else (pct_change if pct_change_first_run_as_speed else 0.0)
        industry = industry_map.get(key, {})
        concepts = concept_map.get(key, [])
        rows.append(
            {
                "captured_at": captured_at,
                "rank_speed": "",
                "rank_pct_change": "",
                "market": item.get("market", ""),
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "price": round(price, 4),
                "speed": round(speed, 4),
                "pct_change": round(pct_change, 4),
                "last_close": last_close,
                "open": item.get("open", ""),
                "high": item.get("high", ""),
                "low": item.get("low", ""),
                "amount": item.get("amount", ""),
                "amount_delta_15s": round(amount_delta, 2),
                "vol": item.get("vol", ""),
                "vol_delta_15s": int(vol_delta),
                "cur_vol": item.get("cur_vol", ""),
                "bid1": item.get("bid1", ""),
                "ask1": item.get("ask1", ""),
                "industry": industry.get("industry", ""),
                "sub_industry": industry.get("sub_industry", ""),
                "industry_code": industry.get("industry_code", ""),
                "sub_industry_code": industry.get("sub_industry_code", ""),
                "concepts": ",".join(concepts[:concept_limit]),
                "concept_count": len(concepts),
                "server": server,
                "basis": basis,
                "is_index": item.get("is_index", ""),
            }
        )

    for idx, row in enumerate(sorted(rows, key=lambda row: safe_to_float(row["speed"]), reverse=True), start=1):
        row["rank_speed"] = idx
    for idx, row in enumerate(sorted(rows, key=lambda row: safe_to_float(row["pct_change"]), reverse=True), start=1):
        row["rank_pct_change"] = idx
    return rows, basis


__all__ = ["build_quote_rows", "safe_to_float"]
