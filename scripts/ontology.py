"""
scripts/ontology.py - Ontology pure logic module (Big-Loop #1)
No file IO (ADR-1): accepts ontology tree dict, returns results.
"""


def find_node(tree, term):
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
    ancestors = []
    current = term
    seen = set()
    while current and current not in seen:
        seen.add(current)
        parent = get_parent_node(tree, current)
        if parent is None:
            break
        ancestors.append(parent["term"])
        current = parent["term"]
    return ancestors


def get_siblings(tree, term):
    parent = get_parent_node(tree, term)
    if parent is None:
        return [n.get("term") for n in tree
                if isinstance(n, dict) and n.get("term") != term]
    return [c.get("term") for c in parent.get("children", [])
            if isinstance(c, dict) and c.get("term") != term]


def _all_terms(tree):
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


def _attach_under_parent(tree, parent_term, child_node):
    parent = find_node(tree, parent_term)
    if parent is None:
        return False
    parent.setdefault("children", [])
    if not any(isinstance(c, dict) and c.get("term") == child_node["term"]
               for c in parent["children"]):
        parent["children"].append(child_node)
    return True


def merge_ontology_nodes(tree, new_nodes):
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

        if parent and find_node(tree, parent) is None:
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
                tree.append({
                    "term": parent,
                    "parent": None,
                    "definition": "",
                    "children": [],
                })
                existing.add(parent)
                added += 1

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


def expand_query_with_ontology(query, tree):
    if not tree or not query:
        return []

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


def rebuild_tree_from_nodes(seed_tree, per_doc_nodes):
    reg = {}

    def _ensure(term, parent=None, definition=""):
        if not term:
            return
        if term in reg:
            if reg[term]["placeholder"]:
                if parent is not None:
                    reg[term]["parent"] = parent
                    reg[term]["placeholder"] = False
                if definition:
                    reg[term]["definition"] = definition
        else:
            reg[term] = {"parent": parent, "definition": definition or "",
                         "placeholder": parent is None and not definition}

    def _walk(nodes):
        for n in nodes or []:
            if not isinstance(n, dict):
                continue
            _ensure(n.get("term"), n.get("parent"), n.get("definition", ""))
            _walk(n.get("children", []))

    _walk(seed_tree)

    for n in per_doc_nodes or []:
        if not isinstance(n, dict):
            continue
        _ensure(n.get("term"), n.get("parent"), n.get("definition", ""))

    for n in per_doc_nodes or []:
        if not isinstance(n, dict):
            continue
        for key in ("parent", "grandparent"):
            _ensure(n.get(key))

    for term, info in list(reg.items()):
        p = info["parent"]
        if p is not None and p not in reg:
            _ensure(p)

    children_of = {}
    for term, info in reg.items():
        children_of.setdefault(info["parent"], []).append(term)

    roots = [t for t, info in reg.items()
             if info["parent"] is None or info["parent"] not in reg]

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


def get_entity_neighbors(term, entity_relations, depth=1):
    if not entity_relations or not term or depth < 1:
        return []

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
    if not entity_relations or not query:
        return []

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
    terms = set()
    for r in entity_relations or []:
        if not isinstance(r, dict):
            continue
        if r.get("source"):
            terms.add(r["source"])
        if r.get("target"):
            terms.add(r["target"])
    return terms


def infer_cross_doc_relations(entity_relations, ontology_tree=None):
    if not entity_relations:
        return []

    term_docs = {}
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
            return
        seen_pairs.add(pair)
        inferred.append({
            "source": a, "target": b, "type": "related_to",
            "provenance": "cross_doc_inferred", "via_hub": hub,
            "confidence": round(conf, 3),
        })

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

    if ontology_tree:
        from collections import defaultdict
        parent_groups = defaultdict(list)
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
    best = 0.0
    for s, t, r in edges_clean:
        if {s, t} == {term_a, term_b}:
            c = r.get("confidence", 0.5)
            if isinstance(c, (int, float)) and c > best:
                best = c
    return best