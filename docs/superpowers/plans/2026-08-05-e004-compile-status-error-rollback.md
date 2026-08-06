# E004 后台编译状态、错误回滚与前端友好提示闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将上传和重编译统一为可靠的后台编译任务合同，使API来源编译在当前单API进程内具备可验证的状态终态、失败回滚、错误脱敏和前端友好提示。

**Architecture:** `api/main.py`只保留HTTP端点和短时调度临界区；新增`api/compile_jobs.py`封装错误分类、快照、回滚、子进程执行和进程内全局串行锁；`scripts/doc_admin.py`提供原子元数据状态读写。前端沿用现有`/api/v1/wiki/index`轮询，不新增任务接口或推送协议，通过稳定`error_code`显示固定中文提示。

**Tech Stack:** Python 3.11/3.12、FastAPI、Starlette BackgroundTasks、PyYAML、pytest、Next.js 16.2.2、React 19.2.4、TypeScript、Jest 30、Testing Library、PowerShell 7、Git worktree。

**Suggested Effort:** High

**Multi-Agent:** 建议使用多个独立Agent按任务串行实施和复核；禁止两个Agent并行修改同一worktree或共享文件。推荐每个任务由新实施Agent完成，再由规格审查Agent和代码质量审查Agent依次复核。

**Design Spec:** `docs/superpowers/specs/2026-08-05-e004-compile-status-error-rollback-design.md`

## Global Constraints

- `target_backend: api`；不得修改`app/`后端。
- 实施分支使用`fix/e004-compile-status-rollback`，从最新`origin/docs/e004-compile-status-design`创建，使已确认设计和本计划进入同一最终PR。
- PR目标分支只能是`dev`；不得以`main`或`master`为基线。
- 不修改`scripts/compile.py`和`scripts/relate.py`的核心编译语义；关系检测warning仍不升级为整篇编译失败。
- 不新增数据库、Redis、Celery、任务查询API、SSE、WebSocket、进度百分比、任务取消或技术详情入口。
- API接受任务后必须在响应返回前写入`status=compiling`；后台最终必须为`compiled`或`error`。
- 同一文档`compiling`时重复重编译返回`409`和`detail.code=compile_in_progress`，不得调度第二任务。
- 每次API来源编译都快照七类产物；失败时恢复原字节或删除本轮新增文件。
- `raw/{doc_id}.meta.yaml`不整体回滚，必须保留本轮最新`status`、`error_code`和脱敏`error_message`。
- `error_message`单行、最多500字符；不得包含完整API Key、Bearer令牌、Authorization值、密码、token、secret或敏感URL查询参数。
- 前端不得读取或渲染`error_message`，不得显示HTTP状态码、`Conflict`、异常类名、环境变量名、路径、堆栈或子进程原始输出。
- 存在`raw/compiling`文档时每3000毫秒轮询；全部进入`compiled/error`后停止。
- 首次加载已经是`error`的历史文档不弹Toast；本轮`raw/compiling -> error`只弹一次；重新进入`compiling`后下一轮失败可再次提示。
- 所有后端测试使用`tmp_path`和mock子进程；不得调用真实LLM、真实Embedding、外部网络或真实编译进程。
- `originals/`、`raw/`、`wiki/`、`meta/`真实数据在任务前后路径、大小、SHA-256和`mtime_ns`必须一致。
- PowerShell续行只使用反引号；不得使用Unix反斜杠续行。
- 禁止force、rebase、reset、clean、amend、`--admin`、直接推送`dev`、删除来源不明文件。
- 每个代码任务必须执行红灯、绿灯和目标回归；每个任务完成后独立commit。
- 未获得用户新的Git授权前，本计划中的实施commit、push、PR和merge步骤只作为待执行指令，不得提前执行。

---

## File Map

### Create

- `api/compile_jobs.py`：后台编译执行、错误分类与脱敏、产物快照与回滚、进程内全局执行锁。
- `tests/test_compile_jobs.py`：上述模块的纯逻辑、文件恢复、子进程终态和串行化测试。
- `frontend/tests/unit/api-errors.test.ts`：结构化API错误解析与用户友好文案测试。

### Modify

- `scripts/doc_admin.py`：增加原子元数据读写、调度前状态准备和终态写入；保留删除能力。
- `tests/test_doc_admin.py`：覆盖`compiling`准备、冲突、错误字段清理和原子终态。
- `api/main.py`：统一上传和重编译调度、409合同、`error_code`目录投影；删除两套旧后台路径。
- `tests/test_api.py`：覆盖上传即时`compiling`、统一任务、重复调度409和目录错误码。
- `frontend/src/lib/api.ts`：增加`CompileErrorCode`、`ApiError`、响应体错误码解析和统一友好映射。
- `frontend/src/components/WikiCard.tsx`：错误状态行内友好说明。
- `frontend/tests/unit/WikiCard.actions.test.tsx`：错误说明和技术信息不泄露。
- `frontend/src/app/wiki/page.tsx`：3000毫秒条件轮询、状态转换检测、单次Toast和友好即时错误。
- `frontend/tests/unit/wiki-polling.test.tsx`：fake timer、停止轮询、历史错误、单轮失败Toast和重试新轮次。
- `frontend/src/components/UploadZone.tsx`：上传即时失败使用统一友好文案。
- `frontend/tests/unit/UploadZone.test.tsx`：原始异常不泄露。
- `CLAUDE.md`：将已知风险第9项更新为E004已处理，同时保留跨进程、强制终止和通用共享YAML风险边界。

---

## Execution Preflight

- [ ] **Step 1: Fetch and verify the planning branch**

```powershell
$Repo = "D:\administrator\Desktop\大模型产品化\port-knowledge-base"
$Worktree = "D:\administrator\Desktop\大模型产品化\port-knowledge-base-e004"
git -C $Repo fetch origin
git -C $Repo rev-parse origin/docs/e004-compile-status-design
git -C $Repo status --short
```

Expected:

- `origin/docs/e004-compile-status-design`存在并包含设计规格和本计划；
- 主工作区无来源不明修改；有修改时停止并报告。

- [ ] **Step 2: Create the isolated implementation worktree**

```powershell
git -C $Repo worktree add $Worktree -b fix/e004-compile-status-rollback origin/docs/e004-compile-status-design
Set-Location $Worktree
git branch --show-current
git status --short
git log -5 --oneline
git rev-parse --show-toplevel
```

Expected:

- branch=`fix/e004-compile-status-rollback`；
- worktree clean；
- HEAD包含`docs: add E004 implementation plan`。

- [ ] **Step 3: Install dependencies without changing lockfiles**

```powershell
python -m pip install -r requirements.txt
Push-Location frontend
npm ci
Pop-Location
```

Expected: both commands exit 0；`git status --short`仍为空。

- [ ] **Step 4: Capture the immutable data manifest**

```powershell
$BeforeManifest = Join-Path $env:TEMP "e004-data-before.json"
@'
import hashlib, json, sys
from pathlib import Path
root = Path.cwd()
rows = []
for folder in ("originals", "raw", "wiki", "meta"):
    base = root / folder
    if not base.exists():
        continue
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        stat = path.stat()
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
Path(sys.argv[1]).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(rows))
'@ | python - $BeforeManifest
```

Expected: prints the current real-data file count and writes the manifest only under`$env:TEMP`。

- [ ] **Step 5: Run the baseline gates**

```powershell
$env:OPENAI_API_KEY = ""
$env:EMBEDDING_API_KEY = ""
$env:HTTP_PROXY = "http://127.0.0.1:9"
$env:HTTPS_PROXY = "http://127.0.0.1:9"
python -m pytest tests/ -q
Push-Location frontend
npm test -- --runInBand
npm run build
Pop-Location
```

Expected: all baseline commands exit 0。任一失败时停止实施，报告失败命令和输出，不把基线失败归因于E004。

---

### Task 1: Atomic Document Compile State

**Files:**

- Modify: `scripts/doc_admin.py`
- Modify: `tests/test_doc_admin.py`

**Interfaces:**

- Produces: `read_doc_meta(doc_id: str, base_dir: Path | None = None) -> dict | None`
- Produces: `prepare_doc_compile(doc_id: str, base_dir: Path | None = None) -> dict`
- Produces: `write_doc_compile_result(doc_id: str, status: str, *, error_code: str | None = None, error_message: str | None = None, base_dir: Path | None = None) -> bool`
- Produces: `_atomic_yaml_dump(path: Path, data: dict) -> None`
- Consumers: Tasks 4 and 5.

- [ ] **Step 1: Write failing state-transition tests**

Append deterministic tests to`tests/test_doc_admin.py`:

```python
from scripts.doc_admin import prepare_doc_compile, read_doc_meta, write_doc_compile_result


def test_prepare_doc_compile_marks_compiling_and_clears_errors(project_dir):
    doc_id = "doc_20260805_001"
    meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({
            "id": doc_id,
            "status": "error",
            "error_code": "compile_failed",
            "error_message": "old failure",
        }, allow_unicode=True),
        encoding="utf-8",
    )

    result = prepare_doc_compile(doc_id, base_dir=project_dir)

    assert result == {"doc_id": doc_id, "prepared": True}
    meta = read_doc_meta(doc_id, base_dir=project_dir)
    assert meta["status"] == "compiling"
    assert "error_code" not in meta
    assert "error_message" not in meta


def test_prepare_doc_compile_rejects_existing_compiling(project_dir):
    doc_id = "doc_20260805_002"
    meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiling"}),
        encoding="utf-8",
    )

    result = prepare_doc_compile(doc_id, base_dir=project_dir)

    assert result == {
        "doc_id": doc_id,
        "prepared": False,
        "reason": "compile_in_progress",
    }


def test_write_doc_compile_result_persists_terminal_error(project_dir):
    doc_id = "doc_20260805_003"
    meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiling"}),
        encoding="utf-8",
    )

    written = write_doc_compile_result(
        doc_id,
        "error",
        error_code="timeout",
        error_message="request timed out",
        base_dir=project_dir,
    )

    assert written is True
    meta = read_doc_meta(doc_id, base_dir=project_dir)
    assert meta["status"] == "error"
    assert meta["error_code"] == "timeout"
    assert meta["error_message"] == "request timed out"


def test_write_doc_compile_result_clears_errors_on_success(project_dir):
    doc_id = "doc_20260805_004"
    meta_path = project_dir / "raw" / f"{doc_id}.meta.yaml"
    meta_path.write_text(
        yaml.safe_dump({
            "id": doc_id,
            "status": "error",
            "error_code": "compile_failed",
            "error_message": "old",
        }),
        encoding="utf-8",
    )

    assert write_doc_compile_result(doc_id, "compiled", base_dir=project_dir) is True
    meta = read_doc_meta(doc_id, base_dir=project_dir)
    assert meta["status"] == "compiled"
    assert "error_code" not in meta
    assert "error_message" not in meta
```

- [ ] **Step 2: Run the tests and confirm the red state**

```powershell
python -m pytest tests/test_doc_admin.py -k "prepare_doc_compile or write_doc_compile_result" -v
```

Expected: collection/import failure because the new functions do not exist.

- [ ] **Step 3: Implement atomic metadata helpers**

Add these behaviors to`scripts/doc_admin.py`:

```python
import tempfile


def _raw_dir(base_dir: Path | None = None) -> Path:
    return Path(base_dir) / "raw" if base_dir is not None else RAW_DIR


def _atomic_yaml_dump(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            yaml.dump(data, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def read_doc_meta(doc_id: str, base_dir: Path | None = None) -> dict | None:
    path = _raw_dir(base_dir) / f"{doc_id}.meta.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def prepare_doc_compile(doc_id: str, base_dir: Path | None = None) -> dict:
    path = _raw_dir(base_dir) / f"{doc_id}.meta.yaml"
    meta = read_doc_meta(doc_id, base_dir)
    if meta is None:
        return {"doc_id": doc_id, "prepared": False, "reason": "meta_not_found"}
    if meta.get("status") == "compiling":
        return {"doc_id": doc_id, "prepared": False, "reason": "compile_in_progress"}
    meta["status"] = "compiling"
    meta.pop("error_code", None)
    meta.pop("error_message", None)
    _atomic_yaml_dump(path, meta)
    return {"doc_id": doc_id, "prepared": True}


def write_doc_compile_result(
    doc_id: str,
    status: str,
    *,
    error_code: str | None = None,
    error_message: str | None = None,
    base_dir: Path | None = None,
) -> bool:
    if status not in {"compiled", "error"}:
        raise ValueError(f"unsupported terminal compile status: {status}")
    path = _raw_dir(base_dir) / f"{doc_id}.meta.yaml"
    meta = read_doc_meta(doc_id, base_dir)
    if meta is None:
        return False
    meta["status"] = status
    if status == "error":
        meta["error_code"] = error_code or "compile_failed"
        meta["error_message"] = error_message or "编译失败"
    else:
        meta.pop("error_code", None)
        meta.pop("error_message", None)
    _atomic_yaml_dump(path, meta)
    return True
```

Change`recompile_doc()`to delegate to`prepare_doc_compile()`so it no longer writes`raw`。Preserve its existing return keys for compatibility:

```python
def recompile_doc(doc_id: str) -> dict:
    result = prepare_doc_compile(doc_id)
    return {
        "doc_id": doc_id,
        "reset": result.get("prepared", False),
        **({"reason": result["reason"]} if result.get("reason") else {}),
    }
```

Do not change`remove_doc()`semantics in this task.

- [ ] **Step 4: Run target and existing document-admin tests**

```powershell
python -m pytest tests/test_doc_admin.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 1**

```powershell
git add scripts/doc_admin.py tests/test_doc_admin.py
git commit -m "feat: add atomic compile state transitions"
```

---

### Task 2: Compile Error Classification and Redaction

**Files:**

- Create: `api/compile_jobs.py`
- Create: `tests/test_compile_jobs.py`

**Interfaces:**

- Produces: `CompileErrorCode` literal values.
- Produces: `sanitize_compile_error(text: str, limit: int = 500) -> str`
- Produces: `classify_compile_error(text: str) -> str`
- Consumers: Task 4.

- [ ] **Step 1: Write failing classification and redaction tests**

Create`tests/test_compile_jobs.py`with:

```python
import pytest

from api.compile_jobs import classify_compile_error, sanitize_compile_error


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("未找到 OPENAI_API_KEY", "llm_configuration"),
        ("401 Unauthorized invalid api key", "llm_configuration"),
        ("Connection refused by provider", "service_unavailable"),
        ("503 Service Unavailable", "service_unavailable"),
        ("ReadTimeout request timed out", "timeout"),
        ("找不到原始文本", "document_processing"),
        ("unexpected compiler failure", "compile_failed"),
    ],
)
def test_classify_compile_error(text, expected):
    assert classify_compile_error(text) == expected


def test_sanitize_compile_error_redacts_secrets_and_flattens_lines():
    raw = (
        "Authorization: Bearer token-abc\n"
        "OPENAI_API_KEY=sk-supersecret123\n"
        "https://provider.test/v1?api_key=query-secret&model=x\n"
        "password=hunter2"
    )

    safe = sanitize_compile_error(raw)

    assert "token-abc" not in safe
    assert "sk-supersecret123" not in safe
    assert "query-secret" not in safe
    assert "hunter2" not in safe
    assert "\n" not in safe
    assert "<redacted>" in safe


def test_sanitize_compile_error_limits_to_500_characters():
    safe = sanitize_compile_error("x" * 900)
    assert len(safe) == 500
```

- [ ] **Step 2: Verify red state**

```powershell
python -m pytest tests/test_compile_jobs.py -v
```

Expected: import failure because`api.compile_jobs`does not exist.

- [ ] **Step 3: Implement stable error codes and sanitization**

Create`api/compile_jobs.py`with these constants and functions:

```python
from __future__ import annotations

import re
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
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (
        re.compile(r"(?i)([?&](?:api[_-]?key|apikey|access_token|token|key)=)[^&\s]+"),
        r"\1<redacted>",
    ),
    (
        re.compile(r"(?i)(authorization\s*[:=]\s*)([^\s,;]+)"),
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
        "connection refused", "connection error", "service unavailable", "503",
        "502", "bad gateway", "name resolution", "network is unreachable", "服务不可用",
    )):
        return "service_unavailable"
    if any(token in value for token in (
        "找不到原始文本", "document content", "decode", "parse", "内容为空",
    )):
        return "document_processing"
    return "compile_failed"
```

Do not log or return the original unsanitized text from these helpers.

- [ ] **Step 4: Run target tests**

```powershell
python -m pytest tests/test_compile_jobs.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add api/compile_jobs.py tests/test_compile_jobs.py
git commit -m "feat: classify and redact compile failures"
```

---

### Task 3: Artifact Snapshot and Byte-Exact Rollback

**Files:**

- Modify: `api/compile_jobs.py`
- Modify: `tests/test_compile_jobs.py`

**Interfaces:**

- Produces: `artifact_paths(base_dir: Path, doc_id: str) -> tuple[Path, ...]`
- Produces: `ArtifactSnapshot`
- Produces: `create_artifact_snapshot(base_dir: Path, doc_id: str, snapshot_dir: Path) -> ArtifactSnapshot`
- Produces: `restore_artifact_snapshot(snapshot: ArtifactSnapshot) -> list[str]`
- Consumers: Task 4.

- [ ] **Step 1: Write failing snapshot tests**

Append tests that prove existing files are restored byte-for-byte and new files are deleted:

```python
from pathlib import Path

from api.compile_jobs import create_artifact_snapshot, restore_artifact_snapshot


def test_restore_artifact_snapshot_restores_existing_and_deletes_new(tmp_path):
    base = tmp_path / "repo"
    snapshot_dir = tmp_path / "snapshot"
    doc_id = "doc_20260805_010"
    index = base / "wiki" / "index.yaml"
    summary = base / "wiki" / f"{doc_id}.summary.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index\r\n")

    snapshot = create_artifact_snapshot(base, doc_id, snapshot_dir)

    index.write_bytes(b"new-index\n")
    summary.write_bytes(b"new-summary\n")
    failures = restore_artifact_snapshot(snapshot)

    assert failures == []
    assert index.read_bytes() == b"old-index\r\n"
    assert not summary.exists()


def test_artifact_snapshot_contains_all_seven_paths(tmp_path):
    base = tmp_path / "repo"
    snapshot = create_artifact_snapshot(
        base,
        "doc_20260805_011",
        tmp_path / "snapshot",
    )
    relative = {entry.target.relative_to(base).as_posix() for entry in snapshot.entries}
    assert relative == {
        "wiki/doc_20260805_011.summary.yaml",
        "wiki/index.yaml",
        "meta/ontology/doc_20260805_011.ontology.yaml",
        "meta/ontology/global_ontology.yaml",
        "meta/relations/doc_20260805_011.relations.yaml",
        "meta/relations/knowledge_graph.yaml",
        "meta/ontology/entity_relations.yaml",
    }
```

- [ ] **Step 2: Verify red state**

```powershell
python -m pytest tests/test_compile_jobs.py -k "artifact_snapshot" -v
```

Expected: import failure for snapshot interfaces.

- [ ] **Step 3: Implement snapshot records and atomic byte restore**

Add to`api/compile_jobs.py`:

```python
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SnapshotEntry:
    target: Path
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
        if target.exists():
            backup = snapshot_dir / f"{index:02d}.bin"
            backup.write_bytes(target.read_bytes())
            entries.append(SnapshotEntry(target=target, existed=True, backup=backup))
        else:
            entries.append(SnapshotEntry(target=target, existed=False, backup=None))
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
            failures.append(entry.target.as_posix())
    return failures
```

Do not include`raw/{doc_id}.meta.yaml`in`artifact_paths()`。

- [ ] **Step 4: Run snapshot tests and all compile-job tests**

```powershell
python -m pytest tests/test_compile_jobs.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add api/compile_jobs.py tests/test_compile_jobs.py
git commit -m "feat: snapshot and restore compile artifacts"
```

---

### Task 4: Unified Background Compile Runner and Global Serialization

**Files:**

- Modify: `api/compile_jobs.py`
- Modify: `tests/test_compile_jobs.py`

**Interfaces:**

- Consumes: Task 1 metadata helpers and Task 3 snapshot interfaces.
- Produces: `run_compile_task(doc_id: str, base_dir: Path | None = None) -> None`
- Produces: `COMPILE_EXECUTION_LOCK`
- Consumer: Task 5.

- [ ] **Step 1: Write failing success, failure, rollback and lock tests**

Add tests using a fake repository and patched`subprocess.run`。The fake subprocess must explicitly write the terminal metadata that the real child process would write:

```python
import subprocess
import threading
import time
from unittest.mock import patch

import yaml

from api.compile_jobs import run_compile_task
from scripts.doc_admin import read_doc_meta, write_doc_compile_result


def _write_meta(base, doc_id, status="compiling"):
    path = base / "raw" / f"{doc_id}.meta.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"id": doc_id, "status": status}),
        encoding="utf-8",
    )


def _write_compile_script(base):
    path = base / "scripts" / "compile.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# test sentinel\n", encoding="utf-8")


def test_run_compile_task_accepts_only_compiled_terminal_state(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_020"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    def fake_run(*_args, **_kwargs):
        write_doc_compile_result(doc_id, "compiled", base_dir=base)
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    assert read_doc_meta(doc_id, base)["status"] == "compiled"


def test_run_compile_task_rolls_back_when_returncode_zero_without_compiled_state(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_021"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    index = base / "wiki" / "index.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index")

    def fake_run(*_args, **_kwargs):
        index.write_bytes(b"partial-index")
        return subprocess.CompletedProcess([], 0, stdout="done", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert index.read_bytes() == b"old-index"


def test_run_compile_task_records_configuration_failure_before_compile_doc(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_022"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    result = subprocess.CompletedProcess(
        [], 1, stdout="", stderr="RuntimeError: 未找到 OPENAI_API_KEY=sk-secretvalue"
    )
    with patch("api.compile_jobs.subprocess.run", return_value=result):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "llm_configuration"
    assert "sk-secretvalue" not in meta["error_message"]
    assert len(meta["error_message"]) <= 500


def test_run_compile_task_marks_rollback_failed(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_023"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    with patch("api.compile_jobs.subprocess.run", side_effect=OSError("spawn failed")), patch(
        "api.compile_jobs.restore_artifact_snapshot",
        return_value=["wiki/index.yaml"],
    ):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "rollback_failed"
    assert "wiki/index.yaml" in meta["error_message"]


def test_run_compile_task_serializes_different_documents(tmp_path):
    base = tmp_path / "repo"
    docs = ["doc_20260805_024", "doc_20260805_025"]
    for doc_id in docs:
        _write_meta(base, doc_id)
    _write_compile_script(base)
    state_lock = threading.Lock()
    active = 0
    peak = 0

    def fake_run(args, **_kwargs):
        nonlocal active, peak
        doc_id = args[-1]
        with state_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        write_doc_compile_result(doc_id, "compiled", base_dir=base)
        with state_lock:
            active -= 1
        return subprocess.CompletedProcess([], 0, stdout="ok", stderr="")

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        threads = [threading.Thread(target=run_compile_task, args=(doc_id, base)) for doc_id in docs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

    assert peak == 1
    assert all(read_doc_meta(doc_id, base)["status"] == "compiled" for doc_id in docs)
```

Also add a missing-script test and assert it becomes`error`instead of silently returning.

- [ ] **Step 2: Verify red state**

```powershell
python -m pytest tests/test_compile_jobs.py -k "run_compile_task" -v
```

Expected: failures because the runner and lock do not exist.

- [ ] **Step 3: Implement the unified runner**

Add imports and the lock:

```python
import subprocess
import sys
import threading
from tempfile import TemporaryDirectory

from scripts.doc_admin import read_doc_meta, write_doc_compile_result

COMPILE_EXECUTION_LOCK = threading.Lock()
```

Implement a diagnostic helper:

```python
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
```

Implement`run_compile_task()`with this exact decision order:

```python
def run_compile_task(doc_id: str, base_dir: Path | None = None) -> None:
    base = Path(base_dir) if base_dir is not None else Path(__file__).resolve().parent.parent
    with COMPILE_EXECUTION_LOCK:
        with TemporaryDirectory(prefix=f"port-kb-{doc_id}-") as temp_name:
            snapshot = create_artifact_snapshot(base, doc_id, Path(temp_name))
            failure_code: CompileErrorCode | None = None
            failure_message = ""
            try:
                compile_script = base / "scripts" / "compile.py"
                if not compile_script.exists():
                    raise FileNotFoundError(f"compile script missing: {compile_script}")
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
                        raise RuntimeError("compiled terminal state could not be persisted")
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
            write_doc_compile_result(
                doc_id,
                "error",
                error_code=failure_code or "compile_failed",
                error_message=failure_message or "编译失败",
                base_dir=base,
            )
```

Required implementation notes:

- Import`os`because the child environment is inherited.
- Do not add`timeout=`to`subprocess.run()`in E004；hard watchdog is a declared non-goal.
- A child return code of0is not sufficient；final metadata must be`compiled`。
- Read the child-written raw`error_message`only for classification, then replace it with the sanitized wrapper result.
- Never print or persist the full captured streams.
- If final terminal metadata cannot be written because metadata disappeared, do not invent success；allow the function to return after the failed write and let the test expose the contract gap. If this path appears during implementation, add a deterministic test and report it before broadening scope.

- [ ] **Step 4: Run compile-job and document-state suites**

```powershell
python -m pytest tests/test_compile_jobs.py tests/test_doc_admin.py -v
```

Expected: all tests pass and no real subprocess or network call occurs.

- [ ] **Step 5: Commit Task 4**

```powershell
git add api/compile_jobs.py tests/test_compile_jobs.py
git commit -m "feat: close background compile task outcomes"
```

---

### Task 5: FastAPI Scheduling Contract and 409 Guard

**Files:**

- Modify: `api/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**

- Consumes: `prepare_doc_compile()` and `run_compile_task()`.
- Produces: `_schedule_compile(background_tasks: BackgroundTasks, doc_id: str) -> None`
- Produces: HTTP`409 {"detail":{"code":"compile_in_progress"}}`.
- Produces: catalog projection of`error_code`.
- Consumer: frontend Task 6.

- [ ] **Step 1: Replace shallow endpoint tests with failing contract tests**

Update the autouse fixture to patch any new module-level paths only when needed. Add tests:

```python
@patch("api.main.run_compile_task")
@patch("api.main.ingest_file")
def test_upload_marks_compiling_before_background_task(
    mock_ingest, mock_run, isolate_api_originals
):
    import api.main as api_mod

    doc_id = "doc_20260805_030"

    def fake_ingest(_stored):
        api_mod.RAW_DIR.mkdir(parents=True, exist_ok=True)
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").write_text(
            yaml.safe_dump({"id": doc_id, "status": "raw"}),
            encoding="utf-8",
        )
        return {"id": doc_id, "status": "raw"}

    mock_ingest.side_effect = fake_ingest
    response = client.post(
        "/api/v1/upload",
        files={"file": ("task.md", b"# task", "text/markdown")},
    )

    assert response.status_code == 200
    meta = yaml.safe_load(
        (api_mod.RAW_DIR / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
    mock_run.assert_called_once_with(doc_id, api_mod.BASE_DIR)


def test_recompile_returns_409_when_document_is_compiling(tmp_path, monkeypatch):
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_031"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiling"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    response = client.post(f"/api/v1/docs/{doc_id}/recompile")

    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "compile_in_progress"}}


def test_catalog_projects_error_code(tmp_path, monkeypatch):
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_032"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({
            "id": doc_id,
            "status": "error",
            "error_code": "timeout",
            "error_message": "sanitized backend detail",
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)
    monkeypatch.setattr(api_mod, "INDEX_FILE", tmp_path / "missing-index.yaml")

    response = client.get("/api/v1/wiki/index")

    assert response.status_code == 200
    assert response.json()["documents"][0]["error_code"] == "timeout"
```

Delete the old test that only patched`scripts.compile.compile_doc`and asserted`recompiling`without observing the scheduled task.

- [ ] **Step 2: Verify red state**

```powershell
python -m pytest tests/test_api.py -k "upload_marks_compiling or recompile_returns_409 or catalog_projects_error_code" -v
```

Expected: failures because the old paths do not prepare state, do not return409, and do not project`error_code`。

- [ ] **Step 3: Implement one scheduling path**

In`api/main.py`:

```python
import threading

from api.compile_jobs import run_compile_task
from scripts.doc_admin import prepare_doc_compile

COMPILE_SCHEDULE_LOCK = threading.Lock()
```

Add:

```python
def _schedule_compile(background_tasks: BackgroundTasks, doc_id: str) -> None:
    with COMPILE_SCHEDULE_LOCK:
        result = prepare_doc_compile(doc_id, base_dir=BASE_DIR)
        if result.get("prepared"):
            background_tasks.add_task(run_compile_task, doc_id, BASE_DIR)
            return
        if result.get("reason") == "compile_in_progress":
            raise HTTPException(
                status_code=409,
                detail={"code": "compile_in_progress"},
            )
        raise HTTPException(status_code=404, detail="Document metadata not found")
```

Then:

- Replace`background_tasks.add_task(compile_ingested_task, doc_id)`inside`_accept_upload()`with`_schedule_compile(background_tasks, doc_id)`。
- Replace the nested recompile`_compile_task()`with`_schedule_compile(background_tasks, doc_id)`。
- Remove the old independent`compile_ingested_task()`implementation；do not keep a second subprocess wrapper or an alias that can diverge.
- Add`"error_code"`to`_load_document_catalog()`metadata projection keys.
- Keep successful upload and recompile response shapes unchanged.
- Keep duplicate upload behavior unchanged and do not schedule it.

The schedule lock must cover read/check, atomic`compiling`write, and`BackgroundTasks.add_task()`as one short critical section.

- [ ] **Step 4: Run API and backend target suites**

```powershell
python -m pytest tests/test_api.py tests/test_doc_admin.py tests/test_compile_jobs.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 5**

```powershell
git add api/main.py tests/test_api.py
git commit -m "feat: unify API compile scheduling"
```

---

### Task 6: Structured Frontend API Errors

**Files:**

- Modify: `frontend/src/lib/api.ts`
- Create: `frontend/tests/unit/api-errors.test.ts`

**Interfaces:**

- Consumes: backend`detail.code`contract from Task 5.
- Produces: `CompileErrorCode`
- Produces: `ApiError`
- Produces: `getCompileErrorMessage(code?: string) -> string`
- Produces: `getUserFacingErrorMessage(error: unknown, fallback: string) -> string`
- Consumers: Tasks 7, 8 and 9.

- [ ] **Step 1: Write failing API error tests**

Create`frontend/tests/unit/api-errors.test.ts`:

```typescript
import {
    ApiError,
    getCompileErrorMessage,
    getUserFacingErrorMessage,
    recompileDoc,
} from '@/lib/api';

describe('structured API errors', () => {
    afterEach(() => {
        jest.restoreAllMocks();
    });

    test('parses compile_in_progress without exposing 409 or Conflict', async () => {
        jest.spyOn(global, 'fetch').mockResolvedValue(new Response(
            JSON.stringify({ detail: { code: 'compile_in_progress' } }),
            {
                status: 409,
                statusText: 'Conflict',
                headers: { 'Content-Type': 'application/json' },
            },
        ));

        await expect(recompileDoc('doc_1')).rejects.toMatchObject({
            name: 'ApiError',
            status: 409,
            code: 'compile_in_progress',
            message: '该文档正在编译，请稍后再试',
        });
    });

    test.each([
        ['llm_configuration', '模型服务暂不可用，请联系管理员检查配置'],
        ['service_unavailable', '编译服务暂不可用，请稍后重试'],
        ['timeout', '编译服务响应超时，请稍后重试'],
        ['document_processing', '文档编译未完成，请检查文件内容后重试'],
        ['compile_failed', '编译失败，请稍后重试或联系管理员'],
        ['rollback_failed', '编译失败，旧版本恢复异常，请联系管理员'],
    ])('maps %s to fixed user copy', (code, expected) => {
        expect(getCompileErrorMessage(code)).toBe(expected);
    });

    test('does not expose an arbitrary Error.message', () => {
        const error = new Error('RuntimeError OPENAI_API_KEY=sk-secret API error 500');
        expect(getUserFacingErrorMessage(error, '上传失败，请稍后重试')).toBe(
            '上传失败，请稍后重试',
        );
    });

    test('maps network TypeError to a stable network message', () => {
        expect(getUserFacingErrorMessage(new TypeError('Failed to fetch'), 'fallback')).toBe(
            '网络连接异常，请检查连接后重试',
        );
    });
});
```

- [ ] **Step 2: Verify red state**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/api-errors.test.ts
Pop-Location
```

Expected: compile/import failures because the exported types and helpers do not exist.

- [ ] **Step 3: Implement structured response handling**

In`frontend/src/lib/api.ts`add:

```typescript
export type CompileErrorCode =
    | 'compile_in_progress'
    | 'llm_configuration'
    | 'service_unavailable'
    | 'timeout'
    | 'document_processing'
    | 'compile_failed'
    | 'rollback_failed';

const COMPILE_ERROR_MESSAGES: Record<CompileErrorCode, string> = {
    compile_in_progress: '该文档正在编译，请稍后再试',
    llm_configuration: '模型服务暂不可用，请联系管理员检查配置',
    service_unavailable: '编译服务暂不可用，请稍后重试',
    timeout: '编译服务响应超时，请稍后重试',
    document_processing: '文档编译未完成，请检查文件内容后重试',
    compile_failed: '编译失败，请稍后重试或联系管理员',
    rollback_failed: '编译失败，旧版本恢复异常，请联系管理员',
};

export function getCompileErrorMessage(code?: string): string {
    return COMPILE_ERROR_MESSAGES[code as CompileErrorCode]
        ?? COMPILE_ERROR_MESSAGES.compile_failed;
}

export class ApiError extends Error {
    constructor(
        message: string,
        public readonly status: number,
        public readonly code?: string,
    ) {
        super(message);
        this.name = 'ApiError';
    }
}

function statusMessage(status: number): string {
    if (status === 404) return '未找到对应文档';
    if (status === 422) return '文件无法处理，请检查格式和内容后重试';
    if (status >= 500) return '服务暂不可用，请稍后重试';
    return '请求未完成，请稍后重试';
}

export function getUserFacingErrorMessage(error: unknown, fallback: string): string {
    if (error instanceof ApiError) return error.message;
    if (error instanceof TypeError) return '网络连接异常，请检查连接后重试';
    return fallback;
}
```

Extend`DocMeta`with only the stable machine field:

```typescript
error_code?: CompileErrorCode;
```

Do not add`error_message`to`DocMeta`。

Replace`handleResponse()`with a single-consumption JSON parser:

```typescript
async function handleResponse<T>(res: Response): Promise<T> {
    const text = await res.text();
    let payload: unknown;
    if (text) {
        try {
            payload = JSON.parse(text);
        } catch {
            payload = undefined;
        }
    }
    if (!res.ok) {
        const detail = payload && typeof payload === 'object'
            ? (payload as { detail?: unknown }).detail
            : undefined;
        const code = detail && typeof detail === 'object'
            ? (detail as { code?: unknown }).code
            : undefined;
        const stableCode = typeof code === 'string' ? code : undefined;
        const message = stableCode
            ? getCompileErrorMessage(stableCode)
            : statusMessage(res.status);
        throw new ApiError(message, res.status, stableCode);
    }
    return payload as T;
}
```

Do not include`res.statusText`or backend raw`detail`string in the thrown message.

- [ ] **Step 4: Run API error tests**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/api-errors.test.ts
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 6**

```powershell
git add frontend/src/lib/api.ts frontend/tests/unit/api-errors.test.ts
git commit -m "feat: map API failures to safe user messages"
```

---

### Task 7: WikiCard Friendly Failure Detail

**Files:**

- Modify: `frontend/src/components/WikiCard.tsx`
- Modify: `frontend/tests/unit/WikiCard.actions.test.tsx`

**Interfaces:**

- Consumes: `DocMeta.error_code` and`getCompileErrorMessage()`from Task 6.
- Produces: `data-testid="compile-error-message"`line for error documents.

- [ ] **Step 1: Write failing card tests**

Append:

```typescript
test('error status renders fixed friendly reason', () => {
    render(
        <WikiCard
            doc={{ ...baseDoc, status: 'error', error_code: 'llm_configuration' }}
            onRecompile={jest.fn()}
        />,
    );
    expect(screen.getByTestId('compile-error-message')).toHaveTextContent(
        '模型服务暂不可用，请联系管理员检查配置',
    );
});

test('error card never renders backend technical detail', () => {
    const doc = {
        ...baseDoc,
        status: 'error' as const,
        error_code: 'compile_failed' as const,
        error_message: 'RuntimeError OPENAI_API_KEY=sk-secret API error 500',
    } as typeof baseDoc & { error_message: string };
    render(<WikiCard doc={doc} onRecompile={jest.fn()} />);
    expect(screen.queryByText(/OPENAI_API_KEY|sk-secret|API error 500/)).toBeNull();
});
```

- [ ] **Step 2: Verify red state**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/WikiCard.actions.test.tsx
Pop-Location
```

Expected: missing`compile-error-message`failure.

- [ ] **Step 3: Add the fixed line without changing badge behavior**

Import`getCompileErrorMessage`and render below the abstract:

```tsx
{isError && (
    <p
        data-testid="compile-error-message"
        role="status"
        className="rounded-md bg-danger-soft px-2.5 py-2 text-[12px] leading-5 text-danger-ink"
    >
        {getCompileErrorMessage(doc.error_code)}
    </p>
)}
```

Keep the existing error badge and recompile button. Do not add a disclosure, tooltip or raw detail field.

- [ ] **Step 4: Run card tests**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/WikiCard.actions.test.tsx
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 7**

```powershell
git add frontend/src/components/WikiCard.tsx frontend/tests/unit/WikiCard.actions.test.tsx
git commit -m "feat: show safe compile failure reasons"
```

---

### Task 8: Three-Second Polling and One-Time Transition Toast

**Files:**

- Modify: `frontend/src/app/wiki/page.tsx`
- Modify: `frontend/tests/unit/wiki-polling.test.tsx`

**Interfaces:**

- Consumes: Task 6 error helpers and Task 7 card rendering.
- Produces: no-overlap 3000ms conditional polling.
- Produces: one Toast per failure round.

- [ ] **Step 1: Expand polling tests with fake timers**

Replace the shallow polling tests with controlled sequences. Mock`useToast()`and API functions:

```typescript
import { act, render, screen, waitFor } from '@testing-library/react';
import WikiPage from '@/app/wiki/page';
import { fetchWikiIndex, recompileDoc } from '@/lib/api';

const mockToastPush = jest.fn();

jest.mock('@/components/ui/Toast', () => ({
    useToast: () => ({ push: mockToastPush }),
}));

jest.mock('@/lib/api', () => ({
    fetchWikiIndex: jest.fn(),
    deleteDoc: jest.fn(),
    recompileDoc: jest.fn(),
    getUserFacingErrorMessage: jest.requireActual('@/lib/api').getUserFacingErrorMessage,
}));

const mockedFetch = fetchWikiIndex as jest.MockedFunction<typeof fetchWikiIndex>;
const mockedRecompile = recompileDoc as jest.MockedFunction<typeof recompileDoc>;
```

Add tests for:

```typescript
test('polls after 3000ms while a document is compiling', async () => {
    jest.useFakeTimers();
    mockedFetch
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'compiling' }],
        })
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'compiled' }],
        });

    render(<WikiPage />);
    await screen.findByTestId('compiling-hint');
    expect(mockedFetch).toHaveBeenCalledTimes(1);

    await act(async () => {
        jest.advanceTimersByTime(3000);
        await Promise.resolve();
    });

    await waitFor(() => expect(mockedFetch).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('compiling-hint')).toBeNull();
    jest.useRealTimers();
});

test('initial historical error shows no toast', async () => {
    mockedFetch.mockResolvedValue({
        total_docs: 1,
        documents: [{ id: 'doc_1', status: 'error', error_code: 'timeout' }],
    });
    render(<WikiPage />);
    await screen.findByTestId('compile-error-message');
    expect(mockToastPush).not.toHaveBeenCalled();
});

test('compiling to error emits one friendly toast only', async () => {
    jest.useFakeTimers();
    mockedFetch
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'compiling' }],
        })
        .mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'error', error_code: 'timeout' }],
        });
    render(<WikiPage />);
    await screen.findByTestId('compiling-hint');

    await act(async () => {
        jest.advanceTimersByTime(3000);
        await Promise.resolve();
    });
    await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));
    expect(mockToastPush).toHaveBeenCalledWith(
        '编译服务响应超时，请稍后重试',
        'error',
    );

    await act(async () => {
        jest.advanceTimersByTime(6000);
        await Promise.resolve();
    });
    expect(mockToastPush).toHaveBeenCalledTimes(1);
    jest.useRealTimers();
});
```

Add a fourth test that clicks recompile, observes local`compiling`, then returns`error`again and expects a second Toast for the new round. Add a fifth test where`recompileDoc()`rejects with`ApiError(..., 409, 'compile_in_progress')`and assert the Toast is exactly“该文档正在编译，请稍后再试”with no“409”or“Conflict”。

- [ ] **Step 2: Verify red state**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/wiki-polling.test.tsx
Pop-Location
```

Expected: interval, Toast and safe-error assertions fail against the old page.

- [ ] **Step 3: Implement stable snapshot application and polling**

In`WikiPage`add refs:

```typescript
const statusByIdRef = useRef(new Map<string, DocMeta['status']>());
const initializedRef = useRef(false);
const pollInFlightRef = useRef(false);
```

Create a single snapshot application callback:

```typescript
const applySnapshot = useCallback((data: WikiIndexData, notify: boolean) => {
    if (notify && initializedRef.current) {
        for (const doc of data.documents) {
            const previous = statusByIdRef.current.get(doc.id);
            if ((previous === 'raw' || previous === 'compiling') && doc.status === 'error') {
                toast.push(getCompileErrorMessage(doc.error_code), 'error');
            }
        }
    }
    statusByIdRef.current = new Map(data.documents.map(doc => [doc.id, doc.status]));
    initializedRef.current = true;
    setDocs(data.documents);
    setTotal(data.total_docs);
}, [toast]);
```

Import`WikiIndexData`and error helpers from`@/lib/api`。Create one refresh callback that never exposes raw errors:

```typescript
const refreshDocs = useCallback(async (notify: boolean) => {
    const data = await fetchWikiIndex();
    applySnapshot(data, notify);
}, [applySnapshot]);
```

Initial load calls`refreshDocs(false)`and on failure sets a fixed load message via`getUserFacingErrorMessage(error, '知识库加载失败，请稍后重试')`。

Replace the 10-second interval with:

```typescript
useEffect(() => {
    if (!hasPending) return;
    const timer = window.setInterval(async () => {
        if (pollInFlightRef.current) return;
        pollInFlightRef.current = true;
        try {
            await refreshDocs(true);
        } catch {
            // A transient polling failure does not replace the last usable catalog.
        } finally {
            pollInFlightRef.current = false;
        }
    }, 3000);
    return () => window.clearInterval(timer);
}, [hasPending, refreshDocs]);
```

Update visible copy to“有文档编译中，每3秒自动刷新…”。

On successful recompile, update both React state and`statusByIdRef`to`compiling`before the next poll:

```typescript
statusByIdRef.current.set(id, 'compiling');
setDocs(current => current.map(doc => (
    doc.id === id
        ? { ...doc, status: 'compiling', error_code: undefined }
        : doc
)));
```

On delete and recompile catches, use`getUserFacingErrorMessage()`with fixed fallbacks. Never interpolate`e.message`。

Avoid side effects inside a`setDocs()`state updater；Toast transitions must be calculated before setting state to remain stable under React Strict Mode.

- [ ] **Step 4: Run polling and related component tests**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/wiki-polling.test.tsx tests/unit/WikiCard.actions.test.tsx tests/unit/api-errors.test.ts
Pop-Location
```

Expected: all tests pass with no`act()`warning or`console.error`。

- [ ] **Step 5: Commit Task 8**

```powershell
git add frontend/src/app/wiki/page.tsx frontend/tests/unit/wiki-polling.test.tsx
git commit -m "feat: surface background compile outcomes"
```

---

### Task 9: Safe Upload Failure Copy

**Files:**

- Modify: `frontend/src/components/UploadZone.tsx`
- Modify: `frontend/tests/unit/UploadZone.test.tsx`

**Interfaces:**

- Consumes: `getUserFacingErrorMessage()`from Task 6.
- Produces: fixed upload error text; no backend technical detail.

- [ ] **Step 1: Write a failing non-disclosure test**

Append:

```typescript
test('shows a friendly upload failure without raw technical detail', async () => {
    const onUpload = jest.fn().mockRejectedValue(
        new Error('RuntimeError OPENAI_API_KEY=sk-secret API error 500'),
    );
    render(<UploadZone onUpload={onUpload} />);
    const file = new File(['# Test'], 'failed.md', { type: 'text/markdown' });

    fireEvent.drop(screen.getByTestId('dropzone'), {
        dataTransfer: { files: [file] },
    });

    expect(await screen.findByText('上传失败，请稍后重试')).toBeInTheDocument();
    expect(screen.queryByText(/OPENAI_API_KEY|sk-secret|API error 500/)).toBeNull();
});
```

- [ ] **Step 2: Verify red state**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/UploadZone.test.tsx
Pop-Location
```

Expected: current component renders the raw`Error.message`and fails the test.

- [ ] **Step 3: Use the shared safe mapper**

Import`getUserFacingErrorMessage`and replace the catch assignment:

```typescript
} catch (error) {
    const message = getUserFacingErrorMessage(
        error,
        '上传失败，请稍后重试',
    );
    setItems(previous => previous.map(item => (
        item.file === file
            ? { ...item, state: 'error', error: message }
            : item
    )));
}
```

Keep local invalid-extension validation unchanged because it is already controlled user copy.

- [ ] **Step 4: Run UploadZone and API error tests**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/UploadZone.test.tsx tests/unit/api-errors.test.ts
Pop-Location
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 9**

```powershell
git add frontend/src/components/UploadZone.tsx frontend/tests/unit/UploadZone.test.tsx
git commit -m "fix: hide technical upload errors"
```

---

### Task 10: Documentation, Full Verification, Scope Audit and Review Handoff

**Files:**

- Modify: `CLAUDE.md`
- Verify only: all E004 code and tests.

**Interfaces:**

- Consumes: all prior tasks.
- Produces: truthful risk register and fresh completion evidence.

- [ ] **Step 1: Update the known-risk entry without overstating scope**

Replace risk 9 with wording equivalent to:

```markdown
9. ~~后台编译任务缺少可靠状态和错误记录~~（E004已处理API来源编译闭环：上传和重编译统一调度，接受任务前写`compiling`，当前单API进程内全局串行，失败时回滚摘要、索引、本体和关系产物，元数据保存脱敏`error_code/error_message`，前端3秒轮询并仅显示友好文案；仍不覆盖多API进程、外部CLI并发、API强制终止恢复、后台硬超时和全部共享YAML通用原子化，风险6仍保留）；
```

Update the CLAUDE version/date consistently. Do not mark risk 6 resolved.

- [ ] **Step 2: Run the backend target suite**

```powershell
python -m pytest tests/test_doc_admin.py tests/test_compile_jobs.py tests/test_api.py -q
```

Expected: exit 0 with no real network or child compiler process.

- [ ] **Step 3: Run the full offline backend gate**

```powershell
$env:OPENAI_API_KEY = ""
$env:EMBEDDING_API_KEY = ""
$env:HTTP_PROXY = "http://127.0.0.1:9"
$env:HTTPS_PROXY = "http://127.0.0.1:9"
python -m pytest tests/ -q
```

Expected: exit 0. Record exact passed, failed, skipped and warning counts from the fresh output.

- [ ] **Step 4: Run frontend target tests**

```powershell
Push-Location frontend
npm test -- --runInBand tests/unit/api-errors.test.ts tests/unit/WikiCard.actions.test.tsx tests/unit/wiki-polling.test.tsx tests/unit/UploadZone.test.tsx
Pop-Location
```

Expected: exit 0.

- [ ] **Step 5: Run full Jest, touched-file ESLint and production build**

```powershell
Push-Location frontend
npm test -- --runInBand
npm exec eslint -- src/lib/api.ts src/app/wiki/page.tsx src/components/WikiCard.tsx src/components/UploadZone.tsx tests/unit/api-errors.test.ts tests/unit/WikiCard.actions.test.tsx tests/unit/wiki-polling.test.tsx tests/unit/UploadZone.test.tsx
npm run build
Pop-Location
```

Expected: all three commands exit 0；no`act()`warning、`console.error`or TypeScript build failure.

- [ ] **Step 6: Compare the immutable data manifest**

```powershell
$AfterManifest = Join-Path $env:TEMP "e004-data-after.json"
@'
import hashlib, json, sys
from pathlib import Path
root = Path.cwd()
rows = []
for folder in ("originals", "raw", "wiki", "meta"):
    base = root / folder
    if not base.exists():
        continue
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        stat = path.stat()
        rows.append({
            "path": path.relative_to(root).as_posix(),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
Path(sys.argv[1]).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(rows))
'@ | python - $AfterManifest
$BeforeHash = (Get-FileHash $BeforeManifest -Algorithm SHA256).Hash
$AfterHash = (Get-FileHash $AfterManifest -Algorithm SHA256).Hash
if ($BeforeHash -ne $AfterHash) { throw "E004 real-data manifest changed" }
```

Expected: before and after manifest hashes are identical.

- [ ] **Step 7: Run repository and scope checks**

```powershell
git status --short
git diff --check origin/dev...HEAD
git diff --stat origin/dev...HEAD
git diff --name-only origin/dev...HEAD
git log --oneline origin/dev..HEAD
```

Expected:

- only files listed in this plan changed；
- no generated data, lockfile, environment file or unrelated source changed；
- no whitespace errors；
- worktree clean after final documentation commit.

- [ ] **Step 8: Commit documentation after all evidence is fresh**

```powershell
git add CLAUDE.md
git commit -m "docs: record E004 compile task closure"
```

Then rerun:

```powershell
git status --short
git diff --check origin/dev...HEAD
```

Expected: clean worktree and no diff-check errors.

- [ ] **Step 9: Prepare the implementation report and stop before remote writes**

Report:

- starting and ending HEAD；
- all changed files and purpose；
- root cause；
- each commit；
- exact commands, exit codes and counts；
- data-manifest hash equality；
- known remaining boundaries；
- proposed PR title and body；
- whether Codex GitHub Review and multi-Agent review are recommended.

Do not push, create a PR, trigger review or merge until the user explicitly authorizes those Git operations.

---

## Review Gates After User Authorizes Push and PR

These are later governance steps, not part of the initial implementation authorization:

1. Push only`fix/e004-compile-status-rollback`。
2. Create one PR targeting`dev`。
3. Wait for`repository-integrity`、`python-core`、`frontend-unit-build`required checks。
4. Trigger`@codex review`against the current Head。
5. Independently review correctness, security, test isolation, rollback completeness, frontend non-disclosure and scope consistency。
6. Treat P0/P1 and verified blocker-class P2 findings as merge blockers。
7. If`dev`advances, merge`origin/dev`into the task branch；do not rebase。Rerun all gates and re-review the new Head。
8. Merge only with merge commit and expected Head guard after explicit user authorization。

## Completion Definition

E004 is not complete until all of the following are proven on the same final Head:

- upload and recompile use one scheduler and one background runner；
- accepted tasks are already`compiling`when the response returns；
- duplicate recompile is`409 compile_in_progress`without a second task；
- success requires child return code0and final metadata`compiled`；
- every detected failure ends in`error`with sanitized, bounded detail；
- first-compile failure leaves no search/index half-product；
- recompile failure restores old artifacts byte-for-byte；
- rollback failure is distinguishable as`rollback_failed`；
- API-origin jobs are globally serial within the current process；
- frontend shows only fixed friendly copy and never technical details；
- polling occurs every3000ms only while pending；
- one failure round produces one Toast and a retry permits a new Toast；
- backend target tests, full offline pytest, frontend target tests, full Jest, touched ESLint and production build all pass；
- real-data before/after manifests are identical；
- CI required checks and independent current-Head review pass；
- remaining cross-process and crash-recovery limits are explicitly reported rather than hidden。
