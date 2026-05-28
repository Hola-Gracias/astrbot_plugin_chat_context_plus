"""图片持久化存储。

将群聊消息中的图片保存到磁盘，按平台独立管理。
支持 URL 级去重、过期清理、数量裁剪，以及 URL → caption 缓存。

目录结构::

    {plugin_data_dir}/
    └── aiocqhttp/
        └── image/
            ├── image_caption.json
            ├── a1b2c3d4.jpg
            └── e5f6g7h8.png
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import time
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger

_CLEANUP_PROBABILITY = 0.05
_FAILED_CAPTION_SENTINEL = "__CAPTION_FAILED__"


class ImageStore:
    """管理图片持久化存储和 caption 缓存。"""

    def __init__(
        self,
        base_dir: Path,
        retention_days: int = 7,
        max_per_platform: int = 100,
    ) -> None:
        self._base_dir = base_dir
        self.retention_days = retention_days
        self.max_per_platform = max_per_platform
        self._locks: dict[str, asyncio.Lock] = {}
        # 内存中的 caption 缓存: platform_type -> {source_id: caption}
        self._caption_cache: dict[str, dict[str, str]] = {}

    # ── 路径工具 ──

    def _image_dir(self, platform_type: str) -> Path:
        return self._base_dir / platform_type / "image"

    def _caption_path(self, platform_type: str) -> Path:
        return self._image_dir(platform_type) / "image_caption.json"

    @staticmethod
    def _hash(source_id: str) -> str:
        return hashlib.sha256(source_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _ext(source_id: str) -> str:
        """从 URL/路径猜测文件扩展名，fallback .jpg。"""
        # 去掉 query string
        path_part = source_id.split("?")[0]
        _, ext = os.path.splitext(path_part)
        ext = ext.lower()
        if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
            return ext
        if ext == ".jpeg":
            return ".jpg"
        return ".jpg"

    def get_image_path(self, platform_type: str, source_id: str) -> Path | None:
        """返回已存储图片的本地路径，不存在则返回 None。"""
        file_hash = self._hash(source_id)
        ext = self._ext(source_id)
        target = self._image_dir(platform_type) / f"{file_hash}{ext}"
        return target if target.exists() else None

    # ── 锁 ──

    def _get_lock(self, platform_type: str) -> asyncio.Lock:
        if platform_type not in self._locks:
            self._locks[platform_type] = asyncio.Lock()
        return self._locks[platform_type]

    # ── 图片保存 ──

    async def save_image(
        self,
        platform_type: str,
        image_comp: Comp.Image,
        source_id: str,
    ) -> tuple[str, str] | None:
        """保存图片到持久化目录。

        Args:
            platform_type: 平台类型（如 ``aiocqhttp``）。
            image_comp: Image 组件实例。
            source_id: 图片来源标识（URL 或 file:// 路径），用于去重和缓存 key。

        Returns:
            ``(hash, file_path)`` 或 None（失败时）。
        """
        if not platform_type or not source_id:
            return None

        lock = self._get_lock(platform_type)
        async with lock:
            try:
                image_dir = self._image_dir(platform_type)
                image_dir.mkdir(parents=True, exist_ok=True)

                file_hash = self._hash(source_id)
                ext = self._ext(source_id)
                target = image_dir / f"{file_hash}{ext}"

                # URL 级去重
                if target.exists():
                    return file_hash, str(target)

                # 下载/解析图片到本地临时路径
                tmp_path = await image_comp.convert_to_file_path()
                if not tmp_path or not os.path.exists(tmp_path):
                    return None

                shutil.copy2(tmp_path, target)
                logger.debug(
                    f"[ChatContextPlus] 图片已保存: {platform_type}/{file_hash}{ext}"
                )
            except Exception:
                logger.warning(
                    f"[ChatContextPlus] 保存图片失败 source={source_id[:80]}",
                    exc_info=True,
                )
                return None

        self._maybe_cleanup(platform_type)
        return file_hash, str(target)

    # ── caption 缓存 ──

    async def get_caption(self, platform_type: str, source_id: str) -> str | None:
        """获取图片的缓存 caption。"""
        if platform_type not in self._caption_cache:
            lock = self._get_lock(platform_type)
            async with lock:
                if platform_type not in self._caption_cache:
                    self._caption_cache[platform_type] = self._read_captions(
                        platform_type
                    )
        return self._caption_cache[platform_type].get(source_id)

    async def set_caption(
        self, platform_type: str, source_id: str, caption: str
    ) -> None:
        """设置图片的 caption 并持久化。"""
        lock = self._get_lock(platform_type)
        async with lock:
            if platform_type not in self._caption_cache:
                self._caption_cache[platform_type] = self._read_captions(platform_type)
            self._caption_cache[platform_type][source_id] = caption
            self._write_captions(platform_type)

    def _read_captions(self, platform_type: str) -> dict[str, str]:
        path = self._caption_path(platform_type)
        if not path.exists():
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_captions(self, platform_type: str) -> None:
        path = self._caption_path(platform_type)
        path.parent.mkdir(parents=True, exist_ok=True)
        cache = self._caption_cache.get(platform_type, {})
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning(f"[ChatContextPlus] 写入 caption 缓存失败: {e}")

    # ── 清理 ──

    async def cleanup(self, platform_type: str) -> None:
        """清理过期和超量的图片，同步清理 caption 缓存。"""
        lock = self._get_lock(platform_type)
        async with lock:
            image_dir = self._image_dir(platform_type)
            if not image_dir.exists():
                return

            now = time.time()
            retention_seconds = self.retention_days * 86400

            # 收集图片文件信息
            files: list[tuple[float, Path]] = []
            for entry in image_dir.iterdir():
                if entry.is_file() and entry.name != "image_caption.json":
                    try:
                        mtime = entry.stat().st_mtime
                    except OSError:
                        mtime = 0
                    files.append((mtime, entry))

            files.sort(key=lambda x: x[0])

            deleted_hashes: set[str] = set()

            # 1. 按 retention_days 删除过期
            for mtime, fpath in files:
                if now - mtime > retention_seconds:
                    try:
                        fpath.unlink()
                        deleted_hashes.add(fpath.stem)
                    except OSError:
                        pass

            # 重新统计
            remaining = [
                (m, fp)
                for m, fp in files
                if fp.exists() and fp.name != "image_caption.json"
            ]

            # 2. 按 max_per_platform 裁剪最旧
            if len(remaining) > self.max_per_platform:
                overflow = len(remaining) - self.max_per_platform
                for mtime, fpath in remaining[:overflow]:
                    try:
                        fpath.unlink()
                        deleted_hashes.add(fpath.stem)
                    except OSError:
                        pass

            # 3. 清理 caption 缓存中已删除的条目
            if deleted_hashes and platform_type in self._caption_cache:
                captions = self._caption_cache[platform_type]
                stale_keys = [k for k in captions if self._hash(k) in deleted_hashes]
                for k in stale_keys:
                    del captions[k]
                if stale_keys:
                    self._write_captions(platform_type)

    def _maybe_cleanup(self, platform_type: str) -> None:
        """概率触发清理。"""
        if random.random() < _CLEANUP_PROBABILITY:
            asyncio.ensure_future(self.cleanup(platform_type))
