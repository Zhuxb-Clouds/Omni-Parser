#!/usr/bin/env python3
"""找出所有空输出文件并删除，以便 batch_convert 重新解析它们。

步骤：
  1. 扫描输出目录中 documents=[] 的 JSON
  2. 删除对应的 .md + .json
  3. 清除缓存中这些文件的条目
  4. 然后用户可以重新运行 batch_convert
"""

import json
import os
import shutil
from pathlib import Path


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"环境变量 {name} 未设置，请在 .env 或 shell 中配置")
    return val


SRC_DIR = Path(_require_env("OMNIPARSER_SRC_DIR"))
DST_DIR = Path(_require_env("OMNIPARSER_DST_DIR"))
CACHE_DIR = Path(os.environ.get("OMNIPARSER_CACHE_DIR", ".omniparser_cache"))


def find_empty_outputs():
    """找出 documents 为空且 error 为 null 的输出"""
    empty = []
    for json_path in DST_DIR.rglob("*.json"):
        if json_path.name.startswith("_"):
            continue
        try:
            data = json.loads(json_path.read_text())
            if (
                isinstance(data, dict)
                and not data.get("documents")
                and not data.get("error")
            ):
                empty.append(json_path)
        except Exception:
            empty.append(json_path)  # 损坏的 JSON 也算
    return empty


def delete_outputs(json_paths):
    """删除 json + 同名 md"""
    deleted = 0
    for jp in json_paths:
        md = jp.with_suffix(".md")
        for f in (jp, md):
            if f.exists():
                f.unlink()
                deleted += 1
    return deleted


def clear_cache_for_empty(json_paths):
    """清除缓存中对应的源文件条目"""
    if not CACHE_DIR.exists():
        print(f"  缓存目录不存在: {CACHE_DIR}")
        return 0

    # 构造源文件相对路径集合
    src_rels = set()
    for jp in json_paths:
        rel = jp.relative_to(DST_DIR)
        stem = rel.stem
        parent = rel.parent
        # 找源文件
        src_dir = SRC_DIR / parent
        if src_dir.exists():
            for sf in src_dir.iterdir():
                if sf.stem == stem and sf.is_file():
                    src_rels.add(str(sf.resolve()))

    # 扫描缓存文件，删除匹配的
    cleared = 0
    for cache_file in CACHE_DIR.rglob("*.json"):
        try:
            data = json.loads(cache_file.read_text())
            if isinstance(data, dict):
                src = data.get("source", "")
                if src in src_rels:
                    cache_file.unlink()
                    cleared += 1
        except Exception:
            pass

    return cleared


def main():
    print("=== 查找空输出文件 ===")
    empty = find_empty_outputs()
    print(f"  找到 {len(empty)} 个空输出 JSON")

    if not empty:
        print("  没有需要重新解析的文件")
        return

    print(f"\n=== 删除空输出 (json+md) ===")
    deleted = delete_outputs(empty)
    print(f"  已删除 {deleted} 个文件")

    print(f"\n=== 清除对应缓存 ===")
    cleared = clear_cache_for_empty(empty)
    print(f"  已清除 {cleared} 条缓存")

    print(f"\n=== 完成 ===")
    print(f"现在可以重新运行 batch_convert 来解析这些文件:")
    print(f"  export GEMINI_API_KEY=...")
    print(f'  python -m omniparser.batch_convert -i "{SRC_DIR}" -o "{DST_DIR}" -v -w 8')


if __name__ == "__main__":
    main()
