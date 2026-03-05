"""OmniParser MCP Server

Exposes OmniParser document parsing capabilities as an MCP (Model Context Protocol) service,
enabling Claude Desktop, VS Code Copilot and other MCP clients to invoke parsing directly.

Usage:
    omniparser-mcp                          # via console script
    python -m omniparser.mcp_server         # run module directly
    mcp dev omniparser/mcp_server.py        # debug with MCP Inspector
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------- Environment Init ----------

# Auto-load .env (MCP process is spawned by client, bypasses shell profile)
try:
    from dotenv import load_dotenv

    # Try project root .env first, fall back to cwd
    _project_root = Path(__file__).resolve().parent.parent
    _env_file = _project_root / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
    else:
        load_dotenv()
except ImportError:
    pass  # silently skip if python-dotenv is not installed

from .config import Config
from .pipeline import Pipeline
from .cache import FileCache

logger = logging.getLogger("omniparser.mcp")

# ---------- Global Singletons ----------

_config: Config | None = None
_pipeline: Pipeline | None = None


def _get_config() -> Config:
    """Lazy-load configuration (singleton)."""
    global _config
    if _config is None:
        # Prefer config.yaml from project root
        project_root = Path(__file__).resolve().parent.parent
        yaml_path = project_root / "config.yaml"
        if yaml_path.exists():
            _config = Config.from_yaml(yaml_path)
        else:
            _config = Config.default()
    return _config


def _get_pipeline() -> Pipeline:
    """Lazy-load Pipeline (singleton)."""
    global _pipeline
    if _pipeline is None:
        _pipeline = Pipeline(_get_config())
    return _pipeline


# ---------- MCP Server ----------

mcp = FastMCP(
    "OmniParser",
    instructions=(
        "Universal document parsing service — converts PDF, DOCX, XLSX, PPTX, "
        "images and more into structured Markdown + JSON for RAG / LLM applications."
    ),
)

# ==================== Tools ====================


@mcp.tool()
async def parse_file(
    file_path: str,
    format: str = "markdown",
) -> str:
    """Parse a single file and return structured content.

    Supported formats: PDF, DOCX, XLSX, PPTX, PNG, JPG, GIF, WEBP, TIFF, BMP

    Args:
        file_path: Absolute or relative path to the file
        format: Output format - "markdown" (default) / "json" / "both"
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return f"❌ File not found: {path}"
    if not path.is_file():
        return f"❌ Path is not a file: {path}"

    pipeline = _get_pipeline()
    result = await asyncio.to_thread(pipeline.parse_file, path)

    if not result.success:
        return f"❌ Parse failed: {result.error}"

    return _format_result(result, format)


@mcp.tool()
async def parse_directory(
    dir_path: str,
    recursive: bool = False,
    format: str = "markdown",
    max_files: int = 50,
) -> str:
    """Batch parse all supported files in a directory.

    Args:
        dir_path: Absolute or relative path to the directory
        recursive: Whether to process subdirectories recursively
        format: Output format - "markdown" (default) / "json" / "both"
        max_files: Maximum number of files to process (default 50, prevents overload)
    """
    path = Path(dir_path).resolve()
    if not path.exists():
        return f"❌ Directory not found: {path}"
    if not path.is_dir():
        return f"❌ Path is not a directory: {path}"

    pipeline = _get_pipeline()

    # Collect file list and check count
    from .utils import collect_files

    files = collect_files(
        path, recursive=recursive, extensions=pipeline.supported_extensions
    )

    if len(files) == 0:
        exts = ", ".join(sorted(pipeline.supported_extensions))
        return f"No supported files found in directory. Supported formats: {exts}"

    truncated = False
    if len(files) > max_files:
        truncated = True
        files = files[:max_files]

    # Parse files one by one
    results = []
    for f in files:
        r = await asyncio.to_thread(pipeline.parse_file, f)
        results.append(r)

    # Assemble output
    success = sum(1 for r in results if r.success)
    failed = len(results) - success

    parts = [
        f"## Batch Parse Results\n\n📁 Directory: `{path}`\n✅ Success: {success} | ❌ Failed: {failed}"
    ]
    if truncated:
        parts.append(
            f"⚠️ Only processed the first {max_files} files (directory contains more)\n"
        )
    parts.append("")

    for result in results:
        parts.append(f"---\n### 📄 {Path(result.source).name}\n")
        if not result.success:
            parts.append(f"**Error:** {result.error}\n")
        else:
            parts.append(_format_result(result, format))
        parts.append("")

    return "\n".join(parts)


@mcp.tool()
async def supported_formats() -> str:
    """List all currently supported file formats."""
    pipeline = _get_pipeline()
    exts = sorted(pipeline.supported_extensions)

    lines = ["## Supported File Formats\n"]
    for ext in exts:
        lines.append(f"- `{ext}`")

    lines.append(f"\n{len(exts)} formats in total")
    return "\n".join(lines)


@mcp.tool()
async def cache_info() -> str:
    """View parsing cache status information."""
    config = _get_config()
    cache_dir = Path(config.cache.dir)

    if not config.cache.enabled:
        return "Cache is disabled"

    if not cache_dir.exists():
        return f"Cache directory `{cache_dir.resolve()}` does not exist (no cache generated yet)"

    files = list(cache_dir.glob("*.json"))
    total_size = sum(f.stat().st_size for f in files)

    return (
        f"## Cache Info\n\n"
        f"- 📂 Directory: `{cache_dir.resolve()}`\n"
        f"- 📄 Files: {len(files)}\n"
        f"- 💾 Total size: {total_size / 1024:.1f} KB\n"
        f"- 🔄 Status: {'enabled' if config.cache.enabled else 'disabled'}"
    )


@mcp.tool()
async def cache_clear() -> str:
    """Clear all parsing cache."""
    pipeline = _get_pipeline()
    count = await asyncio.to_thread(pipeline.cache.clear)
    return f"Cleared {count} cached files ✅"


# ==================== Resources ====================


@mcp.resource("omniparser://cache/list")
async def list_cached_results() -> str:
    """List all cached parse results."""
    config = _get_config()
    cache_dir = Path(config.cache.dir)

    if not cache_dir.exists():
        return json.dumps({"cached_files": [], "count": 0}, ensure_ascii=False)

    entries = []
    for f in sorted(cache_dir.glob("*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            entries.append(
                {
                    "file_hash": data.get("file_hash", ""),
                    "source": data.get("source", ""),
                    "success": data.get("success", True),
                    "documents_count": len(data.get("documents", [])),
                    "chunks_count": len(data.get("chunks", [])),
                    "cache_file": f.name,
                }
            )
        except (json.JSONDecodeError, OSError):
            continue

    return json.dumps(
        {"cached_files": entries, "count": len(entries)},
        ensure_ascii=False,
        indent=2,
    )


@mcp.resource("omniparser://cache/{file_hash}")
async def get_cached_result(file_hash: str) -> str:
    """Get a cached parse result by file hash.

    Args:
        file_hash: SHA-256 hash of the file (without sha256: prefix)
    """
    config = _get_config()
    cache_dir = Path(config.cache.dir)
    cache_path = cache_dir / f"{file_hash}.json"

    if not cache_path.exists():
        return json.dumps(
            {"error": f"No cache found for hash {file_hash}"}, ensure_ascii=False
        )

    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return json.dumps(data, ensure_ascii=False, indent=2)


# ==================== Helpers ====================


def _format_result(result: Any, format: str) -> str:
    """Convert a ParseResult to string in the specified format."""
    if format == "json":
        return result.to_json(indent=2)
    elif format == "both":
        md_parts = []
        for doc in result.documents:
            md_parts.append(doc.content)
        markdown = "\n\n".join(md_parts)
        json_str = result.to_json(indent=2)
        return f"{markdown}\n\n---\n\n<details>\n<summary>JSON Structured Data</summary>\n\n```json\n{json_str}\n```\n\n</details>"
    else:
        # markdown (default)
        parts = []
        for doc in result.documents:
            parts.append(doc.content)
        text = "\n\n".join(parts)
        # Append summary
        text += f"\n\n---\n*📊 {len(result.documents)} content blocks, {len(result.chunks)} chunks*"
        return text


# ---------- Entry Point ----------


def main():
    """MCP Server entry point — runs via stdio transport."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
