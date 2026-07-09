"""
test_doc_admin.py — 文档管理(删除/重编译)测试(Loop #10)
覆盖:
  - edges_excluding_doc: 纯逻辑,从边列表移除涉及某文档的边(删除时清理图)
  - remove_doc: 删除文档的全部产物 + 清理引用(集成,用 fixture 隔离)
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pytest
import yaml

from scripts.doc_admin import edges_excluding_doc, remove_doc


class TestEdgesExcludingDoc:
    """纯逻辑:删除文档时,从图/KG 里移除涉及它的边。"""

    def test_removes_edges_involving_doc(self):
        edges = [
            {"source": "doc_A", "target": "doc_B", "type": "same_topic"},
            {"source": "doc_B", "target": "doc_C", "type": "supplements"},
            {"source": "doc_C", "target": "doc_D", "type": "same_topic"},
        ]
        # 删 doc_B → 移除前两条(涉及 doc_B),留第三条
        out = edges_excluding_doc(edges, "doc_B")
        assert len(out) == 1
        assert out[0]["source"] == "doc_C"

    def test_target_side_also_removed(self):
        """边 target == 被删文档也要移除。"""
        edges = [
            {"source": "doc_A", "target": "doc_X", "type": "same_topic"},
            {"source": "doc_A", "target": "doc_B", "type": "supplements"},
        ]
        out = edges_excluding_doc(edges, "doc_X")
        assert len(out) == 1
        assert out[0]["target"] == "doc_B"

    def test_no_match_returns_unchanged(self):
        edges = [{"source": "A", "target": "B"}]
        assert edges_excluding_doc(edges, "Z") == edges

    def test_entity_relations_by_doc_id_field(self):
        """实体关系边用 doc_id 字段(非 source/target),按 doc_id 过滤。"""
        edges = [
            {"source": "5G", "target": "eMBB", "doc_id": "doc_A"},
            {"source": "5G", "target": "uRLLC", "doc_id": "doc_B"},
        ]
        out = edges_excluding_doc(edges, "doc_A")
        assert len(out) == 1
        assert out[0]["doc_id"] == "doc_B"

    def test_empty(self):
        assert edges_excluding_doc([], "X") == []
        assert edges_excluding_doc(None, "X") == []


class TestRemoveDoc:
    """集成:删除文档的全部产物 + 清理 index/KG/entity_relations 引用。"""

    def test_removes_artifacts_and_cleans_references(self, project_dir, monkeypatch):
        """删 doc_X:移除 raw/wiki/meta/ontology 产物 + 从 index/KG/entity 清理。"""
        import scripts.doc_admin as mod
        monkeypatch.setattr(mod, "BASE_DIR", project_dir)
        monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
        monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
        monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
        monkeypatch.setattr(mod, "META_DIR", project_dir / "meta")

        doc_id = "doc_20260405_099"
        # 造产物
        (project_dir / "raw" / f"{doc_id}.txt").write_text("内容", encoding="utf-8")
        (project_dir / "raw" / f"{doc_id}.meta.yaml").write_text(
            f"id: {doc_id}\nstatus: compiled\n", encoding="utf-8")
        (project_dir / "wiki" / f"{doc_id}.summary.yaml").write_text(
            "abstract: x", encoding="utf-8")
        ont_dir = project_dir / "meta" / "ontology"
        ont_dir.mkdir(parents=True, exist_ok=True)
        (ont_dir / f"{doc_id}.ontology.yaml").write_text("nodes: []", encoding="utf-8")
        rel_dir = project_dir / "meta" / "relations"
        rel_dir.mkdir(parents=True, exist_ok=True)
        (rel_dir / f"{doc_id}.relations.yaml").write_text("edges: []", encoding="utf-8")

        # index 含该 doc
        idx_path = project_dir / "wiki" / "index.yaml"
        yaml.dump({"documents": [
            {"id": doc_id, "title": "要删的"},
            {"id": "doc_keep", "title": "保留"},
        ]}, open(idx_path, "w", encoding="utf-8"), allow_unicode=True)
        # KG 含涉及它的边
        kg_path = rel_dir / "knowledge_graph.yaml"
        yaml.dump({"edges": [
            {"source": doc_id, "target": "doc_keep", "type": "same_topic"},
            {"source": "doc_keep", "target": "doc_other", "type": "supplements"},
        ]}, open(kg_path, "w", encoding="utf-8"), allow_unicode=True)
        # entity_relations 含它的边
        ent_path = ont_dir / "entity_relations.yaml"
        yaml.dump({"edges": [
            {"source": "5G", "target": "eMBB", "doc_id": doc_id},
            {"source": "5G", "target": "uRLLC", "doc_id": "doc_keep"},
        ]}, open(ent_path, "w", encoding="utf-8"), allow_unicode=True)

        summary = remove_doc(doc_id)

        # 产物文件删除
        assert not (project_dir / "raw" / f"{doc_id}.txt").exists()
        assert not (project_dir / "raw" / f"{doc_id}.meta.yaml").exists()
        assert not (project_dir / "wiki" / f"{doc_id}.summary.yaml").exists()
        # index 不再含它
        idx = yaml.safe_load(open(idx_path, encoding="utf-8"))
        assert all(d["id"] != doc_id for d in idx["documents"])
        assert len(idx["documents"]) == 1  # 只剩 doc_keep
        # KG 移除涉及它的边
        kg = yaml.safe_load(open(kg_path, encoding="utf-8"))
        assert all(e.get("source") != doc_id and e.get("target") != doc_id for e in kg["edges"])
        assert len(kg["edges"]) == 1
        # entity_relations 移除它的边
        ent = yaml.safe_load(open(ent_path, encoding="utf-8"))
        assert all(e.get("doc_id") != doc_id for e in ent["edges"])
        assert len(ent["edges"]) == 1
        # 返回摘要
        assert summary["doc_id"] == doc_id
        assert summary["removed"] is True

    def test_remove_nonexistent_doc_safe(self, project_dir, monkeypatch):
        """删不存在的文档 → 安全返回 removed=False,不崩。"""
        import scripts.doc_admin as mod
        monkeypatch.setattr(mod, "BASE_DIR", project_dir)
        monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
        monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
        monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
        monkeypatch.setattr(mod, "META_DIR", project_dir / "meta")
        summary = remove_doc("doc_nonexistent")
        assert summary["removed"] is False
