"""LLM 请求注入器。

在 on_llm_request 中执行，负责：
1. 清空 req.contexts（必须执行，不做配置项）
2. 渲染 <history> 和可选的 <tool_history>
3. 根据 injection_mode 选择注入位置：
   - extra_user_content_parts: 使用 mark_as_temp() 注入（推荐），失败时 fallback 到 system_prompt_append
   - system_prompt_append: 直接追加到系统提示词末尾
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.provider import ProviderRequest

from ..render.history_renderer import render_history
from ..render.tool_history_renderer import render_tool_history


def _inject_via_extra_user(req: ProviderRequest, combined: str) -> None:
    """通过 extra_user_content_parts + mark_as_temp 注入。"""
    from astrbot.core.agent.message import TextPart

    part = TextPart(text=combined)
    part.mark_as_temp()
    req.extra_user_content_parts.append(part)


def _inject_via_system_prompt(req: ProviderRequest, combined: str) -> None:
    """追加到 system_prompt 末尾注入。"""
    req.system_prompt = (req.system_prompt or "") + "\n\n" + combined


async def inject_history(
    req: ProviderRequest,
    events: list[dict[str, Any]],
    inject_message_count: int,
    exclude_event_id: str | None,
    enable_tool_history: bool,
    tool_args_max_chars: int,
    tool_result_max_chars: int,
    session_id: str = "",
    bot_id: str = "",
    injection_mode: str = "extra_user_content_parts",
    debug_logging: bool = False,
    image_captions: dict[str, str] | None = None,
) -> None:
    """向 LLM 请求注入群聊历史上下文。

    Args:
        req: 当前 LLM 请求对象。
        events: 当前会话的完整 event log。
        inject_message_count: 注入的消息数量上限。
        exclude_event_id: 当前消息的 event ID，需从历史中排除。
        enable_tool_history: 是否启用工具历史。
        tool_args_max_chars: 工具参数最大字符数。
        tool_result_max_chars: 工具结果最大字符数。
        session_id: 群号 / 会话 ID。
        bot_id: 机器人自身 ID。
        injection_mode: 注入位置，"extra_user_content_parts" 或 "system_prompt_append"。
        debug_logging: 是否输出 debug 级调试日志。
    """
    # 1. 必须清空 contexts，由插件 history 接管
    req.contexts = []

    # 2. 渲染 <history>
    history_text, tool_events_in_window = render_history(
        events=events,
        inject_message_count=inject_message_count,
        exclude_event_id=exclude_event_id,
        enable_tool_history=enable_tool_history,
        session_id=session_id,
        bot_id=bot_id,
        image_captions=image_captions,
    )

    # 3. 渲染 <tool_history>（如果启用）
    tool_history_text = ""
    if enable_tool_history and tool_events_in_window:
        tool_history_text = render_tool_history(
            tool_events=tool_events_in_window,
            tool_args_max_chars=tool_args_max_chars,
            tool_result_max_chars=tool_result_max_chars,
        )

    # 4. 合并注入文本
    combined = ""
    if history_text:
        combined = history_text
    if tool_history_text:
        combined = (
            combined + "\n\n" + tool_history_text if combined else tool_history_text
        )

    if not combined.strip():
        return

    if debug_logging:
        logger.debug(
            "[ChatContextPlus] 即将注入群聊历史上下文 "
            f"session={session_id} mode={injection_mode}\n{combined}"
        )

    # 5. 根据 injection_mode 选择注入位置，system_prompt_append 作为 fallback
    if injection_mode == "system_prompt_append":
        _inject_via_system_prompt(req, combined)
        return "system_prompt_append"

    try:
        _inject_via_extra_user(req, combined)
        return "extra_user_content_parts"
    except Exception as e:
        logger.warning(
            f"[ChatContextPlus] extra_user_content_parts 注入失败，"
            f"已回退到 system_prompt_append: {e}"
        )
        _inject_via_system_prompt(req, combined)
        return "system_prompt_append"
