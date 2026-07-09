"""
app/routers/knowledge.py - Knowledge graph / ontology / wiki routes
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


def _load_yaml(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or default


@router.get("/graph", response_model=GraphResponse)
async def get_graph(settings: Settings = Depends(get_settings)):
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

    node_ids = {e.source for e in edges} | {e.target for e in edges}
    for d in index.get("documents", []):
        node_ids.add(d["id"])

    nodes = [
        GraphNode(id=nid, title=id2title.get(nid))
        for nid in sorted(node_ids)
    ]

    return GraphResponse(nodes=nodes, edges=edges)


@router.get("/graph/{doc_id}/relations", response_model=RelationsResponse)
async def get_doc_relations(doc_id: str, settings: Settings = Depends(get_settings)):
    rel_path = settings.relations_dir / f"{doc_id}.relations.yaml"
    if not rel_path.exists():
        return RelationsResponse(doc_id=doc_id, relations=[])

    data = _load_yaml(rel_path, {"relations": []})
    return RelationsResponse(
        doc_id=doc_id,
        relations=data.get("relations", []),
    )


@router.post("/relate/{doc_id}")
async def trigger_relate(
    doc_id: str,
    background_tasks: BackgroundTasks,
    settings: Settings = Depends(get_settings),
):
    summary_path = settings.wiki_dir / f"{doc_id}.summary.yaml"
    if not summary_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Summary for {doc_id} not found. Compile first.",
        )

    background_tasks.add_task(
        compile_then_relate,
        doc_id=doc_id,
        base_dir=settings.base_dir,
        settings=settings,
    )
    return {"message": f"Queued {doc_id} for relation re-computation.", "doc_id": doc_id}


@router.get("/ontology", response_model=OntologyResponse)
async def get_ontology(settings: Settings = Depends(get_settings)):
    data = _load_yaml(settings.global_ontology_file, {"ontology_tree": [], "total_nodes": 0})
    return OntologyResponse(
        ontology_tree=data.get("ontology_tree", []),
        total_nodes=data.get("total_nodes", 0),
        last_updated=data.get("last_updated"),
    )


@router.get("/wiki/index", response_model=WikiIndexResponse)
async def get_wiki_index(settings: Settings = Depends(get_settings)):
    index = _load_yaml(settings.index_file, {"documents": []})
    documents = index.get("documents", [])
    return WikiIndexResponse(
        total_docs=len(documents),
        documents=documents,
    )