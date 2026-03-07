"""OmniParser CLI 入口"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Config
from .pipeline import Pipeline
from .utils import setup_logging, ensure_dir


def main():
    parser = argparse.ArgumentParser(
        prog="omniparser",
        description="OmniParser - 通用文档解析管道，将任意格式文件统一转换为结构化 Markdown + JSON",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # parse 命令
    parse_cmd = subparsers.add_parser("parse", help="解析文件或目录")
    parse_cmd.add_argument(
        "input",
        type=str,
        help="输入文件或目录路径",
    )
    parse_cmd.add_argument(
        "-o",
        "--output",
        type=str,
        default="output",
        help="输出目录 (默认: output/)",
    )
    parse_cmd.add_argument(
        "-f",
        "--format",
        choices=["json", "markdown", "both"],
        default="both",
        help="输出格式 (默认: both)",
    )
    parse_cmd.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="递归处理子目录",
    )
    parse_cmd.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    parse_cmd.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="详细日志输出",
    )

    # cache 命令
    cache_cmd = subparsers.add_parser("cache", help="缓存管理")
    cache_cmd.add_argument(
        "action",
        choices=["clear", "info"],
        help="缓存操作",
    )
    cache_cmd.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径",
    )

    # convert 命令
    convert_cmd = subparsers.add_parser("convert", help="将 Excel 文件转换为 CSV")
    convert_cmd.add_argument(
        "input",
        type=str,
        help="输入 Excel 文件路径 (.xlsx / .xls)",
    )
    convert_cmd.add_argument(
        "-o",
        "--output",
        type=str,
        default="",
        help="CSV 输出目录 (默认: 源文件所在目录)",
    )
    convert_cmd.add_argument(
        "-c",
        "--config",
        type=str,
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)",
    )
    convert_cmd.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="详细日志输出",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # 配置日志
    setup_logging("DEBUG" if getattr(args, "verbose", False) else "INFO")
    logger = logging.getLogger("omniparser")

    # 加载配置
    config = Config.from_yaml(args.config)

    if args.command == "parse":
        _handle_parse(args, config, logger)
    elif args.command == "cache":
        _handle_cache(args, config, logger)
    elif args.command == "convert":
        _handle_convert(args, config, logger)


def _handle_parse(args, config: Config, logger: logging.Logger):
    """处理 parse 命令"""
    input_path = Path(args.input).resolve()
    output_dir = Path(args.output).resolve()

    if not input_path.exists():
        logger.error("Input path does not exist: %s", input_path)
        sys.exit(1)

    ensure_dir(output_dir)

    pipeline = Pipeline(config)

    # 解析
    if input_path.is_file():
        results = [pipeline.parse_file(input_path)]
    else:
        results = pipeline.parse_directory(input_path, recursive=args.recursive)

    # 输出
    success_count = 0
    fail_count = 0

    for result in results:
        if not result.success:
            fail_count += 1
            logger.error("FAILED: %s - %s", result.source, result.error)
            continue

        success_count += 1
        source_name = Path(result.source).stem

        # 输出 JSON
        if args.format in ("json", "both"):
            json_path = output_dir / f"{source_name}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("Written: %s", json_path)

        # 输出 Markdown
        if args.format in ("markdown", "both"):
            md_path = output_dir / f"{source_name}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                for doc in result.documents:
                    f.write(doc.content)
                    f.write("\n\n")
            logger.info("Written: %s", md_path)

    # 汇总
    print(f"\n{'='*50}")
    print(f"解析完成: {success_count} 成功, {fail_count} 失败")
    print(f"输出目录: {output_dir}")
    print(f"{'='*50}")


def _handle_cache(args, config: Config, logger: logging.Logger):
    """处理 cache 命令"""
    from .cache import FileCache

    cache = FileCache(config.cache)

    if args.action == "clear":
        count = cache.clear()
        print(f"已清除 {count} 个缓存文件")
    elif args.action == "info":
        cache_dir = Path(config.cache.dir)
        if cache_dir.exists():
            files = list(cache_dir.glob("*.json"))
            total_size = sum(f.stat().st_size for f in files)
            print(f"缓存目录: {cache_dir.resolve()}")
            print(f"缓存文件数: {len(files)}")
            print(f"总大小: {total_size / 1024:.1f} KB")
        else:
            print("缓存目录不存在")


def _handle_convert(args, config: Config, logger: logging.Logger):
    """处理 convert 命令 —— 将 Excel 转换为 CSV"""
    input_path = Path(args.input).resolve()

    if not input_path.exists():
        logger.error("文件不存在: %s", input_path)
        sys.exit(1)

    if not input_path.is_file():
        logger.error("路径不是文件: %s", input_path)
        sys.exit(1)

    output_dir = Path(args.output).resolve() if args.output else None

    pipeline = Pipeline(config)

    try:
        results = pipeline.convert_excel_to_csv(input_path, output_dir)
    except (ValueError, FileNotFoundError) as e:
        logger.error("转换失败: %s", e)
        sys.exit(1)

    if not results:
        print("⚠️ 未找到非空的 Sheet")
        return

    print(f"\n{'='*50}")
    print(f"Excel → CSV 转换完成")
    print(f"源文件: {input_path}")
    print(f"共转换 {len(results)} 个 Sheet:")
    for r in results:
        print(f"  📄 {r.sheet_name} → {r.csv_path} ({r.rows} 行, {r.columns} 列)")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
