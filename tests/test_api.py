import pytest
import yaml
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Will fail here on first run
from api.main import app

client = TestClient(app)

# 真实数据目录(与 api/main.py 的 BASE_DIR 一致)
_DATA_DIR = Path(__file__).resolve().parent.parent

@patch("api.main.ingest_and_compile_task")
def test_ingest_endpoint(mock_task):
    response = client.post(
        "/api/v1/ingest",
        files={"file": ("test.txt", b"Mock document content", "text/plain")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert "doc_id" in data
    assert mock_task.called

@patch("api.main.search")
@patch("api.main.get_llm_client")
def test_search_endpoint(mock_get_client, mock_search):
    mock_search.return_value = "Mock LLM/Search Answer"
    response = client.post(
        "/api/v1/search",
        json={"query": "test query"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Mock LLM/Search Answer"
    assert mock_search.called

@patch("api.main.Linter")
def test_lint_endpoint(mock_linter_cls):
    mock_linter_instance = mock_linter_cls.return_value

    response = client.post("/api/v1/lint")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert mock_linter_instance.run_lint.called


# ─── Big-Loop #1: /graph 边修复 + /ontology 端点 ──────────────────────────────

def test_graph_returns_real_edges():
    """U-5 回归守卫:/graph 必须返回 knowledge_graph.yaml 的真实 edges。
    旧实现读 'relations' 键(KG 实为 'edges')→ 返回 0 边,前端图谱无连线。
    """
    kg_path = _DATA_DIR / "meta" / "relations" / "knowledge_graph.yaml"
    if not kg_path.exists():
        pytest.skip("真实 knowledge_graph.yaml 不存在(跳过集成断言)")
    with open(kg_path, "r", encoding="utf-8") as f:
        kg = yaml.safe_load(f) or {}
    expected_edges = len(kg.get("edges", []))

    response = client.get("/api/v1/graph")
    assert response.status_code == 200
    data = response.json()

    # 边数须等于 KG 文件的 edges 数(去重后)
    assert len(data["edges"]) == expected_edges
    if expected_edges > 0:
        e = data["edges"][0]
        assert {"source", "target", "type"} <= set(e.keys())


def test_ontology_endpoint():
    """U-6:GET /api/v1/ontology 返回完整本体树。"""
    response = client.get("/api/v1/ontology")
    assert response.status_code == 200
    data = response.json()
    assert "ontology_tree" in data
    assert isinstance(data["ontology_tree"], list)
    assert "total_nodes" in data
    ont_path = _DATA_DIR / "meta" / "ontology" / "global_ontology.yaml"
    if ont_path.exists():
        with open(ont_path, "r", encoding="utf-8") as f:
            ont = yaml.safe_load(f) or {}
        assert data["total_nodes"] == ont.get("total_nodes", 0)
        assert data["total_nodes"] > 0


@patch("api.main.layer3_answer", return_value="mock answer")
@patch("api.main.layer2_score", return_value=[{"id": "doc_20260405_001", "title": "T"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_qa_passes_ontology_to_layer1(
    mock_client, mock_l1, mock_l2, mock_l3
):
    """P0 回归守卫:/qa(前端 ChatPanel 主路径)必须把本体传给 layer1_filter。
    评审发现:旧版 /qa 与 /search/stream 直接调 layer1_filter 未传 ontology,
    导致本体扩展在用户路径上完全不生效。本测试防止该断线回归。
    """
    mock_l1.return_value = [{"id": "doc_20260405_001", "title": "岸桥远控"}]

    response = client.post("/api/v1/qa", json={"query": "岸桥远控"})
    assert response.status_code == 200

    # layer1_filter 被调用且 ontology 参数非空(真实 global_ontology.yaml 存在)
    assert mock_l1.called
    _args, kwargs = mock_l1.call_args
    assert "ontology" in kwargs
    assert kwargs["ontology"], "/qa 未向 layer1_filter 传入有效本体"


@patch("api.main.layer3_answer", return_value="mock answer")
@patch("api.main.layer2_score", return_value=[{"id": "doc_001", "title": "T"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_search_stream_passes_ontology_to_layer1(
    mock_client, mock_l1, mock_l2, mock_l3
):
    """P0 回归守卫:/search/stream 同样必须传入本体。"""
    mock_l1.return_value = [{"id": "doc_001", "title": "岸桥远控"}]

    response = client.get("/api/v1/search/stream?q=岸桥远控")
    assert response.status_code == 200

    assert mock_l1.called
    _args, kwargs = mock_l1.call_args
    assert "ontology" in kwargs
    assert kwargs["ontology"], "/search/stream 未向 layer1_filter 传入有效本体"
