import pytest
from unittest.mock import MagicMock
from scripts.search import BM25Engine, VectorEngine, reciprocal_rank_fusion

def test_bm25_exact_match():
    docs = [
        {"id": "d1", "text": "港口岸桥远控的网络延迟要求"},
        {"id": "d2", "text": "关于卡车调度的优化策略"},
        {"id": "d3", "text": "岸桥起升机构的故障分析"}
    ]
    engine = BM25Engine(docs)
    scores = engine.search("岸桥远控")

    # 期望 d1 分数最高
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    assert ranked[0][0] == "d1"

def test_vector_semantic_search():
    # E002: 经 client 注入确定性假客户端,不构造真实 EmbeddingClient,
    # 不设置真实 Key,不访问网络
    docs = [
        {"id": "d1", "text": "通信要求很高", "embedding": [1.0, 0.0, 0.0]},
        {"id": "d2", "text": "苹果很好吃", "embedding": [0.0, 1.0, 0.0]}
    ]
    fake_client = MagicMock()
    fake_client.get_embedding.return_value = [0.9, 0.1, 0.0]

    engine = VectorEngine(docs, client=fake_client)
    scores = engine.search("网络延迟")
    assert scores.get("d1", 0) > scores.get("d2", 0)
    # 文档自带 embedding,仅需一次查询向量调用
    fake_client.get_embedding.assert_called_once_with("网络延迟")

def test_rrf_fusion():
    bm25_scores = {"d1": 10.5, "d2": 5.2, "d3": 1.1}
    vector_scores = {"d2": 0.85, "d3": 0.92, "d1": 0.1}
    
    # bm25 rank: d1(1), d2(2), d3(3)
    # vector rank: d3(1), d2(2), d1(3)
    # rrf_score = 1 / (60 + rank)
    fused = reciprocal_rank_fusion(bm25_scores, vector_scores, k=60)
    
    # d1 = 1/61 + 1/63 = 0.01639 + 0.01587 = 0.03226
    # d2 = 1/62 + 1/62 = 0.01612 + 0.01612 = 0.03225
    # d3 = 1/63 + 1/61 = 0.01587 + 0.01639 = 0.03226
    
    assert "d1" in fused
    assert "d2" in fused
    assert "d3" in fused
