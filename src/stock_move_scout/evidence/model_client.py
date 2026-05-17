from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.request


SYSTEM_PROMPT = (
    "你是股票异动侦查系统的事实整理员。你的任务不是做交易判断，"
    "而是把已经筛出的 current_facts 按时间整理成清晰、可核验的事实时间线。"
    "必须保留原文里的关键指标、金额、比例、同比、客户、产品、订单、产能、项目名称和日期；"
    "禁止编造、禁止补充材料外信息、禁止给买卖建议。"
)


USER_PROMPT_TEMPLATE = (
    "请只基于 payload.current_facts 生成结构化摘要。\n\n"
    "核心要求：\n"
    "1. 按 fact_date 倒序整理，每一条 current_facts 都尽量生成一条事实摘要。\n"
    "2. 每条摘要格式建议为：YYYY-MM-DD｜标题：一句话事实。不要写空泛判断。\n"
    "3. 内容中的数字不要丢：金额、百分比、同比、环比、数量、客户名、产品名、项目名、订单/合同/中标金额都要保留。\n"
    "4. 不需要判断强弱、不需要推演股价、不需要写交易观点。无法判断就少写，不要猜。\n"
    "5. key_points、key_facts、hard_catalysts、core_support、sustainability_basis 尽量使用同一组时间线事实。\n"
    "6. impact_factors 也按每条事实生成，evidence 放完整事实句；factor_type 只能选：业绩、重组、合同订单、题材正宗性、增减持、产能、政策行业、风险、其他。\n"
    "7. core_evidence_items 按每条事实生成，reason 放完整事实句，source_date 放 fact_date。\n"
    "8. summary_text/final_view/final_analysis 只写一句总括，例如“按时间线整理N条近10日有效事实”。\n"
    "9. risks、counterpoints、missing_evidence、evidence_gaps 没有明确材料就填空数组。\n"
    "10. quality_label 只在以下值中选一个：公告驱动、业绩驱动、题材共振、个股脉冲、资金脉冲、无法解释；本层无法判断时选公告驱动或无法解释。\n\n"
    "payload：\n{payload_json}"
)


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


def call_async_evidence_model(
    payload: dict[str, Any],
    *,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    schema: dict[str, Any],
) -> dict[str, Any]:
    if not api_key and (base_url or "").rstrip("/") == "https://api.openai.com/v1":
        raise RuntimeError("OPENAI_API_KEY is not set")

    body = {
        "model": model,
        "input": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": USER_PROMPT_TEMPLATE.format(payload_json=json.dumps(payload, ensure_ascii=False, indent=2)),
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "async_evidence_summary",
                "strict": True,
                "schema": schema,
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

