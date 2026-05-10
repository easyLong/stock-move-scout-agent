#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["market", "code", "symbol", "name", "is_st", "is_delisted", "universe_reason"]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def market_symbol(market: str, code: str) -> str:
    if market == "1":
        return f"SH{code}"
    return f"SZ{code}"


def truthy_config(config: dict[str, Any], key: str, default: bool) -> bool:
    value = config.get(key, default)
    return bool(value)


def is_st_name(name: str) -> bool:
    text = name.upper()
    return "ST" in text or name.startswith("*ST")


def is_delisted_name(name: str) -> bool:
    return "退市" in name or "退" in name


def filter_universe(rows: list[dict[str, str]], config: dict[str, Any]) -> list[dict[str, Any]]:
    include_markets = {str(item) for item in config.get("include_markets", [0, 1])}
    include_prefixes = tuple(str(item) for item in config.get("include_code_prefixes", []))
    exclude_name_contains = [str(item) for item in config.get("exclude_name_contains", []) if str(item)]
    exclude_st = truthy_config(config, "exclude_st", True)
    exclude_delisted = truthy_config(config, "exclude_delisted", True)

    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        market = str(row.get("market", "")).strip()
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        if not market or not code or not name:
            continue
        if market not in include_markets:
            continue
        if include_prefixes and not code.startswith(include_prefixes):
            continue
        if exclude_st and is_st_name(name):
            continue
        if exclude_delisted and is_delisted_name(name):
            continue
        if any(item in name for item in exclude_name_contains):
            continue
        key = (market, code)
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "market": market,
                "code": code,
                "symbol": market_symbol(market, code),
                "name": name,
                "is_st": 1 if is_st_name(name) else 0,
                "is_delisted": 1 if is_delisted_name(name) else 0,
                "universe_reason": "sh_sz_a_exclude_st_delisted",
            }
        )
    result.sort(key=lambda item: (item["market"], item["code"]))
    return result


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Build configurable stock scout universe files.")
    parser.add_argument("--config", type=Path, default=root / "config" / "stock_scout_evidence_refresh.json")
    parser.add_argument("--source-csv", type=Path, default=None)
    parser.add_argument("--output-csv", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    root = project_root()
    policy = read_json(args.config)
    universe_config = policy.get("universe") or {}
    source_csv = args.source_csv or root / str(universe_config.get("source_csv", "data/stock/tdx_a_stock_universe.csv"))
    output_csv = args.output_csv or root / str(universe_config.get("cold_universe_csv", "data/stock/stock_scout_cold_universe.csv"))
    source_rows = read_csv(source_csv)
    rows = filter_universe(source_rows, universe_config)
    write_csv(output_csv, rows)
    print(
        json.dumps(
            {
                "source_csv": str(source_csv),
                "output_csv": str(output_csv),
                "source_count": len(source_rows),
                "filtered_count": len(rows),
                "config": universe_config,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
