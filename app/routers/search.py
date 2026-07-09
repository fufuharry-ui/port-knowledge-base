"""
app/routers/search.py - Search routes
POST /api/v1/search, GET /api/v1/search/stream
"""

import json
import re
import sys
from pathlib import Path
from typing import Generator

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.schemas import SearchRequest, SearchResponse, Source
from app.utils.tokenizer import jieba_tokenize

_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scripts.search import search

router = APIRouter()


def _parse_sources(answer: str, index: dict) -> list[Source]:
    id2title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
    sources = []
    seen = set()
    for m in re.finditer(r"\[?(doc_\w+)\]?", answer):
        doc_id = m.group(1)
        if doc_id not in seen and doc_id in id2title:
            sources.append(Source(doc_id=doc_id, title=id2title[doc_id]))
            seen.add(doc_id)
    return sources


def _load_index(settings: Settings) -> dict:
    if not settings.index_file.exists():
        return {"documents": []}
    with open(settings.index_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"documents": []}


@router.post("/search", response_model=SearchResponse)
async def search_sync(
    req: SearchRequest,
    settings: Settings = Depends(get_settings),
):
    tokens = jieba_tokenize(req.query)
    enhanced_query = " ".join(tokens) if tokens else req.query

    import scripts.search as search_mod
    search_mod.BASE_DIR = settings.base_dir
    search_mod.RAW_DIR = settings.raw_dir
    search_mod.WIKI_DIR = settings.wiki_dir
    search_mod.INDEX_FILE = settings.index_file

    try:
        client = search_mod.get_llm_client()
        model = settings.search_model
        answer = search_mod.search(enhanced_query, client, verbose=False)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Search engine error: {exc}") from exc

    index = _load_index(settings)
    sources = _parse_sources(answer, index)

    return SearchResponse(answer=answer, sources=sources)


def _sse_generator(answer: str) -> Generator[str, None, None]:
    chunk_size = 4
    for i in range(0, len(answer), chunk_size):
        chunk = answer[i: i + chunk_size]
        data = json.dumps({"delta": chunk}, ensure_ascii=False)
        yield f"data: {data}\n\n"
    yield "data: [DONE]\n\n"


@router.get("/search/stream")
async def search_stream(
    q: str = Query(..., min_length=1, description="Search query"),
    settings: Settings = Depends(get_settings),
):
    tokens = jieba_tokenize(q)
    enhanced_query = " ".join(tokens) if tokens else q

    import scripts.search as search_mod
    search_mod.BASE_DIR = settings.base_dir
    search_mod.RAW_DIR = settings.raw_dir
    search_mod.WIKI_DIR = settings.wiki_dir
    search_mod.INDEX_FILE = settings.index_file

    try:
        client = search_mod.get_llm_client()
        answer = search_mod.search(enhanced_query, client, verbose=False)
    except Exception as exc:
        answer = f"Search error: {exc}"

    return StreamingResponse(
        _sse_generator(answer),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )