from __future__ import annotations

import json
from typing import Any


def to_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value).replace("%", "").strip()
        if not text:
            return default
        return int(float(text))
    except Exception:
        return default


def to_float(value: Any) -> float | None:
    try:
        text = str(value).replace("%", "").strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def sql_string(value: Any) -> str:
    if value is None:
        return "NULL"
    text = str(value)
    text = text.replace("\\", "\\\\").replace("\0", "").replace("'", "''")
    return f"'{text}'"


def sql_number(value: Any) -> str:
    parsed = to_float(value)
    if parsed is None:
        return "NULL"
    return str(parsed)


def sql_int(value: Any) -> str:
    return str(to_int(value))


def sql_bool(value: Any) -> str:
    return "1" if value else "0"


def sql_json(value: Any) -> str:
    if value in (None, ""):
        return "NULL"
    return sql_string(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
