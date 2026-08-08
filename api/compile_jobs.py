"""E005 Task 5: 持久化编译执行器与硬超时编排。

对应设计文档第 11 节(重编译事务准备)、第 13 节(执行器与进程身份)、
第 14 节(硬超时与进程树终止)、第 15 节(唯一提交点与成功验证)和
第 16 节(幂等回滚编排)。

职责边界:

- prepare_recompile_transaction 只做纯准备(读取并保存原 meta +
  create_prepared_transaction 发布 PREPARED);不绑定 meta、不迁移
  SCHEDULED、不登记后台任务——这些是 API 调度层的职责;
- run_compile_task 以 job_id 为输入,在执行锁内按
  SCHEDULED → RUNNING → COMMITTED / ROLLBACKING → ROLLED_BACK 推进;
- Manifest 原子进入 COMMITTED 是唯一成功提交点;提交点前的任何失败、
  语义验证失败或硬超时一律回滚;
- 超时顺序固定: Manifest=ROLLBACKING(reason=timeout)→ 终止进程树
  → 等待宽限 → 强制终止 → 确认退出 → 回滚;无法确认退出时失败关闭,
  readiness 进入 recovery_required,绝不回滚、绝不写文档终态;
- 后台任务绝不抛出: 非法 job id 只记录日志;well-formed job 但事务目录
  丢失按证据丢失失败关闭(recovery_required);任何无法证明一致性的
  基础设施失败进入 recovery_required 并返回。

Manifest failure 字段与 raw meta 错误信息一律先经 sanitize_compile_error
脱敏;绝不写入原始 stderr/stdout 或密钥。
"""
from __future__ import annotations

import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal, Mapping

import yaml

from api.compile_transactions import (
    ERROR_CODE_INTERRUPTED,
    ManifestIntegrityError,
    ProcessRecord,
    TransactionKind,
    TransactionState,
    artifact_paths,
    cleanup_terminal_transactions,
    create_prepared_transaction,
    load_manifest,
    recover_transaction,
    transition_manifest,
)
from api.durable_fs import fsync_existing_file
from api.process_tree import (
    STATUS_COMPLETED,
    STATUS_TIMED_OUT,
    ProcessResult,
    SpawnedProcess,
    TerminationResult,
    spawn_compile_process,
    terminate_process_tree,
    wait_for_process,
)
from api.runtime_guard import (
    CompileRuntimeConfig,
    ServiceReadiness,
    load_compile_runtime_config,
)
from scripts.doc_admin import read_doc_meta, write_doc_compile_result

__all__ = [
    "artifact_paths",
    "classify_compile_error",
    "prepare_recompile_transaction",
    "restore_transaction_artifacts",
    "run_compile_task",
    "sanitize_compile_error",
]

logger = logging.getLogger(__name__)

COMPILE_EXECUTION_LOCK = threading.Lock()

CompileErrorCode = Literal[
    "llm_configuration",
    "service_unavailable",
    "timeout",
    "document_processing",
    "compile_failed",
    "rollback_failed",
]

MAX_ERROR_MESSAGE = 500
MAX_CAPTURE_CHARS = 8192

_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")

# E004-FIX-02:带引号键值(JSON/Python dict/header/env)统一由 callable 脱敏,
# 覆盖单/双引号键、单/双/无引号值、:与=、键值间任意空格;
# authorization 整行模式必须先于通用凭据模式,处理无引号且含空格的值
# (如 "Authorization: Bearer xxx"/"Authorization: Basic xxx")。
_CREDENTIAL_KEY_PATTERN = re.compile(
    r"(?P<key>[\"']?(?:x[_-]api[_-]key|api[_-]?key|access[_-]token"
    r"|client[_-]secret|token|secret|password|authorization)[\"']?)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;\}\)&]+)",
    re.IGNORECASE,
)


def _redact_credential(match: re.Match) -> str:
    return f"{match.group('key')}{match.group('sep')}<redacted>"


_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)"),
        r"\1<redacted>",
    ),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"), "sk-<redacted>"),
    (_CREDENTIAL_KEY_PATTERN, _redact_credential),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|apikey|access_token|token|key)=)[^&\s]+"),
        r"\1<redacted>",
    ),
)


def sanitize_compile_error(text: str, limit: int = MAX_ERROR_MESSAGE) -> str:
    safe = str(text or "")
    for pattern, replacement in _SECRET_PATTERNS:
        safe = pattern.sub(replacement, safe)
    safe = re.sub(r"\s+", " ", safe).strip()
    return (safe or "编译失败")[:limit]


def classify_compile_error(text: str) -> CompileErrorCode:
    value = str(text or "").lower()
    if any(token in value for token in (
        "openai_api_key", "api key", "api_key", "unauthorized", "invalid key",
        "authentication", "请安装 openai", "no module named 'openai'",
    )):
        return "llm_configuration"
    if any(token in value for token in (
        "timeout", "timed out", "readtimeout", "connecttimeout", "超时",
    )):
        return "timeout"
    if any(token in value for token in (
        "connection refused", "connection error", "service unavailable",
        "bad gateway", "name resolution", "network is unreachable", "服务不可用",
    )) or re.search(r"\b5[0-9]{2}\b", value):
        return "service_unavailable"
    if any(token in value for token in (
        "找不到原始文本", "document content", "decode", "parse", "内容为空",
    )):
        return "document_processing"
    return "compile_failed"


# artifact_paths 由 api.compile_transactions 提供并在此再导出,
# 保持 E004 既有导入路径 from api.compile_jobs import artifact_paths 兼容。


# ---------------------------------------------------------------------------
# 重编译事务纯准备(设计 §11 至 PREPARED 发布)
# ---------------------------------------------------------------------------


def prepare_recompile_transaction(
    doc_id: str,
    base_dir: Path,
    config: CompileRuntimeConfig,
):
    """读取并保存原 meta,发布 PREPARED 重编译事务。

    只做纯准备: 不绑定 meta、不迁移 SCHEDULED、不登记后台任务。
    任何失败都抛出给调用方,meta 保持字节不变,staging 由
    create_prepared_transaction 负责清理。
    """
    base = Path(base_dir)
    previous_meta = read_doc_meta(doc_id, base)
    # F5: 快照原 meta 文件字节(注释/格式不丢失);未接受回滚逐字节恢复。
    meta_path = base / "raw" / f"{doc_id}.meta.yaml"
    previous_meta_bytes = (
        meta_path.read_bytes() if meta_path.is_file() else None
    )
    return create_prepared_transaction(
        base_dir=base,
        config=config,
        doc_id=doc_id,
        kind=TransactionKind.RECOMPILE,
        previous_meta=previous_meta,
        previous_meta_bytes=previous_meta_bytes,
    )


# ---------------------------------------------------------------------------
# 恢复编排边界(独立命名以便替换与测试;委托 Task 4 恢复库)
# ---------------------------------------------------------------------------


def restore_transaction_artifacts(
    base_dir: Path,
    config: CompileRuntimeConfig,
    job_dir: Path,
    *,
    reason_code: str,
    reason_message: str,
):
    """委托恢复库执行幂等回滚(设计 §16)。

    调用前 Manifest 必须已进入 ROLLBACKING 并保存原始失败原因;
    recover_transaction 保留原始原因,绝不覆盖。
    """
    return recover_transaction(
        base_dir,
        config,
        job_dir,
        reason_code=reason_code,
        reason_message=reason_message,
    )


# ---------------------------------------------------------------------------
# 成功语义验证(设计 §15)
# ---------------------------------------------------------------------------


def _load_yaml_mapping(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _read_doc_meta_safe(base: Path, doc_id: str) -> dict | None:
    try:
        meta = read_doc_meta(doc_id, base)
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("doc meta unreadable during compile validation: %s", exc)
        return None
    return meta if isinstance(meta, dict) else None


def _validate_compile_outputs(base: Path, doc_id: str) -> list[str]:
    """设计 §15 必需产物语义验证;返回失败列表,空列表表示合法。"""
    failures: list[str] = []

    for path, label in (
        (base / "wiki" / f"{doc_id}.summary.yaml", "summary"),
        (base / "meta" / "ontology" / f"{doc_id}.ontology.yaml", "ontology"),
    ):
        data = _load_yaml_mapping(path)
        if data is None:
            failures.append(f"{label} missing or unparsable")
        elif data.get("doc_id") != doc_id:
            failures.append(f"{label} doc_id mismatch")

    index = _load_yaml_mapping(base / "wiki" / "index.yaml")
    documents = index.get("documents") if index else None
    if not isinstance(documents, list) or sum(
        1
        for entry in documents
        if isinstance(entry, Mapping) and entry.get("id") == doc_id
    ) != 1:
        failures.append("wiki/index.yaml must contain exactly one entry for doc")

    if _load_yaml_mapping(base / "raw" / f"{doc_id}.meta.yaml") is None:
        failures.append("doc meta missing or unparsable")

    relations_path = base / "meta" / "relations" / f"{doc_id}.relations.yaml"
    if relations_path.exists():
        data = _load_yaml_mapping(relations_path)
        if data is None or data.get("doc_id") != doc_id:
            failures.append("doc relations missing or doc_id mismatch")

    for global_path in (
        base / "meta" / "ontology" / "global_ontology.yaml",
        base / "meta" / "relations" / "knowledge_graph.yaml",
        base / "meta" / "ontology" / "entity_relations.yaml",
    ):
        if global_path.exists() and _load_yaml_mapping(global_path) is None:
            failures.append(f"{global_path.name} top-level structure invalid")
    return failures


# ---------------------------------------------------------------------------
# 提交前产物耐久化确认(设计 §15 增补): COMMITTED 迁移前对已存在的七项
# 产物与 raw meta 执行 fsync,绝不改写内容
# ---------------------------------------------------------------------------


def _fsync_committed_outputs(base: Path, doc_id: str) -> None:
    """对七项产物中存在的文件与 raw/{doc_id}.meta.yaml 执行 fsync。

    只读打开、绝不改写内容;任一 fsync 失败抛出 OSError,调用方绝不进入
    COMMITTED,按既有验证/提交失败路径回滚。
    """
    for path in artifact_paths(base, doc_id):
        if path.is_file():
            fsync_existing_file(path)
    fsync_existing_file(base / "raw" / f"{doc_id}.meta.yaml")


# ---------------------------------------------------------------------------
# 执行器内部辅助
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic_text(result: ProcessResult, meta: dict | None) -> str:
    parts = []
    if meta and meta.get("error_message"):
        parts.append(str(meta["error_message"]))
    if result.stderr:
        parts.append(result.stderr[-MAX_CAPTURE_CHARS:])
    if result.stdout:
        parts.append(result.stdout[-MAX_CAPTURE_CHARS:])
    parts.append(f"returncode={result.returncode}")
    return "\n".join(parts)


def _process_record(identity) -> ProcessRecord:
    return ProcessRecord(
        pid=identity.pid,
        create_time=identity.create_time,
        executable=identity.executable,
        cwd=identity.cwd,
        command_fingerprint=identity.command_fingerprint,
        process_group_id=identity.process_group_id,
        platform=identity.platform,
    )


def _reap_popen(spawned: SpawnedProcess, grace_seconds: int) -> None:
    """进程树终止后回收 Popen,避免句柄/僵尸累积(不得抛出)。"""
    popen = spawned.popen
    try:
        popen.communicate(timeout=grace_seconds)
        return
    except Exception as exc:
        logger.warning(
            "reap via communicate failed for pid=%s: %s", popen.pid, exc
        )
    try:
        popen.wait(timeout=grace_seconds)
    except Exception as exc:
        logger.warning("reap via wait failed for pid=%s: %s", popen.pid, exc)


def _verify_and_cleanup_terminal(
    base: Path, config: CompileRuntimeConfig, readiness: ServiceReadiness
) -> None:
    """终态验证并清理(设计 §18);验证失败失败关闭,进入 recovery_required。"""
    report = cleanup_terminal_transactions(base, config)
    if report.blockers:
        readiness.mark_recovery_required(
            sanitize_compile_error("; ".join(report.blockers))
        )


def _rollback_running_job(
    base: Path,
    config: CompileRuntimeConfig,
    readiness: ServiceReadiness,
    manifest,
    expected_state: TransactionState,
    code: str,
    sanitized_message: str,
) -> None:
    """迁移到 ROLLBACKING(保留原始原因)→ 幂等回滚 → 终态验证清理。

    回滚被阻断时失败关闭: readiness 进入 recovery_required,
    不伪造文档终态,Manifest 保留 ROLLBACKING 证据。
    """
    transition_manifest(
        manifest.job_dir,
        expected=expected_state,
        target=TransactionState.ROLLBACKING,
        failure={"original_code": code, "original_message": sanitized_message},
    )
    result = restore_transaction_artifacts(
        base,
        config,
        manifest.job_dir,
        reason_code=code,
        reason_message=sanitized_message,
    )
    if result is not None and getattr(result, "blocked", False):
        readiness.mark_recovery_required(
            sanitize_compile_error(result.reason or "rollback_failed")
        )
        return
    _verify_and_cleanup_terminal(base, config, readiness)


def _terminate_timed_out_job(
    base: Path,
    config: CompileRuntimeConfig,
    readiness: ServiceReadiness,
    manifest,
    spawned: SpawnedProcess,
    grace_seconds: int,
    code: str,
    sanitized_message: str,
) -> None:
    """设计 §14 超时顺序: ROLLBACKING(reason) → 终止树 → 确认退出 → 回滚。"""
    try:
        transition_manifest(
            manifest.job_dir,
            expected=TransactionState.RUNNING,
            target=TransactionState.ROLLBACKING,
            failure={"original_code": code, "original_message": sanitized_message},
        )
    except Exception as exc:
        # 迁移失败时绝不让已超时的进程树存活: best-effort 终止并回收,
        # 失败关闭(Manifest 停留 RUNNING,由启动恢复处理)。
        logger.error(
            "ROLLBACKING transition failed for timed-out job %s: %s",
            manifest.job_id,
            exc,
        )
        _terminate_spawned_best_effort(spawned, grace_seconds)
        readiness.mark_recovery_required("rollback_transition_failed")
        return
    identity = spawned.identity
    try:
        termination = terminate_process_tree(identity, grace_seconds)
    finally:
        # 无论终止结果如何(包括抛出)都必须回收 Popen,避免句柄/僵尸累积。
        _reap_popen(spawned, grace_seconds)
    if not termination.success:
        readiness.mark_recovery_required(
            sanitize_compile_error(
                f"process tree exit unconfirmed: {termination.status}"
            )
        )
        return
    result = restore_transaction_artifacts(
        base,
        config,
        manifest.job_dir,
        reason_code=code,
        reason_message=sanitized_message,
    )
    if result is not None and getattr(result, "blocked", False):
        readiness.mark_recovery_required(
            sanitize_compile_error(result.reason or "rollback_failed")
        )
        return
    _verify_and_cleanup_terminal(base, config, readiness)


def _terminate_spawned_best_effort(
    spawned: SpawnedProcess, grace_seconds: int
) -> TerminationResult | None:
    """RUNNING/ROLLBACKING 迁移失败后的 best-effort 清理;任何异常只记录日志。

    返回终止结果(无身份或终止本身抛异常时返回 None);调用方据以决定
    是否把进程身份持久化为可恢复证据。
    """
    try:
        if spawned.identity is not None:
            return terminate_process_tree(spawned.identity, grace_seconds)
    except Exception as exc:
        logger.error("best-effort tree termination failed: %s", exc)
    finally:
        _reap_popen(spawned, grace_seconds)
    return None


#: RUNNING 迁移失败且进程退出未确认时持久化的失败原因码;
#: 启动恢复据此在回滚前先验证并终止记录进程。
ERROR_CODE_RUNNING_TRANSITION_FAILED = "running_transition_failed"


def _execute_scheduled_job(
    base: Path,
    config: CompileRuntimeConfig,
    readiness: ServiceReadiness,
    manifest,
) -> None:
    """SCHEDULED → RUNNING → COMMITTED / ROLLBACKING → ROLLED_BACK。"""
    job_dir = manifest.job_dir
    doc_id = manifest.doc_id
    timeout_seconds = manifest.timeout_seconds or config.timeout_seconds
    grace_seconds = (
        manifest.termination_grace_seconds or config.termination_grace_seconds
    )
    started_at = datetime.now(timezone.utc)
    deadline = started_at + timedelta(seconds=timeout_seconds)

    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        spawned = spawn_compile_process(base, doc_id, env)
    except Exception as exc:
        sanitized = sanitize_compile_error(
            f"compile process spawn failed: {type(exc).__name__}: {exc}"
        )
        _rollback_running_job(
            base,
            config,
            readiness,
            manifest,
            TransactionState.SCHEDULED,
            classify_compile_error(sanitized),
            sanitized,
        )
        return

    if spawned.identity is None:
        _reap_popen(spawned, grace_seconds)
        _rollback_running_job(
            base,
            config,
            readiness,
            manifest,
            TransactionState.SCHEDULED,
            "compile_failed",
            sanitize_compile_error("compile process identity unavailable"),
        )
        return

    # 设计 §13: 进程已启动并记录身份后,Manifest 原子进入 RUNNING。
    try:
        manifest = transition_manifest(
            job_dir,
            expected=TransactionState.SCHEDULED,
            target=TransactionState.RUNNING,
            started_at=started_at.isoformat(),
            deadline=deadline.isoformat(),
            process=_process_record(spawned.identity),
        )
    except Exception as exc:
        logger.error(
            "RUNNING transition failed for job %s after spawn: %s",
            manifest.job_id,
            exc,
        )
        termination = _terminate_spawned_best_effort(spawned, grace_seconds)
        if termination is None or not termination.success:
            # 退出未确认(身份不匹配/幸存者/终止异常):Manifest 不能停留
            # 无进程记录的 SCHEDULED——否则重启恢复会在不终止可能存活的
            # 编译进程的情况下直接回滚。持久化 SCHEDULED→ROLLBACKING
            # (合法迁移)携带进程身份与失败原因;证据写入也失败时响亮
            # 记录并保持 recovery_required。
            try:
                transition_manifest(
                    job_dir,
                    expected=TransactionState.SCHEDULED,
                    target=TransactionState.ROLLBACKING,
                    failure={
                        "original_code": ERROR_CODE_RUNNING_TRANSITION_FAILED,
                        "original_message": sanitize_compile_error(
                            f"running transition failed: "
                            f"{type(exc).__name__}: {exc}"
                        ),
                    },
                    process=_process_record(spawned.identity),
                )
            except Exception as persist_exc:
                logger.error(
                    "failed to persist rollback evidence for job %s: %s",
                    manifest.job_id,
                    persist_exc,
                )
        readiness.mark_recovery_required(ERROR_CODE_RUNNING_TRANSITION_FAILED)
        return

    try:
        result = wait_for_process(spawned, timeout_seconds)
    except Exception as exc:
        logger.error("wait_for_process failed for job %s: %s", manifest.job_id, exc)
        sanitized = sanitize_compile_error(
            f"compile wait failed: {type(exc).__name__}: {exc}"
        )
        _terminate_timed_out_job(
            base,
            config,
            readiness,
            manifest,
            spawned,
            grace_seconds,
            classify_compile_error(sanitized),
            sanitized,
        )
        return

    if result.status == STATUS_TIMED_OUT:
        _terminate_timed_out_job(
            base,
            config,
            readiness,
            manifest,
            spawned,
            grace_seconds,
            "timeout",
            sanitize_compile_error(
                f"compile exceeded hard timeout of {timeout_seconds} seconds"
            ),
        )
        return

    if result.status == STATUS_COMPLETED:
        meta = _read_doc_meta_safe(base, doc_id)
        validation_failures = _validate_compile_outputs(base, doc_id)
        if (
            meta is not None
            and meta.get("status") == "compiled"
            and not validation_failures
        ):
            # wrapper 规范化 compiled meta 并清除活动字段(设计 §15)。
            if not write_doc_compile_result(doc_id, "compiled", base_dir=base):
                readiness.mark_recovery_required(
                    "compiled_meta_normalization_failed"
                )
                return
            # N4: COMMITTED 迁移前对已存在的七项产物与 raw meta 执行
            # fsync(只读,不改写);fsync 失败绝不提交,按验证/提交失败
            # 路径回滚。
            try:
                _fsync_committed_outputs(base, doc_id)
            except OSError as exc:
                sanitized = sanitize_compile_error(
                    f"commit output fsync failed: {type(exc).__name__}: {exc}"
                )
                _rollback_running_job(
                    base,
                    config,
                    readiness,
                    manifest,
                    TransactionState.RUNNING,
                    classify_compile_error(sanitized),
                    sanitized,
                )
                return
            # Manifest 原子进入 COMMITTED: 唯一成功提交点。
            transition_manifest(
                job_dir,
                expected=TransactionState.RUNNING,
                target=TransactionState.COMMITTED,
            )
            _verify_and_cleanup_terminal(base, config, readiness)
            return
        diagnostic = _diagnostic_text(result, meta)
        if validation_failures:
            diagnostic += "\nsemantic validation failed: " + "; ".join(
                validation_failures
            )
    else:
        meta = _read_doc_meta_safe(base, doc_id)
        diagnostic = _diagnostic_text(result, meta)

    sanitized = sanitize_compile_error(diagnostic)
    _rollback_running_job(
        base,
        config,
        readiness,
        manifest,
        TransactionState.RUNNING,
        classify_compile_error(sanitized),
        sanitized,
    )


def _execute_compile_job(
    job_id: str,
    base: Path,
    config: CompileRuntimeConfig,
    readiness: ServiceReadiness,
) -> None:
    if not _SAFE_JOB_ID.match(job_id) or ".." in job_id:
        # 非法 job id 绝不可能来自合法调度: 只记录日志,readiness 不变。
        logger.warning("compile task rejected unsafe job id %r; ignoring", job_id)
        return
    job_dir = Path(config.transaction_dir) / job_id
    if not job_dir.is_dir():
        # Codex R3 P2-3: well-formed job_id 但事务目录丢失 = 已接受任务丢失
        # 耐久证据(完整性失败)。静默返回会让文档永远停留 compiling 而服务
        # 保持 ready(仅重启可发现孤儿);必须失败关闭,保留全部证据,
        # 绝不伪造终态、绝不删除数据。
        logger.error(
            "accepted compile job %s lost its transaction directory; "
            "marking recovery_required",
            job_id,
        )
        readiness.mark_recovery_required("transaction_evidence_missing")
        return
    try:
        manifest = load_manifest(job_dir)
    except ManifestIntegrityError as exc:
        logger.error("manifest integrity failure for job %s: %s", job_id, exc)
        readiness.mark_recovery_required("manifest_integrity")
        return

    if manifest.state in (TransactionState.COMMITTED, TransactionState.ROLLED_BACK):
        _verify_and_cleanup_terminal(base, config, readiness)
        return

    if manifest.state is not TransactionState.SCHEDULED:
        # SCHEDULED 语义: 不重新排队、不重复执行;其他非终态一律按
        # 恢复库语义处理(设计 §9、§16、§17)。
        logger.warning(
            "compile job %s in state %s; deferring to recovery semantics",
            job_id,
            manifest.state.value,
        )
        result = recover_transaction(
            base,
            config,
            job_dir,
            reason_code=ERROR_CODE_INTERRUPTED,
            reason_message="compile task invoked outside scheduled state",
        )
        if result.blocked:
            readiness.mark_recovery_required(
                sanitize_compile_error(result.reason or "recovery_blocked")
            )
            return
        _verify_and_cleanup_terminal(base, config, readiness)
        return

    _execute_scheduled_job(base, config, readiness, manifest)


def run_compile_task(
    job_id: str,
    base_dir: Path | None = None,
    config: CompileRuntimeConfig | None = None,
    readiness: ServiceReadiness | None = None,
) -> None:
    """以 job_id 为输入的事务化编译后台任务;绝不抛出。

    - 非法 job id(正则拒绝): 只记录日志,readiness 不变;
    - well-formed job 但事务目录丢失: 已接受任务丢失耐久证据,按完整性
      失败进入 recovery_required(transaction_evidence_missing);
    - 非 SCHEDULED: 按恢复库语义处理,绝不重新执行;
    - 任何无法证明一致性的基础设施失败: readiness 进入
      recovery_required,失败关闭。
    """
    base = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent
    config = config if config is not None else load_compile_runtime_config(base)
    readiness = readiness if readiness is not None else ServiceReadiness()
    with COMPILE_EXECUTION_LOCK:
        try:
            _execute_compile_job(str(job_id), base, config, readiness)
        except Exception:
            logger.exception(
                "compile task %s hit an infrastructure failure", job_id
            )
            readiness.mark_recovery_required("compile_infrastructure_error")
