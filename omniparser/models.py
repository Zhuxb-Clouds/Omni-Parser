"""数据模型定义"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any


class ContentType(str, Enum):
    """内容块类型"""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    TABLE = "table"
    LIST = "list"
    IMAGE = "image"
    CODE = "code"
    UNKNOWN = "unknown"


@dataclass
class Document:
    """单个解析结果块"""

    source: str  # 来源文件路径
    content: str  # Markdown 内容
    content_type: ContentType = ContentType.UNKNOWN
    page: int | None = None  # 页码 / 幻灯片编号
    sheet: str | None = None  # Excel Sheet 名
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["content_type"] = self.content_type.value
        # 移除 None 值
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


@dataclass
class Chunk:
    """分块后的文本片段，用于 RAG"""

    content: str
    source: str
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


@dataclass
class ParseResult:
    """整个文件的解析结果"""

    source: str
    documents: list[Document] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    file_hash: str = ""
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "file_hash": self.file_hash,
            "success": self.success,
            "error": self.error,
            "documents": [d.to_dict() for d in self.documents],
            "chunks": [c.to_dict() for c in self.chunks],
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)
