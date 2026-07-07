"""
test_cross_doc_entities.py — 跨文档实体关系推断测试(Loop #6)
覆盖 infer_cross_doc_relations 纯函数:经共享枢纽术语推断跨文档边。
纯逻辑,无文件 IO、无 LLM。
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.ontology import infer_cross_doc_relations


class TestInferCrossDocRelations:
    """跨文档实体关系推断:经共享枢纽发现单文档未声明的关联。"""

    def test_shared_hub_infers_cross_doc_edge(self):
        """两文档经共享枢纽 H 关联 → 推断 A↔B(跨文档新知识)。

        doc_A: 网络切片 → 5G专网(supports)
        doc_B: 边缘计算 → 5G专网(supports)
        → 推断: 网络切片 ↔ 边缘计算(related_to,经 5G专网 枢纽)
        这条边没有任何单文档直接说过。
        """
        edges = [
            {"source": "网络切片", "target": "5G专网", "type": "supports",
             "doc_id": "doc_A", "confidence": 0.9},
            {"source": "边缘计算", "target": "5G专网", "type": "supports",
             "doc_id": "doc_B", "confidence": 0.85},
        ]
        inferred = infer_cross_doc_relations(edges)
        # 应推断出 网络切片 ↔ 边缘计算
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        assert frozenset({"网络切片", "边缘计算"}) in pairs, \
            f"应经 5G专网 枢纽推断 网络切片↔边缘计算,实际: {inferred}"

    def test_inferred_edge_has_cross_doc_provenance(self):
        """推断边标注 provenance=cross_doc_inferred,且 confidence 折扣(低于源边)。"""
        edges = [
            {"source": "A", "target": "H", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H", "type": "supports", "doc_id": "doc_B", "confidence": 0.8},
        ]
        inferred = infer_cross_doc_relations(edges)
        assert len(inferred) == 1
        e = inferred[0]
        assert e.get("provenance") == "cross_doc_inferred"
        assert e["type"] == "related_to"
        # 折扣:低于源边的最小值
        assert e["confidence"] < 0.8

    def test_no_inference_when_hub_only_in_one_doc(self):
        """枢纽术语只出现在一个文档 → 不推断(无跨文档证据)。"""
        edges = [
            {"source": "A", "target": "H", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},  # 同一文档
        ]
        inferred = infer_cross_doc_relations(edges)
        # A-H-B 都在 doc_A → 不算跨文档,不推断新边(A↔B 同文档内,留给单文档抽取)
        assert inferred == []

    def test_no_inference_without_shared_hub(self):
        """两文档无共享枢纽 → 不推断。"""
        edges = [
            {"source": "A", "target": "H1", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H2", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
        ]
        assert infer_cross_doc_relations(edges) == []

    def test_dedup_inferred_edges(self):
        """同一对术语经多个枢纽推断 → 只保留一条(去重)。"""
        edges = [
            {"source": "A", "target": "H1", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H1", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
            {"source": "A", "target": "H2", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H2", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges)
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        assert len(pairs) == 1  # 只有 A↔B 一对(去重)

    def test_existing_direct_edge_not_reinferred(self):
        """若 A↔B 已有直接边 → 不重复推断(避免冗余)。"""
        edges = [
            {"source": "A", "target": "H", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "B", "target": "H", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
            # A↔B 已直接存在(某文档声明过)
            {"source": "A", "target": "B", "type": "depends_on", "doc_id": "doc_C", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges)
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        assert frozenset({"A", "B"}) not in pairs, "已有直接边不应重复推断"

    def test_empty_or_none_input(self):
        assert infer_cross_doc_relations([]) == []
        assert infer_cross_doc_relations(None) == []

    def test_multi_hop_inference(self):
        """三文档经同一枢纽关联 → 推断出多对跨文档边。

        doc_A: X→H, doc_B: Y→H, doc_C: Z→H
        → 推断 X↔Y, X↔Z, Y↔Z(三对)
        """
        edges = [
            {"source": "X", "target": "H", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "Y", "target": "H", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
            {"source": "Z", "target": "H", "type": "supports", "doc_id": "doc_C", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges)
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        assert frozenset({"X", "Y"}) in pairs
        assert frozenset({"X", "Z"}) in pairs
        assert frozenset({"Y", "Z"}) in pairs


class TestOntologyParentInference:
    """经本体父类推断跨文档边(Loop #6 扩展)。

    真实数据诊断:表面术语跨文档重合为 0(实体抽取太文档孤岛),
    但本体分类是跨文档的——经本体共同父类可推断真实边。
    """

    def _tree(self):
        """本体树:通信技术 → {5G技术, 光纤网络}"""
        return [
            {"term": "通信技术", "parent": None, "definition": "",
             "children": [
                 {"term": "5G技术", "parent": "通信技术", "definition": "", "children": []},
                 {"term": "光纤网络", "parent": "通信技术", "definition": "", "children": []},
             ]},
        ]

    def test_shared_ontology_parent_infers_cross_doc_edge(self):
        """两文档的实体术语共享本体父类 → 推断跨文档边。"""
        edges = [
            # doc_A 的实体是 5G技术,doc_B 的实体是 光纤网络,无表面术语重合
            {"source": "5G技术", "target": "某系统A", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "光纤网络", "target": "某系统B", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges, ontology_tree=self._tree())
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        # 5G技术 ↔ 光纤网络(经父类 通信技术)— 表面术语无重合也能推断
        assert frozenset({"5G技术", "光纤网络"}) in pairs, \
            f"经本体父类应推断 5G技术↔光纤网络,实际: {inferred}"

    def test_ontology_inference_absent_without_tree(self):
        """不传 ontology_tree → 不做父类推断(向后兼容,只做表面枢纽)。"""
        edges = [
            {"source": "5G技术", "target": "X", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "光纤网络", "target": "Y", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
        ]
        # 无 tree:5G技术 与 光纤网络 无表面重合 → 0 条
        assert infer_cross_doc_relations(edges) == []
        # 有 tree:推断出
        assert len(infer_cross_doc_relations(edges, ontology_tree=self._tree())) >= 1

    def test_ontology_inference_requires_different_docs(self):
        """共享父类但来自同一文档 → 不推断(留给单文档)。"""
        edges = [
            {"source": "5G技术", "target": "X", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "光纤网络", "target": "Y", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges, ontology_tree=self._tree())
        pairs = {frozenset({e["source"], e["target"]}) for e in inferred}
        assert frozenset({"5G技术", "光纤网络"}) not in pairs

    def test_ontology_inferred_edge_has_provenance_and_hub(self):
        """本体推断边标注 via_hub=父类术语 + cross_doc_inferred。"""
        edges = [
            {"source": "5G技术", "target": "X", "type": "supports", "doc_id": "doc_A", "confidence": 0.9},
            {"source": "光纤网络", "target": "Y", "type": "supports", "doc_id": "doc_B", "confidence": 0.9},
        ]
        inferred = infer_cross_doc_relations(edges, ontology_tree=self._tree())
        ont_edges = [e for e in inferred if e.get("via_hub") == "通信技术"]
        assert len(ont_edges) >= 1
        assert ont_edges[0]["provenance"] == "cross_doc_inferred"
