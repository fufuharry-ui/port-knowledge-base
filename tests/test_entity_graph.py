"""
test_entity_graph.py — 实体级知识图谱纯逻辑测试(Big-Loop #2)
覆盖:
  - get_entity_neighbors: 多跳邻居(双向,去重)
  - expand_query_with_entities: 查询注入实体邻居
  - 降级:无 entity_relations → 空
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.ontology import (
    get_entity_neighbors,
    expand_query_with_entities,
)


# ─── get_entity_neighbors ─────────────────────────────────────────────────────

class TestEntityNeighbors:
    """E-2/E-3: 实体邻居多跳 + 双向"""

    def _rels(self):
        return [
            {"source": "岸桥远控", "target": "5G专网", "type": "depends_on", "confidence": 0.9},
            {"source": "5G专网", "target": "MEC边缘计算", "type": "supports", "confidence": 0.85},
            {"source": "场桥远控", "target": "5G专网", "type": "depends_on", "confidence": 0.8},
        ]

    def test_direct_neighbors_both_directions(self):
        """5G专网 的邻居:岸桥远控(depends_on 它)+ 场桥远控(depends_on 它)+ MEC(it supports)"""
        nbrs = get_entity_neighbors("5G专网", self._rels(), depth=1)
        assert set(nbrs) == {"岸桥远控", "场桥远控", "MEC边缘计算"}

    def test_multi_hop(self):
        """depth=2:岸桥远控 → 5G专网 → MEC边缘计算"""
        nbrs = get_entity_neighbors("岸桥远控", self._rels(), depth=2)
        assert "5G专网" in nbrs
        assert "MEC边缘计算" in nbrs  # 二跳

    def test_depth_1_excludes_two_hop(self):
        nbrs = get_entity_neighbors("岸桥远控", self._rels(), depth=1)
        assert "5G专网" in nbrs
        assert "MEC边缘计算" not in nbrs

    def test_no_neighbors_returns_empty(self):
        assert get_entity_neighbors("不存在术语", self._rels(), depth=2) == []

    def test_empty_relations(self):
        assert get_entity_neighbors("5G专网", [], depth=2) == []
        assert get_entity_neighbors("5G专网", None, depth=2) == []

    def test_no_self_in_neighbors(self):
        nbrs = get_entity_neighbors("5G专网", self._rels(), depth=2)
        assert "5G专网" not in nbrs


# ─── expand_query_with_entities ───────────────────────────────────────────────

class TestExpandQueryWithEntities:
    """E-2: 查询注入实体邻居"""

    def test_expansion_includes_entity_neighbors(self):
        """查 岸桥远控,其 depends_on 目标 5G专网 进入扩展"""
        query = "岸桥远控 的网络"
        rels = [{"source": "岸桥远控", "target": "5G专网", "type": "depends_on", "confidence": 0.9}]
        expansions = expand_query_with_entities(query, rels)
        assert "5G专网" in expansions

    def test_expansion_reverse_direction(self):
        """查 5G专网,反向(depends_on 它的)岸桥远控 也扩展"""
        query = "5G专网"
        rels = [{"source": "岸桥远控", "target": "5G专网", "type": "depends_on", "confidence": 0.9}]
        expansions = expand_query_with_entities(query, rels)
        assert "岸桥远控" in expansions

    def test_no_entity_match_returns_empty(self):
        rels = [{"source": "A", "target": "B", "type": "depends_on", "confidence": 0.9}]
        assert expand_query_with_entities("完全无关XYZ", rels) == []

    def test_empty_relations_degrades(self):
        """E-4: 无实体关系 → 空(降级)"""
        assert expand_query_with_entities("岸桥远控", []) == []
        assert expand_query_with_entities("岸桥远控", None) == []

    def test_combined_with_existing_expansion(self):
        """实体扩展可与 #1 本体扩展并存(去重由调用方合并)"""
        rels = [
            {"source": "岸桥远控", "target": "5G专网", "type": "depends_on", "confidence": 0.9},
            {"source": "岸桥远控", "target": "MEC", "type": "depends_on", "confidence": 0.8},
        ]
        expansions = expand_query_with_entities("岸桥远控", rels)
        assert "5G专网" in expansions
        assert "MEC" in expansions
