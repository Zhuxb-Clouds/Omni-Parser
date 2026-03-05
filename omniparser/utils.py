"""工具函数"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger("omniparser")


def compute_file_hash(path: Path, algorithm: str = "sha256") -> str:
    """计算文件的 hash 值"""
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return f"{algorithm}:{h.hexdigest()}"


def setup_logging(level: str = "INFO") -> None:
    """配置日志"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def ensure_dir(path: Path) -> Path:
    """确保目录存在"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def collect_files(
    path: Path,
    recursive: bool = False,
    extensions: set[str] | None = None,
) -> list[Path]:
    """收集路径下的文件

    Args:
        path: 文件或目录路径
        recursive: 是否递归子目录
        extensions: 限定的文件后缀集合（如 {'.pdf', '.docx'}），None 表示不限

    Returns:
        文件路径列表
    """
    if path.is_file():
        if extensions is None or path.suffix.lower() in extensions:
            return [path]
        return []

    if not path.is_dir():
        return []

    pattern = "**/*" if recursive else "*"
    files = []
    for p in path.glob(pattern):
        if p.is_file():
            if extensions is None or p.suffix.lower() in extensions:
                files.append(p)

    return sorted(files)
