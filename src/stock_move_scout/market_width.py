from __future__ import annotations

from stock_move_scout.db import MySqlConfig, mysql_rows, run_mysql, sql_string


def ensure_market_width_tables(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS market_width_snapshots (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      snapshot_id VARCHAR(32) NOT NULL,
      trade_date DATE NOT NULL,
      captured_at DATETIME(3) NOT NULL,
      source VARCHAR(64) NOT NULL DEFAULT 'akshare_stock_zh_a_spot',
      market_scope VARCHAR(32) NOT NULL DEFAULT 'cn_a_main',
      total_count INT NOT NULL DEFAULT 0,
      up_count INT NOT NULL DEFAULT 0,
      down_count INT NOT NULL DEFAULT 0,
      flat_count INT NOT NULL DEFAULT 0,
      up3_count INT NOT NULL DEFAULT 0,
      down3_count INT NOT NULL DEFAULT 0,
      up5_count INT NOT NULL DEFAULT 0,
      down5_count INT NOT NULL DEFAULT 0,
      limit_up_count INT NOT NULL DEFAULT 0,
      limit_down_count INT NOT NULL DEFAULT 0,
      amount_top50_count INT NOT NULL DEFAULT 0,
      amount_top50_up_count INT NOT NULL DEFAULT 0,
      amount_top50_down_count INT NOT NULL DEFAULT 0,
      amount_top50_flat_count INT NOT NULL DEFAULT 0,
      amount_top50_up3_count INT NOT NULL DEFAULT 0,
      amount_top50_down3_count INT NOT NULL DEFAULT 0,
      amount_top50_up5_count INT NOT NULL DEFAULT 0,
      amount_top50_down5_count INT NOT NULL DEFAULT 0,
      research_pool_trade_date DATE NULL,
      research_pool_rule VARCHAR(64) NOT NULL DEFAULT '',
      research_pool_count INT NOT NULL DEFAULT 0,
      research_pool_up_count INT NOT NULL DEFAULT 0,
      research_pool_down_count INT NOT NULL DEFAULT 0,
      research_pool_flat_count INT NOT NULL DEFAULT 0,
      research_pool_up3_count INT NOT NULL DEFAULT 0,
      research_pool_down3_count INT NOT NULL DEFAULT 0,
      research_pool_up5_count INT NOT NULL DEFAULT 0,
      research_pool_down5_count INT NOT NULL DEFAULT 0,
      sh_index_price DECIMAL(12,4) NULL,
      sh_index_pct_change DECIMAL(10,4) NULL,
      sh_index_amount DECIMAL(24,2) NULL,
      sh_index_volume BIGINT NULL,
      total_volume BIGINT NULL,
      total_amount DECIMAL(24,2) NOT NULL DEFAULT 0,
      top50_amount DECIMAL(24,2) NOT NULL DEFAULT 0,
      raw_meta JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      PRIMARY KEY (id),
      UNIQUE KEY uk_market_width_snapshot_id (snapshot_id),
      KEY idx_market_width_trade_time (trade_date, captured_at),
      KEY idx_market_width_created_at (created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
      COMMENT='盘中市场概览快照：全市场、成交额Top50、研究池宽度统计';

    CREATE TABLE IF NOT EXISTS market_width_amount_top50 (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      snapshot_id VARCHAR(32) NOT NULL,
      trade_date DATE NOT NULL,
      captured_at DATETIME(3) NOT NULL,
      rank_no INT NOT NULL DEFAULT 0,
      code CHAR(6) NOT NULL,
      name VARCHAR(64) NOT NULL DEFAULT '',
      latest_price DECIMAL(12,4) NULL,
      pct_change DECIMAL(10,4) NULL,
      amount DECIMAL(20,2) NULL,
      volume BIGINT NULL,
      raw_row JSON NULL,
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      PRIMARY KEY (id),
      UNIQUE KEY uk_market_width_top_snapshot_code (snapshot_id, code),
      KEY idx_market_width_top_trade_rank (trade_date, captured_at, rank_no),
      KEY idx_market_width_top_code_time (code, captured_at),
      CONSTRAINT fk_market_width_top_snapshot
        FOREIGN KEY (snapshot_id) REFERENCES market_width_snapshots(snapshot_id)
        ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
      COMMENT='盘中成交额最大Top50快照';
    """
    run_mysql(config, sql)
    ensure_market_width_snapshot_columns(config)


def _column_exists(config: MySqlConfig, table_name: str, column_name: str) -> bool:
    sql = f"""
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA=DATABASE()
      AND TABLE_NAME={sql_string(table_name)}
      AND COLUMN_NAME={sql_string(column_name)};
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    return bool(rows and rows[0] and rows[0][0] != "0")


def ensure_market_width_snapshot_columns(config: MySqlConfig) -> None:
    columns = {
        "amount_top50_count": "INT NOT NULL DEFAULT 0 AFTER limit_down_count",
        "up5_count": "INT NOT NULL DEFAULT 0 AFTER down3_count",
        "down5_count": "INT NOT NULL DEFAULT 0 AFTER up5_count",
        "amount_top50_up_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_count",
        "amount_top50_down_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_up_count",
        "amount_top50_flat_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_down_count",
        "amount_top50_up3_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_flat_count",
        "amount_top50_down3_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_up3_count",
        "amount_top50_up5_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_down3_count",
        "amount_top50_down5_count": "INT NOT NULL DEFAULT 0 AFTER amount_top50_up5_count",
        "research_pool_trade_date": "DATE NULL AFTER amount_top50_down5_count",
        "research_pool_rule": "VARCHAR(64) NOT NULL DEFAULT '' AFTER research_pool_trade_date",
        "research_pool_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_rule",
        "research_pool_up_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_count",
        "research_pool_down_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_up_count",
        "research_pool_flat_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_down_count",
        "research_pool_up3_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_flat_count",
        "research_pool_down3_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_up3_count",
        "research_pool_up5_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_down3_count",
        "research_pool_down5_count": "INT NOT NULL DEFAULT 0 AFTER research_pool_up5_count",
        "sh_index_price": "DECIMAL(12,4) NULL AFTER research_pool_down5_count",
        "sh_index_pct_change": "DECIMAL(10,4) NULL AFTER sh_index_price",
        "sh_index_amount": "DECIMAL(24,2) NULL AFTER sh_index_pct_change",
        "sh_index_volume": "BIGINT NULL AFTER sh_index_amount",
        "total_volume": "BIGINT NULL AFTER sh_index_volume",
    }
    statements = [
        f"ALTER TABLE market_width_snapshots ADD COLUMN {name} {definition};"
        for name, definition in columns.items()
        if not _column_exists(config, "market_width_snapshots", name)
    ]
    if statements:
        run_mysql(config, "\n".join(statements))
