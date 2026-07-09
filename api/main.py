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


# ─── .env 加载(启动即读,避免 /health 在首次检索前读到空 env) ────────────────
# 复用 scripts 的 os.environ.setdefault 语义:真实 env 优先,.env 不覆盖已设值。
# 用直接路径,不依赖下方 BASE_DIR(其定义在本块之后,此时尚未赋值)。
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


# ─── jieba 预热 (Big-Loop #5, P-4) ───────────────────────────────────────────
# 进程启动即加载分词词典,消除首次检索的冷启动延迟(~1s)。
try:
    import jieba  # noqa: F401
    list(jieba.cut("智慧港口岸桥远控预热"))  # 触发词典加载
    _JIEBA_READY = True
except Exception:
    pass  # jieba 不可用时 BM25 回退到空白分词,不影响启动

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
RAW_DIR = BASE_DIR / "raw"
INDEX_FILE = WIKI_DIR / "index.yaml"
META_DIR = BASE_DIR / "meta"
ORIGINALS_DIR = BASE_DIR / "originals"
ORIGINALS_DIR.mkdir(exist_ok=True)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_index() -> dict:
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"documents": []}
    return {"documents": []}


def _enrich_doc_status(docs: list[dict]) -> list[dict]:
    """用每篇 .meta.yaml 的权威 status 填充 index 条目(UX 修复)。

    背景:compile.py 只更新 raw/{doc_id}.meta.yaml 的 status(raw→compiled),
    但 wiki/index.yaml 条目的 status 字段不同步(常滞留 None)。仪表盘据此
    统计"已编译"数,会误显示 0。这里在 /wiki/index 返回时即时用 .meta.yaml
    的权威值覆盖,不改 compile.py 核心,也不写回 index.yaml(只读合并)。
    """
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
            pass  # 单篇 meta 读取失败不影响整体
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
                global_logger.log("api_ingest", file_path.stem, "Background task finished successfully.")
            except ImportError:
                pass
    except Exception as e:
        print(f"Background task failed: {e}")

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class SearchQuery(BaseModel):
    query: str
    stream: bool = False

class QAQuery(BaseModel):
    query: str
    # Big-Loop #8: 多轮对话历史。每条 {role:'user'|'assistant', content:str}。
    # 后端注入 Layer3 prompt,让 LLM 解析追问代词。默认空 → 单轮(向后兼容)。
    history: list[dict] = []

# ─── GET /api/v1/health (落地增强:部署健康检查) ─────────────────────────────

@app.get("/api/v1/health")
async def health():
    """运维健康检查。返回服务状态 + 关键依赖可用性(部署监控用)。

    设计:只读、快速、不调 LLM、不抛异常(即使部分依赖缺失也返回 200 + 如实字段)。
    """
    # 文档数
    try:
        index = _load_index()
        doc_count = len(index.get("documents", []))
    except Exception:
        doc_count = 0

    # LLM 是否配置(不检查有效性,只看 Key 是否存在)
    llm_configured = bool(os.environ.get("OPENAI_API_KEY", "").strip())

    # jieba 是否就绪(用预热时设的标志,比探测内部属性可靠)
    jieba_loaded = _JIEBA_READY

    # 本体是否加载
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


# ─── GET /api/v1/wiki/index ───────────────────────────────────────────────────

@app.get("/api/v1/wiki/index")
async def wiki_index():
    """Return the full wiki index as JSON.

    UX 修复:用 .meta.yaml 的权威 status 即时填充(见 _enrich_doc_status),
    否则仪表盘"已编译"统计恒为 0。
    """
    index = _load_index()
    docs = _enrich_doc_status(index.get("documents", []))
    return {"total_docs": len(docs), "documents": docs}

# ─── GET /api/v1/graph ───────────────────────────────────────────────────────

@app.get("/api/v1/graph")
async def graph_data():
    """Build nodes and edges from index and the knowledge graph.

    Big-Loop #1 修正:旧实现读 per-doc 文件取 rel.get('source')/'target'),
    但 per-doc 关系实际字段是 target_doc_id 且无 source;又读 KG 的 relations
    键,但 KG 实际键是 edges → 实际返回 0 条边,前端图谱无连线。改为以
    knowledge_graph.yaml(权威汇总)的 edges 为准。
    """
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

    # Deduplicate edges
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
    """Return the global ontology tree (供前端本体视图;Big-Loop #1 新增)。"""
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
    """返回某术语的实体级邻居(Big-Loop #2 新增,供前端实体图谱查询)。

    ?term=5G专网&depth=2 → 返回该术语在 entity_relations.yaml 中的多跳邻居 + 相关边。
    """
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
    # 只返回与该 term 相关的边(邻居 + 自身)
    relevant = set(neighbors) | ({term} if term else set())
    related_edges = [e for e in edges
                     if e.get("source") in relevant or e.get("target") in relevant]
    return {"term": term, "depth": depth, "neighbors": neighbors,
            "edges": related_edges, "total_edges": len(edges)}

# ─── GET /api/v1/docs ────────────────────────────────────────────────────────

@app.get("/api/v1/docs")
async def list_docs():
    index = _load_index()
    docs = index.get("documents", [])
    return {"documents": docs, "total": len(docs)}

# ─── GET /api/v1/docs/{doc_id} ───────────────────────────────────────────────

@app.get("/api/v1/docs/{doc_id}")
async def get_doc(doc_id: str):
    index = _load_index()
    for doc in index.get("documents", []):
        if doc["id"] == doc_id:
            return doc
    raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")


# ─── 文档管理:删除 + 重编译(Loop #10)─────────────────────────────────────────

@app.delete("/api/v1/docs/{doc_id}")
async def delete_doc(doc_id: str):
    """删除文档 + 全部产物 + 清理 index/KG/entity_relations 引用。

    此前知识库只能追加无法维护——上传错文档/编译失败时无法清理。
    """
    from scripts.doc_admin import remove_doc
    summary = remove_doc(doc_id)
    if not summary.get("removed"):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"status": "deleted", **summary}


@app.post("/api/v1/docs/{doc_id}/recompile")
async def recompile_doc_endpoint(doc_id: str, background_tasks: BackgroundTasks):
    """重置文档状态为 raw 并触发重编译(error 文档重试用)。"""
    from scripts.doc_admin import recompile_doc
    result = recompile_doc(doc_id)
    if not result.get("reset"):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' meta not found")
    # 后台触发编译(复用 compile.compile_doc)
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

# ─── POST /api/v1/upload (alias for ingest) ──────────────────────────────────

@app.post("/api/v1/upload")
async def upload_document(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Frontend-compatible upload endpoint (alias for ingest with BackgroundTask)."""
    file_path = ORIGINALS_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    doc_id = generate_doc_id()
    background_tasks.add_task(ingest_and_compile_task, file_path)

    return {
        "status": "processing",
        "doc_id": doc_id,
        "filename": file.filename,
        "message": "摄入成功，后台自动编译中...",
    }

# ─── POST /api/v1/ingest ─────────────────────────────────────────────────────

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

# ─── POST /api/v1/search (sync JSON) ─────────────────────────────────────────

@app.post("/api/v1/search")
async def search_endpoint(request: SearchQuery):
    try:
        client = get_llm_client()
        answer = search(request.query, client, verbose=False)
        # Extract source doc_ids from answer text
        import re
        source_ids = list({m for m in re.findall(r'\[?(doc_\w+)\]?', answer)})
        index = _load_index()
        id_to_title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
        sources = [{"doc_id": sid, "title": id_to_title.get(sid)} for sid in source_ids]
        return {"answer": answer, "sources": sources}
    except Exception as e:
        return {"answer": f"⚠️ 搜索服务暂时不可用: {e}", "sources": []}

# ─── GET /api/v1/search/stream (SSE) ─────────────────────────────────────────

@app.get("/api/v1/search/stream")
async def search_stream(q: str):
    """SSE streaming search endpoint used by the Search page."""
    async def generate():
        try:
            client = get_llm_client()
            index = _load_index()
            # Big-Loop #1: 本体查询扩展(缺失则降级纯 BM25)
            ontology = _load_ontology()

            # 落地增强:分步 thought 事件,让用户看到检索进度(消除 14-21s 干等焦虑)
            yield {"data": json.dumps({"type": "thought", "step": 1, "message": f"🔍 初筛候选文档(BM25+本体扩展)..."})}
            # Layer 1
            candidates = layer1_filter(q, index, top_k=20, ontology=ontology)
            if not candidates:
                yield {"data": json.dumps({"delta": "⚠️ 未找到相关文档，请调整检索词。"})}
                yield {"data": "[DONE]"}
                return

            yield {"data": json.dumps({"type": "thought", "step": 2, "message": f"🧠 LLM 精选 Top-5(共{len(candidates)}篇候选)..."})}
            # Layer 2
            try:
                top_docs = layer2_score(q, candidates, client, os.environ.get("SEARCH_MODEL", "gpt-4o"), top_k=5)
            except Exception:
                top_docs = candidates[:3]

            # Emit sources as delta header
            id_to_title = {d["id"]: d.get("title", d["id"]) for d in candidates}
            source_ids = [d["id"] for d in top_docs]
            sources_line = "📎 **来源：** " + " | ".join(
                f"`{sid}` {id_to_title.get(sid, '')}" for sid in source_ids
            )
            yield {"data": json.dumps({"delta": sources_line + "\n\n"})}

            # Layer 3 - 真流式(Big-Loop #5:layer3_answer_stream 逐 token)
            yield {"data": json.dumps({"type": "thought", "step": 3, "message": "✍️ 生成精确回答并注入原文引用..."})}
            try:
                # Big-Loop #3: 加载已知矛盾,Top 文档间有矛盾 → 回答附 ⚠️ 提示
                contradictions = load_contradictions().get("contradictions", [])
                model = os.environ.get("SEARCH_MODEL", "gpt-4o")
                for token in layer3_answer_stream(
                    q, top_docs, client, model, index, contradictions=contradictions,
                ):
                    yield {"data": json.dumps({"delta": token})}
            except Exception as e:
                yield {"data": json.dumps({"delta": f"\n\n⚠️ 生成回答时出错: {e}"})}

            yield {"data": "[DONE]"}
        except Exception as e:
            yield {"data": json.dumps({"delta": f"⚠️ 检索失败: {e}"})}
            yield {"data": "[DONE]"}

    return EventSourceResponse(generate())

# ─── POST /api/v1/qa (SSE Q&A with thought trace) ────────────────────────────

@app.post("/api/v1/qa")
async def qa_stream(request: QAQuery):
    """SSE streaming Q&A used by the ChatPanel on /qa page.
    Emits: thought, source, entity, delta, done events.
    """
    async def generate():
        try:
            client = get_llm_client()
            index = _load_index()
            model = os.environ.get("SEARCH_MODEL", "gpt-4o")

            # Big-Loop #1: 本体查询扩展(缺失则降级纯 BM25)
            ontology = _load_ontology()
            expansion_terms = []
            if ontology:
                try:
                    expansion_terms = expand_query_with_ontology(
                        request.query, ontology.get("ontology_tree", [])
                    )
                except Exception:
                    expansion_terms = []

            # Step 1: Thought - BM25 filter
            yield {"data": json.dumps({"type": "thought", "step": 1, "message": "🔍 BM25 关键词初筛中..."})}
            candidates = layer1_filter(request.query, index, top_k=20, ontology=ontology)

            # Step 1.5: 本体扩展的可 thought(若有扩展词,显式告知用户)
            if expansion_terms:
                shown = ", ".join(expansion_terms[:8])
                yield {"data": json.dumps({"type": "thought", "step": 1, "message": f"🧭 本体扩展词: {shown}"})}

            if not candidates:
                yield {"data": json.dumps({"type": "delta", "text": "⚠️ 未找到相关文档，请调整提问关键词。"})}
                yield {"data": json.dumps({"type": "done"})}
                return

            # Step 2: Thought - LLM scoring
            yield {"data": json.dumps({"type": "thought", "step": 2, "message": f"🧠 LLM 精选候选文档 ({len(candidates)} → Top-5)..."})}
            try:
                top_docs = layer2_score(request.query, candidates, client, model, top_k=5)
            except Exception:
                top_docs = candidates[:3]

            # Step 3: Emit sources
            source_ids = [d["id"] for d in top_docs]
            id_to_title = {d["id"]: d.get("title", d["id"]) for d in top_docs}
            citations = [
                {"ref": f"[{i+1}]", "doc_id": sid, "title": id_to_title.get(sid)}
                for i, sid in enumerate(source_ids)
            ]
            yield {"data": json.dumps({"type": "source", "citations": citations})}

            # Step 4: Emit entity highlights
            yield {"data": json.dumps({"type": "entity", "ids": source_ids})}

            # Step 5: Thought - generating answer
            yield {"data": json.dumps({"type": "thought", "step": 3, "message": "✍️ 生成精确回答并注入原文引用..."})}

            # Step 6: Stream answer (Big-Loop #5: 真流式透传 token,首 token 立即可见)
            try:
                # Big-Loop #3: 加载已知矛盾,Top 文档间有矛盾 → 回答附 ⚠️ 提示
                contradictions = load_contradictions().get("contradictions", [])
                for token in layer3_answer_stream(
                    request.query, top_docs, client, model, index,
                    contradictions=contradictions, history=request.history,
                ):
                    if token:
                        yield {"data": json.dumps({"type": "delta", "text": token})}
            except Exception as e:
                yield {"data": json.dumps({"type": "delta", "text": f"\n\n⚠️ 生成回答失败: {e}"})}

            yield {"data": json.dumps({"type": "done"})}

        except Exception as e:
            yield {"data": json.dumps({"type": "delta", "text": f"⚠️ 服务异常: {e}"})}
            yield {"data": json.dumps({"type": "done"})}

    return EventSourceResponse(generate())

# ─── POST /api/v1/lint ────────────────────────────────────────────────────────

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


# ─── GET/POST /api/v1/consistency (Big-Loop #3: 跨文档一致性稽核) ─────────────

@app.get("/api/v1/consistency")
async def consistency_get():
    """查看已知矛盾报告(不触发 LLM,只读 contradictions.yaml)。"""
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
    """触发全库一致性稽核:生成候选对 → LLM 逐对判定 → 写 contradictions.yaml。

    返回报告摘要。LLM 不可用/无候选 → 返回空报告(降级,不报错)。
    """
    try:
        client = get_llm_client()
        model = os.environ.get("RELATE_MODEL", os.environ.get("SEARCH_MODEL", "gpt-4o"))
        report = run_consistency_check(client, model)
    except Exception as e:
        return {"status": "error", "message": f"稽核失败: {e}", "total": 0,
                "contradictions": []}
    return {
        "status": "success",
        "total": report.get("total", 0),
        "candidates_checked": report.get("candidates_checked", 0),
        "last_updated": report.get("last_updated"),
        "contradictions": report.get("contradictions", []),
    }
