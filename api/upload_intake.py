"""E005 Task 7: 上传 intake staging 与原子发布。

对应设计文档 §7.1(upload-intake 目录结构)、§12.3(上传事务顺序)和
§12.4(上传失败与崩溃恢复)。本模块只负责上传字节暂存、PREPARED 上传事务
创建、三项业务目标的按序原子发布与 journal 驱动的精确撤销;去重(file_hash)、
调度锁与 doc 绑定由 API 层(Task 10)负责。

安全合同:

- stage_upload 只写 config.upload_intake_dir/.staging-{intake_id}/,
  暂存文件名即最终安全原始文件名(basename,扩展名防御性复检),staging
  之外不产生任何写入;
- publish_upload_intake 按 original → raw text → raw meta 固定顺序耐久
  发布,任一目标已存在即停止并抛 FileExistsError,绝不覆盖既有字节;
- intake.yaml journal 只在每个目标耐久发布完成后记录该目标的精确相对
  路径与 created_by_this_request 布尔值,是 rollback 的唯一权威依据;
- rollback_published_intake 只删除 journal 证明由本请求创建、且按
  base_dir + doc_id 重算后落在 originals/ 直接子文件或
  raw/{doc_id}.txt|raw/{doc_id}.meta.yaml 精确匹配的目标(镜像
  compile_transactions._recompute_intake_targets 的拒绝规则);幂等;
  journal 缺失视为本轮未发布(no-op),journal 不可解析则失败关闭、
  不删除任何文件;
- 空文本(whitespace-only)在发布路径拒绝(纵深防御;API 层更早已检查)。

所有耐久写入复用 api.durable_fs 原语,本模块不自行实现耐久写入。
"""
from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import yaml

from api.compile_transactions import (
    CompileManifest,
    PublishedIntake,
    TransactionKind,
    create_prepared_transaction,
    update_published_intake,
)
from api.durable_fs import durable_write_bytes, durable_write_yaml
from api.runtime_guard import CompileRuntimeConfig
from scripts.ingest import PARSERS, PreparedIngest

STAGING_PREFIX = ".staging-"
INTAKE_JOURNAL_FILENAME = "intake.yaml"
INTAKE_JOURNAL_SCHEMA_VERSION = 1

_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class StagedUpload:
    """已暂存的上传:staging 目录、最终安全原始文件名与暂存文件路径。"""

    intake_id: str
    original_name: str
    staging_dir: Path
    staged_file: Path


# ---------------------------------------------------------------------------
# stage_upload: 上传字节 → intake staging
# ---------------------------------------------------------------------------


def _safe_original_name(filename: str | None) -> str:
    """取 basename 并复检扩展名(防御性;API 层 _safe_upload_name 已校验)。"""
    supplied = filename or ""
    normalized = supplied.replace("\\", "/")
    safe = PurePosixPath(normalized).name
    if not safe or safe in (".", ".."):
        raise ValueError(f"upload filename is empty or unsafe: {supplied!r}")
    suffix = Path(safe).suffix.lower()
    if suffix not in PARSERS:
        raise ValueError(f"unsupported upload extension: {suffix or supplied!r}")
    return safe


def _generate_intake_id() -> str:
    now = datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%S%f}Z-{secrets.token_hex(4)}"


def _read_upload_payload(stream: Any) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def stage_upload(
    file: Any, config: CompileRuntimeConfig, *, safe_name: str | None = None
) -> StagedUpload:
    """把上传字节流耐久写入 .staging-{intake_id}/ 下的最终安全文件名。

    file 只需暴露 .file(二进制流)与 .filename;API 层在调度锁下选定无冲突
    名称时可经 safe_name 传入,否则由 file.filename 取 basename 派生。
    扩展名与空文件名在此防御性复检;任何失败清理 staging, staging 之外
    不产生写入。
    """
    original_name = _safe_original_name(
        safe_name if safe_name is not None else getattr(file, "filename", None)
    )
    intake_id = _generate_intake_id()
    staging_dir = Path(config.upload_intake_dir) / f"{STAGING_PREFIX}{intake_id}"
    staged_file = staging_dir / original_name
    try:
        staging_dir.mkdir(parents=True, exist_ok=False)
        durable_write_bytes(staged_file, _read_upload_payload(file.file))
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return StagedUpload(
        intake_id=intake_id,
        original_name=original_name,
        staging_dir=staging_dir,
        staged_file=staged_file,
    )


def discard_staged_upload(staged: StagedUpload) -> None:
    """幂等删除本请求的 intake staging 目录(中止/失败路径专用)。"""
    staging_dir = Path(staged.staging_dir)
    if staging_dir.name.startswith(STAGING_PREFIX) and staging_dir.is_dir():
        shutil.rmtree(staging_dir, ignore_errors=True)


def cleanup_orphan_intake_staging(config: CompileRuntimeConfig) -> list[str]:
    """删除 upload_intake_dir 下全部 .staging-* 目录,返回失败警告列表。

    所有权说明: CLI 单事务恢复(scripts/compile_recovery.py)对
    PREPARED-unbound 上传事务只清理事务目录,不清理 upload-intake staging
    (staging 与 manifest 无绑定关系);后续 CLI 可在恢复完成后调用本助手,
    规则与 recover_startup 的 staging 清理一致。本任务不修改 CLI。
    """
    warnings: list[str] = []
    root = Path(config.upload_intake_dir)
    if not root.is_dir():
        return warnings
    for entry in sorted(root.iterdir()):
        if entry.is_dir() and entry.name.startswith(STAGING_PREFIX):
            try:
                shutil.rmtree(entry)
            except OSError as exc:
                warnings.append(
                    f"intake staging cleanup failed: {entry.name}: {exc}"
                )
    return warnings


# ---------------------------------------------------------------------------
# prepare_upload_transaction: PREPARED(kind=UPLOAD) 事务
# ---------------------------------------------------------------------------


def prepare_upload_transaction(
    staged: StagedUpload,
    prepared: PreparedIngest,
    base_dir: Path,
    config: CompileRuntimeConfig,
) -> CompileManifest:
    """为新 doc_id 创建 PREPARED 上传事务(七项快照,published=False)。

    previous_meta=None: 上传事务没有事务前 meta;intake staging 保留,
    业务目录不变。
    """
    intake = PublishedIntake(
        original_path=f"originals/{staged.original_name}",
        raw_text_path=f"raw/{prepared.doc_id}.txt",
        raw_meta_path=f"raw/{prepared.doc_id}.meta.yaml",
        published=False,
    )
    return create_prepared_transaction(
        base_dir=Path(base_dir),
        config=config,
        doc_id=prepared.doc_id,
        kind=TransactionKind.UPLOAD,
        previous_meta=None,
        published_intake=intake,
    )


# ---------------------------------------------------------------------------
# intake.yaml journal: 只在每个目标耐久发布完成后追加记录
# ---------------------------------------------------------------------------


def _journal_path(job_dir: Path) -> Path:
    return Path(job_dir) / INTAKE_JOURNAL_FILENAME


def _read_journal_entries(job_dir: Path) -> list[dict[str, Any]]:
    path = _journal_path(job_dir)
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = data.get("entries") if isinstance(data, Mapping) else None
    return list(entries) if isinstance(entries, list) else []


def _append_journal_entry(job_dir: Path, relative_path: str) -> None:
    entries = _read_journal_entries(job_dir)
    entries.append({"path": relative_path, "created_by_this_request": True})
    durable_write_yaml(
        _journal_path(job_dir),
        {"schema_version": INTAKE_JOURNAL_SCHEMA_VERSION, "entries": entries},
    )


# ---------------------------------------------------------------------------
# 发布目标安全重算(镜像 _recompute_intake_targets 的拒绝规则)
# ---------------------------------------------------------------------------


def _recompute_safe_intake_target(
    base_dir: Path, doc_id: str, relative_path: Any
) -> Path | None:
    """把声明的相对路径重算为安全绝对目标;不安全返回 None(失败关闭)。

    - raw 目标必须与 raw/{doc_id}.txt 或 raw/{doc_id}.meta.yaml 精确一致;
    - original 目标必须是 originals/ 的直接子文件;
    - 绝对路径、.. 越界、反斜杠与其他前缀一律拒绝。
    """
    if not isinstance(relative_path, str) or not relative_path:
        return None
    if "\\" in relative_path:
        return None
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or PureWindowsPath(relative_path).is_absolute():
        return None
    parts = pure.parts
    if any(part == ".." for part in parts):
        return None
    if parts[0] == "raw":
        if relative_path not in (f"raw/{doc_id}.txt", f"raw/{doc_id}.meta.yaml"):
            return None
    elif parts[0] == "originals":
        if len(parts) != 2:
            return None
    else:
        return None
    return Path(base_dir) / pure.as_posix()


# ---------------------------------------------------------------------------
# publish_upload_intake: original → raw text → raw meta 顺序原子发布
# ---------------------------------------------------------------------------


def publish_upload_intake(
    manifest: CompileManifest,
    staged: StagedUpload,
    prepared: PreparedIngest,
    base_dir: Path,
) -> CompileManifest:
    """按固定顺序耐久发布三项上传目标并翻转 published=True。

    - 空文本(whitespace-only)拒绝发布(纵深防御);
    - manifest.published_intake 与 staged/prepared 不一致时失败关闭;
    - 任一目标已存在: 停止并抛 FileExistsError,既有字节不动,该目标
      不进入 journal(非本请求创建);
    - 每个目标耐久发布完成后才追加 intake.yaml 记录;
    - 全部完成后经原子 Manifest 写入翻转 published_intake.published=True。
    """
    base_dir = Path(base_dir)
    intake = manifest.published_intake
    if intake is None:
        raise ValueError("upload manifest missing published_intake section")
    if not prepared.text_bytes or not prepared.text_bytes.strip():
        raise ValueError("refusing to publish empty document text")
    if manifest.doc_id != prepared.doc_id:
        raise ValueError(
            f"manifest doc_id {manifest.doc_id!r} does not match "
            f"prepared doc_id {prepared.doc_id!r}"
        )
    expected = (
        f"originals/{staged.original_name}",
        f"raw/{prepared.doc_id}.txt",
        f"raw/{prepared.doc_id}.meta.yaml",
    )
    declared = (intake.original_path, intake.raw_text_path, intake.raw_meta_path)
    if declared != expected:
        raise ValueError(
            f"manifest published_intake paths {declared!r} do not match "
            f"staged upload targets {expected!r}"
        )

    original_payload = staged.staged_file.read_bytes()
    steps = (
        (intake.original_path, "bytes", original_payload),
        (intake.raw_text_path, "bytes", prepared.text_bytes),
        (intake.raw_meta_path, "yaml", prepared.meta),
    )
    for relative_path, mode, payload in steps:
        target = _recompute_safe_intake_target(
            base_dir, manifest.doc_id, relative_path
        )
        if target is None:
            raise ValueError(f"unsafe intake publish target: {relative_path!r}")
        if target.exists() or target.is_symlink():
            raise FileExistsError(
                f"intake publish target already exists: {relative_path}"
            )
        if mode == "bytes":
            durable_write_bytes(target, payload)
        else:
            durable_write_yaml(target, payload)
        _append_journal_entry(manifest.job_dir, relative_path)

    return update_published_intake(
        manifest.job_dir, replace(intake, published=True)
    )


# ---------------------------------------------------------------------------
# rollback_published_intake: journal 驱动的精确撤销
# ---------------------------------------------------------------------------


def rollback_published_intake(
    manifest: CompileManifest, base_dir: Path
) -> list[str]:
    """删除且仅删除 journal 证明由本请求创建的目标;返回失败路径列表。

    - journal 缺失: 本轮未发布任何目标,no-op;
    - journal 不可解析或结构非法: 失败关闭,不删除任何文件;
    - created_by_this_request 非 True 的条目跳过(绝不触碰既有文件);
    - 路径必须与 Manifest 声明的三项 intake 目标(published_intake 的
      original_path/raw_text_path/raw_meta_path)精确一致;任何其他路径
      (含 originals/ 合法直接子文件)计入 failures 并保留文件(失败关闭);
    - 重算安全校验作为第二道防线(raw 非本 doc_id 精确目标、originals
      非直接子文件、越界等): 计入 failures 并保留文件;
    - 幂等: 目标已不存在视为成功,第二次调用为 no-op。
    """
    base_dir = Path(base_dir)
    journal = _journal_path(manifest.job_dir)
    if not journal.is_file():
        return []
    try:
        data = yaml.safe_load(journal.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return [INTAKE_JOURNAL_FILENAME]
    entries = data.get("entries") if isinstance(data, Mapping) else None
    if not isinstance(entries, list):
        return [INTAKE_JOURNAL_FILENAME]

    intake = manifest.published_intake
    declared = (
        {intake.original_path, intake.raw_text_path, intake.raw_meta_path}
        if intake is not None
        else frozenset()
    )
    failures: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            failures.append(INTAKE_JOURNAL_FILENAME)
            continue
        relative_path = entry.get("path")
        if entry.get("created_by_this_request") is not True:
            continue
        if relative_path not in declared:
            failures.append(str(relative_path))
            continue
        target = _recompute_safe_intake_target(
            base_dir, manifest.doc_id, relative_path
        )
        if target is None:
            failures.append(str(relative_path))
            continue
        try:
            if target.is_file() or target.is_symlink():
                target.unlink()
            elif target.exists():
                failures.append(str(relative_path))
        except OSError:
            failures.append(str(relative_path))
    return failures
