"""安全清洗模块。

转义用户消息中的危险标签，防止用户消息提前闭合
<history>、</history>、<tool_history>、</tool_history> 等插件使用的 XML 标签。

策略：将 < > & 中涉及插件关键标签的部分替换为全角字符。
为避免过度转义普通文本中的 < >，只精确匹配插件标签进行替换。
"""

from __future__ import annotations

import re

# 需要防护的关键标签名
_DANGEROUS_TAGS = [
    "history",
    "tool_history",
]

# 构建匹配模式：匹配 <history>, </history>, <tool_history>, </tool_history> 等
# 包含可能的属性或空格变体
_PATTERNS: list[re.Pattern[str]] = []
for tag in _DANGEROUS_TAGS:
    # 匹配开标签 <tag...> 和闭标签 </tag>（不区分大小写）
    _PATTERNS.append(re.compile(rf"<\s*/?\s*{re.escape(tag)}\b[^>]*>", re.IGNORECASE))
    # 匹配裸标签名前后的尖括号（如 <history 没有闭合的情况）
    _PATTERNS.append(re.compile(rf"<\s*/?\s*{re.escape(tag)}\b", re.IGNORECASE))


def _replace_angle_brackets(match: re.Match[str]) -> str:
    """将匹配到的危险标签中的 < > 替换为全角。"""
    text = match.group(0)
    return text.replace("<", "＜").replace(">", "＞")


def sanitize(text: str) -> str:
    """清洗文本中的危险标签。

    将用户消息中可能破坏插件 XML 结构的标签替换为安全的全角字符。
    普通的 < > 不受影响，只处理与插件标签名匹配的内容。

    Args:
        text: 原始用户消息文本。

    Returns:
        清洗后的安全文本。
    """
    if not text:
        return text

    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(_replace_angle_brackets, result)

    return result
