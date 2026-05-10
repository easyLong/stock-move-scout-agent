#!/usr/bin/env python
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stock_move_scout.feed import (
    auction_top10_sql,
    build_evidence_view,
    intel_feed_sql,
    latest_scan_sql,
    latest_window_sql,
    status_sql,
    trade_dates_sql,
    window_top10_sql,
)
from stock_move_scout.web import json_query, latest_data_date, resolve_trade_date

from stock_scout_mysql import MySqlConfig, add_mysql_args, mysql_config_from_args, run_mysql, sql_string


APP_TITLE = "AI情报引擎"


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_async_evidence_summary_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS async_evidence_summaries (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      trade_date DATE NOT NULL,
      code CHAR(6) NOT NULL,
      stock_name VARCHAR(64) NOT NULL DEFAULT '',
      evidence_hash CHAR(64) NOT NULL,
      summary_text TEXT NULL,
      evidence_filter_summary TEXT NULL,
      key_facts JSON NULL,
      move_reason TEXT NULL,
      sustainability_basis JSON NULL,
      main_flaw TEXT NULL,
      missing_evidence JSON NULL,
      core_evidence_items JSON NULL,
      timeliness_label VARCHAR(32) NOT NULL DEFAULT 'unknown',
      timeliness_reason TEXT NULL,
      final_analysis TEXT NULL,
      move_explanation TEXT NULL,
      explanation_strength VARCHAR(32) NOT NULL DEFAULT 'none',
      anchor_match VARCHAR(32) NOT NULL DEFAULT 'weak',
      anchor_match_reason TEXT NULL,
      quality_label VARCHAR(64) NOT NULL DEFAULT '',
      core_support JSON NULL,
      counterpoints JSON NULL,
      final_view TEXT NULL,
      key_points JSON NULL,
      hard_catalysts JSON NULL,
      impact_factors JSON NULL,
      impact_summary_text TEXT NULL,
      risks JSON NULL,
      evidence_strength ENUM('pending','weak','medium','strong') NOT NULL DEFAULT 'pending',
      evidence_gaps JSON NULL,
      source_counts JSON NULL,
      source_payload MEDIUMTEXT NULL,
      model VARCHAR(128) NOT NULL DEFAULT '',
      status ENUM('ready','fallback','failed') NOT NULL DEFAULT 'ready',
      error_message TEXT NULL,
      raw_json JSON NULL,
      summarized_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_async_evidence_day_code (trade_date, code),
      KEY idx_async_evidence_code (code),
      KEY idx_async_evidence_hash (evidence_hash)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)
    ensure_async_evidence_summary_column(config, "evidence_filter_summary", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "key_facts", "JSON NULL")
    ensure_async_evidence_summary_column(config, "move_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "sustainability_basis", "JSON NULL")
    ensure_async_evidence_summary_column(config, "main_flaw", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "missing_evidence", "JSON NULL")
    ensure_async_evidence_summary_column(config, "core_evidence_items", "JSON NULL")
    ensure_async_evidence_summary_column(config, "timeliness_label", "VARCHAR(32) NOT NULL DEFAULT 'unknown'")
    ensure_async_evidence_summary_column(config, "timeliness_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "final_analysis", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "move_explanation", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "explanation_strength", "VARCHAR(32) NOT NULL DEFAULT 'none'")
    ensure_async_evidence_summary_column(config, "anchor_match", "VARCHAR(32) NOT NULL DEFAULT 'weak'")
    ensure_async_evidence_summary_column(config, "anchor_match_reason", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "quality_label", "VARCHAR(64) NOT NULL DEFAULT ''")
    ensure_async_evidence_summary_column(config, "core_support", "JSON NULL")
    ensure_async_evidence_summary_column(config, "counterpoints", "JSON NULL")
    ensure_async_evidence_summary_column(config, "final_view", "TEXT NULL")
    ensure_async_evidence_summary_column(config, "impact_factors", "JSON NULL")
    ensure_async_evidence_summary_column(config, "impact_summary_text", "TEXT NULL")


def ensure_async_evidence_summary_column(config: MySqlConfig, column_name: str, column_sql: str) -> None:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'async_evidence_summaries'
      AND COLUMN_NAME = {sql_string(column_name)};
    """
    exists = (run_mysql(config, sql, batch=True, raw=True) or "").splitlines()[-1].strip() == "1"
    if not exists:
        run_mysql(config, f"ALTER TABLE async_evidence_summaries ADD COLUMN {column_name} {column_sql};")


def attach_evidence_views(feed: object) -> object:
    if not isinstance(feed, list):
        return feed
    for row in feed:
        if isinstance(row, dict):
            row["evidence_view"] = build_evidence_view(row)
    return feed












def create_app(config: MySqlConfig) -> FastAPI:
    ensure_async_evidence_summary_table(config)
    app = FastAPI(title=APP_TITLE)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return HTML

    @app.get("/api/top10")
    def api_top10() -> JSONResponse:
        payload = {
            "window": json_query(config, latest_window_sql(), {}),
            "window_top10": json_query(config, window_top10_sql(), []),
            "auction_top10": json_query(config, auction_top10_sql(), []),
            "latest_scan": json_query(config, latest_scan_sql(), {"run": None, "rows": []}),
            "status": json_query(config, status_sql(), {}),
        }
        return JSONResponse(payload)

    @app.get("/api/feed")
    def api_feed(trade_date: str = "") -> JSONResponse:
        target_date = resolve_trade_date(config, trade_date)
        feed = json_query(config, intel_feed_sql(target_date), [])
        payload = {
            "trade_date": target_date,
            "feed": attach_evidence_views(feed),
            "status": json_query(config, status_sql(target_date), {}),
            "window": json_query(config, latest_window_sql(target_date), {}),
        }
        return JSONResponse(payload)

    @app.get("/api/trade_dates")
    def api_trade_dates() -> JSONResponse:
        return JSONResponse(json_query(config, trade_dates_sql(), {"latest": latest_data_date(config), "dates": []}))

    return app


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AI情报引擎</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f7fb;
      --panel: #ffffff;
      --soft: #f8fafc;
      --line: #e4e8f0;
      --line-strong: #cbd5e1;
      --text: #172033;
      --muted: #667085;
      --accent: #2563eb;
      --scan: #596579;
      --window: #b4553a;
      --auction: #a15c22;
      --anchor-text: #08736b;
      --anchor-bg: #edfdfa;
      --anchor-line: #a7f3e8;
      --highlight-text: #635b8f;
      --highlight-bg: #f3f0ff;
      --leader-text: #a33a3a;
      --leader-bg: #fff1f2;
      --leader-line: #fecdd3;
      --core-text: #315a9f;
      --core-bg: #eff6ff;
      --core-line: #bfdbfe;
      --metric-change-text: #9a4a1f;
      --metric-change-bg: #fff7ed;
      --metric-change-line: #fed7aa;
      --metric-speed-text: #2457a6;
      --metric-speed-bg: #eef6ff;
      --metric-speed-line: #bfdbfe;
      --shadow: 0 12px 30px rgba(30, 41, 59, 0.06);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Microsoft YaHei", "PingFang SC", system-ui, -apple-system, Segoe UI, sans-serif;
      font-size: 14px;
      line-height: 1.5;
    }
    .topbar {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      min-height: 56px;
      padding: 0 22px;
      background: rgba(255, 255, 255, 0.94);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--line);
    }
    h1 {
      margin: 0;
      font-size: 17px;
      font-weight: 700;
      letter-spacing: 0;
      white-space: nowrap;
    }
    .meta {
      display: flex;
      align-items: center;
      gap: 12px;
      color: var(--muted);
      font-size: 12px;
      min-width: 0;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: #2e9f7b;
      display: inline-block;
    }
    main {
      width: min(1680px, calc(100vw - 16px));
      margin: 0 auto;
      padding: 14px 0 34px;
    }
    .summarybar {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .summarybar span {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 9px;
    }
    .toolbar {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
      flex-wrap: wrap;
    }
    .date-picker {
      height: 32px;
      min-width: 142px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: #111827;
      padding: 0 8px;
      font-size: 13px;
    }
    .toolbtn {
      height: 32px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
      color: #334155;
      padding: 0 10px;
      font-size: 13px;
      cursor: pointer;
    }
    .toolbtn:hover {
      border-color: #bfdbfe;
      color: var(--accent);
    }
    .intel-layout {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      gap: 12px;
      align-items: start;
    }
    .detail-panel, .feed-panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      overflow: hidden;
      box-shadow: var(--shadow);
    }
    .detail-title, .feed-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      min-height: 38px;
      padding: 0 12px;
      border-bottom: 1px solid var(--line);
      color: #1f2937;
      font-size: 13px;
      font-weight: 700;
      background: linear-gradient(#ffffff, #fafbfc);
    }
    .detail-title small, .feed-title small {
      color: var(--muted);
      font-weight: 400;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .feed-title small {
      white-space: nowrap;
    }
    .metric-chip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      height: 22px;
      min-width: 66px;
      padding: 0 7px;
      border-radius: 999px;
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      color: #566174;
      font-size: 12px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .metric-chip.change {
      color: var(--metric-change-text);
      background: var(--metric-change-bg);
      border-color: var(--metric-change-line);
    }
    .metric-chip.speed {
      color: var(--metric-speed-text);
      background: var(--metric-speed-bg);
      border-color: var(--metric-speed-line);
    }
    .detail-panel {
      position: sticky;
      top: 70px;
      height: calc(100vh - 84px);
      max-height: calc(100vh - 84px);
      display: grid;
      grid-template-rows: auto minmax(0, 1fr);
    }
    #detailPanel {
      min-height: 0;
      overflow: hidden;
    }
    .detail-body {
      height: 100%;
      min-height: 0;
      padding: 12px 14px 14px;
      overflow-y: auto;
      overflow-x: hidden;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
    }
    .detail-empty {
      padding: 16px 12px;
      color: var(--muted);
    }
    .evidence-summary {
      border: 1px solid #e4ddf4;
      border-radius: 8px;
      background: linear-gradient(180deg, #fdfcff 0%, #faf8ff 100%);
      padding: 10px;
      margin-bottom: 10px;
      color: #374151;
      font-size: 13px;
      line-height: 1.5;
    }
    .evidence-verdict {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }
    .evidence-verdict strong {
      color: #211b37;
    }
    .evidence-level {
      border-radius: 999px;
      padding: 1px 8px;
      font-size: 12px;
      font-weight: 700;
      background: #eff6ff;
      color: #2457a6;
      border: 1px solid #bfdbfe;
      white-space: nowrap;
    }
    .evidence-level.strong {
      background: #ecfdf5;
      color: #047857;
      border-color: #a7f3d0;
    }
    .evidence-level.weak {
      background: #fafafa;
      color: #71717a;
      border-color: #e4e4e7;
    }
    .evidence-basis {
      color: #536174;
    }
    .decision-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 7px;
      margin: 8px 0 8px;
    }
    .decision-card {
      border: 1px solid #e8e2f7;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.78);
      padding: 7px 8px;
      min-width: 0;
    }
    .decision-label {
      display: block;
      color: #7a718d;
      font-size: 11px;
      font-weight: 700;
      margin-bottom: 2px;
    }
    .decision-value {
      display: block;
      color: #211b37;
      font-size: 13px;
      font-weight: 750;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .evidence-chip-row {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      margin-top: 7px;
    }
    .evidence-chip {
      display: inline-flex;
      align-items: center;
      max-width: 100%;
      border-radius: 999px;
      border: 1px solid #d8d0ef;
      background: #fff;
      color: #4c4661;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 650;
      line-height: 1.5;
    }
    .evidence-chip.good {
      border-color: #badbcc;
      background: #f1fbf6;
      color: #23654b;
    }
    .evidence-chip.warn {
      border-color: #f0d6a7;
      background: #fff8ea;
      color: #7a5522;
    }
    .evidence-chip.weak {
      border-color: #e1e5eb;
      background: #f8fafc;
      color: #64748b;
    }
    .evidence-key {
      color: #1f2937;
      font-weight: 650;
      margin-top: 2px;
    }
    .evidence-facts {
      display: grid;
      gap: 5px;
      margin: 2px 0 0;
      padding: 0;
      list-style: none;
    }
    .evidence-fact {
      position: relative;
      padding-left: 12px;
      color: #1f2937;
      font-weight: 650;
      line-height: 1.55;
    }
    .evidence-fact::before {
      content: "";
      position: absolute;
      left: 0;
      top: 0.72em;
      width: 4px;
      height: 4px;
      border-radius: 999px;
      background: #8b5cf6;
    }
    .evidence-judgement {
      margin-top: 7px;
      color: #475569;
      line-height: 1.55;
    }
    .evidence-judgement span {
      color: #64748b;
      font-size: 12px;
      font-weight: 700;
      margin-right: 6px;
    }
    .evidence-block {
      display: grid;
      gap: 10px;
    }
    .evidence-layer {
      display: grid;
      gap: 8px;
    }
    .evidence-layer-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: #334155;
      font-size: 12px;
      font-weight: 700;
      padding: 0 2px;
    }
    .evidence-layer-title small {
      color: var(--muted);
      font-weight: 500;
    }
    .evidence-layer.async .evidence-layer-title {
      color: #6d5f95;
    }
    .evidence-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 9px 10px;
    }
    .evidence-section.theme {
      border-left: 3px solid var(--anchor-line);
    }
    .evidence-section.stock {
      border-left: 3px solid var(--core-line);
    }
    .evidence-section.event, .evidence-section.announcement {
      border-left: 3px solid var(--metric-change-line);
    }
    .evidence-section.summary {
      border-left: 3px solid #c4b5fd;
      background: #f8f6ff;
    }
    .evidence-section.impact {
      border-left: 3px solid #f0b86a;
      background: #fffaf2;
    }
    .impact-list {
      display: grid;
      gap: 7px;
    }
    .impact-list.compact {
      gap: 5px;
    }
    .impact-item {
      display: grid;
      gap: 4px;
      padding: 7px 8px;
      border: 1px solid #f4dfbf;
      border-radius: 7px;
      background: #fffdf8;
    }
    .impact-meta {
      display: flex;
      align-items: center;
      gap: 5px;
      flex-wrap: wrap;
    }
    .impact-badge {
      border-radius: 999px;
      padding: 1px 7px;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid #ead2a8;
      color: #7a5522;
      background: #fff7e8;
      line-height: 1.45;
    }
    .impact-badge.up {
      color: #8b3f36;
      border-color: #f0c7bd;
      background: #fff1ee;
    }
    .impact-badge.down {
      color: #31675b;
      border-color: #b7ded3;
      background: #edf9f5;
    }
    .impact-evidence {
      color: #374151;
      line-height: 1.65;
    }
    .mini-fold {
      border: 1px solid #e8e2f7;
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
    }
    .mini-fold summary {
      cursor: pointer;
      list-style: none;
      padding: 7px 8px;
      color: #4c4661;
      font-size: 12px;
      font-weight: 750;
    }
    .mini-fold summary::-webkit-details-marker {
      display: none;
    }
    .mini-fold summary::after {
      content: "展开";
      float: right;
      color: #8a7ba8;
      font-weight: 650;
    }
    .mini-fold[open] summary::after {
      content: "收起";
    }
    .mini-fold-body {
      border-top: 1px solid #efeaf8;
      padding: 7px 8px;
      color: #374151;
      line-height: 1.65;
      background: #fdfcff;
    }
    .evidence-fold {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      overflow: hidden;
    }
    .evidence-fold summary {
      cursor: pointer;
      list-style: none;
      padding: 9px 10px;
    }
    .evidence-fold summary::-webkit-details-marker {
      display: none;
    }
    .evidence-fold .evidence-heading {
      margin-bottom: 0;
    }
    .evidence-fold summary::after {
      content: "展开";
      display: block;
      margin-top: 5px;
      color: #8a7ba8;
      font-size: 12px;
      font-weight: 650;
    }
    .evidence-fold[open] summary::after {
      content: "收起";
    }
    .evidence-fold-body {
      border-top: 1px solid var(--line);
      padding: 9px 10px;
    }
    .evidence-layer.async .evidence-section {
      background: #fdfcff;
      border-color: #e8e2f7;
    }
    .evidence-heading {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      color: #0f172a;
      font-weight: 700;
      font-size: 13px;
      margin-bottom: 5px;
    }
    .evidence-source {
      color: var(--muted);
      font-size: 12px;
      font-weight: 500;
      white-space: nowrap;
    }
    .evidence-text {
      color: #334155;
      font-size: 13px;
      line-height: 1.7;
      white-space: pre-wrap;
    }
    .evidence-gap {
      margin-top: 10px;
      color: #7a8495;
      font-size: 12px;
      line-height: 1.55;
      border-top: 1px dashed var(--line);
      padding-top: 8px;
    }
    .feed {
      display: grid;
      gap: 0;
    }
    .stream-group {
      border-bottom: 1px solid #edf2f7;
    }
    .stream-group:last-child {
      border-bottom: 0;
    }
    .stream-group-title {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 8px 12px 4px;
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      border-left: 4px solid #dbeafe;
    }
    .group-summary {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: #546179;
      font-weight: 500;
    }
    .time {
      color: var(--muted);
      font-variant-numeric: tabular-nums;
      padding-top: 2px;
    }
    .kind {
      flex: 0 0 auto;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      color: #fff;
      background: var(--accent);
    }
    .kind.window { background: var(--window); }
    .kind.auction { background: var(--auction); }
    .kind.scan { background: var(--scan); }
    .kind.hot_concept { background: #0f766e; }
    .kind.market_news { background: #3867b7; }
    .kind.daily_theme { background: #6f5ab8; }
    .tags {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
    }
    .tag {
      color: #374151;
      background: #f7f8fa;
      border: 1px solid #e3e7ee;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      display: inline-flex;
      align-items: center;
      text-decoration: none;
    }
    .tag.weak {
      color: #7a8495;
      background: #fafbfc;
      border-color: #e7ebf2;
      font-weight: 500;
    }
    .stream-item {
      display: grid;
      grid-template-columns: 170px minmax(0, 1fr) auto;
      grid-template-areas:
        "title tags metric"
        "highlight highlight highlight";
      column-gap: 10px;
      row-gap: 5px;
      align-items: center;
      min-height: 54px;
      padding: 8px 12px 9px;
      background: #fff;
      border: 0;
      border-bottom: 1px solid #edf2f7;
      border-radius: 0;
      font-size: 13px;
      cursor: pointer;
    }
    .stream-item:last-child { border-bottom: 0; }
    .stream-item:hover { background: #fafcff; }
    .stream-item.active {
      background: #f3f8ff;
      box-shadow: inset 4px 0 0 var(--accent);
    }
    .stream-item.active .stream-title a {
      color: #174ea6;
      font-weight: 800;
    }
    .stream-item.active .stream-highlight {
      padding: 1px 6px;
      background: var(--highlight-bg);
    }
    .stream-title {
      grid-area: title;
      min-width: 0;
      font-weight: 700;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .stream-title a {
      color: inherit;
      text-decoration: none;
    }
    .stream-title a:hover {
      color: var(--accent);
      text-decoration: underline;
    }
    .stream-highlight {
      grid-area: highlight;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      color: var(--highlight-text);
      border-radius: 4px;
      padding: 0;
      font-weight: 600;
    }
    .stream-metric {
      grid-area: metric;
      display: flex;
      justify-content: flex-end;
      gap: 5px;
    }
    .stream-tags {
      grid-area: tags;
      display: flex;
      flex-wrap: nowrap;
      gap: 4px;
      min-width: 0;
      overflow: hidden;
      align-items: center;
    }
    .stream-tags .tag {
      padding: 1px 7px;
    }
    .tag.role {
      border-color: var(--leader-line);
      background: var(--leader-bg);
      color: var(--leader-text);
      font-weight: 700;
    }
    .tag.core {
      border-color: var(--core-line);
      background: var(--core-bg);
      color: var(--core-text);
      font-weight: 700;
    }
    .tag.anchor {
      border-color: var(--anchor-line);
      background: var(--anchor-bg);
      color: var(--anchor-text);
      font-weight: 700;
    }
    a.tag:hover {
      filter: brightness(0.98);
      text-decoration: underline;
    }
    .empty, .error {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 18px;
      color: var(--muted);
    }
    .error { color: #b42318; }
    @media (max-width: 640px) {
      .topbar {
        height: auto;
        min-height: 52px;
        align-items: flex-start;
        flex-direction: column;
        padding: 9px 12px;
        gap: 4px;
      }
      .meta { flex-wrap: wrap; gap: 7px; }
      main { width: calc(100vw - 20px); padding-top: 10px; }
      .intel-layout {
        grid-template-columns: 1fr;
      }
      .detail-panel {
        position: static;
        height: auto;
        max-height: none;
      }
      #detailPanel {
        overflow: visible;
      }
      .detail-body {
        height: auto;
        overflow: visible;
      }
      .stream-item {
        grid-template-columns: minmax(0, 1fr) auto;
        grid-template-areas:
          "title metric"
          "tags tags"
          "highlight highlight";
      }
      .stream-title {
        max-width: none;
      }
      .metric-chip {
        min-width: 58px;
      }
      .time { font-size: 12px; }
    }
  </style>
</head>
<body>
  <header class="topbar">
    <h1>AI情报引擎</h1>
    <div class="meta">
      <span><span class="dot"></span> 自动刷新</span>
      <span id="refreshText">等待数据</span>
    </div>
  </header>
  <main>
    <div class="toolbar">
      <select class="date-picker" id="tradeDateSelect" aria-label="选择交易日"></select>
      <button class="toolbtn" id="latestBtn" type="button">最近交易日</button>
    </div>
    <div class="summarybar" id="summarybar"></div>
    <section class="intel-layout">
      <section class="feed-panel">
        <div class="feed-title">AI情报流 <small>按时间滚动，点击查看右侧证据</small></div>
        <section class="feed" id="feed"></section>
      </section>
      <aside class="detail-panel">
        <div class="detail-title">证据详情 <small id="detailTime"></small></div>
        <div id="detailPanel"></div>
      </aside>
    </section>
  </main>
  <script>
    const $ = id => document.getElementById(id);
    const clean = value => String(value ?? "").replace(/[ \t\r\f\v]+/g, " ").trim();
    const esc = value => clean(value).replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
    const escBlock = value => String(value ?? "").replace(/[&<>"']/g, c => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
    function parseTags(value) {
      if (Array.isArray(value)) return value;
      if (!value) return [];
      try { return JSON.parse(value); } catch { return String(value).split(/[,\s，、]+/); }
    }
    let currentFeed = [];
    let selectedKey = "";
    function rowKey(row) {
      return [row.kind, row.event_time, row.code, row.title].map(clean).join("|");
    }
    function stockUrl(code) {
      return `https://stockpage.10jqka.com.cn/${encodeURIComponent(clean(code))}/`;
    }
    function stockAnchorHtml(row) {
      const title = row.title || `${row.name || ""} ${row.code || ""}`;
      const code = clean(row.code);
      if (/^\d{6}$/.test(code)) {
        return `<a href="${stockUrl(code)}" target="_blank" rel="noopener noreferrer">${esc(title)}</a>`;
      }
      return esc(title);
    }
    function stockTitleText(row) {
      return clean(row?.title || `${row?.name || ""} ${row?.code || ""}`) || "未选择";
    }
    function tagValue(tags, prefix) {
      const hit = tags.find(tag => tag.startsWith(prefix));
      return hit ? hit.slice(prefix.length) : "";
    }
    function anchorText(row) {
      const tags = parseTags(row.tags).map(clean).filter(Boolean);
      return tagValue(tags, "锚点:") || "未锚定";
    }
    function timeText(value) {
      if (!value) return "--:--:--";
      const [date, time] = String(value).split(" ");
      return time || date || "--:--:--";
    }
    function renderSummary(data) {
      const status = data.status || {};
      const windowInfo = data.window || {};
      const tradeDate = data.trade_date || status.trade_date || "最近交易日";
      $("summarybar").innerHTML = [
        `${tradeDate} 扫描 ${status.scan_count_today || 0}`,
        `窗口 ${status.window_count_today || 0}`,
        `有效锚点 ${status.active_anchors || 0}`,
        `最新 ${status.latest_scan_at || "-"}`,
        windowInfo.window_id ? `当前窗口 ${windowInfo.window_id}` : ""
      ].filter(Boolean).map(x => `<span>${esc(x)}</span>`).join("");
    }
    const EVIDENCE_LAYER_META = {
      realtime: { key: "实时证据", title: "实时链路证据", hint: "随扫描即时产生", className: "realtime" },
      async: { key: "异步证据", title: "异步补充证据", hint: "盘后/任务补全", className: "async" }
    };
    const EVIDENCE_TYPE_META = {
      facts: { label: "关键事实", className: "stock", source: "事实卡", priority: 0, limit: 120, maxItems: 3 },
      move: { label: "异动解释", className: "summary", source: "模型解释", priority: 1, limit: 100, maxItems: 1 },
      quality: { label: "异动质量", className: "event", source: "模型判断", priority: 2, limit: 100, maxItems: 1 },
      period: { label: "区间领头", className: "theme", source: "问财区间排名", priority: 3, limit: 120, maxItems: 3 },
      initiative: { label: "主动性", className: "theme", source: "扫描触发", priority: 3, limit: 120, maxItems: 3 },
      influence: { label: "带动性", className: "event", source: "同锚扩散", priority: 4, limit: 140, maxItems: 8 },
      lhb: { label: "龙虎榜席位", className: "event", source: "东方财富龙虎榜", priority: 3, limit: 130, maxItems: 4 },
      anchor: { label: "锚点一致性", className: "theme", source: "模型判断", priority: 3, limit: 120, maxItems: 1 },
      support: { label: "核心支撑", className: "stock", source: "模型筛选", priority: 4, limit: 110, maxItems: 2 },
      counter: { label: "瑕疵", className: "announcement", source: "模型判断", priority: 6, limit: 110, maxItems: 1 },
      final: { label: "核心结论", className: "summary", source: "模型结论", priority: 5, limit: 100, maxItems: 1 },
      timeliness: { label: "时效判断", className: "event", source: "模型判断", priority: 8, limit: 120, maxItems: 1 },
      flaw: { label: "最大瑕疵", className: "announcement", source: "事实卡", priority: 8, limit: 120, maxItems: 1 },
      gap: { label: "证据缺口", className: "event", source: "事实卡", priority: 9, limit: 110, maxItems: 3 },
      core: { label: "核心证据", className: "stock", source: "过滤后证据", priority: 9, limit: 130, maxItems: 3 },
      impact: { label: "影响要素", className: "impact", source: "模型判断", priority: 10, limit: 130, maxItems: 3 },
      summary: { label: "异步总结", className: "summary", source: "模型总结", priority: 20, limit: 120, maxItems: 1 },
      realtime: { label: "实时判断", className: "theme", source: "实时扫描", priority: 25, limit: 160, maxItems: 1 },
      theme: { label: "题材证据", className: "theme", source: "题材解释", priority: 30, limit: 160, maxItems: 2 },
      stock: { label: "个股证据", className: "stock", source: "个股解释", priority: 40, limit: 160, maxItems: 2 },
      announcement: { label: "公告", className: "announcement", source: "公告", priority: 80, limit: 120, maxItems: 1 },
      event: { label: "事件", className: "event", source: "事件", priority: 90, limit: 120, maxItems: 1 }
    };
    const EVIDENCE_LABEL_TYPE = {
      "关键事实": "facts",
      "异动解释": "move",
      "异动质量": "quality",
      "区间领头": "period",
      "主动性": "initiative",
      "带动性": "influence",
      "龙虎榜席位": "lhb",
      "锚点一致性": "anchor",
      "核心支撑": "support",
      "持续依据": "support",
      "瑕疵": "counter",
      "核心结论": "final",
      "时效判断": "timeliness",
      "最大瑕疵": "flaw",
      "证据缺口": "gap",
      "核心证据": "core",
      "影响要素": "impact",
      "异步总结": "summary",
      "实时判断": "realtime",
      "题材证据": "theme",
      "题材": "theme",
      "个股证据": "stock",
      "公告": "announcement",
      "事件": "event"
    };
    function evidenceMeta(partOrType) {
      const raw = typeof partOrType === "string" ? partOrType : (partOrType.type || EVIDENCE_LABEL_TYPE[partOrType.label] || "");
      return EVIDENCE_TYPE_META[raw] || EVIDENCE_TYPE_META.event;
    }
    function tagText(tag) {
      return clean(tag).replace(/\|\d{6}$/, "");
    }
    function tagStockCode(tag) {
      const match = clean(tag).match(/\|(\d{6})$/);
      return match ? match[1] : "";
    }
    function tagClass(tag) {
      const text = tagText(tag);
      if (text.startsWith("锚点:")) return "tag anchor";
      if (["领涨", "领涨中军", "先锋", "高标"].includes(text)) return "tag role";
      if (text.startsWith("领涨:") || text.startsWith("全池领涨:") || text.startsWith("局部领涨:")) return "tag role";
      if (text === "中军" || text.startsWith("中军:") || text.startsWith("全池中军:") || text.startsWith("局部中军:")) return "tag core";
      if (["孤立脉冲", "锚点成员"].includes(text) || text.startsWith("出现:")) return "tag weak";
      return "tag";
    }
    function tagHtml(tag) {
      const text = tagText(tag);
      const code = tagStockCode(tag);
      const cls = tagClass(tag);
      if (code && /^(领涨|全池领涨|局部领涨|中军|全池中军|局部中军):/.test(text)) {
        return `<a class="${cls}" href="${stockUrl(code)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>`;
      }
      return `<span class="${cls}">${esc(text)}</span>`;
    }
    function tagPriority(tag) {
      const text = tagText(tag);
      if (text.startsWith("锚点:")) return 10;
      if (["领涨", "领涨中军", "中军", "先锋", "高标", "孤立脉冲", "锚点成员"].includes(text)) return 20;
      if (/^(领涨|全池领涨|局部领涨):/.test(text)) return 30;
      if (/^(中军|全池中军|局部中军):/.test(text)) return 40;
      if (text.startsWith("出现:")) return 50;
      return 100;
    }
    function isStrongTag(tag) {
      const text = tagText(tag);
      if (text.startsWith("锚点:")) return !text.includes("未锚定");
      if (["领涨", "领涨中军", "中军", "先锋", "高标", "孤立脉冲", "锚点成员"].includes(text)) return true;
      if (/^(领涨|全池领涨|局部领涨|中军|全池中军|局部中军):/.test(text)) return true;
      return false;
    }
    function visibleTagsFor(row) {
      const tags = parseTags(row.tags).map(clean).filter(Boolean);
      const anchor = anchorText(row);
      return [
        anchor && anchor !== "未锚定" ? `锚点:${anchor}` : "",
        ...tags
      ].filter(Boolean)
        .filter(tag => tagText(tag) !== "证据:pending")
        .filter(isStrongTag)
        .filter((tag, index, arr) => arr.indexOf(tag) === index)
        .sort((a, b) => tagPriority(a) - tagPriority(b))
        .slice(0, 8);
    }
    function normalizeEvidenceRaw(row) {
      let raw = String(row.detail ?? "").replace(/\r/g, "").trim();
      raw = raw.replace(/^【亮点】[^\n]*(\n|$)/, "");
      const marker = raw.search(/【实时证据】|【异步证据】|异步总结：|影响要素：|题材证据：|个股证据：|事件：|公告：|题材：/);
      if (marker >= 0) raw = raw.slice(marker);
      else raw = "";
      return raw.replace(/\n{3,}/g, "\n\n").trim();
    }
    function evidenceType(label) {
      return EVIDENCE_LABEL_TYPE[label] || "event";
    }
    function evidenceSource(label, body) {
      const meta = EVIDENCE_TYPE_META[evidenceType(label)] || EVIDENCE_TYPE_META.event;
      if (["影响要素", "异步总结", "实时判断", "题材证据", "个股证据", "公告", "事件"].includes(label)) return meta.source;
      if (/互动易/.test(body)) return "互动易";
      if (/公告/.test(body) || label === "公告") return "公告";
      if (/年报|半年报|季报/.test(body)) return "定期报告";
      if (/官微|公众号/.test(body)) return "官微";
      return meta.source;
    }
    function parseJsonArray(value) {
      if (Array.isArray(value)) return value;
      if (!value) return [];
      if (typeof value === "string") {
        try {
          const parsed = JSON.parse(value);
          return Array.isArray(parsed) ? parsed : [];
        } catch {
          return [];
        }
      }
      return [];
    }
    function parseJsonObject(value) {
      if (value && typeof value === "object" && !Array.isArray(value)) return value;
      if (!value || typeof value !== "string") return {};
      try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      } catch {
        return {};
      }
    }
    function evidenceView(row) {
      const view = parseJsonObject(row.evidence_view);
      return Number(view.schema_version) >= 1 ? view : {};
    }
    function normalizeEvidenceLayer(layer) {
      const text = clean(layer);
      if (text === "async" || text === "异步证据" || text === "异步补充证据") return "异步证据";
      return "实时证据";
    }
    function normalizeEvidenceItem(item) {
      const label = clean(item.label || "");
      const body = clean(item.body || item.text || "");
      if (!label || !body) return null;
      return {
        layer: normalizeEvidenceLayer(item.layer),
        label,
        body,
        source: clean(item.source || ""),
        type: clean(item.type || evidenceType(label)),
        priority: Number(item.priority ?? evidenceMeta(evidenceType(label)).priority),
        payload: item.payload ?? null
      };
    }
    function clampText(text, limit) {
      const value = clean(text);
      if (value.length <= limit) return value;
      return `${value.slice(0, Math.max(0, limit - 1))}…`;
    }
    function conciseEvidenceBody(label, body) {
      const lines = String(body || "").split(/\n+/).map(clean).filter(Boolean);
      const deduped = lines.filter((line, index, arr) => arr.indexOf(line) === index);
      const meta = evidenceMeta(evidenceType(label));
      if (meta.maxItems <= 1) return clampText(deduped.join("；"), meta.limit);
      return deduped.slice(0, meta.maxItems).map(line => clampText(line, meta.limit)).join("\n");
    }
    function evidencePartPriority(part) {
      if (part.layer === "异步证据") {
        const preferred = {
          "关键事实": 0,
          "龙虎榜席位": 1,
          "区间领头": 2,
          "主动性": 3,
          "带动性": 4,
          "持续依据": 5,
          "最大瑕疵": 4,
          "证据缺口": 5,
          "持续性": 6,
          "异动解释": 7,
          "核心证据": 20,
          "影响要素": 21,
          "核心支撑": 30,
          "瑕疵": 31,
          "时效判断": 32,
          "核心结论": 40,
          "异动质量": 41,
          "锚点一致性": 42,
          "异步总结": 50
        };
        if (Object.prototype.hasOwnProperty.call(preferred, part.label)) return preferred[part.label];
      }
      if (Number.isFinite(Number(part.priority))) return Number(part.priority);
      return evidenceMeta(part).priority || 100;
    }
    function isUsefulDecisionPart(part) {
      const useful = new Set(["关键事实", "龙虎榜席位", "区间领头", "主动性", "带动性", "持续依据", "最大瑕疵", "证据缺口", "持续性"]);
      if (!useful.has(part.label)) return false;
      if (part.label === "持续性" && /走弱|风险-[1-9]/.test(part.body || "")) return true;
      if (part.label === "持续性") return false;
      if (part.label === "证据缺口" && !clean(part.body)) return false;
      return true;
    }
    function curateEvidenceParts(parts, layer) {
      let scoped = parts.filter(part => part.layer === layer);
      if (layer === "实时证据" && parts.some(part => part.layer === "异步证据" && isUsefulDecisionPart(part))) {
        return [];
      }
      if (layer === "异步证据") {
        const decisionParts = scoped.filter(isUsefulDecisionPart);
        if (decisionParts.length) {
          return decisionParts
            .map(part => ({ ...part, body: conciseEvidenceBody(part.label, part.body) }))
            .filter(part => part.body)
            .sort((a, b) => evidencePartPriority(a) - evidencePartPriority(b))
            .slice(0, 6);
        }
      }
      const hasImpact = scoped.some(part => part.label === "影响要素");
      const hasCore = scoped.some(part => part.label === "核心证据");
      const hasJudgement = hasImpact || scoped.some(part => part.label === "异步总结");
      if (layer === "异步证据" && hasImpact) {
        scoped = scoped.filter(part => part.label !== "异步总结");
      }
      if (layer === "异步证据" && (hasCore || hasImpact)) {
        scoped = scoped.filter(part => !["核心结论", "异动质量", "锚点一致性"].includes(part.label));
      }
      if (layer === "异步证据" && hasJudgement) {
        scoped = scoped.filter(part => !["公告", "事件", "题材"].includes(part.label));
      }
      return scoped
        .map(part => ({ ...part, body: conciseEvidenceBody(part.label, part.body) }))
        .filter(part => part.body)
        .sort((a, b) => evidencePartPriority(a) - evidencePartPriority(b))
        .slice(0, layer === "异步证据" ? 6 : 1);
    }
    function impactLineHtml(line) {
      const match = clean(line).match(/^([^/：]+)(?:\/([^/：]+))?(?:\/([^：]+))?：(.+)$/);
      if (!match) return `<div class="impact-item"><div class="impact-evidence">${esc(line)}</div></div>`;
      const factor = match[1] || "影响";
      const direction = match[2] || "";
      const importance = match[3] || "";
      const evidence = match[4] || "";
      const dirClass = direction.includes("正") ? "up" : direction.includes("负") ? "down" : "";
      return `<div class="impact-item">
        <div class="impact-meta">
          <span class="impact-badge">${esc(factor)}</span>
          ${direction ? `<span class="impact-badge ${dirClass}">${esc(direction)}</span>` : ""}
          ${importance ? `<span class="impact-badge">${esc(importance)}</span>` : ""}
        </div>
        <div class="impact-evidence">${esc(evidence)}</div>
      </div>`;
    }
    function influenceLineHtml(line) {
      const text = clean(line);
      if (!text) return "";
      if (text.startsWith("时间序列：") || text.startsWith("活跃序列：") || text.startsWith("扩散股：")) {
        const [title, ...rest] = text.split("：");
        const body = rest.join("：");
        return `<details class="mini-fold">
          <summary>${esc(title)}</summary>
          <div class="mini-fold-body">${esc(body || text)}</div>
        </details>`;
      }
      const cls = /强扩散|冷启动|首发质量：强|疑似带动强|具备/.test(text)
        ? "good"
        : /热启动|晚启动|后排|弱|缺|不足/.test(text)
        ? "warn"
        : "weak";
      const labelMatch = text.match(/^([^：；]+)[：；](.+)$/);
      if (labelMatch) {
        return `<span class="evidence-chip ${cls}">${esc(labelMatch[1])}：${esc(clampText(labelMatch[2], 80))}</span>`;
      }
      return `<span class="evidence-chip ${cls}">${esc(clampText(text, 90))}</span>`;
    }
    function evidenceBodyHtml(part) {
      if (part.label === "带动性") {
        const structured = Array.isArray(part.payload) ? part.payload : [];
        const lines = structured.length
          ? structured.slice(0, 8).map(item => clampText(typeof item === "string" ? item : item.reason || item.evidence || "", 150)).filter(Boolean)
          : String(part.body || "").split(/\n+/).map(clean).filter(Boolean);
        return `<div class="impact-list compact">${lines.map(influenceLineHtml).filter(Boolean).join("")}</div>`;
      }
      if (part.label === "关键事实" || part.label === "区间领头" || part.label === "主动性" || part.label === "带动性" || part.label === "龙虎榜席位" || part.label === "证据缺口") {
        const structured = Array.isArray(part.payload) ? part.payload : [];
        const maxLines = part.label === "带动性" ? 8 : 3;
        const lines = structured.length
          ? structured.slice(0, maxLines).map(item => clampText(typeof item === "string" ? item : item.reason || item.evidence || "", part.label === "带动性" ? 140 : 120)).filter(Boolean)
          : String(part.body || "").split(/\n+/).map(clean).filter(Boolean);
        return `<div class="impact-list">${lines.map(line => `<div class="impact-item"><div class="impact-evidence">${esc(line)}</div></div>`).join("")}</div>`;
      }
      if (part.label === "核心证据") {
        const structured = Array.isArray(part.payload) ? part.payload : [];
        const lines = structured.length
          ? structured.slice(0, 3).map(item => {
              const title = clampText(item.title || item.reason || "", 72);
              const reason = clampText(item.reason || "", 95);
              const when = clean(item.source_date || item.timeliness || "");
              return `${when ? `${when} ` : ""}${title}${reason && reason !== title ? `：${reason}` : ""}`;
            })
          : String(part.body || "").split(/\n+/).map(clean).filter(Boolean);
        return `<div class="impact-list">${lines.map(line => `<div class="impact-item"><div class="impact-evidence">${esc(line)}</div></div>`).join("")}</div>`;
      }
      if (part.label === "影响要素") {
        const structured = Array.isArray(part.payload) ? part.payload : [];
        const lines = structured.length
          ? structured.slice(0, 3).map(item => {
              const factor = clean(item.factor_type || "影响");
              const direction = clean(item.direction || "");
              const importance = clean(item.importance || "");
              const evidence = clampText(item.evidence || "", 130);
              return `${factor}${direction ? `/${direction}` : ""}${importance ? `/${importance}` : ""}：${evidence}`;
            })
          : String(part.body || "").split(/\n+/).map(clean).filter(Boolean);
        return `<div class="impact-list">${lines.map(impactLineHtml).join("")}</div>`;
      }
      return `<div class="evidence-text">${escBlock(part.body)}</div>`;
    }
    function evidenceSectionClass(part) {
      if (part.class_name) return part.class_name;
      if (part.className) return part.className;
      return evidenceMeta(part).className;
    }
    function evidenceParts(row) {
      const structured = parseJsonArray(row.evidence_items)
        .map(normalizeEvidenceItem)
        .filter(Boolean);
      if (structured.length) {
        return structured.filter((part, index, arr) =>
          !arr.some((prev, prevIndex) => prevIndex < index && prev.layer === part.layer && prev.label === part.label && prev.body === part.body)
        );
      }
      const raw = normalizeEvidenceRaw(row);
      if (!raw) return [];
      const parts = [];
      const pattern = /(【实时证据】|【异步证据】)|(异步总结|影响要素|题材证据|个股证据|事件|公告|题材)：/g;
      const matches = [...raw.matchAll(pattern)];
      let currentLayer = "实时证据";
      for (let i = 0; i < matches.length; i += 1) {
        if (matches[i][1]) {
          currentLayer = matches[i][1].replace(/[【】]/g, "");
          continue;
        }
        const label = matches[i][2];
        const start = matches[i].index || 0;
        const bodyStart = start + matches[i][0].length;
        let end = raw.length;
        for (let j = i + 1; j < matches.length; j += 1) {
          if (matches[j][1] || matches[j][2]) {
            end = matches[j].index || raw.length;
            break;
          }
        }
        let body = clean(raw.slice(bodyStart, end));
        body = body.replace(/【实时证据】|【异步证据】/g, "").trim();
        if (body && !parts.some(part => part.layer === currentLayer && part.label === label && part.body === body)) {
          parts.push({ layer: currentLayer, label, body });
        }
      }
      return parts;
    }
    function sectionCollapsed(part) {
      if (part.layer !== "异步证据") return false;
      return ["核心支撑", "核心证据", "影响要素", "锚点一致性", "异动质量", "时效判断", "核心结论"].includes(part.label);
    }
    function evidenceSectionHtml(part) {
      const title = part.label === "题材" ? "题材要点" : part.label;
      const head = `<div class="evidence-heading">
        <span>${esc(title)}</span>
        <span class="evidence-source">${esc(part.source || evidenceSource(part.label, part.body))}</span>
      </div>`;
      const body = evidenceBodyHtml(part);
      if (sectionCollapsed(part)) {
        return `<details class="evidence-fold ${evidenceSectionClass(part)}">
          <summary>${head}</summary>
          <div class="evidence-fold-body">${body}</div>
        </details>`;
      }
      return `<section class="evidence-section ${evidenceSectionClass(part)}">
        ${head}
        ${body}
      </section>`;
    }
    function evidenceLayerTitle(layer) {
      return layer === "异步证据" ? "短线决策证据" : EVIDENCE_LAYER_META.realtime.title;
    }
    function evidenceLayerHint(layer) {
      return layer === "异步证据" ? "只保留有用信息" : EVIDENCE_LAYER_META.realtime.hint;
    }
    function evidenceHtml(row) {
      const view = evidenceView(row);
      const viewLayers = Array.isArray(view.layers) ? view.layers : [];
      const renderedLayers = viewLayers
        .map(group => ({
          layer: clean(group.layer || ""),
          title: clean(group.title || ""),
          hint: clean(group.hint || ""),
          className: clean(group.class_name || group.className || group.layer || ""),
          sections: Array.isArray(group.sections) ? group.sections : []
        }))
        .filter(group => group.sections.length);
      if (renderedLayers.length) {
        return `<div class="evidence-block">${renderedLayers.map(group => `<section class="evidence-layer ${esc(group.className)}">
          <div class="evidence-layer-title">
            <span>${esc(group.title || (group.layer === "async" ? "短线决策证据" : "实时链路证据"))}</span>
            <small>${esc(group.hint || "")}</small>
          </div>
          ${group.sections.map(evidenceSectionHtml).join("")}
        </section>`).join("")}</div>`;
      }
      const parts = evidenceParts(row);
      if (!parts.length) return `<div class="detail-empty">暂无证据详情</div>`;
      const layers = ["实时证据", "异步证据"].map(layer => ({
        layer,
        parts: curateEvidenceParts(parts, layer)
      })).filter(group => group.parts.length);
      return `<div class="evidence-block">${layers.map(group => `<section class="evidence-layer ${group.layer === "异步证据" ? "async" : "realtime"}">
        <div class="evidence-layer-title">
          <span>${esc(evidenceLayerTitle(group.layer))}</span>
          <small>${esc(evidenceLayerHint(group.layer))}</small>
        </div>
        ${group.parts.map(evidenceSectionHtml).join("")}
      </section>`).join("")}</div>`;
    }
    function evidenceLevelInfo(row) {
      const tags = parseTags(row.tags).map(clean).filter(Boolean).map(tagText);
      const explicit = tags.find(tag => tag.startsWith("证据:"));
      const value = explicit ? explicit.slice("证据:".length) : "";
      const raw = String(row.detail ?? "");
      const parts = evidenceParts(row);
      const hasAnnouncement = /公告|年报|半年报|互动易|问询|回复/.test(raw);
      const hasDirectEvidence = parts.some(part => ["题材证据", "个股证据"].includes(part.label));
      const hasImpact = parts.some(part => part.label === "影响要素");
      const hasRealtime = parts.some(part => part.layer === "实时证据");
      const hasAsync = parts.some(part => part.layer === "异步证据");
      if (value && value !== "pending") return { level: value, cls: value.includes("强") ? "strong" : "", basis: `${hasRealtime ? "实时链路" : ""}${hasRealtime && hasAsync ? " + " : ""}${hasAsync ? "异步补充" : ""} · 来自证据层标记` };
      if (hasImpact) return { level: "强", cls: "strong", basis: `${hasRealtime ? "实时链路" : ""}${hasRealtime && hasAsync ? " + " : ""}${hasAsync ? "异步补充" : ""} · 已提取影响股价要素` };
      if (hasAnnouncement && hasDirectEvidence) return { level: "强", cls: "strong", basis: `${hasRealtime ? "实时链路" : ""}${hasRealtime && hasAsync ? " + " : ""}${hasAsync ? "异步补充" : ""} · 公司披露/互动易 + 题材解释` };
      if (hasDirectEvidence || parts.length) return { level: "中", cls: "", basis: `${hasRealtime ? "实时链路" : ""}${hasRealtime && hasAsync ? " + " : ""}${hasAsync ? "异步补充" : ""} · 题材/个股解释` };
      return { level: "待补全", cls: "weak", basis: "暂无可核验证据" };
    }
    function cleanJudgementText(text) {
      return clean(text)
        .replace(/^最强解释是/, "")
        .replace(/^异动主要由/, "")
        .replace(/^异动主要因/, "")
        .replace(/^因/, "")
        .replace(/。$/, "");
    }
    function evidenceFactText(item) {
      if (!item || typeof item !== "object") return "";
      const reason = clean(item.reason || item.evidence || "");
      const title = clean(item.title || item.factor_type || "");
      const date = clean(item.source_date || "");
      let text = reason || title;
      if (!text) return "";
      if (title && reason && !reason.includes(title) && title.length <= 18 && !/公告|报告|披露|资料|龙虎榜/.test(reason)) {
        text = `${title}：${reason}`;
      }
      return clampText(`${date ? `${date} ` : ""}${text}`, 92);
    }
    function evidenceSummaryFacts(parts) {
      const facts = [];
      const addFact = value => {
        const text = clampText(clean(value), 92);
        if (!text) return;
        if (/逻辑|较硬|推论|判断|解释强度/.test(text) && !/\d|同比|净买|订单|合同|客户|供货|中标|业绩|净利|营收|龙虎榜/.test(text)) return;
        if (!facts.some(item => item === text || item.includes(text) || text.includes(item))) facts.push(text);
      };
      const factPart = parts.find(part => part.label === "关键事实");
      const factPayload = Array.isArray(factPart?.payload) ? factPart.payload : [];
      factPayload.slice(0, 3).forEach(addFact);
      if (factPart?.body) {
        String(factPart.body).split(/\n+/).slice(0, 3).forEach(addFact);
      }
      const periodPart = parts.find(part => part.label === "区间领头");
      if (periodPart?.body) {
        String(periodPart.body).split(/\n+/).slice(0, 2).forEach(addFact);
      }
      const lhbPart = parts.find(part => part.label === "龙虎榜席位");
      if (lhbPart?.body) {
        String(lhbPart.body).split(/\n+/).slice(0, 2).forEach(addFact);
      }
      const corePart = parts.find(part => part.label === "核心证据");
      const corePayload = Array.isArray(corePart?.payload) ? corePart.payload : [];
      corePayload.slice(0, 5).forEach(item => addFact(evidenceFactText(item)));
      const impactPart = parts.find(part => part.label === "影响要素");
      const impactPayload = Array.isArray(impactPart?.payload) ? impactPart.payload : [];
      impactPayload.slice(0, 5).forEach(item => addFact(evidenceFactText(item)));
      const supportPart = parts.find(part => part.label === "核心支撑");
      const supportPayload = Array.isArray(supportPart?.payload) ? supportPart.payload : [];
      supportPayload.slice(0, 3).forEach(item => addFact(typeof item === "string" ? item : evidenceFactText(item)));
      if (!facts.length && supportPart?.body) {
        String(supportPart.body).split(/\n+/).slice(0, 2).forEach(addFact);
      }
      if (!facts.length && impactPart?.body) {
        String(impactPart.body).split(/\n+/).slice(0, 2).forEach(line => {
          const match = clean(line).match(/：(.+)$/);
          addFact(match ? match[1] : line);
        });
      }
      return facts.slice(0, 3);
    }
    function lineWithPrefix(part, prefix) {
      if (!part) return "";
      const payloadLines = Array.isArray(part.payload)
        ? part.payload.map(item => clean(typeof item === "string" ? item : item?.reason || item?.evidence || "")).filter(Boolean)
        : [];
      const bodyLines = String(part.body || "").split(/\n+/).map(clean).filter(Boolean);
      return [...payloadLines, ...bodyLines].find(line => line.startsWith(prefix)) || "";
    }
    function stripPrefix(line) {
      const idx = clean(line).indexOf("：");
      return idx >= 0 ? clean(line).slice(idx + 1) : clean(line);
    }
    function decisionCard(label, value, cls = "") {
      if (!value) return "";
      return `<div class="decision-card ${esc(cls)}">
        <span class="decision-label">${esc(label)}</span>
        <span class="decision-value" title="${esc(value)}">${esc(value)}</span>
      </div>`;
    }
    function displayContract(row) {
      const contract = parseJsonObject(row.display_contract);
      return Number(contract.schema_version) >= 1 ? contract : {};
    }
    function contractDecisionCards(contract) {
      const cards = Array.isArray(contract.decision_cards) ? contract.decision_cards : [];
      return cards.slice(0, 4)
        .map(card => decisionCard(clean(card.label), clean(card.value), clean(card.tone || "")))
        .join("");
    }
    function contractChips(contract) {
      const chips = Array.isArray(contract.chips) ? contract.chips : [];
      return chips.slice(0, 5).map(chip => {
        const label = clean(chip.label);
        const value = clean(chip.value);
        if (!label || !value) return "";
        return `<span class="evidence-chip ${esc(clean(chip.tone || "weak"))}">${esc(label)} ${esc(value)}</span>`;
      }).join("");
    }
    function evidenceSummaryHtml(row) {
      const view = evidenceView(row);
      const summary = view && typeof view.summary === "object" && !Array.isArray(view.summary) ? view.summary : null;
      if (summary) {
        const cards = Array.isArray(summary.cards) ? summary.cards : [];
        const chips = Array.isArray(summary.chips) ? summary.chips : [];
        const facts = Array.isArray(summary.facts) ? summary.facts.map(clean).filter(Boolean).slice(0, 3) : [];
        const reason = clampText(summary.reason || "", 90);
        const flaw = clampText(summary.flaw || "", 90);
        const gap = clampText(summary.gap || "", 110);
        return `<div class="evidence-summary">
          <div class="evidence-verdict">
            <strong>${esc(clean(summary.title || (reason ? "异动原因" : facts.length ? "关键事实" : "证据强度")))}</strong>
            <span class="evidence-level ${esc(clean(summary.level_class || ""))}">${esc(clean(summary.level || "待补全"))}</span>
          </div>
          ${reason ? `<div class="evidence-judgement"><span>原因</span>${esc(reason)}</div>` : ""}
          <div class="decision-grid">
            ${cards.slice(0, 4).map(card => decisionCard(clean(card.label), clean(card.value), clean(card.tone || ""))).join("")}
          </div>
          <div class="evidence-chip-row">
            ${chips.slice(0, 5).map(chip => {
              const label = clean(chip.label);
              const value = clean(chip.value);
              return label && value ? `<span class="evidence-chip ${esc(clean(chip.tone || "weak"))}">${esc(label)} ${esc(value)}</span>` : "";
            }).join("")}
          </div>
          ${facts.length ? `<ul class="evidence-facts">${facts.map(fact => `<li class="evidence-fact">${esc(fact)}</li>`).join("")}</ul>` : ""}
          ${flaw ? `<div class="evidence-judgement"><span>瑕疵</span>${esc(flaw)}</div>` : ""}
          <div class="evidence-basis">${esc(clean(summary.basis || ""))}</div>
          ${gap ? `<div class="evidence-gap">${esc(gap)}</div>` : ""}
        </div>`;
      }
      const info = evidenceLevelInfo(row);
      const parts = evidenceParts(row);
      const contract = displayContract(row);
      const movePart = parts.find(part => part.label === "异动解释");
      const finalPart = parts.find(part => part.label === "核心结论");
      const impact = parts.find(part => part.label === "影响要素");
      const facts = evidenceSummaryFacts(parts);
      const firstLine = part => part?.body ? clampText(String(part.body || "").split(/\n+/).map(clean).filter(Boolean)[0] || "", 96) : "";
      const judgement = movePart
        ? clampText(cleanJudgementText(movePart.body), 90)
        : finalPart
        ? clampText(cleanJudgementText(finalPart.body), 90)
        : impact ? clampText(String(impact.body || "").split(/\n+/).map(clean).filter(Boolean)[0] || "", 90) : "";
      const sustainPart = parts.find(part => part.label === "持续依据") || parts.find(part => part.label === "区间领头");
      const sustain = firstLine(sustainPart);
      const lhbPart = parts.find(part => part.label === "龙虎榜席位");
      const initiativePart = parts.find(part => part.label === "主动性");
      const influencePart = parts.find(part => part.label === "带动性");
      const qualityPart = parts.find(part => part.label === "持续性");
      const influencePosition = stripPrefix(firstLine(influencePart));
      const preheat = stripPrefix(lineWithPrefix(influencePart, "本股启动前"));
      const firstQuality = stripPrefix(lineWithPrefix(influencePart, "首发质量"));
      const spread = stripPrefix(lineWithPrefix(influencePart, "扩散"));
      const lhb = firstLine(lhbPart);
      const initiative = firstLine(initiativePart);
      const tapeLine = [lhb, initiative].filter(Boolean).slice(0, 2).join("；");
      const flawPart = parts.find(part => part.label === "最大瑕疵");
      const gapPart = parts.find(part => part.label === "证据缺口");
      const flaw = flawPart ? clampText(flawPart.body, 90) : "";
      const firstGap = gapPart ? clampText(String(gapPart.body || "").split(/\n+/).map(clean).filter(Boolean)[0] || "", 90) : "";
      const gap = info.level === "待补全"
        ? "建议补充：公告、互动易、定期报告或同花顺题材解释。"
        : flaw || firstGap ? "" : impact ? "" : parts.some(part => ["公告", "事件"].includes(part.label)) ? "" : "可继续补充公告/互动易作为硬证据。";
      const contractCards = contractDecisionCards(contract);
      const contractChipHtml = contractChips(contract);
      return `<div class="evidence-summary">
        <div class="evidence-verdict">
          <strong>${judgement ? "异动原因" : facts.length ? "关键事实" : "证据强度"}</strong>
          <span class="evidence-level ${esc(info.cls)}">${esc(info.level)}</span>
        </div>
        ${judgement ? `<div class="evidence-judgement"><span>原因</span>${esc(judgement)}</div>` : ""}
        <div class="decision-grid">
          ${contractCards || `
            ${decisionCard("持续性", firstLine(qualityPart) || info.level)}
            ${decisionCard("带动性", influencePosition)}
            ${decisionCard("启动前", preheat)}
            ${decisionCard("首发", firstQuality)}
          `}
        </div>
        <div class="evidence-chip-row">
          ${contractChipHtml || `
            ${spread ? `<span class="evidence-chip ${/强扩散/.test(spread) ? "good" : /弱扩散/.test(spread) ? "warn" : "weak"}">扩散 ${esc(spread)}</span>` : ""}
            ${sustain ? `<span class="evidence-chip good">持续 ${esc(sustain)}</span>` : ""}
            ${tapeLine ? `<span class="evidence-chip weak">盘口/资金 ${esc(tapeLine)}</span>` : ""}
          `}
        </div>
        ${facts.length ? `<ul class="evidence-facts">${facts.map(fact => `<li class="evidence-fact">${esc(fact)}</li>`).join("")}</ul>` : ""}
        ${flaw ? `<div class="evidence-judgement"><span>瑕疵</span>${esc(flaw)}</div>` : firstGap ? `<div class="evidence-judgement"><span>缺口</span>${esc(firstGap)}</div>` : ""}
        <div class="evidence-basis">${esc(info.basis)}</div>
        ${gap ? `<div class="evidence-gap">${esc(gap)}</div>` : ""}
      </div>`;
    }
    function pctText(value) {
      const number = Number(value);
      if (!Number.isFinite(number)) return "";
      return `${number > 0 ? "+" : ""}${number.toFixed(2)}%`;
    }
    function metricChipsHtml(row) {
      const change = pctText(row.change_pct);
      const speed = pctText(row.speed_pct);
      const changeLabel = row.kind === "window" ? "高" : "现";
      return [
        change ? `<span class="metric-chip change">${esc(changeLabel)} ${esc(change)}</span>` : "",
        speed ? `<span class="metric-chip speed">速 ${esc(speed)}</span>` : ""
      ].filter(Boolean).join("");
    }
    function highlightText(row) {
      const summary = clean(row.summary || "");
      if (!summary.startsWith("【亮点】")) return "";
      return clean(summary.slice("【亮点】".length).split("；")[0]);
    }
    function renderDetail(row) {
      if (!row) {
        $("detailTime").textContent = "";
        $("detailPanel").innerHTML = `<div class="detail-empty">暂无详情</div>`;
        return;
      }
      $("detailTime").textContent = `${stockTitleText(row)} · ${timeText(row.event_time)}`;
      $("detailPanel").innerHTML = `<div class="detail-body">
        ${evidenceSummaryHtml(row)}
        ${evidenceHtml(row)}
      </div>`;
    }
    function streamItemHtml(row) {
      const key = rowKey(row);
      const active = key === selectedKey ? " active" : "";
      const highlight = highlightText(row);
      const visibleTags = visibleTagsFor(row);
      return `<article class="stream-item${active}" data-select-key="${esc(key)}">
        <div class="stream-title">${stockAnchorHtml(row)}</div>
        <div class="stream-metric">${metricChipsHtml(row)}</div>
        <div class="stream-tags">${visibleTags.map(tagHtml).join("")}</div>
        ${highlight ? `<div class="stream-highlight">${esc(highlight)}</div>` : `<div class="stream-highlight">暂无公司亮点</div>`}
      </article>`;
    }
    function groupKey(row) {
      return `${clean(row.event_time)}|${clean(row.kind_label || row.kind)}`;
    }
    function groupTitleHtml(row) {
      const rows = arguments.length > 1 ? arguments[1] : [row];
      const anchors = [];
      for (const item of rows) {
        const anchor = anchorText(item);
        if (anchor && anchor !== "未锚定" && !anchors.includes(anchor)) anchors.push(anchor);
      }
      const anchorSummary = anchors.length ? `主锚点 ${anchors.slice(0, 3).join(" / ")}` : "未锚定";
      const summary = `${rows.length}只 · ${anchorSummary}`;
      return `<div class="stream-group-title">
        <span>${esc(timeText(row.event_time))}</span>
        <span class="kind ${esc(row.kind)}">${esc(row.kind_label || row.kind)}</span>
        <span class="group-summary">${esc(summary)}</span>
      </div>`;
    }
    function groupedFeedHtml(rows) {
      const groups = [];
      for (const row of rows) {
        const key = groupKey(row);
        let group = groups[groups.length - 1];
        if (!group || group.key !== key) {
          group = { key, first: row, rows: [] };
          groups.push(group);
        }
        group.rows.push(row);
      }
      return groups.map(group => `<section class="stream-group">
        ${groupTitleHtml(group.first, group.rows)}
        ${group.rows.map(streamItemHtml).join("")}
      </section>`).join("");
    }
    function renderList(id, rows, emptyText) {
      if (rows.error) {
        $(id).innerHTML = `<div class="error">${esc(rows.error)}</div>`;
      } else if (!rows.length) {
        $(id).innerHTML = `<div class="empty">${esc(emptyText)}</div>`;
      } else {
        $(id).innerHTML = groupedFeedHtml(rows);
      }
    }
    let availableDates = [];
    let latestTradeDate = "";
    function queryTradeDate() {
      const params = new URLSearchParams(window.location.search);
      return clean(params.get("trade_date"));
    }
    function setTradeDateParam(value) {
      const url = new URL(window.location.href);
      if (value) url.searchParams.set("trade_date", value);
      else url.searchParams.delete("trade_date");
      window.history.replaceState({}, "", url);
    }
    function feedUrl() {
      const tradeDate = clean($("tradeDateSelect").value || queryTradeDate());
      if (!tradeDate) return "/api/feed";
      return `/api/feed?trade_date=${encodeURIComponent(tradeDate)}`;
    }
    function renderTradeDates(selectedDate) {
      const select = $("tradeDateSelect");
      const dates = availableDates.length ? availableDates : [selectedDate].filter(Boolean);
      select.innerHTML = dates.map(day => `<option value="${esc(day)}">${esc(day)}</option>`).join("");
      if (selectedDate && dates.includes(selectedDate)) select.value = selectedDate;
      else if (latestTradeDate && dates.includes(latestTradeDate)) select.value = latestTradeDate;
    }
    async function loadTradeDates() {
      try {
        const response = await fetch("/api/trade_dates", { cache: "no-store" });
        const data = await response.json();
        availableDates = Array.isArray(data.dates) ? data.dates : [];
        latestTradeDate = clean(data.latest);
        renderTradeDates(queryTradeDate() || latestTradeDate);
      } catch {
        renderTradeDates(queryTradeDate());
      }
    }
    async function refresh() {
      try {
        const response = await fetch(feedUrl(), { cache: "no-store" });
        const data = await response.json();
        if (!queryTradeDate() && data.trade_date) {
          renderTradeDates(data.trade_date);
        }
        renderSummary(data);
        const feed = data.feed || [];
        currentFeed = Array.isArray(feed) ? feed : [];
        if (!currentFeed.some(row => rowKey(row) === selectedKey)) {
          selectedKey = rowKey(currentFeed[0] || {});
        }
        renderDetail(currentFeed.find(row => rowKey(row) === selectedKey) || currentFeed[0]);
        renderList("feed", feed, "暂无情报");
        $("refreshText").textContent = `已刷新 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`;
      } catch (error) {
        $("feed").innerHTML = `<div class="error">${esc(error)}</div>`;
        $("refreshText").textContent = "刷新失败";
      }
    }
    $("tradeDateSelect").addEventListener("change", () => {
      setTradeDateParam($("tradeDateSelect").value);
      refresh();
    });
    $("latestBtn").addEventListener("click", () => {
      const target = latestTradeDate || availableDates[0] || "";
      renderTradeDates(target);
      setTradeDateParam(target);
      refresh();
    });
    document.addEventListener("click", event => {
      const target = event.target.closest("[data-select-key]");
      if (!target) return;
      selectedKey = target.dataset.selectKey || "";
      renderDetail(currentFeed.find(row => rowKey(row) === selectedKey));
      renderList("feed", currentFeed, "暂无情报");
    });
    loadTradeDates().then(refresh);
    setInterval(refresh, 15000);
  </script>
</body>
</html>"""




def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Stock Scout web dashboard.")
    parser.add_argument("--host", default=os.environ.get("STOCK_SCOUT_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("STOCK_SCOUT_WEB_PORT", "8788")))
    add_mysql_args(parser)
    return parser.parse_args()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = parse_args()
    config = mysql_config_from_args(args)
    app = create_app(config)
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


