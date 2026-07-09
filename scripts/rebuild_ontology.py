"""
scripts/rebuild_ontology.py — 全局本体树一次性回填(Big-Loop #1)

背景:旧版 compile.py 的扁平追加在 global_ontology.yaml 留下大量顶层孤儿
(带 parent 标签但未挂进树)。本脚本用 ontology.rebuild_tree_from_nodes
基于现有 parent 标签**拓扑重建**真树,消除孤儿。

用法:
  python scripts/rebuild_ontology.py --dry-run    # 预览,不落盘
  python scripts/rebuild_ontology.py              # 重建并写回
"""
import sys
from pathlib import Path

# 确保项目根在 sys.path(直接 python scripts/xxx.py 运行时需要)
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
    """用注册表拓扑法重建真树(无需 per-doc 文件——现有 parent 标签即足)。"""
    from scripts.ontology import rebuild_tree_from_nodes
    tree, total = rebuild_tree_from_nodes(seed_tree, per_doc_nodes=[])
    return tree, total


def count_top_roots(tree):
    return len(tree)


def main():
    dry_run = "--dry-run" in sys.argv

    seed_tree, data = load_global_tree()
    if not seed_tree:
        print("未找到 global_ontology.yaml 或本体树为空。")
        return

    roots_before = count_top_roots(seed_tree)
    new_tree, total = rebuild(seed_tree)
    roots_after = count_top_roots(new_tree)

    print(f"[回填] 顶层根: {roots_before} → {roots_after}")
    print(f"[回填] 总节点数: {total}")
    # 列出重建后的顶层根
    print(f"[回填] 新顶层根: {[n['term'] for n in new_tree]}")

    if dry_run:
        print("[回填] --dry-run 模式:不写回。")
        return

    data["ontology_tree"] = new_tree
    data["total_nodes"] = total
    from datetime import datetime, timezone, timedelta
    data["last_updated"] = datetime.now(timezone(timedelta(hours=8))).isoformat()
    with open(GLOBAL_ONTOLOGY_FILE, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    print(f"[回填] 已写回 {GLOBAL_ONTOLOGY_FILE.name}")


if __name__ == "__main__":
    main()
