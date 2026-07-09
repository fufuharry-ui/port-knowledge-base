"""
consistency.py — 跨文档一致性推理(Big-Loop #3)

职责:从知识库内检出对同一事实的冲突论断,并给出推理链。
与 relate.py 的区别:relate 是"关联"(谁和谁有关),
consistency 是"稽核"(谁和谁矛盾)。混入会膨胀 relate 的测试面,
故独立模块(ADR-8)。

分层:
  - 纯逻辑:find_contradiction_candidates(kg_edges, entity_relations)
            无文件 IO、无 LLM,可隔离单测(ADR-9)。
  - LLM 判定:detect_contradiction(pair, ...) 读两文档摘要/原文,
              返回是否矛盾 + 推理链,写入 contradictions.yaml。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Iterable

import yaml

# ── 路径常量(测试通过 monkeypatch 隔离) ───────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
META_DIR = BASE_DIR / "meta"
WIKI_DIR = BASE_DIR / "wiki"
RAW_DIR = BASE_DIR / "raw"
INDEX_FILE = WIKI_DIR / "index.yaml"
CONSISTENCY_DIR = META_DIR / "consistency"
CONTRADICTIONS_FILE = CONSISTENCY_DIR / "contradictions.yaml"

TZ_CST = timezone(timedelta(hours=8))

# 候选对生成阈值
STRONG_RELATION_TYPES = {"same_topic", "supplements"}  # 文档级强关联 → 矛盾候选
MIN_CONFIDENCE = 0.70  # 低于此置信度的 same_topic/supplements 视为弱关联,不纳入


def find_contradiction_candidates(
    kg_edges: Iterable[dict] | None,
    entity_relations: Iterable[dict] | None,
) -> list[tuple[str, str]]:
    """从文档级关系 + 共享实体术语,推导矛盾检测候选对(纯函数,ADR-9)。

    两个来源:
      1. KG 中 same_topic/supplements 边(confidence >= MIN_CONFIDENCE):
         两文档被判定为同主题/互补 → 可能对同一事实有不同论断。
      2. 共享实体术语:两文档都声明了相同的实体关系
         (entity_relations 中 source+target 术语相同,方向无关):
         说明二者都论及同一实体对 → 候选。

    参数:
      kg_edges: knowledge_graph.yaml 的 edges 列表
                (含 source/target/doc_id? /type/confidence)。
      entity_relations: entity_relations.yaml 的 edges 列表
                        (含 source/target【术语】/doc_id/type/confidence)。

    返回: 去重后的 (doc_a, doc_b) 元组列表,每对内部按字典序排序。
    """
    if not kg_edges and not entity_relations:
        return []

    pairs: set[frozenset[str]] = set()

    # ── 来源 1:文档级强关联边 ──
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

    # ── 来源 2:共享实体术语对 ──
    # 按 (术语对,归一化) 分组 → 收集声明该术语对的文档集合
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

    # frozenset → 排序后的元组(确定性输出,便于断言)
    result: list[tuple[str, str]] = []
    for p in pairs:
        if len(p) == 2:
            a, b = sorted(p)
            result.append((a, b))
    result.sort()
    return result


# ── LLM 判定的常量和工具函数 ─────────────────────────────────────

CONTRADICTION_SYSTEM = """你是知识库一致性稽核员。给定两份文档的摘要与原文片段,
判断二者是否对【同一事实/指标/论断】给出冲突结论。

冲突的判定标准(严格):
- 两文档论及同一事实(如"远控端到端延迟要求""岸桥单机台时效率")。
- 对该事实给出不一致的数值/结论/规范(版本迭代导致的差异也算,需标注)。
- 单纯的详略互补、视角不同(一个讲网络一个讲机械)不算冲突。

输出严格 JSON:
{
  "has_conflict": true|false,
  "conflict_point": "冲突的具体事实点(无冲突则空串)",
  "reasoning_chain": "推理链:文档A说X,文档B说Y,冲突点=...(无冲突则空串)",
  "confidence": 0.0-1.0
}
只输出 JSON,不要其他文字。"""


def detect_contradiction(doc_a_id, doc_b_id, client, model, *, index=None, raw_dir=None):
    """LLM 判定两文档是否矛盾(单对)。"""
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
        f"【文档A】{da.get('title', doc_a_id)} (id={doc_a_id})\n{text_a}\n\n"
        f"【文档B】{db.get('title', doc_b_id)} (id={doc_b_id})\n{text_b}\n\n"
        f"请按系统指令判定两文档是否存在事实性冲突。"
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


# ─── 批量化稽核(Loop #9) ─────────────────────────────────────────

BATCH_CONTRADICTION_SYSTEM = """你是知识库一致性稽核员。给定若干文档对的摘要与原文片段,
逐对判断每对是否对【同一事实/指标/论断】给出冲突结论。

冲突判定标准(严格,同单对版):
- 两文档论及同一事实,且对该事实给出不一致的数值/结论/规范。
- 单纯详略互补、视角不同,不算冲突。

输出严格 JSON,results 数组,顺序与输入对一致:
{
  "results": [
    {"doc_a": "...", "doc_b": "...", "has_conflict": true|false,
     "conflict_point": "冲突点(无则空串)", "reasoning_chain": "推理链(无则空串)",
     "confidence": 0.0-1.0}
  ]
}
只输出 JSON。"""


def detect_contradictions_batch(pairs, client, model, *, index=None, raw_dir=None, batch_size=5):
    """批量矛盾判定:多对一次 LLM 调用,把 N 次降到 ceil(N/batch_size) 次。

    解决规模化瓶颈:100 文档 ~222 对,单对串行 ~14 分钟;batch=10 → ~22 调用 ~2 分钟。
    返回:与 pairs 等长的列表,每项为判定 dict 或 None(证据缺失/批次失败时降级)。
    """
    if not pairs:
        return []
    if client is None:
        return [None] * len(pairs)
    idx = index if index is not None else _load_index()
    rdir = raw_dir if raw_dir is not None else RAW_DIR
    docs_by_id = {d.get("id"): d for d in idx.get("documents", [])}
    results_out = [None] * len(pairs)
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
                f"[对{i+1}] doc_a={a} doc_b={b}\n"
                f"文档A《{da.get('title', a)}》:\n{_doc_evidence(da, rdir)}\n\n"
                f"文档B《{db.get('title', b)}》:\n{_doc_evidence(db, rdir)}",
            ))
        if not prompt_parts:
            continue
        prompt = "请逐对判定以下文档对是否存在事实性冲突:\n\n" + \
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


def _parse_batch_json(content):
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


def _doc_evidence(doc, raw_dir):
    parts = []
    abstract = doc.get("abstract_short") or doc.get("abstract") or ""
    if abstract:
        parts.append(f"[摘要] {abstract}")
    raw_path = raw_dir / f"{doc.get('id')}.txt"
    try:
        if raw_path.exists():
            raw_text = raw_path.read_text(encoding="utf-8", errors="ignore")[:2000]
            parts.append(f"[原文片段]\n{raw_text}")
    except Exception:
        pass
    return "\n\n".join(parts)


def _parse_contradiction_json(content):
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


def _load_index():
    try:
        if INDEX_FILE.exists():
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {}


def run_consistency_check(client, model, *, kg_file=None, entity_file=None,
                          out_file=None, index=None, raw_dir=None, batch_size=5):
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


def _safe_load_edges(path):
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data.get("edges", []) or []
    except Exception:
        pass
    return []


def load_contradictions(path=None):
    p = path if path is not None else CONTRADICTIONS_FILE
    try:
        if p.exists():
            with open(p, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    except Exception:
        pass
    return {"contradictions": [], "total": 0}


def contradictions_for_docs(doc_ids, contradictions=None):
    if contradictions is None:
        contradictions = load_contradictions().get("contradictions", [])
    target = set(doc_ids)
    hits = []
    for c in contradictions:
        if c.get("doc_a") in target and c.get("doc_b") in target:
            hits.append(c)
    return hits
