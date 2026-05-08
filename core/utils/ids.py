"""Event ID 生成工具。

生成格式：
- 消息事件: M0001, M0002, M0003 ...
- 工具事件: T0001, T0002, T0003 ...

同一个 session 内递增，读取已有 JSON 后继续递增。
"""

from __future__ import annotations


class EventIdGenerator:
    """为单个 session 生成递增的 Event ID。"""

    def __init__(self) -> None:
        self._msg_counter: int = 0
        self._tool_counter: int = 0

    def restore_from_events(self, events: list[dict]) -> None:
        """从已有 events 列表中恢复计数器，确保后续 ID 不重复。"""
        for ev in events:
            eid: str = ev.get("id", "")
            if eid.startswith("M"):
                try:
                    num = int(eid[1:])
                    if num > self._msg_counter:
                        self._msg_counter = num
                except ValueError:
                    pass
            elif eid.startswith("T"):
                try:
                    num = int(eid[1:])
                    if num > self._tool_counter:
                        self._tool_counter = num
                except ValueError:
                    pass

    def next_message_id(self) -> str:
        self._msg_counter += 1
        return f"M{self._msg_counter:04d}"

    def next_tool_id(self) -> str:
        self._tool_counter += 1
        return f"T{self._tool_counter:04d}"
