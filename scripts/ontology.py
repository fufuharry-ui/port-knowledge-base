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
