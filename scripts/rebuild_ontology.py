"""
scripts/rebuild_ontology.py - Global ontology tree rebuild (Big-Loop #1)
"""
import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import yaml

BASE_DIR = Path(__file__).parent.parent
ONTOLOGY_DIR = BASE_DIR / "meta" / "ontology"
GLOBAL_ONTOLOGY_FILE = ONTOLOGY_DIR / "global_ontology.yaml"


def load_global_tree():
    if not GLOBAL_ONTOLOGY_FILE.exists():
        return [], {}
    with open(GLOBAL_ONTOLOGY_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("ontology_tree", []), data


def rebuild(seed_tree):
    from scripts.ontology import rebuild_tree_from_nodes
    tree, total = rebuild_tree_from_nodes(seed_tree, per_doc_nodes=[])
    return tree, total


def count_top_roots(tree):
    return len(tree)


def main():
    dry_run = "--dry-run" in sys.argv

    seed_tree, data = load_global_tree()
    if not seed_tree:
        print("global_ontology.yaml not found or tree is empty.")
        return

    roots_before = count_top_roots(seed_tree)
    new_tree, total = rebuild(seed_tree)
    roots_after = count_top_roots(new_tree)

    print(f"[Rebuild] Roots: {roots_before} -> {roots_after}")
    print(f"[Rebuild] Total nodes: {total}")
    print(f"[Rebuild] New roots: {[n['term'] for n in new_tree]}")

    if dry_run:
        print("[Rebuild] --dry-run: not writing back.")
        return

    data["ontology_tree"] = new_tree
    data["total_nodes"] = total
    from datetime import datetime, timezone, timedelta
    data["last_updated"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with open(GLOBAL_ONTOLOGY_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"[Rebuild] Written to {GLOBAL_ONTOLOGY_FILE.name}")


if __name__ == "__main__":
    main()