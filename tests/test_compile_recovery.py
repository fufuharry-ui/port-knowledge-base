"""E005 Task 4: 幂等恢复引擎与离线 CLI 测试。

全部测试仅使用 tmp_path 隔离目录;进程身份验证与进程树终止通过
monkeypatch 命名边界注入,不访问真实进程、真实知识库、网络或模型 Key。
"""
from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from api.compile_transactions import (
    CompileManifest,
    ManifestIntegrityError,
    PublishedIntake,
    TransactionKind,
    TransactionState,
    cleanup_terminal_transactions,
    create_prepared_transaction,
    list_transaction_dirs,
    load_manifest,
    recover_startup,
    recover_transaction,
    transition_manifest,
    verify_terminal_transaction,
)
from api.durable_fs import sha256_file
from api.process_tree import IdentityStatus, TerminationResult
from api.runtime_guard import load_compile_runtime_config
from scripts.compile_recovery import build_parser, main as cli_main

DOC_ID = "doc_20260806_001"

EXPECTED_ARTIFACT_PATHS = (
    f"wiki/{DOC_ID}.summary.yaml",
    "wiki/index.yaml",
    f"meta/ontology/{DOC_ID}.ontology.yaml",
    "meta/ontology/global_ontology.yaml",
    f"meta/relations/{DOC_ID}.relations.yaml",
    "meta/relations/knowledge_graph.yaml",
    "meta/ontology/entity_relations.yaml",
)

PREPARED = TransactionState.PREPARED
SCHEDULED = TransactionState.SCHEDULED
RUNNING = TransactionState.RUNNING
COMMITTED = TransactionState.COMMITTED
ROLLBACKING = TransactionState.ROLLBACKING
ROLLED_BACK = TransactionState.ROLLED_BACK

BUSINESS_SUBDIRS = ("raw", "wiki", "meta", "originals")


def runtime_config(base):
    return load_compile_runtime_config(base, env={})


def seed_business_tree(base, doc_id=DOC_ID):
    for rel in EXPECTED_ARTIFACT_PATHS:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"payload::{rel}".encode("utf-8"))


def write_doc_meta(base, doc_id, meta):
    raw_dir = base / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{doc_id}.meta.yaml").write_text(
        yaml.dump(meta, allow_unicode=True), encoding="utf-8"
    )


def read_meta(base, doc_id):
    return yaml.safe_load(
        (base / "raw" / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )


def rewrite_manifest_yaml(job_dir, data):
    (job_dir / "manifest.yaml").write_text(
        yaml.dump(data, allow_unicode=True), encoding="utf-8"
    )


def hash_tree(root, subdirs=BUSINESS_SUBDIRS):
    """对指定业务子树做 path → sha256 清单,用于证明阻断路径未触碰业务文件。"""
    result = {}
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def make_prepared(tmp_path, *, seed=True, kind=TransactionKind.RECOMPILE,
                  previous_meta=None, published_intake=None):
    if seed:
        seed_business_tree(tmp_path)
    if previous_meta is None and kind is TransactionKind.RECOMPILE:
        previous_meta = {"id": DOC_ID, "status": "compiled"}
    return create_prepared_transaction(
        base_dir=tmp_path,
        config=runtime_config(tmp_path),
        doc_id=DOC_ID,
        kind=kind,
        previous_meta=previous_meta,
        published_intake=published_intake,
    )


def scheduled_manifest(tmp_path):
    """SCHEDULED 中断现场: meta 已绑定 job, 编译进程已改写部分产物。"""
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID,
        "status": "compiling",
        "compile_job_id": manifest.job_id,
    })
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED,
                        scheduled_at="2026-08-06T14:30:01+00:00")
    (tmp_path / "wiki" / f"{DOC_ID}.summary.yaml").write_bytes(b"compiled-garbage")
    return load_manifest(manifest.job_dir)


def running_manifest(tmp_path):
    from api.compile_transactions import ProcessRecord

    manifest = scheduled_manifest(tmp_path)
    transition_manifest(
        manifest.job_dir, expected=SCHEDULED, target=RUNNING,
        started_at="2026-08-06T14:30:02+00:00",
        process=ProcessRecord(
            pid=4321,
            create_time=1786007401.25,
            executable="C:/Python312/python.exe",
            cwd="D:/repo",
            command_fingerprint=f"scripts.compile|{DOC_ID}",
            process_group_id=4321,
            platform="windows",
        ),
    )
    return load_manifest(manifest.job_dir)


def rollbacking_manifest_with_three_partially_restored_files(tmp_path):
    """R5 现场: ROLLBACKING 中再次崩溃, 七项产物仅前三项已恢复。"""
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID,
        "status": "compiling",
        "compile_job_id": manifest.job_id,
    })
    manifest = transition_manifest(
        manifest.job_dir, expected=PREPARED, target=ROLLBACKING,
        failure={"original_code": "interrupted", "original_message": "service restart"},
    )
    for record in manifest.artifacts:
        (tmp_path / record.path).write_bytes(
            b"compiled-garbage::" + record.path.encode("utf-8")
        )
    for record in manifest.artifacts[:3]:
        snapshot = manifest.job_dir / record.snapshot
        (tmp_path / record.path).write_bytes(snapshot.read_bytes())
    return manifest


def _committed_process_record():
    """R5-P1-1: RUNNING 迁移必须携带进程记录(状态机不变量)。"""
    from api.compile_transactions import ProcessRecord

    return ProcessRecord(
        pid=4321,
        create_time=1786007401.25,
        executable="C:/Python312/python.exe",
        cwd="D:/repo",
        command_fingerprint=f"scripts.compile|{DOC_ID}",
        process_group_id=4321,
        platform="windows",
    )


def committed_manifest(tmp_path):
    """R4 现场: COMMITTED 后崩溃, 业务产物为语义合法的编译成功结果。"""
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID,
        "status": "compiling",
        "compile_job_id": manifest.job_id,
    })
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED,
                        scheduled_at="2026-08-06T14:30:01+00:00")
    transition_manifest(manifest.job_dir, expected=SCHEDULED, target=RUNNING,
                        started_at="2026-08-06T14:30:02+00:00",
                        process=_committed_process_record())
    transition_manifest(manifest.job_dir, expected=RUNNING, target=COMMITTED)
    (tmp_path / "wiki" / f"{DOC_ID}.summary.yaml").write_text(
        yaml.dump({"doc_id": DOC_ID, "abstract": "a"}), encoding="utf-8"
    )
    (tmp_path / "meta" / "ontology" / f"{DOC_ID}.ontology.yaml").write_text(
        yaml.dump({"doc_id": DOC_ID, "keywords": []}), encoding="utf-8"
    )
    (tmp_path / "wiki" / "index.yaml").write_text(
        yaml.dump({"documents": [{"id": DOC_ID, "title": "t"}]}), encoding="utf-8"
    )
    (tmp_path / "meta" / "relations" / f"{DOC_ID}.relations.yaml").write_text(
        yaml.dump({"doc_id": DOC_ID, "relations": []}), encoding="utf-8"
    )
    (tmp_path / "meta" / "ontology" / "global_ontology.yaml").write_text(
        yaml.dump({"ontology_tree": [], "total_nodes": 0}), encoding="utf-8"
    )
    (tmp_path / "meta" / "relations" / "knowledge_graph.yaml").write_text(
        yaml.dump({"edges": []}), encoding="utf-8"
    )
    (tmp_path / "meta" / "ontology" / "entity_relations.yaml").write_text(
        yaml.dump({"edges": []}), encoding="utf-8"
    )
    write_doc_meta(tmp_path, DOC_ID, {"id": DOC_ID, "status": "compiled"})
    return load_manifest(manifest.job_dir)


# ---------------------------------------------------------------------------
# 启动恢复: 状态表
# ---------------------------------------------------------------------------


def test_scheduled_is_rolled_back_not_rescheduled(tmp_path, monkeypatch):
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    add_task = Mock()
    monkeypatch.setattr(transactions, "add_task", add_task, raising=False)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    assert report.recovered == [manifest.job_id]
    add_task.assert_not_called()
    assert read_meta(tmp_path, manifest.doc_id)["error_code"] == "interrupted"
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert "compile_job_id" not in meta
    # 被改写的产物已恢复为事务前字节
    restored = tmp_path / "wiki" / f"{DOC_ID}.summary.yaml"
    assert restored.read_bytes() == f"payload::wiki/{DOC_ID}.summary.yaml".encode("utf-8")
    # 恢复后终态验证通过并清理事务目录
    assert not manifest.job_dir.exists()


def test_rollbacking_recovery_is_idempotent_after_partial_restore(tmp_path):
    manifest = rollbacking_manifest_with_three_partially_restored_files(tmp_path)
    first = recover_transaction(tmp_path, runtime_config(tmp_path), manifest.job_dir,
                                reason_code="interrupted", reason_message="service restart")
    second = recover_transaction(tmp_path, runtime_config(tmp_path), manifest.job_dir,
                                 reason_code="interrupted", reason_message="service restart")
    assert first.completed is True
    assert second.already_terminal is True
    for record in manifest.artifacts:
        restored = (tmp_path / record.path).read_bytes()
        assert restored == (manifest.job_dir / record.snapshot).read_bytes()


def test_prepared_unbound_is_cleaned_without_interrupted(tmp_path):
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {"id": DOC_ID, "status": "compiled"})
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    assert not manifest.job_dir.exists()
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "compiled"
    assert "error_code" not in meta
    assert meta.get("error_code") != "interrupted"


def test_prepared_bound_is_rolled_back_as_interrupted(tmp_path):
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID,
        "status": "compiling",
        "compile_job_id": manifest.job_id,
    })
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "interrupted"
    assert not manifest.job_dir.exists()


def test_unknown_schema_blocks_startup_without_touching_business_files(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    data = yaml.safe_load(
        (manifest.job_dir / "manifest.yaml").read_text(encoding="utf-8")
    )
    data["schema_version"] = 2
    rewrite_manifest_yaml(manifest.job_dir, data)
    before = hash_tree(tmp_path, subdirs=BUSINESS_SUBDIRS + (".runtime",))
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert hash_tree(tmp_path, subdirs=BUSINESS_SUBDIRS + (".runtime",)) == before
    assert read_meta(tmp_path, DOC_ID)["status"] == "compiling"


def test_two_active_transactions_block_startup(tmp_path):
    first = scheduled_manifest(tmp_path)
    second = make_prepared(tmp_path)
    before = hash_tree(tmp_path)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert report.recovered == []
    assert any("multiple" in blocker or "active" in blocker
               for blocker in report.blockers)
    assert first.job_dir.exists()
    assert second.job_dir.exists()
    assert hash_tree(tmp_path) == before


def test_orphan_compiling_meta_blocks_startup_with_doc_id(tmp_path):
    orphan_id = "doc_20260806_099"
    write_doc_meta(tmp_path, orphan_id, {"id": orphan_id, "status": "compiling"})
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any(orphan_id in blocker for blocker in report.blockers)
    # 不得自动修复: meta 保持 compiling
    assert read_meta(tmp_path, orphan_id)["status"] == "compiling"


def test_corrupted_snapshot_blocks_without_touching_business_files(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    snapshot_file = manifest.job_dir / "snapshots" / "00.bin"
    payload = bytearray(snapshot_file.read_bytes())
    payload[0] ^= 0xFF
    snapshot_file.write_bytes(bytes(payload))
    before = hash_tree(tmp_path)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert report.recovered == []
    assert manifest.job_dir.exists()
    assert hash_tree(tmp_path) == before


def test_unpublished_staging_dirs_are_cleaned_first(tmp_path):
    config = runtime_config(tmp_path)
    (config.transaction_dir / ".staging-job-x").mkdir(parents=True)
    (config.upload_intake_dir / ".staging-intake-y").mkdir(parents=True)
    report = recover_startup(tmp_path, config)
    assert report.ready is True
    assert not (config.transaction_dir / ".staging-job-x").exists()
    assert not (config.upload_intake_dir / ".staging-intake-y").exists()


# ---------------------------------------------------------------------------
# RUNNING 进程处理
# ---------------------------------------------------------------------------


def test_running_with_gone_process_is_rolled_back(tmp_path, monkeypatch):
    """RUNNING 且根进程已退出: 恢复先经 already-gone 路径确认记录树无幸存
    后代(幸存者清扫),再回滚为 interrupted。"""
    manifest = running_manifest(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock(return_value=TerminationResult(status="already_gone"))
    monkeypatch.setattr(
        transactions, "verify_process_identity",
        lambda identity: IdentityStatus(status="process_gone"),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    # 根进程 gone ≠ 树已退出: 必须调用一次 already-gone 幸存者确认
    terminate.assert_called_once()
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "interrupted"


def test_running_identity_match_terminates_tree_then_rolls_back(tmp_path, monkeypatch):
    manifest = running_manifest(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock(return_value=TerminationResult(status="terminated"))
    monkeypatch.setattr(
        transactions, "verify_process_identity",
        lambda identity: IdentityStatus(status="verified"),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    terminate.assert_called_once()
    assert terminate.call_args[0][0].pid == 4321
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "interrupted"


def test_running_identity_mismatch_blocks_without_kill(tmp_path, monkeypatch):
    manifest = running_manifest(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock()
    monkeypatch.setattr(
        transactions, "verify_process_identity",
        lambda identity: IdentityStatus(
            status="identity_mismatch", mismatched_fields=("create_time",)
        ),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    before = hash_tree(tmp_path)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any("identity" in blocker for blocker in report.blockers)
    terminate.assert_not_called()
    # 保留证据: 事务目录与业务文件均不触碰
    assert manifest.job_dir.exists()
    assert hash_tree(tmp_path) == before


def test_running_survivors_block_rollback(tmp_path, monkeypatch):
    manifest = running_manifest(tmp_path)
    import api.compile_transactions as transactions

    monkeypatch.setattr(
        transactions, "verify_process_identity",
        lambda identity: IdentityStatus(status="verified"),
    )
    monkeypatch.setattr(
        transactions, "terminate_process_tree",
        lambda identity, grace: TerminationResult(
            status="survivors_remaining", survivors=(4322,)
        ),
    )
    before = hash_tree(tmp_path)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any("survivor" in blocker for blocker in report.blockers)
    # 幸存者存在时绝不回滚
    assert hash_tree(tmp_path) == before
    assert load_manifest(manifest.job_dir).state is RUNNING


# ---------------------------------------------------------------------------
# 上传事务回滚
# ---------------------------------------------------------------------------


def test_upload_rollback_removes_only_this_round_published_files(tmp_path):
    (tmp_path / "originals").mkdir(parents=True)
    (tmp_path / "originals" / "keep.pdf").write_bytes(b"keep")
    write_doc_meta(tmp_path, "doc_20260806_002", {
        "id": "doc_20260806_002", "status": "compiled",
    })
    (tmp_path / "raw" / "doc_20260806_002.txt").write_text("other", encoding="utf-8")

    intake = PublishedIntake(
        original_path="originals/example.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = make_prepared(
        tmp_path, seed=False, kind=TransactionKind.UPLOAD,
        previous_meta={"id": DOC_ID, "status": "raw"},
        published_intake=intake,
    )
    # 本轮发布的业务文件
    (tmp_path / "originals" / "example.pdf").write_bytes(b"example")
    (tmp_path / "raw" / f"{DOC_ID}.txt").write_text("text", encoding="utf-8")
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": manifest.job_id,
    })
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED,
                        scheduled_at="2026-08-06T14:30:01+00:00")
    # 编译进程已生成部分产物(事务前不存在)
    summary = tmp_path / "wiki" / f"{DOC_ID}.summary.yaml"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_bytes(b"partial-summary")

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    # 本轮发布与本轮生成的文件全部撤销
    assert not (tmp_path / "originals" / "example.pdf").exists()
    assert not (tmp_path / "raw" / f"{DOC_ID}.txt").exists()
    assert not (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").exists()
    assert not summary.exists()
    # 既有文件绝不触碰; 上传不保留孤儿 error 文档
    assert (tmp_path / "originals" / "keep.pdf").read_bytes() == b"keep"
    assert read_meta(tmp_path, "doc_20260806_002")["status"] == "compiled"
    assert (tmp_path / "raw" / "doc_20260806_002.txt").read_text(
        encoding="utf-8"
    ) == "other"


def test_upload_manifest_with_unsafe_intake_path_blocks(tmp_path):
    intake = PublishedIntake(
        original_path="originals/../../outside.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = make_prepared(
        tmp_path, seed=False, kind=TransactionKind.UPLOAD,
        previous_meta={"id": DOC_ID, "status": "raw"},
        published_intake=intake,
    )
    # 越界路径必须在 load 阶段被拒绝(ManifestIntegrityError)。
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def _scheduled_upload_with_intake(tmp_path, intake):
    """上传事务进入 SCHEDULED 并发布 intake 声明的全部文件。"""
    manifest = make_prepared(
        tmp_path, seed=False, kind=TransactionKind.UPLOAD,
        previous_meta={"id": DOC_ID, "status": "raw"},
        published_intake=intake,
    )
    for stored in (intake.original_path, intake.raw_text_path, intake.raw_meta_path):
        target = tmp_path / stored
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"published::" + stored.encode("utf-8"))
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED,
                        scheduled_at="2026-08-06T14:30:01+00:00")
    return load_manifest(manifest.job_dir)


def test_upload_rollback_blocks_on_raw_name_mismatch_without_deleting(tmp_path):
    # raw/evil.txt 能通过 load 的安全相对路径校验,但不等于本 doc_id 的
    # 重算目标;恢复必须失败关闭且不删除任何已发布文件。
    intake = PublishedIntake(
        original_path="originals/example.pdf",
        raw_text_path="raw/evil.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = _scheduled_upload_with_intake(tmp_path, intake)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
    for stored in (intake.original_path, intake.raw_text_path, intake.raw_meta_path):
        assert (tmp_path / stored).read_bytes() == b"published::" + stored.encode("utf-8")


def test_upload_rollback_blocks_on_nested_original_path_without_deleting(tmp_path):
    # originals/sub/f.pdf 能通过 load 校验,但不是 originals/ 直接子文件;
    # 恢复必须失败关闭且不删除任何已发布文件。
    intake = PublishedIntake(
        original_path="originals/sub/f.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = _scheduled_upload_with_intake(tmp_path, intake)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
    for stored in (intake.original_path, intake.raw_text_path, intake.raw_meta_path):
        assert (tmp_path / stored).read_bytes() == b"published::" + stored.encode("utf-8")


# ---------------------------------------------------------------------------
# 损坏/不可读 meta 的失败关闭(Finding 1)
# Codex R6-P1-3 语义更新: 已接受重编译的当前 meta 可能被编译进程的截断写
# 损坏;恢复先从 source-meta-before.yaml 快照字节级恢复 meta,再写错误
# 终态——当前 meta 损坏不再阻断(快照是唯一权威依据),快照本身损坏才
# 失败关闭。
# ---------------------------------------------------------------------------


def test_corrupt_bound_meta_is_restored_from_snapshot(tmp_path):
    """R6-P1-3(a): 当前 meta 截断损坏(不可解析)→ 从快照恢复后完成回滚,
    终态 meta 携带快照中的原始字段 + error 终态,终态验证通过。"""
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert meta["error_code"] == "interrupted"
    # 原始字段来自事务前快照
    assert meta["id"] == DOC_ID
    assert "compile_job_id" not in meta
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLED_BACK
    assert reloaded.failure["original_code"] == "interrupted"
    verification = verify_terminal_transaction(tmp_path, reloaded)
    assert verification.ok is True, verification.failures


def test_non_mapping_bound_meta_is_restored_from_snapshot(tmp_path):
    """R6-P1-3(a) 变体: 当前 meta 为合法 YAML 但非映射 → 同样从快照恢复。"""
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "- a\n- b\n", encoding="utf-8"
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["id"] == DOC_ID
    assert meta["status"] == "error"


def test_startup_recovers_with_corrupt_meta_via_snapshot(tmp_path):
    """R6-P1-3: 启动恢复对截断损坏的当前 meta 经快照恢复,ready。"""
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    assert manifest.job_id in report.recovered
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert meta["id"] == DOC_ID


def test_prepared_with_corrupt_meta_blocks_startup_without_cleaning(tmp_path):
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {"id": DOC_ID, "status": "compiled"})
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    # 无法证明业务未绑定 → 失败关闭,绝不清理事务目录
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert manifest.job_dir.exists()


def test_corrupt_meta_fails_terminal_verification_without_raising(tmp_path):
    manifest = committed_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    verification = verify_terminal_transaction(tmp_path, load_manifest(manifest.job_dir))
    assert verification.ok is False
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert manifest.job_dir.exists()


# ---------------------------------------------------------------------------
# 终态验证与清理
# ---------------------------------------------------------------------------


def test_committed_terminal_is_verified_and_cleaned(tmp_path):
    manifest = committed_manifest(tmp_path)
    verification = verify_terminal_transaction(tmp_path, manifest)
    assert verification.ok is True
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == [manifest.job_id]
    assert report.blockers == []
    assert not manifest.job_dir.exists()


def test_committed_verification_failure_blocks_cleanup(tmp_path):
    manifest = committed_manifest(tmp_path)
    # index 缺少目标文档条目 → 语义验证失败
    (tmp_path / "wiki" / "index.yaml").write_text(
        yaml.dump({"documents": []}), encoding="utf-8"
    )
    verification = verify_terminal_transaction(tmp_path, load_manifest(manifest.job_dir))
    assert verification.ok is False
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert manifest.job_dir.exists()


def test_committed_cleanup_rejects_meta_with_active_fields(tmp_path):
    manifest = committed_manifest(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiled", "compile_job_id": manifest.job_id,
    })
    verification = verify_terminal_transaction(tmp_path, load_manifest(manifest.job_dir))
    assert verification.ok is False


def test_rolled_back_terminal_is_verified_and_cleaned(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    verification = verify_terminal_transaction(tmp_path, load_manifest(manifest.job_dir))
    assert verification.ok is True
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == [manifest.job_id]
    assert not manifest.job_dir.exists()


def test_rolled_back_verification_failure_blocks_cleanup(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    # 回滚后产物被篡改 → 与事务前 SHA 不一致
    (tmp_path / "wiki" / f"{DOC_ID}.summary.yaml").write_bytes(b"tampered")
    verification = verify_terminal_transaction(tmp_path, load_manifest(manifest.job_dir))
    assert verification.ok is False
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert manifest.job_dir.exists()


def test_cleanup_never_deletes_active_transaction(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert manifest.job_id in report.kept
    assert manifest.job_dir.exists()


def test_cleanup_directory_deletion_failure_is_warning_only(tmp_path, monkeypatch):
    manifest = committed_manifest(tmp_path)
    import api.compile_transactions as transactions

    def boom(path, *args, **kwargs):
        raise OSError("simulated rmtree failure")

    monkeypatch.setattr(transactions.shutil, "rmtree", boom)
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert report.blockers == []
    assert any(manifest.job_id in warning for warning in report.warnings)
    # Codex R3 P2-1: 递归删除前先原子换名隔离——rmtree 失败时正式目录
    # 已不存在,残留位于 .cleanup-* 隔离区(绝不半删除正式事务目录)。
    assert not manifest.job_dir.exists()
    residue = (
        runtime_config(tmp_path).transaction_dir / f".cleanup-{manifest.job_id}"
    )
    assert residue.is_dir()


def test_cleanup_blocks_on_unreadable_manifest_without_deleting(tmp_path):
    manifest = committed_manifest(tmp_path)
    (manifest.job_dir / "manifest.yaml").write_text("{{{{", encoding="utf-8")
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert manifest.job_dir.exists()


# ---------------------------------------------------------------------------
# 启动恢复整合: R4 提交点后崩溃只验证清理
# ---------------------------------------------------------------------------


def test_startup_recovery_verifies_and_cleans_committed(tmp_path):
    manifest = committed_manifest(tmp_path)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    assert report.recovered == []
    assert manifest.job_id in report.cleaned
    assert not manifest.job_dir.exists()
    # COMMITTED 绝不回滚: 编译成功产物保持
    assert read_meta(tmp_path, DOC_ID)["status"] == "compiled"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_parser_exposes_exactly_the_four_subcommands():
    parser = build_parser()
    subparsers = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    assert set(subparsers.choices) == {
        "inspect", "verify", "recover", "cleanup-terminal",
    }
    forbidden = {"force-delete", "ignore-checksum", "skip-process-check",
                 "mark-resolved", "start-api-anyway"}
    assert forbidden.isdisjoint(set(subparsers.choices))


def test_cli_inspect_on_clean_tree_exits_zero_and_is_readonly(tmp_path, capsys):
    before = hash_tree(tmp_path, subdirs=(".",))
    exit_code = cli_main(["inspect", "--base-dir", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "api_instance_lock_held: False" in out
    assert hash_tree(tmp_path, subdirs=(".",)) == before


def test_cli_verify_terminal_job(tmp_path, capsys):
    manifest = committed_manifest(tmp_path)
    exit_code = cli_main(
        ["verify", manifest.job_id, "--base-dir", str(tmp_path)]
    )
    assert exit_code == 0
    assert manifest.job_id in capsys.readouterr().out
    # verify 只读: 事务目录保留
    assert manifest.job_dir.exists()


def test_cli_recover_scheduled_job(tmp_path, capsys):
    manifest = scheduled_manifest(tmp_path)
    exit_code = cli_main(
        ["recover", manifest.job_id, "--base-dir", str(tmp_path)]
    )
    assert exit_code == 0
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "interrupted"
    assert load_manifest(manifest.job_dir).state is ROLLED_BACK


def test_cli_cleanup_terminal(tmp_path, capsys):
    manifest = committed_manifest(tmp_path)
    exit_code = cli_main(["cleanup-terminal", "--base-dir", str(tmp_path)])
    assert exit_code == 0
    assert not manifest.job_dir.exists()


def test_cli_rejects_unsafe_job_id(tmp_path, capsys):
    exit_code = cli_main(["verify", "../escape", "--base-dir", str(tmp_path)])
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Codex Round 3 (P1-2): CLI recover 在持有实例锁后、恢复目标前必须重扫全部
# 事务目录——存在其他活动事务或其他 Manifest 不可读时拒绝恢复(失败关闭),
# 绝不猜测恢复顺序;拒绝信息只含 job_id,不泄露绝对路径。
# ---------------------------------------------------------------------------


def test_cli_recover_refuses_when_other_transaction_active(tmp_path, capsys):
    """P1-2: 目标活动 + 另一活动事务 → 拒绝恢复,两份 Manifest 与业务文件
    全部字节不变。"""
    target = scheduled_manifest(tmp_path)
    other = make_prepared(tmp_path)
    target_bytes = (target.job_dir / "manifest.yaml").read_bytes()
    other_bytes = (other.job_dir / "manifest.yaml").read_bytes()
    before = hash_tree(tmp_path)

    exit_code = cli_main(["recover", target.job_id, "--base-dir", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "refus" in out
    assert other.job_id in out
    # 拒绝信息不得包含绝对路径
    assert str(tmp_path) not in out
    assert (target.job_dir / "manifest.yaml").read_bytes() == target_bytes
    assert (other.job_dir / "manifest.yaml").read_bytes() == other_bytes
    assert hash_tree(tmp_path) == before


def test_cli_recover_refuses_terminal_target_when_other_active(tmp_path, capsys):
    """P1-2: 目标已终态但另一事务活动 → 同样拒绝(绝不猜测恢复顺序)。"""
    target = committed_manifest(tmp_path)
    other = make_prepared(tmp_path)

    exit_code = cli_main(["recover", target.job_id, "--base-dir", str(tmp_path)])

    assert exit_code == 1
    assert "refus" in capsys.readouterr().out
    assert load_manifest(target.job_dir).state is COMMITTED
    assert load_manifest(other.job_dir).state is PREPARED


def test_cli_recover_refuses_when_other_manifest_unreadable(tmp_path, capsys):
    """P1-2: 另一事务目录 Manifest 损坏 → 失败关闭,拒绝恢复目标。"""
    target = scheduled_manifest(tmp_path)
    other = make_prepared(tmp_path)
    (other.job_dir / "manifest.yaml").write_text("{{{{", encoding="utf-8")
    target_bytes = (target.job_dir / "manifest.yaml").read_bytes()
    before = hash_tree(tmp_path)

    exit_code = cli_main(["recover", target.job_id, "--base-dir", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "refus" in out
    assert str(tmp_path) not in out
    assert (target.job_dir / "manifest.yaml").read_bytes() == target_bytes
    assert hash_tree(tmp_path) == before


def test_cli_inspect_excludes_active_docs_from_orphan_report(tmp_path, capsys):
    """R4-P2-2: 携带合法活动事务的 compiling 文档只报 ACTIVE,不再重复报
    orphan_compiling;真正孤立(无事务)的 compiling 文档仍报告。"""
    manifest = scheduled_manifest(tmp_path)
    orphan_id = "doc_20260806_099"
    write_doc_meta(tmp_path, orphan_id, {"id": orphan_id, "status": "compiling"})

    exit_code = cli_main(["inspect", "--base-dir", str(tmp_path)])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert f"transaction {manifest.job_id}" in out
    assert "ACTIVE" in out
    assert f"orphan_compiling: {DOC_ID}" not in out
    assert f"orphan_compiling: {orphan_id}" in out


# ---------------------------------------------------------------------------
# Codex 修复(F3b):ROLLBACKING 携带进程记录时,恢复必须先验证并终止
# 记录进程、确认记录树无幸存后代,才允许继续回滚
# ---------------------------------------------------------------------------


def rollbacking_manifest_with_process(tmp_path):
    """ROLLBACKING + 进程记录现场(如 RUNNING 迁移失败后持久化的证据)。"""
    manifest = running_manifest(tmp_path)
    return transition_manifest(
        manifest.job_dir,
        expected=RUNNING,
        target=ROLLBACKING,
        failure={
            "original_code": "running_transition_failed",
            "original_message": "manifest io failed",
        },
    )


def test_rollbacking_with_recorded_process_terminates_before_restore(
    tmp_path, monkeypatch
):
    """F3(b): ROLLBACKING 携带进程记录 → 先验证身份并终止,再幂等回滚。"""
    manifest = rollbacking_manifest_with_process(tmp_path)
    import api.compile_transactions as transactions

    calls = []
    monkeypatch.setattr(
        transactions,
        "verify_process_identity",
        lambda identity: IdentityStatus(status="verified"),
    )
    monkeypatch.setattr(
        transactions,
        "terminate_process_tree",
        lambda identity, grace: calls.append(("terminate", identity.pid))
        or TerminationResult(status="terminated"),
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    assert calls == [("terminate", 4321)]
    # 回滚完成: 原始失败原因保留在 Manifest(running_transition_failed),
    # 文档终态按公开合同码(R4-P2-3: 内部码映射为 compile_failed)
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert (
        load_manifest(manifest.job_dir).failure["original_code"]
        == "running_transition_failed"
    )
    for record in manifest.artifacts:
        restored = (tmp_path / record.path).read_bytes()
        assert restored == (manifest.job_dir / record.snapshot).read_bytes()


def test_rollbacking_identity_mismatch_blocks_without_kill(tmp_path, monkeypatch):
    """F3(b): ROLLBACKING 进程记录身份不匹配 → 绝不 kill、绝不回滚,
    保留 ROLLBACKING 证据阻断。"""
    manifest = rollbacking_manifest_with_process(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock()
    monkeypatch.setattr(
        transactions,
        "verify_process_identity",
        lambda identity: IdentityStatus(
            status="identity_mismatch", mismatched_fields=("create_time",)
        ),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    before = hash_tree(tmp_path)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert "identity" in (result.reason or "")
    terminate.assert_not_called()
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
    assert hash_tree(tmp_path) == before


def test_rollbacking_gone_process_sweeps_survivors_then_rolls_back(
    tmp_path, monkeypatch
):
    """F3(b)+F6: ROLLBACKING 根进程已退出 → already-gone 幸存者确认干净后
    才回滚。"""
    manifest = rollbacking_manifest_with_process(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock(return_value=TerminationResult(status="already_gone"))
    monkeypatch.setattr(
        transactions,
        "verify_process_identity",
        lambda identity: IdentityStatus(status="process_gone"),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    terminate.assert_called_once()


def test_rollbacking_gone_process_with_survivors_blocks_rollback(
    tmp_path, monkeypatch
):
    """F3(b)+F6: 根进程已退出但记录树有幸存者 → 绝不回滚。"""
    manifest = rollbacking_manifest_with_process(tmp_path)
    import api.compile_transactions as transactions

    monkeypatch.setattr(
        transactions,
        "verify_process_identity",
        lambda identity: IdentityStatus(status="process_gone"),
    )
    monkeypatch.setattr(
        transactions,
        "terminate_process_tree",
        lambda identity, grace: TerminationResult(
            status="survivors_remaining", survivors=(4322,)
        ),
    )
    before = hash_tree(tmp_path)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert "unconfirmed" in (result.reason or "")
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
    assert hash_tree(tmp_path) == before


# ---------------------------------------------------------------------------
# Codex 修复(F5):source-meta-before.yaml 必须是原 meta 文件字节快照,
# 未接受回滚逐字节恢复(注释/格式不丢失);旧序列化形式仅作 legacy 回退
# ---------------------------------------------------------------------------


def _write_quirky_meta(base, doc_id):
    """带注释与非常规空白的 meta 字节(解析等价但字节 unique)。"""
    raw = base / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    meta_path = raw / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        "# 运维手工注释:不得丢失\n"
        f"id: {doc_id}\n"
        "status:   compiled\n"
        "title: 港口月报\n"
        "\n",
        encoding="utf-8",
    )
    return meta_path


def test_prepare_snapshots_original_meta_bytes_verbatim(tmp_path):
    """F5: PREPARED 事务的 source-meta-before.yaml 与原 meta 文件字节一致。"""
    from api.compile_jobs import prepare_recompile_transaction

    seed_business_tree(tmp_path)
    meta_path = _write_quirky_meta(tmp_path, DOC_ID)
    original_bytes = meta_path.read_bytes()
    manifest = prepare_recompile_transaction(
        DOC_ID, tmp_path, runtime_config(tmp_path)
    )
    snapshot = (manifest.job_dir / "source-meta-before.yaml").read_bytes()
    assert snapshot == original_bytes
    assert b"# " in snapshot  # 注释字节保留(序列化形式必然丢失)


def test_unaccepted_rollback_restores_meta_bytes_verbatim(tmp_path):
    """F5: 未接受请求的回滚把 meta 逐字节恢复为绑定前文件(含注释/格式),
    且终态验证一致通过。"""
    from api.compile_jobs import prepare_recompile_transaction

    seed_business_tree(tmp_path)
    meta_path = _write_quirky_meta(tmp_path, DOC_ID)
    original_bytes = meta_path.read_bytes()
    manifest = prepare_recompile_transaction(
        DOC_ID, tmp_path, runtime_config(tmp_path)
    )
    # 模拟请求线程绑定(改写 meta)后未被接受
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": manifest.job_id,
    })
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="unaccepted",
        reason_message="schedule transition failed before acceptance",
    )
    assert result.completed is True
    assert meta_path.read_bytes() == original_bytes
    verification = verify_terminal_transaction(
        tmp_path, load_manifest(manifest.job_dir)
    )
    assert verification.ok is True, verification.failures


def test_unaccepted_rollback_legacy_serialized_snapshot_still_verifies(tmp_path):
    """F5 legacy: 旧格式事务(序列化快照,无原始字节)仍按快照字节恢复并
    通过终态验证。"""
    manifest = make_prepared(tmp_path)  # previous_meta 仅 dict → 序列化快照
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": manifest.job_id,
    })
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="unaccepted", reason_message="add_task failed",
    )
    assert result.completed is True
    snapshot = (manifest.job_dir / "source-meta-before.yaml").read_bytes()
    assert (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").read_bytes() == snapshot
    verification = verify_terminal_transaction(
        tmp_path, load_manifest(manifest.job_dir)
    )
    assert verification.ok is True, verification.failures


# ---------------------------------------------------------------------------
# Codex Round 2 (N2): 进程身份防御性护栏——即使加载层被绕过,携带非正
# pid/process_group_id 的记录也绝不允许进入 verify/killpg 路径(失败关闭)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codex Round 5 (R5-P1-1): RUNNING + 无进程记录 = 损坏/不兼容证据
# (RUNNING 只能连同进程身份一起写入);恢复必须失败关闭——绝不回滚、
# 绝不 kill、保留证据;ROLLBACKING + 无进程记录保持合法(spawn 失败路径)。
# ---------------------------------------------------------------------------


def test_running_without_process_blocks_startup_without_touching_business_files(
    tmp_path,
):
    """R5-P1-1(a): RUNNING + process=null 的事务目录使启动恢复硬阻断
    (manifest 完整性),业务文件字节不变,事务目录保留。"""
    manifest = running_manifest(tmp_path)
    data = yaml.safe_load(
        (manifest.job_dir / "manifest.yaml").read_text(encoding="utf-8")
    )
    data["process"] = None
    rewrite_manifest_yaml(manifest.job_dir, data)
    before = hash_tree(tmp_path, subdirs=BUSINESS_SUBDIRS + (".runtime",))

    report = recover_startup(tmp_path, runtime_config(tmp_path))

    assert report.ready is False
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert hash_tree(tmp_path, subdirs=BUSINESS_SUBDIRS + (".runtime",)) == before
    assert manifest.job_dir.exists()


def test_recovery_blocks_running_with_null_process_even_if_load_bypassed(
    tmp_path, monkeypatch
):
    """R5-P1-1(b) 纵深防御: 即使加载层被绕过,RUNNING + 无进程记录也
    直接阻断,绝不调用 verify/kill,绝不回滚(镜像 N2 护栏)。"""
    from dataclasses import replace as dc_replace

    manifest = running_manifest(tmp_path)
    tampered = dc_replace(manifest, process=None)
    import api.compile_transactions as transactions

    monkeypatch.setattr(
        transactions, "load_manifest", lambda _job_dir: tampered
    )
    verify = Mock()
    terminate = Mock()
    monkeypatch.setattr(transactions, "verify_process_identity", verify)
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    before = hash_tree(tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    assert "process" in (result.reason or "")
    verify.assert_not_called()
    terminate.assert_not_called()
    assert hash_tree(tmp_path) == before


def test_recovery_never_signals_nonpositive_recorded_identity(
    tmp_path, monkeypatch
):
    """N2: 纵深防御——_terminate_leftover_process 对非正 pid/pgid 直接
    阻断,绝不调用 verify_process_identity / terminate_process_tree
    (POSIX killpg(0) 会把信号发向恢复进程自身的进程组)。"""
    from dataclasses import replace as dc_replace

    manifest = running_manifest(tmp_path)
    manifest = transition_manifest(
        manifest.job_dir,
        expected=RUNNING,
        target=ROLLBACKING,
        failure={"original_code": "interrupted", "original_message": "restart"},
    )
    import api.compile_transactions as transactions

    tampered = dc_replace(
        manifest,
        process=dc_replace(manifest.process, pid=0, process_group_id=0),
    )
    monkeypatch.setattr(
        transactions, "load_manifest", lambda _job_dir: tampered
    )
    verify = Mock()
    terminate = Mock()
    monkeypatch.setattr(transactions, "verify_process_identity", verify)
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    before = hash_tree(tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    verify.assert_not_called()
    terminate.assert_not_called()
    # 绝不回滚、绝不触碰业务文件
    assert hash_tree(tmp_path) == before


# ---------------------------------------------------------------------------
# Codex Round 2 (N6): 终态验证通过后、目录删除失败时,必须把"已验证"
# 耐久记录进 Manifest(cleanup_verified);后续清理只重试删除,绝不重新
# 内容验证——否则后续合法编译改变共享产物会被误判为损坏而阻断启动。
# ---------------------------------------------------------------------------


def test_cleanup_deletion_failure_marks_cleanup_verified(tmp_path, monkeypatch):
    """N6: 验证通过但删除失败 → warning 不变,且 Manifest 耐久记录
    cleanup_verified=True(P2-1 后证据随目录一起进入 .cleanup-* 隔离区)。"""
    manifest = committed_manifest(tmp_path)
    import api.compile_transactions as transactions

    def boom(path, *args, **kwargs):
        raise OSError("simulated rmtree failure")

    monkeypatch.setattr(transactions.shutil, "rmtree", boom)
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert report.blockers == []
    assert any(manifest.job_id in warning for warning in report.warnings)
    assert not manifest.job_dir.exists()
    residue = (
        runtime_config(tmp_path).transaction_dir / f".cleanup-{manifest.job_id}"
    )
    data = yaml.safe_load(
        (residue / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert data["cleanup_verified"] is True


def test_cleanup_verified_terminal_deletion_retry_never_reverifies(
    tmp_path, monkeypatch
):
    """N6 核心(P2-1 语义): ROLLED_BACK 目录隔离换名失败后,后续合法编译
    改变了共享产物;下一次清理必须只重试删除、绝不重新内容验证(旧行为把
    合法变化误判为损坏并阻断启动)。"""
    manifest = scheduled_manifest(tmp_path)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True

    import api.compile_transactions as transactions

    real_replace = transactions.os.replace

    def flaky_replace(src, dst):
        # 只拦截事务目录的隔离换名;耐久写入的临时文件 replace 不受影响
        if Path(src).name == manifest.job_id:
            raise OSError("simulated quarantine rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr(transactions.os, "replace", flaky_replace)
    first = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert first.blockers == []
    assert first.cleaned == []
    assert any(manifest.job_id in warning for warning in first.warnings)
    monkeypatch.undo()
    assert manifest.job_dir.exists()
    assert load_manifest(manifest.job_dir).cleanup_verified is True

    # 模拟后续合法编译改变共享产物(与事务前 SHA 不再一致)
    (tmp_path / "wiki" / "index.yaml").write_bytes(b"legitimate new index")
    (tmp_path / "meta" / "ontology" / "global_ontology.yaml").write_bytes(
        b"legitimate new ontology"
    )
    second = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert second.blockers == []
    assert second.cleaned == [manifest.job_id]
    assert not manifest.job_dir.exists()


def test_cleanup_unverified_terminal_still_verifies_before_deletion(tmp_path):
    """N6 对照: 从未验证过的终态目录仍然先验证;验证失败 → 不清理 + 阻断。"""
    manifest = committed_manifest(tmp_path)
    (tmp_path / "wiki" / "index.yaml").write_text(
        yaml.dump({"documents": []}), encoding="utf-8"
    )
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))
    assert report.cleaned == []
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert manifest.job_dir.exists()
    # 验证失败的目录绝不落 cleanup_verified
    data = yaml.safe_load(
        (manifest.job_dir / "manifest.yaml").read_text(encoding="utf-8")
    )
    assert data.get("cleanup_verified") is not True


# ---------------------------------------------------------------------------
# Codex Round 3 (P1-1): 回滚删除必须经 durable_unlink 耐久化(POSIX 父目录
# fsync),且全部删除先于 Manifest 的 ROLLED_BACK 提交点;删除失败失败关闭。
# ---------------------------------------------------------------------------


def _scheduled_upload_with_new_artifacts(tmp_path):
    """上传事务 SCHEDULED 现场: 本轮已发布 original/raw + 本轮新建产物。"""
    intake = PublishedIntake(
        original_path="originals/example.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = make_prepared(
        tmp_path, seed=False, kind=TransactionKind.UPLOAD,
        previous_meta={"id": DOC_ID, "status": "raw"},
        published_intake=intake,
    )
    for stored in (intake.original_path, intake.raw_text_path, intake.raw_meta_path):
        target = tmp_path / stored
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"published::" + stored.encode("utf-8"))
    summary = tmp_path / "wiki" / f"{DOC_ID}.summary.yaml"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_bytes(b"partial-summary")
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": manifest.job_id,
    })
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED,
                        scheduled_at="2026-08-06T14:30:01+00:00")
    return load_manifest(manifest.job_dir)


def test_rollback_deletions_are_durable_and_precede_rolled_back(
    tmp_path, monkeypatch
):
    """P1-1: 回滚路径上每个业务文件删除都经 durable_unlink(耐久删除原语),
    且全部删除严格先于 ROLLED_BACK 原子提交点。"""
    manifest = _scheduled_upload_with_new_artifacts(tmp_path)
    import api.compile_transactions as transactions
    from api.durable_fs import durable_unlink as real_unlink

    events = []

    def spy_unlink(path):
        events.append(("unlink", Path(path).name))
        return real_unlink(path)

    real_transition = transactions.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        events.append(("transition", target))
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr(transactions, "durable_unlink", spy_unlink)
    monkeypatch.setattr(transactions, "transition_manifest", spy_transition)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    deleted = {
        name for kind, name in events if kind == "unlink"
    }
    assert deleted == {
        f"{DOC_ID}.summary.yaml",
        "example.pdf",
        f"{DOC_ID}.txt",
        f"{DOC_ID}.meta.yaml",
    }
    rolled_back_at = next(
        index for index, event in enumerate(events)
        if event == ("transition", ROLLED_BACK)
    )
    assert all(
        index < rolled_back_at
        for index, event in enumerate(events)
        if event[0] == "unlink"
    )


def test_rollback_unlink_failure_blocks_and_stays_rollbacking(tmp_path, monkeypatch):
    """P1-1: 删除(含父目录 fsync)失败一律按删除失败处理: 恢复阻断、
    保持 ROLLBACKING 证据,绝不进入 ROLLED_BACK。"""
    manifest = make_prepared(tmp_path, seed=False)
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": manifest.job_id,
    })
    index = tmp_path / "wiki" / "index.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"garbage")
    transition_manifest(
        manifest.job_dir, expected=PREPARED, target=ROLLBACKING,
        failure={"original_code": "interrupted", "original_message": "restart"},
    )

    import api.compile_transactions as transactions

    def boom(path):
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(transactions, "durable_unlink", boom)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert "wiki/index.yaml" in result.failed_paths
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    assert "wiki/index.yaml" in reloaded.recovery["failed_paths"]


# ---------------------------------------------------------------------------
# Codex Round 3 (P2-1): 终态清理必须先原子换名 .cleanup-* 隔离区(POSIX
# 父目录 fsync)再递归删除——rmtree 半失败绝不留下损坏的正式事务目录
# (否则下次启动把半删除现场误判为 manifest-integrity blocker)。
# ---------------------------------------------------------------------------


def test_cleanup_quarantines_verified_terminal_before_recursive_delete(
    tmp_path, monkeypatch
):
    """P2-1(a): 验证通过的终态目录按 rename → 父目录 fsync → rmtree 顺序
    清理;清理完成后事务根无任何残留。"""
    manifest = committed_manifest(tmp_path)
    config = runtime_config(tmp_path)
    import api.compile_transactions as transactions

    events = []
    real_replace = transactions.os.replace

    def spy_replace(src, dst):
        events.append(("replace", Path(src).name, Path(dst).name))
        return real_replace(src, dst)

    real_fsync_parent = transactions.fsync_parent_directory

    def spy_fsync_parent(path):
        events.append(("fsync_parent", Path(path).name))
        return real_fsync_parent(path)

    real_rmtree = transactions.shutil.rmtree

    def spy_rmtree(path, *args, **kwargs):
        events.append(("rmtree", Path(path).name))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(transactions.os, "replace", spy_replace)
    monkeypatch.setattr(transactions, "fsync_parent_directory", spy_fsync_parent)
    monkeypatch.setattr(transactions.shutil, "rmtree", spy_rmtree)

    report = cleanup_terminal_transactions(tmp_path, config)

    assert report.cleaned == [manifest.job_id]
    assert report.blockers == []
    quarantine_name = f".cleanup-{manifest.job_id}"
    rename_idx = next(
        index for index, event in enumerate(events)
        if event == ("replace", manifest.job_id, quarantine_name)
    )
    fsync_idx = next(
        index for index, event in enumerate(events)
        if event == ("fsync_parent", quarantine_name)
    )
    rmtree_idx = next(
        index for index, event in enumerate(events)
        if event == ("rmtree", quarantine_name)
    )
    assert rename_idx < fsync_idx < rmtree_idx
    assert not manifest.job_dir.exists()
    assert list(config.transaction_dir.iterdir()) == []


def test_cleanup_partial_rmtree_failure_leaves_quarantine_residue_not_blocker(
    tmp_path, monkeypatch
):
    """P2-1(b): rmtree 在 manifest 删除后失败 → 残留位于 .cleanup-*,
    list_transaction_dirs 排除它,下次启动 ready 并 best-effort 重试清除。"""
    manifest = committed_manifest(tmp_path)
    config = runtime_config(tmp_path)
    import api.compile_transactions as transactions

    real_rmtree = transactions.shutil.rmtree

    def partial_rmtree(path, *args, **kwargs):
        target = Path(path)
        inner = target / "manifest.yaml"
        if target.name.startswith(".cleanup-") and inner.exists():
            # 模拟半删除现场: manifest 已删,目录与其他内容残留
            inner.unlink()
            raise OSError("simulated rmtree failure after manifest deletion")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(transactions.shutil, "rmtree", partial_rmtree)
    report = cleanup_terminal_transactions(tmp_path, config)
    assert report.cleaned == []
    assert report.blockers == []
    assert any(manifest.job_id in warning for warning in report.warnings)
    residue = config.transaction_dir / f".cleanup-{manifest.job_id}"
    assert residue.is_dir()
    assert not manifest.job_dir.exists()
    monkeypatch.undo()

    # 半删除残留绝不被当作损坏事务: 不被识别为事务目录,启动 ready
    assert list_transaction_dirs(config) == []
    startup = recover_startup(tmp_path, config)
    assert startup.ready is True, startup
    assert startup.blockers == []
    assert not residue.exists()


def test_cleanup_quarantine_rename_failure_keeps_verified_dir(tmp_path, monkeypatch):
    """P2-1(c): 隔离换名失败 → 仅警告,已验证目录原样保留(下次重试),
    证据 manifest 仍可严格加载。"""
    manifest = committed_manifest(tmp_path)
    import api.compile_transactions as transactions

    real_replace = transactions.os.replace

    def flaky_replace(src, dst):
        if Path(src).name == manifest.job_id:
            raise OSError("simulated rename failure")
        return real_replace(src, dst)

    monkeypatch.setattr(transactions.os, "replace", flaky_replace)
    report = cleanup_terminal_transactions(tmp_path, runtime_config(tmp_path))

    assert report.cleaned == []
    assert report.blockers == []
    assert any(manifest.job_id in warning for warning in report.warnings)
    assert manifest.job_dir.exists()
    assert load_manifest(manifest.job_dir).cleanup_verified is True


def test_startup_recovery_cleans_committed_via_quarantine_without_residue(tmp_path):
    """P2-1(d): 启动恢复端到端——COMMITTED 经隔离清理,无 blocker 无残留。"""
    manifest = committed_manifest(tmp_path)
    config = runtime_config(tmp_path)
    report = recover_startup(tmp_path, config)
    assert report.ready is True
    assert report.blockers == []
    assert manifest.job_id in report.cleaned
    assert not manifest.job_dir.exists()
    assert list(config.transaction_dir.iterdir()) == []


def test_prepared_unbound_recovery_completes_when_quarantine_rmtree_fails(
    tmp_path, monkeypatch
):
    """P2-1(e): PREPARED 未绑定清理——隔离换名成功后,隔离区 rmtree 失败
    绝不阻断恢复完成(仅警告),残留由启动清理重试。"""
    manifest = make_prepared(tmp_path)
    write_doc_meta(tmp_path, DOC_ID, {"id": DOC_ID, "status": "compiled"})
    import api.compile_transactions as transactions

    def boom(path, *args, **kwargs):
        raise OSError("simulated quarantine rmtree failure")

    monkeypatch.setattr(transactions.shutil, "rmtree", boom)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    assert result.reason == "prepared_unbound_cleaned"
    assert not manifest.job_dir.exists()
    residue = (
        runtime_config(tmp_path).transaction_dir / f".cleanup-{manifest.job_id}"
    )
    assert residue.is_dir()


# ---------------------------------------------------------------------------
# Codex Round 4 (R4-P1-1): spawn→RUNNING 身份窗口——服务在 spawn 之后、
# RUNNING 持久化之前死亡时,Manifest 停留无进程记录的 SCHEDULED,孤儿编译器
# 可能仍在写产物。SCHEDULED 回滚前必须扫描活进程(指纹 + cwd 匹配即阻断,
# 绝不自动 kill);扫描失败同样失败关闭。
# ---------------------------------------------------------------------------


class _FakeLiveProc:
    """最小活进程替身: 仅暴露 pid/cmdline()/cwd()。"""

    def __init__(self, pid, cmdline, cwd):
        self.pid = pid
        self._cmdline = cmdline
        self._cwd = cwd

    def cmdline(self):
        return self._cmdline

    def cwd(self):
        return self._cwd


def test_scheduled_recovery_blocks_on_live_orphan_compiler(tmp_path, monkeypatch):
    """R4-P1-1(a): SCHEDULED + 指纹与 cwd 均匹配的活进程 → 恢复阻断,
    Manifest 保持 SCHEDULED,业务文件字节不变,reason 指出 pid 供运维定位。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    orphan = _FakeLiveProc(
        pid=55501,
        cmdline=["python", "-m", "scripts.compile", DOC_ID],
        cwd=str(tmp_path),
    )
    monkeypatch.setattr(transactions.psutil, "process_iter", lambda: [orphan])
    before = hash_tree(tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    assert "55501" in (result.reason or "")
    assert load_manifest(manifest.job_dir).state is SCHEDULED
    assert hash_tree(tmp_path) == before


def test_scheduled_recovery_proceeds_when_match_has_different_cwd(
    tmp_path, monkeypatch
):
    """R4-P1-1(b): 指纹匹配但 cwd 不同的活进程(其他知识库的编译)→
    不属于本事务,正常回滚。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    other = _FakeLiveProc(
        pid=55502,
        cmdline=["python", "-m", "scripts.compile", DOC_ID],
        cwd="/elsewhere",
    )
    monkeypatch.setattr(transactions.psutil, "process_iter", lambda: [other])

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True


def test_scheduled_recovery_proceeds_without_matching_process(
    tmp_path, monkeypatch
):
    """R4-P1-1(c): 无匹配活进程 → 正常回滚(既有行为不变)。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    unrelated = _FakeLiveProc(
        pid=55503, cmdline=["/usr/sbin/cron", "-f"], cwd=str(tmp_path)
    )
    monkeypatch.setattr(transactions.psutil, "process_iter", lambda: [unrelated])

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "interrupted"


def test_scheduled_recovery_blocks_when_orphan_scan_fails(tmp_path, monkeypatch):
    """R4-P1-1(d): 活进程扫描自身失败 → 失败关闭阻断,Manifest 保持
    SCHEDULED,业务文件字节不变。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    def boom():
        raise transactions.psutil.Error("scan failed")

    monkeypatch.setattr(transactions.psutil, "process_iter", boom)
    before = hash_tree(tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    assert load_manifest(manifest.job_dir).state is SCHEDULED
    assert hash_tree(tmp_path) == before


# ---------------------------------------------------------------------------
# Codex Round 4 (R4-P1-3): doc_admin 的终态 meta 写入只有文件级 fsync +
# os.replace,无父目录 fsync;ROLLED_BACK 提交点前必须 fsync raw/ 父目录,
# fsync 失败失败关闭(保持 ROLLBACKING 证据)。
# ---------------------------------------------------------------------------


def test_rollback_fsyncs_raw_parent_after_error_terminal_before_rolled_back(
    tmp_path, monkeypatch
):
    """R4-P1-3: 错误终态 meta 写入之后、ROLLED_BACK 提交点之前必须
    fsync raw/ 父目录(顺序可证)。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    events = []
    real_write_result = transactions.write_doc_compile_result

    def spy_write_result(*args, **kwargs):
        events.append(("meta_write",))
        return real_write_result(*args, **kwargs)

    real_fsync_parent = transactions.fsync_parent_directory

    def spy_fsync_parent(path):
        events.append(("fsync_parent", Path(path).parent.name, Path(path).name))
        return real_fsync_parent(path)

    real_transition = transactions.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        events.append(("transition", target))
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr(transactions, "write_doc_compile_result", spy_write_result)
    monkeypatch.setattr(transactions, "fsync_parent_directory", spy_fsync_parent)
    monkeypatch.setattr(transactions, "transition_manifest", spy_transition)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.completed is True
    write_idx = next(i for i, e in enumerate(events) if e == ("meta_write",))
    fsync_idx = next(
        i for i, e in enumerate(events)
        if e == ("fsync_parent", "raw", f"{DOC_ID}.meta.yaml")
    )
    rolled_back_idx = next(
        i for i, e in enumerate(events) if e == ("transition", ROLLED_BACK)
    )
    assert write_idx < fsync_idx < rolled_back_idx


def test_rollback_blocks_when_raw_parent_fsync_fails(tmp_path, monkeypatch):
    """R4-P1-3: raw/ 父目录 fsync 失败 → 阻断,保持 ROLLBACKING,
    meta 终态证据保留(绝不进入 ROLLED_BACK)。"""
    manifest = scheduled_manifest(tmp_path)
    import api.compile_transactions as transactions

    def boom(path):
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(transactions, "fsync_parent_directory", boom)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    assert reloaded.recovery["failed_paths"]
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert meta["error_code"] == "interrupted"


# ---------------------------------------------------------------------------
# Codex Round 4 (R4-P2-3): 内部失败原因码(如 running_transition_failed)
# 绝不直接进入公开 meta error_code(设计 §20.1 + 前端 DocumentCompileErrorCode
# 合同);合同之外的内部码一律映射为 compile_failed,Manifest 保留原始码。
# ---------------------------------------------------------------------------


def _rollbacking_manifest_with_code(tmp_path, code):
    manifest = scheduled_manifest(tmp_path)
    return transition_manifest(
        manifest.job_dir, expected=SCHEDULED, target=ROLLBACKING,
        failure={"original_code": code, "original_message": "diag"},
    )


def test_rollback_maps_running_transition_failed_to_compile_failed(tmp_path):
    """R4-P2-3: original_code=running_transition_failed 回滚后 meta
    error_code 必须是公开合同码 compile_failed,终态验证通过,
    Manifest 保留内部码供运维诊断。"""
    manifest = _rollbacking_manifest_with_code(tmp_path, "running_transition_failed")
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.failure["original_code"] == "running_transition_failed"
    verification = verify_terminal_transaction(tmp_path, reloaded)
    assert verification.ok is True, verification.failures


def test_rollback_unknown_internal_code_maps_to_compile_failed(tmp_path):
    """R4-P2-3 结构性护栏: 任何合同之外的内部码(含未来新增)一律
    compile_failed,绝不渗透公开合同。"""
    manifest = _rollbacking_manifest_with_code(tmp_path, "future_internal_code")
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    assert read_meta(tmp_path, DOC_ID)["error_code"] == "compile_failed"
    verification = verify_terminal_transaction(
        tmp_path, load_manifest(manifest.job_dir)
    )
    assert verification.ok is True, verification.failures


@pytest.mark.parametrize(
    "public_code",
    ["interrupted", "timeout", "compile_failed", "llm_configuration",
     "service_unavailable", "document_processing", "rollback_failed"],
)
def test_rollback_public_contract_codes_pass_through(tmp_path, public_code):
    """R4-P2-3: 公开合同集合内的错误码原样透传,终态验证通过。"""
    manifest = _rollbacking_manifest_with_code(tmp_path, public_code)
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    assert read_meta(tmp_path, DOC_ID)["error_code"] == public_code
    verification = verify_terminal_transaction(
        tmp_path, load_manifest(manifest.job_dir)
    )
    assert verification.ok is True, verification.failures


# ---------------------------------------------------------------------------
# Codex Round 6 (R6-P1-2 纵深防御 / R6-P1-3 快照恢复补充)
# ---------------------------------------------------------------------------


def test_recovery_never_signals_invalid_recorded_create_time(tmp_path, monkeypatch):
    """R6-P1-2(b): 即使加载层被绕过,非有限/非正 create_time 的记录也
    直接阻断,绝不调用 verify/kill,绝不回滚(镜像 N2/R5 护栏)。"""
    from dataclasses import replace as dc_replace

    manifest = running_manifest(tmp_path)
    tampered = dc_replace(
        manifest, process=dc_replace(manifest.process, create_time=float("nan"))
    )
    import api.compile_transactions as transactions

    monkeypatch.setattr(transactions, "load_manifest", lambda _job_dir: tampered)
    verify = Mock()
    terminate = Mock()
    monkeypatch.setattr(transactions, "verify_process_identity", verify)
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    before = hash_tree(tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    assert "create_time" in (result.reason or "")
    verify.assert_not_called()
    terminate.assert_not_called()
    assert hash_tree(tmp_path) == before


def test_rollback_restores_partial_meta_keys_from_snapshot(tmp_path):
    """R6-P1-3(b): 当前 meta 为合法 YAML 但缺失键(部分写入)→ 终态 meta
    仍携带快照中的 id 等原始字段。"""
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "status: compiling\ncompile_job_id: " + manifest.job_id + "\n",
        encoding="utf-8",
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    meta = read_meta(tmp_path, DOC_ID)
    assert meta["id"] == DOC_ID
    assert meta["status"] == "error"
    assert meta["error_code"] == "interrupted"
    assert "compile_job_id" not in meta
    verification = verify_terminal_transaction(
        tmp_path, load_manifest(manifest.job_dir)
    )
    assert verification.ok is True, verification.failures


def test_rollback_blocks_when_source_meta_snapshot_missing(tmp_path):
    """R6-P1-3(c): 快照缺失 → 失败关闭,保持 ROLLBACKING,当前 meta 与
    事务证据原样保留。"""
    manifest = scheduled_manifest(tmp_path)
    meta_path = tmp_path / "raw" / f"{DOC_ID}.meta.yaml"
    meta_before = meta_path.read_bytes()
    (manifest.job_dir / "source-meta-before.yaml").unlink()

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )

    assert result.blocked is True
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    assert reloaded.recovery["failed_paths"]
    assert meta_path.read_bytes() == meta_before


def test_rollback_blocks_when_source_meta_snapshot_not_a_mapping(tmp_path):
    """R6-P1-3(c) 变体: 快照可解析但非映射 → 同样失败关闭。"""
    manifest = scheduled_manifest(tmp_path)
    (manifest.job_dir / "source-meta-before.yaml").write_text(
        "- a\n- b\n", encoding="utf-8"
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
