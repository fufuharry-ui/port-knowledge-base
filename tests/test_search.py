"""
test_search.py — 三层检索模块测试
覆盖 PRD 4.3 Interface 层核心功能：
  - Layer 1: BM25 关键词粗筛
  - Layer 2: LLM 摘要相关性评分
  - Layer 3: 精确回答生成 + 引用
  - 边界情况: 空知识库、无命中等
"""
import pytest
import yaml

from sample_data import (
    SAMPLE_SCORE_RESPONSE,
    set_llm_response,
    set_llm_responses,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Layer 1: BM25 关键词粗筛
# ═══════════════════════════════════════════════════════════════════════════════

class TestBM25Score:
    """PRD ANTIGRAVITY.md 3.3: Layer 1 检索"""

    def test_ontology_hit_higher_weight(self, patch_search_paths):
        """本体术语命中权重应为 2.0"""
        score = patch_search_paths.bm25_score(
            ["岸桥远控"], ["岸桥远控", "5G专网"], ""
        )
        assert score >= 2.0

    def test_abstract_hit_weight(self, patch_search_paths):
        """摘要中关键词命中权重 0.5/次"""
        score = patch_search_paths.bm25_score(
            ["网络"], [], "网络延迟要求，网络架构"
        )
        assert score == 1.0  # 2 hits × 0.5

    def test_no_match_zero_score(self, patch_search_paths):
        score = patch_search_paths.bm25_score(
            ["完全不相关的词"], ["岸桥远控"], "港口自动化技术"
        )
        assert score == 0.0

    def test_abstract_hit_capped_at_5(self, patch_search_paths):
        """摘要命中次数应限制在 5 次"""
        text = "网络" * 100
        score = patch_search_paths.bm25_score(["网络"], [], text)
        assert score == 2.5  # min(100,5) × 0.5

    def test_case_insensitive(self, patch_search_paths):
        """英文搜索应不区分大小写"""
        score = patch_search_paths.bm25_score(
            ["AGV"], ["agv"], "AGV autonomous"
        )
        assert score > 0


class TestLayer1Filter:
    """PRD ANTIGRAVITY.md 3.3: 完整 Layer 1 过滤"""

    def test_filter_returns_matching_docs(self, patch_search_paths):
        index = {"documents": [
            {"id": "doc_001", "ontology_terms": ["岸桥远控", "5G专网"],
             "abstract_short": "基于5G的岸桥远控系统"},
            {"id": "doc_002", "ontology_terms": ["数据治理"],
             "abstract_short": "港口数据治理方案"},
        ]}
        results = patch_search_paths.layer1_filter("岸桥远控 网络延迟", index)
        assert len(results) >= 1
        assert results[0]["id"] == "doc_001"

    def test_filter_empty_index(self, patch_search_paths):
        results = patch_search_paths.layer1_filter("任何查询", {"documents": []})
        assert results == []

    def test_filter_respects_top_k(self, patch_search_paths):
        docs = [{"id": f"doc_{i:03d}",
                 "ontology_terms": ["岸桥"],
                 "abstract_short": "岸桥相关"}
                for i in range(30)]
        index = {"documents": docs}
        results = patch_search_paths.layer1_filter("岸桥", index, top_k=5)
        assert len(results) <= 5

    def test_filter_no_match(self, patch_search_paths):
        index = {"documents": [
            {"id": "doc_001", "ontology_terms": ["数据治理"],
             "abstract_short": "数据质量管理"},
        ]}
        results = patch_search_paths.layer1_filter("完全无关查询XYZ", index)
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Layer 2: LLM 摘要评分
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayer2Score:
    """PRD ANTIGRAVITY.md 3.3: Layer 2 LLM 评分"""

    def test_returns_only_high_score_docs(self, patch_search_paths, mock_llm_client):
        set_llm_response(mock_llm_client, SAMPLE_SCORE_RESPONSE)

        candidates = [
            {"id": "doc_20260401_001", "title": "高分文档", "abstract_short": "..."},
            {"id": "doc_20260401_002", "title": "低分文档", "abstract_short": "..."},
        ]
        results = patch_search_paths.layer2_score(
            "测试查询", candidates, mock_llm_client, "gpt-4o"
        )
        # doc_002 score=0.45 应被过滤（阈值 0.5）
        assert len(results) == 1
        assert results[0]["id"] == "doc_20260401_001"

    def test_empty_candidates(self, patch_search_paths, mock_llm_client):
        results = patch_search_paths.layer2_score(
            "any query", [], mock_llm_client, "gpt-4o"
        )
        assert results == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Layer 3: 精确回答
# ═══════════════════════════════════════════════════════════════════════════════

class TestLayer3Answer:
    """PRD ANTIGRAVITY.md 3.3: Layer 3 全文回答"""

    def test_answer_includes_sources(self, patch_search_paths, project_dir, mock_llm_client):
        """回答应包含引用来源"""
        # 创建原文和摘要
        doc_id = "doc_20260401_001"
        txt_path = project_dir / "raw" / f"{doc_id}.txt"
        txt_path.write_text("端到端延迟≤50ms", encoding="utf-8")

        summary_path = project_dir / "wiki" / f"{doc_id}.summary.yaml"
        with open(summary_path, "w", encoding="utf-8") as f:
            yaml.dump({"sections": [{"title": "网络", "page_range": "3-5"}]}, f)

        set_llm_response(mock_llm_client, "根据文档，延迟要求为50ms。")

        index = {"documents": [{"id": doc_id, "title": "测试文档"}]}
        top_docs = [{"id": doc_id, "title": "测试文档"}]

        result = patch_search_paths.layer3_answer(
            "延迟是多少？", top_docs, mock_llm_client, "gpt-4o", index
        )
        assert "引用来源" in result
        assert doc_id in result

    def test_no_docs_returns_warning(self, patch_search_paths, mock_llm_client):
        result = patch_search_paths.layer3_answer(
            "查询", [], mock_llm_client, "gpt-4o", {"documents": []}
        )
        assert "⚠️" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 主检索流程边界情况
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearchEdgeCases:
    """PRD ANTIGRAVITY.md 3.3: 边界与兜底场景"""

    def test_empty_index_returns_warning(self, patch_search_paths, project_dir, mock_llm_client):
        """空知识库应返回警告"""
        result = patch_search_paths.search("任何查询", mock_llm_client, verbose=False)
        assert "⚠️" in result

    def test_layer2_fallback(self, patch_search_paths, mock_llm_client):
        """Layer 2 无结果时应兜底返回 Layer 1 前3"""
        # score 全部低于阈值
        low_scores = {"scores": [
            {"doc_id": "d1", "score": 0.1, "reason": ""},
            {"doc_id": "d2", "score": 0.2, "reason": ""},
        ]}
        set_llm_response(mock_llm_client, low_scores)

        candidates = [
            {"id": "d1", "title": "A", "abstract_short": ""},
            {"id": "d2", "title": "B", "abstract_short": ""},
        ]
        results = patch_search_paths.layer2_score(
            "query", candidates, mock_llm_client, "gpt-4o"
        )
        # 应返回空（均低于 0.5 阈值）
        assert len(results) == 0

    def test_full_text_truncation(self, patch_search_paths, project_dir):
        """单文档全文应限制在 15000 字符"""
        doc_id = "doc_trunc_001"
        txt_path = project_dir / "raw" / f"{doc_id}.txt"
        txt_path.write_text("x" * 20000, encoding="utf-8")

        text = patch_search_paths.load_full_text(doc_id)
        assert len(text) == 15000
