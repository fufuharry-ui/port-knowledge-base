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
                        started_at="2026-08-06T14:30:02+00:00")
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
    manifest = running_manifest(tmp_path)
    import api.compile_transactions as transactions

    terminate = Mock()
    monkeypatch.setattr(
        transactions, "verify_process_identity",
        lambda identity: IdentityStatus(status="process_gone"),
    )
    monkeypatch.setattr(transactions, "terminate_process_tree", terminate)
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True
    terminate.assert_not_called()
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
# ---------------------------------------------------------------------------


def test_corrupt_bound_meta_blocks_recovery_without_raising(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert any("meta" in path for path in result.failed_paths)
    # 保持 ROLLBACKING,记录失败路径,下次可幂等继续
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    assert reloaded.recovery["failed_paths"]
    # 原始失败原因保留
    assert reloaded.failure["original_code"] == "interrupted"


def test_non_mapping_bound_meta_blocks_recovery_without_raising(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "- a\n- b\n", encoding="utf-8"
    )
    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.blocked is True
    assert load_manifest(manifest.job_dir).state is ROLLBACKING


def test_corrupt_bound_meta_blocks_startup_with_report(tmp_path):
    manifest = scheduled_manifest(tmp_path)
    (tmp_path / "raw" / f"{DOC_ID}.meta.yaml").write_text(
        "key: [unclosed\n", encoding="utf-8"
    )
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is False
    assert any(manifest.job_id in blocker for blocker in report.blockers)
    assert manifest.job_dir.exists()


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
    assert manifest.job_dir.exists()


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
