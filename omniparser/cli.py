"""OmniParser CLI 入口"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
from pathlib import Path

from .config import Config
from .pipeline import Pipeline
from .utils import setup_logging, ensure_dir


# ---------- 格式分组（用于 formats 子命令） ----------
_FORMAT_GROUPS = {
    "文档": [".docx", ".doc"],
    "表格": [".xlsx", ".xls", ".csv"],
    "演示文稿": [".pptx", ".ppt"],
    "PDF": [".pdf"],
    "图片": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"],
}


def main():
    parser = argparse.ArgumentParser(
        prog="omniparser",
        description="OmniParser - 通用文档解析管道，将任意格式文件统一转换为结构化 Markdown + JSON",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ---- parse 命令 ----
    parse_cmd = subparsers.add_parser("parse", help="解析文件或目录")
    parse_cmd.add_argument(
        "input",
        type=str,
        help="输入文件或目录路径（使用 '-' 从 stdin 读取）",
    )
    parse_cmd.add_argument(
        "-o",
        "--output",
        type=str,
        default="output",
        help="输出目录（使用 '-' 输出到 stdout，默认: output/）",
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
    parse_cmd.add_argument(
        "--ext",
        type=str,
        default=None,
        help="从 stdin 读取时指定文件后缀，如 .pdf、.docx",
    )

    # ---- cache 命令 ----
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

    # ---- convert 命令 ----
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

    # ---- formats 命令 ----
    subparsers.add_parser("formats", help="列出支持的文件格式")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    # formats 不需要配置/日志
    if args.command == "formats":
        _handle_formats()
        return

    # 配置日志（输出到 stderr，不污染 stdout 管道）
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


# ================================================================
#  formats
# ================================================================


def _handle_formats():
    """列出所有支持的文件格式"""
    pipeline = Pipeline(Config.default())
    supported = pipeline.supported_extensions

    print("OmniParser 支持的文件格式:\n")
    for group, extensions in _FORMAT_GROUPS.items():
        available = [ext for ext in extensions if ext in supported]
        if available:
            print(f"  {group}:  {', '.join(available)}")

    # 显示未归类的扩展名
    categorized = {ext for exts in _FORMAT_GROUPS.values() for ext in exts}
    uncategorized = sorted(supported - categorized)
    if uncategorized:
        print(f"  其他:  {', '.join(uncategorized)}")

    print(f"\n共 {len(supported)} 种格式")


# ================================================================
#  parse
# ================================================================


def _handle_parse(args, config: Config, logger: logging.Logger):
    """处理 parse 命令"""
    stdout_mode = args.output == "-"
    stdin_mode = args.input == "-"

    pipeline = Pipeline(config)

    # ---------- stdin 输入 ----------
    if stdin_mode:
        ext = args.ext
        if not ext:
            logger.error("从 stdin 读取时必须通过 --ext 指定文件后缀，如 --ext .pdf")
            sys.exit(1)
        if not ext.startswith("."):
            ext = f".{ext}"

        tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
        try:
            tmp.write(sys.stdin.buffer.read())
            tmp.close()
            results = [pipeline.parse_file(Path(tmp.name))]
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    # ---------- 常规文件/目录输入 ----------
    else:
        input_path = Path(args.input).resolve()
        if not input_path.exists():
            logger.error("Input path does not exist: %s", input_path)
            sys.exit(1)

        if input_path.is_file():
            results = [pipeline.parse_file(input_path)]
        else:
            # 目录模式：带 tqdm 进度条
            from tqdm import tqdm

            pbar = tqdm(desc="Parsing", unit="file", file=sys.stderr)

            def _on_progress(result, current, total):
                if pbar.total is None:
                    pbar.total = total
                    pbar.refresh()
                pbar.set_postfix_str(Path(result.source).name, refresh=False)
                pbar.update(1)

            results = pipeline.parse_directory(
                input_path,
                recursive=args.recursive,
                on_progress=_on_progress,
            )
            pbar.close()

    # ---------- 输出 ----------
    success_count = 0
    fail_count = 0

    for result in results:
        if not result.success:
            fail_count += 1
            logger.error("FAILED: %s - %s", result.source, result.error)
            continue

        success_count += 1

        if stdout_mode:
            _write_result_stdout(result, args.format)
        else:
            _write_result_file(result, Path(args.output).resolve(), args.format, logger)

    # 汇总（写到 stderr 以免影响 stdout 管道）
    summary = f"\n{'='*50}\n" f"解析完成: {success_count} 成功, {fail_count} 失败\n"
    if not stdout_mode:
        summary += f"输出目录: {Path(args.output).resolve()}\n"
    summary += f"{'='*50}"
    print(summary, file=sys.stderr)


def _write_result_stdout(result, fmt: str):
    """将解析结果写到 stdout"""
    if fmt in ("json", "both"):
        json.dump(result.to_dict(), sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    if fmt in ("markdown", "both"):
        for doc in result.documents:
            sys.stdout.write(doc.content)
            sys.stdout.write("\n\n")
    sys.stdout.flush()


def _write_result_file(result, output_dir: Path, fmt: str, logger: logging.Logger):
    """将解析结果写到文件"""
    ensure_dir(output_dir)
    source_name = Path(result.source).stem

    if fmt in ("json", "both"):
        json_path = output_dir / f"{source_name}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("Written: %s", json_path)

    if fmt in ("markdown", "both"):
        md_path = output_dir / f"{source_name}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            for doc in result.documents:
                f.write(doc.content)
                f.write("\n\n")
        logger.info("Written: %s", md_path)


# ================================================================
#  cache
# ================================================================


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


# ================================================================
#  convert
# ================================================================


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
