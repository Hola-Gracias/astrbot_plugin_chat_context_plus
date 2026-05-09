"""chat_context_plus 插件主入口。

功能：
- 接管群聊场景下的短期上下文记录
- 规则触发判断（@Bot、回复Bot、关键词、概率）
- 临时上下文注入（extra_user_content_parts + mark_as_temp）
- 工具调用历史可选记录与注入
- 插件指令（/ccp status, /ccp clear, /ccp history）

注意事项：
- 用户必须关闭 AstrBot 本体的「群聊上下文感知」和「主动回复」
- 插件会清空本轮 req.contexts，由插件 history 接管群聊上下文
- 插件不会删除本体 DB 历史
- 插件注入的 history 默认使用 mark_as_temp，不写入本体 conversation history
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.platform.message_session import MessageSession
from astrbot.core.platform.message_type import MessageType
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .core.compat.injector import inject_history
from .core.storage.json_store import JsonStore
from .core.storage.schema import MessageEvent, ToolEvent
from .core.trigger.rule_trigger import RuleTrigger
from .core.utils.truncate import truncate_text

# ─── 消息内容提取辅助 ───


def get_plugin_data_dir() -> Path:
    """
    获取插件数据存储目录，位于 AstrBot 数据目录下的 plugin_data/astrbot_plugin_chat_context_plus。
    """
    return (
        Path(get_astrbot_data_path())
        / "plugin_data"
        / "astrbot_plugin_chat_context_plus"
    )


def _is_ccp_command(event: AstrMessageEvent) -> bool:
    """Return True when the message is handled by this plugin's command group."""
    try:
        text = event.get_message_str() or ""
    except Exception:
        text = getattr(event, "message_str", "") or ""
    return text.strip().lower().startswith("/ccp")


def _extract_content(event: AstrMessageEvent) -> tuple[str, str, str, list[dict]]:
    """从事件中提取消息内容。

    Returns:
        (outline, content, message_str, components_summary)
    """
    # 主路径：get_message_outline
    outline = ""
    try:
        outline = event.get_message_outline() or ""
    except Exception:
        pass

    # fallback：get_message_str
    message_str = ""
    try:
        message_str = event.get_message_str() or ""
    except Exception:
        pass

    content = outline or message_str

    # 如果都为空，手动解析组件生成占位符
    if not content:
        content = _fallback_content(event)

    # 轻量 components 摘要
    components = _extract_components_summary(event)

    return outline, content, message_str, components


def _fallback_content(event: AstrMessageEvent) -> str:
    """手动解析消息组件，生成占位符文本。"""
    try:
        chain = event.message_obj.message or []
    except Exception:
        return "[未知消息]"

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
            target = getattr(comp, "user_id", None) or getattr(comp, "qq", None) or ""
            parts.append(f"[@{target}]")
        elif isinstance(comp, Comp.Face):
            parts.append("[表情]")
        else:
            parts.append("[未知消息组件]")

    return " ".join(parts) if parts else "[空消息]"


def _extract_components_summary(event: AstrMessageEvent) -> list[dict]:
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


# ─── 插件主类 ───


class ChatContextPlusPlugin(Star):
    """群聊上下文增强插件。"""

    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.config = config
        self.store: JsonStore | None = None
        self.trigger: RuleTrigger | None = None
        self.enabled = True
        self.store_max_events = 100
        self.inject_message_count = 30
        self.enable_tool_history = False
        self.tool_args_max_chars = 500
        self.tool_result_max_chars = 1000
        self.debug_logging = False
        self.injection_mode = "extra_user_content_parts"

    async def initialize(self) -> None:
        """插件初始化：读取配置、创建存储和触发器。"""
        self._load_config()
        logger.info("[ChatContextPlus] 插件已初始化")

    def _load_config(self) -> None:
        """从配置中加载参数，初始化 store 和 trigger。"""
        gc = self.config.get("group_context", {})
        rp = self.config.get("reply", {})
        compat = self.config.get("compatibility", {})

        self.enabled = gc.get("enabled", True)
        self.store_max_events = gc.get("store_max_events", 100)
        self.inject_message_count = gc.get("inject_message_count", 30)
        self.enable_tool_history = gc.get("enable_tool_history", False)
        self.tool_args_max_chars = gc.get("tool_args_max_chars", 500)
        self.tool_result_max_chars = gc.get("tool_result_max_chars", 1000)
        self.debug_logging = self.config.get("debug_logging", False)
        self.injection_mode = compat.get("injection_mode", "extra_user_content_parts")

        # 存储
        data_dir = get_plugin_data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)

        if self.store is None:
            self.store = JsonStore(
                base_dir=data_dir, store_max_events=self.store_max_events
            )
        else:
            self.store.store_max_events = self.store_max_events

        # 触发器
        trigger_kwargs = {
            "trigger_on_at": rp.get("trigger_on_at", True),
            "trigger_on_reply": rp.get("trigger_on_reply", True),
            "trigger_keywords": rp.get("trigger_keywords", []),
            "blacklist_keywords": rp.get("blacklist_keywords", []),
            "active_reply_probability": rp.get("active_reply_probability", 0.0),
        }
        if self.trigger is None:
            self.trigger = RuleTrigger(**trigger_kwargs)
        else:
            self.trigger.update_config(**trigger_kwargs)

    def _is_enabled(self) -> bool:
        return self.enabled

    def _resolve_send_message_target_umo(
        self, event: AstrMessageEvent, tool_args: dict | None
    ) -> str | None:
        """Resolve send_message_to_user target session to UMO if it is a group."""
        current_session = event.unified_msg_origin
        session_arg = (
            tool_args.get("session")
            if isinstance(tool_args, dict) and tool_args.get("session")
            else current_session
        )

        try:
            if isinstance(session_arg, MessageSession):
                target_session = session_arg
            elif isinstance(session_arg, str) and ":" in session_arg:
                target_session = MessageSession.from_str(session_arg)
            elif isinstance(session_arg, str) and current_session:
                current = MessageSession.from_str(current_session)
                target_session = MessageSession(
                    platform_name=current.platform_id,
                    message_type=current.message_type,
                    session_id=session_arg,
                )
            else:
                return None
        except Exception as e:
            if self.debug_logging:
                logger.debug(
                    f"[ChatContextPlus] 解析 send_message_to_user 目标会话失败: {e}"
                )
            return None

        if target_session.message_type != MessageType.GROUP_MESSAGE:
            return None
        return str(target_session)

    def _render_send_message_content(
        self, tool_args: dict | None
    ) -> tuple[str, list[dict]]:
        """Render send_message_to_user message args into stored bot history text."""
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

    async def _append_bot_message(
        self,
        umo: str,
        event: AstrMessageEvent,
        content: str,
        components: list[dict] | None = None,
    ) -> str | None:
        """Append a bot message event to the target UMO history."""
        if self.store is None or not content:
            return None

        id_gen = self.store.get_id_generator(umo)
        event_id = id_gen.next_message_id()
        bot_id = event.get_self_id() or "astrbot"

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
        )

        await self.store.append_event(umo, msg_event.to_dict())
        return event_id

    async def _record_send_message_to_user(
        self,
        event: AstrMessageEvent,
        tool_args: dict | None,
        status: str,
        result_text: str,
    ) -> None:
        """Record successful send_message_to_user calls as bot group history."""
        if status != "success" or result_text.strip().lower().startswith("error:"):
            return

        target_umo = self._resolve_send_message_target_umo(event, tool_args)
        if not target_umo:
            return

        content, components = self._render_send_message_content(tool_args)
        if not content:
            return

        event_id = await self._append_bot_message(
            target_umo, event, content, components
        )
        if self.debug_logging and event_id:
            logger.debug(
                f"[ChatContextPlus] 已记录主动发送消息: {target_umo} event={event_id}"
            )

    async def _get_current_conversation(self, event: AstrMessageEvent) -> Any | None:
        """获取或创建当前 AstrBot 会话，用于保留本体人格选择。"""
        try:
            conv_mgr = self.context.conversation_manager
            umo = event.unified_msg_origin
            cid = await conv_mgr.get_curr_conversation_id(umo)
            if not cid:
                cid = await conv_mgr.new_conversation(umo, event.get_platform_id())
            conversation = await conv_mgr.get_conversation(umo, cid)
            if not conversation:
                cid = await conv_mgr.new_conversation(umo, event.get_platform_id())
                conversation = await conv_mgr.get_conversation(umo, cid)
            return conversation
        except Exception as e:
            logger.warning(f"[ChatContextPlus] 获取当前会话失败: {e}")
            return None

    # ─── 群聊消息 handler ───

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """处理所有群聊消息：记录 + 触发判断。"""
        if not self._is_enabled() or self.store is None or self.trigger is None:
            return

        umo = event.unified_msg_origin

        if _is_ccp_command(event):
            event.set_extra("ccp_command", True)
            return

        # 构造 message event
        outline, content, message_str, components = _extract_content(event)
        id_gen = self.store.get_id_generator(umo)
        event_id = id_gen.next_message_id()

        msg_event = MessageEvent(
            id=event_id,
            timestamp=int(time.time()),
            sender_id=event.get_sender_id(),
            sender_name=event.get_sender_name(),
            role="user",
            content=content,
            outline=outline,
            message_str=message_str,
            components=components,
        )

        # 立即写入 JSON
        await self.store.append_event(umo, msg_event.to_dict())

        # 将 current_event_id 写入 event.extra
        event.set_extra("ccp_event_id", event_id)
        event.set_extra("ccp_umo", umo)

        # 规则判断是否触发 LLM
        should_trigger = self.trigger.should_trigger(event)

        if should_trigger:
            # 普通群消息由插件规则触发时，本体不会自动标记为 @/唤醒词消息。
            event.is_wake = True
            event.is_at_or_wake_command = True

        # 纯 @ / 空文本但规则已触发时，给核心 Agent 一个非空 ProviderRequest
        if should_trigger and not (event.message_str or "").strip():
            conversation = await self._get_current_conversation(event)
            req = event.request_llm(
                prompt=(
                    "用户在群聊中 @ 了你，但没有输入文字。"
                    "请先结合最近群聊历史自然回应；如果上下文不足再简短询问对方有什么事。"
                    "不要提到这是一条系统补全文本。"
                ),
                conversation=conversation,
            )
            event.set_extra("provider_request", req)

        event.should_call_llm(not should_trigger)

        if should_trigger:
            event.set_extra("ccp_triggered", True)
            logger.debug(f"[ChatContextPlus] 触发 LLM: {umo} event={event_id}")

        # 不 stop_event

    # ─── LLM 请求 hook ───

    @filter.on_llm_request()
    async def on_llm_request(
        self, event: AstrMessageEvent, req: ProviderRequest
    ) -> None:
        """LLM 请求钩子：注入群聊历史上下文。"""
        if not self._is_enabled() or self.store is None:
            return

        # 确认是插件接管的群聊请求
        umo = event.get_extra("ccp_umo")
        if not umo:
            return
        if not event.get_extra("ccp_triggered"):
            return

        exclude_id = event.get_extra("ccp_event_id")

        # 读取事件列表
        events = await self.store.get_events(umo)

        # 获取会话 ID 和 Bot ID
        session_id = event.get_session_id()
        bot_id = event.get_self_id()

        await inject_history(
            req=req,
            events=events,
            inject_message_count=self.inject_message_count,
            exclude_event_id=exclude_id,
            enable_tool_history=self.enable_tool_history,
            tool_args_max_chars=self.tool_args_max_chars,
            tool_result_max_chars=self.tool_result_max_chars,
            session_id=session_id,
            bot_id=bot_id,
            injection_mode=self.injection_mode,
            debug_logging=self.debug_logging,
        )

    # ─── 工具调用 hooks ───

    @filter.on_using_llm_tool()
    async def on_tool_start(
        self, event: AstrMessageEvent, tool, tool_args: dict | None
    ) -> None:
        """工具开始 hook：记录 tool event（状态 running）。"""
        if not self.enable_tool_history:
            return
        if self.store is None:
            return

        umo = event.get_extra("ccp_umo")
        if not umo:
            return

        id_gen = self.store.get_id_generator(umo)
        tool_id = id_gen.next_tool_id()
        tool_name = getattr(tool, "name", "unknown_tool")

        # 截断 tool_args
        truncated_args = None
        if tool_args:
            try:
                import json

                args_str = json.dumps(tool_args, ensure_ascii=False)
                if len(args_str) > self.tool_args_max_chars:
                    truncated_args = {
                        "_truncated": truncate_text(args_str, self.tool_args_max_chars)
                    }
                else:
                    truncated_args = tool_args
            except Exception:
                truncated_args = tool_args

        tool_event = ToolEvent(
            id=tool_id,
            timestamp=int(time.time()),
            tool_name=tool_name,
            tool_args=truncated_args,
            status="running",
        )

        await self.store.append_event(umo, tool_event.to_dict())

        # 记录 pending tool 信息到 event.extra
        event.set_extra("ccp_pending_tool_name", tool_name)
        event.set_extra("ccp_pending_tool_id", tool_id)

    @filter.on_llm_tool_respond()
    async def on_tool_end(
        self, event: AstrMessageEvent, tool, tool_args: dict | None, tool_result
    ) -> None:
        """工具结束 hook：更新 tool event 状态和结果。"""
        if not self._is_enabled() or self.store is None:
            return

        tool_name = getattr(tool, "name", "unknown_tool")

        # 提取结果文本
        result_text = ""
        if tool_result is not None:
            try:
                # CallToolResult 有 content 属性
                if hasattr(tool_result, "content") and tool_result.content:
                    parts = []
                    for block in tool_result.content:
                        if hasattr(block, "text"):
                            parts.append(block.text)
                    result_text = "\n".join(parts)
                else:
                    result_text = str(tool_result)
            except Exception:
                result_text = str(tool_result)

        result_text = truncate_text(result_text, self.tool_result_max_chars)

        # 判断状态
        status = "success"
        if (
            tool_result is not None
            and hasattr(tool_result, "isError")
            and tool_result.isError
        ):
            status = "error"

        if tool_name == "send_message_to_user":
            await self._record_send_message_to_user(
                event=event,
                tool_args=tool_args,
                status=status,
                result_text=result_text,
            )

        if not self.enable_tool_history:
            return

        umo = event.get_extra("ccp_umo")
        if not umo:
            return

        pending_tool_id = event.get_extra("ccp_pending_tool_id")
        updated = False
        if pending_tool_id:
            updated = await self.store.update_tool_event(
                umo,
                pending_tool_id,
                {
                    "status": status,
                    "tool_result": result_text,
                },
            )

        if not updated:
            await self.store.update_last_tool_event(
                umo,
                tool_name,
                {
                    "status": status,
                    "tool_result": result_text,
                },
            )

    # ─── Bot 回复记录 hook ───

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent) -> None:
        """消息发送后 hook：记录 Bot 的实际回复。"""
        if not self._is_enabled() or self.store is None:
            return
        if event.get_extra("ccp_command"):
            return

        umo = event.get_extra("ccp_umo")
        if not umo:
            return

        # 提取 Bot 回复内容
        bot_content = ""
        result = event.get_result()
        if result:
            try:
                bot_content = result.get_plain_text() or ""
            except Exception:
                pass

        if not bot_content:
            # 尝试从 chain 中提取
            if result and hasattr(result, "chain") and result.chain:
                parts = []
                for comp in result.chain:
                    if isinstance(comp, Comp.Plain):
                        parts.append(comp.text)
                    elif isinstance(comp, Comp.Image):
                        parts.append("[图片]")
                    else:
                        parts.append(f"[{type(comp).__name__}]")
                bot_content = "".join(parts)

        if not bot_content:
            # fallback: 使用 on_llm_response 预存的 LLM 输出
            bot_content = event.get_extra("ccp_llm_response_text") or ""

        if not bot_content:
            bot_content = "[Bot 回复内容无法提取]"

        await self._append_bot_message(umo, event, bot_content)

    # ─── LLM 响应 fallback hook ───

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, response: LLMResponse
    ) -> None:
        """LLM 响应 hook：作为 after_message_sent 的 fallback 备份记录。

        仅在 after_message_sent 未成功记录时使用。
        通过 extra 标记避免重复记录。
        """
        # 预存 LLM 输出，供 after_message_sent fallback 使用
        if not self._is_enabled():
            return

        umo = event.get_extra("ccp_umo")
        if not umo:
            return

        try:
            completion = response.completion_text or ""
            if completion:
                event.set_extra("ccp_llm_response_text", completion)
        except Exception:
            pass

    # ─── 插件指令 ───

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("ccp")
    async def ccp_command(self, event: AstrMessageEvent, sub: str = ""):
        """群聊上下文增强插件指令。

        用法：
          /ccp status  - 查看当前群聊插件状态和历史数量
          /ccp clear   - 清空当前群聊历史
          /ccp history - 查看最近保存的历史条数
        """
        event.set_extra("ccp_command", True)
        sub = sub.strip().lower()

        if sub == "status":
            yield event.plain_result(await self._cmd_status(event))
        elif sub == "clear":
            yield event.plain_result(await self._cmd_clear(event))
        elif sub == "history":
            yield event.plain_result(await self._cmd_history(event))
        else:
            yield event.plain_result(
                "用法：\n"
                "  /ccp status  - 查看插件状态\n"
                "  /ccp clear   - 清空当前群聊历史\n"
                "  /ccp history - 查看最近历史摘要"
            )

    async def _cmd_status(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin

        if self.store is None:
            return "插件存储未初始化。"

        count = await self.store.get_event_count(umo)
        msg_count = 0
        tool_count = 0
        events = await self.store.get_events(umo)
        for ev in events:
            if ev.get("type") == "message":
                msg_count += 1
            elif ev.get("type") == "tool":
                tool_count += 1

        return (
            f"ChatContextPlus 状态\n"
            f"  启用: {'是' if self.enabled else '否'}\n"
            f"  当前群聊: {umo}\n"
            f"  总事件数: {count}\n"
            f"  消息事件: {msg_count}\n"
            f"  工具事件: {tool_count}\n"
            f"  存储上限: {self.store_max_events}\n"
            f"  注入消息数: {self.inject_message_count}\n"
            f"  工具历史: {'开启' if self.enable_tool_history else '关闭'}\n"
            f"  调试日志: {'开启' if self.debug_logging else '关闭'}"
        )

    async def _cmd_clear(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        if self.store is None:
            return "插件存储未初始化。"
        await self.store.clear_events(umo)
        return f"已清空群聊 {umo} 的历史记录。"

    async def _cmd_history(self, event: AstrMessageEvent) -> str:
        umo = event.unified_msg_origin
        if self.store is None:
            return "插件存储未初始化。"

        events = await self.store.get_events(umo)
        msg_events = [ev for ev in events if ev.get("type") == "message"]

        if not msg_events:
            return "当前群聊没有保存的历史消息。"

        # 展示最近 10 条
        recent = msg_events[-10:]
        lines = [f"最近 {len(recent)} 条消息（共 {len(msg_events)} 条）："]
        for ev in recent:
            role = ev.get("role", "user")
            name = ev.get("sender_name", "未知")
            if role == "bot":
                name = "AstrBot"
            content = ev.get("outline") or ev.get("content") or ""
            if len(content) > 50:
                content = content[:50] + "..."
            lines.append(f"  [{ev.get('id', '?')}] {name}: {content}")

        return "\n".join(lines)

    # ─── 生命周期 ───

    async def terminate(self) -> None:
        """插件卸载时的清理。"""
        logger.info("[ChatContextPlus] 插件已卸载")
