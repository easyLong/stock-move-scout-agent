#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_company_profiles_csv,
    import_hard_evidence_csv,
    import_ths_root_evidence_json,
    mysql_config_from_args,
)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def current_warm_slot() -> str:
    now = datetime.now()
    minutes = now.hour * 60 + now.minute
    if 11 * 60 + 30 <= minutes <= 13 * 60 + 30:
        return "noon"
    if 18 * 60 <= minutes <= 23 * 60 + 30:
        return "evening"
    return ""


def run_command(command: list[str], root: Path, timeout: int) -> dict[str, Any]:
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
    output = (result.stdout + "\n" + result.stderr).strip()
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "duration_ms": elapsed_ms(started),
        "output_tail": output[-3000:],
    }


def configured_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def run_cold_company_profile(args: argparse.Namespace, root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    source_config = ((policy.get("sources") or {}).get("cold_company_profile") or {})
    universe_config = policy.get("universe") or {}
    batch_size = int(args.batch_size or source_config.get("batch_size") or 100)
    offset = int(args.offset)
    cold_universe_csv = configured_path(root, str(universe_config.get("cold_universe_csv", "data/stock/stock_scout_cold_universe.csv")))
    output_csv = configured_path(root, str(source_config.get("output_csv", "data/stock/official_site_evidence_latest.csv")))
    cache_json = configured_path(root, str(source_config.get("cache_json", "data/stock/cache/official_site_evidence_cache.json")))

    steps: dict[str, Any] = {}
    steps["build_universe"] = run_command(
        [sys.executable, str(root / "scripts" / "build_stock_scout_universe.py"), "--config", str(args.config)],
        root,
        args.timeout,
    )
    command = [
        sys.executable,
        str(root / "scripts" / "collect_official_site_evidence.py"),
        "--top10-csv",
        str(cold_universe_csv),
        "--output-csv",
        str(output_csv),
        "--output-json",
        str(output_csv.with_suffix(".json")),
        "--cache-json",
        str(cache_json),
        "--offset",
        str(offset),
        "--limit",
        str(batch_size),
        "--timeout",
        str(args.request_timeout),
        "--max-pages",
        str(args.max_pages),
        "--workers",
        str(args.workers),
        "--refresh",
    ]
    if args.cache_only:
        command = [item for item in command if item != "--refresh"]
        command.append("--cache-only")
    steps["collect_company_profile"] = run_command(command, root, args.timeout)
    mysql_result: dict[str, Any] = {"enabled": False}
    if args.mysql_enabled and steps["collect_company_profile"].get("ok"):
        try:
            imported = import_company_profiles_csv(mysql_config_from_args(args), output_csv)
            root_imported = import_ths_root_evidence_json(mysql_config_from_args(args), output_csv.with_suffix(".json"))
            mysql_result = {"enabled": True, "ok": True, "stock_company_profiles": imported, "ths_root": root_imported}
        except Exception as exc:
            mysql_result = {"enabled": True, "ok": False, "error": f"{type(exc).__name__}:{exc}"}
    ok = all(step.get("ok") for step in steps.values())
    if mysql_result.get("enabled") and not mysql_result.get("ok"):
        ok = False
    return {
        "task": "cold_company_profile",
        "ok": ok,
        "offset": offset,
        "batch_size": batch_size,
        "next_offset": offset + batch_size,
        "universe_csv": str(cold_universe_csv),
        "output_csv": str(output_csv),
        "steps": steps,
        "mysql": mysql_result,
    }


def run_ths_root_extended_items(args: argparse.Namespace, root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    source_config = ((policy.get("sources") or {}).get("ths_root_extended_items") or {})
    universe_config = policy.get("universe") or {}
    batch_size = int(args.batch_size or source_config.get("batch_size") or 100)
    offset = int(args.offset)
    cold_universe_csv = configured_path(root, str(universe_config.get("cold_universe_csv", "data/stock/stock_scout_cold_universe.csv")))
    cache_json = configured_path(root, str(source_config.get("cache_json", "data/stock/cache/official_site_evidence_cache.json")))
    work_dir = root / "runs" / "data_tasks" / "ths_root_extended_items"
    output_csv = work_dir / f"ths_root_extended_items_{offset:06d}.csv"
    output_json = work_dir / f"ths_root_extended_items_{offset:06d}.json"

    steps: dict[str, Any] = {}
    steps["build_universe"] = run_command(
        [sys.executable, str(root / "scripts" / "build_stock_scout_universe.py"), "--config", str(args.config)],
        root,
        args.timeout,
    )
    command = [
        sys.executable,
        str(root / "scripts" / "collect_official_site_evidence.py"),
        "--top10-csv",
        str(cold_universe_csv),
        "--output-csv",
        str(output_csv),
        "--output-json",
        str(output_json),
        "--cache-json",
        str(cache_json),
        "--offset",
        str(offset),
        "--limit",
        str(batch_size),
        "--timeout",
        str(args.request_timeout),
        "--max-pages",
        str(args.max_pages),
        "--workers",
        str(args.workers),
        "--refresh",
    ]
    if args.cache_only:
        command = [item for item in command if item != "--refresh"]
        command.append("--cache-only")
    steps["collect_ths_root"] = run_command(command, root, args.timeout)
    mysql_result: dict[str, Any] = {"enabled": False}
    if args.mysql_enabled and steps["collect_ths_root"].get("ok"):
        try:
            imported = import_ths_root_evidence_json(mysql_config_from_args(args), output_json)
            mysql_result = {"enabled": True, "ok": True, "ths_root": imported}
        except Exception as exc:
            mysql_result = {"enabled": True, "ok": False, "error": f"{type(exc).__name__}:{exc}"}
    ok = all(step.get("ok") for step in steps.values())
    if mysql_result.get("enabled") and not mysql_result.get("ok"):
        ok = False
    return {
        "task": "ths_root_extended_items",
        "ok": ok,
        "offset": offset,
        "batch_size": batch_size,
        "next_offset": offset + batch_size,
        "universe_csv": str(cold_universe_csv),
        "output_json": str(output_json),
        "steps": steps,
        "mysql": mysql_result,
    }


def run_warm_hard_evidence(args: argparse.Namespace, root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    source_policy = (policy.get("sources") or {}).get("warm_ths_announcements") or {}
    if not source_policy.get("enabled", False):
        return {"task": "warm_hard_evidence", "ok": True, "ran": False, "reason": "disabled_use_ths_root_items"}

    slot = current_warm_slot() if args.slot == "auto" else args.slot
    if not slot:
        return {"task": "warm_hard_evidence", "ok": True, "ran": False, "reason": "outside_noon_evening_slot"}

    today = datetime.now().strftime("%Y-%m-%d")
    state = read_json(args.state_json)
    key = f"{today}:{slot}"
    if state.get("warm_hard_evidence_last_success_key") == key and not args.force:
        return {"task": "warm_hard_evidence", "ok": True, "ran": False, "reason": "already_ran", "key": key}

    stock_dir = root / "data" / "stock"
    top10_csv = stock_dir / "tdx_mover_window_top10_latest.csv"
    hard_csv = stock_dir / "hard_catalyst_evidence_latest.csv"
    hard_json = stock_dir / "hard_catalyst_evidence_latest.json"

    steps: dict[str, Any] = {}
    steps["ths_announcement"] = run_command(
        [
            sys.executable,
            str(root / "scripts" / "collect_ths_announcement_evidence.py"),
            "--top10-csv",
            str(top10_csv),
            "--output-csv",
            str(hard_csv),
            "--output-json",
            str(hard_json),
            "--limit",
            "10",
        ],
        root,
        args.timeout,
    )
    result = {"ok": all(step.get("ok") for step in steps.values()), "slot": slot, "key": key, "steps": steps}
    mysql_result: dict[str, Any] = {"enabled": False}
    if args.mysql_enabled and result.get("ok"):
        try:
            mysql_result = {"enabled": True, "ok": True, "hard_evidence_rows": import_hard_evidence_csv(mysql_config_from_args(args), hard_csv)}
        except Exception as exc:
            mysql_result = {"enabled": True, "ok": False, "error": f"{type(exc).__name__}:{exc}"}
    ok = bool(result.get("ok")) and (not mysql_result.get("enabled") or bool(mysql_result.get("ok")))
    if ok:
        state["warm_hard_evidence_last_success_key"] = key
        write_json(args.state_json, state)
    return {"task": "warm_hard_evidence", "ok": ok, "ran": True, "step": result, "mysql": mysql_result}


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Run evidence refresh tasks from configurable hot/warm/cold policy.")
    parser.add_argument("--config", type=Path, default=root / "config" / "stock_scout_evidence_refresh.json")
    parser.add_argument("--task", choices=["cold_company_profile", "ths_root_extended_items", "warm_hard_evidence"], required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=0)
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--slot", choices=["auto", "noon", "evening"], default="auto")
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--request-timeout", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--state-json", type=Path, default=root / "data" / "stock" / "configured_evidence_refresh_state.json")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    root = project_root()
    policy = read_json(args.config)
    if args.task == "cold_company_profile":
        result = run_cold_company_profile(args, root, policy)
    elif args.task == "ths_root_extended_items":
        result = run_ths_root_extended_items(args, root, policy)
    else:
        result = run_warm_hard_evidence(args, root, policy)
    result["ran_at"] = now_text()
    result["config"] = str(args.config)
    state = read_json(args.state_json)
    state["last_run"] = result
    write_json(args.state_json, state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
