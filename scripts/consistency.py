"""
consistency.py - Cross-document consistency reasoning (Big-Loop #3)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Iterable

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
META_DIR = BASE_DIR / "meta"
WIKI_DIR = BASE_DIR / "wiki"
RAW_DIR = BASE_DIR / "raw"
INDEX_FILE = WIKI_DIR / "index.yaml"
CONSISTENCY_DIR = META_DIR / "consistency"
CONTRADICTIONS_FILE = CONSISTENCY_DIR / "contradictions.yaml"

TZ_CST = timezone(timedelta(hours=8))

STRONG_RELATION_TYPES = {"same_topic", "supplements"}
MIN_CONFIDENCE = 0.70


def find_contradiction_candidates(
    kg_edges: Iterable[dict] | None,
    entity_relations: Iterable[dict] | None,
) -> list[tuple[str, str]]:
    if not kg_edges and not entity_relations:
        return []

    pairs: set[frozenset[str]] = set()

    for edge in (kg_edges or []):
        etype = edge.get("type")
        if etype not in STRONG_RELATION_TYPES:
            continue
        conf = edge.get("confidence", 1.0)
        if conf is not None and conf < MIN_CONFIDENCE:
            continue
        src = edge.get("source")
        tgt = edge.get("target")
        if src and tgt and src != tgt:
            pairs.add(frozenset({src, tgt}))

    term_pair_docs: dict[frozenset[str], set[str]] = {}
    for er in (entity_relations or []):
        doc_id = er.get("doc_id")
        s = er.get("source")
        t = er.get("target")
        if not doc_id or not s or not t or s == t:
            continue
        key = frozenset({s, t})
        term_pair_docs.setdefault(key, set()).add(doc_id)

    for docs in term_pair_docs.values():
        if len(docs) < 2:
            continue
        doc_list = sorted(docs)
        for i in range(len(doc_list)):
            for j in range(i + 1, len(doc_list)):
                pairs.add(frozenset({doc_list[i], doc_list[j]}))

    result: list[tuple[str, str]] = []
    for p in pairs:
        if len(p) == 2:
            a, b = sorted(p)
            result.append((a, b))
    result.sort()
    return result


CONTRADICTION_SYSTEM = "You are a knowledge base consistency auditor."


def detect_contradiction(
    doc_a_id: str,
    doc_b_id: str,
    client,
    model: str,
    *,
    index: dict | None = None,
    raw_dir: Path | None = None,
) -> dict | None:
    idx = index if index is not None else _load_index()
    rdir = raw_dir if raw_dir is not None else RAW_DIR

    docs_by_id = {d.get("id"): d for d in idx.get("documents", [])}
    da = docs_by_id.get(doc_a_id)
    db = docs_by_id.get(doc_b_id)
    if not da or not db:
        return None

    text_a = _doc_evidence(da, rdir)
    text_b = _doc_evidence(db, rdir)
    if not text_a or not text_b:
        return None

    if client is None:
        return None

    prompt = (
        f"[Doc A] {da.get('title', doc_a_id)} (id={doc_a_id})\n"
        f"{text_a}\n\n"
        f"[Doc B] {db.get('title', doc_b_id)} (id={doc_b_id})\n"
        f"{text_b}\n\n"
        f"Determine if there are factual conflicts."
    )

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": CONTRADICTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
    except Exception:
        return None

    return _parse_contradiction_json(content)


BATCH_CONTRADICTION_SYSTEM = "You are a knowledge base consistency auditor. Process each pair and return results."


def detect_contradictions_batch(
    pairs: list[tuple[str, str]],
    client,
    model: str,
    *,
    index: dict | None = None,
    raw_dir: Path | None = None,
    batch_size: int = 5,
) -> list[dict | None]:
    if not pairs:
        return []
    if client is None:
        return [None] * len(pairs)

    idx = index if index is not None else _load_index()
    rdir = raw_dir if raw_dir is not None else RAW_DIR
    docs_by_id = {d.get("id"): d for d in idx.get("documents", [])}

    results_out: list[dict | None] = [None] * len(pairs)

    for start in range(0, len(pairs), batch_size):
        batch = pairs[start:start + batch_size]
        prompt_parts = []
        for i, (a, b) in enumerate(batch):
            da = docs_by_id.get(a)
            db = docs_by_id.get(b)
            if not da or not db:
                results_out[start + i] = None
                continue
            prompt_parts.append((
                i, a, b,
                f"[Pair {i+1}] doc_a={a} doc_b={b}\n"
                f"Doc A: {_doc_evidence(da, rdir)}\n\n"
                f"Doc B: {_doc_evidence(db, rdir)}",
            ))
        if not prompt_parts:
            continue

        prompt = "Detect factual conflicts for each pair:\n\n" + \
                 "\n\n".join(p[3] for p in prompt_parts)
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": BATCH_CONTRADICTION_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                response_format={"type": "json_object"},
                extra_body={"enable_thinking": False},
            )
            content = resp.choices[0].message.content
        except Exception:
            content = None

        batch_results = _parse_batch_json(content) if content else None
        verdict_map = {}
        if batch_results:
            for r in batch_results:
                if isinstance(r, dict):
                    a, b = r.get("doc_a"), r.get("doc_b")
                    if a and b:
                        verdict_map[(a, b)] = verdict_map[(b, a)] = r
        for i, a, b, _ in prompt_parts:
            r = verdict_map.get((a, b))
            if r:
                results_out[start + i] = {
                    "has_conflict": bool(r.get("has_conflict", False)),
                    "conflict_point": str(r.get("conflict_point", "")).strip(),
                    "reasoning_chain": str(r.get("reasoning_chain", "")).strip(),
                    "confidence": float(r.get("confidence", 0.0) or 0.0),
                }

    return results_out


def _parse_batch_json(content: str) -> list[dict] | None:
    if not content:
        return None
    import re
    import json as _json
    txt = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL)
    if fence:
        txt = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", txt, re.DOTALL)
        if brace:
            txt = brace.group(0)
    try:
        data = _json.loads(txt)
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("results", [])
    if isinstance(data, list):
        return data
    return None



def _doc_evidence(doc: dict, raw_dir: Path) -> str:
    parts = []
    abstract = doc.get("abstract_short") or doc.get("abstract") or ""
    if abstract:
        parts.append(f"[Abstract] {abstract}")
    raw_path = raw_dir / f"{doc.get('id')}.txt"
    try:
        if raw_path.exists():
            raw_text = raw_path.read_text(encoding="utf-8", errors="ignore")[:2000]
            parts.append(f"[Text]\n{raw_text}")
    except Exception:
        pass
    return "\n\n".join(parts)


def _parse_contradiction_json(content: str) -> dict | None:
    if not content:
        return None
    import re
    import json

    txt = content.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, re.DOTALL)
    if fence:
        txt = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", txt, re.DOTALL)
        if brace:
            txt = brace.group(0)
    try:
        data = json.loads(txt)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "has_conflict": bool(data.get("has_conflict", False)),
        "conflict_point": str(data.get("conflict_point", "")).strip(),
        "reasoning_chain": str(data.get("reasoning_chain", "")).strip(),
        "confidence": float(data.get("confidence", 0.0) or 0.0),
    }


def _load_index() -> dict:
    try:
        if INDEX_FILE.exists():
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def run_consistency_check(
    client,
    model: str,
    *,
    kg_file: Path | None = None,
    entity_file: Path | None = None,
    out_file: Path | None = None,
    index: dict | None = None,
    raw_dir: Path | None = None,
    batch_size: int = 5,
) -> dict:
    kg_path = kg_file if kg_file is not None else (
        BASE_DIR / "meta" / "relations" / "knowledge_graph.yaml"
    )
    ent_path = entity_file if entity_file is not None else (
        BASE_DIR / "meta" / "ontology" / "entity_relations.yaml"
    )
    out_path = out_file if out_file is not None else CONTRADICTIONS_FILE

    kg_edges = _safe_load_edges(kg_path)
    ent_edges = _safe_load_edges(ent_path)

    candidates = find_contradiction_candidates(kg_edges, ent_edges)

    contradictions = []
    verdicts = detect_contradictions_batch(
        candidates, client, model,
        index=index, raw_dir=raw_dir, batch_size=batch_size,
    )
    for (doc_a, doc_b), result in zip(candidates, verdicts):
        if result and result.get("has_conflict"):
            contradictions.append({
                "doc_a": doc_a,
                "doc_b": doc_b,
                "conflict_point": result.get("conflict_point", ""),
                "reasoning_chain": result.get("reasoning_chain", ""),
                "confidence": result.get("confidence", 0.0),
                "detected_at": datetime.now(TZ_CST).isoformat(),
            })

    report = {
        "contradictions": contradictions,
        "total": len(contradictions),
        "candidates_checked": len(candidates),
        "last_updated": datetime.now(TZ_CST).isoformat(),
    }

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            yaml.dump(report, f, allow_unicode=True, sort_keys=False)
    except Exception:
        pass
    return report


def _safe_load_edges(path: Path) -> list[dict]:
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("edges", []) or []
    except Exception:
        pass
    return []


def load_contradictions(path: Path | None = None) -> dict:
    p = path if path is not None else CONTRADICTIONS_FILE
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {"contradictions": [], "total": 0}


def contradictions_for_docs(
    doc_ids: list[str],
    contradictions: list[dict] | None = None,
) -> list[dict]:
    if contradictions is None:
        contradictions = load_contradictions().get("contradictions", [])
    target = set(doc_ids)
    hits = []
    for c in contradictions:
        if c.get("doc_a") in target and c.get("doc_b") in target:
            hits.append(c)
    return hits