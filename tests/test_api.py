import pytest
import yaml
from pathlib import Path
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Will fail here on first run
from api.main import app, create_app  # noqa: F401  (app: uvicorn/startup-smoke 引用保持)

# E005 Task 8:既有测试使用显式 allow_unmanaged=True 的测试实例。
# 纯 TestClient(非上下文管理器)不触发 lifespan,app.state.runtime 不存在;
# 生产 module-level app 不设置该旗标,同一情形 fail-closed(503)。
unmanaged_app = create_app(allow_unmanaged=True)
client = TestClient(unmanaged_app)

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

    E005 Task 9:_schedule_compile/_remove_doc_exclusively 的 config/readiness
    一律来自 app.state.runtime;为共享测试 app 挂接指向 tmp_path 的就绪
    runtime(与 monkeypatch 的 BASE_DIR 一致),不触碰真实仓库。
    """
    import api.main as api_mod
    import scripts.ingest as ingest_mod
    import scripts.logger as logger_mod
    from api.runtime_guard import (
        ApiInstanceLock,
        ServiceReadiness,
        load_compile_runtime_config,
    )
    originals = tmp_path / "originals"
    originals.mkdir()
    monkeypatch.setattr(api_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(api_mod, "RAW_DIR", tmp_path / "raw")
    # E005 Task 10:两阶段上传的 catalog 去重经 _load_index 读 INDEX_FILE;
    # 一并隔离,绝不读取真实 wiki/index.yaml。
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
    # BASE_DIR: _schedule_compile 以 BASE_DIR 调用 prepare_recompile_transaction/
    # bind_doc_compile_job,并把 BASE_DIR 传给 run_compile_task。
    # 指向 tmp_path 后 compile_script 不存在 → 终态 error(隔离),绝不 spawn 指向真实仓库的子进程。
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ingest_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ingest_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ingest_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ingest_mod, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(ingest_mod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
    sandbox_logger = logger_mod.ActivityLogger(tmp_path / "wiki")
    monkeypatch.setattr(logger_mod, "global_logger", sandbox_logger)
    runtime = api_mod.AppRuntime(
        config=load_compile_runtime_config(tmp_path),
        readiness=ServiceReadiness(),
        instance_lock=ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock"),
    )
    had_runtime = hasattr(unmanaged_app.state, "runtime")
    previous = getattr(unmanaged_app.state, "runtime", None)
    unmanaged_app.state.runtime = runtime
    try:
        yield originals
    finally:
        if had_runtime:
            unmanaged_app.state.runtime = previous
        else:
            del unmanaged_app.state.runtime


@patch("api.main.run_compile_task")
def test_ingest_endpoint(mock_run, isolate_api_originals):
    """UAT Big-Loop Task 6 + E005 Task 10:/ingest 与 /upload 共用两阶段上传事务。

    权威 doc_id 来自 prepare_ingest(非上传时预生成);original/raw text/raw meta
    经 intake journal 耐久发布;meta 绑定同一 job;后台任务以持久化事务的
    job_id(绝非 doc_id)登记 run_compile_task(job_id, BASE_DIR, config, readiness);
    Manifest 已持久迁移到 SCHEDULED。
    """
    import api.main as api_mod
    from api.compile_transactions import (
        TransactionState,
        list_transaction_dirs,
        load_manifest,
    )

    response = client.post(
        "/api/v1/ingest",
        files={"file": ("test.txt", b"Mock document content", "text/plain")}
    )
    assert response.status_code == 200
    data = response.json()
    doc_id = data["doc_id"]
    assert data["status"] == "processing"
    assert doc_id.startswith("doc_")
    assert data["skipped"] is False
    assert data["filename"] == "test.txt"
    assert "job_id" not in response.text
    # 三项业务目标已发布
    assert (isolate_api_originals / "test.txt").read_bytes() == b"Mock document content"
    assert (api_mod.RAW_DIR / f"{doc_id}.txt").read_bytes() == b"Mock document content"
    meta = yaml.safe_load(
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
    mock_run.assert_called_once()
    args = mock_run.call_args.args
    job_id = args[0]
    assert isinstance(job_id, str) and job_id != doc_id
    assert args[1] == api_mod.BASE_DIR
    runtime = unmanaged_app.state.runtime
    assert args[2] is runtime.config
    assert args[3] is runtime.readiness
    assert meta["compile_job_id"] == job_id
    job_dirs = list_transaction_dirs(runtime.config)
    assert [path.name for path in job_dirs] == [job_id]
    assert load_manifest(job_dirs[0]).state is TransactionState.SCHEDULED

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


# ─── Codex Round 3 (P2-2):删除 readiness TOCTOU ─────────────────────────────

def test_delete_gated_returns_recovery_required(managed_client):
    """P2-2: readiness 进入 recovery_required 时,删除 503(精确响应体),
    remove_doc 绝不被调用。"""
    _repo_runtime(managed_client).readiness.mark_recovery_required("rollback_failed")
    with patch("scripts.doc_admin.remove_doc") as mock_remove:
        response = managed_client.delete("/api/v1/docs/doc_1")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "recovery_required"}}
    mock_remove.assert_not_called()


def test_remove_doc_exclusively_rechecks_readiness_inside_locks(tmp_path, monkeypatch):
    """P2-2 核心: 门禁通过后、锁内 readiness 已翻转为 recovery_required
    (后台编译线程在窗口内标记)→ 删除必须 503 recovery_required,
    remove_doc 绝不被调用,两把锁均正确释放(封住 TOCTOU 窗口)。"""
    import api.main as api_mod
    from api.compile_jobs import COMPILE_EXECUTION_LOCK
    from api.runtime_guard import ServiceReadiness, load_compile_runtime_config
    from fastapi import HTTPException

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260806_060"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    readiness = ServiceReadiness()
    readiness.mark_recovery_required("rollback_failed")
    runtime = api_mod.AppRuntime(
        config=load_compile_runtime_config(tmp_path, env={}),
        readiness=readiness,
        instance_lock=None,
        resolved_base=tmp_path,
    )
    remove_calls = []
    monkeypatch.setattr(
        "scripts.doc_admin.remove_doc",
        lambda *args, **kwargs: remove_calls.append(args) or {"removed": True},
    )

    with pytest.raises(HTTPException) as excinfo:
        api_mod._remove_doc_exclusively(doc_id, runtime)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == {"code": "recovery_required"}
    assert remove_calls == []
    assert not api_mod.COMPILE_SCHEDULE_LOCK.locked()
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
    # E005 Task 8:liveness 始终 200,并附 ready 布尔与粗粒度 service_mode
    assert data["ready"] is True
    assert data["service_mode"] == "ready"


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
def test_upload_returns_authoritative_ingested_id(mock_run, isolate_api_originals):
    """CAP-INGEST + E005 Task 10:/upload 返回 prepare_ingest 的权威 doc_id,
    成功响应体精确为既有合同(含 skipped=false,无 job_id);
    后台任务以 job_id(绝非 doc_id)登记,meta 绑定同一 job。"""
    import api.main as api_mod

    response = client.post(
        "/api/v1/upload",
        files={"file": ("truthful.md", b"# Truthful", "text/markdown")},
    )
    assert response.status_code == 200
    data = response.json()
    doc_id = data["doc_id"]
    assert doc_id.startswith("doc_")
    assert data == {
        "status": "processing",
        "skipped": False,
        "doc_id": doc_id,
        "filename": "truthful.md",
        "message": "摄入成功，后台自动编译中...",
    }
    # E005 Task 9/10:后台任务以 job_id(绝非 doc_id)登记。
    mock_run.assert_called_once()
    args = mock_run.call_args.args
    job_id = args[0]
    assert isinstance(job_id, str) and job_id != doc_id
    assert args[1] == api_mod.BASE_DIR
    runtime = unmanaged_app.state.runtime
    assert args[2] is runtime.config
    assert args[3] is runtime.readiness
    meta = yaml.safe_load(
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["compile_job_id"] == job_id


@patch("api.main.run_compile_task")
def test_duplicate_upload_returns_existing_id_without_compile(
    mock_run, isolate_api_originals, monkeypatch
):
    """CAP-INGEST + E005 Task 10:重复哈希必须返回既有 doc_id + skipped=true
    精确响应体,不创建编译事务、不调度编译(无幽灵任务),intake staging 被丢弃。
    """
    import api.main as api_mod
    from api.compile_transactions import list_transaction_dirs

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
    assert response.json() == {
        "status": "skipped",
        "skipped": True,
        "doc_id": "doc_existing",
        "filename": "duplicate.md",
        "message": "文件已存在，已跳过",
    }
    mock_run.assert_not_called()
    runtime = unmanaged_app.state.runtime
    assert list_transaction_dirs(runtime.config) == []
    staging_root = runtime.config.upload_intake_dir
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


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
    with patch("api.main.run_compile_task"):
        response = client.post(
            "/api/v1/upload",
            files={"file": ("../safe.md", b"# Safe", "text/markdown")},
        )
    assert response.status_code in (200, 422)
    assert not (isolate_api_originals.parent / "safe.md").exists()


# ─── E004 Task 5:统一编译调度契约(_schedule_compile)──────────────────────────

@patch("api.main.run_compile_task")
def test_upload_marks_compiling_before_background_task(
    mock_run, isolate_api_originals
):
    """E004/E005 Task 10:上传必须在后台任务登记前把 meta 原子置为 compiling
    并绑定同一 job_id(two-phase:publish_upload_intake → bind → SCHEDULED)。

    旧路径 compile_ingested_task 只 spawn 子进程、不准备状态,导致 catalog
    在编译期间停留 raw、重复上传/重编译无法被 409 拦截。
    """
    import api.main as api_mod

    response = client.post(
        "/api/v1/upload",
        files={"file": ("task.md", b"# task", "text/markdown")},
    )

    assert response.status_code == 200
    doc_id = response.json()["doc_id"]
    meta = yaml.safe_load(
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
    # E005 Task 9/10:后台任务以 job_id(绝非 doc_id)登记,与 meta 绑定一致。
    mock_run.assert_called_once()
    args = mock_run.call_args.args
    assert args[0] == meta["compile_job_id"]
    assert args[0] != doc_id
    assert args[1] == api_mod.BASE_DIR


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
    """E004/E005 Task 9 并发守卫:同一 doc 的两个并发调度,恰一个成功,另一个得 409。

    直接调用 _schedule_compile(不经 TestClient),Starlette 不会执行排队的
    后台任务;锁必须覆盖 readiness 校验 → 活动事务检查 → PREPARED 准备 →
    meta 绑定 → SCHEDULED 迁移 → add_task 整个临界区。E005 Task 9:
    第二名请求经活动 Manifest 检查被拒绝(manifest 驱动),config/readiness
    来自显式构造的 runtime(与直接调用的生产语义一致)。
    """
    import threading
    from fastapi import BackgroundTasks, HTTPException
    import api.main as api_mod
    from api.runtime_guard import (
        ApiInstanceLock,
        ServiceReadiness,
        load_compile_runtime_config,
    )

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_033"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)
    runtime = api_mod.AppRuntime(
        config=load_compile_runtime_config(tmp_path),
        readiness=ServiceReadiness(),
        instance_lock=ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock"),
    )

    barrier = threading.Barrier(2)
    backgrounds = [BackgroundTasks(), BackgroundTasks()]
    outcomes = []
    outcome_lock = threading.Lock()

    def worker(background_tasks):
        barrier.wait(timeout=2)
        try:
            api_mod._schedule_compile(background_tasks, doc_id, runtime)
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


# ─── E005 Task 8:lifespan、readiness 与全局业务门禁 ──────────────────────────

@pytest.fixture()
def readiness(tmp_path):
    """为共享测试 app 显式挂接一个带全新 ServiceReadiness 的 AppRuntime。

    生产路径 runtime 由 lifespan 设置;纯 TestClient 不触发 lifespan,
    这里显式挂接以驱动门禁分支。所有对象指向 tmp_path,绝不触碰真实仓库。
    """
    import api.main as api_mod
    from api.runtime_guard import (
        ApiInstanceLock,
        ServiceReadiness,
        load_compile_runtime_config,
    )

    service_readiness = ServiceReadiness()
    runtime = api_mod.AppRuntime(
        config=load_compile_runtime_config(tmp_path),
        readiness=service_readiness,
        instance_lock=ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock"),
    )
    had_runtime = hasattr(unmanaged_app.state, "runtime")
    previous = getattr(unmanaged_app.state, "runtime", None)
    unmanaged_app.state.runtime = runtime
    try:
        yield service_readiness
    finally:
        if had_runtime:
            unmanaged_app.state.runtime = previous
        else:
            del unmanaged_app.state.runtime


def test_ready_endpoint_reports_ready():
    """E005 Task 8:GET /api/v1/ready 在 ready 时返回 200 {"status": "ready"}。"""
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_recovery_required_blocks_business_routes_but_not_health(readiness):
    """E005 Task 8(brief 示例):门禁阻断业务路由,health 豁免,ready 变 503。"""
    readiness.mark_recovery_required("rollback_failed")
    gated = client.get("/api/v1/wiki/index")
    assert gated.status_code == 503
    assert gated.json() == {"detail": {"code": "recovery_required"}}
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 503


def test_gate_blocks_representative_business_routes(readiness):
    """E005 Task 8:门禁覆盖代表性业务路由(上传/删除/检索/问答/图谱等),
    响应体精确为 {"detail": {"code": "recovery_required"}},不泄露 reason。"""
    readiness.mark_recovery_required("process_tree_survivors")
    responses = [
        client.get("/api/v1/wiki/index"),
        client.get("/api/v1/docs"),
        client.get("/api/v1/docs/doc_X"),
        client.delete("/api/v1/docs/doc_X"),
        client.post("/api/v1/docs/doc_X/recompile"),
        client.post(
            "/api/v1/upload",
            files={"file": ("a.md", b"# a", "text/markdown")},
        ),
        client.post(
            "/api/v1/ingest",
            files={"file": ("a.md", b"# a", "text/markdown")},
        ),
        client.post("/api/v1/search", json={"query": "x"}),
        client.get("/api/v1/search/stream?q=x"),
        client.post("/api/v1/qa", json={"query": "x"}),
        client.get("/api/v1/graph"),
        client.get("/api/v1/ontology"),
        client.get("/api/v1/entity-graph"),
        client.post("/api/v1/lint"),
        client.get("/api/v1/consistency"),
        client.post("/api/v1/consistency"),
    ]
    for response in responses:
        assert response.status_code == 503, response
        assert response.json() == {"detail": {"code": "recovery_required"}}
        assert "process_tree_survivors" not in response.text


def test_health_always_200_under_gate_with_safe_body(readiness):
    """E005 Task 8:liveness 在门禁下仍 200,保留既有字段,
    ready=False 且 service_mode 为粗粒度字符串,不暴露内部原因码。"""
    readiness.mark_recovery_required("rollback_failed")
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "doc_count" in data and isinstance(data["doc_count"], int)
    assert "llm_configured" in data
    assert "jieba_loaded" in data
    assert "ontology_loaded" in data
    assert "version" in data
    assert data["ready"] is False
    assert data["service_mode"] == "recovery_required"
    # 安全合同(设计 §19):不暴露 reason code、job、路径、PID 等内部信息
    assert "rollback_failed" not in response.text


def test_unmanaged_strict_app_fails_closed(tmp_path):
    """E005 Task 8 fail-closed 合同:未经 lifespan(无 runtime)且未显式
    opt-in 的 app,业务路由一律 503;health/ready 仍可用。

    生产 uvicorn 必经 lifespan,该分支仅防御 --lifespan off 类误用;
    测试实例必须显式 allow_unmanaged=True 才能绕过。
    """
    strict_app = create_app(base_dir=tmp_path, allow_path_override=True)
    strict_client = TestClient(strict_app)
    assert strict_client.get("/api/v1/health").status_code == 200
    assert strict_client.get("/api/v1/ready").status_code == 503
    gated = strict_client.get("/api/v1/wiki/index")
    assert gated.status_code == 503
    assert gated.json() == {"detail": {"code": "recovery_required"}}


async def _enter_lifespan(application):
    """直接进入 app 的 lifespan 上下文(确定性,不经 TestClient 的 anyio 包装)。"""
    async with application.router.lifespan_context(application):
        pass


def _run_lifespan(application):
    import asyncio

    asyncio.run(_enter_lifespan(application))


def test_lifespan_starts_ready_on_clean_tmp_repo(tmp_path):
    """E005 Task 8:干净 tmp 仓库 lifespan 启动成功,runtime 挂接,锁随 shutdown 释放。"""
    import api.main as api_mod
    from api.runtime_guard import ApiInstanceLock, ServiceReadiness

    app = create_app(base_dir=tmp_path, allow_path_override=True)
    with TestClient(app) as test_client:
        response = test_client.get("/api/v1/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
        runtime = app.state.runtime
        assert isinstance(runtime, api_mod.AppRuntime)
        assert isinstance(runtime.readiness, ServiceReadiness)
        assert isinstance(runtime.instance_lock, ApiInstanceLock)
        assert runtime.config.transaction_dir.is_dir()
        business = test_client.get("/api/v1/wiki/index")
        assert business.status_code == 200
    # lifespan 结束后实例锁已释放:第二个实例可在同目录正常启动
    with TestClient(create_app(base_dir=tmp_path, allow_path_override=True)):
        pass


def test_startup_refused_when_instance_lock_held(tmp_path):
    """E005 Task 8(设计 §6.1):实例锁被持有时,第二个实例启动必须失败,不得服务。"""
    import portalocker

    first = create_app(base_dir=tmp_path, allow_path_override=True)
    with TestClient(first):
        second = create_app(base_dir=tmp_path, allow_path_override=True)
        with pytest.raises(portalocker.AlreadyLocked):
            _run_lifespan(second)


def test_startup_refused_on_orphan_compiling(tmp_path):
    """E005 Task 8(设计 §17):存在孤立 compiling 文档时启动必须被拒绝(fail closed)。"""
    raw = tmp_path / "raw"
    raw.mkdir()
    meta_path = raw / "doc_20260806_900.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({"id": "doc_20260806_900", "status": "compiling"}),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="startup recovery"):
        _run_lifespan(create_app(base_dir=tmp_path, allow_path_override=True))
    # 拒绝启动后实例锁已释放;修复孤儿后同目录可重新启动
    meta_path.write_text(
        yaml.safe_dump({"id": "doc_20260806_900", "status": "compiled"}),
        encoding="utf-8",
    )
    _run_lifespan(create_app(base_dir=tmp_path, allow_path_override=True))


# ─── E005 Task 9:重编译/删除的持久化事务合同 ──────────────────────────────────

def _write_repo_doc(repo: Path, doc_id: str, status: str = "raw") -> Path:
    raw = repo / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    meta_path = raw / f"{doc_id}.meta.yaml"
    # 与生产写入(doc_admin._atomic_yaml_dump / durable_write_yaml)相同的
    # dump 设置与 LF 行尾(二进制写入,避免 Windows 文本模式 CRLF 转换),
    # 保证"恢复绑定前 meta"断言可做字节级比较。
    meta_path.write_bytes(
        yaml.dump(
            {"id": doc_id, "title": doc_id, "status": status},
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
    )
    return meta_path


def _read_meta_bytes(repo: Path, doc_id: str) -> bytes:
    return (repo / "raw" / f"{doc_id}.meta.yaml").read_bytes()


def _business_manifest(repo: Path) -> dict:
    """业务目录(raw/wiki/meta/originals)的 path→sha256 清单(字节级比对)。"""
    from api.durable_fs import sha256_file

    result = {}
    for sub in ("raw", "wiki", "meta", "originals"):
        base = repo / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                result[path.relative_to(repo).as_posix()] = sha256_file(path)
    return result


@pytest.fixture()
def tmp_repo(tmp_path, monkeypatch):
    """隔离知识库:两篇 raw 文档;api.main 与 scripts.ingest 路径常量指向该仓库。

    E005 Task 10:补齐 ORIGINALS_DIR/INDEX_FILE 与 scripts.ingest/scripts.logger
    路径隔离——两阶段上传流程(originals 唯一命名、catalog 去重)及旧流程的
    RED 运行都绝不读取或写入真实仓库。
    """
    import api.main as api_mod
    import scripts.ingest as ingest_mod
    import scripts.logger as logger_mod

    (tmp_path / "wiki").mkdir(exist_ok=True)
    originals = tmp_path / "originals"
    originals.mkdir(exist_ok=True)
    _write_repo_doc(tmp_path, "doc_1")
    _write_repo_doc(tmp_path, "doc_2")
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(api_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
    monkeypatch.setattr(ingest_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(ingest_mod, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(ingest_mod, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ingest_mod, "WIKI_DIR", tmp_path / "wiki")
    monkeypatch.setattr(ingest_mod, "INDEX_FILE", tmp_path / "wiki" / "index.yaml")
    sandbox_logger = logger_mod.ActivityLogger(tmp_path / "wiki")
    monkeypatch.setattr(logger_mod, "global_logger", sandbox_logger)
    return tmp_path


@pytest.fixture()
def managed_client(tmp_repo):
    """经真实 lifespan 启动的 app(base_dir=tmp_repo):runtime/config/readiness 就位。"""
    managed_app = create_app(base_dir=tmp_repo, allow_path_override=True)
    with TestClient(managed_app) as test_client:
        yield test_client


def _repo_runtime(test_client):
    return test_client.app.state.runtime


@pytest.fixture()
def active_transaction(managed_client, tmp_repo):
    """lifespan 启动完成后,在 doc_1 上发布一个 PREPARED 持久化事务(未绑定)。"""
    from api.compile_transactions import (
        TransactionKind,
        create_prepared_transaction,
    )
    from scripts.doc_admin import read_doc_meta

    runtime = _repo_runtime(managed_client)
    return create_prepared_transaction(
        base_dir=tmp_repo,
        config=runtime.config,
        doc_id="doc_1",
        kind=TransactionKind.RECOMPILE,
        previous_meta=read_doc_meta("doc_1", tmp_repo),
    )


def test_other_active_transaction_rejects_recompile(managed_client, active_transaction):
    """E005 Task 9(brief 示例):其他文档存在活动事务时,重编译必须 409
    knowledge_base_busy(精确响应体)。"""
    response = managed_client.post("/api/v1/docs/doc_2/recompile")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}


def test_same_doc_active_transaction_rejects_recompile(
    managed_client, active_transaction, tmp_repo
):
    """E005 Task 9:同一文档已有活动事务 → 409 compile_in_progress(精确响应体),
    meta 与既有事务保持原样。"""
    before = _read_meta_bytes(tmp_repo, "doc_1")
    response = managed_client.post("/api/v1/docs/doc_1/recompile")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "compile_in_progress"}}
    assert _read_meta_bytes(tmp_repo, "doc_1") == before


def test_transaction_prepare_failure_does_not_change_meta(
    managed_client, tmp_repo, monkeypatch
):
    """E005 Task 9(brief 示例):事务准备任何失败 → 503
    compile_transaction_unavailable(精确响应体),meta 字节不变(请求未被接受)。"""
    before = _read_meta_bytes(tmp_repo, "doc_1")

    def raising_prepare(*args, **kwargs):
        raise OSError("durable publish failed")

    monkeypatch.setattr("api.main.prepare_recompile_transaction", raising_prepare)
    response = managed_client.post("/api/v1/docs/doc_1/recompile")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    assert _read_meta_bytes(tmp_repo, "doc_1") == before


def test_recompile_gated_returns_recovery_required(managed_client):
    """E005 Task 9:readiness 进入 recovery_required 时,重编译 503(精确响应体)。"""
    _repo_runtime(managed_client).readiness.mark_recovery_required("rollback_failed")
    response = managed_client.post("/api/v1/docs/doc_1/recompile")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "recovery_required"}}


# ─── Codex Round 4 (R4-P2-1):调度与后台终态验证/清理串行化 ──────────────────

def test_recompile_rejected_during_terminal_cleanup_window(
    managed_client, tmp_repo
):
    """R4-P2-1(a): 无活动事务但后台线程持有执行锁(终态验证/清理窗口)→
    重编译必须 409 knowledge_base_busy,meta 字节不变,绝不登记后台任务。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    before = _read_meta_bytes(tmp_repo, "doc_1")
    COMPILE_EXECUTION_LOCK.acquire()
    try:
        with patch("api.main.run_compile_task") as mock_run:
            response = managed_client.post("/api/v1/docs/doc_1/recompile")
    finally:
        COMPILE_EXECUTION_LOCK.release()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    mock_run.assert_not_called()
    assert _read_meta_bytes(tmp_repo, "doc_1") == before
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_recompile_rejected_while_committed_cleanup_in_progress(
    managed_client, tmp_repo
):
    """R4-P2-1(e) 端到端竞态回归: COMMITTED 事务目录存在(不再活动)+
    执行锁被后台清理持有 → 新调度被拒绝,meta 不绑定,readiness 不被
    旧验证器绊倒(合法新状态不再被误判为损坏)。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK
    from api.compile_transactions import (
        TransactionKind,
        TransactionState,
        create_prepared_transaction,
        transition_manifest,
    )
    from scripts.doc_admin import read_doc_meta

    runtime = _repo_runtime(managed_client)
    manifest = create_prepared_transaction(
        base_dir=tmp_repo,
        config=runtime.config,
        doc_id="doc_1",
        kind=TransactionKind.RECOMPILE,
        previous_meta=read_doc_meta("doc_1", tmp_repo),
    )
    transition_manifest(manifest.job_dir, expected=TransactionState.PREPARED,
                        target=TransactionState.SCHEDULED)
    # R5-P1-1: RUNNING 迁移必须携带进程记录(状态机不变量)
    from api.compile_transactions import ProcessRecord
    transition_manifest(manifest.job_dir, expected=TransactionState.SCHEDULED,
                        target=TransactionState.RUNNING,
                        process=ProcessRecord(
                            pid=4321,
                            create_time=1786007401.25,
                            executable="python",
                            cwd="D:/repo",
                            command_fingerprint="scripts.compile|doc_1",
                            process_group_id=4321,
                            platform="windows",
                        ))
    transition_manifest(manifest.job_dir, expected=TransactionState.RUNNING,
                        target=TransactionState.COMMITTED)

    before = _read_meta_bytes(tmp_repo, "doc_1")
    COMPILE_EXECUTION_LOCK.acquire()
    try:
        with patch("api.main.run_compile_task") as mock_run:
            response = managed_client.post("/api/v1/docs/doc_1/recompile")
    finally:
        COMPILE_EXECUTION_LOCK.release()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    mock_run.assert_not_called()
    assert _read_meta_bytes(tmp_repo, "doc_1") == before
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_recompile_success_releases_execution_lock_for_background_task(
    managed_client, tmp_repo
):
    """R4-P2-1(d): 正常调度成功后执行锁已释放,后台任务可立即获取
    (请求线程绝不把执行锁带过响应)。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    with patch("api.main.run_compile_task"):
        response = managed_client.post("/api/v1/docs/doc_2/recompile")

    assert response.status_code == 200
    assert not COMPILE_EXECUTION_LOCK.locked()
    assert COMPILE_EXECUTION_LOCK.acquire(blocking=False)
    COMPILE_EXECUTION_LOCK.release()


def test_recompile_success_schedules_background_task_by_job_id(
    managed_client, tmp_repo
):
    """E005 Task 9 成功路径:响应体精确且不含 job_id;后台任务以 job_id(绝非
    doc_id)登记 run_compile_task,config/readiness 来自 app runtime;meta 绑定
    同一 job;Manifest 已持久迁移到 SCHEDULED。"""
    from api.compile_transactions import (
        TransactionState,
        list_transaction_dirs,
        load_manifest,
    )

    runtime = _repo_runtime(managed_client)
    with patch("api.main.run_compile_task") as mock_run:
        response = managed_client.post("/api/v1/docs/doc_2/recompile")

    assert response.status_code == 200
    assert response.json() == {"status": "recompiling", "doc_id": "doc_2"}
    assert "job_id" not in response.text

    meta = yaml.safe_load(
        (tmp_repo / "raw" / "doc_2.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
    job_id = meta["compile_job_id"]
    assert job_id and job_id != "doc_2"

    mock_run.assert_called_once()
    args = mock_run.call_args.args
    assert args[0] == job_id
    assert args[1] == tmp_repo
    assert args[2] is runtime.config
    assert args[3] is runtime.readiness

    job_dirs = list_transaction_dirs(runtime.config)
    assert [path.name for path in job_dirs] == [job_id]
    assert load_manifest(job_dirs[0]).state is TransactionState.SCHEDULED


def test_bind_failure_rolls_back_prepared_transaction(
    managed_client, tmp_repo, monkeypatch
):
    """E005 Task 9:bind 拒绝时,已发布的 PREPARED 事务必须经恢复库回滚(事务
    目录清理、无活动事务残留),meta 不得残留 compiling 或 job 绑定字段,
    readiness 不被污染,请求 503 compile_transaction_unavailable。"""
    from api.compile_transactions import list_transaction_dirs

    runtime = _repo_runtime(managed_client)
    monkeypatch.setattr("api.main.bind_doc_compile_job", lambda *args, **kwargs: False)
    response = managed_client.post("/api/v1/docs/doc_1/recompile")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    meta = yaml.safe_load(
        (tmp_repo / "raw" / "doc_1.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "raw"
    assert "compile_job_id" not in meta
    assert list_transaction_dirs(runtime.config) == []
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"


def test_delete_rejected_under_active_prepared_transaction_same_doc(
    managed_client, active_transaction, tmp_repo
):
    """E005 Task 9:活动事务处于 PREPARED(尚未取得执行锁)时,同文档删除必须
    409 compile_in_progress,文档文件保持完整。"""
    response = managed_client.delete("/api/v1/docs/doc_1")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "compile_in_progress"}}
    assert (tmp_repo / "raw" / "doc_1.meta.yaml").exists()


def test_delete_rejected_under_active_prepared_transaction_other_doc(
    managed_client, active_transaction, tmp_repo
):
    """E005 Task 9:活动事务处于 PREPARED 时,其他文档删除必须 409
    knowledge_base_busy,文档文件保持完整。"""
    response = managed_client.delete("/api/v1/docs/doc_2")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    assert (tmp_repo / "raw" / "doc_2.meta.yaml").exists()


# ─── E005 Task 9 评审修复:未接受请求的回滚不得制造文档错误终态(设计 §11)──────

def test_add_task_failure_restores_pre_bind_meta_without_error_terminal(
    managed_client, tmp_repo
):
    """E005 Task 9 评审修复:add_task 失败 = 请求未被接受。请求线程必须立即
    回滚:meta 恢复为绑定前内容(字节一致),绝不写入 error 终态;响应 503
    compile_transaction_unavailable;无活动事务残留,readiness 不被污染,
    回滚后终态事务满足清理合同(下次启动绝不阻断)。"""
    from fastapi import BackgroundTasks
    from api.compile_transactions import (
        list_active_manifests,
        list_transaction_dirs,
        load_manifest,
        verify_terminal_transaction,
        TransactionState,
    )

    runtime = _repo_runtime(managed_client)
    before = _read_meta_bytes(tmp_repo, "doc_1")
    with patch.object(
        BackgroundTasks, "add_task", side_effect=RuntimeError("queue down")
    ):
        response = managed_client.post("/api/v1/docs/doc_1/recompile")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    # meta 恢复绑定前内容(字节一致),无制造的 error 终态
    assert _read_meta_bytes(tmp_repo, "doc_1") == before
    meta = yaml.safe_load(before.decode("utf-8"))
    assert meta["status"] == "raw"
    assert "error_code" not in meta
    assert "compile_job_id" not in meta
    # 回滚已执行:无活动事务;ROLLED_BACK 终态满足清理合同
    assert list_active_manifests(runtime.config) == []
    job_dirs = list_transaction_dirs(runtime.config)
    assert len(job_dirs) == 1
    rolled_back = load_manifest(job_dirs[0])
    assert rolled_back.state is TransactionState.ROLLED_BACK
    assert verify_terminal_transaction(tmp_repo, rolled_back).ok is True
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"


def test_scheduled_transition_failure_restores_meta_and_returns_unavailable(
    managed_client, tmp_repo, monkeypatch
):
    """E005 Task 9 评审修复:bind 已生效但 PREPARED→SCHEDULED 持久迁移失败,
    请求未被接受:回滚恢复绑定前 meta(无 error 终态);响应必须是 503
    compile_transaction_unavailable——回滚后没有任何编译在进行,不得报 409。"""
    runtime = _repo_runtime(managed_client)
    before = _read_meta_bytes(tmp_repo, "doc_1")

    def raising_transition(*args, **kwargs):
        raise OSError("manifest write failed")

    monkeypatch.setattr("api.main.transition_manifest", raising_transition)
    response = managed_client.post("/api/v1/docs/doc_1/recompile")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    assert _read_meta_bytes(tmp_repo, "doc_1") == before
    from api.compile_transactions import list_active_manifests

    assert list_active_manifests(runtime.config) == []
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"


def test_unaccepted_rollback_blocked_enters_recovery_required(
    managed_client, tmp_repo, monkeypatch
):
    """E005 Task 9 评审修复:未接受请求的回滚被阻断(无法证明一致性)→
    fail closed:readiness 进入 recovery_required,响应 503 recovery_required。"""
    from fastapi import BackgroundTasks
    from api.compile_transactions import RecoveryResult

    runtime = _repo_runtime(managed_client)
    monkeypatch.setattr(
        "api.main.recover_transaction",
        lambda *args, **kwargs: RecoveryResult(
            job_id="stub", blocked=True, reason="rollback_failed"
        ),
    )
    with patch.object(
        BackgroundTasks, "add_task", side_effect=RuntimeError("queue down")
    ):
        response = managed_client.post("/api/v1/docs/doc_1/recompile")

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "recovery_required"}}
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "recovery_required"


# ─── E005 Task 10:两阶段上传 API 合同 ─────────────────────────────────────────

def test_busy_upload_publishes_no_business_files(
    managed_client, tmp_repo, active_transaction
):
    """E005 Task 10(brief 示例):存在活动事务时上传必须 409
    knowledge_base_busy(精确响应体);业务数据目录字节级不变,
    且不残留 intake staging、不创建新事务。"""
    from api.compile_transactions import list_transaction_dirs

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    response = managed_client.post(
        "/api/v1/upload",
        files={"file": ("a.txt", b"content", "text/plain")},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    assert _business_manifest(tmp_repo) == before
    assert len(list_transaction_dirs(runtime.config)) == 1  # 仅既有活动事务
    staging_root = runtime.config.upload_intake_dir
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


def test_upload_publish_failure_revokes_published_files(
    managed_client, tmp_repo, monkeypatch
):
    """E005 Task 10:发布在 original 之后、raw text 之前失败 → 请求线程立即经
    恢复库回滚(journal 精确撤销本轮 original 并清理 PREPARED 事务),响应 503
    compile_transaction_unavailable;既有业务文件字节不变,无活动事务,
    readiness 不被污染。"""
    import api.upload_intake as intake_mod
    from api.compile_transactions import (
        list_active_manifests,
        list_transaction_dirs,
    )
    from api.durable_fs import durable_write_bytes as real_write_bytes

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    observed = {"raw_text_write": False}

    def failing_write_bytes(path, payload, **kwargs):
        path = Path(path)
        if path.parent.name == "raw" and path.suffix == ".txt":
            observed["raw_text_write"] = True
            raise OSError("simulated durable write failure")
        return real_write_bytes(path, payload, **kwargs)

    monkeypatch.setattr(intake_mod, "durable_write_bytes", failing_write_bytes)
    response = managed_client.post(
        "/api/v1/upload",
        files={"file": ("newdoc.txt", b"brand new content", "text/plain")},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    assert observed["raw_text_write"] is True
    # 本轮 original 已撤销;既有业务文件字节不变
    assert _business_manifest(tmp_repo) == before
    assert list_active_manifests(runtime.config) == []
    # PREPARED-unbound 事务目录已清理
    assert list_transaction_dirs(runtime.config) == []
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"


def test_upload_whitespace_only_returns_422_without_business_writes(
    managed_client, tmp_repo
):
    """E005 Task 10:空白文本在任何 PREPARED/业务写入之前拒绝 → 422
    (既有不可解析合同);无事务、无业务写入、无 staging 残留。"""
    from api.compile_transactions import list_transaction_dirs

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    response = managed_client.post(
        "/api/v1/upload",
        files={"file": ("empty.txt", b"   \n\t  ", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "文件无法解析或内容为空"}
    assert _business_manifest(tmp_repo) == before
    assert list_transaction_dirs(runtime.config) == []
    staging_root = runtime.config.upload_intake_dir
    assert not staging_root.exists() or list(staging_root.iterdir()) == []


def test_upload_unparseable_returns_422_without_business_writes(
    managed_client, tmp_repo
):
    """E005 Task 10:解析失败(IngestParseError)→ 同一 422 合同;
    不写 error meta、不创建事务、不发布任何业务文件。"""
    from api.compile_transactions import list_transaction_dirs

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    response = managed_client.post(
        "/api/v1/upload",
        files={"file": ("broken.txt", b"\xff\xfe\x00\x01", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "文件无法解析或内容为空"}
    assert _business_manifest(tmp_repo) == before
    assert list_transaction_dirs(runtime.config) == []


def test_upload_add_task_failure_fully_revokes_published_upload(
    managed_client, tmp_repo
):
    """E005 Task 10:add_task 失败 = 请求未被接受:本轮发布的
    original/raw text/raw meta 全部撤销(无孤儿 doc、无制造的 error 终态),
    既有业务文件字节不变,响应 503 compile_transaction_unavailable。"""
    from fastapi import BackgroundTasks
    from api.compile_transactions import (
        TransactionState,
        list_active_manifests,
        list_transaction_dirs,
        load_manifest,
        verify_terminal_transaction,
    )

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    with patch.object(
        BackgroundTasks, "add_task", side_effect=RuntimeError("queue down")
    ):
        response = managed_client.post(
            "/api/v1/upload",
            files={"file": ("queued.txt", b"queued upload content", "text/plain")},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    # 本轮上传完全撤销:业务清单与上传前字节一致(无孤儿 original/raw/meta)
    assert _business_manifest(tmp_repo) == before
    assert list_active_manifests(runtime.config) == []
    job_dirs = list_transaction_dirs(runtime.config)
    assert len(job_dirs) == 1
    rolled_back = load_manifest(job_dirs[0])
    assert rolled_back.state is TransactionState.ROLLED_BACK
    # ROLLED_BACK 终态满足清理合同(下次启动绝不阻断)
    assert verify_terminal_transaction(tmp_repo, rolled_back).ok is True
    mode, _reason = runtime.readiness.snapshot()
    assert mode == "ready"


# ─── Codex Round 5 (R5-P2-1):上传接受与 readiness 翻转/清理窗口串行化 ────────

def test_upload_rejected_during_terminal_cleanup_window(managed_client, tmp_repo):
    """R5-P2-1: 无活动事务但后台线程持有执行锁(终态验证/清理窗口)→
    上传 409 knowledge_base_busy;零业务文件发布、无事务目录、无 intake
    残留,绝不登记后台任务。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK
    from api.compile_transactions import list_transaction_dirs

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    COMPILE_EXECUTION_LOCK.acquire()
    try:
        with patch("api.main.run_compile_task") as mock_run:
            response = managed_client.post(
                "/api/v1/upload",
                files={"file": ("fresh.txt", b"fresh upload content", "text/plain")},
            )
    finally:
        COMPILE_EXECUTION_LOCK.release()

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    mock_run.assert_not_called()
    assert _business_manifest(tmp_repo) == before
    assert list_transaction_dirs(runtime.config) == []
    intake_root = runtime.config.upload_intake_dir
    assert not intake_root.exists() or list(intake_root.iterdir()) == []
    assert not COMPILE_EXECUTION_LOCK.locked()


def test_upload_rejected_when_readiness_flips_during_cleanup_window(
    managed_client, tmp_repo
):
    """R5-P2-1 变体: 清理窗口内旧验证器把 readiness 翻转为
    recovery_required → 上传被拒绝(503),无 SCHEDULED 任务入队,
    零业务文件发布。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    runtime = _repo_runtime(managed_client)
    before = _business_manifest(tmp_repo)
    COMPILE_EXECUTION_LOCK.acquire()
    runtime.readiness.mark_recovery_required("terminal_verification_failed")
    try:
        with patch("api.main.run_compile_task") as mock_run:
            response = managed_client.post(
                "/api/v1/upload",
                files={"file": ("fresh.txt", b"fresh upload content", "text/plain")},
            )
    finally:
        COMPILE_EXECUTION_LOCK.release()

    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "recovery_required"}}
    mock_run.assert_not_called()
    assert _business_manifest(tmp_repo) == before


def test_upload_success_releases_execution_lock_for_background_task(
    managed_client, tmp_repo
):
    """R5-P2-1(d): 正常上传接受(200 processing)后执行锁已释放,
    后台任务可立即获取(请求线程绝不把执行锁带过响应)。"""
    from api.compile_jobs import COMPILE_EXECUTION_LOCK

    with patch("api.main.run_compile_task"):
        response = managed_client.post(
            "/api/v1/upload",
            files={"file": ("fresh.txt", b"fresh upload content", "text/plain")},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "processing"
    assert not COMPILE_EXECUTION_LOCK.locked()
    assert COMPILE_EXECUTION_LOCK.acquire(blocking=False)
    COMPILE_EXECUTION_LOCK.release()


# ─── Codex 修复(F4/F9):base_dir 覆盖门禁、runtime 解析根、上传名归一 ─────────

def test_create_app_rejects_foreign_base_dir_without_optin(tmp_path):
    """F4: 路由处理器依赖模块级路径常量(单实例合同);未显式 opt-in 时
    create_app 必须拒绝与模块 BASE_DIR 不同的 base_dir(fail-closed),
    避免 app 对仓库 X 报 ready 却在默认仓库上读写。"""
    with pytest.raises(ValueError):
        create_app(base_dir=tmp_path / "elsewhere")


def test_create_app_allows_foreign_base_dir_with_explicit_optin(tmp_path):
    """F4: 显式 allow_path_override=True 的测试 opt-in 才允许 base_dir
    覆盖(调用方负责同步重绑定模块路径常量)。"""
    app = create_app(base_dir=tmp_path / "elsewhere", allow_path_override=True)
    assert app.state.base_dir == Path(tmp_path / "elsewhere")


def test_schedule_compile_prefers_runtime_resolved_base(tmp_path, monkeypatch):
    """F4: 事务面向调用(prepare/bind/add_task base)必须使用 runtime
    解析根,而非原始模块 BASE_DIR。"""
    import api.main as api_mod
    from fastapi import BackgroundTasks
    from api.runtime_guard import (
        ApiInstanceLock,
        ServiceReadiness,
        load_compile_runtime_config,
    )
    from scripts.doc_admin import read_doc_meta

    module_base = tmp_path / "module-base"
    resolved = tmp_path / "resolved"
    for repo in (module_base, resolved):
        _write_repo_doc(repo, "doc_1", status="raw")
    monkeypatch.setattr(api_mod, "BASE_DIR", module_base)
    config = load_compile_runtime_config(resolved)
    runtime = api_mod.AppRuntime(
        config=config,
        readiness=ServiceReadiness(),
        instance_lock=ApiInstanceLock(resolved / ".runtime" / "api-instance.lock"),
        resolved_base=resolved,
    )
    background = BackgroundTasks()
    api_mod._schedule_compile(background, "doc_1", runtime)

    assert len(background.tasks) == 1
    task = background.tasks[0]
    assert Path(task.args[1]) == resolved
    # meta 绑定发生在 resolved 仓库,而非模块 BASE_DIR 仓库
    assert read_doc_meta("doc_1", base_dir=resolved)["status"] == "compiling"
    assert read_doc_meta("doc_1", base_dir=module_base)["status"] == "raw"


def test_safe_upload_name_normalizes_windows_separators_on_posix(monkeypatch):
    """F9: POSIX 宿主上 Windows 风格 multipart 文件名(C:\\fakepath\\x.pdf)
    也必须归一为 basename,保证唯一性检查、staging 与响应使用同一安全名。
    直接测试归一函数(模拟 POSIX Path 语义),不在 Windows 上跳过。"""
    from pathlib import PurePosixPath

    import api.main as api_mod

    monkeypatch.setattr(api_mod, "Path", PurePosixPath)
    assert api_mod._safe_upload_name("C:\\fakepath\\report.pdf") == "report.pdf"
    assert api_mod._safe_upload_name("/tmp/upload/report.txt") == "report.txt"
    assert api_mod._safe_upload_name("..\\sub\\report.md") == "report.md"
