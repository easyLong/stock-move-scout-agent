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
from stock_move_scout.evidence.payload import fetch_candidates, fetch_payload, fetch_payloads
from stock_move_scout.evidence.schema import SUMMARY_SCHEMA, evidence_hash
from stock_move_scout.evidence.storage import (
    delete_summary,
    delete_summaries_without_current_facts,
    enqueue_dirty_analysis,
    ensure_incremental_tables,
    ensure_summary_table,
    existing_evidence_hash,
    existing_evidence_hashes,
    fetch_dirty_candidates,
    mark_dirty,
    record_source_fingerprint,
    reuse_summary_by_hash,
    source_impact_priority,
    write_summary,
)
from stock_move_scout.evidence.summary import fallback_summary
from stock_move_scout.research_pool import ResearchPoolProvider
from stock_move_scout.web import resolve_trade_date

from stock_scout_mysql import (
    add_mysql_args,
    mysql_rows,
    mysql_config_from_args,
    run_mysql,
    sql_string,
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
    parser.add_argument("--fallback-only", action="store_true", help="Write deterministic timeline summaries without calling the model.")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--sync-dirty", action="store_true", help="Only refresh source fingerprints and enqueue changed stocks.")
    parser.add_argument("--dirty-only", action="store_true", help="Only analyze pending rows from evidence_analysis_dirty_queue.")
    parser.add_argument("--preserve-trade-date", action="store_true", help="Use the requested date as the service trade date even before intraday data exists.")
    parser.add_argument("--research-pool-only", dest="research_pool_only", action="store_true", help="Only process active research pool stocks.")
    add_mysql_args(parser)
    return parser.parse_args()


def current_facts(payload: dict) -> list:
    value = payload.get("current_facts")
    if not isinstance(value, list):
        return []
    return sorted(
        [item for item in value if isinstance(item, dict)],
        key=lambda item: str(item.get("fact_date") or ""),
        reverse=True,
    )


def current_fact_model_payload(payload: dict) -> dict:
    """Keep the model focused on facts that passed the current-effective layer."""
    stable_facts = []
    for item in current_facts(payload):
        stable_facts.append(
            {
                "source_table": item.get("source_table"),
                "fact_type": item.get("fact_type"),
                "fact_subtype": item.get("fact_subtype"),
                "title": item.get("title"),
                "body": item.get("body"),
                "fact_date": item.get("fact_date"),
                "valid_status": item.get("valid_status"),
                "valid_score": item.get("valid_score"),
                "evidence_role": item.get("evidence_role"),
                "evidence_group": item.get("evidence_group"),
                "display_level": item.get("display_level"),
            }
        )
    return {
        "trade_date": payload.get("trade_date"),
        "code": payload.get("code"),
        "stock_name": payload.get("stock_name"),
        "current_facts": stable_facts,
    }


def fact_identity(item: dict) -> str:
    raw = json.dumps(
        {
            "fact_type": item.get("fact_type"),
            "fact_subtype": item.get("fact_subtype"),
            "title": item.get("title"),
            "body": item.get("body"),
            "fact_date": item.get("fact_date"),
            "evidence_role": item.get("evidence_role"),
            "display_level": item.get("display_level"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return raw


def fact_match_tokens(item: dict) -> list[str]:
    values = [
        str(item.get("fact_date") or "").strip(),
        str(item.get("fact_subtype") or "").strip(),
        str(item.get("title") or "").strip(),
        str(item.get("body") or "").strip()[:80],
    ]
    return [value for value in values if value]


def text_matches_current_fact(text: str, current_facts: list[dict]) -> bool:
    value = str(text or "")
    if not value:
        return False
    for fact in current_facts:
        date = str(fact.get("fact_date") or "").strip()
        title = str(fact.get("title") or "").strip()
        subtype = str(fact.get("fact_subtype") or "").strip()
        body = str(fact.get("body") or "").strip()
        body_token = body[:80] if len(body) >= 12 else body
        strong_tokens = [token for token in (body_token, title, subtype) if len(token) >= 2]
        if any(token in value for token in strong_tokens):
            return True
        if date and any(token and token in value for token in (title, subtype)):
            return True
    return False


def filter_text_list(value: object, current_facts: list[dict]) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text_matches_current_fact(text, current_facts) and text not in out:
            out.append(text)
    return out


def filter_core_items(value: object, current_facts: list[dict]) -> list[dict]:
    if not isinstance(value, list):
        return []
    out: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = " ".join(str(item.get(key) or "") for key in ("source_date", "title", "reason", "evidence"))
        if text_matches_current_fact(text, current_facts):
            out.append(item)
    return out


def trim_summary_for_current_facts(old_summary: dict, model_payload: dict) -> dict:
    current_facts = current_fact_items_for_trim(model_payload)
    fallback = fallback_summary(model_payload)
    summary = dict(old_summary or {})
    for key in ("key_facts", "sustainability_basis", "core_support", "key_points", "hard_catalysts"):
        filtered = filter_text_list(summary.get(key), current_facts)
        summary[key] = filtered or fallback.get(key, [])
    summary["core_evidence_items"] = filter_core_items(summary.get("core_evidence_items"), current_facts) or fallback.get("core_evidence_items", [])
    summary["impact_factors"] = filter_core_items(summary.get("impact_factors"), current_facts) or fallback.get("impact_factors", [])
    line_count = len(summary.get("key_facts") or [])
    summary["summary_text"] = f"按时间线整理{line_count}条近10日有效事实" if line_count else fallback.get("summary_text", "")
    summary["final_view"] = summary["summary_text"]
    summary["final_analysis"] = summary["summary_text"]
    first_line = (summary.get("key_facts") or [""])[0]
    summary["move_reason"] = first_line
    summary["move_explanation"] = first_line
    summary["evidence_filter_summary"] = summary.get("evidence_filter_summary") or fallback.get("evidence_filter_summary", "")
    summary["main_flaw"] = "" if line_count else fallback.get("main_flaw", "")
    summary["missing_evidence"] = [] if line_count else fallback.get("missing_evidence", [])
    summary["counterpoints"] = summary.get("counterpoints") or []
    summary["risks"] = summary.get("risks") or []
    summary["evidence_gaps"] = [] if line_count else fallback.get("evidence_gaps", [])
    summary["evidence_strength"] = summary.get("evidence_strength") or fallback.get("evidence_strength", "medium")
    summary["timeliness_label"] = summary.get("timeliness_label") or fallback.get("timeliness_label", "recent")
    summary["timeliness_reason"] = summary.get("timeliness_reason") or fallback.get("timeliness_reason", "")
    summary["explanation_strength"] = summary.get("explanation_strength") or ("medium" if line_count else "none")
    summary["anchor_match"] = summary.get("anchor_match") or "weak"
    summary["anchor_match_reason"] = summary.get("anchor_match_reason") or ""
    summary["quality_label"] = summary.get("quality_label") or fallback.get("quality_label", "")
    return summary


def current_fact_items_for_trim(payload: dict) -> list[dict]:
    value = payload.get("current_facts")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def reuse_trimmed_summary_from_superset(config, model_payload: dict, current_hash: str) -> bool:
    code = str(model_payload.get("code") or "").strip()
    trade_date = str(model_payload.get("trade_date") or "").strip()
    current_facts = current_fact_items_for_trim(model_payload)
    if not code or not trade_date or not current_facts:
        return False
    current_keys = {fact_identity(item) for item in current_facts}
    sql = f"""
    SELECT DATE_FORMAT(trade_date, '%Y-%m-%d'), COALESCE(source_payload, ''), COALESCE(raw_json, '{{}}'), model, status
    FROM async_evidence_summaries
    WHERE code={sql_string(code)}
      AND trade_date < {sql_string(trade_date)}
      AND status IN ('ready', 'fallback')
    ORDER BY trade_date DESC, updated_at DESC
    LIMIT 20;
    """
    for row in mysql_rows(run_mysql(config, sql, batch=True, raw=True)):
        if len(row) < 5:
            continue
        try:
            old_payload = json.loads(row[1] or "{}")
            old_summary = json.loads(row[2] or "{}")
        except Exception:
            continue
        old_facts = current_fact_items_for_trim(old_payload)
        old_keys = {fact_identity(item) for item in old_facts}
        if not old_keys or not current_keys.issubset(old_keys) or current_keys == old_keys:
            continue
        trimmed = trim_summary_for_current_facts(old_summary, model_payload)
        model = str(row[3] or "reused_summary")
        status = str(row[4] or "ready")
        write_summary(config, model_payload, trimmed, f"{model}+trimmed_reuse", status)
        return existing_evidence_hash(config, trade_date, code) == current_hash
    return False


def include_research_pool_codes(candidates: list[dict[str, str]], research_codes: list[str] | None) -> list[dict[str, str]]:
    if not research_codes:
        return candidates
    out = list(candidates)
    seen = {str(item.get("code") or "").strip() for item in out}
    for code in research_codes:
        code = str(code or "").strip()
        if code and code not in seen:
            out.append({"code": code, "stock_name": code})
            seen.add(code)
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    requested_trade_date = str(args.trade_date or "").strip()
    args.trade_date = requested_trade_date if args.preserve_trade_date and requested_trade_date and requested_trade_date != "latest" else resolve_trade_date(config, requested_trade_date)
    ensure_summary_table(config)
    ensure_incremental_tables(config)
    ensure_model_config_table(config)
    research_codes = None
    bulk_empty_deleted = 0
    if args.research_pool_only and not args.code.strip():
        research_codes = ResearchPoolProvider(config).latest_codes(args.trade_date)
        if not args.dirty_only:
            bulk_empty_deleted = delete_summaries_without_current_facts(config, args.trade_date, research_codes)
    if args.import_model_config_file:
        env_values = read_openai_env_file(args.import_model_config_file)
        if not env_values.get("OPENAI_API_KEY"):
            raise RuntimeError(f"OPENAI_API_KEY not found in {args.import_model_config_file}")
        upsert_model_config(config, env_values, args.model_config or "default")
        if args.limit <= 0:
            print(json.dumps({"imported_model_config": args.model_config or "default"}, ensure_ascii=False))
            return 0
    if args.sync_dirty:
        candidates = fetch_candidates(
            config,
            args.trade_date,
            args.code.strip(),
            args.limit,
            research_pool_only=args.research_pool_only and not args.code.strip(),
        )
        payloads = fetch_payloads(config, args.trade_date, candidates, args.per_kind_limit)
        changed = 0
        unchanged = 0
        skipped_no_current_facts = bulk_empty_deleted
        for item in candidates:
            payload = payloads.get(item["code"]) or fetch_payload(config, args.trade_date, item["code"], item["stock_name"], args.per_kind_limit)
            h, is_changed, change_meta = record_source_fingerprint(config, payload)
            if not current_facts(payload):
                delete_summary(config, args.trade_date, item["code"])
                skipped_no_current_facts += 1
                if not is_changed:
                    unchanged += 1
                continue
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
                changed += 1
            else:
                unchanged += 1
        print(json.dumps({"trade_date": args.trade_date, "candidates": len(candidates), "changed": changed, "unchanged": unchanged, "skipped_no_current_facts": skipped_no_current_facts}, ensure_ascii=False))
        return 0

    candidates = (
        fetch_dirty_candidates(config, args.trade_date, args.limit, args.code.strip(), research_codes)
        if args.dirty_only
        else fetch_candidates(
            config,
            args.trade_date,
            args.code.strip(),
            args.limit,
            research_pool_only=args.research_pool_only and not args.code.strip(),
        )
    )
    payloads = fetch_payloads(config, args.trade_date, candidates, args.per_kind_limit)
    existing_hash_by_code = existing_evidence_hashes(
        config,
        args.trade_date,
        [str(item.get("code") or "") for item in candidates],
    )
    model_runtime = None
    if not args.fallback_only:
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
    reused = 0
    trimmed_reused = 0
    failed = 0
    skipped = 0
    skipped_no_current_facts = bulk_empty_deleted
    for item in candidates:
        payload = payloads.get(item["code"]) or fetch_payload(config, args.trade_date, item["code"], item["stock_name"], args.per_kind_limit)
        if not current_facts(payload):
            delete_summary(config, args.trade_date, item["code"])
            skipped_no_current_facts += 1
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "ignored")
            continue
        model_payload = current_fact_model_payload(payload)
        current_hash = evidence_hash(model_payload)
        if not args.force and existing_hash_by_code.get(item["code"]) == current_hash:
            skipped += 1
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "ignored")
            continue
        if not args.force and reuse_summary_by_hash(config, model_payload, current_hash):
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            reused += 1
            continue
        if not args.force and reuse_trimmed_summary_from_superset(config, model_payload, current_hash):
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            trimmed_reused += 1
            continue
        if args.fallback_only:
            summary = fallback_summary(model_payload)
            write_summary(config, model_payload, summary, "fallback_timeline", "fallback")
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            written += 1
            continue
        try:
            summary = call_async_evidence_model(
                model_payload,
                model=model_runtime.model if model_runtime else args.model,
                base_url=model_runtime.base_url if model_runtime else args.base_url,
                api_key=model_runtime.api_key if model_runtime else "",
                timeout=model_runtime.timeout if model_runtime else args.timeout,
                schema=SUMMARY_SCHEMA,
            )
            write_summary(config, model_payload, summary, model_runtime.model if model_runtime else args.model, "ready")
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            written += 1
        except Exception as exc:
            if not args.fallback_without_model:
                failed += 1
                write_summary(config, model_payload, fallback_summary(model_payload), model_runtime.model if model_runtime else args.model, "failed", str(exc))
                if args.dirty_only:
                    mark_dirty(config, item.get("dirty_id", ""), "failed", str(exc))
                continue
            summary = fallback_summary(model_payload)
            write_summary(config, model_payload, summary, "fallback_without_model", "fallback", str(exc))
            if args.dirty_only:
                mark_dirty(config, item.get("dirty_id", ""), "done")
            written += 1
    print(json.dumps({"trade_date": args.trade_date, "candidates": len(candidates), "written": written, "reused": reused, "trimmed_reused": trimmed_reused, "failed": failed, "skipped": skipped, "skipped_no_current_facts": skipped_no_current_facts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
