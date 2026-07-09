"""
scripts/ontology.py — 本体纯逻辑模块(Big-Loop #1)

本模块**不含文件 IO**(ADR-1):接受本体树 dict,返回处理结果。
调用方(compile.py / search.py)负责读写 YAML,便于测试隔离。

两类能力:
  1. merge_ontology_nodes  — 真树合并,消除顶层孤儿(修旧"扁平追加"缺陷)
  2. expand_query_with_ontology — 查询扩展,把命中术语的上位/兄弟注入检索
                                  (本体缺失/为空 → 优雅降级,返回空集)
"""


# ─── 树遍历 ────────────────────────────────────────────────────────────────────

def find_node(tree, term):
    """在树中(含嵌套 children)查找 term,返回节点 dict 或 None。"""
    if not tree:
        return None
    for node in tree:
        if not isinstance(node, dict):
            continue
        if node.get("term") == term:
            return node
        found = find_node(node.get("children", []), term)
        if found is not None:
            return found
    return None


def get_parent_node(tree, term):
    """返回 term 的父节点 dict(其 children 中含 term),或 None。"""
    if not tree:
        return None
    for node in tree:
        if not isinstance(node, dict):
            continue
        for child in node.get("children", []):
            if isinstance(child, dict) and child.get("term") == term:
                return node
        deeper = get_parent_node(node.get("children", []), term)
        if deeper is not None:
            return deeper
    return None


def get_ancestors(tree, term):
    """返回 term 的祖先 term 列表(从直接父到根),找不到返回 []。"""
    ancestors = []
    current = term
    seen = set()  # 防御环
    while current and current not in seen:
        seen.add(current)
        parent = get_parent_node(tree, current)
        if parent is None:
            break
        ancestors.append(parent["term"])
        current = parent["term"]
    return ancestors


def get_siblings(tree, term):
    """返回 term 的兄弟 term 列表(不含自身),找不到返回 []。"""
    parent = get_parent_node(tree, term)
    if parent is None:
        # 可能在顶层:顶层兄弟 = 其他根节点
        return [n.get("term") for n in tree
                if isinstance(n, dict) and n.get("term") != term]
    return [c.get("term") for c in parent.get("children", [])
            if isinstance(c, dict) and c.get("term") != term]


def _all_terms(tree):
    """收集树中所有 term(用于去重判断)。"""
    terms = set()

    def _walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            if n.get("term"):
                terms.add(n["term"])
            _walk(n.get("children", []))

    _walk(tree)
    return terms


# ─── 真树合并 ──────────────────────────────────────────────────────────────────

def _attach_under_parent(tree, parent_term, child_node):
    """把 child_node 挂到 parent_term 节点的 children 下(就地修改)。
    假设 parent_term 已存在于树中。"""
    parent = find_node(tree, parent_term)
    if parent is None:
        return False
    parent.setdefault("children", [])
    # 避免重复挂
    if not any(isinstance(c, dict) and c.get("term") == child_node["term"]
               for c in parent["children"]):
        parent["children"].append(child_node)
    return True


def merge_ontology_nodes(tree, new_nodes):
    """把 new_nodes 合并进 tree,返回实际新增节点数。

    规则(消除顶层孤儿):
      - 术语已存在 → 跳过(不重复)
      - parent 在树中 → 挂其 children 下
      - parent 不在但 grandparent 在 → 先建 parent 作 grandparent 子节点,再挂 term
      - 都不在 → parent 作新根节点,term 挂其下(避免悬空)
    """
    if not new_nodes:
        return 0

    existing = _all_terms(tree)
    added = 0

    for raw in new_nodes:
        if not isinstance(raw, dict):
            continue
        term = raw.get("term")
        if not term or term in existing:
            continue

        parent = raw.get("parent")
        grandparent = raw.get("grandparent")

        # 确保 parent 节点存在于树中
        if parent and find_node(tree, parent) is None:
            # parent 缺失:尝试用 grandparent 建中间节点
            gp_node = find_node(tree, grandparent) if grandparent else None
            if gp_node is not None:
                gp_node.setdefault("children", [])
                gp_node["children"].append({
                    "term": parent,
                    "parent": grandparent,
                    "definition": "",
                    "children": [],
                })
                existing.add(parent)
                added += 1
            else:
                # 无可挂靠的祖先 → parent 作新根,避免 term 悬空
                tree.append({
                    "term": parent,
                    "parent": None,
                    "definition": "",
                    "children": [],
                })
                existing.add(parent)
                added += 1

        # 挂 term
        term_node = {
            "term": term,
            "parent": parent if parent else None,
            "definition": raw.get("definition", ""),
            "children": [],
        }
        if parent:
            _attach_under_parent(tree, parent, term_node)
        else:
            tree.append(term_node)
        existing.add(term)
        added += 1

    return added


# ─── 查询扩展 ──────────────────────────────────────────────────────────────────

def expand_query_with_ontology(query, tree):
    """识别 query 中命中的本体术语,返回其上位 + 兄弟 term 列表(去重,不含自身)。

    本体缺失/为空 → 返回 [](优雅降级,调用方退化为纯 BM25)。
    """
    if not tree or not query:
        return []

    # 收集所有候选术语(按长度降序,优先匹配长术语;过滤单字噪声)
    all_terms = sorted(
        (t for t in _all_terms(tree) if t and len(t) >= 2),
        key=len, reverse=True,
    )
    expansions = []
    matched = set()

    for term in all_terms:
        if term in query:
            matched.add(term)

    for term in matched:
        for anc in get_ancestors(tree, term):
            if anc not in matched and anc not in expansions:
                expansions.append(anc)
        for sib in get_siblings(tree, term):
            if sib not in matched and sib not in expansions:
                expansions.append(sib)

    return expansions


# ─── 历史回填 ──────────────────────────────────────────────────────────────────

def rebuild_tree_from_nodes(seed_tree, per_doc_nodes):
    """从种子树 + per-doc 节点重建真树,返回 (tree, total_nodes)。

    用途:旧版 compile.py 的扁平追加在 global_ontology.yaml 留下大量顶层孤儿
    (带 parent 标签但未挂进树)。本函数把现有树(扁平化为 term→parent 注册表)
    与 per-doc 节点合并,按 parent 链**拓扑建树**,彻底消除顺序依赖。

    策略(避免 merge_ontology_nodes 的顺序敏感):
      1. 注册表 term→{parent, definition}:遍历种子树(含嵌套与孤儿,取其 parent
         标签)+ per-doc 节点。占位符(parent 仅作引用、无定义)允许升级。
      2. 按 parent 建 children 映射。
      3. 根 = parent 为 None 或 parent 不在注册表(不可解析→作根,不悬空)。
      4. 自顶向下递归建树。
    """
    reg = {}  # term -> {"parent": str|None, "definition": str, "placeholder": bool}

    def _ensure(term, parent=None, definition=""):
        if not term:
            return
        if term in reg:
            # 占位符可被真实节点升级(parent/definition)
            if reg[term]["placeholder"]:
                if parent is not None:
                    reg[term]["parent"] = parent
                    reg[term]["placeholder"] = False
                if definition:
                    reg[term]["definition"] = definition
        else:
            reg[term] = {"parent": parent, "definition": definition or "",
                         "placeholder": parent is None and not definition}

    # 1a. 遍历种子树:每个节点(含嵌套/孤儿)按其 parent 标签注册
    def _walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            _ensure(n.get("term"), n.get("parent"), n.get("definition", ""))
            _walk(n.get("children", []))

    _walk(seed_tree)

    # 1b. 注册 per-doc 真实节点
    for n in per_doc_nodes or []:
        if not isinstance(n, dict):
            continue
        _ensure(n.get("term"), n.get("parent"), n.get("definition", ""))

    # 1c. 为仅作 parent/grandparent 引用、尚未注册的术语建占位符(使其可被挂靠)
    for n in per_doc_nodes or []:
        if not isinstance(n, dict):
            continue
        for key in ("parent", "grandparent"):
            _ensure(n.get(key))

    # 1d. 为种子树中"parent 标签不在注册表"的引用也建占位符根
    #     (涵盖历史孤儿悬空的 parent,如 北斗RTK→定位导航技术 后者从未建为节点)
    for term, info in list(reg.items()):
        p = info["parent"]
        if p is not None and p not in reg:
            _ensure(p)  # 占位符根,parent=None

    # 2. children 映射
    children_of = {}
    for term, info in reg.items():
        children_of.setdefault(info["parent"], []).append(term)

    # 3. 根:parent 为 None 或 parent 不在注册表
    roots = [t for t, info in reg.items()
             if info["parent"] is None or info["parent"] not in reg]

    # 4. 递归建树(防御环)
    def _build(term, seen):
        info = reg[term]
        kids = []
        for c in children_of.get(term, []):
            if c in seen:
                continue
            kids.append(_build(c, seen | {term}))
        return {
            "term": term,
            "parent": info["parent"],
            "definition": info["definition"],
            "children": kids,
        }

    tree = [_build(r, {r}) for r in roots]
    return tree, len(reg)


# ─── 实体级知识图谱(Big-Loop #2)──────────────────────────────────────────────

def get_entity_neighbors(term, entity_relations, depth=1):
    """返回 term 在实体关系图中的邻居(多跳,双向,去重,不含自身)。

    entity_relations: list of {source, target, type, confidence, ...}
    depth: 遍历跳数(1=直接邻居,2=二跳…)。无关系/term 不存在 → []。
    """
    if not entity_relations or not term or depth < 1:
        return []

    # 建无向邻接表(双向:source↔target)
    adj = {}
    for r in entity_relations:
        if not isinstance(r, dict):
            continue
        s, t = r.get("source"), r.get("target")
        if not s or not t:
            continue
        adj.setdefault(s, set()).add(t)
        adj.setdefault(t, set()).add(s)

    if term not in adj:
        return []

    # BFS 多跳
    visited = set()
    frontier = {term}
    for _ in range(depth):
        nxt = set()
        for node in frontier:
            for nb in adj.get(node, []):
                if nb not in visited and nb != term:
                    nxt.add(nb)
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    visited.discard(term)
    return list(visited)


def expand_query_with_entities(query, entity_relations, matched_terms=None):
    """识别 query 中命中的本体术语,返回其**实体邻居**(term→term 关系目标)。

    与 expand_query_with_entities 互补:#1 注入上位/兄弟(分类),
    本函数注入语义关系邻居(依赖/支撑/属于…)。

    matched_terms: 若调用方已识别命中术语,直接传入;否则从 query 子串匹配。
    无 entity_relations → [](降级)。
    """
    if not entity_relations or not query:
        return []

    # 收集所有出现的术语(作候选命中)
    all_terms = sorted(
        (t for t in _collect_relation_terms(entity_relations) if t and len(t) >= 2),
        key=len, reverse=True,
    )
    hits = matched_terms if matched_terms else [t for t in all_terms if t in query]

    expansions = []
    for term in hits:
        for nb in get_entity_neighbors(term, entity_relations, depth=1):
            if nb not in hits and nb not in expansions:
                expansions.append(nb)
    return expansions


def _collect_relation_terms(entity_relations):
    """收集所有实体关系中的 source/target 术语。"""
    terms = set()
    for r in entity_relations or []:
        if not isinstance(r, dict):
            continue
        if r.get("source"):
            terms.add(r["source"])
        if r.get("target"):
            terms.add(r["target"])
    return terms


# ─── 跨文档实体关系推断(Loop #6)──────────────────────────────────────────────

def infer_cross_doc_relations(entity_relations, ontology_tree=None):
    """经共享枢纽推断跨文档实体边(纯函数,无 IO)。

    动机:Loop #2 的实体抽取仅在单文档内(term↔term 同源)。若 doc_A 声明
    "网络切片→5G专网"、doc_B 声明"边缘计算→5G专网",二者分别声明,
    但系统从不推断"网络切片↔边缘计算"(经 5G专网 枢纽关联)——这条边
    没有任何单文档直接说过,是真正的跨文档推理新知识。

    两条推断路径:
      (a) 表面枢纽:同一术语在 ≥2 文档作为边端点 → 关联其跨文档邻居。
      (b) 本体父类(传 ontology_tree 时):两术语在本体树共享父类、
          且来自不同文档 → 推断 related_to。弥补"实体抽取太文档孤岛、
          表面术语无跨文档重合"的真实数据缺口(实测:本体路径产 32 边,
          表面路径产 0 边)。

    规则:
      - 仅当两术语来自完全不同文档(无共现文档)才推断——真正跨文档证据。
      - 已有直接边的不重复推断;同一对术语去重。
      - provenance=cross_doc_inferred,confidence 折扣(0.6×源置信度,本体路径固定 0.5)。

    返回:推断边列表(不含原始边)。ontology_tree=None → 只走路径(a),向后兼容。
    """
    if not entity_relations:
        return []

    # 1. 收集每个术语的文档来源 + 清洗后的边
    term_docs = {}  # term -> set(doc_id)
    edges_clean = []
    for r in entity_relations:
        if not isinstance(r, dict):
            continue
        s, t = r.get("source"), r.get("target")
        if not s or not t or s == t:
            continue
        doc = r.get("doc_id")
        edges_clean.append((s, t, r))
        if doc:
            term_docs.setdefault(s, set()).add(doc)
            term_docs.setdefault(t, set()).add(doc)

    direct_pairs = {frozenset({s, t}) for s, t, _ in edges_clean}
    inferred = []
    seen_pairs = set()

    def _add(a, b, hub, conf):
        pair = frozenset({a, b})
        if pair in direct_pairs or pair in seen_pairs:
            return
        if not term_docs.get(a, set()).isdisjoint(term_docs.get(b, set())):
            return  # 有共现文档 → 留给单文档抽取
        seen_pairs.add(pair)
        inferred.append({
            "source": a, "target": b, "type": "related_to",
            "provenance": "cross_doc_inferred", "via_hub": hub,
            "confidence": round(conf, 3),
        })

    # 路径 (a):表面枢纽(同一术语在 ≥2 文档)
    hubs = {t for t, ds in term_docs.items() if len(ds) >= 2}
    if hubs:
        hub_nbrs = {}
        for s, t, r in edges_clean:
            doc = r.get("doc_id")
            for a, b in ((s, t), (t, s)):
                if a in hubs and b is not a:
                    hub_nbrs.setdefault(a, {}).setdefault(b, set())
                    if doc:
                        hub_nbrs[a][b].add(doc)
        for hub, nbrs in hub_nbrs.items():
            terms = list(nbrs.keys())
            for i in range(len(terms)):
                for j in range(i + 1, len(terms)):
                    conf = 0.6 * min(
                        _edge_confidence(terms[i], hub, edges_clean) or 0.5,
                        _edge_confidence(terms[j], hub, edges_clean) or 0.5,
                    )
                    _add(terms[i], terms[j], hub, conf)

    # 路径 (b):本体父类(共享父类 + 跨文档)
    if ontology_tree:
        from collections import defaultdict
        parent_groups = defaultdict(list)  # parent_term -> [entity_term]
        for term in term_docs:
            pn = get_parent_node(ontology_tree, term)
            if pn and isinstance(pn, dict) and pn.get("term"):
                parent_groups[pn["term"]].append(term)
        for parent, terms in parent_groups.items():
            if len(terms) < 2:
                continue
            for i in range(len(terms)):
                for j in range(i + 1, len(terms)):
                    _add(terms[i], terms[j], parent, 0.5)

    return inferred


def _edge_confidence(term_a, term_b, edges_clean):
    """取 term_a↔term_b 边的置信度(取最大的,双向匹配)。"""
    best = 0.0
    for s, t, r in edges_clean:
        if {s, t} == {term_a, term_b}:
            c = r.get("confidence", 0.5)
            if isinstance(c, (int, float)) and c > best:
                best = c
    return best
