"""
test_relate.py — 文档关系检测模块测试
覆盖 PRD 4.2.3 知识图谱功能：
  - 关系检测与验证
  - 关系文件写入
  - 全局知识图谱更新
  - 关联推荐展示
"""
import pytest
import yaml

from sample_data import (
    SAMPLE_RELATIONS_RESPONSE,
    SAMPLE_SUMMARY_RESPONSE,
    set_llm_response,
)


# ─── 辅助：预填充索引 ────────────────────────────────────────────────────────

def _seed_index(project_dir, doc_ids):
    """向索引中预填充文档条目"""
    index = {"documents": [
        {"id": d, "title": f"文档_{d}", "abstract_short": "测试摘要",
         "ontology_terms": ["岸桥远控", "5G专网"]}
        for d in doc_ids
    ]}
    with open(project_dir / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump(index, f, allow_unicode=True)


def _seed_summary(project_dir, doc_id):
    """为文档创建摘要文件"""
    summary = {
        "doc_id": doc_id,
        "title": f"文档_{doc_id}",
        "abstract": "测试摘要内容",
        "key_points": ["测试论点"],
        "writing_style": {"key_terminology": {"岸桥远控": 3, "5G专网": 2}},
    }
    with open(project_dir / "wiki" / f"{doc_id}.summary.yaml", "w", encoding="utf-8") as f:
        yaml.dump(summary, f, allow_unicode=True)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 关系检测逻辑
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectRelations:
    """PRD 4.2.3: LLM-based 文档关系检测"""

    def test_detect_valid_relations(self, patch_relate_paths, project_dir, mock_llm_client):
        """应返回合法的关系列表"""
        doc_ids = ["doc_20260401_001", "doc_20260401_002", "doc_20260405_new"]
        _seed_index(project_dir, doc_ids)
        _seed_summary(project_dir, "doc_20260405_new")

        set_llm_response(mock_llm_client, SAMPLE_RELATIONS_RESPONSE)

        rels = patch_relate_paths.detect_relations(
            "doc_20260405_new", mock_llm_client, "gpt-4o"
        )

        assert len(rels) == 2
        assert rels[0]["type"] == "supplements"
        assert rels[0]["confidence"] >= 0.70

    def test_filter_low_confidence(self, patch_relate_paths, project_dir, mock_llm_client):
        """confidence < 0.70 的关系应被过滤"""
        doc_ids = ["doc_20260401_001", "doc_20260405_new"]
        _seed_index(project_dir, doc_ids)
        _seed_summary(project_dir, "doc_20260405_new")

        low_conf = {"relations": [
            {"target_doc_id": "doc_20260401_001", "type": "same_topic",
             "confidence": 0.50, "evidence": "弱关系"}
        ]}
        set_llm_response(mock_llm_client, low_conf)

        rels = patch_relate_paths.detect_relations(
            "doc_20260405_new", mock_llm_client, "gpt-4o"
        )
        assert len(rels) == 0

    def test_filter_invalid_type(self, patch_relate_paths, project_dir, mock_llm_client):
        """非法关系类型应被过滤"""
        doc_ids = ["doc_20260401_001", "doc_20260405_new"]
        _seed_index(project_dir, doc_ids)
        _seed_summary(project_dir, "doc_20260405_new")

        bad_type = {"relations": [
            {"target_doc_id": "doc_20260401_001", "type": "unknown_relation",
             "confidence": 0.90, "evidence": "测试"}
        ]}
        set_llm_response(mock_llm_client, bad_type)

        rels = patch_relate_paths.detect_relations(
            "doc_20260405_new", mock_llm_client, "gpt-4o"
        )
        assert len(rels) == 0

    def test_filter_nonexistent_target(self, patch_relate_paths, project_dir, mock_llm_client):
        """target_doc_id 不在索引中的关系应被过滤"""
        doc_ids = ["doc_20260401_001", "doc_20260405_new"]
        _seed_index(project_dir, doc_ids)
        _seed_summary(project_dir, "doc_20260405_new")

        ghost = {"relations": [
            {"target_doc_id": "doc_GHOST_999", "type": "same_topic",
             "confidence": 0.95, "evidence": "幽灵文档"}
        ]}
        set_llm_response(mock_llm_client, ghost)

        rels = patch_relate_paths.detect_relations(
            "doc_20260405_new", mock_llm_client, "gpt-4o"
        )
        assert len(rels) == 0

    def test_no_summary_returns_empty(self, patch_relate_paths, project_dir, mock_llm_client):
        """无摘要的文档直接返回空"""
        _seed_index(project_dir, ["doc_nosummary"])
        rels = patch_relate_paths.detect_relations(
            "doc_nosummary", mock_llm_client, "gpt-4o"
        )
        assert rels == []

    def test_single_doc_in_kb_returns_empty(self, patch_relate_paths, project_dir, mock_llm_client):
        """知识库中只有自己一个文档时跳过"""
        _seed_index(project_dir, ["doc_20260405_new"])
        _seed_summary(project_dir, "doc_20260405_new")
        rels = patch_relate_paths.detect_relations(
            "doc_20260405_new", mock_llm_client, "gpt-4o"
        )
        assert rels == []


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 关系写入
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteRelations:
    """PRD 4.5: 文档关系 YAML 写入"""

    def test_relation_file_created(self, patch_relate_paths, project_dir):
        doc_id = "doc_20260405_write"
        rels = [{"target_doc_id": "doc_20260401_001", "type": "supplements",
                 "confidence": 0.85, "evidence": "测试"}]

        patch_relate_paths.write_relations(doc_id, rels)

        rel_path = project_dir / "meta" / "relations" / f"{doc_id}.relations.yaml"
        assert rel_path.exists()

        with open(rel_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["doc_id"] == doc_id
        assert len(data["relations"]) == 1
        assert "detected_at" in data

    def test_empty_relations(self, patch_relate_paths, project_dir):
        doc_id = "doc_20260405_empty"
        patch_relate_paths.write_relations(doc_id, [])

        rel_path = project_dir / "meta" / "relations" / f"{doc_id}.relations.yaml"
        assert rel_path.exists()
        with open(rel_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data["relations"] == []


# ═══════════════════════════════════════════════════════════════════════════════
# 3. 知识图谱更新
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateKnowledgeGraph:
    """PRD 4.6: 全局知识图谱维护"""

    def test_edges_appended(self, patch_relate_paths, project_dir):
        rels = [{"target_doc_id": "doc_20260401_001", "type": "supplements",
                 "confidence": 0.85}]

        patch_relate_paths.update_kg("doc_new_001", rels)

        kg_path = project_dir / "meta" / "relations" / "knowledge_graph.yaml"
        with open(kg_path, "r", encoding="utf-8") as f:
            kg = yaml.safe_load(f)

        assert len(kg["edges"]) == 1
        assert kg["edges"][0]["source"] == "doc_new_001"
        assert kg["edges"][0]["type"] == "supplements"

    def test_old_edges_replaced_on_recompute(self, patch_relate_paths, project_dir):
        """重新检测时应移除该文档的旧边，再追加新边"""
        # 第一次
        rels1 = [{"target_doc_id": "doc_001", "type": "cites", "confidence": 0.8}]
        patch_relate_paths.update_kg("doc_X", rels1)

        # 第二次（新关系）
        rels2 = [{"target_doc_id": "doc_002", "type": "same_topic", "confidence": 0.9}]
        patch_relate_paths.update_kg("doc_X", rels2)

        kg_path = project_dir / "meta" / "relations" / "knowledge_graph.yaml"
        with open(kg_path, "r", encoding="utf-8") as f:
            kg = yaml.safe_load(f)

        # 只应有新的边
        doc_x_edges = [e for e in kg["edges"] if e["source"] == "doc_X"]
        assert len(doc_x_edges) == 1
        assert doc_x_edges[0]["target"] == "doc_002"

    def test_other_doc_edges_preserved(self, patch_relate_paths, project_dir):
        """更新一个文档的边时不应影响其他文档"""
        rels_a = [{"target_doc_id": "doc_001", "type": "cites", "confidence": 0.8}]
        rels_b = [{"target_doc_id": "doc_002", "type": "supplements", "confidence": 0.9}]

        patch_relate_paths.update_kg("doc_A", rels_a)
        patch_relate_paths.update_kg("doc_B", rels_b)

        kg_path = project_dir / "meta" / "relations" / "knowledge_graph.yaml"
        with open(kg_path, "r", encoding="utf-8") as f:
            kg = yaml.safe_load(f)

        assert len(kg["edges"]) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 关联推荐展示
# ═══════════════════════════════════════════════════════════════════════════════

class TestRecommend:
    """PRD ANTIGRAVITY.md 3.4: 关联推荐"""

    def test_recommend_with_relations(self, patch_relate_paths, project_dir, capsys):
        doc_id = "doc_20260405_rec"
        _seed_index(project_dir, [doc_id, "doc_20260401_001"])
        _seed_summary(project_dir, doc_id)

        # 写入关系文件
        rels_data = {
            "doc_id": doc_id,
            "detected_at": "2026-04-05",
            "relations": [
                {"target_doc_id": "doc_20260401_001", "type": "supplements",
                 "confidence": 0.85, "evidence": "补充了技术细节"}
            ]
        }
        rel_path = project_dir / "meta" / "relations" / f"{doc_id}.relations.yaml"
        with open(rel_path, "w", encoding="utf-8") as f:
            yaml.dump(rels_data, f, allow_unicode=True)

        patch_relate_paths.recommend(doc_id)
        captured = capsys.readouterr()
        assert "补充" in captured.out
        assert "85%" in captured.out

    def test_recommend_no_relations_file(self, patch_relate_paths, capsys):
        patch_relate_paths.recommend("doc_nonexistent")
        captured = capsys.readouterr()
        assert "未找到" in captured.out


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Big-Loop #2: 实体级关系抽取(术语→术语)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEntityRelations:
    """实体级关系抽取与合并"""

    def _seed_for_entity(self, project_dir, doc_id, terms):
        """预填 index + summary 供实体抽取"""
        index = {"documents": [
            {"id": doc_id, "title": f"文档_{doc_id}", "abstract_short": "摘要",
             "ontology_terms": terms}
        ]}
        with open(project_dir / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
            yaml.dump(index, f, allow_unicode=True)
        summary = {"doc_id": doc_id, "title": f"文档_{doc_id}",
                   "abstract": "测试摘要", "key_points": ["论点"]}
        with open(project_dir / "wiki" / f"{doc_id}.summary.yaml", "w", encoding="utf-8") as f:
            yaml.dump(summary, f, allow_unicode=True)

    def test_extract_valid_entity_relations(self, patch_relate_paths, project_dir, mock_llm_client):
        """E-1: 合法实体关系被抽取并写入"""
        self._seed_for_entity(project_dir, "doc_e1", ["岸桥远控", "5G专网", "MEC"])
        mock_resp = {"relations": [
            {"source": "岸桥远控", "target": "5G专网", "type": "depends_on",
             "confidence": 0.9, "evidence": "远控依赖5G低延迟"},
            {"source": "5G专网", "target": "MEC", "type": "supports",
             "confidence": 0.85, "evidence": "5G+MEC降时延"},
        ]}
        set_llm_response(mock_llm_client, mock_resp)

        rels = patch_relate_paths.extract_entity_relations(
            "doc_e1", mock_llm_client, "gpt-4o"
        )
        assert len(rels) == 2
        assert rels[0]["type"] == "depends_on"
        assert rels[0]["doc_id"] == "doc_e1"

        # 写入 entity_relations.yaml
        ent_path = project_dir / "meta" / "ontology" / "entity_relations.yaml"
        assert ent_path.exists()
        with open(ent_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert len(data["edges"]) == 2

    def test_filter_invalid_entity_type(self, patch_relate_paths, project_dir, mock_llm_client):
        """非法实体关系类型应过滤"""
        self._seed_for_entity(project_dir, "doc_e2", ["A", "B"])
        bad = {"relations": [
            {"source": "A", "target": "B", "type": "unknown", "confidence": 0.9},
        ]}
        set_llm_response(mock_llm_client, bad)
        rels = patch_relate_paths.extract_entity_relations("doc_e2", mock_llm_client, "gpt-4o")
        assert rels == []

    def test_filter_low_confidence_entity(self, patch_relate_paths, project_dir, mock_llm_client):
        """confidence < 0.70 的实体关系过滤"""
        self._seed_for_entity(project_dir, "doc_e3", ["A", "B"])
        low = {"relations": [
            {"source": "A", "target": "B", "type": "depends_on", "confidence": 0.5},
        ]}
        set_llm_response(mock_llm_client, low)
        rels = patch_relate_paths.extract_entity_relations("doc_e3", mock_llm_client, "gpt-4o")
        assert rels == []

    def test_insufficient_terms_skips(self, patch_relate_paths, project_dir, mock_llm_client):
        """术语 < 2 时跳过(无关系可抽)"""
        self._seed_for_entity(project_dir, "doc_e4", ["唯一术语"])
        rels = patch_relate_paths.extract_entity_relations("doc_e4", mock_llm_client, "gpt-4o")
        assert rels == []

    def test_merge_replaces_old_doc_edges(self, patch_relate_paths, project_dir, mock_llm_client):
        """重新抽取应替换该 doc 的旧实体边"""
        self._seed_for_entity(project_dir, "doc_e5", ["A", "B", "C"])
        # 第一次
        set_llm_response(mock_llm_client, {"relations": [
            {"source": "A", "target": "B", "type": "depends_on", "confidence": 0.9}]})
        patch_relate_paths.extract_entity_relations("doc_e5", mock_llm_client, "gpt-4o")
        # 第二次(新边)
        set_llm_response(mock_llm_client, {"relations": [
            {"source": "A", "target": "C", "type": "depends_on", "confidence": 0.9}]})
        patch_relate_paths.extract_entity_relations("doc_e5", mock_llm_client, "gpt-4o")

        ent_path = project_dir / "meta" / "ontology" / "entity_relations.yaml"
        with open(ent_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        doc_edges = [e for e in data["edges"] if e["doc_id"] == "doc_e5"]
        assert len(doc_edges) == 1
        assert doc_edges[0]["target"] == "C"
