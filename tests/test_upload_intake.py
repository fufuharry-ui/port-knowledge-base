"""E005 Task 7: 上传 intake staging 与原子发布测试。

全部测试仅使用 tmp_path 隔离目录;UploadFile 以内存 stub 替代,不访问
真实 HTTP、真实知识库目录、网络或模型 Key。

覆盖合同(设计 §7.1、§12.3、§12.4):
- stage_upload 只写 config.upload_intake_dir/.staging-{intake_id}/,
  暂存文件名即最终安全原始文件名;
- publish_upload_intake 按 original → raw text → raw meta 顺序耐久发布,
  绝不覆盖既有文件(FileExistsError 且既有字节不变);
- intake.yaml 只在每个目标耐久发布完成后记录该目标;
- rollback_published_intake 只删除 journal 证明由本请求创建的目标,
  重算安全路径(镜像 Task 4 _recompute_intake_targets 拒绝规则),
  幂等,绝不触碰既有文件;
- 空文本拒绝发布(纵深防御)。
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
import yaml

from api.compile_transactions import (
    PublishedIntake,
    TransactionKind,
    TransactionState,
    load_manifest,
    recover_transaction,
)
from api.runtime_guard import load_compile_runtime_config
from api.upload_intake import (
    StagedUpload,
    prepare_upload_transaction,
    publish_upload_intake,
    rollback_published_intake,
    stage_upload,
)
from scripts.ingest import prepare_ingest

JOURNAL_FILENAME = "intake.yaml"


class FakeUploadFile:
    """最小 UploadFile stub: 仅暴露 .file 与 .filename。"""

    def __init__(self, filename: str | None, payload: bytes):
        self.filename = filename
        self.file = io.BytesIO(payload)


def runtime_config(base: Path):
    return load_compile_runtime_config(base, env={})


def prepared_upload_transaction(tmp_path, filename="report.txt",
                                payload=b"port upload text"):
    """stage → prepare_ingest → PREPARED 上传事务; 不发布任何业务文件。"""
    config = runtime_config(tmp_path)
    staged = stage_upload(FakeUploadFile(filename, payload), config)
    prepared = prepare_ingest(
        staged.staged_file, base_dir=tmp_path, existing_doc_ids=set()
    )
    manifest = prepare_upload_transaction(staged, prepared, tmp_path, config)
    return manifest, staged, prepared


def read_journal(job_dir: Path) -> list[dict]:
    data = yaml.safe_load(
        (job_dir / JOURNAL_FILENAME).read_text(encoding="utf-8")
    )
    return data["entries"]


def write_journal(job_dir: Path, entries: list[dict]) -> None:
    (job_dir / JOURNAL_FILENAME).write_text(
        yaml.dump({"schema_version": 1, "entries": entries}, allow_unicode=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# stage_upload: 只写 intake staging
# ---------------------------------------------------------------------------


def test_stage_upload_writes_only_under_intake_staging(tmp_path):
    config = runtime_config(tmp_path)
    staged = stage_upload(FakeUploadFile("report.txt", b"abc"), config)
    assert isinstance(staged, StagedUpload)
    assert staged.original_name == "report.txt"
    assert staged.staged_file.name == "report.txt"
    assert staged.staged_file.read_bytes() == b"abc"
    assert staged.staged_file.parent == (
        config.upload_intake_dir / f".staging-{staged.intake_id}"
    )
    # staging 之外无任何业务写入
    assert not (tmp_path / "originals").exists()
    assert not (tmp_path / "raw").exists()


def test_stage_upload_strips_path_components_defensively(tmp_path):
    config = runtime_config(tmp_path)
    staged = stage_upload(FakeUploadFile("../sub/report.txt", b"x"), config)
    assert staged.original_name == "report.txt"
    assert staged.staged_file.parent.parent == config.upload_intake_dir


def test_stage_upload_revalidates_extension(tmp_path):
    config = runtime_config(tmp_path)
    with pytest.raises(ValueError):
        stage_upload(FakeUploadFile("evil.exe", b"x"), config)
    assert list(config.upload_intake_dir.glob("*")) == [] or not any(
        (config.upload_intake_dir / name).exists()
        for name in ("evil.exe",)
    )


def test_stage_upload_rejects_empty_filename(tmp_path):
    config = runtime_config(tmp_path)
    with pytest.raises(ValueError):
        stage_upload(FakeUploadFile("", b"x"), config)
    with pytest.raises(ValueError):
        stage_upload(FakeUploadFile(None, b"x"), config)


# ---------------------------------------------------------------------------
# prepare_upload_transaction: PREPARED + published_intake(published=False)
# ---------------------------------------------------------------------------


def test_prepare_upload_transaction_creates_prepared_upload_manifest(tmp_path):
    config = runtime_config(tmp_path)
    staged = stage_upload(FakeUploadFile("report.txt", b"body"), config)
    prepared = prepare_ingest(
        staged.staged_file, base_dir=tmp_path, existing_doc_ids=set()
    )
    manifest = prepare_upload_transaction(staged, prepared, tmp_path, config)

    assert manifest.kind is TransactionKind.UPLOAD
    assert manifest.state is TransactionState.PREPARED
    assert manifest.doc_id == prepared.doc_id
    assert manifest.published_intake == PublishedIntake(
        original_path="originals/report.txt",
        raw_text_path=f"raw/{prepared.doc_id}.txt",
        raw_meta_path=f"raw/{prepared.doc_id}.meta.yaml",
        published=False,
    )
    # 新 doc_id: 七项快照全部 existed=False
    assert len(manifest.artifacts) == 7
    assert all(not record.existed for record in manifest.artifacts)
    # Manifest 可被 load_manifest 严格回读; intake staging 保留
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.published_intake == manifest.published_intake
    assert staged.staged_file.is_file()


# ---------------------------------------------------------------------------
# publish_upload_intake: 顺序、不覆盖、journal 时序、published 翻转
# ---------------------------------------------------------------------------


def test_publish_writes_three_targets_in_order_and_flips_published(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(
        tmp_path, "report.txt", payload=b"port text"
    )
    updated = publish_upload_intake(manifest, staged, prepared, tmp_path)

    assert (tmp_path / "originals" / "report.txt").read_bytes() == b"port text"
    raw_text = tmp_path / "raw" / f"{prepared.doc_id}.txt"
    assert raw_text.read_bytes() == prepared.text_bytes
    meta = yaml.safe_load(
        (tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert meta == prepared.meta
    assert meta["source_original"] == "originals/report.txt"

    entries = read_journal(manifest.job_dir)
    assert entries == [
        {"path": "originals/report.txt", "created_by_this_request": True,
         "completed": True},
        {"path": f"raw/{prepared.doc_id}.txt", "created_by_this_request": True,
         "completed": True},
        {"path": f"raw/{prepared.doc_id}.meta.yaml",
         "created_by_this_request": True, "completed": True},
    ]
    assert updated.published_intake.published is True
    assert load_manifest(manifest.job_dir).published_intake.published is True


def test_publish_never_overwrites_existing_original(tmp_path):
    existing = tmp_path / "originals" / "report.txt"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)
    assert existing.read_bytes() == b"existing"
    # 第一目标即冲突: 不发布后续目标, 不产生 journal
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    assert not (manifest.job_dir / JOURNAL_FILENAME).exists()


def test_publish_never_overwrites_existing_raw_text(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    existing = tmp_path / "raw" / f"{prepared.doc_id}.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"existing-raw")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)
    assert existing.read_bytes() == b"existing-raw"
    # raw text 不是本请求创建: journal 只记录 original(intent-first:
    # 存在性冲突在 journal 落笔之前拒绝, 绝不记录非本请求目标)
    entries = read_journal(manifest.job_dir)
    assert entries == [
        {"path": "originals/report.txt", "created_by_this_request": True,
         "completed": True}
    ]


def test_publish_refuses_empty_text(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(
        tmp_path, "report.txt", payload=b"   \n\t  "
    )
    with pytest.raises(ValueError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert not (manifest.job_dir / JOURNAL_FILENAME).exists()


# ---------------------------------------------------------------------------
# 崩溃窗口: journal 精确驱动 rollback
# ---------------------------------------------------------------------------


def test_crash_after_original_publish_rollback_removes_only_original(tmp_path):
    """crash after_original_publish: journal 仅含 original, 只撤销 original。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    preexisting_text = tmp_path / "raw" / f"{prepared.doc_id}.txt"
    preexisting_text.parent.mkdir(parents=True, exist_ok=True)
    preexisting_text.write_bytes(b"preexisting-text")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)

    assert read_journal(manifest.job_dir) == [
        {"path": "originals/report.txt", "created_by_this_request": True,
         "completed": True}
    ]
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert preexisting_text.read_bytes() == b"preexisting-text"
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml").exists()


def test_partial_publish_rollback_removes_only_request_created_files(tmp_path):
    """crash after_raw_text_publish: 撤销 original+raw text, 既有文件不动。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    unrelated = tmp_path / "originals" / "keep.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"keep")
    preexisting_meta = tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml"
    preexisting_meta.parent.mkdir(parents=True, exist_ok=True)
    preexisting_meta.write_bytes(b"preexisting-meta")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)

    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    assert unrelated.read_bytes() == b"keep"
    assert preexisting_meta.read_bytes() == b"preexisting-meta"


def test_rollback_after_full_publish_removes_all_three(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    unrelated = tmp_path / "originals" / "keep.txt"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"keep")
    publish_upload_intake(manifest, staged, prepared, tmp_path)

    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml").exists()
    assert unrelated.read_bytes() == b"keep"


def test_rollback_is_idempotent(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    publish_upload_intake(manifest, staged, prepared, tmp_path)
    assert rollback_published_intake(manifest, tmp_path) == []
    # 第二次调用: no-op, 空 failures
    assert rollback_published_intake(manifest, tmp_path) == []
    assert not (tmp_path / "originals" / "report.txt").exists()


def test_rollback_ignores_entries_not_created_by_request(tmp_path):
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    preexisting = tmp_path / "originals" / "report.txt"
    preexisting.parent.mkdir(parents=True, exist_ok=True)
    preexisting.write_bytes(b"not-ours")
    write_journal(manifest.job_dir, [
        {"path": "originals/report.txt", "created_by_this_request": False}
    ])
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert preexisting.read_bytes() == b"not-ours"


def test_rollback_without_journal_is_noop(tmp_path):
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    assert rollback_published_intake(manifest, tmp_path) == []


# ---------------------------------------------------------------------------
# Codex Round 3 (P1-1): journal 驱动的精确撤销同样必须经 durable_unlink
# 耐久删除(POSIX 父目录 fsync);删除失败计入 failures(失败关闭)
# ---------------------------------------------------------------------------


def test_rollback_published_intake_uses_durable_unlink(tmp_path, monkeypatch):
    """P1-1: journal 证明由本请求创建的每个目标都经 durable_unlink 删除。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    publish_upload_intake(manifest, staged, prepared, tmp_path)

    import api.upload_intake as intake_mod
    from api.durable_fs import durable_unlink as real_unlink

    calls = []

    def spy_unlink(path):
        calls.append(Path(path).name)
        return real_unlink(path)

    monkeypatch.setattr(intake_mod, "durable_unlink", spy_unlink)
    failures = rollback_published_intake(manifest, tmp_path)

    assert failures == []
    assert set(calls) == {
        "report.txt",
        f"{prepared.doc_id}.txt",
        f"{prepared.doc_id}.meta.yaml",
    }


def test_rollback_published_intake_unlink_failure_is_failure(tmp_path, monkeypatch):
    """P1-1: 耐久删除(含父目录 fsync)失败 → 计入 failures,失败关闭。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    publish_upload_intake(manifest, staged, prepared, tmp_path)

    import api.upload_intake as intake_mod

    def boom(path):
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(intake_mod, "durable_unlink", boom)
    failures = rollback_published_intake(manifest, tmp_path)

    assert set(failures) == {
        "originals/report.txt",
        f"raw/{prepared.doc_id}.txt",
        f"raw/{prepared.doc_id}.meta.yaml",
    }


# ---------------------------------------------------------------------------
# 不安全 journal 路径: 失败关闭, 绝不删除
# ---------------------------------------------------------------------------


def test_rollback_refuses_raw_path_not_matching_doc_id(tmp_path):
    """raw/evil.txt 不等于本 doc_id 重算目标: 拒绝并保留文件(同 Task 4)。"""
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    evil = tmp_path / "raw" / "evil.txt"
    evil.parent.mkdir(parents=True, exist_ok=True)
    evil.write_bytes(b"evil")
    write_journal(manifest.job_dir, [
        {"path": "raw/evil.txt", "created_by_this_request": True}
    ])
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures
    assert evil.read_bytes() == b"evil"


def test_rollback_refuses_nested_original_path(tmp_path):
    """originals/sub/f.pdf 不是 originals/ 直接子文件: 拒绝并保留(同 Task 4)。"""
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    nested = tmp_path / "originals" / "sub" / "f.pdf"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"nested")
    write_journal(manifest.job_dir, [
        {"path": "originals/sub/f.pdf", "created_by_this_request": True}
    ])
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures
    assert nested.read_bytes() == b"nested"


def test_rollback_refuses_path_escape(tmp_path):
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    outside = tmp_path / "wiki" / "index.yaml"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"index")
    write_journal(manifest.job_dir, [
        {"path": "wiki/index.yaml", "created_by_this_request": True}
    ])
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures
    assert outside.read_bytes() == b"index"


def test_rollback_fails_closed_on_unparseable_journal(tmp_path):
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    publish_upload_intake(manifest, staged, prepared, tmp_path)
    (manifest.job_dir / JOURNAL_FILENAME).write_bytes(b"{{{ not yaml: [")
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures
    # 无法证明创建关系: 三个目标全部保留
    assert (tmp_path / "originals" / "report.txt").exists()
    assert (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    assert (tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml").exists()


# ---------------------------------------------------------------------------
# 与 Task 4 恢复的集成: PREPARED-unbound 上传先按 journal 撤销再清理事务
# ---------------------------------------------------------------------------


def test_recovery_prepared_unbound_revokes_journaled_partial_publish(tmp_path):
    """PREPARED 已发布、业务未绑定的崩溃窗口(设计 §12.4):
    recover_transaction 必须先按 intake.yaml 撤销本轮发布, 再清理事务目录。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    preexisting_text = tmp_path / "raw" / f"{prepared.doc_id}.txt"
    preexisting_text.parent.mkdir(parents=True, exist_ok=True)
    preexisting_text.write_bytes(b"preexisting-text")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)

    result = recover_transaction(
        tmp_path, runtime_config(tmp_path), manifest.job_dir,
        reason_code="interrupted", reason_message="service restart",
    )
    assert result.completed is True
    # journal 证明由本请求创建的 original 已撤销; 既有 raw text 不动
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert preexisting_text.read_bytes() == b"preexisting-text"
    # 事务目录(含 journal)已清理
    assert not manifest.job_dir.exists()


# ---------------------------------------------------------------------------
# Codex 修复(F8):journal 路径必须与 Manifest 声明的三项 intake 目标
# 精确一致;任何其他路径(含 originals/ 合法直接子文件)失败关闭、保留文件
# ---------------------------------------------------------------------------


def test_rollback_refuses_journal_path_not_declared_in_manifest(tmp_path):
    """F8: journal 声明 originals/unrelated.pdf(能通过直接子文件重算)
    但不是本事务 Manifest 声明的 original 目标 → 拒绝删除,计入 failures。"""
    manifest, _, _ = prepared_upload_transaction(tmp_path, "report.txt")
    unrelated = tmp_path / "originals" / "unrelated.pdf"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_bytes(b"unrelated")
    write_journal(manifest.job_dir, [
        {"path": "originals/unrelated.pdf", "created_by_this_request": True}
    ])
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == ["originals/unrelated.pdf"]
    assert unrelated.read_bytes() == b"unrelated"


def test_rollback_refuses_journal_path_of_other_doc_raw_target(tmp_path):
    """F8: journal 声明 raw/<本 doc>.txt 之外的既有文件路径(raw/keep.txt
    不是本事务声明目标)→ 拒绝删除。"""
    manifest, _, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    keep = tmp_path / "raw" / "keep.txt"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"keep")
    write_journal(manifest.job_dir, [
        {"path": "raw/keep.txt", "created_by_this_request": True},
        {"path": f"raw/{prepared.doc_id}.txt", "created_by_this_request": True},
    ])
    # 声明目标不存在(本轮未发布): 幂等成功;非声明目标失败关闭
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == ["raw/keep.txt"]
    assert keep.read_bytes() == b"keep"


def test_rollback_declared_targets_still_removed(tmp_path):
    """F8 对照: journal 路径恰为 Manifest 声明目标时行为不变(精确撤销)。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    publish_upload_intake(manifest, staged, prepared, tmp_path)
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.meta.yaml").exists()


# ---------------------------------------------------------------------------
# Codex Round 2 (N1): intent-first journal — 每个目标先落 pending journal
# (耐久),再耐久发布,再翻转为 completed(耐久)。发布成功与 journal 追加
# 之间的崩溃窗口必须仍由 journal 证明归属,rollback 不得留下孤儿发布文件。
# ---------------------------------------------------------------------------


class _InjectedCrash(BaseException):
    """模拟进程崩溃: 不被 except Exception 捕获,保持崩溃时刻现场。"""


def _crash_after_target_publish(monkeypatch, target_suffix):
    """在指定目标(raw text/raw meta)耐久发布完成后、下一条 journal 写入
    之前注入崩溃。只挂 durable_fs 命名边界,不触碰生产开关。"""
    import api.upload_intake as intake_mod

    real_write_bytes = intake_mod.durable_write_bytes
    real_write_yaml = intake_mod.durable_write_yaml
    state = {"published": False}

    def spy_write_bytes(path, payload):
        result = real_write_bytes(path, payload)
        path = Path(path)
        if path.parent.name == "raw" and path.name.endswith(target_suffix):
            state["published"] = True
        return result

    def spy_write_yaml(path, data):
        path = Path(path)
        if path.parent.name == "raw" and path.name.endswith(target_suffix):
            real_write_yaml(path, data)
            state["published"] = True
            return
        if state["published"] and path.name == JOURNAL_FILENAME:
            raise _InjectedCrash("crash after publish before journal")
        real_write_yaml(path, data)

    monkeypatch.setattr(intake_mod, "durable_write_bytes", spy_write_bytes)
    monkeypatch.setattr(intake_mod, "durable_write_yaml", spy_write_yaml)


def test_crash_between_publish_and_journal_leaves_no_orphan(tmp_path, monkeypatch):
    """N1: raw text 已耐久发布但 journal 翻转前崩溃 → journal 中的 pending
    条目仍证明归属, rollback 必须撤销 raw text(不得永久孤儿/阻断未来上传)。"""
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    _crash_after_target_publish(monkeypatch, ".txt")
    with pytest.raises(_InjectedCrash):
        publish_upload_intake(manifest, staged, prepared, tmp_path)

    # 崩溃现场: raw text 已发布, journal 含 pending 条目
    assert (tmp_path / "raw" / f"{prepared.doc_id}.txt").is_file()
    entries = read_journal(manifest.job_dir)
    pending = [e for e in entries if e.get("completed") is not True]
    assert [e["path"] for e in pending] == [f"raw/{prepared.doc_id}.txt"]

    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()


def test_crash_between_pending_journal_and_publish_is_idempotent(
    tmp_path, monkeypatch
):
    """N1: pending journal 落笔后、目标发布前崩溃 → 目标不存在, rollback
    对 pending 条目按幂等成功处理, 仍撤销已发布的 original。"""
    import api.upload_intake as intake_mod

    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    real_write_bytes = intake_mod.durable_write_bytes
    state = {"pending_seen": False}

    def crash_on_raw_text_publish(path, payload):
        path = Path(path)
        if path.parent.name == "raw" and path.suffix == ".txt":
            raise _InjectedCrash("crash before raw text publish")
        return real_write_bytes(path, payload)

    real_write_yaml = intake_mod.durable_write_yaml

    def spy_write_yaml(path, data):
        if Path(path).name == JOURNAL_FILENAME and any(
            isinstance(e, dict) and e.get("completed") is False
            for e in (data.get("entries") or [])
        ):
            state["pending_seen"] = True
        return real_write_yaml(path, data)

    monkeypatch.setattr(intake_mod, "durable_write_bytes", crash_on_raw_text_publish)
    monkeypatch.setattr(intake_mod, "durable_write_yaml", spy_write_yaml)
    with pytest.raises(_InjectedCrash):
        publish_upload_intake(manifest, staged, prepared, tmp_path)

    assert state["pending_seen"] is True
    assert not (tmp_path / "raw" / f"{prepared.doc_id}.txt").exists()
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not (tmp_path / "originals" / "report.txt").exists()


# ---------------------------------------------------------------------------
# Codex Round 2 (N3): stage_upload 必须流式写入, 不得把整个上传体读入内存
# (E004 为 shutil.copyfileobj 流式);暂存仍为同目录临时文件 + fsync +
# os.replace + 父目录 fsync 的耐久合同。
# ---------------------------------------------------------------------------


class ChunkedStream:
    """按固定小块返回数据的流;read(size) 每次最多返回 chunk_size 字节。"""

    def __init__(self, payload: bytes, chunk_size: int = 3):
        self._payload = payload
        self._chunk_size = chunk_size
        self._offset = 0

    def read(self, _size: int = -1) -> bytes:
        if self._offset >= len(self._payload):
            return b""
        end = min(self._offset + self._chunk_size, len(self._payload))
        chunk = self._payload[self._offset:end]
        self._offset = end
        return chunk


def test_stage_upload_streams_without_holding_all_chunks(tmp_path):
    """N3: 暂存不得同时持有全部上传分块(整体内存复制);流式写入时先前
    分块在后续 read 前必须已释放(CPython 引用计数语义)。"""
    import weakref

    payload = b"0123456789ABCDEF"
    registry = []

    class TrackingStream(ChunkedStream):
        def read(self, size: int = -1) -> bytes:
            data = super().read(size)
            if not data:
                return data
            # memoryview 支持 weakref(bytes 不支持),且 join/update/write 兼容
            chunk = memoryview(data)
            alive = sum(1 for ref in registry if ref() is not None)
            # 流式合同: 消费方至多持有 1 个尚未释放的分块(循环变量);
            # 整体缓冲(read 全部 → join)会同时持有全部分块
            assert alive <= 1, (
                f"stage_upload still holds {alive} earlier chunks"
            )
            registry.append(weakref.ref(chunk))
            return chunk

    config = runtime_config(tmp_path)
    upload = type("U", (), {"filename": "report.txt",
                            "file": TrackingStream(payload)})()
    staged = stage_upload(upload, config)
    assert staged.staged_file.read_bytes() == payload
    assert len(registry) > 2  # 确实走了多分块路径


def test_stage_upload_chunked_stream_content_exact(tmp_path):
    """N3 合同: 小块流暂存内容逐字节等于上传体。"""
    payload = b"port streaming payload " * 100
    upload = type("U", (), {
        "filename": "report.txt",
        "file": ChunkedStream(payload, chunk_size=7),
    })()
    config = runtime_config(tmp_path)
    staged = stage_upload(upload, config)
    assert staged.staged_file.read_bytes() == payload
    # 无临时文件残留
    assert list(staged.staging_dir.iterdir()) == [staged.staged_file]


def test_stage_upload_mid_stream_failure_cleans_staging(tmp_path):
    """N3 合同: 流中途失败清理 staging, staging 之外无写入, 无临时残留。"""
    class FailingStream:
        def __init__(self):
            self.calls = 0

        def read(self, _size: int = -1) -> bytes:
            self.calls += 1
            if self.calls > 2:
                raise OSError("simulated stream failure")
            return b"chunk"

    config = runtime_config(tmp_path)
    upload = type("U", (), {"filename": "report.txt", "file": FailingStream()})()
    with pytest.raises(OSError, match="simulated stream failure"):
        stage_upload(upload, config)
    root = config.upload_intake_dir
    assert not root.exists() or list(root.iterdir()) == []
