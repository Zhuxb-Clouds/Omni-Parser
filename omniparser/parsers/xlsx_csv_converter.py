"""Excel 转 CSV 转换器

将 .xlsx / .xls 文件的每个 Sheet 导出为独立的 CSV 文件，
同时返回 CSV 文本内容，供 AI / MCP 工具直接消费。
"""

from __future__ import annotations

import logging
from io import StringIO
from pathlib import Path

import pandas as pd

from ..config import Config
from ..models import CsvConvertResult

logger = logging.getLogger("omniparser.parsers.xlsx_csv")

# 支持转换的文件后缀
SUPPORTED_EXTENSIONS = {".xlsx", ".xls"}


class XlsxCsvConverter:
    """将 Excel 文件按 Sheet 转换为 CSV 文件

    每个非空 Sheet 生成一个独立的 CSV 文件，命名规则:
    - 单 sheet: {stem}.csv
    - 多 sheet: {stem}_{sheet_name}.csv
    """

    def __init__(self, config: Config | None = None):
        self.config = config

    def convert(
        self,
        file_path: Path,
        output_dir: Path | None = None,
    ) -> list[CsvConvertResult]:
        """将 Excel 文件转换为 CSV

        Args:
            file_path: Excel 文件路径 (.xlsx / .xls)
            output_dir: CSV 输出目录，默认为源文件所在目录

        Returns:
            每个 Sheet 的转换结果列表

        Raises:
            ValueError: 文件后缀不支持
            FileNotFoundError: 文件不存在
        """
        file_path = Path(file_path).resolve()

        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"不支持的文件格式: {file_path.suffix}，"
                f"仅支持 {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            )

        if output_dir is None:
            output_dir = file_path.parent
        else:
            output_dir = Path(output_dir).resolve()
            output_dir.mkdir(parents=True, exist_ok=True)

        source = str(file_path)
        stem = file_path.stem
        results: list[CsvConvertResult] = []

        excel = pd.ExcelFile(file_path)
        sheet_names = excel.sheet_names
        multi_sheet = len(sheet_names) > 1

        for sheet_name in sheet_names:
            df = excel.parse(sheet_name)

            if df.empty:
                logger.debug("Skipping empty sheet: %s", sheet_name)
                continue

            # 数据清洗
            df = df.fillna("")
            df = df.map(
                lambda x: str(x).strip() if not isinstance(x, str) else x.strip()
            )
            # 去掉全空行
            df = df[df.apply(lambda row: any(cell != "" for cell in row), axis=1)]

            if df.empty:
                continue

            # 生成 CSV 文件名
            if multi_sheet:
                # 清理 sheet 名中不适合做文件名的字符
                safe_name = self._sanitize_filename(sheet_name)
                csv_filename = f"{stem}_{safe_name}.csv"
            else:
                csv_filename = f"{stem}.csv"

            csv_path = output_dir / csv_filename

            # 生成 CSV 内容（UTF-8 with BOM，Excel 打开不乱码）
            csv_content = df.to_csv(index=False)

            # 写入文件
            csv_path.write_text(csv_content, encoding="utf-8-sig")
            logger.info("Written CSV: %s (%d rows)", csv_path, len(df))

            results.append(
                CsvConvertResult(
                    source=source,
                    sheet_name=sheet_name,
                    csv_path=str(csv_path),
                    csv_content=csv_content,
                    rows=len(df),
                    columns=len(df.columns),
                )
            )

        return results

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """清理字符串使其可安全用作文件名"""
        # 替换文件系统不允许的字符
        for ch in r'<>:"/\|?*':
            name = name.replace(ch, "_")
        return name.strip().strip(".")
