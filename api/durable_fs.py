"""E005 耐久文件工具。

实现设计文档第 8 节的耐久写入合同:

    同目录创建临时文件
    → 写完整内容
    → flush
    → fsync(文件)
    → os.replace
    → 重新读取或哈希验证
    → POSIX fsync(父目录)

Windows 仅执行文件级 fsync 与原子替换,不伪造父目录同步承诺。
本模块只提供原语,不绑定任何业务路径;所有调用方必须显式传入目标路径。
"""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: Path) -> str:
    """计算文件的 SHA-256 十六进制摘要。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_parent_directory(path: Path) -> None:
    """POSIX 下 fsync 父目录;Windows 不做等价承诺,直接返回。"""
    if os.name == "nt":
        return
    fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_parent_directory(path: Path) -> None:
    """POSIX 下 fsync path 的父目录;Windows 不做等价承诺,直接返回。

    供删除/换名等不经过本模块写入原语、但仍需父目录耐久确认的调用方
    使用;所有父目录同步统一走本原语,调用方不得自行散落 fsync。
    """
    _fsync_parent_directory(Path(path))


def durable_unlink(path: Path) -> None:
    """删除文件并在 POSIX 下 fsync 父目录,使删除先于后续状态提交点耐久。

    语义与 Path.unlink 一致: 目标不存在抛 FileNotFoundError;父目录
    fsync 失败抛 OSError(此时目标可能已删除,调用方必须按删除失败
    失败关闭)。Windows 仅执行 unlink,不伪造父目录同步承诺。
    """
    path = Path(path)
    path.unlink()
    _fsync_parent_directory(path)


def durable_write_bytes(path: Path, payload: bytes) -> None:
    """按耐久写入合同把 payload 原子发布到 path 并回读验证。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        if path.read_bytes() != payload:
            raise OSError(f"durable write verification failed: {path}")
        _fsync_parent_directory(path)
    except BaseException:
        # 清理未发布的临时文件;已 replace 时 tmp_path 不存在,missing_ok 兜底
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def durable_write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    """以 UTF-8、保持键顺序、允许 Unicode 的方式耐久写入 YAML 映射。"""
    text = yaml.dump(data, allow_unicode=True, sort_keys=False)
    durable_write_bytes(path, text.encode("utf-8"))


def durable_stream_to_file(path: Path, stream: Any) -> str:
    """耐久写入合同的流式变体: 分块复制 stream → 同目录临时文件 → flush →
    fsync → os.replace → sha256 回读验证 → POSIX fsync(父目录)。

    写入过程中增量计算 SHA-256,全程不把整个流读入内存;返回内容的
    SHA-256 十六进制摘要。任何失败清理未发布的临时文件。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as handle:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        if sha256_file(path) != digest.hexdigest():
            raise OSError(f"durable stream verification failed: {path}")
        _fsync_parent_directory(path)
    except BaseException:
        # 清理未发布的临时文件;已 replace 时 tmp_path 不存在,missing_ok 兜底
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return digest.hexdigest()


def fsync_existing_file(path: Path) -> None:
    """对已存在文件执行文件级 fsync 并(POSIX)fsync 其父目录。

    绝不写入任何字节;O_RDWR 仅为满足 Windows 对 fsync 句柄需可写的
    要求(POSIX 语义相同),不执行任何写调用。供提交点前的产物耐久化
    确认使用。
    """
    path = Path(path)
    fd = os.open(str(path), os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_parent_directory(path)


def durable_publish_directory(staging: Path, target: Path) -> None:
    """把 staging 目录原子发布为同目录下的 target。

    staging 与 target 必须是同级兄弟路径;target 已存在时拒绝,
    不覆盖既有内容。
    """
    staging = Path(staging)
    target = Path(target)
    if not staging.is_dir():
        raise FileNotFoundError(f"staging directory does not exist: {staging}")
    if target.exists():
        raise FileExistsError(f"publish target already exists: {target}")
    if staging.parent != target.parent:
        raise ValueError(
            f"staging and target must be siblings: {staging} -> {target}"
        )
    os.replace(staging, target)
    _fsync_parent_directory(target)


def probe_durable_directory(path: Path) -> None:
    """启动探针:验证目录可创建、可写、可 fsync、可原子替换、可回读、可删除。

    任一环节失败直接抛出原始异常,调用方据此阻止启动。
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".durable-probe"
    payload = b"e005-durable-probe"
    durable_write_bytes(probe, payload)
    if probe.read_bytes() != payload:
        raise OSError(f"durable probe read-back failed: {probe}")
    probe.unlink()
    _fsync_parent_directory(probe)
