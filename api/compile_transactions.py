"""E005 Task 2: 编译事务 Manifest 模型与持久快照存储。

对应设计文档第 7 节(事务目录与 Manifest)、第 9 节(状态机)、
第 11 节(重编译事务准备顺序)和第 16 节(幂等回滚的恢复依据)。

职责边界:

- 在 config.transaction_dir 下以 .staging-{job_id}  staging 生成七项编译
  产物快照,验证大小与 SHA-256 后原子发布为正式事务目录 {job_id};
- Manifest(schema_version=1)原子写入,state=PREPARED;
- load_manifest 对 schema、state、未知键、路径越界、白名单一致性和
  快照完整性全部失败关闭(fail closed);
- transition_manifest 以原子 Manifest 写入作为状态迁移提交点;
- 恢复目标永远由 base_dir + doc_id + 固定白名单重新计算,不信任
  Manifest 中存储的路径。

Manifest 不记录 API Key、环境变量值、文档正文或未脱敏输出。
所有耐久写入复用 api.durable_fs 原语,本模块不自行实现耐久写入。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from api.durable_fs import (
    durable_publish_directory,
    durable_write_bytes,
    durable_write_yaml,
    sha256_file,
)
from api.process_tree import (
    STATUS_IDENTITY_MISMATCH,
    STATUS_PROCESS_GONE,
    ProcessIdentity,
    terminate_process_tree,
    verify_process_identity,
)
from api.runtime_guard import CompileRuntimeConfig
from scripts.doc_admin import read_doc_meta, write_doc_compile_result

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "manifest.yaml"
SOURCE_META_FILENAME = "source-meta-before.yaml"
STAGING_PREFIX = ".staging-"

_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


class TransactionState(str, Enum):
    PREPARED = "PREPARED"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMMITTED = "COMMITTED"
    ROLLBACKING = "ROLLBACKING"
    ROLLED_BACK = "ROLLED_BACK"


class TransactionKind(str, Enum):
    RECOMPILE = "recompile"
    UPLOAD = "upload"


ACTIVE_STATES = frozenset({
    TransactionState.PREPARED,
    TransactionState.SCHEDULED,
    TransactionState.RUNNING,
    TransactionState.ROLLBACKING,
})

ALLOWED_TRANSITIONS = {
    TransactionState.PREPARED: {TransactionState.SCHEDULED, TransactionState.ROLLBACKING},
    TransactionState.SCHEDULED: {TransactionState.RUNNING, TransactionState.ROLLBACKING},
    TransactionState.RUNNING: {TransactionState.COMMITTED, TransactionState.ROLLBACKING},
    TransactionState.ROLLBACKING: {TransactionState.ROLLED_BACK},
    TransactionState.COMMITTED: set(),
    TransactionState.ROLLED_BACK: set(),
}


class TransactionError(Exception):
    """编译事务错误基类。"""


class TransactionStateError(TransactionError):
    """状态机违约:当前状态与 expected 不符,或迁移不在 ALLOWED_TRANSITIONS。"""


class ManifestIntegrityError(TransactionError):
    """Manifest schema、安全校验或快照完整性违约;启动恢复必须严格阻断。"""


@dataclass(frozen=True)
class ArtifactRecord:
    slot: int
    path: str
    existed: bool
    snapshot: str | None
    snapshot_size: int | None
    snapshot_sha256: str | None
    original_sha256: str | None


@dataclass(frozen=True)
class ProcessRecord:
    pid: int
    create_time: float
    executable: str
    cwd: str
    command_fingerprint: str
    process_group_id: int | None
    platform: str


@dataclass(frozen=True)
class PublishedIntake:
    original_path: str
    raw_text_path: str
    raw_meta_path: str
    published: bool


@dataclass(frozen=True)
class CompileManifest:
    schema_version: int
    job_id: str
    doc_id: str
    kind: TransactionKind
    state: TransactionState
    created_at: str
    scheduled_at: str | None
    started_at: str | None
    deadline: str | None
    timeout_seconds: int
    termination_grace_seconds: int
    previous_document_status: str | None
    published_intake: PublishedIntake | None
    process: ProcessRecord | None
    failure: dict[str, Any]
    recovery: dict[str, Any]
    artifacts: tuple[ArtifactRecord, ...]
    job_dir: Path


# ---------------------------------------------------------------------------
# 七项编译产物白名单
# ---------------------------------------------------------------------------


def _artifact_relative_paths(doc_id: str) -> tuple[str, ...]:
    return (
        f"wiki/{doc_id}.summary.yaml",
        "wiki/index.yaml",
        f"meta/ontology/{doc_id}.ontology.yaml",
        "meta/ontology/global_ontology.yaml",
        f"meta/relations/{doc_id}.relations.yaml",
        "meta/relations/knowledge_graph.yaml",
        "meta/ontology/entity_relations.yaml",
    )


def artifact_paths(base_dir: Path, doc_id: str) -> tuple[Path, ...]:
    """返回 base_dir 下 doc_id 的七项编译产物绝对路径(固定白名单)。"""
    base = Path(base_dir)
    return tuple(base / rel for rel in _artifact_relative_paths(doc_id))


# ---------------------------------------------------------------------------
# Manifest 序列化
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEYS = frozenset({
    "schema_version", "job_id", "doc_id", "kind", "state",
    "created_at", "scheduled_at", "started_at", "deadline",
    "timeout_seconds", "termination_grace_seconds",
    "previous_document_status",
    "published_intake", "process", "failure", "recovery", "artifacts",
})
_ARTIFACT_KEYS = frozenset({
    "slot", "path", "existed", "snapshot",
    "snapshot_size", "snapshot_sha256", "original_sha256",
})
_PROCESS_KEYS = frozenset({
    "pid", "create_time", "executable", "cwd",
    "command_fingerprint", "process_group_id", "platform",
})
_INTAKE_KEYS = frozenset({
    "original_path", "raw_text_path", "raw_meta_path", "published",
})
_FAILURE_KEYS = frozenset({"original_code", "original_message"})
_RECOVERY_KEYS = frozenset({"last_error", "failed_paths"})

# transition_manifest 允许变更的字段;状态机字段与身份字段不可变。
_TRANSITION_CHANGEABLE_FIELDS = frozenset({
    "scheduled_at", "started_at", "deadline",
    "process", "published_intake", "failure", "recovery",
})


def _artifact_to_dict(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "slot": record.slot,
        "path": record.path,
        "existed": record.existed,
        "snapshot": record.snapshot,
        "snapshot_size": record.snapshot_size,
        "snapshot_sha256": record.snapshot_sha256,
        "original_sha256": record.original_sha256,
    }


def _process_to_dict(record: ProcessRecord) -> dict[str, Any]:
    return {
        "pid": record.pid,
        "create_time": record.create_time,
        "executable": record.executable,
        "cwd": record.cwd,
        "command_fingerprint": record.command_fingerprint,
        "process_group_id": record.process_group_id,
        "platform": record.platform,
    }


def _intake_to_dict(record: PublishedIntake) -> dict[str, Any]:
    return {
        "original_path": record.original_path,
        "raw_text_path": record.raw_text_path,
        "raw_meta_path": record.raw_meta_path,
        "published": record.published,
    }


def _manifest_to_dict(manifest: CompileManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "job_id": manifest.job_id,
        "doc_id": manifest.doc_id,
        "kind": manifest.kind.value,
        "state": manifest.state.value,
        "created_at": manifest.created_at,
        "scheduled_at": manifest.scheduled_at,
        "started_at": manifest.started_at,
        "deadline": manifest.deadline,
        "timeout_seconds": manifest.timeout_seconds,
        "termination_grace_seconds": manifest.termination_grace_seconds,
        "previous_document_status": manifest.previous_document_status,
        "published_intake": (
            _intake_to_dict(manifest.published_intake)
            if manifest.published_intake is not None
            else None
        ),
        "process": (
            _process_to_dict(manifest.process)
            if manifest.process is not None
            else None
        ),
        "failure": dict(manifest.failure),
        "recovery": {
            "last_error": manifest.recovery.get("last_error"),
            "failed_paths": list(manifest.recovery.get("failed_paths") or []),
        },
        "artifacts": [_artifact_to_dict(item) for item in manifest.artifacts],
    }


# ---------------------------------------------------------------------------
# Manifest 反序列化与失败关闭校验
# ---------------------------------------------------------------------------


def _reject(message: str) -> None:
    raise ManifestIntegrityError(message)


def _require_keys(data: Mapping[str, Any], allowed: frozenset, section: str) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        _reject(f"manifest {section} contains unknown keys: {sorted(unknown)}")


def _require_safe_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        _reject(f"manifest {field} must be a non-empty relative path string")
    if "\\" in value:
        _reject(f"manifest {field} must use posix separators: {value!r}")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        _reject(f"manifest {field} must be relative: {value!r}")
    parts = PurePosixPath(value).parts
    if any(part == ".." for part in parts):
        _reject(f"manifest {field} must not escape the transaction root: {value!r}")
    return PurePosixPath(value).as_posix()


def _require_safe_token(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_TOKEN.match(value):
        _reject(f"manifest {field} is not a safe identifier: {value!r}")
    return value


def _require_sha256(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.match(value):
        _reject(f"manifest {field} must be a sha256 hex digest")
    return value


def _parse_artifact(raw: Any, job_dir: Path, index: int) -> ArtifactRecord:
    if not isinstance(raw, Mapping):
        _reject(f"manifest artifacts[{index}] must be a mapping")
    _require_keys(raw, _ARTIFACT_KEYS, f"artifacts[{index}]")
    slot = raw.get("slot")
    if not isinstance(slot, int) or isinstance(slot, bool) or slot != index:
        _reject(f"manifest artifacts[{index}] has invalid slot: {slot!r}")
    path = _require_safe_relative_path(raw.get("path"), f"artifacts[{index}].path")
    existed = raw.get("existed")
    if not isinstance(existed, bool):
        _reject(f"manifest artifacts[{index}].existed must be a boolean")
    snapshot = raw.get("snapshot")
    snapshot_size = raw.get("snapshot_size")
    snapshot_sha256 = raw.get("snapshot_sha256")
    original_sha256 = raw.get("original_sha256")
    if existed:
        if snapshot is None or snapshot_size is None or original_sha256 is None:
            _reject(
                f"manifest artifacts[{index}] existed=true requires snapshot, "
                "snapshot_size and original_sha256"
            )
        snapshot = _require_safe_relative_path(
            snapshot, f"artifacts[{index}].snapshot"
        )
        if not isinstance(snapshot_size, int) or isinstance(snapshot_size, bool):
            _reject(f"manifest artifacts[{index}].snapshot_size must be an integer")
        snapshot_sha256 = _require_sha256(
            snapshot_sha256, f"artifacts[{index}].snapshot_sha256"
        )
        original_sha256 = _require_sha256(
            original_sha256, f"artifacts[{index}].original_sha256"
        )
        snapshot_file = job_dir / snapshot
        if not snapshot_file.is_file():
            _reject(
                f"manifest artifacts[{index}] snapshot missing: {snapshot}"
            )
        if snapshot_file.stat().st_size != snapshot_size:
            _reject(
                f"manifest artifacts[{index}] snapshot size mismatch: {snapshot}"
            )
        if sha256_file(snapshot_file) != snapshot_sha256:
            _reject(
                f"manifest artifacts[{index}] snapshot sha256 mismatch: {snapshot}"
            )
    else:
        if snapshot is not None or snapshot_size is not None or snapshot_sha256 is not None:
            _reject(
                f"manifest artifacts[{index}] existed=false must not reference "
                "a snapshot"
            )
    return ArtifactRecord(
        slot=slot,
        path=path,
        existed=existed,
        snapshot=snapshot,
        snapshot_size=snapshot_size,
        snapshot_sha256=snapshot_sha256,
        original_sha256=original_sha256,
    )


def _parse_process(raw: Any) -> ProcessRecord | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _reject("manifest process must be a mapping or null")
    _require_keys(raw, _PROCESS_KEYS, "process")
    pid = raw.get("pid")
    create_time = raw.get("create_time")
    if not isinstance(pid, int) or isinstance(pid, bool):
        _reject("manifest process.pid must be an integer")
    if not isinstance(create_time, (int, float)) or isinstance(create_time, bool):
        _reject("manifest process.create_time must be a number")
    pgid = raw.get("process_group_id")
    if pgid is not None and (not isinstance(pgid, int) or isinstance(pgid, bool)):
        _reject("manifest process.process_group_id must be an integer or null")
    for field in ("executable", "cwd", "command_fingerprint", "platform"):
        if not isinstance(raw.get(field), str):
            _reject(f"manifest process.{field} must be a string")
    return ProcessRecord(
        pid=pid,
        create_time=float(create_time),
        executable=raw["executable"],
        cwd=raw["cwd"],
        command_fingerprint=raw["command_fingerprint"],
        process_group_id=pgid,
        platform=raw["platform"],
    )


def _parse_intake(raw: Any) -> PublishedIntake | None:
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        _reject("manifest published_intake must be a mapping or null")
    _require_keys(raw, _INTAKE_KEYS, "published_intake")
    original_path = _require_safe_relative_path(
        raw.get("original_path"), "published_intake.original_path"
    )
    raw_text_path = _require_safe_relative_path(
        raw.get("raw_text_path"), "published_intake.raw_text_path"
    )
    raw_meta_path = _require_safe_relative_path(
        raw.get("raw_meta_path"), "published_intake.raw_meta_path"
    )
    published = raw.get("published")
    if not isinstance(published, bool):
        _reject("manifest published_intake.published must be a boolean")
    return PublishedIntake(
        original_path=original_path,
        raw_text_path=raw_text_path,
        raw_meta_path=raw_meta_path,
        published=published,
    )


def _parse_failure(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"original_code": None, "original_message": None}
    if not isinstance(raw, Mapping):
        _reject("manifest failure must be a mapping or null")
    _require_keys(raw, _FAILURE_KEYS, "failure")
    code = raw.get("original_code")
    message = raw.get("original_message")
    if code is not None and not isinstance(code, str):
        _reject("manifest failure.original_code must be a string or null")
    if message is not None and not isinstance(message, str):
        _reject("manifest failure.original_message must be a string or null")
    return {"original_code": code, "original_message": message}


def _parse_recovery(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {"last_error": None, "failed_paths": []}
    if not isinstance(raw, Mapping):
        _reject("manifest recovery must be a mapping or null")
    _require_keys(raw, _RECOVERY_KEYS, "recovery")
    last_error = raw.get("last_error")
    failed_paths = raw.get("failed_paths")
    if last_error is not None and not isinstance(last_error, str):
        _reject("manifest recovery.last_error must be a string or null")
    if failed_paths is None:
        failed_paths = []
    if not isinstance(failed_paths, list) or not all(
        isinstance(item, str) for item in failed_paths
    ):
        _reject("manifest recovery.failed_paths must be a list of strings")
    return {"last_error": last_error, "failed_paths": list(failed_paths)}


def load_manifest(job_dir: Path) -> CompileManifest:
    """读取并严格校验 {job_dir}/manifest.yaml。

    未知 schema_version、未知 state/kind、安全关键区段的未知键、路径越界、
    白名单不一致以及快照缺失/大小或 SHA-256 不匹配均抛出
    ManifestIntegrityError,调用方必须失败关闭。
    """
    job_dir = Path(job_dir)
    manifest_path = job_dir / MANIFEST_FILENAME
    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestIntegrityError(
            f"manifest unreadable: {manifest_path} ({exc})"
        ) from exc
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ManifestIntegrityError(
            f"manifest is not valid YAML: {manifest_path}"
        ) from exc
    if not isinstance(data, Mapping):
        _reject("manifest root must be a mapping")
    _require_keys(data, _TOP_LEVEL_KEYS, "top-level")

    if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        _reject(f"unknown manifest schema_version: {data.get('schema_version')!r}")

    job_id = _require_safe_token(data.get("job_id"), "job_id")
    if job_id != job_dir.name:
        _reject(
            f"manifest job_id {job_id!r} does not match directory {job_dir.name!r}"
        )
    doc_id = _require_safe_token(data.get("doc_id"), "doc_id")

    try:
        kind = TransactionKind(data.get("kind"))
    except ValueError:
        _reject(f"unknown manifest kind: {data.get('kind')!r}")
    try:
        state = TransactionState(data.get("state"))
    except ValueError:
        _reject(f"unknown manifest state: {data.get('state')!r}")

    timeout_seconds = data.get("timeout_seconds")
    grace_seconds = data.get("termination_grace_seconds")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        _reject("manifest timeout_seconds must be an integer")
    if not isinstance(grace_seconds, int) or isinstance(grace_seconds, bool):
        _reject("manifest termination_grace_seconds must be an integer")

    for field in ("created_at",):
        if not isinstance(data.get(field), str):
            _reject(f"manifest {field} must be a string")
    for field in ("scheduled_at", "started_at", "deadline", "previous_document_status"):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            _reject(f"manifest {field} must be a string or null")

    raw_artifacts = data.get("artifacts")
    if not isinstance(raw_artifacts, list):
        _reject("manifest artifacts must be a list")
    artifacts = tuple(
        _parse_artifact(item, job_dir, index)
        for index, item in enumerate(raw_artifacts)
    )
    expected_paths = _artifact_relative_paths(doc_id)
    actual_paths = tuple(item.path for item in artifacts)
    if actual_paths != expected_paths:
        _reject(
            "manifest artifacts do not match the seven-artifact whitelist "
            f"for doc_id {doc_id!r}"
        )

    return CompileManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        job_id=job_id,
        doc_id=doc_id,
        kind=kind,
        state=state,
        created_at=data["created_at"],
        scheduled_at=data.get("scheduled_at"),
        started_at=data.get("started_at"),
        deadline=data.get("deadline"),
        timeout_seconds=timeout_seconds,
        termination_grace_seconds=grace_seconds,
        previous_document_status=data.get("previous_document_status"),
        published_intake=_parse_intake(data.get("published_intake")),
        process=_parse_process(data.get("process")),
        failure=_parse_failure(data.get("failure")),
        recovery=_parse_recovery(data.get("recovery")),
        artifacts=artifacts,
        job_dir=job_dir,
    )


# ---------------------------------------------------------------------------
# 事务创建与状态迁移
# ---------------------------------------------------------------------------


def _generate_job_id() -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%S%f}Z-{secrets.token_hex(4)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_prepared_transaction(
    *,
    base_dir: Path,
    config: CompileRuntimeConfig,
    doc_id: str,
    kind: TransactionKind,
    previous_meta: dict[str, object] | None,
    published_intake: PublishedIntake | None = None,
) -> CompileManifest:
    """在 config.transaction_dir 下创建 PREPARED 事务。

    顺序(设计 §11):
        staging 目录 → 七项快照(耐久写入)→ 快照大小与 SHA-256 验证
        → 保存 source-meta-before.yaml → 原子写 PREPARED Manifest
        → durable_publish_directory 原子发布为正式目录。

    正式发布前任何失败删除 staging,业务目录保持不变。
    """
    base_dir = Path(base_dir)
    if not isinstance(kind, TransactionKind):
        kind = TransactionKind(kind)
    _require_safe_token(doc_id, "doc_id")
    transaction_root = Path(config.transaction_dir)
    transaction_root.mkdir(parents=True, exist_ok=True)

    job_id = _generate_job_id()
    staging = transaction_root / f"{STAGING_PREFIX}{job_id}"
    final_dir = transaction_root / job_id
    try:
        snapshot_dir = staging / "snapshots"
        snapshot_dir.mkdir(parents=True)

        artifacts: list[ArtifactRecord] = []
        for index, target in enumerate(artifact_paths(base_dir, doc_id)):
            relative_path = target.relative_to(base_dir).as_posix()
            if target.is_file():
                payload = target.read_bytes()
                original_sha256 = sha256_file(target)
                snapshot_rel = f"snapshots/{index:02d}.bin"
                snapshot_path = staging / snapshot_rel
                durable_write_bytes(snapshot_path, payload)
                snapshot_size = snapshot_path.stat().st_size
                if snapshot_size != len(payload):
                    raise OSError(
                        f"snapshot size verification failed: {snapshot_path}"
                    )
                snapshot_sha256 = sha256_file(snapshot_path)
                if snapshot_sha256 != original_sha256:
                    raise OSError(
                        f"snapshot sha256 verification failed: {snapshot_path}"
                    )
                artifacts.append(ArtifactRecord(
                    slot=index,
                    path=relative_path,
                    existed=True,
                    snapshot=snapshot_rel,
                    snapshot_size=snapshot_size,
                    snapshot_sha256=snapshot_sha256,
                    original_sha256=original_sha256,
                ))
            else:
                artifacts.append(ArtifactRecord(
                    slot=index,
                    path=relative_path,
                    existed=False,
                    snapshot=None,
                    snapshot_size=None,
                    snapshot_sha256=None,
                    original_sha256=None,
                ))

        if previous_meta is not None:
            durable_write_yaml(staging / SOURCE_META_FILENAME, previous_meta)

        manifest = CompileManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            job_id=job_id,
            doc_id=doc_id,
            kind=kind,
            state=TransactionState.PREPARED,
            created_at=_now_iso(),
            scheduled_at=None,
            started_at=None,
            deadline=None,
            timeout_seconds=config.timeout_seconds,
            termination_grace_seconds=config.termination_grace_seconds,
            previous_document_status=(
                str(previous_meta.get("status"))
                if previous_meta is not None and previous_meta.get("status") is not None
                else None
            ),
            published_intake=published_intake,
            process=None,
            failure={"original_code": None, "original_message": None},
            recovery={"last_error": None, "failed_paths": []},
            artifacts=tuple(artifacts),
            job_dir=final_dir,
        )
        durable_write_yaml(staging / MANIFEST_FILENAME, _manifest_to_dict(manifest))
        durable_publish_directory(staging, final_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def transition_manifest(
    job_dir: Path,
    expected: TransactionState,
    target: TransactionState,
    **changes: Any,
) -> CompileManifest:
    """原子迁移 Manifest 状态。

    - 当前状态与 expected 不符时抛出 TransactionStateError;
    - (current → target) 不在 ALLOWED_TRANSITIONS 时抛出 TransactionStateError;
    - **changes 只允许 _TRANSITION_CHANGEABLE_FIELDS;
    - 状态翻转本身通过耐久(原子)Manifest 写入提交,COMMITTED 由此成为
      唯一提交点。
    """
    job_dir = Path(job_dir)
    manifest = load_manifest(job_dir)
    if manifest.state is not expected:
        raise TransactionStateError(
            f"manifest state is {manifest.state.value}, expected {expected.value}"
        )
    if target not in ALLOWED_TRANSITIONS[manifest.state]:
        raise TransactionStateError(
            f"transition {manifest.state.value} -> {target.value} is not allowed"
        )
    unknown_changes = set(changes) - _TRANSITION_CHANGEABLE_FIELDS
    if unknown_changes:
        raise TransactionStateError(
            f"manifest fields are not transition-changeable: {sorted(unknown_changes)}"
        )
    updated = replace(manifest, state=target, **changes)
    durable_write_yaml(job_dir / MANIFEST_FILENAME, _manifest_to_dict(updated))
    return load_manifest(job_dir)


# ---------------------------------------------------------------------------
# 事务目录扫描
# ---------------------------------------------------------------------------


def list_transaction_dirs(config: CompileRuntimeConfig) -> list[Path]:
    """返回正式事务目录(排除 .staging-* staging 与非目录项),按名称排序。"""
    root = Path(config.transaction_dir)
    if not root.is_dir():
        return []
    return sorted(
        entry
        for entry in root.iterdir()
        if entry.is_dir() and not entry.name.startswith(STAGING_PREFIX)
    )


def list_active_manifests(config: CompileRuntimeConfig) -> list[CompileManifest]:
    """返回全部非终态(ACTIVE_STATES)事务 Manifest,供启动恢复扫描。

    返回数量大于一时,启动恢复必须严格阻断(设计 §17)。
    """
    manifests: list[CompileManifest] = []
    for job_dir in list_transaction_dirs(config):
        manifest = load_manifest(job_dir)
        if manifest.state in ACTIVE_STATES:
            manifests.append(manifest)
    return manifests


# ---------------------------------------------------------------------------
# E005 Task 4: 幂等恢复引擎(设计 §16、§17、§18)
#
# 安全合同:
# - 恢复目标永远由 base_dir + doc_id + 七项白名单重新计算;上传发布文件
#   只删除重算后确认落在 originals/ 或 raw/ 且属于本 doc_id 的目标;
# - ManifestIntegrityError(含快照损坏、未知 schema/state)一律硬阻断,
#   绝不 catch-and-continue,绝不触碰业务文件;
# - RUNNING 进程身份不匹配时绝不 kill、绝不回滚,保留证据阻断;
# - 进程树存在幸存者时绝不回滚;
# - 恢复失败保持 ROLLBACKING 并记录失败路径,下次调用幂等继续。
# ---------------------------------------------------------------------------

#: raw meta 中的活动 job 绑定字段;终态(meta compiled/error)不得携带。
#: 与 scripts.doc_admin.ACTIVE_JOB_FIELDS 保持一致。
META_ACTIVE_JOB_FIELDS = (
    "compile_job_id",
    "compile_started_at",
    "compile_deadline",
)

#: 文档终态错误码: 未提交事务因服务生命周期中断而恢复(设计 §20.1)。
ERROR_CODE_INTERRUPTED = "interrupted"
ERROR_CODE_ROLLBACK_FAILED = "rollback_failed"


@dataclass(frozen=True)
class RecoveryResult:
    """单个事务恢复结果: completed / already_terminal / blocked 三态互斥。"""

    job_id: str
    completed: bool = False
    already_terminal: bool = False
    blocked: bool = False
    reason: str | None = None
    failed_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerificationReport:
    """终态事务验证结果;ok=False 时绝不清理目录。"""

    job_id: str
    state: str
    ok: bool
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class CleanupReport:
    """终态清理报告: 验证失败计入 blockers,纯目录删除失败仅 warnings。"""

    cleaned: list[str]
    kept: list[str]
    warnings: list[str]
    blockers: list[str]


@dataclass(frozen=True)
class StartupRecoveryReport:
    """启动恢复报告;blockers 非空即 ready=False,实例不得开始服务。"""

    ready: bool
    recovered: list[str]
    cleaned: list[str]
    blockers: list[str]
    warnings: list[str]


def _sanitize_error_message(text: str | None) -> str:
    """脱敏、单行化并限长技术错误信息(复用 compile_jobs 脱敏规则)。

    延迟导入避免与 api.compile_jobs 的循环依赖。
    """
    from api.compile_jobs import sanitize_compile_error

    return sanitize_compile_error(text or "")


def _read_doc_meta_safe(
    base_dir: Path, doc_id: str
) -> tuple[dict | None, str | None]:
    """read_doc_meta 的失败关闭包装。

    返回 (meta, None);文件不存在返回 (None, None);损坏、不可读或
    非映射返回 (None, 脱敏原因),绝不向恢复/启动路径抛出异常。
    """
    try:
        meta = read_doc_meta(doc_id, Path(base_dir))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"doc meta unreadable: {_sanitize_error_message(exc)}"
    if meta is None:
        return None, None
    if not isinstance(meta, dict):
        return None, f"raw/{doc_id}.meta.yaml is not a mapping"
    return meta, None


def _doc_is_bound(
    base_dir: Path, manifest: CompileManifest
) -> tuple[bool, str | None]:
    """判断业务文档是否已绑定本事务(meta 绑定 job 或 status=compiling)。

    返回 (bound, error);meta 损坏/不可读时返回 (False, error),
    调用方必须失败关闭,不得按"未绑定"清理事务。
    """
    meta, error = _read_doc_meta_safe(base_dir, manifest.doc_id)
    if error is not None:
        return False, error
    if meta is None:
        return False, None
    for field in META_ACTIVE_JOB_FIELDS:
        if meta.get(field) == manifest.job_id:
            return True, None
    return meta.get("status") == "compiling", None


def _recompute_intake_targets(
    base_dir: Path, manifest: CompileManifest
) -> list[Path] | None:
    """重算上传发布撤销目标;任一目标不安全时返回 None(失败关闭)。

    - raw 目标必须与 base_dir + doc_id 重算值完全一致;
    - original 目标必须是 originals/ 的直接子文件;
    - 其他前缀或越界一律拒绝,绝不按 Manifest 存储路径删除。
    """
    intake = manifest.published_intake
    if intake is None or not intake.published:
        return []
    expected_raw = {f"raw/{manifest.doc_id}.txt", f"raw/{manifest.doc_id}.meta.yaml"}
    targets: list[Path] = []
    for stored in (
        intake.original_path,
        intake.raw_text_path,
        intake.raw_meta_path,
    ):
        parts = PurePosixPath(stored).parts
        if parts[0] == "raw":
            if stored not in expected_raw:
                return None
        elif parts[0] == "originals":
            if len(parts) != 2:
                return None
        else:
            return None
        targets.append(Path(base_dir) / stored)
    return targets


def _record_recovery_failure(
    job_dir: Path, last_error: str, failed_paths: list[str]
) -> None:
    """在 ROLLBACKING 上记录恢复失败证据;状态不变,原始失败原因保留。"""
    manifest = load_manifest(job_dir)
    updated = replace(
        manifest,
        recovery={
            "last_error": _sanitize_error_message(last_error),
            "failed_paths": list(failed_paths),
        },
    )
    durable_write_yaml(Path(job_dir) / MANIFEST_FILENAME, _manifest_to_dict(updated))


def _terminate_leftover_process(
    manifest: CompileManifest, config: CompileRuntimeConfig
) -> str | None:
    """RUNNING 遗留进程处理;返回 None 表示可继续回滚,否则为阻断原因。"""
    record = manifest.process
    if record is None:
        return None
    identity = ProcessIdentity(
        pid=record.pid,
        create_time=record.create_time,
        executable=record.executable,
        cwd=record.cwd,
        command_fingerprint=record.command_fingerprint,
        process_group_id=(
            record.process_group_id
            if record.process_group_id is not None
            else record.pid
        ),
        platform=record.platform,
    )
    status = verify_process_identity(identity)
    if status.status == STATUS_IDENTITY_MISMATCH:
        fields = ",".join(status.mismatched_fields)
        return (
            f"process identity mismatch (fields: {fields}); "
            "evidence preserved, nothing killed"
        )
    if status.status == STATUS_PROCESS_GONE:
        return None
    grace = manifest.termination_grace_seconds or config.termination_grace_seconds
    result = terminate_process_tree(identity, grace)
    if result.status == STATUS_IDENTITY_MISMATCH:
        return "process identity mismatch during termination; evidence preserved"
    if not result.success:
        return f"process tree survivors remaining: {list(result.survivors)}"
    return None


def _execute_rollback(
    base_dir: Path, manifest: CompileManifest
) -> RecoveryResult:
    """在 ROLLBACKING 上执行幂等回滚(设计 §16 顺序)。

    每一步都可安全重入: 恢复已恢复的文件、删除已删除的文件、重写
    文档终态均为幂等操作;崩溃后下次调用从同一状态继续。
    """
    base_dir = Path(base_dir)
    job_dir = manifest.job_dir
    doc_id = manifest.doc_id
    targets = artifact_paths(base_dir, doc_id)  # 重算白名单目标
    failed: list[str] = []

    # 1. 恢复快照中原本存在的文件 / 删除本轮新建的文件。
    for record in manifest.artifacts:
        target = targets[record.slot]
        if record.existed:
            payload = (job_dir / record.snapshot).read_bytes()
            if hashlib.sha256(payload).hexdigest() != record.snapshot_sha256:
                failed.append(record.path)
                continue
            try:
                durable_write_bytes(target, payload)
            except OSError:
                failed.append(record.path)
        else:
            try:
                if target.is_file() or target.is_symlink():
                    target.unlink()
                elif target.exists():
                    failed.append(record.path)
            except OSError:
                failed.append(record.path)

    # 2. 撤销上传事务本轮发布的 original/raw 文件(重算安全目标)。
    intake_targets = _recompute_intake_targets(base_dir, manifest)
    if manifest.kind is TransactionKind.UPLOAD:
        if intake_targets is None:
            failed.append("published_intake")
        else:
            for path in intake_targets:
                try:
                    if path.is_file() or path.is_symlink():
                        path.unlink()
                    elif path.exists():
                        failed.append(path.name)
                except OSError:
                    failed.append(path.name)

    # 3. 验证恢复结果与事务前存在性 + SHA-256 完全一致。
    for record in manifest.artifacts:
        target = targets[record.slot]
        if record.existed:
            if not target.is_file() or sha256_file(target) != record.original_sha256:
                if record.path not in failed:
                    failed.append(record.path)
        elif target.exists():
            if record.path not in failed:
                failed.append(record.path)
    if manifest.kind is TransactionKind.UPLOAD and intake_targets:
        for path in intake_targets:
            if path.exists() and path.name not in failed:
                failed.append(path.name)

    if failed:
        _record_recovery_failure(job_dir, "rollback verification failed", failed)
        return RecoveryResult(
            job_id=manifest.job_id,
            blocked=True,
            reason=ERROR_CODE_ROLLBACK_FAILED,
            failed_paths=tuple(failed),
        )

    # 4. 文档终态: 重编译写 error + 原始稳定错误码并清除活动字段;
    #    上传不保留孤儿 error 文档(本轮 raw/meta/original 已撤销)。
    if manifest.kind is TransactionKind.RECOMPILE:
        meta_rel = f"raw/{doc_id}.meta.yaml"
        meta, meta_error = _read_doc_meta_safe(base_dir, doc_id)
        if meta is None:
            _record_recovery_failure(
                job_dir,
                meta_error or "doc meta missing during rollback",
                [meta_rel],
            )
            return RecoveryResult(
                job_id=manifest.job_id,
                blocked=True,
                reason=ERROR_CODE_ROLLBACK_FAILED,
                failed_paths=(meta_rel,),
            )
        cleaned_meta = {
            key: value
            for key, value in meta.items()
            if key not in META_ACTIVE_JOB_FIELDS
        }
        durable_write_yaml(base_dir / "raw" / f"{doc_id}.meta.yaml", cleaned_meta)
        code = manifest.failure.get("original_code") or ERROR_CODE_INTERRUPTED
        message = _sanitize_error_message(
            manifest.failure.get("original_message") or "编译任务被中断"
        )
        if not write_doc_compile_result(
            doc_id, "error",
            error_code=code, error_message=message, base_dir=base_dir,
        ):
            _record_recovery_failure(
                job_dir, "failed to write doc error terminal", [meta_rel]
            )
            return RecoveryResult(
                job_id=manifest.job_id,
                blocked=True,
                reason=ERROR_CODE_ROLLBACK_FAILED,
                failed_paths=(meta_rel,),
            )

    # 5. Manifest → ROLLED_BACK(原子状态提交点)。
    transition_manifest(job_dir, expected=TransactionState.ROLLBACKING,
                        target=TransactionState.ROLLED_BACK)
    return RecoveryResult(job_id=manifest.job_id, completed=True)


def recover_transaction(
    base_dir: Path,
    config: CompileRuntimeConfig,
    job_dir: Path,
    *,
    reason_code: str,
    reason_message: str,
) -> RecoveryResult:
    """恢复单个非终态事务(设计 §16、§17 状态表)。

    - COMMITTED / ROLLED_BACK: already_terminal,不做任何修改;
    - PREPARED 且业务未绑定: 清理事务目录,不写 interrupted;
    - PREPARED 已绑定 / SCHEDULED: 按中断事务回滚,绝不重新排队;
    - RUNNING: 验证进程身份并终止遗留树,确认退出后才回滚;
    - ROLLBACKING: 继续幂等回滚;
    - ManifestIntegrityError: 硬阻断,不触碰业务文件。
    """
    base_dir = Path(base_dir)
    job_dir = Path(job_dir)
    try:
        manifest = load_manifest(job_dir)
    except ManifestIntegrityError as exc:
        return RecoveryResult(
            job_id=job_dir.name,
            blocked=True,
            reason=f"manifest integrity: {_sanitize_error_message(exc)}",
        )

    if manifest.state in (TransactionState.COMMITTED, TransactionState.ROLLED_BACK):
        return RecoveryResult(job_id=manifest.job_id, already_terminal=True)

    if manifest.state is TransactionState.PREPARED:
        bound, binding_error = _doc_is_bound(base_dir, manifest)
        if binding_error is not None:
            return RecoveryResult(
                job_id=manifest.job_id,
                blocked=True,
                reason=f"cannot prove binding state: {binding_error}",
            )
        if not bound:
            try:
                shutil.rmtree(job_dir)
            except OSError as exc:
                return RecoveryResult(
                    job_id=manifest.job_id,
                    blocked=True,
                    reason=f"prepared transaction cleanup failed: "
                    f"{_sanitize_error_message(exc)}",
                )
            return RecoveryResult(
                job_id=manifest.job_id,
                completed=True,
                reason="prepared_unbound_cleaned",
            )

    if manifest.state is TransactionState.RUNNING:
        blocked_reason = _terminate_leftover_process(manifest, config)
        if blocked_reason is not None:
            return RecoveryResult(
                job_id=manifest.job_id, blocked=True, reason=blocked_reason
            )

    if manifest.state is not TransactionState.ROLLBACKING:
        changes: dict[str, Any] = {}
        if not manifest.failure.get("original_code"):
            changes["failure"] = {
                "original_code": reason_code,
                "original_message": _sanitize_error_message(reason_message),
            }
        manifest = transition_manifest(
            job_dir,
            expected=manifest.state,
            target=TransactionState.ROLLBACKING,
            **changes,
        )

    return _execute_rollback(base_dir, manifest)


def _load_yaml_mapping(path: Path) -> dict | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def _verify_committed_terminal(base_dir: Path, manifest: CompileManifest) -> list[str]:
    """COMMITTED 清理前验证(设计 §15、§18): meta compiled 无活动字段,
    必需产物语义合法。"""
    failures: list[str] = []
    doc_id = manifest.doc_id
    meta, meta_error = _read_doc_meta_safe(base_dir, doc_id)
    if meta_error is not None:
        failures.append(meta_error)
    elif meta is None:
        failures.append(f"raw/{doc_id}.meta.yaml missing")
    else:
        if meta.get("status") != "compiled":
            failures.append("doc meta status is not compiled")
        for field in META_ACTIVE_JOB_FIELDS:
            if field in meta:
                failures.append(f"doc meta still carries active field {field}")

    for path, label in (
        (base_dir / "wiki" / f"{doc_id}.summary.yaml", "summary"),
        (base_dir / "meta" / "ontology" / f"{doc_id}.ontology.yaml", "ontology"),
    ):
        data = _load_yaml_mapping(path)
        if data is None:
            failures.append(f"{label} missing or unparsable")
        elif data.get("doc_id") != doc_id:
            failures.append(f"{label} doc_id mismatch")

    index = _load_yaml_mapping(base_dir / "wiki" / "index.yaml")
    documents = index.get("documents") if index else None
    if not isinstance(documents, list) or sum(
        1
        for entry in documents
        if isinstance(entry, Mapping) and entry.get("id") == doc_id
    ) != 1:
        failures.append("wiki/index.yaml must contain exactly one entry for doc")

    relations_path = base_dir / "meta" / "relations" / f"{doc_id}.relations.yaml"
    if relations_path.exists():
        data = _load_yaml_mapping(relations_path)
        if data is None or data.get("doc_id") != doc_id:
            failures.append("doc relations missing or doc_id mismatch")

    for global_path in (
        base_dir / "meta" / "ontology" / "global_ontology.yaml",
        base_dir / "meta" / "relations" / "knowledge_graph.yaml",
        base_dir / "meta" / "ontology" / "entity_relations.yaml",
    ):
        if global_path.exists() and _load_yaml_mapping(global_path) is None:
            failures.append(f"{global_path.name} top-level structure invalid")
    return failures


def _verify_rolled_back_terminal(
    base_dir: Path, manifest: CompileManifest
) -> list[str]:
    """ROLLED_BACK 清理前验证(设计 §18): 七项产物与事务前存在性 + SHA
    一致,上传发布已撤销,重编译 meta 为 error 且错误码匹配原始失败原因。"""
    failures: list[str] = []
    targets = artifact_paths(base_dir, manifest.doc_id)
    for record in manifest.artifacts:
        target = targets[record.slot]
        if record.existed:
            if not target.is_file():
                failures.append(f"{record.path} missing after rollback")
            elif sha256_file(target) != record.original_sha256:
                failures.append(f"{record.path} sha256 differs from pre-transaction")
        elif target.exists():
            failures.append(f"{record.path} must not exist after rollback")

    if manifest.kind is TransactionKind.UPLOAD:
        intake_targets = _recompute_intake_targets(base_dir, manifest)
        if intake_targets is None:
            failures.append("published_intake paths fail safety recomputation")
        else:
            for path in intake_targets:
                if path.exists():
                    failures.append(f"upload published file not revoked: {path.name}")
    else:
        meta, meta_error = _read_doc_meta_safe(base_dir, manifest.doc_id)
        expected_code = manifest.failure.get("original_code")
        if meta_error is not None:
            failures.append(meta_error)
        elif meta is None:
            failures.append("recompile doc meta missing after rollback")
        else:
            if meta.get("status") != "error":
                failures.append("recompile doc meta is not error after rollback")
            if (
                expected_code is not None
                and meta.get("error_code") != expected_code
            ):
                failures.append(
                    "recompile error_code does not match original failure reason"
                )
            for field in META_ACTIVE_JOB_FIELDS:
                if field in meta:
                    failures.append(f"doc meta still carries active field {field}")
    return failures


def verify_terminal_transaction(
    base_dir: Path, manifest: CompileManifest
) -> VerificationReport:
    """验证终态事务是否满足清理合同(设计 §18);只看 state 不足以免验证。"""
    base_dir = Path(base_dir)
    if manifest.state in ACTIVE_STATES:
        return VerificationReport(
            job_id=manifest.job_id,
            state=manifest.state.value,
            ok=False,
            failures=("transaction is not terminal",),
        )
    if manifest.state is TransactionState.COMMITTED:
        failures = _verify_committed_terminal(base_dir, manifest)
    else:
        failures = _verify_rolled_back_terminal(base_dir, manifest)
    return VerificationReport(
        job_id=manifest.job_id,
        state=manifest.state.value,
        ok=not failures,
        failures=tuple(failures),
    )


def cleanup_terminal_transactions(
    base_dir: Path, config: CompileRuntimeConfig
) -> CleanupReport:
    """验证并清理全部终态事务(设计 §18)。

    - 非终态事务只报告保留,绝不删除;
    - Manifest 不可读或验证失败: 计入 blockers,保留目录;
    - 纯目录删除失败: 仅 warnings,后续启动或 CLI 继续尝试。
    """
    base_dir = Path(base_dir)
    cleaned: list[str] = []
    kept: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []
    for job_dir in list_transaction_dirs(config):
        try:
            manifest = load_manifest(job_dir)
        except ManifestIntegrityError as exc:
            blockers.append(
                f"manifest integrity: {job_dir.name}: "
                f"{_sanitize_error_message(exc)}"
            )
            continue
        if manifest.state in ACTIVE_STATES:
            kept.append(manifest.job_id)
            continue
        verification = verify_terminal_transaction(base_dir, manifest)
        if not verification.ok:
            blockers.append(
                f"terminal verification failed for {manifest.job_id}: "
                f"{list(verification.failures)}"
            )
            continue
        try:
            shutil.rmtree(job_dir)
        except OSError as exc:
            warnings.append(
                f"directory deletion failed for {manifest.job_id}: "
                f"{_sanitize_error_message(exc)}"
            )
            continue
        cleaned.append(manifest.job_id)
    return CleanupReport(
        cleaned=cleaned, kept=kept, warnings=warnings, blockers=blockers
    )


def find_orphan_compiling_docs(
    base_dir: Path, exclude_doc_ids: frozenset[str] | None = None
) -> tuple[list[str], list[str]]:
    """扫描全部 raw/*.meta.yaml,返回 (孤立 compiling doc_id 列表, 不可读 meta 列表)。

    只读;孤立 compiling 由调用方阻断并展示 doc_id,绝不自动修复。
    """
    base_dir = Path(base_dir)
    excluded = exclude_doc_ids or frozenset()
    orphans: list[str] = []
    unreadable: list[str] = []
    raw_dir = base_dir / "raw"
    if not raw_dir.is_dir():
        return orphans, unreadable
    suffix = ".meta.yaml"
    for meta_path in sorted(raw_dir.glob(f"*{suffix}")):
        doc_id = meta_path.name[: -len(suffix)]
        data = _load_yaml_mapping(meta_path)
        if data is None:
            unreadable.append(doc_id)
            continue
        if data.get("status") == "compiling" and doc_id not in excluded:
            orphans.append(doc_id)
    return orphans, unreadable


def _clean_unpublished_staging(config: CompileRuntimeConfig) -> list[str]:
    """清理未发布的 upload/compile staging 目录(.staging-*);失败仅警告。"""
    warnings: list[str] = []
    for root in (Path(config.transaction_dir), Path(config.upload_intake_dir)):
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if entry.is_dir() and entry.name.startswith(STAGING_PREFIX):
                try:
                    shutil.rmtree(entry)
                except OSError as exc:
                    warnings.append(
                        f"staging cleanup failed: {entry.name}: "
                        f"{_sanitize_error_message(exc)}"
                    )
    return warnings


def recover_startup(
    base_dir: Path, config: CompileRuntimeConfig
) -> StartupRecoveryReport:
    """启动恢复(设计 §17 顺序)。

    清理未发布 staging → 扫描正式事务 → 硬阻断(未知 schema/state、
    ≥2 活动事务、PID 身份不匹配、快照损坏)→ 按状态表恢复非终态事务
    → 验证并清理终态事务 → 扫描孤立 compiling(输出 doc_id 并阻断,
    绝不自动修复)→ ready。任何 blocker 都意味着 NOT ready。
    """
    base_dir = Path(base_dir)
    recovered: list[str] = []
    cleaned: list[str] = []
    blockers: list[str] = []
    warnings = _clean_unpublished_staging(config)

    manifests: list[CompileManifest] = []
    for job_dir in list_transaction_dirs(config):
        try:
            manifests.append(load_manifest(job_dir))
        except ManifestIntegrityError as exc:
            blockers.append(
                f"manifest integrity: {job_dir.name}: "
                f"{_sanitize_error_message(exc)}"
            )
    if blockers:
        return StartupRecoveryReport(
            ready=False,
            recovered=recovered,
            cleaned=cleaned,
            blockers=blockers,
            warnings=warnings,
        )

    active = [m for m in manifests if m.state in ACTIVE_STATES]
    if len(active) >= 2:
        blockers.append(
            "multiple active transactions: "
            + ", ".join(sorted(m.job_id for m in active))
        )
        return StartupRecoveryReport(
            ready=False,
            recovered=recovered,
            cleaned=cleaned,
            blockers=blockers,
            warnings=warnings,
        )

    if active:
        manifest = active[0]
        result = recover_transaction(
            base_dir,
            config,
            manifest.job_dir,
            reason_code=ERROR_CODE_INTERRUPTED,
            reason_message="compile interrupted by service restart",
        )
        if result.blocked:
            blockers.append(
                f"recovery blocked for {manifest.job_id}: {result.reason}"
            )
            return StartupRecoveryReport(
                ready=False,
                recovered=recovered,
                cleaned=cleaned,
                blockers=blockers,
                warnings=warnings,
            )
        if result.completed:
            recovered.append(manifest.job_id)

    cleanup = cleanup_terminal_transactions(base_dir, config)
    cleaned.extend(cleanup.cleaned)
    blockers.extend(cleanup.blockers)
    warnings.extend(cleanup.warnings)
    if blockers:
        return StartupRecoveryReport(
            ready=False,
            recovered=recovered,
            cleaned=cleaned,
            blockers=blockers,
            warnings=warnings,
        )

    orphans, unreadable = find_orphan_compiling_docs(base_dir)
    for doc_id in unreadable:
        blockers.append(f"unreadable doc meta: {doc_id}")
    for doc_id in orphans:
        blockers.append(f"orphan compiling document: {doc_id}")

    return StartupRecoveryReport(
        ready=not blockers,
        recovered=recovered,
        cleaned=cleaned,
        blockers=blockers,
        warnings=warnings,
    )
