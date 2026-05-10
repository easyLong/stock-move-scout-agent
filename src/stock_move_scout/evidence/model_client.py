from __future__ import annotations

import json
from typing import Any
import urllib.error
import urllib.request


SYSTEM_PROMPT = (
    "你是股票异动侦察系统的证据编辑。你的任务是判断公司披露、互动易、定期报告和题材解释中"
    "真正可能影响股价的关键要素。只允许基于输入材料归纳，不得编造事实，不给买卖建议。"
    "输出要短、可核验、面向盘中快速判断。"
)


USER_PROMPT_TEMPLATE = (
    "请把下面股票的异步证据压缩成结构化摘要。\n"
    "要求：\n"
    "1. summary_text：一句话说明异步证据支持什么，不超过60字。\n"
    "2. key_points：最多4条，保留可验证事实。\n"
    "3. hard_catalysts：公告、互动易、定期报告、订单合作等硬催化；没有则空数组。\n"
    "4. impact_factors：提取真正可能影响股价的元素，重点看业绩、重组、合同订单、大订单、题材正宗性、增减持、产能、政策行业、风险。\n"
    "   分层理解：S级=重组/控制权/大订单/业绩大增/重大合同；A级=明确客户/中标/定点/产品放量；B级=互动易确认/题材正宗性；C级=普通概念或旧资料。\n"
    "5. 题材正宗性优先围绕 market_context.current_anchors 判断；无关题材不要强行写入。\n"
    "6. direction 只判断材料本身偏正向、负向、中性或不确定；importance 按对股价解释力高/中/低。\n"
    "7. risks：最多3条，仅写材料中能推导出的风险或不确定性。\n"
    "8. evidence_strength：strong/medium/weak/pending。\n"
    "9. evidence_gaps：还缺什么证据。\n"
    "10. 不要输出材料中没有的信息；无法判断就少写，不要凑数。\n\n"
    "新增关键要求：先过滤无效信息，再分析。"
    "evidence_filter_summary 用一句话说明过滤掉了什么、保留了什么；"
    "core_evidence_items 只保留最多5条真正影响股价判断的核心证据，"
    "剔除股东大会流程、普通会议、无实质内容公告、泛题材材料；"
    "timeliness_label 判断证据时效：fresh=当天/昨日盘后，recent=近两周，stale=较旧，unknown=无法判断；"
    "final_analysis 必须简洁有力，不超过80字，直接说明当前最重要的股价解释。\n\n"
    "异动解释要求：你不是做材料摘要，而是判断这只股票为什么异动。"
    "move_explanation 用一句话回答“为什么异动”；"
    "explanation_strength 判断证据能否解释异动：strong/medium/weak/none；"
    "anchor_match 判断当前锚点与证据是否一致：strong/medium/weak/mismatch；"
    "quality_label 只能在题材共振、个股脉冲、公告驱动、业绩驱动、资金脉冲、无法解释中选择；"
    "core_support 最多2条，必须是最硬的支撑；counterpoints 最多1条，指出最大瑕疵；"
    "final_view 用交易员语言输出一句最终观点，不超过60字，必须有判断力。\n\n"
    "\n事实先行字段必须这样写：\n"
    "key_facts 最多3条，只写可核验事实，必须包含日期/来源/数字/对象中的至少一种；禁止写逻辑较硬、题材发酵、资金强攻这类抽象判断。\n"
    "move_reason 用一句话说明异动原因，必须由 key_facts 推出。\n"
    "sustainability_basis 最多3条，分别优先覆盖硬催化、区间强度/锚点地位、盘口/资金确认；没有事实就不要硬写。\n"
    "main_flaw 只写最大瑕疵，例如缺订单金额、客户细节、公告确认、题材不一致；没有就写空字符串。\n"
    "missing_evidence 最多3条，写下一步最该补的证据。\n\n"
    "异步证据材料：\n{payload_json}"
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
