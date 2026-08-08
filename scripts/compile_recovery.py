"""E005 Task 4: 离线编译事务恢复 CLI(设计 §23)。

子命令(仅以下四个,禁止任何绕过类命令):

    python -m scripts.compile_recovery inspect --base-dir .
    python -m scripts.compile_recovery verify <job_id> --base-dir .
    python -m scripts.compile_recovery recover <job_id> --base-dir .
    python -m scripts.compile_recovery cleanup-terminal --base-dir .

- recover 与 cleanup-terminal 先获取 ApiInstanceLock;
- recover 在锁内重扫全部事务目录: 存在其他活动事务或其他 Manifest
  不可读时拒绝恢复(失败关闭,绝不猜测恢复顺序);
- inspect 与 verify 保持只读,但报告实例锁当前是否被持有;
- 恢复、验证与清理全部复用 api.compile_transactions 的恢复引擎,
  不存在 force-delete / ignore-checksum / skip-process-check /
  mark-resolved / start-api-anyway 等绕过入口;
- 输出只包含 job_id、doc_id、状态与脱敏后的诊断,不打印密钥、
  环境变量值或未脱敏错误。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from api.compile_jobs import sanitize_compile_error
from api.compile_transactions import (
    ACTIVE_STATES,
    ERROR_CODE_INTERRUPTED,
    ManifestIntegrityError,
    cleanup_terminal_transactions,
    find_orphan_compiling_docs,
    list_transaction_dirs,
    load_manifest,
    recover_transaction,
    verify_terminal_transaction,
)
from api.runtime_guard import ApiInstanceLock, load_compile_runtime_config

SUBCOMMANDS = ("inspect", "verify", "recover", "cleanup-terminal")

_JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--base-dir",
        default=".",
        help="知识库根目录(默认当前目录)",
    )
    parser = argparse.ArgumentParser(
        prog="scripts.compile_recovery",
        description="E005 离线编译事务恢复 CLI(无绕过命令)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "inspect", parents=[common],
        help="只读列出事务、孤立 compiling 与实例锁状态",
    )
    verify = subparsers.add_parser(
        "verify", parents=[common],
        help="只读验证指定事务(终态验证,不修改任何文件)",
    )
    verify.add_argument("job_id")
    recover = subparsers.add_parser(
        "recover", parents=[common],
        help="恢复指定非终态事务(先获取实例锁)",
    )
    recover.add_argument("job_id")
    subparsers.add_parser(
        "cleanup-terminal", parents=[common],
        help="验证并清理终态事务(先获取实例锁)",
    )
    return parser


def _api_lock_held(config) -> bool:
    """探测实例锁是否被其他进程持有;锁文件不存在时不创建,保持只读。"""
    path = Path(config.instance_lock_path)
    if not path.exists():
        return False
    lock = ApiInstanceLock(path)
    try:
        lock.acquire()
    except Exception:
        return True
    lock.release()
    return False


def _resolve_job_dir(config, job_id: str) -> Path | None:
    if not _JOB_ID_PATTERN.match(job_id):
        return None
    job_dir = Path(config.transaction_dir) / job_id
    return job_dir if job_dir.is_dir() else None


def _cmd_inspect(base_dir: Path, config) -> int:
    print(f"base_dir: {base_dir}")
    print(f"api_instance_lock_held: {_api_lock_held(config)}")
    exit_code = 0
    job_dirs = list_transaction_dirs(config)
    if not job_dirs:
        print("transactions: none")
    for job_dir in job_dirs:
        try:
            manifest = load_manifest(job_dir)
        except ManifestIntegrityError as exc:
            print(
                f"transaction {job_dir.name}: MANIFEST_INTEGRITY_ERROR "
                f"{sanitize_compile_error(exc)}"
            )
            exit_code = 1
            continue
        print(
            f"transaction {manifest.job_id}: state={manifest.state.value} "
            f"kind={manifest.kind.value} doc_id={manifest.doc_id}"
        )
        if manifest.state in ACTIVE_STATES:
            print(f"transaction {manifest.job_id}: ACTIVE, recovery required")
            exit_code = 1
    orphans, unreadable = find_orphan_compiling_docs(base_dir)
    for doc_id in unreadable:
        print(f"unreadable_doc_meta: {doc_id}")
        exit_code = 1
    for doc_id in orphans:
        print(f"orphan_compiling: {doc_id}")
        exit_code = 1
    return exit_code


def _cmd_verify(base_dir: Path, config, job_id: str) -> int:
    print(f"api_instance_lock_held: {_api_lock_held(config)}")
    job_dir = _resolve_job_dir(config, job_id)
    if job_dir is None:
        print(f"unknown or unsafe job_id: {sanitize_compile_error(job_id)}")
        return 1
    try:
        manifest = load_manifest(job_dir)
    except ManifestIntegrityError as exc:
        print(
            f"transaction {job_dir.name}: MANIFEST_INTEGRITY_ERROR "
            f"{sanitize_compile_error(exc)}"
        )
        return 1
    if manifest.state in ACTIVE_STATES:
        print(
            f"transaction {manifest.job_id}: state={manifest.state.value} "
            "is active; run recover instead of verify"
        )
        return 1
    report = verify_terminal_transaction(base_dir, manifest)
    if report.ok:
        print(f"transaction {report.job_id}: state={report.state} verified")
        return 0
    print(f"transaction {report.job_id}: state={report.state} verification failed")
    for failure in report.failures:
        print(f"  - {sanitize_compile_error(failure)}")
    return 1


def _acquire_instance_lock(config) -> ApiInstanceLock | None:
    lock = ApiInstanceLock(config.instance_lock_path)
    try:
        lock.acquire()
    except Exception:
        print(
            "api_instance_lock is held by another process; "
            "refusing to mutate transactions"
        )
        return None
    return lock


def _cmd_recover(base_dir: Path, config, job_id: str) -> int:
    job_dir = _resolve_job_dir(config, job_id)
    if job_dir is None:
        print(f"unknown or unsafe job_id: {sanitize_compile_error(job_id)}")
        return 1
    lock = _acquire_instance_lock(config)
    if lock is None:
        return 1
    try:
        # Codex R3 P1-2: 恢复目标前重扫全部事务目录——存在其他活动事务
        # (目标自身除外)时拒绝恢复: 另一事务的遗留编译进程可能仍在写共享
        # 产物,此时回滚会与残留写入交错;绝不猜测恢复顺序。其他目录的
        # Manifest 不可读同样失败关闭(无法证明无其他活动事务)。
        other_active: list[str] = []
        for other_dir in list_transaction_dirs(config):
            try:
                other = load_manifest(other_dir)
            except ManifestIntegrityError:
                print(
                    f"refusing to recover {job_dir.name}: another transaction "
                    "manifest is unreadable; run inspect and resolve it first"
                )
                return 1
            if other.state in ACTIVE_STATES and other.job_id != job_id:
                other_active.append(other.job_id)
        if other_active:
            print(
                f"refusing to recover {job_dir.name}: other active "
                "transactions exist: "
                + ", ".join(sorted(other_active))
                + "; run inspect and recover them first"
            )
            return 1
        result = recover_transaction(
            base_dir,
            config,
            job_dir,
            reason_code=ERROR_CODE_INTERRUPTED,
            reason_message="manual offline recovery",
        )
    finally:
        lock.release()
    if result.blocked:
        print(
            f"transaction {result.job_id}: recovery blocked: "
            f"{sanitize_compile_error(result.reason)}"
        )
        return 1
    if result.already_terminal:
        print(f"transaction {result.job_id}: already terminal")
        return 0
    print(f"transaction {result.job_id}: recovered ({result.reason or 'rolled back'})")
    return 0


def _cmd_cleanup_terminal(base_dir: Path, config) -> int:
    lock = _acquire_instance_lock(config)
    if lock is None:
        return 1
    try:
        report = cleanup_terminal_transactions(base_dir, config)
    finally:
        lock.release()
    for job_id in report.cleaned:
        print(f"cleaned: {job_id}")
    for job_id in report.kept:
        print(f"kept (active): {job_id}")
    for warning in report.warnings:
        print(f"warning: {sanitize_compile_error(warning)}")
    for blocker in report.blockers:
        print(f"blocker: {sanitize_compile_error(blocker)}")
    return 1 if report.blockers else 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    config = load_compile_runtime_config(base_dir)
    try:
        if args.command == "inspect":
            return _cmd_inspect(base_dir, config)
        if args.command == "verify":
            return _cmd_verify(base_dir, config, args.job_id)
        if args.command == "recover":
            return _cmd_recover(base_dir, config, args.job_id)
        if args.command == "cleanup-terminal":
            return _cmd_cleanup_terminal(base_dir, config)
    except Exception as exc:
        print(f"error: {sanitize_compile_error(exc)}")
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
