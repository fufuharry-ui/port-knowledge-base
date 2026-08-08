"""
scripts/ingest.py — 文档摄入脚本
将原始文件从 originals/ 解析为标准化纯文本，并生成 metadata。
用法: python scripts/ingest.py [文件路径或目录]
"""

import os
import sys
import hashlib
import re
from dataclasses import dataclass
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


def generate_doc_id(raw_dir: Path | None = None) -> str:
    """生成唯一 doc_id，格式: doc_{YYYYMMDD}_{seq:03d}

    目录扫描行为保持不变：按 raw_dir 下今日 meta 文件数量 + 1。
    """
    today = datetime.now(TZ_CST).strftime("%Y%m%d")
    raw_dir = RAW_DIR if raw_dir is None else raw_dir
    existing = [
        f.stem for f in raw_dir.glob(f"doc_{today}_*.meta.yaml")
    ] if raw_dir.exists() else []
    seq = len(existing) + 1
    return f"doc_{today}_{seq:03d}"


def _allocate_doc_id(existing_doc_ids: set[str]) -> str:
    """基于已知 doc_id 集合分配今日下一个序号：max seq + 1。

    中间删除号与不连续编号均不复用；非今日或格式不符的 id 忽略。
    """
    today = datetime.now(TZ_CST).strftime("%Y%m%d")
    prefix = f"doc_{today}_"
    max_seq = 0
    for doc_id in existing_doc_ids:
        if not doc_id.startswith(prefix):
            continue
        try:
            seq = int(doc_id[len(prefix):])
        except ValueError:
            continue
        if seq > max_seq:
            max_seq = seq
    return f"{prefix}{max_seq + 1:03d}"


def _resolve_business_dirs(base_dir: Path | None) -> tuple[Path, Path]:
    """返回 (raw_dir, index_file)。base_dir=None 时使用模块级路径常量。"""
    if base_dir is None:
        return RAW_DIR, INDEX_FILE
    base = Path(base_dir)
    return base / "raw", base / "wiki" / "index.yaml"


def load_index(index_file: Path | None = None) -> dict:
    index_file = INDEX_FILE if index_file is None else index_file
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
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

@dataclass(frozen=True)
class PreparedIngest:
    """纯摄入准备产物：候选 doc_id、文本字节与 meta，尚未写入任何业务目录。"""
    doc_id: str
    original_name: str
    source_type: str
    file_hash: str
    text_bytes: bytes
    meta: dict[str, object]


class IngestParseError(Exception):
    """解析失败。携带已分配的 doc_id 与 file_hash，供发布方按旧合同写 error meta。"""

    def __init__(self, doc_id: str, file_hash: str, original: Exception):
        super().__init__(str(original))
        self.doc_id = doc_id
        self.file_hash = file_hash
        self.original = original


def prepare_ingest(staged_source: Path, *, base_dir: Path | None = None,
                   existing_doc_ids: set[str] | None = None) -> PreparedIngest:
    """纯准备：解析暂存文件、计算哈希/语言/字符数、分配 doc_id、构建候选 meta。

    不写 originals/、raw/、wiki/ 或 meta/ 任何业务目录；不更新索引、本体、
    关系或编译状态。解析失败抛出 IngestParseError；不支持的格式抛 ValueError。
    """
    staged_source = Path(staged_source)
    suffix = staged_source.suffix.lower()
    if suffix not in PARSERS:
        raise ValueError(f"不支持的文件格式: {staged_source.name}")

    raw_dir, _ = _resolve_business_dirs(base_dir)

    file_hash = get_file_hash(staged_source)
    if existing_doc_ids is None:
        # CLI 路径：保持旧的目录扫描行为
        doc_id = generate_doc_id(raw_dir)
    else:
        doc_id = _allocate_doc_id(existing_doc_ids)

    try:
        parser = PARSERS[suffix]
        text = parser(staged_source)
    except Exception as e:
        raise IngestParseError(doc_id, file_hash, e) from e

    language = detect_language(text)
    char_count = len(text)
    meta = _build_meta(doc_id, staged_source, file_hash, char_count, language)

    return PreparedIngest(
        doc_id=doc_id,
        original_name=staged_source.name,
        source_type=suffix.lstrip("."),
        file_hash=file_hash,
        text_bytes=text.encode("utf-8"),
        meta=meta,
    )


def publish_prepared_ingest(prepared: PreparedIngest, source_path: Path, *,
                            base_dir: Path | None = None) -> dict:
    """按旧 _write_meta 合同发布：写 raw/{doc_id}.txt 与 raw/{doc_id}.meta.yaml。

    source_path 为暂存源文件路径（CLI 场景下已在 originals/ 中），当前仅用于
    调用方上下文，本函数不移动或复制源文件。
    """
    raw_dir, _ = _resolve_business_dirs(base_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    txt_path = raw_dir / f"{prepared.doc_id}.txt"
    with open(txt_path, "wb") as f:
        f.write(prepared.text_bytes)

    meta_path = raw_dir / f"{prepared.doc_id}.meta.yaml"
    with open(meta_path, "w", encoding="utf-8") as f:
        yaml.dump(prepared.meta, f, allow_unicode=True, sort_keys=False)

    return prepared.meta


def ingest_file(file_path: Path, *, base_dir: Path | None = None) -> dict | None:
    """摄入单个文件，返回摄入结果 metadata 或 None（跳过）。

    由 prepare_ingest + publish_prepared_ingest 组合而成，CLI 可观察行为不变。
    """
    suffix = file_path.suffix.lower()
    if suffix not in PARSERS:
        print(f"[SKIP] 不支持的文件格式: {file_path.name}")
        return None

    print(f"[INGEST] 处理文件: {file_path.name}")

    raw_dir, index_file = _resolve_business_dirs(base_dir)

    # 1. 哈希校验去重
    file_hash = get_file_hash(file_path)
    index = load_index(index_file)
    if is_duplicate(file_hash, index):
        print(f"[SKIP] 重复文档（哈希已存在）: {file_path.name}")
        return None

    # 2. 纯准备：解析 + doc_id + 候选 meta（不写业务目录）
    try:
        prepared = prepare_ingest(file_path, base_dir=base_dir)
    except IngestParseError as e:
        print(f"[ERROR] 解析失败: {file_path.name} — {e.original}")
        # 写入 error metadata
        _write_meta(e.doc_id, file_path, e.file_hash, 0, "zh-CN",
                    status="error", error_message=str(e.original),
                    raw_dir=raw_dir)
        return None

    if not prepared.text_bytes.decode("utf-8").strip():
        print(f"[WARN] 文档内容为空: {file_path.name}")
        return None

    # 3. 发布纯文本与 metadata
    meta = publish_prepared_ingest(prepared, file_path, base_dir=base_dir)

    from scripts.logger import global_logger
    global_logger.log(
        action="ingest",
        target=file_path.stem,
        details=f"Files created: raw/{prepared.doc_id}.txt, raw/{prepared.doc_id}.meta.yaml"
    )

    print(f"[OK] 摄入完成: {prepared.doc_id} ({meta['char_count']} 字符)")
    return meta


def _build_meta(doc_id: str, source: Path, file_hash: str,
                char_count: int, language: str,
                status: str = "raw",
                error_message: str = "") -> dict:
    return {
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


def _write_meta(doc_id: str, source: Path, file_hash: str,
                char_count: int, language: str,
                status: str = "raw",
                error_message: str = "",
                raw_dir: Path | None = None) -> dict:
    meta = _build_meta(doc_id, source, file_hash, char_count, language,
                       status=status, error_message=error_message)
    raw_dir = RAW_DIR if raw_dir is None else raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    meta_path = raw_dir / f"{doc_id}.meta.yaml"
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
