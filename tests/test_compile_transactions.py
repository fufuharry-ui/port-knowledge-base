"""E005 Task 2: 编译事务 Manifest 模型与持久快照存储测试。

全部测试仅使用 tmp_path 隔离目录,不访问真实知识库目录,不访问网络,
不依赖任何模型 API Key。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from api.compile_transactions import (
    ACTIVE_STATES,
    ALLOWED_TRANSITIONS,
    CompileManifest,
    ManifestIntegrityError,
    ProcessRecord,
    PublishedIntake,
    TransactionKind,
    TransactionState,
    TransactionStateError,
    artifact_paths,
    create_prepared_transaction,
    list_active_manifests,
    list_transaction_dirs,
    load_manifest,
    transition_manifest,
)
from api.durable_fs import sha256_file
from api.runtime_guard import load_compile_runtime_config

DOC_ID = "doc_20260806_001"

EXPECTED_ARTIFACT_PATHS = {
    f"wiki/{DOC_ID}.summary.yaml",
    "wiki/index.yaml",
    f"meta/ontology/{DOC_ID}.ontology.yaml",
    "meta/ontology/global_ontology.yaml",
    f"meta/relations/{DOC_ID}.relations.yaml",
    "meta/relations/knowledge_graph.yaml",
    "meta/ontology/entity_relations.yaml",
}

PREPARED = TransactionState.PREPARED
SCHEDULED = TransactionState.SCHEDULED
RUNNING = TransactionState.RUNNING
COMMITTED = TransactionState.COMMITTED
ROLLBACKING = TransactionState.ROLLBACKING
ROLLED_BACK = TransactionState.ROLLED_BACK


def runtime_config(base):
    return load_compile_runtime_config(base, env={})


def seed_business_tree(base, doc_id=DOC_ID):
    for rel in EXPECTED_ARTIFACT_PATHS:
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"payload::{rel}".encode("utf-8"))


def prepared_manifest(tmp_path, *, seed=True, previous_meta=None, kind=TransactionKind.RECOMPILE):
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
    )


def read_manifest_yaml(job_dir):
    return yaml.safe_load((job_dir / "manifest.yaml").read_text(encoding="utf-8"))


def _running_process_record():
    """R5-P1-1: RUNNING 迁移必须携带进程记录(状态机不变量)。"""
    return ProcessRecord(
        pid=12345,
        create_time=1786007401.25,
        executable="C:/Python312/python.exe",
        cwd="D:/repo",
        command_fingerprint=f"scripts.compile|{DOC_ID}",
        process_group_id=12345,
        platform="windows",
    )


def rewrite_manifest_yaml(job_dir, data):
    (job_dir / "manifest.yaml").write_text(
        yaml.dump(data, allow_unicode=True), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 创建与快照
# ---------------------------------------------------------------------------


def test_create_prepared_transaction_snapshots_exact_seven_paths(tmp_path):
    manifest = create_prepared_transaction(
        base_dir=tmp_path,
        config=runtime_config(tmp_path),
        doc_id=DOC_ID,
        kind=TransactionKind.RECOMPILE,
        previous_meta={"id": DOC_ID, "status": "compiled"},
    )
    assert manifest.state is TransactionState.PREPARED
    assert len(manifest.artifacts) == 7
    assert {item.path for item in manifest.artifacts} == EXPECTED_ARTIFACT_PATHS


def test_snapshots_capture_bytes_size_and_sha256(tmp_path):
    seed_business_tree(tmp_path)
    manifest = prepared_manifest(tmp_path)
    for record in manifest.artifacts:
        assert record.existed is True
        assert record.snapshot == f"snapshots/{record.slot:02d}.bin"
        snapshot_file = manifest.job_dir / record.snapshot
        original = tmp_path / record.path
        assert snapshot_file.is_file()
        assert snapshot_file.read_bytes() == original.read_bytes()
        assert record.snapshot_size == len(original.read_bytes())
        assert record.snapshot_sha256 == sha256_file(snapshot_file)
        assert record.original_sha256 == sha256_file(original)


def test_missing_artifacts_recorded_without_snapshot(tmp_path):
    manifest = prepared_manifest(tmp_path, seed=False)
    assert len(manifest.artifacts) == 7
    for record in manifest.artifacts:
        assert record.existed is False
        assert record.snapshot is None
        assert record.snapshot_size is None
        assert record.snapshot_sha256 is None
        assert record.original_sha256 is None
    assert not any((manifest.job_dir / "snapshots").iterdir())


def test_create_prepared_transaction_creates_transaction_root_durably(
    tmp_path, monkeypatch
):
    """R5-P1-2: 事务根目录链必须经 durable_makedirs 耐久创建(新建层
    父目录 fsync),不得裸 mkdir(parents=True)。"""
    import api.compile_transactions as transactions

    calls = []
    real = transactions.durable_makedirs

    def spy(path, *args, **kwargs):
        calls.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(transactions, "durable_makedirs", spy)
    config = runtime_config(tmp_path)
    manifest = prepared_manifest(tmp_path)
    assert calls == [config.transaction_dir]
    assert manifest.job_dir.is_dir()


def test_formal_directory_published_and_staging_removed(tmp_path):
    seed_business_tree(tmp_path)
    config = runtime_config(tmp_path)
    manifest = prepared_manifest(tmp_path)
    assert manifest.job_dir == config.transaction_dir / manifest.job_id
    assert manifest.job_dir.is_dir()
    assert not (config.transaction_dir / f".staging-{manifest.job_id}").exists()
    assert (manifest.job_dir / "manifest.yaml").is_file()


def test_job_ids_are_unique_and_time_ordered(tmp_path):
    seed_business_tree(tmp_path)
    first = prepared_manifest(tmp_path)
    second = prepared_manifest(tmp_path)
    assert first.job_id != second.job_id
    assert first.job_id < second.job_id


def test_previous_meta_saved_durably(tmp_path):
    meta = {"id": DOC_ID, "status": "compiled", "title": "旧文档"}
    seed_business_tree(tmp_path)
    manifest = prepared_manifest(tmp_path, previous_meta=meta)
    saved = yaml.safe_load(
        (manifest.job_dir / "source-meta-before.yaml").read_text(encoding="utf-8")
    )
    assert saved == meta
    assert manifest.previous_document_status == "compiled"


def test_upload_transaction_records_published_intake(tmp_path):
    seed_business_tree(tmp_path)
    intake = PublishedIntake(
        original_path="originals/example.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=True,
    )
    manifest = create_prepared_transaction(
        base_dir=tmp_path,
        config=runtime_config(tmp_path),
        doc_id=DOC_ID,
        kind=TransactionKind.UPLOAD,
        previous_meta=None,
        published_intake=intake,
    )
    loaded = load_manifest(manifest.job_dir)
    assert loaded.kind is TransactionKind.UPLOAD
    assert loaded.published_intake == intake
    assert loaded.previous_document_status is None


def test_manifest_roundtrip_through_load(tmp_path):
    seed_business_tree(tmp_path)
    manifest = prepared_manifest(tmp_path)
    loaded = load_manifest(manifest.job_dir)
    assert loaded == manifest
    data = read_manifest_yaml(manifest.job_dir)
    assert data["schema_version"] == 1
    assert data["state"] == "PREPARED"
    assert data["kind"] == "recompile"


def test_staging_cleanup_on_failure_leaves_business_state_untouched(
    tmp_path, monkeypatch
):
    seed_business_tree(tmp_path)
    before = {
        rel: sha256_file(tmp_path / rel) for rel in sorted(EXPECTED_ARTIFACT_PATHS)
    }
    config = runtime_config(tmp_path)

    import api.compile_transactions as transactions

    def boom(*args, **kwargs):
        raise OSError("simulated durable write failure")

    monkeypatch.setattr(transactions, "durable_write_yaml", boom)
    with pytest.raises(OSError, match="simulated durable write failure"):
        create_prepared_transaction(
            base_dir=tmp_path,
            config=config,
            doc_id=DOC_ID,
            kind=TransactionKind.RECOMPILE,
            previous_meta={"id": DOC_ID, "status": "compiled"},
        )
    assert list(config.transaction_dir.iterdir()) == []
    after = {
        rel: sha256_file(tmp_path / rel) for rel in sorted(EXPECTED_ARTIFACT_PATHS)
    }
    assert after == before


# ---------------------------------------------------------------------------
# 状态机
# ---------------------------------------------------------------------------


def test_active_states_and_allowed_transitions_contract():
    assert ACTIVE_STATES == frozenset({PREPARED, SCHEDULED, RUNNING, ROLLBACKING})
    assert ALLOWED_TRANSITIONS == {
        PREPARED: {SCHEDULED, ROLLBACKING},
        SCHEDULED: {RUNNING, ROLLBACKING},
        RUNNING: {COMMITTED, ROLLBACKING},
        ROLLBACKING: {ROLLED_BACK},
        COMMITTED: set(),
        ROLLED_BACK: set(),
    }


def test_transition_rejects_wrong_expected_state(tmp_path):
    manifest = prepared_manifest(tmp_path)
    with pytest.raises(TransactionStateError):
        transition_manifest(
            manifest.job_dir,
            expected=TransactionState.RUNNING,
            target=TransactionState.COMMITTED,
        )


def test_transition_full_allowed_chain(tmp_path):
    manifest = prepared_manifest(tmp_path)
    updated = transition_manifest(
        manifest.job_dir,
        expected=PREPARED,
        target=SCHEDULED,
        scheduled_at="2026-08-06T14:30:01+00:00",
    )
    assert updated.state is SCHEDULED
    updated = transition_manifest(
        manifest.job_dir,
        expected=SCHEDULED,
        target=RUNNING,
        started_at="2026-08-06T14:30:02+00:00",
        process=ProcessRecord(
            pid=12345,
            create_time=1786007401.25,
            executable="C:/Python312/python.exe",
            cwd="D:/repo",
            command_fingerprint=f"scripts.compile|{DOC_ID}",
            process_group_id=12345,
            platform="windows",
        ),
    )
    assert updated.state is RUNNING
    assert updated.process is not None
    assert updated.process.pid == 12345
    updated = transition_manifest(
        manifest.job_dir, expected=RUNNING, target=COMMITTED
    )
    assert updated.state is COMMITTED
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is COMMITTED
    assert reloaded.process is not None
    assert reloaded.process.command_fingerprint == f"scripts.compile|{DOC_ID}"


@pytest.mark.parametrize("target", [PREPARED, RUNNING, COMMITTED, ROLLED_BACK])
def test_transition_rejects_disallowed_targets(tmp_path, target):
    manifest = prepared_manifest(tmp_path)
    with pytest.raises(TransactionStateError):
        transition_manifest(manifest.job_dir, expected=PREPARED, target=target)


def test_transition_from_terminal_state_rejected(tmp_path):
    manifest = prepared_manifest(tmp_path)
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED)
    transition_manifest(manifest.job_dir, expected=SCHEDULED, target=RUNNING,
                        process=_running_process_record())
    transition_manifest(manifest.job_dir, expected=RUNNING, target=COMMITTED)
    with pytest.raises(TransactionStateError):
        transition_manifest(manifest.job_dir, expected=COMMITTED, target=ROLLBACKING)


def test_transition_rollback_chain(tmp_path):
    manifest = prepared_manifest(tmp_path)
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED)
    transition_manifest(manifest.job_dir, expected=SCHEDULED, target=RUNNING,
                        process=_running_process_record())
    updated = transition_manifest(
        manifest.job_dir,
        expected=RUNNING,
        target=ROLLBACKING,
        failure={"original_code": "timeout", "original_message": "hard timeout"},
    )
    assert updated.state is ROLLBACKING
    assert updated.failure["original_code"] == "timeout"
    updated = transition_manifest(
        manifest.job_dir, expected=ROLLBACKING, target=ROLLED_BACK
    )
    assert updated.state is ROLLED_BACK


def test_transition_rejects_unknown_change_field(tmp_path):
    manifest = prepared_manifest(tmp_path)
    with pytest.raises(TransactionStateError):
        transition_manifest(
            manifest.job_dir,
            expected=PREPARED,
            target=SCHEDULED,
            doc_id="doc_20990101_999",
        )


# ---------------------------------------------------------------------------
# Manifest 完整性与安全校验
# ---------------------------------------------------------------------------


def test_load_manifest_rejects_unknown_schema_version(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["schema_version"] = 2
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_unknown_state(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["state"] = "EXPLODED"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_unknown_top_level_key(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["unexpected"] = "value"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_unknown_artifact_key(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["artifacts"][0]["backdoor"] = True
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_unknown_process_key(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["process"] = {
        "pid": 1,
        "create_time": 1.0,
        "executable": "x",
        "cwd": "y",
        "command_fingerprint": "z",
        "process_group_id": 1,
        "platform": "windows",
        "extra": "nope",
    }
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_unknown_published_intake_key(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["published_intake"] = {
        "original_path": "originals/a.pdf",
        "raw_text_path": f"raw/{DOC_ID}.txt",
        "raw_meta_path": f"raw/{DOC_ID}.meta.yaml",
        "published": False,
        "extra": "nope",
    }
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


# ---------------------------------------------------------------------------
# Codex Round 2 (N2): 进程身份数字必须为正——pid/process_group_id 为 0 或
# 负数时, POSIX killpg 恢复路径会把信号发向恢复进程自身进程组(0)或误解
# 负值;Manifest 加载必须失败关闭(ManifestIntegrityError),绝不 kill。
# ---------------------------------------------------------------------------


def _manifest_with_process(data, **process_overrides):
    process = {
        "pid": 4321,
        "create_time": 1786007401.25,
        "executable": "python",
        "cwd": "D:/repo",
        "command_fingerprint": f"scripts.compile|{DOC_ID}",
        "process_group_id": 4321,
        "platform": "posix",
    }
    process.update(process_overrides)
    data["process"] = process
    return data


# ---------------------------------------------------------------------------
# Codex Round 5 (R5-P1-1): RUNNING ⇒ 必须携带进程记录(状态机不变量——
# RUNNING 只能连同进程身份一起写入);RUNNING + process=null 是损坏/不兼容
# 证据,加载必须失败关闭(ManifestIntegrityError),绝不按"无遗留进程"回滚。
# ---------------------------------------------------------------------------


def test_load_manifest_rejects_running_state_without_process(tmp_path):
    """R5-P1-1(a): state=RUNNING 且 process=null → ManifestIntegrityError。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["state"] = "RUNNING"
    data["process"] = None
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_accepts_rollbacking_without_process(tmp_path):
    """R5-P1-1 对照: ROLLBACKING + process=null 合法(SCHEDULED→ROLLBACKING
    的 spawn 失败路径不携带进程记录),不得被不变量误伤。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["state"] = "ROLLBACKING"
    data["process"] = None
    rewrite_manifest_yaml(manifest.job_dir, data)
    loaded = load_manifest(manifest.job_dir)
    assert loaded.state is ROLLBACKING
    assert loaded.process is None


# ---------------------------------------------------------------------------
# Codex Round 6 (R6-P2-1): UPLOAD 事务的 intake 不变量——
# published_intake=null 对上传事务绝不合法(所有状态);PREPARED 之后的
# 状态必须 published=True(发布完成才迁移 SCHEDULED)。违反即损坏证据,
# 加载失败关闭,绝不空转回滚删除证据而搁浅已上传文件。
# ---------------------------------------------------------------------------


def _upload_manifest_data(tmp_path, *, published):
    intake = PublishedIntake(
        original_path="originals/example.pdf",
        raw_text_path=f"raw/{DOC_ID}.txt",
        raw_meta_path=f"raw/{DOC_ID}.meta.yaml",
        published=published,
    )
    manifest = create_prepared_transaction(
        base_dir=tmp_path,
        config=runtime_config(tmp_path),
        doc_id=DOC_ID,
        kind=TransactionKind.UPLOAD,
        previous_meta=None,
        published_intake=intake,
    )
    return manifest, read_manifest_yaml(manifest.job_dir)


def test_load_manifest_rejects_upload_with_null_intake(tmp_path):
    """R6-P2-1: kind=upload + published_intake=null → ManifestIntegrityError。"""
    manifest, data = _upload_manifest_data(tmp_path, published=True)
    data["published_intake"] = None
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


@pytest.mark.parametrize(
    "state", ["SCHEDULED", "ROLLBACKING", "ROLLED_BACK", "COMMITTED"]
)
def test_load_manifest_rejects_upload_unpublished_past_prepared(tmp_path, state):
    """R6-P2-1: 上传事务 PREPARED 之后的状态携带 published=false →
    ManifestIntegrityError(失败关闭)。"""
    manifest, data = _upload_manifest_data(tmp_path, published=False)
    data["state"] = state
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_upload_running_unpublished(tmp_path):
    """R6-P2-1: RUNNING + published=false(携带合法进程记录以隔离
    intake 规则)→ ManifestIntegrityError。"""
    manifest, data = _upload_manifest_data(tmp_path, published=False)
    data["state"] = "RUNNING"
    data["process"] = {
        "pid": 4321,
        "create_time": 1786007401.25,
        "executable": "python",
        "cwd": "D:/repo",
        "command_fingerprint": f"scripts.compile|{DOC_ID}",
        "process_group_id": 4321,
        "platform": "windows",
    }
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


@pytest.mark.parametrize("published", [False, True])
def test_load_manifest_accepts_upload_prepared_intake_states(tmp_path, published):
    """R6-P2-1 对照: PREPARED 下 published=false(发布前)与 true
    (发布后绑定前窗口)均合法。"""
    manifest, _data = _upload_manifest_data(tmp_path, published=published)
    loaded = load_manifest(manifest.job_dir)
    assert loaded.kind is TransactionKind.UPLOAD
    assert loaded.published_intake.published is published


# ---------------------------------------------------------------------------
# Codex Round 6 (R6-P1-2): create_time 必须有限且为正——nan 使
# abs(actual - nan) > tolerance 恒为 False,create_time 校验被静默绕过,
# PID 复用的新编译器会被误杀(与 N2 非正 pid 同一防御模式)。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_create_time",
    [float("nan"), float("inf"), float("-inf"), 0, -1],
)
def test_load_manifest_rejects_invalid_create_time(tmp_path, bad_create_time):
    """R6-P1-2(a): 非有限/非正 create_time → ManifestIntegrityError。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    rewrite_manifest_yaml(
        manifest.job_dir,
        _manifest_with_process(data, create_time=bad_create_time),
    )
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


@pytest.mark.parametrize("bad_pid", [0, -1, -9999])
def test_load_manifest_rejects_nonpositive_pid(tmp_path, bad_pid):
    """N2: 非正 pid 一律 ManifestIntegrityError(失败关闭)。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    rewrite_manifest_yaml(
        manifest.job_dir, _manifest_with_process(data, pid=bad_pid)
    )
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


@pytest.mark.parametrize("bad_pgid", [0, -1, -9999])
def test_load_manifest_rejects_nonpositive_process_group_id(tmp_path, bad_pgid):
    """N2: 非正 process_group_id 一律 ManifestIntegrityError(失败关闭);
    0 在 POSIX 下正是恢复进程自身的进程组。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    rewrite_manifest_yaml(
        manifest.job_dir, _manifest_with_process(data, process_group_id=bad_pgid)
    )
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_accepts_null_process_group_id(tmp_path):
    """N2 对照: process_group_id=null(Windows 语义)仍然合法。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    rewrite_manifest_yaml(
        manifest.job_dir, _manifest_with_process(data, process_group_id=None)
    )
    loaded = load_manifest(manifest.job_dir)
    assert loaded.process is not None
    assert loaded.process.process_group_id is None


# ---------------------------------------------------------------------------
# Codex Round 2 (N7): durable_publish_directory 内部 rename 完成后、
# 父目录 fsync 失败时,异常处理必须一并移除本调用已发布的正式事务目录,
# 绝不留下无归属 PREPARED 孤儿(原请求 503 + 后续变更全部 busy)。
# ---------------------------------------------------------------------------


def test_create_failure_after_directory_publish_removes_final_dir(
    tmp_path, monkeypatch
):
    """N7: rename 已完成但发布后续步骤失败 → staging 与 final_dir 均移除。"""
    seed_business_tree(tmp_path)
    config = runtime_config(tmp_path)

    import api.compile_transactions as transactions

    real_publish = transactions.durable_publish_directory

    def publish_then_fail(staging, target):
        real_publish(staging, target)  # rename 已完成
        raise OSError("simulated post-rename fsync failure")

    monkeypatch.setattr(
        transactions, "durable_publish_directory", publish_then_fail
    )
    with pytest.raises(OSError, match="post-rename fsync failure"):
        create_prepared_transaction(
            base_dir=tmp_path,
            config=config,
            doc_id=DOC_ID,
            kind=TransactionKind.RECOMPILE,
            previous_meta={"id": DOC_ID, "status": "compiled"},
        )
    # staging 与已发布的正式事务目录都不残留
    assert list(config.transaction_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Codex Round 2 (N6): cleanup_verified 终态清理旗标的 Manifest 合同
# ---------------------------------------------------------------------------


def test_manifest_cleanup_verified_roundtrip(tmp_path):
    """N6: cleanup_verified 缺省为 False;为 True 时可严格回读。"""
    manifest = prepared_manifest(tmp_path)
    loaded = load_manifest(manifest.job_dir)
    assert loaded.cleanup_verified is False

    data = read_manifest_yaml(manifest.job_dir)
    data["cleanup_verified"] = True
    rewrite_manifest_yaml(manifest.job_dir, data)
    assert load_manifest(manifest.job_dir).cleanup_verified is True


def test_load_manifest_rejects_non_boolean_cleanup_verified(tmp_path):
    """N6: 非布尔 cleanup_verified 失败关闭。"""
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["cleanup_verified"] = "yes"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


@pytest.mark.parametrize(
    "bad_path",
    [
        "../outside.yaml",
        "wiki/../../outside.yaml",
        "..\\outside.yaml",
        "/etc/passwd",
        "C:/Windows/win.ini",
    ],
)
def test_load_manifest_rejects_artifact_path_traversal(tmp_path, bad_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["artifacts"][0]["path"] = bad_path
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_snapshot_path_traversal(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["artifacts"][0]["snapshot"] = "snapshots/../../escape.bin"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_artifact_paths_outside_whitelist(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["artifacts"][0]["path"] = "wiki/other.summary.yaml"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_corrupted_snapshot(tmp_path):
    seed_business_tree(tmp_path)
    manifest = prepared_manifest(tmp_path)
    snapshot_file = manifest.job_dir / "snapshots" / "00.bin"
    payload = bytearray(snapshot_file.read_bytes())
    payload[0] ^= 0xFF
    snapshot_file.write_bytes(bytes(payload))
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_missing_snapshot_file(tmp_path):
    seed_business_tree(tmp_path)
    manifest = prepared_manifest(tmp_path)
    (manifest.job_dir / "snapshots" / "00.bin").unlink()
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


def test_load_manifest_rejects_tampered_doc_id(tmp_path):
    manifest = prepared_manifest(tmp_path)
    data = read_manifest_yaml(manifest.job_dir)
    data["doc_id"] = "../evil"
    rewrite_manifest_yaml(manifest.job_dir, data)
    with pytest.raises(ManifestIntegrityError):
        load_manifest(manifest.job_dir)


# ---------------------------------------------------------------------------
# 事务目录扫描
# ---------------------------------------------------------------------------


def test_list_transaction_dirs_excludes_staging(tmp_path):
    config = runtime_config(tmp_path)
    config.transaction_dir.mkdir(parents=True)
    (config.transaction_dir / ".staging-orphan").mkdir()
    (config.transaction_dir / "stray-file.txt").write_text("x", encoding="utf-8")
    manifest = prepared_manifest(tmp_path)
    assert list_transaction_dirs(config) == [manifest.job_dir]


def test_list_transaction_dirs_excludes_cleanup_quarantine(tmp_path):
    """Codex R3 P2-1: .cleanup-* 隔离区残留绝不视为事务目录(半删除现场
    不得成为 manifest-integrity blocker)。"""
    config = runtime_config(tmp_path)
    config.transaction_dir.mkdir(parents=True)
    residue = config.transaction_dir / ".cleanup-20260806T000000000000Z-deadbeef"
    residue.mkdir()
    (residue / "snapshots").mkdir()  # 半删除现场: manifest 已消失
    manifest = prepared_manifest(tmp_path)
    assert list_transaction_dirs(config) == [manifest.job_dir]


def test_list_transaction_dirs_empty_when_no_transactions(tmp_path):
    assert list_transaction_dirs(runtime_config(tmp_path)) == []


def test_list_active_manifests_detects_multiple_active(tmp_path):
    seed_business_tree(tmp_path)
    first = prepared_manifest(tmp_path)
    second = prepared_manifest(tmp_path)
    active = list_active_manifests(runtime_config(tmp_path))
    assert {m.job_id for m in active} == {first.job_id, second.job_id}
    assert len(active) == 2

    transition_manifest(first.job_dir, expected=PREPARED, target=SCHEDULED)
    transition_manifest(first.job_dir, expected=SCHEDULED, target=RUNNING,
                        process=_running_process_record())
    transition_manifest(first.job_dir, expected=RUNNING, target=COMMITTED)
    active = list_active_manifests(runtime_config(tmp_path))
    assert [m.job_id for m in active] == [second.job_id]

    transition_manifest(second.job_dir, expected=PREPARED, target=ROLLBACKING)
    transition_manifest(second.job_dir, expected=ROLLBACKING, target=ROLLED_BACK)
    assert list_active_manifests(runtime_config(tmp_path)) == []


# ---------------------------------------------------------------------------
# compile_jobs 兼容再导出
# ---------------------------------------------------------------------------


def test_artifact_paths_reexported_from_compile_jobs(tmp_path):
    from api.compile_jobs import artifact_paths as legacy_artifact_paths

    assert legacy_artifact_paths is artifact_paths
    paths = legacy_artifact_paths(tmp_path, DOC_ID)
    assert len(paths) == 7
    assert {p.relative_to(tmp_path).as_posix() for p in paths} == EXPECTED_ARTIFACT_PATHS


# ---------------------------------------------------------------------------
# 恢复协调(E005 Task 4 增补)
# ---------------------------------------------------------------------------


def _bind_meta_compiling(base, job_id, doc_id=DOC_ID):
    raw_dir = base / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{doc_id}.meta.yaml").write_text(
        yaml.dump(
            {"id": doc_id, "status": "compiling", "compile_job_id": job_id},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )


def test_recover_transaction_reports_already_terminal_for_committed(tmp_path):
    from api.compile_transactions import recover_transaction

    manifest = prepared_manifest(tmp_path)
    transition_manifest(manifest.job_dir, expected=PREPARED, target=SCHEDULED)
    transition_manifest(manifest.job_dir, expected=SCHEDULED, target=RUNNING,
                        process=_running_process_record())
    transition_manifest(manifest.job_dir, expected=RUNNING, target=COMMITTED)
    result = recover_transaction(
        tmp_path,
        runtime_config(tmp_path),
        manifest.job_dir,
        reason_code="interrupted",
        reason_message="service restart",
    )
    assert result.already_terminal is True
    assert result.completed is False
    assert result.blocked is False
    assert load_manifest(manifest.job_dir).state is COMMITTED


def test_rollback_failure_keeps_rollbacking_and_records_failed_paths(
    tmp_path, monkeypatch
):
    from api.compile_transactions import recover_transaction

    manifest = prepared_manifest(tmp_path)
    _bind_meta_compiling(tmp_path, manifest.job_id)
    transition_manifest(
        manifest.job_dir,
        expected=PREPARED,
        target=ROLLBACKING,
        failure={"original_code": "interrupted", "original_message": "restart"},
    )
    (tmp_path / "wiki" / f"{DOC_ID}.summary.yaml").write_bytes(b"garbage")

    import api.compile_transactions as transactions

    def boom(*args, **kwargs):
        raise OSError("simulated restore write failure")

    monkeypatch.setattr(transactions, "durable_write_bytes", boom)
    first = recover_transaction(
        tmp_path,
        runtime_config(tmp_path),
        manifest.job_dir,
        reason_code="interrupted",
        reason_message="service restart",
    )
    assert first.blocked is True
    assert first.failed_paths
    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    assert reloaded.recovery["failed_paths"]
    assert reloaded.recovery["last_error"]
    # 原始失败原因被保留,不被恢复错误覆盖
    assert reloaded.failure["original_code"] == "interrupted"

    monkeypatch.undo()
    second = recover_transaction(
        tmp_path,
        runtime_config(tmp_path),
        manifest.job_dir,
        reason_code="interrupted",
        reason_message="service restart",
    )
    assert second.completed is True
    assert load_manifest(manifest.job_dir).state is ROLLED_BACK
