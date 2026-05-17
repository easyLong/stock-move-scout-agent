from __future__ import annotations

from datetime import datetime
from typing import Any

from stock_move_scout.db import MySqlConfig, run_mysql, sql_int, sql_json, sql_number, sql_string


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text_value(row: dict[str, Any], key: str) -> str:
    value = row.get(key, "")
    return "" if value is None else str(value)


def upsert_stock_sql(row: dict[str, Any]) -> str:
    code = text_value(row, "code")
    if not code:
        return ""
    name = text_value(row, "name")
    is_st = int("ST" in name.upper() or name.startswith("*ST"))
    return f"""
    INSERT INTO stocks(code, symbol, market, name, industry, sub_industry, is_st)
    VALUES({sql_string(code)}, {sql_string(text_value(row, "symbol"))}, {sql_string(text_value(row, "market"))},
           {sql_string(name)}, {sql_string(text_value(row, "industry"))}, {sql_string(text_value(row, "sub_industry"))},
           {is_st})
    ON DUPLICATE KEY UPDATE
      symbol=IF(VALUES(symbol)='', symbol, VALUES(symbol)),
      market=IF(VALUES(market)='', market, VALUES(market)),
      name=IF(VALUES(name)='', name, VALUES(name)),
      industry=IF(VALUES(industry)='', industry, VALUES(industry)),
      sub_industry=IF(VALUES(sub_industry)='', sub_industry, VALUES(sub_industry)),
      is_st=VALUES(is_st);
    """


def delete_auction_candidates_for_date(config: MySqlConfig, trade_date: str) -> None:
    run_mysql(config, f"DELETE FROM auction_candidates WHERE trade_date={sql_string(trade_date)};")


def delete_auction_trend_summary_for_date(config: MySqlConfig, trade_date: str) -> None:
    run_mysql(config, f"DELETE FROM auction_trend_summary WHERE trade_date={sql_string(trade_date)};")


def import_auction_candidate_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    trade_dates = sorted({text_value(row, "trade_date") for row in rows if text_value(row, "trade_date")})
    for trade_date in trade_dates:
        statements.append(f"DELETE FROM auction_candidates WHERE trade_date={sql_string(trade_date)};")
    for row in rows:
        trade_date = text_value(row, "trade_date")
        code = text_value(row, "code")
        if not trade_date or not code:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_candidates(
              trade_date, captured_at, rank_no, code, stock_name, auction_price, preclose,
              auction_pct, auction_amount, matched_volume, buy_pressure, industry, sub_industry,
              concepts, theme_matches, theme_score, sector_hot_count, concept_hot_count,
              resonance_score, score, risk_flags, raw_json
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(text_value(row, "captured_at") or now_text())},
              {sql_int(row.get("rank_no"))}, {sql_string(code)}, {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_number(row.get("auction_price"))}, {sql_number(row.get("preclose"))},
              {sql_number(row.get("auction_pct"))}, {sql_number(row.get("auction_amount"))},
              {sql_int(row.get("matched_volume"))}, {sql_number(row.get("buy_pressure"))},
              {sql_string(text_value(row, "industry"))}, {sql_string(text_value(row, "sub_industry"))},
              {sql_json(row.get("concepts") or [])}, {sql_json(row.get("theme_matches") or [])},
              {sql_number(row.get("theme_score"))}, {sql_int(row.get("sector_hot_count"))},
              {sql_int(row.get("concept_hot_count"))}, {sql_number(row.get("resonance_score"))},
              {sql_number(row.get("score"))}, {sql_string(text_value(row, "risk_flags"))}, {sql_json(row)}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len([row for row in rows if text_value(row, "trade_date") and text_value(row, "code")])


def import_auction_minute_analysis_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    minute_keys = sorted(
        {
            (text_value(row, "trade_date"), text_value(row, "snapshot_minute"))
            for row in rows
            if text_value(row, "trade_date") and text_value(row, "snapshot_minute")
        }
    )
    for trade_date, snapshot_minute in minute_keys:
        statements.append(
            f"""
            DELETE FROM auction_minute_analysis
            WHERE trade_date={sql_string(trade_date)}
              AND snapshot_minute={sql_string(snapshot_minute)};
            """
        )
    for row in rows:
        trade_date = text_value(row, "trade_date")
        snapshot_minute = text_value(row, "snapshot_minute")
        code = text_value(row, "code")
        analysis_kind = text_value(row, "analysis_kind")
        if not trade_date or not snapshot_minute or not code or not analysis_kind:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_minute_analysis(
              trade_date, snapshot_minute, captured_at, analysis_kind, rank_no, code, stock_name,
              auction_price, preclose, auction_pct, auction_amount, matched_volume,
              bid1, ask1, bid_vol1, ask_vol1, limit_side, limit_price, seal_volume, seal_amount,
              buy_pressure, industry, sub_industry, concepts, theme_matches, theme_score,
              sector_hot_count, concept_hot_count, resonance_score, score, risk_flags, raw_json
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(snapshot_minute)}, {sql_string(text_value(row, "captured_at") or now_text())},
              {sql_string(analysis_kind)}, {sql_int(row.get("rank_no"))}, {sql_string(code)},
              {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_number(row.get("auction_price"))}, {sql_number(row.get("preclose"))},
              {sql_number(row.get("auction_pct"))}, {sql_number(row.get("auction_amount"))},
              {sql_int(row.get("matched_volume"))}, {sql_number(row.get("bid1"))}, {sql_number(row.get("ask1"))},
              {sql_int(row.get("bid_vol1"))}, {sql_int(row.get("ask_vol1"))},
              {sql_string(text_value(row, "limit_side") or "none")}, {sql_number(row.get("limit_price"))},
              {sql_int(row.get("seal_volume"))}, {sql_number(row.get("seal_amount"))},
              {sql_number(row.get("buy_pressure"))}, {sql_string(text_value(row, "industry"))},
              {sql_string(text_value(row, "sub_industry"))}, {sql_json(row.get("concepts") or [])},
              {sql_json(row.get("theme_matches") or [])}, {sql_number(row.get("theme_score"))},
              {sql_int(row.get("sector_hot_count"))}, {sql_int(row.get("concept_hot_count"))},
              {sql_number(row.get("resonance_score"))}, {sql_number(row.get("score"))},
              {sql_string(text_value(row, "risk_flags"))}, {sql_json(row)}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len(
        [
            row
            for row in rows
            if text_value(row, "trade_date")
            and text_value(row, "snapshot_minute")
            and text_value(row, "analysis_kind")
            and text_value(row, "code")
        ]
    )


def import_auction_trend_summary_rows(config: MySqlConfig, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    statements: list[str] = []
    trade_dates = sorted({text_value(row, "trade_date") for row in rows if text_value(row, "trade_date")})
    for trade_date in trade_dates:
        statements.append(f"DELETE FROM auction_trend_summary WHERE trade_date={sql_string(trade_date)};")
    for row in rows:
        trade_date = text_value(row, "trade_date")
        code = text_value(row, "code")
        if not trade_date or not code:
            continue
        statements.append(upsert_stock_sql(row))
        statements.append(
            f"""
            INSERT INTO auction_trend_summary(
              trade_date, code, stock_name, first_seen_minute, last_seen_minute,
              minute_count, pct_top_count, limit_up_count, limit_down_count,
              best_pct_rank, final_candidate_rank, first_auction_pct, last_auction_pct,
              pct_delta, first_auction_amount, last_auction_amount, amount_delta,
              amount_growth_ratio, max_seal_amount, last_seal_amount, theme_score,
              theme_matches, sector_hot_count, concept_hot_count, final_score,
              trend_score, trend_label, key_points, action_hint, raw_json, generated_at
            )
            VALUES(
              {sql_string(trade_date)}, {sql_string(code)}, {sql_string(text_value(row, "stock_name") or text_value(row, "name"))},
              {sql_string(text_value(row, "first_seen_minute") or None)}, {sql_string(text_value(row, "last_seen_minute") or None)},
              {sql_int(row.get("minute_count"))}, {sql_int(row.get("pct_top_count"))},
              {sql_int(row.get("limit_up_count"))}, {sql_int(row.get("limit_down_count"))},
              {sql_int(row.get("best_pct_rank"))}, {sql_int(row.get("final_candidate_rank"))},
              {sql_number(row.get("first_auction_pct"))}, {sql_number(row.get("last_auction_pct"))},
              {sql_number(row.get("pct_delta"))}, {sql_number(row.get("first_auction_amount"))},
              {sql_number(row.get("last_auction_amount"))}, {sql_number(row.get("amount_delta"))},
              {sql_number(row.get("amount_growth_ratio"))}, {sql_number(row.get("max_seal_amount"))},
              {sql_number(row.get("last_seal_amount"))}, {sql_number(row.get("theme_score"))},
              {sql_json(row.get("theme_matches") or [])}, {sql_int(row.get("sector_hot_count"))},
              {sql_int(row.get("concept_hot_count"))}, {sql_number(row.get("final_score"))},
              {sql_number(row.get("trend_score"))}, {sql_string(text_value(row, "trend_label"))},
              {sql_json(row.get("key_points") or [])}, {sql_string(text_value(row, "action_hint"))},
              {sql_json(row)}, {sql_string(text_value(row, "generated_at") or now_text())}
            );
            """
        )
    if not statements:
        return 0
    run_mysql(config, "START TRANSACTION;\n" + "\n".join(statements) + "\nCOMMIT;")
    return len([row for row in rows if text_value(row, "trade_date") and text_value(row, "code")])


__all__ = [
    "delete_auction_candidates_for_date",
    "delete_auction_trend_summary_for_date",
    "import_auction_candidate_rows",
    "import_auction_minute_analysis_rows",
    "import_auction_trend_summary_rows",
    "text_value",
    "upsert_stock_sql",
]
