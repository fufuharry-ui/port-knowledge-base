"""
test_ingest.py — 文档摄入模块测试
覆盖 PRD 4.1 Ingest 层的核心功能：
  - 多格式解析 (Markdown, TXT, HTML)
  - SHA256 去重
  - doc_id 生成规则
  - Metadata 格式合规性
  - 语言检测
  - 空文档/不支持格式处理
"""
import hashlib
from pathlib import Path

import pytest
import yaml

from sample_data import SAMPLE_CHINESE_TEXT, SAMPLE_ENGLISH_TEXT, SAMPLE_MIXED_TEXT


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 工具函数测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestLanguageDetection:
    """PRD 4.1: Metadata 中 language 字段的准确性"""

    def test_chinese_text(self, patch_ingest_paths):
        assert patch_ingest_paths.detect_language(SAMPLE_CHINESE_TEXT) == "zh-CN"

    def test_english_text(self, patch_ingest_paths):
        assert patch_ingest_paths.detect_language(SAMPLE_ENGLISH_TEXT) == "en"

    def test_mixed_text(self, patch_ingest_paths):
        assert patch_ingest_paths.detect_language(SAMPLE_MIXED_TEXT) == "zh-EN-mixed"

    def test_empty_text(self, patch_ingest_paths):
        assert patch_ingest_paths.detect_language("") == "en"


class TestFileHash:
    """PRD 6.3: SHA256 去重机制"""

    def test_hash_format(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello", encoding="utf-8")
        h = patch_ingest_paths.get_file_hash(f)
        assert h.startswith("sha256:")
        assert len(h) == len("sha256:") + 64  # sha256 hex = 64 chars

    def test_hash_deterministic(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("same content", encoding="utf-8")
        assert patch_ingest_paths.get_file_hash(f) == patch_ingest_paths.get_file_hash(f)

    def test_different_content_different_hash(self, patch_ingest_paths, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content A", encoding="utf-8")
        f2.write_text("content B", encoding="utf-8")
        assert patch_ingest_paths.get_file_hash(f1) != patch_ingest_paths.get_file_hash(f2)


class TestDuplicateDetection:
    """PRD 6.3: 文件入库时的哈希去重"""

    def test_not_duplicate_empty_index(self, patch_ingest_paths):
        assert not patch_ingest_paths.is_duplicate("sha256:abc123", {"documents": []})

    def test_duplicate_detected(self, patch_ingest_paths):
        index = {"documents": [{"file_hash": "sha256:abc123"}]}
        assert patch_ingest_paths.is_duplicate("sha256:abc123", index)

    def test_not_duplicate_different_hash(self, patch_ingest_paths):
        index = {"documents": [{"file_hash": "sha256:abc123"}]}
        assert not patch_ingest_paths.is_duplicate("sha256:def456", index)


class TestDocIdGeneration:
    """PRD ANTIGRAVITY.md 命名规范: doc_{YYYYMMDD}_{seq:03d}"""

    def test_doc_id_format(self, patch_ingest_paths):
        doc_id = patch_ingest_paths.generate_doc_id()
        parts = doc_id.split("_")
        assert parts[0] == "doc"
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 3  # 3-digit seq
        assert parts[1].isdigit()

    def test_doc_id_seq_increment(self, patch_ingest_paths, project_dir):
        """当同日已有文档时，seq 应递增"""
        # 生成第一个 ID
        id1 = patch_ingest_paths.generate_doc_id()
        # 创建对应的 meta 文件模拟已存在
        raw_dir = project_dir / "raw"
        (raw_dir / f"{id1}.meta.yaml").write_text("id: " + id1, encoding="utf-8")
        # 生成第二个 ID
        id2 = patch_ingest_paths.generate_doc_id()
        # seq 应该从 001 变为 002
        assert id1.endswith("001")
        assert id2.endswith("002")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 解析器测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestMarkdownParser:
    """PRD 4.1: .md 格式支持"""

    def test_parse_plain_markdown(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# 标题\n\n正文内容", encoding="utf-8")
        text = patch_ingest_paths.parse_markdown(f)
        assert "标题" in text
        assert "正文内容" in text

    def test_strip_yaml_frontmatter(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("---\ntitle: test\n---\n# 正文", encoding="utf-8")
        text = patch_ingest_paths.parse_markdown(f)
        assert "title: test" not in text
        assert "正文" in text

    def test_no_frontmatter(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("纯文本，无 frontmatter", encoding="utf-8")
        text = patch_ingest_paths.parse_markdown(f)
        assert "纯文本" in text


class TestHtmlParser:
    """PRD 4.1: 网页收藏支持"""

    def test_strip_html_tags(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("<html><body><p>港口自动化</p></body></html>", encoding="utf-8")
        text = patch_ingest_paths.parse_html(f)
        assert "港口自动化" in text
        assert "<p>" not in text

    def test_empty_html(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("<html><body></body></html>", encoding="utf-8")
        text = patch_ingest_paths.parse_html(f)
        # Should not error, may return empty or minimal text
        assert isinstance(text, str)


class TestTxtParser:
    """PRD 4.1: .txt 格式支持"""

    def test_read_txt(self, patch_ingest_paths, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("纯文本文件内容", encoding="utf-8")
        from scripts.ingest import PARSERS
        text = PARSERS[".txt"](f)
        assert "纯文本文件内容" in text


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 端到端摄入测试
# ═══════════════════════════════════════════════════════════════════════════════

class TestIngestFile:
    """PRD 3.1: 完整摄入流程"""

    def test_ingest_markdown_success(self, patch_ingest_paths, project_dir):
        """成功摄入一个 Markdown 文件，验证输出文件"""
        md_file = project_dir / "originals" / "测试文档.md"
        md_file.write_text(SAMPLE_CHINESE_TEXT, encoding="utf-8")

        result = patch_ingest_paths.ingest_file(md_file)

        assert result is not None
        assert result["status"] == "raw"
        assert result["source_type"] == "md"
        assert result["language"] == "zh-CN"
        assert result["char_count"] > 0

        # 验证输出文件存在
        doc_id = result["id"]
        assert (project_dir / "raw" / f"{doc_id}.txt").exists()
        assert (project_dir / "raw" / f"{doc_id}.meta.yaml").exists()

    def test_ingest_txt_success(self, patch_ingest_paths, project_dir):
        txt_file = project_dir / "originals" / "test.txt"
        txt_file.write_text(SAMPLE_ENGLISH_TEXT, encoding="utf-8")

        result = patch_ingest_paths.ingest_file(txt_file)

        assert result is not None
        assert result["language"] == "en"
        assert result["source_type"] == "txt"

    def test_ingest_unsupported_format_returns_none(self, patch_ingest_paths, project_dir):
        """不支持的格式应跳过"""
        f = project_dir / "originals" / "unknown.xyz"
        f.write_text("data", encoding="utf-8")
        assert patch_ingest_paths.ingest_file(f) is None

    def test_ingest_duplicate_returns_none(self, patch_ingest_paths, project_dir):
        """已存在相同哈希的文档应跳过"""
        md_file = project_dir / "originals" / "doc.md"
        md_file.write_text(SAMPLE_CHINESE_TEXT, encoding="utf-8")

        # 第一次摄入
        r1 = patch_ingest_paths.ingest_file(md_file)
        assert r1 is not None

        # 手动将 hash 写入 index 模拟已编译
        index_path = project_dir / "wiki" / "index.yaml"
        index = {"documents": [{"file_hash": r1["file_hash"]}]}
        with open(index_path, "w", encoding="utf-8") as f:
            yaml.dump(index, f, allow_unicode=True)

        # 第二次摄入应跳过
        r2 = patch_ingest_paths.ingest_file(md_file)
        assert r2 is None

    def test_ingest_empty_content_returns_none(self, patch_ingest_paths, project_dir):
        """空内容文档应跳过"""
        f = project_dir / "originals" / "empty.md"
        f.write_text("", encoding="utf-8")
        result = patch_ingest_paths.ingest_file(f)
        assert result is None


class TestMetadataFormat:
    """PRD 4.1: Metadata YAML 字段完整性验证"""

    REQUIRED_FIELDS = [
        "id", "title", "source_type", "source_original",
        "ingested_at", "file_hash", "char_count", "language", "status"
    ]

    def test_metadata_has_all_required_fields(self, patch_ingest_paths, project_dir):
        md_file = project_dir / "originals" / "complete_test.md"
        md_file.write_text(SAMPLE_CHINESE_TEXT, encoding="utf-8")

        result = patch_ingest_paths.ingest_file(md_file)
        assert result is not None

        # 从磁盘读取写入的 meta.yaml
        meta_path = project_dir / "raw" / f"{result['id']}.meta.yaml"
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = yaml.safe_load(f)

        for field in self.REQUIRED_FIELDS:
            assert field in meta, f"Metadata 缺少必需字段: {field}"

    def test_metadata_source_original_path(self, patch_ingest_paths, project_dir):
        """source_original 应指向 originals/ 下"""
        md_file = project_dir / "originals" / "my_doc.md"
        md_file.write_text(SAMPLE_CHINESE_TEXT, encoding="utf-8")
        result = patch_ingest_paths.ingest_file(md_file)
        assert result["source_original"] == "originals/my_doc.md"
