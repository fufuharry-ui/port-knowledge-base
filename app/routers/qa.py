"""
app/routers/qa.py - Q&A streaming endpoint
POST /api/v1/qa -> Server-Sent Events
"""

import json
import sys
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.schemas import QARequest

_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import scripts.search as search_mod

router = APIRouter()


async def search_stream_generator(
    query: str,
    client,
    smod,
) -> AsyncIterator[str]:
    import os
    import re
    import yaml

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    if not smod.INDEX_FILE.exists():
        yield _sse({"type": "thought", "step": 1, "message": "Index empty."})
        yield _sse({"type": "done"})
        return

    with open(smod.INDEX_FILE, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f) or {"documents": []}

    candidates = smod.layer1_filter(query, index, top_k=20)

    hit_terms: list[str] = []
    for doc in candidates[:5]:
        hit_terms.extend(doc.get("ontology_terms", [])[:3])
    hit_terms_str = " ".join(f"[{t}]" for t in dict.fromkeys(hit_terms))

    yield _sse({
        "type": "thought",
        "step": 1,
        "message": f"Ontology terms matched: {hit_terms_str or '(keyword match)'}, {len(candidates)} candidates",
    })

    if not candidates:
        yield _sse({"type": "thought", "step": 2, "message": "No candidates found."})
        yield _sse({"type": "done"})
        return

    search_model = os.environ.get("SEARCH_MODEL", "qwen-plus")
    top_docs = smod.layer2_score(query, candidates, client, search_model, top_k=5)
    if not top_docs:
        top_docs = candidates[:3]

    doc_titles = "、".join(f"{d.get('title', d['id'])}" for d in top_docs)
    yield _sse({
        "type": "thought",
        "step": 2,
        "message": f"Layer2 selected {len(top_docs)} docs: {doc_titles}",
    })

    id2entry = {d["id"]: d for d in index.get("documents", [])}
    context_parts: list[str] = []
    sources_info: list[dict] = []
    entity_ids: list[str] = []
    CHAR_BUDGET = 40000

    total_chars = 0
    ref_counter = 1

    for doc in top_docs:
        if total_chars >= CHAR_BUDGET:
            break
        doc_id = doc["id"]
        title = doc.get("title", doc_id)
        full_text = smod.load_full_text(doc_id)
        summary = smod.load_summary_full(doc_id)

        sections_info = ""
        first_section = ""
        if summary.get("sections"):
            first_section = summary["sections"][0].get("title", "")
            sections_info = "Sections: " + " | ".join(
                f"{s['title']}({s.get('page_range','')})"
                for s in summary.get("sections", [])[:8]
            )

        doc_ctx = f"[Doc: {title}]\n{sections_info}\n\n{full_text}"
        available = CHAR_BUDGET - total_chars
        context_parts.append(doc_ctx[:available])
        total_chars += min(len(doc_ctx), available)

        ref = f"[{ref_counter}]"
        sources_info.append({
            "ref": ref,
            "doc_id": doc_id,
            "title": title,
            "section": first_section or sections_info[:50],
        })
        entity_ids.append(doc_id)
        ref_counter += 1

        yield _sse({
            "type": "thought",
            "step": 3,
            "message": f"Loading full text of {title} ({min(len(full_text), 15000)} chars)...",
        })

    context = "\n\n" + "=" * 40 + "\n\n".join(context_parts)

    yield _sse({"type": "source", "citations": sources_info})
    yield _sse({"type": "entity", "ids": entity_ids})

    ref_list = "\n".join(
        f"{s['ref']} = {s['title']} {s['section']}"
        for s in sources_info
    )

    answer_system = f"""You are a professional technical documentation assistant in port digitalization.
Answer based strictly on provided documents. Cite sources using:
{ref_list}
Format: '5G latency <= 20ms[1]'
End with list of sources."""

    user_prompt = f"Query: {query}\n\nReference Docs:{context}"

    try:
        stream = client.chat.completions.create(
            model=search_model,
            messages=[
                {"role": "system", "content": answer_system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            stream=True,
        )
        for chunk in stream:
            delta_text = chunk.choices[0].delta.content or ""
            if delta_text:
                yield _sse({"type": "delta", "text": delta_text})
    except Exception as exc:
        yield _sse({"type": "delta", "text": f"\nGeneration error: {exc}"})

    yield _sse({"type": "done"})


@router.post("/qa")
async def qa_endpoint(
    req: QARequest,
    settings: Settings = Depends(get_settings),
):
    search_mod.BASE_DIR = settings.base_dir
    search_mod.RAW_DIR = settings.raw_dir
    search_mod.WIKI_DIR = settings.wiki_dir
    search_mod.INDEX_FILE = settings.index_file

    client = search_mod.get_llm_client()

    return StreamingResponse(
        search_stream_generator(req.query, client, search_mod),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )