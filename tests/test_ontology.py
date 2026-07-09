"""
test_ontology.py — 本体纯逻辑测试(Big-Loop #1 新增)
覆盖:
  - merge_ontology_nodes:真树插入(消除顶层孤儿、建中间父节点)
  - expand_query_with_ontology:查询扩展(上位/兄弟词注入)
  - 优雅降级:本体缺失/为空 → 返回空扩展集,不报错

设计:scripts/ontology.py 为纯函数(入参传 tree,无文件 IO),
故无需 patch_*_paths fixture,直接调用。
"""
import sys
from pathlib import Path

# 确保项目根可导入
_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest

from scripts.ontology import (
    merge_ontology_nodes,
    expand_query_with_ontology,
    find_node,
    get_ancestors,
    get_siblings,
    rebuild_tree_from_nodes,
)


# ─── merge_ontology_nodes: 真树插入 ────────────────────────────────────────────

class TestMergeBuildsRealTree:
    """U-1 / U-2: 合并必须把节点插到真实 parent 下,而非顶层孤儿"""

    def test_new_node_nested_under_existing_parent(self):
        """parent 已存在于树中 → 挂其 children 下,不出现在顶层"""
        tree = [
            {"term": "智慧港口", "parent": None, "definition": "root", "children": [
                {"term": "港口自动化", "parent": "智慧港口", "definition": "auto",
                 "children": []},
            ]},
        ]
        new_nodes = [
            {"term": "岸桥远控", "parent": "港口自动化", "definition": "remote crane",
             "is_new_node": True},
        ]

        added = merge_ontology_nodes(tree, new_nodes)

        # 顶层仍只有 智慧港口(岸桥远控 不在顶层)
        top_terms = [n["term"] for n in tree]
        assert "岸桥远控" not in top_terms
        # 岸桥远控 嵌套在 港口自动化.children 下
        auto = find_node(tree, "港口自动化")
        assert auto is not None
        child_terms = [c["term"] for c in auto["children"]]
        assert "岸桥远控" in child_terms
        # 返回真实新增计数
        assert added == 1

    def test_missing_parent_with_grandparent_creates_intermediate(self):
        """parent 缺失但 grandparent 存在 → 建中间 parent 再挂(U-2 核心)"""
        tree = [
            {"term": "基础设施", "parent": None, "definition": "infra", "children": []},
        ]
        new_nodes = [
            {"term": "5G专网", "parent": "通信技术", "grandparent": "基础设施",
             "definition": "5G sa", "is_new_node": True},
        ]

        added = merge_ontology_nodes(tree, new_nodes)

        # 通信技术 应被建为 基础设施 的子节点
        comm = find_node(tree, "通信技术")
        assert comm is not None
        assert comm["parent"] == "基础设施"
        # 5G专网 挂在 通信技术 下
        assert "5G专网" in [c["term"] for c in comm["children"]]
        # 新增 = 通信技术 + 5G专网
        assert added == 2

    def test_no_top_level_orphans_for_known_parent_chain(self):
        """SAMPLE 场景:岸桥远控→港口自动化(在树中)、5G专网→通信技术(不在,grandparent 基础设施 在)"""
        tree = [
            {"term": "智慧港口", "parent": None, "definition": "root", "children": [
                {"term": "港口自动化", "parent": "智慧港口", "definition": "auto",
                 "children": []},
            ]},
            {"term": "基础设施", "parent": None, "definition": "infra", "children": []},
        ]
        new_nodes = [
            {"term": "岸桥远控", "parent": "港口自动化", "grandparent": "智慧港口",
             "definition": "d1", "is_new_node": True},
            {"term": "5G专网", "parent": "通信技术", "grandparent": "基础设施",
             "definition": "d2", "is_new_node": True},
        ]

        added = merge_ontology_nodes(tree, new_nodes)

        # 顶层只有两个根
        assert {n["term"] for n in tree} == {"智慧港口", "基础设施"}
        # 岸桥远控 嵌套
        assert find_node(tree, "岸桥远控") is not None
        assert find_node(tree, "岸桥远控")["parent"] == "港口自动化"
        # 5G专网 经由新建的 通信技术 嵌套
        assert find_node(tree, "5G专网") is not None
        assert find_node(tree, "5G专网")["parent"] == "通信技术"
        assert find_node(tree, "通信技术")["parent"] == "基础设施"
        # 新增 = 岸桥远控(1) + 通信技术 + 5G专网(2) = 3
        assert added == 3

    def test_existing_term_not_duplicated(self):
        """术语已存在 → 不重复添加,计数为 0"""
        tree = [
            {"term": "智慧港口", "parent": None, "definition": "root", "children": [
                {"term": "岸桥远控", "parent": "智慧港口", "definition": "exists",
                 "children": []},
            ]},
        ]
        new_nodes = [
            {"term": "岸桥远控", "parent": "智慧港口", "definition": "dup",
             "is_new_node": True},
        ]

        added = merge_ontology_nodes(tree, new_nodes)
        assert added == 0
        # 不新建第二个岸桥远控
        assert len([n for n in tree if n["term"] == "岸桥远控"]) == 0  # 顶层无
        auto = find_node(tree, "智慧港口")
        assert sum(1 for c in auto["children"] if c["term"] == "岸桥远控") == 1

    def test_empty_new_nodes_returns_zero(self):
        tree = [{"term": "根", "parent": None, "definition": "", "children": []}]
        assert merge_ontology_nodes(tree, []) == 0


# ─── expand_query_with_ontology: 查询扩展 ──────────────────────────────────────

class TestQueryExpansion:
    """U-3 / U-4: 本体查询扩展 + 降级"""

    def _sample_tree(self):
        return [
            {"term": "智慧港口", "parent": None, "definition": "", "children": [
                {"term": "港口自动化", "parent": "智慧港口", "definition": "", "children": [
                    {"term": "岸桥远控", "parent": "港口自动化", "definition": "", "children": []},
                    {"term": "场桥远控", "parent": "港口自动化", "definition": "", "children": []},
                ]},
            ]},
        ]

    def test_expansion_includes_siblings(self):
        """命中 岸桥远控 → 兄弟 场桥远控 进入扩展集"""
        tree = self._sample_tree()
        expansions = expand_query_with_ontology("岸桥远控 的网络延迟", tree)
        assert "场桥远控" in expansions

    def test_expansion_includes_ancestor(self):
        """命中 岸桥远控 → 上位 港口自动化 进入扩展集"""
        tree = self._sample_tree()
        expansions = expand_query_with_ontology("岸桥远控", tree)
        assert "港口自动化" in expansions

    def test_expansion_excludes_self(self):
        tree = self._sample_tree()
        expansions = expand_query_with_ontology("岸桥远控", tree)
        assert "岸桥远控" not in expansions

    def test_no_ontology_match_returns_empty(self):
        """查询词不命中任何术语 → 空扩展集"""
        tree = self._sample_tree()
        assert expand_query_with_ontology("完全不相关的查询词XYZ", tree) == []

    def test_empty_or_missing_tree_degrades_gracefully(self):
        """U-4: 本体缺失/为空 → 不报错,返回空(降级纯 BM25)"""
        assert expand_query_with_ontology("岸桥远控", []) == []
        assert expand_query_with_ontology("岸桥远控", None) == []
        assert expand_query_with_ontology("岸桥远控", {}) == []

    def test_hit_only_in_query_not_in_tree(self):
        """查询含多词,只对树中存在的术语做扩展"""
        tree = self._sample_tree()
        expansions = expand_query_with_ontology("岸桥远控 和 某个不存在词", tree)
        # 仍基于 岸桥远控 给出兄弟/上位
        assert "场桥远控" in expansions
        assert "港口自动化" in expansions


# ─── 树遍历辅助 ─────────────────────────────────────────────────────────────────

class TestTreeHelpers:
    def test_find_node_nested(self):
        tree = self._t = [
            {"term": "A", "parent": None, "definition": "", "children": [
                {"term": "B", "parent": "A", "definition": "", "children": [
                    {"term": "C", "parent": "B", "definition": "", "children": []},
                ]},
            ]},
        ]
        assert find_node(tree, "C") is not None
        assert find_node(tree, "C")["term"] == "C"
        assert find_node(tree, "X") is None

    def test_get_ancestors(self):
        tree = [
            {"term": "A", "parent": None, "definition": "", "children": [
                {"term": "B", "parent": "A", "definition": "", "children": [
                    {"term": "C", "parent": "B", "definition": "", "children": []},
                ]},
            ]},
        ]
        anc = get_ancestors(tree, "C")
        assert anc == ["B", "A"]

    def test_get_siblings(self):
        tree = [
            {"term": "A", "parent": None, "definition": "", "children": [
                {"term": "B", "parent": "A", "definition": "", "children": []},
                {"term": "C", "parent": "A", "definition": "", "children": []},
                {"term": "D", "parent": "A", "definition": "", "children": []},
            ]},
        ]
        sib = get_siblings(tree, "C")
        assert set(sib) == {"B", "D"}
        assert "C" not in sib


# ─── rebuild_tree_from_nodes: 历史回填 ──────────────────────────────────────────

class TestRebuildTree:
    """回填工具:从 per-doc 节点重建真树,消除历史扁平孤儿"""

    def test_rebuild_from_scratch_builds_tree(self):
        """从空树+一组 per-doc 节点(含 parent/grandparent)重建出真树"""
        seed = [{"term": "智慧港口", "parent": None, "definition": "", "children": []}]
        per_doc_nodes = [
            {"term": "港口自动化", "parent": "智慧港口", "definition": ""},
            {"term": "岸桥远控", "parent": "港口自动化", "definition": ""},
            {"term": "5G专网", "parent": "通信技术", "grandparent": "基础设施",
             "definition": ""},
            {"term": "通信技术", "parent": "基础设施", "definition": ""},
            {"term": "基础设施", "parent": None, "definition": ""},
        ]

        tree, total = rebuild_tree_from_nodes(seed, per_doc_nodes)

        # 顶层应只剩两个根(智慧港口 / 基础设施),无孤儿
        top = [n["term"] for n in tree]
        assert set(top) == {"智慧港口", "基础设施"}
        # 岸桥远控 嵌套在 港口自动化 下
        assert find_node(tree, "岸桥远控")["parent"] == "港口自动化"
        # 5G专网 嵌套在 通信技术 下,通信技术 在 基础设施 下
        assert find_node(tree, "5G专网")["parent"] == "通信技术"
        assert find_node(tree, "通信技术")["parent"] == "基础设施"
        # 总数 = 6 个唯一术语(智慧港口/港口自动化/岸桥远控/5G专网/通信技术/基础设施)
        assert total == 6

    def test_rebuild_no_top_level_orphans_when_parent_chain_present(self):
        """只要 parent 链可解析,回填后不应留下顶层孤儿"""
        seed = []
        nodes = [
            {"term": "根", "parent": None, "definition": ""},
            {"term": "子", "parent": "根", "definition": ""},
            {"term": "孙", "parent": "子", "definition": ""},
        ]
        tree, _ = rebuild_tree_from_nodes(seed, nodes)
        # 顶层只有 根
        assert [n["term"] for n in tree] == ["根"]

    def test_rebuild_dedupes(self):
        """重复术语不重复建"""
        seed = []
        nodes = [
            {"term": "根", "parent": None, "definition": ""},
            {"term": "根", "parent": None, "definition": ""},
            {"term": "子", "parent": "根", "definition": ""},
            {"term": "子", "parent": "根", "definition": ""},
        ]
        tree, total = rebuild_tree_from_nodes(seed, nodes)
        assert total == 2  # 根 + 子
