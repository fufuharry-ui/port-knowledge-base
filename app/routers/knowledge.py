"""
app/routers/knowledge.py — 知识图谱 / 本体 / Wiki 路由
GET  /api/v1/graph                   全局知识图谱
GET  /api/v1/graph/{doc_id}/relations 单文档关系
POST /api/v1/relate/{doc_id}         手动触发关系重算
GET  /api/v1/ontology                全局本体树
GET  /api/v1/wiki/index              Wiki 统计快照
"""

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app.config import Settings, get_settings
from app.schemas import (
    GraphResponse, GraphEdge, GraphNode,
    OntologyResponse, RelationsResponse, WikiIndexResponse,
)
from app.utils.background import compile_then_relate

router = APIRouter()


# ─── 内部工具 ─────────────────────────────────────────────────────────────────

def _load_yaml(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or default


# ─── GET /api/v1/graph ────────────────────────────────────────────────────────

@router.get("/graph", response_model=GraphResponse)
async def get_graph(settings: Settings = Depends(get_settings)):
    """返回全局知识图谱（nodes + edges）"""
    kg = _load_yaml(settings.kg_file, {"edges": []})
    index = _load_yaml(settings.index_file, {"documents": []})

    id2title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}

    edges = [
        GraphEdge(
            source=e.get("source", ""),
            target=e.get("target", ""),
            type=e.get("type", "same_topic"),
            confidence=e.get("confidence"),
        )
        for e in kg.get("edges", [])
    ]

    # 从 edges 中推导 nodes（去重）
    node_ids = {e.source for e in edges} | {e.target for e in edges}
    # 也从 index 中加入所有文档节点
    for d in index.get("documents", []):
        node_ids.add(d["id"])

    nodes = [
        GraphNode(id=nid, title=id2title.get(nid))
        for nid in sorted(node_ids)
    ]

    return GraphResponse(nodes=nodes, edges=edges)


# ─── GET /api/v1/graph/{doc_id}/relations ─────────────────────────────────────

@router.get("/graph/{doc_id}/relations", response_model=RelationsResponse)
async def get_doc_relations(doc_id: str, settings: Settings = Depends(get_settings)):
    """返回单文档的关系列表（无关系文件时返回空列表）"""
    rel_path = settings.relations_dir / f"{doc_id}.relations.yaml"
    if not rel_path.exists():
        return RelationsResponse(doc_id=doc_id, relations=[])

    data = _load_yaml(rel_path, {"relations": []})
    return RelationsResponse(
        doc_id=doc_id,
        relations=data.get("relations", []),
    )


# ─── POST /api/v1/relate/{doc_id} ────────────────────────────────────────────

@router.post("/relate/{doc_id}")
async def trigger_relate(
    doc_id: str,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    """手动触发单文档的关系检测（要求文档已编译，即存在 summary 文件）"""
    summary_path = settings.wiki_dir / f"{doc_id}.summary.yaml"
    if not summary_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"文档 {doc_id} 的摘要不存在，请先完成编译。",
        )

    background_tasks.add_task(
        compile_then_relate,
        doc_id=doc_id,
        base_dir=settings.base_dir,
        settings=settings,
    )
    return {"message": f"已将 {doc_id} 的关系重算任务加入后台队列", "doc_id": doc_id}


# ─── GET /api/v1/ontology ─────────────────────────────────────────────────────

@router.get("/ontology", response_model=OntologyResponse)
async def get_ontology(settings: Settings = Depends(get_settings)):
    """返回全局本体树"""
    data = _load_yaml(settings.global_ontology_file, {"ontology_tree": [], "total_nodes": 0})
    return OntologyResponse(
        ontology_tree=data.get("ontology_tree", []),
        total_nodes=data.get("total_nodes", 0),
        last_updated=data.get("last_updated"),
    )


# ─── GET /api/v1/wiki/index ───────────────────────────────────────────────────

@router.get("/wiki/index", response_model=WikiIndexResponse)
async def get_wiki_index(settings: Settings = Depends(get_settings)):
    """返回 wiki/index.yaml 统计快照（供前端仪表盘使用）"""
    index = _load_yaml(settings.index_file, {"documents": []})
    documents = index.get("documents", [])
    return WikiIndexResponse(
        total_docs=len(documents),
        documents=documents,
    )
