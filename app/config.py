"""
app/config.py - Unified configuration management
"""

import os
from pathlib import Path
from functools import lru_cache


def _load_dot_env(env_file: Path):
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


class Settings:
    def __init__(self):
        env_base = os.environ.get("KB_BASE_DIR")
        if env_base:
            self.base_dir = Path(env_base)
        else:
            self.base_dir = Path(__file__).parent.parent

        _load_dot_env(self.base_dir / ".env")

        self.raw_dir = self.base_dir / "raw"
        self.wiki_dir = self.base_dir / "wiki"
        self.meta_dir = self.base_dir / "meta"
        self.originals_dir = self.base_dir / "originals"
        self.ontology_dir = self.meta_dir / "ontology"
        self.relations_dir = self.meta_dir / "relations"

        self.index_file = self.wiki_dir / "index.yaml"
        self.kg_file = self.relations_dir / "knowledge_graph.yaml"
        self.global_ontology_file = self.ontology_dir / "global_ontology.yaml"

        self.openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
        self.openai_base_url: str = os.environ.get("OPENAI_BASE_URL", "")
        self.compile_model: str = os.environ.get("COMPILE_MODEL", "qwen-plus")
        self.search_model: str = os.environ.get("SEARCH_MODEL", "qwen-plus")
        self.relate_model: str = os.environ.get("RELATE_MODEL", "qwen-plus")

        for d in [self.raw_dir, self.wiki_dir, self.originals_dir,
                  self.ontology_dir, self.relations_dir]:
            d.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()