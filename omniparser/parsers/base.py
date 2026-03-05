"""解析器抽象基类"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import Config
from ..models import Document

logger = logging.getLogger("omniparser.parsers")


class BaseParser(ABC):
    """所有解析器的抽象基类

    子类只需实现:
      - supported_extensions: 支持的文件后缀集合
      - parse(): 解析文件并返回 Document 列表
    """

    # 子类需覆盖此属性
    supported_extensions: set[str] = set()

    def __init__(self, config: Config):
        self.config = config

    @abstractmethod
    def parse(self, file_path: Path) -> list[Document]:
        """解析文件，返回 Document 列表

        Args:
            file_path: 文件路径

        Returns:
            解析出的 Document 列表

        Raises:
            解析失败时应抛出异常，由 Pipeline 捕获
        """
        ...

    def can_handle(self, file_path: Path) -> bool:
        """判断是否能处理该文件"""
        return file_path.suffix.lower() in self.supported_extensions
