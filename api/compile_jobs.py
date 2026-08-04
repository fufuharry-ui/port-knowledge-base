from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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

_SECRET_PATTERNS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"), "sk-<redacted>"),
    (
        re.compile(r"(?i)(?<![A-Za-z0-9])(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|apikey|access_token|token|key)=)[^&\s]+"),
        r"\1<redacted>",
    ),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)([^\r\n]+)"),
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
    )) or re.search(r"\b50[23]\b", value):
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


def artifact_paths(base_dir: Path, doc_id: str) -> tuple[Path, ...]:
    return (
        base_dir / "wiki" / f"{doc_id}.summary.yaml",
        base_dir / "wiki" / "index.yaml",
        base_dir / "meta" / "ontology" / f"{doc_id}.ontology.yaml",
        base_dir / "meta" / "ontology" / "global_ontology.yaml",
        base_dir / "meta" / "relations" / f"{doc_id}.relations.yaml",
        base_dir / "meta" / "relations" / "knowledge_graph.yaml",
        base_dir / "meta" / "ontology" / "entity_relations.yaml",
    )


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
