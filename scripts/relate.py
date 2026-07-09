"""
scripts/relate.py — 文档关系检测脚本
识别新文档与现有文档的语义关系，写入 meta/relations/。

用法:
  python scripts/relate.py <doc_id>
  python scripts/relate.py --rebuild-all
  python scripts/relate.py --recommend <doc_id>
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
# Big-Loop #2: 实体级关系汇总文件
ENTITY_RELATIONS_FILE = ONTOLOGY_DIR / "entity_relations.yaml"

TZ_CST = timezone(timedelta(hours=8))
CONFIDENCE_THRESHOLD = 0.70
RELATION_TYPES = {"cites", "supplements", "contradicts", "same_topic", "version_iteration"}
RELATION_LABELS = {
    "cites": "引用", "supplements": "补充", "contradicts": "⚠️ 矛盾",
    "same_topic": "同主题", "version_iteration": "版本迭代",
}
# Big-Loop #2: 实体级关系类型(术语→术语)
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
        raise RuntimeError("未找到 OPENAI_API_KEY，请在 .env 中配置。")
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


RELATE_SYSTEM = """你是一个知识图谱工程师，识别文档间的语义关系。
关系类型: cites(引用), supplements(补充), contradicts(矛盾), same_topic(同主题), version_iteration(版本迭代)。
仅输出 confidence >= 0.70 的关系，同一对文档最多2种关系。
输出严格 JSON: {"relations": [{"target_doc_id": "...", "type": "...", "confidence": 0.85, "evidence": "..."}]}"""


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
        f"标题: {summary.get('title', doc_id)}\n"
        f"摘要: {summary.get('abstract', '')[:400]}\n"
        f"论点: {'; '.join(summary.get('key_points', [])[:4])}"
    )
    existing_ctx = "\n---\n".join(
        f"doc_id: {d['id']}\n标题: {d.get('title','')}\n摘要: {d.get('abstract_short','')}"
        for d in candidates
    )

    result = llm_call(client, model, RELATE_SYSTEM,
                      f"新文档:\n{new_ctx}\n\n现有文档:\n{existing_ctx}")
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
    print(f"  [OK] 关系写入完成 ({len(relations)} 条)")


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
    print(f"  [OK] 知识图谱更新，共 {len(kg['edges'])} 条边")


# ─── Big-Loop #2: 实体级关系抽取(术语→术语)──────────────────────────────────

ENTITY_SYSTEM = """你是一个领域本体工程师，识别**同一文档内术语之间**的语义关系。
关系类型:
  depends_on(依赖) — A 的实现/运行依赖 B
  part_of(属于/构成) — A 是 B 的组成部分
  supports(支撑) — A 为 B 提供能力支撑(与 depends_on 互逆,选更贴切者)
  alternative_of(替代) — A 与 B 互为替代方案
仅输出 confidence >= 0.70 的关系。每对术语最多 1 条关系。
严格 JSON: {"relations": [{"source": "术语A", "target": "术语B", "type": "depends_on", "confidence": 0.85, "evidence": "证据(30字内)"}]}"""


def extract_entity_relations(doc_id, client, model):
    """抽取某文档术语间的实体级关系,合并写入 entity_relations.yaml。"""
    summary = load_summary(doc_id)
    if not summary:
        return []
    index = load_index()
    # 取该文档的本体术语(从 index 条目)
    terms = []
    for d in index.get("documents", []):
        if d["id"] == doc_id:
            terms = d.get("ontology_terms", [])
            break
    if len(terms) < 2:
        return []  # 不足两个术语,无关系可抽

    user = (
        f"文档: {summary.get('title', doc_id)}\n"
        f"摘要: {summary.get('abstract', '')[:300]}\n"
        f"论点: {'; '.join(summary.get('key_points', [])[:3])}\n"
        f"术语列表: {', '.join(terms)}"
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
        print(f"  [OK] 实体关系抽取 {len(relations)} 条")
    return relations


def _merge_entity_relations(doc_id, relations):
    """合并到全局 entity_relations.yaml(先移除该 doc 的旧边)。"""
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
        print(f"未找到 {doc_id} 的关系文件。")
        return
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    index = load_index()
    id2title = {d["id"]: d.get("title", d["id"]) for d in index.get("documents", [])}
    summary = load_summary(doc_id)
    print(f"\n📄 当前文档: {summary.get('title', doc_id)}\n🔗 关联文档:")
    for r in sorted(data.get("relations", []),
                    key=lambda x: x.get("confidence", 0), reverse=True):
        label = RELATION_LABELS.get(r["type"], r["type"])
        pct = int(r["confidence"] * 100)
        print(f"  ├── [{label} {pct}%] 《{id2title.get(r['target_doc_id'], r['target_doc_id'])}》")
        print(f"  │   └─ {r.get('evidence','')}")


def main():
    if len(sys.argv) < 2:
        print("用法: python scripts/relate.py <doc_id>")
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
        # Big-Loop #2: 实体级关系抽取
        extract_entity_relations(doc_id, client, model)
    print("\n✅ 关系检测完成")


if __name__ == "__main__":
    main()
