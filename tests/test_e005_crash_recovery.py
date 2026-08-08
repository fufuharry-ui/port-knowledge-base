"""E005 Task 10: R8 上传崩溃窗口恢复测试(设计 §12.3、§12.4、§24.6 R8)。

六个崩溃窗口全部经命名边界注入,绝不使用生产环境开关:

1. after_intake_stage       → api.main._r8_boundary_after_intake_stage
2. after_prepare_ingest     → api.main._r8_boundary_after_prepare_ingest
3. after_prepared_manifest  → api.main._r8_boundary_after_prepared_manifest
4. after_original_publish   → api.upload_intake.durable_write_bytes
                              (original 发布 + journal 后、raw text 发布前)
5. after_raw_text_publish   → api.upload_intake.durable_write_yaml
                              (raw text 发布 + journal 后、raw meta 发布前)
6. after_scheduled          → api.main._r8_boundary_after_scheduled
                              (SCHEDULED 持久迁移后、add_task 前)

崩溃以专用 _InjectedCrash(BaseException)注入:请求线程的 except Exception
恢复处理不捕获它,try/finally 只丢弃本请求未接受的 intake staging(与真实
崩溃后由 recover_startup 清理 .staging-* 等价),其余现场保持崩溃时刻状态,
由 recover_startup 完成恢复。

每个窗口恢复后必须满足(设计 §24.6 R8):
- report.blockers == [];
- 无孤立 doc(本轮 raw meta/txt、originals 新文件全部不存在);
- 无孤立 compiling;
- 共享产物(index/本体/关系全局文件)与崩溃前字节一致;
- 既有业务文件字节不变;
- after_scheduled 窗口的上传按中断事务完整撤销(本轮 original/raw 删除,
  无孤儿 error 文档)。

全部测试仅使用 tmp_path 仓库;无网络、无模型 Key、不访问真实知识库目录。
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from api.compile_transactions import (
    find_orphan_compiling_docs,
    recover_startup,
)
from api.durable_fs import sha256_file
from api.runtime_guard import (
    ApiInstanceLock,
    ServiceReadiness,
    load_compile_runtime_config,
)
from scripts.ingest import TZ_CST

UPLOAD_NAME = "upload.txt"
UPLOAD_PAYLOAD = b"port upload crash window payload"

#: 共享全局产物(崩溃前后必须字节一致)。
SHARED_ARTIFACTS = (
    "wiki/index.yaml",
    "meta/ontology/global_ontology.yaml",
    "meta/relations/knowledge_graph.yaml",
    "meta/ontology/entity_relations.yaml",
)

CRASH_POINTS = (
    "after_intake_stage",
    "after_prepare_ingest",
    "after_prepared_manifest",
    "after_original_publish",
    "after_raw_text_publish",
    "after_scheduled",
)


class _InjectedCrash(BaseException):
    """模拟进程在命名边界崩溃;绝不被 except Exception 恢复处理捕获。"""


class FakeUploadFile:
    """最小 UploadFile stub: 仅暴露 .file 与 .filename。"""

    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.file = io.BytesIO(payload)


def _write_yaml_bytes(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        yaml.dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
    )


class UploadCrashScenario:
    """在 tmp 仓库中驱动两阶段上传流程直到指定崩溃窗口,并提供恢复后断言。"""

    def __init__(self, tmp_path: Path, crash_point: str):
        assert crash_point in CRASH_POINTS
        self.repo = Path(tmp_path)
        self.crash_point = crash_point
        self.config = load_compile_runtime_config(self.repo, env={})
        today = datetime.now(TZ_CST).strftime("%Y%m%d")
        self.existing_doc_id = f"doc_{today}_001"
        self.new_doc_id = f"doc_{today}_002"
        self._seed_repo()
        self._before_shared = self._hash_paths(SHARED_ARTIFACTS)
        self._before_preexisting = self._hash_paths(self._preexisting_files)

    # ------------------------------------------------------------------
    # 仓库种子与快照
    # ------------------------------------------------------------------

    def _seed_repo(self) -> None:
        """一篇既有 compiled 文档 + 四个共享全局产物;既有 doc_id 占今日 001 序号。"""
        doc = self.existing_doc_id
        originals = self.repo / "originals"
        originals.mkdir(parents=True)
        (originals / "existing.txt").write_bytes(b"existing original bytes")
        raw = self.repo / "raw"
        raw.mkdir(parents=True)
        (raw / f"{doc}.txt").write_bytes(b"existing raw text")
        _write_yaml_bytes(raw / f"{doc}.meta.yaml", {
            "id": doc,
            "title": "existing",
            "source_type": "txt",
            "file_hash": "sha256:existing",
            "status": "compiled",
            "char_count": 17,
        })
        _write_yaml_bytes(self.repo / "wiki" / "index.yaml", {
            "documents": [{
                "id": doc,
                "title": "existing",
                "file_hash": "sha256:existing",
                "status": "compiled",
            }],
        })
        _write_yaml_bytes(
            self.repo / "wiki" / f"{doc}.summary.yaml",
            {"doc_id": doc, "abstract": "existing summary"},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "global_ontology.yaml",
            {"ontology_tree": [], "total_nodes": 0},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / f"{doc}.ontology.yaml",
            {"doc_id": doc, "keywords": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "entity_relations.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / "knowledge_graph.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / f"{doc}.relations.yaml",
            {"doc_id": doc, "relations": []},
        )
        self._preexisting_files = [
            f"originals/existing.txt",
            f"raw/{doc}.txt",
            f"raw/{doc}.meta.yaml",
            f"wiki/{doc}.summary.yaml",
            f"meta/ontology/{doc}.ontology.yaml",
            f"meta/relations/{doc}.relations.yaml",
            *SHARED_ARTIFACTS,
        ]

    def _hash_paths(self, relative_paths) -> dict:
        result = {}
        for rel in relative_paths:
            path = self.repo / rel
            result[rel] = sha256_file(path) if path.is_file() else None
        return result

    # ------------------------------------------------------------------
    # 崩溃注入与流程驱动
    # ------------------------------------------------------------------

    def _install_crash(self, mp, api_mod, intake_mod) -> None:
        def crash(_arg=None):
            raise _InjectedCrash(self.crash_point)

        if self.crash_point == "after_intake_stage":
            mp.setattr(api_mod, "_r8_boundary_after_intake_stage", crash)
        elif self.crash_point == "after_prepare_ingest":
            mp.setattr(api_mod, "_r8_boundary_after_prepare_ingest", crash)
        elif self.crash_point == "after_prepared_manifest":
            mp.setattr(api_mod, "_r8_boundary_after_prepared_manifest", crash)
        elif self.crash_point == "after_original_publish":
            real_write_bytes = intake_mod.durable_write_bytes

            def fail_on_raw_text(path, payload):
                path = Path(path)
                if path.parent.name == "raw" and path.suffix == ".txt":
                    raise _InjectedCrash(self.crash_point)
                return real_write_bytes(path, payload)

            mp.setattr(intake_mod, "durable_write_bytes", fail_on_raw_text)
        elif self.crash_point == "after_raw_text_publish":
            real_write_yaml = intake_mod.durable_write_yaml

            def fail_on_raw_meta(path, data):
                path = Path(path)
                if path.parent.name == "raw" and path.name.endswith(".meta.yaml"):
                    raise _InjectedCrash(self.crash_point)
                return real_write_yaml(path, data)

            mp.setattr(intake_mod, "durable_write_yaml", fail_on_raw_meta)
        elif self.crash_point == "after_scheduled":
            mp.setattr(api_mod, "_r8_boundary_after_scheduled", crash)

    def _patch_all_path_constants(self, mp, api_mod) -> None:
        """把上传流程可能触及的全部模块级路径常量重绑定到 tmp 仓库。

        隔离合同(测试污染事故修复):两阶段流程及其降级路径绝不读取或写入
        真实知识库目录。除 api.main 外,scripts.ingest 拥有独立的模块级路径
        常量(旧 _accept_upload 经 ingest_file 写真实 raw/),scripts.logger
        的 global_logger 按 CWD 解析 wiki/log.md;任何一个漏 patch 都会污染
        真实仓库。此处全量重绑定,并在驱动前逐一断言常量落在 tmp 仓库内。
        """
        import scripts.ingest as ingest_mod
        import scripts.logger as logger_mod

        wiki_dir = self.repo / "wiki"
        raw_dir = self.repo / "raw"
        originals_dir = self.repo / "originals"
        meta_dir = self.repo / "meta"
        index_file = wiki_dir / "index.yaml"

        for module, names in (
            (api_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                       "META_DIR", "INDEX_FILE")),
            (ingest_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                          "INDEX_FILE")),
        ):
            for name in names:
                target = {
                    "BASE_DIR": self.repo,
                    "RAW_DIR": raw_dir,
                    "ORIGINALS_DIR": originals_dir,
                    "WIKI_DIR": wiki_dir,
                    "META_DIR": meta_dir,
                    "INDEX_FILE": index_file,
                }[name]
                mp.setattr(module, name, target, raising=False)
        sandbox_logger = logger_mod.ActivityLogger(wiki_dir)
        mp.setattr(logger_mod, "global_logger", sandbox_logger)

        # 驱动前 fail-closed:任何常量逃出 tmp 仓库即拒绝运行(防污染护栏)。
        for module, names in (
            (api_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                       "META_DIR", "INDEX_FILE")),
            (ingest_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                          "INDEX_FILE")),
        ):
            for name in names:
                value = Path(getattr(module, name)).resolve()
                assert value == self.repo.resolve() or self.repo.resolve() in value.parents, (
                    f"{module.__name__}.{name} escapes the tmp repo: {value}"
                )

    def run_until_crash(self) -> None:
        """在调度锁与命名边界语义下运行上传流程,直到注入的崩溃。"""
        import api.main as api_mod
        import api.upload_intake as intake_mod
        from fastapi import BackgroundTasks

        with pytest.MonkeyPatch.context() as mp:
            self._patch_all_path_constants(mp, api_mod)
            self._install_crash(mp, api_mod, intake_mod)
            runtime = api_mod.AppRuntime(
                config=self.config,
                readiness=ServiceReadiness(),
                instance_lock=ApiInstanceLock(
                    self.repo / ".runtime" / "api-instance.lock"
                ),
            )
            upload = FakeUploadFile(UPLOAD_NAME, UPLOAD_PAYLOAD)
            with pytest.raises(_InjectedCrash):
                api_mod._accept_upload(BackgroundTasks(), upload, runtime)

    # ------------------------------------------------------------------
    # 恢复后断言
    # ------------------------------------------------------------------

    def orphan_docs(self) -> list[str]:
        """本轮上传残留的业务文件(raw/ 与 originals/ 中非既有文件)。"""
        orphans: list[str] = []
        for sub in ("raw", "originals"):
            base = self.repo / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.repo).as_posix()
                if rel not in self._before_preexisting:
                    orphans.append(rel)
        return orphans

    def orphan_compiling(self) -> list[str]:
        orphans, unreadable = find_orphan_compiling_docs(self.repo)
        return sorted(orphans + unreadable)

    def shared_artifacts_match_before(self) -> bool:
        return self._hash_paths(SHARED_ARTIFACTS) == self._before_shared

    def preexisting_files_unchanged(self) -> bool:
        current = self._hash_paths(self._preexisting_files)
        return current == self._before_preexisting

    def runtime_dirs_clean(self) -> bool:
        """恢复后事务目录与 intake staging 全部清空。"""
        for root in (
            self.config.transaction_dir,
            self.config.upload_intake_dir,
        ):
            if root.is_dir() and list(root.iterdir()):
                return False
        return True

    def upload_fully_revoked(self) -> bool:
        """after_scheduled 窗口:本轮 original/raw text/raw meta 全部撤销。"""
        return not (
            (self.repo / "originals" / UPLOAD_NAME).exists()
            or (self.repo / "raw" / f"{self.new_doc_id}.txt").exists()
            or (self.repo / "raw" / f"{self.new_doc_id}.meta.yaml").exists()
        )


@pytest.mark.parametrize("crash_point", list(CRASH_POINTS))
def test_r8_upload_crash_windows_recover_without_orphans(tmp_path, crash_point):
    scenario = UploadCrashScenario(tmp_path, crash_point)
    scenario.run_until_crash()
    report = recover_startup(tmp_path, scenario.config)
    assert report.blockers == []
    assert scenario.orphan_docs() == []
    assert scenario.orphan_compiling() == []
    assert scenario.shared_artifacts_match_before()
    assert scenario.preexisting_files_unchanged()
    assert scenario.runtime_dirs_clean()
    if crash_point == "after_scheduled":
        # 已进入 SCHEDULED 的上传按中断事务完整撤销,不保留孤儿 error 文档
        assert scenario.upload_fully_revoked()
