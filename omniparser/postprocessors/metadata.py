"""元数据提取器 - 为 Document 补充文件级元数据"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..models import Document

logger = logging.getLogger("omniparser.postprocessors.metadata")


class MetadataExtractor:
    """从文件系统和文件内容中提取元数据"""

    def enrich(self, doc: Document, file_path: Path) -> None:
        """为 Document 补充元数据

        添加的字段:
          - file_name: 文件名
          - file_size: 文件大小 (bytes)
          - file_ext: 文件后缀
          - modified_at: 文件修改时间 (ISO 格式)
          - created_at: 文件创建时间 (ISO 格式, 仅 Linux 部分系统支持)
        """
        if not file_path.exists():
            return

        stat = file_path.stat()

        doc.metadata["file_name"] = file_path.name
        doc.metadata["file_size"] = stat.st_size
        doc.metadata["file_ext"] = file_path.suffix.lower()
        doc.metadata["modified_at"] = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()

        # 创建时间（不是所有系统都支持）
        try:
            ctime = stat.st_birthtime  # macOS
        except AttributeError:
            ctime = stat.st_ctime  # Linux: 最后一次元数据变更
        doc.metadata["created_at"] = datetime.fromtimestamp(
            ctime, tz=timezone.utc
        ).isoformat()
