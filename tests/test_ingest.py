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


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 审计日志隔离测试(UAT Big-Loop: 测试不得污染真实 wiki/log.md)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ingest_audit_log_stays_in_project_dir(
    patch_ingest_paths, project_dir, tmp_path
):
    """RED 守卫:ingest 的审计日志必须写进 sandbox(project_dir/wiki/log.md),
    而不是真实仓库的 wiki/log.md。

    根因:scripts/logger.py 的 global_logger 用相对路径 "wiki/log.md",
    按 CWD 解析;patch_ingest_paths 此前只 patch 路径常量,未 patch logger,
    导致 ingest_file() 内 `from scripts.logger import global_logger` 写到真实仓库。
    """
    source = tmp_path / "audit_isolation.md"
    source.write_text("# 审计隔离\n\n岸桥远控测试内容。", encoding="utf-8")

    meta = patch_ingest_paths.ingest_file(source)

    assert meta is not None
    sandbox_log = project_dir / "wiki" / "log.md"
    assert sandbox_log.exists()
    assert "audit_isolation" in sandbox_log.read_text(encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. E005 Task 6: 纯摄入准备边界 (prepare_ingest / publish_prepared_ingest)
# ═══════════════════════════════════════════════════════════════════════════════

class TestPrepareIngestPurity:
    """prepare_ingest 是纯准备：解析 + 哈希 + doc_id + 候选 meta，
    不得写 originals/ raw/ wiki/ meta/ 任何业务目录。"""

    def test_prepare_ingest_returns_candidate_without_writing_business_directories(self, tmp_path):
        from scripts.ingest import prepare_ingest
        staged = tmp_path / "upload.txt"
        staged.write_text("港口数字化测试", encoding="utf-8")
        prepared = prepare_ingest(staged, base_dir=tmp_path, existing_doc_ids=set())
        assert prepared.doc_id.startswith("doc_")
        assert prepared.text_bytes.decode("utf-8") == "港口数字化测试"
        assert not (tmp_path / "originals").exists()
        assert not (tmp_path / "raw").exists()
        assert not (tmp_path / "wiki").exists()
        assert not (tmp_path / "meta").exists()

    def test_prepared_ingest_is_frozen_dataclass(self, tmp_path):
        import dataclasses
        from scripts.ingest import prepare_ingest
        staged = tmp_path / "frozen.txt"
        staged.write_text("冻结测试", encoding="utf-8")
        prepared = prepare_ingest(staged, base_dir=tmp_path, existing_doc_ids=set())
        assert dataclasses.is_dataclass(prepared)
        with pytest.raises(dataclasses.FrozenInstanceError):
            prepared.doc_id = "doc_19990101_999"

    def test_prepare_ingest_rejects_unsupported_suffix(self, tmp_path):
        from scripts.ingest import prepare_ingest
        staged = tmp_path / "upload.xyz"
        staged.write_text("data", encoding="utf-8")
        with pytest.raises(ValueError):
            prepare_ingest(staged, base_dir=tmp_path, existing_doc_ids=set())
        assert not (tmp_path / "raw").exists()


class TestPrepareIngestDocIdAllocation:
    """existing_doc_ids 提供时：取今日 max seq + 1，
    覆盖中间删除号与不连续编号，且不复用。"""

    @staticmethod
    def _today() -> str:
        from datetime import datetime
        from scripts.ingest import TZ_CST
        return datetime.now(TZ_CST).strftime("%Y%m%d")

    def _prepare(self, tmp_path, existing_doc_ids):
        from scripts.ingest import prepare_ingest
        staged = tmp_path / "alloc.txt"
        staged.write_text("序号分配测试", encoding="utf-8")
        return prepare_ingest(staged, base_dir=tmp_path,
                              existing_doc_ids=existing_doc_ids)

    def test_empty_set_starts_at_001(self, tmp_path):
        today = self._today()
        prepared = self._prepare(tmp_path, set())
        assert prepared.doc_id == f"doc_{today}_001"

    def test_allocates_max_seq_plus_one(self, tmp_path):
        today = self._today()
        prepared = self._prepare(
            tmp_path, {f"doc_{today}_001", f"doc_{today}_002"})
        assert prepared.doc_id == f"doc_{today}_003"

    def test_deleted_middle_number_not_reused(self, tmp_path):
        """002 已被删除：{001, 003} → 分配 004，不复用 002"""
        today = self._today()
        prepared = self._prepare(
            tmp_path, {f"doc_{today}_001", f"doc_{today}_003"})
        assert prepared.doc_id == f"doc_{today}_004"

    def test_non_continuous_ids(self, tmp_path):
        """编号不连续：{005} → 006"""
        today = self._today()
        prepared = self._prepare(tmp_path, {f"doc_{today}_005"})
        assert prepared.doc_id == f"doc_{today}_006"

    def test_other_days_ignored(self, tmp_path):
        today = self._today()
        prepared = self._prepare(tmp_path, {"doc_19990101_007"})
        assert prepared.doc_id == f"doc_{today}_001"

    def test_none_existing_doc_ids_keeps_directory_scan(
        self, patch_ingest_paths, project_dir, tmp_path
    ):
        """existing_doc_ids=None（CLI 路径）保持旧的目录扫描行为：
        扫描 raw/ 下今日 meta 数量 + 1。"""
        today = self._today()
        staged = tmp_path / "scan.txt"
        staged.write_text("目录扫描测试", encoding="utf-8")
        p1 = patch_ingest_paths.prepare_ingest(staged)
        assert p1.doc_id == f"doc_{today}_001"
        # 模拟已存在的今日 meta（含空洞扫描语义与原有一致：按数量而非 max）
        raw_dir = project_dir / "raw"
        (raw_dir / f"{p1.doc_id}.meta.yaml").write_text(
            "id: " + p1.doc_id, encoding="utf-8")
        p2 = patch_ingest_paths.prepare_ingest(staged)
        assert p2.doc_id == f"doc_{today}_002"
        # 目录扫描不得写入任何文件：raw/ 中只有本测试手动创建的 meta
        assert [p.name for p in raw_dir.iterdir()] == [f"{p1.doc_id}.meta.yaml"]


class TestPublishPreparedIngest:
    """publish_prepared_ingest 按旧 _write_meta 合同写 raw/{doc_id}.txt
    与 raw/{doc_id}.meta.yaml。"""

    LEGACY_META_KEYS = {
        "id", "title", "source_type", "source_original", "source_url",
        "ingested_at", "file_hash", "char_count", "language",
        "status", "error_message",
    }

    def test_prepare_publish_round_trip_matches_legacy_meta_shape(self, tmp_path):
        from scripts.ingest import prepare_ingest, publish_prepared_ingest
        content = "岸桥远控 round-trip 测试内容"
        staged = tmp_path / "roundtrip.txt"
        staged.write_text(content, encoding="utf-8")

        prepared = prepare_ingest(staged, base_dir=tmp_path, existing_doc_ids=set())
        # 发布前仍无业务目录
        assert not (tmp_path / "raw").exists()

        meta = publish_prepared_ingest(prepared, staged, base_dir=tmp_path)

        raw_dir = tmp_path / "raw"
        txt_path = raw_dir / f"{prepared.doc_id}.txt"
        meta_path = raw_dir / f"{prepared.doc_id}.meta.yaml"
        assert txt_path.exists()
        assert meta_path.exists()
        assert txt_path.read_text(encoding="utf-8") == content

        with open(meta_path, "r", encoding="utf-8") as f:
            disk_meta = yaml.safe_load(f)
        assert set(disk_meta) == self.LEGACY_META_KEYS
        assert disk_meta == meta
        for key in self.LEGACY_META_KEYS - {"ingested_at"}:
            assert disk_meta[key] == prepared.meta[key]
        assert disk_meta["status"] == "raw"
        assert disk_meta["error_message"] == ""
        assert disk_meta["source_url"] == ""
        assert disk_meta["file_hash"].startswith("sha256:")
        assert disk_meta["char_count"] == len(content)
        assert disk_meta["language"] == "zh-CN"
        assert disk_meta["source_original"] == f"originals/{staged.name}"


class TestIngestFileComposition:
    """ingest_file 由 prepare + publish 组合，保持全部 CLI 可观察行为。"""

    def test_ingest_file_keeps_cli_publish_contract(self, patch_ingest_paths, tmp_path):
        source = tmp_path / "originals" / "a.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("content", encoding="utf-8")
        meta = patch_ingest_paths.ingest_file(source, base_dir=tmp_path)
        assert (tmp_path / "raw" / f"{meta['id']}.txt").exists()
        assert (tmp_path / "raw" / f"{meta['id']}.meta.yaml").exists()

    def test_ingest_file_empty_text_publishes_nothing(self, patch_ingest_paths, tmp_path):
        source = tmp_path / "empty.txt"
        source.write_text("   \n  ", encoding="utf-8")
        assert patch_ingest_paths.ingest_file(source, base_dir=tmp_path) is None
        # raw/ 由 project_dir fixture 创建；其中不得有任何摄入产物
        assert list((tmp_path / "raw").iterdir()) == []

    def test_ingest_file_parse_failure_writes_error_meta(self, patch_ingest_paths, tmp_path):
        """解析失败：写 error meta 并返回 None（docx 内容损坏可确定性触发）。"""
        source = tmp_path / "broken.docx"
        source.write_bytes(b"this is not a real docx file")
        result = patch_ingest_paths.ingest_file(source, base_dir=tmp_path)
        assert result is None
        metas = list((tmp_path / "raw").glob("*.meta.yaml"))
        assert len(metas) == 1
        with open(metas[0], "r", encoding="utf-8") as f:
            disk_meta = yaml.safe_load(f)
        assert disk_meta["status"] == "error"
        assert disk_meta["error_message"]
        assert disk_meta["char_count"] == 0
        # 失败路径不得留下 txt 产物
        assert not list((tmp_path / "raw").glob("*.txt"))
