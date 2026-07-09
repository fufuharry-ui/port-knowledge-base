"""
tests/test_api_knowledge.py — Knowledge API 路由 TDD 测试
覆盖 /api/v1/graph、/api/v1/ontology、/api/v1/wiki/index、/api/v1/relate
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

_root = str(Path(__file__).parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from app.config import Settings, get_settings


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with full tmp dir scaffold"""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "originals").mkdir()
    (tmp_path / "meta" / "ontology").mkdir(parents=True)
    (tmp_path / "meta" / "relations").mkdir(parents=True)

    # 初始化 index.yaml
    with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump({
            "documents": [
                {"id": "doc_001", "title": "岸桥方案", "status": "compiled",
                 "ontology_terms": ["岸桥远控", "5G"], "abstract_short": "基础摘要"}
            ]
        }, f, allow_unicode=True)

    # 初始化 knowledge_graph.yaml
    with open(tmp_path / "meta" / "relations" / "knowledge_graph.yaml", "w", encoding="utf-8") as f:
        yaml.dump({
            "edges": [
                {"source": "doc_001", "target": "doc_002",
                 "type": "supplements", "confidence": 0.85}
            ]
        }, f, allow_unicode=True)

    # 初始化 global_ontology.yaml
    with open(tmp_path / "meta" / "ontology" / "global_ontology.yaml", "w", encoding="utf-8") as f:
        yaml.dump({
            "ontology_tree": [
                {"term": "智慧港口", "parent": None, "children": [
                    {"term": "港口自动化", "parent": "智慧港口", "children": []}
                ]}
            ],
            "total_nodes": 2,
        }, f, allow_unicode=True)

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
# 1. GET /api/v1/graph — 全局知识图谱
# ═══════════════════════════════════════════════════════════════════════════════

class TestGraphEndpoint:
    """GET /api/v1/graph 全局图谱端点"""

    def test_graph_returns_nodes_and_edges(self, client):
        """图谱响应应包含 nodes 和 edges 字段"""
        test_client, _ = client
        response = test_client.get("/api/v1/graph")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert isinstance(data["nodes"], list)
        assert isinstance(data["edges"], list)

    def test_graph_edges_from_yaml(self, client):
        """edges 应与 knowledge_graph.yaml 一致"""
        test_client, _ = client
        response = test_client.get("/api/v1/graph")
        data = response.json()
        # 已有 1 条 edge
        assert len(data["edges"]) >= 1
        edge = data["edges"][0]
        assert "source" in edge
        assert "target" in edge
        assert "type" in edge

    def test_graph_empty_kg(self, client):
        """空图谱应返回 {nodes:[], edges:[]}"""
        test_client, tmp_path = client
        # 覆盖为空图谱
        with open(tmp_path / "meta" / "relations" / "knowledge_graph.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"edges": []}, f, allow_unicode=True)

        response = test_client.get("/api/v1/graph")
        assert response.status_code == 200
        data = response.json()
        assert data["edges"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. GET /api/v1/graph/{doc_id}/relations — 单文档关系
# ═══════════════════════════════════════════════════════════════════════════════

class TestDocRelations:
    """GET /api/v1/graph/{doc_id}/relations"""

    def test_relations_existing_doc(self, client):
        """有关系文件的文档应返回关系列表"""
        test_client, tmp_path = client
        doc_id = "doc_001"
        rel_data = {
            "doc_id": doc_id,
            "relations": [
                {"target_doc_id": "doc_002", "type": "supplements",
                 "confidence": 0.85, "evidence": "补充了MEC细节"}
            ]
        }
        with open(tmp_path / "meta" / "relations" / f"{doc_id}.relations.yaml", "w", encoding="utf-8") as f:
            yaml.dump(rel_data, f, allow_unicode=True)

        response = test_client.get(f"/api/v1/graph/{doc_id}/relations")
        assert response.status_code == 200
        data = response.json()
        assert "relations" in data
        assert len(data["relations"]) == 1

    def test_relations_no_file_returns_empty(self, client):
        """无关系文件的文档应返回空 relations 列表而非 404"""
        test_client, _ = client
        response = test_client.get("/api/v1/graph/doc_no_relation/relations")
        assert response.status_code == 200
        data = response.json()
        assert data.get("relations") == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GET /api/v1/ontology — 全局本体树
# ═══════════════════════════════════════════════════════════════════════════════

class TestOntologyEndpoint:
    """GET /api/v1/ontology 全局本体端点"""

    def test_ontology_returns_tree(self, client):
        """应返回 ontology_tree 字段"""
        test_client, _ = client
        response = test_client.get("/api/v1/ontology")
        assert response.status_code == 200
        data = response.json()
        assert "ontology_tree" in data
        assert isinstance(data["ontology_tree"], list)

    def test_ontology_total_nodes(self, client):
        """应返回 total_nodes 统计"""
        test_client, _ = client
        response = test_client.get("/api/v1/ontology")
        data = response.json()
        assert "total_nodes" in data
        assert data["total_nodes"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. GET /api/v1/wiki/index — Wiki 统计快照
# ═══════════════════════════════════════════════════════════════════════════════

class TestWikiIndex:
    """GET /api/v1/wiki/index Wiki 仪表盘统计"""

    def test_wiki_index_returns_summary(self, client):
        """应返回文档数量和状态摘要"""
        test_client, _ = client
        response = test_client.get("/api/v1/wiki/index")
        assert response.status_code == 200
        data = response.json()
        assert "total_docs" in data
        assert "documents" in data

    def test_wiki_index_total_docs_accurate(self, client):
        """total_docs 应与 documents 列表长度一致"""
        test_client, _ = client
        response = test_client.get("/api/v1/wiki/index")
        data = response.json()
        assert data["total_docs"] == len(data["documents"])


# ═══════════════════════════════════════════════════════════════════════════════
# 5. POST /api/v1/relate/{doc_id} — 手动触发关系重算
# ═══════════════════════════════════════════════════════════════════════════════

class TestRelateEndpoint:
    """POST /api/v1/relate/{doc_id} 手动触发关系检测"""

    def test_relate_trigger_accepted(self, client):
        """存在摘要的文档触发关系重算应返回 202"""
        test_client, tmp_path = client
        doc_id = "doc_001"
        # 创建 summary 文件
        with open(tmp_path / "wiki" / f"{doc_id}.summary.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"doc_id": doc_id, "abstract": "测试摘要"}, f, allow_unicode=True)

        with patch("app.routers.knowledge.compile_then_relate"):
            response = test_client.post(f"/api/v1/relate/{doc_id}")

        assert response.status_code in (200, 202)

    def test_relate_no_summary_returns_404(self, client):
        """没有摘要的文档触发关系重算应返回 404"""
        test_client, _ = client
        response = test_client.post("/api/v1/relate/doc_nonexistent_zzz")
        assert response.status_code == 404
