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
            f"doc_id: {d['id']}\nAbstract: {d.get('abstract_short', '')}" 
            for d in candidates
        )
        
        system_prompt = "You are a knowledge base health reviewer. Check for contradictions. Output JSON."
        result = llm_call_json(client, model, system_prompt, context)
        return result.get("contradictions", [])

    def run_lint(self):
        print("Knowledge base health review starting...")
        report_lines = ["# KB Health Report", f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", ""]
        
        orphans = self.detect_orphan_pages()
        report_lines.append(f"## Orphan Pages ({len(orphans)})")
        for o in orphans:
            report_lines.append(f"- {o}")
            
        report_lines.append("")
        
        missing = self.detect_missing_concepts()
        report_lines.append(f"## Missing Concepts ({len(missing)})")
        for m in missing:
            report_lines.append(f"- {m}")
            
        report_lines.append("")
        
        try:
            client = get_llm_client()
            search_model = os.environ.get("SEARCH_MODEL", "gpt-4o")
            contradictions = self.detect_contradictions(client, search_model)
            
            report_lines.append(f"## Contradictions ({len(contradictions)})")
            for c in contradictions:
                docs_str = ", ".join(c.get("docs", []))
                report_lines.append(f"- [Conflict: {docs_str}] {c.get('conflict', '')}")
                
        except Exception as e:
            report_lines.append(f"## Contradictions\nLLM call failed: {e}")

        self.wiki_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d")
        report_path = self.wiki_dir / f"lint_report_{date_str}.md"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(report_lines))
            
        print(f"Lint report generated: {report_path}")
        
        try:
            from scripts.logger import global_logger
            global_logger.log(
                action="lint",
                target="wiki/index.yaml",
                details=f"Orphans: {len(orphans)}, Missing: {len(missing)}\nReport: {report_path.name}"
            )
        except ImportError:
            pass

if __name__ == "__main__":
    Linter().run_lint()