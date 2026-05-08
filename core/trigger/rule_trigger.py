"""规则触发器。

根据配置的规则判断群聊消息是否应触发 LLM 回复。

优先级：
1. 黑名单关键词命中 → 不触发
2. 明确 @Bot → 触发
3. 回复 Bot 消息 → 触发
4. 触发关键词命中 → 触发
5. 主动回复概率命中 → 触发
6. 否则 → 不触发
"""

from __future__ import annotations

import random
from typing import Any

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class RuleTrigger:
    """规则触发判断器。"""

    def __init__(
        self,
        trigger_on_at: bool = True,
        trigger_on_reply: bool = True,
        trigger_keywords: list[str] | None = None,
        blacklist_keywords: list[str] | None = None,
        active_reply_probability: float = 0.0,
    ) -> None:
        self.trigger_on_at = trigger_on_at
        self.trigger_on_reply = trigger_on_reply
        self.trigger_keywords = trigger_keywords or []
        self.blacklist_keywords = blacklist_keywords or []
        self.active_reply_probability = active_reply_probability

    def update_config(
        self,
        trigger_on_at: bool = True,
        trigger_on_reply: bool = True,
        trigger_keywords: list[str] | None = None,
        blacklist_keywords: list[str] | None = None,
        active_reply_probability: float = 0.0,
    ) -> None:
        """动态更新配置。"""
        self.trigger_on_at = trigger_on_at
        self.trigger_on_reply = trigger_on_reply
        self.trigger_keywords = trigger_keywords or []
        self.blacklist_keywords = blacklist_keywords or []
        self.active_reply_probability = active_reply_probability

    def should_trigger(self, event: AstrMessageEvent) -> bool:
        """判断是否应触发 LLM 回复。

        Args:
            event: 群聊消息事件。

        Returns:
            True 表示应触发，False 表示不触发。
        """
        message_text = event.message_str or ""
        bot_id = event.get_self_id()
        message_chain = self._get_message_chain(event)

        # 1. 黑名单关键词 → 不触发（最高优先级）
        if self._check_blacklist(message_text):
            return False

        # 2. 被 @Bot → 触发
        if self.trigger_on_at and self._check_at_bot(message_chain, bot_id):
            return True

        # 3. 回复 Bot 消息 → 触发
        if self.trigger_on_reply and self._check_reply_bot(message_chain, bot_id):
            return True

        # 4. 触发关键词 → 触发
        if self._check_keywords(message_text):
            return True

        # 5. 主动回复概率 → 触发
        if self._check_probability():
            return True

        # 6. 否则 → 不触发
        return False

    def _get_message_chain(self, event: AstrMessageEvent) -> list[Any]:
        """安全获取消息链。优先使用 event.get_messages()，fallback 到 message_obj。"""
        try:
            return event.get_messages() or []
        except Exception:
            pass

        try:
            msg_obj = getattr(event, "message_obj", None)
            if msg_obj and hasattr(msg_obj, "message"):
                return msg_obj.message or []
        except Exception:
            pass

        return []

    def _check_blacklist(self, text: str) -> bool:
        """检查是否命中黑名单关键词。"""
        if not self.blacklist_keywords or not text:
            return False
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.blacklist_keywords if kw)

    def _check_at_bot(self, chain: list[Any], bot_id: str) -> bool:
        """检查消息链中是否有 @Bot。

        兼容策略：优先检查 At.user_id（文档推荐），再 fallback 到 At.qq。
        """
        for comp in chain:
            if isinstance(comp, Comp.At):
                # 优先使用 user_id（文档中 Comp.At(user_id=...) 的写法）
                user_id = getattr(comp, "user_id", None)
                if user_id is not None and str(user_id) == str(bot_id):
                    return True
                # 兜底：At.qq（文档中 At(qq=...) 的写法），可能是 int 或 str
                qq = getattr(comp, "qq", None)
                if qq is not None and str(qq) == str(bot_id):
                    return True
        return False

    def _check_reply_bot(self, chain: list[Any], bot_id: str) -> bool:
        """检查消息是否是对 Bot 消息的回复。"""
        for comp in chain:
            if isinstance(comp, Comp.Reply):
                sender_id = getattr(comp, "sender_id", None)
                if sender_id is not None and str(sender_id) == str(bot_id):
                    return True
        return False

    def _check_keywords(self, text: str) -> bool:
        """检查是否命中触发关键词。"""
        if not self.trigger_keywords or not text:
            return False
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.trigger_keywords if kw)

    def _check_probability(self) -> bool:
        """根据概率判断是否主动回复。"""
        probability = float(self.active_reply_probability)
        if probability <= 0.0:
            logger.debug(f"[ChatContextPlus] 当前回复概率{probability:.3f}，未触发回复")
            return False
        if probability >= 1.0:
            logger.debug(f"[ChatContextPlus] 当前回复概率{probability:.3f}，触发回复")
            return True
        triggered = random.random() < probability
        status = "触发回复" if triggered else "未触发回复"
        logger.debug(f"[ChatContextPlus] 当前回复概率{probability:.3f}，{status}")
        return triggered
