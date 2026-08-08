"""E005 Task 3: 跨平台进程身份验证与进程树终止测试。

全部测试仅使用 tmp_path 隔离目录与本测试显式启动的辅助进程;
不访问真实知识库目录,不访问网络,不触碰真实 compile 进程。
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
from unittest.mock import Mock

import psutil
import pytest

from api.process_tree import (
    ProcessIdentity,
    SpawnedProcess,
    command_fingerprint,
    spawn_compile_process,
    terminate_process_tree,
    verify_process_identity,
    wait_for_process,
)

IS_WINDOWS = os.name == "nt"
EXPECTED_PLATFORM = "windows" if IS_WINDOWS else "posix"

# 辅助进程: 启动一个子进程后双方长时间睡眠,用于真实进程树终止测试。
HELPER_CHILD_CODE = "import time;time.sleep(120)"
HELPER_PARENT_CODE = (
    "import subprocess,sys,time\n"
    f"child = subprocess.Popen([sys.executable,'-c',{HELPER_CHILD_CODE!r}])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def _spawn_helper_tree(tmp_path):
    """以 spawn 同等机制启动 父进程→子进程 辅助树,返回 (popen, identity, child_pid)。"""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    popen = subprocess.Popen(
        [sys.executable, "-c", HELPER_PARENT_CODE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(tmp_path),
        creationflags=creationflags,
        start_new_session=not IS_WINDOWS,
    )
    child_pid = int(popen.stdout.readline().strip())
    proc = psutil.Process(popen.pid)
    identity = ProcessIdentity(
        pid=popen.pid,
        create_time=proc.create_time(),
        executable=proc.exe(),
        cwd=proc.cwd(),
        command_fingerprint=command_fingerprint(proc.cmdline()),
        process_group_id=popen.pid if IS_WINDOWS else os.getpgid(popen.pid),
        platform=EXPECTED_PLATFORM,
    )
    return popen, identity, child_pid


def _cleanup_pids(*pids):
    for pid in pids:
        if pid and psutil.pid_exists(pid):
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def _deadline_assert_gone(*pids, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    remaining = list(pids)
    while remaining and time.monotonic() < deadline:
        remaining = [pid for pid in remaining if psutil.pid_exists(pid)]
        if remaining:
            time.sleep(0.05)
    assert not remaining, f"processes still alive: {remaining}"


class _FakeProcess:
    """最小 psutil.Process 替身;记录所有终止类调用用于断言。"""

    def __init__(self, pid, create_time, executable, cwd, cmdline, children=(), log=None):
        self.pid = pid
        self._create_time = create_time
        self._executable = executable
        self._cwd = cwd
        self._cmdline = cmdline
        self._children = list(children)
        self.calls = log if log is not None else []

    def create_time(self):
        return self._create_time

    def exe(self):
        return self._executable

    def cwd(self):
        return self._cwd

    def cmdline(self):
        return self._cmdline

    def children(self, recursive=False):
        self.calls.append(("children", self.pid))
        return list(self._children)

    def send_signal(self, sig):
        self.calls.append(("send_signal", self.pid))

    def terminate(self):
        self.calls.append(("terminate", self.pid))

    def kill(self):
        self.calls.append(("kill", self.pid))

    def is_running(self):
        return True

    def status(self):
        return "running"


def _matching_fake(identity, children=(), log=None):
    return _FakeProcess(
        pid=identity.pid,
        create_time=identity.create_time,
        executable=identity.executable,
        cwd=identity.cwd,
        cmdline=["python", "-m", "scripts.compile", "doc_1"],
        children=children,
        log=log,
    )


def _fake_identity(pid=424242, create_time=1.0):
    return ProcessIdentity(
        pid=pid,
        create_time=create_time,
        executable="python",
        cwd="/repo",
        command_fingerprint="scripts.compile|doc_1",
        process_group_id=pid,
        platform=EXPECTED_PLATFORM,
    )


def test_command_fingerprint_ignores_interpreter_absolute_path():
    left = command_fingerprint(["C:/Python/python.exe", "-m", "scripts.compile", "doc_1"])
    right = command_fingerprint(["/usr/bin/python3", "-m", "scripts.compile", "doc_1"])
    assert left == right == "scripts.compile|doc_1"


def test_command_fingerprint_distinguishes_doc_ids():
    first = command_fingerprint(["/usr/bin/python3", "-m", "scripts.compile", "doc_1"])
    second = command_fingerprint(["/usr/bin/python3", "-m", "scripts.compile", "doc_2"])
    assert first != second


def test_command_fingerprint_without_module_flag_drops_interpreter():
    fingerprint = command_fingerprint(["/usr/bin/python3", "-c", "pass"])
    assert fingerprint == "-c|pass"


def test_spawn_compile_process_records_identity_and_failed_exit(tmp_path):
    doc_id = "doc_20260806_001"
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")} if IS_WINDOWS else {}
    spawned = spawn_compile_process(tmp_path, doc_id, env)

    assert isinstance(spawned, SpawnedProcess)
    assert spawned.command_fingerprint == f"scripts.compile|{doc_id}"
    identity = spawned.identity
    assert identity.pid == spawned.popen.pid
    assert identity.command_fingerprint == spawned.command_fingerprint
    assert identity.platform == EXPECTED_PLATFORM
    assert identity.process_group_id
    assert identity.executable
    assert identity.cwd

    # base_dir 为隔离空目录,scripts.compile 必然无法导入 → 快速失败退出。
    result = wait_for_process(spawned, timeout_seconds=60)
    assert result.status == "failed"
    assert result.returncode not in (0, None)


def test_wait_for_process_completed():
    popen = subprocess.Popen(
        [sys.executable, "-c", "print('ok')"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    spawned = SpawnedProcess(popen=popen, identity=None, command_fingerprint="-c|print('ok')")
    result = wait_for_process(spawned, timeout_seconds=60)
    assert result.status == "completed"
    assert result.returncode == 0
    assert "ok" in result.stdout


def test_wait_for_process_timed_out():
    popen = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(30)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    spawned = SpawnedProcess(popen=popen, identity=None, command_fingerprint="-c|sleep")
    try:
        result = wait_for_process(spawned, timeout_seconds=1)
        assert result.status == "timed_out"
        assert result.returncode is None
    finally:
        popen.kill()
        popen.wait()


def test_identity_mismatch_never_terminates_process(monkeypatch):
    identity = _fake_identity(pid=123, create_time=1.0)
    fake = _matching_fake(identity)
    fake._create_time = 2.0
    monkeypatch.setattr(psutil, "Process", lambda _pid: fake)

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "identity_mismatch"
    assert not result.success
    assert "create_time" in result.mismatched_fields
    # 身份不匹配时严禁任何终止动作(不取子进程、不发送信号、不 kill)。
    assert fake.calls == []


def test_verify_process_identity_reports_process_gone():
    identity = _fake_identity(pid=2**22 + 12345)
    status = verify_process_identity(identity)
    assert status.status == "process_gone"

    result = terminate_process_tree(identity, grace_seconds=1)
    assert result.status == "already_gone"
    assert result.success
    assert result.survivors == ()


def test_terminate_reports_survivors_and_kills_descendants_before_root(monkeypatch):
    root_pid, child_pid = 424242, 434343
    log = []
    identity = _fake_identity(pid=root_pid)
    child = _matching_fake(_fake_identity(pid=child_pid), log=log)
    root = _matching_fake(identity, children=[child], log=log)

    monkeypatch.setattr(psutil, "Process", lambda pid: {root_pid: root, child_pid: child}[pid])
    monkeypatch.setattr(psutil, "wait_procs", lambda procs, timeout=None: ([], list(procs)))

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "survivors_remaining"
    assert not result.success
    assert set(result.survivors) == {root_pid, child_pid}
    # 强制终止顺序: 先后代,后根进程。
    kill_order = [pid for name, pid in log if name == "kill"]
    assert kill_order[0] == child_pid
    assert root_pid in kill_order


def test_verify_and_terminate_real_process_tree(tmp_path):
    popen, identity, child_pid = _spawn_helper_tree(tmp_path)
    try:
        assert psutil.pid_exists(popen.pid)
        assert psutil.pid_exists(child_pid)

        status = verify_process_identity(identity)
        assert status.status == "verified"
        assert status.mismatched_fields == ()

        result = terminate_process_tree(identity, grace_seconds=2)
        assert result.status == "terminated", result
        assert result.success
        assert result.survivors == ()

        _deadline_assert_gone(popen.pid, child_pid)
    finally:
        _cleanup_pids(child_pid, popen.pid)
        popen.stdout.close()
        popen.stderr.close()


# ---------------------------------------------------------------------------
# Codex 修复(F1/F2/F6/F7):管道解码、身份捕获失败清理、
# already-gone 幸存者确认、检查中途退出分类
# ---------------------------------------------------------------------------


class _RecordingPopen:
    """记录终止/回收调用的假 Popen;绝不指向真实进程。"""

    def __init__(self, pid=4321):
        self.pid = pid
        self.killed = False
        self.terminated = False
        self.waited = False

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.waited = True
        return -9


def test_spawn_pipes_use_utf8_decoding_with_replacement(tmp_path, monkeypatch):
    """F1: Popen 管道必须显式 encoding="utf-8" + errors="replace",
    且子进程输出非 locale 字节时 communicate 不得抛 UnicodeDecodeError。"""
    captured = {}
    real_popen = subprocess.Popen

    def spy_popen(argv, **kwargs):
        captured.update(kwargs)
        # 替换为受控子进程: 直接向 stdout 写入非 UTF-8/非 locale 原始字节。
        return real_popen(
            [sys.executable, "-c", "import os;os.write(1,b'\\xff\\xfe')"],
            **kwargs,
        )

    monkeypatch.setattr(subprocess, "Popen", spy_popen)
    env = (
        {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows")}
        if IS_WINDOWS
        else {}
    )
    spawned = spawn_compile_process(tmp_path, "doc_enc", env)
    try:
        result = wait_for_process(spawned, timeout_seconds=30)
    finally:
        if spawned.popen.returncode is None:
            spawned.popen.kill()
            spawned.popen.wait()
    assert captured.get("encoding") == "utf-8"
    assert captured.get("errors") == "replace"
    # 原始字节经 replacement 解码而非抛出
    assert result.status == "completed"
    assert "" in result.stdout


def test_spawn_identity_capture_failure_kills_and_reaps_child(
    tmp_path, monkeypatch
):
    """F2: Popen 成功后、身份捕获阶段任何异常(如 AccessDenied)都必须
    best-effort 终止并回收不可跟踪的子进程,再重抛;绝不留存孤儿编译进程。"""
    fake = _RecordingPopen(pid=4321)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(
        psutil, "Process", Mock(side_effect=psutil.AccessDenied(pid=4321))
    )

    with pytest.raises(psutil.AccessDenied):
        spawn_compile_process(tmp_path, "doc_x", {})

    assert fake.killed or fake.terminated
    assert fake.waited


def test_identity_read_no_such_process_mid_check_is_process_gone(monkeypatch):
    """F7: exe()/cwd()/cmdline() 读取时进程刚好退出(NoSuchProcess)是
    process_gone,不是 identity_mismatch;AccessDenied 仍为不匹配证据。"""
    for attr in ("exe", "cwd", "cmdline"):
        identity = _fake_identity()
        fake = _matching_fake(identity)

        def gone(pid=identity.pid):
            raise psutil.NoSuchProcess(pid)

        setattr(fake, attr, gone)
        monkeypatch.setattr(psutil, "Process", lambda _pid, f=fake: f)
        status = verify_process_identity(identity)
        assert status.status == "process_gone", attr


def test_identity_read_access_denied_remains_mismatch_evidence(monkeypatch):
    identity = _fake_identity()
    fake = _matching_fake(identity)

    def denied(pid=identity.pid):
        raise psutil.AccessDenied(pid=pid)

    fake.exe = denied
    monkeypatch.setattr(psutil, "Process", lambda _pid: fake)
    status = verify_process_identity(identity)
    assert status.status == "identity_mismatch"
    assert "executable" in status.mismatched_fields


def _survivor_fake(pid, identity, log=None, children=()):
    """与记录树匹配(同指纹、同 cwd)的伪幸存进程。"""
    return _FakeProcess(
        pid=pid,
        create_time=identity.create_time,
        executable=identity.executable,
        cwd=identity.cwd,
        cmdline=["python", "-m", "scripts.compile", "doc_1"],
        children=children,
        log=log,
    )


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 幸存者扫描分支")
def test_already_gone_with_matching_survivor_terminates_then_succeeds(
    monkeypatch,
):
    """F6: 根进程已退出但存在同指纹同 cwd 的幸存后代时,必须先终止该幸存
    进程并复查干净,才允许返回 already_gone。"""
    identity = _fake_identity(pid=424242)
    log = []
    survivor = _survivor_fake(434343, identity, log=log)
    scan_pool = [survivor]

    def fake_process(pid):
        if pid == identity.pid:
            raise psutil.NoSuchProcess(pid)
        return survivor

    def fake_kill():
        log.append(("kill", survivor.pid))
        scan_pool.clear()

    survivor.kill = fake_kill
    monkeypatch.setattr(psutil, "Process", fake_process)
    monkeypatch.setattr(psutil, "process_iter", lambda: list(scan_pool))

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "already_gone", result
    assert result.success
    assert ("kill", 434343) in log


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 幸存者扫描分支")
def test_already_gone_with_persistent_survivor_fails_closed(monkeypatch):
    """F6: 幸存进程终止无效(复查仍在)时必须 survivors_remaining,绝不
    返回 already_gone。"""
    identity = _fake_identity(pid=424242)
    survivor = _survivor_fake(434343, identity)
    # kill 无效: 进程仍在扫描结果中
    monkeypatch.setattr(
        psutil,
        "Process",
        lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid))
        if pid == identity.pid
        else survivor,
    )
    monkeypatch.setattr(psutil, "process_iter", lambda: [survivor])

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "survivors_remaining"
    assert not result.success
    assert result.survivors == (434343,)


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 幸存者扫描分支")
def test_already_gone_pid_reuse_is_never_killed(monkeypatch):
    """F6 + 身份安全合同: 终止前指纹/cwd 复核无法证明身份(PID 复用或
    进程已退出)时绝不 kill。"""
    identity = _fake_identity(pid=424242)
    log = []
    # 第一次扫描: 指纹匹配(判为幸存);终止前复核 psutil.Process 抛
    # NoSuchProcess(进程已退出/无法证明身份)→ 绝不 kill。
    reused = _survivor_fake(434343, identity, log=log)
    scans = [[reused], []]

    def fake_process(pid):
        raise psutil.NoSuchProcess(pid)

    def fake_scan():
        return scans.pop(0) if scans else []

    monkeypatch.setattr(psutil, "Process", fake_process)
    monkeypatch.setattr(psutil, "process_iter", fake_scan)

    result = terminate_process_tree(identity, grace_seconds=1)

    assert ("kill", 434343) not in log
    assert result.status == "already_gone"


@pytest.mark.skipif(not IS_WINDOWS, reason="Windows 幸存者扫描分支")
def test_already_gone_survivor_check_error_is_uncertain(monkeypatch):
    """F6: 幸存者检查自身失败时不得返回 already_gone;返回非成功的不确定
    状态(失败关闭)。"""
    identity = _fake_identity(pid=424242)
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )

    def boom():
        raise psutil.Error("scan failed")

    monkeypatch.setattr(psutil, "process_iter", boom)

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status != "already_gone"
    assert not result.success


def test_already_gone_posix_group_alive_terminates_group(monkeypatch):
    """F6(POSIX 分支)+ Codex R3 P1-3: 根进程退出但进程组仍存活,且全部
    组成员经指纹 + cwd 复核属于记录树 → SIGTERM 终止组,组消失后才允许
    already_gone。"""
    identity = _fake_identity(pid=424245)
    member = _survivor_fake(434345, identity)
    calls = []

    def fake_killpg(pgid, sig):
        calls.append(sig)
        if sig == 0 and signal.SIGTERM not in calls:
            return  # 组仍存活
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(
        os,
        "getpgid",
        lambda pid: (
            identity.process_group_id if pid == member.pid else pid + 10**9
        ),
        raising=False,
    )
    monkeypatch.setattr(psutil, "process_iter", lambda: [member])
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "already_gone", result
    assert signal.SIGTERM in calls


def test_already_gone_posix_group_empty_returns_already_gone(monkeypatch):
    identity = _fake_identity(pid=424245)

    def fake_killpg(pgid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "already_gone"
    assert result.success


def test_already_gone_posix_group_check_error_is_uncertain(monkeypatch):
    identity = _fake_identity(pid=424245)

    def fake_killpg(pgid, sig):
        raise OSError("unexpected killpg failure")

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status != "already_gone"
    assert not result.success


# ---------------------------------------------------------------------------
# Codex Round 3 (P1-3): POSIX 进程组在发信号前必须绑定记录树身份——
# 组存在性(killpg 0)不是归属证明;PGID 复用或损坏(但可解析)的 Manifest
# 携带的正 PGID 绝不允许误杀无关进程组(防误杀优先于自动恢复)。
# ---------------------------------------------------------------------------


def _posix_group_member(pid, identity, cmdline=None):
    """属于记录进程组的伪成员进程;默认与记录树指纹 + cwd 匹配。"""
    return _FakeProcess(
        pid=pid,
        create_time=identity.create_time,
        executable=identity.executable,
        cwd=identity.cwd,
        cmdline=(
            cmdline
            if cmdline is not None
            else ["python", "-m", "scripts.compile", "doc_1"]
        ),
    )


def _install_posix_probe(monkeypatch, identity, signals, alive=True):
    """安装 killpg 探活替身: alive=True 时 sig=0 成功,否则 ProcessLookupError。"""
    def fake_killpg(pgid, sig):
        signals.append(sig)
        if sig == 0 and not alive:
            raise ProcessLookupError

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )


def test_already_gone_posix_member_fingerprint_mismatch_never_signaled(monkeypatch):
    """P1-3(a): 组存活但成员指纹不匹配 → 绝不 SIGTERM/SIGKILL,
    exit_unconfirmed(失败关闭),绝非 already_gone。"""
    identity = _fake_identity(pid=424245)
    member = _posix_group_member(
        434345, identity, cmdline=["/usr/bin/python3", "-m", "scripts.compile", "doc_2"]
    )
    signals = []
    _install_posix_probe(monkeypatch, identity, signals)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: identity.process_group_id, raising=False
    )
    monkeypatch.setattr(psutil, "process_iter", lambda: [member])

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "exit_unconfirmed"
    assert not result.success
    # 除探活(0)外绝不发送任何信号
    assert all(sig == 0 for sig in signals)


def test_already_gone_posix_pgid_reuse_by_unrelated_group_never_killed(monkeypatch):
    """P1-3(c): PGID 被无关进程组复用(成员与记录树毫无关系)→ 绝不 kill。"""
    identity = _fake_identity(pid=424245)
    member = _posix_group_member(
        434345, identity, cmdline=["/usr/sbin/cron", "-f"]
    )
    signals = []
    _install_posix_probe(monkeypatch, identity, signals)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: identity.process_group_id, raising=False
    )
    monkeypatch.setattr(psutil, "process_iter", lambda: [member])

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "exit_unconfirmed"
    assert all(sig == 0 for sig in signals)


def test_already_gone_posix_membership_flip_before_signal_never_kills(monkeypatch):
    """P1-3(e): 幸存确认与发信号之间组成员翻转(第二次复核不再匹配)→
    SIGTERM/SIGKILL 均不得发出,exit_unconfirmed(失败关闭)。"""
    identity = _fake_identity(pid=424245)
    matching = _posix_group_member(434345, identity)
    foreign = _posix_group_member(
        434346, identity, cmdline=["/usr/bin/python3", "-m", "http.server"]
    )
    scans = [[matching], [foreign], [foreign]]
    signals = []
    _install_posix_probe(monkeypatch, identity, signals)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: identity.process_group_id, raising=False
    )
    monkeypatch.setattr(
        psutil,
        "process_iter",
        lambda: scans.pop(0) if scans else [foreign],
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "exit_unconfirmed"
    assert not result.success
    assert all(sig == 0 for sig in signals)


def test_already_gone_posix_probe_permission_error_fails_closed(monkeypatch):
    """P1-3: killpg 探活 PermissionError(组属于其他用户)→ 无法证明归属,
    绝不发信号,exit_unconfirmed(失败关闭)。"""
    identity = _fake_identity(pid=424245)
    signals = []

    def fake_killpg(pgid, sig):
        signals.append(sig)
        raise PermissionError("group owned by another user")

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(
        psutil,
        "Process",
        Mock(side_effect=psutil.NoSuchProcess(identity.pid)),
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "exit_unconfirmed"
    assert not result.success
    assert all(sig == 0 for sig in signals)


def test_already_gone_posix_group_without_members_returns_already_gone(monkeypatch):
    """P1-3: killpg 探活成功但枚举不到任何成员(竞态: 成员刚好全部退出)
    → 视为组已消失,already_gone,不发信号。"""
    identity = _fake_identity(pid=424245)
    signals = []
    _install_posix_probe(monkeypatch, identity, signals)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: identity.process_group_id, raising=False
    )
    monkeypatch.setattr(psutil, "process_iter", lambda: [])

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "already_gone"
    assert result.success
    assert all(sig == 0 for sig in signals)


# ---------------------------------------------------------------------------
# Codex R3 后续加固: POSIX 协作终止(根进程存活路径)必须把 SIGTERM 发向
# 经验证根进程的活进程组(os.getpgid(root.pid)),绝不发向记录 pgid——
# 记录值可能损坏或与复用组冲突;根进程刚通过五要素身份验证,其活组可信。
# 记录 pgid 仅保留给根进程已退出的恢复路径(_posix_recorded_group_members)。
# ---------------------------------------------------------------------------


def _install_live_root(monkeypatch, identity, root, signals):
    """安装根进程存活路径替身: os.name=posix + killpg 记录 + wait_procs 全灭。"""
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pgid, sig: signals.append((pgid, sig)),
        raising=False,
    )
    monkeypatch.setattr(psutil, "Process", lambda pid: root)
    monkeypatch.setattr(
        psutil, "wait_procs", lambda procs, timeout=None: ([], [])
    )


def test_cooperative_terminate_signals_live_group_of_verified_root(monkeypatch):
    """(a) 记录 pgid 损坏/与活根不一致而根身份验证通过 → SIGTERM 发向
    活根进程组(777777),绝不发向记录 pgid。"""
    identity = _fake_identity(pid=424242)
    root = _matching_fake(identity)
    signals = []
    _install_live_root(monkeypatch, identity, root, signals)
    monkeypatch.setattr(os, "getpgid", lambda pid: 777777, raising=False)

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "terminated"
    assert signals == [(777777, signal.SIGTERM)]
    assert all(pgid != identity.process_group_id for pgid, _ in signals)


def test_cooperative_terminate_falls_back_to_root_terminate_when_getpgid_fails(
    monkeypatch,
):
    """(b) getpgid(root.pid) 抛 OSError → 回退 root.terminate(),
    绝不发向记录 pgid。"""
    identity = _fake_identity(pid=424242)
    log = []
    root = _matching_fake(identity, log=log)
    signals = []
    _install_live_root(monkeypatch, identity, root, signals)

    def broken_getpgid(pid):
        raise OSError("simulated getpgid failure")

    monkeypatch.setattr(os, "getpgid", broken_getpgid, raising=False)

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "terminated"
    assert signals == []
    assert ("terminate", identity.pid) in log


def test_cooperative_terminate_signals_group_exactly_once(monkeypatch):
    """(c) 正常路径(记录 pgid == 活组): 恰向进程组发一次 SIGTERM,
    绝不回退 root.terminate。"""
    identity = _fake_identity(pid=424242)
    log = []
    root = _matching_fake(identity, log=log)
    signals = []
    _install_live_root(monkeypatch, identity, root, signals)
    monkeypatch.setattr(
        os, "getpgid", lambda pid: identity.process_group_id, raising=False
    )

    result = terminate_process_tree(identity, grace_seconds=1)

    assert result.status == "terminated"
    assert signals == [(identity.process_group_id, signal.SIGTERM)]
    assert ("terminate", identity.pid) not in log
