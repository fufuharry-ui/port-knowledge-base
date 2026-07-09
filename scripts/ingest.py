"""
scripts/ingest.py — 文档摄入脚本
将原始文件从 originals/ 解析为标准化纯文本，并生成 metadata。
用法: python scripts/ingest.py [文件路径或目录]
"""

import os
import sys
import hashlib
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

# ─── 路径配置 ───────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
RAW_DIR = BASE_DIR / "raw"
ORIGINALS_DIR = BASE_DIR / "originals"
WIKI_DIR = BASE_DIR / "wiki"
INDEX_FILE = WIKI_DIR / "index.yaml"

TZ_CST = timezone(timedelta(hours=8))


# ─── 工具函数 ───────────────────────────────────────────────────────────────

def get_file_hash(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            sha.update(chunk)
    return f"sha256:{sha.hexdigest()}"


def generate_doc_id() -> str:
    """生成唯一 doc_id，格式: doc_{YYYYMMDD}_{seq:03d}"""
    today = datetime.now(TZ_CST).strftime("%Y%m%d")
    existing = [
        f.stem for f in RAW_DIR.glob(f"doc_{today}_*.meta.yaml")
    ] if RAW_DIR.exists() else []
    seq = len(existing) + 1
    return f"doc_{today}_{seq:03d}"


def load_index() -> dict:
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"documents": []}
    return {"documents": []}


def is_duplicate(file_hash: str, index: dict) -> bool:
    return any(doc.get("file_hash") == file_hash for doc in index.get("documents", []))


def detect_language(text: str) -> str:
    """简单语言检测：按中文字符占比判断"""
    chinese = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ratio = chinese / max(len(text), 1)
    if ratio > 0.3:
        return "zh-CN"
    elif ratio > 0.05:
        return "zh-EN-mixed"
    return "en"


# ─── 各格式解析器 ────────────────────────────────────────────────────────────

def parse_pdf(path: Path) -> str:
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tables_text = ""
                for table in page.extract_tables():
                    for row in table:
                        tables_text += " | ".join(
                            str(cell or "") for cell in row
                        ) + "\n"
                pages.append(f"[第{i+1}页]\n{text}\n{tables_text}")
        return "\n\n".join(pages)
    except ImportError:
        raise RuntimeError(
            "请安装 pdfplumber: pip install pdfplumber"
        )


def parse_docx(path: Path) -> str:
    try:
        from docx import Document
        doc = Document(path)
        parts = []
        for para in doc.paragraphs:
            if para.style.name.startswith("Heading"):
                level = re.search(r"\d+", para.style.name)
                prefix = "#" * (int(level.group()) if level else 1) + " "
                parts.append(prefix + para.text)
            else:
                parts.append(para.text)
        return "\n\n".join(p for p in parts if p.strip())
    except ImportError:
        raise RuntimeError(
            "请安装 python-docx: pip install python-docx"
        )


def parse_markdown(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    # 剥除 YAML frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            content = content[end + 3:].strip()
    return content


def parse_html(path: Path) -> str:
    try:
        from readability import Document as ReadDoc
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        doc = ReadDoc(html)
        # 简单去除 HTML 标签
        text = re.sub(r"<[^>]+>", "", doc.summary())
        return text.strip()
    except ImportError:
        # 降级：直接读取，剥除标签
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
        return re.sub(r"<[^>]+>", "", html).strip()


PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_docx,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".html": parse_html,
    ".htm": parse_html,
    ".txt": lambda p: p.read_text(encoding="utf-8"),
}


# ─── 核心摄入逻辑 ────────────────────────────────────────────────────────────

def ingest_file(file_path: Path) -> dict | None:
    """摄入单个文件，返回摄入结果 metadata 或 None（跳过）"""
    suffix = file_path.suffix.lower()
    if suffix not in PARSERS:
        print(f"[SKIP] 不支持的文件格式: {file_path.name}")
        return None

    print(f"[INGEST] 处理文件: {file_path.name}")

    # 1. 哈希校验去重
    file_hash = get_file_hash(file_path)
    index = load_index()
    if is_duplicate(file_hash, index):
        print(f"[SKIP] 重复文档（哈希已存在）: {file_path.name}")
        return None

    # 2. 生成 doc_id
    doc_id = generate_doc_id()

    # 3. 解析文本
    try:
        parser = PARSERS[suffix]
        text = parser(file_path)
        if not text.strip():
            print(f"[WARN] 文档内容为空: {file_path.name}")
            return None
    except Exception as e:
        print(f"[ERROR] 解析失败: {file_path.name} — {e}")
        # 写入 error metadata
        _write_meta(doc_id, file_path, file_hash, 0, "zh-CN",
                    status="error", error_message=str(e))
        return None

    # 4. 写入纯文本
    RAW_DIR.mkdir(exist_ok=True)
    txt_path = RAW_DIR / f"{doc_id}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(text)

    language = detect_language(text)
    char_count = len(text)

    # 5. 写入 metadata
    meta = _write_meta(doc_id, file_path, file_hash, char_count, language)
    
    from scripts.logger import global_logger
    global_logger.log(
        action="ingest",
        target=file_path.stem,
        details=f"Files created: raw/{doc_id}.txt, raw/{doc_id}.meta.yaml"
    )

    print(f"[OK] 摄入完成: {doc_id} ({char_count} 字符)")
    return meta


def _write_meta(doc_id: str, source: Path, file_hash: str,
                char_count: int, language: str,
                status: str = "raw",
                error_message: str = "") -> dict:
    meta = {
        "id": doc_id,
        "title": source.stem,
        "source_type": source.suffix.lower().lstrip("."),
        "source_original": f"originals/{source.name}",
        "source_url": "",
        "ingested_at": datetime.now(TZ_CST).isoformat(),
        "file_hash": file_hash,
        "char_count": char_count,
        "language": language,
        "status": status,
        "error_message": error_message,
    }
    meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(meta, f, allow_unicode=True, sort_keys=False)
    return meta


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else [str(ORIGINALS_DIR)]
    files = []
    for t in targets:
        p = Path(t)
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            for suffix in PARSERS:
                files.extend(p.glob(f"**/*{suffix}"))
        else:
            print(f"[WARN] 路径不存在: {t}")

    if not files:
        print("没有找到可摄入的文件。请将原始文件放入 originals/ 目录。")
        return

    ingested = 0
    for f in sorted(files):
        result = ingest_file(f)
        if result:
            ingested += 1

    print(f"\n✅ 摄入完成：共处理 {len(files)} 个文件，成功摄入 {ingested} 个。")
    print("📌 下一步：运行 python scripts/compile.py 进行 LLM 编译。")


if __name__ == "__main__":
    main()
