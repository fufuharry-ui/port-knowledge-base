from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from api.compile_transactions import artifact_paths
from scripts.doc_admin import read_doc_meta, write_doc_compile_result

__all__ = [
    "artifact_paths",
    "create_artifact_snapshot",
    "restore_artifact_snapshot",
    "run_compile_task",
    "sanitize_compile_error",
    "classify_compile_error",
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


@dataclass(frozen=True)
class SnapshotEntry:
    target: Path
    relative_path: str
    existed: bool
    backup: Path | None


@dataclass(frozen=True)
class ArtifactSnapshot:
    entries: tuple[SnapshotEntry, ...]


# artifact_paths 由 api.compile_transactions 提供并在此再导出,
# 保持 E004 既有导入路径 from api.compile_jobs import artifact_paths 兼容。


def create_artifact_snapshot(
    base_dir: Path,
    doc_id: str,
    snapshot_dir: Path,
) -> ArtifactSnapshot:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, target in enumerate(artifact_paths(base_dir, doc_id)):
        relative_path = target.relative_to(base_dir).as_posix()
        if target.exists():
            backup = snapshot_dir / f"{index:02d}.bin"
            backup.write_bytes(target.read_bytes())
            entries.append(SnapshotEntry(
                target=target,
                relative_path=relative_path,
                existed=True,
                backup=backup,
            ))
        else:
            entries.append(SnapshotEntry(
                target=target,
                relative_path=relative_path,
                existed=False,
                backup=None,
            ))
    return ArtifactSnapshot(entries=tuple(entries))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def restore_artifact_snapshot(snapshot: ArtifactSnapshot) -> list[str]:
    failures = []
    for entry in snapshot.entries:
        try:
            if entry.existed:
                if entry.backup is None:
                    raise RuntimeError("snapshot backup missing")
                _atomic_write_bytes(entry.target, entry.backup.read_bytes())
            else:
                entry.target.unlink(missing_ok=True)
        except Exception:
            failures.append(entry.relative_path)
    return failures


def _diagnostic_text(result: subprocess.CompletedProcess, meta: dict | None) -> str:
    parts = []
    if meta and meta.get("error_message"):
        parts.append(str(meta["error_message"]))
    if result.stderr:
        parts.append(result.stderr[-MAX_CAPTURE_CHARS:])
    if result.stdout:
        parts.append(result.stdout[-MAX_CAPTURE_CHARS:])
    parts.append(f"returncode={result.returncode}")
    return "\n".join(parts)


def _persist_terminal_error(
    doc_id: str,
    base: Path,
    code: CompileErrorCode,
    message: str,
) -> None:
    written = write_doc_compile_result(
        doc_id,
        "error",
        error_code=code,
        error_message=sanitize_compile_error(message),
        base_dir=base,
    )
    if not written:
        logger.error(
            "compile job terminal metadata missing for %s; code=%s",
            doc_id,
            code,
        )


def run_compile_task(doc_id: str, base_dir: Path | None = None) -> None:
    base = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent
    with COMPILE_EXECUTION_LOCK:
        try:
            with TemporaryDirectory(prefix=f"port-kb-{doc_id}-") as temp_name:
                try:
                    snapshot = create_artifact_snapshot(base, doc_id, Path(temp_name))
                except Exception as exc:
                    _persist_terminal_error(
                        doc_id,
                        base,
                        "compile_failed",
                        f"snapshot creation failed: {type(exc).__name__}: {exc}",
                    )
                    return

                failure_code: CompileErrorCode | None = None
                failure_message = ""
                try:
                    compile_script = base / "scripts" / "compile.py"
                    if not compile_script.exists():
                        raise FileNotFoundError("compile script missing")
                    env = {**os.environ, "PYTHONUTF8": "1"}
                    result = subprocess.run(
                        [sys.executable, "-m", "scripts.compile", doc_id],
                        cwd=str(base),
                        env=env,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    meta = read_doc_meta(doc_id, base)
                    if result.returncode == 0 and meta and meta.get("status") == "compiled":
                        if not write_doc_compile_result(doc_id, "compiled", base_dir=base):
                            logger.error(
                                "compiled terminal metadata missing for %s",
                                doc_id,
                            )
                        return
                    diagnostic = _diagnostic_text(result, meta)
                    failure_code = classify_compile_error(diagnostic)
                    failure_message = sanitize_compile_error(diagnostic)
                except Exception as exc:
                    failure_code = classify_compile_error(str(exc))
                    failure_message = sanitize_compile_error(
                        f"{type(exc).__name__}: {exc}"
                    )

                rollback_failures = restore_artifact_snapshot(snapshot)
                if rollback_failures:
                    failure_code = "rollback_failed"
                    failure_message = sanitize_compile_error(
                        f"{failure_message}; rollback failed: {', '.join(rollback_failures)}"
                    )
                _persist_terminal_error(
                    doc_id,
                    base,
                    failure_code or "compile_failed",
                    failure_message or "编译失败",
                )
        except Exception as exc:
            _persist_terminal_error(
                doc_id,
                base,
                classify_compile_error(str(exc)),
                f"compile task infrastructure failed: {type(exc).__name__}: {exc}",
            )
