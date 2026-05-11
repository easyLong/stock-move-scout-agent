#!/usr/bin/env python
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.evidence import call_async_evidence_model
from stock_move_scout.evidence.model_config import (
    ensure_model_config_table,
    read_openai_env_file,
    resolve_model_runtime_config,
    upsert_model_config,
)
from stock_move_scout.evidence.payload import fetch_candidates, fetch_payload
from stock_move_scout.evidence.schema import SUMMARY_SCHEMA, evidence_hash
from stock_move_scout.evidence.storage import (
    enqueue_dirty_analysis,
    enqueue_dirty_judgement,
    ensure_incremental_tables,
    ensure_summary_table,
    existing_evidence_hash,
    fetch_dirty_candidates,
    mark_dirty,
    record_source_fingerprint,
    source_impact_priority,
    write_summary,
)
from stock_move_scout.evidence.summary import fallback_summary
from stock_move_scout.web import resolve_trade_date

from stock_scout_mysql import (
    add_mysql_args,
    mysql_config_from_args,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize asynchronous stock evidence into MySQL.")
    parser.add_argument("--trade-date", default="latest")
    parser.add_argument("--code", default="")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--per-kind-limit", type=int, default=8)
    parser.add_argument("--model-config", default=os.environ.get("MODEL_CONFIG_NAME", "default"))
    parser.add_argument("--no-model-config", action="store_true")
    parser.add_argument("--import-model-config-file", default="")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1")
    parser.add_argument("--api-key-file", default=os.environ.get("OPENAI_API_KEY_FILE", ""))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--fallback-without-model", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sync-dirty", action="store_true", help="Only refresh source fingerprints and enqueue changed stocks.")
    parser.add_argument("--dirty-only", action="store_true", help="Only analyze pending rows from evidence_analysis_dirty_queue.")
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    args.trade_date = resolve_trade_date(config, args.trade_date)
    ensure_summary_table(config)
    ensure_incremental_tables(config)
    ensure_model_config_table(config)
    if args.import_model_config_file:
        env_values = read_openai_env_file(args.import_model_config_file)
        if not env_values.get("OPENAI_API_KEY"):
            raise RuntimeError(f"OPENAI_API_KEY not found in {args.import_model_config_file}")
        upsert_model_config(config, env_values, args.model_config or "default")
        if args.limit <= 0:
            print(json.dumps({"imported_model_config": args.model_config or "default"}, ensure_ascii=False))
            return 0
    if args.sync_dirty:
        candidates = fetch_candidates(config, args.trade_date, args.code.strip(), args.limit)
        changed = 0
        unchanged = 0
        for item in candidates:
            payload = fetch_payload(config, args.trade_date, item["code"], item["stock_name"], args.per_kind_limit)
            if not (payload.get("root_items") or payload.get("evidence_layers") or payload.get("theme_reason_bank")):
                continue
            h, is_changed, change_meta = record_source_fingerprint(config, payload)
            if is_changed:
                priority, impact_hint = source_impact_priority(change_meta.get("changed_sources", []), payload)
                changed_sources = list(change_meta.get("changed_sources") or [])
                enqueue_dirty_analysis(
                    config,
                    payload,
                    h,
                    "source_changed",
                    priority,
                    str(change_meta.get("previous_hash") or ""),
                    changed_sources,
                    impact_hint,
                )
                if any(
                    source in set(changed_sources)
                    for source in ("period_rankings", "market_anchors", "evidence_layers", "theme_reason_bank", "profile")
                ):
                    enqueue_dirty_judgement(
                        config,
                        trade_date=args.trade_date,
                        code=item["code"],
                        stock_name=item["stock_name"],
                        source_hash_value=h,
                        reason="source_changed",
                        changed_sources=changed_sources,
                        impact_hint=impact_hint,
                    )
                changed += 1
            else:
                unchanged += 1
        print(json.dumps({"trade_date": args.trade_date, "candidates": len(candidates), "changed": changed, "unchanged": unchanged}, ensure_ascii=False))
        return 0

    candidates = fetch_dirty_candidates(config, args.trade_date, args.limit, args.code.strip()) if args.dirty_only else fetch_candidates(config, args.trade_date, args.code.strip(), args.limit)
    model_runtime = resolve_model_runtime_config(
        config=config,
        config_name=args.model_config,
        no_model_config=args.no_model_config,
        api_key_file=args.api_key_file,
        base_url=args.base_url,
        model=args.model,
        timeout=args.timeout,
    )
    written = 0
    failed = 0
    skipped = 0
    for item in candidates:
        payload = fetch_payload(config, args.trade_date, item["code"], item["stock_name"], args.per_kind_limit)
        if not (payload.get("root_items") or payload.get("evidence_layers") or payload.get("theme_reason_bank")):
            continue
        current_hash = evidence_hash(payload)
        if not args.force and existing_evidence_hash(config, args.trade_date, item["code"]) == current_hash:
            skipped += 1
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "ignored")
            continue
        try:
            summary = call_async_evidence_model(
                payload,
                model=model_runtime.model,
                base_url=model_runtime.base_url,
                api_key=model_runtime.api_key,
                timeout=model_runtime.timeout,
                schema=SUMMARY_SCHEMA,
            )
            write_summary(config, payload, summary, model_runtime.model, "ready")
            enqueue_dirty_judgement(
                config,
                trade_date=args.trade_date,
                code=item["code"],
                stock_name=item["stock_name"],
                source_hash_value=evidence_hash(payload),
                reason="async_summary_updated",
                changed_sources=["async_evidence_summaries"],
            )
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            written += 1
        except Exception as exc:
            if not args.fallback_without_model:
                failed += 1
                write_summary(config, payload, fallback_summary(payload), model_runtime.model, "failed", str(exc))
                if args.dirty_only:
                    mark_dirty(config, item.get("dirty_id", ""), "failed", str(exc))
                continue
            summary = fallback_summary(payload)
            write_summary(config, payload, summary, "fallback_without_model", "fallback", str(exc))
            enqueue_dirty_judgement(
                config,
                trade_date=args.trade_date,
                code=item["code"],
                stock_name=item["stock_name"],
                source_hash_value=evidence_hash(payload),
                reason="async_summary_updated",
                changed_sources=["async_evidence_summaries"],
                impact_hint="异步证据摘要已用 fallback 更新",
            )
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            written += 1
    print(json.dumps({"trade_date": args.trade_date, "candidates": len(candidates), "written": written, "failed": failed, "skipped": skipped}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
