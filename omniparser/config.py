"""全局配置管理"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class CacheConfig:
    enabled: bool = True
    dir: str = ".omniparser_cache"


@dataclass
class ImageConfig:
    provider: str = "gemini"  # gemini / openai
    api_key: str = ""
    model: str = "gemini-2.0-flash"
    prompt: str = (
        "请详细描述这张图片的内容。如果是图表，请提取其中的数据；"
        "如果是照片，请描述场景和主要物体。最后请以 Markdown 格式输出。"
    )


@dataclass
class ChunkingConfig:
    strategy: str = "heading"  # heading / fixed_token
    max_tokens: int = 512
    overlap: int = 50


@dataclass
class PdfConfig:
    ocr_threshold: float = 0.3  # 文本提取率低于此值时降级到 OCR
    ocr_backend: str = "pytesseract"  # pytesseract / surya


@dataclass
class Config:
    cache: CacheConfig = field(default_factory=CacheConfig)
    image: ImageConfig = field(default_factory=ImageConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    pdf: PdfConfig = field(default_factory=PdfConfig)
    output_dir: str = "output"

    @classmethod
    def from_yaml(cls, path: str | Path) -> Config:
        """从 YAML 文件加载配置"""
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        config = cls()

        # 解析各段
        if "cache" in raw:
            config.cache = CacheConfig(**raw["cache"])
        if "image" in raw:
            img = raw["image"]
            # 支持环境变量替换 ${VAR}
            if "api_key" in img and img["api_key"].startswith("${"):
                var_name = img["api_key"].strip("${}")
                img["api_key"] = os.environ.get(var_name, "")
            config.image = ImageConfig(**img)
        if "chunking" in raw:
            config.chunking = ChunkingConfig(**raw["chunking"])
        if "pdf" in raw:
            config.pdf = PdfConfig(**raw["pdf"])
        if "output_dir" in raw:
            config.output_dir = raw["output_dir"]

        return config

    @classmethod
    def default(cls) -> Config:
        """返回默认配置，自动读取环境变量中的 API key"""
        config = cls()
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            import logging
            logging.getLogger("omniparser.config").warning(
                "GEMINI_API_KEY 环境变量未设置，图片解析功能将不可用"
            )
        config.image.api_key = api_key
        return config
