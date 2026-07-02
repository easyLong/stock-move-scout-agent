from __future__ import annotations

"""Reusable writing constraints for the user's morning reference posts."""


__all__ = [
    "FORBIDDEN_MACHINE_PHRASES",
    "MORNING_POST_OUTPUT_RULES",
    "USER_VOCABULARY",
]


USER_VOCABULARY = {
    "修复延续": "修复还在，但修复强度没有前一日强",
    "继续修复": "修复力度和昨日相当",
    "加强修复": "修复力度比昨日更强",
    "分歧延续": "分歧还在，但分歧力度比前一日弱",
}

FORBIDDEN_MACHINE_PHRASES = (
    "有几个事实",
    "推演如下",
    "因素叠加",
    "最合理的推演",
    "可以得出",
    "映射方向",
    "消息密度最高",
    "风险偏好形成压制",
    "持续性取决于",
    "总体思路",
    "核心不是单一利好",
    "盘中验证点",
)

MORNING_POST_OUTPUT_RULES = {
    "platform": "xueqiu",
    "title": "【日期盘前：行情概况 + 直接策略】",
    "sections": ("自然开头", "今日最佳入手机会/今日风险点", "今天盯什么", "免责声明"),
    "only_fixed_heading": "今天盯什么",
    "disclaimer": "仅为个人盘前复盘，不构成投资建议。",
}
