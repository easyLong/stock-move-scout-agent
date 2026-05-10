#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


COLUMNS = [
    "generated_at",
    "rank_speed",
    "code",
    "name",
    "symbol",
    "community_main_claim",
    "community_trigger_claim",
    "community_trigger_event",
    "community_trigger_timing",
    "community_imagination_path",
    "community_verification_anchor",
    "community_evidence_type",
    "community_support_points",
    "community_disagreements",
    "community_risk_flags",
    "community_verification_need",
    "community_signal_quality",
    "model",
    "source_status",
]

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "stocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "code": {"type": "string"},
                    "name": {"type": "string"},
                    "community_main_claim": {"type": "string"},
                    "community_trigger_claim": {"type": "string"},
                    "community_trigger_event": {"type": "string"},
                    "community_trigger_timing": {"type": "string"},
                    "community_imagination_path": {"type": "string"},
                    "community_verification_anchor": {"type": "array", "items": {"type": "string"}},
                    "community_evidence_type": {"type": "string"},
                    "community_support_points": {"type": "array", "items": {"type": "string"}},
                    "community_disagreements": {"type": "array", "items": {"type": "string"}},
                    "community_risk_flags": {"type": "array", "items": {"type": "string"}},
                    "community_verification_need": {"type": "array", "items": {"type": "string"}},
                    "community_signal_quality": {"type": "string", "enum": ["high", "medium", "low", "noise", "interrupted"]},
                },
                "required": [
                    "code",
                    "name",
                    "community_main_claim",
                    "community_trigger_claim",
                    "community_trigger_event",
                    "community_trigger_timing",
                    "community_imagination_path",
                    "community_verification_anchor",
                    "community_evidence_type",
                    "community_support_points",
                    "community_disagreements",
                    "community_risk_flags",
                    "community_verification_need",
                    "community_signal_quality",
                ],
            },
        }
    },
    "required": ["stocks"],
}

THEME_KEYWORDS = [
    "AI",
    "算力",
    "芯片",
    "半导体",
    "业绩",
    "订单",
    "公告",
    "并购",
    "收购",
    "华为",
    "中国移动",
    "商业航天",
    "军工",
    "CPO",
    "PCB",
    "存储",
    "MCU",
    "数据中心",
    "资金",
    "新股",
    "次新",
    "涨停",
]

EVIDENCE_PATTERNS = [
    ("公告", ["公告", "披露", "报告", "财报", "一季报", "年报", "业绩快报"]),
    ("业绩", ["业绩", "营收", "净利润", "扭亏", "亏损", "毛利率"]),
    ("订单/合作", ["订单", "合同", "合作", "中标", "客户", "供应", "华为", "中国移动"]),
    ("产品/技术", ["产品", "芯片", "设备", "解决方案", "AI", "算力", "数据集", "三类证"]),
    ("资金/情绪", ["资金", "拉升", "涨停", "龙头", "强势", "低吸", "反抽"]),
    ("传闻/推测", ["听说", "传", "可能", "预期", "想象", "感觉"]),
]

RISK_WORDS = ["风险", "亏损", "减持", "高估值", "高负债", "商誉", "下滑", "落后", "不及", "质疑", "恶心", "烂"]


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compact(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[:limit]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?；;\n]", text or "")
    return [compact(part, 180) for part in parts if len(compact(part, 180)) >= 8]


def group_posts(rows: list[dict[str, str]], limit_per_stock: int) -> dict[tuple[str, str, str, str], list[dict[str, str]]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        key = (row.get("rank_speed", ""), row.get("code", ""), row.get("name", ""), row.get("symbol", ""))
        groups[key].append(row)
    for key in list(groups):
        groups[key] = sorted(groups[key], key=lambda item: int(float(item.get("hot_rank") or 999)))[:limit_per_stock]
    return dict(sorted(groups.items(), key=lambda item: int(float(item[0][0] or 999))))


def responses_url(base_url: str) -> str:
    base = (base_url or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/responses"):
        return base
    return f"{base}/responses"


def extract_response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for output in response.get("output", []) or []:
        for content in output.get("content", []) or []:
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
            if isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("No text output found in model response")


def build_payload(posts: list[dict[str, str]], limit_per_stock: int, text_limit: int) -> str:
    payload: list[dict[str, Any]] = []
    for (rank_speed, code, name, symbol), rows in group_posts(posts, limit_per_stock).items():
        payload.append(
            {
                "rank_speed": rank_speed,
                "code": code,
                "name": name,
                "symbol": symbol,
                "posts": [
                    {
                        "rank": row.get("hot_rank", ""),
                        "author": row.get("user", ""),
                        "time": row.get("time_hint", ""),
                        "title": row.get("title", ""),
                        "text": compact(row.get("text", ""), text_limit),
                        "url": row.get("detail_url", ""),
                    }
                    for row in rows
                ],
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def call_model(payload: str, model: str, base_url: str, api_key: str, timeout: int) -> dict[str, Any]:
    if not api_key and (base_url or "").rstrip("/") == "https://api.openai.com/v1":
        raise RuntimeError("OPENAI_API_KEY is not set")
    system_prompt = (
        "你是股票异动侦察系统里的社区叙事分析员。"
        "你的任务是把雪球热帖拆成可验证的社区叙事结构。"
        "不要荐股，不要给买卖建议，不要把传闻当事实。"
        "要区分主叙事、具体催化、支撑点、反对观点、风险和待验证项。"
    )
    user_prompt = (
        "请分析下面的雪球热帖数据，为每只股票输出社区叙事结构。\n\n"
        "要求：\n"
        "1. community_main_claim：一句话说明社区主要如何解释这只股票。\n"
        "2. community_trigger_claim：一句话说明社区提到的具体催化，如果没有就写“未形成明确催化”。\n"
        "3. community_trigger_event：帖子里提到的具体事件、公告、合作、订单、产品、政策或传闻。\n"
        "4. community_trigger_timing：为什么现在被讨论，是刚公告、刚涨、财报季、板块发酵、旧故事重炒，还是纯情绪。\n"
        "5. community_imagination_path：市场从触发事件推演到更大故事的路径，体现想象力，但必须标明推演关系。\n"
        "6. community_verification_anchor：这个想象力要成立，必须核实的锚点。\n"
        "7. community_evidence_type：从公告、业绩、订单/合作、产品/技术、资金/情绪、传闻/推测、混合中选择或组合。\n"
        "8. 支撑点、争议、风险、待验证项都要短句。\n"
        "9. community_signal_quality：high/medium/low/noise/interrupted。\n\n"
        f"热帖数据：\n{payload}"
    )
    body = {
        "model": model,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "community_narrative_summary",
                "strict": True,
                "schema": SUMMARY_SCHEMA,
            }
        },
    }
    request = urllib.request.Request(
        responses_url(base_url),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {api_key}"} if api_key else {}),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI-compatible API HTTP {exc.code}: {detail[:1200]}") from exc
    return json.loads(extract_response_text(data))


def infer_themes(text: str) -> list[str]:
    counter = Counter()
    lowered = text.lower()
    for keyword in THEME_KEYWORDS:
        if keyword.lower() in lowered:
            counter[keyword] += lowered.count(keyword.lower())
    return [item for item, _ in counter.most_common(5)]


def infer_evidence_type(text: str) -> str:
    hits: list[str] = []
    for label, words in EVIDENCE_PATTERNS:
        if any(word.lower() in text.lower() for word in words):
            hits.append(label)
    return "、".join(hits[:3]) if hits else "情绪/讨论"


def pick_sentence(sentences: list[str], words: list[str], default: str) -> str:
    for sentence in sentences:
        if any(word.lower() in sentence.lower() for word in words):
            return sentence
    return default


def fallback_one(rank_speed: str, code: str, name: str, symbol: str, posts: list[dict[str, str]]) -> dict[str, Any]:
    text = " ".join((row.get("title", "") + " " + row.get("text", "")) for row in posts)
    sentences = split_sentences(text)
    themes = infer_themes(text)
    evidence_type = infer_evidence_type(text)
    if not posts:
        return {
            "code": code,
            "name": name,
            "community_main_claim": "未抓到有效社区热帖。",
            "community_trigger_claim": "未形成明确催化。",
            "community_trigger_event": "未抓到有效社区热帖。",
            "community_trigger_timing": "社区采集缺失，无法判断时间敏感性。",
            "community_imagination_path": "暂无可用想象力路径。",
            "community_verification_anchor": ["补抓热帖或更换社区源"],
            "community_evidence_type": "缺失",
            "community_support_points": [],
            "community_disagreements": [],
            "community_risk_flags": ["社区证据缺失"],
            "community_verification_need": ["补抓热帖或更换社区源"],
            "community_signal_quality": "interrupted",
        }
    theme_text = "、".join(themes) if themes else "当前讨论"
    trigger = pick_sentence(sentences, ["公告", "业绩", "订单", "合作", "华为", "中国移动", "并购", "收购", "涨"], "未形成明确催化。")
    timing = pick_sentence(sentences, ["近期", "今日", "一季度", "2026", "公告", "涨", "新股", "次新"], "时间敏感性不明确。")
    support = [
        sentence
        for sentence in sentences
        if any(word in sentence for word in ["公告", "披露", "研报", "合作", "产品", "业绩", "营收", "订单", "华为", "中国移动"])
    ][:3]
    risks = [sentence for sentence in sentences if any(word in sentence for word in RISK_WORDS)][:3]
    disagreements = [
        sentence
        for sentence in sentences
        if any(word in sentence for word in ["质疑", "但是", "不过", "不该", "落后", "不确定", "风险"])
    ][:2]
    quality = "medium" if support else "low"
    if len(posts) <= 1 and not support:
        quality = "noise"
    return {
        "code": code,
        "name": name,
        "community_main_claim": f"社区主要把{name}解读为{theme_text}方向的异动标的。",
        "community_trigger_claim": trigger,
        "community_trigger_event": trigger,
        "community_trigger_timing": timing,
        "community_imagination_path": f"{theme_text}线索 -> 市场预期公司业务或估值被重新解释 -> 需要硬证据确认。",
        "community_verification_anchor": ["公告/财报原文", "订单或合作真实性", "板块联动和资金持续性"],
        "community_evidence_type": evidence_type,
        "community_support_points": support or ["热帖有讨论，但缺少明确可核验支撑点"],
        "community_disagreements": disagreements,
        "community_risk_flags": risks,
        "community_verification_need": ["核对公告/财报/新闻原文", "观察板块联动和资金持续性"],
        "community_signal_quality": quality,
    }


def fallback_summary(posts: list[dict[str, str]], limit_per_stock: int) -> dict[str, Any]:
    stocks = []
    for (rank_speed, code, name, symbol), rows in group_posts(posts, limit_per_stock).items():
        stocks.append(fallback_one(rank_speed, code, name, symbol, rows))
    return {"stocks": stocks}


def render_markdown(rows: list[dict[str, Any]], output: Path) -> None:
    lines = ["# 社区叙事结构化", "", f"生成时间：{now_text()}", ""]
    for row in rows:
        lines.extend(
            [
                f"## {row.get('name', '')} {row.get('code', '')}",
                "",
                f"- 主叙事：{row.get('community_main_claim', '')}",
                f"- 催化说法：{row.get('community_trigger_claim', '')}",
                f"- 触发事件：{row.get('community_trigger_event', '')}",
                f"- 时间敏感性：{row.get('community_trigger_timing', '')}",
                f"- 想象力路径：{row.get('community_imagination_path', '')}",
                f"- 验证锚点：{row.get('community_verification_anchor', '')}",
                f"- 证据类型：{row.get('community_evidence_type', '')}",
                f"- 信号质量：{row.get('community_signal_quality', '')}",
                f"- 支撑点：{row.get('community_support_points', '')}",
                f"- 争议：{row.get('community_disagreements', '')}",
                f"- 风险：{row.get('community_risk_flags', '')}",
                f"- 待验证：{row.get('community_verification_need', '')}",
                "",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description="Summarize Xueqiu hot posts into structured community narratives.")
    parser.add_argument("--hot-posts-csv", type=Path, default=root / "data" / "stock" / "xueqiu_focus_hot_posts_latest.csv")
    parser.add_argument("--output-csv", type=Path, default=root / "data" / "stock" / "community_narrative_latest.csv")
    parser.add_argument("--output-json", type=Path, default=root / "data" / "stock" / "community_narrative_latest.json")
    parser.add_argument("--output-md", type=Path, default=root / "data" / "stock" / "community_narrative_latest.md")
    parser.add_argument("--model", default=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    parser.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL") or os.environ.get("OPENAI_API_BASE") or "https://api.openai.com/v1")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--limit-per-stock", type=int, default=8)
    parser.add_argument("--text-limit", type=int, default=1400)
    parser.add_argument("--fallback-without-model", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    posts = read_csv(args.hot_posts_csv)
    generated_at = now_text()
    model_ok = False
    error = ""
    try:
        summary = call_model(
            build_payload(posts, args.limit_per_stock, args.text_limit),
            args.model,
            args.base_url,
            os.environ.get("OPENAI_API_KEY", ""),
            args.timeout,
        )
        model_ok = True
    except Exception as exc:
        if not args.fallback_without_model:
            raise
        error = f"{type(exc).__name__}:{exc}"
        summary = fallback_summary(posts, args.limit_per_stock)

    source_map = {
        code: rows[0]
        for (_, code, _, _), rows in group_posts(posts, args.limit_per_stock).items()
        if rows
    }
    output_rows: list[dict[str, Any]] = []
    for item in summary.get("stocks", []) or []:
        source = source_map.get(item.get("code", ""), {})
        output_rows.append(
            {
                "generated_at": generated_at,
                "rank_speed": source.get("rank_speed", ""),
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "symbol": source.get("symbol", ""),
                "community_main_claim": item.get("community_main_claim", ""),
                "community_trigger_claim": item.get("community_trigger_claim", ""),
                "community_trigger_event": item.get("community_trigger_event", ""),
                "community_trigger_timing": item.get("community_trigger_timing", ""),
                "community_imagination_path": item.get("community_imagination_path", ""),
                "community_verification_anchor": " || ".join(item.get("community_verification_anchor", []) or []),
                "community_evidence_type": item.get("community_evidence_type", ""),
                "community_support_points": " || ".join(item.get("community_support_points", []) or []),
                "community_disagreements": " || ".join(item.get("community_disagreements", []) or []),
                "community_risk_flags": " || ".join(item.get("community_risk_flags", []) or []),
                "community_verification_need": " || ".join(item.get("community_verification_need", []) or []),
                "community_signal_quality": item.get("community_signal_quality", ""),
                "model": args.model if model_ok else "fallback_without_model",
                "source_status": "ok" if source else "missing_posts",
            }
        )
    write_csv(args.output_csv, output_rows, COLUMNS)
    write_json(
        args.output_json,
        {
            "generated_at": generated_at,
            "model": args.model,
            "model_ok": model_ok,
            "error": error,
            "hot_posts_csv": str(args.hot_posts_csv),
            "row_count": len(output_rows),
            "stocks": output_rows,
        },
    )
    render_markdown(output_rows, args.output_md)
    print(f"community_narrative_csv={args.output_csv}")
    print(f"community_narrative_json={args.output_json}")
    print(f"community_narrative_md={args.output_md}")
    print(f"model_ok={model_ok}")
    if error:
        print(f"model_error={error[:500]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
