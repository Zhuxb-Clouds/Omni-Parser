"""文件 hash 缓存，避免重复解析"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .config import CacheConfig
from .models import ParseResult
from .utils import compute_file_hash, ensure_dir

logger = logging.getLogger("omniparser.cache")


class FileCache:
    """基于文件 hash 的解析结果缓存"""

    def __init__(self, config: CacheConfig):
        self.enabled = config.enabled
        self.cache_dir = Path(config.dir)
        if self.enabled:
            ensure_dir(self.cache_dir)

    def _cache_path(self, file_hash: str) -> Path:
        """缓存文件路径：cache_dir/<hash>.json"""
        # hash 格式: "sha256:abc123..."  取冒号后面部分做文件名
        hash_value = file_hash.split(":", 1)[-1]
        return self.cache_dir / f"{hash_value}.json"

    def get(self, file_path: Path) -> ParseResult | None:
        """尝试从缓存获取解析结果，返回 None 表示未命中"""
        if not self.enabled:
            return None

        file_hash = compute_file_hash(file_path)
        cache_path = self._cache_path(file_hash)

        if not cache_path.exists():
            logger.debug("Cache miss: %s", file_path)
            return None

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Cache hit: %s", file_path)
            return self._deserialize(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Cache corrupted for %s: %s", file_path, e)
            cache_path.unlink(missing_ok=True)
            return None

    def put(self, result: ParseResult) -> None:
        """将解析结果写入缓存"""
        if not self.enabled or not result.success:
            return

        cache_path = self._cache_path(result.file_hash)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            logger.debug("Cached: %s", result.source)
        except OSError as e:
            logger.warning("Failed to write cache for %s: %s", result.source, e)

    def _deserialize(self, data: dict) -> ParseResult:
        """从 dict 反序列化为 ParseResult"""
        from .models import Document, Chunk, ContentType

        documents = []
        for d in data.get("documents", []):
            documents.append(
                Document(
                    source=d["source"],
                    content=d["content"],
                    content_type=ContentType(d.get("content_type", "unknown")),
                    page=d.get("page"),
                    sheet=d.get("sheet"),
                    metadata=d.get("metadata", {}),
                )
            )

        chunks = []
        for c in data.get("chunks", []):
            chunks.append(
                Chunk(
                    content=c["content"],
                    source=c["source"],
                    chunk_index=c["chunk_index"],
                    metadata=c.get("metadata", {}),
                )
            )

        return ParseResult(
            source=data["source"],
            documents=documents,
            chunks=chunks,
            file_hash=data.get("file_hash", ""),
            error=data.get("error"),
        )

    def clear(self) -> int:
        """清空缓存，返回删除的文件数"""
        if not self.cache_dir.exists():
            return 0
        count = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        logger.info("Cleared %d cached files", count)
        return count
