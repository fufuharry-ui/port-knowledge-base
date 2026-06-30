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


@patch("api.main.load_contradictions")
@patch("api.main.layer3_answer", return_value="mock answer")
@patch("api.main.layer2_score", return_value=[{"id": "doc_A", "title": "A"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_qa_passes_contradictions_to_layer3(
    mock_client, mock_l1, mock_l2, mock_l3, mock_load_con
):
    """P0 回归守卫(Big-Loop #3):/qa 必须把 contradictions 传给 layer3_answer。
    复刻 Loop #1 ontology 断线教训:用户主路径若忘传 contradictions,
    Layer3 的矛盾提示在 /qa 上完全不生效。本测试防止该断线。
    """
    mock_l1.return_value = [{"id": "doc_A", "title": "A"}]
    mock_load_con.return_value = {"contradictions": [
        {"doc_a": "doc_A", "doc_b": "doc_B", "conflict_point": "x"},
    ]}

    response = client.post("/api/v1/qa", json={"query": "岸桥远控"})
    assert response.status_code == 200

    assert mock_l3.called
    _args, kwargs = mock_l3.call_args
    assert "contradictions" in kwargs, "/qa 未向 layer3_answer 传 contradictions 参数"
    assert kwargs["contradictions"], "/qa 传入的 contradictions 为空(断线)"


def test_entity_graph_endpoint():
    """E-5:GET /api/v1/entity-graph 返回术语邻居结构。"""
    response = client.get("/api/v1/entity-graph?term=5G专网&depth=2")
    assert response.status_code == 200
    data = response.json()
    assert data["term"] == "5G专网"
    assert data["depth"] == 2
    assert "neighbors" in data
    assert isinstance(data["neighbors"], list)
    assert "total_edges" in data


def test_entity_graph_empty_term():
    """term 为空时返回空邻居,不报错"""
    response = client.get("/api/v1/entity-graph")
    assert response.status_code == 200
    data = response.json()
    assert data["neighbors"] == []


# ─── UX 修复:仪表盘编译状态同步 ──────────────────────────────────────────────

def test_wiki_index_enriches_status_from_meta(tmp_path, monkeypatch):
    """UX 回归守卫:/wiki/index 必须用 .meta.yaml 的权威 status 填充。

    走查发现:index.yaml 条目 status 滞留 None(compile.py 只更新 .meta.yaml),
    导致仪表盘"已编译"恒为 0。_enrich_doc_status 在端点返回时即时合并。
    """
    import api.main as api_mod

    # 构造:index 条目 status=None,但 .meta.yaml status=compiled(模拟真实不一致)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    for doc_id, meta_status in [("doc_A", "compiled"), ("doc_B", "raw")]:
        with open(raw_dir / f"{doc_id}.meta.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"id": doc_id, "status": meta_status}, f, allow_unicode=True)

    monkeypatch.setattr(api_mod, "RAW_DIR", raw_dir)
    fake_index = {"documents": [
        {"id": "doc_A", "title": "A", "status": None},
        {"id": "doc_B", "title": "B", "status": None},
    ]}
    with patch("api.main._load_index", return_value=fake_index):
        response = client.get("/api/v1/wiki/index")

    assert response.status_code == 200
    docs = {d["id"]: d for d in response.json()["documents"]}
    assert docs["doc_A"]["status"] == "compiled", "已编译文档状态未从 .meta.yaml 同步"
    assert docs["doc_B"]["status"] == "raw"


# ─── Big-Loop #3: /consistency 端点 ─────────────────────────────────────────

def test_consistency_get():
    """C-5: GET /api/v1/consistency 返回报告结构(只读)。"""
    response = client.get("/api/v1/consistency")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert "total" in data
    assert isinstance(data["contradictions"], list)
    assert "last_updated" in data


@patch("api.main.run_consistency_check")
def test_consistency_post_triggers_check(mock_run):
    """C-5: POST /api/v1/consistency 触发稽核并返回报告。"""
    mock_run.return_value = {
        "total": 1,
        "candidates_checked": 5,
        "last_updated": "2026-06-29T22:00:00+08:00",
        "contradictions": [
            {"doc_a": "doc_A", "doc_b": "doc_B",
             "conflict_point": "延迟要求", "reasoning_chain": "A说10ms,B说20ms",
             "confidence": 0.85},
        ],
    }
    response = client.post("/api/v1/consistency")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total"] == 1
    assert mock_run.called
    assert data["contradictions"][0]["conflict_point"] == "延迟要求"


@patch("api.main.run_consistency_check", side_effect=Exception("LLM down"))
def test_consistency_post_degrades_on_error(mock_run):
    """LLM 不可用 → POST 返回 error 状态(降级,不 500)。"""
    response = client.post("/api/v1/consistency")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["total"] == 0
