"""
app/routers/search.py — 检索路由
POST /api/v1/search          - 同步 JSON 检索
GET  /api/v1/search/stream   - SSE 流式检索
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

# 确保 scripts/ 可导入
_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from scripts.search import search  # noqa: E402

router = APIRouter()


def _parse_sources(answer: str, index: dict) -> list[Source]:
    """
    从 answer 文本中解析 '📎 引用来源' 区块，
    提取 doc_id 并与 index 中的 title 对应。
    """
    id2title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
    sources = []
    seen = set()
    # 匹配形如 [doc_20260405_001] 的 doc_id
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
    """
    同步检索接口：
    1. jieba 分词预处理 query
    2. 调用三层渐进式检索引擎
    3. 返回 answer + sources
    """
    # jieba 预处理（增强中文召回）
    tokens = jieba_tokenize(req.query)
    # 将分词结果空格拼接后传入检索引擎，完全不修改 search.py
    enhanced_query = " ".join(tokens) if tokens else req.query

    # 重定向 search.py 内部的路径常量
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
        raise HTTPException(status_code=500, detail=f"检索引擎错误: {exc}") from exc

    index = _load_index(settings)
    sources = _parse_sources(answer, index)

    return SearchResponse(answer=answer, sources=sources)


def _sse_generator(answer: str) -> Generator[str, None, None]:
    """将 answer 按字逐步输出为 SSE 事件流"""
    chunk_size = 4  # 每次推送 4 个字符，模拟打字机效果
    for i in range(0, len(answer), chunk_size):
        chunk = answer[i: i + chunk_size]
        data = json.dumps({"delta": chunk}, ensure_ascii=False)
        yield f"data: {data}\n\n"
    # 结束信号
    yield "data: [DONE]\n\n"


@router.get("/search/stream")
async def search_stream(
    q: str = Query(..., min_length=1, description="检索查询（不能为空）"),
    settings: Settings = Depends(get_settings),
):
    """
    SSE 流式检索（text/event-stream）：
    - 后端获取完整回答后，分块推送字符（打字机效果）
    - 生产环境可替换为真正的 LLM streaming
    """
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
        # 即使出错也通过 SSE 回传错误信息
        answer = f"⚠️ 检索错误: {exc}"

    return StreamingResponse(
        _sse_generator(answer),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
