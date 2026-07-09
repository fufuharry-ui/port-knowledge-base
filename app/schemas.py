"""
app/schemas.py - Pydantic data models
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    doc_id: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    char_count: Optional[int] = None
    skipped: bool = False
    message: Optional[str] = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language query")
    stream: bool = False
    top_k: int = Field(default=5, ge=1, le=20)


class Source(BaseModel):
    doc_id: str
    title: Optional[str] = None
    section: Optional[str] = None
    ref: Optional[str] = None


class CitationMeta(BaseModel):
    ref: str
    doc_id: str
    title: Optional[str] = None
    section: Optional[str] = None


class SearchResponse(BaseModel):
    answer: str
    sources: List[Source] = []
    tokens_used: Optional[int] = None


class QARequest(BaseModel):
    query: str = Field(..., min_length=1, description="Natural language question")
    top_k: int = Field(default=5, ge=1, le=20)


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


class OntologyResponse(BaseModel):
    ontology_tree: List[dict] = []
    total_nodes: int = 0
    last_updated: Optional[str] = None