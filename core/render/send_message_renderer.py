"""send_message_to_user 消息内容渲染器。

将 send_message_to_user 工具调用的 messages 参数渲染为文本和组件列表，
用于记录 Bot 主动发送的消息。
"""

from __future__ import annotations


def render_send_message_content(tool_args: dict | None) -> tuple[str, list[dict]]:
    """将 send_message_to_user 的消息参数渲染为 Bot 历史文本。

    Args:
        tool_args: send_message_to_user 工具的参数，其中 messages 为消息列表。

    Returns:
        (rendered_text, components): 渲染后的纯文本和组件列表。
    """
    if not isinstance(tool_args, dict):
        return "", []

    messages = tool_args.get("messages")
    if not isinstance(messages, list):
        return "", []

    parts: list[str] = []
    components: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue

        msg_type = str(msg.get("type", "")).lower()
        if not msg_type:
            continue
        components.append({"type": msg_type})

        if msg_type == "plain":
            text = str(msg.get("text", "")).strip()
            if text:
                parts.append(text)
        elif msg_type == "image":
            parts.append("[图片]")
        elif msg_type == "record":
            parts.append("[语音]")
        elif msg_type == "video":
            parts.append("[视频]")
        elif msg_type == "file":
            parts.append("[文件]")
        elif msg_type == "mention_user":
            mention_user_id = str(msg.get("mention_user_id", "")).strip()
            parts.append(f"[@{mention_user_id}]" if mention_user_id else "[@]")
        else:
            parts.append("[未知消息组件]")

    return " ".join(parts), components
