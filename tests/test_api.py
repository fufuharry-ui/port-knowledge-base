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


@pytest.fixture(autouse=True)
def isolate_api_originals(tmp_path, monkeypatch):
    """UAT Big-Loop:把 API 测试可能触发的所有真实写入隔离到 tmp_path。

    覆盖三处写入源头:
    - api.main: ORIGINALS_DIR(上传落盘)、RAW_DIR(管理 catalog 读)
    - scripts.ingest: 后台 ingest_file 用的是 ingest 模块自己的 RAW_DIR/INDEX_FILE
      (独立常量,不是 api.main 的),不一并 patch 会写真实 raw/
    - scripts.logger: ingest_file 调用时 `from scripts.logger import global_logger`
    """
    import api.main as api_mod
    import scripts.ingest as ingest_mod
    import scripts.logger as logger_mod
    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(api_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(api_mod, "RAW_DIR", tmp_path / "raw")
    # BASE_DIR: _schedule_compile 以 BASE_DIR 调用 prepare_doc_compile/run_compile_task,
    # run_compile_task 用其作 cwd + 找 scripts/compile.py。
    # 指向 tmp_path 后 compile_script 不存在 → 终态 error(隔离),绝不 spawn 指向真实仓库的子进程。
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ingest_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ingest_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ingest_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ingest_mod, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(ingest_mod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
    sandbox_logger = logger_mod.ActivityLogger(tmp_path / "wiki")
    monkeypatch.setattr(logger_mod, "global_logger", sandbox_logger)
    return originals


@patch("api.main.run_compile_task")
@patch("api.main.ingest_file")
def test_ingest_endpoint(mock_ingest, mock_run, isolate_api_originals):
    """UAT Big-Loop Task 6:/ingest 返回权威 doc_id(来自 ingest_file,而非上传时预生成),
    含 skipped 字段,并经统一入口 _schedule_compile 调度 run_compile_task(doc_id, BASE_DIR)。
    参数顺序:@patch mock 在前,fixture 在后(pytest 9.x arg_names[N:] 语义)。
    """
    import api.main as api_mod

    doc_id = "doc_test_001"

    def fake_ingest(_stored):
        api_mod.RAW_DIR.mkdir(parents=True, exist_ok=True)
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").write_text(
            yaml.safe_dump({
                "id": doc_id, "title": "test", "status": "raw",
                "file_hash": "sha256:test", "source_type": "txt",
            }),
            encoding="utf-8",
        )
        return {
            "id": doc_id, "title": "test", "status": "raw",
            "file_hash": "sha256:test", "source_type": "txt",
        }

    mock_ingest.side_effect = fake_ingest
    response = client.post(
        "/api/v1/ingest",
        files={"file": ("test.txt", b"Mock document content", "text/plain")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert data["doc_id"] == "doc_test_001"
    assert data["skipped"] is False
    assert (isolate_api_originals / "test.txt").exists()
    mock_ingest.assert_called_once()
    mock_run.assert_called_once_with(doc_id, api_mod.BASE_DIR)

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
@patch("api.main.layer3_answer_stream", return_value=iter(["mock answer"]))
@patch("api.main.layer2_score", return_value=[{"id": "doc_A", "title": "A"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_qa_passes_contradictions_to_layer3(
    mock_client, mock_l1, mock_l2, mock_l3_stream, mock_load_con
):
    """P0 回归守卫(Big-Loop #3/#5):/qa 必须把 contradictions 传给 layer3_answer_stream。
    Loop #5 起 /qa 主路径改用真流式 layer3_answer_stream;矛盾提示参数必须照传,
    否则 Layer3 的矛盾提示在 /qa 上完全不生效。本测试防止断线。
    """
    mock_l1.return_value = [{"id": "doc_A", "title": "A"}]
    mock_load_con.return_value = {"contradictions": [
        {"doc_a": "doc_A", "doc_b": "doc_B", "conflict_point": "x"},
    ]}

    response = client.post("/api/v1/qa", json={"query": "岸桥远控"})
    assert response.status_code == 200

    assert mock_l3_stream.called
    _args, kwargs = mock_l3_stream.call_args
    assert "contradictions" in kwargs, "/qa 未向 layer3_answer_stream 传 contradictions"
    assert kwargs["contradictions"], "/qa 传入的 contradictions 为空(断线)"


@patch("api.main.layer3_answer_stream", return_value=iter(["mock answer"]))
@patch("api.main.layer2_score", return_value=[{"id": "doc_A", "title": "A"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_qa_passes_history_to_layer3(
    mock_client, mock_l1, mock_l2, mock_l3_stream
):
    """P0 回归守卫(Big-Loop #8):/qa 必须把 history 传给 layer3_answer_stream。
    多轮对话上下文若断线,追问代词无法解析,Q&A 退化为单轮。
    """
    mock_l1.return_value = [{"id": "doc_A", "title": "A"}]
    history = [
        {"role": "user", "content": "岸桥远控用什么网络"},
        {"role": "assistant", "content": "采用5G专网"},
    ]
    response = client.post("/api/v1/qa", json={"query": "那它的延迟要求", "history": history})
    assert response.status_code == 200

    assert mock_l3_stream.called
    _args, kwargs = mock_l3_stream.call_args
    assert "history" in kwargs, "/qa 未向 layer3_answer_stream 传 history"
    assert kwargs["history"], "/qa 传入的 history 为空(断线)"


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


# ─── 文档管理:删除 + 重编译(Loop #10)────────────────────────────────────────

@patch("scripts.doc_admin.remove_doc")
def test_delete_doc_endpoint(mock_remove):
    """DELETE /api/v1/docs/{id} 调用 remove_doc 并返回删除摘要。"""
    mock_remove.return_value = {"doc_id": "doc_X", "removed": True,
                                "cleaned_refs": {"index_removed": 1}}
    response = client.delete("/api/v1/docs/doc_X")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    assert data["removed"] is True
    assert mock_remove.called


@patch("scripts.doc_admin.remove_doc", return_value={"doc_id": "doc_X", "removed": False})
def test_delete_nonexistent_doc_returns_404(mock_remove):
    """删不存在的文档 → 404。"""
    response = client.delete("/api/v1/docs/doc_missing")
    assert response.status_code == 404


# ─── E004-FIX-02:删除与编译事务互斥 ─────────────────────────────────────────

def test_delete_returns_busy_while_compile_transaction_is_active(tmp_path, monkeypatch):
    """E004-FIX-02:编译事务(快照/回滚)持有执行锁时,删除必须立即 409,
    不得阻塞等待,也不得让回滚覆盖删除造成的共享文件清理。"""
    import api.main as api_mod
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260806_050"
    meta_path = raw / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    COMPILE_EXECUTION_LOCK.acquire()
    try:
        with patch("scripts.doc_admin.remove_doc") as mock_remove:
            response = client.delete(f"/api/v1/docs/{doc_id}")
    finally:
        COMPILE_EXECUTION_LOCK.release()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    mock_remove.assert_not_called()
    assert meta_path.exists()
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_delete_rejects_document_with_queued_compilation(tmp_path, monkeypatch):
    """E004-FIX-02:文档已调度(status=compiling)但后台任务尚未取得执行锁时,
    删除必须 409 compile_in_progress,封住排队窗口。"""
    import api.main as api_mod
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260806_051"
    meta_path = raw / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiling"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    with patch("scripts.doc_admin.remove_doc") as mock_remove:
        response = client.delete(f"/api/v1/docs/{doc_id}")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "compile_in_progress"}}
    mock_remove.assert_not_called()
    assert meta_path.exists()
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_delete_holds_both_locks_while_removing(tmp_path, monkeypatch):
    """E004-FIX-02:空闲删除时,remove_doc 调用瞬间调度锁与执行锁均被持有,
    响应结束后执行锁已释放。"""
    import api.main as api_mod
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260806_052"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    observed = {}

    def fake_remove(doc_id_arg, base_dir=None):
        observed["schedule_locked"] = api_mod.COMPILE_SCHEDULE_LOCK.locked()
        observed["execution_locked"] = COMPILE_EXECUTION_LOCK.locked()
        return {"doc_id": doc_id_arg, "removed": True, "cleaned_refs": {"index_removed": 1}}

    with patch("scripts.doc_admin.remove_doc", side_effect=fake_remove):
        response = client.delete(f"/api/v1/docs/{doc_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    assert observed == {"schedule_locked": True, "execution_locked": True}
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_document_catalog_never_exposes_backend_error_message(tmp_path, monkeypatch):
    """E004-FIX-02:公共 catalog 只投影 error_code,绝不返回 error_message;
    raw 元数据中的脱敏诊断信息必须保留供后端排查。"""
    import api.main as api_mod
    from scripts.doc_admin import read_doc_meta

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260806_053"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({
            "id": doc_id,
            "status": "error",
            "error_code": "llm_configuration",
            "error_message": "api_key=<redacted>",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "missing-index.yaml")

    wiki = client.get("/api/v1/wiki/index")
    assert wiki.status_code == 200
    wiki_doc = wiki.json()["documents"][0]
    assert wiki_doc["error_code"] == "llm_configuration"
    assert "error_message" not in wiki_doc

    listing = client.get("/api/v1/docs")
    assert listing.status_code == 200
    list_doc = listing.json()["documents"][0]
    assert list_doc["error_code"] == "llm_configuration"
    assert "error_message" not in list_doc

    detail = client.get(f"/api/v1/docs/{doc_id}")
    assert detail.status_code == 200
    detail_doc = detail.json()
    assert detail_doc["error_code"] == "llm_configuration"
    assert "error_message" not in detail_doc

    meta = read_doc_meta(doc_id, base_dir=tmp_path)
    assert meta["error_message"] == "api_key=<redacted>"


@patch("api.main.get_llm_client")
@patch("api.main.run_consistency_check")
def test_consistency_post_triggers_check(mock_run, mock_get_client, monkeypatch):
    """C-5: POST /api/v1/consistency 触发稽核并返回报告。

    T001 测试隔离修复:端点顺序为 get_llm_client() → 确定 model →
    run_consistency_check(client, model)。必须同时 mock 客户端创建与一致性检查,
    否则无 OPENAI_API_KEY 时 get_llm_client 先抛 RuntimeError,mock 的检查函数
    从未执行,端点按设计降级返回 status=error(假失败)。
    """
    fake_client = MagicMock(name="consistency_llm_client")
    mock_get_client.return_value = fake_client
    monkeypatch.setenv("RELATE_MODEL", "test-relate-model")
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
    assert data["contradictions"][0]["conflict_point"] == "延迟要求"
    mock_get_client.assert_called_once_with()
    mock_run.assert_called_once_with(fake_client, "test-relate-model")


@patch("api.main.get_llm_client")
@patch("api.main.run_consistency_check", side_effect=Exception("LLM down"))
def test_consistency_post_degrades_on_error(mock_run, mock_get_client, monkeypatch):
    """一致性检查过程抛异常 → POST 返回 error 状态(降级,不 500)。

    T001 测试隔离修复:必须先让 get_llm_client 成功,error 才确定性地来自
    run_consistency_check 本身,而不是无 Key 导致的客户端创建提前失败(假阳性)。
    """
    fake_client = MagicMock(name="consistency_llm_client")
    mock_get_client.return_value = fake_client
    monkeypatch.setenv("RELATE_MODEL", "test-relate-model")

    response = client.post("/api/v1/consistency")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "error"
    assert data["total"] == 0
    mock_get_client.assert_called_once_with()
    mock_run.assert_called_once_with(fake_client, "test-relate-model")


# ─── 落地增强: /api/v1/health 健康检查 ───────────────────────────────────────

def test_health_endpoint():
    """GET /api/v1/health 返回运维所需的健康字段(部署监控用)。"""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    # 关键运维字段
    assert "doc_count" in data and isinstance(data["doc_count"], int)
    assert "llm_configured" in data   # OPENAI_API_KEY 是否配置
    assert "jieba_loaded" in data     # 分词引擎是否就绪
    assert "ontology_loaded" in data  # 本体是否加载
    assert "version" in data          # 版本可追溯


# ─── 落地增强: /search/stream 进度反馈 ───────────────────────────────────────

@patch("api.main.layer3_answer_stream", return_value=iter(["答案。"]))
@patch("api.main.layer2_score", return_value=[{"id": "doc_001", "title": "T"}])
@patch("api.main.layer1_filter")
@patch("api.main.get_llm_client")
def test_search_stream_emits_thought_progress(mock_client, mock_l1, mock_l2, mock_l3):
    """落地增强:/search/stream 应在检索各阶段发 thought 事件,让用户看到进度。

    痛点:Layer1(3s)+Layer2(~10s)期间用户只见"正在检索..."干等 14-21s,
    易以为系统卡死。发 thought 事件(初筛/精选/生成)让前端能渲染分步进度。
    """
    mock_l1.return_value = [{"id": "doc_001", "title": "岸桥方案"}]

    response = client.get("/api/v1/search/stream?q=岸桥", headers={"Accept": "text/event-stream"})
    assert response.status_code == 200

    # 解析 SSE 流,收集 thought 事件
    thought_steps = []
    for line in response.iter_lines():
        if line and line.startswith("data:"):
            import json as _json
            try:
                ev = _json.loads(line[5:].strip())
                if ev.get("type") == "thought":
                    thought_steps.append(ev.get("step"))
            except _json.JSONDecodeError:
                pass

    # 至少 3 个步骤(初筛/精选/生成)
    assert len(thought_steps) >= 3, f"/search/stream 应发≥3 个 thought 事件,实际 {thought_steps}"
    assert 1 in thought_steps and 2 in thought_steps and 3 in thought_steps


# ─── UAT Big-Loop Task 6:truthful upload + observable catalog joint point ────

def test_catalog_includes_raw_meta_before_compile(tmp_path, monkeypatch):
    """CAP-PROGRESS:raw 文档(未编译)必须出现在管理 catalog,这样上传后立即在仪表盘可见。
    旧实现 /wiki/index 只读 compiled index → 新文档编译完成前不可见。
    """
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    meta = {
        "id": "doc_20260719_001",
        "title": "蓝鲸门机智能巡检协议",
        "status": "raw",
        "source_type": "md",
        "file_hash": "sha256:raw",
        "char_count": 120,
        "ingested_at": "2026-07-19T10:00:00+08:00",
    }
    (raw / "doc_20260719_001.meta.yaml").write_text(
        yaml.safe_dump(meta, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "missing-index.yaml")

    response = client.get("/api/v1/wiki/index")
    assert response.status_code == 200
    docs = response.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["id"] == meta["id"]
    assert docs[0]["status"] == "raw"


@patch("api.main.run_compile_task")
@patch("api.main.ingest_file")
def test_upload_returns_authoritative_ingested_id(mock_ingest, mock_run, isolate_api_originals):
    """CAP-INGEST:/upload 必须返回 ingest_file() 的权威 doc_id(而非上传时预生成),
    含 skipped=false,并经统一入口调度 run_compile_task(权威 doc_id, BASE_DIR)。
    """
    import api.main as api_mod

    doc_id = "doc_20260719_007"

    def fake_ingest(_stored):
        api_mod.RAW_DIR.mkdir(parents=True, exist_ok=True)
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").write_text(
            yaml.safe_dump({
                "id": doc_id, "title": "truthful", "status": "raw",
                "file_hash": "sha256:new",
            }),
            encoding="utf-8",
        )
        return {
            "id": doc_id, "title": "truthful", "status": "raw",
            "file_hash": "sha256:new",
        }

    mock_ingest.side_effect = fake_ingest
    response = client.post(
        "/api/v1/upload",
        files={"file": ("truthful.md", b"# Truthful", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["doc_id"] == "doc_20260719_007"
    assert response.json()["skipped"] is False
    mock_ingest.assert_called_once()
    mock_run.assert_called_once_with(doc_id, api_mod.BASE_DIR)


@patch("api.main.run_compile_task")
def test_duplicate_upload_returns_existing_id_without_compile(
    mock_run, isolate_api_originals, monkeypatch
):
    """CAP-INGEST:重复哈希必须返回既有 doc_id + skipped=true,且不调度编译(无幽灵任务)。
    """
    import api.main as api_mod
    monkeypatch.setattr(
        api_mod,
        "_find_duplicate_doc",
        lambda _hash: {"id": "doc_existing", "title": "existing"},
    )
    response = client.post(
        "/api/v1/upload",
        files={"file": ("duplicate.md", b"same bytes", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["skipped"] is True
    assert response.json()["doc_id"] == "doc_existing"
    mock_run.assert_not_called()


def test_upload_rejects_unsupported_extension_without_writing(isolate_api_originals):
    """安全最小线:非法扩展名 → 422,originals/ 无任何落盘。"""
    response = client.post(
        "/api/v1/upload",
        files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
    )
    assert response.status_code == 422
    assert list(isolate_api_originals.iterdir()) == []


def test_upload_strips_path_components(isolate_api_originals):
    """安全最小线:文件名中的路径分量被剥离(只取 basename),不写到 originals/ 之外。"""
    response = client.post(
        "/api/v1/upload",
        files={"file": ("../safe.md", b"# Safe", "text/markdown")},
    )
    assert response.status_code in (200, 422)
    assert not (isolate_api_originals.parent / "safe.md").exists()


# ─── E004 Task 5:统一编译调度契约(_schedule_compile)──────────────────────────

@patch("api.main.run_compile_task")
@patch("api.main.ingest_file")
def test_upload_marks_compiling_before_background_task(
    mock_ingest, mock_run, isolate_api_originals
):
    """E004:上传必须在调度后台任务之前把 meta 原子置为 compiling。

    旧路径 compile_ingested_task 只 spawn 子进程、不准备状态,导致 catalog
    在编译期间停留 raw、重复上传/重编译无法被 409 拦截。
    """
    import api.main as api_mod

    doc_id = "doc_20260805_030"

    def fake_ingest(_stored):
        api_mod.RAW_DIR.mkdir(parents=True, exist_ok=True)
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").write_text(
            yaml.safe_dump({"id": doc_id, "status": "raw"}),
            encoding="utf-8",
        )
        return {"id": doc_id, "status": "raw"}

    mock_ingest.side_effect = fake_ingest
    response = client.post(
        "/api/v1/upload",
        files={"file": ("task.md", b"# task", "text/markdown")},
    )

    assert response.status_code == 200
    meta = yaml.safe_load(
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
    mock_run.assert_called_once_with(doc_id, api_mod.BASE_DIR)


def test_recompile_returns_409_when_document_is_compiling(tmp_path, monkeypatch):
    """E004:文档已在 compiling 时,重编译必须 409 且 detail 只含稳定错误码。"""
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_031"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiling"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    response = client.post(f"/api/v1/docs/{doc_id}/recompile")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "compile_in_progress"}}


def test_catalog_projects_error_code(tmp_path, monkeypatch):
    """E004:管理 catalog 必须投影 error_code(error 文档在前端可分类展示)。"""
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_032"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({
            "id": doc_id,
            "status": "error",
            "error_code": "timeout",
            "error_message": "sanitized backend detail",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "missing-index.yaml")

    response = client.get("/api/v1/wiki/index")

    assert response.status_code == 200
    assert response.json()["documents"][0]["error_code"] == "timeout"


def test_schedule_compile_allows_only_one_concurrent_request(tmp_path, monkeypatch):
    """E004 并发守卫:同一 doc 的两个并发调度,恰一个成功,另一个得 409。

    直接调用 _schedule_compile(不经 TestClient),Starlette 不会执行排队的
    后台任务;锁必须覆盖 读/判 → 置 compiling → add_task 整个临界区。
    """
    import threading
    from fastapi import BackgroundTasks, HTTPException
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_033"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    barrier = threading.Barrier(2)
    backgrounds = [BackgroundTasks(), BackgroundTasks()]
    outcomes = []
    outcome_lock = threading.Lock()

    def worker(background_tasks):
        barrier.wait(timeout=2)
        try:
            api_mod._schedule_compile(background_tasks, doc_id)
            result = "scheduled"
        except HTTPException as exc:
            result = (exc.status_code, exc.detail)
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=(background_tasks,))
        for background_tasks in backgrounds
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(
        outcomes,
        key=lambda item: 0 if item == "scheduled" else 1,
    ) == [
        "scheduled",
        (409, {"code": "compile_in_progress"}),
    ]
    assert sum(len(background.tasks) for background in backgrounds) == 1
    meta = yaml.safe_load(
        (raw / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
