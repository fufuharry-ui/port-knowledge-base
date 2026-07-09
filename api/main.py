import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import AsyncIterable
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import yaml

from scripts.ingest import ingest_file, generate_doc_id
from scripts.search import (
    search, get_llm_client, layer1_filter, layer2_score, layer3_answer,
    layer3_answer_stream, _load_ontology,
)
from scripts.ontology import expand_query_with_ontology, get_entity_neighbors
from scripts.lint import Linter
from scripts.consistency import (
    run_consistency_check, load_contradictions, find_contradiction_candidates,
)

app = FastAPI(title="Karpathy-Style LLM Wiki API", version="2.0.0")


_JIEBA_READY = False
try:
    _env_file = Path(__file__).resolve().parent.parent / ".env"
    if _env_file.exists():
        for _line in _env_file.read_text(encoding="utf-8").splitlines():
            if "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
except Exception:
    pass


try:
    import jieba
    list(jieba.cut("warmup"))
    _JIEBA_READY = True
except Exception:
    pass

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
RAW_DIR = BASE_DIR / "raw"
INDEX_FILE = WIKI_DIR / "index.yaml"
META_DIR = BASE_DIR / "meta"
ORIGINALS_DIR = BASE_DIR / "originals"
ORIGINALS_DIR.mkdir(exist_ok=True)


def _load_index() -> dict:
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"documents": []}
    return {"documents": []}


def _enrich_doc_status(docs: list[dict]) -> list[dict]:
    for doc in docs:
        doc_id = doc.get("id")
        if not doc_id:
            continue
        meta_path = RAW_DIR / f"{doc_id}.meta.yaml"
        try:
            if meta_path.exists():
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = yaml.safe_load(f) or {}
                if meta.get("status"):
                    doc["status"] = meta["status"]
        except Exception:
            pass
    return docs


def ingest_and_compile_task(file_path: Path):
    try:
        result = ingest_file(file_path)
        if result:
            compile_script = BASE_DIR / "scripts" / "compile.py"
            if compile_script.exists():
                subprocess.run(["python", str(compile_script)], cwd=str(BASE_DIR))
            try:
                from scripts.logger import global_logger
                global_logger.log("api_ingest", file_path.stem, "Background task finished.")
            except ImportError:
                pass
    except Exception as e:
        print(f"Background task failed: {e}")


class SearchQuery(BaseModel):
    query: str
    stream: bool = False

class QAQuery(BaseModel):
    query: str
    history: list[dict] = []


@app.get("/api/v1/health")
async def health():
    try:
        index = _load_index()
        doc_count = len(index.get("documents", []))
    except Exception:
        doc_count = 0

    llm_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())
    jieba_loaded = _JIEBA_READY

    ontology_loaded = False
    try:
        from scripts.search import GLOBAL_ONTOLOGY_FILE
        ontology_loaded = GLOBAL_ONTOLOGY_FILE.exists()
    except Exception:
        ontology_loaded = False

    return {
        "status": "ok",
        "doc_count": doc_count,
        "llm_configured": llm_configured,
        "jieba_loaded": jieba_loaded,
        "ontology_loaded": ontology_loaded,
        "version": app.version,
    }


@app.get("/api/v1/wiki/index")
async def wiki_index():
    index = _load_index()
    docs = _enrich_doc_status(index.get("documents", []))
    return {"total_docs": len(docs), "documents": docs}


@app.get("/api/v1/graph")
async def graph_data():
    index = _load_index()
    docs = index.get("documents", [])

    nodes = [{"id": d["id"], "title": d.get("title", d["id"])} for d in docs]
    edges = []

    kg_file = META_DIR / "relations" / "knowledge_graph.yaml"
    if kg_file.exists():
        try:
            with open(kg_file, "r", encoding="utf-8") as f:
                kg = yaml.safe_load(f) or {}
            for e in kg.get("edges", []):
                src = e.get("source")
                tgt = e.get("target")
                if src and tgt:
                    edges.append({
                        "source": src,
                        "target": tgt,
                        "type": e.get("type", "relates_to"),
                        "confidence": e.get("confidence"),
                    })
        except Exception:
            pass

    seen = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["type"])
        if key not in seen:
            seen.add(key)
            unique_edges.append(e)

    return {"nodes": nodes, "edges": unique_edges}


@app.get("/api/v1/ontology")
async def ontology_data():
    ont_file = META_DIR / "ontology" / "global_ontology.yaml"
    if not ont_file.exists():
        return {"ontology_tree": [], "total_nodes": 0, "last_updated": None}
    with open(ont_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {
        "ontology_tree": data.get("ontology_tree", []),
        "total_nodes": data.get("total_nodes", 0),
        "last_updated": data.get("last_updated"),
    }


@app.get("/api/v1/entity-graph")
async def entity_graph(term: str = "", depth: int = 1):
    ent_file = META_DIR / "ontology" / "entity_relations.yaml"
    edges = []
    if ent_file.exists():
        try:
            with open(ent_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            edges = data.get("edges", [])
        except Exception:
            edges = []

    from scripts.ontology import get_entity_neighbors
    neighbors = get_entity_neighbors(term, edges, depth=depth) if term else []
    relevant = set(neighbors) | ({term} if term else set())
    related_edges = [e for e in edges
                     if e.get("source") in relevant or e.get("target") in relevant]
    return {"term": term, "depth": depth, "neighbors": neighbors,
            "edges": related_edges, "total_edges": len(edges)}


@app.get("/api/v1/docs")
async def list_docs():
    index = _load_index()
    docs = index.get("documents", [])
    return {"documents": docs, "total": len(docs)}


@app.get("/api/v1/docs/{doc_id}")
async def get_doc(doc_id: str):
    index = _load_index()
    for doc in index.get("documents", []):
        if doc["id"] == doc_id:
            return doc
    raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")


@app.delete("/api/v1/docs/{doc_id}")
async def delete_doc(doc_id: str):
    from scripts.doc_admin import remove_doc
    summary = remove_doc(doc_id)
    if not summary.get("removed"):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"status": "deleted", **summary}


@app.post("/api/v1/docs/{doc_id}/recompile")
async def recompile_doc_endpoint(doc_id: str, background_tasks: BackgroundTasks):
    from scripts.doc_admin import recompile_doc
    result = recompile_doc(doc_id)
    if not result.get("reset"):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' meta not found")
    def _compile_task():
        try:
            import scripts.compile as cmod
            cmod._load_env()
            client = cmod.get_llm_client()
            cmod.compile_doc(doc_id, client,
                             os.environ.get("COMPILE_MODEL", "gpt-4o"),
                             os.environ.get("ONTOLOGY_MODEL", "gpt-4o-mini"))
        except Exception:
            pass
    background_tasks.add_task(_compile_task)
    return {"status": "recompiling", "doc_id": doc_id}


@app.post("/api/v1/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    file_path = ORIGINALS_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc_id = generate_doc_id()
    background_tasks.add_task(ingest_and_compile_task, file_path)

    return {
        "status": "processing",
        "doc_id": doc_id,
        "filename": file.filename,
        "message": "Ingested, compiling in background...",
    }


@app.post("/api/v1/ingest")
async def ingest_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    file_path = ORIGINALS_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc_id = generate_doc_id()
    background_tasks.add_task(ingest_and_compile_task, file_path)

    return {
        "status": "processing",
        "doc_id": doc_id,
        "filename": file.filename,
        "message": "File uploaded and background processing started.",
    }


@app.post("/api/v1/search")
async def search_endpoint(request: SearchQuery):
    try:
        client = get_llm_client()
        answer = search(request.query, client, verbose=False)
        import re
        source_ids = list({m for m in re.findall(r'\[?(doc_\w+)\]?', answer)})
        index = _load_index()
        id_to_title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
        sources = [{"doc_id": sid, "title": id_to_title.get(sid)} for sid in source_ids]
        return {"answer": answer, "sources": sources}
    except Exception as e:
        return {"answer": f"Search unavailable: {e}", "sources": []}


@app.get("/api/v1/search/stream")
async def search_stream(q: str):
    async def generate():
        try:
            client = get_llm_client()
            index = _load_index()
            ontology = _load_ontology()

            yield {"data": json.dumps({"type": "thought", "step": 1, "message": f"Filtering candidates..."})}
            candidates = layer1_filter(q, index, top_k=20, ontology=ontology)
            if not candidates:
                yield {"data": json.dumps({"delta": "No relevant documents found."})}
                yield {"data": "[DONE]"}
                return

            yield {"data": json.dumps({"type": "thought", "step": 2, "message": f"LLM scoring ({len(candidates)} candidates)..."})}
            try:
                top_docs = layer2_score(q, candidates, client, os.environ.get("SEARCH_MODEL", "gpt-4o"), top_k=5)
            except Exception:
                top_docs = candidates[:3]

            id_to_title = {d["id"]: d.get("title", d["id"]) for d in candidates}
            source_ids = [d["id"] for d in top_docs]
            sources_line = "Sources: " + " | ".join(
                f"`{sid}` {id_to_title.get(sid, '')}" for sid in source_ids
            )
            yield {"data": json.dumps({"delta": sources_line + "\n\n"})}

            yield {"data": json.dumps({"type": "thought", "step": 3, "message": "Generating answer..."})}
            try:
                contradictions = load_contradictions().get("contradictions", [])
                model = os.environ.get("SEARCH_MODEL", "gpt-4o")
                for token in layer3_answer_stream(
                    q, top_docs, client, model, index, contradictions=contradictions,
                ):
                    yield {"data": json.dumps({"delta": token})}
            except Exception as e:
                yield {"data": json.dumps({"delta": f"\n\nError generating answer: {e}"})}

            yield {"data": "[DONE]"}
        except Exception as e:
            yield {"data": json.dumps({"delta": f"Search failed: {e}"})}
            yield {"data": "[DONE]"}

    return EventSourceResponse(generate())


@app.post("/api/v1/qa")
async def qa_stream(request: QAQuery):
    async def generate():
        try:
            client = get_llm_client()
            index = _load_index()
            model = os.environ.get("SEARCH_MODEL", "gpt-4o")

            ontology = _load_ontology()
            expansion_terms = []
            if ontology:
                try:
                    expansion_terms = expand_query_with_ontology(
                        request.query, ontology.get("ontology_tree", [])
                    )
                except Exception:
                    expansion_terms = []

            yield {"data": json.dumps({"type": "thought", "step": 1, "message": "BM25 filtering..."})}
            candidates = layer1_filter(request.query, index, top_k=20, ontology=ontology)

            if expansion_terms:
                shown = ", ".join(expansion_terms[:8])
                yield {"data": json.dumps({"type": "thought", "step": 1, "message": f"Ontology expansion: {shown}"})}

            if not candidates:
                yield {"data": json.dumps({"type": "delta", "text": "No relevant documents found."})}
                yield {"data": json.dumps({"type": "done"})}
                return

            yield {"data": json.dumps({"type": "thought", "step": 2, "message": f"LLM scoring ({len(candidates)} -> Top-5)..."})}
            try:
                top_docs = layer2_score(request.query, candidates, client, model, top_k=5)
            except Exception:
                top_docs = candidates[:3]

            source_ids = [d["id"] for d in top_docs]
            id_to_title = {d["id"]: d.get("title", d["id"]) for d in top_docs}
            citations = [
                {"ref": f"[{i+1}]", "doc_id": sid, "title": id_to_title.get(sid)}
                for i, sid in enumerate(source_ids)
            ]
            yield {"data": json.dumps({"type": "source", "citations": citations})}

            yield {"data": json.dumps({"type": "entity", "ids": source_ids})}

            yield {"data": json.dumps({"type": "thought", "step": 3, "message": "Generating answer..."})}

            try:
                contradictions = load_contradictions().get("contradictions", [])
                for token in layer3_answer_stream(
                    request.query, top_docs, client, model, index,
                    contradictions=contradictions, history=request.history,
                ):
                    if token:
                        yield {"data": json.dumps({"type": "delta", "text": token})}
            except Exception as e:
                yield {"data": json.dumps({"type": "delta", "text": f"\n\nAnswer generation failed: {e}"})}

            yield {"data": json.dumps({"type": "done"})}

        except Exception as e:
            yield {"data": json.dumps({"type": "delta", "text": f"Service error: {e}"})}
            yield {"data": json.dumps({"type": "done"})}

    return EventSourceResponse(generate())


@app.post("/api/v1/lint")
async def lint_endpoint():
    linter = Linter()
    orphans = linter.detect_orphan_pages()
    missing_concepts = linter.detect_missing_concepts()

    try:
        client = get_llm_client()
        search_model = os.environ.get("SEARCH_MODEL", "gpt-4o")
        contradictions = linter.detect_contradictions(client, search_model)
    except Exception:
        contradictions = []

    linter.run_lint()

    return {
        "status": "success",
        "report": {
            "orphans_count": len(orphans),
            "missing_concepts_count": len(missing_concepts),
            "contradictions_count": len(contradictions),
        },
    }


@app.get("/api/v1/consistency")
async def consistency_get():
    report = load_contradictions()
    return {
        "status": "success",
        "total": report.get("total", 0),
        "candidates_checked": report.get("candidates_checked", 0),
        "last_updated": report.get("last_updated"),
        "contradictions": report.get("contradictions", []),
    }


@app.post("/api/v1/consistency")
async def consistency_run():
    try:
        client = get_llm_client()
        model = os.environ.get("RELATE_MODEL", os.environ.get("SEARCH_MODEL", "gpt-4o"))
        report = run_consistency_check(client, model)
    except Exception as e:
        return {"status": "error", "message": f"Audit failed: {e}", "total": 0,
                "contradictions": []}
    return {
        "status": "success",
        "total": report.get("total", 0),
        "candidates_checked": report.get("candidates_checked", 0),
        "last_updated": report.get("last_updated"),
        "contradictions": report.get("contradictions", []),
    }