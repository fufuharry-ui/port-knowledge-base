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
from api.runtime_guard import CompileRuntimeConfig

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
