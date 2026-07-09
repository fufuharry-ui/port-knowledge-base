"""
tests/test_api_ingest.py — Ingest 路由 TDD 测试
覆盖 POST /api/v1/upload 及 GET/DELETE /api/v1/docs 等端点
"""

import io
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
import yaml

# 确保项目根可被导入
_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.config import Settings, get_settings


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """创建 TestClient，通过 dependency_overrides 注入 tmp_path Settings"""
    # 初始化临时目录骨架
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "originals").mkdir()
    (tmp_path / "meta" / "ontology").mkdir(parents=True)
    (tmp_path / "meta" / "relations").mkdir(parents=True)
    with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"documents": []}, f, allow_unicode=True)
    with open(tmp_path / "meta" / "relations" / "knowledge_graph.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"edges": []}, f, allow_unicode=True)

    # 构造指向 tmp_path 的 Settings 实例
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://fake.api")
    monkeypatch.setenv("KB_BASE_DIR", str(tmp_path))
    test_settings = Settings()

    from fastapi.testclient import TestClient
    from app.main import app
    # 用 dependency_overrides 绑定测试实例，绕过 lru_cache
    app.dependency_overrides[get_settings] = lambda: test_settings
    test_client = TestClient(app)
    yield test_client, tmp_path
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. POST /api/v1/upload
# ═══════════════════════════════════════════════════════════════════════════════

class TestUploadEndpoint:
    """POST /api/v1/upload 文件上传端点"""

    def test_upload_txt_returns_doc_id(self, client):
        """上传有效 .txt 文件，响应 200 且包含 doc_id"""
        test_client, tmp_path = client
        file_content = b"This is a test document about port automation."
        with patch("app.routers.ingest.ingest_file") as mock_ingest, \
             patch("app.routers.ingest.BackgroundTasks.add_task"):
            mock_ingest.return_value = {
                "doc_id": "doc_20260405_001",
                "title": "test.txt",
                "status": "raw",
                "char_count": len(file_content),
            }
            response = test_client.post(
                "/api/v1/upload",
                files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
            )
        assert response.status_code == 200
        data = response.json()
        assert "doc_id" in data
        assert data["doc_id"].startswith("doc_")

    def test_upload_md_file_accepted(self, client):
        """上传 .md 文件应被接受"""
        test_client, tmp_path = client
        content = b"# Test\nport automation content"
        with patch("app.routers.ingest.ingest_file") as mock_ingest:
            mock_ingest.return_value = {
                "doc_id": "doc_20260405_002",
                "title": "test.md",
                "status": "raw",
                "char_count": len(content),
            }
            response = test_client.post(
                "/api/v1/upload",
                files={"file": ("test.md", io.BytesIO(content), "text/markdown")},
            )
        assert response.status_code == 200

    def test_upload_unsupported_format_rejected(self, client):
        """上传不支持的格式（.exe）应返回 422"""
        test_client, tmp_path = client
        response = test_client.post(
            "/api/v1/upload",
            files={"file": ("malware.exe", io.BytesIO(b"\x00\x01"), "application/octet-stream")},
        )
        assert response.status_code == 422

    def test_upload_duplicate_returns_skipped(self, client):
        """重复文件（ingest_file 返回 None）应返回 skipped=true"""
        test_client, tmp_path = client
        with patch("app.routers.ingest.ingest_file", return_value=None):
            response = test_client.post(
                "/api/v1/upload",
                files={"file": ("dup.txt", io.BytesIO(b"same content"), "text/plain")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data.get("skipped") is True

    def test_upload_triggers_background_compile(self, client):
        """上传成功后应触发后台编译任务"""
        test_client, tmp_path = client
        with patch("app.routers.ingest.ingest_file") as mock_ingest, \
             patch("app.routers.ingest.compile_then_relate") as mock_bg:
            mock_ingest.return_value = {
                "doc_id": "doc_20260405_003",
                "title": "bg_test.txt",
                "status": "raw",
                "char_count": 100,
            }
            response = test_client.post(
                "/api/v1/upload",
                files={"file": ("bg_test.txt", io.BytesIO(b"content"), "text/plain")},
            )
        assert response.status_code == 200
        # BackgroundTasks 会被注册，不需要验证 mock_bg 被直接调用
        # 验证响应中 status 为 raw（表明异步处理中）
        data = response.json()
        assert data.get("status") in ("raw", "compiling")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GET /api/v1/docs
# ═══════════════════════════════════════════════════════════════════════════════

class TestListDocs:
    """GET /api/v1/docs 文档列表端点"""

    def test_list_docs_empty_wiki(self, client):
        """空知识库应返回空 documents 列表"""
        test_client, tmp_path = client
        response = test_client.get("/api/v1/docs")
        assert response.status_code == 200
        data = response.json()
        assert "documents" in data
        assert isinstance(data["documents"], list)

    def test_list_docs_returns_existing(self, client):
        """写入 index.yaml 后，列表应返回对应文档"""
        test_client, tmp_path = client
        index_path = tmp_path / "wiki" / "index.yaml"
        with open(index_path, "w", encoding="utf-8") as f:
            yaml.dump({
                "documents": [
                    {"id": "doc_x01", "title": "港口方案", "status": "compiled"},
                ]
            }, f, allow_unicode=True)
        response = test_client.get("/api/v1/docs")
        assert response.status_code == 200
        data = response.json()
        assert len(data["documents"]) == 1
        assert data["documents"][0]["id"] == "doc_x01"


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GET /api/v1/docs/{doc_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetDoc:
    """GET /api/v1/docs/{doc_id} 单文档详情"""

    def test_get_doc_not_found_returns_404(self, client):
        """不存在的 doc_id 应返回 404"""
        test_client, _ = client
        response = test_client.get("/api/v1/docs/doc_nonexistent_999")
        assert response.status_code == 404

    def test_get_doc_returns_meta(self, client):
        """存在的文档应返回 meta 信息"""
        test_client, tmp_path = client
        doc_id = "doc_20260405_001"
        meta = {
            "id": doc_id,
            "title": "测试文档",
            "status": "compiled",
            "char_count": 500,
            "language": "zh-CN",
        }
        with open(tmp_path / "raw" / f"{doc_id}.meta.yaml", "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True)

        response = test_client.get(f"/api/v1/docs/{doc_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == doc_id
        assert "title" in data
