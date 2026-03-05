# OmniParser

[English](README.md)

> 通用文档解析管道：将任意格式文件统一转换为结构化 Markdown + JSON，为 RAG / LLM 提供高质量输入。

---

## 架构概览

```
输入文件 → 路由器(按后缀分发) → 解析器插件 → 后处理器(分块/元数据) → 结构化输出
```

### 核心设计原则

- **插件式管道架构**：新增格式只需注册一个 Parser，不改主流程
- **三层降级策略**：本地解析 → 本地 OCR → 云端多模态 AI，按成本递增自动降级
- **结构化输出**：Markdown 内容 + JSON 元数据，同时满足可读性和检索需求
- **幂等与缓存**：基于文件 hash 的缓存机制，避免重复解析

---

## 分层处理策略

| 层级 | 方式 | 成本 | 适用场景 |
|------|------|------|----------|
| **Layer 1** | 纯本地解析（python-docx, pandas, pymupdf） | 零成本 | 文本型文档 |
| **Layer 2** | 本地 OCR（Surya / Tesseract） | 低成本，需 GPU | 扫描型 PDF |
| **Layer 3** | 云端多模态 AI（Gemini / GPT-4o） | 高成本 | 图片描述、复杂版面 |

---

## 格式支持

| 文件类型 | 解析器 | 工具库 | 处理逻辑 |
|----------|--------|--------|----------|
| **DOCX** | `DocxParser` | `python-docx` | 提取段落、标题和表格，保留层级 |
| **DOC** | `DocParser` | `libreoffice --headless` 预转换 | 先转 DOCX 再解析 |
| **XLSX** | `XlsxParser` | `pandas` + `openpyxl` | 转为 Markdown 表格，保留 Sheet 名 |
| **PPTX** | `PptxParser` | `python-pptx` | 按幻灯片编号提取标题和正文 |
| **PDF** | `PdfParser` | `PyMuPDF` → Surya（降级） | 文本型直接提取；扫描型自动降级 OCR |
| **图片** | `ImageParser` | Gemini API | 多模态 AI 描述 + OCR |

---

## 输出格式

OmniParser 将各种格式的源文件统一转换为结构化的 **JSON** 和/或 **Markdown** 文件。

- 输出目录**保留与源目录相同的目录树结构**
- 每个源文件对应一个 `{文件名}.json` 和/或 `{文件名}.md`

### JSON 结构

#### 完整示例

```json
{
  "source": "子目录/年报2024.pdf",
  "file_hash": "a1b2c3d4e5f6...",
  "success": true,
  "error": null,
  "documents": [
    {
      "source": "/absolute/path/子目录/年报2024.pdf",
      "content": "## 第一章 公司概况\n\n本公司成立于...",
      "content_type": "heading",
      "page": 1,
      "metadata": {
        "file_hash": "a1b2c3d4e5f6...",
        "author": "张三",
        "created": "2024-06-01"
      }
    },
    {
      "source": "/absolute/path/子目录/年报2024.pdf",
      "content": "| 指标 | 2023 | 2024 |\n|---|---|---|\n| 营收 | 100亿 | 120亿 |",
      "content_type": "table",
      "page": 5,
      "metadata": { "file_hash": "a1b2c3d4e5f6..." }
    }
  ],
  "chunks": [
    {
      "content": "## 第一章 公司概况\n\n本公司成立于...",
      "source": "/absolute/path/子目录/年报2024.pdf",
      "chunk_index": 0,
      "metadata": {}
    },
    {
      "content": "## 第二章 财务数据\n\n| 指标 | 2023 | 2024 |...",
      "source": "/absolute/path/子目录/年报2024.pdf",
      "chunk_index": 1,
      "metadata": {}
    }
  ]
}
```

#### 顶层字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | `string` | 源文件相对路径（相对于输入目录） |
| `file_hash` | `string` | 文件内容的 SHA-256 哈希值，可用于判断文件是否变更、去重 |
| `success` | `boolean` | 解析是否成功 |
| `error` | `string \| null` | 失败时的错误信息；成功时为 `null` |
| `documents` | `array` | 解析出的**内容块**列表（见下文） |
| `chunks` | `array` | 面向 RAG 检索的**分块**列表（见下文） |

#### `documents[]` — 内容块

每个 document 是文档中的一个逻辑片段（段落、标题、表格、图片描述等）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `source` | `string` | 源文件**绝对路径** |
| `content` | `string` | 提取出的内容，**Markdown 格式** |
| `content_type` | `string` | 内容块类型（见下方枚举值） |
| `page` | `int \| null` | 页码（PDF）或幻灯片编号（PPTX），其他格式可能为 `null` |
| `sheet` | `string \| null` | Excel 工作表名称，仅 `.xlsx` 文件有此字段 |
| `metadata` | `object` | 附加元数据（file_hash、author、created 等） |

**`content_type` 枚举值：**

| 值 | 含义 |
|----|------|
| `heading` | 标题 |
| `paragraph` | 段落 |
| `table` | 表格（Markdown 表格语法） |
| `list` | 列表 |
| `image` | 图片描述（由多模态 AI 生成） |
| `code` | 代码块 |
| `unknown` | 未识别类型 |

#### `chunks[]` — RAG 分块

每个 chunk 是经过分块策略处理后的文本片段，适合直接输入向量数据库或检索引擎。

| 字段 | 类型 | 说明 |
|------|------|------|
| `content` | `string` | 分块后的文本内容 |
| `source` | `string` | 来源文件路径 |
| `chunk_index` | `int` | 分块序号（从 0 开始） |
| `metadata` | `object` | 附加元数据 |

**分块策略（可通过 `config.yaml` 配置）：**

- `heading`（默认）：按 Markdown 标题层级切分，遇到新标题即开始新 chunk
- `fixed_token`：按固定 token 数切分，带重叠窗口（默认 max_tokens=512, overlap=50）

### Markdown 结构

同目录下会生成 `{文件名}.md`，内容为所有 `documents[].content` 的顺序拼接：

```markdown
<!-- source: 子目录/年报2024.pdf -->

## 第一章 公司概况

本公司成立于...

| 指标 | 2023 | 2024 |
|---|---|---|
| 营收 | 100亿 | 120亿 |

...
```

- 文件头有 `<!-- source: 相对路径 -->` 注释标记来源
- Markdown 文件适合**人工阅读与审校**
- JSON 文件适合**程序消费与检索**

### 特殊情况处理

| 情况 | 处理方式 |
|------|----------|
| **图片文件**（jpg/png/gif/webp/tiff/bmp） | 通过多模态 AI（如 Gemini）生成描述；`content_type` 为 `"image"` |
| **图多字少的文档**（PDF/DOCX/PPTX 中嵌入大量图片） | 预扫描检测，自动调用多模态 AI 描述嵌入图片 |
| **`.txt` 纯文本** | 直接复制原文；`metadata` 中标记 `"direct_copy": true` |
| **解析失败** | `success: false`，`error` 包含具体错误，`documents` 和 `chunks` 为 `[]` |

### 下游消费建议

| 场景 | 建议做法 |
|------|----------|
| **RAG / 向量检索** | 直接使用 `chunks[]` 数组，每个 chunk 的 `content` 作为检索单元 |
| **全文分析** | 遍历 `documents[]`，拼接 `content` 还原完整文档 |
| **增量更新** | 比对 `file_hash` 判断文件是否变更，避免重复处理 |
| **质量过滤** | 检查 `success` 字段，筛除解析失败的文件 |
| **按类型处理** | 利用 `content_type` 字段，针对表格、图片等做差异化处理 |
| **分页定位** | 使用 `page` 字段回溯内容在原文档中的位置 |

---

## 项目结构

```
omniparser/
├── __init__.py
├── cli.py                  # CLI 入口
├── config.py               # 全局配置
├── pipeline.py             # 管道调度器
├── models.py               # 数据模型 (Document, Chunk)
├── parsers/                # 解析器插件
│   ├── __init__.py
│   ├── base.py             # BaseParser 抽象类
│   ├── docx_parser.py
│   ├── xlsx_parser.py
│   ├── pptx_parser.py
│   ├── pdf_parser.py
│   └── image_parser.py
├── postprocessors/         # 后处理器
│   ├── __init__.py
│   ├── chunker.py          # 分块策略
│   └── metadata.py         # 元数据提取
├── cache.py                # 文件 hash 缓存
└── utils.py                # 工具函数
```

---

## 快速开始

```bash
# 安装
pip install -e .

# 解析单个文件
omniparser parse report.pdf -o output/

# 批量解析目录
omniparser parse ./documents/ -o output/ --recursive

# 指定输出格式
omniparser parse ./documents/ -o output/ --format json
```

### 批量转换

```bash
# 同时输出 JSON 和 Markdown（默认）
omniparser batch -i /path/to/source/ -o /path/to/output/

# 仅输出 JSON
omniparser batch -i /path/to/source/ -o /path/to/output/ -f json

# 指定 API 并发数
omniparser batch -i /path/to/source/ -o /path/to/output/ --workers 8
```

输出目录结构示例：

```
output/
├── 子目录A/
│   ├── 报告.json
│   ├── 报告.md
│   ├── 数据表.json
│   └── 数据表.md
├── 子目录B/
│   ├── 演示文稿.json
│   ├── 演示文稿.md
│   ├── photo.json
│   └── photo.md
└── readme.txt.json
```

---

## 配置

通过 `config.yaml` 或环境变量配置：

```yaml
# config.yaml
cache:
  enabled: true
  dir: .omniparser_cache

image:
  provider: gemini          # gemini / openai
  api_key: ${GEMINI_API_KEY}
  prompt: "请详细描述这张图片的内容。如果是图表，请提取其中的数据；如果是照片，请描述场景和主要物体。最后请以 Markdown 格式输出。"

chunking:
  strategy: heading         # heading / fixed_token
  max_tokens: 512
  overlap: 50

pdf:
  ocr_threshold: 0.3        # 文本提取率低于此值时降级到 OCR
```
