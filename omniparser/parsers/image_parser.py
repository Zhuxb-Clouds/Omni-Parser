"""图片文件解析器 - 多模态 AI 描述"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from ..models import Document, ContentType
from ..config import Config
from .base import BaseParser

logger = logging.getLogger("omniparser.parsers.image")

# 支持的图片格式
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}


class ImageParser(BaseParser):
    """使用多模态 AI 描述图片内容

    支持后端:
      - gemini: Google Gemini API
      - openai: OpenAI GPT-4o API
    """

    supported_extensions = IMAGE_EXTENSIONS

    def parse(self, file_path: Path) -> list[Document]:
        source = str(file_path)
        provider = self.config.image.provider

        # 跳过小图片（可能是图标）
        from .image_describer import MIN_IMAGE_SIZE_KB

        file_size_kb = file_path.stat().st_size / 1024
        if file_size_kb < MIN_IMAGE_SIZE_KB:
            logger.info(
                "跳过小图片 %s (%.1fKB < %dKB)",
                file_path.name,
                file_size_kb,
                MIN_IMAGE_SIZE_KB,
            )
            return []

        if not self.config.image.api_key:
            logger.warning(
                "No API key configured for image parsing. "
                "Set GEMINI_API_KEY or OPENAI_API_KEY environment variable."
            )
            return [
                Document(
                    source=source,
                    content=f"![{file_path.name}]({file_path.name})\n\n> (图片未解析：未配置 API Key)",
                    content_type=ContentType.IMAGE,
                    metadata={"parsed": False},
                )
            ]

        if provider == "gemini":
            content = self._parse_with_gemini(file_path)
        elif provider == "openai":
            content = self._parse_with_openai(file_path)
        else:
            raise ValueError(f"Unknown image provider: {provider}")

        return [
            Document(
                source=source,
                content=content,
                content_type=ContentType.IMAGE,
                metadata={"provider": provider, "parsed": True},
            )
        ]

    def _read_image_base64(self, file_path: Path) -> str:
        """读取图片并编码为 base64"""
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")

    def _get_mime_type(self, file_path: Path) -> str:
        """获取图片的 MIME 类型"""
        ext = file_path.suffix.lower()
        mime_map = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".bmp": "image/bmp",
            ".webp": "image/webp",
            ".tiff": "image/tiff",
            ".tif": "image/tiff",
        }
        return mime_map.get(ext, "image/png")

    def _parse_with_gemini(self, file_path: Path) -> str:
        """使用 Google Gemini API 解析图片"""
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            raise ImportError(
                "google-genai not installed. " "Install with: pip install google-genai"
            )

        client = genai.Client(api_key=self.config.image.api_key)

        # 读取图片
        image_data = self._read_image_base64(file_path)
        mime_type = self._get_mime_type(file_path)
        file_size_kb = file_path.stat().st_size / 1024

        logger.info(
            "→ 发送图片到 Gemini: %s (%s, %.1fKB, model=%s)",
            file_path.name,
            mime_type,
            file_size_kb,
            self.config.image.model,
        )

        response = client.models.generate_content(
            model=self.config.image.model,
            contents=[
                self.config.image.prompt,
                types.Part.from_bytes(
                    data=base64.b64decode(image_data), mime_type=mime_type
                ),
            ],
        )

        content = response.text.strip()
        preview = content[:80].replace("\n", " ")
        logger.info(
            "← Gemini 返回: %d字 | %s...",
            len(content),
            preview,
        )
        # 包装为图片块
        return f"![{file_path.name}]({file_path.name})\n\n{content}"

    def _parse_with_openai(self, file_path: Path) -> str:
        """使用 OpenAI GPT-4o API 解析图片"""
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai not installed. " "Install with: pip install openai"
            )

        client = OpenAI(api_key=self.config.image.api_key)
        image_data = self._read_image_base64(file_path)
        mime_type = self._get_mime_type(file_path)

        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": self.config.image.prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{image_data}",
                            },
                        },
                    ],
                }
            ],
            max_tokens=2000,
        )

        content = response.choices[0].message.content.strip()
        return f"![{file_path.name}]({file_path.name})\n\n{content}"
