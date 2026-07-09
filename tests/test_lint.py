import os
import pytest
from pathlib import Path
from unittest.mock import patch
import yaml

from scripts.lint import Linter

@pytest.fixture
def temp_workspace(tmp_path):
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir()
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    relations_dir = meta_dir / "relations"
    relations_dir.mkdir()
    
    # 1. 模拟 index.yaml
    index_data = {
        "documents": [
            {"id": "doc_linked", "ontology_terms": ["港口"], "abstract_short": "这是关于港口的文档。"},
            {"id": "doc_orphan", "ontology_terms": [], "abstract_short": "这是孤立页面。"}
        ]
    }
    with open(wiki_dir / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump(index_data, f, allow_unicode=True)
        
    # 2. 模拟 relations
    rel_data = {
        "relations": [
            {"source": "doc_linked", "target": "doc_other", "type": "relates_to"}
        ]
    }
    with open(relations_dir / "doc_linked.yaml", "w", encoding="utf-8") as f:
        yaml.dump(rel_data, f, allow_unicode=True)
        
    # 3. 模拟 global_ontology.yaml
    ontology = {
        "terms": {
            "岸桥": {"description": "..."}
        }
    }
    ontology_dir = meta_dir / "ontology"
    ontology_dir.mkdir(parents=True, exist_ok=True)
    with open(ontology_dir / "global_ontology.yaml", "w", encoding="utf-8") as f:
        yaml.dump(ontology, f, allow_unicode=True)
        
    return tmp_path

def test_detect_orphan_pages(temp_workspace):
    linter = Linter(base_dir=temp_workspace)
    orphans = linter.detect_orphan_pages()
    
    assert "doc_orphan" in orphans
    assert "doc_linked" not in orphans

@patch("scripts.lint.llm_call_json")
def test_detect_contradictions(mock_llm, temp_workspace):
    # Mock LLM 返回矛盾
    mock_llm.return_value = {
        "contradictions": [
            {"docs": ["doc_A", "doc_B"], "conflict": "关于延迟要求的描述不一致"}
        ]
    }
    
    linter = Linter(base_dir=temp_workspace)
    # mock client
    class MockClient: pass
    
    contradictions = linter.detect_contradictions(client=MockClient(), model="test-model")
    assert len(contradictions) == 1
    assert "doc_A" in contradictions[0]["docs"]
    assert "doc_B" in contradictions[0]["docs"]
