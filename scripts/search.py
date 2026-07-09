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
# Big-Loop #1: 全局本体树路径(查询扩展读取)。模块常量,便于测试隔离
# (见 tests/conftest.py patch_search_paths)。
GLOBAL_ONTOLOGY_FILE = BASE_DIR / "meta" / "ontology" / "global_ontology.yaml"
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


def llm_call_text_stream(client, model, system, user, retries=3, enable_thinking=True):
    """流式返回文本 token 的 generator(Big-Loop #5)。

    真流式:逐 token yield,首 token 不等全生成。
    enable_thinking: qwen3 思考模型默认开思考(首 token 慢 ~30s);Layer3 传 False
    可关思考(首 token ~0.4s,实测提速 11x,核心答案质量保留——Context Stuffing
    已喂全文,思考非必需)。非 qwen3 模型该参数被忽略(extra_body 透传)。
    """
    last_err = None
    extra_body = {"enable_thinking": enable_thinking}
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                temperature=0.3,
                stream=True,
                extra_body=extra_body,
            )
            for chunk in resp:
                if not getattr(chunk, "choices", None):
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    yield content
            return  # 流式成功完成
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
    raise last_err if last_err else RuntimeError("stream failed")


def llm_call_json(client, model, system, user, retries=3, enable_thinking=True):
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system},
                           {"role": "user", "content": user}],
                temperature=0.1,
                extra_body={"enable_thinking": enable_thinking},
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


def layer1_filter(query: str, index: dict, top_k: int = 20,
                  ontology: dict | None = None) -> list[dict]:
    """Layer 1：从全局索引中通过混合检索粗筛候选文档。
    Only includes documents with either a BM25 hit or a meaningful vector score.

    Big-Loop #1 新增(本体查询扩展):若传入 ontology(含 ontology_tree),
    则把查询命中的本体术语的上位/兄弟词注入 BM25 加权,捞回"字面词 miss、
    本体相关"的文档。ontology=None 时行为与旧版完全一致(向后兼容)。
    """
    docs = index.get("documents", [])
    if not docs:
        return []

    # 本体查询扩展(本体缺失/为空 → 空列表,降级纯 BM25)
    expanded_query = query
    if ontology:
        try:
            from scripts.ontology import (
                expand_query_with_ontology, expand_query_with_entities,
            )
            extra = expand_query_with_ontology(query, ontology.get("ontology_tree", []))
            # Big-Loop #2: 合并实体邻居(术语→术语 语义关系)
            entity_rels = ontology.get("entity_relations", []) if isinstance(ontology, dict) else []
            extra += expand_query_with_entities(query, entity_rels)
            # 去重保序
            seen = set()
            extra_terms = [t for t in extra if not (t in seen or seen.add(t))]
            if extra_terms:
                expanded_query = query + " " + " ".join(extra_terms)
        except Exception:
            # 扩展失败不应阻断检索
            pass

    bm25 = BM25Engine(docs)
    vector = VectorEngine(docs)

    # BM25 用扩展后的查询;向量仍用原始查询(语义不稀释)
    bm25_scores = bm25.search(expanded_query)
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


# ─── Layer 2 结果缓存 (Big-Loop #5, ADR-17) ──────────────────────────────────
# 进程级 LRU,key=(query, 候选 id 元组),TTL 60s。同查询短期复用,避免重复 LLM 评分。
_LAYER2_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_LAYER2_CACHE_TTL = 60.0  # 秒
_LAYER2_CACHE_MAX = 64  # 最多缓存条目(防无界增长)


def _layer2_cache_reset():
    """清空缓存(测试用)。"""
    _LAYER2_CACHE.clear()


def _layer2_cache_get(key):
    """命中返回 (scores),否则 None。过期自动失效。"""
    entry = _LAYER2_CACHE.get(key)
    if not entry:
        return None
    ts, scores = entry
    if time.time() - ts > _LAYER2_CACHE_TTL:
        _LAYER2_CACHE.pop(key, None)
        return None
    return scores


def _layer2_cache_put(key, scores):
    """写入缓存;超限时丢弃最旧条目。"""
    if len(_LAYER2_CACHE) >= _LAYER2_CACHE_MAX:
        # 丢最旧(按时间戳)
        oldest = min(_LAYER2_CACHE, key=lambda k: _LAYER2_CACHE[k][0])
        _LAYER2_CACHE.pop(oldest, None)
    _LAYER2_CACHE[key] = (time.time(), scores)


def layer2_score(query: str, candidates: list[dict], client,
                 model: str, top_k: int = 5) -> list[dict]:
    """Layer 2：LLM 对候选摘要进行相关性评分。

    Big-Loop #5: 结果按 (query, 候选 id 元组) 缓存 60s,同查询复用(ADR-17)。
    """
    if not candidates:
        return []

    # 缓存 key:query + 候选 id 有序集合(top_k 变化不影响评分本身,故不进 key)
    cand_key = tuple(sorted(d["id"] for d in candidates))
    cache_key = (query, cand_key)

    scores = _layer2_cache_get(cache_key)
    if scores is None:
        # 构建候选摘要上下文
        docs_text = "\n---\n".join(
            f"doc_id: {d['id']}\n标题: {d.get('title','')}\n摘要: {d.get('abstract_short','')}"
            for d in candidates
        )
        user_prompt = f"查询: {query}\n\n候选文档:\n{docs_text}"
        # Big-Loop #5: Layer2 默认关思考(实测 4-5x 提速,Top 召回 3/3 一致,ADR-16 满足)。
        # 操作侧可设 SCORE_ENABLE_THINKING=true 开回深度判断模式。
        score_thinking = os.environ.get("SCORE_ENABLE_THINKING", "false").lower() == "true"
        result = llm_call_json(client, model, SCORE_SYSTEM, user_prompt,
                               enable_thinking=score_thinking)
        scores = result.get("scores", [])
        _layer2_cache_put(cache_key, scores)

    # 排序并筛选 Top-K(缓存的是原始 scores,top_k 在此应用)
    scores_sorted = sorted(scores, key=lambda x: x.get("score", 0), reverse=True)
    top_ids = {s["doc_id"] for s in scores_sorted[:top_k] if s.get("score", 0) >= 0.5}

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


def _load_ontology() -> dict:
    """加载全局本体树 + 实体关系供查询扩展用(缺失/为空 → 返回空 dict,降级纯 BM25)。"""
    if not GLOBAL_ONTOLOGY_FILE.exists():
        return {}
    try:
        with open(GLOBAL_ONTOLOGY_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Big-Loop #2: 并入实体级关系(Layer1 扩展用)
        ent_file = GLOBAL_ONTOLOGY_FILE.parent / "entity_relations.yaml"
        if ent_file.exists():
            try:
                with open(ent_file, "r", encoding="utf-8") as f:
                    ent = yaml.safe_load(f) or {}
                edges = list(ent.get("edges", []))
                # Loop #6: 追加跨文档推断边(经共享枢纽 + 本体父类,纯推理,不改原文件)。
                # 让现有 expand_query_with_entities 自动获得更深的邻居扩展。
                # 传 ontology_tree 启用本体父类路径(弥补实体表面术语无跨文档重合)。
                try:
                    from scripts.ontology import infer_cross_doc_relations
                    edges = edges + infer_cross_doc_relations(
                        edges, ontology_tree=data.get("ontology_tree", []),
                    )
                except Exception:
                    pass
                data["entity_relations"] = edges
            except Exception:
                pass
        return data
    except Exception:
        return {}


ANSWER_SYSTEM = """你是一个专业的技术文档助理，服务于港口智慧化与数字化转型领域。

你的回答要求：
1. 严格基于提供的文档内容，不要添加文档中没有的信息
2. 回答要准确、专业，保持原文的技术术语
3. 每个关键信息点后面必须附带引用标注，格式: [文档标题 · 章节名]
4. 在回答末尾统一列出"📎 来源"，包含文档 ID、标题、相关章节

如果文档内容不足以回答问题，明确说明"现有文档中未找到充分信息"。"""


def layer3_answer(query: str, top_docs: list[dict],
                  client, model: str, index: dict,
                  contradictions: list[dict] | None = None,
                  history: list[dict] | None = None) -> str:
    """Layer 3：基于精选文档全文生成最终回答(非流式,返回完整字符串)。

    Big-Loop #5: 委派给 layer3_answer_stream 并收集,保持单一真源。
    Big-Loop #3: contradictions 携带已知矛盾对,Top 文档间有矛盾则附 ⚠️ 提示。
    Big-Loop #8: history 携带多轮对话。
    """
    return "".join(layer3_answer_stream(
        query, top_docs, client, model, index,
        contradictions=contradictions, history=history,
    ))


def _build_layer3_context(query: str, top_docs: list[dict], index: dict,
                          history: list[dict] | None = None):
    """构建 Layer3 的 user_prompt + sources_section。

    返回 (user_prompt, sources_section) 或 None(无候选文档)。
    Big-Loop #5 抽出,供流式/非流式共用,避免逻辑重复。
    Big-Loop #8: history 携带多轮对话(最近若干轮),注入 prompt 让 LLM
    解析追问代词(如"它的延迟要求"中的"它")。None/空 → 不注入(向后兼容)。
    """
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
        return None

    context = "\n\n" + "=" * 40 + "\n\n".join(context_parts)

    # Big-Loop #8: 注入最近若干轮对话历史(截断到最近 10 条,避免吃文档预算)
    history_block = ""
    if history:
        recent = history[-10:]
        lines = []
        for turn in recent:
            role = turn.get("role", "user")
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            label = "用户" if role == "user" else "助手"
            lines.append(f"{label}: {content}")
        if lines:
            history_block = "\n\n【对话历史】(用于理解追问,勿重复其中信息)\n" + "\n".join(lines)

    user_prompt = f"查询问题: {query}{history_block}\n\n参考文档:{context}"
    sources_section = "\n\n---\n📎 **引用来源:**\n" + "\n".join(sources)
    return user_prompt, sources_section


def layer3_answer_stream(query: str, top_docs: list[dict],
                         client, model: str, index: dict,
                         contradictions: list[dict] | None = None,
                         history: list[dict] | None = None):
    """Layer 3 流式版(Big-Loop #5):逐 token yield 答案,末尾 yield 提示+来源。

    真流式:首 token 不等全生成(感知延迟从"总时长"降到"首 token")。
    无候选文档 → yield 提示并返回。
    Big-Loop #8: history 携带多轮对话,透传给 _build_layer3_context。
    """
    built = _build_layer3_context(query, top_docs, index, history=history)
    if built is None:
        yield "⚠️ 未找到相关文档，请调整查询关键词或扩充知识库。"
        return

    user_prompt, sources_section = built

    # Big-Loop #5: Layer3 默认关思考(首 token ~0.4s,11x 提速)。
    # 全文已 Stuffing,思考非必需;操作侧可设 ANSWER_ENABLE_THINKING=true 开回质量模式。
    enable_thinking = os.environ.get("ANSWER_ENABLE_THINKING", "false").lower() == "true"

    # 流式产出答案 token
    for token in llm_call_text_stream(
        client, model, ANSWER_SYSTEM, user_prompt, enable_thinking=enable_thinking,
    ):
        yield token

    # 矛盾提示(附加,在来源前)
    hint = _build_contradiction_hint(top_docs, contradictions)
    if hint:
        yield hint

    # 来源
    yield sources_section


def _build_contradiction_hint(top_docs: list[dict],
                              contradictions: list[dict] | None) -> str:
    """构建 ⚠️ 矛盾提示块。无矛盾/contradictions 为空 → 返回空串(无提示)。

    懒导入 scripts.consistency 避免无矛盾场景的加载开销;
    contradictions 非 None 时直接用(供测试注入,不读文件)。
    """
    if not top_docs:
        return ""
    top_ids = [d.get("id") for d in top_docs if d.get("id")]
    if not top_ids:
        return ""
    try:
        from scripts.consistency import contradictions_for_docs, load_contradictions
        if contradictions is None:
            contradictions = load_contradictions().get("contradictions", [])
    except Exception:
        return ""
    hits = contradictions_for_docs(top_ids, contradictions)
    if not hits:
        return ""
    lines = ["", "---", "⚠️ **知识库内存在不一致** — 以下文档对同一事实有冲突论断,请注意甄别:"]
    for c in hits:
        a = c.get("doc_a", "?")
        b = c.get("doc_b", "?")
        point = c.get("conflict_point", "未指明")
        chain = c.get("reasoning_chain", "")
        conf = c.get("confidence", 0.0)
        lines.append(f"- `{a}` ↔ `{b}` · 冲突点: {point} (置信度 {conf:.2f})")
        if chain:
            lines.append(f"  推理链: {chain}")
    return "\n".join(lines) + "\n"


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

    # Big-Loop #1: 加载全局本体做查询扩展(缺失则降级纯 BM25)
    ontology = _load_ontology()

    # Layer 1
    candidates = layer1_filter(query, index, top_k=20, ontology=ontology)
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
