"""E005 Task 3: 跨平台进程身份验证与进程树终止。

对应设计文档第 13 节(编译执行器与进程身份)与第 14 节(硬超时与进程树终止)。

安全合同:
- 终止前必须完成五要素身份验证(pid 存在、create_time、executable、cwd、
  规范化业务命令指纹);
- PID 存在但身份不匹配时,绝不终止、绝不 kill,返回 identity_mismatch 并保留证据;
- 只有根进程与全部已捕获后代都退出时才返回成功;存在幸存者时返回
  survivors_remaining,调用方据此进入 recovery_required 且不得回滚;
- POSIX 进程组: 组存在性(killpg 0)不是归属证明——每次发信号前必须
  重新枚举组成员并逐一复核指纹 + cwd;任一成员不可读或不匹配即失败
  关闭,绝不发信号(防误杀优先于自动恢复);
- POSIX 协作终止(根进程存活): 根进程刚通过身份验证,信号发向活根的
  实际进程组(os.getpgid(root.pid));记录 pgid 只用于根已退出的
  恢复路径,绝不作为根存活路径的信号目标。

子进程 stdout/stderr 使用管道捕获(communicate 排出,避免缓冲死锁),仅用于
诊断,不写入 Manifest。
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
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
#: 幸存者检查自身失败: 无法证明记录树已退出,非成功状态(失败关闭)。
STATUS_EXIT_UNCONFIRMED = "exit_unconfirmed"

#: compile.py 以子进程派生 relate 的脚本后缀(指纹含脚本绝对路径)。
_RELATE_SCRIPT_SUFFIX = "scripts/relate.py"


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
    status: str  # terminated | already_gone | identity_mismatch | survivors_remaining | exit_unconfirmed
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
    """以独立进程组启动 `sys.executable -m scripts.compile <doc_id>` 并记录身份。

    - 管道显式 encoding="utf-8" + errors="replace": 子进程按 PYTHONUTF8
      输出 UTF-8,宿主 locale(cp936/cp1252)不得导致 communicate 抛出
      UnicodeDecodeError 而回滚一次成功的编译;
    - Popen 成功后、SpawnedProcess 返回前的身份捕获阶段任何异常:
      该子进程对调用方不可见(无身份、无 Manifest 记录),必须 best-effort
      终止并回收后重抛,绝不留存不可跟踪的编译进程。
    """
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
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
        start_new_session=(os.name != "nt"),
    )
    fingerprint = command_fingerprint(argv)
    try:
        identity = ProcessIdentity(
            pid=popen.pid,
            create_time=_read_create_time(popen.pid),
            executable=_read_attr(popen.pid, "exe", fallback=sys.executable),
            cwd=_read_attr(popen.pid, "cwd", fallback=str(base_dir)),
            command_fingerprint=fingerprint,
            process_group_id=_read_process_group_id(popen.pid),
            platform=PLATFORM,
        )
    except BaseException:
        _terminate_untracked_child(popen)
        raise
    return SpawnedProcess(popen=popen, identity=identity, command_fingerprint=fingerprint)


def _terminate_untracked_child(popen: subprocess.Popen) -> None:
    """best-effort 终止并回收身份捕获失败的不可跟踪子进程(不得抛出)。"""
    try:
        popen.kill()
    except Exception as exc:
        logger.error(
            "failed to kill untracked compile process pid=%s: %s", popen.pid, exc
        )
    try:
        popen.wait(timeout=5)
    except Exception as exc:
        logger.error(
            "failed to reap untracked compile process pid=%s: %s", popen.pid, exc
        )


def _read_create_time(pid: int) -> float:
    """读取进程 create_time;进程已退出时抛出 NoSuchProcess(身份捕获失败)。

    R7-P1-1: 绝不返回 0.0 兜底——0.0 会被 SCHEDULED→RUNNING 迁移持久化,
    而 load_manifest(R6-P1-2)拒绝非正 create_time,留下启动恢复永久
    阻断的 RUNNING manifest。spawn 处抛出由 spawn_compile_process 的
    except BaseException 路径 best-effort 终止并回收不可跟踪子进程,
    再由调用方按 spawn 失败从 SCHEDULED 干净回滚(写者/读者一致性:
    不可读的身份绝不持久化)。
    """
    return psutil.Process(pid).create_time()


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
    """五要素身份验证:pid 存在、create_time、executable、cwd、命令指纹。

    任一身份读取阶段抛出 NoSuchProcess 表示进程在检查中途退出:分类为
    process_gone(不是不匹配);AccessDenied 仍是身份不匹配证据。
    """
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
    except psutil.NoSuchProcess:
        return IdentityStatus(status=STATUS_PROCESS_GONE)
    except psutil.AccessDenied:
        mismatched.append("executable")
    try:
        if _normalize_path(proc.cwd()) != _normalize_path(identity.cwd):
            mismatched.append("cwd")
    except psutil.NoSuchProcess:
        return IdentityStatus(status=STATUS_PROCESS_GONE)
    except psutil.AccessDenied:
        mismatched.append("cwd")
    try:
        if command_fingerprint(proc.cmdline()) != identity.command_fingerprint:
            mismatched.append("command_fingerprint")
    except psutil.NoSuchProcess:
        return IdentityStatus(status=STATUS_PROCESS_GONE)
    except psutil.AccessDenied:
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
        return _settle_already_gone(identity, grace_seconds)

    try:
        root = psutil.Process(identity.pid)
        descendants = root.children(recursive=True)
    except psutil.NoSuchProcess:
        return _settle_already_gone(identity, grace_seconds)

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
            # 根进程刚通过五要素身份验证: 信号必须发向活根进程的实际进程组
            # (os.getpgid(root.pid)),绝不发向记录 pgid——记录值可能损坏
            # 或与复用组冲突(防误杀优先)。记录 pgid 仅用于根进程已退出的
            # 恢复路径(_posix_recorded_group_members 会复核组成员身份)。
            try:
                live_pgid = os.getpgid(root.pid)
            except OSError as exc:
                logger.warning(
                    "getpgid(%d) failed, falling back to root.terminate: %s",
                    root.pid,
                    exc,
                )
                root.terminate()
            else:
                try:
                    os.killpg(live_pgid, signal.SIGTERM)
                except OSError as exc:
                    logger.warning(
                        "killpg(%d, SIGTERM) failed, falling back to "
                        "root.terminate: %s",
                        live_pgid,
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


# ---------------------------------------------------------------------------
# already-gone 幸存者确认(根进程已退出不代表记录树已退出)
# ---------------------------------------------------------------------------


def _settle_already_gone(
    identity: ProcessIdentity, grace_seconds: int
) -> TerminationResult:
    """根进程已退出: 确认记录树无幸存后代后才允许 already_gone。

    - 幸存者检查自身失败 → exit_unconfirmed(无法证明,失败关闭);
    - 发现幸存者 → 只终止经指纹 + cwd 复核的确切进程,再复查;
      仍有幸存 → survivors_remaining(失败关闭);
    - 无幸存 → already_gone。
    """
    survivors = _recorded_tree_survivors(identity)
    if survivors is None:
        return TerminationResult(status=STATUS_EXIT_UNCONFIRMED)
    if survivors:
        _terminate_verified_survivors(identity, survivors, grace_seconds)
        survivors = _recorded_tree_survivors(identity)
        if survivors is None:
            return TerminationResult(status=STATUS_EXIT_UNCONFIRMED)
        if survivors:
            return TerminationResult(
                status=STATUS_SURVIVORS_REMAINING, survivors=survivors
            )
    return TerminationResult(status=STATUS_ALREADY_GONE)


def _recorded_tree_survivors(identity: ProcessIdentity) -> tuple[int, ...] | None:
    """返回记录树的幸存后代标识;检查本身失败返回 None(不确定,失败关闭)。"""
    if os.name != "nt":
        return _posix_recorded_group_members(identity)
    return _scan_business_command_survivors(identity)


def _posix_recorded_group_members(
    identity: ProcessIdentity,
) -> tuple[int, ...] | None:
    """POSIX: 枚举记录进程组成员并逐一绑定记录树身份(防误杀优先)。

    - killpg(pgid, 0) ProcessLookupError → 组已消失,返回 ();
    - killpg 探活 PermissionError → 组属于其他用户,无法证明归属,
      返回 None(失败关闭,绝不发信号);
    - 探活其他 OSError 或成员枚举失败 → None;
    - 组存在但枚举不到成员(竞态: 成员刚好全部退出) → ();
    - 每个成员必须经 _is_recorded_tree_fingerprint + 规范化 cwd 复核属于
      记录树(与 _scan_business_command_survivors 同一规则);任一成员
      不可读或不匹配 → None(PGID 复用/损坏证据,绝不发信号);
    - 全部匹配 → 返回成员 pid 元组。
    """
    pgid = identity.process_group_id
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return ()
    except PermissionError:
        logger.warning(
            "process group %d probe denied (owned by another user); "
            "cannot prove ownership, nothing signaled",
            pgid,
        )
        return None
    except OSError as exc:
        logger.warning(
            "process group %d survivor check failed: %s", pgid, exc,
        )
        return None
    expected_cwd = _normalize_path(identity.cwd)
    members: list[int] = []
    try:
        for proc in psutil.process_iter():
            try:
                if os.getpgid(proc.pid) != pgid:
                    continue
            except (ProcessLookupError, PermissionError, OSError, psutil.Error):
                continue
            try:
                fingerprint = command_fingerprint(proc.cmdline() or [])
                cwd = proc.cwd()
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.Error) as exc:
                logger.warning(
                    "process group %d member %d unreadable (%s); "
                    "cannot prove ownership, nothing signaled",
                    pgid, proc.pid, exc,
                )
                return None
            if not _is_recorded_tree_fingerprint(identity, fingerprint):
                logger.warning(
                    "process group %d member %d fingerprint mismatch "
                    "(possible pgid reuse); nothing signaled",
                    pgid, proc.pid,
                )
                return None
            if _normalize_path(cwd) != expected_cwd:
                logger.warning(
                    "process group %d member %d cwd mismatch; nothing signaled",
                    pgid, proc.pid,
                )
                return None
            members.append(proc.pid)
    except psutil.Error as exc:
        logger.warning("process group %d membership scan failed: %s", pgid, exc)
        return None
    return tuple(members)


def _is_recorded_tree_fingerprint(
    identity: ProcessIdentity, fingerprint: str
) -> bool:
    """判断命令指纹是否属于记录树: 编译根命令或 compile.py 派生的 relate
    子进程(指纹 "<…>/scripts/relate.py|<doc_id>",含脚本绝对路径)。"""
    if fingerprint == identity.command_fingerprint:
        return True
    script, sep, doc = fingerprint.rpartition("|")
    if not sep or not doc:
        return False
    identity_doc = identity.command_fingerprint.rpartition("|")[2]
    return (
        doc == identity_doc
        and script.replace("\\", "/").endswith(_RELATE_SCRIPT_SUFFIX)
    )


def _scan_business_command_survivors(
    identity: ProcessIdentity,
) -> tuple[int, ...] | None:
    """Windows: 扫描与记录树同业务命令指纹且同 cwd 的存活进程。"""
    expected_cwd = _normalize_path(identity.cwd)
    survivors: list[int] = []
    try:
        for proc in psutil.process_iter():
            try:
                fingerprint = command_fingerprint(proc.cmdline() or [])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not _is_recorded_tree_fingerprint(identity, fingerprint):
                continue
            try:
                if _normalize_path(proc.cwd()) != expected_cwd:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            survivors.append(proc.pid)
    except psutil.Error as exc:
        logger.warning("survivor scan failed: %s", exc)
        return None
    return tuple(survivors)


def _terminate_verified_survivors(
    identity: ProcessIdentity, survivors: tuple[int, ...], grace_seconds: int
) -> None:
    """只终止经指纹 + cwd 复核的确切幸存进程(身份不匹配绝不 kill)。

    POSIX: 每次 killpg(SIGTERM / 宽限后 SIGKILL)前都重新枚举并复核组
    成员身份——组存在性不是归属证明;复核失败立即返回,不发任何信号,
    由调用方的幸存复查失败关闭(exit_unconfirmed / survivors_remaining)。
    """
    if os.name != "nt":
        pgid = identity.process_group_id
        if not _posix_recorded_group_members(identity):
            logger.warning(
                "refusing to signal process group %d: membership no longer "
                "verifiably bound to the recorded tree",
                pgid,
            )
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError as exc:
            logger.warning(
                "killpg(%d, SIGTERM) for survivors failed: %s", pgid, exc,
            )
            return
        deadline = time.monotonic() + max(grace_seconds, 0)
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except OSError:
                return
            time.sleep(0.05)
        if not _posix_recorded_group_members(identity):
            logger.warning(
                "refusing to SIGKILL process group %d: membership changed "
                "during grace wait",
                pgid,
            )
            return
        try:
            os.killpg(
                pgid,
                getattr(signal, "SIGKILL", signal.SIGTERM),
            )
        except OSError as exc:
            logger.warning(
                "killpg(%d, SIGKILL) for survivors failed: %s", pgid, exc,
            )
        return
    expected_cwd = _normalize_path(identity.cwd)
    for pid in survivors:
        try:
            proc = psutil.Process(pid)
            fingerprint = command_fingerprint(proc.cmdline() or [])
            if not _is_recorded_tree_fingerprint(identity, fingerprint):
                # PID 复用: 无法证明属于记录树,绝不 kill。
                logger.warning(
                    "survivor pid %d fingerprint mismatch at kill time; skipped",
                    pid,
                )
                continue
            if _normalize_path(proc.cwd()) != expected_cwd:
                logger.warning(
                    "survivor pid %d cwd mismatch at kill time; skipped", pid
                )
                continue
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.warning("survivor pid %d termination skipped: %s", pid, exc)
