# E004 Implementation Plan — Mandatory Coverage Addendum

> Read order: main implementation plan → mandatory self-review amendment → this coverage addendum. This file prevails for the tests below. It closes final spec-coverage gaps found during line-by-line review and does not authorize implementation.

## 1. Task 4 Must Prove All Seven Artifacts

Import `artifact_paths` in `tests/test_compile_jobs.py` and add both tests below. These tests are required in addition to the helper-level snapshot tests.

### 1.1 First compilation failure removes every newly created artifact

```python
def test_first_compile_failure_removes_all_new_artifacts(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_028"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    targets = artifact_paths(base, doc_id)

    def fake_run(*_args, **_kwargs):
        for index, target in enumerate(targets):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"new-{index}".encode("utf-8"))
        write_doc_compile_result(
            doc_id,
            "error",
            error_code="compile_failed",
            error_message="child failed",
            base_dir=base,
        )
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="child failed",
        )

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    assert all(not target.exists() for target in targets)
    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "compile_failed"
```

This test proves a first upload cannot leave a summary, index, per-document ontology, global ontology, per-document relations, knowledge graph or entity-relations half-product when none existed before.

### 1.2 Recompile failure restores every artifact byte-for-byte

```python
def test_recompile_failure_restores_all_artifacts_byte_for_byte(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_029"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    targets = artifact_paths(base, doc_id)
    original = {}
    for index, target in enumerate(targets):
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"old-{index}\r\n".encode("utf-8")
        target.write_bytes(payload)
        original[target] = payload

    def fake_run(*_args, **_kwargs):
        for index, target in enumerate(targets):
            target.write_bytes(f"partial-{index}\n".encode("utf-8"))
        write_doc_compile_result(
            doc_id,
            "error",
            error_code="service_unavailable",
            error_message="provider unavailable",
            base_dir=base,
        )
        return subprocess.CompletedProcess(
            [],
            1,
            stdout="",
            stderr="503 Service Unavailable",
        )

    with patch("api.compile_jobs.subprocess.run", side_effect=fake_run):
        run_compile_task(doc_id, base)

    for target, payload in original.items():
        assert target.read_bytes() == payload
    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"
```

The assertion is byte-based, not YAML-semantic, so line endings, key ordering and timestamps cannot be silently changed by rollback.

### 1.3 Spawn failure with a healthy rollback is terminal and sanitized

```python
def test_spawn_oserror_restores_old_artifacts_and_records_error(tmp_path):
    base = tmp_path / "repo"
    doc_id = "doc_20260805_030"
    _write_meta(base, doc_id)
    _write_compile_script(base)
    index = base / "wiki" / "index.yaml"
    index.parent.mkdir(parents=True)
    index.write_bytes(b"old-index")

    with patch(
        "api.compile_jobs.subprocess.run",
        side_effect=OSError("connection refused token=secret-value"),
    ):
        run_compile_task(doc_id, base)

    meta = read_doc_meta(doc_id, base)
    assert meta["status"] == "error"
    assert meta["error_code"] == "service_unavailable"
    assert "secret-value" not in meta["error_message"]
    assert index.read_bytes() == b"old-index"
```

If the classifier does not map this `OSError` text to `service_unavailable`, update the Task 2 pattern list before making this test green. Do not weaken the assertion to `compile_failed` merely to pass.

## 2. Task 5 Must Prove the Schedule Lock Under Concurrency

Add this test to `tests/test_api.py`. It calls the scheduling helper directly so Starlette does not execute the queued background tasks.

```python
def test_schedule_compile_allows_only_one_concurrent_request(tmp_path, monkeypatch):
    import threading
    from fastapi import BackgroundTasks, HTTPException
    import api.main as api_mod

    raw = tmp_path / "raw"
    raw.mkdir()
    doc_id = "doc_20260805_031"
    (raw / f"{doc_id}.meta.yaml").write_text(
        yaml.safe_dump({"id": doc_id, "status": "compiled"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(api_mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "RAW_DIR", raw)

    barrier = threading.Barrier(2)
    backgrounds = [BackgroundTasks(), BackgroundTasks()]
    outcomes = []
    outcome_lock = threading.Lock()

    def worker(background_tasks):
        barrier.wait(timeout=2)
        try:
            api_mod._schedule_compile(background_tasks, doc_id)
            result = "scheduled"
        except HTTPException as exc:
            result = (exc.status_code, exc.detail)
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=worker, args=(background_tasks,))
        for background_tasks in backgrounds
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert sorted(
        outcomes,
        key=lambda item: 0 if item == "scheduled" else 1,
    ) == [
        "scheduled",
        (409, {"code": "compile_in_progress"}),
    ]
    assert sum(len(background.tasks) for background in backgrounds) == 1
    meta = yaml.safe_load(
        (raw / f"{doc_id}.meta.yaml").read_text(encoding="utf-8")
    )
    assert meta["status"] == "compiling"
```

If thread scheduling makes the sorted assertion awkward, assert `outcomes.count("scheduled") == 1` and separately assert the single conflict tuple; do not remove the concurrency barrier or replace this with two sequential calls.

## 3. Task 10 Target Commands Must Include These Tests

The backend target command remains:

```powershell
python -m pytest tests/test_doc_admin.py tests/test_compile_jobs.py tests/test_api.py -q
```

The completion report must explicitly identify the three artifact tests and the concurrent scheduling test as passed on the final Head. A generic full-pytest count is not sufficient evidence for these contracts.

## 4. Final Coverage Mapping

- Immediate `compiling`: Task 1 + Task 5 upload test.
- Duplicate conflict: Task 5 sequential endpoint test + concurrent scheduling test.
- Child startup/config/execution outcomes: Task 2 + Task 4.
- Snapshot infrastructure failure: mandatory self-review amendment.
- First compile cleanup across all seven artifacts: this addendum §1.1.
- Recompile byte-exact recovery across all seven artifacts: this addendum §1.2.
- Spawn failure and sanitization: this addendum §1.3.
- Rollback failure classification: main Task 4.
- Single-process global execution serialization: main Task 4 concurrency test.
- Frontend structured errors, row-level copy, 3-second polling and one Toast per round: Tasks 6–9 plus the first amendment.
- Full offline tests, build, lint, data manifest and scope audit: Task 10.

No product code, test code, implementation branch, PR or merge is created by this addendum.
