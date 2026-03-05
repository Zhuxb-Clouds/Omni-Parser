#!/usr/bin/env python3
"""找出含 "(图片未解析：未配置 API Key)" 的输出文件并删除，
以便 batch_convert 带 API Key 重新解析。

需要先设置环境变量 OMNIPARSER_SRC_DIR / OMNIPARSER_DST_DIR。
"""

import json
import os
import subprocess
from pathlib import Path


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"环境变量 {name} 未设置，请在 .env 或 shell 中配置")
    return val


SRC_DIR = Path(_require_env("OMNIPARSER_SRC_DIR"))
DST_DIR = Path(_require_env("OMNIPARSER_DST_DIR"))
CACHE_DIR = Path(os.environ.get("OMNIPARSER_CACHE_DIR", ".omniparser_cache"))

MARKER = "图片未解析"


def find_affected_md():
    """用 grep 找到所有含占位符的 .md 文件"""
    result = subprocess.run(
        ["grep", "-rl", MARKER, str(DST_DIR)], capture_output=True, text=True
    )
    paths = [Path(p.strip()) for p in result.stdout.strip().splitlines() if p.strip()]
    # 去重：只保留 .md，后面自动处理对应 .json
    md_files = sorted(set(p for p in paths if p.suffix == ".md"))
    return md_files


def delete_outputs(md_files):
    """删除 .md 和对应 .json"""
    deleted = 0
    for md in md_files:
        jp = md.with_suffix(".json")
        for f in (md, jp):
            if f.exists():
                f.unlink()
                deleted += 1
    return deleted


def clear_cache(md_files):
    """按源文件路径清除缓存"""
    if not CACHE_DIR.exists():
        print(f"  缓存目录不存在: {CACHE_DIR}")
        return 0

    # 构造源文件路径集合
    src_paths = set()
    for md in md_files:
        rel = md.relative_to(DST_DIR)
        stem = rel.stem
        parent = rel.parent
        src_dir = SRC_DIR / parent
        if src_dir.exists():
            for sf in src_dir.iterdir():
                if sf.stem == stem and sf.is_file():
                    src_paths.add(str(sf.resolve()))

    cleared = 0
    for cache_file in CACHE_DIR.rglob("*.json"):
        try:
            data = json.loads(cache_file.read_text())
            if isinstance(data, dict):
                src = data.get("source", "")
                if src in src_paths:
                    cache_file.unlink()
                    cleared += 1
        except Exception:
            pass
    return cleared


def main():
    print("=== 查找含 '图片未解析：未配置 API Key' 的输出 ===")
    md_files = find_affected_md()
    print(f"  找到 {len(md_files)} 个受影响的输出文件")

    if not md_files:
        print("  没有需要重新解析的文件！")
        return

    # 列出前10个示例
    print("\n  示例:")
    for f in md_files[:10]:
        print(f"    {f.relative_to(DST_DIR)}")
    if len(md_files) > 10:
        print(f"    ... 还有 {len(md_files) - 10} 个")

    print(f"\n=== 删除输出 (md + json) ===")
    deleted = delete_outputs(md_files)
    print(f"  已删除 {deleted} 个文件")

    print(f"\n=== 清除缓存 ===")
    cleared = clear_cache(md_files)
    print(f"  已清除 {cleared} 条缓存")

    print(f"\n=== 自动重新解析 ===")
    _load_dotenv()

    if not os.environ.get("GEMINI_API_KEY"):
        print("  错误: 请先在 .env 中填入 GEMINI_API_KEY")
        return

    import sys
    import logging

    sys.path.insert(0, str(Path(__file__).resolve().parent))

    from omniparser.utils import setup_logging

    setup_logging("DEBUG")
    for noisy in ("httpcore", "httpx", "urllib3", "google_genai", "google.auth"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from omniparser.config import Config
    from omniparser.batch_convert import batch_convert

    config = Config.from_yaml(str(Path(__file__).resolve().parent / "config.yaml"))
    stats = batch_convert(SRC_DIR, DST_DIR, config, output_format="both", api_workers=8)

    print(f"\n=== 重新解析完成 ===")
    print(f"  成功: {stats['success']}")
    print(f"  失败: {stats['failed']}")
    print(f"  跳过: {stats['skipped']}")
    if stats["errors"]:
        for e in stats["errors"]:
            print(f"  ✗ {e['file']}: {e.get('error', '')}")


def _load_dotenv():
    """从 .env 加载环境变量"""
    env_file = Path(__file__).resolve().parent / ".env"
    if not env_file.exists():
        return
    import os

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()
