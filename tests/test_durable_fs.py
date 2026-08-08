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
    """P1-1: Windows 分支只删除,绝不尝试父目录 fsync。"""
    import api.durable_fs as durable_fs

    target = tmp_path / "gone.bin"
    target.write_bytes(b"x")
    monkeypatch.setattr(durable_fs.os, "name", "nt")

    def forbidden_open(*args, **kwargs):
        raise AssertionError("Windows 不得尝试父目录 fsync")

    monkeypatch.setattr(durable_fs.os, "open", forbidden_open)
    durable_unlink(target)
    assert not target.exists()


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
