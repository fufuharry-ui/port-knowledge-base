"""
test_search_ontology.py — 检索层接入本体扩展的集成测试(Big-Loop #1)
验证:
  - layer1_filter 接受 ontology 参数,扩展词参与 BM25 加分
  - ontology=None 时行为与旧版完全一致(向后兼容,不破现有 test_search.py)
  - 本体扩展捞出"字面词 miss、本体相关"的文档(U-3 核心)

需要 patch_search_paths 同步 patch GLOBAL_ONTOLOGY_FILE(见 conftest 改动)。

注:为隔离本体扩展效果,VectorEngine 在相关用例中被 mock 为返回空
(向量路径由 test_hybrid_search.py 单独覆盖)——这是合法的单元隔离,
非掩盖缺陷。
"""
import sys
from pathlib import Path
from unittest.mock import patch

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
import yaml

from sample_data import set_llm_response


def _no_vector():
    """禁用 VectorEngine(返回空得分),隔离 BM25 + 本体扩展路径。"""
    mock = patch("scripts.search.VectorEngine")
    mock_obj = mock.start()
    mock_obj.return_value.search.return_value = {}
    return mock


class TestLayer1OntologyExpansion:
    """U-3: 本体扩展使相关文档进入候选"""

    def test_ontology_expansion_pulls_sibling_doc(self, patch_search_paths):
        """doc_B 仅标了 自动化轨道吊(岸桥远控 的兄弟,无子词/语义重叠);
        查 岸桥远控:无本体 doc_B BM25 miss,有本体靠兄弟词捞回。"""
        index = {"documents": [
            {"id": "doc_A", "ontology_terms": ["岸桥远控"],
             "abstract_short": "岸桥远控系统"},
            {"id": "doc_B", "ontology_terms": ["自动化轨道吊"],
             "abstract_short": "轨道吊堆存作业"},
        ]}
        ontology_tree = [
            {"term": "港口自动化", "parent": None, "definition": "", "children": [
                {"term": "岸桥远控", "parent": "港口自动化", "definition": "", "children": []},
                {"term": "自动化轨道吊", "parent": "港口自动化", "definition": "", "children": []},
            ]},
        ]

        m = _no_vector()
        try:
            # 无本体:doc_B 不在候选(与查询无 BM25 命中)
            no_ont = patch_search_paths.layer1_filter("岸桥远控", index, top_k=20)
            no_ont_ids = {d["id"] for d in no_ont}
            assert "doc_A" in no_ont_ids
            assert "doc_B" not in no_ont_ids  # 字面 miss

            # 有本体:兄弟词 自动化轨道吊 被扩展进来,doc_B 进入候选
            with_ont = patch_search_paths.layer1_filter(
                "岸桥远控", index, top_k=20, ontology={"ontology_tree": ontology_tree}
            )
            with_ont_ids = {d["id"] for d in with_ont}
            assert "doc_A" in with_ont_ids
            assert "doc_B" in with_ont_ids  # 靠本体扩展捞回
        finally:
            m.stop()

    def test_ontology_none_is_backward_compatible(self, patch_search_paths):
        """ontology=None 时结果与不传完全一致(不破 test_search.py 现有断言)"""
        index = {"documents": [
            {"id": "doc_001", "ontology_terms": ["岸桥远控", "5G专网"],
             "abstract_short": "基于5G的岸桥远控系统"},
            {"id": "doc_002", "ontology_terms": ["数据治理"],
             "abstract_short": "港口数据治理方案"},
        ]}
        m = _no_vector()
        try:
            r_default = patch_search_paths.layer1_filter("岸桥远控 网络延迟", index)
            r_none = patch_search_paths.layer1_filter(
                "岸桥远控 网络延迟", index, ontology=None
            )
            assert [d["id"] for d in r_default] == [d["id"] for d in r_none]
        finally:
            m.stop()

    def test_empty_ontology_tree_degrades_to_bm25(self, patch_search_paths):
        """U-4 降级:本体树为空 → 不报错,等价无本体"""
        index = {"documents": [
            {"id": "doc_001", "ontology_terms": ["岸桥远控"],
             "abstract_short": "岸桥远控系统"},
        ]}
        m = _no_vector()
        try:
            # 空树
            r1 = patch_search_paths.layer1_filter(
                "岸桥远控", index, ontology={"ontology_tree": []}
            )
            assert [d["id"] for d in r1] == ["doc_001"]
            # 缺失键
            r2 = patch_search_paths.layer1_filter(
                "岸桥远控", index, ontology={}
            )
            assert [d["id"] for d in r2] == ["doc_001"]
        finally:
            m.stop()
