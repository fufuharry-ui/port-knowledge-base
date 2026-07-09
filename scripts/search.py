"""
scripts/search.py - Progressive 3-layer retrieval
Implements the Context Stuffing retrieval flow defined in ANTIGRAVITY.md Section 3.3.
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
GLOBAL_ONTOLOGY_FILE = BASE_DIR / "meta" / "ontology" / "global_ontology.yaml"
TZ_CST = timezone(timedelta(hours=8))


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
        raise RuntimeError("OPENAI_API_KEY not found.")
    base_url = os.environ.get("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))


def llm_call_text(client, model, system, user, retries=3):
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
            return
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


def bm25_score(query_tokens: list[str], doc_terms: list[str], doc_abstract: str) -> float:
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
    docs = index.get("documents", [])
    if not docs:
        return []

    expanded_query = query
    if ontology:
        try:
            from scripts.ontology import (
                expand_query_with_ontology, expand_query_with_entities,
            )
            extra = expand_query_with_ontology(query, ontology.get("ontology_tree", []))
            entity_rels = ontology.get("entity_relations", []) if isinstance(ontology, dict) else []
            extra += expand_query_with_entities(query, entity_rels)
            seen = set()
            extra_terms = [t for t in extra if not (t in seen or seen.add(t))]
            if extra_terms:
                expanded_query = query + " " + " ".join(extra_terms)
        except Exception:
            pass

    bm25 = BM25Engine(docs)
    vector = VectorEngine(docs)

    bm25_scores = bm25.search(expanded_query)
    vector_scores = vector.search(query)
    
    candidate_ids = set(bm25_scores.keys()) | set(vector_scores.keys())
    if not candidate_ids:
        return []
    
    if bm25_scores:
        filtered_vector = {k: v for k, v in vector_scores.items() if v > 0.01}
        fused_scores = reciprocal_rank_fusion(bm25_scores, filtered_vector, k=60)
        final_ids = {doc_id for doc_id, _ in fused_scores.items() if doc_id in bm25_scores}
    else:
        fused_scores = {k: v for k, v in vector_scores.items() if v > 0.5}
        final_ids = set(fused_scores.keys())
    
    ranked_docs = sorted(
        [(doc_id, fused_scores.get(doc_id, 0)) for doc_id in final_ids],
        key=lambda x: x[1], reverse=True
    )
    
    id_to_doc = {d["id"]: d for d in docs}
    return [id_to_doc[doc_id] for doc_id, _ in ranked_docs[:top_k] if doc_id in id_to_doc]


SCORE_SYSTEM = "You are a document relevance evaluator. Score each candidate 0.0-1.0. Output JSON: {\"scores\": [{\"doc_id\": \"...\", \"score\": 0.85, \"reason\": \"...\"}]}"

_LAYER2_CACHE: dict[tuple, tuple[float, list[dict]]] = {}
_LAYER2_CACHE_TTL = 60.0
_LAYER2_CACHE_MAX = 64


def _layer2_cache_reset():
    _LAYER2_CACHE.clear()


def _layer2_cache_get(key):
    entry = _LAYER2_CACHE.get(key)
    if not entry:
        return None
    ts, scores = entry
    if time.time() - ts > _LAYER2_CACHE_TTL:
        _LAYER2_CACHE.pop(key, None)
        return None
    return scores


def _layer2_cache_put(key, scores):
    if len(_LAYER2_CACHE) >= _LAYER2_CACHE_MAX:
        oldest = min(_LAYER2_CACHE, key=lambda k: _LAYER2_CACHE[k][0])
        _LAYER2_CACHE.pop(oldest, None)
    _LAYER2_CACHE[key] = (time.time(), scores)


def layer2_score(query: str, candidates: list[dict], client,
                 model: str, top_k: int = 5) -> list[dict]:
    if not candidates:
        return []

    cand_key = tuple(sorted(d["id"] for d in candidates))
    cache_key = (query, cand_key)

    scores = _layer2_cache_get(cache_key)
    if scores is None:
        docs_text = "\n---\n".join(
            f"doc_id: {d['id']}\nTitle: {d.get('title','')}\nAbstract: {d.get('abstract_short','')}"
            for d in candidates
        )
        user_prompt = f"Query: {query}\n\nCandidates:\n{docs_text}"
        score_thinking = os.environ.get("SCORE_ENABLE_THINKING", "false").lower() == "true"
        result = llm_call_json(client, model, SCORE_SYSTEM, user_prompt,
                               enable_thinking=score_thinking)
        scores = result.get("scores", [])
        _layer2_cache_put(cache_key, scores)

    scores_sorted = sorted(scores, key=lambda x: x.get("score", 0), reverse=True)
    top_ids = {s["doc_id"] for s in scores_sorted[:top_k] if s.get("score", 0) >= 0.5}

    return [d for d in candidates if d["id"] in top_ids]


def load_full_text(doc_id: str) -> str:
    txt_path = RAW_DIR / f"{doc_id}.txt"
    if txt_path.exists():
        return txt_path.read_text(encoding="utf-8")[:15000]
    return ""


def load_summary_full(doc_id: str) -> dict:
    p = WIKI_DIR / f"{doc_id}.summary.yaml"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _load_ontology() -> dict:
    if not GLOBAL_ONTOLOGY_FILE.exists():
        return {}
    try:
        with open(GLOBAL_ONTOLOGY_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        ent_file = GLOBAL_ONTOLOGY_FILE.parent / "entity_relations.yaml"
        if ent_file.exists():
            try:
                with open(ent_file, "r", encoding="utf-8") as f:
                    ent = yaml.safe_load(f) or {}
                edges = list(ent.get("edges", []))
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


ANSWER_SYSTEM = "You are a professional technical documentation assistant in port digitalization. Answer based strictly on provided documents. Cite sources as [Document Title - Section]. End with list of sources."


def layer3_answer(query: str, top_docs: list[dict],
                  client, model: str, index: dict,
                  contradictions: list[dict] | None = None,
                  history: list[dict] | None = None) -> str:
    return "".join(layer3_answer_stream(
        query, top_docs, client, model, index,
        contradictions=contradictions, history=history,
    ))


def _build_layer3_context(query: str, top_docs: list[dict], index: dict,
                          history: list[dict] | None = None):
    context_parts = []
    sources = []
    total_chars = 0
    CHAR_BUDGET = 40000

    for doc in top_docs:
        if total_chars >= CHAR_BUDGET:
            break
        doc_id = doc["id"]
        title = doc.get("title", doc_id)
        full_text = load_full_text(doc_id)
        summary = load_summary_full(doc_id)

        sections_info = ""
        if summary.get("sections"):
            sections_info = "Sections: " + " | ".join(
                f"{s['title']}({s.get('page_range','')})"
                for s in summary.get("sections", [])[:8]
            )

        doc_ctx = f"[Doc: {title}]\n{sections_info}\n\n{full_text}"
        available = CHAR_BUDGET - total_chars
        context_parts.append(doc_ctx[:available])
        total_chars += min(len(doc_ctx), available)
        sources.append(f"- [{doc_id}] {title}")

    if not context_parts:
        return None

    context = "\n\n" + "=" * 40 + "\n\n".join(context_parts)

    history_block = ""
    if history:
        recent = history[-10:]
        lines = []
        for turn in recent:
            role = turn.get("role", "user")
            content = (turn.get("content") or "").strip()
            if not content:
                continue
            label = "User" if role == "user" else "Assistant"
            lines.append(f"{label}: {content}")
        if lines:
            history_block = "\n\n[Conversation History]\n" + "\n".join(lines)

    user_prompt = f"Query: {query}{history_block}\n\nReference Docs:{context}"
    sources_section = "\n\n---\nSources:\n" + "\n".join(sources)
    return user_prompt, sources_section


def layer3_answer_stream(query: str, top_docs: list[dict],
                         client, model: str, index: dict,
                         contradictions: list[dict] | None = None,
                         history: list[dict] | None = None):
    built = _build_layer3_context(query, top_docs, index, history=history)
    if built is None:
        yield "No relevant documents found."
        return

    user_prompt, sources_section = built

    enable_thinking = os.environ.get("ANSWER_ENABLE_THINKING", "false").lower() == "true"

    for token in llm_call_text_stream(
        client, model, ANSWER_SYSTEM, user_prompt, enable_thinking=enable_thinking,
    ):
        yield token

    hint = _build_contradiction_hint(top_docs, contradictions)
    if hint:
        yield hint

    yield sources_section


def _build_contradiction_hint(top_docs: list[dict],
                              contradictions: list[dict] | None) -> str:
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
    lines = ["", "---", "WARNING: Knowledge base inconsistencies detected:"]
    for c in hits:
        a = c.get("doc_a", "?")
        b = c.get("doc_b", "?")
        point = c.get("conflict_point", "unspecified")
        chain = c.get("reasoning_chain", "")
        conf = c.get("confidence", 0.0)
        lines.append(f"- `{a}` <-> `{b}` - Conflict: {point} (confidence {conf:.2f})")
        if chain:
            lines.append(f"  Reasoning: {chain}")
    return "\n".join(lines) + "\n"


def search(query: str, client, verbose: bool = True) -> str:
    search_model = os.environ.get("SEARCH_MODEL", "gpt-4o")

    if not INDEX_FILE.exists():
        return "Knowledge base index is empty."
    with open(INDEX_FILE, "r", encoding="utf-8") as f:
        index = yaml.safe_load(f) or {"documents": []}

    if not index.get("documents"):
        return "No documents in knowledge base."

    if verbose:
        print(f"\nQuery: {query}")
        print(f"Total docs: {len(index['documents'])}")

    ontology = _load_ontology()

    candidates = layer1_filter(query, index, top_k=20, ontology=ontology)
    if verbose:
        print(f"[Layer 1] BM25 candidates: {len(candidates)}")

    if not candidates:
        return "No relevant documents found."

    top_docs = layer2_score(query, candidates, client, search_model, top_k=5)
    if verbose:
        print(f"[Layer 2] LLM selected: {len(top_docs)}")
        for d in top_docs:
            print(f"  OK {d['id']}: {d.get('title','')}")

    if not top_docs:
        top_docs = candidates[:3]

    if verbose:
        print("[Layer 3] Generating answer...")
    answer = layer3_answer(query, top_docs, client, search_model, index)

    from scripts.logger import global_logger
    used_docs = ", ".join(d["id"] for d in top_docs)
    global_logger.log(
        action="search",
        target=query,
        details=f"Used docs: {used_docs}"
    )

    return answer


def main():
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
        print("Knowledge Base Search (type 'quit' to exit)")
        print("-" * 50)
        while True:
            try:
                query = input("\nQuery: ").strip()
                if query.lower() in ("quit", "exit", "q"):
                    print("Goodbye!")
                    break
                if not query:
                    continue
                result = search(query, client)
                print(f"\n{'-'*50}\n{result}\n{'-'*50}")
            except KeyboardInterrupt:
                print("\nExited.")
                break


if __name__ == "__main__":
    main()