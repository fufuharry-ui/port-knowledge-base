"""E005 Task 3: 跨平台进程身份验证与进程树终止。

对应设计文档第 13 节(编译执行器与进程身份)与第 14 节(硬超时与进程树终止)。

安全合同:
- 终止前必须完成五要素身份验证(pid 存在、create_time、executable、cwd、
  规范化业务命令指纹);
- PID 存在但身份不匹配时,绝不终止、绝不 kill,返回 identity_mismatch 并保留证据;
- 只有根进程与全部已捕获后代都退出时才返回成功;存在幸存者时返回
  survivors_remaining,调用方据此进入 recovery_required 且不得回滚。

子进程 stdout/stderr 使用管道捕获(communicate 排出,避免缓冲死锁),仅用于
诊断,不写入 Manifest。
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

import psutil

logger = logging.getLogger(__name__)

CREATE_TIME_TOLERANCE_SECONDS = 0.01

PLATFORM = "windows" if os.name == "nt" else "posix"

STATUS_VERIFIED = "verified"
STATUS_PROCESS_GONE = "process_gone"
STATUS_IDENTITY_MISMATCH = "identity_mismatch"

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_TIMED_OUT = "timed_out"

STATUS_TERMINATED = "terminated"
STATUS_ALREADY_GONE = "already_gone"
STATUS_SURVIVORS_REMAINING = "survivors_remaining"


@dataclass(frozen=True)
class ProcessIdentity:
    """启动时刻记录的进程身份(对应设计文档 7.3 节 process 段)。"""

    pid: int
    create_time: float
    executable: str
    cwd: str
    command_fingerprint: str
    process_group_id: int
    platform: str


@dataclass(frozen=True)
class SpawnedProcess:
    popen: subprocess.Popen
    identity: ProcessIdentity | None
    command_fingerprint: str


@dataclass(frozen=True)
class IdentityStatus:
    status: str  # verified | process_gone | identity_mismatch
    mismatched_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProcessResult:
    status: str  # completed | failed | timed_out
    returncode: int | None
    stdout: str
    stderr: str


@dataclass(frozen=True)
class TerminationResult:
    status: str  # terminated | already_gone | identity_mismatch | survivors_remaining
    survivors: tuple[int, ...] = ()
    mismatched_fields: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return self.status in (STATUS_TERMINATED, STATUS_ALREADY_GONE)


def command_fingerprint(argv: Sequence[str]) -> str:
    """规范化业务命令指纹,排除解释器绝对路径。

    ["C:/Python/python.exe", "-m", "scripts.compile", "doc_1"] 与
    ["/usr/bin/python3", "-m", "scripts.compile", "doc_1"] 均归一化为
    "scripts.compile|doc_1"。无 -m 时排除 argv[0] 后拼接其余参数。
    """
    tokens = [str(arg) for arg in argv]
    if "-m" in tokens:
        business = tokens[tokens.index("-m") + 1:]
    else:
        business = tokens[1:] if len(tokens) > 1 else tokens
    return "|".join(business)


def _normalize_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(str(value)))


def spawn_compile_process(
    base_dir: Path, doc_id: str, env: Mapping[str, str] | None
) -> SpawnedProcess:
    """以独立进程组启动 `sys.executable -m scripts.compile <doc_id>` 并记录身份。"""
    base_dir = Path(base_dir)
    argv = [sys.executable, "-m", "scripts.compile", doc_id]
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    popen = subprocess.Popen(
        argv,
        cwd=str(base_dir),
        env=dict(env) if env is not None else None,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    fingerprint = command_fingerprint(argv)
    identity = ProcessIdentity(
        pid=popen.pid,
        create_time=_read_create_time(popen.pid),
        executable=_read_attr(popen.pid, "exe", fallback=sys.executable),
        cwd=_read_attr(popen.pid, "cwd", fallback=str(base_dir)),
        command_fingerprint=fingerprint,
        process_group_id=_read_process_group_id(popen.pid),
        platform=PLATFORM,
    )
    return SpawnedProcess(popen=popen, identity=identity, command_fingerprint=fingerprint)


def _read_create_time(pid: int) -> float:
    try:
        return psutil.Process(pid).create_time()
    except psutil.NoSuchProcess:
        # 进程已快速退出;create_time 未知,后续身份验证会得到 process_gone。
        return 0.0


def _read_attr(pid: int, attr: str, fallback: str) -> str:
    try:
        return getattr(psutil.Process(pid), attr)()
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logger.warning("process %d %s unreadable at spawn: %s", pid, attr, exc)
        return _normalize_path(fallback)


def _read_process_group_id(pid: int) -> int:
    if os.name == "nt":
        # CREATE_NEW_PROCESS_GROUP 使子进程成为组首领,组 ID 等于其 pid。
        return pid
    try:
        return os.getpgid(pid)
    except OSError as exc:
        logger.warning("process %d getpgid failed at spawn: %s", pid, exc)
        return pid


def verify_process_identity(identity: ProcessIdentity) -> IdentityStatus:
    """五要素身份验证:pid 存在、create_time、executable、cwd、命令指纹。"""
    try:
        proc = psutil.Process(identity.pid)
        create_time = proc.create_time()
    except psutil.NoSuchProcess:
        return IdentityStatus(status=STATUS_PROCESS_GONE)

    mismatched: list[str] = []
    if abs(create_time - identity.create_time) > CREATE_TIME_TOLERANCE_SECONDS:
        mismatched.append("create_time")
    try:
        if _normalize_path(proc.exe()) != _normalize_path(identity.executable):
            mismatched.append("executable")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        mismatched.append("executable")
    try:
        if _normalize_path(proc.cwd()) != _normalize_path(identity.cwd):
            mismatched.append("cwd")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        mismatched.append("cwd")
    try:
        if command_fingerprint(proc.cmdline()) != identity.command_fingerprint:
            mismatched.append("command_fingerprint")
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        mismatched.append("command_fingerprint")

    if mismatched:
        return IdentityStatus(
            status=STATUS_IDENTITY_MISMATCH, mismatched_fields=tuple(mismatched)
        )
    return IdentityStatus(status=STATUS_VERIFIED)


def wait_for_process(spawned: SpawnedProcess, timeout_seconds: int) -> ProcessResult:
    """最多等待 timeout_seconds;区分 completed / failed / timed_out。

    timed_out 时不在此处终止进程,由调用方走 terminate_process_tree。
    """
    try:
        stdout, stderr = spawned.popen.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        return ProcessResult(
            status=STATUS_TIMED_OUT, returncode=None, stdout="", stderr=""
        )
    returncode = spawned.popen.returncode
    status = STATUS_COMPLETED if returncode == 0 else STATUS_FAILED
    return ProcessResult(
        status=status,
        returncode=returncode,
        stdout=stdout or "",
        stderr=stderr or "",
    )


def terminate_process_tree(
    identity: ProcessIdentity, grace_seconds: int
) -> TerminationResult:
    """按合同顺序终止完整进程树。

    1. 验证根进程身份;
    2. 快照全部后代;
    3. 协作终止(Windows 投递 CTRL_BREAK_EVENT,POSIX 向进程组发 SIGTERM);
    4. 等待 grace_seconds;
    5. 先后代、后根进程强制 kill;
    6. 根进程与全部已捕获后代都退出才返回成功。
    """
    status = verify_process_identity(identity)
    if status.status == STATUS_IDENTITY_MISMATCH:
        return TerminationResult(
            status=STATUS_IDENTITY_MISMATCH,
            mismatched_fields=status.mismatched_fields,
        )
    if status.status == STATUS_PROCESS_GONE:
        return TerminationResult(status=STATUS_ALREADY_GONE)

    try:
        root = psutil.Process(identity.pid)
        descendants = root.children(recursive=True)
    except psutil.NoSuchProcess:
        return TerminationResult(status=STATUS_ALREADY_GONE)

    _cooperative_terminate(root, identity)

    members = list(descendants) + [root]
    _, alive = psutil.wait_procs(members, timeout=grace_seconds)

    # 强制终止: 先后代,后根进程。
    ordered = [p for p in alive if p.pid != identity.pid] + [
        p for p in alive if p.pid == identity.pid
    ]
    for proc in ordered:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            continue
    _, alive = psutil.wait_procs(alive, timeout=grace_seconds)

    survivors = [p for p in alive if _still_running(p)]
    if survivors:
        return TerminationResult(
            status=STATUS_SURVIVORS_REMAINING,
            survivors=tuple(p.pid for p in survivors),
        )
    return TerminationResult(status=STATUS_TERMINATED)


def _cooperative_terminate(root: psutil.Process, identity: ProcessIdentity) -> None:
    try:
        if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
            root.send_signal(signal.CTRL_BREAK_EVENT)
        elif os.name != "nt":
            try:
                os.killpg(identity.process_group_id, signal.SIGTERM)
            except OSError as exc:
                logger.warning(
                    "killpg(%d, SIGTERM) failed, falling back to root.terminate: %s",
                    identity.process_group_id,
                    exc,
                )
                root.terminate()
        else:
            root.terminate()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError) as exc:
        # 协作终止失败不阻断后续强制 kill 阶段。
        logger.warning("cooperative terminate of pid %d failed: %s", identity.pid, exc)


def _still_running(proc: psutil.Process) -> bool:
    try:
        if not proc.is_running():
            return False
        try:
            return proc.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
    except psutil.NoSuchProcess:
        return False
