"""Event 数据模型。

定义统一 event log 的数据结构，所有事件共用一个有序列表。

schema_version = 1

事件类型:
- message: 用户消息或 Bot 回复
- tool: 工具调用记录
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class MessageEvent:
    """消息事件（用户消息或 Bot 回复）。"""

    id: str
    type: str = "message"
    timestamp: int = 0
    sender_id: str = ""
    sender_name: str = ""
    role: str = "user"  # "user" | "bot"
    content: str = ""
    outline: str = ""
    message_str: str = ""
    components: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolEvent:
    """工具调用事件。"""

    id: str
    type: str = "tool"
    timestamp: int = 0
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    tool_result: str = ""
    status: str = "running"  # "running" | "success" | "error"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionData:
    """单个会话的完整 JSON 数据。"""

    schema_version: int = SCHEMA_VERSION
    umo: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "umo": self.umo,
            "events": self.events,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionData:
        return cls(
            schema_version=data.get("schema_version", SCHEMA_VERSION),
            umo=data.get("umo", ""),
            events=data.get("events", []),
        )
