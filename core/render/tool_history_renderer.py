"""工具历史渲染器。

将时间窗口内的 tool event 渲染为 <tool_history> 格式文本。
工具参数和结果按配置进行硬截断。

本模块接收 history_renderer 筛选好的 tool_events_in_window，
不再重复做时间窗口选取。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..utils.truncate import truncate_text


def _format_datetime(ts: int) -> str:
    """将时间戳格式化为完整日期时间：YYYY年M月D日 HH:MM:SS。"""
    dt = datetime.fromtimestamp(ts)
    return f"{dt.year}年{dt.month}月{dt.day}日 {dt.strftime('%H:%M:%S')}"


def _format_tool_args(args: dict[str, Any] | None, max_chars: int) -> str:
    """格式化工具参数，超长则截断。"""
    if not args:
        return "(无参数)"
    try:
        text = json.dumps(args, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(args)
    return truncate_text(text, max_chars)


def _format_tool_result(result: str, max_chars: int) -> str:
    """格式化工具结果，超长则截断。"""
    if not result:
        return "(无结果)"
    return truncate_text(result, max_chars)


def render_tool_history(
    tool_events: list[dict[str, Any]],
    tool_args_max_chars: int = 500,
    tool_result_max_chars: int = 1000,
) -> str:
    """渲染 <tool_history> 文本。

    Args:
        tool_events: 时间窗口内的 tool event 列表
                     （由 history_renderer.render_history 返回）。
        tool_args_max_chars: 工具参数最大字符数。
        tool_result_max_chars: 工具结果最大字符数。

    Returns:
        渲染后的 <tool_history> 文本。如果没有工具事件则返回空字符串。
    """
    if not tool_events:
        return ""

    lines: list[str] = ["<tool_history>"]

    for ev in tool_events:
        tool_id = ev.get("id", "T???")
        ts = ev.get("timestamp", 0)
        time_str = _format_datetime(ts) if ts > 0 else "未知时间"
        tool_name = ev.get("tool_name", "unknown_tool")
        status = ev.get("status", "unknown")
        args = ev.get("tool_args")
        result = ev.get("tool_result", "")

        lines.append(f"#{tool_id}")
        lines.append(f"时间：{time_str}")
        lines.append(f"工具：{tool_name}")
        lines.append(f"状态：{status}")
        lines.append("参数：")
        lines.append(_format_tool_args(args, tool_args_max_chars))
        lines.append("结果：")
        lines.append(_format_tool_result(result, tool_result_max_chars))
        lines.append("---")

    lines.append("</tool_history>")

    return "\n".join(lines)
