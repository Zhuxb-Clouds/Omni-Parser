"""内嵌图片描述工具 - 供各解析器共享

优化策略：
  1. 字多图少 → 跳过全部图片
  2. 图多字少 → 合批发送 + Hash去重 + 压缩大图
     - 同一文档的所有图片合成 1 次 API 调用
     - 相同图片（SHA256）只发一次，复用描述
     - 超过 MAX_IMAGE_KB 的图片先压缩到 1024px
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Config

logger = logging.getLogger("omniparser.parsers.image_describer")

# 每张图对应的文字量阈值（字符数）
TEXT_PER_IMAGE_THRESHOLD = 200

# 最小图片大小（KB），低于此值认为是小图标，跳过
MIN_IMAGE_SIZE_KB = 50

# 超过此大小的图片会被压缩后再发送（KB）
MAX_IMAGE_KB = 200

# 单次合批最大图片数（Gemini 限制）
BATCH_MAX_IMAGES = 16


def should_parse_images(text_char_count: int, image_count: int) -> bool:
    """判断是否应该解析内嵌图片"""
    if image_count == 0:
        return False

    chars_per_image = text_char_count / image_count
    should_parse = chars_per_image < TEXT_PER_IMAGE_THRESHOLD

    if should_parse:
        logger.info(
            "图多字少 (%.0f 字/图, %d 图): 启用图片解析",
            chars_per_image,
            image_count,
        )
    else:
        logger.info(
            "字多图少 (%.0f 字/图, %d 图): 跳过图片",
            chars_per_image,
            image_count,
        )
    return should_parse


@dataclass
class ImageItem:
    """待描述的图片"""

    image_bytes: bytes
    mime_type: str = "image/png"
    context: str = ""  # 位置信息，如 "PDF第3页"
    # 以下由 batch 函数填充
    description: str | None = None
    _hash: str = field(default="", repr=False)


def _compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _compress_image(
    image_bytes: bytes, mime_type: str, max_px: int = 1024
) -> tuple[bytes, str]:
    """压缩大图到 max_px，返回 (压缩后bytes, mime_type)"""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if max(w, h) <= max_px:
            return image_bytes, mime_type

        ratio = max_px / max(w, h)
        new_size = (int(w * ratio), int(h * ratio))
        img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        fmt = "JPEG" if "jpeg" in mime_type or "jpg" in mime_type else "PNG"
        img.save(buf, format=fmt, quality=85)
        out_mime = "image/jpeg" if fmt == "JPEG" else "image/png"

        old_kb = len(image_bytes) / 1024
        new_kb = buf.tell() / 1024
        logger.debug(
            "压缩图片: %dx%d→%dx%d, %.1fKB→%.1fKB", w, h, *new_size, old_kb, new_kb
        )
        return buf.getvalue(), out_mime
    except ImportError:
        logger.debug("Pillow 未安装，跳过压缩")
        return image_bytes, mime_type
    except Exception as e:
        logger.debug("压缩失败: %s", e)
        return image_bytes, mime_type


def describe_images_batch(
    items: list[ImageItem],
    config: Config,
) -> list[ImageItem]:
    """合批描述多张图片（同一文档）

    - 过滤小图标
    - Hash 去重
    - 压缩大图
    - 一次 API 调用描述所有图片
    - 回填 description 到每个 ImageItem

    Returns:
        原始 items 列表（description 已填充）
    """
    if not config.image.api_key:
        logger.warning("No API key, skipping batch image description")
        return items

    # 1. 过滤小图片
    valid_items: list[tuple[int, ImageItem]] = []
    for i, item in enumerate(items):
        size_kb = len(item.image_bytes) / 1024
        if size_kb < MIN_IMAGE_SIZE_KB:
            logger.debug(
                "跳过小图片 #%d (%.1fKB < %dKB)", i + 1, size_kb, MIN_IMAGE_SIZE_KB
            )
            continue
        valid_items.append((i, item))

    if not valid_items:
        logger.info("所有图片均为小图标，全部跳过")
        return items

    # 2. Hash 去重
    hash_map: dict[str, str] = {}  # hash -> description (填充后)
    unique_items: list[tuple[int, ImageItem, str]] = []  # (orig_idx, item, hash)
    dup_items: list[tuple[int, str]] = []  # (orig_idx, hash) — 等去重后复用

    for orig_idx, item in valid_items:
        h = _compute_hash(item.image_bytes)
        item._hash = h
        if h in hash_map or any(u[2] == h for u in unique_items):
            dup_items.append((orig_idx, h))
            logger.debug("图片去重: #%d 与已有图片相同 (hash=%s)", orig_idx + 1, h)
        else:
            unique_items.append((orig_idx, item, h))

    logger.info(
        "合批: %d 张图 → %d 张有效 → %d 张唯一 (%d 重复)",
        len(items),
        len(valid_items),
        len(unique_items),
        len(dup_items),
    )

    # 3. 压缩大图
    processed: list[tuple[int, bytes, str, str, str]] = (
        []
    )  # (orig_idx, bytes, mime, context, hash)
    for orig_idx, item, h in unique_items:
        img_bytes, mime = item.image_bytes, item.mime_type
        if len(img_bytes) / 1024 > MAX_IMAGE_KB:
            img_bytes, mime = _compress_image(img_bytes, mime)
        processed.append((orig_idx, img_bytes, mime, item.context, h))

    # 4. 分批调用（每批最多 BATCH_MAX_IMAGES 张）
    all_descriptions: dict[str, str] = {}  # hash -> description
    for batch_start in range(0, len(processed), BATCH_MAX_IMAGES):
        batch = processed[batch_start : batch_start + BATCH_MAX_IMAGES]
        batch_descs = _call_batch_api(batch, config)
        all_descriptions.update(batch_descs)

    # 5. 回填描述
    for orig_idx, item, h in unique_items:
        items[orig_idx].description = all_descriptions.get(h)

    for orig_idx, h in dup_items:
        items[orig_idx].description = all_descriptions.get(h)

    filled = sum(1 for it in items if it.description)
    logger.info("合批完成: %d/%d 张获得描述", filled, len(items))

    return items


def _call_batch_api(
    batch: list[tuple[int, bytes, str, str, str]],
    config: Config,
) -> dict[str, str]:
    """一次 API 调用描述多张图片，返回 {hash: description}"""
    provider = config.image.provider
    n = len(batch)

    total_kb = sum(len(b[1]) / 1024 for b in batch)
    logger.info(
        "→ 发送 %d 张图片到 %s (总计 %.1fKB, model=%s)",
        n,
        provider,
        total_kb,
        config.image.model,
    )

    prompt = (
        f"以下是同一文档中的 {n} 张图片。请逐一描述每张图片的内容。\n"
        f"请严格按以下格式输出，每张图片用标记分隔：\n\n"
    )
    for i, (_, _, _, ctx, _) in enumerate(batch, 1):
        ctx_hint = f"（{ctx}）" if ctx else ""
        prompt += f"[图片{i}]{ctx_hint}\n"

    prompt += (
        f"\n对每张图片：如果是图表，请提取数据；如果是照片，请描述场景。"
        f"以 Markdown 格式输出。"
    )

    try:
        if provider == "gemini":
            text = _batch_gemini(batch, prompt, config)
        elif provider == "openai":
            text = _batch_openai(batch, prompt, config)
        else:
            logger.error("Unknown provider: %s", provider)
            return {}

        preview = text[:100].replace("\n", " ") if text else "(空)"
        logger.info("← %s 返回 %d 字 | %s...", provider, len(text), preview)

        # 解析返回，按 [图片N] 标记分割
        return _parse_batch_response(text, batch)

    except Exception as e:
        logger.error("合批 API 调用失败: %s", e)
        return {}


def _batch_gemini(
    batch: list[tuple[int, bytes, str, str, str]],
    prompt: str,
    config: Config,
) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.image.api_key)

    contents: list = [prompt]
    for _, img_bytes, mime, _, _ in batch:
        contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

    response = client.models.generate_content(
        model=config.image.model,
        contents=contents,
    )
    return response.text.strip()


def _batch_openai(
    batch: list[tuple[int, bytes, str, str, str]],
    prompt: str,
    config: Config,
) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=config.image.api_key)

    content_parts: list[dict] = [{"type": "text", "text": prompt}]
    for _, img_bytes, mime, _, _ in batch:
        b64 = base64.b64encode(img_bytes).decode("utf-8")
        content_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            }
        )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": content_parts}],
        max_tokens=4000,
    )
    return response.choices[0].message.content.strip()


def _parse_batch_response(
    text: str,
    batch: list[tuple[int, bytes, str, str, str]],
) -> dict[str, str]:
    """解析合批返回文本，按 [图片N] 标记分割"""
    result: dict[str, str] = {}

    # 用正则按 [图片1] [图片2] ... 分割
    pattern = r"\[图片(\d+)\]"
    parts = re.split(pattern, text)

    # parts 结构: [前导文本, "1", 图片1描述, "2", 图片2描述, ...]
    for i in range(1, len(parts) - 1, 2):
        idx = int(parts[i]) - 1  # 0-based
        desc = parts[i + 1].strip()
        if 0 <= idx < len(batch) and desc:
            h = batch[idx][4]  # hash
            result[h] = desc

    # 兜底：如果只有1张图且没匹配到标记，整个文本就是描述
    if not result and len(batch) == 1:
        h = batch[0][4]
        if text.strip():
            result[h] = text.strip()

    return result


# --- 向后兼容：单张图片描述（供 image_parser.py 独立图片使用）---


def describe_image_bytes(
    image_bytes: bytes,
    config: Config,
    context: str = "",
    mime_type: str = "image/png",
) -> str | None:
    """单张图片描述（向后兼容）"""
    item = ImageItem(image_bytes=image_bytes, mime_type=mime_type, context=context)
    items = describe_images_batch([item], config)
    return items[0].description
