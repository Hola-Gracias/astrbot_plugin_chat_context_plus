"""UMO 路径转换工具。

将统一消息源 (UMO) 转换为 JSON 存储的相对路径。

UMO 格式为 ``platform_id:MessageType:session_id``：
- platform_id = platform_meta.id（用户配置的实例名）
- MessageType = 枚举值（GroupMessage / FriendMessage / OtherMessage）
- session_id = 会话 ID

优先使用调用方传入的 ``platform_type``（= platform_meta.name，如 ``aiocqhttp``），
未传入时回退到 UMO 第一段（经 _safe_filename 处理）。
"""

from __future__ import annotations

import re
from pathlib import Path

# 合法路径字符：字母、数字、下划线、短横线
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_\-]")

# MessageType 枚举值 → 目录短名称
_MSG_TYPE_MAP: dict[str, str] = {
    "GroupMessage": "group",
    "FriendMessage": "private",
    "OtherMessage": "other",
}


def _safe_filename(raw: str) -> str:
    """将任意字符串转换为安全的文件名片段。"""
    cleaned = _SAFE_NAME_RE.sub("_", raw)
    return cleaned or "unknown"


def _normalize_msg_type(raw: str) -> str:
    """将 MessageType 枚举值映射为目录短名称。"""
    return _MSG_TYPE_MAP.get(raw, raw.lower())


def umo_to_relative_path(umo: str, platform_type: str | None = None) -> Path:
    """将 UMO 字符串转换为相对于插件数据目录的 JSON 文件路径。

    Args:
        umo: UMO 字符串。
        platform_type: 平台类型（platform_meta.name，如 ``aiocqhttp``）。
                       传入时优先使用；未传入时从 UMO 第一段解析。

    Returns:
        相对路径，例如 ``sessions/aiocqhttp/group/123456.json``。
    """
    parts = umo.split(":")
    if len(parts) >= 3:
        platform = (
            _safe_filename(platform_type) if platform_type else _safe_filename(parts[0])
        )
        msg_type = _normalize_msg_type(parts[1])
        session_id = _safe_filename(parts[2])
    elif len(parts) == 2:
        platform = (
            _safe_filename(platform_type) if platform_type else _safe_filename(parts[0])
        )
        msg_type = "unknown"
        session_id = _safe_filename(parts[1])
    else:
        platform = _safe_filename(platform_type) if platform_type else "unknown"
        msg_type = "unknown"
        session_id = _safe_filename(umo)

    return Path(platform) / "history" / msg_type / f"{session_id}.json"


def is_group_umo(umo: str) -> bool:
    """判断 UMO 是否为群聊类型。"""
    parts = umo.split(":")
    if len(parts) >= 2:
        return _normalize_msg_type(parts[1]) == "group"
    return False


def get_plugin_data_dir() -> Path:
    """插件数据存储目录，位于 AstrBot 数据目录下的 plugin_data/astrbot_plugin_chat_context_plus。"""
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path

    return (
        Path(get_astrbot_data_path())
        / "plugin_data"
        / "astrbot_plugin_chat_context_plus"
    )
