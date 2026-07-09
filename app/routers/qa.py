"""
app/routers/qa.py — Q&A 流式端点
POST /api/v1/qa → Server-Sent Events 混合流

事件序列 (按顺序):
  thought  — 三层检索的执行轨迹 (step 1/2/3)
  source   — 引用元数据列表 (CitationMeta[])
  entity   — 命中文档 ID 列表 (用于图谱高亮)
  delta    — LLM 流式文本 token
  done     — 流结束标志
"""

import json
import sys
from pathlib import Path
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.config import Settings, get_settings
from app.schemas import QARequest

# 确保 scripts/ 可导入
_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import scripts.search as search_mod  # noqa: E402

router = APIRouter()


async def search_stream_generator(
    query: str,
    client,
    smod,
) -> AsyncIterator[str]:
    """
    核心异步生成器：
    1. Layer1 BM25 粗筛  → yield thought(step=1)
    2. Layer2 LLM 精选  → yield thought(step=2)
    3. Layer3 全文加载  → yield thought(step=3)
    4. 解析引用         → yield source
    5. 提取实体 IDs     → yield entity
    6. OpenAI streaming → yield delta per token
    7. 完成             → yield done
    """
    import os
    import re
    import yaml

    def _sse(obj: dict) -> str:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    # ── 加载全局索引 ──────────────────────────────────────────────────────────
    if not smod.INDEX_FILE.exists():
        yield _sse({"type": "thought", "step": 1, "message": "⚠️ 知识库索引为空，请先执行 ingest & compile。"})
        yield _sse({"type": "done"})
        return

    with open(smod.INDEX_FILE, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f) or {"documents": []}

    # ── Layer 1 ───────────────────────────────────────────────────────────────
    candidates = smod.layer1_filter(query, index, top_k=20)

    hit_terms: list[str] = []
    for doc in candidates[:5]:
        hit_terms.extend(doc.get("ontology_terms", [])[:3])
    hit_terms_str = " ".join(f"[{t}]" for t in dict.fromkeys(hit_terms))

    yield _sse({
        "type": "thought",
        "step": 1,
        "message": f"命中本体关键词：{hit_terms_str or '(关键词匹配)'}，候选文档 {len(candidates)} 篇",
    })

    if not candidates:
        yield _sse({"type": "thought", "step": 2, "message": "⚠️ 无候选文档，请调整关键词或扩充知识库。"})
        yield _sse({"type": "done"})
        return

    # ── Layer 2 ───────────────────────────────────────────────────────────────
    search_model = os.environ.get("SEARCH_MODEL", "qwen-plus")
    top_docs = smod.layer2_score(query, candidates, client, search_model, top_k=5)
    if not top_docs:
        top_docs = candidates[:3]

    doc_titles = "、".join(f"《{d.get('title', d['id'])}》" for d in top_docs)
    yield _sse({
        "type": "thought",
        "step": 2,
        "message": f"Layer2 精选 {len(top_docs)} 篇高相关文档：{doc_titles}",
    })

    # ── Layer 3 准备 ──────────────────────────────────────────────────────────
    id2entry = {d["id"]: d for d in index.get("documents", [])}
    context_parts: list[str] = []
    sources_info: list[dict] = []           # 用于 source 事件
    entity_ids: list[str] = []             # 用于 entity 事件
    CHAR_BUDGET = 40000

    total_chars = 0
    ref_counter = 1                         # [1], [2], ...

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
            sections_info = "章节目录: " + " | ".join(
                f"{s['title']}({s.get('page_range','')})"
                for s in summary.get("sections", [])[:8]
            )

        doc_ctx = f"【文档: {title}】\n{sections_info}\n\n{full_text}"
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
            "message": f"正在加载《{title}》原始全文 ({min(len(full_text), 15000)} 字符)...",
        })

    context = "\n\n" + "=" * 40 + "\n\n".join(context_parts)

    # ── 推送 source & entity 事件 ────────────────────────────────────────────
    yield _sse({"type": "source", "citations": sources_info})
    yield _sse({"type": "entity", "ids": entity_ids})

    # ── Layer 3 — 构建 prompt ─────────────────────────────────────────────────
    ref_list = "\n".join(
        f"{s['ref']} = 《{s['title']}》{s['section']}"
        for s in sources_info
    )

    answer_system = f"""你是一个专业的技术文档助理，服务于港口智慧化与数字化转型领域。

你的回答要求：
1. 严格基于提供的文档内容，不要添加文档中没有的信息
2. 回答要准确、专业，保持原文的技术术语
3. 每个关键信息点后面必须附带引用标注，使用以下引用编号：
{ref_list}
   格式示例：「5G空口延迟≤20ms[1]」
4. 在回答末尾统一列出 "📎 来源"，包含引用编号和对应文档名

如果文档内容不足以回答问题，明确说明"现有文档中未找到充分信息"。"""

    user_prompt = f"查询问题: {query}\n\n参考文档:{context}"

    # ── OpenAI Streaming ──────────────────────────────────────────────────────
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
        yield _sse({"type": "delta", "text": f"\n⚠️ 生成出错: {exc}"})

    yield _sse({"type": "done"})


@router.post("/qa")
async def qa_endpoint(
    req: QARequest,
    settings: Settings = Depends(get_settings),
):
    """
    Q&A 流式端点 — 混合 SSE 响应

    推送顺序: thought(1) → thought(2) → thought(3) → source → entity
             → delta(×N LLM tokens) → done
    """
    # 重定向 search 模块路径常量（与现有 search router 保持一致）
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
