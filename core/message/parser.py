"""消息内容提取、组件渲染与引用处理。

从 main.py 拆分出来，供 ChatContextPlusPlugin 的 on_group_message 调用。
"""

from __future__ import annotations

from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api.event import AstrMessageEvent

from ..utils.truncate import truncate_text


def is_ccp_command(event: AstrMessageEvent) -> bool:
    """Return True when the message is handled by this plugin's command group."""
    try:
        text = event.get_message_str() or ""
    except Exception:
        text = getattr(event, "message_str", "") or ""
    return text.strip().lower().startswith("/ccp")


def is_plain_text_message(event: AstrMessageEvent) -> bool:
    """纯文本消息（仅含 Plain 组件）返回 True，可走本体 outline 快速路径。"""
    try:
        chain = event.message_obj.message or []
    except Exception:
        return True
    has_plain = False
    for comp in chain:
        if isinstance(comp, Comp.Plain):
            has_plain = True
        else:
            return False
    return has_plain


def render_message_chain(chain: list) -> str:
    """将消息组件链递归渲染为纯文本摘要（插件自定义格式，不依赖本体 outline）。"""
    parts: list[str] = []
    for comp in chain:
        if isinstance(comp, Comp.Plain):
            if comp.text.strip():
                parts.append(comp.text)
        elif isinstance(comp, Comp.Image):
            parts.append("[图片]")
        elif isinstance(comp, Comp.Record):
            parts.append("[语音]")
        elif isinstance(comp, Comp.Video):
            parts.append("[视频]")
        elif isinstance(comp, Comp.File):
            parts.append("[文件]")
        elif isinstance(comp, Comp.At):
            qq = getattr(comp, "qq", None) or ""
            name = getattr(comp, "name", None) or ""
            if str(qq).lower() == "all":
                parts.append("@全体成员")
            elif name:
                parts.append(f"@{name}({qq})")
            else:
                parts.append(f"@{qq}")
        elif isinstance(comp, Comp.Face):
            parts.append("[表情]")
        elif isinstance(comp, Comp.Reply):
            sender = getattr(comp, "sender_nickname", "") or "未知"
            sender_id = getattr(comp, "sender_id", "") or getattr(comp, "qq", "") or ""
            reply_chain = getattr(comp, "chain", None) or []
            if reply_chain:
                inner = render_message_chain(reply_chain)
            else:
                inner = (
                    getattr(comp, "message_str", "")
                    or getattr(comp, "text", "")
                    or "[空消息]"
                )
            parts.append(f"[引用消息: {sender}({sender_id}): {inner}]")
        elif getattr(comp, "type", "") in ("forward", "node", "nodes"):
            parts.append("[合并转发消息]")
        else:
            parts.append("[未知消息组件]")
    return " ".join(parts) if parts else ""


def extract_content(event: AstrMessageEvent) -> tuple[str, str, str, list[dict]]:
    """从事件中提取消息内容。

    纯文本消息走本体 get_message_outline() 快速路径；
    含 Reply/Image/At 等组件的消息走 render_message_chain() 自定义渲染。

    Returns:
        (outline, content, message_str, components_summary)
    """
    message_str = ""
    try:
        message_str = event.get_message_str() or ""
    except Exception:
        pass

    if is_plain_text_message(event):
        outline = ""
        try:
            outline = event.get_message_outline() or ""
        except Exception:
            pass
        content = outline or message_str
    else:
        outline = ""
        try:
            chain = event.message_obj.message or []
        except Exception:
            chain = []
        content = render_message_chain(chain) or message_str

    components = extract_components_summary(event)

    return outline, content, message_str, components


def extract_reply(event: AstrMessageEvent) -> dict | None:
    """从事件的消息链中提取 Reply 组件信息，使用自定义渲染器处理 reply.chain。

    渲染后清掉原始 Reply.chain 中的 Image，阻止本体 _process_quote_message
    再次调 VL 转述引用图片。
    """
    try:
        chain = event.message_obj.message or []
    except Exception:
        return None

    for comp in chain:
        if not isinstance(comp, Comp.Reply):
            continue

        reply_sender_id = str(
            getattr(comp, "sender_id", "") or getattr(comp, "qq", "") or ""
        )
        reply_sender_name = str(getattr(comp, "sender_nickname", "") or "")
        reply_time = int(getattr(comp, "time", 0) or 0)

        reply_chain = getattr(comp, "chain", None) or []
        if reply_chain:
            reply_content = render_message_chain(reply_chain)
            # 清掉原始 Reply.chain 中的 Image
            comp.chain = [c for c in reply_chain if not isinstance(c, Comp.Image)]
        else:
            reply_content = str(
                getattr(comp, "message_str", "") or getattr(comp, "text", "") or ""
            )

        if not reply_content.strip():
            return None

        reply_content = truncate_text(reply_content, 150)

        return {
            "sender_id": reply_sender_id,
            "sender_name": reply_sender_name,
            "time": reply_time,
            "content": reply_content,
        }

    return None


def extract_components_summary(event: AstrMessageEvent) -> list[dict]:
    """提取轻量的消息组件摘要，为后续多模态扩展预留。"""
    try:
        chain = event.message_obj.message or []
    except Exception:
        return []

    summary: list[dict] = []
    for comp in chain:
        comp_type = getattr(comp, "type", None)
        if comp_type:
            summary.append({"type": str(comp_type)})
        else:
            summary.append({"type": type(comp).__name__})
    return summary


def is_empty_mention(event: AstrMessageEvent) -> bool:
    """Return True only when the message is just an @ mention to this bot."""
    try:
        chain = event.get_messages() or []
    except Exception:
        try:
            chain = event.message_obj.message or []
        except Exception:
            return False

    meaningful: list[Any] = []
    for comp in chain:
        if isinstance(comp, Comp.Plain):
            if comp.text.strip():
                return False
            continue
        meaningful.append(comp)

    if len(meaningful) != 1 or not isinstance(meaningful[0], Comp.At):
        return False

    at = meaningful[0]
    target = getattr(at, "user_id", None) or getattr(at, "qq", None)
    return target is not None and str(target) == str(event.get_self_id())
