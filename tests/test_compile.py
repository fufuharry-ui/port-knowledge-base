"""
test_compile.py — LLM 编译模块测试
覆盖 PRD 4.2 Process 层的核心功能：
  - Step A: 摘要编译 + 长文档截断
  - Step B: 本体抽取 + 全局本体更新
  - Step C: 全局索引更新（追加/替换）
  - 状态流转: raw → compiling → compiled / error
"""
from pathlib import Path

import pytest
import yaml

from sample_data import (
    SAMPLE_CHINESE_TEXT,
    SAMPLE_SUMMARY_RESPONSE,
    SAMPLE_ONTOLOGY_RESPONSE,
    set_llm_response,
    set_llm_responses,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Step A: 摘要编译
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompileSummary:
    """PRD 4.2.1: 摘要编译"""

    def test_summary_written_to_disk(self, patch_compile_paths, project_dir, mock_llm_client):
        set_llm_response(mock_llm_client, SAMPLE_SUMMARY_RESPONSE)

        doc_id = "doc_20260405_001"
        result = patch_compile_paths.compile_summary(
            mock_llm_client, doc_id, SAMPLE_CHINESE_TEXT, "gpt-4o"
        )

        # 验证文件写入
        summary_path = project_dir / "wiki" / f"{doc_id}.summary.yaml"
        assert summary_path.exists()

        with open(summary_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["doc_id"] == doc_id
        assert "abstract" in data
        assert "key_points" in data
        assert "compiled_at" in data

    def test_summary_has_required_fields(self, patch_compile_paths, project_dir, mock_llm_client):
        set_llm_response(mock_llm_client, SAMPLE_SUMMARY_RESPONSE)

        result = patch_compile_paths.compile_summary(
            mock_llm_client, "doc_test_001", SAMPLE_CHINESE_TEXT, "gpt-4o"
        )

        assert "abstract" in result
        assert "key_points" in result
        assert isinstance(result["key_points"], list)
        assert "sections" in result
        assert "document_type" in result
        assert "writing_style" in result

    def test_long_text_truncation(self, patch_compile_paths, project_dir, mock_llm_client):
        """PRD 6.2: 文档超过 60K 字符应截断"""
        set_llm_response(mock_llm_client, SAMPLE_SUMMARY_RESPONSE)

        long_text = "测试" * 40000  # 80K 字符
        patch_compile_paths.compile_summary(
            mock_llm_client, "doc_long_001", long_text, "gpt-4o"
        )

        # 验证 LLM 实际收到的文本被截断
        call_args = mock_llm_client.chat.completions.create.call_args
        user_msg = call_args.kwargs["messages"][1]["content"]
        # 截断后应 < 原文长度
        assert len(user_msg) < 80000


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Step B: 本体抽取
# ═══════════════════════════════════════════════════════════════════════════════

class TestCompileOntology:
    """PRD 4.2.2: 动态本体构建"""

    def test_ontology_written_to_disk(self, patch_compile_paths, project_dir, mock_llm_client):
        set_llm_response(mock_llm_client, SAMPLE_ONTOLOGY_RESPONSE)

        doc_id = "doc_20260405_001"
        result = patch_compile_paths.compile_ontology(
            mock_llm_client, doc_id, SAMPLE_CHINESE_TEXT,
            SAMPLE_SUMMARY_RESPONSE, "gpt-4o-mini"
        )

        ont_path = project_dir / "meta" / "ontology" / f"{doc_id}.ontology.yaml"
        assert ont_path.exists()

        with open(ont_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert data["doc_id"] == doc_id
        assert len(data["ontology_nodes"]) == 2

    def test_global_ontology_updated_with_new_nodes(
        self, patch_compile_paths, project_dir, mock_llm_client
    ):
        """新节点应合并进 global_ontology.yaml 为真树(非顶层孤儿)。

        Big-Loop #1 修正:旧断言 +2 实为对"扁平追加"bug 的编码。
        SAMPLE 响应给 岸桥远控(parent=港口自动化,在树中)与 5G专网
        (parent=通信技术[不在],grandparent=基础设施[在])。
        真树合并:岸桥远控挂 港口自动化(+1);新建 通信技术 挂 基础设施、
        5G专网挂 通信技术(+2)。共 +3,且无顶层孤儿。
        """
        set_llm_response(mock_llm_client, SAMPLE_ONTOLOGY_RESPONSE)

        global_ont_path = project_dir / "meta" / "ontology" / "global_ontology.yaml"
        with open(global_ont_path, "r", encoding="utf-8") as f:
            before = yaml.safe_load(f)
        nodes_before = before.get("total_nodes", 0)

        patch_compile_paths.compile_ontology(
            mock_llm_client, "doc_test_002", SAMPLE_CHINESE_TEXT,
            SAMPLE_SUMMARY_RESPONSE, "gpt-4o-mini"
        )

        with open(global_ont_path, "r", encoding="utf-8") as f:
            after = yaml.safe_load(f)

        # 真实增量 3(见上),修正旧 +2 断言
        assert after["total_nodes"] == nodes_before + 3

        # 岸桥远控 必须嵌套在 港口自动化.children 下,而非顶层孤儿
        def _find(tree, term):
            for n in tree:
                if n.get("term") == term:
                    return n
                found = _find(n.get("children", []), term)
                if found:
                    return found
            return None

        assert _find(after["ontology_tree"], "岸桥远控") is not None
        auto = _find(after["ontology_tree"], "港口自动化")
        assert auto is not None
        assert any(c["term"] == "岸桥远控" for c in auto.get("children", []))
        # 5G专网 挂在 通信技术 下。注:conftest 种子树无"基础设施",
        # 故 grandparent 缺失 → 通信技术 被建为**新根**(parent=None,不悬空)。
        # U-2(grandparent 存在则建中间父节点)由 test_ontology.py 覆盖。
        fiveg = _find(after["ontology_tree"], "5G专网")
        assert fiveg is not None and fiveg["parent"] == "通信技术"
        comm = _find(after["ontology_tree"], "通信技术")
        assert comm is not None and comm["parent"] is None  # 种子无 基础设施 → 新根
        assert any(c["term"] == "5G专网" for c in comm.get("children", []))

    def test_existing_terms_not_duplicated(
        self, patch_compile_paths, project_dir, mock_llm_client
    ):
        """已存在的术语不应重复添加"""
        # 先执行一次
        set_llm_response(mock_llm_client, SAMPLE_ONTOLOGY_RESPONSE)
        patch_compile_paths.compile_ontology(
            mock_llm_client, "doc_test_003", SAMPLE_CHINESE_TEXT,
            SAMPLE_SUMMARY_RESPONSE, "gpt-4o-mini"
        )

        global_ont_path = project_dir / "meta" / "ontology" / "global_ontology.yaml"
        with open(global_ont_path, "r", encoding="utf-8") as f:
            after1 = yaml.safe_load(f)
        count1 = after1["total_nodes"]

        # 再执行一次（相同的 ontology response）
        set_llm_response(mock_llm_client, SAMPLE_ONTOLOGY_RESPONSE)
        patch_compile_paths.compile_ontology(
            mock_llm_client, "doc_test_004", SAMPLE_CHINESE_TEXT,
            SAMPLE_SUMMARY_RESPONSE, "gpt-4o-mini"
        )

        with open(global_ont_path, "r", encoding="utf-8") as f:
            after2 = yaml.safe_load(f)

        # 不应增加新节点
        assert after2["total_nodes"] == count1

    def test_get_existing_terms(self, patch_compile_paths, project_dir):
        """应能遍历本体树获取所有 term"""
        terms = patch_compile_paths._get_existing_terms()
        assert "智慧港口" in terms
        assert "港口自动化" in terms


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Step C: 全局索引更新
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateIndex:
    """PRD 4.4: 全局索引格式与更新逻辑"""

    def test_append_new_document(self, patch_compile_paths, project_dir):
        meta = {"title": "测试文档", "ingested_at": "2026-04-05", "source_type": "md", "file_hash": "sha256:abc"}
        summary = {"abstract": "这是一个测试摘要，用于验证索引更新功能。" * 5, "document_type": "technical_spec"}
        ontology = {"ontology_nodes": [{"term": "测试术语"}]}

        patch_compile_paths.update_index("doc_20260405_099", meta, summary, ontology)

        index_path = project_dir / "wiki" / "index.yaml"
        with open(index_path, "r", encoding="utf-8") as f:
            index = yaml.safe_load(f)

        assert len(index["documents"]) == 1
        entry = index["documents"][0]
        assert entry["id"] == "doc_20260405_099"
        assert entry["title"] == "测试文档"
        assert "测试术语" in entry["ontology_terms"]

    def test_replace_on_recompile(self, patch_compile_paths, project_dir):
        """重新编译时应替换旧记录，不应重复追加"""
        meta = {"title": "文档v1", "ingested_at": "2026-04-05", "source_type": "md", "file_hash": "sha256:v1"}
        summary = {"abstract": "版本1", "document_type": "report"}
        ontology = {"ontology_nodes": []}

        patch_compile_paths.update_index("doc_20260405_100", meta, summary, ontology)

        # 更新同一 doc_id
        meta2 = {"title": "文档v2", "ingested_at": "2026-04-05", "source_type": "md", "file_hash": "sha256:v2"}
        summary2 = {"abstract": "版本2", "document_type": "technical_spec"}

        patch_compile_paths.update_index("doc_20260405_100", meta2, summary2, ontology)

        index_path = project_dir / "wiki" / "index.yaml"
        with open(index_path, "r", encoding="utf-8") as f:
            index = yaml.safe_load(f)

        assert len(index["documents"]) == 1
        assert index["documents"][0]["title"] == "文档v2"

    def test_abstract_short_truncation(self, patch_compile_paths, project_dir):
        """abstract_short 应截断到 100 字符"""
        meta = {"title": "长摘要", "ingested_at": "2026-04-05", "source_type": "md", "file_hash": "sha256:x"}
        long_abstract = "A" * 200
        summary = {"abstract": long_abstract, "document_type": "report"}
        ontology = {"ontology_nodes": []}

        patch_compile_paths.update_index("doc_20260405_101", meta, summary, ontology)

        index_path = project_dir / "wiki" / "index.yaml"
        with open(index_path, "r", encoding="utf-8") as f:
            index = yaml.safe_load(f)

        abstract_short = index["documents"][0]["abstract_short"]
        assert len(abstract_short) <= 104  # 100 + "..."


# ═══════════════════════════════════════════════════════════════════════════════
# 4. 状态管理
# ═══════════════════════════════════════════════════════════════════════════════

class TestStatusManagement:
    """PRD ANTIGRAVITY.md 3.1: status 状态流转"""

    def test_update_meta_status(self, patch_compile_paths, project_dir):
        # 创建 meta 文件
        doc_id = "doc_20260405_status_test"
        meta = {"id": doc_id, "status": "raw", "error_message": ""}
        meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
        with open(meta_path, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True)

        # 更新状态
        patch_compile_paths._update_meta_status(doc_id, "compiled")

        with open(meta_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert updated["status"] == "compiled"

    def test_error_status_with_message(self, patch_compile_paths, project_dir):
        doc_id = "doc_20260405_err_test"
        meta = {"id": doc_id, "status": "raw", "error_message": ""}
        meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
        with open(meta_path, "w", encoding="utf-8") as f:
            yaml.dump(meta, f, allow_unicode=True)

        patch_compile_paths._update_meta_status(doc_id, "error", "LLM timeout")

        with open(meta_path, "r", encoding="utf-8") as f:
            updated = yaml.safe_load(f)

        assert updated["status"] == "error"
        assert updated["error_message"] == "LLM timeout"

    def test_get_raw_doc_ids(self, patch_compile_paths, project_dir):
        """应只返回 status=raw 的文档"""
        raw_dir = project_dir / "raw"
        # raw
        with open(raw_dir / "doc_a.meta.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"id": "doc_a", "status": "raw"}, f)
        # compiled (应排除)
        with open(raw_dir / "doc_b.meta.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"id": "doc_b", "status": "compiled"}, f)
        # error (应排除)
        with open(raw_dir / "doc_c.meta.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"id": "doc_c", "status": "error"}, f)

        ids = patch_compile_paths.get_raw_doc_ids("raw")
        assert "doc_a" in ids
        assert "doc_b" not in ids
        assert "doc_c" not in ids
