"""
test_search_cache.py — Layer2 结果缓存测试 (Big-Loop #5 P-2)
覆盖: 同查询+同候选集 60s 内复用,LLM 只调一次;过期/不同查询不复用。
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.search import layer2_score, _layer2_cache_reset


def _cands():
    return [
        {"id": "doc_A", "title": "A", "abstract_short": "岸桥远控"},
        {"id": "doc_B", "title": "B", "abstract_short": "5G网络"},
    ]


@patch("scripts.search.llm_call_json")
def test_cache_hits_same_query_skips_llm(mock_llm):
    """P-2: 同 query+同候选集 第二次不调 LLM(命中缓存)。"""
    _layer2_cache_reset()
    mock_llm.return_value = {"scores": [{"doc_id": "doc_A", "score": 0.9}]}

    layer2_score("岸桥延迟", _cands(), None, "m", top_k=5)
    layer2_score("岸桥延迟", _cands(), None, "m", top_k=5)

    assert mock_llm.call_count == 1, "第二次应命中缓存,不调 LLM"


@patch("scripts.search.llm_call_json")
def test_cache_miss_on_different_query(mock_llm):
    """不同 query → 不命中,各调一次。"""
    _layer2_cache_reset()
    mock_llm.return_value = {"scores": [{"doc_id": "doc_A", "score": 0.9}]}

    layer2_score("查询甲", _cands(), None, "m")
    layer2_score("查询乙", _cands(), None, "m")

    assert mock_llm.call_count == 2


@patch("scripts.search.llm_call_json")
def test_cache_miss_on_different_candidates(mock_llm):
    """同 query 但候选集不同 → 不命中。"""
    _layer2_cache_reset()
    mock_llm.return_value = {"scores": [{"doc_id": "doc_A", "score": 0.9}]}

    layer2_score("q", _cands(), None, "m")
    other = [{"id": "doc_C", "title": "C", "abstract_short": "x"}]
    layer2_score("q", other, None, "m")

    assert mock_llm.call_count == 2


@patch("scripts.search.llm_call_json")
def test_cache_returns_same_result(mock_llm):
    """命中缓存时返回与首次一致的结果。"""
    _layer2_cache_reset()
    mock_llm.return_value = {"scores": [
        {"doc_id": "doc_A", "score": 0.9},
        {"doc_id": "doc_B", "score": 0.3},
    ]}

    r1 = layer2_score("岸桥", _cands(), None, "m", top_k=5)
    r2 = layer2_score("岸桥", _cands(), None, "m", top_k=5)
    assert r1 == r2
    assert mock_llm.call_count == 1
