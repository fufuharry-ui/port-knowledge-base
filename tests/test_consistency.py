"""
test_consistency.py — 一致性推理纯逻辑测试(Big-Loop #3)
覆盖:
  - find_contradiction_candidates: 从文档级关系 + 共享实体术语生成矛盾检测候选对
  - 纯逻辑,无 LLM、无文件 IO
"""
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.consistency import (
    find_contradiction_candidates,
    detect_contradiction,
    detect_contradictions_batch,
    contradictions_for_docs,
)


class TestContradictionCandidates:
    """C-1: 矛盾检测候选对生成"""

    def test_same_topic_pair_is_candidate(self):
        """两文档 same_topic → 候选对"""
        kg_edges = [
            {"source": "doc_A", "target": "doc_B", "type": "same_topic", "confidence": 0.9},
        ]
        entity_relations = []
        pairs = find_contradiction_candidates(kg_edges, entity_relations)
        assert ("doc_A", "doc_B") in pairs or ("doc_B", "doc_A") in pairs

    def test_shared_entity_terms_make_candidate(self):
        """两文档无 same_topic 边,但都涉及同一实体(通过 entity_relations 关联同一术语)→ 候选"""
        kg_edges = []
        entity_relations = [
            {"source": "岸桥远控", "target": "5G专网", "type": "depends_on",
             "doc_id": "doc_A", "confidence": 0.9},
            {"source": "岸桥远控", "target": "5G专网", "type": "depends_on",
             "doc_id": "doc_B", "confidence": 0.9},
        ]
        pairs = find_contradiction_candidates(kg_edges, entity_relations)
        # doc_A 与 doc_B 都声明了相同实体关系 → 候选
        assert any(frozenset(p) == frozenset({"doc_A", "doc_B"}) for p in pairs)

    def test_no_overlap_no_candidate(self):
        """两文档无任何关联 → 无候选"""
        kg_edges = []
        entity_relations = [
            {"source": "X", "target": "Y", "type": "depends_on", "doc_id": "doc_A"},
        ]
        pairs = find_contradiction_candidates(kg_edges, entity_relations)
        assert pairs == []

    def test_dedup_pairs(self):
        """同一对文档不重复生成"""
        kg_edges = [
            {"source": "doc_A", "target": "doc_B", "type": "same_topic"},
            {"source": "doc_B", "target": "doc_A", "type": "supplements"},
        ]
        pairs = find_contradiction_candidates(kg_edges, [])
        # 去重后只有一对
        normalized = {frozenset(p) for p in pairs}
        assert len(normalized) == 1

    def test_empty_inputs(self):
        assert find_contradiction_candidates([], []) == []
        assert find_contradiction_candidates(None, None) == []

    def test_low_confidence_same_topic_excluded(self):
        """confidence < 0.7 的 same_topic 不算候选(弱关联)"""
        kg_edges = [
            {"source": "doc_A", "target": "doc_B", "type": "same_topic", "confidence": 0.5},
        ]
        pairs = find_contradiction_candidates(kg_edges, [])
        assert pairs == []


# ─── C-2: LLM 矛盾判定 ────────────────────────────────────────────────

def _mock_client_with_json(payload: dict):
    """构造一个 mock OpenAI client,其 chat.completions.create 返回给定 JSON。"""
    client = MagicMock()
    msg = MagicMock()
    msg.content = json.dumps(payload, ensure_ascii=False)  # msg 即 choice.message
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    return client


class TestDetectContradiction:
    """C-2: LLM 判定两文档矛盾 → 返回 has_conflict + 推理链。"""

    def test_detects_conflict(self, patch_consistency_paths):
        """LLM 判定冲突 → 返回 has_conflict=True + 推理链"""
        mod = patch_consistency_paths
        index = {"documents": [
            {"id": "doc_A", "title": "A", "abstract_short": "端到端延迟应低于10ms"},
            {"id": "doc_B", "title": "B", "abstract_short": "端到端延迟要求20ms以内"},
        ]}
        client = _mock_client_with_json({
            "has_conflict": True,
            "conflict_point": "端到端延迟要求",
            "reasoning_chain": "A说低于10ms,B说20ms以内,数值冲突",
            "confidence": 0.85,
        })
        result = detect_contradiction("doc_A", "doc_B", client, "m", index=index)
        assert result is not None
        assert result["has_conflict"] is True
        assert "10ms" in result["reasoning_chain"] or "冲突" in result["reasoning_chain"]
        assert result["confidence"] >= 0.7

    def test_no_conflict(self, patch_consistency_paths):
        """LLM 判定无冲突 → has_conflict=False"""
        index = {"documents": [
            {"id": "doc_A", "title": "A", "abstract_short": "讲网络架构"},
            {"id": "doc_B", "title": "B", "abstract_short": "讲机械结构"},
        ]}
        client = _mock_client_with_json({
            "has_conflict": False,
            "conflict_point": "",
            "reasoning_chain": "",
            "confidence": 0.9,
        })
        result = detect_contradiction("doc_A", "doc_B", client, "m", index=index)
        assert result is not None
        assert result["has_conflict"] is False

    def test_missing_doc_returns_none(self, patch_consistency_paths):
        """任一文档不在 index → None(不调 LLM)"""
        index = {"documents": [{"id": "doc_A", "title": "A", "abstract_short": "x"}]}
        client = MagicMock()
        result = detect_contradiction("doc_A", "doc_MISSING", client, "m", index=index)
        assert result is None
        client.chat.completions.create.assert_not_called()

    def test_bad_json_returns_none(self, patch_consistency_paths):
        """LLM 返回非 JSON → None(降级,不崩)"""
        index = {"documents": [
            {"id": "doc_A", "title": "A", "abstract_short": "x"},
            {"id": "doc_B", "title": "B", "abstract_short": "y"},
        ]}
        client = MagicMock()
        msg = MagicMock()
        msg.content = "这不是JSON"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        client.chat.completions.create.return_value = resp
        result = detect_contradiction("doc_A", "doc_B", client, "m", index=index)
        assert result is None


class TestContradictionsForDocs:
    """contradictions_for_docs:从已知矛盾中筛选涉及给定文档集的(供 Layer3)。"""

    def test_both_docs_in_top_set_returned(self):
        """矛盾对的两个文档都在 Top 集合 → 命中"""
        contradictions = [
            {"doc_a": "A", "doc_b": "B", "conflict_point": "x"},
            {"doc_a": "C", "doc_b": "D", "conflict_point": "y"},
        ]
        hits = contradictions_for_docs(["A", "B", "E"], contradictions)
        assert len(hits) == 1
        assert hits[0]["conflict_point"] == "x"

    def test_only_one_doc_in_set_not_returned(self):
        """矛盾对只有一个文档在 Top 集合 → 不算(Top 内部无矛盾)"""
        contradictions = [
            {"doc_a": "A", "doc_b": "Z", "conflict_point": "x"},
        ]
        hits = contradictions_for_docs(["A", "B"], contradictions)
        assert hits == []

    def test_empty_top_set(self):
        assert contradictions_for_docs([], [{"doc_a": "A", "doc_b": "B"}]) == []


# ─── 批量化稽核(Loop #9,解决规模化瓶颈)─────────────────────────────────────

class TestBatchContradictionDetection:
    """detect_contradictions_batch: 多对一次 LLM,把 N 次调用降到 N/batch_size 次。"""

    def _client_returning_batch(self, results: list[dict]):
        """mock client,chat.completions.create 返回含 results 数组的 JSON。"""
        client = MagicMock()
        msg = MagicMock()
        msg.content = json.dumps({"results": results}, ensure_ascii=False)
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        client.chat.completions.create.return_value = resp
        return client

    def test_batch_reduces_llm_call_count(self, patch_consistency_paths):
        """5 对 + batch_size=5 → 仅 1 次 LLM 调用(而非 5 次)。"""
        mod = patch_consistency_paths
        index = {"documents": [
            {"id": f"doc_{i}", "title": f"T{i}", "abstract_short": f"摘要{i}"}
            for i in range(2)  # 2 docs → 1 pair, 放大用 below
        ]}
        # 构造 5 对(用 10 个文档)
        docs = [{"id": f"d{i}", "title": f"T{i}", "abstract_short": f"摘要{i}"} for i in range(10)]
        index = {"documents": docs}
        pairs = [(f"d{i}", f"d{i+1}") for i in range(0, 10, 2)]  # 5 对
        results = [{"doc_a": a, "doc_b": b, "has_conflict": False, "confidence": 0.9}
                   for a, b in pairs]
        client = self._client_returning_batch(results)

        out = detect_contradictions_batch(pairs, client, "m", index=index, batch_size=5)
        assert client.chat.completions.create.call_count == 1  # 5对1批=1次
        assert len(out) == 5

    def test_batch_splits_when_exceeding_batch_size(self, patch_consistency_paths):
        """12 对 + batch_size=5 → 3 次调用(5+5+2)。"""
        docs = [{"id": f"d{i}", "title": f"T{i}", "abstract_short": f"摘要{i}"} for i in range(24)]
        index = {"documents": docs}
        pairs = [(f"d{i}", f"d{i+1}") for i in range(0, 24, 2)]  # 12 对
        client = self._client_returning_batch(
            [{"doc_a": a, "doc_b": b, "has_conflict": False, "confidence": 0.9} for a, b in pairs]
        )
        detect_contradictions_batch(pairs, client, "m", index=index, batch_size=5)
        assert client.chat.completions.create.call_count == 3  # ceil(12/5)=3

    def test_batch_preserves_verdicts(self, patch_consistency_paths):
        """批量返回的冲突判定正确映射到对应文档对。"""
        docs = [{"id": "d0", "title": "A", "abstract_short": "延迟10ms"},
                {"id": "d1", "title": "B", "abstract_short": "延迟20ms"},
                {"id": "d2", "title": "C", "abstract_short": "讲机械"}]
        index = {"documents": docs}
        pairs = [("d0", "d1"), ("d0", "d2")]
        results = [
            {"doc_a": "d0", "doc_b": "d1", "has_conflict": True,
             "conflict_point": "延迟", "reasoning_chain": "10ms vs 20ms", "confidence": 0.85},
            {"doc_a": "d0", "doc_b": "d2", "has_conflict": False, "confidence": 0.9},
        ]
        client = self._client_returning_batch(results)
        out = detect_contradictions_batch(pairs, client, "m", index=index, batch_size=5)
        conflict = [r for r in out if r and r.get("has_conflict")]
        assert len(conflict) == 1
        assert conflict[0]["conflict_point"] == "延迟"

    def test_batch_empty_pairs(self, patch_consistency_paths):
        client = MagicMock()
        out = detect_contradictions_batch([], client, "m", index={"documents": []})
        assert out == []
        client.chat.completions.create.assert_not_called()

    def test_batch_degrades_on_bad_json(self, patch_consistency_paths):
        """某批 LLM 返回非 JSON → 该批返回 None(不崩,降级)。"""
        docs = [{"id": "d0", "title": "A", "abstract_short": "x"},
                {"id": "d1", "title": "B", "abstract_short": "y"}]
        index = {"documents": docs}
        client = MagicMock()
        msg = MagicMock()
        msg.content = "这不是JSON"
        choice = MagicMock()
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]
        client.chat.completions.create.return_value = resp
        out = detect_contradictions_batch([("d0", "d1")], client, "m", index=index, batch_size=5)
        # 坏 JSON → 该对结果为 None
        assert len(out) == 1
        assert out[0] is None
