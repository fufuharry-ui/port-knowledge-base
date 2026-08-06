"""E005 Task 3: 跨平台进程身份验证与进程树终止测试。

全部测试仅使用 tmp_path 隔离目录与本测试显式启动的辅助进程;
不访问真实知识库目录,不访问网络,不触碰真实 compile 进程。
"""
from __future__ import annotations

import os
import subprocess
import sys

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
