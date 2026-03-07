"""管道调度器 - 核心编排逻辑"""

from __future__ import annotations

import logging
from pathlib import Path

from .cache import FileCache
from .config import Config
from .models import Document, ParseResult
from .parsers.base import BaseParser
from .parsers.docx_parser import DocxParser
from .parsers.xlsx_parser import XlsxParser
from .parsers.pptx_parser import PptxParser
from .parsers.pdf_parser import PdfParser
from .parsers.image_parser import ImageParser
from .parsers.xlsx_csv_converter import XlsxCsvConverter
from .models import CsvConvertResult
from .postprocessors.chunker import Chunker
from .postprocessors.metadata import MetadataExtractor
from .utils import compute_file_hash, collect_files

logger = logging.getLogger("omniparser.pipeline")


class Pipeline:
    """文档解析管道

    路由器模式：按文件后缀分发到对应的解析器插件，
    再经过后处理（元数据提取、分块）输出结构化结果。
    """

    def __init__(self, config: Config | None = None):
        self.config = config or Config.default()
        self.cache = FileCache(self.config.cache)
        self.chunker = Chunker(self.config.chunking)
        self.metadata_extractor = MetadataExtractor()

        # Excel → CSV 转换器
        self._csv_converter = XlsxCsvConverter(self.config)

        # 注册解析器 —— 插件式架构，新增格式只需在此注册
        self._parsers: list[BaseParser] = [
            DocxParser(self.config),
            XlsxParser(self.config),
            PptxParser(self.config),
            PdfParser(self.config),
            ImageParser(self.config),
        ]

        # 构建后缀 → 解析器的映射表
        self._extension_map: dict[str, BaseParser] = {}
        for parser in self._parsers:
            for ext in parser.supported_extensions:
                self._extension_map[ext] = parser

    @property
    def supported_extensions(self) -> set[str]:
        """返回所有支持的文件后缀"""
        return set(self._extension_map.keys())

    def register_parser(self, parser: BaseParser) -> None:
        """动态注册额外的解析器"""
        self._parsers.append(parser)
        for ext in parser.supported_extensions:
            self._extension_map[ext] = parser
        logger.info("Registered parser for: %s", parser.supported_extensions)

    def _get_parser(self, file_path: Path) -> BaseParser | None:
        """根据文件后缀路由到对应解析器"""
        return self._extension_map.get(file_path.suffix.lower())

    def parse_file(self, file_path: Path) -> ParseResult:
        """解析单个文件

        流程: 缓存检查 → 路由 → 解析 → 元数据提取 → 分块 → 缓存写入
        """
        file_path = Path(file_path).resolve()
        source = str(file_path)

        # 1. 缓存检查
        cached = self.cache.get(file_path)
        if cached is not None:
            return cached

        # 2. 路由到解析器
        parser = self._get_parser(file_path)
        if parser is None:
            return ParseResult(
                source=source,
                error=f"Unsupported file format: {file_path.suffix}",
            )

        # 3. 计算文件 hash
        file_hash = compute_file_hash(file_path)

        # 4. 解析
        try:
            logger.info("Parsing: %s (via %s)", file_path.name, type(parser).__name__)
            documents = parser.parse(file_path)
        except Exception as e:
            logger.error("Failed to parse %s: %s", file_path, e)
            return ParseResult(source=source, file_hash=file_hash, error=str(e))

        # 5. 元数据提取
        for doc in documents:
            doc.metadata["file_hash"] = file_hash
            self.metadata_extractor.enrich(doc, file_path)

        # 6. 分块
        chunks = self.chunker.chunk_documents(documents)

        # 7. 组装结果
        result = ParseResult(
            source=source,
            documents=documents,
            chunks=chunks,
            file_hash=file_hash,
        )

        # 8. 写入缓存
        self.cache.put(result)

        logger.info(
            "Parsed %s: %d documents, %d chunks",
            file_path.name,
            len(documents),
            len(chunks),
        )
        return result

    def parse_directory(
        self,
        dir_path: Path,
        recursive: bool = False,
    ) -> list[ParseResult]:
        """批量解析目录下的文件"""
        dir_path = Path(dir_path).resolve()
        files = collect_files(
            dir_path,
            recursive=recursive,
            extensions=self.supported_extensions,
        )

        logger.info("Found %d files to parse in %s", len(files), dir_path)

        results = []
        for file_path in files:
            result = self.parse_file(file_path)
            results.append(result)

        # 统计
        success = sum(1 for r in results if r.success)
        failed = len(results) - success
        logger.info("Batch complete: %d success, %d failed", success, failed)

        return results

    def convert_excel_to_csv(
        self,
        file_path: Path,
        output_dir: Path | None = None,
    ) -> list[CsvConvertResult]:
        """将 Excel 文件的每个 Sheet 转换为独立的 CSV 文件

        Args:
            file_path: Excel 文件路径 (.xlsx / .xls)
            output_dir: CSV 输出目录，默认为源文件所在目录

        Returns:
            每个 Sheet 的转换结果列表
        """
        file_path = Path(file_path).resolve()
        return self._csv_converter.convert(file_path, output_dir)
