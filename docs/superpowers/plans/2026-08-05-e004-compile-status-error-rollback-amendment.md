# E004 Implementation Plan — Mandatory Self-Review Amendment

> This amendment is normative. It must be read before `2026-08-05-e004-compile-status-error-rollback.md`. Where wording or code differs, this file prevails. It corrects issues found during the required plan self-review; it does not authorize implementation.

## 1. Preflight Additions

Before Task 1, the implementing agent must read the frontend instructions and installed Next.js documentation:

```powershell
Set-Location "D:\administrator\Desktop\大模型产品化\port-knowledge-base-e004"
Get-Content frontend\AGENTS.md
Get-ChildItem frontend\node_modules\next\dist\docs -Recurse -File | Select-Object -First 20 FullName
```

The implementing agent must invoke `superpowers:test-driven-development` before changing production code. A fresh agent that receives only one task must still read the design spec, the main plan, this amendment, `CLAUDE.md`, and any scoped `AGENTS.md` before editing.

## 2. Task 3 Override: Snapshot Entries Must Carry Relative Paths

Replace the Task 3 `SnapshotEntry`, `create_artifact_snapshot()` and `restore_artifact_snapshot()` definitions with the following. Rollback diagnostics must never store absolute repository paths.

```python
@dataclass(frozen=True)
class SnapshotEntry:
    target: Path
    relative_path: str
    existed: bool
    backup: Path | None


@dataclass(frozen=True)
class ArtifactSnapshot:
    entries: tuple[SnapshotEntry, ...]


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
```

Update the Task 3 path assertion to use the stored relative field:

```python
relative = {entry.relative_path for entry in snapshot.entries}
```

Add this non-disclosure assertion:

```python
def test_restore_artifact_snapshot_reports_relative_failure_paths(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_012"
    target = base / "wiki" / "index.yaml"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"old")
    snapshot = create_artifact_snapshot(base, doc_id, tmp_path / "snapshot")

    with patch("api.compile_jobs._atomic_write_bytes", side_effect=OSError("denied")):
        failures = restore_artifact_snapshot(snapshot)

    assert failures == ["wiki/index.yaml"]
    assert str(base) not in failures[0]
```

## 3. Task 4 Override: Snapshot Creation Failure Must Become `error`

The main plan placed snapshot creation before the runner's exception handling. That would leave a document in `compiling` if snapshot creation failed. Replace the Task 4 runner with the following structure.

Add logging and a terminal-write helper:

```python
import logging

logger = logging.getLogger(__name__)


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
```

Use this runner:

```python
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
```

The outer exception branch covers `TemporaryDirectory` setup and other wrapper infrastructure failures. It does not claim to solve concurrent deletion of the metadata file; when metadata is missing, the helper emits an explicit server log rather than silently claiming success.

Add exact tests that were only described in the main plan:

```python
def test_run_compile_task_missing_script_is_terminal_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_026"
    _write_meta(base, doc_id)

    run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert "compile script missing" in meta["error_message"]


def test_run_compile_task_snapshot_failure_is_terminal_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_027"
    _write_meta(base, doc_id)
    _write_compile_script(base)

    with patch(
        "api.compile_jobs.create_artifact_snapshot",
        side_effect=OSError("snapshot denied"),
    ), patch("api.compile_jobs.subprocess.run") as mock_run:
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
    assert "snapshot denied" in meta["error_message"]
    mock_run.assert_not_called()
```

Delete the main plan sentence that suggests deferring a failed terminal metadata write to a future test. The implementation must log it explicitly as shown above and report the residual concurrent-deletion boundary.

## 4. Task 6 Override: Jest Fetch Mock Must Not Assume `Response` Exists

Use a writable mock function instead of `jest.spyOn(global, 'fetch')` plus `new Response(...)`:

```typescript
import {
    getCompileErrorMessage,
    getUserFacingErrorMessage,
    recompileDoc,
} from '@/lib/api';

const mockFetch = jest.fn();
const originalFetch = global.fetch;

beforeAll(() => {
    Object.defineProperty(global, 'fetch', {
        configurable: true,
        writable: true,
        value: mockFetch,
    });
});

beforeEach(() => {
    mockFetch.mockReset();
});

afterAll(() => {
    Object.defineProperty(global, 'fetch', {
        configurable: true,
        writable: true,
        value: originalFetch,
    });
});


test('parses compile_in_progress without exposing 409 or Conflict', async () => {
    mockFetch.mockResolvedValue({
        ok: false,
        status: 409,
        statusText: 'Conflict',
        text: async () => JSON.stringify({
            detail: { code: 'compile_in_progress' },
        }),
    } as Response);

    await expect(recompileDoc('doc_1')).rejects.toMatchObject({
        name: 'ApiError',
        status: 409,
        code: 'compile_in_progress',
        message: '该文档正在编译，请稍后再试',
    });
});
```

The test file does not need to import `ApiError` unless it directly constructs one.

## 5. Task 8 Override: Preserve All Real Error Helpers in the Module Mock

Replace the main plan's `@/lib/api` mock with this factory. Omitting `getCompileErrorMessage` would make the page test fail for the wrong reason.

```typescript
const mockToastPush = jest.fn();

jest.mock('@/components/ui/Toast', () => ({
    useToast: () => ({ push: mockToastPush }),
}));

jest.mock('@/lib/api', () => {
    const actual = jest.requireActual('@/lib/api');
    return {
        ...actual,
        fetchWikiIndex: jest.fn(),
        deleteDoc: jest.fn(),
        recompileDoc: jest.fn(),
    };
});
```

Use this cleanup in the polling suite:

```typescript
beforeEach(() => {
    mockedFetch.mockReset();
    mockedRecompile.mockReset();
    mockToastPush.mockReset();
});

afterEach(() => {
    jest.clearAllTimers();
    jest.useRealTimers();
});
```

Destructure the stable callback in the page:

```typescript
const { push: pushToast } = useToast();
```

Use `pushToast` in `applySnapshot()` and set its dependency to `[pushToast]`, not the whole context object.

After the terminal response in the 3000ms test, advance another 6000ms and prove no further fetch occurs:

```typescript
await act(async () => {
    jest.advanceTimersByTime(6000);
    await Promise.resolve();
});
expect(mockedFetch).toHaveBeenCalledTimes(2);
```

Add the complete new-round test:

```typescript
test('a retry starts a new failure round and may toast again', async () => {
    jest.useFakeTimers();
    mockedFetch
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{
                id: 'doc_1',
                title: 'T',
                status: 'error',
                error_code: 'timeout',
            }],
        })
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{
                id: 'doc_1',
                title: 'T',
                status: 'error',
                error_code: 'timeout',
            }],
        })
        .mockResolvedValueOnce({
            total_docs: 1,
            documents: [{
                id: 'doc_1',
                title: 'T',
                status: 'error',
                error_code: 'compile_failed',
            }],
        });
    mockedRecompile.mockResolvedValue({ status: 'recompiling' });

    render(<WikiPage />);
    const firstButton = await screen.findByTestId('recompile-btn');
    fireEvent.click(firstButton);
    await screen.findByTestId('compiling-hint');

    await act(async () => {
        jest.advanceTimersByTime(3000);
        await Promise.resolve();
    });
    await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(1));

    fireEvent.click(await screen.findByTestId('recompile-btn'));
    await screen.findByTestId('compiling-hint');
    await act(async () => {
        jest.advanceTimersByTime(3000);
        await Promise.resolve();
    });

    await waitFor(() => expect(mockToastPush).toHaveBeenCalledTimes(2));
    expect(mockToastPush).toHaveBeenLastCalledWith(
        '编译失败，请稍后重试或联系管理员',
        'error',
    );
});
```

Add the complete 409 test:

```typescript
test('duplicate recompile shows friendly conflict copy only', async () => {
    const { ApiError } = jest.requireActual('@/lib/api');
    mockedFetch.mockResolvedValue({
        total_docs: 1,
        documents: [{ id: 'doc_1', title: 'T', status: 'compiled' }],
    });
    mockedRecompile.mockRejectedValue(
        new ApiError(
            '该文档正在编译，请稍后再试',
            409,
            'compile_in_progress',
        ),
    );

    render(<WikiPage />);
    fireEvent.click(await screen.findByTestId('recompile-btn'));

    await waitFor(() => {
        expect(mockToastPush).toHaveBeenCalledWith(
            '该文档正在编译，请稍后再试',
            'error',
        );
    });
    expect(mockToastPush.mock.calls.flat().join(' ')).not.toMatch(
        /409|Conflict|RuntimeError|OPENAI_API_KEY/,
    );
});
```

Import `fireEvent` in this test file.

## 6. Task 10 Override: Exact CLAUDE Metadata

Set the header exactly to:

```markdown
> Version: 1.4 | Updated: 2026-08-05
```

Use the exact risk-9 replacement given in the main plan. Risk 6 remains unstruck and unchanged.

## 7. Self-Review Result

The plan package now has explicit coverage for:

- snapshot setup failure before the child process starts;
- relative-only rollback diagnostics;
- missing compile script terminal state;
- deterministic Jest fetch mocking without relying on a global `Response` constructor;
- complete frontend retry-round and 409 non-disclosure tests;
- stable Toast callback dependencies;
- exact documentation versioning;
- required frontend instructions and local Next.js documentation review.

No business code, tests, PR, merge or implementation branch is created by this amendment.
