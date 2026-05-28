"""图片转述引擎。

负责调用图片转述模型，管理 URL → caption 缓存的读写。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger
from astrbot.api.provider import Provider
from astrbot.api.star import Context

from ..storage.image_store import _FAILED_CAPTION_SENTINEL, ImageStore

_DEFAULT_PROMPT = "请描述这张图片。"


class ImageCaptioner:
    """图片转述器，封装模型调用和缓存逻辑。"""

    def __init__(self, context: Context, image_store: ImageStore) -> None:
        self._context = context
        self._image_store = image_store
        self._warned_missing_provider = False

    # ── 公开方法 ──

    def has_provider(self, umo: str | None = None) -> bool:
        """检测是否配置了可用的图片转述模型。"""
        return self._resolve_provider(umo) is not None

    async def ensure_events_captioned(
        self, events: list[dict[str, Any]], umo: str, platform_type: str
    ) -> dict[str, str]:
        """遍历事件列表中的图片，未缓存则调模型转述并写入缓存。

        Returns:
            ``{source_id: caption}`` 供渲染器使用。
        """
        result: dict[str, str] = {}
        provider = self._resolve_provider(umo)
        if provider is None:
            return result
        prompt = self._resolve_prompt(umo)

        for ev in events:
            if ev.get("type") != "message":
                continue
            for comp in ev.get("raw_chain") or []:
                if comp.get("type") != "image":
                    continue
                data = comp.get("data") or {}
                source_id = data.get("url") or data.get("file") or ""
                if not source_id or source_id in result:
                    continue

                # 查缓存
                cached = await self._image_store.get_caption(platform_type, source_id)
                if cached is not None:
                    if cached == _FAILED_CAPTION_SENTINEL:
                        continue
                    result[source_id] = cached
                    continue

                # 调模型
                caption = await self._call_provider(
                    provider, platform_type, source_id, prompt
                )
                if caption:
                    await self._image_store.set_caption(
                        platform_type, source_id, caption
                    )
                    result[source_id] = caption
                else:
                    # 缓存失败结果，避免每次新消息都重试无效图片
                    await self._image_store.set_caption(
                        platform_type, source_id, _FAILED_CAPTION_SENTINEL
                    )

        return result

    async def collect_captions_from_events(
        self, events: list[dict[str, Any]], platform_type: str
    ) -> dict[str, str]:
        """从事件列表中收集已有缓存（不调模型，auto 模式注入时用）。"""
        result: dict[str, str] = {}
        for ev in events:
            if ev.get("type") != "message":
                continue
            for comp in ev.get("raw_chain") or []:
                if comp.get("type") != "image":
                    continue
                data = comp.get("data") or {}
                source_id = data.get("url") or data.get("file") or ""
                if not source_id or source_id in result:
                    continue
                caption = await self._image_store.get_caption(platform_type, source_id)
                if caption is not None and caption != _FAILED_CAPTION_SENTINEL:
                    result[source_id] = caption
        return result

    # ── 内部方法 ──

    def _resolve_provider(self, umo: str | None) -> Provider | None:
        cfg = self._context.get_config(umo)
        prov_id = cfg.get("provider_settings", {}).get(
            "default_image_caption_provider_id", ""
        )
        if not prov_id:
            if not self._warned_missing_provider:
                self._warned_missing_provider = True
                logger.warning(
                    "[ChatContextPlus] 未配置图片转述模型 (default_image_caption_provider_id 为空)，"
                    "图片转述不可用"
                )
            return None
        prov = self._context.get_provider_by_id(prov_id)
        if prov is None:
            if not self._warned_missing_provider:
                self._warned_missing_provider = True
                logger.warning(
                    f"[ChatContextPlus] 图片转述模型 {prov_id} 不可用，"
                    f"请检查 Provider 配置是否正确"
                )
            return None
        if not isinstance(prov, Provider):
            if not self._warned_missing_provider:
                self._warned_missing_provider = True
                logger.warning(
                    f"[ChatContextPlus] 已配置的图片转述模型 {prov_id} 不是有效的 LLM Provider"
                )
            return None
        return prov

    def _resolve_prompt(self, umo: str | None) -> str:
        cfg = self._context.get_config(umo)
        prompt = cfg.get("provider_settings", {}).get("image_caption_prompt", "")
        return prompt or _DEFAULT_PROMPT

    async def _call_provider(
        self,
        provider: Provider,
        platform_type: str,
        source_id: str,
        prompt: str,
    ) -> str | None:
        """调用图片转述模型。"""
        # 优先用已存储的本地路径，fallback 到原始 source_id
        local = self._image_store.get_image_path(platform_type, source_id)
        image_url = f"file:///{local}" if local else source_id

        try:
            resp = await provider.text_chat(
                prompt=prompt,
                image_urls=[image_url],
            )
            return resp.completion_text.strip() if resp.completion_text else None
        except Exception:
            logger.warning(
                f"[ChatContextPlus] 图片转述失败 source={source_id[:80]}",
                exc_info=True,
            )
            return None
