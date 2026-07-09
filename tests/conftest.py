"""
conftest.py — 共享 Pytest Fixtures
提供临时目录、module path patching 等。
不包含样本数据——样本数据在 sample_data.py 中。
"""
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock
from datetime import datetime, timezone, timedelta

import pytest
import yaml

# 让 tests/sample_data.py 可直接 import
_tests_dir = str(Path(__file__).parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

TZ_CST = timezone(timedelta(hours=8))


@pytest.fixture()
def project_dir(tmp_path):
    """在 tmp_path 中创建完整的知识库目录骨架"""
    (tmp_path / "raw").mkdir()
    (tmp_path / "wiki").mkdir()
    (tmp_path / "meta" / "ontology").mkdir(parents=True)
    (tmp_path / "meta" / "relations").mkdir(parents=True)
    (tmp_path / "originals").mkdir()
    (tmp_path / "scripts").mkdir()

    with open(tmp_path / "wiki" / "index.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"documents": []}, f, allow_unicode=True)

    with open(tmp_path / "meta" / "ontology" / "global_ontology.yaml", "w", encoding="utf-8") as f:
        yaml.dump({
            "ontology_tree": [
                {"term": "智慧港口", "parent": None, "definition": "测试根节点", "children": [
                    {"term": "港口自动化", "parent": "智慧港口", "definition": "自动化", "children": []},
                ]},
            ],
            "last_updated": datetime.now(TZ_CST).isoformat(),
            "total_nodes": 2,
            "version": "test",
        }, f, allow_unicode=True)

    with open(tmp_path / "meta" / "relations" / "knowledge_graph.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"edges": []}, f, allow_unicode=True)

    return tmp_path


def _ensure_scripts_importable():
    """确保 scripts/ 可作为 Python 包导入"""
    project_root = Path(__file__).parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))


@pytest.fixture()
def patch_ingest_paths(project_dir, monkeypatch):
    _ensure_scripts_importable()
    import scripts.ingest as mod
    monkeypatch.setattr(mod, "BASE_DIR", project_dir)
    monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
    monkeypatch.setattr(mod, "ORIGINALS_DIR", project_dir / "originals")
    monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
    monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
    return mod


@pytest.fixture()
def patch_compile_paths(project_dir, monkeypatch):
    _ensure_scripts_importable()
    import scripts.compile as mod
    monkeypatch.setattr(mod, "BASE_DIR", project_dir)
    monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
    monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
    monkeypatch.setattr(mod, "META_DIR", project_dir / "meta")
    monkeypatch.setattr(mod, "ONTOLOGY_DIR", project_dir / "meta" / "ontology")
    monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
    monkeypatch.setattr(mod, "GLOBAL_ONTOLOGY_FILE",
                        project_dir / "meta" / "ontology" / "global_ontology.yaml")
    return mod


@pytest.fixture()
def patch_search_paths(project_dir, monkeypatch):
    _ensure_scripts_importable()
    import scripts.search as mod
    monkeypatch.setattr(mod, "BASE_DIR", project_dir)
    monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
    monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
    monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
    # Big-Loop #1: 检索层读取全局本体做查询扩展,必须同步 patch 隔离
    monkeypatch.setattr(mod, "GLOBAL_ONTOLOGY_FILE",
                        project_dir / "meta" / "ontology" / "global_ontology.yaml")
    return mod


@pytest.fixture()
def patch_relate_paths(project_dir, monkeypatch):
    _ensure_scripts_importable()
    import scripts.relate as mod
    monkeypatch.setattr(mod, "BASE_DIR", project_dir)
    monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
    monkeypatch.setattr(mod, "RELATIONS_DIR", project_dir / "meta" / "relations")
    monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
    monkeypatch.setattr(mod, "KG_FILE",
                        project_dir / "meta" / "relations" / "knowledge_graph.yaml")
    # Big-Loop #2: 实体关系抽取的路径常量(隔离)
    monkeypatch.setattr(mod, "ONTOLOGY_DIR", project_dir / "meta" / "ontology")
    monkeypatch.setattr(mod, "ENTITY_RELATIONS_FILE",
                        project_dir / "meta" / "ontology" / "entity_relations.yaml")
    return mod


@pytest.fixture()
def patch_consistency_paths(project_dir, monkeypatch):
    """Big-Loop #3: 一致性模块的路径常量(隔离)。"""
    _ensure_scripts_importable()
    import scripts.consistency as mod
    monkeypatch.setattr(mod, "BASE_DIR", project_dir)
    monkeypatch.setattr(mod, "META_DIR", project_dir / "meta")
    monkeypatch.setattr(mod, "WIKI_DIR", project_dir / "wiki")
    monkeypatch.setattr(mod, "RAW_DIR", project_dir / "raw")
    monkeypatch.setattr(mod, "INDEX_FILE", project_dir / "wiki" / "index.yaml")
    monkeypatch.setattr(mod, "CONSISTENCY_DIR", project_dir / "meta" / "consistency")
    monkeypatch.setattr(mod, "CONTRADICTIONS_FILE",
                        project_dir / "meta" / "consistency" / "contradictions.yaml")
    return mod


@pytest.fixture()
def mock_llm_client():
    return MagicMock()
