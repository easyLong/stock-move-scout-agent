#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from stock_scout_mysql import (
    add_mysql_args,
    import_community_evidence_csv,
    import_community_posts_csv,
    import_company_profiles_csv,
    import_ths_root_evidence_json,
    mysql_rows,
    mysql_cli_args_from_args,
    mysql_config_from_args,
    run_mysql,
    sql_int,
    sql_json,
    sql_string,
    window_evidence_candidate_rows,
)


HOT_POST_COLUMNS = [
    "fetched_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "hot_rank",
    "time_hint",
    "user",
    "title",
    "text",
    "detail_url",
    "repost_count",
    "comment_count",
    "like_count",
    "heat_score",
    "source_status",
    "snapshot_path",
]

EVIDENCE_COLUMNS = [
    "fetched_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "comment_count",
    "hot_post_count",
    "hot_terms",
    "community_explanation",
    "evidence_value",
    "evidence_gap",
    "sample_comments",
    "sample_hot_posts",
    "source_status",
    "snapshot_path",
]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def run_command(command: list[str], root: Path, timeout: int) -> tuple[int, str, int]:
    started = time.monotonic()
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        command,
        cwd=str(root),
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
        check=False,
    )
    return result.returncode, (result.stdout + "\n" + result.stderr).strip(), int((time.monotonic() - started) * 1000)


def now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def market_symbol(code: str) -> str:
    return f"SH{code}" if str(code).startswith(("6", "9")) else f"SZ{code}"


def to_int(value: Any) -> int:
    try:
        return int(float(str(value or "0").replace(",", "")))
    except Exception:
        return 0


def compact_text(value: Any, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_cached_community_outputs(
    config: Any,
    window_id: str,
    hot_posts_csv: Path,
    community_evidence_csv: Path,
    limit: int,
    hot_posts_per_stock: int,
    cache_hours: int,
) -> dict[str, Any]:
    candidates = window_evidence_candidate_rows(config, window_id)[:limit]
    codes = [row.get("code", "") for row in candidates if row.get("code")]
    if not codes:
        write_csv(hot_posts_csv, [], HOT_POST_COLUMNS)
        write_csv(community_evidence_csv, [], EVIDENCE_COLUMNS)
        return {"ok": True, "mode": "cache", "candidate_count": 0, "hot_post_count": 0}

    code_sql = ", ".join(sql_string(code) for code in codes)
    sql = f"""
    SELECT code, post_id, url, author_name, title, content,
           COALESCE(DATE_FORMAT(post_time, '%Y-%m-%d %H:%i:%s'), '') AS post_time,
           like_count, comment_count, repost_count,
           COALESCE(DATE_FORMAT(collected_at, '%Y-%m-%d %H:%i:%s'), '') AS collected_at
    FROM (
      SELECT cp.*,
             ROW_NUMBER() OVER (
               PARTITION BY cp.code
               ORDER BY (COALESCE(cp.repost_count,0) * 3 + COALESCE(cp.comment_count,0) * 2 + COALESCE(cp.like_count,0)) DESC,
                        cp.collected_at DESC
             ) AS rn
      FROM community_posts cp
      WHERE cp.platform = 'xueqiu'
        AND cp.code IN ({code_sql})
        AND cp.collected_at >= DATE_SUB(NOW(), INTERVAL {max(1, int(cache_hours))} HOUR)
        AND COALESCE(cp.content, '') <> ''
    ) ranked
    WHERE rn <= {max(1, int(hot_posts_per_stock))}
    ORDER BY FIELD(code, {code_sql}), rn;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True))
    posts_by_code: dict[str, list[list[str]]] = {}
    for row in rows:
        if len(row) >= 11:
            posts_by_code.setdefault(row[0], []).append(row)

    fetched_at = now_text()
    hot_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    for stock in candidates:
        code = stock.get("code", "")
        symbol = market_symbol(code)
        posts = posts_by_code.get(code, [])
        stock_hot_rows: list[dict[str, Any]] = []
        for idx, post in enumerate(posts, start=1):
            like_count = to_int(post[7])
            comment_count = to_int(post[8])
            repost_count = to_int(post[9])
            heat_score = repost_count * 3 + comment_count * 2 + like_count
            stock_hot_rows.append(
                {
                    "fetched_at": fetched_at,
                    "rank_speed": stock.get("rank_speed", ""),
                    "code": code,
                    "name": stock.get("name", ""),
                    "symbol": symbol,
                    "hot_rank": idx,
                    "time_hint": post[6] or post[10],
                    "user": post[3],
                    "title": post[4],
                    "text": post[5],
                    "detail_url": post[2],
                    "repost_count": repost_count,
                    "comment_count": comment_count,
                    "like_count": like_count,
                    "heat_score": heat_score,
                    "source_status": f"cached_db_{cache_hours}h",
                    "snapshot_path": "",
                }
            )
        if not stock_hot_rows:
            continue
        hot_rows.extend(stock_hot_rows)
        samples = " || ".join(compact_text(row.get("title") or row.get("text"), 140) for row in stock_hot_rows[:3])
        evidence_rows.append(
            {
                "fetched_at": fetched_at,
                "rank_speed": stock.get("rank_speed", ""),
                "code": code,
                "name": stock.get("name", ""),
                "symbol": symbol,
                "comment_count": len(stock_hot_rows),
                "hot_post_count": len(stock_hot_rows),
                "hot_terms": "",
                "community_explanation": f"复用近{cache_hours}小时雪球缓存，看到{len(stock_hot_rows)}条相关讨论。",
                "evidence_value": "缓存社区证据，不抢占前台浏览器。",
                "evidence_gap": "如需最新雪球热帖，可手动切换 live 模式补抓。",
                "sample_comments": "",
                "sample_hot_posts": samples,
                "source_status": f"cached_db_{cache_hours}h",
                "snapshot_path": "",
            }
        )

    write_csv(hot_posts_csv, hot_rows, HOT_POST_COLUMNS)
    write_csv(community_evidence_csv, evidence_rows, EVIDENCE_COLUMNS)
    return {
        "ok": True,
        "mode": "cache",
        "candidate_count": len(candidates),
        "hot_post_count": len(hot_rows),
        "evidence_count": len(evidence_rows),
        "cache_hours": cache_hours,
    }


def record_job_result(config: Any, window_id: str, ok: bool, duration_ms: int, payload: dict[str, Any]) -> None:
    status = "done" if ok else "failed"
    sql = f"""
    UPDATE evidence_jobs ej
    JOIN windows w ON w.id = ej.window_id
    SET ej.status = {sql_string(status)},
        ej.finished_at = NOW(3),
        ej.result_json = {sql_json(payload)}
    WHERE w.window_id = {sql_string(window_id)};

    INSERT INTO pipeline_events(window_id, event_type, stage, status, duration_ms, message, payload_json)
    VALUES(
      (SELECT id FROM windows WHERE window_id={sql_string(window_id)}),
      'hot_evidence_done',
      'hot_evidence_worker',
      {sql_string("ok" if ok else "failed")},
      {sql_int(duration_ms)},
      '',
      {sql_json(payload)}
    );
    """
    run_mysql(config, sql)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run evidence worker from MySQL window candidates.")
    parser.add_argument("--window-id", required=True)
    parser.add_argument("--evidence-top", type=int, default=10)
    parser.add_argument("--community-top", type=int, default=3)
    parser.add_argument("--official-site-mode", choices=["skip", "cache", "refresh"], default="cache")
    parser.add_argument("--community-mode", choices=["cache", "live", "skip"], default=os.environ.get("XUEQIU_COMMUNITY_MODE", "cache"))
    parser.add_argument("--community-cache-hours", type=int, default=int(os.environ.get("XUEQIU_COMMUNITY_CACHE_HOURS", "72")))
    parser.add_argument("--community-hot-posts-per-stock", type=int, default=8)
    parser.add_argument("--community-manual-verify-wait", type=int, default=8)
    parser.add_argument("--community-verify-retries", type=int, default=0)
    parser.add_argument("--community-bridge-timeout", type=int, default=40)
    parser.add_argument("--community-timeout", type=int, default=420)
    parser.add_argument("--timeout", type=int, default=1200)
    parser.add_argument("--work-dir", type=Path, default=project_root() / "runs" / "mysql_hot_evidence")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", ""))
    parser.add_argument("--openai-base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    root = project_root()
    config = mysql_config_from_args(args)

    work_dir = args.work_dir / args.window_id
    hot_posts_csv = work_dir / "xueqiu_hot_posts.csv"
    community_evidence_csv = work_dir / "xueqiu_evidence.csv"
    community_narrative_csv = work_dir / "community_narrative.csv"
    community_narrative_json = work_dir / "community_narrative.json"
    community_narrative_md = work_dir / "community_narrative.md"
    official_profile_csv = work_dir / "official_profile.csv"
    official_profile_json = work_dir / "official_profile.json"
    mysql_cli_args = mysql_cli_args_from_args(args)

    collect_command = [
        sys.executable,
        str(root / "scripts" / "xueqiu_top10_fast_bridge.py"),
        "--limit",
        str(args.community_top),
        "--mysql-window-id",
        args.window_id,
        "--hot-posts-csv",
        str(hot_posts_csv),
        "--evidence-csv",
        str(community_evidence_csv),
        "--manual-verify-wait",
        str(args.community_manual_verify_wait),
        "--verify-retries",
        str(args.community_verify_retries),
        "--timeout",
        str(args.community_bridge_timeout),
    ] + mysql_cli_args
    if args.community_mode == "live":
        returncode, output, duration_ms = run_command(collect_command, root, args.community_timeout)
    elif args.community_mode == "cache":
        started = time.monotonic()
        cache_result = collect_cached_community_outputs(
            config,
            args.window_id,
            hot_posts_csv,
            community_evidence_csv,
            args.community_top,
            args.community_hot_posts_per_stock,
            args.community_cache_hours,
        )
        returncode = 0
        output = json.dumps(cache_result, ensure_ascii=False)
        duration_ms = int((time.monotonic() - started) * 1000)
    else:
        returncode = 0
        output = "community_mode=skip"
        duration_ms = 0

    narrative_returncode = 0
    narrative_output = ""
    narrative_duration_ms = 0
    if returncode == 0 and args.community_mode != "skip" and hot_posts_csv.exists():
        narrative_command = [
            sys.executable,
            str(root / "scripts" / "summarize_community_narrative.py"),
            "--hot-posts-csv",
            str(hot_posts_csv),
            "--output-csv",
            str(community_narrative_csv),
            "--output-json",
            str(community_narrative_json),
            "--output-md",
            str(community_narrative_md),
            "--limit-per-stock",
            "8",
            "--fallback-without-model",
        ]
        if args.model:
            narrative_command.extend(["--model", args.model])
        if args.openai_base_url:
            narrative_command.extend(["--base-url", args.openai_base_url])
        narrative_returncode, narrative_output, narrative_duration_ms = run_command(narrative_command, root, 240)
        if narrative_returncode != 0:
            returncode = narrative_returncode

    official_returncode = 0
    official_output = ""
    official_duration_ms = 0
    if returncode == 0 and args.official_site_mode != "skip":
        official_command = [
            sys.executable,
            str(root / "scripts" / "collect_official_site_evidence.py"),
            "--mysql-window-id",
            args.window_id,
            "--output-csv",
            str(official_profile_csv),
            "--output-json",
            str(official_profile_json),
            "--limit",
            str(args.evidence_top),
            "--ttl-days",
            "30",
            "--timeout",
            "8",
            "--max-pages",
            "4",
        ]
        if args.official_site_mode == "cache":
            official_command.append("--cache-only")
        elif args.official_site_mode == "refresh":
            official_command.append("--refresh")
        official_command.extend(mysql_cli_args)
        official_returncode, official_output, official_duration_ms = run_command(official_command, root, 300)
        if official_returncode != 0:
            returncode = official_returncode

    imports: dict[str, int] = {}
    if returncode == 0:
        if args.community_mode != "skip":
            imports["community_posts"] = import_community_posts_csv(config, hot_posts_csv)
            imports["community_evidence"] = import_community_evidence_csv(
                config,
                community_evidence_csv,
                community_narrative_csv,
                hot_posts_csv,
                args.window_id,
            )
        if args.official_site_mode != "skip":
            imports["company_profiles"] = import_company_profiles_csv(config, official_profile_csv)
            imports["ths_root"] = import_ths_root_evidence_json(config, official_profile_json)

    rebuild_returncode = 0
    rebuild_output = ""
    rebuild_duration_ms = 0
    render_returncode = 0
    render_output = ""
    render_duration_ms = 0
    if returncode == 0:
        rebuild_command = [
            sys.executable,
            str(root / "scripts" / "build_stock_evidence_layer.py"),
            "--mysql-window-id",
            args.window_id,
            "--mysql-write-evidence-layer",
            "--no-file-output",
            "--limit",
            str(args.evidence_top),
        ] + mysql_cli_args
        rebuild_returncode, rebuild_output, rebuild_duration_ms = run_command(rebuild_command, root, 120)
        if rebuild_returncode != 0:
            returncode = rebuild_returncode
    if returncode == 0:
        render_command = [
            sys.executable,
            str(root / "scripts" / "render_mysql_dav_info_gap_posts.py"),
            "--mysql-window-id",
            args.window_id,
            "--limit",
            str(args.evidence_top),
        ] + mysql_cli_args
        render_returncode, render_output, render_duration_ms = run_command(render_command, root, 120)
        if render_returncode != 0:
            returncode = render_returncode
    payload = {
        "ok": returncode == 0,
        "returncode": returncode,
        "duration_ms": duration_ms,
        "community_mode": args.community_mode,
        "community_narrative_ok": narrative_returncode == 0,
        "community_narrative_duration_ms": narrative_duration_ms,
        "official_profile_ok": official_returncode == 0,
        "official_profile_duration_ms": official_duration_ms,
        "mysql_imports": imports,
        "mysql_evidence_layer_rebuild_ok": rebuild_returncode == 0,
        "mysql_evidence_layer_rebuild_duration_ms": rebuild_duration_ms,
        "mysql_dav_render_ok": render_returncode == 0,
        "mysql_dav_render_duration_ms": render_duration_ms,
        "window_id": args.window_id,
        "work_dir": str(work_dir),
        "output_tail": output[-2000:],
        "community_narrative_output_tail": narrative_output[-2000:],
        "official_profile_output_tail": official_output[-2000:],
        "mysql_evidence_layer_rebuild_output_tail": rebuild_output[-2000:],
        "mysql_dav_render_output_tail": render_output[-2000:],
    }
    record_job_result(config, args.window_id, returncode == 0, duration_ms + narrative_duration_ms + official_duration_ms + rebuild_duration_ms + render_duration_ms, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())
