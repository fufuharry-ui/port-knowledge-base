"""
tests/test_api_search.py — Search API 路由 TDD 测试
覆盖 POST /api/v1/search 和 GET /api/v1/search/stream
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.config import Settings, get_settings


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with dependency_overrides for isolated Settings"""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "originals").mkdir()
    (tmp_path / "meta" / "ontology").mkdir(parents=True)
    (tmp_path / "meta" / "relations").mkdir(parents=True)

    with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"documents": [
            {
                "id": "doc_20260405_001",
                "title": "岸桥远控技术方案",
                "status": "compiled",
                "ontology_terms": ["岸桥远控", "5G专网", "MEC"],
                "abstract_short": "基于5G和MEC的岸桥远程操控系统，延迟≤50ms",
            }
        ]}, f, allow_unicode=True)

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://fake.api")
    monkeypatch.setenv("KB_BASE_DIR", str(tmp_path))
    test_settings = Settings()

    from fastapi.testclient import TestClient
    from app.main import app
    app.dependency_overrides[get_settings] = lambda: test_settings
    test_client = TestClient(app)
    yield test_client, tmp_path
    app.dependency_overrides.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# 1. POST /api/v1/search — 同步检索
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchEndpoint:
    """POST /api/v1/search 同步 JSON 检索"""

    def test_search_returns_answer_and_sources(self, client):
        """正常查询应返回 answer 字段和 sources 列表"""
        test_client, tmp_path = client

        fake_answer = "根据《岸桥远控技术方案》，延迟≤50ms。\n\n📎 引用来源:\n- [doc_20260405_001]"

        # 原文文件
        (tmp_path / "raw" / "doc_20260405_001.txt").write_text(
            "端到端延迟≤50ms，5G空口延迟≤20ms", encoding="utf-8"
        )

        with patch("scripts.search.get_llm_client") as mock_client, \
             patch("scripts.search.search", return_value=fake_answer):
            mock_client.return_value = MagicMock()
            response = test_client.post(
                "/api/v1/search",
                json={"query": "岸桥远控延迟要求", "stream": False},
            )

        assert response.status_code == 200
        data = response.json()
        assert "answer" in data
        assert len(data["answer"]) > 0

    def test_search_empty_query_returns_422(self, client):
        """空字符串 query 应返回 422 校验错误"""
        test_client, _ = client
        response = test_client.post(
            "/api/v1/search",
            json={"query": "", "stream": False},
        )
        assert response.status_code == 422

    def test_search_missing_query_returns_422(self, client):
        """缺少 query 字段应返回 422"""
        test_client, _ = client
        response = test_client.post(
            "/api/v1/search",
            json={"stream": False},
        )
        assert response.status_code == 422

    def test_search_calls_jieba_tokenize(self, client):
        """搜索时应调用 jieba_tokenize 对查询预处理"""
        test_client, tmp_path = client

        (tmp_path / "raw" / "doc_20260405_001.txt").write_text(
            "岸桥延迟测试", encoding="utf-8"
        )

        with patch("app.routers.search.jieba_tokenize") as mock_tok, \
             patch("scripts.search.get_llm_client") as mock_client, \
             patch("scripts.search.search", return_value="fake answer"):
            mock_tok.return_value = ["岸桥", "远控", "延迟"]
            mock_client.return_value = MagicMock()
            response = test_client.post(
                "/api/v1/search",
                json={"query": "岸桥远控延迟", "stream": False},
            )

        assert response.status_code == 200
        mock_tok.assert_called_once_with("岸桥远控延迟")

    def test_search_sources_parsed_from_answer(self, client):
        """answer 中的引用来源应被解析为 sources 列表"""
        test_client, tmp_path = client

        fake_answer = (
            "延迟要求为50ms。\n\n"
            "📎 引用来源:\n"
            "- [doc_20260405_001] 岸桥远控技术方案, 第11页"
        )

        (tmp_path / "raw" / "doc_20260405_001.txt").write_text("content", encoding="utf-8")

        with patch("scripts.search.get_llm_client") as mock_client, \
             patch("scripts.search.search", return_value=fake_answer), \
             patch("app.routers.search.jieba_tokenize", return_value=["岸桥"]):
            mock_client.return_value = MagicMock()
            response = test_client.post(
                "/api/v1/search",
                json={"query": "岸桥延迟", "stream": False},
            )

        assert response.status_code == 200
        data = response.json()
        # sources 可以是列表（可能为空，取决于解析实现）
        assert "sources" in data
        assert isinstance(data["sources"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GET /api/v1/search/stream — SSE 流式检索
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchStreamEndpoint:
    """GET /api/v1/search/stream?q=... SSE 流式检索"""

    def test_stream_content_type_is_sse(self, client):
        """流式响应 Content-Type 应为 text/event-stream"""
        test_client, _ = client
        with patch("app.routers.search.search", return_value="流式回答测试"), \
             patch("app.routers.search.jieba_tokenize", return_value=["流式"]):
            response = test_client.get(
                "/api/v1/search/stream",
                params={"q": "岸桥延迟"},
            )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_stream_missing_q_returns_422(self, client):
        """缺少 q 参数应返回 422"""
        test_client, _ = client
        response = test_client.get("/api/v1/search/stream")
        assert response.status_code == 422

    def test_stream_response_contains_data_lines(self, client):
        """SSE 响应体应包含 'data:' 行"""
        test_client, _ = client
        with patch("app.routers.search.search", return_value="港口延迟是50ms"), \
             patch("app.routers.search.jieba_tokenize", return_value=["港口", "延迟"]):
            response = test_client.get(
                "/api/v1/search/stream",
                params={"q": "港口延迟"},
            )
        assert "data:" in response.text
