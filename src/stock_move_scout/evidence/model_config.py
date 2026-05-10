from __future__ import annotations

import os
from pathlib import Path
import re
from dataclasses import dataclass

from stock_scout_mysql import MySqlConfig, mysql_rows, run_mysql, sql_string


@dataclass(frozen=True)
class ModelRuntimeConfig:
    base_url: str
    model: str
    api_key: str
    timeout: int
    source: str


def ensure_model_config_table(config: MySqlConfig) -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS model_api_configs (
      id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
      config_name VARCHAR(64) NOT NULL,
      provider VARCHAR(64) NOT NULL DEFAULT 'openai_compatible',
      base_url VARCHAR(512) NOT NULL DEFAULT 'https://api.openai.com/v1',
      model VARCHAR(128) NOT NULL DEFAULT '',
      api_key TEXT NULL,
      timeout_seconds INT NOT NULL DEFAULT 60,
      enabled TINYINT(1) NOT NULL DEFAULT 1,
      is_default TINYINT(1) NOT NULL DEFAULT 0,
      notes VARCHAR(512) NOT NULL DEFAULT '',
      created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
      updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
      UNIQUE KEY uk_model_config_name (config_name),
      KEY idx_model_config_default (is_default, enabled)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
    """
    run_mysql(config, sql)


def load_model_config(config: MySqlConfig, config_name: str = "") -> dict[str, str]:
    name_filter = f"AND config_name={sql_string(config_name)}" if config_name else ""
    sql = f"""
    SELECT config_name, provider, base_url, model, COALESCE(api_key, ''), timeout_seconds
    FROM model_api_configs
    WHERE enabled=1
      {name_filter}
    ORDER BY is_default DESC, updated_at DESC
    LIMIT 1;
    """
    rows = mysql_rows(run_mysql(config, sql, batch=True, raw=True))
    if not rows or len(rows[0]) < 6:
        return {}
    row = rows[0]
    return {
        "config_name": row[0],
        "provider": row[1],
        "base_url": row[2],
        "model": row[3],
        "api_key": row[4],
        "timeout_seconds": row[5],
    }


def upsert_model_config(config: MySqlConfig, values: dict[str, str], config_name: str = "default") -> None:
    base_url = values.get("OPENAI_BASE_URL") or values.get("OPENAI_API_BASE") or "https://api.openai.com/v1"
    model = values.get("OPENAI_MODEL") or "gpt-4o-mini"
    api_key = values.get("OPENAI_API_KEY") or ""
    sql = f"""
    INSERT INTO model_api_configs(
      config_name, provider, base_url, model, api_key, timeout_seconds, enabled, is_default, notes
    ) VALUES (
      {sql_string(config_name)}, 'openai_compatible', {sql_string(base_url)}, {sql_string(model)},
      {sql_string(api_key)}, 60, 1, 1, 'imported from local env file'
    )
    ON DUPLICATE KEY UPDATE
      provider=VALUES(provider),
      base_url=VALUES(base_url),
      model=VALUES(model),
      api_key=VALUES(api_key),
      timeout_seconds=VALUES(timeout_seconds),
      enabled=VALUES(enabled),
      is_default=VALUES(is_default),
      notes=VALUES(notes),
      updated_at=CURRENT_TIMESTAMP(3);

    UPDATE model_api_configs
    SET is_default = IF(config_name={sql_string(config_name)}, 1, 0);
    """
    run_mysql(config, sql)


def read_openai_env_file(path_text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path_text:
        return values
    path = Path(path_text)
    if not path.exists() or not path.is_file():
        return values
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"\$?env:?(OPENAI_[A-Z_]+)\s*=\s*(.+)$", stripped)
        if match:
            name = match.group(1)
            value = match.group(2).strip().strip("'\"")
            if value:
                values[name] = value
            continue
        key_match = re.search(r"sk-[A-Za-z0-9_-]+", stripped)
        if key_match:
            values.setdefault("OPENAI_API_KEY", key_match.group(0))
    return values


def read_api_key_file(path_text: str) -> str:
    return read_openai_env_file(path_text).get("OPENAI_API_KEY", "")


def resolve_api_key(api_key_file: str) -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip() or read_api_key_file(api_key_file)


def resolve_model_runtime_config(
    *,
    config: MySqlConfig,
    config_name: str,
    no_model_config: bool,
    api_key_file: str,
    base_url: str,
    model: str,
    timeout: int,
) -> ModelRuntimeConfig:
    db_model_config = {} if no_model_config else load_model_config(config, config_name)
    file_env = read_openai_env_file(api_key_file)

    runtime_base_url = base_url
    runtime_model = model
    runtime_timeout = timeout
    source = "args"

    if db_model_config:
        runtime_base_url = db_model_config.get("base_url") or runtime_base_url
        runtime_model = db_model_config.get("model") or runtime_model
        if db_model_config.get("timeout_seconds"):
            try:
                runtime_timeout = int(db_model_config["timeout_seconds"])
            except Exception:
                pass
        source = f"db:{db_model_config.get('config_name') or config_name or 'default'}"
    else:
        if runtime_base_url == "https://api.openai.com/v1" and file_env.get("OPENAI_BASE_URL"):
            runtime_base_url = file_env["OPENAI_BASE_URL"]
            source = "file"
        if runtime_model == "gpt-4o-mini" and file_env.get("OPENAI_MODEL"):
            runtime_model = file_env["OPENAI_MODEL"]
            source = "file"

    api_key = db_model_config.get("api_key", "") or resolve_api_key(api_key_file)
    return ModelRuntimeConfig(
        base_url=runtime_base_url,
        model=runtime_model,
        api_key=api_key,
        timeout=runtime_timeout,
        source=source,
    )
