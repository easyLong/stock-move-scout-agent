#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    cold_profile_counts,
    cold_profile_missing_codes,
    import_company_profiles_csv,
    import_ths_root_evidence_json,
    import_stock_universe_csv,
    mysql_config_from_args,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ["market", "code", "symbol", "name", "is_st", "is_delisted", "universe_reason", "rank_speed", "captured_at"]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(command: list[str], root: Path, timeout: int) -> tuple[int, str, int]:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=str(root),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return result.returncode, (result.stdout + "\n" + result.stderr).strip(), int((time.monotonic() - started) * 1000)


def row_by_code(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("code", ""): row for row in rows if row.get("code")}


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Initialize one-time cold stock data into MySQL.")
    add_mysql_args(parser)
    parser.add_argument("--config", type=Path, default=root / "config" / "stock_scout_evidence_refresh.json")
    parser.add_argument("--universe-csv", type=Path, default=root / "data" / "stock" / "stock_scout_cold_universe.csv")
    parser.add_argument("--work-dir", type=Path, default=root / "runs" / "cold_data_init")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-batches", type=int, default=1, help="0 means run until no missing profiles.")
    parser.add_argument("--start-batch", type=int, default=0, help="refresh-all mode only: skip batches already completed")
    parser.add_argument("--request-timeout", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1, help="parallel network workers passed to profile collector")
    parser.add_argument("--cache-only", action="store_true", help="Use cache only; useful for quick verification.")
    parser.add_argument("--skip-profile-collect", action="store_true")
    parser.add_argument("--refresh-all", action="store_true", help="Refresh every stock in the cold universe instead of only missing profiles.")
    parser.add_argument("--timeout", type=int, default=1200)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    if not args.mysql_enabled:
        raise SystemExit("--mysql-enabled is required")
    root = project_root()
    config = mysql_config_from_args(args)

    build_code, build_output, build_ms = run_command(
        [
            sys.executable,
            str(root / "scripts" / "build_stock_scout_universe.py"),
            "--config",
            str(args.config),
            "--output-csv",
            str(args.universe_csv),
        ],
        root,
        args.timeout,
    )
    if build_code != 0:
        print(json.dumps({"ok": False, "stage": "build_universe", "output_tail": build_output[-3000:]}, ensure_ascii=False, indent=2))
        return build_code

    universe_rows = read_csv(args.universe_csv)
    imported_stocks = import_stock_universe_csv(config, args.universe_csv)
    universe_by_code = row_by_code(universe_rows)
    before_counts = cold_profile_counts(config)

    batches: list[dict[str, Any]] = []
    start_batch = max(0, args.start_batch) if args.refresh_all else 0
    collected_total = min(start_batch * args.batch_size, len(universe_rows)) if args.refresh_all else 0
    state_path = args.work_dir / "cold_data_init_state.json"
    write_json(
        state_path,
        {
            "ok": None,
            "status": "running",
            "ran_at": now_text(),
            "start_batch": start_batch,
            "universe_csv": str(args.universe_csv),
            "universe_rows": len(universe_rows),
            "imported_stocks": imported_stocks,
            "before_counts": before_counts,
            "after_counts": before_counts,
            "collected_profiles": collected_total,
            "batches": batches,
        },
    )
    if not args.skip_profile_collect:
        batch_index = start_batch
        while True:
            if args.max_batches and (batch_index - start_batch) >= args.max_batches:
                break
            if args.refresh_all:
                start = batch_index * args.batch_size
                batch_codes = [row.get("code", "") for row in universe_rows[start : start + args.batch_size] if row.get("code")]
            else:
                batch_codes = cold_profile_missing_codes(config, args.batch_size)
            if not batch_codes:
                break
            rows: list[dict[str, Any]] = []
            for idx, code in enumerate(batch_codes, start=1):
                source = dict(universe_by_code.get(code, {"code": code, "name": ""}))
                source["rank_speed"] = str(idx)
                source["captured_at"] = now_text()
                rows.append(source)
            batch_csv = args.work_dir / f"cold_profile_batch_{batch_index:04d}.csv"
            output_csv = args.work_dir / f"cold_profile_result_{batch_index:04d}.csv"
            output_json = args.work_dir / f"cold_profile_result_{batch_index:04d}.json"
            write_csv(batch_csv, rows)

            command = [
                sys.executable,
                str(root / "scripts" / "collect_official_site_evidence.py"),
                "--top10-csv",
                str(batch_csv),
                "--output-csv",
                str(output_csv),
                "--output-json",
                str(output_json),
                "--limit",
                str(len(rows)),
                "--ttl-days",
                "3650",
                "--timeout",
                str(args.request_timeout),
                "--max-pages",
                str(args.max_pages),
                "--workers",
                str(args.workers),
            ]
            if args.refresh_all:
                command.append("--refresh")
            if args.cache_only:
                command.append("--cache-only")
            code, output, duration_ms = run_command(command, root, args.timeout)
            imported_profiles = 0
            imported_root_items = {"snapshots": 0, "items": 0}
            if code == 0:
                imported_profiles = import_company_profiles_csv(config, output_csv)
                imported_root_items = import_ths_root_evidence_json(config, output_json)
                collected_total += imported_profiles
            batches.append(
                {
                    "batch": batch_index,
                    "input_rows": len(rows),
                    "return_code": code,
                    "duration_ms": duration_ms,
                    "imported_profiles": imported_profiles,
                    "imported_ths_root_snapshots": imported_root_items.get("snapshots", 0),
                    "imported_ths_root_items": imported_root_items.get("items", 0),
                    "batch_csv": str(batch_csv),
                    "output_csv": str(output_csv),
                    "output_tail": output[-1500:],
                }
            )
            if code != 0:
                break
            batch_index += 1
            current_counts = cold_profile_counts(config)
            write_json(
                state_path,
                {
                    "ok": None,
                    "status": "running",
                    "updated_at": now_text(),
                    "start_batch": start_batch,
                    "universe_csv": str(args.universe_csv),
                    "universe_rows": len(universe_rows),
                    "imported_stocks": imported_stocks,
                    "before_counts": before_counts,
                    "after_counts": current_counts,
                    "collected_profiles": collected_total,
                    "batches": batches,
                },
            )

    after_counts = cold_profile_counts(config)
    result = {
        "ok": True,
        "ran_at": now_text(),
        "start_batch": start_batch,
        "universe_csv": str(args.universe_csv),
        "universe_rows": len(universe_rows),
        "imported_stocks": imported_stocks,
        "build_universe_duration_ms": build_ms,
        "before_counts": before_counts,
        "after_counts": after_counts,
        "collected_profiles": collected_total,
        "batches": batches,
    }
    write_json(state_path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
