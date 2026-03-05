"""PDF 文件解析器 - 支持三层降级"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz  # PyMuPDF

from ..models import Document, ContentType
from ..config import Config
from .base import BaseParser

logger = logging.getLogger("omniparser.parsers.pdf")


class PdfParser(BaseParser):
    """解析 PDF 文件

    三层降级策略:
      Layer 1: PyMuPDF 文本提取（零成本）
      Layer 2: OCR 降级（当文本提取率低于阈值时）
      Layer 3: 多模态 AI（由 ImageParser 处理，此处不涉及）
    """

    supported_extensions = {".pdf"}

    def parse(self, file_path: Path) -> list[Document]:
        """解析 PDF，内嵌图片按字图比例决定是否解析"""
        from .image_describer import (
            should_parse_images,
            describe_images_batch,
            ImageItem,
        )

        source = str(file_path)
        doc = fitz.open(str(file_path))
        documents: list[Document] = []

        total_pages = len(doc)
        text_pages = 0

        # --- 第一遍：统计文本量和图片数 ---
        total_text_chars = 0
        total_image_count = 0
        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text("text").strip()
            total_text_chars += len(text)
            total_image_count += len(page.get_images(full=False))

        parse_images = should_parse_images(total_text_chars, total_image_count)

        # --- 第二遍：提取文本 + 收集图片 ---
        # image_slots[i] = 在 documents 中的插入位置索引
        image_items: list[ImageItem] = []
        image_slots: list[int] = []  # documents 中的占位索引

        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text("text").strip()

            if text:
                text_pages += 1

            page_docs = self._parse_page_text(text, source, page_num + 1)
            documents.extend(page_docs)

            # 收集页面内嵌图片（仅在图多字少时）
            if parse_images:
                for img_info in page.get_images(full=True):
                    xref = img_info[0]
                    try:
                        base_image = doc.extract_image(xref)
                        if base_image:
                            img_bytes = base_image["image"]
                            img_ext = base_image.get("ext", "png")
                            mime_map = {
                                "png": "image/png",
                                "jpeg": "image/jpeg",
                                "jpg": "image/jpeg",
                                "bmp": "image/bmp",
                            }
                            mime_type = mime_map.get(img_ext, f"image/{img_ext}")
                            image_items.append(
                                ImageItem(
                                    image_bytes=img_bytes,
                                    mime_type=mime_type,
                                    context=f"PDF第{page_num + 1}页",
                                )
                            )
                            # 插入占位 Document
                            documents.append(None)  # type: ignore[arg-type]
                            image_slots.append(len(documents) - 1)
                    except Exception as e:
                        logger.warning(
                            "Failed to extract PDF image (page %d, xref %d): %s",
                            page_num + 1,
                            xref,
                            e,
                        )

        doc.close()

        # --- 合批描述图片 ---
        if image_items:
            describe_images_batch(image_items, self.config)
            for slot_idx, item in zip(image_slots, image_items):
                if item.description:
                    page_num_from_ctx = (
                        int(item.context.replace("PDF第", "").replace("页", ""))
                        if "PDF第" in item.context
                        else 0
                    )
                    documents[slot_idx] = Document(
                        source=source,
                        content=item.description,
                        content_type=ContentType.IMAGE,
                        page=page_num_from_ctx,
                        metadata={"embedded_image": True},
                    )
            # 移除未获得描述的占位 None
            documents = [d for d in documents if d is not None]

        # 判断是否需要 OCR 降级
        text_ratio = text_pages / total_pages if total_pages > 0 else 0
        threshold = self.config.pdf.ocr_threshold

        if text_ratio < threshold and total_pages > 0:
            logger.warning(
                "Low text ratio (%.1f%%) for %s, attempting OCR fallback",
                text_ratio * 100,
                file_path.name,
            )
            ocr_docs = self._ocr_fallback(file_path)
            if ocr_docs:
                return ocr_docs
            logger.warning("OCR fallback failed, returning partial text results")

        return documents

    def _parse_page_text(self, text: str, source: str, page_num: int) -> list[Document]:
        """解析单页文本，尝试识别结构"""
        if not text:
            return []

        documents = []
        lines = text.split("\n")
        current_block: list[str] = []
        current_type = ContentType.PARAGRAPH

        for line in lines:
            stripped = line.strip()
            if not stripped:
                # 空行 = 段落分隔
                if current_block:
                    documents.append(
                        Document(
                            source=source,
                            content="\n".join(current_block),
                            content_type=current_type,
                            page=page_num,
                        )
                    )
                    current_block = []
                    current_type = ContentType.PARAGRAPH
                continue

            # 简单的标题启发式：短行 + 全大写或加粗样式
            if len(stripped) < 80 and stripped.isupper():
                if current_block:
                    documents.append(
                        Document(
                            source=source,
                            content="\n".join(current_block),
                            content_type=current_type,
                            page=page_num,
                        )
                    )
                    current_block = []
                current_block.append(f"## {stripped}")
                current_type = ContentType.HEADING
            else:
                if current_type == ContentType.HEADING:
                    if current_block:
                        documents.append(
                            Document(
                                source=source,
                                content="\n".join(current_block),
                                content_type=current_type,
                                page=page_num,
                            )
                        )
                        current_block = []
                    current_type = ContentType.PARAGRAPH
                current_block.append(stripped)

        # 处理最后一个块
        if current_block:
            documents.append(
                Document(
                    source=source,
                    content="\n".join(current_block),
                    content_type=current_type,
                    page=page_num,
                )
            )

        return documents

    def _ocr_fallback(self, file_path: Path) -> list[Document]:
        """OCR 降级处理扫描型 PDF"""
        backend = self.config.pdf.ocr_backend
        source = str(file_path)

        if backend == "pytesseract":
            return self._ocr_pytesseract(file_path, source)
        elif backend == "surya":
            return self._ocr_surya(file_path, source)
        else:
            logger.error("Unknown OCR backend: %s", backend)
            return []

    def _ocr_pytesseract(self, file_path: Path, source: str) -> list[Document]:
        """使用 pytesseract OCR"""
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            logger.warning(
                "pytesseract not installed. "
                "Install with: pip install pytesseract Pillow"
            )
            return []

        documents = []
        doc = fitz.open(str(file_path))

        for page_num in range(len(doc)):
            page = doc[page_num]
            # 渲染页面为图片
            pix = page.get_pixmap(dpi=300)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            # OCR
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
            if text.strip():
                documents.append(
                    Document(
                        source=source,
                        content=text.strip(),
                        content_type=ContentType.PARAGRAPH,
                        page=page_num + 1,
                        metadata={"ocr": True, "ocr_backend": "pytesseract"},
                    )
                )

        doc.close()
        return documents

    def _ocr_surya(self, file_path: Path, source: str) -> list[Document]:
        """使用 Surya OCR（需要 GPU）"""
        try:
            from surya.ocr import run_ocr
            from surya.model.detection.model import load_model as load_det_model
            from surya.model.detection.processor import (
                load_processor as load_det_processor,
            )
            from surya.model.recognition.model import load_model as load_rec_model
            from surya.model.recognition.processor import (
                load_processor as load_rec_processor,
            )
        except ImportError:
            logger.warning(
                "surya-ocr not installed. " "Install with: pip install surya-ocr"
            )
            return []

        documents = []
        doc = fitz.open(str(file_path))

        # 加载模型
        det_model = load_det_model()
        det_processor = load_det_processor()
        rec_model = load_rec_model()
        rec_processor = load_rec_processor()

        images = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            pix = page.get_pixmap(dpi=300)
            from PIL import Image

            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            images.append(img)

        doc.close()

        # 批量 OCR
        langs = [["zh", "en"]] * len(images)
        results = run_ocr(
            images,
            langs,
            det_model,
            det_processor,
            rec_model,
            rec_processor,
        )

        for page_num, result in enumerate(results, start=1):
            text_lines = [line.text for line in result.text_lines]
            text = "\n".join(text_lines)
            if text.strip():
                documents.append(
                    Document(
                        source=source,
                        content=text.strip(),
                        content_type=ContentType.PARAGRAPH,
                        page=page_num,
                        metadata={"ocr": True, "ocr_backend": "surya"},
                    )
                )

        return documents
