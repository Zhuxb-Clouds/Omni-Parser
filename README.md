# OmniParser

[中文文档](README-zh.md)

> Universal document parsing pipeline: convert files of any format into structured Markdown + JSON, providing high-quality input for RAG / LLM applications.

---

## Architecture Overview

```
Input Files → Router (dispatch by extension) → Parser Plugins → Post-processors (chunking/metadata) → Structured Output
```

### Core Design Principles

- **Plugin-based pipeline**: Add a new format by registering a single Parser — no changes to the main flow
- **Three-tier fallback strategy**: Local parsing → Local OCR → Cloud multimodal AI, with automatic fallback in order of increasing cost
- **Structured output**: Markdown content + JSON metadata, satisfying both readability and retrieval needs
- **Idempotent with caching**: File-hash-based cache to avoid redundant parsing

---

## Tiered Processing Strategy

| Tier        | Method                                            | Cost                   | Use Case                            |
| ----------- | ------------------------------------------------- | ---------------------- | ----------------------------------- |
| **Layer 1** | Pure local parsing (python-docx, pandas, pymupdf) | Zero cost              | Text-based documents                |
| **Layer 2** | Local OCR (Surya / Tesseract)                     | Low cost, requires GPU | Scanned PDFs                        |
| **Layer 3** | Cloud multimodal AI (Gemini / GPT-4o)             | High cost              | Image descriptions, complex layouts |

---

## Supported Formats

| File Type  | Parser        | Library                                 | Processing Logic                                              |
| ---------- | ------------- | --------------------------------------- | ------------------------------------------------------------- |
| **DOCX**   | `DocxParser`  | `python-docx`                           | Extracts paragraphs, headings, and tables with hierarchy      |
| **DOC**    | `DocParser`   | `libreoffice --headless` pre-conversion | Converts to DOCX first, then parses                           |
| **XLSX**   | `XlsxParser`  | `pandas` + `openpyxl`                   | Converts to Markdown tables, preserves sheet names            |
| **PPTX**   | `PptxParser`  | `python-pptx`                           | Extracts titles and body text by slide number                 |
| **PDF**    | `PdfParser`   | `PyMuPDF` → Surya (fallback)            | Direct text extraction; auto-fallback to OCR for scanned docs |
| **Images** | `ImageParser` | Gemini API                              | Multimodal AI description + OCR                               |

---

## Output Format

OmniParser converts source files into structured **JSON** and/or **Markdown** files.

- The output directory **mirrors the source directory tree**
- Each source file produces a `{filename}.json` and/or `{filename}.md`

### JSON Structure

#### Full Example

```json
{
  "source": "subdir/annual-report-2024.pdf",
  "file_hash": "a1b2c3d4e5f6...",
  "success": true,
  "error": null,
  "documents": [
    {
      "source": "/absolute/path/subdir/annual-report-2024.pdf",
      "content": "## Chapter 1: Company Overview\n\nThe company was founded in...",
      "content_type": "heading",
      "page": 1,
      "metadata": {
        "file_hash": "a1b2c3d4e5f6...",
        "author": "John Doe",
        "created": "2024-06-01"
      }
    },
    {
      "source": "/absolute/path/subdir/annual-report-2024.pdf",
      "content": "| Metric | 2023 | 2024 |\n|---|---|---|\n| Revenue | $10B | $12B |",
      "content_type": "table",
      "page": 5,
      "metadata": { "file_hash": "a1b2c3d4e5f6..." }
    }
  ],
  "chunks": [
    {
      "content": "## Chapter 1: Company Overview\n\nThe company was founded in...",
      "source": "/absolute/path/subdir/annual-report-2024.pdf",
      "chunk_index": 0,
      "metadata": {}
    },
    {
      "content": "## Chapter 2: Financials\n\n| Metric | 2023 | 2024 |...",
      "source": "/absolute/path/subdir/annual-report-2024.pdf",
      "chunk_index": 1,
      "metadata": {}
    }
  ]
}
```

#### Top-Level Fields

| Field       | Type             | Description                                                                  |
| ----------- | ---------------- | ---------------------------------------------------------------------------- |
| `source`    | `string`         | Relative path of the source file (relative to the input directory)           |
| `file_hash` | `string`         | SHA-256 hash of file contents; useful for change detection and deduplication |
| `success`   | `boolean`        | Whether parsing succeeded                                                    |
| `error`     | `string \| null` | Error message on failure; `null` on success                                  |
| `documents` | `array`          | List of extracted **content blocks** (see below)                             |
| `chunks`    | `array`          | List of **chunks** for RAG retrieval (see below)                             |

#### `documents[]` — Content Blocks

Each document represents a logical segment of the source file (paragraph, heading, table, image description, etc.).

| Field          | Type             | Description                                                              |
| -------------- | ---------------- | ------------------------------------------------------------------------ |
| `source`       | `string`         | **Absolute path** of the source file                                     |
| `content`      | `string`         | Extracted content in **Markdown format**                                 |
| `content_type` | `string`         | Block type (see enum values below)                                       |
| `page`         | `int \| null`    | Page number (PDF) or slide index (PPTX); may be `null` for other formats |
| `sheet`        | `string \| null` | Excel worksheet name; only present for `.xlsx` files                     |
| `metadata`     | `object`         | Additional metadata (file_hash, author, created, etc.)                   |

**`content_type` enum values:**

| Value       | Meaning                                        |
| ----------- | ---------------------------------------------- |
| `heading`   | Heading / title                                |
| `paragraph` | Body paragraph                                 |
| `table`     | Table (Markdown table syntax)                  |
| `list`      | List                                           |
| `image`     | Image description (generated by multimodal AI) |
| `code`      | Code block                                     |
| `unknown`   | Unrecognized type                              |

#### `chunks[]` — RAG Chunks

Each chunk is a text fragment produced by the chunking strategy, ready to be fed into a vector database or search engine.

| Field         | Type     | Description                     |
| ------------- | -------- | ------------------------------- |
| `content`     | `string` | Chunked text content            |
| `source`      | `string` | Source file path                |
| `chunk_index` | `int`    | Chunk sequence number (0-based) |
| `metadata`    | `object` | Additional metadata             |

**Chunking strategies (configurable via `config.yaml`):**

- `heading` (default): Splits on Markdown heading boundaries — a new chunk begins at each heading
- `fixed_token`: Splits by fixed token count with an overlap window (default: max_tokens=512, overlap=50)

### Markdown Structure

A `{filename}.md` file is generated alongside the JSON, containing all `documents[].content` concatenated in order:

```markdown
<!-- source: subdir/annual-report-2024.pdf -->

## Chapter 1: Company Overview

The company was founded in...

| Metric  | 2023 | 2024 |
| ------- | ---- | ---- |
| Revenue | $10B | $12B |

...
```

- A `<!-- source: relative/path -->` HTML comment at the top identifies the source file
- Markdown files are intended for **human reading and review**
- JSON files are intended for **programmatic consumption and retrieval**

### Special Cases

| Case                                                                    | Behavior                                                                 |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| **Image files** (jpg/png/gif/webp/tiff/bmp)                             | Described by multimodal AI (e.g. Gemini); `content_type` is `"image"`    |
| **Image-heavy documents** (PDF/DOCX/PPTX with many images, little text) | Pre-scanned; multimodal AI auto-invoked for embedded images              |
| **`.txt` plain text**                                                   | Copied verbatim; `metadata` includes `"direct_copy": true`               |
| **Parse failures**                                                      | `success: false`, `error` has details, `documents` and `chunks` are `[]` |

### Downstream Consumption Guide

| Scenario                     | Recommendation                                                                   |
| ---------------------------- | -------------------------------------------------------------------------------- |
| **RAG / Vector Search**      | Use `chunks[]` directly; each chunk's `content` is a retrieval unit              |
| **Full-Text Analysis**       | Iterate `documents[]` and concatenate `content` to reconstruct the full document |
| **Incremental Updates**      | Compare `file_hash` to detect changes and avoid redundant processing             |
| **Quality Filtering**        | Check `success` to filter out failed files                                       |
| **Type-Specific Processing** | Use `content_type` for differentiated handling of tables, images, etc.           |
| **Page Localization**        | Use `page` to trace content back to its position in the original document        |

---

## Project Structure

```
omniparser/
├── __init__.py
├── cli.py                  # CLI entry point
├── mcp_server.py           # MCP Server entry point
├── config.py               # Global configuration
├── pipeline.py             # Pipeline dispatcher
├── models.py               # Data models (Document, Chunk)
├── parsers/                # Parser plugins
│   ├── __init__.py
│   ├── base.py             # BaseParser abstract class
│   ├── docx_parser.py
│   ├── xlsx_parser.py
│   ├── pptx_parser.py
│   ├── pdf_parser.py
│   └── image_parser.py
├── postprocessors/         # Post-processors
│   ├── __init__.py
│   ├── chunker.py          # Chunking strategies
│   └── metadata.py         # Metadata extraction
├── cache.py                # File hash cache
└── utils.py                # Utility functions
```

---

## Quick Start

```bash
# Install
pip install -e .

# Parse a single file
omniparser parse report.pdf -o output/

# Batch parse a directory
omniparser parse ./documents/ -o output/ --recursive

# Specify output format
omniparser parse ./documents/ -o output/ --format json
```

### Batch Conversion

```bash
# Output both JSON and Markdown (default)
omniparser batch -i /path/to/source/ -o /path/to/output/

# JSON only
omniparser batch -i /path/to/source/ -o /path/to/output/ -f json

# Set API concurrency
omniparser batch -i /path/to/source/ -o /path/to/output/ --workers 8
```

Example output directory structure:

```
output/
├── subdir-a/
│   ├── report.json
│   ├── report.md
│   ├── spreadsheet.json
│   └── spreadsheet.md
├── subdir-b/
│   ├── presentation.json
│   ├── presentation.md
│   ├── photo.json
│   └── photo.md
└── readme.txt.json
```

---

## Configuration

Configure via `config.yaml` or environment variables:

```yaml
# config.yaml
cache:
  enabled: true
  dir: .omniparser_cache

image:
  provider: gemini          # gemini / openai
  api_key: ${GEMINI_API_KEY}
  prompt: "Describe this image in detail. If it's a chart, extract the data; if it's a photo, describe the scene. Output in Markdown format."

chunking:
  strategy: heading         # heading / fixed_token
  max_tokens: 512
  overlap: 50

pdf:
  ocr_threshold: 0.3        # Falls back to OCR when text extraction rate is below this value
```

---

## MCP Server (Model Context Protocol)

OmniParser can run as an MCP Server, allowing AI clients like Claude Desktop and VS Code Copilot to invoke document parsing capabilities directly.

### Installation

```bash
pip install -e ".[mcp]"
```

### Start the Server

```bash
# Via console script
omniparser-mcp

# Or run the module directly
python -m omniparser.mcp_server

# Debug with MCP Inspector
mcp dev omniparser/mcp_server.py
```

### Available Tools

| Tool                | Description                                                   |
| ------------------- | ------------------------------------------------------------- |
| `parse_file`        | Parse a single file with markdown/json/both output formats    |
| `parse_directory`   | Batch parse a directory with recursive and file limit support |
| `supported_formats` | List all supported file extensions                            |
| `cache_info`        | View cache status                                             |
| `cache_clear`       | Clear all parsing cache                                       |

### Available Resources

| URI                              | Description                                |
| -------------------------------- | ------------------------------------------ |
| `omniparser://cache/list`        | List summaries of all cached parse results |
| `omniparser://cache/{file_hash}` | Get the full parse result for a given hash |

### Client Configuration

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "omniparser": {
      "command": "omniparser-mcp",
      "env": {
        "GEMINI_API_KEY": "your-api-key-here"
      }
    }
  }
}
```

**VS Code** (`.vscode/settings.json`):

```json
{
  "mcp": {
    "servers": {
      "omniparser": {
        "command": "omniparser-mcp",
        "env": {
          "GEMINI_API_KEY": "your-api-key-here"
        }
      }
    }
  }
}
```

> 💡 If the project root has a `.env` file with `GEMINI_API_KEY` configured, the MCP Server will load it automatically — no need to repeat it in the client configuration.
