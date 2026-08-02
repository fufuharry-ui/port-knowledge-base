"""
tests/test_embedding_fallback.py — E002 Embedding 可选降级合同测试

证明 scripts.search.layer1_filter 的运行时合同:
  - Embedding 未配置 / 初始化失败 / 调用失败 → 自动降级 BM25,基础检索不整体失败;
  - Embedding 可用 → 混合检索(BM25 + 向量 RRF 增强 + vector-only 召回)行为不变;
  - EmbeddingClient 直接使用仍保持严格(无 Key 抛 RuntimeError)。

全部用例离线运行: 无真实 Key、无网络、无真实数据目录、不读取 .env。
假 Key 统一使用明显测试值 FAKE_KEY。
"""
import logging

import pytest
import requests
from unittest.mock import MagicMock

import scripts.search as search_mod

FAKE_KEY = "sk-e002-fake-key-not-real-0000"


@pytest.fixture()
def no_network(monkeypatch):
    """任何真实 HTTP 发送尝试立即失败(证明被测代码无网络访问)。"""
    def _blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in offline retrieval tests")
    monkeypatch.setattr("requests.sessions.Session.send", _blocked)


def _bm25_docs():
    """三篇文档: d2 关键词命中 3 次 > d1 命中 1 次 > d3 无命中。"""
    return [
        {"id": "d1", "text": "网络延迟", "ontology_terms": []},
        {"id": "d2", "text": "网络网络网络", "ontology_terms": []},
        {"id": "d3", "text": "完全无关的内容", "ontology_terms": []},
    ]


# ─── F1: 无 Key 自动降级(BM25 有命中) ─────────────────────────────────────────

def test_no_key_falls_back_to_bm25_with_hits(monkeypatch, caplog, no_network):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    with caplog.at_level(logging.WARNING, logger="scripts.search"):
        results = search_mod.layer1_filter("网络", {"documents": _bm25_docs()})

    # 不抛 RuntimeError;按 BM25 原始分数降序;无命中的 d3 不返回
    assert [d["id"] for d in results] == ["d2", "d1"]
    # 降级必须伴随明确 warning
    assert any(
        r.name == "scripts.search" and r.levelno >= logging.WARNING and "BM25" in r.getMessage()
        for r in caplog.records
    ), "Embedding 不可用时必须记录降级 warning"


# ─── F2: 无 Key 且 BM25 无命中 ────────────────────────────────────────────────

def test_no_key_bm25_miss_returns_empty(monkeypatch, no_network):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    docs = [{"id": "d1", "text": "数据质量管理", "ontology_terms": ["数据治理"]}]

    results = search_mod.layer1_filter("完全无关查询XYZ", {"documents": docs})

    assert results == []
    assert len(results) != len(docs), "BM25 无命中时不得返回所有文档"


# ─── F3: Embedding 请求失败(get_embedding 抛异常)仍返回 BM25 结果 ─────────────

def test_request_exception_still_returns_bm25(monkeypatch, no_network):
    monkeypatch.setenv("EMBEDDING_API_KEY", FAKE_KEY)

    def _boom(self, text):
        raise ConnectionError("simulated embedding outage")
    monkeypatch.setattr(
        "scripts.embedding_client.EmbeddingClient.get_embedding", _boom
    )

    results = search_mod.layer1_filter("网络", {"documents": _bm25_docs()})

    assert [d["id"] for d in results] == ["d2", "d1"]


# ─── F4: 请求失败零向量降级不制造伪命中(真实 get_embedding 路径) ───────────────

def test_zero_vector_degrade_creates_no_fake_hits(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", FAKE_KEY)
    intercepted = []

    def _post(self, url, **kwargs):
        intercepted.append(url)
        raise requests.ConnectionError("simulated network unreachable")
    monkeypatch.setattr("requests.sessions.Session.post", _post)

    docs = [{"id": "d1", "text": "数据质量管理", "ontology_terms": ["数据治理"]}]
    results = search_mod.layer1_filter("完全无关查询XYZ", {"documents": docs})

    assert results == [], "零向量余弦为 0,不得制造伪命中"
    assert intercepted, "HTTP 尝试必须被 stub 拦截(未发生真实网络)"


# ─── F5: 降级 warning 安全性(不泄漏密钥/请求头) ───────────────────────────────

def test_fallback_warning_excludes_secrets(monkeypatch, caplog, no_network):
    monkeypatch.setenv("EMBEDDING_API_KEY", FAKE_KEY)

    def _boom(self, text):
        # 异常 message 故意夹带密钥材料: warning 若记录 message 即泄漏
        raise RuntimeError(f"upstream rejected key {FAKE_KEY} in Authorization header")
    monkeypatch.setattr(
        "scripts.embedding_client.EmbeddingClient.get_embedding", _boom
    )

    with caplog.at_level(logging.WARNING, logger="scripts.search"):
        results = search_mod.layer1_filter("网络", {"documents": _bm25_docs()})

    assert [d["id"] for d in results] == ["d2", "d1"]
    warnings = [
        r for r in caplog.records
        if r.name == "scripts.search" and r.levelno >= logging.WARNING
    ]
    assert warnings, "应出现明确的 Embedding 降级 warning"
    assert FAKE_KEY not in caplog.text, "warning 不得包含 API Key"
    assert "Authorization" not in caplog.text, "warning 不得包含 Authorization"


# ─── F6: Embedding 可用 — vector-only 召回(BM25 无命中,相似度超阈值) ──────────

def _inject_fake_client(monkeypatch, fake):
    """让 layer1_filter 内部的 VectorEngine 使用确定性 fake client。"""
    real_ve = search_mod.VectorEngine
    monkeypatch.setattr(
        search_mod, "VectorEngine", lambda docs: real_ve(docs, client=fake)
    )


def test_vector_only_recall_with_injected_client(monkeypatch, no_network):
    fake = MagicMock()
    fake.get_embedding.return_value = [1.0, 0.0, 0.0]
    _inject_fake_client(monkeypatch, fake)

    docs = [
        {"id": "d1", "text": "港口自动化导航", "embedding": [0.99, 0.1, 0.0]},
        {"id": "d2", "text": "苹果种植技术", "embedding": [0.0, 1.0, 0.0]},
    ]
    # 查询与文档无关键词重合(BM25 无命中);d1 余弦 ≈0.995>0.5,d2 余弦 0
    results = search_mod.layer1_filter("量子计算", {"documents": docs})

    assert [d["id"] for d in results] == ["d1"]
    fake.get_embedding.assert_called_once_with("量子计算")


# ─── F7: Embedding 可用 — 混合检索 RRF 排序增强保持 ───────────────────────────

def test_hybrid_rrf_boost_preserved(monkeypatch, no_network):
    fake = MagicMock()
    fake.get_embedding.return_value = [1.0, 0.0, 0.0]
    _inject_fake_client(monkeypatch, fake)

    docs = [
        # BM25 rank: d1(4 命中) > d2(2) > d3(1)
        # vector rank: d2(≈0.995) > d3(≈0.707) > d1(≈0.200)
        {"id": "d1", "text": "网络网络网络网络", "ontology_terms": [],
         "embedding": [0.2, 0.98, 0.0]},
        {"id": "d2", "text": "网络网络", "ontology_terms": [],
         "embedding": [0.99, 0.1, 0.0]},
        {"id": "d3", "text": "网络", "ontology_terms": [],
         "embedding": [0.7, 0.7, 0.0]},
    ]
    results = search_mod.layer1_filter("网络", {"documents": docs})

    ids = [d["id"] for d in results]
    assert set(ids) == {"d1", "d2", "d3"}, "BM25 命中文档应全部保留"
    assert ids[0] == "d2", "RRF 后向量第一名 d2 应超越纯 BM25 第一名 d1"
    assert ids == ["d2", "d1", "d3"]


# ─── F8: 降级模式遵守 top_k 且顺序确定 ────────────────────────────────────────

def test_fallback_respects_top_k(monkeypatch, no_network):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    docs = [
        {"id": f"doc_{i:03d}", "ontology_terms": ["岸桥"], "abstract_short": "岸桥相关"}
        for i in range(30)
    ]

    results = search_mod.layer1_filter("岸桥", {"documents": docs}, top_k=5)

    assert len(results) == 5
    # 同分保持文档原顺序(确定性)
    assert [d["id"] for d in results] == [f"doc_{i:03d}" for i in range(5)]


# ─── F9: 严格性保持与注入接口 ─────────────────────────────────────────────────

def test_vector_engine_without_client_stays_strict(monkeypatch, no_network):
    """无 Key 且未注入 client 时,VectorEngine 仍抛 RuntimeError(不静默)。"""
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        search_mod.VectorEngine([{"id": "d1", "text": "通信"}])


def test_embedding_client_direct_instantiation_stays_strict(monkeypatch, no_network):
    """直接实例化 EmbeddingClient 且无 Key 时仍明确报错(E002 不改客户端)。"""
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    from scripts.embedding_client import EmbeddingClient
    with pytest.raises(RuntimeError):
        EmbeddingClient()


def test_vector_engine_uses_injected_client(no_network):
    """注入 client 时不构造真实 EmbeddingClient,search 直接用注入客户端。"""
    fake = MagicMock()
    fake.get_embedding.return_value = [1.0, 0.0]
    engine = search_mod.VectorEngine(
        [{"id": "d1", "text": "通信要求", "embedding": [1.0, 0.0]}],
        client=fake,
    )
    scores = engine.search("网络延迟")

    assert engine.client is fake
    assert scores["d1"] == pytest.approx(1.0)
    fake.get_embedding.assert_called_once_with("网络延迟")
