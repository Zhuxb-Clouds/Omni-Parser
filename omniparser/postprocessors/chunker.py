"""分块策略 - 将 Document 列表切分为适合 RAG 检索的 Chunk"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from ..config import ChunkingConfig
from ..models import Document, Chunk

logger = logging.getLogger("omniparser.postprocessors.chunker")


class Chunker:
    """文本分块器

    支持两种策略:
      - heading: 按 Markdown 标题层级切分（推荐）
      - fixed_token: 按固定 token 数切分，带重叠窗口
    """

    def __init__(self, config: ChunkingConfig):
        self.config = config

    def chunk_documents(self, documents: Sequence[Document]) -> list[Chunk]:
        """将 Document 列表分块"""
        if self.config.strategy == "heading":
            return self._chunk_by_heading(documents)
        elif self.config.strategy == "fixed_token":
            return self._chunk_by_tokens(documents)
        else:
            logger.warning(
                "Unknown chunking strategy: %s, falling back to heading",
                self.config.strategy,
            )
            return self._chunk_by_heading(documents)

    def _chunk_by_heading(self, documents: Sequence[Document]) -> list[Chunk]:
        """按标题层级分块

        将连续的内容聚合到同一个 chunk 中，
        遇到新的标题时创建新 chunk。
        """
        chunks: list[Chunk] = []
        current_parts: list[str] = []
        current_source = ""
        chunk_idx = 0

        heading_pattern = re.compile(r"^#{1,6}\s+")

        for doc in documents:
            lines = doc.content.split("\n")

            for line in lines:
                is_heading = heading_pattern.match(line)

                if is_heading and current_parts:
                    # 遇到新标题，保存当前块
                    content = "\n".join(current_parts).strip()
                    if content:
                        chunks.append(
                            Chunk(
                                content=content,
                                source=current_source or doc.source,
                                chunk_index=chunk_idx,
                                metadata=self._build_chunk_metadata(doc),
                            )
                        )
                        chunk_idx += 1
                    current_parts = []

                current_parts.append(line)
                current_source = doc.source

        # 处理最后一个块
        if current_parts:
            content = "\n".join(current_parts).strip()
            if content:
                chunks.append(
                    Chunk(
                        content=content,
                        source=current_source,
                        chunk_index=chunk_idx,
                        metadata=self._build_chunk_metadata(
                            documents[-1] if documents else None
                        ),
                    )
                )

        # 如果某些 chunk 超过 max_tokens，进一步拆分
        if self.config.max_tokens > 0:
            chunks = self._split_oversized_chunks(chunks)

        return chunks

    def _chunk_by_tokens(self, documents: Sequence[Document]) -> list[Chunk]:
        """按固定 token 数分块（简化实现：按字符数估算）

        1 token ≈ 1.5 个中文字符 或 4 个英文字符
        这里用字符数的简单估算。
        """
        max_chars = self.config.max_tokens * 2  # 粗略估算
        overlap_chars = self.config.overlap * 2

        # 合并所有文档为一个大文本
        all_text = "\n\n".join(doc.content for doc in documents)
        source = documents[0].source if documents else ""

        chunks = []
        start = 0
        chunk_idx = 0

        while start < len(all_text):
            end = start + max_chars

            # 尝试在句子边界切分
            if end < len(all_text):
                # 向后找到最近的句子终止符
                for sep in ["\n\n", "\n", "。", ".", "！", "!", "？", "?"]:
                    pos = all_text.rfind(sep, start, end)
                    if pos > start:
                        end = pos + len(sep)
                        break

            chunk_text = all_text[start:end].strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        content=chunk_text,
                        source=source,
                        chunk_index=chunk_idx,
                    )
                )
                chunk_idx += 1

            # 下一个窗口，带重叠
            start = end - overlap_chars if end < len(all_text) else end

        return chunks

    def _split_oversized_chunks(self, chunks: list[Chunk]) -> list[Chunk]:
        """拆分超过 max_tokens 的 chunk"""
        max_chars = self.config.max_tokens * 2
        result = []
        new_idx = 0

        for chunk in chunks:
            if len(chunk.content) <= max_chars:
                chunk.chunk_index = new_idx
                result.append(chunk)
                new_idx += 1
            else:
                # 按段落拆分
                paragraphs = chunk.content.split("\n\n")
                current_parts = []
                current_len = 0

                for para in paragraphs:
                    if current_len + len(para) > max_chars and current_parts:
                        result.append(
                            Chunk(
                                content="\n\n".join(current_parts),
                                source=chunk.source,
                                chunk_index=new_idx,
                                metadata=chunk.metadata.copy(),
                            )
                        )
                        new_idx += 1
                        current_parts = []
                        current_len = 0

                    current_parts.append(para)
                    current_len += len(para)

                if current_parts:
                    result.append(
                        Chunk(
                            content="\n\n".join(current_parts),
                            source=chunk.source,
                            chunk_index=new_idx,
                            metadata=chunk.metadata.copy(),
                        )
                    )
                    new_idx += 1

        return result

    def _build_chunk_metadata(self, doc: Document | None) -> dict:
        """从 Document 继承关键元数据到 Chunk"""
        if doc is None:
            return {}
        meta = {}
        if doc.page is not None:
            meta["page"] = doc.page
        if doc.sheet is not None:
            meta["sheet"] = doc.sheet
        meta["content_type"] = doc.content_type.value
        return meta
