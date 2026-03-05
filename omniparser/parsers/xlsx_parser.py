"""XLSX 文件解析器"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from ..models import Document, ContentType
from ..config import Config
from .base import BaseParser

logger = logging.getLogger("omniparser.parsers.xlsx")


class XlsxParser(BaseParser):
    """解析 .xlsx / .xls 文件

    将每个 Sheet 转为 Markdown 表格，保留 Sheet 名作为标题。
    """

    supported_extensions = {".xlsx", ".xls"}

    def parse(self, file_path: Path) -> list[Document]:
        source = str(file_path)
        documents: list[Document] = []

        # 读取所有 sheet
        excel = pd.ExcelFile(file_path)

        for sheet_name in excel.sheet_names:
            df = excel.parse(sheet_name)

            if df.empty:
                logger.debug("Skipping empty sheet: %s", sheet_name)
                continue

            # 清理数据：先填充空值，再统一转字符串
            df = df.fillna("")
            df = df.map(
                lambda x: str(x).strip() if not isinstance(x, str) else x.strip()
            )

            # 跳过全空行
            df = df[df.apply(lambda row: any(cell for cell in row), axis=1)]

            if df.empty:
                continue

            # 构建 Markdown
            md_lines = []

            # Sheet 名作为二级标题
            md_lines.append(f"## {sheet_name}")
            md_lines.append("")

            # 表头
            headers = [str(col).strip() for col in df.columns]
            md_lines.append("| " + " | ".join(headers) + " |")
            md_lines.append("| " + " | ".join(["---"] * len(headers)) + " |")

            # 数据行
            for _, row in df.iterrows():
                cells = [str(v).strip().replace("\n", " ") for v in row]
                md_lines.append("| " + " | ".join(cells) + " |")

            documents.append(
                Document(
                    source=source,
                    content="\n".join(md_lines),
                    content_type=ContentType.TABLE,
                    sheet=sheet_name,
                    metadata={"rows": len(df), "columns": len(df.columns)},
                )
            )

        return documents
