"""E005 Task 1: 耐久文件工具测试。

全部测试仅使用 tmp_path 隔离目录,不访问真实知识库目录,不访问网络。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
import yaml

from api.durable_fs import (
    durable_makedirs,
    durable_publish_directory,
    durable_unlink,
    durable_write_bytes,
    durable_write_yaml,
    probe_durable_directory,
    sha256_file,
)


def test_sha256_file_matches_known_digest(tmp_path):
    target = tmp_path / "a.bin"
    payload = b"hello durable world"
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_durable_write_bytes_round_trip(tmp_path):
    target = tmp_path / "sub" / "payload.bin"
    payload = b"\x00\x01binary-payload" * 100
    durable_write_bytes(target, payload)
    assert target.read_bytes() == payload
    # 同目录临时文件不得残留
    assert [p.name for p in target.parent.iterdir()] == ["payload.bin"]


def test_durable_write_bytes_overwrites_existing(tmp_path):
    target = tmp_path / "payload.bin"
    durable_write_bytes(target, b"first")
    durable_write_bytes(target, b"second-longer")
    assert target.read_bytes() == b"second-longer"


def test_durable_write_bytes_fsyncs_file_before_atomic_replace(tmp_path, monkeypatch):
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def recording_replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    monkeypatch.setattr(os, "replace", recording_replace)

    target = tmp_path / "ordered.bin"
    durable_write_bytes(target, b"ordered")

    assert "fsync" in events
    assert "replace" in events
    assert events.index("fsync") < events.index("replace")
    assert target.read_bytes() == b"ordered"


def test_durable_write_yaml_round_trip_preserves_order_and_unicode(tmp_path):
    target = tmp_path / "doc.yaml"
    data = {"zeta": "中文内容", "alpha": [1, 2], "mid": {"k": "值"}}
    durable_write_yaml(target, data)

    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded == data

    raw = target.read_text(encoding="utf-8")
    # sort_keys=False: 键顺序保持映射原始顺序
    assert raw.index("zeta") < raw.index("alpha") < raw.index("mid")
    assert "中文内容" in raw


def test_durable_publish_directory_moves_staging_to_target(tmp_path):
    staging = tmp_path / ".staging-job1"
    staging.mkdir()
    (staging / "manifest.yaml").write_text("state: PREPARED", encoding="utf-8")
    target = tmp_path / "job1"

    durable_publish_directory(staging, target)

    assert not staging.exists()
    assert (target / "manifest.yaml").read_text(encoding="utf-8") == "state: PREPARED"


def test_durable_publish_directory_refuses_existing_target(tmp_path):
    staging = tmp_path / ".staging-job1"
    staging.mkdir()
    target = tmp_path / "job1"
    target.mkdir()

    with pytest.raises(FileExistsError):
        durable_publish_directory(staging, target)

    # 失败不得改动 staging
    assert staging.is_dir()


def test_durable_publish_directory_requires_sibling_paths(tmp_path):
    staging = tmp_path / "a" / "staging"
    staging.mkdir(parents=True)
    target = tmp_path / "b" / "target"

    with pytest.raises(ValueError):
        durable_publish_directory(staging, target)


def test_durable_publish_directory_requires_existing_staging(tmp_path):
    with pytest.raises(FileNotFoundError):
        durable_publish_directory(tmp_path / "missing", tmp_path / "target")


def test_probe_durable_directory_success(tmp_path):
    target = tmp_path / "runtime" / "compile-transactions"
    probe_durable_directory(target)
    assert target.is_dir()
    # 探针文件必须被清理
    assert list(target.iterdir()) == []


def test_probe_durable_directory_raises_when_directory_uncreatable(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        probe_durable_directory(blocker / "nested")


# ---------------------------------------------------------------------------
# Codex Round 3 (P1-1): durable_unlink —— 事务语义删除必须在状态提交点前
# 耐久(POSIX fsync 父目录);Windows 仅删除,不伪造父目录同步承诺。
# ---------------------------------------------------------------------------


def test_durable_unlink_deletes_file_and_fsyncs_parent(tmp_path, monkeypatch):
    """P1-1: 删除文件后必须经父目录 fsync 原语(以目标路径为参数)。"""
    import api.durable_fs as durable_fs

    target = tmp_path / "sub" / "gone.bin"
    target.parent.mkdir()
    target.write_bytes(b"x")
    events = []
    monkeypatch.setattr(
        durable_fs,
        "_fsync_parent_directory",
        lambda path: events.append(Path(path)),
    )

    durable_unlink(target)

    assert not target.exists()
    assert events == [target]


def test_durable_unlink_posix_branch_opens_fsyncs_closes_parent(
    tmp_path, monkeypatch
):
    """P1-1: POSIX 父目录 fsync 分支必须 open(父目录)→fsync→close。"""
    import api.durable_fs as durable_fs

    target = tmp_path / "sub" / "gone.bin"
    events = []
    monkeypatch.setattr(durable_fs.os, "name", "posix")
    monkeypatch.setattr(
        durable_fs.os, "open", lambda *args: events.append(("open", args[0])) or 999
    )
    monkeypatch.setattr(
        durable_fs.os, "fsync", lambda fd: events.append(("fsync", fd))
    )
    monkeypatch.setattr(
        durable_fs.os, "close", lambda fd: events.append(("close", fd))
    )

    durable_fs._fsync_parent_directory(target)

    assert ("open", str(target.parent)) in events
    assert ("fsync", 999) in events
    assert ("close", 999) in events


def test_durable_unlink_skips_parent_fsync_on_windows(tmp_path, monkeypatch):
    """P1-1: Windows 分支只删除,绝不尝试父目录 fsync。

    直接调用 _fsync_parent_directory 而非 durable_unlink: os.name 翻转后,
    对立平台 Path 类的实例化/派生会抛 NotImplementedError(3.11 在 Path(...)
    选择时经 _flavour.is_supported 抛;3.12 在 parent/joinpath 派生时经
    导入期绑定的 __new__ stub 抛)——durable_unlink 内部有 Path(path),
    在非 Windows CI 上会让本测试报错,并连带 pytest 失败格式化器
    INTERNALERROR。durable_unlink → _fsync_parent_directory 的接线由
    test_durable_unlink_deletes_file_and_fsyncs_parent 覆盖;真实分支的
    端到端删除由 test_durable_unlink_deletes_file_on_native_platform 覆盖。
    """
    import api.durable_fs as durable_fs

    target = tmp_path / "gone.bin"
    monkeypatch.setattr(durable_fs.os, "name", "nt")

    def forbidden_open(*args, **kwargs):
        raise AssertionError("Windows 不得尝试父目录 fsync")

    monkeypatch.setattr(durable_fs.os, "open", forbidden_open)
    durable_fs._fsync_parent_directory(target)


def test_durable_unlink_deletes_file_on_native_platform(tmp_path):
    """P1-1: 无任何补丁的原生分支端到端删除(Windows 上即 nt 分支)。"""
    target = tmp_path / "gone.bin"
    target.write_bytes(b"x")
    durable_unlink(target)
    assert not target.exists()


# ---------------------------------------------------------------------------
# Codex Round 5 (R5-P1-2): durable_makedirs —— 新建目录链的每一层都必须
# 使其父目录条目耐久(POSIX fsync 父目录,自底向上);整链已存在时不 fsync;
# fsync 失败原样传播(调用方失败关闭)。测试只 spy 内部边界,绝不翻转
# os.name(对立平台 Path 实例化/派生会崩溃,见 R3 教训)。
# ---------------------------------------------------------------------------


def test_durable_makedirs_fsyncs_each_new_level_bottom_up(tmp_path, monkeypatch):
    """R5-P1-2: 三层新目录 → 每层父目录恰 fsync 一次,自底向上。"""
    import api.durable_fs as durable_fs

    events = []
    monkeypatch.setattr(
        durable_fs,
        "_fsync_parent_directory",
        lambda path: events.append(Path(path)),
    )

    target = tmp_path / "a" / "b" / "c"
    durable_makedirs(target)

    assert target.is_dir()
    assert events == [tmp_path / "a" / "b" / "c", tmp_path / "a" / "b", tmp_path / "a"]


def test_durable_makedirs_fsyncs_only_missing_levels(tmp_path, monkeypatch):
    """R5-P1-2: 部分已存在的链只为新建层 fsync;整链已存在 → 零 fsync。"""
    import api.durable_fs as durable_fs

    (tmp_path / "x").mkdir()
    events = []
    monkeypatch.setattr(
        durable_fs,
        "_fsync_parent_directory",
        lambda path: events.append(Path(path)),
    )

    durable_makedirs(tmp_path / "x" / "y")
    assert events == [tmp_path / "x" / "y"]

    events.clear()
    durable_makedirs(tmp_path / "x" / "y")
    assert events == []


def test_durable_makedirs_propagates_fsync_failure(tmp_path, monkeypatch):
    """R5-P1-2: 父目录 fsync 失败原样传播(启动/发布路径失败关闭)。"""
    import api.durable_fs as durable_fs

    def boom(path):
        raise OSError("simulated parent fsync failure")

    monkeypatch.setattr(durable_fs, "_fsync_parent_directory", boom)

    with pytest.raises(OSError, match="simulated parent fsync failure"):
        durable_makedirs(tmp_path / "a" / "b")


def test_durable_makedirs_exist_ok_false_rejects_existing(tmp_path):
    """R5-P1-2: exist_ok=False 时目标已存在即 FileExistsError。"""
    with pytest.raises(FileExistsError):
        durable_makedirs(tmp_path, exist_ok=False)


def test_probe_durable_directory_creates_chain_durably(tmp_path, monkeypatch):
    """R5-P1-2: 启动探针的目录链创建同样走耐久原语。"""
    import api.durable_fs as durable_fs

    calls = []
    real = durable_fs.durable_makedirs

    def spy(path, *args, **kwargs):
        calls.append(Path(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(durable_fs, "durable_makedirs", spy)
    target = tmp_path / ".runtime" / "compile-transactions"

    probe_durable_directory(target)

    assert calls == [target]
    assert target.is_dir()
    assert list(target.iterdir()) == []


def test_durable_unlink_propagates_parent_fsync_failure(tmp_path, monkeypatch):
    """P1-1: 父目录 fsync 失败必须抛出 OSError(调用方按删除失败失败关闭)。"""
    import api.durable_fs as durable_fs

    target = tmp_path / "gone.bin"
    target.write_bytes(b"x")

    def boom_fsync(path):
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(durable_fs, "_fsync_parent_directory", boom_fsync)

    with pytest.raises(OSError, match="simulated fsync failure"):
        durable_unlink(target)
