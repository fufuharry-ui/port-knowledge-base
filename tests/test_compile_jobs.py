"""E005 Task 5: 持久化编译执行器与硬超时测试。

对应设计文档第 13 节(执行器与进程身份)、第 14 节(硬超时)、
第 15 节(唯一提交点与成功验证)和第 16 节(幂等回滚编排)。

全部测试仅使用 tmp_path 隔离仓库;进程边界(spawn/wait/terminate)
经 monkeypatch 注入假进程,绝不启动真实编译进程、不访问网络、
不访问真实知识库目录、不依赖模型 Key。
"""
from __future__ import annotations

import threading
import time

import pytest
import yaml

import api.compile_jobs as compile_jobs
from api.compile_jobs import (
    classify_compile_error,
    prepare_recompile_transaction,
    run_compile_task,
    sanitize_compile_error,
)
from api.compile_transactions import (
    RecoveryResult,
    TransactionKind,
    TransactionState,
    load_manifest,
    transition_manifest,
)
from api.process_tree import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SURVIVORS_REMAINING,
    STATUS_TERMINATED,
    STATUS_TIMED_OUT,
    ProcessIdentity,
    ProcessResult,
    SpawnedProcess,
    TerminationResult,
)
from api.runtime_guard import ServiceReadiness, load_compile_runtime_config
from scripts.doc_admin import bind_doc_compile_job, read_doc_meta

DOC_ID = "doc_20260806_100"
DOC_B_ID = "doc_20260806_101"

PREPARED = TransactionState.PREPARED
SCHEDULED = TransactionState.SCHEDULED
RUNNING = TransactionState.RUNNING
COMMITTED = TransactionState.COMMITTED
ROLLBACKING = TransactionState.ROLLBACKING

ACTIVE_JOB_FIELDS = ("compile_job_id", "compile_started_at", "compile_deadline")

#: 假进程 pid: Windows pid 恒为 4 的倍数(奇数不可能存在),POSIX 默认
#: pid_max=2^22(该值越界);恢复路径的真实 psutil 查询必然判定 process_gone。
FAKE_PID = 2**22 + 54321

STARTED_AT = "2026-08-06T14:30:01+00:00"
DEADLINE = "2026-08-06T15:00:01+00:00"


def _artifact_rel_paths(doc_id):
    return (
        f"wiki/{doc_id}.summary.yaml",
        "wiki/index.yaml",
        f"meta/ontology/{doc_id}.ontology.yaml",
        "meta/ontology/global_ontology.yaml",
        f"meta/relations/{doc_id}.relations.yaml",
        "meta/relations/knowledge_graph.yaml",
        "meta/ontology/entity_relations.yaml",
    )


def runtime_config(base):
    return load_compile_runtime_config(base, env={})


def write_doc_meta(base, doc_id, meta):
    raw_dir = base / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{doc_id}.meta.yaml").write_text(
        yaml.dump(meta, allow_unicode=True), encoding="utf-8"
    )


def seed_compiled_business_tree(base, doc_id=DOC_ID):
    """事务前已编译状态: 七项产物存在且内容可区分, meta=compiled。"""
    payloads = {}
    for rel in _artifact_rel_paths(doc_id):
        path = base / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = f"old::{rel}\r\n".encode("utf-8")
        path.write_bytes(payload)
        payloads[rel] = payload
    write_doc_meta(base, doc_id, {"id": doc_id, "status": "compiled"})
    return payloads


def write_valid_compile_outputs(base, doc_id):
    """模拟成功子进程: 写入语义合法的七项产物并把 meta 置为 compiled。

    index 保留既有条目(模拟 compile.py 的合并行为),只替换本文档条目。
    """
    (base / "wiki" / f"{doc_id}.summary.yaml").write_text(
        yaml.dump({"doc_id": doc_id, "abstract": "a"}), encoding="utf-8"
    )
    (base / "meta" / "ontology" / f"{doc_id}.ontology.yaml").write_text(
        yaml.dump({"doc_id": doc_id, "keywords": []}), encoding="utf-8"
    )
    index_path = base / "wiki" / "index.yaml"
    try:
        existing = yaml.safe_load(index_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        existing = None
    documents = []
    if isinstance(existing, dict) and isinstance(existing.get("documents"), list):
        documents = [
            entry
            for entry in existing["documents"]
            if isinstance(entry, dict) and entry.get("id") != doc_id
        ]
    documents.append({"id": doc_id, "title": "t"})
    index_path.write_text(
        yaml.dump({"documents": documents}), encoding="utf-8"
    )
    (base / "meta" / "relations" / f"{doc_id}.relations.yaml").write_text(
        yaml.dump({"doc_id": doc_id, "relations": []}), encoding="utf-8"
    )
    (base / "meta" / "ontology" / "global_ontology.yaml").write_text(
        yaml.dump({"ontology_tree": [], "total_nodes": 0}), encoding="utf-8"
    )
    (base / "meta" / "relations" / "knowledge_graph.yaml").write_text(
        yaml.dump({"edges": []}), encoding="utf-8"
    )
    (base / "meta" / "ontology" / "entity_relations.yaml").write_text(
        yaml.dump({"edges": []}), encoding="utf-8"
    )
    meta = read_doc_meta(doc_id, base)
    meta["status"] = "compiled"
    write_doc_meta(base, doc_id, meta)


def scheduled_transaction(base, doc_id=DOC_ID):
    """构造 SCHEDULED 事务: PREPARED 发布 → meta 绑定 job → Manifest=SCHEDULED。

    返回 (manifest, old_payloads);old_payloads 为事务前七项产物字节。
    """
    old_payloads = seed_compiled_business_tree(base, doc_id)
    manifest = prepare_recompile_transaction(doc_id, base, runtime_config(base))
    assert bind_doc_compile_job(
        doc_id, manifest.job_id, STARTED_AT, DEADLINE, base_dir=base
    )
    transition_manifest(
        manifest.job_dir,
        expected=PREPARED,
        target=SCHEDULED,
        scheduled_at=STARTED_AT,
    )
    return load_manifest(manifest.job_dir), old_payloads


class FakePopen:
    """假 Popen: 支持 communicate/wait/kill,绝不指向真实进程。"""

    def __init__(self, pid=FAKE_PID):
        self.pid = pid
        self.returncode = 0
        self.communicate_calls = 0
        self.wait_calls = 0

    def communicate(self, timeout=None):
        self.communicate_calls += 1
        return ("", "")

    def wait(self, timeout=None):
        self.wait_calls += 1
        return self.returncode

    def kill(self):
        self.returncode = -9


def make_spawned(base, doc_id, pid=FAKE_PID):
    popen = FakePopen(pid=pid)
    identity = ProcessIdentity(
        pid=pid,
        create_time=1786007401.25,
        executable="python",
        cwd=str(base),
        command_fingerprint=f"scripts.compile|{doc_id}",
        process_group_id=pid,
        platform="windows",
    )
    return SpawnedProcess(
        popen=popen,
        identity=identity,
        command_fingerprint=identity.command_fingerprint,
    )


def install_fake_process(
    monkeypatch,
    base,
    doc_id,
    *,
    result=None,
    on_wait=None,
    pid=FAKE_PID,
):
    """把 spawn/wait 边界替换为假进程;on_wait 在等待时模拟子进程写产物。"""
    spawned = make_spawned(base, doc_id, pid=pid)
    monkeypatch.setattr(
        "api.compile_jobs.spawn_compile_process", lambda *args, **kwargs: spawned
    )

    def fake_wait(_spawned, _timeout):
        if on_wait is not None:
            on_wait()
        return result or ProcessResult(
            status=STATUS_COMPLETED, returncode=0, stdout="ok", stderr=""
        )

    monkeypatch.setattr("api.compile_jobs.wait_for_process", fake_wait)
    return spawned


# ---------------------------------------------------------------------------
# sanitize / classify 合同(E004 保留,逐字不动)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("未找到 OPENAI_API_KEY", "llm_configuration"),
        ("401 Unauthorized invalid api key", "llm_configuration"),
        ("Connection refused by provider", "service_unavailable"),
        ("503 Service Unavailable", "service_unavailable"),
        ("500 Internal Server Error", "service_unavailable"),
        ("HTTP status 501", "service_unavailable"),
        ("HTTP status 504", "service_unavailable"),
        ("provider returned 599", "service_unavailable"),
        ("504 Gateway Timeout", "timeout"),
        ("HTTP 400 Bad Request", "compile_failed"),
        ("ReadTimeout request timed out", "timeout"),
        ("找不到原始文本", "document_processing"),
        ("unexpected compiler failure", "compile_failed"),
    ],
)
def test_classify_compile_error(text, expected):
    assert classify_compile_error(text) == expected


def test_sanitize_compile_error_redacts_secrets_and_flattens_lines():
    raw = (
        "Authorization: Bearer token-abc\n"
        "OPENAI_API_KEY=sk-supersecret123\n"
        "https://provider.test/v1?api_key=query-secret&model=x\n"
        "password=hunter2"
    )

    safe = sanitize_compile_error(raw)

    assert "token-abc" not in safe
    assert "sk-supersecret123" not in safe
    assert "query-secret" not in safe
    assert "hunter2" not in safe
    assert "\n" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_limits_to_500_characters():
    safe = sanitize_compile_error("x" * 900)
    assert len(safe) == 500


def test_sanitize_compile_error_redacts_full_authorization_value():
    safe = sanitize_compile_error("Authorization: Basic dXNlcjpwYXNz")
    assert "dXNlcjpwYXNz" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_redacts_env_var_style_secret():
    safe = sanitize_compile_error("OPENAI_API_KEY=plainsecretvalue")
    assert "plainsecretvalue" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_bearer_token_still_fully_redacted():
    safe = sanitize_compile_error("Bearer token-abc")
    assert "token-abc" not in safe
    assert "<redacted>" in safe


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ('{"api_key": "plainsecret-a"}', "plainsecret-a"),
        ("{'x-api-key': 'plainsecret-b'}", "plainsecret-b"),
        ('{"access_token":"plainsecret-c"}', "plainsecret-c"),
        ('{"client_secret": "plainsecret-d"}', "plainsecret-d"),
        ('{"Authorization": "Bearer plainsecret-e"}', "plainsecret-e"),
        ("{'authorization': 'Basic plainsecret-f'}", "plainsecret-f"),
        ('api_key="plainsecret-g"', "plainsecret-g"),
        ("x-api-key='plainsecret-h'", "plainsecret-h"),
    ],
)
def test_sanitize_compile_error_redacts_quoted_credentials(raw, secret):
    """E004-FIX-02:JSON/Python 字典、header 与环境变量形式中带引号的
    凭据键值必须整体脱敏,不能只覆盖无引号键的特例。"""
    safe = sanitize_compile_error(raw)
    assert secret not in safe
    assert "<redacted>" in safe


# ---------------------------------------------------------------------------
# prepare_recompile_transaction: 纯准备(设计 §11 至 PREPARED 发布)
# ---------------------------------------------------------------------------


def test_prepare_recompile_transaction_publishes_prepared_without_binding(tmp_path):
    old_payloads = seed_compiled_business_tree(tmp_path)
    meta_path = tmp_path / "raw" / f"{DOC_ID}.meta.yaml"
    meta_before = meta_path.read_bytes()

    manifest = prepare_recompile_transaction(
        DOC_ID, tmp_path, runtime_config(tmp_path)
    )

    assert manifest.state is PREPARED
    assert manifest.kind is TransactionKind.RECOMPILE
    assert manifest.doc_id == DOC_ID
    assert manifest.previous_document_status == "compiled"
    assert len(manifest.artifacts) == 7
    # 原 meta 已保存到事务目录
    source_meta = yaml.safe_load(
        (manifest.job_dir / "source-meta-before.yaml").read_text(encoding="utf-8")
    )
    assert source_meta["status"] == "compiled"
    # meta 字节不变且未绑定任何 job 字段
    assert meta_path.read_bytes() == meta_before
    meta = read_doc_meta(DOC_ID, tmp_path)
    assert meta["status"] == "compiled"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    # 快照内容与原产物一致
    for record in manifest.artifacts:
        assert record.existed is True
        snapshot = manifest.job_dir / record.snapshot
        assert snapshot.read_bytes() == old_payloads[record.path]


def test_prepare_recompile_transaction_failure_leaves_meta_byte_identical(
    tmp_path, monkeypatch
):
    seed_compiled_business_tree(tmp_path)
    meta_path = tmp_path / "raw" / f"{DOC_ID}.meta.yaml"
    meta_before = meta_path.read_bytes()
    config = runtime_config(tmp_path)

    def broken_create(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "api.compile_jobs.create_prepared_transaction", broken_create
    )
    with pytest.raises(OSError):
        prepare_recompile_transaction(DOC_ID, tmp_path, config)

    assert meta_path.read_bytes() == meta_before
    transaction_root = config.transaction_dir
    if transaction_root.exists():
        assert list(transaction_root.iterdir()) == []


# ---------------------------------------------------------------------------
# 唯一提交点(设计 §15)
# ---------------------------------------------------------------------------


def test_run_compile_task_commits_only_after_semantic_validation(
    tmp_path, monkeypatch
):
    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=lambda: write_valid_compile_outputs(tmp_path, manifest.doc_id),
    )
    observed = {}
    real_cleanup = compile_jobs.cleanup_terminal_transactions

    def spy_cleanup(base_dir, config):
        observed["state_at_cleanup"] = load_manifest(manifest.job_dir).state
        return real_cleanup(base_dir, config)

    monkeypatch.setattr(
        "api.compile_jobs.cleanup_terminal_transactions", spy_cleanup
    )

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    assert observed["state_at_cleanup"] is COMMITTED
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "compiled"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    # 终态验证通过,事务目录已清理
    assert not manifest.job_dir.exists()


def test_run_compile_task_never_commits_when_semantic_validation_fails(
    tmp_path, monkeypatch
):
    """rc=0 且 meta=compiled,但 summary doc_id 不匹配 → 不得进入 COMMITTED,
    旧产物逐字节恢复,文档进入 error 终态。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)

    def fake_child():
        write_valid_compile_outputs(tmp_path, manifest.doc_id)
        (tmp_path / "wiki" / f"{manifest.doc_id}.summary.yaml").write_text(
            yaml.dump({"doc_id": "doc_wrong", "abstract": "a"}), encoding="utf-8"
        )

    install_fake_process(monkeypatch, tmp_path, manifest.doc_id, on_wait=fake_child)
    transitions = []
    real_transition = compile_jobs.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        transitions.append(target)
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr("api.compile_jobs.transition_manifest", spy_transition)

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert COMMITTED not in transitions
    assert ROLLBACKING in transitions
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    assert readiness.snapshot()[0] == "ready"
    assert not manifest.job_dir.exists()


def test_run_compile_task_rolls_back_when_returncode_zero_without_compiled_state(
    tmp_path, monkeypatch
):
    """rc=0 但 meta 未进入 compiled → 一律回滚(提交点前崩溃语义)。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)
    index = tmp_path / "wiki" / "index.yaml"

    def fake_child():
        index.write_bytes(b"partial-index")

    install_fake_process(monkeypatch, tmp_path, manifest.doc_id, on_wait=fake_child)

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert index.read_bytes() == old_payloads["wiki/index.yaml"]


def test_run_compile_task_records_deadline_and_process_identity(
    tmp_path, monkeypatch
):
    """设计 §7.3/§13: RUNNING 迁移必须记录 started_at/deadline 与进程身份;
    超时为从 RUNNING 起算的绝对时间,从不延长。"""
    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(status=STATUS_TERMINATED),
    )
    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: RecoveryResult(
            job_id=manifest.job_id, completed=True
        ),
    )

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    running = load_manifest(manifest.job_dir)
    assert running.state is ROLLBACKING
    assert running.started_at is not None
    assert running.deadline is not None
    assert running.deadline > running.started_at
    assert running.process is not None
    assert running.process.pid == FAKE_PID
    assert running.process.command_fingerprint == (
        f"scripts.compile|{manifest.doc_id}"
    )
    assert running.failure["original_code"] == "timeout"


# ---------------------------------------------------------------------------
# 失败分类与回滚(设计 §16、§20)
# ---------------------------------------------------------------------------


def test_run_compile_task_records_configuration_failure(tmp_path, monkeypatch):
    manifest, old_payloads = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_FAILED,
            returncode=1,
            stdout="",
            stderr="RuntimeError: 未找到 OPENAI_API_KEY=sk-secretvalue",
        ),
    )

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "llm_configuration"
    assert "sk-secretvalue" not in meta["error_message"]
    assert len(meta["error_message"]) <= 500
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload


def test_run_compile_task_failed_returncode_restores_artifacts_byte_for_byte(
    tmp_path, monkeypatch
):
    manifest, old_payloads = scheduled_transaction(tmp_path)

    def fake_child():
        for rel in old_payloads:
            (tmp_path / rel).write_bytes(b"partial\n")

    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=fake_child,
        result=ProcessResult(
            status=STATUS_FAILED,
            returncode=1,
            stdout="",
            stderr="503 Service Unavailable",
        ),
    )

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"


def test_run_compile_task_never_persists_quoted_provider_credentials(
    tmp_path, monkeypatch
):
    """E004-FIX-02 + Task 2 审查结转: 带引号凭据在写入 raw meta 与
    Manifest failure 字段前都必须脱敏。"""
    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_FAILED,
            returncode=1,
            stdout="",
            stderr='ProviderError: {"x-api-key": "plainsecret"}',
        ),
    )
    recorded = {}
    real_transition = compile_jobs.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        if target is ROLLBACKING and "failure" in changes:
            recorded["failure"] = dict(changes["failure"])
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr("api.compile_jobs.transition_manifest", spy_transition)

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    assert "failure" in recorded
    message = recorded["failure"]["original_message"]
    assert "plainsecret" not in message
    assert "<redacted>" in message
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert "plainsecret" not in meta["error_message"]
    assert len(meta["error_message"]) <= 500
    assert "\n" not in meta["error_message"]


def test_spawn_failure_recovers_with_classified_sanitized_error(
    tmp_path, monkeypatch
):
    manifest, old_payloads = scheduled_transaction(tmp_path)

    def broken_spawn(*args, **kwargs):
        raise OSError("connection refused token=secret-value")

    monkeypatch.setattr("api.compile_jobs.spawn_compile_process", broken_spawn)

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"
    assert "secret-value" not in meta["error_message"]
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    assert readiness.snapshot()[0] == "ready"


# ---------------------------------------------------------------------------
# 硬超时与进程树终止(设计 §14)
# ---------------------------------------------------------------------------


def test_timeout_terminates_tree_before_restoring(tmp_path, monkeypatch):
    manifest, _old = scheduled_transaction(tmp_path)
    calls = []
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: calls.append("terminate")
        or TerminationResult(status=STATUS_TERMINATED),
    )
    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: calls.append("restore")
        or RecoveryResult(job_id=manifest.job_id, completed=True),
    )

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    assert calls == ["terminate", "restore"]


def test_timeout_full_recovery_restores_old_version(tmp_path, monkeypatch):
    """超时 → ROLLBACKING(reason=timeout)→ 终止 → 确认退出 → 幂等回滚,
    文档 error_code=timeout,旧产物逐字节恢复,Popen 被 reap。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)

    def fake_child():
        for rel in old_payloads:
            (tmp_path / rel).write_bytes(b"partial\n")

    spawned = install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=fake_child,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(status=STATUS_TERMINATED),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "timeout"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    assert readiness.snapshot()[0] == "ready"
    assert spawned.popen.communicate_calls >= 1 or spawned.popen.wait_calls >= 1
    assert not manifest.job_dir.exists()


def test_timeout_transition_failure_terminates_tree_best_effort(
    tmp_path, monkeypatch
):
    """审查修复(Important): 超时后 ROLLBACKING 迁移自身失败(Manifest IO)
    时,必须 best-effort 终止进程树并回收 Popen,失败关闭;绝不让存活的
    编译子进程在 Manifest 停留 RUNNING 时继续改写业务文件。"""
    manifest, _old = scheduled_transaction(tmp_path)

    def fake_child():
        (tmp_path / "wiki" / "index.yaml").write_bytes(b"partial-index")

    spawned = install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=fake_child,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )
    real_transition = compile_jobs.transition_manifest

    def flaky_transition(job_dir, expected, target, **changes):
        if target is ROLLBACKING:
            raise OSError("manifest io failed")
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr("api.compile_jobs.transition_manifest", flaky_transition)
    terminate_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: terminate_calls.append(identity.pid)
        or TerminationResult(status=STATUS_TERMINATED),
    )
    restore_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: restore_calls.append(args),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    # best-effort 终止已尝试且 Popen 已回收
    assert terminate_calls == [FAKE_PID]
    assert spawned.popen.communicate_calls >= 1 or spawned.popen.wait_calls >= 1
    # 失败关闭: readiness 进入 recovery_required,不回滚、不写文档终态
    assert readiness.snapshot()[0] == "recovery_required"
    assert restore_calls == []
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "compiling"
    assert meta["compile_job_id"] == manifest.job_id
    # Manifest 保持 RUNNING(迁移从未成功),业务文件未被回滚触碰
    assert load_manifest(manifest.job_dir).state is RUNNING
    assert (tmp_path / "wiki" / "index.yaml").read_bytes() == b"partial-index"


def test_timeout_terminate_raise_still_reaps_popen(tmp_path, monkeypatch):
    """审查修复(Minor): terminate_process_tree 抛异常时 Popen 仍必须
    被回收(try/finally),并失败关闭进入 recovery_required。"""
    manifest, _old = scheduled_transaction(tmp_path)
    spawned = install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )

    def broken_terminate(identity, grace_seconds):
        raise OSError("signal delivery failed")

    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree", broken_terminate
    )
    restore_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: restore_calls.append(args),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert spawned.popen.communicate_calls >= 1 or spawned.popen.wait_calls >= 1
    assert readiness.snapshot()[0] == "recovery_required"
    assert restore_calls == []


def test_timeout_unconfirmed_exit_marks_recovery_required_without_restore(
    tmp_path, monkeypatch
):
    """无法确认进程树退出 → 失败关闭: 不回滚、不写文档终态、
    readiness 进入 recovery_required,Manifest 保留 ROLLBACKING 证据。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)

    def fake_child():
        (tmp_path / "wiki" / "index.yaml").write_bytes(b"partial-index")

    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=fake_child,
        result=ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(
            status=STATUS_SURVIVORS_REMAINING, survivors=(9876,)
        ),
    )
    restore_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: restore_calls.append(args),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    mode, reason = readiness.snapshot()
    assert mode == "recovery_required"
    assert reason
    assert restore_calls == []
    # 未回滚: 子进程改写的产物保持改写状态
    assert (tmp_path / "wiki" / "index.yaml").read_bytes() == b"partial-index"
    # 未写文档终态: meta 仍绑定 job
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "compiling"
    assert meta["compile_job_id"] == manifest.job_id
    # Manifest 保留 ROLLBACKING + timeout 证据供启动恢复
    evidence = load_manifest(manifest.job_dir)
    assert evidence.state is ROLLBACKING
    assert evidence.failure["original_code"] == "timeout"


# ---------------------------------------------------------------------------
# 恢复阻塞与基础设施失败 → 失败关闭(设计 §14、§19)
# ---------------------------------------------------------------------------


def test_run_compile_task_marks_recovery_required_when_rollback_blocked(
    tmp_path, monkeypatch
):
    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        result=ProcessResult(
            status=STATUS_FAILED, returncode=1, stdout="", stderr="boom"
        ),
    )

    class _Blocked:
        blocked = True
        completed = False
        already_terminal = False
        reason = "rollback_failed"
        failed_paths = ("wiki/index.yaml",)

    monkeypatch.setattr(
        "api.compile_jobs.restore_transaction_artifacts",
        lambda *args, **kwargs: _Blocked(),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert readiness.snapshot()[0] == "recovery_required"
    # 失败关闭: 不伪造文档终态,Manifest 保留 ROLLBACKING
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "compiling"
    assert load_manifest(manifest.job_dir).state is ROLLBACKING


def test_run_compile_task_corrupt_manifest_marks_recovery_required(
    tmp_path, monkeypatch
):
    manifest, _old = scheduled_transaction(tmp_path)
    (manifest.job_dir / "manifest.yaml").write_text(
        "schema_version: 99\n", encoding="utf-8"
    )
    spawn_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.spawn_compile_process",
        lambda *args, **kwargs: spawn_calls.append(args),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert readiness.snapshot()[0] == "recovery_required"
    assert spawn_calls == []


# ---------------------------------------------------------------------------
# 编排边界: 未知 job、非 SCHEDULED 状态、串行执行
# ---------------------------------------------------------------------------


def test_run_compile_task_missing_transaction_dir_marks_recovery_required(tmp_path):
    """Codex R3 P2-3: well-formed job_id 但事务目录丢失 = 已接受任务丢失
    耐久证据(完整性失败)→ readiness 进入 recovery_required
    (transaction_evidence_missing),业务证据(meta)字节不变,绝不伪造
    终态、绝不静默返回。"""
    seed_compiled_business_tree(tmp_path)
    job_id = "20260806T120000000000Z-deadbeef"
    write_doc_meta(tmp_path, DOC_ID, {
        "id": DOC_ID, "status": "compiling", "compile_job_id": job_id,
    })
    meta_path = tmp_path / "raw" / f"{DOC_ID}.meta.yaml"
    meta_before = meta_path.read_bytes()

    readiness = ServiceReadiness()
    run_compile_task(job_id, tmp_path, runtime_config(tmp_path), readiness)

    mode, reason = readiness.snapshot()
    assert mode == "recovery_required"
    assert reason == "transaction_evidence_missing"
    assert meta_path.read_bytes() == meta_before


def test_run_compile_task_rejects_unsafe_job_id(tmp_path):
    readiness = ServiceReadiness()
    run_compile_task("../escape", tmp_path, runtime_config(tmp_path), readiness)
    assert readiness.snapshot() == ("ready", None)


def test_run_compile_task_non_scheduled_state_never_reexecutes(
    tmp_path, monkeypatch
):
    """PREPARED 已绑定: 按恢复库语义回滚为 interrupted,绝不重新执行。"""
    seed_compiled_business_tree(tmp_path)
    manifest = prepare_recompile_transaction(
        DOC_ID, tmp_path, runtime_config(tmp_path)
    )
    assert bind_doc_compile_job(
        DOC_ID, manifest.job_id, STARTED_AT, DEADLINE, base_dir=tmp_path
    )
    spawn_calls = []
    monkeypatch.setattr(
        "api.compile_jobs.spawn_compile_process",
        lambda *args, **kwargs: spawn_calls.append(args),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert spawn_calls == []
    meta = read_doc_meta(DOC_ID, tmp_path)
    assert meta["status"] == "error"
    assert meta["error_code"] == "interrupted"
    assert readiness.snapshot()[0] == "ready"


def test_run_compile_task_serializes_concurrent_jobs(tmp_path, monkeypatch):
    manifest_a, _ = scheduled_transaction(tmp_path, DOC_ID)
    manifest_b, _ = scheduled_transaction(tmp_path, DOC_B_ID)
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def fake_wait(spawned_process, _timeout):
        nonlocal active, peak
        doc_id = DOC_ID if spawned_process.popen.pid == 1001 else DOC_B_ID
        with state_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        write_valid_compile_outputs(tmp_path, doc_id)
        with state_lock:
            active -= 1
        return ProcessResult(
            status=STATUS_COMPLETED, returncode=0, stdout="ok", stderr=""
        )

    spawned = {
        DOC_ID: make_spawned(tmp_path, DOC_ID, pid=1001),
        DOC_B_ID: make_spawned(tmp_path, DOC_B_ID, pid=1002),
    }
    monkeypatch.setattr(
        "api.compile_jobs.spawn_compile_process",
        lambda _base, doc_id, _env=None: spawned[doc_id],
    )
    monkeypatch.setattr("api.compile_jobs.wait_for_process", fake_wait)

    threads = [
        threading.Thread(
            target=run_compile_task,
            args=(m.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()),
        )
        for m in (manifest_a, manifest_b)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert peak == 1
    for doc_id in (DOC_ID, DOC_B_ID):
        assert read_doc_meta(doc_id, tmp_path)["status"] == "compiled"


# ---------------------------------------------------------------------------
# Codex 修复(F3):RUNNING 迁移失败且退出未确认时,必须持久化可恢复的
# 进程证据(ROLLBACKING + process + failure),供启动恢复先终止再回滚
# ---------------------------------------------------------------------------


def _flaky_transition_raising_on(target_state):
    real_transition = compile_jobs.transition_manifest

    def flaky(job_dir, expected, target, **changes):
        if target is target_state:
            raise OSError(f"manifest io failed on {target_state.value}")
        return real_transition(job_dir, expected, target, **changes)

    return flaky


def test_running_transition_failure_persists_recoverable_process_evidence(
    tmp_path, monkeypatch
):
    """F3(a): RUNNING 迁移失败且 best-effort 终止未确认退出(幸存者)时,
    必须把进程身份与失败原因持久化为 ROLLBACKING 证据;随后启动恢复先验证
    并终止记录进程,再完成回滚。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)

    spawned = install_fake_process(monkeypatch, tmp_path, manifest.doc_id)
    monkeypatch.setattr(
        "api.compile_jobs.transition_manifest",
        _flaky_transition_raising_on(RUNNING),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(
            status=STATUS_SURVIVORS_REMAINING, survivors=(9999,)
        ),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    # 失败关闭: readiness 进入 recovery_required,不回滚、不写文档终态
    assert readiness.snapshot()[0] == "recovery_required"
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "compiling"
    # Popen 已回收
    assert spawned.popen.communicate_calls >= 1 or spawned.popen.wait_calls >= 1
    # 关键合同: Manifest 携带可恢复证据(ROLLBACKING + 进程身份 + 失败原因)
    evidence = load_manifest(manifest.job_dir)
    assert evidence.state is ROLLBACKING
    assert evidence.failure["original_code"] == "running_transition_failed"
    assert evidence.process is not None
    assert evidence.process.pid == FAKE_PID
    assert evidence.process.command_fingerprint == (
        f"scripts.compile|{manifest.doc_id}"
    )

    # 模拟重启: 启动恢复必须先终止记录进程,再逐字节回滚。
    import api.compile_transactions as transactions
    from api.compile_transactions import recover_startup
    from api.process_tree import IdentityStatus

    terminate_calls = []
    monkeypatch.setattr(
        transactions,
        "verify_process_identity",
        lambda identity: IdentityStatus(status="verified"),
    )
    monkeypatch.setattr(
        transactions,
        "terminate_process_tree",
        lambda identity, grace: terminate_calls.append(identity.pid)
        or TerminationResult(status="terminated"),
    )
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True, report
    assert terminate_calls == [FAKE_PID]
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    # R4-P2-3: 内部码 running_transition_failed 映射为公开合同码
    # compile_failed;Manifest 证据保留原始码(见上方 evidence 断言)。
    assert meta["error_code"] == "compile_failed"


def test_running_transition_failure_clean_exit_keeps_scheduled(
    tmp_path, monkeypatch
):
    """F3(a) 对照: RUNNING 迁移失败但进程树已确认退出(terminated)时,
    Manifest 保持 SCHEDULED(无存活进程证据),启动恢复按 SCHEDULED 回滚。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)
    install_fake_process(monkeypatch, tmp_path, manifest.doc_id)
    monkeypatch.setattr(
        "api.compile_jobs.transition_manifest",
        _flaky_transition_raising_on(RUNNING),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(status=STATUS_TERMINATED),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert readiness.snapshot()[0] == "recovery_required"
    evidence = load_manifest(manifest.job_dir)
    assert evidence.state is SCHEDULED
    assert evidence.process is None

    # 模拟重启: SCHEDULED 恢复直接回滚(无进程需要终止)。
    from api.compile_transactions import recover_startup

    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.ready is True, report
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    assert read_doc_meta(manifest.doc_id, tmp_path)["error_code"] == "interrupted"


def test_running_transition_failure_evidence_persist_failure_keeps_scheduled(
    tmp_path, monkeypatch, caplog
):
    """F3(a) 兜底: RUNNING 迁移失败、退出未确认、证据持久化也失败时,
    必须响亮记录并仍失败关闭(recovery_required),Manifest 保持 SCHEDULED。"""
    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(monkeypatch, tmp_path, manifest.doc_id)
    monkeypatch.setattr(
        "api.compile_jobs.transition_manifest",
        _flaky_transition_raising_on(RUNNING),
    )
    monkeypatch.setattr(
        "api.compile_jobs.terminate_process_tree",
        lambda identity, grace_seconds: TerminationResult(
            status=STATUS_SURVIVORS_REMAINING, survivors=(9999,)
        ),
    )
    # 证据持久化(第二次 transition 调用)同样失败
    real_transition = compile_jobs.transition_manifest
    calls = {"count": 0}

    def always_flaky(job_dir, expected, target, **changes):
        calls["count"] += 1
        raise OSError("manifest io permanently failed")

    monkeypatch.setattr("api.compile_jobs.transition_manifest", always_flaky)

    readiness = ServiceReadiness()
    with caplog.at_level("ERROR", logger="api.compile_jobs"):
        run_compile_task(
            manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
        )

    assert readiness.snapshot()[0] == "recovery_required"
    assert load_manifest(manifest.job_dir).state is SCHEDULED
    assert calls["count"] == 2  # RUNNING 迁移 + 证据持久化各尝试一次
    assert any(
        "rollback evidence" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Codex Round 2 (N4): COMMITTED 迁移前 wrapper 必须对已存在的七项产物与
# raw/{doc_id}.meta.yaml 执行 fsync(只读打开,不改写);fsync 失败 → 绝不
# 提交,按既有失败路径回滚。提交点仍唯一(Manifest 原子迁移)。
# ---------------------------------------------------------------------------


def test_commit_fsyncs_outputs_before_committed_transition(tmp_path, monkeypatch):
    """N4: 七项产物中存在的文件 + raw meta 全部在 COMMITTED 迁移前 fsync。"""
    from pathlib import Path as _Path

    manifest, _old = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=lambda: write_valid_compile_outputs(tmp_path, manifest.doc_id),
    )
    events = []
    real_fsync = compile_jobs.fsync_existing_file

    def spy_fsync(path):
        events.append(("fsync", _Path(path).name))
        return real_fsync(path)

    monkeypatch.setattr("api.compile_jobs.fsync_existing_file", spy_fsync)
    real_transition = compile_jobs.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        events.append(("transition", target))
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr("api.compile_jobs.transition_manifest", spy_transition)

    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness()
    )

    commit_index = next(
        i for i, event in enumerate(events) if event == ("transition", COMMITTED)
    )
    fsynced_before_commit = {
        name for kind, name in events[:commit_index] if kind == "fsync"
    }
    expected_files = {
        f"{manifest.doc_id}.summary.yaml",
        "index.yaml",
        f"{manifest.doc_id}.ontology.yaml",
        "global_ontology.yaml",
        f"{manifest.doc_id}.relations.yaml",
        "knowledge_graph.yaml",
        "entity_relations.yaml",
        f"{manifest.doc_id}.meta.yaml",
    }
    assert expected_files <= fsynced_before_commit
    # COMMITTED 之后不再有任何产物 fsync(提交点仍是唯一原子迁移)
    assert all(kind != "fsync" for kind, _ in events[commit_index:])
    # 提交后终态验证通过并清理事务目录
    assert not manifest.job_dir.exists()


def test_commit_aborts_and_rolls_back_when_output_fsync_fails(
    tmp_path, monkeypatch
):
    """N4: 提交前 fsync 失败 → 绝不进入 COMMITTED,按既有失败路径回滚,
    旧产物逐字节恢复,文档进入 error 终态。"""
    manifest, old_payloads = scheduled_transaction(tmp_path)
    install_fake_process(
        monkeypatch,
        tmp_path,
        manifest.doc_id,
        on_wait=lambda: write_valid_compile_outputs(tmp_path, manifest.doc_id),
    )

    def broken_fsync(path):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr("api.compile_jobs.fsync_existing_file", broken_fsync)
    transitions = []
    real_transition = compile_jobs.transition_manifest

    def spy_transition(job_dir, expected, target, **changes):
        transitions.append(target)
        return real_transition(job_dir, expected, target, **changes)

    monkeypatch.setattr("api.compile_jobs.transition_manifest", spy_transition)

    readiness = ServiceReadiness()
    run_compile_task(manifest.job_id, tmp_path, runtime_config(tmp_path), readiness)

    assert COMMITTED not in transitions
    assert ROLLBACKING in transitions
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    assert readiness.snapshot()[0] == "ready"
    assert not manifest.job_dir.exists()


# ---------------------------------------------------------------------------
# Codex Round 7 (R7-P1-1) 端到端: 身份捕获失败(create_time 不可读)→
# spawn 抛出 → 按 spawn 失败从 SCHEDULED 回滚为 ROLLED_BACK,文档进入
# error 终态,readiness 保持 ready,事务目录经终态验证清理。
# ---------------------------------------------------------------------------


def test_run_compile_task_rolls_back_when_identity_capture_fails(
    tmp_path, monkeypatch
):
    """R7-P1-1: create_time 不可读绝不持久化 0.0 身份;事务干净回滚。"""
    import api.process_tree as process_tree

    manifest, old_payloads = scheduled_transaction(tmp_path)

    class _DyingPopen:
        """身份捕获前即逝的伪 Popen;记录回收调用,绝不指向真实进程。"""

        def __init__(self):
            self.pid = FAKE_PID
            self.killed = False
            self.waited = False

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
            return -9

    dying = _DyingPopen()
    monkeypatch.setattr(
        process_tree.subprocess, "Popen", lambda *args, **kwargs: dying
    )
    import psutil as _psutil
    monkeypatch.setattr(
        process_tree.psutil,
        "Process",
        lambda pid: (_ for _ in ()).throw(_psutil.NoSuchProcess(pid)),
    )

    readiness = ServiceReadiness()
    run_compile_task(
        manifest.job_id, tmp_path, runtime_config(tmp_path), readiness
    )

    assert dying.killed and dying.waited
    meta = read_doc_meta(manifest.doc_id, tmp_path)
    assert meta["status"] == "error"
    for field in ACTIVE_JOB_FIELDS:
        assert field not in meta
    for rel, payload in old_payloads.items():
        assert (tmp_path / rel).read_bytes() == payload
    assert readiness.snapshot()[0] == "ready"
    # 回滚完成并经终态验证清理(无 RUNNING 完整性阻断残留)
    assert not manifest.job_dir.exists()
