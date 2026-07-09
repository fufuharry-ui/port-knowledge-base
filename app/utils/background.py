"""
app/utils/background.py - Background task unified entry point
compile_then_relate: compile -> relation detection -> KG update
"""

import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


def compile_then_relate(doc_id: str, base_dir: Path, settings=None) -> None:
    try:
        import scripts.compile as compile_mod
        import scripts.relate as relate_mod

        compile_mod.BASE_DIR = base_dir
        compile_mod.RAW_DIR = base_dir / "raw"
        compile_mod.WIKI_DIR = base_dir / "wiki"
        compile_mod.META_DIR = base_dir / "meta"
        compile_mod.ONTOLOGY_DIR = base_dir / "meta" / "ontology"
        compile_mod.INDEX_FILE = base_dir / "wiki" / "index.yaml"
        compile_mod.GLOBAL_ONTOLOGY_FILE = base_dir / "meta" / "ontology" / "global_ontology.yaml"

        relate_mod.BASE_DIR = base_dir
        relate_mod.WIKI_DIR = base_dir / "wiki"
        relate_mod.RELATIONS_DIR = base_dir / "meta" / "relations"
        relate_mod.INDEX_FILE = base_dir / "wiki" / "index.yaml"
        relate_mod.KG_FILE = base_dir / "meta" / "relations" / "knowledge_graph.yaml"

        client = compile_mod.get_llm_client()

        compile_model = (
            settings.compile_model if settings else
            os.environ.get("COMPILE_MODEL", "qwen-plus")
        )
        ontology_model = compile_model
        relate_model = (
            settings.relate_model if settings else
            os.environ.get("RELATE_MODEL", "qwen-plus")
        )

        logger.info("[BG] Starting compile %s", doc_id)
        success = compile_mod.compile_doc(doc_id, client, compile_model, ontology_model)

        if not success:
            logger.warning("[BG] Compile failed %s, skipping relation detection", doc_id)
            return

        logger.info("[BG] Starting relation detection %s", doc_id)
        relate_client = relate_mod.get_llm_client()
        relations = relate_mod.detect_relations(doc_id, relate_client, relate_model)
        relate_mod.write_relations(doc_id, relations)
        relate_mod.update_kg(doc_id, relations)

        logger.info("[BG] Done %s: %d relations", doc_id, len(relations))

    except Exception as exc:
        logger.error("[BG] Background task error doc_id=%s: %s", doc_id, exc, exc_info=True)
        try:
            import scripts.compile as compile_mod
            compile_mod._update_meta_status(doc_id, "error", str(exc))
        except Exception:
            pass