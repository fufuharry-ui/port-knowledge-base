"""
doc_admin.py — 文档管理操作(Big-Loop #10)

提供知识库的**维护**能力(此前只能追加,无法删除/重编译):
  - remove_doc(doc_id):删除文档的全部产物 + 清理 index/KG/entity_relations 引用
  - recompile_doc(doc_id):重置状态为 raw,触发重编译(供 error 文档重试)

与 ingest.py 互补:ingest 负责摄入,doc_admin 负责管理(删除/重试)。
独立模块,不改四个核心引擎的内部(守护栏)。
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# 路径常量(测试通过 monkeypatch 隔离)
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "raw"
WIKI_DIR = BASE_DIR / "wiki"
INDEX_FILE = WIKI_DIR / "index.yaml"
META_DIR = BASE_DIR / "meta"


def edges_excluding_doc(edges, doc_id: str):
    """从边列表移除涉及 doc_id 的边(纯函数,删除时清理图用)。

    匹配规则:
      - 文档级边(source/target 任一 == doc_id)→ 移除
      - 实体关系边(doc_id 字段 == doc_id)→ 移除
    """
    if not edges:
        return []
    out = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        # 实体关系边:按 doc_id 字段
        if e.get("doc_id") == doc_id:
            continue
        # 文档级边:按 source/target
        if e.get("source") == doc_id or e.get("target") == doc_id:
            continue
        out.append(e)
    return out


def remove_doc(doc_id: str, base_dir: Path | None = None) -> dict:
    """删除文档的全部产物 + 清理 index/KG/entity_relations 引用。

    删除:
      raw/{id}.txt, raw/{id}.meta.yaml,
      wiki/{id}.summary.yaml,
      meta/ontology/{id}.ontology.yaml,
      meta/relations/{id}.relations.yaml
    清理:
      wiki/index.yaml 移除该条目,
      meta/relations/knowledge_graph.yaml 移除涉及它的边,
      meta/ontology/entity_relations.yaml 移除 doc_id 字段 == 它的边

    返回 {doc_id, removed: bool, cleaned_refs: {...}}。
    文档不存在 → removed=False(安全,不崩)。
    """
    base = base_dir if base_dir is not None else BASE_DIR
    raw_dir = base / "raw"
    wiki_dir = base / "wiki"
    meta_dir = base / "meta"
    ont_dir = meta_dir / "ontology"
    rel_dir = meta_dir / "relations"

    # 检查是否存在(任一产物或 index 条目)
    index_path = wiki_dir / "index.yaml"
    index_data = _safe_load(index_path) or {"documents": []}
    in_index = any(d.get("id") == doc_id for d in index_data.get("documents", []))
    has_meta = (raw_dir / f"{doc_id}.meta.yaml").exists()
    if not in_index and not has_meta:
        return {"doc_id": doc_id, "removed": False, "reason": "not found"}

    cleaned = {}

    # 1. 删除产物文件(存在才删,忽略不存在)
    for f in [
        raw_dir / f"{doc_id}.txt",
        raw_dir / f"{doc_id}.meta.yaml",
        wiki_dir / f"{doc_id}.summary.yaml",
        ont_dir / f"{doc_id}.ontology.yaml",
        rel_dir / f"{doc_id}.relations.yaml",
    ]:
        try:
            if f.exists():
                f.unlink()
        except Exception:
            pass

    # 2. 从 index 移除条目
    if in_index:
        before = len(index_data.get("documents", []))
        index_data["documents"] = [
            d for d in index_data.get("documents", []) if d.get("id") != doc_id
        ]
        _safe_dump(index_path, index_data)
        cleaned["index_removed"] = before - len(index_data["documents"])

    # 3. 清理 KG 边
    kg_path = rel_dir / "knowledge_graph.yaml"
    kg = _safe_load(kg_path) or {"edges": []}
    if kg.get("edges"):
        before = len(kg["edges"])
        kg["edges"] = edges_excluding_doc(kg["edges"], doc_id)
        if len(kg["edges"]) != before:
            _safe_dump(kg_path, kg)
            cleaned["kg_edges_removed"] = before - len(kg["edges"])

    # 4. 清理实体关系边
    ent_path = ont_dir / "entity_relations.yaml"
    ent = _safe_load(ent_path) or {"edges": []}
    if ent.get("edges"):
        before = len(ent["edges"])
        ent["edges"] = edges_excluding_doc(ent["edges"], doc_id)
        if len(ent["edges"]) != before:
            _safe_dump(ent_path, ent)
            cleaned["entity_edges_removed"] = before - len(ent["edges"])

    return {"doc_id": doc_id, "removed": True, "cleaned_refs": cleaned}


def recompile_doc(doc_id: str) -> dict:
    """重置文档状态为 raw,触发重编译(供 error 文档重试)。

    实现:把 raw/{id}.meta.yaml 的 status 改回 raw,交由调用方(端点)
    触发 compile.py。返回 {doc_id, reset: bool}。
    """
    meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
    if not meta_path.exists():
        return {"doc_id": doc_id, "reset": False, "reason": "meta not found"}
    try:
        meta = _safe_load(meta_path) or {}
        meta["status"] = "raw"
        meta.pop("error_message", None)
        _safe_dump(meta_path, meta)
        return {"doc_id": doc_id, "reset": True}
    except Exception as e:
        return {"doc_id": doc_id, "reset": False, "reason": str(e)}


def _safe_load(path: Path):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return None


def _safe_dump(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
