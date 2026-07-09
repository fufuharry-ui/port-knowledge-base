"""
doc_admin.py - Document management operations (Big-Loop #10)
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "raw"
WIKI_DIR = BASE_DIR / "wiki"
INDEX_FILE = WIKI_DIR / "index.yaml"
META_DIR = BASE_DIR / "meta"


def edges_excluding_doc(edges, doc_id: str):
    if not edges:
        return []
    out = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        if e.get("doc_id") == doc_id:
            continue
        if e.get("source") == doc_id or e.get("target") == doc_id:
            continue
        out.append(e)
    return out


def remove_doc(doc_id: str, base_dir: Path | None = None) -> dict:
    base = base_dir if base_dir is not None else BASE_DIR
    raw_dir = base / "raw"
    wiki_dir = base / "wiki"
    meta_dir = base / "meta"
    ont_dir = meta_dir / "ontology"
    rel_dir = meta_dir / "relations"

    index_path = wiki_dir / "index.yaml"
    index_data = _safe_load(index_path) or {"documents": []}
    in_index = any(d.get("id") == doc_id for d in index_data.get("documents", []))
    has_meta = (raw_dir / f"{doc_id}.meta.yaml").exists()
    if not in_index and not has_meta:
        return {"doc_id": doc_id, "removed": False, "reason": "not found"}

    cleaned = {}

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

    if in_index:
        before = len(index_data.get("documents", []))
        index_data["documents"] = [
            d for d in index_data.get("documents", []) if d.get("id") != doc_id
        ]
        _safe_dump(index_path, index_data)
        cleaned["index_removed"] = before - len(index_data["documents"])

    kg_path = rel_dir / "knowledge_graph.yaml"
    kg = _safe_load(kg_path) or {"edges": []}
    if kg.get("edges"):
        before = len(kg["edges"])
        kg["edges"] = edges_excluding_doc(kg["edges"], doc_id)
        if len(kg["edges"]) != before:
            _safe_dump(kg_path, kg)
            cleaned["kg_edges_removed"] = before - len(kg["edges"])

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