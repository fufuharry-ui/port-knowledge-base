"""E005 Task 14: R1–R8 完整崩溃恢复矩阵(设计 §24.6)与 POSIX 真实进程树证据(§24.3)。

R1–R7 为确定性集成场景:全部在 tmp_path 仓库中构造崩溃现场,重启一律调用
生产恢复入口 recover_startup;绝不使用生产环境开关。

- R1 SCHEDULED 崩溃: 已绑定、进程未启动 → 重启回滚 + interrupted;
- R2 RUNNING 崩溃: 部分产物已改、真实遗留进程树 → 重启终止整树并恢复;
- R3 提交点前崩溃: 子进程成功但 Manifest 仍 RUNNING → 重启回滚成功产物;
- R4 提交点后崩溃: Manifest 已 COMMITTED → 重启只验证清理,绝不回滚;
- R5 回滚中再次崩溃: 部分文件已恢复 → 再次启动幂等完成;
- R6 损坏恢复依据: 快照损坏 → 严格阻断,业务文件字节不变;
- R7 双崩溃: RUNNING 崩溃 → 恢复进入 ROLLBACKING → 再次崩溃 →
  第三次启动逐字节恢复并清理;
- R8 上传六窗口(见下文 R8 区段,Task 10 已实现,保持绿色)。

R5/R7 的"第二次崩溃"经 monkeypatch 的耐久写入边界注入:专用测试异常
_InjectedCrash(BaseException) 在至少一个恢复目标已字节恢复后从
api.compile_transactions.durable_write_bytes 抛出(回滚步骤 1 只捕获
OSError,专用异常穿透即模拟进程崩溃);随后再次调用 recover_startup
完成恢复。

进程树证据:
- Windows 真实进程树测试在 tests/test_process_tree.py
  (test_verify_and_terminate_real_process_tree),本地 Windows 新鲜运行;
- 本文件 test_posix_real_process_tree_group_termination 经 skipif 平台门禁,
  仅在 POSIX 运行(CI ubuntu-latest),Windows 本地运行报告为 skipped,
  不替代本平台自身的真实树测试。

全部测试仅使用 tmp_path 仓库与本测试显式启动的短生命周期辅助进程;
无网络、无模型 Key、不访问真实知识库目录。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psutil
import pytest
import yaml

from api.compile_transactions import (
    META_ACTIVE_JOB_FIELDS,
    ProcessRecord,
    TransactionKind,
    TransactionState,
    create_prepared_transaction,
    find_orphan_compiling_docs,
    load_manifest,
    recover_startup,
    transition_manifest,
)
from api.durable_fs import sha256_file
from api.process_tree import (
    ProcessIdentity,
    command_fingerprint,
    terminate_process_tree,
    verify_process_identity,
)
from api.runtime_guard import (
    ApiInstanceLock,
    ServiceReadiness,
    load_compile_runtime_config,
)
from scripts.ingest import TZ_CST


class _InjectedCrash(BaseException):
    """模拟进程在命名边界崩溃;绝不被 except Exception / except OSError 捕获。"""


# ---------------------------------------------------------------------------
# R1–R7: 重编译事务崩溃场景构造与断言
# ---------------------------------------------------------------------------

RECOMPILE_DOC_ID = "doc_20260808_001"

#: 七项编译产物白名单(与 api.compile_transactions._artifact_relative_paths 一致)。
RECOMPILE_ARTIFACTS = (
    f"wiki/{RECOMPILE_DOC_ID}.summary.yaml",
    "wiki/index.yaml",
    f"meta/ontology/{RECOMPILE_DOC_ID}.ontology.yaml",
    "meta/ontology/global_ontology.yaml",
    f"meta/relations/{RECOMPILE_DOC_ID}.relations.yaml",
    "meta/relations/knowledge_graph.yaml",
    "meta/ontology/entity_relations.yaml",
)

PREPARED = TransactionState.PREPARED
SCHEDULED = TransactionState.SCHEDULED
RUNNING = TransactionState.RUNNING
COMMITTED = TransactionState.COMMITTED
ROLLBACKING = TransactionState.ROLLBACKING

BUSINESS_TREE_SUBDIRS = ("raw", "wiki", "meta", "originals", ".runtime")


def _write_yaml_bytes(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        yaml.dump(data, allow_unicode=True, sort_keys=False).encode("utf-8")
    )


def _hash_tree(root: Path, subdirs=BUSINESS_TREE_SUBDIRS) -> dict:
    """path → sha256 清单,用于证明阻断/恢复路径未触碰业务文件。"""
    result = {}
    for sub in subdirs:
        base = root / sub
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file():
                result[path.relative_to(root).as_posix()] = sha256_file(path)
    return result


def _gone_process_record(doc_id: str) -> ProcessRecord:
    """已退出子进程的身份记录(奇数 pid 在 Windows 不可能存在;POSIX 默认
    pid_max=2^22,该值越界,verify_process_identity 必然判定 process_gone)。"""
    pid = 2**22 + 54321
    return ProcessRecord(
        pid=pid,
        create_time=1786007401.25,
        executable="python",
        cwd=".",
        command_fingerprint=f"scripts.compile|{doc_id}",
        process_group_id=pid,
        platform="windows" if os.name == "nt" else "posix",
    )


class RecompileCrashScenario:
    """在 tmp 仓库中构造重编译事务崩溃现场,驱动恢复并提供字节级断言。"""

    def __init__(self, tmp_path: Path, env: dict | None = None):
        self.repo = Path(tmp_path)
        self.config = load_compile_runtime_config(self.repo, env=env or {})
        self.doc_id = RECOMPILE_DOC_ID
        self.pre_payloads = {
            rel: f"pre-transaction::{rel}".encode("utf-8")
            for rel in RECOMPILE_ARTIFACTS
        }
        self._seed_repo()

    # ------------------------------------------------------------------
    # 仓库种子
    # ------------------------------------------------------------------

    def _seed_repo(self) -> None:
        doc = self.doc_id
        originals = self.repo / "originals"
        originals.mkdir(parents=True)
        (originals / "keep.txt").write_bytes(b"pre-existing original bytes")
        raw = self.repo / "raw"
        raw.mkdir(parents=True)
        (raw / f"{doc}.txt").write_bytes(b"pre-existing raw text")
        self.base_meta = {
            "id": doc,
            "title": "recompile target",
            "source_type": "txt",
            "file_hash": "sha256:seed",
            "status": "compiled",
            "char_count": 21,
        }
        _write_yaml_bytes(raw / f"{doc}.meta.yaml", self.base_meta)
        for rel, payload in self.pre_payloads.items():
            path = self.repo / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)

    # ------------------------------------------------------------------
    # 崩溃现场构造(全部经生产事务 API)
    # ------------------------------------------------------------------

    def prepare(self):
        """经生产 create_prepared_transaction 发布 PREPARED(快照恢复依据)。"""
        self.manifest = create_prepared_transaction(
            base_dir=self.repo,
            config=self.config,
            doc_id=self.doc_id,
            kind=TransactionKind.RECOMPILE,
            previous_meta=dict(self.base_meta),
        )
        return self.manifest

    def bind_meta(self) -> None:
        meta = dict(self.base_meta)
        meta["status"] = "compiling"
        meta["compile_job_id"] = self.manifest.job_id
        _write_yaml_bytes(
            self.repo / "raw" / f"{self.doc_id}.meta.yaml", meta
        )

    def to_scheduled(self):
        self.manifest = transition_manifest(
            self.manifest.job_dir,
            expected=PREPARED,
            target=SCHEDULED,
            scheduled_at="2026-08-08T10:00:01+00:00",
        )
        return self.manifest

    def to_running(self, process: ProcessRecord | None):
        self.manifest = transition_manifest(
            self.manifest.job_dir,
            expected=SCHEDULED,
            target=RUNNING,
            started_at="2026-08-08T10:00:02+00:00",
            deadline="2026-08-08T10:30:02+00:00",
            process=process,
        )
        return self.manifest

    def to_committed(self):
        self.manifest = transition_manifest(
            self.manifest.job_dir,
            expected=RUNNING,
            target=COMMITTED,
        )
        return self.manifest

    def to_rollbacking(self):
        self.manifest = transition_manifest(
            self.manifest.job_dir,
            expected=PREPARED,
            target=ROLLBACKING,
            failure={
                "original_code": "interrupted",
                "original_message": "compile interrupted by service restart",
            },
        )
        return self.manifest

    # ------------------------------------------------------------------
    # 现场改写与断言
    # ------------------------------------------------------------------

    def garbage_artifacts(self, slots) -> None:
        """把指定槽位的产物改写为半成品字节(模拟编译进程部分写入)。"""
        for slot in slots:
            rel = RECOMPILE_ARTIFACTS[slot]
            (self.repo / rel).write_bytes(
                f"compiled-garbage::{rel}".encode("utf-8")
            )

    def write_compiled_outputs(self) -> None:
        """写入语义合法的编译成功产物(模拟子进程成功完成)。"""
        doc = self.doc_id
        _write_yaml_bytes(
            self.repo / "wiki" / f"{doc}.summary.yaml",
            {"doc_id": doc, "abstract": "fresh summary"},
        )
        _write_yaml_bytes(
            self.repo / "wiki" / "index.yaml",
            {"documents": [{"id": doc, "title": "recompile target",
                            "status": "compiled"}]},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / f"{doc}.ontology.yaml",
            {"doc_id": doc, "keywords": ["port"]},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "global_ontology.yaml",
            {"ontology_tree": [], "total_nodes": 0},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / f"{doc}.relations.yaml",
            {"doc_id": doc, "relations": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / "knowledge_graph.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "entity_relations.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(self.repo / "raw" / f"{doc}.meta.yaml", {
            "id": doc,
            "title": "recompile target",
            "status": "compiled",
        })

    def meta(self) -> dict:
        return yaml.safe_load(
            (self.repo / "raw" / f"{self.doc_id}.meta.yaml").read_bytes()
        )

    def artifact_bytes(self, slot: int) -> bytes:
        return (self.repo / RECOMPILE_ARTIFACTS[slot]).read_bytes()

    def artifacts_match_pre(self) -> bool:
        return all(
            self.artifact_bytes(slot) == self.pre_payloads[rel]
            for slot, rel in enumerate(RECOMPILE_ARTIFACTS)
        )

    def transaction_root_empty(self) -> bool:
        root = self.config.transaction_dir
        return not root.is_dir() or not list(root.iterdir())

    def assert_interrupted_terminal(self) -> None:
        """中断回滚的文档终态: error + interrupted + 活动字段清除。"""
        meta = self.meta()
        assert meta["status"] == "error"
        assert meta["error_code"] == "interrupted"
        for field in META_ACTIVE_JOB_FIELDS:
            assert field not in meta


def _install_mid_rollback_crash(mp, *, fail_on_call: int) -> None:
    """在 api.compile_transactions.durable_write_bytes 边界注入第二次崩溃。

    前 fail_on_call - 1 个恢复目标真实字节恢复,第 fail_on_call 次调用抛出
    _InjectedCrash(穿透 except OSError,模拟进程在回滚中途崩溃)。
    """
    import api.compile_transactions as transactions_mod

    real_write = transactions_mod.durable_write_bytes
    calls = {"count": 0}

    def flaky_write(path, payload):
        calls["count"] += 1
        if calls["count"] == fail_on_call:
            raise _InjectedCrash(f"crash on restore call {fail_on_call}")
        return real_write(path, payload)

    mp.setattr(transactions_mod, "durable_write_bytes", flaky_write)


# ---------------------------------------------------------------------------
# R1 SCHEDULED 崩溃: 已绑定、进程未启动 → 重启回滚 + interrupted
# ---------------------------------------------------------------------------


def test_r1_scheduled_crash_rolls_back_with_interrupted(tmp_path):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    manifest = scenario.to_scheduled()
    # 崩溃发生在 SCHEDULED 持久化之后、进程启动之前: process 为 None,
    # 业务产物尚未被触碰。
    assert manifest.process is None

    report = recover_startup(tmp_path, scenario.config)

    assert report.ready is True
    assert report.blockers == []
    assert report.recovered == [manifest.job_id]
    # 回滚 + 终态验证清理;绝不重新排队(无 add_task 路径可触发)。
    assert not manifest.job_dir.exists()
    assert scenario.transaction_root_empty()
    scenario.assert_interrupted_terminal()
    # 业务产物保持事务前字节。
    assert scenario.artifacts_match_pre()


# ---------------------------------------------------------------------------
# R2 RUNNING 崩溃: 部分产物已改 → 重启终止真实遗留进程树并恢复
# ---------------------------------------------------------------------------


def test_r2_running_crash_terminates_leftover_tree_and_restores(tmp_path):
    scenario = RecompileCrashScenario(
        tmp_path, env={"COMPILE_TERMINATION_GRACE_SECONDS": "2"}
    )
    scenario.prepare()
    scenario.bind_meta()
    scenario.to_scheduled()
    # 真实短生命周期遗留树: 父进程派生子进程后双双睡眠(模拟 API 崩溃后
    # 存活的编译进程树)。
    popen, identity, child_pid = _spawn_helper_tree(tmp_path)
    try:
        record = ProcessRecord(
            pid=identity.pid,
            create_time=identity.create_time,
            executable=identity.executable,
            cwd=identity.cwd,
            command_fingerprint=identity.command_fingerprint,
            process_group_id=identity.process_group_id,
            platform=identity.platform,
        )
        manifest = scenario.to_running(record)
        scenario.garbage_artifacts((0, 1, 2))

        report = recover_startup(tmp_path, scenario.config)

        assert report.ready is True
        assert report.blockers == []
        assert report.recovered == [manifest.job_id]
        # 遗留树整树退出(根进程与后代)。
        _assert_pids_gone(popen.pid, child_pid)
        # 半成品产物逐字节恢复为事务前内容。
        assert scenario.artifacts_match_pre()
        scenario.assert_interrupted_terminal()
        assert not manifest.job_dir.exists()
        assert scenario.transaction_root_empty()
    finally:
        _cleanup_pids(child_pid, popen.pid)
        popen.stdout.close()
        popen.stderr.close()


# ---------------------------------------------------------------------------
# R3 提交点前崩溃: 子进程成功但 Manifest 仍 RUNNING → 重启回滚
# ---------------------------------------------------------------------------


def test_r3_pre_commit_point_crash_rolls_back_successful_outputs(tmp_path):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    scenario.to_scheduled()
    # 子进程已成功退出(process_gone)并写出全部成功产物;API 在 COMMITTED
    # 翻转前崩溃,Manifest 停留 RUNNING。
    manifest = scenario.to_running(_gone_process_record(scenario.doc_id))
    scenario.write_compiled_outputs()

    report = recover_startup(tmp_path, scenario.config)

    assert report.ready is True
    assert report.blockers == []
    assert report.recovered == [manifest.job_id]
    # 提交点前一律回滚: 成功形态产物被撤销,逐字节恢复为事务前内容。
    assert scenario.artifacts_match_pre()
    scenario.assert_interrupted_terminal()
    assert not manifest.job_dir.exists()
    assert scenario.transaction_root_empty()


# ---------------------------------------------------------------------------
# R4 提交点后崩溃: Manifest 已 COMMITTED → 重启只验证清理,绝不回滚
# ---------------------------------------------------------------------------


def test_r4_post_commit_point_crash_only_verifies_and_cleans(tmp_path):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    scenario.to_scheduled()
    scenario.to_running(_gone_process_record(scenario.doc_id))
    manifest = scenario.to_committed()
    scenario.write_compiled_outputs()
    before = _hash_tree(tmp_path, subdirs=("raw", "wiki", "meta", "originals"))

    report = recover_startup(tmp_path, scenario.config)

    assert report.ready is True
    assert report.blockers == []
    # COMMITTED 是终态: 不进入恢复列表,只验证后清理事务目录。
    assert report.recovered == []
    assert manifest.job_id in report.cleaned
    assert not manifest.job_dir.exists()
    assert scenario.transaction_root_empty()
    # 绝不回滚: 编译成功产物与 meta 逐字节保持。
    assert _hash_tree(tmp_path, subdirs=("raw", "wiki", "meta", "originals")) == before
    meta = scenario.meta()
    assert meta["status"] == "compiled"
    assert "error_code" not in meta


# ---------------------------------------------------------------------------
# R5 回滚中再次崩溃: 部分文件已恢复 → 再次启动幂等完成
# ---------------------------------------------------------------------------


def test_r5_rollback_crash_again_completes_idempotently(tmp_path):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    manifest = scenario.to_rollbacking()
    scenario.garbage_artifacts(range(len(RECOMPILE_ARTIFACTS)))

    # 第二次崩溃: 回滚已真实恢复前两个目标,第三个目标的耐久写入边界崩溃。
    with pytest.MonkeyPatch.context() as mp:
        _install_mid_rollback_crash(mp, fail_on_call=3)
        with pytest.raises(_InjectedCrash):
            recover_startup(tmp_path, scenario.config)

    # 崩溃现场: Manifest 保持 ROLLBACKING;前两项已字节恢复,其余仍半成品。
    assert load_manifest(manifest.job_dir).state is ROLLBACKING
    assert scenario.artifact_bytes(0) == scenario.pre_payloads[RECOMPILE_ARTIFACTS[0]]
    assert scenario.artifact_bytes(1) == scenario.pre_payloads[RECOMPILE_ARTIFACTS[1]]
    for slot in range(2, len(RECOMPILE_ARTIFACTS)):
        assert scenario.artifact_bytes(slot) != scenario.pre_payloads[
            RECOMPILE_ARTIFACTS[slot]
        ]

    # 再次启动: 从同一 ROLLBACKING 状态幂等继续并完成。
    report = recover_startup(tmp_path, scenario.config)
    assert report.ready is True
    assert report.blockers == []
    assert report.recovered == [manifest.job_id]
    assert scenario.artifacts_match_pre()
    scenario.assert_interrupted_terminal()
    assert not manifest.job_dir.exists()
    assert scenario.transaction_root_empty()

    # 幂等: 第三次启动无副作用、不改变任何业务字节。
    settled = _hash_tree(tmp_path)
    again = recover_startup(tmp_path, scenario.config)
    assert again.ready is True
    assert again.recovered == []
    assert _hash_tree(tmp_path) == settled


# ---------------------------------------------------------------------------
# R6 损坏恢复依据: 快照损坏 → 严格阻断,业务文件字节不变
# ---------------------------------------------------------------------------


def test_r6_corrupted_recovery_basis_blocks_without_touching_business_files(
    tmp_path,
):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    manifest = scenario.to_scheduled()
    # 损坏恢复依据: 快照 00 的字节翻转(大小不变,仅 SHA-256 不匹配)。
    snapshot_file = manifest.job_dir / "snapshots" / "00.bin"
    payload = bytearray(snapshot_file.read_bytes())
    payload[0] ^= 0xFF
    snapshot_file.write_bytes(bytes(payload))
    before = _hash_tree(tmp_path)

    report = recover_startup(tmp_path, scenario.config)

    assert report.ready is False
    assert report.recovered == []
    assert any(
        manifest.job_id in blocker and "integrity" in blocker
        for blocker in report.blockers
    )
    # 严格阻断: 事务目录保留证据,业务文件与 .runtime 逐字节不变。
    assert manifest.job_dir.exists()
    assert _hash_tree(tmp_path) == before
    assert scenario.meta()["status"] == "compiling"


# ---------------------------------------------------------------------------
# R7 双崩溃: RUNNING 崩溃 → 恢复进入 ROLLBACKING → 再次崩溃 →
#            第三次启动逐字节恢复并清理
# ---------------------------------------------------------------------------


def test_r7_double_crash_third_start_byte_restores_and_cleans(tmp_path):
    scenario = RecompileCrashScenario(tmp_path)
    scenario.prepare()
    scenario.bind_meta()
    scenario.to_scheduled()
    manifest = scenario.to_running(_gone_process_record(scenario.doc_id))
    scenario.garbage_artifacts(range(len(RECOMPILE_ARTIFACTS)))

    # 第二次启动: 恢复进入 ROLLBACKING 后,第一个目标已恢复,第二个目标的
    # 耐久写入边界再次崩溃。
    with pytest.MonkeyPatch.context() as mp:
        _install_mid_rollback_crash(mp, fail_on_call=2)
        with pytest.raises(_InjectedCrash):
            recover_startup(tmp_path, scenario.config)

    reloaded = load_manifest(manifest.job_dir)
    assert reloaded.state is ROLLBACKING
    # 原始失败原因在第一次恢复时已持久化并保留。
    assert reloaded.failure["original_code"] == "interrupted"
    assert scenario.artifact_bytes(0) == scenario.pre_payloads[RECOMPILE_ARTIFACTS[0]]
    for slot in range(1, len(RECOMPILE_ARTIFACTS)):
        assert scenario.artifact_bytes(slot) != scenario.pre_payloads[
            RECOMPILE_ARTIFACTS[slot]
        ]

    # 第三次启动: 逐字节恢复全部产物并验证清理。
    report = recover_startup(tmp_path, scenario.config)
    assert report.ready is True
    assert report.blockers == []
    assert report.recovered == [manifest.job_id]
    assert scenario.artifacts_match_pre()
    scenario.assert_interrupted_terminal()
    assert not manifest.job_dir.exists()
    assert scenario.transaction_root_empty()


# ---------------------------------------------------------------------------
# POSIX 真实短生命周期进程树证据(设计 §24.3)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX 真实进程树证据: 仅在 POSIX 运行(CI ubuntu-latest); "
    "Windows 本地运行报告为 skipped,Windows 真实树证据见 "
    "tests/test_process_tree.py",
)
def test_posix_real_process_tree_group_termination(tmp_path):
    """POSIX: session 首领派生子进程,killpg 路径整树终止。"""
    popen, identity, child_pid = _spawn_helper_tree(tmp_path)
    try:
        assert identity.platform == "posix"
        assert identity.process_group_id == os.getpgid(popen.pid)

        status = verify_process_identity(identity)
        assert status.status == "verified"
        assert status.mismatched_fields == ()

        result = terminate_process_tree(identity, grace_seconds=2)
        assert result.status == "terminated", result
        assert result.success
        assert result.survivors == ()

        _assert_pids_gone(popen.pid, child_pid)
    finally:
        _cleanup_pids(child_pid, popen.pid)
        popen.stdout.close()
        popen.stderr.close()


# ---------------------------------------------------------------------------
# 真实进程树辅助(本测试显式启动的短生命周期进程;绝不触碰真实编译进程)
# ---------------------------------------------------------------------------

_HELPER_CHILD_CODE = "import time;time.sleep(120)"
_HELPER_PARENT_CODE = (
    "import subprocess,sys,time\n"
    f"child = subprocess.Popen([sys.executable,'-c',{_HELPER_CHILD_CODE!r}])\n"
    "print(child.pid, flush=True)\n"
    "time.sleep(120)\n"
)


def _spawn_helper_tree(cwd: Path):
    """以生产 spawn 同等机制启动 父进程→子进程 辅助树。"""
    is_windows = os.name == "nt"
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if is_windows else 0
    popen = subprocess.Popen(
        [sys.executable, "-c", _HELPER_PARENT_CODE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(cwd),
        creationflags=creationflags,
        start_new_session=not is_windows,
    )
    child_pid = int(popen.stdout.readline().strip())
    proc = psutil.Process(popen.pid)
    identity = ProcessIdentity(
        pid=popen.pid,
        create_time=proc.create_time(),
        executable=proc.exe(),
        cwd=proc.cwd(),
        command_fingerprint=command_fingerprint(proc.cmdline()),
        process_group_id=popen.pid if is_windows else os.getpgid(popen.pid),
        platform="windows" if is_windows else "posix",
    )
    return popen, identity, child_pid


def _cleanup_pids(*pids) -> None:
    for pid in pids:
        if pid and psutil.pid_exists(pid):
            try:
                psutil.Process(pid).kill()
            except psutil.NoSuchProcess:
                pass


def _assert_pids_gone(*pids, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    remaining = list(pids)
    while remaining and time.monotonic() < deadline:
        remaining = [pid for pid in remaining if psutil.pid_exists(pid)]
        if remaining:
            time.sleep(0.05)
    assert not remaining, f"processes still alive: {remaining}"


# ---------------------------------------------------------------------------
# R8 上传崩溃窗口恢复测试(设计 §12.3、§12.4、§24.6 R8;Task 10 已实现)
#
# 六个崩溃窗口全部经命名边界注入,绝不使用生产环境开关:
#
# 1. after_intake_stage       → api.main._r8_boundary_after_intake_stage
# 2. after_prepare_ingest     → api.main._r8_boundary_after_prepare_ingest
# 3. after_prepared_manifest  → api.main._r8_boundary_after_prepared_manifest
# 4. after_original_publish   → api.upload_intake.durable_write_bytes
#                              (original 发布 + journal 后、raw text 发布前)
# 5. after_raw_text_publish   → api.upload_intake.durable_write_yaml
#                              (raw text 发布 + journal 后、raw meta 发布前)
# 6. after_scheduled          → api.main._r8_boundary_after_scheduled
#                              (SCHEDULED 持久迁移后、add_task 前)
#
# 崩溃以专用 _InjectedCrash(BaseException)注入:请求线程的 except Exception
# 恢复处理不捕获它,try/finally 只丢弃本请求未接受的 intake staging(与真实
# 崩溃后由 recover_startup 清理 .staging-* 等价),其余现场保持崩溃时刻状态,
# 由 recover_startup 完成恢复。
#
# 每个窗口恢复后必须满足(设计 §24.6 R8):
# - report.blockers == [];
# - 无孤立 doc(本轮 raw meta/txt、originals 新文件全部不存在);
# - 无孤立 compiling;
# - 共享产物(index/本体/关系全局文件)与崩溃前字节一致;
# - 既有业务文件字节不变;
# - after_scheduled 窗口的上传按中断事务完整撤销(本轮 original/raw 删除,
#   无孤儿 error 文档)。
# ---------------------------------------------------------------------------

UPLOAD_NAME = "upload.txt"
UPLOAD_PAYLOAD = b"port upload crash window payload"

#: 共享全局产物(崩溃前后必须字节一致)。
SHARED_ARTIFACTS = (
    "wiki/index.yaml",
    "meta/ontology/global_ontology.yaml",
    "meta/relations/knowledge_graph.yaml",
    "meta/ontology/entity_relations.yaml",
)

CRASH_POINTS = (
    "after_intake_stage",
    "after_prepare_ingest",
    "after_prepared_manifest",
    "after_original_publish",
    "after_raw_text_publish",
    "after_scheduled",
)


class FakeUploadFile:
    """最小 UploadFile stub: 仅暴露 .file 与 .filename。"""

    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self.file = io.BytesIO(payload)


class UploadCrashScenario:
    """在 tmp 仓库中驱动两阶段上传流程直到指定崩溃窗口,并提供恢复后断言。"""

    def __init__(self, tmp_path: Path, crash_point: str):
        assert crash_point in CRASH_POINTS
        self.repo = Path(tmp_path)
        self.crash_point = crash_point
        self.config = load_compile_runtime_config(self.repo, env={})
        today = datetime.now(TZ_CST).strftime("%Y%m%d")
        self.existing_doc_id = f"doc_{today}_001"
        self.new_doc_id = f"doc_{today}_002"
        self._seed_repo()
        self._before_shared = self._hash_paths(SHARED_ARTIFACTS)
        self._before_preexisting = self._hash_paths(self._preexisting_files)

    # ------------------------------------------------------------------
    # 仓库种子与快照
    # ------------------------------------------------------------------

    def _seed_repo(self) -> None:
        """一篇既有 compiled 文档 + 四个共享全局产物;既有 doc_id 占今日 001 序号。"""
        doc = self.existing_doc_id
        originals = self.repo / "originals"
        originals.mkdir(parents=True)
        (originals / "existing.txt").write_bytes(b"existing original bytes")
        raw = self.repo / "raw"
        raw.mkdir(parents=True)
        (raw / f"{doc}.txt").write_bytes(b"existing raw text")
        _write_yaml_bytes(raw / f"{doc}.meta.yaml", {
            "id": doc,
            "title": "existing",
            "source_type": "txt",
            "file_hash": "sha256:existing",
            "status": "compiled",
            "char_count": 17,
        })
        _write_yaml_bytes(self.repo / "wiki" / "index.yaml", {
            "documents": [{
                "id": doc,
                "title": "existing",
                "file_hash": "sha256:existing",
                "status": "compiled",
            }],
        })
        _write_yaml_bytes(
            self.repo / "wiki" / f"{doc}.summary.yaml",
            {"doc_id": doc, "abstract": "existing summary"},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "global_ontology.yaml",
            {"ontology_tree": [], "total_nodes": 0},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / f"{doc}.ontology.yaml",
            {"doc_id": doc, "keywords": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "ontology" / "entity_relations.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / "knowledge_graph.yaml",
            {"edges": []},
        )
        _write_yaml_bytes(
            self.repo / "meta" / "relations" / f"{doc}.relations.yaml",
            {"doc_id": doc, "relations": []},
        )
        self._preexisting_files = [
            f"originals/existing.txt",
            f"raw/{doc}.txt",
            f"raw/{doc}.meta.yaml",
            f"wiki/{doc}.summary.yaml",
            f"meta/ontology/{doc}.ontology.yaml",
            f"meta/relations/{doc}.relations.yaml",
            *SHARED_ARTIFACTS,
        ]

    def _hash_paths(self, relative_paths) -> dict:
        result = {}
        for rel in relative_paths:
            path = self.repo / rel
            result[rel] = sha256_file(path) if path.is_file() else None
        return result

    # ------------------------------------------------------------------
    # 崩溃注入与流程驱动
    # ------------------------------------------------------------------

    def _install_crash(self, mp, api_mod, intake_mod) -> None:
        def crash(_arg=None):
            raise _InjectedCrash(self.crash_point)

        if self.crash_point == "after_intake_stage":
            mp.setattr(api_mod, "_r8_boundary_after_intake_stage", crash)
        elif self.crash_point == "after_prepare_ingest":
            mp.setattr(api_mod, "_r8_boundary_after_prepare_ingest", crash)
        elif self.crash_point == "after_prepared_manifest":
            mp.setattr(api_mod, "_r8_boundary_after_prepared_manifest", crash)
        elif self.crash_point == "after_original_publish":
            real_write_bytes = intake_mod.durable_write_bytes

            def fail_on_raw_text(path, payload, **kwargs):
                path = Path(path)
                if path.parent.name == "raw" and path.suffix == ".txt":
                    raise _InjectedCrash(self.crash_point)
                return real_write_bytes(path, payload, **kwargs)

            mp.setattr(intake_mod, "durable_write_bytes", fail_on_raw_text)
        elif self.crash_point == "after_raw_text_publish":
            real_write_yaml = intake_mod.durable_write_yaml

            def fail_on_raw_meta(path, data, **kwargs):
                path = Path(path)
                if path.parent.name == "raw" and path.name.endswith(".meta.yaml"):
                    raise _InjectedCrash(self.crash_point)
                return real_write_yaml(path, data, **kwargs)

            mp.setattr(intake_mod, "durable_write_yaml", fail_on_raw_meta)
        elif self.crash_point == "after_scheduled":
            mp.setattr(api_mod, "_r8_boundary_after_scheduled", crash)

    def _patch_all_path_constants(self, mp, api_mod) -> None:
        """把上传流程可能触及的全部模块级路径常量重绑定到 tmp 仓库。

        隔离合同(测试污染事故修复):两阶段流程及其降级路径绝不读取或写入
        真实知识库目录。除 api.main 外,scripts.ingest 拥有独立的模块级路径
        常量(旧 _accept_upload 经 ingest_file 写真实 raw/),scripts.logger
        的 global_logger 按 CWD 解析 wiki/log.md;任何一个漏 patch 都会污染
        真实仓库。此处全量重绑定,并在驱动前逐一断言常量落在 tmp 仓库内。
        """
        import scripts.ingest as ingest_mod
        import scripts.logger as logger_mod

        wiki_dir = self.repo / "wiki"
        raw_dir = self.repo / "raw"
        originals_dir = self.repo / "originals"
        meta_dir = self.repo / "meta"
        index_file = wiki_dir / "index.yaml"

        for module, names in (
            (api_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                       "META_DIR", "INDEX_FILE")),
            (ingest_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                          "INDEX_FILE")),
        ):
            for name in names:
                target = {
                    "BASE_DIR": self.repo,
                    "RAW_DIR": raw_dir,
                    "ORIGINALS_DIR": originals_dir,
                    "WIKI_DIR": wiki_dir,
                    "META_DIR": meta_dir,
                    "INDEX_FILE": index_file,
                }[name]
                mp.setattr(module, name, target, raising=False)
        sandbox_logger = logger_mod.ActivityLogger(wiki_dir)
        mp.setattr(logger_mod, "global_logger", sandbox_logger)

        # 驱动前 fail-closed:任何常量逃出 tmp 仓库即拒绝运行(防污染护栏)。
        for module, names in (
            (api_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                       "META_DIR", "INDEX_FILE")),
            (ingest_mod, ("BASE_DIR", "RAW_DIR", "ORIGINALS_DIR", "WIKI_DIR",
                          "INDEX_FILE")),
        ):
            for name in names:
                value = Path(getattr(module, name)).resolve()
                assert value == self.repo.resolve() or self.repo.resolve() in value.parents, (
                    f"{module.__name__}.{name} escapes the tmp repo: {value}"
                )

    def run_until_crash(self) -> None:
        """在调度锁与命名边界语义下运行上传流程,直到注入的崩溃。"""
        import api.main as api_mod
        import api.upload_intake as intake_mod
        from fastapi import BackgroundTasks

        with pytest.MonkeyPatch.context() as mp:
            self._patch_all_path_constants(mp, api_mod)
            self._install_crash(mp, api_mod, intake_mod)
            runtime = api_mod.AppRuntime(
                config=self.config,
                readiness=ServiceReadiness(),
                instance_lock=ApiInstanceLock(
                    self.repo / ".runtime" / "api-instance.lock"
                ),
            )
            upload = FakeUploadFile(UPLOAD_NAME, UPLOAD_PAYLOAD)
            with pytest.raises(_InjectedCrash):
                api_mod._accept_upload(BackgroundTasks(), upload, runtime)

    # ------------------------------------------------------------------
    # 恢复后断言
    # ------------------------------------------------------------------

    def orphan_docs(self) -> list[str]:
        """本轮上传残留的业务文件(raw/ 与 originals/ 中非既有文件)。"""
        orphans: list[str] = []
        for sub in ("raw", "originals"):
            base = self.repo / sub
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(self.repo).as_posix()
                if rel not in self._before_preexisting:
                    orphans.append(rel)
        return orphans

    def orphan_compiling(self) -> list[str]:
        orphans, unreadable = find_orphan_compiling_docs(self.repo)
        return sorted(orphans + unreadable)

    def shared_artifacts_match_before(self) -> bool:
        return self._hash_paths(SHARED_ARTIFACTS) == self._before_shared

    def preexisting_files_unchanged(self) -> bool:
        current = self._hash_paths(self._preexisting_files)
        return current == self._before_preexisting

    def runtime_dirs_clean(self) -> bool:
        """恢复后事务目录与 intake staging 全部清空。"""
        for root in (
            self.config.transaction_dir,
            self.config.upload_intake_dir,
        ):
            if root.is_dir() and list(root.iterdir()):
                return False
        return True

    def upload_fully_revoked(self) -> bool:
        """after_scheduled 窗口:本轮 original/raw text/raw meta 全部撤销。"""
        return not (
            (self.repo / "originals" / UPLOAD_NAME).exists()
            or (self.repo / "raw" / f"{self.new_doc_id}.txt").exists()
            or (self.repo / "raw" / f"{self.new_doc_id}.meta.yaml").exists()
        )


@pytest.mark.parametrize("crash_point", list(CRASH_POINTS))
def test_r8_upload_crash_windows_recover_without_orphans(tmp_path, crash_point):
    scenario = UploadCrashScenario(tmp_path, crash_point)
    scenario.run_until_crash()
    report = recover_startup(tmp_path, scenario.config)
    assert report.blockers == []
    assert scenario.orphan_docs() == []
    assert scenario.orphan_compiling() == []
    assert scenario.shared_artifacts_match_before()
    assert scenario.preexisting_files_unchanged()
    assert scenario.runtime_dirs_clean()
    if crash_point == "after_scheduled":
        # 已进入 SCHEDULED 的上传按中断事务完整撤销,不保留孤儿 error 文档
        assert scenario.upload_fully_revoked()
