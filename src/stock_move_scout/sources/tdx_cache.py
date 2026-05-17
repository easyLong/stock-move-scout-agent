from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .quotes import is_main_a_share


def read_text_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="gbk", errors="ignore").splitlines()


def load_industry_names(cache_dir: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    for filename in ("tdxzs.cfg", "tdxzs3.cfg"):
        for line in read_text_lines(cache_dir / filename):
            parts = line.strip().split("|")
            if len(parts) >= 6 and parts[5]:
                names[parts[5]] = parts[0]
    return names


def load_industry_map(cache_dir: Path) -> dict[str, dict[str, str]]:
    code_names = load_industry_names(cache_dir)
    result: dict[str, dict[str, str]] = {}
    for line in read_text_lines(cache_dir / "tdxhy.cfg"):
        parts = line.strip().split("|")
        if len(parts) < 6:
            continue
        market, code, industry_code, _, _, sub_code = parts[:6]
        key = f"{market}:{code}"
        result[key] = {
            "industry_code": industry_code,
            "sub_industry_code": sub_code,
            "industry": code_names.get(industry_code, ""),
            "sub_industry": code_names.get(sub_code, ""),
        }
    return result


def add_concept(concepts: dict[str, set[str]], key: str, name: str) -> None:
    clean_name = name.strip()
    if not clean_name:
        return
    if clean_name.startswith(("FG_", "ZS_")):
        return
    concepts[key].add(clean_name)


def load_spec_concepts(cache_dir: Path, concepts: dict[str, set[str]]) -> None:
    path = cache_dir / "specgpsxzt.txt"
    for line in read_text_lines(path):
        parts = line.strip().split("|")
        if len(parts) < 3:
            continue
        market, code, raw = parts[:3]
        if not is_main_a_share(int(market), code):
            continue
        for name in raw.split(","):
            add_concept(concepts, f"{market}:{code}", name)


def load_infoharbor_concepts(cache_dir: Path, concepts: dict[str, set[str]]) -> None:
    path = cache_dir / "infoharbor_block.dat"
    current_name = ""
    stock_pattern = re.compile(r"([012])#(\d{6})")
    for line in read_text_lines(path):
        text = line.strip()
        if not text:
            continue
        if text.startswith("#"):
            parts = text.split(",")
            current_name = parts[0].replace("#GN_", "").replace("#", "").strip()
            continue
        if not current_name:
            continue
        for market, code in stock_pattern.findall(text):
            market_num = int(market)
            if is_main_a_share(market_num, code):
                add_concept(concepts, f"{market}:{code}", current_name)


def concept_sort_key(name: str) -> tuple[int, str]:
    topic_words = ("AI", "ChatGPT", "DeepSeek", "CPO")
    if any(word in name for word in topic_words):
        return (0, name)
    return (1, name)


def load_concept_map(cache_dir: Path) -> dict[str, list[str]]:
    concepts: dict[str, set[str]] = defaultdict(set)
    load_spec_concepts(cache_dir, concepts)
    load_infoharbor_concepts(cache_dir, concepts)
    return {key: sorted(values, key=concept_sort_key) for key, values in concepts.items()}


def load_tdx_label_cache(cache_dir: Path) -> dict[str, dict[str, object]]:
    return {
        "industry_map": load_industry_map(cache_dir),
        "concept_map": load_concept_map(cache_dir),
    }


__all__ = [
    "add_concept",
    "concept_sort_key",
    "load_concept_map",
    "load_industry_map",
    "load_industry_names",
    "load_infoharbor_concepts",
    "load_spec_concepts",
    "load_tdx_label_cache",
    "read_text_lines",
]
