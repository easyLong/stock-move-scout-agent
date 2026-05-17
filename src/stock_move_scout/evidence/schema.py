from __future__ import annotations

import hashlib
import json
from typing import Any


SUMMARY_VERSION = 5


SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary_text": {"type": "string"},
        "evidence_filter_summary": {"type": "string"},
        "key_facts": {"type": "array", "items": {"type": "string"}},
        "move_reason": {"type": "string"},
        "sustainability_basis": {"type": "array", "items": {"type": "string"}},
        "main_flaw": {"type": "string"},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "core_evidence_items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "source_type": {"type": "string"},
                    "source_date": {"type": "string"},
                    "title": {"type": "string"},
                    "reason": {"type": "string"},
                    "timeliness": {"type": "string", "enum": ["fresh", "recent", "stale", "unknown"]},
                    "importance": {"type": "string", "enum": ["high", "medium", "low"]},
                    "validity": {"type": "string", "enum": ["core", "supporting", "noise"]},
                },
                "required": ["source_type", "source_date", "title", "reason", "timeliness", "importance", "validity"],
            },
        },
        "timeliness_label": {"type": "string", "enum": ["fresh", "recent", "stale", "unknown"]},
        "timeliness_reason": {"type": "string"},
        "final_analysis": {"type": "string"},
        "move_explanation": {"type": "string"},
        "explanation_strength": {"type": "string", "enum": ["strong", "medium", "weak", "none"]},
        "anchor_match": {"type": "string", "enum": ["strong", "medium", "weak", "mismatch"]},
        "anchor_match_reason": {"type": "string"},
        "quality_label": {
            "type": "string",
            "enum": ["题材共振", "个股脉冲", "公告驱动", "业绩驱动", "资金脉冲", "无法解释"],
        },
        "core_support": {"type": "array", "items": {"type": "string"}},
        "counterpoints": {"type": "array", "items": {"type": "string"}},
        "final_view": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "hard_catalysts": {"type": "array", "items": {"type": "string"}},
        "impact_factors": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "factor_type": {
                        "type": "string",
                        "enum": ["业绩", "重组", "合同订单", "题材正宗性", "增减持", "产能", "政策行业", "风险", "其他"],
                    },
                    "direction": {"type": "string", "enum": ["正向", "负向", "中性", "不确定"]},
                    "importance": {"type": "string", "enum": ["高", "中", "低"]},
                    "evidence": {"type": "string"},
                    "source_type": {"type": "string"},
                    "source_date": {"type": "string"},
                },
                "required": ["factor_type", "direction", "importance", "evidence", "source_type", "source_date"],
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "evidence_strength": {"type": "string", "enum": ["strong", "medium", "weak", "pending"]},
        "evidence_gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary_text",
        "evidence_filter_summary",
        "key_facts",
        "move_reason",
        "sustainability_basis",
        "main_flaw",
        "missing_evidence",
        "core_evidence_items",
        "timeliness_label",
        "timeliness_reason",
        "final_analysis",
        "move_explanation",
        "explanation_strength",
        "anchor_match",
        "anchor_match_reason",
        "quality_label",
        "core_support",
        "counterpoints",
        "final_view",
        "key_points",
        "hard_catalysts",
        "impact_factors",
        "risks",
        "evidence_strength",
        "evidence_gaps",
    ],
}


def evidence_hash(payload: dict[str, Any]) -> str:
    hash_payload = payload
    if isinstance(payload.get("current_facts"), list):
        hash_payload = {
            "code": payload.get("code"),
            "current_facts": payload.get("current_facts"),
        }
    raw = json.dumps(
        {"summary_version": SUMMARY_VERSION, "payload": hash_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
