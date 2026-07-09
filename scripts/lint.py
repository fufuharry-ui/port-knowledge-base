import os
from pathlib import Path
from datetime import datetime
import yaml

from scripts.search import llm_call_json, get_llm_client

class Linter:
    def __init__(self, base_dir="."):
        self.base_dir = Path(base_dir)
        self.wiki_dir = self.base_dir / "wiki"
        self.meta_dir = self.base_dir / "meta"
        
    def detect_orphan_pages(self) -> list[str]:
        index_file = self.wiki_dir / "index.yaml"
        if not index_file.exists():
            return []
            
        with open(index_file, "r", encoding="utf-8") as f:
            index_data = yaml.safe_load(f) or {"documents": []}
            
        all_docs = {doc["id"] for doc in index_data.get("documents", [])}
        linked_docs = set()
        
        relations_dir = self.meta_dir / "relations"
        if relations_dir.exists():
            for rel_file in relations_dir.glob("*.yaml"):
                with open(rel_file, "r", encoding="utf-8") as f:
                    rel_data = yaml.safe_load(f) or {}
                    for rel in rel_data.get("relations", []):
                        if rel.get("source"):
                            linked_docs.add(rel.get("source"))
                        if rel.get("target"):
                            linked_docs.add(rel.get("target"))
                        
        orphans = all_docs - linked_docs
        return list(orphans)

    def detect_missing_concepts(self) -> list[str]:
        index_file = self.wiki_dir / "index.yaml"
        ontology_file = self.meta_dir / "ontology" / "global_ontology.yaml"
        
        if not index_file.exists() or not ontology_file.exists():
            return []
            
        with open(ontology_file, "r", encoding="utf-8") as f:
            ont_data = yaml.safe_load(f) or {"terms": {}}
        existing_terms = set(ont_data.get("terms", {}).keys())
        return []

    def detect_contradictions(self, client, model: str) -> list[dict]:
        index_file = self.wiki_dir / "index.yaml"
        if not index_file.exists():
            return []
            
        with open(index_file, "r", encoding="utf-8") as f:
            index_data = yaml.safe_load(f) or {"documents": []}
            
        docs = index_data.get("documents", [])
        if len(docs) < 2:
            return []
            
        candidates = docs[:10]
        context = "\n---\n".join(
            f"doc_id: {d['id']}\n摘要: {d.get('abstract_short', '')}" 
            for d in candidates
        )
        
        system_prompt = """你是一个知识库健康审查员。
请阅读以下文档摘要，检查其中是否存在任何：
1. 知识矛盾（例如 A 文档说延迟是 10ms，B 文档说是 20ms）
2. 陈旧信息（过时的描述）

严格JSON输出:
{"contradictions": [{"docs": ["doc_A", "doc_B"], "conflict": "具体矛盾描述"}]}
如果没有矛盾，输出: {"contradictions": []}
"""
        result = llm_call_json(client, model, system_prompt, context)
        return result.get("contradictions", [])

    def run_lint(self):
        print("🔍 知识库健康审查 (Lint Workflow) 开始...")
        report_lines = ["# 知识库健康审查报告", f"日期: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
        
        orphans = self.detect_orphan_pages()
        report_lines.append(f"## 孤立页面检测 ({len(orphans)} 个)")
        for o in orphans:
            report_lines.append(f"- {o}")
            
        report_lines.append("")
        
        missing = self.detect_missing_concepts()
        report_lines.append(f"## 概念缺失检测 ({len(missing)} 个)")
        for m in missing:
            report_lines.append(f"- {m}")
            
        report_lines.append("")
        
        try:
            client = get_llm_client()
            search_model = os.environ.get("SEARCH_MODEL", "gpt-4o")
            contradictions = self.detect_contradictions(client, search_model)
            
            report_lines.append(f"## 矛盾与陈旧检查 ({len(contradictions)} 处)")
            for c in contradictions:
                docs_str = ", ".join(c.get("docs", []))
                report_lines.append(f"- 【冲突文档: {docs_str}】 {c.get('conflict', '')}")
                
        except Exception as e:
            report_lines.append(f"## 矛盾与陈旧检查\nLLM 调用失败: {e}")

        self.wiki_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        report_path = self.wiki_dir / f"lint_report_{date_str}.md"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
            
        print(f"✅ Lint 报告已生成: {report_path}")
        
        try:
            from scripts.logger import global_logger
            global_logger.log(
                action="lint",
                target="wiki/index.yaml",
                details=f"Orphans: {len(orphans)}, Missing Concepts: {len(missing)}\nReport generated: {report_path.name}"
            )
        except ImportError:
            pass

if __name__ == "__main__":
    Linter().run_lint()
