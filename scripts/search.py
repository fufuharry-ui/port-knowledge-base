"""
scripts/search.py — 渐进式三层检索脚本
实现 ANTIGRAVITY.md 第 3.3 节定义的 Context Stuffing 检索流程：
  Layer 1: 本体关键词 BM25 粗筛  → Top-20
  Layer 2: LLM 摘要相关性评分  → Top-5
  Layer 3: LLM 精确回答 + 引用  → 最终输出

用法:
  python scripts/search.py "港口岸桥远控的网络延迟要求是多少？"
  python scripts/search.py  （进入交互模式）
"""

import json
import os
import re
import sys
import time
from datetime import timezone, timedelta
from pathlib import Path

import yaml

BASE_DIR = Path(__file__).parent.parent
RAW_DIR = BASE_DIR / "raw"
WIKI_DIR = BASE_DIR / "wiki"
INDEX_FILE = WIKI_DIR / "index.yaml"
TZ_CST = timezone(timedelta(hours=8))


# ─── LLM 客户端 ──────────────────────────────────────────────────────────────

def get_llm_client():
    from openai import OpenAI
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("未找到 OPENAI_API_KEY，请在 .env 中配置。")
    base_url = os.environ.get("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))


def llm_call_text(client, model, system, user, retries=3):
    """返回文本（非 JSON）的 LLM 调用"""
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


def llm_call_json(client, model, system, user, retries=3):
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                temperature=0.1,
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))


# ─── Layer 1：BM25 关键词粗筛 ────────────────────────────────────────────────

class BM25Engine:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        
    def search(self, query: str) -> dict[str, float]:
        import jieba
        query_tokens = list(jieba.cut_for_search(query))
        query_tokens = [t for t in query_tokens if len(t.strip()) > 1]
        if not query_tokens:
            query_tokens = [query]
            
        scores = {}
        for doc in self.docs:
            text = doc.get("text", doc.get("abstract_short", ""))
            terms = doc.get("ontology_terms", [])
            score = 0.0
            text_lower = text.lower()
            for token in query_tokens:
                token_lower = token.lower()
                if any(token_lower in t.lower() for t in terms):
                    score += 2.0
                count = text_lower.count(token_lower)
                score += min(count, 5) * 0.5
            if score > 0:
                scores[doc["id"]] = score
        return scores


# ─── Backward-compat standalone function (used by legacy tests) ───────────────
def bm25_score(query_tokens: list[str], doc_terms: list[str], doc_abstract: str) -> float:
    """Legacy BM25 scorer — kept for backward compatibility with existing tests."""
    score = 0.0
    text_lower = doc_abstract.lower()
    for token in query_tokens:
        token_lower = token.lower()
        if any(token_lower in t.lower() for t in doc_terms):
            score += 2.0
        count = text_lower.count(token_lower)
        score += min(count, 5) * 0.5
    return score


class VectorEngine:
    def __init__(self, docs: list[dict]):
        self.docs = docs
        from scripts.embedding_client import EmbeddingClient
        self.client = EmbeddingClient()
        
    def search(self, query: str) -> dict[str, float]:
        import math
        def cosine_similarity(v1, v2):
            dot = sum(a * b for a, b in zip(v1, v2))
            mag1 = math.sqrt(sum(a * a for a in v1))
            mag2 = math.sqrt(sum(b * b for b in v2))
            if mag1 == 0 or mag2 == 0: return 0.0
            return dot / (mag1 * mag2)

        query_vec = self.client.get_embedding(query)
        scores = {}
        for doc in self.docs:
            doc_vec = doc.get("embedding")
            if not doc_vec:
                text = doc.get("text", doc.get("abstract_short", ""))
                if text:
                    doc_vec = self.client.get_embedding(text)
            if doc_vec:
                scores[doc["id"]] = cosine_similarity(query_vec, doc_vec)
        return scores


def reciprocal_rank_fusion(scores1: dict[str, float], scores2: dict[str, float], k: int = 60) -> dict[str, float]:
    fused = {}
    
    def get_ranks(scores: dict[str, float]):
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return {doc_id: rank + 1 for rank, (doc_id, _) in enumerate(ranked)}
        
    rank1 = get_ranks(scores1)
    rank2 = get_ranks(scores2)
    
    all_docs = set(scores1.keys()) | set(scores2.keys())
    for doc_id in all_docs:
        r1 = rank1.get(doc_id, 1000)
        r2 = rank2.get(doc_id, 1000)
        score = 0.0
        if r1 != 1000:
            score += 1.0 / (k + r1)
        if r2 != 1000:
            score += 1.0 / (k + r2)
        fused[doc_id] = score
        
    return fused


def layer1_filter(query: str, index: dict, top_k: int = 20) -> list[dict]:
    """Layer 1：从全局索引中通过混合检索粗筛候选文档。
    Only includes documents with either a BM25 hit or a meaningful vector score.
    """
    docs = index.get("documents", [])
    if not docs:
        return []

    bm25 = BM25Engine(docs)
    vector = VectorEngine(docs)
    
    bm25_scores = bm25.search(query)
    vector_scores = vector.search(query)
    
    # Only include docs that have at least a BM25 hit, or are in vector results
    # (prevent all-zero embedding vectors from inflating unrelated docs)
    candidate_ids = set(bm25_scores.keys()) | set(vector_scores.keys())
    if not candidate_ids:
        return []
    
    # Filter vector_scores to only docs that also had bm25 hits OR bm25 was non-empty
    # If bm25 returned nothing, rely only on vector. If bm25 returned some, filter by that.
    if bm25_scores:
        # Filter vector contributions to only docs that had nonzero cosine
        filtered_vector = {k: v for k, v in vector_scores.items() if v > 0.01}
        fused_scores = reciprocal_rank_fusion(bm25_scores, filtered_vector, k=60)
        # Only return docs that had BM25 hits (vector adds ranking boost, not new candidates)
        final_ids = {doc_id for doc_id, _ in fused_scores.items() if doc_id in bm25_scores}
    else:
        # No BM25 hits at all — fall back to vector only with threshold
        fused_scores = {k: v for k, v in vector_scores.items() if v > 0.5}
        final_ids = set(fused_scores.keys())
    
    ranked_docs = sorted(
        [(doc_id, fused_scores.get(doc_id, 0)) for doc_id in final_ids],
        key=lambda x: x[1], reverse=True
    )
    
    id_to_doc = {d["id"]: d for d in docs}
    return [id_to_doc[doc_id] for doc_id, _ in ranked_docs[:top_k] if doc_id in id_to_doc]


# ─── Layer 2：LLM 摘要相关性评分 ─────────────────────────────────────────────

SCORE_SYSTEM = """你是一个文档相关性评估专家。
对每个候选文档，基于其摘要评估与查询的相关性（0.0-1.0）。
严格 JSON 输出: {"scores": [{"doc_id": "...", "score": 0.85, "reason": "简要说明"}]}"""


def layer2_score(query: str, candidates: list[dict], client,
                 model: str, top_k: int = 5) -> list[dict]:
    """Layer 2：LLM 对候选摘要进行相关性评分"""
    if not candidates:
        return []

    # 构建候选摘要上下文
    docs_text = "\n---\n".join(
        f"doc_id: {d['id']}\n标题: {d.get('title','')}\n摘要: {d.get('abstract_short','')}"
        for d in candidates
    )
    user_prompt = f"查询: {query}\n\n候选文档:\n{docs_text}"

    result = llm_call_json(client, model, SCORE_SYSTEM, user_prompt)
    scores = result.get("scores", [])

    # 排序并筛选 Top-K
    scores.sort(key=lambda x: x.get("score", 0), reverse=True)
    top_ids = {s["doc_id"] for s in scores[:top_k] if s.get("score", 0) >= 0.5}

    return [d for d in candidates if d["id"] in top_ids]


# ─── Layer 3：精确回答 + 引用 ─────────────────────────────────────────────────

def load_full_text(doc_id: str) -> str:
    txt_path = RAW_DIR / f"{doc_id}.txt"
    if txt_path.exists():
        # 限制单文档最多 15000 字符（约 10K token）
        return txt_path.read_text(encoding="utf-8")[:15000]
    return ""


def load_summary_full(doc_id: str) -> dict:
    p = WIKI_DIR / f"{doc_id}.summary.yaml"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


ANSWER_SYSTEM = """你是一个专业的技术文档助理，服务于港口智慧化与数字化转型领域。

你的回答要求：
1. 严格基于提供的文档内容，不要添加文档中没有的信息
2. 回答要准确、专业，保持原文的技术术语
3. 每个关键信息点后面必须附带引用标注，格式: [文档标题 · 章节名]
4. 在回答末尾统一列出"📎 来源"，包含文档 ID、标题、相关章节

如果文档内容不足以回答问题，明确说明"现有文档中未找到充分信息"。"""


def layer3_answer(query: str, top_docs: list[dict],
                  client, model: str, index: dict) -> str:
    """Layer 3：基于精选文档全文生成最终回答"""
    id2entry = {d["id"]: d for d in index.get("documents", [])}

    # 构建上下文：全文 + 摘要结构
    context_parts = []
    sources = []
    total_chars = 0
    CHAR_BUDGET = 40000  # 约 25K token

    for doc in top_docs:
        if total_chars >= CHAR_BUDGET:
            break
        doc_id = doc["id"]
        title = doc.get("title", doc_id)
        full_text = load_full_text(doc_id)
        summary = load_summary_full(doc_id)

        # 章节信息辅助定位
        sections_info = ""
        if summary.get("sections"):
            sections_info = "章节目录: " + " | ".join(
                f"{s['title']}({s.get('page_range','')})"
                for s in summary.get("sections", [])[:8]
            )

        doc_ctx = f"【文档: {title}】\n{sections_info}\n\n{full_text}"
        available = CHAR_BUDGET - total_chars
        context_parts.append(doc_ctx[:available])
        total_chars += min(len(doc_ctx), available)
        sources.append(f"- [{doc_id}] 《{title}》")

    if not context_parts:
        return "⚠️ 未找到相关文档，请调整查询关键词或扩充知识库。"

    context = "\n\n" + "="*40 + "\n\n".join(context_parts)
    user_prompt = f"查询问题: {query}\n\n参考文档:{context}"

    answer = llm_call_text(client, model, ANSWER_SYSTEM, user_prompt)

    # 追加来源列表
    sources_section = "\n\n---\n📎 **引用来源:**\n" + "\n".join(sources)
    return answer + sources_section


# ─── 主检索流程 ──────────────────────────────────────────────────────────────

def search(query: str, client, verbose: bool = True) -> str:
    search_model = os.environ.get("SEARCH_MODEL", "gpt-4o")

    # 加载全局索引
    if not INDEX_FILE.exists():
        return "⚠️ 知识库索引为空，请先运行 ingest.py 和 compile.py。"
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f) or {"documents": []}

    if not index.get("documents"):
        return "⚠️ 知识库中暂无文档。"

    if verbose:
        print(f"\n🔍 查询: {query}")
        print(f"📚 知识库文档总数: {len(index['documents'])}")

    # Layer 1
    candidates = layer1_filter(query, index, top_k=20)
    if verbose:
        print(f"[Layer 1] BM25 筛选: {len(candidates)} 篇候选文档")

    if not candidates:
        return "⚠️ 没有找到与查询相关的文档。请尝试更换关键词。"

    # Layer 2
    top_docs = layer2_score(query, candidates, client, search_model, top_k=5)
    if verbose:
        print(f"[Layer 2] LLM 精选: {len(top_docs)} 篇高相关文档")
        for d in top_docs:
            print(f"  ✓ {d['id']}: {d.get('title','')}")

    if not top_docs:
        top_docs = candidates[:3]  # 兜底：直接取前3

    # Layer 3
    if verbose:
        print("[Layer 3] 生成精确回答...")
    answer = layer3_answer(query, top_docs, client, search_model, index)

    from scripts.logger import global_logger
    used_docs = ", ".join(d["id"] for d in top_docs)
    global_logger.log(
        action="search",
        target=query,
        details=f"Used docs: {used_docs}"
    )

    return answer


# ─── 入口 ────────────────────────────────────────────────────────────────────

def main():
    # 加载 .env
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    client = get_llm_client()

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        result = search(query, client)
        print(f"\n{'='*60}\n{result}\n{'='*60}")
    else:
        # 交互模式
        print("🧠 知识库检索系统（输入 'quit' 退出）")
        print("─" * 50)
        while True:
            try:
                query = input("\n❓ 请输入问题: ").strip()
                if query.lower() in ("quit", "exit", "q", "退出"):
                    print("再见！")
                    break
                if not query:
                    continue
                result = search(query, client)
                print(f"\n{'─'*50}\n{result}\n{'─'*50}")
            except KeyboardInterrupt:
                print("\n已退出。")
                break


if __name__ == "__main__":
    main()
