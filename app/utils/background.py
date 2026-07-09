"""
app/utils/background.py — 后台任务统一入口
compile_then_relate: 顺序执行 LLM 编译 → 关系检测 → 图谱更新
"""

import os
import sys
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# 确保项目根可被 import
_root = str(Path(__file__).parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)


def compile_then_relate(doc_id: str, base_dir: Path, settings=None) -> None:
    """
    后台异步任务：编译文档 → 检测关系 → 更新知识图谱。

    Args:
        doc_id:   已完成 ingest 的文档 ID
        base_dir: 项目根目录（供 monkeypatch 注入）
        settings: 可选的 Settings 对象，用于模型配置
    """
    try:
        import scripts.compile as compile_mod
        import scripts.relate as relate_mod

        # ── 1. 路径重定向（支持测试隔离）──────────────────────────────────────
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

        # ── 2. 初始化 LLM 客户端 ───────────────────────────────────────────────
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

        # ── 3. LLM 编译（摘要 + 本体 + 索引更新）──────────────────────────────
        logger.info("[BG] 开始编译 %s", doc_id)
        success = compile_mod.compile_doc(doc_id, client, compile_model, ontology_model)

        if not success:
            logger.warning("[BG] 编译失败 %s，跳过关系检测", doc_id)
            return

        # ── 4. 关系检测 + 图谱更新 ────────────────────────────────────────────
        logger.info("[BG] 开始关系检测 %s", doc_id)
        relate_client = relate_mod.get_llm_client()
        relations = relate_mod.detect_relations(doc_id, relate_client, relate_model)
        relate_mod.write_relations(doc_id, relations)
        relate_mod.update_kg(doc_id, relations)

        logger.info("[BG] 完成 %s: %d 条关系", doc_id, len(relations))

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("[BG] 后台任务异常 doc_id=%s: %s", doc_id, exc, exc_info=True)
        # 尝试标记文档状态为 error
        try:
            import scripts.compile as compile_mod
            compile_mod._update_meta_status(doc_id, "error", str(exc))
        except Exception:
            pass
