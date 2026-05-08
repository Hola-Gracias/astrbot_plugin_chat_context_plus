"""文本截断工具。

用于工具参数和工具结果的硬截断，超出长度后追加提示文本。
"""

from __future__ import annotations

_TRUNCATE_SUFFIX = "... [内容过长，已截断]"


def truncate_text(text: str, max_chars: int) -> str:
    """将文本截断到指定最大字符数。

    Args:
        text: 原始文本。
        max_chars: 最大允许字符数（不含截断提示）。

    Returns:
        截断后的文本。如果未超出限制则原样返回。
    """
    if max_chars <= 0:
        return _TRUNCATE_SUFFIX
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + _TRUNCATE_SUFFIX
