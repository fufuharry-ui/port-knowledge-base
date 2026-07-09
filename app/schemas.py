"""
app/schemas.py — Pydantic 数据模型定义
供 FastAPI 路由的请求/响应使用。
"""

from typing import List, Optional
from pydantic import BaseModel, Field


# ─── 上传 / Ingest ────────────────────────────────────────────────────────────

class UploadResponse(BaseModel):
    doc_id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    char_count: Optional[int] = None
    skipped: bool = False
    message: Optional[str] = None


# ─── 检索 / Search ────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="自然语言查询（不能为空）")
    stream: bool = False
    top_k: int = Field(default=5, ge=1, le=20)


class Source(BaseModel):
    doc_id: str
    title: Optional[str] = None
    section: Optional[str] = None
    ref: Optional[str] = None   # e.g. "[1]" citation marker


class CitationMeta(BaseModel):
    """引用元数据 — 对应答案文本中的 [N] 标记"""
    ref: str                    # "[1]"
    doc_id: str
    title: Optional[str] = None
    section: Optional[str] = None


class SearchResponse(BaseModel):
    answer: str
    sources: List[Source] = []
    tokens_used: Optional[int] = None


class QARequest(BaseModel):
    """Q&A 流式请求"""
    query: str = Field(..., min_length=1, description="自然语言问题（不能为空）")
    top_k: int = Field(default=5, ge=1, le=20)


# ─── 文档 / Docs ──────────────────────────────────────────────────────────────

class DocMeta(BaseModel):
    id: str
    title: Optional[str] = None
    status: Optional[str] = None
    char_count: Optional[int] = None
    language: Optional[str] = None
    ingested_at: Optional[str] = None
    source_type: Optional[str] = None


class DocListResponse(BaseModel):
    documents: List[DocMeta] = []
    total: int = 0


class WikiIndexResponse(BaseModel):
    total_docs: int
    documents: List[dict] = []


# ─── 图谱 / Graph ─────────────────────────────────────────────────────────────

class GraphEdge(BaseModel):
    source: str
    target: str
    type: str
    confidence: Optional[float] = None
    evidence: Optional[str] = None


class GraphNode(BaseModel):
    id: str
    title: Optional[str] = None
    status: Optional[str] = None


class GraphResponse(BaseModel):
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []


class RelationsResponse(BaseModel):
    doc_id: str
    relations: List[dict] = []


# ─── 本体 / Ontology ──────────────────────────────────────────────────────────

class OntologyResponse(BaseModel):
    ontology_tree: List[dict] = []
    total_nodes: int = 0
    last_updated: Optional[str] = None
