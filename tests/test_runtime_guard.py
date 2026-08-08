"""E005 Task 1: 运行时配置、单实例锁与就绪门禁测试。

全部测试仅使用 tmp_path 隔离目录,不访问真实知识库目录,不访问网络。
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
from portalocker.exceptions import LockException

from api.runtime_guard import (
    ApiInstanceLock,
    ServiceReadiness,
    load_compile_runtime_config,
)


def test_runtime_config_uses_documented_defaults(tmp_path):
    config = load_compile_runtime_config(tmp_path, env={})
    assert config.transaction_dir == tmp_path / ".runtime" / "compile-transactions"
    assert config.upload_intake_dir == tmp_path / ".runtime" / "upload-intake"
    assert config.instance_lock_path == tmp_path / ".runtime" / "api-instance.lock"
    assert config.timeout_seconds == 1800
    assert config.termination_grace_seconds == 5


def test_runtime_config_is_frozen(tmp_path):
    config = load_compile_runtime_config(tmp_path, env={})
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.timeout_seconds = 1


def test_runtime_config_rejects_out_of_range_timeout(tmp_path):
    with pytest.raises(ValueError, match="COMPILE_TIMEOUT_SECONDS"):
        load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "59"})


def test_runtime_config_rejects_above_max_timeout(tmp_path):
    with pytest.raises(ValueError, match="COMPILE_TIMEOUT_SECONDS"):
        load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "86401"})


def test_runtime_config_rejects_non_integer_timeout(tmp_path):
    with pytest.raises(ValueError, match="COMPILE_TIMEOUT_SECONDS"):
        load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "abc"})


def test_runtime_config_accepts_boundary_timeout_overrides(tmp_path):
    config = load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "60"})
    assert config.timeout_seconds == 60
    config = load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "86400"})
    assert config.timeout_seconds == 86400


def test_runtime_config_rejects_out_of_range_grace(tmp_path):
    for bad in ("0", "61", "nope"):
        with pytest.raises(ValueError, match="COMPILE_TERMINATION_GRACE_SECONDS"):
            load_compile_runtime_config(
                tmp_path, env={"COMPILE_TERMINATION_GRACE_SECONDS": bad}
            )


def test_runtime_config_accepts_boundary_grace_overrides(tmp_path):
    config = load_compile_runtime_config(
        tmp_path, env={"COMPILE_TERMINATION_GRACE_SECONDS": "1"}
    )
    assert config.termination_grace_seconds == 1
    config = load_compile_runtime_config(
        tmp_path, env={"COMPILE_TERMINATION_GRACE_SECONDS": "60"}
    )
    assert config.termination_grace_seconds == 60


def test_runtime_config_resolves_relative_transaction_dir_against_base(tmp_path):
    config = load_compile_runtime_config(tmp_path, env={"COMPILE_TRANSACTION_DIR": "tx-store"})
    assert config.transaction_dir == tmp_path / "tx-store"


def test_runtime_config_keeps_absolute_transaction_dir(tmp_path):
    absolute = tmp_path / "elsewhere" / "tx"
    config = load_compile_runtime_config(
        tmp_path, env={"COMPILE_TRANSACTION_DIR": str(absolute)}
    )
    assert config.transaction_dir == absolute


def test_instance_lock_acquire_creates_file_and_release_frees_it(tmp_path):
    lock_path = tmp_path / ".runtime" / "api-instance.lock"
    lock = ApiInstanceLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
    finally:
        lock.release()


def test_instance_lock_second_handle_fails_fast(tmp_path):
    lock_path = tmp_path / "api-instance.lock"
    first = ApiInstanceLock(lock_path)
    first.acquire()
    try:
        second = ApiInstanceLock(lock_path)
        with pytest.raises(LockException):
            second.acquire()
    finally:
        first.release()

    # 释放后新句柄可重新获取
    third = ApiInstanceLock(lock_path)
    third.acquire()
    third.release()


def test_instance_lock_double_acquire_on_same_object_rejected(tmp_path):
    lock = ApiInstanceLock(tmp_path / "api-instance.lock")
    lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            lock.acquire()
    finally:
        lock.release()


def test_instance_lock_release_without_acquire_is_noop(tmp_path):
    ApiInstanceLock(tmp_path / "api-instance.lock").release()


def test_instance_lock_context_manager_releases(tmp_path):
    lock_path = tmp_path / "api-instance.lock"
    with ApiInstanceLock(lock_path):
        other = ApiInstanceLock(lock_path)
        with pytest.raises(LockException):
            other.acquire()
    # 退出上下文后锁已释放
    follower = ApiInstanceLock(lock_path)
    follower.acquire()
    follower.release()


def test_service_readiness_defaults_to_ready():
    readiness = ServiceReadiness()
    readiness.require_ready()
    assert readiness.snapshot() == ("ready", None)


def test_service_readiness_blocks_after_mark_recovery_required():
    readiness = ServiceReadiness()
    readiness.mark_recovery_required("rollback_failed")
    with pytest.raises(RuntimeError, match="recovery_required"):
        readiness.require_ready()
    assert readiness.snapshot() == ("recovery_required", "rollback_failed")


# ---------------------------------------------------------------------------
# Codex Round 6 (R6-P1-1): 实例锁父目录(.runtime)必须经 durable_makedirs
# 耐久创建——裸 mkdir 会让探针的 durable_makedirs 误判"已存在"而跳过
# .runtime 条目的父目录 fsync。测试只 spy 内层边界,不翻转 os.name。
# ---------------------------------------------------------------------------


def test_instance_lock_creates_runtime_parent_durably(tmp_path, monkeypatch):
    """R6-P1-1: 首次获取锁时 .runtime 经耐久原语创建(spy 证明调用),
    且新建层父目录被 fsync。"""
    import api.durable_fs as durable_fs

    events = []
    monkeypatch.setattr(
        durable_fs,
        "_fsync_parent_directory",
        lambda path: events.append(Path(path)),
    )
    lock = ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock")
    lock.acquire()
    try:
        assert (tmp_path / ".runtime" / "api-instance.lock").exists()
    finally:
        lock.release()
    # .runtime 是新建层: 其父目录(tmp_path)被 fsync
    assert tmp_path / ".runtime" in events


def test_instance_lock_skips_fsync_when_runtime_parent_exists(tmp_path, monkeypatch):
    """R6-P1-1: .runtime 已存在时不得重复 fsync(幂等)。"""
    import api.durable_fs as durable_fs

    (tmp_path / ".runtime").mkdir()
    events = []
    monkeypatch.setattr(
        durable_fs,
        "_fsync_parent_directory",
        lambda path: events.append(Path(path)),
    )
    lock = ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock")
    lock.acquire()
    lock.release()
    assert events == []


def test_instance_lock_acquire_fails_closed_on_parent_fsync_failure(
    tmp_path, monkeypatch
):
    """R6-P1-1: 父目录 fsync 失败 → acquire 失败关闭(原样抛出)。"""
    import api.durable_fs as durable_fs

    def boom(path):
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(durable_fs, "_fsync_parent_directory", boom)
    lock = ApiInstanceLock(tmp_path / ".runtime" / "api-instance.lock")
    with pytest.raises(OSError, match="simulated parent fsync failure"):
        lock.acquire()
