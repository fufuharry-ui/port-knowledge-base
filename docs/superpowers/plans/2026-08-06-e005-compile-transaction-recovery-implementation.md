# E005 Compile Transaction Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 API 来源编译升级为单实例、单活动事务、可持久恢复、具备30分钟硬超时和两阶段上传发布的严格一致性执行链路。

**Architecture:** 新增耐久文件、运行门禁、事务存储和进程树四个小模块；`api/compile_jobs.py`只负责编译事务编排，`api/main.py`只负责 lifespan、HTTP 合同和锁顺序。上传先在 `.runtime/upload-intake` 中解析并生成候选内容，编译事务进入 `PREPARED` 后才发布 `originals/raw`。启动恢复和离线 CLI 共用同一事务恢复库。

**Tech Stack:** Python 3.11/3.12、FastAPI lifespan、Starlette BackgroundTasks、PyYAML、portalocker、psutil、pytest、Next.js 16.2.2、React 19.2.4、TypeScript、Jest 30、Testing Library、PowerShell 7、Git worktree。

**Suggested Effort:** High

**Multi-Agent:** 建议使用多个独立Agent按任务串行实施；每个Task由新的实施Agent完成，再进行规格符合性审查和代码质量审查。禁止两个Agent并行修改同一worktree、Manifest模型、`api/main.py`或共享测试夹具。

**Design Spec:** `docs/superpowers/specs/2026-08-06-e005-compile-transaction-recovery-design.md`

## Global Constraints

- `target_backend: api`；不得修改或迁移`app/`后端。
- 实施分支使用`fix/e005-compile-transaction-recovery`，从最新`origin/docs/e005-compile-transaction-recovery-design`创建，使规格和计划进入最终PR。
- PR目标分支只能是`dev`。
- E005只支持单API实例和单全局活动事务；不得增加多worker兼容、Redis、数据库、Celery或分布式锁。
- Manifest原子进入`COMMITTED`是唯一成功提交点；提交点前崩溃一律回滚。
- `SCHEDULED`表示已承诺执行且不可重新排队；启动恢复不得重新调用`add_task`。
- 默认`COMPILE_TIMEOUT_SECONDS=1800`；允许范围60–86400秒。
- 默认`COMPILE_TERMINATION_GRACE_SECONDS=5`；允许范围1–60秒。
- 超时或恢复时必须确认根进程和全部后代退出后才能回滚。
- PID身份至少验证`pid + create_time + executable + cwd + command_fingerprint`；身份不匹配时不得终止。
- 事务目录必须使用耐久原子写；生产部署必须将`.runtime`放在持久卷。
- 同时最多一个`PREPARED/SCHEDULED/RUNNING/ROLLBACKING`事务。
- 上传必须先在intake staging解析并生成候选内容；`PREPARED`发布后才能写入`originals/raw`。
- API来源上传失败或中断不得留下孤立doc、孤立`compiling`或半成品共享产物。
- 重编译失败恢复旧产物并写文档`error`；新上传失败撤销本轮`original/raw/meta`，不保留孤儿错误文档。
- `interrupted`是文档终态错误；`compile_transaction_unavailable`和`recovery_required`只用于请求错误。
- 公共API不得暴露job、PID、进程命令、绝对路径、快照、原始stderr/stdout或`error_message`。
- `/health`是liveness，始终200；`/ready`是readiness，门禁时503。
- 不修改`scripts/compile.py`和`scripts/relate.py`的核心业务语义；事务能力位于外层包装器。
- 所有破坏性测试只使用`tmp_path`临时仓库；不得对真实知识库注入崩溃、杀进程或回滚。
- 所有后端测试显式清空模型Key并阻断网络；不得调用真实LLM或Embedding。
- 真实`originals/`、`raw/`、`wiki/`、`meta/`在任务前后path、size、SHA-256和`mtime_ns`必须一致。
- PowerShell续行仅使用反引号，不使用Unix反斜杠。
- 禁止force、rebase、reset、clean、amend、`--admin`、直接推送`dev`和删除来源不明文件。
- 每个代码Task执行红灯、绿灯、目标回归并独立commit。
- 未获得用户新的Git授权前，计划中的push、PR和merge只作为待执行步骤。

---

## File Map

### Create

- `api/durable_fs.py`：耐久字节/YAML写入、哈希、目录探针和原子目录发布。
- `api/runtime_guard.py`：配置解析、API实例锁、`ready/recovery_required`状态。
- `api/compile_transactions.py`：Manifest模型、七项快照、状态迁移、终态验证和恢复协调。
- `api/process_tree.py`：跨平台编译进程启动、身份验证、等待和完整进程树终止。
- `api/upload_intake.py`：上传intake staging、候选摄入、业务目录发布与撤销。
- `scripts/compile_recovery.py`：`inspect/verify/recover/cleanup-terminal`离线CLI。
- `tests/test_durable_fs.py`
- `tests/test_runtime_guard.py`
- `tests/test_compile_transactions.py`
- `tests/test_process_tree.py`
- `tests/test_upload_intake.py`
- `tests/test_compile_recovery.py`
- `tests/test_e005_crash_recovery.py`

### Modify

- `requirements.txt`：增加`portalocker`和`psutil`直接运行依赖。
- `.gitignore`：忽略`.runtime/`。
- `.env.example`：记录事务目录、硬超时、终止宽限期和持久卷要求。
- `scripts/ingest.py`：增加纯`prepare_ingest()`边界，现有CLI继续通过发布函数保持兼容。
- `tests/test_ingest.py`（不存在时创建）：覆盖纯准备和CLI兼容发布。
- `scripts/doc_admin.py`：活动job字段原子绑定、终态清理和错误终态写入。
- `tests/test_doc_admin.py`：覆盖job绑定和活动字段清除。
- `api/compile_jobs.py`：从临时快照/`subprocess.run`迁移到持久事务/`Popen`执行。
- `tests/test_compile_jobs.py`：编译事务执行、提交、超时和失败回滚。
- `api/main.py`：lifespan、门禁、ready、事务化调度、两阶段上传和删除互斥。
- `tests/test_api.py`：409/503、health/ready、公共投影和上传合同。
- `frontend/src/lib/api.ts`：拆分文档终态错误与请求错误，增加固定文案。
- `frontend/src/app/wiki/page.tsx`：复用现有轮询迁移，支持`interrupted`和503友好提示。
- `frontend/tests/unit/api-errors.test.ts`
- `frontend/tests/unit/wiki-polling.test.tsx`
- `frontend/tests/unit/UploadZone.test.tsx`
- `CLAUDE.md`：更新风险9边界与单实例部署合同。
- `docs/dev/tasks/E005-compile-transaction-recovery.md`：任务证据和完成记录。

### Do Not Modify

- `app/`
- `scripts/compile.py`
- `scripts/relate.py`
- 真实`originals/`、`raw/`、`wiki/`、`meta/`内容
- required check名称

---

## Execution Preflight

- [ ] **Step 1: Fresh-fetch and verify planning head**

```powershell
$Repo = "D:\administrator\Desktop\大模型产品化\知识库研究"
$Worktree = "D:\administrator\Desktop\大模型产品化\port-knowledge-base-e005"
git -C $Repo fetch origin
git -C $Repo rev-parse origin/dev
git -C $Repo rev-parse origin/docs/e005-compile-transaction-recovery-design
git -C $Repo status --short
```

Expected:

- `origin/dev`仍为执行时最新基线；
- 设计分支包含本规格和本计划；
- 主工作区已有未跟踪文件只记录、不触碰；有tracked修改时停止并报告。

- [ ] **Step 2: Create isolated worktree with required skill**

先使用`superpowers:using-git-worktrees`，再执行：

```powershell
git -C $Repo worktree add `
  $Worktree `
  -b fix/e005-compile-transaction-recovery `
  origin/docs/e005-compile-transaction-recovery-design
Set-Location $Worktree
git branch --show-current
git status --short
git log -5 --oneline
```

Expected: branch为`fix/e005-compile-transaction-recovery`，worktree clean，HEAD包含设计和实施计划。

- [ ] **Step 3: Install baseline dependencies without editing files**

```powershell
python -m pip install -r requirements.txt
Push-Location frontend
npm ci
Pop-Location
git status --short
```

Expected: commands exit 0；安装后没有tracked变化。

- [ ] **Step 4: Capture immutable real-data manifest outside repository**

```powershell
$BeforeManifest = Join-Path $env:TEMP "e005-data-before.json"
@'
import hashlib
import json
import sys
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
Path(sys.argv[1]).write_text(
    json.dumps(rows, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print(len(rows))
'@ | python - $BeforeManifest
```

Expected: manifest只写入`$env:TEMP`。

- [ ] **Step 5: Run baseline gates**

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

Expected: all exit 0。基线失败时停止，不开始E005修改。

---

# Phase 1 — Transaction Kernel

### Task 1: Runtime Dependencies, Durable Files, and Configuration

**Files:**

- Create: `api/durable_fs.py`
- Create: `api/runtime_guard.py`
- Create: `tests/test_durable_fs.py`
- Create: `tests/test_runtime_guard.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`

**Interfaces:**

- Produces: `sha256_file(path: Path) -> str`
- Produces: `durable_write_bytes(path: Path, payload: bytes) -> None`
- Produces: `durable_write_yaml(path: Path, data: Mapping[str, Any]) -> None`
- Produces: `durable_publish_directory(staging: Path, target: Path) -> None`
- Produces: `probe_durable_directory(path: Path) -> None`
- Produces: `CompileRuntimeConfig`
- Produces: `load_compile_runtime_config(base_dir: Path, env: Mapping[str, str] | None = None) -> CompileRuntimeConfig`
- Produces: `ApiInstanceLock`
- Produces: `ServiceReadiness`
- Consumers: all later backend tasks.

- [ ] **Step 1: Add dependencies and write failing configuration tests**

Add to`requirements.txt`:

```text
portalocker>=2.10.0    # 跨平台单API实例排他锁
psutil>=6.0.0         # 跨平台进程身份与进程树终止
```

Add to`.gitignore`:

```text
.runtime/
```

Create tests asserting defaults and invalid ranges:

```python
from api.runtime_guard import load_compile_runtime_config


def test_runtime_config_uses_documented_defaults(tmp_path):
    config = load_compile_runtime_config(tmp_path, env={})
    assert config.transaction_dir == tmp_path / ".runtime" / "compile-transactions"
    assert config.instance_lock_path == tmp_path / ".runtime" / "api-instance.lock"
    assert config.timeout_seconds == 1800
    assert config.termination_grace_seconds == 5


def test_runtime_config_rejects_out_of_range_timeout(tmp_path):
    with pytest.raises(ValueError, match="COMPILE_TIMEOUT_SECONDS"):
        load_compile_runtime_config(tmp_path, env={"COMPILE_TIMEOUT_SECONDS": "59"})
```

- [ ] **Step 2: Run RED tests**

```powershell
python -m pytest tests/test_runtime_guard.py tests/test_durable_fs.py -q
```

Expected: import failure because modules do not exist.

- [ ] **Step 3: Implement focused modules**

Use these exact dataclasses and state methods:

```python
@dataclass(frozen=True)
class CompileRuntimeConfig:
    transaction_dir: Path
    upload_intake_dir: Path
    instance_lock_path: Path
    timeout_seconds: int
    termination_grace_seconds: int


class ServiceReadiness:
    def __init__(self) -> None:
        self._mode = "ready"
        self._reason_code: str | None = None
        self._lock = threading.Lock()

    def mark_recovery_required(self, reason_code: str) -> None:
        with self._lock:
            self._mode = "recovery_required"
            self._reason_code = reason_code

    def require_ready(self) -> None:
        with self._lock:
            if self._mode != "ready":
                raise RuntimeError("recovery_required")

    def snapshot(self) -> tuple[str, str | None]:
        with self._lock:
            return self._mode, self._reason_code
```

`ApiInstanceLock.acquire()` must call `portalocker.Lock(str(self._path), mode="a", timeout=0)` and hold the returned handle until `release()`.

`durable_write_bytes()` must write a same-directory temporary file, flush,`os.fsync`,`os.replace`, verify bytes, and on POSIX open/fsync the parent directory.

- [ ] **Step 4: Run GREEN and dependency checks**

```powershell
python -m pip install -r requirements.txt
python -m pytest tests/test_runtime_guard.py tests/test_durable_fs.py -q
python -m pip check
```

Expected: all pass.

- [ ] **Step 5: Commit**

```powershell
git add requirements.txt .gitignore api/durable_fs.py api/runtime_guard.py tests/test_durable_fs.py tests/test_runtime_guard.py
git commit -m "feat: add durable runtime guard primitives"
```

---

### Task 2: Manifest Model and Persistent Snapshot Store

**Files:**

- Create: `api/compile_transactions.py`
- Create: `tests/test_compile_transactions.py`
- Modify: `api/compile_jobs.py`

**Interfaces:**

- Produces: `TransactionState(str, Enum)` with `PREPARED`, `SCHEDULED`, `RUNNING`, `COMMITTED`, `ROLLBACKING`, `ROLLED_BACK`.
- Produces: `TransactionKind(str, Enum)` with `RECOMPILE`, `UPLOAD`.
- Produces: `ArtifactRecord`, `ProcessRecord`, `PublishedIntake`, `CompileManifest` dataclasses.
- Produces: `artifact_paths(base_dir: Path, doc_id: str) -> tuple[Path, ...]`.
- Produces: `create_prepared_transaction(*, base_dir: Path, config: CompileRuntimeConfig, doc_id: str, kind: TransactionKind, previous_meta: dict[str, object] | None, published_intake: PublishedIntake | None = None) -> CompileManifest`.
- Produces: `load_manifest(job_dir: Path) -> CompileManifest`.
- Produces: `transition_manifest(job_dir: Path, expected: TransactionState, target: TransactionState, **changes) -> CompileManifest`.
- Produces: `list_transaction_dirs(config: CompileRuntimeConfig) -> list[Path]`.
- Consumers: Tasks 4–10.

- [ ] **Step 1: Write RED tests for schema, snapshots, and state transitions**

Include tests:

```python
def test_create_prepared_transaction_snapshots_exact_seven_paths(tmp_path):
    manifest = create_prepared_transaction(
        base_dir=tmp_path,
        config=runtime_config(tmp_path),
        doc_id="doc_20260806_001",
        kind=TransactionKind.RECOMPILE,
        previous_meta={"id": "doc_20260806_001", "status": "compiled"},
    )
    assert manifest.state is TransactionState.PREPARED
    assert len(manifest.artifacts) == 7
    assert {item.path for item in manifest.artifacts} == EXPECTED_ARTIFACT_PATHS


def test_transition_rejects_wrong_expected_state(tmp_path):
    manifest = prepared_manifest(tmp_path)
    with pytest.raises(TransactionStateError):
        transition_manifest(
            manifest.job_dir,
            expected=TransactionState.RUNNING,
            target=TransactionState.COMMITTED,
        )
```

Also test unknown schema, path traversal, corrupted snapshot, staging cleanup, and more than one active transaction.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_compile_transactions.py -q
```

Expected: module/functions missing.

- [ ] **Step 3: Implement manifest serialization and durable snapshots**

Manifest loader must reject unknown keys required for security-critical sections and verify:

```python
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
```

Keep`artifact_paths`import-compatible from`api.compile_jobs`by re-exporting it there, so existing E004 tests can migrate incrementally.

- [ ] **Step 4: Run GREEN and E004 snapshot regressions**

```powershell
python -m pytest tests/test_compile_transactions.py tests/test_compile_jobs.py -q
```

Expected: new tests pass; existing compile job tests remain green or fail only where later tasks intentionally change signatures. Any unrelated failure must be fixed before commit.

- [ ] **Step 5: Commit**

```powershell
git add api/compile_transactions.py api/compile_jobs.py tests/test_compile_transactions.py
git commit -m "feat: persist compile transaction manifests"
```

---

### Task 3: Cross-Platform Process Identity and Tree Termination

**Files:**

- Create: `api/process_tree.py`
- Create: `tests/test_process_tree.py`

**Interfaces:**

- Produces: `ProcessIdentity` dataclass.
- Produces: `SpawnedProcess` dataclass with`popen`, `identity`, `command_fingerprint`.
- Produces: `spawn_compile_process(base_dir: Path, doc_id: str, env: Mapping[str, str]) -> SpawnedProcess`.
- Produces: `verify_process_identity(identity: ProcessIdentity) -> IdentityStatus`.
- Produces: `wait_for_process(spawned: SpawnedProcess, timeout_seconds: int) -> ProcessResult`.
- Produces: `terminate_process_tree(identity: ProcessIdentity, grace_seconds: int) -> TerminationResult`.
- Consumers: Tasks 4 and 5.

- [ ] **Step 1: Write RED unit and real-process tests**

Create a helper process that spawns one child and sleeps. Test:

```python
def test_command_fingerprint_ignores_interpreter_absolute_path():
    left = command_fingerprint(["C:/Python/python.exe", "-m", "scripts.compile", "doc_1"])
    right = command_fingerprint(["/usr/bin/python3", "-m", "scripts.compile", "doc_1"])
    assert left == right == "scripts.compile|doc_1"


def test_identity_mismatch_never_terminates_process(monkeypatch):
    identity = fake_identity(pid=123, create_time=1.0)
    monkeypatch.setattr(psutil, "Process", lambda _pid: fake_process(create_time=2.0))
    result = terminate_process_tree(identity, grace_seconds=1)
    assert result.status == "identity_mismatch"
```

Mark the real tree test with platform-specific assertions but do not skip the current platform.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_process_tree.py -q
```

Expected: module missing.

- [ ] **Step 3: Implement spawn and termination**

Use:

```python
creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
start_new_session = os.name != "nt"
```

`terminate_process_tree` must:

1. verify root identity;
2. snapshot descendants through`psutil.Process.children(recursive=True)`;
3. attempt cooperative termination;
4. wait exactly configured grace;
5. kill remaining descendants before root;
6. return success only when root and all captured descendants are gone.

- [ ] **Step 4: Run GREEN twice to catch process leaks**

```powershell
python -m pytest tests/test_process_tree.py -q
python -m pytest tests/test_process_tree.py -q
```

Expected: both pass with no lingering helper processes.

- [ ] **Step 5: Commit**

```powershell
git add api/process_tree.py tests/test_process_tree.py
git commit -m "feat: manage compile process trees safely"
```

---

### Task 4: Idempotent Recovery Engine and Offline CLI

**Files:**

- Modify: `api/compile_transactions.py`
- Create: `scripts/compile_recovery.py`
- Create: `tests/test_compile_recovery.py`
- Modify: `tests/test_compile_transactions.py`

**Interfaces:**

- Produces: `recover_transaction(base_dir, config, job_dir, *, reason_code, reason_message) -> RecoveryResult`.
- Produces: `recover_startup(base_dir, config) -> StartupRecoveryReport`.
- Produces: `verify_terminal_transaction(base_dir, manifest) -> VerificationReport`.
- Produces: `cleanup_terminal_transactions(base_dir, config) -> CleanupReport`.
- CLI consumes exactly these functions.

- [ ] **Step 1: Write RED recovery state tests**

Cover:

```python
def test_scheduled_is_rolled_back_not_rescheduled(tmp_path, monkeypatch):
    manifest = scheduled_manifest(tmp_path)
    add_task = Mock()
    report = recover_startup(tmp_path, runtime_config(tmp_path))
    assert report.recovered == [manifest.job_id]
    add_task.assert_not_called()
    assert read_meta(tmp_path, manifest.doc_id)["error_code"] == "interrupted"


def test_rollbacking_recovery_is_idempotent_after_partial_restore(tmp_path):
    manifest = rollbacking_manifest_with_three_partially_restored_files(tmp_path)
    first = recover_transaction(tmp_path, runtime_config(tmp_path), manifest.job_dir,
                                reason_code="interrupted", reason_message="service restart")
    second = recover_transaction(tmp_path, runtime_config(tmp_path), manifest.job_dir,
                                 reason_code="interrupted", reason_message="service restart")
    assert first.completed is True
    assert second.already_terminal is True
```

Also test unknown schema, two active transactions, orphan`compiling`, corrupted snapshot, terminal cleanup verification, and upload-kind file removal.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_compile_recovery.py tests/test_compile_transactions.py -q
```

- [ ] **Step 3: Implement recovery and CLI without bypass commands**

CLI parser must expose only:

```python
subcommands = ("inspect", "verify", "recover", "cleanup-terminal")
```

`recover` and`cleanup-terminal`must acquire`ApiInstanceLock`;`inspect`and`verify`remain read-only but still report whether the API lock is held.

No command may delete an active transaction without successful verification/recovery.

- [ ] **Step 4: Run GREEN and CLI smoke**

```powershell
python -m pytest tests/test_compile_recovery.py tests/test_compile_transactions.py -q
python -m scripts.compile_recovery inspect --base-dir .
```

Expected: tests pass; smoke prints no secrets and exits 0 on a clean repository.

- [ ] **Step 5: Commit**

```powershell
git add api/compile_transactions.py scripts/compile_recovery.py tests/test_compile_transactions.py tests/test_compile_recovery.py
git commit -m "feat: recover interrupted compile transactions"
```

---

### Task 5: Persistent Compile Executor and Hard Timeout

**Files:**

- Modify: `api/compile_jobs.py`
- Modify: `scripts/doc_admin.py`
- Modify: `tests/test_compile_jobs.py`
- Modify: `tests/test_doc_admin.py`

**Interfaces:**

- Produces: `prepare_recompile_transaction(doc_id, base_dir, config) -> CompileManifest`.
- Produces: `run_compile_task(job_id: str, base_dir: Path | None = None, config: CompileRuntimeConfig | None = None, readiness: ServiceReadiness | None = None) -> None`.
- Produces: `bind_doc_compile_job(doc_id: str, job_id: str, started_at: str, deadline: str, *, base_dir: Path | None = None) -> bool` and `clear_doc_compile_job(doc_id: str, *, base_dir: Path | None = None) -> bool` in `doc_admin.py`.
- Consumers: API tasks.

- [ ] **Step 1: Write RED tests for commit, timeout, and rollback failure**

Add tests asserting:

```python
def test_run_compile_task_commits_only_after_semantic_validation(tmp_path):
    manifest = scheduled_transaction(tmp_path)
    fake_process = successful_compile_process_that_writes_valid_outputs(tmp_path, manifest.doc_id)
    run_compile_task(manifest.job_id, tmp_path, runtime_config(tmp_path), ServiceReadiness())
    committed = load_manifest(manifest.job_dir)
    assert committed.state is TransactionState.COMMITTED


def test_timeout_terminates_tree_before_restoring(tmp_path, monkeypatch):
    manifest = scheduled_transaction(tmp_path)
    calls = []
    monkeypatch.setattr("api.compile_jobs.terminate_process_tree",
                        lambda identity, grace_seconds: calls.append("terminate") or terminated())
    monkeypatch.setattr("api.compile_jobs.restore_transaction_artifacts",
                        lambda *args: calls.append("restore") or restored())
    run_compile_task(manifest.job_id, tmp_path, timeout_config(tmp_path, 60), ServiceReadiness())
    assert calls == ["terminate", "restore"]
```

Test that failure to confirm process exit marks readiness`recovery_required`and does not restore files.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_compile_jobs.py tests/test_doc_admin.py -q
```

- [ ] **Step 3: Replace temporary runner with transaction-aware Popen runner**

Preserve existing`sanitize_compile_error()`and`classify_compile_error()`contracts. Change task input fromdoc_id tojob_id. Execution must transition:

```text
SCHEDULED → RUNNING → COMMITTED
                     ↘ ROLLBACKING → ROLLED_BACK
```

`write_doc_compile_result()`must clear`compile_job_id`, `compile_started_at`, and`compile_deadline`on all terminal writes.

- [ ] **Step 4: Run GREEN and existing E004 regression suites**

```powershell
python -m pytest tests/test_compile_jobs.py tests/test_doc_admin.py tests/test_api.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add api/compile_jobs.py scripts/doc_admin.py tests/test_compile_jobs.py tests/test_doc_admin.py
git commit -m "feat: enforce compile hard timeout and commit point"
```

---

# Phase 1 Review Gate

- [ ] Run Phase 1 suite:

```powershell
python -m pytest `
  tests/test_durable_fs.py `
  tests/test_runtime_guard.py `
  tests/test_compile_transactions.py `
  tests/test_process_tree.py `
  tests/test_compile_recovery.py `
  tests/test_compile_jobs.py `
  tests/test_doc_admin.py `
  -q
```

- [ ] Request independent spec review against Design §§6–18 and test matrix T01–P07.
- [ ] Do not begin API integration until all blocking review findings are resolved.

---

# Phase 2 — API and Two-Phase Upload Integration

### Task 6: Pure Ingest Preparation Boundary

**Files:**

- Modify: `scripts/ingest.py`
- Create: `tests/test_ingest.py`

**Interfaces:**

- Produces: `PreparedIngest` dataclass.
- Produces: `prepare_ingest(staged_source: Path, *, base_dir: Path | None = None, existing_doc_ids: set[str] | None = None) -> PreparedIngest`.
- Produces: `publish_prepared_ingest(prepared: PreparedIngest, source_path: Path, *, base_dir: Path | None = None) -> dict` for CLI compatibility.
- Existing: `ingest_file(file_path: Path) -> dict | None` retains behavior by composing the two functions.
- Consumers: Task 7.

- [ ] **Step 1: Write RED purity and compatibility tests**

```python
def test_prepare_ingest_returns_candidate_without_writing_business_directories(tmp_path):
    staged = tmp_path / "upload.txt"
    staged.write_text("港口数字化测试", encoding="utf-8")
    prepared = prepare_ingest(staged, base_dir=tmp_path, existing_doc_ids=set())
    assert prepared.doc_id.startswith("doc_")
    assert prepared.text_bytes.decode("utf-8") == "港口数字化测试"
    assert not (tmp_path / "originals").exists()
    assert not (tmp_path / "raw").exists()


def test_ingest_file_keeps_cli_publish_contract(tmp_path):
    source = tmp_path / "originals" / "a.txt"
    source.parent.mkdir(parents=True)
    source.write_text("content", encoding="utf-8")
    meta = ingest_file(source, base_dir=tmp_path)
    assert (tmp_path / "raw" / f"{meta['id']}.txt").exists()
    assert (tmp_path / "raw" / f"{meta['id']}.meta.yaml").exists()
```

If adding optional`base_dir`to`ingest_file`, update callers and tests without changing CLI output.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_ingest.py -q
```

- [ ] **Step 3: Refactor parsing without changing parser semantics**

`PreparedIngest`fields:

```python
@dataclass(frozen=True)
class PreparedIngest:
    doc_id: str
    original_name: str
    source_type: str
    file_hash: str
    text_bytes: bytes
    meta: dict[str, object]
```

Do not update index, ontology, relations, or compile status in`prepare_ingest()`.

- [ ] **Step 4: Run GREEN and parser regressions**

```powershell
python -m pytest tests/test_ingest.py tests/test_api.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add scripts/ingest.py tests/test_ingest.py
git commit -m "refactor: prepare ingest before publishing files"
```

---

### Task 7: Upload Intake Staging and Atomic Publish

**Files:**

- Create: `api/upload_intake.py`
- Create: `tests/test_upload_intake.py`
- Modify: `api/compile_transactions.py`

**Interfaces:**

- Produces: `StagedUpload` dataclass.
- Produces: `stage_upload(file: UploadFile, config: CompileRuntimeConfig) -> StagedUpload`.
- Produces: `prepare_upload_transaction(staged, prepared, base_dir, config) -> CompileManifest`.
- Produces: `publish_upload_intake(manifest, staged, prepared, base_dir) -> CompileManifest`.
- Produces: `rollback_published_intake(manifest, base_dir) -> list[str]`.
- Consumers: Task 8.

- [ ] **Step 1: Write RED tests for collision-safe publish and rollback**

Cover each published target independently. Representative test:

```python
def test_publish_never_overwrites_existing_original(tmp_path):
    existing = tmp_path / "originals" / "report.txt"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"existing")
    manifest, staged, prepared = prepared_upload_transaction(tmp_path, "report.txt")
    with pytest.raises(FileExistsError):
        publish_upload_intake(manifest, staged, prepared, tmp_path)
    assert existing.read_bytes() == b"existing"


def test_partial_publish_rollback_removes_only_request_created_files(tmp_path):
    manifest = upload_manifest_with_original_and_raw_text_published(tmp_path)
    failures = rollback_published_intake(manifest, tmp_path)
    assert failures == []
    assert not manifest.original_target.exists()
    assert not manifest.raw_text_target.exists()
    assert unrelated_file(tmp_path).read_bytes() == b"keep"
```

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_upload_intake.py -q
```

- [ ] **Step 3: Implement intake journal and durable publish**

`intake.yaml`must record exact relative paths and booleans only after each durable publish completes. Rollback recomputes allowed targets frommanifest/doc_id and refuses paths outside`originals/`or`raw/`.

- [ ] **Step 4: Run GREEN with transaction recovery tests**

```powershell
python -m pytest tests/test_upload_intake.py tests/test_compile_recovery.py tests/test_compile_transactions.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add api/upload_intake.py api/compile_transactions.py tests/test_upload_intake.py
git commit -m "feat: publish uploads through durable intake staging"
```

---

### Task 8: FastAPI Lifespan, Readiness, and Global Business Gate

**Files:**

- Modify: `api/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**

- Consumes: `CompileRuntimeConfig`, `ApiInstanceLock`, `ServiceReadiness`, `recover_startup`.
- Produces: FastAPI lifespan holding the instance lock.
- Produces: `GET /api/v1/ready`.
- Produces: business-route gate returning503`recovery_required`.

- [ ] **Step 1: Write RED lifespan and readiness tests**

```python
def test_ready_endpoint_reports_ready(client):
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_recovery_required_blocks_business_routes_but_not_health(client, readiness):
    readiness.mark_recovery_required("rollback_failed")
    assert client.get("/api/v1/wiki/index").status_code == 503
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/ready").status_code == 503
```

Add startup tests for instance-lock conflict and orphan`compiling`using a temporary app factory, not the repository's real data.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_api.py -q
```

- [ ] **Step 3: Implement lifespan and route gate**

Create an app-state container:

```python
@dataclass
class AppRuntime:
    config: CompileRuntimeConfig
    readiness: ServiceReadiness
    instance_lock: ApiInstanceLock
```

Attach to`app.state.runtime`. The gate must exempt only`/api/v1/health`and`/api/v1/ready`.

- [ ] **Step 4: Run GREEN and startup smoke**

```powershell
python -m pytest tests/test_api.py -q
python -c "from api.main import app; print(app.title, app.version)"
```

- [ ] **Step 5: Commit**

```powershell
git add api/main.py tests/test_api.py
git commit -m "feat: gate API startup on transaction recovery"
```

---

### Task 9: Recompile and Delete API Transaction Contracts

**Files:**

- Modify: `api/main.py`
- Modify: `tests/test_api.py`

**Interfaces:**

- Produces: `_schedule_compile(background_tasks, doc_id) -> None` using persistent transaction preparation.
- Preserves: recompile success body.
- Preserves: delete success body.
- Adds:409`knowledge_base_busy`,503`compile_transaction_unavailable`,503`recovery_required`.

- [ ] **Step 1: Write RED API contract tests**

Test exact bodies:

```python
def test_other_active_transaction_rejects_recompile(client, active_transaction):
    response = client.post("/api/v1/docs/doc_2/recompile")
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}


def test_transaction_prepare_failure_does_not_change_meta(client, tmp_repo, monkeypatch):
    before = read_meta_bytes(tmp_repo, "doc_1")
    monkeypatch.setattr("api.main.prepare_recompile_transaction", raising_prepare_error)
    response = client.post("/api/v1/docs/doc_1/recompile")
    assert response.status_code == 503
    assert response.json() == {"detail": {"code": "compile_transaction_unavailable"}}
    assert read_meta_bytes(tmp_repo, "doc_1") == before
```

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_api.py -q
```

- [ ] **Step 3: Wire persistent transaction scheduling**

`BackgroundTasks.add_task`must receive`run_compile_task, manifest.job_id, BASE_DIR, config, readiness`. Do not passdoc_id as the task identity.

Delete keeps lock order`COMPILE_SCHEDULE_LOCK → nonblocking COMPILE_EXECUTION_LOCK`and additionally rejects any active Manifest before removing files.

- [ ] **Step 4: Run GREEN and backend contract group**

```powershell
python -m pytest tests/test_api.py tests/test_compile_jobs.py tests/test_doc_admin.py -q
```

- [ ] **Step 5: Commit**

```powershell
git add api/main.py tests/test_api.py
git commit -m "feat: schedule recompiles through persistent transactions"
```

---

### Task 10: Two-Phase Upload API and R8 Crash Windows

**Files:**

- Modify: `api/main.py`
- Modify: `tests/test_api.py`
- Create: `tests/test_e005_crash_recovery.py`

**Interfaces:**

- Consumes: pure ingest and upload intake interfaces.
- Preserves: upload success and duplicate response shapes.
- Produces: R8 injection points represented by an internal test-only callable parameter or monkeypatchable named functions, not a production environment switch.

- [ ] **Step 1: Write RED upload acceptance and crash recovery tests**

API tests:

```python
def test_busy_upload_publishes_no_business_files(client, tmp_repo, active_transaction):
    before = business_manifest(tmp_repo)
    response = client.post(
        "/api/v1/upload",
        files={"file": ("a.txt", b"content", "text/plain")},
    )
    assert response.status_code == 409
    assert response.json() == {"detail": {"code": "knowledge_base_busy"}}
    assert business_manifest(tmp_repo) == before
```

R8 parameterization:

```python
@pytest.mark.parametrize("crash_point", [
    "after_intake_stage",
    "after_prepare_ingest",
    "after_prepared_manifest",
    "after_original_publish",
    "after_raw_text_publish",
    "after_scheduled",
])
def test_r8_upload_crash_windows_recover_without_orphans(tmp_path, crash_point):
    scenario = UploadCrashScenario(tmp_path, crash_point)
    scenario.run_until_crash()
    report = recover_startup(tmp_path, scenario.config)
    assert report.blockers == []
    assert scenario.orphan_docs() == []
    assert scenario.shared_artifacts_match_before()
    assert scenario.preexisting_files_unchanged()
```

Crash injection must call monkeypatched named boundaries; do not add`E005_CRASH_POINT`to production environment handling.

- [ ] **Step 2: Run RED**

```powershell
python -m pytest tests/test_api.py tests/test_e005_crash_recovery.py -q
```

- [ ] **Step 3: Replace `_accept_upload` with two-phase flow**

The full flow stays inside`COMPILE_SCHEDULE_LOCK`until Manifest reaches`SCHEDULED`and`add_task`returns. Use`try/finally`to remove unaccepted intake staging. Any failure afterPREPAREDmust call the same recovery library.

- [ ] **Step 4: Run GREEN and all backend E005 tests**

```powershell
python -m pytest `
  tests/test_ingest.py `
  tests/test_upload_intake.py `
  tests/test_api.py `
  tests/test_e005_crash_recovery.py `
  tests/test_compile_recovery.py `
  -q
```

- [ ] **Step 5: Commit**

```powershell
git add api/main.py tests/test_api.py tests/test_e005_crash_recovery.py
git commit -m "feat: make upload intake crash recoverable"
```

---

# Phase 2 Review Gate

- [ ] Run all backend E005 and existing backend tests offline:

```powershell
$env:OPENAI_API_KEY = ""
$env:EMBEDDING_API_KEY = ""
$env:HTTP_PROXY = "http://127.0.0.1:9"
$env:HTTPS_PROXY = "http://127.0.0.1:9"
python -m pytest tests/ -q
```

- [ ] Request independent review focused on API lock order, upload R8, public-data leakage, and startup/运行门禁。
- [ ] Resolve blocking findings before frontend work.

---

# Phase 3 — Frontend, Documentation, and Full Gates

### Task 11: Frontend Error Type Separation and Fixed Copy

**Files:**

- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/tests/unit/api-errors.test.ts`
- Modify: `frontend/tests/unit/UploadZone.test.tsx`

**Interfaces:**

- Produces: `DocumentCompileErrorCode`.
- Produces: `CompileRequestErrorCode`.
- Produces: `CompileErrorCode` union.
- Preserves: `ApiError`, `getCompileErrorMessage`, `getUserFacingErrorMessage`.

- [ ] **Step 1: Write RED fixed-copy tests**

```typescript
expect(getCompileErrorMessage('interrupted')).toBe(
    '编译任务因服务重启中断，旧版本已恢复，请重新编译',
);
expect(getCompileErrorMessage('compile_transaction_unavailable')).toBe(
    '编译任务暂时无法创建，请稍后重试或联系管理员',
);
expect(getCompileErrorMessage('recovery_required')).toBe(
    '知识库正在恢复或需要管理员处理，暂不可用',
);
```

Assert rendered messages exclude`job_id`, PID, absolute paths, `RuntimeError`, status codes and environment variable names.

- [ ] **Step 2: Run RED**

```powershell
Push-Location frontend
npx jest tests/unit/api-errors.test.ts tests/unit/UploadZone.test.tsx --runInBand
Pop-Location
```

- [ ] **Step 3: Implement type separation and mappings**

Use exact unions:

```typescript
export type DocumentCompileErrorCode =
    | 'llm_configuration'
    | 'service_unavailable'
    | 'timeout'
    | 'document_processing'
    | 'compile_failed'
    | 'interrupted'
    | 'rollback_failed';

export type CompileRequestErrorCode =
    | 'compile_in_progress'
    | 'knowledge_base_busy'
    | 'compile_transaction_unavailable'
    | 'recovery_required';
```

`DocMeta.error_code`must use`DocumentCompileErrorCode`, not the full union.

- [ ] **Step 4: Run GREEN**

```powershell
Push-Location frontend
npx jest tests/unit/api-errors.test.ts tests/unit/UploadZone.test.tsx --runInBand
npx eslint src/lib/api.ts tests/unit/api-errors.test.ts tests/unit/UploadZone.test.tsx
Pop-Location
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/lib/api.ts frontend/tests/unit/api-errors.test.ts frontend/tests/unit/UploadZone.test.tsx
git commit -m "feat: map E005 recovery errors to safe copy"
```

---

### Task 12: Wiki Polling for Interrupted Recovery

**Files:**

- Modify: `frontend/src/app/wiki/page.tsx`
- Modify: `frontend/tests/unit/wiki-polling.test.tsx`

**Interfaces:**

- Reuses existing mutation epoch and request sequence guards.
- Adds no new visible document status.

- [ ] **Step 1: Write RED transition tests**

Add:

```typescript
test('compiling to interrupted emits one recovery toast', async () => {
    mockedFetch
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'compiling' }],
        })
        .mockResolvedValue({
            total_docs: 1,
            documents: [{ id: 'doc_1', status: 'error', error_code: 'interrupted' }],
        });
    render(<WikiPage />);
    await screen.findByTestId('compiling-hint');
    await act(async () => {
        jest.advanceTimersByTime(3000);
        await Promise.resolve();
    });
    await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));
    expect(mockToastPush).toHaveBeenCalledWith(
        '编译任务因服务重启中断，旧版本已恢复，请重新编译',
        'error',
    );
});
```

Also assert historical`interrupted`does not Toast and stale pre-restart response cannot overwrite it.

- [ ] **Step 2: Run RED**

```powershell
Push-Location frontend
npx jest tests/unit/wiki-polling.test.tsx --runInBand
Pop-Location
```

- [ ] **Step 3: Make only minimal page changes**

The current generic`compiling→error`logic should already support the new code after Task 11. Modify page code only if tests prove a gap; do not add a second polling state machine.

- [ ] **Step 4: Run GREEN and complete frontend unit suite**

```powershell
Push-Location frontend
npx jest tests/unit/wiki-polling.test.tsx --runInBand
npm test -- --runInBand
Pop-Location
```

- [ ] **Step 5: Commit**

```powershell
git add frontend/src/app/wiki/page.tsx frontend/tests/unit/wiki-polling.test.tsx
git commit -m "test: cover interrupted compile polling"
```

If production page requires no change, commit only the test file with message`test: cover interrupted compile polling`.

---

### Task 13: Documentation, Environment Contract, and Task Record

**Files:**

- Modify: `.env.example`
- Modify: `CLAUDE.md`
- Create: `docs/dev/tasks/E005-compile-transaction-recovery.md`
- Modify: `docs/superpowers/specs/2026-08-06-e005-compile-transaction-recovery-design.md` only for verified implementation clarifications, not scope changes.

**Interfaces:** none.

- [ ] **Step 1: Add documented environment values**

Append to`.env.example`:

```text
# ─── E005 编译事务恢复 ──────────────────────────────────
# 生产环境必须把该目录放在与 raw/wiki/meta 同等级的持久卷；不得使用 emptyDir 或缓存目录。
COMPILE_TRANSACTION_DIR=.runtime/compile-transactions
COMPILE_TIMEOUT_SECONDS=1800
COMPILE_TERMINATION_GRACE_SECONDS=5
```

- [ ] **Step 2: Update governance boundaries**

In`CLAUDE.md`risk 9, state E005 closes persistent recovery and hard timeout for single API instance, while retaining boundaries:

- no multiworker/multi-host writes;
- no external CLI coordination;
- no universal shared-YAML transaction framework;
- no real LLM UAT claim.

- [ ] **Step 3: Create task record with exact contracts**

`docs/dev/tasks/E005-compile-transaction-recovery.md`must record:

- baseline SHA;
- design and plan paths;
- phase/task commits;
- exact final test counts only after fresh runs;
- R1–R8 evidence;
- Windows/POSIX process-tree evidence;
- data manifest result;
- known boundaries.

Do not prefill unverified counts with guesses; use the literal phrase`未执行`until the relevant command has run, then replace it before completion.

- [ ] **Step 4: Run documentation checks**

```powershell
git diff --check
git grep -n "start-api-anyway\|ignore-checksum\|force-delete" -- . ':!docs/superpowers/specs/2026-08-06-e005-compile-transaction-recovery-design.md'
```

Expected: diff check clean; no prohibited recovery bypass implemented.

- [ ] **Step 5: Commit**

```powershell
git add .env.example CLAUDE.md docs/dev/tasks/E005-compile-transaction-recovery.md docs/superpowers/specs/2026-08-06-e005-compile-transaction-recovery-design.md
git commit -m "docs: document E005 recovery operations"
```

---

### Task 14: Full Crash Matrix, Offline Gates, and Data Integrity

**Files:**

- Modify: `tests/test_e005_crash_recovery.py`
- Modify: `.github/workflows/ci.yml` only if existing full pytest does not automatically run new tests; do not rename jobs.
- Modify: `docs/dev/tasks/E005-compile-transaction-recovery.md` with fresh evidence.

**Interfaces:** none; verification task.

- [ ] **Step 1: Complete R1–R8 deterministic integration scenarios**

Use only temporary repositories and short-lived local processes. For R5/R7, inject the second crash by raising a dedicated test exception from a monkeypatched durable-write boundary after at least one target has been restored; restart by calling the production recovery entry again.

- [ ] **Step 2: Run targeted backend verification**

```powershell
$env:OPENAI_API_KEY = ""
$env:EMBEDDING_API_KEY = ""
$env:HTTP_PROXY = "http://127.0.0.1:9"
$env:HTTPS_PROXY = "http://127.0.0.1:9"
python -m pytest `
  tests/test_durable_fs.py `
  tests/test_runtime_guard.py `
  tests/test_compile_transactions.py `
  tests/test_process_tree.py `
  tests/test_compile_recovery.py `
  tests/test_compile_jobs.py `
  tests/test_upload_intake.py `
  tests/test_ingest.py `
  tests/test_api.py `
  tests/test_e005_crash_recovery.py `
  -q
```

Expected: exit 0; record exact count.

- [ ] **Step 3: Run full backend and frontend gates**

```powershell
python -m pytest tests/ -q
Push-Location frontend
npm test -- --runInBand
npx eslint src tests
npm run build
Pop-Location
python -m pip check
python -m compileall api scripts tests
```

Expected: all exit 0; record exact counts and warnings without hiding them.

- [ ] **Step 4: Verify real-data manifest and repository scope**

```powershell
$AfterManifest = Join-Path $env:TEMP "e005-data-after.json"
@'
import hashlib
import json
import sys
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
Path(sys.argv[1]).write_text(
    json.dumps(rows, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
'@ | python - $AfterManifest
if ((Get-FileHash $BeforeManifest).Hash -ne (Get-FileHash $AfterManifest).Hash) {
    throw "E005 real-data manifest changed"
}
git status --short
git diff --stat
git diff --check
```

Expected: manifests identical; no `.runtime/` tracked files; scope matches File Map.

- [ ] **Step 5: Commit final test evidence**

```powershell
git add tests/test_e005_crash_recovery.py .github/workflows/ci.yml docs/dev/tasks/E005-compile-transaction-recovery.md
git commit -m "test: close E005 crash recovery matrix"
```

If CI file required no change, omit it from`git add`.

---

# Final Review and Integration Preparation

- [ ] **Step 1: Run verification-before-completion checks fresh**

Repeat targeted backend, full backend, full frontend, lint, build, compileall, pip check and data manifest commands from Task 14. Do not rely on earlier output.

- [ ] **Step 2: Inspect commit and diff scope**

```powershell
git log --oneline origin/dev..HEAD
git diff --name-status origin/dev...HEAD
git diff --check origin/dev...HEAD
git status --short
```

Expected: only E005 files and approved documentation; worktree clean.

- [ ] **Step 3: Request independent reviews**

Review sequence:

1. specification compliance review;
2. code quality/security review;
3. Codex GitHub Review after PR creation;
4. rerun checks and re-review after every new Head.

Blocking findings: P0/P1 and verified correctness/security/test-isolation/scope P2.

- [ ] **Step 4: Prepare PR only after user authorization**

Suggested PR title:

```text
feat: recover interrupted compile transactions
```

PR body must include:

- transaction state and commit point;
- R1–R8 crash matrix;
- Windows/POSIX process tree evidence;
- exact backend/frontend counts;
- data manifest result;
- single-instance and non-goal boundaries.

- [ ] **Step 5: Merge only through governed command after approval**

```powershell
$PrNumber = [int](gh pr view --json number --jq '.number')
$HeadSha = git rev-parse HEAD
gh pr merge $PrNumber `
  --merge `
  --match-head-commit $HeadSha
```

Do not use`--admin`; do not delete branch/worktree until remote merge and merge-commit CI are independently verified.

---

## Plan Self-Review

- **Spec coverage:** Tasks 1–5 cover transaction kernel, timeout, process tree and recovery; Tasks 6–10 cover two-phase upload, API lifecycle and R8; Tasks 11–14 cover frontend, operations and complete gates.
- **Four review revisions:** `SCHEDULED` semantics are explicit in Global Constraints and Tasks 2/4/9; upload publishes only after`PREPARED`in Tasks 6–7/10; R8 is explicit in Task 10/14; Phase 1/2/3 gates are explicit.
- **Type consistency:** `CompileRuntimeConfig`, `ServiceReadiness`, `CompileManifest`, `TransactionState`, `PreparedIngest` and process interfaces are introduced before consumers.
- **No scope expansion:** No multiworker, Redis, database, automatic retry, progress UI, repair mode or compile/relate semantic rewrite.
- **No hidden destructive path:** CLI has no force/skip commands; upload rollback deletes only targets recorded as created by the request.
- **No unverified completion claim:** Exact test counts are recorded only after fresh commands run.
