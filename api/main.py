import json
import logging
import os
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterable
from fastapi import FastAPI, Request, UploadFile, File, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
import yaml

from api.compile_jobs import (
    COMPILE_EXECUTION_LOCK,
    prepare_recompile_transaction,
    run_compile_task,
    sanitize_compile_error,
)
from api.compile_transactions import (
    TransactionState,
    list_active_manifests,
    recover_startup,
    recover_transaction,
    transition_manifest,
)
from api.durable_fs import probe_durable_directory
from api.runtime_guard import (
    ApiInstanceLock,
    CompileRuntimeConfig,
    ServiceReadiness,
    load_compile_runtime_config,
)
from scripts.doc_admin import bind_doc_compile_job, read_doc_meta
from scripts.ingest import PARSERS, get_file_hash, ingest_file
from scripts.search import (
    search, get_llm_client, layer1_filter, layer2_score, layer3_answer,
    layer3_answer_stream, _load_ontology,
)
from scripts.ontology import expand_query_with_ontology, get_entity_neighbors
from scripts.lint import Linter
from scripts.consistency import (
    run_consistency_check, load_contradictions, find_contradiction_candidates,
)

logger = logging.getLogger(__name__)

# ─── E005 Task 8:应用运行时与业务门禁原语 ────────────────────────────────────


@dataclass
class AppRuntime:
    """lifespan 启动成功后挂接到 app.state.runtime 的运行时容器。"""

    config: CompileRuntimeConfig
    readiness: ServiceReadiness
    instance_lock: ApiInstanceLock


#: 门禁豁免路径(设计 §19):仅 liveness 与 readiness 探针。
GATE_EXEMPT_PATHS = frozenset({"/api/v1/health", "/api/v1/ready"})

#: 门禁响应体:只含稳定错误码,绝不泄露 reason、job、路径或 PID。
RECOVERY_REQUIRED_BODY = {"detail": {"code": "recovery_required"}}


def _current_service_mode(app: FastAPI) -> str:
    """返回粗粒度 service_mode("ready" | "recovery_required")。

    runtime 缺失(lifespan 未运行)时:显式 allow_unmanaged 的测试实例按
    "ready" 处理,其余一律 fail-closed 视为 "recovery_required"。
    """
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        if getattr(app.state, "allow_unmanaged", False):
            return "ready"
        return "recovery_required"
    mode, _reason = runtime.readiness.snapshot()
    return mode


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

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
RAW_DIR = BASE_DIR / "raw"
INDEX_FILE = WIKI_DIR / "index.yaml"
META_DIR = BASE_DIR / "meta"
ORIGINALS_DIR = BASE_DIR / "originals"
ORIGINALS_DIR.mkdir(exist_ok=True)

# 编译调度串行点:_schedule_compile 的 读/判 → 置 compiling → add_task 临界区。
COMPILE_SCHEDULE_LOCK = threading.Lock()

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


def _load_document_catalog() -> list[dict]:
    """管理端 catalog:compiled index + raw meta 合并投影。

    供 /wiki/index、/docs、/docs/{id} 使用,让 raw/compiling 文档也立即可见
    (CAP-PROGRESS)。搜索与 /qa 继续用 _load_index()(只读 compiled),不污染候选。
    compiled index 条目被 raw meta 的权威字段(title/status/file_hash/...)覆盖。
    """
    compiled = _load_index().get("documents", [])
    by_id = {doc["id"]: dict(doc) for doc in compiled if doc.get("id")}
    # E004-FIX-02:error_message 是后端诊断字段,公共 catalog 一律不投影;
    # 旧 index 中可能残留的该字段同样剥除,只保留稳定 error_code。
    for doc in by_id.values():
        doc.pop("error_message", None)

    for meta_path in sorted(RAW_DIR.glob("*.meta.yaml")):
        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except Exception:
            continue
        doc_id = meta.get("id")
        if not doc_id:
            continue
        doc = by_id.setdefault(doc_id, {"id": doc_id, "abstract_short": "", "ontology_terms": []})
        for key in ("title", "status", "source_type", "file_hash", "char_count",
                    "language", "ingested_at", "error_code"):
            if key in meta:
                doc[key] = meta[key]

    return sorted(by_id.values(), key=lambda d: d.get("ingested_at", ""), reverse=True)


def _find_duplicate_doc(file_hash: str) -> dict | None:
    """按 file_hash 查既有文档(跨 compiled+raw),命中即跳过,避免幽灵任务。"""
    for doc in _load_document_catalog():
        if doc.get("file_hash") == file_hash:
            return doc
    return None


def _safe_upload_name(filename: str | None) -> str:
    """只取 basename(剥离路径分量),并校验扩展名在 PARSERS 内,否则 422。"""
    supplied = filename or ""
    safe = Path(supplied).name
    suffix = Path(safe).suffix.lower()
    if not safe or suffix not in PARSERS:
        raise HTTPException(status_code=422, detail=f"不支持的文件格式 '{suffix or supplied}'")
    return safe


def _unique_original_path(filename: str) -> Path:
    """originals/ 内避免重名:同名时追加 _2/_3/..."""
    candidate = ORIGINALS_DIR / filename
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    sequence = 2
    while True:
        candidate = ORIGINALS_DIR / f"{stem}_{sequence}{suffix}"
        if not candidate.exists():
            return candidate
        sequence += 1


def _stage_upload(file: UploadFile) -> tuple[Path | None, dict | None]:
    """落盘 + 哈希去重。返回 (staged_path, None) 或 (None, duplicate_doc)。

    先写临时文件再哈希,命中重复则删临时文件(不写最终 originals/);否则原子替换到唯一目标。
    """
    filename = _safe_upload_name(file.filename)
    ORIGINALS_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix.lower()
    with tempfile.NamedTemporaryFile(dir=ORIGINALS_DIR, suffix=suffix, delete=False) as handle:
        shutil.copyfileobj(file.file, handle)
        staged = Path(handle.name)

    file_hash = get_file_hash(staged)
    duplicate = _find_duplicate_doc(file_hash)
    if duplicate:
        staged.unlink(missing_ok=True)
        return None, duplicate

    destination = _unique_original_path(filename)
    staged.replace(destination)
    return destination, None


def _endpoint_runtime(request: Request) -> AppRuntime | None:
    """解析当前请求所属 app 的运行时;缺失时返回 None(调用方 fail-closed)。

    config/readiness 的唯一来源是 app.state.runtime(由 lifespan 挂接);
    绝不另行构造 ServiceReadiness,避免第二 readiness 来源。
    """
    return getattr(request.app.state, "runtime", None)


def _list_active_manifests_guarded(runtime: AppRuntime) -> list:
    """枚举活动事务;扫描失败即无法证明无活动事务,失败关闭并进入门禁。"""
    try:
        return list_active_manifests(runtime.config)
    except Exception as exc:
        logger.error("active transaction scan failed: %s", exc)
        runtime.readiness.mark_recovery_required("transaction_scan_failed")
        raise HTTPException(
            status_code=503, detail={"code": "recovery_required"}
        ) from None


def _rollback_unaccepted_compile(
    runtime: AppRuntime,
    manifest,
    *,
    reason_code: str,
    reason_message: str,
) -> bool:
    """请求未被接受时经恢复库回滚事务(设计 §11);返回 False 表示一致性
    无法证明(失败关闭:readiness 已进入 recovery_required)。"""
    try:
        result = recover_transaction(
            BASE_DIR,
            runtime.config,
            manifest.job_dir,
            reason_code=reason_code,
            reason_message=reason_message,
        )
    except Exception as exc:
        logger.error(
            "compile transaction rollback raised for job %s: %s",
            manifest.job_id, exc,
        )
        runtime.readiness.mark_recovery_required("rollback_failed")
        return False
    if result.blocked:
        logger.error(
            "compile transaction rollback blocked for job %s: %s",
            manifest.job_id, result.reason,
        )
        runtime.readiness.mark_recovery_required(
            sanitize_compile_error(result.reason or "rollback_failed")
        )
        return False
    return True


def _schedule_compile(
    background_tasks: BackgroundTasks, doc_id: str, runtime: AppRuntime | None
) -> None:
    """统一编译调度入口(/upload、/ingest、/docs/{id}/recompile 共用)。

    E005 Task 9:调度迁移为持久化事务(设计 §11)。COMPILE_SCHEDULE_LOCK 覆盖
    readiness 校验 → 活动事务检查 → PREPARED 准备 → meta 绑定 → SCHEDULED
    持久迁移 → add_task 整个临界区:并发请求中恰一个能进入编译,其余得 409。
    请求只有在事务正式发布、meta 绑定、Manifest 持久化 SCHEDULED 且后台任务
    登记完成后才被接受;准备/绑定/登记失败时请求线程立即经恢复库回滚,
    绝不留下活动事务或半绑定 meta。detail 只含稳定错误码,不泄露内部技术细节。
    runtime 缺失时 fail-closed(生产路径门禁已 503,此处兜底直接调用)。
    """
    if runtime is None:
        logger.error("schedule compile for %s rejected: runtime absent", doc_id)
        raise HTTPException(status_code=503, detail={"code": "recovery_required"})
    config = runtime.config
    readiness = runtime.readiness
    with COMPILE_SCHEDULE_LOCK:
        try:
            readiness.require_ready()
        except RuntimeError:
            raise HTTPException(
                status_code=503, detail={"code": "recovery_required"}
            ) from None
        active = _list_active_manifests_guarded(runtime)
        if active:
            same_doc = any(manifest.doc_id == doc_id for manifest in active)
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "compile_in_progress" if same_doc else "knowledge_base_busy"
                },
            )
        meta = read_doc_meta(doc_id, base_dir=BASE_DIR)
        if meta is None:
            raise HTTPException(status_code=404, detail="Document metadata not found")
        if meta.get("status") == "compiling":
            raise HTTPException(
                status_code=409, detail={"code": "compile_in_progress"}
            )
        try:
            manifest = prepare_recompile_transaction(doc_id, BASE_DIR, config)
        except Exception as exc:
            logger.error(
                "compile transaction preparation failed for %s: %s", doc_id, exc
            )
            raise HTTPException(
                status_code=503, detail={"code": "compile_transaction_unavailable"}
            ) from None

        scheduled_at = datetime.now(timezone.utc)
        deadline = scheduled_at + timedelta(seconds=manifest.timeout_seconds)
        scheduling_error: Exception | None = None
        scheduled = False
        try:
            bound = bind_doc_compile_job(
                doc_id,
                manifest.job_id,
                scheduled_at.isoformat(),
                deadline.isoformat(),
                base_dir=BASE_DIR,
            )
            if bound:
                manifest = transition_manifest(
                    manifest.job_dir,
                    expected=TransactionState.PREPARED,
                    target=TransactionState.SCHEDULED,
                    scheduled_at=scheduled_at.isoformat(),
                )
                scheduled = True
        except Exception as exc:
            scheduling_error = exc
        if not scheduled:
            logger.error(
                "compile scheduling failed for %s (job %s): %s",
                doc_id, manifest.job_id,
                scheduling_error or "doc binding refused",
            )
            # 先按绑定前 meta 分类响应码,再回滚(回滚可能写文档终态)。
            meta_after = read_doc_meta(doc_id, base_dir=BASE_DIR)
            status_after = (
                meta_after.get("status") if isinstance(meta_after, dict) else None
            )
            if not _rollback_unaccepted_compile(
                runtime,
                manifest,
                reason_code="compile_failed",
                reason_message=(
                    "compile scheduling failed before acceptance: "
                    f"{scheduling_error or 'doc binding refused'}"
                ),
            ):
                raise HTTPException(
                    status_code=503, detail={"code": "recovery_required"}
                )
            if meta_after is None:
                raise HTTPException(
                    status_code=404, detail="Document metadata not found"
                )
            if status_after == "compiling":
                raise HTTPException(
                    status_code=409, detail={"code": "compile_in_progress"}
                )
            raise HTTPException(
                status_code=503, detail={"code": "compile_transaction_unavailable"}
            )
        try:
            background_tasks.add_task(
                run_compile_task, manifest.job_id, BASE_DIR, config, readiness
            )
        except Exception as exc:
            logger.error(
                "background task registration failed for job %s: %s",
                manifest.job_id, exc,
            )
            if not _rollback_unaccepted_compile(
                runtime,
                manifest,
                reason_code="compile_failed",
                reason_message=f"background task registration failed: {exc}",
            ):
                raise HTTPException(
                    status_code=503, detail={"code": "recovery_required"}
                ) from None
            raise HTTPException(
                status_code=503, detail={"code": "compile_transaction_unavailable"}
            ) from None


def _accept_upload(
    background_tasks: BackgroundTasks, file: UploadFile, runtime: AppRuntime | None
) -> dict:
    """权威上传契约:落盘 → 去重 → 同步 ingest_file → 调度按 doc_id 编译。

    返回权威 doc_id(来自 ingest_file,而非上传时预生成),含 skipped 字段。
    /upload 与 /ingest 共用此契约。
    """
    stored, duplicate = _stage_upload(file)
    if duplicate:
        return {
            "status": "skipped",
            "skipped": True,
            "doc_id": duplicate["id"],
            "filename": file.filename,
            "message": "文件已存在，已跳过",
        }

    assert stored is not None
    meta = ingest_file(stored)
    if not meta:
        raise HTTPException(status_code=422, detail="文件无法解析或内容为空")

    doc_id = meta["id"]
    _schedule_compile(background_tasks, doc_id, runtime)
    return {
        "status": "processing",
        "skipped": False,
        "doc_id": doc_id,
        "filename": stored.name,
        "message": "摄入成功，后台自动编译中...",
    }

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

async def health(request: Request):
    """运维健康检查(liveness)。返回服务状态 + 关键依赖可用性(部署监控用)。

    设计:只读、快速、不调 LLM、不抛异常(即使部分依赖缺失也返回 200 + 如实字段)。
    E005 Task 8:追加 ready 布尔与粗粒度 service_mode;不暴露 reason、job、
    路径或 PID(设计 §19)。liveness 永远 200,不受业务门禁影响。
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

    service_mode = _current_service_mode(request.app)

    return {
        "status": "ok",
        "doc_count": doc_count,
        "llm_configured": llm_configured,
        "jieba_loaded": jieba_loaded,
        "ontology_loaded": ontology_loaded,
        "ready": service_mode == "ready",
        "service_mode": service_mode,
        "version": request.app.version,
    }


# ─── GET /api/v1/ready (E005 Task 8:readiness 探针) ─────────────────────────

async def ready(request: Request):
    """readiness 探针:ready 时 200 {"status": "ready"};门禁时 503(安全响应体)。"""
    if _current_service_mode(request.app) == "ready":
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "not_ready"})


# ─── GET /api/v1/wiki/index ───────────────────────────────────────────────────

async def wiki_index():
    """Return the full wiki document catalog (compiled + raw) as JSON.

    CAP-PROGRESS:用 _load_document_catalog 让 raw/compiling 文档立即可见,
    而非等编译完成。搜索/QA 继续走 _load_index()(只 compiled),不污染候选。
    """
    docs = _load_document_catalog()
    return {"total_docs": len(docs), "documents": docs}

# ─── GET /api/v1/graph ───────────────────────────────────────────────────────

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

async def list_docs():
    docs = _load_document_catalog()
    return {"documents": docs, "total": len(docs)}

# ─── GET /api/v1/docs/{doc_id} ───────────────────────────────────────────────

async def get_doc(doc_id: str):
    for doc in _load_document_catalog():
        if doc["id"] == doc_id:
            return doc
    raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")


# ─── 文档管理:删除 + 重编译(Loop #10)─────────────────────────────────────────

def _remove_doc_exclusively(doc_id: str, runtime: AppRuntime | None) -> dict:
    """删除与完整编译事务互斥(E004-FIX-02 + E005 Task 9)。

    锁顺序恒为 COMPILE_SCHEDULE_LOCK → COMPILE_EXECUTION_LOCK(非阻塞),与
    _schedule_compile(仅调度锁)、run_compile_task(仅执行锁)不构成循环等待。
    执行锁被占用说明有编译事务(快照/编译/回滚)在执行:删除必须立即 409,
    不得阻塞等待数分钟,否则失败回滚或成功发布都可能复活已删除的共享引用。
    存在任一活动 Manifest(PREPARED/SCHEDULED/RUNNING/ROLLBACKING)同样拒绝
    ——同文档 409 compile_in_progress,其他文档 409 knowledge_base_busy——
    封住调度窗口与恢复窗口。runtime 缺失时 fail-closed(生产路径门禁已 503)。
    """
    if runtime is None:
        logger.error("delete %s rejected: runtime absent", doc_id)
        raise HTTPException(status_code=503, detail={"code": "recovery_required"})
    with COMPILE_SCHEDULE_LOCK:
        if not COMPILE_EXECUTION_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail={"code": "knowledge_base_busy"},
            )
        try:
            active = _list_active_manifests_guarded(runtime)
            if active:
                same_doc = any(manifest.doc_id == doc_id for manifest in active)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": (
                            "compile_in_progress" if same_doc else "knowledge_base_busy"
                        )
                    },
                )
            meta = read_doc_meta(doc_id, base_dir=BASE_DIR)
            if meta and meta.get("status") == "compiling":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "compile_in_progress"},
                )
            from scripts.doc_admin import remove_doc
            return remove_doc(doc_id, base_dir=BASE_DIR)
        finally:
            COMPILE_EXECUTION_LOCK.release()


async def delete_doc(request: Request, doc_id: str):
    """删除文档 + 全部产物 + 清理 index/KG/entity_relations 引用。

    此前知识库只能追加无法维护——上传错文档/编译失败时无法清理。
    E004-FIX-02:经 _remove_doc_exclusively 与编译事务互斥;编译进行中
    返回 409(knowledge_base_busy / compile_in_progress),不阻塞等待。
    E005 Task 9:任一活动 Manifest 期间删除一律 409。
    """
    summary = _remove_doc_exclusively(doc_id, _endpoint_runtime(request))
    if not summary.get("removed"):
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found")
    return {"status": "deleted", **summary}


async def recompile_doc_endpoint(
    request: Request, doc_id: str, background_tasks: BackgroundTasks
):
    """重编译既有文档(error 文档重试用)。

    E005 Task 9:_schedule_compile 经持久化事务调度(PREPARED → 绑定 →
    SCHEDULED → add_task);同文档活动事务 → 409 compile_in_progress,其他
    文档活动事务 → 409 knowledge_base_busy,事务准备失败 → 503
    compile_transaction_unavailable,meta 缺失 → 404。响应形状保持不变,
    绝不返回 job_id。
    """
    _schedule_compile(background_tasks, doc_id, _endpoint_runtime(request))
    return {"status": "recompiling", "doc_id": doc_id}

# ─── POST /api/v1/upload (alias for ingest) ──────────────────────────────────

async def upload_document(
    request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    """Frontend-compatible upload endpoint. 返回权威 doc_id + skipped 字段。"""
    return _accept_upload(background_tasks, file, _endpoint_runtime(request))

# ─── POST /api/v1/ingest ─────────────────────────────────────────────────────

async def ingest_document(
    request: Request, background_tasks: BackgroundTasks, file: UploadFile = File(...)
):
    """Alias of /upload — 同一权威契约(权威 doc_id + skipped + 按 doc_id 编译)。"""
    return _accept_upload(background_tasks, file, _endpoint_runtime(request))

# ─── POST /api/v1/search (sync JSON) ─────────────────────────────────────────

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


# ─── E005 Task 8:lifespan、单实例锁与启动恢复门禁 ────────────────────────────

def _build_lifespan(base_dir: Path):
    """构造绑定 base_dir 的 lifespan(设计 §6.1、§17)。

    启动顺序(任何一步失败即拒绝启动,进程不得开始服务):

        加载并校验 CompileRuntimeConfig(纯解析,无 I/O)
        → 获取 ApiInstanceLock(第二实例立即失败)
        → probe_durable_directory(transaction_dir)
        → recover_startup(任何 blocker → 拒绝启动)
        → 挂接 AppRuntime,service_mode=ready,开始服务

    实例锁覆盖 配置/探针/恢复/服务 全周期,仅在 lifespan shutdown 释放。
    recover_startup 自身不获取实例锁(Phase 1 评审结论),锁由本 lifespan 持有。
    """

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        config = load_compile_runtime_config(base_dir)  # ValueError → 启动失败
        lock = ApiInstanceLock(config.instance_lock_path)
        lock.acquire()  # portalocker.AlreadyLocked → 第二实例启动失败
        try:
            probe_durable_directory(config.transaction_dir)
            report = recover_startup(base_dir, config)
            for warning in report.warnings:
                logger.warning("startup recovery warning: %s", warning)
            if not report.ready:
                for blocker in report.blockers:
                    logger.error("startup recovery blocker: %s", blocker)
                raise RuntimeError(
                    "startup recovery blocked; refusing to serve "
                    f"({len(report.blockers)} blockers)"
                )
            app.state.runtime = AppRuntime(
                config=config,
                readiness=ServiceReadiness(),
                instance_lock=lock,
            )
            yield
        finally:
            app.state.runtime = None
            lock.release()

    return _lifespan


def create_app(
    base_dir: Path | str | None = None,
    *,
    allow_unmanaged: bool = False,
) -> FastAPI:
    """应用工厂。

    - base_dir:知识库根目录(事务目录、实例锁、启动恢复的作用域);
      默认模块级 BASE_DIR(生产 uvicorn 路径)。
    - allow_unmanaged: 仅测试使用的显式 opt-in。纯 TestClient(非上下文
      管理器)不执行 lifespan,app.state.runtime 不存在;设 True 时该情形
      按 ready 放行,设 False(默认,生产语义)时 fail-closed 返回 503。
      生产 ASGI 服务器必经 lifespan,此旗标不得用于生产实例。
    """
    resolved_base = Path(base_dir) if base_dir is not None else BASE_DIR

    app = FastAPI(
        title="Karpathy-Style LLM Wiki API",
        version="2.0.0",
        lifespan=_build_lifespan(resolved_base),
    )
    app.state.base_dir = resolved_base
    app.state.allow_unmanaged = allow_unmanaged

    @app.middleware("http")
    async def recovery_gate(request: Request, call_next):
        """全局业务门禁(设计 §19):readiness != ready 时,除 health/ready 外
        一律 503 {"detail": {"code": "recovery_required"}}(单向、fail-closed)。"""
        if request.url.path in GATE_EXEMPT_PATHS:
            return await call_next(request)
        runtime = getattr(request.app.state, "runtime", None)
        if runtime is None:
            if getattr(request.app.state, "allow_unmanaged", False):
                return await call_next(request)
            logger.error(
                "rejecting %s %s: runtime absent (lifespan not run)",
                request.method, request.url.path,
            )
            return JSONResponse(status_code=503, content=RECOVERY_REQUIRED_BODY)
        mode, reason = runtime.readiness.snapshot()
        if mode != "ready":
            # reason 仅服务端日志,绝不进入公共响应体。
            logger.warning(
                "gate rejecting %s %s: service_mode=%s reason=%s",
                request.method, request.url.path, mode, reason,
            )
            return JSONResponse(status_code=503, content=RECOVERY_REQUIRED_BODY)
        return await call_next(request)

    # CORS 在门禁之后注册,成为最外层中间件,门禁 503 响应同样携带 CORS 头。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            # 常规本地 dev(3000)+ UAT 隔离 live 套件(3001);均为本地回环,非生产策略。
            "http://localhost:3000", "http://127.0.0.1:3000",
            "http://localhost:3001", "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 路由注册(处理器为模块级函数,路径常量仍在调用时解析模块属性,
    # 既有测试的 monkeypatch 语义保持不变)。
    app.get("/api/v1/health")(health)
    app.get("/api/v1/ready")(ready)
    app.get("/api/v1/wiki/index")(wiki_index)
    app.get("/api/v1/graph")(graph_data)
    app.get("/api/v1/ontology")(ontology_data)
    app.get("/api/v1/entity-graph")(entity_graph)
    app.get("/api/v1/docs")(list_docs)
    app.get("/api/v1/docs/{doc_id}")(get_doc)
    app.delete("/api/v1/docs/{doc_id}")(delete_doc)
    app.post("/api/v1/docs/{doc_id}/recompile")(recompile_doc_endpoint)
    app.post("/api/v1/upload")(upload_document)
    app.post("/api/v1/ingest")(ingest_document)
    app.post("/api/v1/search")(search_endpoint)
    app.get("/api/v1/search/stream")(search_stream)
    app.post("/api/v1/qa")(qa_stream)
    app.post("/api/v1/lint")(lint_endpoint)
    app.get("/api/v1/consistency")(consistency_get)
    app.post("/api/v1/consistency")(consistency_run)
    return app


# 生产入口(uvicorn api.main:app):严格 fail-closed 语义;
# lifespan 在服务器启动时执行,import 时不触发、不创建 .runtime。
app = create_app()
