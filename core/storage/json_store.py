"""JSON 持久化存储。

每个会话对应一个 JSON 文件，内部保存有序 events 列表。
写入后按 store_max_events 裁剪旧事件。
线程安全通过 asyncio.Lock 保证（单会话粒度）。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from astrbot.api import logger

from ..utils.ids import EventIdGenerator
from ..utils.path import umo_to_relative_path
from .schema import SCHEMA_VERSION, MessageEvent, SessionData


class JsonStore:
    """管理所有会话的 JSON 持久化。"""

    def __init__(self, base_dir: Path, store_max_events: int = 100) -> None:
        """
        Args:
            base_dir: 插件数据根目录，例如 data/plugin_data/astrbot_plugin_chat_context_plus/
            store_max_events: 每个会话最多保留的 event 数量。
        """
        self._base_dir = base_dir
        self._store_max_events = store_max_events
        # UMO -> lock，避免同一会话并发写入
        self._locks: dict[str, asyncio.Lock] = {}
        # UMO -> EventIdGenerator 缓存
        self._id_generators: dict[str, EventIdGenerator] = {}
        # UMO -> platform_type 映射（用于路径中的平台目录名）
        self._platform_types: dict[str, str] = {}

    @property
    def store_max_events(self) -> int:
        return self._store_max_events

    @store_max_events.setter
    def store_max_events(self, value: int) -> None:
        self._store_max_events = value

    def set_platform_type(self, umo: str, platform_type: str) -> None:
        """注册 UMO 对应的平台类型，用于生成路径中的平台目录名。"""
        self._platform_types[umo] = platform_type

    def _get_lock(self, umo: str) -> asyncio.Lock:
        if umo not in self._locks:
            self._locks[umo] = asyncio.Lock()
        return self._locks[umo]

    def _file_path(self, umo: str) -> Path:
        platform_type = self._platform_types.get(umo)
        return self._base_dir / umo_to_relative_path(umo, platform_type=platform_type)

    def _ensure_dir(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

    def _read_raw(self, path: Path) -> SessionData:
        """从磁盘读取 JSON 文件，返回 SessionData。"""
        if not path.exists():
            return SessionData()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return SessionData.from_dict(data)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[ChatContextPlus] 读取会话文件失败 {path}: {e}")
            return SessionData()

    def _write_raw(self, path: Path, session: SessionData) -> None:
        """将 SessionData 写入磁盘。"""
        self._ensure_dir(path)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.error(f"[ChatContextPlus] 写入会话文件失败 {path}: {e}")

    def _trim_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """按 store_max_events 裁剪，只保留最近的事件。"""
        if self._store_max_events <= 0:
            return []
        if len(events) > self._store_max_events:
            return events[-self._store_max_events :]
        return events

    def get_id_generator(self, umo: str) -> EventIdGenerator:
        """获取指定会话的 ID 生成器（带缓存，首次自动从文件恢复）。"""
        if umo not in self._id_generators:
            gen = EventIdGenerator()
            path = self._file_path(umo)
            session = self._read_raw(path)
            gen.restore_from_events(session.events)
            self._id_generators[umo] = gen
        return self._id_generators[umo]

    async def append_event(self, umo: str, event_dict: dict[str, Any]) -> None:
        """向指定会话追加一个事件，写入后裁剪。"""
        lock = self._get_lock(umo)
        async with lock:
            path = self._file_path(umo)
            session = self._read_raw(path)
            session.umo = umo
            session.schema_version = SCHEMA_VERSION
            session.events.append(event_dict)
            session.events = self._trim_events(session.events)
            self._write_raw(path, session)

    async def append_bot_message(
        self,
        umo: str,
        content: str,
        bot_id: str,
        components: list[dict] | None = None,
        raw_chain: list[dict] | None = None,
    ) -> str:
        """构造一条 Bot 消息事件并追加到存储，返回 event_id。"""
        id_gen = self.get_id_generator(umo)
        event_id = id_gen.next_message_id()
        msg_event = MessageEvent(
            id=event_id,
            timestamp=int(time.time()),
            sender_id=bot_id,
            sender_name="AstrBot",
            role="bot",
            content=content,
            outline=content,
            message_str=content,
            components=components or [],
            raw_chain=raw_chain or [],
        )
        await self.append_event(umo, msg_event.to_dict())
        return event_id

    async def update_last_tool_event(
        self, umo: str, tool_name: str, updates: dict[str, Any]
    ) -> None:
        """找到最近一个 running 状态的同名工具事件并更新。"""
        lock = self._get_lock(umo)
        async with lock:
            path = self._file_path(umo)
            session = self._read_raw(path)
            for ev in reversed(session.events):
                if (
                    ev.get("type") == "tool"
                    and ev.get("tool_name") == tool_name
                    and ev.get("status") == "running"
                ):
                    ev.update(updates)
                    break
            self._write_raw(path, session)

    async def update_tool_event(
        self, umo: str, tool_id: str, updates: dict[str, Any]
    ) -> bool:
        """按 event id 精确更新工具事件。"""
        lock = self._get_lock(umo)
        async with lock:
            path = self._file_path(umo)
            session = self._read_raw(path)
            for ev in session.events:
                if ev.get("type") == "tool" and ev.get("id") == tool_id:
                    ev.update(updates)
                    self._write_raw(path, session)
                    return True
            self._write_raw(path, session)
            return False

    async def get_events(self, umo: str) -> list[dict[str, Any]]:
        """读取指定会话的所有事件。"""
        lock = self._get_lock(umo)
        async with lock:
            path = self._file_path(umo)
            session = self._read_raw(path)
            return session.events

    async def clear_events(self, umo: str) -> None:
        """清空指定会话的所有事件。"""
        lock = self._get_lock(umo)
        async with lock:
            path = self._file_path(umo)
            session = self._read_raw(path)
            session.events = []
            self._write_raw(path, session)
            # 重置 ID 生成器
            if umo in self._id_generators:
                del self._id_generators[umo]
            self._platform_types.pop(umo, None)

    async def get_event_count(self, umo: str) -> int:
        """获取指定会话的事件数量。"""
        events = await self.get_events(umo)
        return len(events)
