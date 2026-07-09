"""
scripts/relate.py - Document relation detection
"""

import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml

BASE_DIR = Path(__file__).parent.parent
WIKI_DIR = BASE_DIR / "wiki"
RELATIONS_DIR = BASE_DIR / "meta" / "relations"
ONTOLOGY_DIR = BASE_DIR / "meta" / "ontology"
INDEX_FILE = WIKI_DIR / "index.yaml"
KG_FILE = RELATIONS_DIR / "knowledge_graph.yaml"
ENTITY_RELATIONS_FILE = ONTOLOGY_DIR / "entity_relations.yaml"

TZ_CST = timezone(timedelta(hours=8))
CONFIDENCE_THRESHOLD = 0.70
RELATION_TYPES = {"cites", "supplements", "contradicts", "same_topic", "version_iteration"}
RELATION_LABELS = {
    "cites": "cites", "supplements": "supplements", "contradicts": "!contradicts",
    "same_topic": "same topic", "version_iteration": "version iteration",
}
ENTITY_RELATION_TYPES = {"depends_on", "part_of", "supports", "alternative_of"}


def _load_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_llm_client():
    from openai import OpenAI
    _load_env()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not found.")
    base_url = os.environ.get("OPENAI_BASE_URL")
    return OpenAI(api_key=api_key, **({"base_url": base_url} if base_url else {}))


def llm_call(client, model, system, user, retries=3):
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


def load_index():
    if INDEX_FILE.exists():
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"documents": []}
    return {"documents": []}


def load_summary(doc_id):
    p = WIKI_DIR / f"{doc_id}.summary.yaml"
    if p.exists():
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


RELATE_SYSTEM = "You are a knowledge graph engineer detecting semantic relations between documents."


def detect_relations(doc_id, client, model):
    index = load_index()
    summary = load_summary(doc_id)
    if not summary:
        return []

    others = [d for d in index.get("documents", []) if d["id"] != doc_id]
    if not others:
        return []

    max_compare = int(os.environ.get("RELATE_MAX_COMPARE", "50"))
    new_terms = set(summary.get("writing_style", {}).get("key_terminology", {}).keys())

    def overlap(d):
        return len(new_terms & set(d.get("ontology_terms", [])))

    candidates = sorted(others, key=overlap, reverse=True)[:max_compare]

    new_ctx = (
        f"Title: {summary.get('title', doc_id)}\n"
        f"Abstract: {summary.get('abstract', '')[:400]}\n"
        f"Key points: {'; '.join(summary.get('key_points', [])[:4])}"
    )
    existing_ctx = "\n---\n".join(
        f"doc_id: {d['id']}\nTitle: {d.get('title','')}\nAbstract: {d.get('abstract_short','')}"
        for d in candidates
    )

    result = llm_call(client, model, RELATE_SYSTEM,
                      f"New doc:\n{new_ctx}\n\nExisting docs:\n{existing_ctx}")
    valid_ids = {d["id"] for d in candidates}
    seen = set()
    relations = []
    for r in result.get("relations", []):
        t = r.get("target_doc_id", "")
        if (t in valid_ids and r.get("type") in RELATION_TYPES
                and float(r.get("confidence", 0)) >= CONFIDENCE_THRESHOLD
                and t not in seen):
            relations.append(r)
            seen.add(t)
    return relations


def write_relations(doc_id, relations):
    RELATIONS_DIR.mkdir(parents=True, exist_ok=True)
    out = {"doc_id": doc_id, "detected_at": datetime.now(TZ_CST).isoformat(),
           "relations": relations}
    with open(RELATIONS_DIR / f"{doc_id}.relations.yaml", "w", encoding="utf-8") as f:
        yaml.dump(out, f, allow_unicode=True, sort_keys=False)
    print(f"  [OK] Relations written ({len(relations)} total)")


def update_kg(doc_id, relations):
    kg = {"edges": []}
    if KG_FILE.exists():
        with open(KG_FILE, "r", encoding="utf-8") as f:
            kg = yaml.safe_load(f) or {"edges": []}
    kg["edges"] = [e for e in kg["edges"] if e.get("source") != doc_id]
    now = datetime.now(TZ_CST).isoformat()
    for r in relations:
        kg["edges"].append({
            "source": doc_id, "target": r["target_doc_id"],
            "type": r["type"], "confidence": r["confidence"], "created_at": now,
        })
    with open(KG_FILE, "w", encoding="utf-8") as f:
        yaml.dump(kg, f, allow_unicode=True, sort_keys=False)
    print(f"  [OK] KG updated, {len(kg['edges'])} edges total")


ENTITY_SYSTEM = "You are a domain ontology engineer detecting entity-level semantic relations."


def extract_entity_relations(doc_id, client, model):
    summary = load_summary(doc_id)
    if not summary:
        return []
    index = load_index()
    terms = []
    for d in index.get("documents", []):
        if d["id"] == doc_id:
            terms = d.get("ontology_terms", [])
            break
    if len(terms) < 2:
        return []

    user = (
        f"Doc: {summary.get('title', doc_id)}\n"
        f"Abstract: {summary.get('abstract', '')[:300]}\n"
        f"Key points: {'; '.join(summary.get('key_points', [])[:3])}\n"
        f"Terms: {', '.join(terms)}"
    )
    result = llm_call(client, model, ENTITY_SYSTEM, user)

    valid_terms = set(terms)
    seen = set()
    relations = []
    for r in result.get("relations", []):
        s, t = r.get("source"), r.get("target")
        key = tuple(sorted([s, t]))
        if (s in valid_terms and t in valid_terms
                and r.get("type") in ENTITY_RELATION_TYPES
                and float(r.get("confidence", 0)) >= 0.70
                and s != t and key not in seen):
            relations.append({
                "source": s, "target": t, "type": r["type"],
                "confidence": r["confidence"], "evidence": r.get("evidence", ""),
                "doc_id": doc_id,
            })
            seen.add(key)

    _merge_entity_relations(doc_id, relations)
    if relations:
        print(f"  [OK] Entity relations extracted: {len(relations)}")
    return relations


def _merge_entity_relations(doc_id, relations):
    ONTOLOGY_DIR.mkdir(parents=True, exist_ok=True)
    data = {"edges": []}
    if ENTITY_RELATIONS_FILE.exists():
        with open(ENTITY_RELATIONS_FILE, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {"edges": []}
    data["edges"] = [e for e in data.get("edges", []) if e.get("doc_id") != doc_id]
    data["edges"].extend(relations)
    data["last_updated"] = datetime.now(TZ_CST).isoformat()
    with open(ENTITY_RELATIONS_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def recommend(doc_id):
    p = RELATIONS_DIR / f"{doc_id}.relations.yaml"
    if not p.exists():
        print(f"No relations found for {doc_id}.")
        return
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    index = load_index()
    id2title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
    summary = load_summary(doc_id)
    print(f"\nDoc: {summary.get('title', doc_id)}\nRelated:")
    for r in sorted(data.get("relations", []),
                    key=lambda x: x.get("confidence", 0), reverse=True):
        label = RELATION_LABELS.get(r["type"], r["type"])
        pct = int(r["confidence"] * 100)
        print(f"  + [{label} {pct}%] {id2title.get(r['target_doc_id'], r['target_doc_id'])}")
        print(f"    - {r.get('evidence','')}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/relate.py <doc_id>")
        return

    _load_env()

    if sys.argv[1] == "--recommend":
        recommend(sys.argv[2] if len(sys.argv) > 2 else "")
        return

    if sys.argv[1] == "--rebuild-all":
        index = load_index()
        targets = [d["id"] for d in index.get("documents", [])]
    else:
        targets = sys.argv[1:]

    model = os.environ.get("RELATE_MODEL", "gpt-4o")
    client = get_llm_client()

    for doc_id in targets:
        print(f"\n[RELATE] {doc_id}")
        rels = detect_relations(doc_id, client, model)
        write_relations(doc_id, rels)
        update_kg(doc_id, rels)
        extract_entity_relations(doc_id, client, model)
    print("\nDone - relation detection complete")


if __name__ == "__main__":
    main()