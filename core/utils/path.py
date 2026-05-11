"""UMO 路径转换工具。

将统一消息源 (UMO) 转换为 JSON 存储的相对路径。

示例:
    aiocqhttp:group:123456  →  sessions/aiocqhttp/group/123456.json
    telegram:group:123456   →  sessions/telegram/group/123456.json
"""

from __future__ import annotations

import re
from pathlib import Path

# 合法路径字符：字母、数字、下划线、短横线
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _safe_filename(raw: str) -> str:
    """将任意字符串转换为安全的文件名片段。"""
    cleaned = _SAFE_NAME_RE.sub("_", raw)
    return cleaned or "unknown"


def umo_to_relative_path(umo: str) -> Path:
    """将 UMO 字符串转换为相对于插件数据目录的 JSON 文件路径。

    Args:
        umo: 格式为 ``platform_id:message_type:session_id`` 的 UMO 字符串。

    Returns:
        相对路径，例如 ``sessions/aiocqhttp/group/123456.json``。
    """
    parts = umo.split(":")
    if len(parts) >= 3:
        platform = _safe_filename(parts[0])
        msg_type = _safe_filename(parts[1])
        session_id = _safe_filename(parts[2])
    elif len(parts) == 2:
        platform = _safe_filename(parts[0])
        msg_type = "unknown"
        session_id = _safe_filename(parts[1])
    else:
        # 完全无法解析，使用整体哈希作为文件名
        platform = "unknown"
        msg_type = "unknown"
        session_id = _safe_filename(umo)

    return Path("sessions") / platform / msg_type / f"{session_id}.json"


def is_group_umo(umo: str) -> bool:
    """判断 UMO 是否为群聊类型。"""
    parts = umo.split(":")
    if len(parts) >= 2:
        return parts[1].lower() == "group"
    return False


def get_plugin_data_dir() -> Path:
    """插件数据存储目录，位于 AstrBot 数据目录下的 plugin_data/astrbot_plugin_chat_context_plus。"""
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return (
        Path(get_astrbot_data_path())
        / "plugin_data"
        / "astrbot_plugin_chat_context_plus"
    )
